"""Review queue and document decisions (Phase 2). Permissions live in identity/perms.py; the one data-scoped rule
(HR Ops decides low-risk documents only) is in documents.service.can_decide."""
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import records
import work_queue
from documents import route, service, store
from documents.service import DocError
from identity import perms
from identity.service import current_user
from shared import db

router = APIRouter()
DOC_KINDS = ("doc.review", "doc.spot_check")


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except DocError as e:
        raise HTTPException(e.status, str(e))


def _public(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("path", "extra")}


@router.get("/api/review-queue")
def review_queue(kind: str = "doc.review", reason: str = "", assignee: str = "all", status: str = "open"):
    """Open items (or done in the last 7 days) with the document, the joiner and the SLA."""
    if kind not in DOC_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(DOC_KINDS)}")
    user = current_user.get()
    since = datetime.now(timezone.utc) - timedelta(days=7) if status == "resolved" else None
    rows = work_queue.list_items(None, status, kind, assignee, user["id"], reason, since)
    ids = [r["entity_id"] for r in rows]
    docs = {r["id"]: r for r in db.fetch("""SELECT d.id, d.filename, d.doc_type, d.tier, d.confidence, d.status, d.received_at,
                                                   d.employee_id, e.name AS employee_name
                                            FROM documents d JOIN employees e ON e.id = d.employee_id WHERE d.id = ANY(%s)""", (ids,))}
    llm_down = ""
    try:
        import llm
        llm_down = llm.unavailable_reason()
    except Exception:
        pass
    auto_week = db.fetch1("""SELECT count(*) AS n FROM doc_decisions WHERE kind='auto_accept' AND at > now() - interval '7 days'""")["n"]
    return {"items": [{**r, "document": docs.get(r["entity_id"])} for r in rows], "llm_unavailable": llm_down,
            "auto_accepted_this_week": auto_week}


class Claim(BaseModel):
    takeover: bool = False


@router.post("/api/review-queue/{item_id}/claim")
def claim(item_id: int, body: Claim | None = None):
    user = current_user.get()
    it = work_queue.get(item_id)
    if not it or it["kind"] not in DOC_KINDS:
        raise HTTPException(404, "Not found")
    doc = store.get(it["entity_id"])
    may_spot = perms.allowed("POST", f"/api/review-queue/{item_id}/spot-check", user["role"])
    if (it["kind"] == "doc.spot_check" and not may_spot) or not service.can_decide(doc, user):
        raise HTTPException(403, "High-risk documents and spot checks are for approvers")
    return work_queue.claim(item_id, user, takeover=bool(body and body.takeover))


class SpotCheck(BaseModel):
    verdict: str
    reason_text: str = Field(min_length=3, max_length=2000)


@router.post("/api/review-queue/{item_id}/spot-check")
def spot_check(item_id: int, body: SpotCheck):
    return _public(_call(service.spot_check, item_id, current_user.get(), body.verdict, body.reason_text))


@router.get("/api/documents/{doc_id}")
def document_detail(doc_id: str):
    """Everything a reviewer needs: the policy facts first, then evidence, then history."""
    d = store.get(doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    user = current_user.get()
    emp = records.employee(d["employee_id"])
    item = records.item(d["item_id"]) if d.get("item_id") else None
    decisions = store.decisions(doc_id)
    current = next((x for x in decisions if x["id"] == d.get("current_decision_id")), None)
    version = db.fetch1("SELECT id, number, state FROM rule_set_versions WHERE id=%s", (d["policy_version_id"],)) \
        if d.get("policy_version_id") else None
    reviews = [r for r in work_queue.list_items(None, "open", list(DOC_KINDS)) if r["entity_id"] == doc_id]
    return {
        "document": _public(d),
        "policy": {"tier": d.get("tier"), "threshold": (current or {}).get("threshold"), "version": version,
                   "always_high": d.get("doc_type") in route.ALWAYS_HIGH,
                   "decided_by": "system" if current and current["decided_by"] is None else (current or {}).get("decided_by_email"),
                   "decision_kind": (current or {}).get("kind"), "reason_code": (current or {}).get("reason_code"),
                   "reason_label": route.REASON_LABEL.get((current or {}).get("reason_code"), (current or {}).get("reason_code"))},
        "employee": {k: (emp or {}).get(k) for k in ("id", "name", "email", "date_of_birth", "joining_date", "department", "location")},
        "item": {k: item.get(k) for k in ("id", "key", "name", "status", "due")} if item else None,
        "mismatches": store.mismatches_of(doc_id),
        "decisions": [{k: v for k, v in x.items() if k != "extracted"} for x in decisions],
        "reviews": reviews,
        "can_decide": service.can_decide(d, user) and d["status"] == "Under Review",
        "can_override": bool(perms.allowed("POST", f"/api/documents/{doc_id}/override", user["role"]))
                        and d["status"] not in ("Submitted", "Under Review"),
        "reason_codes": list(service.REASON_CODES),
    }


class Decision(BaseModel):
    decision: str
    reason_code: str
    reason_text: str = Field(min_length=3, max_length=2000)
    corrected_fields: dict | None = None
    issues: list[str] | None = None
    decision_key: str | None = Field(default=None, max_length=100)


@router.post("/api/documents/{doc_id}/decision")
def decision(doc_id: str, body: Decision):
    return _public(_call(service.decide_human, doc_id, current_user.get(), body.decision, body.reason_code, body.reason_text,
                         body.corrected_fields, body.issues, body.decision_key))


@router.post("/api/documents/{doc_id}/override")
def override(doc_id: str, body: Decision):
    return _public(_call(service.override, doc_id, current_user.get(), body.decision, body.reason_code, body.reason_text,
                         body.decision_key))


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion (good at small n, unlike the normal approximation)."""
    if n == 0:
        return None
    p = passes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(centre - half, 4), round(centre + half, 4)


@router.get("/api/quality/ai-accuracy")
def ai_accuracy(days: int = 90):
    """How the policy-driven pipeline is doing: auto-accept rate, spot-check precision, review mix, time to decision."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    first = db.fetch("""SELECT DISTINCT ON (dd.document_id) dd.document_id, dd.kind, dd.reason_code, d.doc_type, dd.model
                        FROM doc_decisions dd JOIN documents d ON d.id = dd.document_id
                        WHERE dd.at >= %s AND dd.decided_by IS NULL AND dd.kind <> 'backfill'
                        ORDER BY dd.document_id, dd.at""", (since,))
    spots = db.fetch("""SELECT dd.kind, d.doc_type FROM doc_decisions dd JOIN documents d ON d.id = dd.document_id
                        WHERE dd.at >= %s AND dd.kind IN ('spot_check_pass','spot_check_fail')""", (since,))
    ttd = db.fetch1("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM h.at - r.at)) AS s
                       FROM doc_decisions r JOIN LATERAL (
                         SELECT at FROM doc_decisions h WHERE h.document_id = r.document_id AND h.kind LIKE 'human_%%' AND h.at > r.at
                         ORDER BY at LIMIT 1) h ON true
                       WHERE r.kind = 'routed_review' AND r.at >= %s""", (since,))
    by_type: dict[str, dict] = {}
    for r in first:
        t = by_type.setdefault(r["doc_type"] or "unknown", {"total": 0, "auto_accept": 0, "review": 0, "correction": 0,
                                                            "spot_pass": 0, "spot_fail": 0})
        t["total"] += 1
        t[{"auto_accept": "auto_accept", "routed_review": "review"}.get(r["kind"], "correction")] += 1
    for s in spots:
        t = by_type.setdefault(s["doc_type"] or "unknown", {"total": 0, "auto_accept": 0, "review": 0, "correction": 0,
                                                            "spot_pass": 0, "spot_fail": 0})
        t["spot_pass" if s["kind"] == "spot_check_pass" else "spot_fail"] += 1
    n_spot = len(spots)
    passes = sum(1 for s in spots if s["kind"] == "spot_check_pass")
    reasons: dict[str, int] = {}
    for r in first:
        if r["kind"] == "routed_review":
            reasons[r["reason_code"]] = reasons.get(r["reason_code"], 0) + 1
    total = len(first)
    return {"days": days, "documents": total,
            "auto_accept_rate": round(sum(1 for r in first if r["kind"] == "auto_accept") / total, 4) if total else None,
            "spot_checks": n_spot, "spot_check_precision": round(passes / n_spot, 4) if n_spot else None,
            "precision_ci95": wilson(passes, n_spot), "too_small": n_spot < 30,
            "review_reasons": [{"reason_code": k, "label": route.REASON_LABEL.get(k, k), "n": v}
                               for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
            "median_seconds_to_decision": round(ttd["s"]) if ttd and ttd["s"] is not None else None,
            "by_type": by_type, "models": sorted({r["model"] for r in first if r["model"]})}

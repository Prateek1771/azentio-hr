"""Document flow (Phase 2): receive -> extract (job) -> evaluate -> route (pure) -> apply decision -> derive item status
-> open a work-queue item if a human is needed.

- The AI perceives, policy decides: `extract` returns facts + confidence, `validate.evaluate` returns checks, and
  `route.decide` picks the outcome from the governed tier and threshold of the active rule-set version.
- Every status change is a `doc_decisions` row with provenance; `documents.status` is that decision's result, and the
  item status is derived from it through `item_status_for()` only.
- Every write locks the document and checks it is still in the state the decision expects; idempotency keys make
  retries (job or double click) no-ops. Side effects (projection, recompute, emails) are outbox jobs.
"""
import hashlib
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import audit
import records
import work_queue
from documents import route, store
from shared import db, jobs

UPLOADS = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS.mkdir(exist_ok=True)
EXTRACT_ATTEMPTS = 4  # with the queue's backoff (10s, 20s, 40s) the last try lands ~70s after upload

# The single mapping from a document's status to its checklist item's status.
ITEM_STATUS = {"Submitted": "Submitted", "Under Review": "Under Review", "Accepted": "Accepted",
               "Correction Requested": "Correction Requested", "Rejected": "Correction Requested"}
HUMAN = {"accept": ("human_accept", "Accepted"), "reject": ("human_reject", "Rejected"),
         "request_correction": ("human_correction", "Correction Requested")}
REASON_CODES = ("verified_against_original", "name_variant_acceptable", "document_expired", "wrong_document",
                "illegible", "other")


class DocError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def item_status_for(doc_status: str) -> str:
    return ITEM_STATUS[doc_status]


def guess_mime(filename: str, given: str | None) -> str:
    if given and given != "application/octet-stream":
        return given
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def active_policy(conn=None) -> tuple[int | None, dict, dict]:
    """(rule-set version id, effective document policy, doc_types) of the active version."""
    from policy import store as pstore
    v = pstore.active_version(conn=conn)
    cfg = dict(v["config"]) if v else {}
    if not cfg.get("doc_types"):
        import checklist
        cfg["doc_types"] = checklist.config("doc_types") or {}
    return (v["id"] if v else None), route.policy_for(cfg), cfg["doc_types"]


def _live_item(eid: str, key: str, conn=None) -> dict | None:
    it = records.item_by_key(eid, key, conn=conn) if key else None
    return None if not it or it.get("cancelled_by_version") or it.get("superseded_by_version") else it


def _sync_item(item: dict | None, doc_status: str, conn, force: bool = False) -> None:
    """Derive the item's status from the document decision. A later document never downgrades an accepted item,
    except through an explicit human override (force)."""
    if not item:
        return
    if item["status"] == "Accepted" and not force:
        return
    records.update_item(item["id"], conn=conn, status=item_status_for(doc_status))


def _follow_ups(doc: dict, decision_id: int, status: str, line: str, conn) -> None:
    """Outbox: projection, recompute, feed and integrations (and a correction email) commit with the decision."""
    jobs.enqueue("doc.after_decision", {"employee_id": doc["employee_id"], "document_id": doc["id"], "line": line},
                 idempotency_key=f"doc.after_decision:{decision_id}", conn=conn)
    if status == "Correction Requested":
        jobs.enqueue("notify.correction", {"document_id": doc["id"]}, idempotency_key=f"notify.correction:{decision_id}", conn=conn)


# ---------- 1. receive (synchronous, in the request) ----------

def receive(eid: str, filename: str, data: bytes, mime: str | None, source: str, item_key: str | None = None) -> dict:
    """Save the file and queue extraction. Returns at once; the LLM never blocks an upload or the mail poller."""
    from graph import event, new_id
    if not records.employee(eid):
        raise DocError(404, "Employee not found")
    mime = guess_mime(filename, mime)
    doc_id = new_id("doc")
    path = UPLOADS / f"{doc_id}_{Path(filename).name}"
    path.write_bytes(data)
    item = _live_item(eid, item_key or "")
    with db.tx() as c:
        store.insert({"id": doc_id, "employee_id": eid, "item_id": item["id"] if item else None, "filename": filename,
                      "path": str(path), "mime": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "source": source, "status": "Submitted", "doc_type": item_key or "", "item_key": item_key or ""}, c)
        _sync_item(item, "Submitted", c)
        jobs.enqueue("doc.extract", {"document_id": doc_id}, idempotency_key=f"doc.extract:{doc_id}",
                     max_attempts=EXTRACT_ATTEMPTS, conn=c)
    records.project(eid)
    event(eid, "document", f"Received '{filename}' via {source}; checking it now")
    return {"document_id": doc_id, "status": "Submitted"}


# ---------- 2-5. extract -> evaluate -> route -> apply (job) ----------

@jobs.handler("doc.extract")
async def extract_job(job: dict) -> None:
    import extract
    import validate
    from graph import event
    doc = store.get(job["payload"]["document_id"])
    if not doc or doc["status"] != "Submitted":
        return  # already decided (a retry after the decision committed)
    emp = records.employee(doc["employee_id"])
    vid, policy, doc_types = active_policy()
    item_key = doc.get("item_key") or ""
    file_issues = validate.check_file(doc["mime"], doc["size"])
    ex, extraction_ok = {}, True
    if not file_issues:
        try:
            data = Path(doc["path"]).read_bytes()
            ex = await extract.extract(data, doc["mime"], doc["filename"], doc_types,
                                       hint=doc_types.get(item_key, {}).get("name"))
        except Exception as e:
            received = datetime.fromisoformat(doc["received_at"])
            deadline = received + timedelta(minutes=float(policy["extraction_deadline_minutes"]))
            if datetime.now(timezone.utc) < deadline and job["attempts"] < job["max_attempts"]:
                raise  # retry with backoff
            event(emp["id"], "error", f"AI extraction unavailable for '{doc['filename']}' ({e}); sent to human review")
            extraction_ok = False
    key = item_key or ex.get("doc_type") or "unknown"
    item = _live_item(emp["id"], key)
    tier, threshold = route.tier_for(key, policy)
    check = {"issues": [], "mismatches": [], "checks": []}
    if extraction_ok and not file_issues:
        check = validate.evaluate(ex, emp, doc_types.get(key, {}))
        confidence = float(ex.get("confidence") or 0)
        if tier == "low" and not check["issues"] and confidence < threshold:
            # AwaitVerify is an input check for low-tier documents only; it never stands in for a human decision.
            verified = await extract.human_verify(doc["path"], ex, f"Verify the {key} of {emp['name']}")
            if verified:
                ex = {**verified, **{k: v for k, v in ex.items() if k.startswith("_")}, "_human_verified": True}
                check = validate.evaluate(ex, emp, doc_types.get(key, {}))
    r = route.decide(file_issues=file_issues, issues=check["issues"], extraction_ok=extraction_ok,
                     on_checklist=item is not None, tier=tier, confidence=float(ex.get("confidence") or 0),
                     threshold=threshold, doc_id=doc["id"], spot_check_rate=float(policy["spot_check_rate"]))
    apply_route(doc["id"], r, key=key, item=item, ex=ex, check=check, file_issues=file_issues, tier=tier,
                threshold=threshold, policy_version_id=vid)


def _review_reason(r: route.Route, key: str, confidence: float, threshold: float) -> str:
    if r.reason_code == "low_confidence":
        return f"Low confidence {confidence:.2f} < {threshold:.2f}"
    if r.reason_code == "unexpected_type":
        return f"Classified as '{key}', which isn't on this joiner's checklist"
    return route.REASON_LABEL.get(r.reason_code, r.reason_code)


def _open_review(doc: dict, reason_code: str, reason: str, item: dict | None, conn, detail: dict | None = None) -> int | None:
    due = datetime.now(timezone.utc) + timedelta(days=2)
    if item and item.get("due"):
        due = min(due, datetime.fromisoformat(item["due"]).replace(tzinfo=timezone.utc))
    return work_queue.open_item("doc.review", "document", doc["id"], reason, assignee_role="approver", sla_due=due,
                                dedupe_key=f"doc.review:{doc['id']}:{reason_code}",
                                detail={"reason_code": reason_code, "employee_id": doc["employee_id"],
                                        "doc_type": doc.get("doc_type"), **(detail or {})}, conn=conn)


def apply_route(doc_id: str, r: route.Route, *, key: str, item: dict | None, ex: dict, check: dict, file_issues: list,
                tier: str, threshold: float, policy_version_id: int | None) -> int | None:
    """Write the decision, the document, its mismatches and the derived item status in one transaction."""
    with db.tx() as c:
        doc = store.get(doc_id, c, lock=True)
        if doc["status"] != "Submitted":
            return None  # someone (a concurrent runner) already decided
        issues = list(file_issues or check["issues"])
        if r.reason_code == "unexpected_type":
            issues.append(f"Document classified as '{key}', which is not on this checklist.")
        confidence = float(ex.get("confidence") or 0)
        store.update(doc_id, c, item_id=item["id"] if item else None, doc_type=key, tier=tier, confidence=confidence,
                     extracted=ex, issues=issues, checks=check["checks"], model=ex.get("_model"),
                     prompt_version=ex.get("_prompt_version"), policy_version_id=policy_version_id)
        store.set_mismatches(doc_id, check["mismatches"], c)
        doc = {**doc, "doc_type": key}
        wid = _open_review(doc, r.reason_code, _review_reason(r, key, confidence, threshold), item, c,
                           {"confidence": confidence, "threshold": threshold, "tier": tier}) if r.review else None
        dec = store.add_decision({"document_id": doc_id, "kind": r.kind, "reason_code": r.reason_code,
                                  "status_after": r.status, "model": ex.get("_model"), "prompt_version": ex.get("_prompt_version"),
                                  "confidence": confidence, "tier": tier, "threshold": threshold,
                                  "policy_version_id": policy_version_id, "checks": check["checks"], "extracted": ex,
                                  "work_item_id": wid, "idempotency_key": f"doc.extract:{doc_id}:route"}, c)
        if dec is None:
            return None
        if r.spot_check:
            work_queue.open_item("doc.spot_check", "document", doc_id, f"Spot check: {key} auto-accepted at {confidence:.2f}",
                                 assignee_role="approver", dedupe_key=f"doc.spot_check:{doc_id}",
                                 detail={"employee_id": doc["employee_id"], "doc_type": key, "confidence": confidence}, conn=c)
        _sync_item(item, r.status, c)
        label = (item or {}).get("name") or key
        _follow_ups(doc, dec, r.status, f"{label}: {r.status} ({route.REASON_LABEL.get(r.reason_code, r.reason_code)})"
                    + (f" — {'; '.join(issues)}" if issues else ""), c)
    return dec


# ---------- human decisions ----------

def _close_reviews(doc_id: str, user: dict, reason: str, conn, kind: str = "doc.review") -> None:
    conn.execute("""UPDATE work_queue SET status='resolved', resolution=%s, resolved_by=%s, resolved_at=now()
                    WHERE entity_type='document' AND entity_id=%s AND kind=%s AND status IN ('open','in_progress')""",
                 (reason, user["id"], doc_id, kind))


def _human_row(doc: dict, user: dict, kind: str, status: str, reason_code: str, reason_text: str, key: str | None,
               corrected: dict | None = None) -> dict:
    last = db.fetch1("SELECT threshold FROM doc_decisions WHERE id=%s", (doc["current_decision_id"],)) if doc.get("current_decision_id") else None
    return {"document_id": doc["id"], "kind": kind, "decided_by": user["id"], "reason_code": reason_code,
            "reason_text": reason_text.strip(), "status_after": status, "model": doc.get("model"),
            "prompt_version": doc.get("prompt_version"), "confidence": doc.get("confidence"), "tier": doc.get("tier"),
            "threshold": (last or {}).get("threshold"), "policy_version_id": doc.get("policy_version_id"),
            "checks": doc.get("checks") or [], "extracted": doc.get("extracted") or {}, "corrected_fields": corrected or {},
            "idempotency_key": key or f"human:{doc['id']}:{uuid.uuid4().hex}"}


def _require_reason(reason_code: str, reason_text: str) -> None:
    if reason_code not in REASON_CODES:
        raise DocError(422, f"reason_code must be one of {', '.join(REASON_CODES)}")
    if len((reason_text or "").strip()) < 3:
        raise DocError(422, "A reason is required (at least 3 characters)")


def can_decide(doc: dict, user: dict) -> bool:
    """Approvers decide any document; HR Ops only low-risk ones. (Tier is data, so this is checked here, not in perms.)"""
    return user["role"] == "approver" or (user["role"] == "hr_ops" and doc.get("tier") != "high")


def decide_human(doc_id: str, user: dict, decision: str, reason_code: str, reason_text: str,
                 corrected_fields: dict | None = None, issues: list[str] | None = None, decision_key: str | None = None) -> dict:
    if decision not in HUMAN:
        raise DocError(422, "decision must be accept, reject or request_correction")
    _require_reason(reason_code, reason_text)
    kind, status = HUMAN[decision]
    with db.tx() as c:
        doc = store.get(doc_id, c, lock=True)
        if not doc:
            raise DocError(404, "Document not found")
        if decision_key and db.fetch1("SELECT 1 AS x FROM doc_decisions WHERE idempotency_key=%s", (decision_key,), conn=c):
            return store.get(doc_id, c)  # the same click, replayed
        if not can_decide(doc, user):
            raise DocError(403, "High-risk documents need an approver's decision")
        if doc["status"] != "Under Review":
            raise DocError(409, f"This document is {doc['status']}, not waiting for review. Use override to reverse a decision.")
        held = db.fetch1("""SELECT u.email FROM work_queue w JOIN users u ON u.id = w.assignee_id
                            WHERE w.entity_type='document' AND w.entity_id=%s AND w.kind='doc.review'
                            AND w.status='in_progress' AND w.assignee_id <> %s""", (doc_id, user["id"]), conn=c)
        if held:
            raise DocError(409, f"Being reviewed by {held['email']}; take it over from the review queue first")
        if corrected_fields:
            store.update(doc_id, c, extracted={**(doc.get("extracted") or {}), **corrected_fields, "_corrected_by": user["email"]})
        if status == "Correction Requested" and issues:
            store.update(doc_id, c, issues=[i for i in issues if i.strip()])
        dec = store.add_decision(_human_row(doc, user, kind, status, reason_code, reason_text, decision_key, corrected_fields), c)
        _close_reviews(doc_id, user, reason_text, c)
        _sync_item(records.item(doc["item_id"], conn=c) if doc.get("item_id") else None, status, c)
        _follow_ups(doc, dec, status, f"{doc['filename']}: {status} by {user['email']} ({reason_text.strip()})", c)
        audit.record_audit("document.decision", "document", doc_id, f"{status} '{doc['filename']}'", reason=reason_text,
                           metadata={"decision": decision, "reason_code": reason_code, "tier": doc.get("tier"),
                                     "corrected_fields": sorted(corrected_fields or {})}, conn=c)
    return store.get(doc_id)


def override(doc_id: str, user: dict, decision: str, reason_code: str, reason_text: str, decision_key: str | None = None) -> dict:
    """Reverse an automatic (or earlier) decision. Approver only (perms). Recorded as human_override."""
    if decision not in HUMAN:
        raise DocError(422, "decision must be accept, reject or request_correction")
    _require_reason(reason_code, reason_text)
    status = HUMAN[decision][1]
    with db.tx() as c:
        doc = store.get(doc_id, c, lock=True)
        if not doc:
            raise DocError(404, "Document not found")
        if decision_key and db.fetch1("SELECT 1 AS x FROM doc_decisions WHERE idempotency_key=%s", (decision_key,), conn=c):
            return store.get(doc_id, c)
        if doc["status"] in ("Submitted", "Under Review"):
            raise DocError(409, "This document is still waiting for a decision; review it instead")
        if doc["status"] == status:
            raise DocError(409, f"This document is already {status}")
        dec = store.add_decision(_human_row(doc, user, "human_override", status, reason_code, reason_text, decision_key), c)
        _close_reviews(doc_id, user, reason_text, c)
        _sync_item(records.item(doc["item_id"], conn=c) if doc.get("item_id") else None, status, c, force=True)
        _follow_ups(doc, dec, status, f"{doc['filename']}: overridden to {status} by {user['email']} ({reason_text.strip()})", c)
        audit.record_audit("document.override", "document", doc_id, f"Overrode '{doc['filename']}' to {status}",
                           reason=reason_text, metadata={"from": doc["status"], "to": status, "reason_code": reason_code}, conn=c)
    return store.get(doc_id)


def spot_check(work_item_id: int, user: dict, verdict: str, reason_text: str) -> dict:
    """Pass: the automatic acceptance stands. Fail: the document goes back to review, and it counts against AI precision."""
    if verdict not in ("pass", "fail"):
        raise DocError(422, "verdict must be pass or fail")
    if len((reason_text or "").strip()) < 3:
        raise DocError(422, "A reason is required (at least 3 characters)")
    with db.tx() as c:
        wi = db.fetch1("SELECT * FROM work_queue WHERE id=%s FOR UPDATE", (work_item_id,), conn=c)
        if not wi or wi["kind"] != "doc.spot_check":
            raise DocError(404, "Spot check not found")
        if wi["status"] not in ("open", "in_progress"):
            raise DocError(409, "This spot check is already done")
        doc = store.get(wi["entity_id"], c, lock=True)
        status = doc["status"] if verdict == "pass" else "Under Review"
        row = _human_row(doc, user, f"spot_check_{verdict}", status, "spot_check", reason_text, f"spot_check:{work_item_id}")
        row["work_item_id"] = work_item_id
        dec = store.add_decision(row, c)
        if verdict == "fail":
            item = records.item(doc["item_id"], conn=c) if doc.get("item_id") else None
            _open_review(doc, "spot_check_failed", "Spot check failed: re-review this document", item, c,
                         {"spot_check_reason": reason_text.strip()})
            _sync_item(item, status, c, force=True)
            _follow_ups(doc, dec, status, f"{doc['filename']}: spot check failed, back to review ({reason_text.strip()})", c)
        _close_reviews(doc["id"], user, reason_text, c, kind="doc.spot_check")
        audit.record_audit(f"document.spot_check_{verdict}", "document", doc["id"], f"Spot check {verdict}: '{doc['filename']}'",
                           reason=reason_text, conn=c)
    return store.get(doc["id"])


def _decide_instead(item: dict, action: str, user: dict, reason: str) -> str:
    raise DocError(409, "Document reviews close with a decision on the document (POST /api/documents/{id}/decision), "
                        "or a spot-check verdict; they can't be resolved generically")


work_queue.RESOLVERS["doc.review"] = _decide_instead
work_queue.RESOLVERS["doc.spot_check"] = _decide_instead


# ---------- outbox handlers ----------

@jobs.handler("doc.after_decision")
def after_decision_job(job: dict) -> None:
    import checklist
    from graph import event
    p = job["payload"]
    records.project(p["employee_id"])
    event(p["employee_id"], "verification", p["line"])
    checklist.recompute(p["employee_id"])
    from integrations.engine import on_documents_changed
    on_documents_changed(p["employee_id"])


@jobs.handler("notify.correction")
async def correction_job(job: dict) -> None:
    # ponytail: keyed per decision, so a retry can't queue a second email; a crash between Gmail accepting the send and
    # this job finishing can still resend once. Phase 4 moves email onto the outbox with a sent-marker.
    import gmail
    if not gmail.configured():
        return
    doc = store.get(job["payload"]["document_id"])
    emp = records.employee(doc["employee_id"])
    item = records.item(doc["item_id"]) if doc.get("item_id") else None
    await gmail.send_correction(emp, item or {"name": doc["filename"]}, doc.get("issues") or ["Please re-submit a clearer, valid copy."])

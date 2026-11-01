"""Pure document routing: the AI perceives, policy decides (ADR 0001, ADR 0012).

`decide()` turns the deterministic check results plus the governed policy (tier, threshold) into a route. It never
looks at anything the model *says* about acceptability, only at its extracted facts and confidence.
No I/O here; test_phase2 calls it directly, and the policy backtest replays stored history through it.
"""
import copy
import hashlib
from dataclasses import dataclass

# Legal floor. A policy edit can't lower these to "low"; changing this set needs a code review and an ADR.
ALWAYS_HIGH = frozenset({"photo_id", "i9_documents", "visa", "work_permit", "bgv_report"})

DEFAULT_DOC_POLICY = {
    "default": {"tier": "low", "auto_accept_min": 0.9},
    "types": {"photo_id": {"tier": "high"}, "i9_documents": {"tier": "high"}, "bank_details": {"auto_accept_min": 0.92}},
    "spot_check_rate": 0.10,
    "extraction_deadline_minutes": 30,
}

REASON_LABEL = {
    "file_check": "File problem", "content_issues": "Content issues", "ai_unavailable": "AI unavailable",
    "unexpected_type": "Unexpected type", "high_risk": "High risk", "low_confidence": "Low confidence",
    "auto_accept": "Auto-accepted", "spot_check_failed": "Spot check failed",
}


def policy_for(config: dict | None) -> dict:
    """The effective document policy of a rule-set version: defaults, overlaid by its `doc_policy`, with the floor applied."""
    p = copy.deepcopy(DEFAULT_DOC_POLICY)
    given = (config or {}).get("doc_policy") or {}
    p["default"].update(given.get("default") or {})
    for t, v in (given.get("types") or {}).items():
        p["types"].setdefault(t, {}).update(v or {})
    for k in ("spot_check_rate", "extraction_deadline_minutes"):
        if k in given:
            p[k] = given[k]
    for t in ALWAYS_HIGH & set(p["types"]):
        p["types"][t]["tier"] = "high"
    return p


def tier_for(doc_type: str, policy: dict) -> tuple[str, float]:
    """(tier, auto_accept_min) for a type. Types on the legal floor are always high."""
    t = {**policy["default"], **policy["types"].get(doc_type, {})}
    return ("high" if doc_type in ALWAYS_HIGH else t["tier"]), float(t["auto_accept_min"])


def sampled(doc_id: str, rate: float) -> bool:
    """Reproducible spot-check sample: an auditor can recompute which documents should have been checked."""
    return int(hashlib.sha256(doc_id.encode()).hexdigest()[:8], 16) / 2 ** 32 < rate


@dataclass(frozen=True)
class Route:
    status: str        # document status this decision produces
    kind: str          # doc_decisions.kind
    reason_code: str
    review: bool       # opens a doc.review work item
    spot_check: bool = False


def decide(*, file_issues: list, issues: list, extraction_ok: bool, on_checklist: bool, tier: str,
           confidence: float, threshold: float, doc_id: str = "", spot_check_rate: float = 0.0) -> Route:
    """Evaluated in order; the first matching row wins. Only the last row accepts, and never for tier 'high'."""
    if file_issues:
        return Route("Correction Requested", "auto_correction", "file_check", False)
    if not extraction_ok:
        return Route("Under Review", "routed_review", "ai_unavailable", True)
    if issues:
        return Route("Correction Requested", "auto_correction", "content_issues", False)
    if not on_checklist:
        return Route("Under Review", "routed_review", "unexpected_type", True)
    if tier == "high":
        return Route("Under Review", "routed_review", "high_risk", True)
    if confidence < threshold:
        return Route("Under Review", "routed_review", "low_confidence", True)
    return Route("Accepted", "auto_accept", "auto_accept", False, sampled(doc_id, spot_check_rate))


def backtest(decisions: list[dict], policy: dict) -> dict:
    """Replay stored automatic decisions under another policy. Pure: never touches a live document.
    Each decision needs: doc_type, kind, reason_code, confidence, and optionally spot_check ('pass'/'fail')."""
    out = {"decisions": 0, "changed": 0, "newly_auto_accepted": 0, "newly_reviewed": 0,
           "newly_auto_accepted_spot_failed": 0, "by_type": {}}
    for d in decisions:
        if d["kind"] not in ("auto_accept", "routed_review") or d["reason_code"] not in ("auto_accept", "high_risk", "low_confidence"):
            continue  # only threshold/tier outcomes can change; file, content, AI-down and checklist routes can't
        tier, threshold = tier_for(d["doc_type"], policy)
        r = decide(file_issues=[], issues=[], extraction_ok=True, on_checklist=True, tier=tier,
                   confidence=float(d.get("confidence") or 0), threshold=threshold)
        out["decisions"] += 1
        was_auto = d["kind"] == "auto_accept"
        now_auto = r.kind == "auto_accept"
        if was_auto == now_auto:
            continue
        out["changed"] += 1
        bt = out["by_type"].setdefault(d["doc_type"], {"newly_auto_accepted": 0, "newly_reviewed": 0})
        key = "newly_auto_accepted" if now_auto else "newly_reviewed"
        out[key] += 1
        bt[key] += 1
        if now_auto and d.get("spot_check") == "fail":
            out["newly_auto_accepted_spot_failed"] += 1
    return out


if __name__ == "__main__":
    pol = policy_for({"doc_policy": {"types": {"photo_id": {"tier": "low"}}}})
    assert tier_for("photo_id", pol)[0] == "high", "the legal floor wins over the policy"
    kw = dict(file_issues=[], issues=[], extraction_ok=True, on_checklist=True)
    assert decide(**kw, tier="high", confidence=1.0, threshold=0.9).status == "Under Review"
    assert decide(**kw, tier="low", confidence=0.95, threshold=0.9).status == "Accepted"
    assert decide(**kw, tier="low", confidence=0.89, threshold=0.9).reason_code == "low_confidence"
    print("route: ok")

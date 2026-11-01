"""The single exceptions inbox. Anything that needs a human (a dead job now; doc reviews, rule approvals,
near-start changes and appeals in later phases) opens one item here, assigned to a role."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared import db

router = APIRouter(prefix="/api/work-queue")
# kind -> resolver(item, action, user, reason) -> final status. Registered by the module that owns the kind.
RESOLVERS: dict[str, callable] = {}

# Every kind of exception is a contract, not a generic inbox entry: who owns it, by when, who it escalates to, and
# what resolving it means. open_item() refuses kinds that aren't registered here (also checked by
# scripts/check_boundaries.py). Escalation is *declared* now; the Phase 6 ladder / Temporal acts on it.
KINDS: dict[str, dict] = {
    "ops": {"sla": timedelta(days=1), "escalate_to": "admin",
            "steps": "Read the job error, fix the cause (config, credentials, an upstream outage), then retry or discard the job."},
    "policy.approval": {"sla": timedelta(days=2), "escalate_to": "admin",
                        "steps": "Review the rule diff and its impact preview, then approve or reject with a reason. "
                                 "Compliance changes need a second approver."},
    "doc.review": {"sla": "caller", "escalate_to": "admin",
                   "steps": "Compare the document with the extracted fields and the profile, then accept, request a "
                            "correction or reject, with a reason. High-risk documents need an approver."},
    "doc.spot_check": {"sla": timedelta(days=5), "escalate_to": "admin",
                       "steps": "Check an automatically accepted document against the original: mark it correct, or wrong "
                                "with a reason (it goes back to review and counts against AI precision)."},
    "policy.near_start": {"sla": "caller", "escalate_to": "admin",
                          "steps": "Check whether the joiner can still meet the new requirements before day one, then apply "
                                   "the change or skip it, with a reason."},
}


class UnknownKind(ValueError):
    pass


def open_item(kind: str, entity_type: str, entity_id: str, reason: str, assignee_role: str,
              detail: dict | None = None, dedupe_key: str | None = None, sla_due=None, conn=None) -> int | None:
    """Open an exception. A repeat with the same dedupe_key is ignored (one open item per cause)."""
    spec = KINDS.get(kind)
    if spec is None:
        raise UnknownKind(f"work_queue kind '{kind}' isn't registered in work_queue.KINDS")
    if sla_due is None:
        if spec["sla"] == "caller":
            raise ValueError(f"work_queue kind '{kind}' needs an explicit sla_due")
        sla_due = datetime.now(timezone.utc) + spec["sla"]
    row = db.fetch1("""INSERT INTO work_queue (kind, entity_type, entity_id, reason, detail, assignee_role, dedupe_key, sla_due)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
                    (kind, entity_type, entity_id, reason, db.Json(detail or {}), assignee_role, dedupe_key, sla_due),
                    conn=conn)
    return row["id"] if row else None


TAKEOVER_AFTER = timedelta(minutes=30)
OPEN = ("open", "in_progress")
_SELECT = """SELECT w.*, u.email AS assignee FROM work_queue w LEFT JOIN users u ON u.id = w.assignee_id"""


def serialize(r: dict) -> dict:
    """The one shape every API returns for a work item; the frontend reads only this."""
    spec = KINDS.get(r["kind"], {})
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "reason": r["reason"], "sla_due": r["sla_due"],
            "escalate_to": spec.get("escalate_to"), "steps": spec.get("steps", ""), "assignee": r.get("assignee"),
            "assignee_id": r.get("assignee_id"), "assignee_role": r["assignee_role"], "claimed_at": r.get("claimed_at"),
            "entity_type": r["entity_type"], "entity_id": r["entity_id"], "detail": r.get("detail") or {},
            "created_at": r["created_at"], "resolution": r.get("resolution"), "resolved_at": r.get("resolved_at")}


def get(item_id: int, conn=None) -> dict | None:
    r = db.fetch1(_SELECT + " WHERE w.id=%s", (item_id,), conn=conn)
    return serialize(r) if r else None


def list_items(role: str | None = None, status: str = "open", kind: str | list[str] = "", assignee: str = "all",
               user_id: int | None = None, reason_code: str = "", since=None) -> list[dict]:
    """Open means open or claimed (in progress). `role=None` lists every role's items (read-only screens)."""
    where, params = [], []
    if status == "open":
        where.append("w.status IN ('open','in_progress')")
    else:
        where.append("w.status = %s")
        params.append(status)
    if role and role not in ("admin", "auditor"):
        where.append("w.assignee_role = %s")
        params.append(role)
    if kind:
        where.append("w.kind = ANY(%s)")
        params.append([kind] if isinstance(kind, str) else list(kind))
    if assignee == "me":
        where.append("w.assignee_id = %s")
        params.append(user_id)
    elif assignee == "unassigned":
        where.append("w.assignee_id IS NULL")
    if reason_code:
        where.append("w.detail->>'reason_code' = %s")
        params.append(reason_code)
    if since:
        where.append("coalesce(w.resolved_at, w.created_at) >= %s")
        params.append(since)
    order = "w.sla_due NULLS LAST, w.created_at" if status == "open" else "w.resolved_at DESC NULLS LAST"
    return [serialize(r) for r in db.fetch(f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY {order}", params)]


def claim(item_id: int, user: dict, takeover: bool = False) -> dict:
    """Assign an open item to `user`. Someone else's claim can be taken over only after TAKEOVER_AFTER."""
    with db.tx() as c:
        it = db.fetch1(_SELECT + " WHERE w.id=%s FOR UPDATE OF w", (item_id,), conn=c)
        if not it:
            raise HTTPException(404, "Not found")
        if it["status"] not in OPEN:
            raise HTTPException(409, "Already closed")
        mine = it["assignee_id"] == user["id"]
        stale = it["claimed_at"] and it["claimed_at"] < datetime.now(timezone.utc) - TAKEOVER_AFTER
        if it["assignee_id"] and not mine and not (takeover and stale):
            raise HTTPException(409, f"Being reviewed by {it['assignee']} (claimed {it['claimed_at']:%H:%M})")
        if not mine:
            c.execute("UPDATE work_queue SET assignee_id=%s, claimed_at=now(), status='in_progress' WHERE id=%s",
                      (user["id"], item_id))
            import audit
            audit.record_audit("work_queue.claimed", "work_queue", str(item_id), f"Claimed '{it['reason']}'"
                               + (f" (took over from {it['assignee']})" if it["assignee_id"] else ""), conn=c)
    return get(item_id)


@router.get("")
def get_items(status: str = "open", kind: str = ""):
    from identity.service import current_user
    return list_items(current_user.get()["role"], status, kind)


class Resolve(BaseModel):
    resolution: str = Field(min_length=3, max_length=2000)
    dismiss: bool = False
    action: str | None = None  # kind-specific, e.g. 'apply' | 'skip' for policy.near_start


@router.post("/{item_id}/resolve")
def resolve(item_id: int, body: Resolve):
    from identity.service import current_user
    import audit
    user = current_user.get()
    with db.tx() as c:
        it = db.fetch1("SELECT * FROM work_queue WHERE id=%s FOR UPDATE", (item_id,), conn=c)
        if not it:
            raise HTTPException(404, "Not found")
        if user["role"] != "admin" and it["assignee_role"] != user["role"]:
            raise HTTPException(403, "This item is assigned to another role")
        if it["status"] in ("resolved", "dismissed"):
            raise HTTPException(409, "Already closed")
        status = "dismissed" if body.dismiss else "resolved"
        if it["kind"] in RESOLVERS:
            try:
                status = RESOLVERS[it["kind"]](it, body.action or "", user, body.resolution)
            except Exception as e:  # PolicyError / DocError carry an HTTP status
                if getattr(e, "status", None):
                    raise HTTPException(e.status, str(e))
                raise
        it = db.fetch1("""UPDATE work_queue SET status=%s, resolution=%s, resolved_by=%s, resolved_at=now()
                          WHERE id=%s RETURNING *""", (status, body.resolution, user["id"], item_id), conn=c)
        audit.record_audit(f"work_queue.{status}", "work_queue", str(item_id), f"{status.title()} '{it['reason']}'",
                           reason=body.resolution, conn=c)
    return get(item_id)

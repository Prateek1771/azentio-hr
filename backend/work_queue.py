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


def list_items(role: str, status: str = "open", kind: str = "") -> list[dict]:
    see_all = role in ("admin", "auditor")
    where, params = ["status = %s"], [status]
    if not see_all:
        where.append("assignee_role = %s")
        params.append(role)
    if kind:
        where.append("kind = %s")
        params.append(kind)
    rows = db.fetch(f"SELECT * FROM work_queue WHERE {' AND '.join(where)} ORDER BY sla_due NULLS LAST, created_at", params)
    return [{**r, "escalate_to": KINDS.get(r["kind"], {}).get("escalate_to"), "steps": KINDS.get(r["kind"], {}).get("steps", "")}
            for r in rows]


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
            from policy.store import PolicyError
            try:
                status = RESOLVERS[it["kind"]](it, body.action or "", user, body.resolution)
            except PolicyError as e:
                raise HTTPException(e.status, str(e))
        it = db.fetch1("""UPDATE work_queue SET status=%s, resolution=%s, resolved_by=%s, resolved_at=now()
                          WHERE id=%s RETURNING *""", (status, body.resolution, user["id"], item_id), conn=c)
        audit.record_audit(f"work_queue.{status}", "work_queue", str(item_id), f"{status.title()} '{it['reason']}'",
                           reason=body.resolution, conn=c)
    return it

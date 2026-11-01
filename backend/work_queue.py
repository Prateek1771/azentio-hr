"""The single exceptions inbox. Anything that needs a human (a dead job now; doc reviews, rule approvals,
near-start changes and appeals in later phases) opens one item here, assigned to a role."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared import db

router = APIRouter(prefix="/api/work-queue")
# kind -> resolver(item, action, user, reason) -> final status. Registered by the module that owns the kind.
RESOLVERS: dict[str, callable] = {}


def open_item(kind: str, entity_type: str, entity_id: str, reason: str, assignee_role: str,
              detail: dict | None = None, dedupe_key: str | None = None, sla_due=None, conn=None) -> int | None:
    """Open an exception. A repeat with the same dedupe_key is ignored (one open item per cause)."""
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
    return db.fetch(f"SELECT * FROM work_queue WHERE {' AND '.join(where)} ORDER BY sla_due NULLS LAST, created_at", params)


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

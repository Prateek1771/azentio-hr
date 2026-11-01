"""Audit log entry points used across the app. Storage is the hash-chained Postgres log in audit_log/service.py.

The actor comes from the signed-in user of the current request. Callers that pass the legacy default "HR"
get the real user automatically; outside a request (jobs, scheduler, poller) the actor is "system".
Explicit actors such as "Agent" are kept as given.
"""
from audit_log import service as _svc
import logging_setup as ls

log = ls.get("audit")


def _actor(actor: str) -> tuple[str, str]:
    """'HR' -> the signed-in user; 'HR (via agent)' -> 'user@x (via agent)'; anything else is kept."""
    if actor != "HR" and not actor.startswith("HR "):
        return actor, ""
    from identity.service import current_user
    u = current_user.get()
    who, role = (u["email"], u["role"]) if u else ("system", "")
    return who + actor[2:], role


def record_audit(action: str, entity_type: str, entity_id: str | None, summary: str,
                 actor: str = "HR", metadata: dict | None = None, reason: str = "", conn=None,
                 actor_role: str = "") -> None:
    who, role = _actor(actor)
    role = actor_role or role
    _svc.record(action, entity_type, entity_id, summary, actor=who, actor_role=role, metadata=metadata,
                reason=reason, conn=conn)
    ls.log_event(log, "audit", action=action, entity=entity_type, id=entity_id, actor=who)


def list_audit(page: int = 1, page_size: int = 50, action: str = "", entity_type: str = "", q_: str = "") -> dict:
    return _svc.list_entries(page, page_size, action, entity_type, q_)

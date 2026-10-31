"""Audit log: who did what. Append-only and never affected by integration log exclusions.

ponytail: the app has no sign-in, so the actor is "HR" for people and "Agent"/"System" for automation.
Pass a real user id once authentication exists.
"""
import json
import time

from graph import q, new_id
import logging_setup as ls

log = ls.get("audit")


def record_audit(action: str, entity_type: str, entity_id: str | None, summary: str,
                 actor: str = "HR", metadata: dict | None = None) -> None:
    q("CREATE (a:AuditLog) SET a = $p", p={
        "id": new_id("aud"), "action": action, "entity_type": entity_type, "entity_id": entity_id or "",
        "summary": summary, "actor": actor, "metadata": json.dumps(metadata or {}), "ts": time.time()})
    ls.log_event(log, "audit", action=action, entity=entity_type, id=entity_id, actor=actor)


def list_audit(page: int = 1, page_size: int = 50, action: str = "", entity_type: str = "", q_: str = "") -> dict:
    rows = [r["a"] for r in q("MATCH (a:AuditLog) RETURN a ORDER BY a.ts DESC")]
    if action:
        rows = [r for r in rows if r["action"].startswith(action)]
    if entity_type:
        rows = [r for r in rows if r["entity_type"] == entity_type]
    if q_:
        rows = [r for r in rows if q_.lower() in r["summary"].lower()]
    start = (max(page, 1) - 1) * page_size
    items = [{**r, "metadata": json.loads(r.get("metadata") or "{}")} for r in rows[start:start + page_size]]
    return {"items": items, "total": len(rows), "page": page, "page_size": page_size,
            "actions": sorted({r["action"] for r in rows})}

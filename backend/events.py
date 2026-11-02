"""Activity events: the timeline of what happened to each joiner (Phase 3 batch B). The only reader and writer.

`graph.event()` stays the choke point its callers use; it calls `record` here. Postgres is the record; the graph no
longer holds events.
"""
import time

import graph_migration as gm
from shared import db

COLS = ("id", "employee_id", "kind", "message", "ts")


def record(employee_id: str | None, kind: str, message: str) -> str:
    from graph import new_id
    eid = new_id("ev")
    db.execute("INSERT INTO events (id, employee_id, kind, message, ts) VALUES (%s, %s, %s, %s, %s)",
               (eid, employee_id or "", kind, message, time.time()))
    return eid


def for_employee(eid: str, limit: int = 100) -> list[dict]:
    return db.fetch("SELECT * FROM events WHERE employee_id=%s ORDER BY ts DESC LIMIT %s", (eid, limit))


def recent(limit: int = 50, exclude_kind: str | None = None) -> list[dict]:
    """Newest first, with the joiner's name and manager (for manager scoping) when the event belongs to one."""
    return db.fetch("""SELECT v.*, e.name AS employee_name, e.manager_email
                       FROM events v LEFT JOIN employees e ON e.id = v.employee_id AND v.employee_id <> ''
                       WHERE %(x)s::text IS NULL OR v.kind <> %(x)s
                       ORDER BY v.ts DESC LIMIT %(l)s""", {"x": exclude_kind, "l": limit})


def _canon(r: dict) -> dict:
    return {"id": r["id"], "employee_id": r.get("employee_id") or "", "kind": r.get("kind") or "",
            "message": r.get("message") or "", "ts": gm.epoch(r.get("ts"))}


gm.register(gm.LabelSpec(
    label="Event", batch="B", table="events",
    to_row=lambda p: {**_canon(p), "ts": float(p.get("ts") or 0)}, canonical=_canon,
    from_row=lambda r: {**_canon(r), "ts": r["ts"]},
    mapped_keys=frozenset(COLS)))

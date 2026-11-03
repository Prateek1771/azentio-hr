"""The graph as a read model (Phase 3 batch E): its state, the full rebuild, drift repair and the one-time cleanup.

FalkorDB holds only the projection (Employee, Item, Document, Owner) and the policy retrieval index (PolicyChunk);
Postgres is the record. A lost or drifted graph is rebuilt from Postgres. While a full rebuild runs, graph-backed views
say "rebuilding" (`Rebuilding`) rather than serve a partial projection.

CLI: python -m read_model status | verify | rebuild | cleanup
"""
import asyncio
import json
import logging_setup as ls
from graph import READ_MODEL
from shared import db, jobs

_log = ls.get("read_model")


REBUILD_LOCK = 0x72656264  # pg advisory lock: one full rebuild at a time, across processes (CLI, job, instances)


class RebuildBusy(RuntimeError):
    """Another full rebuild holds the lock; this one stands down (that one sets the state when it finishes)."""


class Rebuilding(RuntimeError):
    """The graph is being rebuilt from Postgres; graph-backed views answer 503 / 'try again in a moment'."""


def state() -> dict:
    return db.fetch1("SELECT * FROM graph_state WHERE id = 1") or {"state": "ready", "cleaned_at": None}


def rebuilding() -> bool:
    return state()["state"] == "rebuilding"


def require_ready() -> None:
    if rebuilding():
        raise Rebuilding("The relationship view is rebuilding; try again in a moment")


# ---------- status ----------

def _graph_counts() -> dict[str, int]:
    from graph import q
    return {r["l"]: r["n"] for r in q("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n")}


def expected_counts() -> dict[str, int]:
    """What the projection should hold, from Postgres."""
    one = lambda sql: db.fetch1(sql)["n"]  # noqa: E731
    return {"Employee": one("SELECT count(*) AS n FROM employees"),
            "Item": one("SELECT count(*) AS n FROM items i JOIN employees e ON e.id = i.employee_id"),
            "Owner": one("SELECT count(*) AS n FROM owners"),
            "Document": one("SELECT count(*) AS n FROM documents d JOIN employees e ON e.id = d.employee_id")}


def status() -> dict:
    """The read model's health: state, per-label counts against Postgres, unexpected labels (after the cleanup)."""
    st = state()
    counts = _graph_counts()
    want = expected_counts()
    from graph import q
    sources = q("MATCH (c:PolicyChunk) RETURN count(DISTINCT c.source) AS n")[0]["n"]
    want_sources = db.fetch1("SELECT count(*) AS n FROM policies")["n"]
    mismatched = {k: {"graph": counts.get(k, 0), "postgres": v} for k, v in want.items() if counts.get(k, 0) != v}
    if sources != want_sources:
        mismatched["PolicyChunk sources"] = {"graph": sources, "postgres": want_sources}
    unexpected = sorted(set(counts) - READ_MODEL) if st.get("cleaned_at") else []
    lost = want["Employee"] > 0 and counts.get("Employee", 0) == 0
    return {"state": st["state"], "counts": counts, "mismatched": mismatched, "unexpected_labels": unexpected,
            "lost": lost, "cleaned_at": st["cleaned_at"].isoformat() if st.get("cleaned_at") else None,
            "ok": st["state"] == "ready" and not mismatched and not unexpected}


_cached: tuple[float, str] = (0.0, "")


def summary_cached(ttl: float = 30.0) -> str:
    """summary() for /ready, recomputed at most every `ttl` seconds: it scans every graph node."""
    global _cached
    import time
    if time.monotonic() - _cached[0] > ttl:
        _cached = (time.monotonic(), summary())
    return _cached[1]


def summary(st: dict | None = None) -> str:
    """For /ready: ok | rebuilding | drift (...)."""
    st = st or status()
    if st["state"] == "rebuilding":
        return "rebuilding"
    if st["unexpected_labels"]:
        return f"drift (unexpected labels: {', '.join(st['unexpected_labels'])})"
    if st["mismatched"]:
        return f"drift ({', '.join(st['mismatched'])})"
    return "ok"


# ---------- the full rebuild ----------

def _set(state_: str, reason: str = "", report: dict | None = None, conn=None) -> None:
    if state_ == "rebuilding":
        db.execute("UPDATE graph_state SET state='rebuilding', reason=%s, started_at=now(), finished_at=NULL WHERE id=1",
                   (reason,), conn=conn)
    else:
        db.execute("UPDATE graph_state SET state='ready', finished_at=now(), report=%s WHERE id=1",
                   (db.Json(report or {}),), conn=conn)


async def rebuild(reason: str = "") -> dict:
    """The full rebuild, holding REBUILD_LOCK so a CLI cleanup, a job and a second instance never interleave wipes."""
    with db.pool().connection() as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(%s) AS ok", (REBUILD_LOCK,)).fetchone()["ok"]:
            raise RebuildBusy("another full rebuild of the graph is running")
        try:
            return await _rebuild(reason)
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (REBUILD_LOCK,))


async def _rebuild(reason: str = "") -> dict:
    """Wipe the graph and project everything again from Postgres: owners, every employee with items and documents,
    then the policy index. Retired labels and anything else outside the read model disappear with the wipe.
    ponytail: a request that edits an employee while this runs can have its projection overwritten by this pass's
    older read of that employee; the hourly verify (item counts) repairs the common case. Lock per employee if that
    window ever matters."""
    import chat
    import records
    from graph import ensure_indexes, q
    from shared import bridge
    await asyncio.to_thread(bridge.load)  # which store owns documents: a CLI process hasn't loaded it (it skipped them)
    await asyncio.to_thread(_set, "rebuilding", reason)
    await asyncio.to_thread(q, "MATCH (n) DETACH DELETE n")
    await asyncio.to_thread(ensure_indexes)
    n = await asyncio.to_thread(records.rebuild_graph)
    chunks = await chat.reindex_policies()
    report = {"reason": reason, "employees": n, "policies": chunks, "counts": await asyncio.to_thread(_graph_counts)}
    await asyncio.to_thread(_set, "ready", report=report)
    ls.log_event(_log, "graph.rebuilt", reason=reason, employees=n, policies=chunks)
    return report


@jobs.handler("graph.rebuild")
async def _rebuild_job(job: dict) -> None:
    try:
        await rebuild(job["payload"].get("reason", ""))
    except RebuildBusy:  # the running rebuild sets 'ready' when it's done
        ls.log_event(_log, "graph.rebuild_skipped", reason="another rebuild is running")


def request_rebuild(reason: str) -> bool:
    """Mark the graph as rebuilding and queue the rebuild, once. False if a rebuild job is already queued or running.
    Keyed on a live job, not on the state: a rebuild whose job died (and opened its `ops` item) is queued again by the
    next call (verify, a restart), instead of leaving the graph 'rebuilding' with nothing running."""
    from graph import new_id
    with db.tx() as conn:
        db.fetch1("SELECT state FROM graph_state WHERE id=1 FOR UPDATE", conn=conn)  # one requester at a time
        if db.fetch1("SELECT 1 AS x FROM jobs WHERE kind='graph.rebuild' AND state IN ('queued', 'running') LIMIT 1",
                     conn=conn):
            return False
        _set("rebuilding", reason, conn=conn)
        jobs.enqueue("graph.rebuild", {"reason": reason}, idempotency_key=f"graph.rebuild:{new_id('r')}", conn=conn)
    ls.log_event(_log, "graph.rebuild_requested", reason=reason)
    return True


# ---------- drift repair ----------

def drifting_employees() -> set[str]:
    """Employees whose projection differs from Postgres: missing node, or a different number of items."""
    from graph import q
    pg = {r["id"]: r["n"] for r in db.fetch("""SELECT e.id, count(i.id) AS n FROM employees e
                                               LEFT JOIN items i ON i.employee_id = e.id GROUP BY e.id""")}
    g = {r["e"]: r["n"] for r in q("MATCH (e:Employee) OPTIONAL MATCH (e)-[:HAS_ITEM]->(i:Item) RETURN e.id AS e, count(i) AS n")}
    return {e for e in pg.keys() | g.keys() if pg.get(e) != g.get(e)}


def verify() -> dict:
    """Hourly: a lost graph or unexpected labels -> full rebuild; per-employee drift -> re-project those employees;
    drift that survives the repair -> one `ops` work item (deduped)."""
    import records
    import work_queue
    import chat
    st = status()
    if st["state"] == "rebuilding":
        if _rebuild_died():  # it already failed max_attempts times and opened an ops item: a person takes it from here
            return {"action": "none", "reason": "the rebuild failed; see the ops item (restart or `read_model rebuild`)"}
        queued = request_rebuild("the previous rebuild didn't finish")  # e.g. a CLI rebuild that crashed; no-op if live
        return {"action": "rebuild" if queued else "none", "reason": "rebuilding"}
    if st["lost"] or st["unexpected_labels"]:
        why = "graph lost" if st["lost"] else f"unexpected labels: {', '.join(st['unexpected_labels'])}"
        request_rebuild(why)
        return {"action": "rebuild", "reason": why}
    affected = drifting_employees()
    for eid in sorted(affected):
        try:
            records.rebuild_graph(eid)
        except Exception as e:  # keep going: what's left is reported below
            ls.log_event(_log, "graph.verify_repair_failed", employee=eid, error=type(e).__name__)
    still = drifting_employees() if affected else set()
    counts = status()["mismatched"]  # owners, documents or the policy index can drift without any employee's items
    if {"Owner", "Document"} & set(counts):
        records.rebuild_graph()  # every employee in place, owners first (no wipe)
    if "PolicyChunk sources" in counts:
        asyncio.run(chat.reindex_policies())
    left = status()["mismatched"] if counts else {}
    if still or left:
        work_queue.open_item("ops", "graph", "projection",
                             "The relationship view still differs from the records after an automatic repair "
                             f"({len(still)} employee(s){', ' + ', '.join(left) if left else ''})", "admin",
                             detail={"employees": sorted(still)[:50], "counts": left}, dedupe_key="graph.drift")
    ls.log_event(_log, "graph.verified", repaired=len(affected) - len(still), persistent=len(still), counts=len(left))
    return {"action": "repair" if affected or counts else "none", "repaired": sorted(affected - still),
            "persistent": sorted(still), "counts_repaired": sorted(set(counts) - set(left)), "counts_left": left}


def _rebuild_died() -> bool:
    """A graph.rebuild job went dead after the current rebuild was requested."""
    return bool(db.fetch1("""SELECT 1 AS x FROM jobs j, graph_state g WHERE g.id = 1 AND j.kind = 'graph.rebuild'
                             AND j.state = 'dead' AND j.finished_at >= g.started_at LIMIT 1"""))


# ---------- the one-time cleanup ----------

def cleanup() -> dict:
    """Remove every retired label from the graph: refuses unless every migrated label is on Postgres and the legacy
    AuditLog history is already in audit_log. Take a BGSAVE first (the runbook); it stays the rollback point."""
    import graph_migration as gm
    from graph import q
    pending = sorted(s.label for s in gm.load().values() if (gm._state(s.label) or {}).get("source") != "postgres")
    if pending:
        raise RuntimeError(f"cleanup refused: {', '.join(pending)} still read from the graph; cut them over first")
    # every legacy AuditLog node must be in audit_log (import_graph_rows), not merely "audit_log has rows": the import
    # runs only on a first boot with the graph up, and logins write audit rows regardless.
    missing = [a for a in q("MATCH (a:AuditLog) RETURN a.action AS action, a.entity_id AS e, a.ts AS ts")
               if not db.fetch1("""SELECT 1 AS x FROM audit_log WHERE action = %s AND entity_id = %s
                                   AND abs(extract(epoch FROM ts) - %s) < 0.001 LIMIT 1""",
                                (a["action"] or "", a["e"] or "", float(a["ts"] or 0)))]
    if missing:
        raise RuntimeError(f"cleanup refused: {len(missing)} legacy AuditLog row(s) in the graph aren't in audit_log "
                           "(e.g. the first boot couldn't reach the graph); import them before removing the graph copy")
    db.execute("UPDATE graph_state SET cleaned_at=now() WHERE id=1")
    report = asyncio.run(rebuild("cleanup"))
    st = status()
    if set(st["counts"]) - READ_MODEL:
        raise RuntimeError(f"cleanup left labels outside the read model: {sorted(set(st['counts']) - READ_MODEL)}")
    return {**report, "status": st}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    out = {"status": status, "verify": verify, "cleanup": cleanup,
           "rebuild": lambda: asyncio.run(rebuild("manual"))}.get(cmd)
    if not out:
        sys.exit("usage: python -m read_model status | verify | rebuild | cleanup")
    print(json.dumps(out(), indent=1, default=str))

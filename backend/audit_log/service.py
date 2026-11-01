"""Hash-chained, append-only audit log in Postgres.

hash = sha256(prev_hash + canonical JSON of the row). Editing, deleting or reordering any row breaks
`verify_chain()`; the table trigger additionally refuses UPDATE/DELETE outright.
"""
import hashlib
import json
from datetime import datetime, timezone

from shared import db

GENESIS = "0" * 64
_LOCK = 7243002  # serialises chain appends so two writers never chain off the same row


def _canonical(r: dict) -> str:
    ts = r["ts"]
    ts = ts.astimezone(timezone.utc).isoformat(timespec="microseconds") if isinstance(ts, datetime) else ts
    return json.dumps({"ts": ts, "actor": r["actor"], "actor_role": r["actor_role"], "action": r["action"],
                       "entity_type": r["entity_type"], "entity_id": r["entity_id"], "summary": r["summary"],
                       "metadata": r["metadata"], "reason": r["reason"]}, sort_keys=True, separators=(",", ":"))


def _hash(prev: str, row: dict) -> str:
    return hashlib.sha256((prev + _canonical(row)).encode()).hexdigest()


def record(action: str, entity_type: str, entity_id: str | None, summary: str, actor: str, actor_role: str = "",
           metadata: dict | None = None, reason: str = "", ts: datetime | None = None, conn=None) -> dict:
    """Append one entry. Pass `conn` to write inside the caller's transaction (the entry commits with the change)."""
    row = {"ts": (ts or datetime.now(timezone.utc)).astimezone(timezone.utc), "actor": actor, "actor_role": actor_role,
           "action": action, "entity_type": entity_type, "entity_id": entity_id or "", "summary": summary,
           "metadata": metadata or {}, "reason": reason}

    def _write(c):
        c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK,))
        last = c.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else GENESIS
        h = _hash(prev, row)
        c.execute("""INSERT INTO audit_log (ts, actor, actor_role, action, entity_type, entity_id, summary, metadata, reason, prev_hash, hash)
                     VALUES (%(ts)s, %(actor)s, %(actor_role)s, %(action)s, %(entity_type)s, %(entity_id)s, %(summary)s,
                             %(metadata)s, %(reason)s, %(prev)s, %(hash)s)""",
                  {**row, "metadata": db.Json(row["metadata"]), "prev": prev, "hash": h})
        return {**row, "prev_hash": prev, "hash": h}

    if conn is not None:
        return _write(conn)
    with db.tx() as c:
        return _write(c)


def verify_chain() -> tuple[bool, int | None]:
    """Walk the whole chain. Returns (ok, first_bad_seq)."""
    prev = GENESIS
    with db.tx() as c:
        cur = c.execute("SELECT * FROM audit_log ORDER BY seq")
        for r in cur:
            if r["prev_hash"] != prev or _hash(prev, r) != r["hash"]:
                return False, r["seq"]
            prev = r["hash"]
    return True, None


def list_entries(page: int = 1, page_size: int = 50, action: str = "", entity_type: str = "", q_: str = "") -> dict:
    where, p = ["true"], {}
    if action:
        where.append("action LIKE %(action)s")
        p["action"] = action + "%"
    if entity_type:
        where.append("entity_type = %(et)s")
        p["et"] = entity_type
    if q_:
        where.append("(summary ILIKE %(q)s OR actor ILIKE %(q)s)")
        p["q"] = f"%{q_}%"
    w = " AND ".join(where)
    page = max(page, 1)
    total = db.fetch1(f"SELECT count(*) AS n FROM audit_log WHERE {w}", p)["n"]
    rows = db.fetch(f"""SELECT seq, ts, actor, actor_role, action, entity_type, entity_id, summary, metadata, reason
                        FROM audit_log WHERE {w} ORDER BY seq DESC LIMIT %(lim)s OFFSET %(off)s""",
                    {**p, "lim": page_size, "off": (page - 1) * page_size})
    actions = [r["action"] for r in db.fetch("SELECT DISTINCT action FROM audit_log ORDER BY action")]
    items = [{**r, "id": f"aud_{r['seq']}", "ts": r["ts"].timestamp()} for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "actions": actions}


def import_graph_rows() -> int:
    """One-off: copy legacy graph AuditLog rows into the chain, oldest first. Only runs while the table is empty."""
    if db.fetch1("SELECT 1 AS x FROM audit_log LIMIT 1"):
        return 0
    try:
        from graph import q
        rows = [r["a"] for r in q("MATCH (a:AuditLog) RETURN a ORDER BY a.ts")]
    except Exception:
        return 0
    with db.tx() as c:
        for a in rows:
            record(a.get("action", ""), a.get("entity_type", ""), a.get("entity_id"), a.get("summary", ""),
                   actor=a.get("actor", "HR"), metadata=json.loads(a.get("metadata") or "{}"),
                   ts=datetime.fromtimestamp(float(a.get("ts", 0)), timezone.utc), conn=c)
    return len(rows)

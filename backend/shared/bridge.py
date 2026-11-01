"""Graph -> Postgres migration bridge (hardening design, section 4).

Rules:
- Every record type is read from exactly ONE source, named here.
- A type may switch to "postgres" only after `parity()` passes (equal row count and checksum). `switch()` enforces it
  and persists the decision with its parity report in `bridge_state`, so the gate runs once, not on every boot.
- Graph writes for a type stop only after every read path has switched (tracked in the phase docs).

Phase 1 deviation (see docs/plans/phase-1-policy-governance.md): Employee and Item reads go straight to Postgres
through records.py. If parity fails at startup the app refuses to start rather than falling back to graph reads,
so there is never a second read path to keep alive.
"""
import hashlib
import json

SOURCE: dict[str, str] = {
    "Employee": "graph",  # Phase 1
    "Item": "graph",      # Phase 1
    "Rule": "graph",      # Phase 1
    "Document": "graph",  # Phase 2: documents.store; switched at startup by bootstrap.migrate_documents
}


class ParityError(Exception):
    pass


def source(record_type: str) -> str:
    return SOURCE[record_type]


def checksum(rows: list[dict], key: str = "id") -> str:
    """Order-independent digest over canonical JSON of each row."""
    canon = sorted(json.dumps(r, sort_keys=True, default=str, separators=(",", ":")) for r in
                   sorted(rows, key=lambda r: str(r.get(key))))
    return hashlib.sha256("\n".join(canon).encode()).hexdigest()


def parity(graph_rows: list[dict], pg_rows: list[dict], key: str = "id") -> dict:
    """Compare the two stores' canonical rows for one record type."""
    g, p = {str(r[key]): r for r in graph_rows}, {str(r[key]): r for r in pg_rows}
    gsha, psha = checksum(graph_rows, key), checksum(pg_rows, key)
    first = next((k for k in sorted(g.keys() | p.keys()) if g.get(k) != p.get(k)), None)
    return {"ok": len(graph_rows) == len(pg_rows) and gsha == psha,
            "graph_count": len(graph_rows), "pg_count": len(pg_rows), "graph_sha": gsha, "pg_sha": psha,
            "first_mismatch_id": first,
            "missing_in_pg": sorted(g.keys() - p.keys())[:20], "extra_in_pg": sorted(p.keys() - g.keys())[:20]}


def switch(record_type: str, graph_rows: list[dict], pg_rows: list[dict], key: str = "id") -> dict:
    """Move reads for a type to Postgres, refusing unless parity passes. Persists when the DB is available."""
    result = parity(graph_rows, pg_rows, key)
    if not result["ok"]:
        raise ParityError(f"{record_type}: parity failed {result}")
    SOURCE[record_type] = "postgres"
    try:
        from shared import db
        db.execute("""INSERT INTO bridge_state (record_type, source, report) VALUES (%s, 'postgres', %s)
                      ON CONFLICT (record_type) DO NOTHING""", (record_type, db.Json(result)))
    except Exception:  # pool not initialised (pure unit checks) or table not migrated yet
        pass
    return result


def load() -> dict:
    """Restore switched sources from bridge_state (called by bootstrap after migrations)."""
    from shared import db
    for r in db.fetch("SELECT record_type, source FROM bridge_state"):
        SOURCE[r["record_type"]] = r["source"]
    return dict(SOURCE)

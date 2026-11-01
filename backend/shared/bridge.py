"""Graph -> Postgres migration bridge (hardening design, section 4).

Rules:
- Every record type is read from exactly ONE source, named here. Modules read through `source()`, never both stores.
- A type may switch to "postgres" only after `parity()` passes (equal row count and checksum). `switch()` enforces it.
- Graph writes for a type stop only after every read path has switched (tracked in the phase docs).
"""
import hashlib
import json

SOURCE: dict[str, str] = {
    "Employee": "graph",  # Phase 1
    "Item": "graph",      # Phase 1
    "Rule": "graph",      # Phase 1
    "Document": "graph",  # Phase 2
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
    g, p = {str(r[key]) for r in graph_rows}, {str(r[key]) for r in pg_rows}
    return {"ok": len(graph_rows) == len(pg_rows) and checksum(graph_rows, key) == checksum(pg_rows, key),
            "graph_count": len(graph_rows), "pg_count": len(pg_rows),
            "missing_in_pg": sorted(g - p)[:20], "extra_in_pg": sorted(p - g)[:20]}


def switch(record_type: str, graph_rows: list[dict], pg_rows: list[dict], key: str = "id") -> dict:
    """Move reads for a type to Postgres, refusing unless parity passes."""
    result = parity(graph_rows, pg_rows, key)
    if not result["ok"]:
        raise ParityError(f"{record_type}: parity failed {result}")
    SOURCE[record_type] = "postgres"
    return result

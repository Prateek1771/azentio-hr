"""FalkorDB access. One graph ('hr') holds everything: people, rules, checklist items, documents, policies."""
import os
import time
import uuid

from falkordb import FalkorDB
from falkordb.node import Node
from falkordb.edge import Edge

_db = FalkorDB.from_url(os.getenv("FALKORDB_URL", "redis://localhost:6379"))
G = _db.select_graph(os.getenv("FALKORDB_GRAPH", "hr"))


def _val(v):
    if isinstance(v, Node):
        return dict(v.properties)
    if isinstance(v, Edge):
        return dict(v.properties)
    if isinstance(v, list):
        return [_val(x) for x in v]
    return v


def q(cypher: str, **params) -> list[dict]:
    """Run Cypher, return rows as dicts (nodes flattened to their properties)."""
    res = G.query(cypher, params)
    cols = [h[1] for h in res.header]
    return [{c: _val(v) for c, v in zip(cols, row)} for row in res.result_set]


def q1(cypher: str, **params) -> dict | None:
    rows = q(cypher, **params)
    return rows[0] if rows else None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def event(employee_id: str | None, kind: str, message: str):
    q("CREATE (:Event {id:$id, employee_id:$eid, kind:$k, message:$m, ts:$ts})",
      id=new_id("ev"), eid=employee_id or "", k=kind, m=message, ts=time.time())


def ensure_indexes():
    for label, prop in [("Employee", "id"), ("Employee", "token"), ("Item", "id"), ("Document", "id"),
                        ("Rule", "id"), ("Owner", "email"), ("Event", "employee_id")]:
        try:
            G.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # already exists
    try:
        G.query("CREATE VECTOR INDEX FOR (c:PolicyChunk) ON (c.embedding) "
                "OPTIONS {dimension:1536, similarityFunction:'cosine'}")
    except Exception:
        pass

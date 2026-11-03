"""FalkorDB access. Since Phase 3 the graph ('hr') is a read model: the projection of employees, items, documents and
owners, plus the policy retrieval index. Postgres is the record. `q()` refuses any write naming another label."""
import os
import re
import uuid
from contextlib import contextmanager

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


READ_MODEL = frozenset({"Employee", "Item", "Document", "Owner", "PolicyChunk"})
_WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", re.I)
_INDEX = re.compile(r"^\s*CREATE\s+(VECTOR\s+)?INDEX\b", re.I)
_NODE_LABELS = re.compile(r"\(\s*\w*\s*((?::\s*\w+)+)")              # (n:A:B  (:A
_TEST_LABEL = re.compile(r"\b(?i:WHERE|AND|OR|XOR|NOT)\s+\(?\s*\w+:(\w+)")  # WHERE n:A, OR n:B
_SET_LABEL = re.compile(r"\b(?i:SET|REMOVE)\s+\w+((?::\s*\w+)+)")          # SET n:A, REMOVE n:A:B
ALLOW_LEGACY_WRITES = False


class ReadModelViolation(RuntimeError):
    """A write to a graph label outside the read model: that record lives in Postgres now."""


def labels_in(cypher: str) -> set[str]:
    """Labels a query names: node patterns, label tests and label assignments (backtick quoting removed).
    ponytail: a guard against reintroducing a retired label, not against hostile Cypher. A label compared as a string
    (`'Event' IN labels(n)`) or a write that names no label at all isn't seen; guard rule 8 and review cover code."""
    cypher = cypher.replace("`", "")
    found = {lbl for rx in (_NODE_LABELS, _SET_LABEL) for m in rx.finditer(cypher) for lbl in re.findall(r"\w+", m.group(1))}
    return found | set(_TEST_LABEL.findall(cypher))


def check_write(cypher: str) -> None:
    if ALLOW_LEGACY_WRITES or not _WRITE.search(cypher) or _INDEX.match(cypher):
        return
    bad = labels_in(cypher) - READ_MODEL
    if bad:
        raise ReadModelViolation(f"the graph is a read model; refusing a write that names {', '.join(sorted(bad))} "
                                 "(that record lives in Postgres)")


@contextmanager
def legacy_writes():
    """For the few writers of retired labels: the rollback tool, the batch C secret scrub, old-release test setups."""
    global ALLOW_LEGACY_WRITES
    before, ALLOW_LEGACY_WRITES = ALLOW_LEGACY_WRITES, True
    try:
        yield
    finally:
        ALLOW_LEGACY_WRITES = before


def q(cypher: str, **params) -> list[dict]:
    """Run Cypher, return rows as dicts (nodes flattened to their properties). Writes outside the read model raise."""
    check_write(cypher)
    res = G.query(cypher, params)
    cols = [h[1] for h in res.header]
    return [{c: _val(v) for c, v in zip(cols, row)} for row in res.result_set]


def q1(cypher: str, **params) -> dict | None:
    rows = q(cypher, **params)
    return rows[0] if rows else None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def event(employee_id: str | None, kind: str, message: str):
    """The activity choke point its callers use. Events live in Postgres (Phase 3 batch B): `events.record`."""
    import events
    events.record(employee_id, kind, message)


def ensure_indexes():
    for label, prop in [("Employee", "id"), ("Employee", "token"), ("Item", "id"), ("Document", "id"),
                        ("Owner", "email")]:
        try:
            G.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # already exists
    try:
        G.query("CREATE VECTOR INDEX FOR (c:PolicyChunk) ON (c.embedding) "
                "OPTIONS {dimension:1536, similarityFunction:'cosine'}")
    except Exception:
        pass

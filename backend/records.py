"""Employees and checklist items: Postgres is the system of record (ADR 0005); the graph keeps a projection (ADR 0006).

Every read and write of an Employee or Item goes through here. Rows come back as the same dicts the graph used to
return (ISO date strings, graph-only properties merged back from `extra`), so callers didn't have to change shape.

After every write, `project(eid)` refreshes that employee's nodes in the graph *in place* (MERGE by id). Nodes are
never deleted or recreated: connector records hang off them (SYNCED_TO, FROM_CANDIDATE), and documents are projected
here too (documents.store.project).
"""
from datetime import date, datetime

from shared import db

EMP_COLS = ("id", "name", "email", "role", "grade", "department", "location", "manager_email", "joining_date",
            "date_of_birth", "worker_type", "token", "thread_id", "doc_scope", "status")
ITEM_COLS = ("id", "employee_id", "key", "name", "kind", "category", "status", "due", "reason", "policy_ref", "auto",
             "depends", "owner_name", "owner_email", "rule_id", "rule_version_id", "superseded_by_version",
             "cancelled_by_version")
_DATES = {"joining_date", "date_of_birth", "due"}
_ARRAYS = {"doc_scope", "depends"}


class NotFound(LookupError):
    pass


# ---------- row <-> dict ----------

def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _to_dict(row: dict | None, cols: tuple) -> dict | None:
    if row is None:
        return None
    out = {**(row.get("extra") or {})}
    for c in cols:
        v = row.get(c)
        if c in _DATES:
            v = _iso(v) if v else ""
        elif c in _ARRAYS:
            v = list(v or [])
        out[c] = v
    return out


def _split(d: dict, cols: tuple) -> tuple[dict, dict]:
    """A graph-shaped dict -> (column values, extra). Empty dates become NULL."""
    colv, extra = {}, {}
    for k, v in d.items():
        if k in cols:
            if k in _DATES:
                v = v or None
            elif k in _ARRAYS:
                v = list(v or [])
            colv[k] = v
        elif k not in ("extra", "created_at", "updated_at"):
            extra[k] = v
    return colv, extra


def employee_dict(row: dict | None) -> dict | None:
    return _to_dict(row, EMP_COLS)


def item_dict(row: dict | None) -> dict | None:
    return _to_dict(row, ITEM_COLS)


# ---------- reads ----------

def employee(eid: str, conn=None) -> dict | None:
    return employee_dict(db.fetch1("SELECT * FROM employees WHERE id=%s", (eid,), conn=conn))


def employee_by(email: str | None = None, token: str | None = None, thread_id: str | None = None, conn=None) -> dict | None:
    if email is not None:
        row = db.fetch1("SELECT * FROM employees WHERE lower(email)=lower(%s)", (email,), conn=conn)
    elif token is not None:
        row = db.fetch1("SELECT * FROM employees WHERE token=%s", (token,), conn=conn)
    elif thread_id:
        row = db.fetch1("SELECT * FROM employees WHERE thread_id=%s", (thread_id,), conn=conn)
    else:
        return None
    return employee_dict(row)


def employees(conn=None) -> list[dict]:
    return [employee_dict(r) for r in db.fetch("SELECT * FROM employees ORDER BY joining_date, id", conn=conn)]


def employee_ids(conn=None) -> list[str]:
    return [r["id"] for r in db.fetch("SELECT id FROM employees ORDER BY joining_date, id", conn=conn)]


def emails(conn=None) -> set[str]:
    return {r["e"] for r in db.fetch("SELECT lower(email) AS e FROM employees WHERE email <> ''", conn=conn)}


def items(eid: str, include_closed: bool = False, conn=None) -> list[dict]:
    """The joiner's checklist. Cancelled items and items kept only as evidence are hidden unless asked for."""
    closed = "" if include_closed else "AND cancelled_by_version IS NULL AND superseded_by_version IS NULL"
    return [item_dict(r) for r in db.fetch(f"SELECT * FROM items WHERE employee_id=%s {closed} ORDER BY due NULLS LAST, key",
                                           (eid,), conn=conn)]


def item(item_id: str, conn=None) -> dict | None:
    return item_dict(db.fetch1("SELECT * FROM items WHERE id=%s", (item_id,), conn=conn))


def item_by_key(eid: str, key: str, conn=None) -> dict | None:
    return item_dict(db.fetch1("SELECT * FROM items WHERE employee_id=%s AND key=%s", (eid, key), conn=conn))


# ---------- writes ----------

def _upsert(table: str, cols: tuple, d: dict, conn) -> None:
    colv, extra = _split(d, cols)
    names = list(colv) + ["extra"]
    vals = [colv[c] for c in colv] + [db.Json(extra)]
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in names if c != "id")
    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) "
                 f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at=now()", vals)


def _update(table: str, cols: tuple, key: str, fields: dict, conn) -> int:
    colv, extra = _split(fields, cols)
    colv.pop("id", None)
    sets, vals = [f"{c}=%s" for c in colv], list(colv.values())
    if extra:
        sets.append("extra = extra || %s")
        vals.append(db.Json(extra))
    if not sets:
        return 0
    return conn.execute(f"UPDATE {table} SET {', '.join(sets)}, updated_at=now() WHERE id=%s", (*vals, key)).rowcount


def _write(eid: str, fn, conn=None, project_after: bool = True):
    """Run `fn(conn)` in the caller's transaction, or in a new one followed by a graph projection refresh."""
    if conn is not None:
        return fn(conn)
    with db.tx() as c:
        out = fn(c)
    if project_after:
        project(eid)
    return out


def save_employee(d: dict, conn=None) -> dict:
    """Insert or replace an employee from a graph-shaped dict (must include id, token, joining_date)."""
    _write(d["id"], lambda c: _upsert("employees", EMP_COLS, d, c), conn)
    return employee(d["id"], conn=conn)


def update_employee(eid: str, conn=None, **fields) -> dict:
    def fn(c):
        if not _update("employees", EMP_COLS, eid, fields, c):
            raise NotFound(eid)
    _write(eid, fn, conn)
    return employee(eid, conn=conn)


def save_item(d: dict, conn=None) -> dict:
    """Insert or replace an item from a graph-shaped dict (must include id, employee_id)."""
    _write(d["employee_id"], lambda c: _upsert("items", ITEM_COLS, d, c), conn)
    return item(d["id"], conn=conn)


def update_item(item_id: str, conn=None, **fields) -> dict:
    eid_box = {}

    def fn(c):
        row = c.execute("SELECT employee_id FROM items WHERE id=%s", (item_id,)).fetchone()
        if not row:
            raise NotFound(item_id)
        eid_box["eid"] = row["employee_id"]
        _update("items", ITEM_COLS, item_id, fields, c)

    if conn is not None:
        fn(conn)
        return item(item_id, conn=conn)
    with db.tx() as c:
        fn(c)
    project(eid_box["eid"])
    return item(item_id)


def set_items_status(eid: str, keys: list[str], status_for_kind: dict, conn=None) -> None:
    """Bulk status change by item key (demo seeding)."""
    def fn(c):
        for kind, status in status_for_kind.items():
            c.execute("UPDATE items SET status=%s, updated_at=now() WHERE employee_id=%s AND kind=%s AND key = ANY(%s)",
                      (status, eid, kind, keys))
    _write(eid, fn, conn)


# ---------- graph projection ----------

def _props(d: dict) -> dict:
    """FalkorDB can't store NULL; drop it (SET n = props then removes the property)."""
    return {k: v for k, v in d.items() if v is not None}


def project(eid: str) -> None:
    """Refresh one employee's subgraph from Postgres, in place."""
    from graph import q
    e = employee(eid)
    if e is None:
        return
    q("MERGE (e:Employee {id:$id}) SET e = $p", id=eid, p=_props(e))
    q("MATCH (e:Employee {id:$id})-[r:REPORTS_TO]->() DELETE r", id=eid)
    if e.get("manager_email"):
        q("MATCH (e:Employee {id:$id}), (m) WHERE (m:Owner OR m:Employee) AND toLower(m.email)=toLower(e.manager_email) "
          "AND m <> e CREATE (e)-[:REPORTS_TO]->(m)", id=eid)
    rows = [_props(i) for i in items(eid, include_closed=True)]
    if rows:
        q("""UNWIND $rows AS r MERGE (i:Item {id:r.id}) SET i = r
             WITH i MATCH (e:Employee {id:$e}) MERGE (e)-[:HAS_ITEM]->(i)""", rows=rows, e=eid)
    # Items that are cancelled or kept only as evidence aren't part of the live plan: no dependency/owner edges.
    q("MATCH (:Employee {id:$e})-[:HAS_ITEM]->(:Item)-[d:DEPENDS_ON|OWNED_BY]->() DELETE d", e=eid)
    q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item) WHERE i.cancelled_by_version IS NULL AND i.superseded_by_version IS NULL
         UNWIND i.depends AS dk
         MATCH (:Employee {id:$e})-[:HAS_ITEM]->(j:Item {key:dk}) CREATE (i)-[:DEPENDS_ON]->(j)""", e=eid)
    q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item) WHERE i.cancelled_by_version IS NULL AND i.superseded_by_version IS NULL
         MATCH (o:Owner {email:i.owner_email}) CREATE (i)-[:OWNED_BY]->(o)""", e=eid)
    from shared import bridge
    if bridge.source("Document") == "postgres":  # before the Phase 2 cutover the graph still owns documents
        from documents import store as docs
        docs.project(eid)


def rebuild_graph(eid: str | None = None) -> int:
    """Repair the projection from Postgres: one employee, or everyone."""
    ids = [eid] if eid else employee_ids()
    for i in ids:
        project(i)
    return len(ids)


# ---------- migration: backfill + parity (hardening design section 4) ----------

def _graph_rows() -> tuple[list[dict], list[dict]]:
    from graph import q
    emps = [r["e"] for r in q("MATCH (e:Employee) RETURN e")]
    its = [r["i"] for r in q("MATCH (e:Employee)-[:HAS_ITEM]->(i:Item) RETURN i")]
    return emps, its


def backfill() -> dict:
    """Copy graph Employees/Items into Postgres (idempotent: upsert by id). Returns counts and unmapped keys."""
    emps, its = _graph_rows()
    ids = {e["id"] for e in emps}
    skipped = [i["id"] for i in its if i.get("employee_id") not in ids]
    extra_keys = sorted({k for e in emps for k in _split(e, EMP_COLS)[1]} | {k for i in its for k in _split(i, ITEM_COLS)[1]})
    with db.tx() as c:
        for e in emps:
            _upsert("employees", EMP_COLS, e, c)
        for i in its:
            if i.get("employee_id") in ids:
                _upsert("items", ITEM_COLS, i, c)
    return {"employees": len(emps), "items": len(its) - len(skipped), "skipped_items": skipped, "extra_keys": extra_keys}


def canonical(record_type: str, rows: list[dict]) -> list[dict]:
    """Both stores' rows in one comparable form: round-trip through the column mapping."""
    cols = EMP_COLS if record_type == "Employee" else ITEM_COLS

    def norm(d):
        colv, extra = _split(d, cols)
        out = {**extra, **{c: (_iso(colv[c]) or "") if c in _DATES else colv[c] for c in colv}}
        # Defaults the graph never stored but Postgres always has:
        for k, default in (("worker_type", "employee"), ("status", "active"), ("doc_scope", []), ("thread_id", ""),
                           ("rule_version_id", None), ("superseded_by_version", None), ("cancelled_by_version", None)):
            if out.get(k) in (None, default, []) and k in cols:
                out.pop(k, None)
        return out
    return [norm(r) for r in rows]


def parity_rows(record_type: str) -> tuple[list[dict], list[dict]]:
    emps, its = _graph_rows()
    if record_type == "Employee":
        pg = db.fetch("SELECT * FROM employees")
        return canonical("Employee", emps), canonical("Employee", [employee_dict(r) for r in pg])
    pg = db.fetch("SELECT * FROM items")
    return canonical("Item", its), canonical("Item", [item_dict(r) for r in pg])

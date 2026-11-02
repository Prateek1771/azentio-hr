"""Connectors in Postgres (Phase 3 batch C): the catalog, instances and their connection settings, connector records,
the employee <-> record links, custom fields and import runs. The only reader and writer of those tables.

Connection secrets are stored only encrypted (`connectors/secrets.py`, ADR 0013). `connection()` returns a flag and a
hint and never decrypts; `secret()` decrypts for the one call that needs it.
"""
import hashlib
import json
import time

import graph_migration as gm
from shared import db
from . import secrets
from .secrets import KeyConfigError, SecretUnavailable  # noqa: F401  (re-exported for callers)

KEEP = object()  # save_connection: keep the stored secret

# Runtime labels, so this module (which registers the specs) isn't reported as a graph read path.
C, CI, R, EMP = "Connector", "ConnectorInstance", "Record", "Employee"
LINK_TYPES = {"synced_to": "SYNCED_TO", "from_candidate": "FROM_CANDIDATE"}


# ---------- catalog and instances ----------

_INSTANCE = """SELECT i.id, i.name, c.key, c.role, c.record_kind, c.demo, c.description, c.name AS type_name
               FROM connector_instances i JOIN connectors c ON c.key = i.key"""


def connector_keys() -> set[str]:
    return {r["key"] for r in db.fetch("SELECT key FROM connectors")}


def has_non_app_connectors() -> bool:
    return bool(db.fetch1("SELECT 1 AS x FROM connectors WHERE role <> 'app' LIMIT 1"))


def add_connector(c: dict, instance_id: str) -> None:
    """Catalog entry plus this workspace's one instance of it."""
    with db.tx() as conn:
        db.execute("""INSERT INTO connectors (key, name, role, record_kind, demo, description)
                      VALUES (%(key)s, %(name)s, %(role)s, %(record_kind)s, %(demo)s, %(description)s)
                      ON CONFLICT (key) DO NOTHING""", {"description": "", "demo": False, **c}, conn=conn)
        db.execute("INSERT INTO connector_instances (id, key, name, created) VALUES (%s, %s, %s, %s)",
                   (instance_id, c["key"], c["name"], time.time()), conn=conn)


def instances(role: str | None = None) -> list[dict]:
    """Every instance in creation order (role filter optional), with its catalog fields."""
    return db.fetch(_INSTANCE + " WHERE %(r)s::text IS NULL OR c.role = %(r)s ORDER BY i.created, i.id", {"r": role})


def instance(instance_id: str) -> dict | None:
    return db.fetch1(_INSTANCE + " WHERE i.id = %s", (instance_id,))


def instance_id_for(key: str) -> str | None:
    row = db.fetch1("SELECT id FROM connector_instances WHERE key=%s ORDER BY created LIMIT 1", (key,))
    return row["id"] if row else None


# ---------- records ----------

def _row(r: dict) -> dict:
    return {"id": r["id"], "instance_id": r["instance_id"], "kind": r["kind"], "values": r["data"],
            "created": r["created"], "updated": r["updated"]}


def records(instance_id: str, page: int = 1, page_size: int = 25, q: str = "") -> dict:
    """Newest first, paged in SQL. Every word of `q` must appear in some field value (not a key name), any case, so
    "Ananya K" finds Ananya Krishnan as the old joined-values search did."""
    where, params = "instance_id = %(i)s", {"i": instance_id}
    for n, word in enumerate(q.split()):
        where += f""" AND EXISTS (SELECT 1 FROM jsonb_each(data) f WHERE jsonb_typeof(f.value) <> 'object'
                                  AND (f.value #>> '{{}}') ILIKE %(q{n})s ESCAPE '\\')"""
        params[f"q{n}"] = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    total = db.fetch1(f"SELECT count(*) AS n FROM connector_records WHERE {where}", params)["n"]
    rows = db.fetch(f"SELECT * FROM connector_records WHERE {where} ORDER BY created DESC, id LIMIT %(l)s OFFSET %(o)s",
                    {**params, "l": page_size, "o": (max(page, 1) - 1) * page_size})
    return {"items": [_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def record(instance_id: str, record_id: str) -> dict | None:
    r = db.fetch1("SELECT * FROM connector_records WHERE id=%s AND instance_id=%s", (record_id, instance_id))
    return _row(r) if r else None


def count(instance_id: str) -> int:
    return db.fetch1("SELECT count(*) AS n FROM connector_records WHERE instance_id=%s", (instance_id,))["n"]


def find(instance_id: str, field: str, value: str) -> dict | None:
    r = db.fetch1("""SELECT * FROM connector_records WHERE instance_id=%s AND lower(data->>%s) = lower(%s)
                     ORDER BY created LIMIT 1""", (instance_id, field, str(value)))
    return _row(r) if r else None


def write(instance_id: str, kind: str, values: dict, record_id: str | None = None) -> str:
    from graph import new_id
    now = time.time()
    if record_id:
        db.execute("UPDATE connector_records SET data=%s, updated=%s WHERE id=%s", (db.Json(values), now, record_id))
        return record_id
    rid = new_id("rec")
    db.execute("INSERT INTO connector_records (id, instance_id, kind, data, created, updated) VALUES (%s,%s,%s,%s,%s,%s)",
               (rid, instance_id, kind, db.Json(values), now, now))
    return rid


# ---------- employee <-> record links ----------

def link(employee_id: str, record_id: str, type_: str, replace: bool = False) -> None:
    """Link a joiner to a connector record. `replace` first drops the joiner's other links of this type (an employee
    is synced to one HR record at a time)."""
    assert type_ in LINK_TYPES, type_
    with db.tx() as conn:
        if replace:
            db.execute("DELETE FROM employee_record_links WHERE employee_id=%s AND type=%s", (employee_id, type_), conn=conn)
        db.execute("INSERT INTO employee_record_links VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                   (employee_id, record_id, type_), conn=conn)


def linked_employees(instance_id: str) -> dict[str, str]:
    """record id -> employee id, for one connector's records."""
    rows = db.fetch("""SELECT l.record_id, l.employee_id FROM employee_record_links l
                       JOIN connector_records r ON r.id = l.record_id WHERE r.instance_id=%s
                       ORDER BY l.type""", (instance_id,))
    return {r["record_id"]: r["employee_id"] for r in rows}


def linked_employee(record_id: str) -> dict | None:
    return db.fetch1("""SELECT e.id, e.name FROM employee_record_links l JOIN employees e ON e.id = l.employee_id
                        WHERE l.record_id=%s ORDER BY l.type LIMIT 1""", (record_id,))


def employee_links(employee_id: str) -> list[dict]:
    rows = db.fetch("""SELECT l.type, r.id AS record_id, r.data, i.id AS connector_id, i.name AS connector
                       FROM employee_record_links l JOIN connector_records r ON r.id = l.record_id
                       JOIN connector_instances i ON i.id = r.instance_id
                       WHERE l.employee_id=%s ORDER BY l.type, r.id""", (employee_id,))
    return [{"type": LINK_TYPES[r["type"]], "record_id": r["record_id"], "connector_id": r["connector_id"],
             "connector": r["connector"], "employee_number": r["data"].get("employee_number")} for r in rows]


# ---------- custom fields, import runs ----------

def custom_fields(instance_id: str) -> list[dict]:
    return db.fetch("SELECT * FROM custom_fields WHERE instance_id=%s ORDER BY created, key", (instance_id,))


def add_custom_field(instance_id: str, key: str, label: str, type_: str, required: bool) -> None:
    db.execute("INSERT INTO custom_fields (id, instance_id, key, label, type, required, created) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               (f"{instance_id}:{key}", instance_id, key, label, type_, required, time.time()))


def import_runs(instance_id: str) -> list[dict]:
    return db.fetch("SELECT * FROM import_runs WHERE instance_id=%s ORDER BY ts DESC", (instance_id,))


def add_import_run(run: dict) -> None:
    db.execute("""INSERT INTO import_runs (id, instance_id, filename, total, imported, errors, ts)
                  VALUES (%(id)s, %(instance_id)s, %(filename)s, %(total)s, %(imported)s, %(errors)s, %(ts)s)""",
               {**run, "errors": db.Json(run["errors"])})


# ---------- connection settings ----------

def connection(instance_id: str) -> dict:
    """Saved settings with the secret replaced by a flag and a hint. Never decrypts."""
    r = db.fetch1("SELECT connection, secret_enc, secret_hint FROM connector_instances WHERE id=%s", (instance_id,)) or {}
    c = r.get("connection") or {}
    return {"tenant": c.get("tenant", ""), "auth_type": c.get("auth_type", "token"), "username": c.get("username", ""),
            "secret_set": bool(r.get("secret_enc")), "secret_hint": r.get("secret_hint") or "",
            "configured_at": c.get("configured_at"), "last_test": c.get("last_test")}


def saved_auth_type(instance_id: str) -> str | None:
    r = db.fetch1("SELECT connection->>'auth_type' AS t, secret_enc FROM connector_instances WHERE id=%s", (instance_id,))
    return r["t"] if r and r["secret_enc"] else None


def save_connection(instance_id: str, cfg: dict, secret=KEEP, conn=None) -> None:
    if secret is KEEP:
        db.execute("UPDATE connector_instances SET connection=%s WHERE id=%s", (db.Json(cfg), instance_id), conn=conn)
        return
    enc, kid = secrets.encrypt(secret) if secret else ("", "")
    db.execute("""UPDATE connector_instances SET connection=%s, secret_enc=%s, secret_key_id=%s, secret_hint=%s
                  WHERE id=%s""", (db.Json(cfg), enc, kid, secrets.hint(secret), instance_id), conn=conn)


def set_last_test(instance_id: str, result: dict, conn=None) -> None:
    db.execute("UPDATE connector_instances SET connection = connection || jsonb_build_object('last_test', %s::jsonb) "
               "WHERE id=%s", (json.dumps(result), instance_id), conn=conn)


def secret(instance_id: str) -> str:
    """The plaintext secret, for a call that needs it. Raises SecretUnavailable if no configured key can read it."""
    r = db.fetch1("SELECT secret_enc FROM connector_instances WHERE id=%s", (instance_id,))
    return secrets.decrypt(r["secret_enc"]) if r and r["secret_enc"] else ""


def rotate() -> int:
    """Re-encrypt every stored secret with the current key, in one transaction. Returns how many."""
    with db.tx() as conn:
        rows = db.fetch("SELECT id, secret_enc FROM connector_instances WHERE secret_enc <> '' FOR UPDATE", conn=conn)
        for r in rows:
            enc, kid = secrets.encrypt(secrets.decrypt(r["secret_enc"]))
            db.execute("UPDATE connector_instances SET secret_enc=%s, secret_key_id=%s WHERE id=%s",
                       (enc, kid, r["id"]), conn=conn)
    return len(rows)


# ---------- Phase 3 batch C specs ----------

def _f(v) -> float:
    return float(v or 0)


def _json(v, default, what: str):
    """Graph JSON strings: empty -> default; malformed -> refuse the cutover (silently mapping it to {} would pass
    parity on both sides and lose the data)."""
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except ValueError:
            raise ValueError(f"{what} holds malformed JSON; fix it in the graph and restart") from None
    return default if v in (None, "") else v


def _sha_plain(token: str) -> str:
    if not token:
        return ""
    try:
        return hashlib.sha256(secrets.decrypt(token).encode()).hexdigest()
    except SecretUnavailable:
        return "undecryptable"


def _instance_row(p: dict) -> dict:
    plain = p.get("secret") or ""
    enc, kid = secrets.encrypt(plain) if plain else ("", "")
    return {"id": p["id"], "key": p["key"], "name": p.get("name") or "", "created": _f(p.get("created")),
            "connection": _json(p.get("connection"), {}, f"{CI} {p['id']} connection"), "secret_enc": enc, "secret_key_id": kid,
            "secret_hint": secrets.hint(plain)}


def _instance_canon(r: dict) -> dict:  # the secret compares as a hash of its plaintext: never in a report
    return {"id": r["id"], "key": r["key"], "name": r["name"], "created": gm.epoch(r["created"]),
            "connection": r["connection"] or {}, "secret_sha": _sha_plain(r["secret_enc"])}


def _instance_props(r: dict) -> dict:
    return {"id": r["id"], "key": r["key"], "name": r["name"], "created": r["created"],
            "connection": json.dumps(r["connection"] or {}), "secret": secrets.decrypt(r["secret_enc"]) if r["secret_enc"] else ""}


def _record_row(p: dict) -> dict:
    return {"id": p["id"], "instance_id": p["instance_id"], "kind": p.get("kind") or "",
            "data": _json(p.get("values"), {}, f"{R} {p['id']} values"), "created": _f(p.get("created")), "updated": _f(p.get("updated") or p.get("created"))}


def _record_canon(r: dict) -> dict:
    return {**r, "created": gm.epoch(r["created"]), "updated": gm.epoch(r["updated"])}


def _backfill_links(conn) -> None:
    """The graph is the source while the batch is pending: replace the table, so links the previous release removed
    (a re-sync after a rollback) don't come back."""
    from graph import q
    db.execute("DELETE FROM employee_record_links", conn=conn)
    for t, rel in LINK_TYPES.items():
        for row in q(f"MATCH (e:{EMP})-[:{rel}]->(r:{R}) RETURN DISTINCT e.id AS e, r.id AS r"):
            db.execute("INSERT INTO employee_record_links VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                       (row["e"], row["r"], t), conn=conn)


def _scrub_graph_secrets() -> None:
    from graph import q
    q(f"MATCH (i:{CI}) WHERE i.secret IS NOT NULL REMOVE i.secret")


def _restore_of() -> None:
    from graph import q
    q(f"MATCH (i:{CI})-[l:OF]->() DELETE l")
    q(f"MATCH (i:{CI}), (c:{C}) WHERE c.key = i.key CREATE (i)-[:OF]->(c)")


def _restore_in() -> None:
    from graph import q
    q(f"MATCH (r:{R})-[l:IN]->() DELETE l")
    q(f"MATCH (r:{R}), (i:{CI}) WHERE i.id = r.instance_id CREATE (r)-[:IN]->(i)")


def _restore_links() -> None:
    from graph import q
    for t, rel in LINK_TYPES.items():
        q(f"MATCH (:{EMP})-[l:{rel}]->(:{R}) DELETE l")
        for row in db.fetch("SELECT employee_id, record_id FROM employee_record_links WHERE type=%s", (t,)):
            q(f"MATCH (e:{EMP} {{id:$e}}), (r:{R} {{id:$r}}) CREATE (e)-[:{rel}]->(r)", e=row["employee_id"], r=row["record_id"])


gm.register(gm.LabelSpec(
    label=C, batch="C", table="connectors", key="key",
    to_row=lambda p: {"key": p["key"], "name": p.get("name") or "", "role": p.get("role") or "",
                      "record_kind": p.get("record_kind") or "", "demo": bool(p.get("demo")),
                      "description": p.get("description") or ""},
    canonical=lambda r: dict(r), from_row=lambda r: dict(r),
    mapped_keys=frozenset({"key", "name", "role", "record_kind", "demo", "description"})))

gm.register(gm.LabelSpec(
    label=CI, batch="C", table="connector_instances",
    to_row=_instance_row, canonical=_instance_canon, from_row=_instance_props, after_cutover=_scrub_graph_secrets,
    mapped_keys=frozenset({"id", "key", "name", "created", "connection", "secret"}),
    edges=[gm.EdgeSpec("OF", f"MATCH (:{CI})-[l:OF]->(:{C}) RETURN count(l) AS n",
                 "SELECT count(*) AS n FROM connector_instances", restore=_restore_of)]))

gm.register(gm.LabelSpec(
    label=R, batch="C", table="connector_records",
    to_row=_record_row, canonical=_record_canon,
    from_row=lambda r: {"id": r["id"], "instance_id": r["instance_id"], "kind": r["kind"],
                        "values": json.dumps(r["data"]), "created": r["created"], "updated": r["updated"]},
    mapped_keys=frozenset({"id", "instance_id", "kind", "values", "created", "updated"}),
    edges=[gm.EdgeSpec("IN", f"MATCH (:{R})-[l:IN]->(:{CI}) RETURN count(l) AS n",
                 "SELECT count(*) AS n FROM connector_records", restore=_restore_in),
           *[gm.EdgeSpec(rel, f"MATCH (:{EMP})-[l:{rel}]->(:{R}) RETURN count(l) AS n",
                   f"SELECT count(*) AS n FROM employee_record_links WHERE type='{t}'",
                   restore=_restore_links if t == "synced_to" else None,  # one restore rebuilds both link types
                   backfill=_backfill_links if t == "synced_to" else None)
             for t, rel in LINK_TYPES.items()]]))

gm.register(gm.LabelSpec(
    label="CustomField", batch="C", table="custom_fields",
    to_row=lambda p: {"id": f"{p['instance_id']}:{p['key']}", "instance_id": p["instance_id"], "key": p["key"],
                      "label": p.get("label") or "", "type": p.get("type") or "string",
                      "required": bool(p.get("required")), "created": _f(p.get("created"))},
    canonical=lambda r: {**r, "created": gm.epoch(r["created"])}, from_row=lambda r: dict(r),
    mapped_keys=frozenset({"id", "instance_id", "key", "label", "type", "required", "created"})))

gm.register(gm.LabelSpec(
    label="ImportRun", batch="C", table="import_runs",
    to_row=lambda p: {"id": p["id"], "instance_id": p.get("instance_id") or "", "filename": p.get("filename") or "",
                      "total": int(p.get("total") or 0), "imported": int(p.get("imported") or 0),
                      "errors": gm.jsonv(p.get("errors"), []), "ts": _f(p.get("ts"))},
    canonical=lambda r: {**r, "ts": gm.epoch(r["ts"])},
    from_row=lambda r: {**r, "errors": json.dumps(r["errors"])},
    mapped_keys=frozenset({"id", "instance_id", "filename", "total", "imported", "errors", "ts"})))

gm.register(gm.LabelSpec(  # dead since the integrations engine replaced connectors/sync.run: archived for rollback only
    label="IntegrationRun", batch="C", table="legacy_sync_runs",
    to_row=lambda p: {"id": p["id"], "props": {k: v for k, v in p.items() if v is not None}},
    canonical=lambda r: {"id": r["id"], "props": json.loads(json.dumps(r["props"], sort_keys=True))},
    from_row=lambda r: dict(r["props"]),
    mapped_keys=frozenset({"id", "source_id", "target_id", "source_name", "target_name", "mapping", "policy",
                           "counts", "results", "ts"})))

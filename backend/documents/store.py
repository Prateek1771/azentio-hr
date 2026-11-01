"""Documents: Postgres is the system of record (Phase 2); the graph keeps a projection for the relationship view.

Every read and write of a document, its mismatches and its decisions goes through here (the boundary guard enforces
it for graph writes). Rows come back in the shape the graph used to return (`ts` epoch seconds, `extracted`/`issues`
as objects), so API consumers didn't change.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from shared import db

COLS = ("id", "employee_id", "item_id", "filename", "path", "mime", "size", "sha256", "source", "status", "doc_type",
        "tier", "confidence", "extracted", "issues", "checks", "model", "prompt_version", "policy_version_id",
        "current_decision_id", "received_at", "decided_at")
JSON_COLS = {"extracted", "issues", "checks"}
STATUSES = ("Submitted", "Under Review", "Accepted", "Correction Requested", "Rejected")


def doc_dict(row: dict | None) -> dict | None:
    if row is None:
        return None
    d = {**(row.get("extra") or {}), **{c: row.get(c) for c in COLS if c in row}}
    d["ts"] = row["received_at"].timestamp() if row.get("received_at") else 0
    for c in ("received_at", "decided_at"):
        if isinstance(d.get(c), datetime):
            d[c] = d[c].isoformat()
    d["confidence"] = float(d.get("confidence") or 0)
    return d


# ---------- reads ----------

def get(doc_id: str, conn=None, lock: bool = False) -> dict | None:
    return doc_dict(db.fetch1("SELECT * FROM documents WHERE id=%s" + (" FOR UPDATE" if lock else ""), (doc_id,), conn=conn))


def for_items(item_ids: list[str], conn=None) -> dict[str, list[dict]]:
    """item id -> its documents, newest first."""
    out: dict[str, list[dict]] = {}
    if item_ids:
        for r in db.fetch("SELECT * FROM documents WHERE item_id = ANY(%s) ORDER BY received_at DESC", (item_ids,), conn=conn):
            out.setdefault(r["item_id"], []).append(doc_dict(r))
    return out


def latest_for_item(item_id: str, conn=None) -> dict | None:
    return doc_dict(db.fetch1("SELECT * FROM documents WHERE item_id=%s ORDER BY received_at DESC LIMIT 1", (item_id,), conn=conn))


def has_docs(item_ids: list[str], conn=None) -> set[str]:
    if not item_ids:
        return set()
    return {r["item_id"] for r in db.fetch("SELECT DISTINCT item_id FROM documents WHERE item_id = ANY(%s)", (item_ids,), conn=conn)}


def for_employee(eid: str, conn=None) -> list[dict]:
    return [doc_dict(r) for r in db.fetch("SELECT * FROM documents WHERE employee_id=%s ORDER BY received_at DESC", (eid,), conn=conn)]


def unmatched(eid: str, conn=None) -> list[dict]:
    return [doc_dict(r) for r in db.fetch("SELECT * FROM documents WHERE employee_id=%s AND item_id IS NULL ORDER BY received_at DESC",
                                          (eid,), conn=conn)]


def mismatches(eid: str, conn=None) -> list[dict]:
    """Profile mismatches found on this joiner's documents: field, expected, found, filename."""
    return db.fetch("""SELECT m.field, m.expected, m.found, d.filename, d.id AS document_id FROM document_mismatches m
                       JOIN documents d ON d.id = m.document_id WHERE d.employee_id=%s ORDER BY d.received_at DESC, m.field""",
                    (eid,), conn=conn)


def mismatches_of(doc_id: str, conn=None) -> list[dict]:
    return db.fetch("SELECT field, expected, found FROM document_mismatches WHERE document_id=%s ORDER BY field", (doc_id,), conn=conn)


def decisions(doc_id: str, conn=None) -> list[dict]:
    return db.fetch("""SELECT d.*, u.email AS decided_by_email FROM doc_decisions d LEFT JOIN users u ON u.id = d.decided_by
                       WHERE d.document_id=%s ORDER BY d.at, d.id""", (doc_id,), conn=conn)


# ---------- writes ----------

def _vals(fields: dict) -> dict:
    return {k: (db.Json(v) if k in JSON_COLS else v) for k, v in fields.items()}


def insert(d: dict, conn) -> None:
    cols = [c for c in COLS if c in d]
    extra = {k: v for k, v in d.items() if k not in COLS and k != "ts"}
    v = _vals({c: d[c] for c in cols})
    conn.execute(f"INSERT INTO documents ({', '.join(cols)}, extra) VALUES ({', '.join(['%s'] * len(cols))}, %s)",
                 [*v.values(), db.Json(extra)])


def update(doc_id: str, conn, **fields) -> None:
    v = _vals({k: fields[k] for k in fields if k in COLS and k != "id"})
    if v:
        conn.execute(f"UPDATE documents SET {', '.join(f'{k}=%s' for k in v)} WHERE id=%s", [*v.values(), doc_id])


def set_mismatches(doc_id: str, rows: list[dict], conn) -> None:
    conn.execute("DELETE FROM document_mismatches WHERE document_id=%s", (doc_id,))
    for m in rows:
        conn.execute("""INSERT INTO document_mismatches (document_id, field, expected, found) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (document_id, field) DO UPDATE SET expected=EXCLUDED.expected, found=EXCLUDED.found""",
                     (doc_id, m["field"], str(m.get("expected") or ""), str(m.get("found") or "")))


DECISION_COLS = ("document_id", "kind", "decided_by", "reason_code", "reason_text", "status_after", "model", "prompt_version",
                 "confidence", "tier", "threshold", "policy_version_id", "checks", "extracted", "corrected_fields",
                 "work_item_id", "idempotency_key")


def add_decision(row: dict, conn) -> int | None:
    """Append a decision and make it the document's current one. Returns its id, or None when the idempotency key
    was already used (a retry): nothing changes then."""
    cols = [c for c in DECISION_COLS if c in row]
    vals = [db.Json(row[c]) if c in ("checks", "extracted", "corrected_fields") else row[c] for c in cols]
    got = conn.execute(f"""INSERT INTO doc_decisions ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})
                           ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""", vals).fetchone()
    if not got:
        return None
    conn.execute("UPDATE documents SET current_decision_id=%s, status=%s, decided_at=now() WHERE id=%s",
                 (got["id"], row["status_after"], row["document_id"]))
    return got["id"]


# ---------- graph projection ----------

def project(eid: str) -> None:
    """Refresh this joiner's Document nodes and their SUBMITTED / HAS_DOC / MISMATCH edges from Postgres, in place.
    Called from records.project; the only graph writer for documents."""
    from graph import q
    docs = for_employee(eid)
    if not docs:
        return
    rows = [{k: v for k, v in {"id": d["id"], "employee_id": eid, "filename": d["filename"], "mime": d["mime"],
                               "size": d["size"], "source": d["source"], "status": d["status"], "ts": d["ts"],
                               "doc_type": d["doc_type"], "confidence": d["confidence"], "tier": d.get("tier"),
                               "item_id": d.get("item_id")}.items() if v is not None} for d in docs]
    q("""UNWIND $rows AS r MERGE (d:Document {id:r.id}) SET d = r
         WITH d MATCH (e:Employee {id:$e}) MERGE (e)-[:SUBMITTED]->(d)""", rows=rows, e=eid)
    ids = [d["id"] for d in docs]
    q("MATCH ()-[h:HAS_DOC]->(d:Document) WHERE d.id IN $ids DELETE h", ids=ids)
    q("""UNWIND $rows AS r WITH r WHERE r.item_id IS NOT NULL
         MATCH (i:Item {id:r.item_id}), (d:Document {id:r.id}) MERGE (i)-[:HAS_DOC]->(d)""", rows=rows)
    q("MATCH (d:Document)-[m:MISMATCH]->() WHERE d.id IN $ids DELETE m", ids=ids)
    mm = [{**m, "document_id": m["document_id"]} for m in mismatches(eid)]
    if mm:
        q("""UNWIND $mm AS m MATCH (d:Document {id:m.document_id}), (e:Employee {id:$e})
             CREATE (d)-[x:MISMATCH]->(e) SET x.field=m.field, x.expected=m.expected, x.found=m.found""", mm=mm, e=eid)


# ---------- migration: backfill + parity ----------

def _graph_docs() -> list[dict]:
    from graph import q
    rows = q("""MATCH (d:Document) OPTIONAL MATCH (i:Item)-[:HAS_DOC]->(d)
                OPTIONAL MATCH (d)-[m:MISMATCH]->()
                RETURN d, i.id AS item_id, collect({field:m.field, expected:m.expected, found:m.found}) AS mm""")
    out = []
    for r in rows:
        d = dict(r["d"])
        d["item_id"] = r["item_id"]
        d["mismatches"] = [m for m in r["mm"] if m.get("field")]
        out.append(d)
    return out


def _json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def backfill() -> dict:
    """Copy graph Documents into Postgres (idempotent: skips ids already there). Every copied document gets one
    decision row labelled honestly as backfill, so its history starts somewhere; nothing claims a review that didn't happen."""
    docs = _graph_docs()
    have = {r["id"] for r in db.fetch("SELECT id FROM documents")}
    emps = {r["id"] for r in db.fetch("SELECT id FROM employees")}
    items = {r["id"] for r in db.fetch("SELECT id FROM items")}
    report = {"documents": 0, "missing_files": [], "orphans": [], "unknown_status": [], "accepted_high_risk": []}
    from documents.route import ALWAYS_HIGH
    with db.tx() as c:
        for g in docs:
            if g["id"] in have:
                continue
            if g.get("employee_id") not in emps:
                report["orphans"].append(g["id"])
                continue
            status = g.get("status") if g.get("status") in STATUSES else "Under Review"
            if status != g.get("status"):
                report["unknown_status"].append(g["id"])
            p = Path(g.get("path") or "")
            sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            if sha is None:
                report["missing_files"].append(g["id"])
            ex = _json(g.get("extracted"), {})
            insert({"id": g["id"], "employee_id": g["employee_id"],
                    "item_id": g["item_id"] if g.get("item_id") in items else None,
                    "filename": g.get("filename") or "", "path": g.get("path") or "", "mime": g.get("mime") or "",
                    "size": int(g.get("size") or 0), "sha256": sha, "source": g.get("source") or "portal",
                    "status": status, "doc_type": g.get("doc_type") or "", "confidence": float(g.get("confidence") or 0),
                    "extracted": ex, "issues": _json(g.get("issues"), []), "model": ex.get("_model"),
                    "received_at": datetime.fromtimestamp(float(g.get("ts") or 0), timezone.utc)}, c)
            set_mismatches(g["id"], g["mismatches"], c)
            if status == "Accepted" and g.get("doc_type") in ALWAYS_HIGH:
                report["accepted_high_risk"].append(g["id"])
            add_decision({"document_id": g["id"], "kind": "backfill", "reason_code": "backfill",
                          "reason_text": "Decided before Phase 2; who decided and why wasn't recorded",
                          "status_after": status, "model": ex.get("_model"), "confidence": float(g.get("confidence") or 0),
                          "extracted": ex, "idempotency_key": f"backfill:{g['id']}"}, c)
            report["documents"] += 1
    return report


def canonical(rows: list[dict]) -> list[dict]:
    return [{"id": r["id"], "employee_id": r["employee_id"], "item_id": r.get("item_id"), "status": r["status"],
             "doc_type": r.get("doc_type") or "", "confidence": round(float(r.get("confidence") or 0), 4),
             "filename": r.get("filename") or "",
             "mismatches": sorted((m["field"], str(m["expected"]), str(m["found"])) for m in r.get("mismatches") or [])}
            for r in rows]


def parity_rows() -> tuple[list[dict], list[dict]]:
    """Graph documents normalised exactly as backfill stores them, against Postgres."""
    items = {r["id"] for r in db.fetch("SELECT id FROM items")}
    graph = [{**g, "status": g.get("status") if g.get("status") in STATUSES else "Under Review",
              "item_id": g["item_id"] if g.get("item_id") in items else None} for g in _graph_docs()]
    pg = [doc_dict(r) for r in db.fetch("SELECT * FROM documents")]
    mm: dict[str, list] = {}
    for m in db.fetch("SELECT * FROM document_mismatches"):
        mm.setdefault(m["document_id"], []).append(m)
    for d in pg:
        d["mismatches"] = mm.get(d["id"], [])
    return canonical(graph), canonical(pg)

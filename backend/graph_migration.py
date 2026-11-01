"""Phase 3: the one engine that moves graph record labels to Postgres, batch by batch.

Every label is described once by a `LabelSpec` (how to read it from the graph, which table it lands in, how both sides
compare, and how to write it back for a rollback). For a batch, the engine:
  1. checks the freeze: nothing else may be writing (no other live worker); cutover runs before this process serves;
  2. backfills idempotently (INSERT .. ON CONFLICT DO UPDATE);
  3. reports per label: counts, checksums, first mismatch, missing/extra ids, schema diff (graph keys vs mapped
     columns), edge counts, and remaining graph read paths in code;
  4. switches the bridge only if every label in the batch is clean, recording the graph checksum at cutover;
  5. afterwards, `drift_since_cutover()` recomputes the graph side: any change means something still writes a retired label.
`rollback(batch)` writes the Postgres rows (including any written after cutover) back to the graph, checks parity and
switches the bridge back. The report for every batch is kept in bridge_state and printed by `python -m graph_migration report`.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from shared import bridge, db

ROOT = Path(__file__).resolve().parent
HEARTBEAT_FRESH = 60  # seconds: a heartbeat newer than this is another live instance


class FreezeError(RuntimeError):
    pass


@dataclass
class EdgeSpec:
    name: str              # e.g. "SYNCED_TO"
    graph_count: str       # Cypher returning {n}
    pg_count: str          # SQL returning {n}
    restore: Callable[[], None] | None = None  # rollback: rebuild these edges in the graph from Postgres


@dataclass
class LabelSpec:
    label: str
    batch: str
    table: str
    graph_query: str                     # Cypher returning one node per row as `n` (extra columns are merged in)
    to_row: Callable[[dict], dict]       # graph props -> table columns
    canonical: Callable[[dict], dict]    # table row -> comparable dict (same function for both sides, after to_row)
    from_row: Callable[[dict], dict]     # table row -> graph props (rollback)
    key: str = "id"
    mapped_keys: frozenset = frozenset()     # graph property names consumed by to_row
    dropped_keys: frozenset = frozenset()    # graph properties deliberately not carried over (documented)
    edges: list[EdgeSpec] = field(default_factory=list)
    allowed_readers: tuple = ()              # files allowed to keep MATCHing the label (e.g. the rebuild)


SPECS: dict[str, LabelSpec] = {}


def register(spec: LabelSpec) -> LabelSpec:
    SPECS[spec.label] = spec
    return spec


def batch_labels(batch: str) -> list[LabelSpec]:
    return [s for s in SPECS.values() if s.batch == batch]


def batches() -> list[str]:
    return sorted({s.batch for s in SPECS.values()})


# ---------- reading both sides ----------

def graph_props(spec: LabelSpec) -> list[dict]:
    from graph import q
    out = []
    for r in q(spec.graph_query):
        d = dict(r["n"])
        d.update({k: v for k, v in r.items() if k != "n"})
        out.append(d)
    return out


def pg_rows(spec: LabelSpec, conn=None) -> list[dict]:
    return db.fetch(f"SELECT * FROM {spec.table}", conn=conn)


def _canon_graph(spec: LabelSpec, props: list[dict]) -> list[dict]:
    return [spec.canonical(spec.to_row(p)) for p in props]


def _canon_pg(spec: LabelSpec, rows: list[dict]) -> list[dict]:
    return [spec.canonical(r) for r in rows]


def graph_checksum(spec: LabelSpec) -> str:
    return bridge.checksum(_canon_graph(spec, graph_props(spec)), spec.key)


# ---------- backfill ----------

def _upsert(spec: LabelSpec, row: dict, conn) -> None:
    cols = list(row)
    vals = [db.Json(v) if isinstance(v, (dict, list)) else v for v in row.values()]
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != spec.key) or f"{spec.key}=EXCLUDED.{spec.key}"
    conn.execute(f"INSERT INTO {spec.table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                 f"ON CONFLICT ({spec.key}) DO UPDATE SET {updates}", vals)


def backfill(spec: LabelSpec, conn) -> int:
    props = graph_props(spec)
    for p in props:
        _upsert(spec, spec.to_row(p), conn)
    return len(props)


# ---------- the report ----------

def read_paths(label: str, allowed: tuple = ()) -> list[str]:
    """Code that still reads the label from the graph: `MATCH (x:Label` / `(:Label` in a MATCH, outside the allowed files."""
    rx = re.compile(rf"MATCH[^\n]*\(\s*\w*\s*:\s*{re.escape(label)}\b")
    hits = []
    for base in (ROOT, ROOT.parent / "scripts"):
        for p in base.rglob("*.py"):
            rel = p.relative_to(ROOT.parent).as_posix()
            if ".venv" in rel or rel.endswith("graph_migration.py") or rel in allowed or "/migrations/" in rel:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}")
    return hits


def label_report(spec: LabelSpec) -> dict:
    props = graph_props(spec)
    rows = pg_rows(spec)
    par = bridge.parity(_canon_graph(spec, props), _canon_pg(spec, rows), spec.key)
    seen = sorted({k for p in props for k in p})
    unmapped = [k for k in seen if k not in spec.mapped_keys and k not in spec.dropped_keys]
    edges = []
    for e in spec.edges:
        from graph import q1
        g = (q1(e.graph_count) or {}).get("n", 0)
        p = (db.fetch1(e.pg_count) or {}).get("n", 0)
        edges.append({"edge": e.name, "graph": g, "pg": p, "ok": g == p})
    paths = read_paths(spec.label, spec.allowed_readers)
    ok = par["ok"] and not unmapped and all(e["ok"] for e in edges)
    return {"label": spec.label, "table": spec.table, "ok": ok, **par, "graph_keys": seen, "unmapped_keys": unmapped,
            "dropped_keys": sorted(spec.dropped_keys), "edges": edges, "read_paths_remaining": paths}


# ---------- the freeze ----------

FREEZE_WATCH = 15.0  # seconds to watch other heartbeats: a live worker beats every ~10s
LOCK_KEY = 0x6D696772  # pg advisory lock: one cutover at a time across instances


def check_freeze(watch: float | None = None) -> dict:
    """Cutover needs a frozen source. This process hasn't started its worker yet (bootstrap runs first); no other
    instance may be live. A heartbeat newer than 60s that keeps advancing is a live instance -> refuse. One that stands
    still is a process that was just stopped (a restart) -> fine. Returns the evidence for the report."""
    import time
    from shared import jobs
    watch = FREEZE_WATCH if watch is None else watch
    sql = (f"SELECT worker, seen_at FROM worker_heartbeat WHERE seen_at > now() - interval '{HEARTBEAT_FRESH} seconds' "
           "AND worker <> %s")
    first = {r["worker"]: r["seen_at"] for r in db.fetch(sql, (jobs.WORKER_ID,))}
    deadline = time.monotonic() + watch
    live: list[str] = []
    while first and time.monotonic() < deadline and not live:
        time.sleep(min(0.5, watch))
        now = {r["worker"]: r["seen_at"] for r in db.fetch(sql, (jobs.WORKER_ID,))}
        live = [w for w, t in now.items() if w not in first or t > first[w]]
    if live:
        raise FreezeError(f"another instance is live ({', '.join(live)}): stop other instances, then restart")
    return {"single_writer": True, "checked_at": datetime.now(timezone.utc).isoformat(), "this_worker": jobs.WORKER_ID,
            "recent_stopped_heartbeats": sorted(first), "watched_seconds": watch if first else 0}


# ---------- cutover, drift, rollback ----------

def _state(label: str) -> dict | None:
    return db.fetch1("SELECT * FROM bridge_state WHERE record_type=%s", (label,))


def _set_state(label: str, source: str, report: dict, conn=None) -> None:
    db.execute("""INSERT INTO bridge_state (record_type, source, report, switched_at) VALUES (%s, %s, %s, now())
                  ON CONFLICT (record_type) DO UPDATE SET source=EXCLUDED.source, report=EXCLUDED.report, switched_at=now()""",
               (label, source, db.Json(report)), conn=conn)
    bridge.SOURCE[label] = source


def pending(batch: str) -> list[LabelSpec]:
    return [s for s in batch_labels(batch) if (_state(s.label) or {}).get("source") != "postgres"]


def cutover(batch: str, watch: float | None = None) -> dict:
    """Backfill, report and switch one batch. Raises (and switches nothing) unless every label is clean.
    Holds a Postgres advisory lock on a dedicated connection for the whole cutover."""
    todo = pending(batch)
    if not todo:
        return {"batch": batch, "already": True}
    with db.pool().connection() as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
            raise FreezeError(f"batch {batch}: another instance is cutting over right now; restart after it finishes")
        try:
            return _cutover(batch, todo, check_freeze(watch))
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _cutover(batch: str, todo: list[LabelSpec], freeze: dict) -> dict:
    before = {s.label: {"graph_count": len(graph_props(s)), "graph_sha": graph_checksum(s), "pg_count": len(pg_rows(s)),
                        "pg_sha": bridge.checksum(_canon_pg(s, pg_rows(s)), s.key)} for s in todo}
    with db.tx() as c:
        c.execute("SET LOCAL azentio.backfill = '1'")
        for s in todo:
            backfill(s, c)
    reports = [label_report(s) for s in todo]
    bad = [r for r in reports if not r["ok"] or r["read_paths_remaining"]]
    if bad:
        raise bridge.ParityError(f"batch {batch}: not clean, nothing switched: "
                                 + json.dumps([{k: r[k] for k in ('label', 'ok', 'first_mismatch_id', 'unmapped_keys',
                                                                     'edges', 'read_paths_remaining')} for r in bad]))
    at = datetime.now(timezone.utc).isoformat()
    for r in reports:
        _set_state(r["label"], "postgres", {**r, "batch": batch, "cutover_at": at, "before": before[r["label"]],
                                             "freeze": freeze, "graph_checksum_at_cutover": r["graph_sha"]})
    return {"batch": batch, "cutover_at": at, "freeze": freeze, "labels": reports, "before": before}


def drift_since_cutover() -> list[dict]:
    """For every switched label: has the graph side changed since cutover? (A stray writer of a retired label.)"""
    out = []
    for s in SPECS.values():
        st = _state(s.label)
        if not st or st["source"] != "postgres":
            continue
        then = (st["report"] or {}).get("graph_checksum_at_cutover")
        now = graph_checksum(s)
        out.append({"label": s.label, "batch": s.batch, "ok": then == now, "at_cutover": then, "now": now,
                    "cutover_at": (st["report"] or {}).get("cutover_at")})
    return out


def rollback(batch: str) -> dict:
    """Write the batch's Postgres rows back to the graph (including rows written after cutover), check parity,
    and switch the bridge back to the graph. Use with the previous release."""
    from graph import q
    reports = []
    for s in batch_labels(batch):
        rows = pg_rows(s)
        q(f"MATCH (n:{s.label}) WHERE NOT n.{s.key} IN $keep DETACH DELETE n", keep=[r[s.key] for r in rows])
        for r in rows:
            q(f"MERGE (n:{s.label} {{{s.key}: $k}}) SET n = $p", k=r[s.key], p=s.from_row(r))
        for e in s.edges:
            if e.restore:
                e.restore()
        rep = label_report(s)
        if not rep["ok"]:
            raise bridge.ParityError(f"rollback of {s.label} did not reach parity: {rep['first_mismatch_id']}")
        _set_state(s.label, "graph", {"rolled_back_at": datetime.now(timezone.utc).isoformat(), "parity": rep})
        reports.append(rep)
    return {"batch": batch, "labels": reports}


def full_report() -> dict:
    """Everything an auditor needs, from bridge_state plus a live re-check."""
    states = {r["record_type"]: r for r in db.fetch("SELECT * FROM bridge_state")}
    drift = {d["label"]: d for d in drift_since_cutover()}
    labels = []
    for s in SPECS.values():
        st = states.get(s.label) or {}
        rep = st.get("report") or {}
        labels.append({"label": s.label, "batch": s.batch, "table": s.table, "source": st.get("source", "graph"),
                       "switched_at": st["switched_at"].isoformat() if st.get("switched_at") else None,
                       "graph_count": rep.get("graph_count"), "pg_count": rep.get("pg_count"), "parity_ok": rep.get("ok"),
                       "unmapped_keys": rep.get("unmapped_keys"), "edges": rep.get("edges"),
                       "before": rep.get("before"), "cutover_at": rep.get("cutover_at"),
                       "drift_since_cutover": (drift.get(s.label) or {}).get("ok")})
    return {"labels": labels, "batches": batches(),
            "projection_types": {t: states.get(t, {}).get("source", "graph") for t in ("Employee", "Item", "Document")}}


# ---------- shared canonical helpers for specs ----------

def epoch(v) -> float | None:
    """Graph floats and Postgres timestamptz compare as epoch seconds (rounded to the millisecond)."""
    if v in (None, "", 0):
        return None
    if isinstance(v, datetime):
        return round(v.timestamp(), 3)
    return round(float(v), 3)


def ts(v) -> datetime | None:
    e = epoch(v)
    return datetime.fromtimestamp(e, timezone.utc) if e is not None else None


def jsonv(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v) if v else default
        except ValueError:
            return default
    return default if v is None else v


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Phase 3 graph -> Postgres migration")
    ap.add_argument("command", choices=["report", "rollback", "drift"])
    ap.add_argument("batch", nargs="?")
    ap.add_argument("--out", help="write the report JSON here")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent / ".env")
    db.init()
    bridge.load()
    if a.command == "report":
        out = full_report()
    elif a.command == "drift":
        out = drift_since_cutover()
    else:
        if not a.batch:
            sys.exit("rollback needs a batch name")
        out = rollback(a.batch)
    text = json.dumps(out, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)

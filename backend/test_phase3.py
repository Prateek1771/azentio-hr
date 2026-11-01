"""Phase 3 self-checks: the graph -> Postgres migration engine (freeze, cutover, report, drift, rollback) and the API
contract. Batches add their own checks below as they land.

The engine checks use a synthetic label (P3Probe) and a throwaway table, so they prove the machinery independently of
any real batch. Runs against a throwaway Postgres database and FalkorDB graph.
Usage: cd backend && uv run python test_phase3.py
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ["FALKORDB_GRAPH"] = "test_phase3"  # before anything imports graph
for k in ("OPENROUTER_API_KEY", "LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY", "GMAIL_REFRESH_TOKEN"):
    os.environ[k] = ""

from shared import db  # noqa: E402

db.use_test_database("azentio_test_p3")

from graph import q  # noqa: E402

q("MATCH (n) DETACH DELETE n")

import graph_migration as gm  # noqa: E402
from identity import perms  # noqa: E402
from shared import bridge, jobs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ok = 0


def check(name, cond, detail=""):
    global ok
    assert cond, f"{name} {detail}"
    ok += 1
    print(f"  ok  {name}")


def raises(fn, exc) -> str | None:
    try:
        fn()
    except exc as e:
        return str(e) or exc.__name__
    return None


# ---------- a synthetic label ----------
db.execute("DROP TABLE IF EXISTS p3_probe")
db.execute("CREATE TABLE p3_probe (id text PRIMARY KEY, name text NOT NULL, n int NOT NULL, created_at timestamptz, "
           "meta jsonb NOT NULL DEFAULT '{}')")
db.execute("DELETE FROM bridge_state WHERE record_type LIKE 'P3%%'")
db.execute("DELETE FROM worker_heartbeat")

ME = "backend/test_phase3.py"


def probe_spec(label="P3Probe", batch="probe", allowed=(ME,), dropped=frozenset({"legacy"})):
    return gm.register(gm.LabelSpec(
        label=label, batch=batch, table="p3_probe",
        graph_query=f"MATCH (n:{label}) RETURN n",
        to_row=lambda p: {"id": p["id"], "name": p.get("name") or "", "n": int(p.get("n") or 0),
                          "created_at": gm.ts(p.get("created_at")), "meta": gm.jsonv(p.get("meta"), {})},
        canonical=lambda r: {"id": r["id"], "name": r["name"], "n": r["n"], "created_at": gm.epoch(r["created_at"]),
                             "meta": r["meta"]},
        from_row=lambda r: {"id": r["id"], "name": r["name"], "n": r["n"], "created_at": gm.epoch(r["created_at"]) or 0,
                            "meta": __import__("json").dumps(r["meta"])},
        mapped_keys=frozenset({"id", "name", "n", "created_at", "meta"}), dropped_keys=dropped,
        edges=[gm.EdgeSpec("P3_LINK", f"MATCH (:{label})-[r:P3_LINK]->() RETURN count(r) AS n",
                           "SELECT count(*)::int AS n FROM p3_probe WHERE meta ? 'link'")],
        allowed_readers=allowed))


spec = probe_spec()
for i in range(5):
    q("CREATE (n:P3Probe {id:$id, name:$nm, n:$n, created_at:$t, meta:$m, legacy:'x'})",
      id=f"p{i}", nm=f"probe {i}", n=i, t=1_700_000_000.5 + i, m='{"a": %d}' % i)
q("CREATE (n:P3Probe {id:'p5', name:'', n:0, created_at:0, meta:''})")  # graph null sentinels

# ---------- report, schema diff, read paths ----------
rep = gm.label_report(spec)
check("report_before_backfill_shows_missing_rows", not rep["ok"] and rep["graph_count"] == 6 and rep["pg_count"] == 0
      and len(rep["missing_in_pg"]) == 6, rep)
check("schema_diff_dropped_keys_are_listed_not_unmapped", rep["unmapped_keys"] == [] and rep["dropped_keys"] == ["legacy"])
q("MATCH (n:P3Probe {id:'p0'}) SET n.surprise = 1")
check("schema_diff_catches_unmapped_key", gm.label_report(spec)["unmapped_keys"] == ["surprise"])
q("MATCH (n:P3Probe {id:'p0'}) REMOVE n.surprise")
check("read_path_scan_allows_listed_files", gm.read_paths("P3Probe", (ME,)) == [])
check("read_path_scan_finds_graph_reads", any(h.startswith(ME) for h in gm.read_paths("P3Probe")))

# ---------- freeze ----------
stop = threading.Event()


def beat():
    while not stop.is_set():
        jobs.heartbeat("other-host:42")
        time.sleep(0.2)


t = threading.Thread(target=beat, daemon=True)
t.start()
time.sleep(0.3)
err = raises(lambda: gm.cutover("probe", watch=1.5), gm.FreezeError)
stop.set()
t.join()
check("freeze_refuses_while_another_instance_is_live", err and "other-host:42" in err, err)
check("freeze_refusal_switches_nothing", bridge.SOURCE.get("P3Probe") != "postgres"
      and not db.fetch1("SELECT 1 AS x FROM p3_probe LIMIT 1"))
ev = gm.check_freeze(watch=1.0)  # the same heartbeat, no longer advancing: a stopped process (a restart)
check("freeze_allows_a_stopped_instance", ev["single_writer"] and "other-host:42" in ev["recent_stopped_heartbeats"], ev)

with db.pool().connection() as other:
    other.execute("SELECT pg_advisory_lock(%s)", (gm.LOCK_KEY,))
    err = raises(lambda: gm.cutover("probe", watch=0), gm.FreezeError)
    other.execute("SELECT pg_advisory_unlock(%s)", (gm.LOCK_KEY,))
check("cutover_refuses_while_another_cutover_holds_the_lock", err and "cutting over" in err, err)

# ---------- cutover refuses when not clean ----------
bad = probe_spec(label="P3Bad", batch="bad", allowed=())
LEFTOVER = "MATCH (n:P3Bad) RETURN n.name"  # a graph read nobody switched: the scan must find it and block the cutover
q("CREATE (n:P3Bad {id:'b0', name:'bad', n:1, created_at:0, meta:''})")
err = raises(lambda: gm.cutover("bad", watch=0), bridge.ParityError)
check("cutover_refuses_with_graph_read_paths_remaining", err and "read_paths_remaining" in err
      and (gm._state("P3Bad") or {}).get("source") != "postgres", err)
del gm.SPECS["P3Bad"]
db.execute("DELETE FROM p3_probe")

# ---------- cutover ----------
res = gm.cutover("probe", watch=0)
st = gm._state("P3Probe")
r = st["report"]
check("cutover_switches_after_parity", st["source"] == "postgres" and bridge.SOURCE["P3Probe"] == "postgres"
      and r["ok"] and r["graph_count"] == r["pg_count"] == 6 and r["graph_sha"] == r["pg_sha"], r)
check("cutover_report_has_before_freeze_and_checksum", r["before"]["graph_count"] == 6 and r["before"]["pg_count"] == 0
      and r["freeze"]["single_writer"] and r["graph_checksum_at_cutover"] == r["graph_sha"] and r["cutover_at"])
check("cutover_maps_graph_null_sentinels", db.fetch1("SELECT * FROM p3_probe WHERE id='p5'")["created_at"] is None
      and db.fetch1("SELECT meta FROM p3_probe WHERE id='p5'")["meta"] == {})
check("cutover_is_idempotent", gm.cutover("probe", watch=0) == {"batch": "probe", "already": True})

# ---------- drift since cutover ----------
check("no_drift_after_clean_cutover", all(d["ok"] for d in gm.drift_since_cutover()))
q("MATCH (n:P3Probe {id:'p1'}) SET n.name = 'stray write'")
d = [x for x in gm.drift_since_cutover() if x["label"] == "P3Probe"][0]
check("drift_catches_a_stray_graph_write", not d["ok"] and d["at_cutover"] != d["now"], d)
q("MATCH (n:P3Probe {id:'p1'}) SET n.name = 'probe 1'")

# ---------- report endpoint ----------
fr = gm.full_report()
row = [x for x in fr["labels"] if x["label"] == "P3Probe"][0]
check("full_report_lists_label", row["source"] == "postgres" and row["parity_ok"] and row["drift_since_cutover"]
      and row["before"]["graph_count"] == 6, row)
check("migration_report_admin_and_auditor_only", perms.allowed("GET", "/api/admin/migration", "auditor")
      and perms.allowed("GET", "/api/admin/migration", "admin") and not perms.allowed("GET", "/api/admin/migration", "hr_ops"))

# ---------- rollback round trip ----------
db.execute("INSERT INTO p3_probe (id, name, n, created_at, meta) VALUES ('p9', 'written after cutover', 9, now(), '{}')")
db.execute("UPDATE p3_probe SET name='edited after cutover' WHERE id='p2'")
rb = gm.rollback("probe")
g = {x["n"]["id"]: x["n"] for x in q("MATCH (n:P3Probe) RETURN n")}
check("rollback_writes_postgres_rows_back", "p9" in g and g["p2"]["name"] == "edited after cutover"
      and rb["labels"][0]["ok"], rb)
check("rollback_switches_bridge_back", gm._state("P3Probe")["source"] == "graph" and bridge.SOURCE["P3Probe"] == "graph")
q("MATCH (n:P3Probe {id:'p3'}) SET n.n = 33")  # the previous release writes the graph again
res = gm.cutover("probe", watch=0)
r = gm._state("P3Probe")["report"]
check("cutover_again_after_rollback", gm._state("P3Probe")["source"] == "postgres" and r["graph_count"] == 7
      and db.fetch1("SELECT n FROM p3_probe WHERE id='p3'")["n"] == 33, r)

del gm.SPECS["P3Probe"]
db.execute("DROP TABLE p3_probe")
db.execute("DELETE FROM bridge_state WHERE record_type LIKE 'P3%%'")

# ---------- API contract ----------
out = subprocess.run([sys.executable, str(ROOT / "scripts" / "api_contract.py"), "--check"], capture_output=True, text=True)
check("api_contract_unchanged", out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:])

print(f"\nphase 3: {ok} checks passed")

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
rx = gm.label_rx("P3Scan")
SCAN = {'MATCH (o:P3Scan) RETURN o': True, 'MATCH (:P3Scan {id:$i})': True, 'MATCH (e)-[:X]->(o:P3Scan)': True,
        '-[:SCANNED_BY]->(o:P3Scan) RETURN o': True, 'MATCH (o) WHERE o:P3Scan': True, 'match (o) where o:P3Scan': True,
        'MATCH (o:Person:P3Scan)': True, 'MERGE (o:P3Scan {id:$i})': True, 'MATCH (o:P3ScanHistory)': False,
        'def f(o: P3Scan) -> P3Scan:': False, 'MATCH ()-[r:P3Scan]->()': False, 'MATCH (o:p3scan)': False}
wrong = {s: w for s, w in SCAN.items() if bool(rx.search(s)) != w}
check("read_path_scan_patterns", not wrong, wrong)
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
jobs.heartbeat("live-app:7")
stop.clear()
t = threading.Thread(target=lambda: [jobs.heartbeat("live-app:7") or time.sleep(0.2) for _ in range(8)], daemon=True)
t.start()
err = raises(lambda: gm.rollback("probe", watch=1.5), gm.FreezeError)
t.join()
check("rollback_refuses_while_the_app_is_live", err and "live-app:7" in err
      and gm._state("P3Probe")["source"] == "postgres", err)
rb = gm.rollback("probe", watch=0)
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

# ---------- batch A1: Owner (Postgres + graph projection), EmailTemplate (retired from the graph) ----------
gm.load()
import gmail  # noqa: E402
import owners  # noqa: E402
import templates  # noqa: E402

OWN, TPL = "Owner", "EmailTemplate"  # labels built at runtime: this file plays the old release, not a read path
db.execute("DELETE FROM bridge_state WHERE record_type IN ('Owner', 'EmailTemplate')")
db.execute("TRUNCATE owners, email_templates")
q(f"MATCH (n:{OWN}) DETACH DELETE n")
q(f"MATCH (n:{TPL}) DETACH DELETE n")
for e, n, d, t in [("Mgr.Eng@x.com", "Asha", "Engineering", "manager"), ("it@x.com", "IT desk", "IT", "owner:IT"),
                   ("fac@x.com", "Facilities", "Admin", "owner:Facilities")]:
    q(f"MERGE (o:{OWN} {{email:$e}}) SET o.name=$n, o.department=$d, o.type=$t", e=e.lower(), n=n, d=d, t=t)
import records  # noqa: E402
q(f"CREATE (o:{OWN} {{email:'Mixed.Case@x.com', name:'Mixed', department:'Sales', type:'manager'}})")  # older code
records.save_employee({"id": "E9100", "name": "Reports To Mixed", "email": "rtm@x.com", "role": "Engineer", "grade": "L2",
                       "department": "Sales", "location": "Bengaluru", "manager_email": "mixed.case@x.com",
                       "joining_date": "2026-12-01", "token": "tok-e9100", "thread_id": ""})
q(f"CREATE (t:{TPL} {{id:'standard', name:'Standard', subject:'Hi {{{{name}}}}', body:'<p>{{{{doc_list}}}}</p>'}})")
q(f"CREATE (t:{TPL} {{id:'intern', name:'Intern', subject:'Hey', body:'b'}})")

check("a1_has_no_graph_read_paths_left", gm.read_paths(OWN, ("backend/records.py",)) == [] and gm.read_paths(TPL) == [],
      (gm.read_paths(OWN, ("backend/records.py",)), gm.read_paths(TPL)))
res = gm.cutover("A1", watch=0)
reps = {r["label"]: r for r in res["labels"]}
check("a1_cutover_clean", all(r["ok"] and not r["unmapped_keys"] for r in reps.values())
      and reps[OWN]["pg_count"] == 4 and reps[TPL]["pg_count"] == 2 and bridge.SOURCE[OWN] == "postgres", reps)
check("a1_owners_read_from_postgres", owners.manager_for("Engineering") == "mgr.eng@x.com"
      and set(owners.by_category()) == {"IT", "Facilities"} and owners.get("MGR.ENG@x.com")["name"] == "Asha")
check("a1_template_renders_from_postgres", gmail.render("standard", {"name": "Ravi", "doc_list": "<ul></ul>"})
      == ("Hi Ravi", "<p><ul></ul></p>"))

owners.upsert([{"email": "hr.ops@x.com", "name": "Neha", "department": "HR", "type": "manager"},
               {"email": "it@x.com", "name": "IT service desk", "department": "IT", "type": "owner:IT"}])
templates.upsert({"id": "welcome", "name": "Welcome", "subject": "s", "body": "b"})
check("a1_cutover_reprojects_legacy_owner_keys", q(f"MATCH (:Employee {{id:'E9100'}})-[:REPORTS_TO]->(o:{OWN}) "
                                                   "RETURN o.email AS e") == [{"e": "mixed.case@x.com"}])
g = {r["n"]["email"]: r["n"] for r in q(f"MATCH (n:{OWN}) RETURN n")}
check("a1_owner_write_keeps_reports_to_edges", q(f"MATCH (:Employee {{id:'E9100'}})-[:REPORTS_TO]->(o:{OWN}) "
                                                 "RETURN o.email AS e") == [{"e": "mixed.case@x.com"}])
check("a1_owner_projection_follows_postgres", g.get("hr.ops@x.com", {}).get("name") == "Neha"
      and g["it@x.com"]["name"] == "IT service desk")
dr = {d["label"]: d for d in gm.drift_since_cutover()}
check("a1_no_drift_after_writes_through_the_app", dr[OWN]["ok"] and dr[TPL]["ok"], dr)
q(f"MATCH (t:{TPL} {{id:'intern'}}) SET t.subject = 'stray'")
check("a1_drift_catches_a_stray_template_write", not [d for d in gm.drift_since_cutover() if d["label"] == TPL][0]["ok"])
q(f"MATCH (t:{TPL} {{id:'intern'}}) SET t.subject = 'Hey'")
q(f"MATCH (o:{OWN} {{email:'it@x.com'}}) SET o.name = 'stray'")
check("a1_drift_catches_projection_out_of_step", not [d for d in gm.drift_since_cutover() if d["label"] == OWN][0]["ok"])
records.rebuild_graph()  # the repair path: re-project owners from Postgres
check("a1_rebuild_repairs_owner_projection", [d for d in gm.drift_since_cutover() if d["label"] == OWN][0]["ok"])

rb = gm.rollback("A1", watch=0)
gt = {r["n"]["id"] for r in q(f"MATCH (n:{TPL}) RETURN n")}
check("a1_rollback_writes_new_rows_back", "welcome" in gt and all(r["ok"] for r in rb["labels"])
      and bridge.SOURCE[TPL] == "graph", rb)
res = gm.cutover("A1", watch=0)
check("a1_cutover_again_after_rollback", all(r["ok"] for r in res["labels"]) and bridge.SOURCE[TPL] == "postgres"
      and len(templates.all()) == 3 and len(owners.all()) == 5)

# ---------- batch A2: Policy (retired from the graph); PolicyChunk stays as an index rebuilt from Postgres ----------
import asyncio  # noqa: E402
import chat  # noqa: E402
import policies  # noqa: E402

POL, CHUNK = "Policy", "PolicyChunk"
db.execute("DELETE FROM bridge_state WHERE record_type='Policy'")
db.execute("TRUNCATE policies")
q(f"MATCH (n:{POL}) DETACH DELETE n")
q(f"MATCH (n:{CHUNK}) DETACH DELETE n")
NOTICE = "# Exit policy\n\n## Notice period\nThe notice period is 30 days.\n\n## Handover\nHand over your laptop.\n"
q(f"CREATE (p:{POL} {{source:'exit.md', title:'Exit policy', text:$t}})", t=NOTICE)
q(f"CREATE (p:{POL} {{source:'coc.md', title:'Code of conduct', text:'# Code of conduct\n\n## Respect\nBe kind.'}})")

check("a2_has_no_graph_read_paths_left", gm.read_paths(POL) == [], gm.read_paths(POL))
res = gm.cutover("A2", watch=0)
check("a2_cutover_clean", res["labels"][0]["ok"] and res["labels"][0]["pg_count"] == 2
      and bridge.SOURCE[POL] == "postgres", res["labels"])
check("a2_policies_read_from_postgres", [p["source"] for p in policies.all()] == ["coc.md", "exit.md"]
      and policies.texts()["exit.md"] == NOTICE)

asyncio.run(chat.index_policy("leave.md", "# Leave policy\n\n## Annual leave\nYou get 20 days of annual leave.\n"))
check("a2_index_writes_table_and_chunks_not_policy_nodes", policies.texts().get("leave.md")
      and q(f"MATCH (c:{CHUNK} {{source:'leave.md'}}) RETURN count(c) AS n")[0]["n"] == 1
      and not q(f"MATCH (p:{POL} {{source:'leave.md'}}) RETURN p"))
check("a2_no_drift_after_upload", [d for d in gm.drift_since_cutover() if d["label"] == POL][0]["ok"])
q(f"MATCH (p:{POL} {{source:'coc.md'}}) SET p.text = 'stray'")
check("a2_drift_catches_a_stray_policy_write", not [d for d in gm.drift_since_cutover() if d["label"] == POL][0]["ok"])
q(f"MATCH (p:{POL} {{source:'coc.md'}}) SET p.text = '# Code of conduct\n\n## Respect\nBe kind.'")

asyncio.run(chat.reindex_policies())  # chunks for every row: exit.md (2), coc.md (1), leave.md (1)
before = [(c["source"], c["section"]) for c in chat.keyword_retrieve("notice period days", 2)]
q(f"MATCH (c:{CHUNK}) DELETE c")
check("a2_wiped_index_retrieves_nothing", chat.keyword_retrieve("notice period days", 2) == [])
n = asyncio.run(chat.reindex_policies())
after = [(c["source"], c["section"]) for c in chat.keyword_retrieve("notice period days", 2)]
check("a2_reindex_rebuilds_from_postgres", n == 3 and after == before and after[0] == ("exit.md", "Notice period")
      and q(f"MATCH (c:{CHUNK}) RETURN count(c) AS n")[0]["n"] == 4, (before, after))

rb = gm.rollback("A2", watch=0)
check("a2_rollback_writes_new_rows_back", rb["labels"][0]["ok"] and bridge.SOURCE[POL] == "graph"
      and q(f"MATCH (p:{POL} {{source:'leave.md'}}) RETURN p.title AS t")[0]["t"] == "Leave policy", rb)
res = gm.cutover("A2", watch=0)
check("a2_cutover_again_after_rollback", res["labels"][0]["ok"] and res["labels"][0]["pg_count"] == 3
      and bridge.SOURCE[POL] == "postgres")

# ---------- batch A3: legacy Rule/Config retire; the requirements import writes rule-set versions ----------
import json  # noqa: E402
import checklist  # noqa: E402
import ingest  # noqa: E402
from identity import service as idsvc  # noqa: E402
from policy import store as pstore  # noqa: E402

RULE, CONF = "Rule", "Config"
REQ = (ROOT / "backend" / "seed" / "requirements.json").read_bytes()
SEEDREQ = json.loads(REQ)
db.execute("DELETE FROM bridge_state WHERE record_type IN ('Rule', 'Config')")
db.execute("TRUNCATE legacy_rules, legacy_config")
q(f"MATCH (n) WHERE n:{RULE} OR n:{CONF} DETACH DELETE n")
for i, r in enumerate(SEEDREQ["rules"], start=1):  # exactly what the previous release's import wrote
    q(f"MERGE (r:{RULE} {{id:$id}}) SET r += $p", id=r["id"],
      p={"id": r["id"], "item": r["item"], "name": r.get("name", ""), "kind": r["kind"], "category": r["category"],
         "match": json.dumps(r.get("match") or {}), "offset_days": int(r.get("offset_days") or 0),
         "depends_on": list(r.get("depends_on") or []), "description": r["description"],
         "policy_ref": r.get("policy_ref", ""), "auto": r.get("auto", ""), "ord": i})
for k in ("departments", "locations", "doc_types", "roles"):
    q(f"MERGE (c:{CONF} {{key:$k}}) SET c.value=$v", k=k, v=json.dumps(SEEDREQ[k]))

check("a3_has_no_graph_read_paths_left", gm.read_paths(RULE) == [] and gm.read_paths(CONF) == [],
      (gm.read_paths(RULE), gm.read_paths(CONF)))
res = gm.cutover("A3", watch=0)
reps = {r["label"]: r for r in res["labels"]}
check("a3_cutover_archives_legacy_nodes", all(r["ok"] and not r["unmapped_keys"] for r in reps.values())
      and reps[RULE]["pg_count"] == len(SEEDREQ["rules"]) and reps[CONF]["pg_count"] == 4, reps)

check("a3_no_active_version_yet", pstore.active_version() is None and checklist.config("roles") is None)
db.execute("UPDATE legacy_rules SET props = jsonb_set(props, '{description}', to_jsonb(%s::text)) WHERE id='R-PAN'",
           ("HR's own PAN rule",))
outs = []
ts = [threading.Thread(target=lambda: outs.append(ingest.seed_rules())) for _ in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
out = max(outs, key=lambda o: o["imported"])
check("a3_racing_starts_create_one_v1", sorted(o["imported"] for o in outs) == [0, len(SEEDREQ["rules"])]
      and db.fetch1("SELECT count(*) AS n FROM rule_set_versions")["n"] == 1
      and db.fetch1("SELECT count(*) AS n FROM audit_log WHERE action='policy.published' AND entity_id='v1'")["n"] == 1, outs)
v1 = pstore.active_version()
check("a3_upgrade_keeps_hr_rules_from_the_archive", "legacy-graph-rules" in v1["title"] and next(
    r for r in pstore.rules_of(v1["id"]) if r["id"] == "R-PAN")["description"] == "HR's own PAN rule"
    and [r["id"] for r in pstore.rules_of(v1["id"])] == [r["id"] for r in SEEDREQ["rules"]], v1["title"])
check("a3_seed_rules_is_a_noop_once_active", ingest.seed_rules() is None)
v1_rules = pstore.rules_of(v1["id"])
check("a3_import_creates_v1_in_postgres", v1 and v1["number"] == 1 and len(v1_rules) == len(SEEDREQ["rules"])
      and out["imported"] == len(SEEDREQ["rules"]) and not out["errors"], out)
check("a3_csv_compliance_text", [pstore.normalise_rule({**SEEDREQ["rules"][0], "compliance": c})["compliance"]
                                  for c in ("false", "no", "0", " ", "TRUE", "yes", "1", True)]
      == [False, False, False, False, True, True, True, True])
check("a3_v1_compliance_flags", {r["id"] for r in v1_rules if r["compliance"]} == pstore.COMPLIANCE_DEFAULT)
check("a3_config_comes_from_the_version", checklist.config("roles") == SEEDREQ["roles"]
      and checklist.config("departments") == SEEDREQ["departments"] and checklist.config("doc_policy") == {})
check("a3_v1_audited_as_system", db.fetch1("SELECT actor FROM audit_log WHERE action='policy.published' AND entity_id='v1' "
                                           "ORDER BY seq DESC LIMIT 1")["actor"] == "system")
check("a3_import_writes_no_graph_rules", q(f"MATCH (r:{RULE}) RETURN count(r) AS n")[0]["n"] == len(SEEDREQ["rules"])
      and all(d["ok"] for d in gm.drift_since_cutover() if d["label"] in (RULE, CONF)))
e = raises(lambda: ingest.import_requirements("requirements.json", REQ), ValueError)
check("a3_import_without_user_refused_once_active", e and "import page" in e, e)
hr3 = idsvc.create_user("hr3@t.io", "HR Three", "hr_ops", "hr3-pass-12345", actor="test")
d = ingest.import_requirements("requirements.json", REQ, hr3)
check("a3_import_with_active_version_opens_a_draft", d.get("draft_version") == 2
      and pstore.active_version()["number"] == 1 and pstore.open_version()["number"] == 2, d)
check("a3_reimport_keeps_compliance_flags", {r["id"] for r in pstore.rules_of(d["draft_id"]) if r["compliance"]}
      == pstore.COMPLIANCE_DEFAULT)
e = raises(lambda: ingest.import_requirements("requirements.json", REQ, hr3), ValueError)
check("a3_second_draft_refused", e and "already open" in e, e)
pstore.discard_draft(d["draft_id"], hr3)

q(f"MATCH (r:{RULE} {{id:'R-ID'}}) SET r.description = 'stray'")
check("a3_drift_catches_a_stray_rule_write", not [x for x in gm.drift_since_cutover() if x["label"] == RULE][0]["ok"])
q(f"MATCH (r:{RULE} {{id:'R-ID'}}) DETACH DELETE r")  # the stray write and the node are gone: rollback must restore
rb = gm.rollback("A3", watch=0)
check("a3_rollback_restores_legacy_nodes", all(r["ok"] for r in rb["labels"]) and bridge.SOURCE[RULE] == "graph"
      and q(f"MATCH (r:{RULE} {{id:'R-ID'}}) RETURN r.match AS m")[0]["m"] == json.dumps(
          next(r for r in SEEDREQ["rules"] if r["id"] == "R-ID").get("match") or {}), rb)
res = gm.cutover("A3", watch=0)
check("a3_cutover_again_after_rollback", all(r["ok"] for r in res["labels"]) and bridge.SOURCE[CONF] == "postgres")

# ---------- batch B: activity, guardrail hits and eval runs move to Postgres ----------
import evals  # noqa: E402
import events  # noqa: E402
import guardrails  # noqa: E402
import quality_routes  # noqa: E402
from graph import event  # noqa: E402

EV, GR, RUN, RES = "Event", "GuardrailEvent", "EvalRun", "EvalResult"
db.execute("DELETE FROM bridge_state WHERE record_type IN ('Event', 'GuardrailEvent', 'EvalRun', 'EvalResult')")
db.execute("TRUNCATE events, guardrail_events, eval_results, eval_runs")
q(f"MATCH (n) WHERE n:{EV} OR n:{GR} OR n:{RUN} OR n:{RES} DETACH DELETE n")
now = time.time()
q(f"CREATE (:{EV} {{id:'ev_old1', employee_id:'E9100', kind:'profile', message:'Onboarding record created', ts:$t}})", t=now - 60)
q(f"CREATE (:{EV} {{id:'ev_old2', employee_id:'', kind:'ai', message:'Assistant answered', ts:$t}})", t=now - 30)
q(f"CREATE (:{GR} {{id:'gr_old', ts:$t, surface:'portal', rule:'injection', action:'blocked'}})", t=now - 20)
q(f"""CREATE (:{RUN} {{id:'eval_old', ts:$t, status:'succeeded', judge:'j', model:'m', summary:'{{"faithfulness": 0.9}}',
       done:2, total:2, finished:$f}})""", t=now - 100, f=now - 90)
q(f"CREATE (:{RUN} {{id:'eval_stale', ts:$t, status:'running', judge:'j', model:'m', summary:'{{}}', done:0, total:2}})",
  t=now - 99999)  # no finished/error: the graph simply had no such keys
q(f"""CREATE (:{RES} {{id:'er_old1', run_id:'eval_old', n:1, question:'q1', reference:'r', answer:'a', ms:120,
       guardrail_ok:true, answered:true, faithfulness:0.9, context_precision:1.0, context_recall:0.5,
       sources:'["x.md § A"]'}})""")
q(f"""CREATE (:{RES} {{id:'er_old2', run_id:'eval_old', n:2, question:'q2', reference:'r', answer:'a', ms:80,
       guardrail_ok:true, sources:'[]'}})""")  # an older result: no answered/metrics keys

def old_overview():  # what the previous release's quality_routes.overview returned, read straight from the graph
    runs = [dict(r["r"], summary=json.loads(r["r"].get("summary") or "{}"))
            for r in q(f"MATCH (r:{RUN}) RETURN r ORDER BY r.ts DESC LIMIT 20")]
    latest = next((r for r in runs if r["status"] == "succeeded"), None)
    results = [dict(x["x"], sources=json.loads(x["x"].get("sources") or "[]"))
               for x in q(f"MATCH (x:{RES} {{run_id:$id}}) RETURN x ORDER BY x.n", id=latest["id"])] if latest else []
    return {"runs": runs, "latest": latest, "results": results}


before_api = old_overview()
check("b_has_no_graph_read_paths_left", all(gm.read_paths(lbl) == [] for lbl in (EV, GR, RUN, RES)))
res = gm.cutover("B", watch=0)
reps = {r["label"]: r for r in res["labels"]}
check("b_cutover_clean", all(r["ok"] and not r["unmapped_keys"] for r in reps.values())
      and [reps[lbl]["pg_count"] for lbl in (EV, GR, RUN, RES)] == [2, 1, 2, 2], reps)

after_api = quality_routes.overview()
check("b_quality_overview_same_as_before", {k: after_api[k] for k in before_api} == before_api
      and after_api["latest"]["summary"] == {"faithfulness": 0.9} and [r["n"] for r in after_api["results"]] == [1, 2]
      and "answered" not in after_api["results"][1] and "finished" not in after_api["runs"][1]
      and after_api["results"][0]["sources"] == ["x.md § A"], (before_api, after_api))

event("E9100", "profile", "Joining date moved")
check("b_event_choke_point_writes_postgres_not_graph",
      events.for_employee("E9100")[0]["message"] == "Joining date moved"
      and q(f"MATCH (v:{EV}) RETURN count(v) AS n")[0]["n"] == 2
      and [d for d in gm.drift_since_cutover() if d["label"] == EV][0]["ok"])
rec = events.recent(10)
check("b_recent_joins_names_and_filters_kind", rec[0]["employee_name"] == "Reports To Mixed"
      and rec[0]["manager_email"] == "mixed.case@x.com" and any(r["kind"] == "ai" for r in rec)
      and not any(r["kind"] == "ai" for r in events.recent(10, exclude_kind="ai")))
guardrails.record("hr_copilot", "email_leak", "noted")
check("b_guardrail_hit_recorded", {(r["rule"], r["n"]) for r in guardrails.by_rule(now - 3600)}
      == {("injection", 1), ("email_leak", 1)} and guardrails.recent(1)[0]["rule"] == "email_leak")

check("b_stale_running_run_is_not_running", evals.running() is None)
rid = evals.start_run()
check("b_start_run_is_running", evals.running() == {"id": rid})
err = raises(lambda: asyncio.run(evals.run(rid)), Exception)  # the seed joiner isn't here yet: the run fails
run = next(r for r in evals.runs() if r["id"] == rid)
check("b_failed_run_recorded", err and run["status"] == "failed" and err in run.get("error", "") and run.get("finished")
      and run["total"] > 0 and evals.running() is None, (err, run))
evals._insert_result({"id": "er_new1", "run_id": rid, "n": 1, "question": "q", "reference": "r", "answer": "a", "ms": 42,
                      "guardrail_ok": True, "answered": False, "faithfulness": None, "context_precision": None,
                      "context_recall": None, "sources": ["x.md § A"]})
nres = len(evals.results(rid))
r1 = evals.results(rid)[0]
check("b_result_row_keeps_graph_shape", r1["ms"] == 42 and isinstance(r1["ms"], int) and r1["sources"] == ["x.md § A"]
      and "faithfulness" not in r1 and r1["answered"] is False, r1)

q(f"MATCH (v:{EV} {{id:'ev_old1'}}) SET v.message = 'stray'")
check("b_drift_catches_a_stray_event_write", not [d for d in gm.drift_since_cutover() if d["label"] == EV][0]["ok"])
rb = gm.rollback("B", watch=0)
g_ev = {r["i"] for r in q(f"MATCH (v:{EV}) RETURN v.id AS i")}
g_res = q(f"MATCH (x:{RES} {{run_id:$r}}) RETURN count(x) AS n", r=rid)[0]["n"]
check("b_rollback_writes_new_rows_back", all(r["ok"] for r in rb["labels"]) and len(g_ev) == 3 and g_res == nres
      and q(f"MATCH (v:{EV} {{id:'ev_old1'}}) RETURN v.message AS m")[0]["m"] == "Onboarding record created"
      and q(f"MATCH (r:{RUN} {{id:$r}}) RETURN r.status AS s", r=rid)[0]["s"] == "failed", rb)
res = gm.cutover("B", watch=0)
check("b_cutover_again_after_rollback", all(r["ok"] for r in res["labels"]) and bridge.SOURCE[EV] == "postgres")

# ---------- batch C: connectors move to Postgres; connection secrets are encrypted at rest ----------
from cryptography.fernet import Fernet  # noqa: E402
from fastapi import HTTPException  # noqa: E402
import connector_routes as croutes  # noqa: E402
from connectors import secrets as csecrets, store as cstore  # noqa: E402
from connectors.candidates import CandidateImport, CandidatePortal  # noqa: E402
from connectors.hr_portal import HrPortal, push_employee  # noqa: E402

CN, CI, REC, CF, IMP, IRUN = "Connector", "ConnectorInstance", "Record", "CustomField", "ImportRun", "IntegrationRun"
C_LABELS = (CN, CI, REC, CF, IMP, IRUN)
db.execute("DELETE FROM bridge_state WHERE record_type = ANY(%s)", (list(C_LABELS),))
db.execute("TRUNCATE employee_record_links, connector_records, custom_fields, import_runs, legacy_sync_runs, "
           "connector_instances, connectors CASCADE")
q("MATCH (n) WHERE " + " OR ".join(f"n:{lbl}" for lbl in C_LABELS) + " DETACH DELETE n")
os.environ["SECRETS_KEY"] = os.environ["SECRETS_KEY_PREVIOUS"] = ""  # dev: the built-in dev key
PLAIN = "tok-supersecret-1234"
for key, role, kind in (("candidate_portal", "source", "candidate"), ("hr_portal", "target", "employee"), ("slack", "app", "activity")):
    q(f"CREATE (:{CN} {{key:$k, name:$k, role:$r, record_kind:$rk, demo:true, description:'d'}})", k=key, r=role, rk=kind)
q(f"MATCH (c:{CN} {{key:'candidate_portal'}}) CREATE (:{CI} {{id:'ci_cp', key:'candidate_portal', name:'Candidate Portal', "
  f"created:$t}})-[:OF]->(c)", t=now - 500)
q(f"MATCH (c:{CN} {{key:'hr_portal'}}) CREATE (:{CI} {{id:'ci_hr', key:'hr_portal', name:'HR Portal', created:$t, "
  f"connection:$c, secret:$s}})-[:OF]->(c)", t=now - 400, s=PLAIN,
  c=json.dumps({"tenant": "azentio-prod", "auth_type": "token", "username": "", "configured_at": now - 300, "last_test": None}))
q(f"MATCH (c:{CN} {{key:'slack'}}) CREATE (:{CI} {{id:'ci_sl', key:'slack', name:'Slack', created:$t, connection:'', "
  f"secret:''}})-[:OF]->(c)", t=now - 300)
q(f"MATCH (i:{CI} {{id:'ci_cp'}}) CREATE (:{REC} {{id:'rec_c1', instance_id:'ci_cp', kind:'candidate', values:$v, "
  f"created:$t, updated:$t}})-[:IN]->(i)", t=now - 200, v=json.dumps(
      {"candidate_id": "C-1", "first_name": "Ananya", "last_name": "K", "email": "ananya@x.com", "status": "hired",
       "education": ["B.Tech"]}))
q(f"MATCH (i:{CI} {{id:'ci_hr'}}) CREATE (:{REC} {{id:'rec_h1', instance_id:'ci_hr', kind:'employee', values:$v, "
  f"created:$t, updated:$t}})-[:IN]->(i)", t=now - 190, v=json.dumps(
      {"employee_number": "EMP-1", "first_name": "Old", "last_name": "Sync", "work_email": "old.sync@x.com"}))
q(f"MATCH (e:Employee {{id:'E9100'}}), (r:{REC} {{id:'rec_c1'}}) CREATE (e)-[:FROM_CANDIDATE]->(r)")
q(f"MATCH (e:Employee {{id:'E9100'}}), (r:{REC} {{id:'rec_h1'}}) CREATE (e)-[:SYNCED_TO]->(r)")
q(f"CREATE (:{CF} {{instance_id:'ci_hr', key:'badge', label:'Badge', type:'string', required:false, created:$t}})", t=now - 150)
q(f"CREATE (:{IMP} {{id:'imp_old', instance_id:'ci_cp', filename:'old.csv', total:2, imported:1, errors:$e, ts:$t}})",
  t=now - 140, e=json.dumps([{"row": 2, "name": "Bad", "field": "email", "message": "missing"}]))
q(f"CREATE (:{IRUN} {{id:'run_old', source_id:'ci_cp', target_id:'ci_hr', source_name:'Candidate Portal', "
  f"target_name:'HR Portal', mapping:'{{}}', policy:'update', counts:'{{}}', results:'[]', ts:$t}})", t=now - 130)


def api_view():  # what the connector endpoints return (shapes and values HR sees)
    return {"list": croutes.list_connectors(), "records": croutes.connector_records("ci_cp", 1, 25, ""),
            "record": croutes.connector_record("ci_hr", "rec_h1"), "links": croutes.employee_links("E9100"),
            "conn": croutes.get_connection("ci_hr"), "imports": croutes.connector_imports("ci_cp"),
            "schema": croutes.connector_schema("ci_hr")}


check("c_has_no_graph_read_paths_left", all(gm.read_paths(lbl) == [] for lbl in C_LABELS),
      {lbl: gm.read_paths(lbl) for lbl in C_LABELS})
res = gm.cutover("C", watch=0)
reps = {r["label"]: r for r in res["labels"]}
check("c_cutover_clean", all(r["ok"] and not r["unmapped_keys"] for r in reps.values())
      and [reps[lbl]["pg_count"] for lbl in C_LABELS] == [3, 3, 2, 1, 1, 1]
      and {e["edge"]: (e["graph"], e["pg"]) for e in reps[REC]["edges"]} == {"IN": (2, 2), "SYNCED_TO": (1, 1), "FROM_CANDIDATE": (1, 1)}
      and reps[CI]["edges"][0] == {"edge": "OF", "graph": 3, "pg": 3, "ok": True}, reps)
check("c_report_never_holds_the_secret", PLAIN not in json.dumps(res, default=str)
      and PLAIN not in json.dumps(db.fetch("SELECT report FROM bridge_state"), default=str))

row = db.fetch1("SELECT * FROM connector_instances WHERE id='ci_hr'")
check("c_secret_encrypted_at_rest", row["secret_enc"] and PLAIN not in row["secret_enc"]
      and row["secret_key_id"] == csecrets.key_id(csecrets.DEV_KEY) and row["secret_hint"] == "••••1234"
      and cstore.secret("ci_hr") == PLAIN, row)
view = api_view()
lst = {c["id"]: c for c in view["list"]}
check("c_connectors_list_from_postgres", list(lst) == ["ci_cp", "ci_hr", "ci_sl"]
      and lst["ci_hr"]["connection_status"] == "untested" and lst["ci_cp"]["record_count"] == 1
      and lst["ci_sl"]["connection_status"] == "not_configured" and lst["ci_hr"]["type_name"] == "hr_portal", lst)
check("c_connection_shows_hint_never_secret", view["conn"]["secret_set"] and view["conn"]["secret_hint"] == "••••1234"
      and view["conn"]["tenant"] == "azentio-prod" and PLAIN not in json.dumps(view, default=str), view["conn"])
check("c_records_and_links_from_postgres", view["records"]["items"][0]["employee_id"] == "E9100"
      and view["records"]["items"][0]["values"]["education"] == ["B.Tech"]
      and view["record"]["employee"]["id"] == "E9100"
      and sorted((x["type"], x["record_id"]) for x in view["links"]) == [("FROM_CANDIDATE", "rec_c1"), ("SYNCED_TO", "rec_h1")]
      and next(x for x in view["links"] if x["type"] == "SYNCED_TO")["employee_number"] == "EMP-1", view)
check("c_imports_and_custom_fields_from_postgres", view["imports"][0]["errors"][0]["field"] == "email"
      and [f["key"] for f in view["schema"] if f["is_custom"]] == ["badge"], view["imports"])

t = croutes.test_connection("ci_hr")
check("c_test_connection_decrypts", t["ok"] and croutes.get_connection("ci_hr")["last_test"]["ok"])
croutes.put_connection("ci_hr", croutes.ConnectionBody(tenant="azentio-prod", auth_type="token", token=""))
check("c_blank_token_keeps_the_saved_secret", cstore.secret("ci_hr") == PLAIN)
croutes.put_connection("ci_hr", croutes.ConnectionBody(tenant="azentio-prod", auth_type="token", token="tok-new-secret-9876"))
check("c_new_token_replaces_it", cstore.secret("ci_hr") == "tok-new-secret-9876"
      and croutes.get_connection("ci_hr")["secret_hint"] == "••••9876" and croutes.get_connection("ci_hr")["last_test"] is None)
e = raises(lambda: croutes.put_connection("ci_hr", croutes.ConnectionBody(tenant="t-1", auth_type="basic", username="u")),
           HTTPException)
check("c_switching_auth_type_needs_a_new_secret", e and "password" in e.lower(), e)

# rotation: new key + previous, rotate, then the old key alone can't read
DEV = csecrets.DEV_KEY
K1 = Fernet.generate_key().decode()
os.environ["SECRETS_KEY"], os.environ["SECRETS_KEY_PREVIOUS"] = K1, DEV
check("c_previous_key_reads_during_rotation", cstore.secret("ci_hr") == "tok-new-secret-9876")
n = cstore.rotate()
os.environ["SECRETS_KEY_PREVIOUS"] = ""
check("c_rotation_reencrypts_with_the_new_key", n == 1 and cstore.secret("ci_hr") == "tok-new-secret-9876"
      and db.fetch1("SELECT secret_key_id FROM connector_instances WHERE id='ci_hr'")["secret_key_id"] == csecrets.key_id(K1))
csecrets.check_at_startup()  # the new key alone: fine
os.environ["SECRETS_KEY"] = DEV
e = raises(lambda: cstore.secret("ci_hr"), csecrets.SecretUnavailable)
check("c_old_key_cannot_read_after_rotation", e and "can't be decrypted" in e, e)
e = raises(csecrets.check_at_startup, SystemExit)
check("c_wrong_key_refuses_start_naming_fingerprints", e and csecrets.key_id(K1) in e and csecrets.key_id(DEV) in e, e)
e = raises(lambda: croutes.test_connection("ci_hr"), HTTPException)
check("c_undecryptable_secret_is_a_clear_error_records_still_list",
      e and "can't be decrypted" in e and croutes.connector_records("ci_hr", 1, 25, "")["total"] == 1
      and croutes.get_connection("ci_hr")["secret_hint"] == "••••9876", e)
os.environ["ENV"], os.environ["SECRETS_KEY"] = "production", ""
e = raises(csecrets.check_at_startup, SystemExit)
e2 = raises(lambda: csecrets.encrypt("x"), SystemExit)
check("c_production_without_key_refuses_start", e and "set SECRETS_KEY" in e and e2 and DEV not in csecrets.keys(), e)
e = raises(lambda: (os.environ.__setitem__("SECRETS_KEY", "not-a-key"), csecrets.keys()), SystemExit)
check("c_malformed_key_is_a_clear_error", e and "not a valid Fernet key" in e, e)
os.environ["ENV"], os.environ["SECRETS_KEY"] = "development", K1

# records, links, custom fields and imports on the new storage
portal = CandidatePortal()
for i, (first, email) in enumerate((("Ravi", "ravi@x.com"), ("Meena", "meena_99@x.com"), ("Ravindra", "rk@x.com"))):
    portal.upsert_candidate("ci_cp", {"candidate_id": f"C-{10 + i}", "first_name": first, "last_name": "S", "email": email,
                                      "status": "hired"})
page = cstore.records("ci_cp", 2, 2)
check("c_records_page_in_sql", page["total"] == 4 and len(page["items"]) == 2 and page["page"] == 2
      and [r["values"]["first_name"] for r in cstore.records("ci_cp", 1, 10)["items"]][-1] == "Ananya")
check("c_record_search_matches_values_not_keys", cstore.records("ci_cp", q="ravi")["total"] == 2
      and cstore.records("ci_cp", q="candidate_id")["total"] == 0 and cstore.records("ci_cp", q="_99")["total"] == 1
      and cstore.records("ci_cp", q="b.tech")["total"] == 1)
portal.upsert_candidate("ci_cp", {"candidate_id": "c-10", "first_name": "Ravi", "last_name": "Updated", "email": "ravi@x.com",
                                  "status": "hired"})
check("c_upsert_matches_case_insensitively", cstore.records("ci_cp")["total"] == 4
      and cstore.find("ci_cp", "candidate_id", "C-10")["values"]["last_name"] == "Updated")
push_employee("E9100")
links = cstore.employee_links("E9100")
synced = [x for x in links if x["type"] == "SYNCED_TO"]
check("c_hr_portal_sync_replaces_synced_to", len(synced) == 1 and synced[0]["record_id"] != "rec_h1"
      and cstore.record("ci_hr", synced[0]["record_id"])["values"]["work_email"] == records.employee("E9100")["email"], links)
rid2, outcome = HrPortal().upsert_record("ci_hr", cstore.record("ci_hr", synced[0]["record_id"])["values"])
check("c_hr_portal_repeat_is_skipped", outcome == "skipped" and rid2 == synced[0]["record_id"])
cand = cstore.find("ci_cp", "candidate_id", "C-11")["id"]
cstore.link("E9100", cand, "from_candidate")
cstore.link("E9100", cand, "from_candidate")
check("c_from_candidate_link_added_once", sum(x["record_id"] == cand for x in cstore.employee_links("E9100")) == 1)
HrPortal().add_field("ci_hr", "Desk number", "string", True)
check("c_custom_field_added", [f.key for f in HrPortal().custom_fields("ci_hr")] == ["badge", "desk_number"]
      and raises(lambda: HrPortal().add_field("ci_hr", "BADGE", "string", False), KeyError))
out = CandidateImport().import_file("ci_cp", "new.csv", b"candidate_id,first_name,last_name,email,status\nC-20,Asha,R,asha@x.com,hired\n")
check("c_import_run_recorded", out["imported"] == 1 and cstore.import_runs("ci_cp")[0]["filename"] == "new.csv"
      and cstore.import_runs("ci_cp")[0]["errors"] == [])
check("c_no_drift_after_writes_through_the_app", all(d["ok"] for d in gm.drift_since_cutover() if d["label"] in C_LABELS))

q(f"MATCH (r:{REC} {{id:'rec_c1'}}) SET r.kind = 'stray'")
check("c_drift_catches_a_stray_record_write", not [d for d in gm.drift_since_cutover() if d["label"] == REC][0]["ok"])
rb = gm.rollback("C", watch=0)
g_rec = {r["i"] for r in q(f"MATCH (r:{REC})-[:IN]->(:{CI}) RETURN r.id AS i")}
check("c_rollback_writes_new_rows_and_edges_back", all(r["ok"] for r in rb["labels"]) and len(g_rec) == 7
      and q(f"MATCH (:Employee {{id:'E9100'}})-[:SYNCED_TO]->(r:{REC}) RETURN r.id AS i") == [{"i": synced[0]["record_id"]}]
      and q(f"MATCH (:Employee {{id:'E9100'}})-[:FROM_CANDIDATE]->(r:{REC}) RETURN count(r) AS n")[0]["n"] == 2
      and q(f"MATCH (r:{REC} {{id:'rec_c1'}}) RETURN r.kind AS k")[0]["k"] == "candidate"
      and q(f"MATCH (f:{CF} {{key:'desk_number'}}) RETURN f.required AS r")[0]["r"] is True
      and q(f"MATCH (:{CI})-[:OF]->(c:{CN}) RETURN count(c) AS n")[0]["n"] == 3
      and bridge.SOURCE[CI] == "graph", rb)
check("c_rollback_restores_the_plaintext_secret_for_the_old_release",
      q(f"MATCH (i:{CI} {{id:'ci_hr'}}) RETURN i.secret AS s")[0]["s"] == "tok-new-secret-9876"
      and json.loads(q(f"MATCH (i:{CI} {{id:'ci_hr'}}) RETURN i.connection AS c")[0]["c"])["tenant"] == "azentio-prod")
res = gm.cutover("C", watch=0)
check("c_cutover_again_after_rollback", all(r["ok"] for r in res["labels"]) and bridge.SOURCE[REC] == "postgres"
      and cstore.secret("ci_hr") == "tok-new-secret-9876"
      and db.fetch1("SELECT secret_key_id FROM connector_instances WHERE id='ci_hr'")["secret_key_id"] == csecrets.key_id(K1))

# ---------- a lost graph never re-seeds over Postgres; the projection and the index are rebuilt from it ----------

records.save_employee({"id": "E9001", "name": "Kept Name", "email": "kept@x.com", "role": "Engineer", "grade": "L2",
                       "department": "Engineering", "location": "Bengaluru", "manager_email": "mgr.eng@x.com",
                       "joining_date": "2026-12-01", "token": "tok-e9001", "thread_id": ""})
templates.upsert({**templates.get("intern"), "subject": "HR edit"})
owners.upsert([{"email": "it@x.com", "name": "HR renamed desk", "department": "IT", "type": "owner:IT"}])
counts = {t: db.fetch1(f"SELECT count(*) AS n FROM {t}")["n"] for t in ("owners", "email_templates", "policies", "employees")}
q("MATCH (n) DETACH DELETE n")  # the graph is lost
asyncio.run(ingest.seed())
check("wiped_graph_keeps_hr_edits", templates.get("intern")["subject"] == "HR edit"
      and owners.get("it@x.com")["name"] == "HR renamed desk" and records.employee("E9001")["name"] == "Kept Name")
check("wiped_graph_seeds_nothing_over_postgres",
      counts == {t: db.fetch1(f"SELECT count(*) AS n FROM {t}")["n"] for t in counts}, counts)
check("wiped_graph_rebuilds_the_projection",
      q(f"MATCH (:Employee {{id:'E9001'}})-[:REPORTS_TO]->(o:{OWN}) RETURN o.email AS e") == [{"e": "mgr.eng@x.com"}]
      and q(f"MATCH (o:{OWN}) RETURN count(o) AS n")[0]["n"] == counts["owners"],
      (q("MATCH (e:Employee) RETURN e.id AS i, e.manager_email AS m"), q(f"MATCH (o:{OWN}) RETURN o.email AS e"), counts))
check("wiped_graph_rebuilds_the_policy_index",
      q(f"MATCH (c:{CHUNK}) RETURN count(DISTINCT c.source) AS n")[0]["n"] == counts["policies"])
check("no_drift_after_the_rebuild", all(d["ok"] for d in gm.drift_since_cutover()), gm.drift_since_cutover())
q(f"MATCH (o:{OWN}) DETACH DELETE o")  # only the owners are lost; employees are still in the graph
asyncio.run(ingest.seed())
check("a_start_restores_a_lost_owner_projection", q(f"MATCH (o:{OWN}) RETURN count(o) AS n")[0]["n"] == counts["owners"])

# a spec module that fails to import fails every time, never a silently partial registry
gm.SPEC_MODULES = gm.SPEC_MODULES + ("no_such_spec_module",)
gm._loaded = False
errs = [raises(gm.load, ImportError) for _ in range(2)]
gm.SPEC_MODULES = gm.SPEC_MODULES[:-1]
check("spec_import_failure_is_never_silent", all(errs) and gm.load() and gm._loaded, errs)

# ---------- API contract ----------
out = subprocess.run([sys.executable, str(ROOT / "scripts" / "api_contract.py"), "--check"], capture_output=True, text=True)
check("api_contract_unchanged", out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:])

print(f"\nphase 3: {ok} checks passed")

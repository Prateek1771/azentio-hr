"""Self-checks for the pure logic: rule matching, blocking, validation, ingestion. Run: uv run python test_core.py"""
import os
os.environ["FALKORDB_GRAPH"] = "test"  # before anything imports graph: these checks wipe integrations, never touch the live graph
from shared import db  # noqa: E402
db.use_test_database("azentio_test_core")  # audit entries land here, never in the live Postgres
import json
from datetime import date
from pathlib import Path

from checklist import build_items, view_state
from ingest import validate_employees, norm_date
from validate import evaluate, names_match, check_file

cfg = json.loads((Path(__file__).parent / "seed" / "requirements.json").read_text())
owners = {"HR": {"name": "HR Ops", "email": "hr@x.com"}, "IT": {"name": "IT", "email": "it@x.com"}}
eng = {"name": "Aarav Sharma", "role": "Software Engineer", "grade": "G3", "department": "Engineering",
       "location": "Bengaluru", "joining_date": "2026-11-16", "date_of_birth": "1997-04-12"}

# Bengaluru engineer: India docs + education + experience + dev tools; no US forms
items = build_items(eng, cfg["rules"], cfg["doc_types"], cfg["locations"], owners)
keys = {i["key"] for i in items}
assert {"photo_id", "pan_card", "education_certificate", "experience_letter", "dev_tools", "background_verification"} <= keys
assert not keys & {"i9_documents", "w4_form", "crm_access"}
edu = next(i for i in items if i["key"] == "education_certificate")
assert "R-EDU" in edu["reason"] and "department = Engineering" in edu["reason"]
assert next(i for i in items if i["key"] == "photo_id")["due"] == "2026-11-06"
assert "w4_form" not in next(i for i in items if i["key"] == "hr_record")["depends"]  # inapplicable deps dropped

# Intern (G1): no experience letter; US hire: I-9/W-4, no PAN
intern = {**eng, "grade": "G1"}
assert "experience_letter" not in {i["key"] for i in build_items(intern, cfg["rules"], cfg["doc_types"], cfg["locations"], owners)}
us = {**eng, "location": "New York"}
us_keys = {i["key"] for i in build_items(us, cfg["rules"], cfg["doc_types"], cfg["locations"], owners)}
assert {"i9_documents", "w4_form"} <= us_keys and "pan_card" not in us_keys

# Blocking / overdue
for i in items:
    i["status"] = "Pending" if i["kind"] != "document" else "Not Submitted"
by_key = {i["key"]: i for i in items}
assert view_state(by_key["laptop"], by_key, date(2026, 11, 1)) == "blocked"
assert view_state(by_key["photo_id"], by_key, date(2026, 11, 10)) == "overdue"
assert view_state(by_key["photo_id"], by_key, date(2026, 11, 1)) == "pending"
by_key["hr_record"]["status"] = "Completed"
assert view_state(by_key["laptop"], by_key, date(2026, 11, 1)) == "pending"

# Validation
assert names_match("SHARMA AARAV", "Aarav Sharma") and names_match("Aarav K Sharma", "Aarav Sharma")
assert not names_match("Rahul Verma", "Aarav Sharma")
ok = {"legible": True, "full_name": "Aarav Sharma", "document_number": "P123", "date_of_birth": "1997-04-12",
      "expiry_date": "2030-01-01", "confidence": 0.95}
assert evaluate(ok, eng, cfg["doc_types"]["photo_id"])["status"] == "Accepted"
bad = evaluate({**ok, "full_name": "Rahul Verma", "expiry_date": "2025-01-01"}, eng, cfg["doc_types"]["photo_id"])
assert bad["status"] == "Correction Requested" and len(bad["issues"]) == 2 and bad["mismatches"][0]["field"] == "name"
assert evaluate({**ok, "confidence": 0.3}, eng, cfg["doc_types"]["photo_id"])["status"] == "Under Review"
assert evaluate({**ok, "legible": False}, eng, cfg["doc_types"]["photo_id"])["status"] == "Correction Requested"
assert check_file("application/zip", 100) and not check_file("application/pdf", 100)

# Ingestion validation
assert norm_date("16/11/2026") == "2026-11-16" and norm_date("nope") is None
rows = [{**eng, "email": "a@x.com", "manager_email": "m@x.com"},
        {**eng, "email": "b@x.com", "manager_email": "ghost@x.com", "joining_date": "31-31-2026", "department": "Space"},
        {**eng, "email": "", "manager_email": "m@x.com"}]
valid, errors = validate_employees(rows, cfg["departments"], cfg["locations"], {"m@x.com"})
assert len(valid) == 1
assert {(e["row"], e["field"]) for e in errors} >= {(2, "manager_email"), (2, "joining_date"), (2, "department"), (3, "email")}

# ---- Connectors (pure rules, then graph-backed against FALKORDB_GRAPH=test) ----
from connectors.base import RecordRejected
from connectors.candidates import normalise
from connectors.hr_portal import HrPortal, slugify, validate as hr_validate
from connectors.sync import suggest_mapping, apply_mapping

assert slugify("Badge number") == "badge_number" and slugify("  T-shirt Size! ") == "t_shirt_size"
good = {"first_name": "Ana", "last_name": "K", "work_email": "ANA@x.com", "job_title": "SE", "start_date": "2026-12-01", "department_name": "engineering"}
cleaned = hr_validate(good, cfg["departments"], [])
assert cleaned["department_name"] == "Engineering" and cleaned["employment_status"] == "active"
assert cleaned["employee_number"].startswith("EMP-") and cleaned["work_email"] == "ana@x.com"
for bad_values, needle in [({**good, "department_name": "Marketing"}, "Department"), ({**good, "employment_status": "fired"}, "Employment status"),
                           ({k: v for k, v in good.items() if k != "job_title"}, "Job title"), ({**good, "start_date": "31/31/2026"}, "Start date")]:
    try:
        hr_validate(bad_values, cfg["departments"], [])
        raise AssertionError(f"expected rejection: {needle}")
    except RecordRejected as ex:
        assert needle in str(ex), ex
m = suggest_mapping(["email", "designation", "joining_date", "first_name", "department_name"], ["work_email", "job_title", "start_date", "first_name", "department_name", "employee_number"])
assert m == {"work_email": "email", "job_title": "designation", "start_date": "joining_date", "first_name": "first_name", "department_name": "department_name"}
assert apply_mapping({"email": "a@b.c", "designation": ["x", "y"]}, {"work_email": "email", "job_title": "designation"}) == {"work_email": "a@b.c", "job_title": "x, y"}
vals, probs = normalise({"candidate_id": "C1", "first_name": "A", "last_name": "B", "email": "bad", "status": "applied", "joining_date": "16/11/2026"})
assert vals is None and any("email" in p for p in probs)
vals, probs = normalise({"candidate_id": "C1", "first_name": "A", "last_name": "B", "email": "A@B.COM", "status": "hired", "education": "BSc; MSc"})
assert not probs and vals["email"] == "a@b.com" and vals["education"] == ["BSc", "MSc"]

from graph import q as gq, q1 as gq1, ensure_indexes, G as _G  # noqa: E402
assert _G.name == "test", "test_core must run on the test graph"
from connectors.seed import seed_connectors  # noqa: E402
import checklist as _cl  # noqa: E402
ensure_indexes()
gq("MERGE (c:Config {key:'departments'}) SET c.value=$v", v=json.dumps(cfg["departments"]))
seed_connectors()
hr_id = gq1("MATCH (i:ConnectorInstance {key:'hr_portal'}) RETURN i.id AS id")["id"]
hr = HrPortal()
email = "idempotent.test@example.com"
gq("MATCH (r:Record {instance_id:$i}) WHERE r.values CONTAINS $e DETACH DELETE r", i=hr_id, e=email)
before = hr.count_records(hr_id)
rid1, o1 = hr.upsert_record(hr_id, {**good, "work_email": email})
rid2, o2 = hr.upsert_record(hr_id, {**good, "work_email": email, "job_title": "Senior SE"})
rid3, o3 = hr.upsert_record(hr_id, {**good, "work_email": email, "job_title": "Staff SE"}, "skip")
rid4, o4 = hr.upsert_record(hr_id, {**good, "work_email": email, "job_title": "Senior SE"})
assert (o1, o2, o3, o4) == ("created", "updated", "skipped", "skipped") and rid1 == rid2 == rid3 == rid4
assert hr.count_records(hr_id) == before + 1  # four writes, one employee
gq("MATCH (f:CustomField {instance_id:$i, key:'badge_number'}) DELETE f", i=hr_id)
hr.add_field(hr_id, "Badge number", "string", False)
try:
    hr.add_field(hr_id, "badge  NUMBER", "string", False)
    raise AssertionError("duplicate custom field must be refused")
except KeyError:
    pass
try:
    hr.add_field(hr_id, "First name", "string", False)
    raise AssertionError("built-in name must be refused")
except KeyError:
    pass

# ---- Integration workflow (pure rules) ----
import asyncio  # noqa: E402
from datetime import datetime as _dt, timezone as _tz  # noqa: E402
from integrations.mapping import apply_mapping as wf_apply, transform  # noqa: E402
from integrations.run_log import mask_record, snapshot_mapping, display_name  # noqa: E402
from integrations import store as wf_store  # noqa: E402

assert transform("  Ana Maria Lopez ", "first_word") == "Ana" and transform("Ana Maria Lopez", "after_first_word") == "Maria Lopez"
assert transform(" X@Y.COM ", "lowercase") == " x@y.com " and transform(" a ", "trim") == "a"
rules = [{"source_field": "email", "target_field": "work_email", "transform": "lowercase"},
         {"source_field": None, "target_field": "employment_status", "default_value": "active"},
         {"source_field": "designation", "target_field": "job_title", "default_value": "Engineer"}]
assert wf_apply(rules, {"email": "A@B.COM", "designation": ""}) == {"work_email": "a@b.com", "employment_status": "active", "job_title": "Engineer"}
snap = snapshot_mapping(rules, {"email": "a@b.com", "designation": "SE"}, {"work_email"}, set())
assert next(e for e in snap if e["target_field"] == "work_email")["value"] is None  # excluded → never stored
assert next(e for e in snap if e["target_field"] == "employment_status")["from_default"] is True
assert snapshot_mapping(rules, {}, set(), {"mapping"}) == []
rec = {"candidate_name": "Ana Lopez", "candidate_ref": "C-1", "actions": [{"section": "offers", "message": "Sent offer"},
       {"section": "documents", "message": "Requested"}], "mapping": [{"target_field": "job_title", "source_field": "designation", "value": "SE"}]}
masked = mask_record(rec, {"designation", "first_name"}, {"offers"})
assert masked["candidate_name"] == "C-1" and [a["section"] for a in masked["actions"]] == ["documents"]  # older runs masked too
assert masked["mapping"][0]["value"] is None and masked["mapping"][0]["excluded"]
assert display_name("Ana Lopez", "C-1", set()) == "Ana Lopez"
fixed = _dt(2026, 10, 31, 8, 0, tzinfo=_tz.utc)
assert wf_store.next_run_time({"kind": "interval", "interval_minutes": 360}, fixed) == fixed.timestamp() + 6 * 3600
assert _dt.fromtimestamp(wf_store.next_run_time({"kind": "daily", "time": "09:00", "timezone": "UTC"}, fixed), _tz.utc).hour == 9
assert wf_store.next_run_time({"kind": "daily", "time": "07:00", "timezone": "UTC"}, fixed) > fixed.timestamp() + 20 * 3600  # tomorrow
assert wf_store.next_run_time({"kind": "manual"}, fixed) is None

# ---- Integration workflow (graph-backed, simulated delivery, no LLM) ----
import os  # noqa: E402
# Present-but-empty, so main's load_dotenv() can't refill them: tests never email anyone or call an LLM.
for _k in ("GMAIL_REFRESH_TOKEN", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "EMAIL_REDIRECT_TO"):
    os.environ[_k] = ""
import llm  # noqa: E402
llm.API_KEY = ""  # rule-based agent only
llm.PROVIDERS = []
from integrations import chat as wf_chat, engine as wf_engine, service as wf_service  # noqa: E402
import logging_setup as _ls  # noqa: E402
_ls.setup()
from integrations.store import SetupError  # noqa: E402

# Postgres is recreated empty each run; clear the test graph's projection of employees/items to match it.
gq("MATCH (n) WHERE n:Employee OR n:Item DETACH DELETE n")
gq("MATCH (r:Record)-[:IN]->(:ConnectorInstance)-[:OF]->(:Connector {role:'app'}) DETACH DELETE r")
gq("MATCH (n) WHERE n:Integration OR n:IntegrationCandidate OR n:WorkflowRun OR n:IntegrationLogRecord OR n:AgentMessage OR n:OutboundMessage DETACH DELETE n")
gq("MERGE (c:Config {key:'locations'}) SET c.value=$v", v=json.dumps(cfg["locations"]))
gq("MERGE (c:Config {key:'doc_types'}) SET c.value=$v", v=json.dumps(cfg["doc_types"]))
gq("MERGE (c:Config {key:'roles'}) SET c.value=$v", v=json.dumps(cfg["roles"]))
import ingest as _ingest  # noqa: E402
import records  # noqa: E402
if not gq1("MATCH (r:Rule) RETURN r LIMIT 1"):
    _ingest.import_requirements("requirements.json", (Path(__file__).parent / "seed" / "requirements.json").read_bytes())
if not gq1("MATCH (o:Owner {type:'manager'}) RETURN o LIMIT 1"):
    _ingest.import_owners("owners.csv", (Path(__file__).parent / "seed" / "owners.csv").read_bytes())
cp_id = gq1("MATCH (i:ConnectorInstance {key:'candidate_portal'}) RETURN i.id AS id")["id"]


async def _wf():
    it = wf_service.create("WF test", "", cp_id, hr_id)
    assert wf_store.get(it["id"])["setup_step"] == "records"
    try:
        wf_service.create("wf TEST", "", cp_id, hr_id)
        raise AssertionError("duplicate name must be refused")
    except SetupError:
        pass
    # Human-only gate: the agent can't approve unless HR's own words ask for it
    tools = wf_chat.Tools(it["id"], "map designation to job title", None)
    assert (await tools.execute("approve_mapping", {}))["ok"] is False
    assert (await tools.execute("run_now", {}))["ok"] is False
    # Setup through the rule-based agent, exactly as HR would type it
    for msg in ["Hired candidates in Engineering, update existing employees", "approve", "Notify HR Operations about failures",
                "No, skip verification", "No offer letter", "No app access", "Only when I run it"]:
        await wf_chat.run_chat_turn(it["id"], msg, "UTC")
    it = wf_store.get(it["id"])
    assert it["source_filter"] == {"statuses": ["hired"], "departments": ["Engineering"]}, it["source_filter"]
    assert it["mapping_approved_at"] and it["doc_verification_enabled"] is False and it["offer_enabled"] is False
    assert it["setup_step"] == "review" and not wf_store.problems(it), (it["setup_step"], wf_store.problems(it))
    await wf_chat.run_chat_turn(it["id"], "activate", "UTC")
    assert wf_store.get(it["id"])["status"] == "active"
    run = await wf_engine.run_integration(it["id"])
    assert run["counts"]["enrolled"] == 1 and sum(run["counts"][k] for k in ("employee_created", "employee_updated", "employee_skipped")) == 1, run["counts"]
    again = await wf_engine.run_integration(it["id"])
    assert again["counts"]["enrolled"] == 0 and again["counts"]["advanced"] == 0  # repeat-safe
    logs = gq("MATCH (l:IntegrationLogRecord {run_id:$r}) RETURN l", r=run["id"])
    assert len(logs) == 1 and json.loads(logs[0]["l"]["mapping"])  # per-candidate log with mapping snapshot
    # Changing an approved mapping pauses a live integration
    wf_service.save_mapping(it["id"], [r for r in wf_store.get(it["id"])["mapping"] if r["target_field"] != "job_title"])
    assert wf_store.get(it["id"])["status"] == "paused"

    # Document verification ON: real document step (simulated delivery here), AI verdict callback, HR approval
    it2 = wf_service.create("WF docs", "", cp_id, hr_id)
    wf_service.save_records(it2["id"], ["offer_accepted"], ["Engineering"], "update")
    wf_service.save_mapping(it2["id"], await wf_service.suggest_mapping(it2["id"]))
    wf_service.approve_mapping(it2["id"])
    wf_service.save_notifications(it2["id"], [], [], False, False, False)
    wf_service.save_documents(it2["id"], True)
    assert wf_store.get(it2["id"])["setup_step"] == "documents"  # still needs the per-role document set
    wf_service.save_role_documents(it2["id"], "all roles", ["photo_id", "address_proof"])
    wf_service.save_role_documents(it2["id"], "Software Engineer", ["photo_id"])
    # Custom expression + unique company email in the mapping
    rules = [r for r in wf_store.get(it2["id"])["mapping"] if r["target_field"] not in ("work_email", "job_title")]
    rules += [{"target_field": "work_email", "transform": "unique_email", "expression": 'slug(first_name) + "." + slug(last_name)',
               "email_domain": "azentio.example"},
              {"target_field": "job_title", "transform": "expression", "expression": 'coalesce(designation, "Associate") + " (" + grade + ")"'}]
    wf_service.save_mapping(it2["id"], rules)
    wf_service.approve_mapping(it2["id"])
    try:
        wf_service.test_expression(it2["id"], "__import__('os')")
        raise AssertionError("unsafe expression must be refused")
    except SetupError:
        pass
    await wf_service.save_offer_choice(it2["id"], True)
    wf_service.decide_offer(it2["id"], True)
    assert wf_store.get(it2["id"])["setup_step"] == "access"
    wf_service.save_access_rules(it2["id"], wf_service.suggested_access(wf_store.get(it2["id"])))
    wf_service.save_schedule(it2["id"], "manual")
    wf_service.activate(it2["id"])
    r1 = await wf_engine.run_integration(it2["id"])
    items = wf_store.items(it2["id"])
    asked = [i for i in items if i["stage"] == "documents_requested"]
    assert asked and r1["counts"]["documents_requested"] == len(asked), (wf_store.get(it2["id"])["role_documents"], r1["counts"], [(i["name"], i["stage"], i.get("last_error")) for i in items])
    first = asked[0]
    for i in _cl.employee_items(first["joiner_id"]):
        if i["kind"] == "document":
            records.update_item(i["id"], status="Accepted")
    assert wf_engine.on_documents_changed(first["joiner_id"]) == "applied"
    assert wf_store.item(first["id"])["stage"] == "awaiting_hr_approval"
    try:
        wf_service.decide_candidate(it2["id"], first["id"], False, "")
        raise AssertionError("rejecting needs a reason")
    except SetupError:
        pass
    wf_service.decide_candidate(it2["id"], first["id"], True, None)
    r2 = await wf_engine.run_integration(it2["id"])
    assert wf_store.item(first["id"])["stage"] == "completed" and r2["counts"]["offer_sent"] >= 1 and r2["counts"]["approval_used"] >= 1
    kinds = {m["kind"] for m in gq("MATCH (m:OutboundMessage {integration_id:$i}) RETURN m", i=it2["id"]) for m in [m["m"]]}
    assert {"document_request", "offer_letter"} <= kinds
    # Documents were limited to the role's set (Software Engineer -> photo ID only, others -> 2)
    for i in wf_store.items(it2["id"]):
        if i.get("joiner_id"):
            docs = {x["key"] for x in _cl.employee_items(i["joiner_id"]) if x["kind"] == "document"}
            assert docs <= {"photo_id", "address_proof"}, (i["name"], docs)
    done = wf_store.item(first["id"])
    email = json.loads(done["generated_values"])["work_email"]
    assert email.endswith("@azentio.example"), email
    apps_done = {(a["app"], a["action"]) for a in done["access"]}
    assert ("Google Workspace", "create_account") in apps_done and ("ServiceNow", "create_ticket") in apps_done, apps_done
    assert all(a["email"] == email for a in done["access"])  # accounts use the generated company email
    assert r2["counts"]["access_granted"] >= 2
    # Repeat run: same email, no duplicate grants
    await wf_engine.run_integration(it2["id"])
    assert json.loads(wf_store.item(first["id"])["generated_values"])["work_email"] == email
    # HR asks the agent for one-off access for this candidate (rule-based agent, gated on HR's words)
    first_name = done["name"].split()[0]
    await wf_chat.run_chat_turn(it2["id"], f"Add {first_name} to #platform in Slack", "UTC")
    assert any(a["resource"] == "#platform" for a in wf_store.item(first["id"])["access"]), wf_store.item(first["id"])["access"]
    tools = wf_chat.Tools(it2["id"], "what's the schedule", None)
    assert (await tools.execute("grant_access", {"candidate": first_name, "app": "Jira", "resource": "PAY"}))["ok"] is False


import gmail as _gmail  # noqa: E402
assert not _gmail.configured(), "tests must never use real Gmail"
asyncio.run(_wf())

# Langfuse export must never carry document scans
import llm as _llm  # noqa: E402
_masked = _llm._mask(data=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0K+/="}}]}])
assert "base64" not in str(_masked) and "redacted" in str(_masked)

print("all checks passed")

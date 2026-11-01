"""Phase 1 self-checks: governed rule-set versions, safe in-flight replay, rollback, Employee/Item migration.

Runs against a throwaway Postgres database and a throwaway FalkorDB graph; never touches live data.
Usage: cd backend && uv run python test_phase1.py
"""
import asyncio
import os
from datetime import date, timedelta
from pathlib import Path

os.environ["FALKORDB_GRAPH"] = "test_phase1"  # before anything imports graph
os.environ["NEAR_START_DAYS"] = "3"

from shared import db  # noqa: E402

db.use_test_database("azentio_test_p1")

import psycopg  # noqa: E402

from graph import q, q1, new_id  # noqa: E402

q("MATCH (n) DETACH DELETE n")  # fresh graph

import ingest  # noqa: E402
import records  # noqa: E402
import checklist  # noqa: E402
from audit_log.service import verify_chain  # noqa: E402
from identity import service as idsvc  # noqa: E402
from policy import replay, service, store  # noqa: E402
from policy.store import PolicyError  # noqa: E402
from shared import bridge, jobs  # noqa: E402

SEED = Path(__file__).parent / "seed"
ok = 0


def check(name, cond, detail=""):
    global ok
    assert cond, f"{name} {detail}"
    ok += 1
    print(f"  ok  {name}")


def drain():
    """Run every queued job (publish, notify) to completion."""
    while asyncio.run(jobs.run_once()):
        pass


# ---------- fixtures ----------
ingest.import_requirements("requirements.json", (SEED / "requirements.json").read_bytes())  # legacy graph rules + config
ingest.import_owners("owners.csv", (SEED / "owners.csv").read_bytes())
hr = idsvc.create_user("hr@t.io", "HR", "hr_ops", "hr-pass-12345", actor="test")
ap1 = idsvc.create_user("ap1@t.io", "Ap One", "approver", "ap1-pass-12345", actor="test")
ap2 = idsvc.create_user("ap2@t.io", "Ap Two", "approver", "ap2-pass-12345", actor="test")
today = date.today()


def joiner(name, email, days, location="Bengaluru", department="Engineering", grade="G3"):
    return ingest.upsert_employee({"name": name, "email": email, "role": "Software Engineer", "grade": grade,
                                   "department": department, "location": location, "manager_email": "",
                                   "joining_date": (today + timedelta(days=days)).isoformat(), "date_of_birth": ""})


far = joiner("Far Away", "far@t.io", 20)
near = joiner("Near Start", "near@t.io", 2)
hr_dept = joiner("Hana HR", "hana@t.io", 25, department="Human Resources")
v1 = store.active_version()
check("v1_created_from_legacy_rules", v1 and v1["number"] == 1 and len(store.rules_of(v1["id"])) == 18)
check("v1_compliance_flags", {r["id"] for r in store.rules_of(v1["id"]) if r["compliance"]} == {"R-ID", "R-I9", "R-BGV"})
keys_far = {i["key"] for i in records.items(far)}
check("joiner_built_from_active_version", {"photo_id", "pan_card", "background_verification"} <= keys_far)


def doc_on(eid, key, status="Accepted"):
    """Attach a document (graph, as in production until Phase 2) and set the item's status."""
    it = records.item_by_key(eid, key)
    did = new_id("doc")
    q("CREATE (d:Document {id:$d, filename:'x.pdf', status:$s, ts:1})", d=did, s=status)
    q("MATCH (i:Item {id:$i}), (d:Document {id:$d}) CREATE (i)-[:HAS_DOC]->(d)", i=it["id"], d=did)
    records.update_item(it["id"], status=status)
    return it["id"], did


def edit_draft(user, changes: dict, remove: list[str] = ()):
    vid = store.open_draft(user["id"])
    rules = {r["id"]: r for r in store.rules_of(vid)}
    for rid, patch in changes.items():
        store.upsert_draft_rule(vid, {**rules.get(rid, {}), **patch})
    for rid in remove:
        store.delete_draft_rule(vid, rid)
    return vid


# ---------- pure diff ----------
cur = [{"id": "a", "key": "x", "name": "X", "kind": "document", "status": "Accepted", "due": "2026-01-01", "depends": []},
       {"id": "b", "key": "y", "name": "Y", "kind": "document", "status": "Not Submitted", "due": "2026-01-01", "depends": []},
       {"id": "c", "key": "z", "name": "Z", "kind": "task", "status": "Pending", "due": "2026-01-01", "depends": []},
       {"id": "d", "key": "w", "name": "W", "kind": "document", "status": "Submitted", "due": "2026-01-01", "depends": []}]
plan = [{"key": "z", "name": "Z", "kind": "task", "category": "IT", "rule_id": "R", "reason": "", "policy_ref": "",
         "due": "2026-01-05", "auto": "", "depends": ["n"], "owner_name": "", "owner_email": ""},
        {"key": "n", "name": "N", "kind": "ack", "category": "HR", "rule_id": "R2", "reason": "", "policy_ref": "",
         "due": "2026-01-02", "auto": "", "depends": [], "owner_name": "", "owner_email": ""}]
d = replay.plan_diff(cur, plan, has_docs={"d"})
check("test_plan_diff_pure",
      [p["key"] for p in d["added"]] == ["n"] and [o["key"] for o in d["cancel"]] == ["y"]
      and sorted(o["key"] for o in d["keep_evidence"]) == ["w", "x"] and [m["key"] for m in d["due_moved"]] == ["z"]
      and d["newly_blocked"] == ["z"], d)

# ---------- approvals ----------
vid = edit_draft(hr, {"R-PAN": {"offset_days": -7}})
try:
    store.upsert_draft_rule(vid + 999, {"id": "R-X"})
except Exception:
    pass
service.submit(vid, hr, "PAN later", "payroll asked")
check("test_noncompliance_needs_one", store.version(vid)["required_approvals"] == 1)
try:
    service.approve(vid, hr, "self")
    check("test_author_cannot_approve", False)
except PolicyError as e:
    check("test_author_cannot_approve", e.status == 403)
req = db.fetch1("SELECT id FROM approval_requests WHERE subject_id=%s", (str(vid),))
try:
    db.execute("INSERT INTO approvals (request_id, approver_id, decision) VALUES (%s, %s, 'approve')", (req["id"], hr["id"]))
    check("test_author_cannot_approve_db", False)
except psycopg.Error:
    check("test_author_cannot_approve_db", True)
try:
    db.execute("UPDATE rules SET offset_days=1 WHERE version_id=%s", (v1["id"],))
    check("test_frozen_versions", False)
except psycopg.Error:
    check("test_frozen_versions", True)
try:
    store.open_draft(hr["id"])
    check("test_one_open_draft", False)
except PolicyError as e:
    check("test_one_open_draft", e.status == 409)

# preview == publish (joiners not near start)
pv = service.preview(vid)
before = {e: {i["key"]: i for i in records.items(e, include_closed=True)} for e in (far, hr_dept)}
service.approve(vid, ap1, "fine")
check("approved_enqueues_publish", db.fetch1("SELECT count(*) AS n FROM jobs WHERE idempotency_key=%s", (f"policy.publish:{vid}",))["n"] == 1)
drain()
check("test_single_active", db.fetch1("SELECT count(*) AS n FROM rule_set_versions WHERE state='active'")["n"] == 1
      and store.active_version()["id"] == vid and store.version(v1["id"])["state"] == "superseded")
moved = sum(1 for e in (far, hr_dept) for i in records.items(e) if before[e][i["key"]]["due"] != i["due"])
check("test_preview_matches_publish", pv["summary"]["due_moved"] - sum(r["due_moved"] for r in pv["rows"] if r["near_start"]) == moved,
      (pv["summary"], moved))
check("test_near_start_not_changed",
      records.item_by_key(near, "pan_card")["due"] == (today + timedelta(days=2 - 10)).isoformat()
      and db.fetch1("SELECT count(*) AS n FROM work_queue WHERE kind='policy.near_start' AND entity_id=%s", (near,))["n"] == 1)
check("test_preview_filters", all(r["department"] == "Human Resources" for r in
                                   service.preview(edit_draft(hr, {"R-PAN": {"offset_days": -9}}), {"department": "Human Resources"})["rows"]))
store.discard_draft(store.open_version()["id"], hr)

# compliance needs two distinct approvers
vid = edit_draft(hr, {"R-BGV": {"offset_days": -6}})
service.submit(vid, hr, "BGV earlier", "compliance asked")
check("test_compliance_needs_two_required", store.version(vid)["required_approvals"] == 2)
service.approve(vid, ap1, "ok")
check("test_compliance_still_pending_after_one", store.version(vid)["state"] == "pending_approval")
try:
    service.approve(vid, ap1, "again")
    check("test_one_vote_per_person", False)
except PolicyError as e:
    check("test_one_vote_per_person", e.status == 409)
service.approve(vid, ap2, "ok too")
check("test_compliance_needs_two", store.version(vid)["state"] == "approved")
drain()
v_bgv = vid

# ---------- removed rules: evidence kept, pending cancelled, nothing deleted ----------
pan_id, pan_doc = doc_on(far, "pan_card")                        # accepted with a document
addr_pending_id = records.item_by_key(far, "address_proof")["id"]  # still "Not Submitted"
n_items_before = db.fetch1("SELECT count(*) AS n FROM items")["n"]
v_nda = edit_draft(hr, {"R-NDA": {"id": "R-NDA", "item": "nda_ack", "name": "NDA", "kind": "ack", "category": "HR",
                                  "description": "NDA", "offset_days": -2}}, remove=["R-PAN", "R-ADDR"])
service.submit(v_nda, hr, "NDA in, PAN and address out", "test")
service.approve(v_nda, ap1, "ok")
drain()
pan = records.item(pan_id)
check("test_done_item_kept_as_evidence", pan["status"] == "Accepted" and pan["superseded_by_version"] == v_nda
      and q1("MATCH (:Item {id:$i})-[:HAS_DOC]->(d:Document {id:$d}) RETURN d", i=pan_id, d=pan_doc) is not None)
addr = records.item(addr_pending_id)
check("test_pending_item_cancelled_not_deleted", addr["status"] == "Cancelled" and addr["cancelled_by_version"] == v_nda)
check("nothing_deleted", db.fetch1("SELECT count(*) AS n FROM items")["n"] >= n_items_before)
check("closed_items_hidden_from_checklist", "pan_card" not in {i["key"] for i in records.items(far)})
nda_jobs = db.fetch1("SELECT count(*) AS n FROM jobs WHERE kind='notify.item_added' AND idempotency_key=%s",
                     (f"notify.item_added:{far}:{v_nda}",))["n"]
service.publish(v_nda)  # re-running a published version is a no-op
check("test_added_item_notifies_once", nda_jobs == 1 and db.fetch1(
    "SELECT count(*) AS n FROM jobs WHERE kind='notify.item_added' AND idempotency_key=%s", (f"notify.item_added:{far}:{v_nda}",))["n"] == 1
      and db.fetch1("SELECT count(*) AS n FROM item_notices WHERE employee_id=%s AND version_id=%s", (far, v_nda))["n"] == 1)

# ---------- rollback restores the rule set without losing evidence or approvals ----------
nda = records.item_by_key(far, "nda_ack")
records.update_item(nda["id"], status="Acknowledged")             # joiner completes the item the rollback removes
approvals_before = db.fetch1("SELECT count(*) AS n FROM approvals")["n"]
rb = service.rollback(v_bgv, hr, "NDA not ready; restore PAN and address")
service.approve(rb["id"], ap1, "ok")
if store.version(rb["id"])["required_approvals"] == 2:
    service.approve(rb["id"], ap2, "ok")
drain()
eff = {r["id"]: {k: v for k, v in r.items() if k != "ord"} for r in store.rules_of(store.active_version()["id"])}
src = {r["id"]: {k: v for k, v in r.items() if k != "ord"} for r in store.rules_of(v_bgv)}
pan = records.item(pan_id)
nda = records.item(nda["id"])
check("test_rollback_restores_without_loss (a) rule set equals the target", eff == src)
check("test_rollback_restores_without_loss (b) pan_card active again with its document",
      pan["superseded_by_version"] is None and pan["status"] == "Accepted"
      and q1("MATCH (:Item {id:$i})-[:HAS_DOC]->(d:Document {id:$d}) RETURN d", i=pan_id, d=pan_doc) is not None)
check("test_rollback_restores_without_loss (c) completed NDA kept as evidence",
      nda["status"] == "Acknowledged" and nda["superseded_by_version"] == rb["id"])
check("test_rollback_restores_without_loss (d) approvals all kept", db.fetch1("SELECT count(*) AS n FROM approvals")["n"] > approvals_before)
check("test_rollback_restores_without_loss (e) previous version superseded, not deleted",
      store.version(v_nda)["state"] == "superseded" and store.active_version()["rollback_of"] == v_bgv)
check("test_rollback_restores_without_loss (f) address proof restored", records.item(addr_pending_id)["cancelled_by_version"] is None)

# ---------- near-start resolution ----------
ns = db.fetch1("SELECT * FROM work_queue WHERE kind='policy.near_start' AND status='open' LIMIT 1")
join = today + timedelta(days=2)
check("near_start_held_back_until_resolved", records.item_by_key(near, "pan_card")["due"] == (join - timedelta(days=10)).isoformat())
status = service.resolve_near_start(ns, "apply", hr, "start date is fine")
pan_rule = next(r for r in store.rules_of(store.active_version()["id"]) if r["id"] == "R-PAN")
check("near_start_apply_runs_replay", status == "resolved" and
      records.item_by_key(near, "pan_card")["due"] == (join + timedelta(days=pan_rule["offset_days"])).isoformat()
      and pan_rule["offset_days"] == -7)
try:
    service.resolve_near_start(ns, "maybe", hr, "x")
    check("near_start_needs_apply_or_skip", False)
except PolicyError as e:
    check("near_start_needs_apply_or_skip", e.status == 422)

# ---------- migration: parity, bridge gate, projection ----------
g, p = records.parity_rows("Item")
check("test_parity_ok_after_writes", bridge.parity(g, p)["ok"])
any_item = db.fetch1("SELECT id FROM items LIMIT 1")["id"]
db.execute("UPDATE items SET reason='drift' WHERE id=%s", (any_item,))   # Postgres only, no projection
g, p = records.parity_rows("Item")
res = bridge.parity(g, p)
check("test_parity_detects_drift", not res["ok"] and res["first_mismatch_id"] == any_item, res)
try:
    bridge.SOURCE["Item"] = "graph"
    bridge.switch("Item", g, p)
    check("test_bridge_refuses_switch", False)
except bridge.ParityError:
    check("test_bridge_refuses_switch", bridge.source("Item") == "graph")
records.rebuild_graph()
g, p = records.parity_rows("Item")
deps_pg = {(i["key"], d) for i in records.items(far) for d in i["depends"] if d in {x["key"] for x in records.items(far)}}
deps_g = {(r["a"], r["b"]) for r in q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item)-[:DEPENDS_ON]->(j:Item)
                                         RETURN i.key AS a, j.key AS b""", e=far)}
check("test_rebuild_graph_matches", bridge.parity(g, p)["ok"] and deps_pg == deps_g
      and q1("MATCH (:Item {id:$i})-[:HAS_DOC]->(:Document) RETURN 1 AS x", i=pan_id) is not None)

check("audit_chain_verifies", verify_chain()[0])
check("audit_names_actors", db.fetch1("SELECT count(*) AS n FROM audit_log WHERE action LIKE 'policy.%%' AND actor <> ''")["n"] >= 8)
print(f"\nphase 1: {ok} checks passed")
db.close()

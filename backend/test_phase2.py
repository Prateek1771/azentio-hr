"""Phase 2 self-checks: documents in Postgres, policy-driven routing, human decisions with provenance, spot checks,
LLM-down behaviour and the threshold policy inside rule-set versions.

Runs against a throwaway Postgres database and a throwaway FalkorDB graph; the LLM is replaced by a fake.
Usage: cd backend && uv run python test_phase2.py
"""
import asyncio
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

os.environ["FALKORDB_GRAPH"] = "test_phase2"  # before anything imports graph
for k in ("OPENROUTER_API_KEY", "LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY", "GMAIL_REFRESH_TOKEN"):
    os.environ[k] = ""

from shared import db  # noqa: E402

db.use_test_database("azentio_test_p2")

import psycopg  # noqa: E402

from graph import q, q1, new_id  # noqa: E402

q("MATCH (n) DETACH DELETE n")  # fresh graph

import extract  # noqa: E402
import ingest  # noqa: E402
import records  # noqa: E402
from audit_log.service import verify_chain  # noqa: E402
from documents import route, service, store as docs  # noqa: E402
from documents.service import DocError  # noqa: E402
from identity import service as idsvc  # noqa: E402
from policy import store as pstore  # noqa: E402
from shared import bridge, jobs  # noqa: E402

SEED = Path(__file__).parent / "seed"
service.UPLOADS = Path(tempfile.mkdtemp(prefix="azentio-p2-"))  # never write test files into the real uploads volume
ok = 0


def check(name, cond, detail=""):
    global ok
    assert cond, f"{name} {detail}"
    ok += 1
    print(f"  ok  {name}")


def drain():
    while asyncio.run(jobs.run_once()):
        pass


def raises(fn, *exc):
    try:
        fn()
    except exc as e:
        return e
    return None


# ---------- fixtures ----------
ingest.import_requirements("requirements.json", (SEED / "requirements.json").read_bytes())
ingest.import_owners("owners.csv", (SEED / "owners.csv").read_bytes())
hr = idsvc.create_user("hr@t.io", "HR", "hr_ops", "hr-pass-12345", actor="test")
ap1 = idsvc.create_user("ap1@t.io", "Ap One", "approver", "ap1-pass-12345", actor="test")
ap2 = idsvc.create_user("ap2@t.io", "Ap Two", "approver", "ap2-pass-12345", actor="test")
today = date.today()


def joiner(name, email, days=20, location="Bengaluru"):
    return ingest.upsert_employee({"name": name, "email": email, "role": "Software Engineer", "grade": "G3",
                                   "department": "Engineering", "location": location, "manager_email": "",
                                   "joining_date": (today + timedelta(days=days)).isoformat(), "date_of_birth": "1997-04-12"})


# ---------- migration: graph documents -> Postgres, parity gate ----------
legacy = joiner("Legacy Person", "legacy@t.io")
legacy_item = records.item_by_key(legacy, "pan_card")
q("""CREATE (d:Document {id:'doc_legacy1', employee_id:$e, filename:'pan.png', path:'/nope/pan.png', mime:'image/png',
     size:10, source:'portal', status:'Accepted', ts:1700000000.0, extracted:'{"full_name":"Legacy Person","_model":"m0"}',
     issues:'[]', confidence:0.97, doc_type:'pan_card'})""", e=legacy)
q("MATCH (i:Item {id:$i}), (d:Document {id:'doc_legacy1'}) CREATE (i)-[:HAS_DOC]->(d)", i=legacy_item["id"])
q("""MATCH (d:Document {id:'doc_legacy1'}), (e:Employee {id:$e}) CREATE (d)-[m:MISMATCH]->(e)
     SET m.field='date_of_birth', m.expected='1997-04-12', m.found='1997-04-13'""", e=legacy)
rep = docs.backfill()
g, p = docs.parity_rows()
check("test_backfill_reports_missing_files", rep["documents"] == 1 and rep["missing_files"] == ["doc_legacy1"])
check("test_parity_documents", bridge.parity(g, p)["ok"], bridge.parity(g, p))
bf = docs.decisions("doc_legacy1")
check("test_backfill_decision_is_honest", len(bf) == 1 and bf[0]["kind"] == "backfill" and bf[0]["decided_by"] is None
      and docs.get("doc_legacy1")["current_decision_id"] == bf[0]["id"])
check("test_backfill_idempotent", docs.backfill()["documents"] == 0)
db.execute("UPDATE documents SET confidence=0.5 WHERE id='doc_legacy1'")
g, p = docs.parity_rows()
check("test_parity_detects_drift", not bridge.parity(g, p)["ok"])
db.execute("UPDATE documents SET confidence=0.97 WHERE id='doc_legacy1'")
bridge.switch("Document", *docs.parity_rows())
check("test_bridge_switched", bridge.source("Document") == "postgres")

# ---------- fake model ----------
FAKE: dict[str, object] = {}


async def fake_extract(data, mime, filename, doc_types, hint=None):
    r = FAKE[filename]
    if isinstance(r, Exception):
        raise r
    return {**r, "_model": "fake-vlm", "_tokens": 1, "_prompt_version": extract.PROMPT_VERSION}

extract.extract = fake_extract


def good(doc_type, name, conf, **kw):
    base = {"doc_type": doc_type, "legible": True, "full_name": name, "document_number": "X1234", "date_of_birth": "1997-04-12",
            "address": "1 MG Road", "issue_date": "2020-01-01", "expiry_date": (today + timedelta(days=3650)).isoformat(),
            "confidence": conf}
    return {**base, **kw}


def upload(eid, key, filename, fake, data=b"\x89PNG fake", mime="image/png"):
    FAKE[filename] = fake
    out = service.receive(eid, filename, data, mime, "portal", key)
    check(f"receive_returns_submitted ({filename})", out["status"] == "Submitted" and docs.get(out["document_id"])["status"] == "Submitted")
    return out["document_id"]


def last_decision(did):
    return docs.decisions(did)[-1]


def open_reviews(did, kind="doc.review"):
    return db.fetch("SELECT * FROM work_queue WHERE entity_type='document' AND entity_id=%s AND kind=%s AND status='open'", (did, kind))


aarav = joiner("Aarav Sharma", "aarav@t.io")
v_active = pstore.active_version()["id"]

# ---------- routing ----------
d_id = upload(aarav, "photo_id", "passport.png", good("photo_id", "Aarav Sharma", 1.0))
drain()
dec = last_decision(d_id)
check("test_high_risk_never_auto", docs.get(d_id)["status"] == "Under Review" and dec["kind"] == "routed_review"
      and dec["reason_code"] == "high_risk" and len(open_reviews(d_id)) == 1
      and records.item_by_key(aarav, "photo_id")["status"] == "Under Review")
e = raises(lambda: db.execute("""INSERT INTO doc_decisions (document_id, kind, reason_code, status_after, tier)
                                  VALUES (%s, 'auto_accept', 'x', 'Accepted', 'high')""", (d_id,)), psycopg.Error)
check("test_high_risk_never_auto_db_trigger", e is not None)
check("test_decide_high_tier_pure", route.decide(file_issues=[], issues=[], extraction_ok=True, on_checklist=True, tier="high",
                                                  confidence=1.0, threshold=0.5).status == "Under Review")

a_id = upload(aarav, "address_proof", "bill.png", good("address_proof", "Aarav Sharma", 0.95))
drain()
check("test_low_risk_auto_accept", docs.get(a_id)["status"] == "Accepted" and last_decision(a_id)["kind"] == "auto_accept"
      and records.item_by_key(aarav, "address_proof")["status"] == "Accepted" and not open_reviews(a_id))
b_id = upload(aarav, "bank_details", "cheque.png", good("bank_details", "Aarav Sharma", 0.91))  # bank threshold 0.92
drain()
check("test_low_risk_threshold", docs.get(b_id)["status"] == "Under Review" and last_decision(b_id)["reason_code"] == "low_confidence"
      and abs(last_decision(b_id)["threshold"] - 0.92) < 1e-6 and len(open_reviews(b_id)) == 1)

p_id = upload(aarav, "pan_card", "pan.png", good("pan_card", "Rahul Verma", 0.99))
drain()
n_mail = db.fetch1("SELECT count(*) AS n FROM jobs WHERE kind='notify.correction' AND payload->>'document_id'=%s", (p_id,))["n"]
check("test_issues_go_to_correction", docs.get(p_id)["status"] == "Correction Requested" and n_mail == 1
      and last_decision(p_id)["kind"] == "auto_correction" and docs.mismatches_of(p_id)[0]["field"] == "name"
      and not open_reviews(p_id))
asyncio.run(service.extract_job({"payload": {"document_id": p_id}, "attempts": 1, "max_attempts": 4}))  # a stray retry
check("test_extract_retry_adds_nothing", len(docs.decisions(p_id)) == 1 and db.fetch1(
    "SELECT count(*) AS n FROM jobs WHERE kind='notify.correction' AND payload->>'document_id'=%s", (p_id,))["n"] == 1)

u_id = upload(aarav, None, "mystery.png", good("w4_form", "Aarav Sharma", 0.99))  # US form, not on an Indian checklist
drain()
check("test_unexpected_type_to_review", docs.get(u_id)["status"] == "Under Review"
      and last_decision(u_id)["reason_code"] == "unexpected_type" and docs.get(u_id)["item_id"] is None)

f_id = upload(aarav, "education_certificate", "degree.zip", good("education_certificate", "Aarav Sharma", 0.99), mime="application/zip")
drain()
check("test_file_issue_to_correction", docs.get(f_id)["status"] == "Correction Requested"
      and last_decision(f_id)["reason_code"] == "file_check")

# ---------- LLM down: retries, then review with ai_unavailable; never stuck in Submitted ----------
x_id = upload(aarav, "experience_letter", "relieving.png", RuntimeError("No AI key is configured"))
attempts = 0
while docs.get(x_id)["status"] == "Submitted" and attempts < 10:
    db.execute("UPDATE jobs SET run_at=now() WHERE state='queued'")
    drain()
    attempts += 1
job = db.fetch1("SELECT * FROM jobs WHERE idempotency_key=%s", (f"doc.extract:{x_id}",))
check("test_llm_down_routes_to_review", docs.get(x_id)["status"] == "Under Review" and last_decision(x_id)["reason_code"] == "ai_unavailable"
      and job["state"] == "done" and job["attempts"] == service.EXTRACT_ATTEMPTS and len(open_reviews(x_id)) == 1, (job["state"], job["attempts"]))

# ---------- provenance ----------
auto = db.fetch("""SELECT * FROM doc_decisions WHERE decided_by IS NULL AND kind <> 'backfill'
                   AND reason_code NOT IN ('file_check', 'ai_unavailable')""")
check("test_provenance_complete", len(auto) >= 4 and all(
    r["model"] == "fake-vlm" and r["prompt_version"] == extract.PROMPT_VERSION and r["confidence"] is not None
    and r["policy_version_id"] == v_active and r["tier"] in ("low", "high") and r["threshold"] and r["checks"] for r in auto))
check("test_prompt_version_bumped", {"2026-11-01": "a68dce9a4d9db0f48024fcd8a654fdb1df8dfdd3dd40e66edf8c82fc4797d88b"}
      .get(extract.PROMPT_VERSION) == extract.PROMPT_SHA, "prompt text changed: bump extract.PROMPT_VERSION and record its hash here")
ids = [new_id("doc") for _ in range(10000)]
rate = sum(route.sampled(i, 0.1) for i in ids) / len(ids)
check("test_spot_check_rate", 0.09 <= rate <= 0.11 and all(route.sampled(i, 0.1) == route.sampled(i, 0.1) for i in ids[:100]), rate)

# ---------- human decisions ----------
e = raises(lambda: service.decide_human(d_id, ap1, "accept", "verified_against_original", ""), DocError)
check("test_human_decision_requires_reason", e is not None and e.status == 422)
e = raises(lambda: db.execute("""INSERT INTO doc_decisions (document_id, kind, decided_by, reason_code, status_after)
                                  VALUES (%s, 'human_accept', %s, 'x', 'Accepted')""", (d_id, ap1["id"])), psycopg.Error)
check("test_human_decision_requires_reason_db", e is not None)
e = raises(lambda: service.decide_human(d_id, hr, "accept", "verified_against_original", "looks fine"), DocError)
check("test_high_risk_requires_approver", e is not None and e.status == 403)
service.decide_human(d_id, ap1, "accept", "verified_against_original", "Matches the original passport", decision_key="k-1")
service.decide_human(d_id, ap1, "accept", "verified_against_original", "Matches the original passport", decision_key="k-1")  # double click
drain()
hum = [r for r in docs.decisions(d_id) if r["kind"] == "human_accept"]
check("test_human_accept_recorded", docs.get(d_id)["status"] == "Accepted" and len(hum) == 1 and hum[0]["decided_by"] == ap1["id"]
      and hum[0]["model"] == "fake-vlm" and not open_reviews(d_id) and records.item_by_key(aarav, "photo_id")["status"] == "Accepted")
e = raises(lambda: service.decide_human(d_id, ap1, "reject", "wrong_document", "changed my mind"), DocError)
check("test_decided_document_needs_override", e is not None and e.status == 409)
service.decide_human(b_id, hr, "accept", "verified_against_original", "Bank letter checked against the original")
check("test_hr_ops_decides_low_tier", docs.get(b_id)["status"] == "Accepted")
check("test_graph_projection_follows", q1("MATCH (:Item)-[:HAS_DOC]->(d:Document {id:$d}) RETURN d.status AS s", d=d_id)["s"] == "Accepted")
e = raises(lambda: db.execute("UPDATE doc_decisions SET reason_text='edited' WHERE document_id=%s", (d_id,)), psycopg.Error)
check("test_decisions_append_only", e is not None)

# ---------- override and spot checks ----------
service.override(a_id, ap1, "request_correction", "illegible", "The bill is from 2019, older than 3 months")
drain()
check("test_override_reverses_auto_accept", docs.get(a_id)["status"] == "Correction Requested"
      and last_decision(a_id)["kind"] == "human_override" and records.item_by_key(aarav, "address_proof")["status"] == "Correction Requested"
      and db.fetch1("SELECT count(*) AS n FROM jobs WHERE kind='notify.correction' AND payload->>'document_id'=%s", (a_id,))["n"] == 1)

route.DEFAULT_DOC_POLICY["spot_check_rate"] = 1.0  # sample everything for this upload
s_id = upload(aarav, "address_proof", "bill2.png", good("address_proof", "Aarav Sharma", 0.97))
drain()
route.DEFAULT_DOC_POLICY["spot_check_rate"] = 0.10
sc = open_reviews(s_id, "doc.spot_check")
check("test_spot_check_sampled", docs.get(s_id)["status"] == "Accepted" and len(sc) == 1)
service.spot_check(sc[0]["id"], ap2, "fail", "Address on the bill is not the joiner's")
drain()
check("test_spot_check_fail_reopens", docs.get(s_id)["status"] == "Under Review" and last_decision(s_id)["kind"] == "spot_check_fail"
      and any(r["detail"]["reason_code"] == "spot_check_failed" for r in open_reviews(s_id))
      and records.item_by_key(aarav, "address_proof")["status"] == "Under Review" and not open_reviews(s_id, "doc.spot_check"))

check("audit_chain_verifies", verify_chain()[0])
print(f"\nphase 2: {ok} checks passed")

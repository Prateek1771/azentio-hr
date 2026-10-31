"""Self-checks for the pure logic: rule matching, blocking, validation, ingestion. Run: uv run python test_core.py"""
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

from graph import q as gq, q1 as gq1, ensure_indexes  # noqa: E402
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

print("all checks passed")

"""CSV / Excel / JSON ingestion with validation, plus first-run seeding."""
import io
import json
import os
import re
import secrets
from datetime import datetime, date
from pathlib import Path

import pandas as pd

import checklist
import owners
import records
import templates
from graph import q1, event

SEED = Path(__file__).parent / "seed"
EMP_REQUIRED = ["name", "email", "role", "grade", "department", "location", "manager_email", "joining_date"]
OWNER_REQUIRED = ["name", "email", "department", "type"]
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y", "%Y/%m/%d"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def read_table(filename: str, data: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(data), dtype=object).fillna("")
    elif ext == ".json":
        obj = json.loads(data)
        if isinstance(obj, dict):  # {"employees": [...]} or any single list value
            obj = next((v for v in obj.values() if isinstance(v, list)), [obj])
        df = pd.DataFrame(obj).fillna("")
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Use CSV, Excel (.xlsx) or JSON.")
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df.to_dict("records")


def norm_date(v) -> str | None:
    if isinstance(v, (datetime, date)):  # Excel cells
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def validate_employees(rows: list[dict], departments: list[str], locations: dict, managers: set[str]) -> tuple[list, list]:
    """Pure. Returns (valid_rows, errors[{row, field, message}]). Row numbers are 1-based data rows."""
    valid, errors, seen = [], [], set()
    file_emails = {str(r.get("email", "")).strip().lower() for r in rows}
    for n, r in enumerate(rows, start=1):
        r = {k: (str(v).strip() if not isinstance(v, (datetime, date)) else v) for k, v in r.items()}
        errs = [(f, "Missing mandatory field") for f in EMP_REQUIRED if not r.get(f)]
        if r.get("email") and not EMAIL_RE.match(r["email"]):
            errs.append(("email", f"Invalid email '{r['email']}'"))
        for f in ("joining_date", "date_of_birth"):
            if r.get(f):
                d = norm_date(r[f])
                if d is None:
                    errs.append((f, f"Unrecognised date '{r[f]}' (use YYYY-MM-DD)"))
                else:
                    r[f] = d
        if r.get("department") and r["department"] not in departments:
            errs.append(("department", f"Unknown department '{r['department']}'"))
        if r.get("location") and r["location"] not in locations:
            errs.append(("location", f"Unknown location '{r['location']}'"))
        me = str(r.get("manager_email", "")).lower()
        if me and me not in managers and me not in file_emails:
            errs.append(("manager_email", f"Manager '{me}' not found in owners or employees"))
        if r.get("email", "").lower() in seen:
            errs.append(("email", "Duplicate employee in file"))
        seen.add(r.get("email", "").lower())
        if errs:
            errors += [{"row": n, "field": f, "message": m, "name": r.get("name", "")} for f, m in errs]
        else:
            valid.append(r)
    return valid, errors


def upsert_employee(r: dict, reason: str = "import") -> str:
    existing = records.employee_by(email=r["email"])
    eid = (existing["id"] if existing else None) or r.get("employee_id") or f"E{secrets.randbelow(90000) + 10000}"
    props = {k: r.get(k, "") for k in EMP_REQUIRED + ["date_of_birth"]}
    props["email"] = props["email"].lower()
    props["manager_email"] = props["manager_email"].lower()
    if existing:
        records.update_employee(eid, **props)  # also refreshes REPORTS_TO in the graph projection
    else:
        records.save_employee({**props, "id": eid, "token": secrets.token_urlsafe(16), "thread_id": "",
                               "created": datetime.now().isoformat()})
        event(eid, "profile", f"Onboarding record created ({reason})")
    checklist.regenerate(eid)
    return eid


def import_employees(filename: str, data: bytes) -> dict:
    rows = read_table(filename, data)
    managers = owners.emails("manager") | records.emails()
    valid, errors = validate_employees(rows, checklist.config("departments") or [], checklist.config("locations") or {}, managers)
    ids = [upsert_employee(r) for r in valid]
    return {"kind": "employees", "total": len(rows), "imported": len(ids), "errors": errors, "ids": ids}


def import_owners(filename: str, data: bytes) -> dict:
    rows = read_table(filename, data)
    hr = os.getenv("HR_MAILBOX", "hr@example.com")
    errors, good = [], []
    for i, r in enumerate(rows, start=1):
        r = {k: str(v).strip() for k, v in r.items()}
        m = re.fullmatch(r"\{HR\+(\w+)\}", r.get("email", ""))  # seed placeholder → plus-address of the HR mailbox
        if m:
            user, _, domain = hr.partition("@")
            r["email"] = f"{user}+{m.group(1)}@{domain}"
        miss = [f for f in OWNER_REQUIRED if not r.get(f)]
        if miss or not EMAIL_RE.match(r.get("email", "")):
            errors += [{"row": i, "field": f, "message": "Missing mandatory field", "name": r.get("name", "")} for f in miss]
            if not miss:
                errors.append({"row": i, "field": "email", "message": f"Invalid email '{r['email']}'", "name": r.get("name", "")})
            continue
        good.append(r)
    n = owners.upsert(good) if good else 0
    return {"kind": "owners", "total": len(rows), "imported": n, "errors": errors}


def _parse_requirements(filename: str, data: bytes) -> tuple[dict, list[dict], list[dict]]:
    """A requirements file (JSON with rules + config, or a rules table) -> (file object, normalised rules, row errors).
    `compliance` defaults to store.COMPLIANCE_DEFAULT unless the row sets it, so re-importing a file keeps the flags."""
    from policy import store
    obj = json.loads(data) if filename.endswith(".json") else {"rules": read_table(filename, data)}
    errors, rules = [], []
    for i, r in enumerate(obj.get("rules", []), start=1):
        miss = [f for f in ("id", "item", "kind", "category", "description") if not r.get(f)]
        if miss:
            errors += [{"row": i, "field": f, "message": "Missing mandatory field", "name": r.get("id", "")} for f in miss]
            continue
        if str(r.get("compliance") or "").strip() == "":
            r = {**r, "compliance": str(r["id"]).strip() in store.COMPLIANCE_DEFAULT}
        rules.append(store.normalise_rule(r))
    return obj, rules, errors


def import_requirements(filename: str, data: bytes, user: dict | None = None) -> dict:
    """The one way a requirements file enters the system (ADR 0009). With no active rule set yet (a fresh install), it
    becomes v1 directly, authored by `user` or the system. Once one is active, it opens a governed draft instead: it
    reaches joiners only after approval and publishing."""
    from policy import store
    from shared import db
    if store.active_version():
        if not user:
            raise ValueError("A rule set is already active: upload requirements on the import page to open a draft.")
        return import_requirements_draft(filename, data, user)
    obj, rules, errors = _parse_requirements(filename, data)
    config = {k: obj.get(k, {} if k in ("doc_types", "locations", "doc_policy") else []) for k in store.CONFIG_KEYS}
    created = False
    with db.tx() as c:
        c.execute("SELECT pg_advisory_xact_lock(7243010)")  # one v1, even if two starts race
        if not store.active_version(conn=c):
            created = True
            store.create_version("active", f"Initial rule set (imported from {filename})", config, rules,
                                 user["id"] if user else None, c, reason="First rule set on a new install")
            import audit
            audit.record_audit("policy.published", "rule_set_version", "v1",
                               f"Rule set v1 created from {len(rules)} imported rules",
                               actor="HR" if user else "system", conn=c)
    if not created:  # another starter won the race: its v1 stands, nothing was imported here
        return {"kind": "requirements", "total": len(obj.get("rules", [])), "imported": 0, "errors": errors,
                "message": "A rule set became active meanwhile; nothing imported."}
    for eid in records.employee_ids():
        checklist.regenerate(eid, "requirements updated")
    return {"kind": "requirements", "total": len(obj.get("rules", [])), "imported": len(rules), "errors": errors}


def import_requirements_draft(filename: str, data: bytes, user: dict) -> dict:
    """An uploaded requirements file becomes a draft rule set (it reaches joiners only once approved and published)."""
    from policy import store
    from shared import db
    obj, rules, errors = _parse_requirements(filename, data)
    if store.open_version():
        raise ValueError("A rule-set draft is already open. Finish or discard it before importing requirements.")
    act = store.active_version()
    config = {**(act["config"] if act else {}), **{k: obj[k] for k in store.CONFIG_KEYS if k in obj}}
    with db.tx() as c:
        vid = store.create_version("draft", f"Imported from {filename}", config, rules, user["id"], c,
                                   based_on=act["id"] if act else None)
        num = c.execute("SELECT number FROM rule_set_versions WHERE id=%s", (vid,)).fetchone()["number"]
    return {"kind": "requirements", "total": len(obj.get("rules", [])), "imported": len(rules), "errors": errors,
            "draft_version": num, "draft_id": vid,
            "message": f"Saved as draft v{num}. Preview its impact and submit it for approval under Requirement rules."}


def seed_rules() -> dict | None:
    """The first rule set, if there's none yet: HR's own archived graph rules on an upgrade from before rule-set
    versions (what `ensure_active` used to build v1 from), otherwise the sample file. None if one is already active."""
    from policy import legacy, store
    if store.active_version():
        return None
    archived = legacy.archived_requirements()
    if archived:
        return import_requirements("legacy-graph-rules.json", json.dumps(archived).encode())
    return import_requirements("requirements.json", (SEED / "requirements.json").read_bytes())


async def seed():
    """Load sample data on first run. "First run" is decided per record type from Postgres, the record: an empty graph
    alone (lost or wiped) must never re-seed over HR's data. It rebuilds the projection instead."""
    import chat
    from graph import ensure_indexes
    ensure_indexes()
    # Decided before anything below writes: a replay would project single employees and hide the loss.
    graph_lost = bool(records.employee_ids()) and not q1("MATCH (e:Employee) RETURN e LIMIT 1")
    seed_rules()
    if not owners.all():
        import_owners("owners.csv", (SEED / "owners.csv").read_bytes())
    if not templates.all():
        for t in json.loads((SEED / "email_templates.json").read_text(encoding="utf-8")):
            templates.upsert(t)
    if not records.employee_ids():
        import_employees("employees.csv", (SEED / "employees.csv").read_bytes())
        demo_progress()
    if graph_lost:  # rebuild the projection (owners first, then every employee) from Postgres
        records.rebuild_graph()
    else:  # the Owner projection follows Postgres on every start, also for a lost graph with no employees yet
        records.project_owners()
    from connectors.seed import seed_connectors
    seed_connectors()
    import policies
    if not policies.all():
        for f in sorted((SEED / "policies").glob("*.md")):
            await chat.index_policy(f.name, f.read_text(encoding="utf-8"))
    elif not q1("MATCH (c:PolicyChunk) RETURN c LIMIT 1"):  # the index was lost (e.g. a wiped graph): rebuild it
        await chat.reindex_policies()


DEMO_PROGRESS = {  # employee -> item keys already done, so the dashboard shows a realistic mix on first run
    "E1006": ["photo_id", "address_proof", "pan_card", "bank_details", "experience_letter", "infosec_ack", "coc_ack"],
    "E1002": ["photo_id", "address_proof", "pan_card", "infosec_ack"],
    "E1004": ["photo_id", "address_proof", "pan_card", "bank_details", "education_certificate", "coc_ack"],
    "E1005": ["w4_form"],
}


def demo_progress():
    for eid, keys in DEMO_PROGRESS.items():
        records.set_items_status(eid, keys, {"ack": "Acknowledged", "task": "Completed"})
        from documents.seed import accept_for_demo  # documents only through a recorded decision, even for demo data
        for key in keys:
            accept_for_demo(eid, key)
        checklist.recompute(eid)

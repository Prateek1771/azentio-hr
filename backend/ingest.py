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
from graph import q, q1, event

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
    existing = q1("MATCH (e:Employee) WHERE toLower(e.email)=toLower($m) RETURN e", m=r["email"])
    eid = (existing["e"]["id"] if existing else None) or r.get("employee_id") or f"E{secrets.randbelow(90000) + 10000}"
    props = {k: r.get(k, "") for k in EMP_REQUIRED + ["date_of_birth"]}
    props["email"] = props["email"].lower()
    props["manager_email"] = props["manager_email"].lower()
    if existing:
        q("MATCH (e:Employee {id:$id}) SET e += $p", id=eid, p=props)
    else:
        q("CREATE (e:Employee) SET e = $p", p={**props, "id": eid, "token": secrets.token_urlsafe(16), "thread_id": "",
                                      "created": datetime.now().isoformat()})
        event(eid, "profile", f"Onboarding record created ({reason})")
    q("MATCH (e:Employee {id:$id})-[r:REPORTS_TO]->() DELETE r", id=eid)
    q("MATCH (e:Employee {id:$id}), (m) WHERE (m:Owner OR m:Employee) AND toLower(m.email)=e.manager_email "
      "CREATE (e)-[:REPORTS_TO]->(m)", id=eid)
    checklist.regenerate(eid)
    return eid


def import_employees(filename: str, data: bytes) -> dict:
    rows = read_table(filename, data)
    managers = {r["m"] for r in q("MATCH (o:Owner {type:'manager'}) RETURN toLower(o.email) AS m")}
    managers |= {r["m"] for r in q("MATCH (e:Employee) RETURN toLower(e.email) AS m")}
    valid, errors = validate_employees(rows, checklist.config("departments") or [], checklist.config("locations") or {}, managers)
    ids = [upsert_employee(r) for r in valid]
    return {"kind": "employees", "total": len(rows), "imported": len(ids), "errors": errors, "ids": ids}


def import_owners(filename: str, data: bytes) -> dict:
    rows = read_table(filename, data)
    hr = os.getenv("HR_MAILBOX", "hr@example.com")
    errors, n = [], 0
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
        q("MERGE (o:Owner {email:$e}) SET o.name=$n, o.department=$d, o.type=$t",
          e=r["email"].lower(), n=r["name"], d=r["department"], t=r["type"])
        n += 1
    return {"kind": "owners", "total": len(rows), "imported": n, "errors": errors}


def import_requirements(filename: str, data: bytes) -> dict:
    obj = json.loads(data) if filename.endswith(".json") else {"rules": read_table(filename, data)}
    for key in ("departments", "locations", "doc_types", "roles"):
        if key in obj:
            q("MERGE (c:Config {key:$k}) SET c.value=$v", k=key, v=json.dumps(obj[key]))
    errors = []
    rules = obj.get("rules", [])
    for i, r in enumerate(rules, start=1):
        if isinstance(r.get("match"), str):
            r["match"] = json.loads(r["match"] or "{}")
        if isinstance(r.get("depends_on"), str):
            r["depends_on"] = [d.strip() for d in r["depends_on"].split(",") if d.strip()]
        miss = [f for f in ("id", "item", "kind", "category", "description") if not r.get(f)]
        if miss:
            errors += [{"row": i, "field": f, "message": "Missing mandatory field", "name": r.get("id", "")} for f in miss]
            continue
        save_rule(r, ord_=i)
    for e in q("MATCH (e:Employee) RETURN e.id AS id"):
        checklist.regenerate(e["id"], "requirements updated")
    return {"kind": "requirements", "total": len(rules), "imported": len(rules) - len({e['row'] for e in errors}), "errors": errors}


def save_rule(r: dict, ord_: int | None = None):
    p = {"id": r["id"], "item": r["item"], "name": r.get("name", ""), "kind": r["kind"], "category": r["category"],
         "match": json.dumps(r.get("match") or {}), "offset_days": int(r.get("offset_days") or 0),
         "depends_on": list(r.get("depends_on") or []), "description": r["description"],
         "policy_ref": r.get("policy_ref", ""), "auto": r.get("auto", "")}
    if ord_ is not None:
        p["ord"] = ord_
    q("MERGE (r:Rule {id:$id}) SET r += $p", id=r["id"], p=p)


async def seed():
    """Load sample data on first run (empty graph)."""
    import chat
    from graph import ensure_indexes
    ensure_indexes()
    if not q1("MATCH (c:Config {key:'doc_types'}) RETURN c"):
        import_requirements("requirements.json", (SEED / "requirements.json").read_bytes())
        import_owners("owners.csv", (SEED / "owners.csv").read_bytes())
        for t in json.loads((SEED / "email_templates.json").read_text(encoding="utf-8")):
            q("MERGE (t:EmailTemplate {id:$id}) SET t += $p", id=t["id"], p=t)
        import_employees("employees.csv", (SEED / "employees.csv").read_bytes())
        demo_progress()
    from connectors.seed import seed_connectors
    seed_connectors()
    if not q1("MATCH (c:Config {key:'roles'}) RETURN c"):  # top-up for graphs seeded before roles existed
        roles = json.loads((SEED / "requirements.json").read_text(encoding="utf-8"))["roles"]
        q("MERGE (c:Config {key:'roles'}) SET c.value=$v", v=json.dumps(roles))
    if not q1("MATCH (c:PolicyChunk) RETURN c LIMIT 1"):
        for f in sorted((SEED / "policies").glob("*.md")):
            await chat.index_policy(f.name, f.read_text(encoding="utf-8"))


DEMO_PROGRESS = {  # employee -> item keys already done, so the dashboard shows a realistic mix on first run
    "E1006": ["photo_id", "address_proof", "pan_card", "bank_details", "experience_letter", "infosec_ack", "coc_ack"],
    "E1002": ["photo_id", "address_proof", "pan_card", "infosec_ack"],
    "E1004": ["photo_id", "address_proof", "pan_card", "bank_details", "education_certificate", "coc_ack"],
    "E1005": ["w4_form"],
}


def demo_progress():
    for eid, keys in DEMO_PROGRESS.items():
        q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item) WHERE i.key IN $k
             SET i.status = CASE i.kind WHEN 'document' THEN 'Accepted' WHEN 'ack' THEN 'Acknowledged' ELSE 'Completed' END""",
          e=eid, k=keys)
        checklist.recompute(eid)

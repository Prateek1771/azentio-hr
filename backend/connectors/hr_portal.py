"""HR Portal: the built-in demo HR system (target). Employees matched on work email; supports custom fields."""
import json
import re
import secrets

from graph import q, q1, event
from .base import Connector, FieldSpec, RecordRejected

BUILTIN = [
    FieldSpec("employee_number", "Employee number", required=True, generated=True, description="Generated as EMP-##### when unmapped"),
    FieldSpec("first_name", "First name", required=True),
    FieldSpec("last_name", "Last name", required=True),
    FieldSpec("work_email", "Work email", required=True, description="Used to match existing employees"),
    FieldSpec("phone", "Phone"),
    FieldSpec("department_name", "Department", description="Must already exist in the workspace"),
    FieldSpec("job_title", "Job title", required=True),
    FieldSpec("grade", "Grade"),
    FieldSpec("location", "Location"),
    FieldSpec("manager_name", "Manager"),
    FieldSpec("start_date", "Start date", "date", required=True),
    FieldSpec("employment_status", "Employment status", required=True, generated=True,
              description="active, on_leave or terminated; defaults to active"),
]
STATUSES = {"active", "on_leave", "terminated"}
FIELD_TYPES = {"string", "number", "boolean", "date"}


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def validate(values: dict, departments: list[str], custom: list[FieldSpec]) -> dict:
    """Pure: apply HR Portal write rules. Returns cleaned values or raises RecordRejected."""
    from validate import parse_date
    v = {k: (val.strip() if isinstance(val, str) else val) for k, val in values.items() if val not in (None, "")}
    v.setdefault("employment_status", "active")
    v.setdefault("employee_number", f"EMP-{secrets.randbelow(90000) + 10000}")
    missing = [f.label for f in BUILTIN + custom if f.required and not v.get(f.key)]
    if missing:
        raise RecordRejected(f"Missing required field(s): {', '.join(missing)}")
    if "@" not in str(v["work_email"]):
        raise RecordRejected(f"Work email '{v['work_email']}' is not a valid address")
    v["work_email"] = str(v["work_email"]).lower()
    if v.get("department_name"):
        match = next((d for d in departments if d.lower() == str(v["department_name"]).lower()), None)
        if not match:
            raise RecordRejected(f"Department '{v['department_name']}' doesn't exist in this workspace — add it or map a different department")
        v["department_name"] = match
    if not parse_date(str(v["start_date"])):
        raise RecordRejected(f"Start date '{v['start_date']}' is not a valid date (use YYYY-MM-DD)")
    v["start_date"] = str(v["start_date"])[:10]
    if v["employment_status"] not in STATUSES:
        raise RecordRejected(f"Employment status '{v['employment_status']}' must be active, on_leave or terminated")
    return v


class HrPortal(Connector):
    key = "hr_portal"
    record_kind = "employee"

    def custom_fields(self, instance_id: str) -> list[FieldSpec]:
        rows = q("MATCH (f:CustomField {instance_id:$i}) RETURN f ORDER BY f.created", i=instance_id)
        return [FieldSpec(r["f"]["key"], r["f"]["label"], r["f"]["type"], r["f"]["required"], is_custom=True) for r in rows]

    def describe_schema(self, instance_id: str) -> list[FieldSpec]:
        return list(BUILTIN) + self.custom_fields(instance_id)

    def add_field(self, instance_id: str, label: str, data_type: str, required: bool) -> FieldSpec:
        key = slugify(label)
        if not key:
            raise ValueError("Label must contain letters or numbers")
        if data_type not in FIELD_TYPES:
            raise ValueError(f"Type must be one of {', '.join(sorted(FIELD_TYPES))}")
        if any(f.key == key for f in self.describe_schema(instance_id)):
            raise KeyError(f"A field named '{key}' already exists")
        import time
        q("CREATE (f:CustomField {instance_id:$i, key:$k, label:$l, type:$t, required:$r, created:$c})",
          i=instance_id, k=key, l=label.strip(), t=data_type, r=required, c=time.time())
        event(None, "connector", f"HR Portal: custom field '{label.strip()}' ({data_type}) added")
        return FieldSpec(key, label.strip(), data_type, required, is_custom=True)

    def upsert_record(self, instance_id: str, values: dict, duplicate_policy: str = "update") -> tuple[str, str]:
        import checklist
        custom = self.custom_fields(instance_id)
        custom_keys = {f.key for f in custom}
        flat = {k: v for k, v in values.items() if k not in custom_keys}
        flat.update(values.get("custom", {}) if isinstance(values.get("custom"), dict) else {})
        cleaned = validate(flat, checklist.config("departments") or [], custom)
        cleaned["custom"] = {k: cleaned.pop(k) for k in list(cleaned) if k in custom_keys}
        existing = self._find(instance_id, "work_email", cleaned["work_email"])
        if existing:
            if duplicate_policy == "skip":
                return existing["id"], "skipped"
            cleaned["employee_number"] = existing["values"].get("employee_number", cleaned["employee_number"])
            merged = {**existing["values"], **cleaned, "custom": {**existing["values"].get("custom", {}), **cleaned["custom"]}}
            if merged == existing["values"]:
                return existing["id"], "skipped"  # nothing changed: repeat-safe
            return self._write(instance_id, merged, existing["id"]), "updated"
        return self._write(instance_id, cleaned), "created"


def instance_id_for(key: str) -> str | None:
    row = q1("MATCH (i:ConnectorInstance {key:$k}) RETURN i.id AS id", k=key)
    return row["id"] if row else None


def push_employee(eid: str):
    """Onboarding → HR Portal: called when a joiner's HR record is created. Never raises."""
    try:
        iid = instance_id_for("hr_portal")
        if not iid:
            return
        e = q1("MATCH (e:Employee {id:$e}) RETURN e", e=eid)["e"]
        mgr = q1("MATCH (m) WHERE (m:Owner OR m:Employee) AND toLower(m.email)=$m RETURN m.name AS n", m=e.get("manager_email", ""))
        first, _, last = e["name"].partition(" ")
        values = {"first_name": first, "last_name": last or first, "work_email": e["email"], "job_title": e["role"],
                  "department_name": e["department"], "grade": e["grade"], "location": e["location"],
                  "manager_name": mgr["n"] if mgr else "", "start_date": e["joining_date"], "employee_number": e["id"]}
        rid, outcome = HrPortal().upsert_record(iid, values, "update")
        q("MATCH (e:Employee {id:$e})-[s:SYNCED_TO]->() DELETE s", e=eid)
        q("MATCH (e:Employee {id:$e}), (r:Record {id:$r}) CREATE (e)-[:SYNCED_TO]->(r)", e=eid, r=rid)
        stored = HrPortal().get_record(iid, rid) or {"values": {}}
        event(eid, "connector", f"HR Portal employee {outcome} ({stored['values'].get('employee_number', values['employee_number'])})")
    except RecordRejected as ex:
        event(eid, "error", f"HR Portal rejected the employee record: {ex}")
    except Exception as ex:
        event(eid, "error", f"HR Portal sync failed: {ex}")

"""Candidate sources: the built-in demo Candidate Portal and file-based Candidate Import."""
import time

import ingest
from graph import new_id, event
from . import store
from .base import Connector, FieldSpec

CANDIDATE_FIELDS = [
    FieldSpec("candidate_id", "Candidate ID", required=True),
    FieldSpec("first_name", "First name", required=True),
    FieldSpec("last_name", "Last name", required=True),
    FieldSpec("email", "Email", required=True),
    FieldSpec("phone", "Phone"),
    FieldSpec("date_of_birth", "Date of birth", "date"),
    FieldSpec("education", "Education", "list", description="Degrees or certificates"),
    FieldSpec("employment_history", "Employment history", "list", description="Previous employers"),
    FieldSpec("department_name", "Department"),
    FieldSpec("designation", "Designation"),
    FieldSpec("grade", "Grade"),
    FieldSpec("location", "Location"),
    FieldSpec("joining_date", "Joining date", "date"),
    FieldSpec("status", "Status", required=True, description="applied, interviewing, offer_accepted, hired, rejected"),
]
REQUIRED = [f.key for f in CANDIDATE_FIELDS if f.required]


def normalise(row: dict) -> tuple[dict | None, list[str]]:
    """Validate one candidate row. Returns (values, problems)."""
    v = {f.key: row.get(f.key, "") for f in CANDIDATE_FIELDS}
    problems = [f"{k}: missing" for k in REQUIRED if not str(v[k]).strip()]
    if v["email"] and not ingest.EMAIL_RE.match(str(v["email"]).strip()):
        problems.append(f"email: invalid '{v['email']}'")
    for k in ("date_of_birth", "joining_date"):
        if v[k]:
            d = ingest.norm_date(v[k])
            if d is None:
                problems.append(f"{k}: unrecognised date '{v[k]}'")
            v[k] = d or v[k]
    for k in ("education", "employment_history"):
        if isinstance(v[k], str):
            v[k] = [s.strip() for s in v[k].replace(";", "|").split("|") if s.strip()]
    for k, val in v.items():
        if isinstance(val, str):
            v[k] = val.strip()
    v["email"] = str(v["email"]).lower()
    return (None if problems else v), problems


class CandidatePortal(Connector):
    key = "candidate_portal"
    record_kind = "candidate"
    FIELDS = CANDIDATE_FIELDS

    def upsert_candidate(self, instance_id: str, values: dict) -> str:
        existing = self._find(instance_id, "candidate_id", values["candidate_id"])
        return self._write(instance_id, values, existing["id"] if existing else None)


class CandidateImport(CandidatePortal):
    key = "candidate_import"

    def import_file(self, instance_id: str, filename: str, data: bytes) -> dict:
        rows = ingest.read_table(filename, data)
        errors, imported = [], 0
        for n, row in enumerate(rows, start=1):
            values, problems = normalise({k: ("" if v is None else v) for k, v in row.items()})
            if problems:
                name = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
                errors += [{"row": n, "name": name, "field": p.split(":")[0], "message": p.split(": ", 1)[1]} for p in problems]
                continue
            self.upsert_candidate(instance_id, values)
            imported += 1
        run = {"id": new_id("imp"), "instance_id": instance_id, "filename": filename, "total": len(rows),
               "imported": imported, "errors": errors, "ts": time.time()}
        store.add_import_run(run)
        event(None, "connector", f"Candidate Import: {imported}/{len(rows)} candidates imported from {filename}")
        return run

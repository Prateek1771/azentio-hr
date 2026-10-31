"""HTTP API for connectors and integrations (mounted under /api by main.py)."""
import json

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from connectors import registry
from connectors.candidates import CandidateImport
from connectors.hr_portal import HrPortal
import audit
from graph import q, q1

router = APIRouter(prefix="/api")


def _conn(cid: str):
    found = registry.get(cid)
    if not found:
        raise HTTPException(404, "Connector not found")
    return found


def _with_count(inst: dict, impl) -> dict:
    return {**inst, "record_count": impl.count_records(inst["id"])}


@router.get("/connectors")
def list_connectors():
    return [_with_count(*_conn(r["id"])) for r in q("MATCH (i:ConnectorInstance) RETURN i.id AS id ORDER BY i.created")]


@router.get("/connectors/{cid}")
def get_connector(cid: str):
    return _with_count(*_conn(cid))


@router.get("/connectors/{cid}/schema")
def connector_schema(cid: str):
    _, impl = _conn(cid)
    return [f.dict() for f in impl.describe_schema(cid)]


@router.get("/connectors/{cid}/records")
def connector_records(cid: str, page: int = 1, page_size: int = 25, q_: str = Query("", alias="q")):
    _, impl = _conn(cid)
    data = impl.list_records(cid, page, min(max(page_size, 1), 200), q_)
    links = {r["r"]: r["e"] for r in q("""MATCH (e:Employee)-[:FROM_CANDIDATE|SYNCED_TO]->(r:Record {instance_id:$i})
                                         RETURN r.id AS r, e.id AS e""", i=cid)}
    for item in data["items"]:
        item["employee_id"] = links.get(item["id"])
    return data


@router.get("/connectors/{cid}/records/{rid}")
def connector_record(cid: str, rid: str):
    _, impl = _conn(cid)
    rec = impl.get_record(cid, rid)
    if not rec:
        raise HTTPException(404, "Record not found in this connector")
    link = q1("MATCH (e:Employee)-[:FROM_CANDIDATE|SYNCED_TO]->(:Record {id:$r}) RETURN e.id AS id, e.name AS name", r=rid)
    return {**rec, "employee": link}


class AttributeBody(BaseModel):
    label: str
    data_type: str = "string"
    is_required: bool = False


@router.post("/connectors/{cid}/attributes", status_code=201)
def add_attribute(cid: str, body: AttributeBody):
    inst, impl = _conn(cid)
    if not isinstance(impl, HrPortal):
        raise HTTPException(400, f"{inst['name']} does not support custom fields")
    try:
        spec = impl.add_field(cid, body.label, body.data_type, body.is_required).dict()
        audit.record_audit("connector.field_added", "connector", cid, f"Added custom field {spec['label']} to {inst['name']}")
        return spec
    except KeyError as ex:
        raise HTTPException(409, ex.args[0])
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@router.post("/connectors/{cid}/import")
async def connector_import(cid: str, file: UploadFile = File(...)):
    inst, impl = _conn(cid)
    if not isinstance(impl, CandidateImport):
        raise HTTPException(400, f"{inst['name']} does not accept file imports")
    try:
        return impl.import_file(cid, file.filename, await file.read())
    except (ValueError, json.JSONDecodeError) as ex:
        raise HTTPException(400, str(ex))


@router.get("/connectors/{cid}/imports")
def connector_imports(cid: str):
    _conn(cid)
    rows = q("MATCH (r:ImportRun {instance_id:$i}) RETURN r ORDER BY r.ts DESC", i=cid)
    return [{**r["r"], "errors": json.loads(r["r"]["errors"])} for r in rows]


class StartOnboarding(BaseModel):
    template_id: str = "standard"
    send: bool = False
    location: str | None = None
    joining_date: str | None = None


@router.post("/connectors/{cid}/records/{rid}/onboard")
async def onboard_candidate(cid: str, rid: str, body: StartOnboarding):
    from main import create_joiner  # shared with the quick-onboard form
    inst, impl = _conn(cid)
    if inst["record_kind"] != "candidate":
        raise HTTPException(400, "Only candidates can start onboarding")
    rec = impl.get_record(cid, rid)
    if not rec:
        raise HTTPException(404, "Record not found in this connector")
    v = rec["values"]
    f = {"name": f"{v.get('first_name', '')} {v.get('last_name', '')}".strip(), "email": v.get("email", ""),
         "role": v.get("designation", ""), "department": v.get("department_name", ""), "grade": v.get("grade", ""),
         "location": body.location or v.get("location", ""), "joining_date": body.joining_date or v.get("joining_date", ""),
         "date_of_birth": v.get("date_of_birth", "")}
    return await create_joiner(f, body.template_id, body.send, f"started from {inst['name']}", candidate_record=rid)


@router.get("/employees/{eid}/links")
def employee_links(eid: str):
    rows = q("""MATCH (e:Employee {id:$e})-[l:FROM_CANDIDATE|SYNCED_TO]->(r:Record)-[:IN]->(i:ConnectorInstance)
                RETURN type(l) AS type, r.id AS record_id, r.values AS v, i.id AS connector_id, i.name AS connector""", e=eid)
    return [{"type": r["type"], "record_id": r["record_id"], "connector_id": r["connector_id"], "connector": r["connector"],
             "employee_number": json.loads(r["v"]).get("employee_number")} for r in rows]

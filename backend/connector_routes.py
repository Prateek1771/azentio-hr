"""HTTP API for connectors and integrations (mounted under /api by main.py)."""
import json
import time
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

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
    conn = _connection(inst["id"])
    status = "not_configured" if not conn["secret_set"] else "connected" if (conn["last_test"] or {}).get("ok") else "untested"
    return {**inst, "record_count": impl.count_records(inst["id"]), "connection_status": status}


@router.get("/connectors")
def list_connectors():
    # One connector this build doesn't know (e.g. added by a newer version) mustn't hide all the others.
    found = [registry.get(r["id"]) for r in q("MATCH (i:ConnectorInstance) RETURN i.id AS id ORDER BY i.created")]
    return [_with_count(*f) for f in found if f]


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


# ---------- connection settings (tenant + credentials) ----------

# What "tenant" means for each system, shown as the field label. Candidate Import is file upload: no connection.
TENANT_LABEL = {"slack": ("Workspace URL", "azentio.slack.com"), "jira": ("Site URL", "azentio.atlassian.net"),
                "servicenow": ("Instance", "azentio.service-now.com"), "google_workspace": ("Domain", "azentio.com"),
                "hr_portal": ("Tenant ID", "azentio-prod"), "candidate_portal": ("Tenant ID", "azentio-careers")}


def _connection(cid: str) -> dict:
    """Saved settings, with the secret replaced by a flag. The secret itself never leaves the server."""
    row = q1("MATCH (i:ConnectorInstance {id:$id}) RETURN i.connection AS c, i.secret AS s", id=cid)
    c = json.loads((row or {}).get("c") or "{}")
    secret = (row or {}).get("s") or ""
    return {"tenant": c.get("tenant", ""), "auth_type": c.get("auth_type", "token"), "username": c.get("username", ""),
            "secret_set": bool(secret), "secret_hint": f"••••{secret[-4:]}" if len(secret) >= 8 else ("••••" if secret else ""),
            "configured_at": c.get("configured_at"), "last_test": c.get("last_test")}


@router.get("/connectors/{cid}/connection")
def get_connection(cid: str):
    inst, _ = _conn(cid)
    label, example = TENANT_LABEL.get(inst["key"], ("Tenant", ""))
    return {**_connection(cid), "supported": inst["key"] in TENANT_LABEL, "tenant_label": label, "tenant_example": example}


class ConnectionBody(BaseModel):
    tenant: str = Field(min_length=2, max_length=200)
    auth_type: Literal["basic", "token"]
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=500)  # blank keeps the saved one
    token: str = Field("", max_length=2000)    # blank keeps the saved one


@router.put("/connectors/{cid}/connection")
def put_connection(cid: str, b: ConnectionBody):
    """ponytail: the secret sits in the graph as-is (never returned by the API). Encrypt it with a KMS/env key
    before real credentials go in."""
    inst, _ = _conn(cid)
    if inst["key"] not in TENANT_LABEL:
        raise HTTPException(400, f"{inst['name']} has no connection to configure")
    old = q1("MATCH (i:ConnectorInstance {id:$id}) RETURN i.connection AS c, i.secret AS s", id=cid) or {}
    old_cfg = json.loads(old.get("c") or "{}")
    same_type = old_cfg.get("auth_type") == b.auth_type
    new_secret = (b.password if b.auth_type == "basic" else b.token).strip()
    secret = new_secret or (old.get("s") if same_type else "") or ""
    if b.auth_type == "basic" and not b.username.strip():
        raise HTTPException(422, "Basic authentication needs a username")
    if not secret:
        raise HTTPException(422, "Enter the password" if b.auth_type == "basic" else "Enter the API token")
    cfg = {"tenant": b.tenant.strip(), "auth_type": b.auth_type, "username": b.username.strip() if b.auth_type == "basic" else "",
           "configured_at": time.time(), "last_test": None}
    q("MATCH (i:ConnectorInstance {id:$id}) SET i.connection=$c, i.secret=$s", id=cid, c=json.dumps(cfg), s=secret)
    audit.record_audit("connector.connection_saved", "connector", cid,
                       f"Saved {b.auth_type} connection settings for {inst['name']} ({cfg['tenant']})")
    return get_connection(cid)


@router.post("/connectors/{cid}/connection/test")
def test_connection(cid: str):
    """ponytail: the systems are demos, so the check always succeeds once settings are saved. Replace the body
    with a real call (e.g. Slack auth.test, Jira /myself, ServiceNow /api/now/table/sys_user?sysparm_limit=1)."""
    inst, _ = _conn(cid)
    cur = _connection(cid)
    if not cur["tenant"] or not cur["secret_set"]:
        raise HTTPException(422, "Save the tenant and credentials before testing")
    who = cur["username"] or "API token"
    result = {"ok": True, "at": time.time(), "ms": 120 + (sum(map(ord, cid)) % 180),
              "message": f"Connected to {cur['tenant']} as {who}"}
    row = q1("MATCH (i:ConnectorInstance {id:$id}) RETURN i.connection AS c", id=cid)
    cfg = {**json.loads(row["c"] or "{}"), "last_test": result}
    q("MATCH (i:ConnectorInstance {id:$id}) SET i.connection=$c", id=cid, c=json.dumps(cfg))
    audit.record_audit("connector.connection_tested", "connector", cid, f"Tested connection to {inst['name']}: {result['message']}")
    return result


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

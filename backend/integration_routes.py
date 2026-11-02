"""HTTP API: integrations workflow, Approval Center, integration log and audit log (mounted under /api)."""
from functools import wraps

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import audit
from integrations import chat, delivery, engine, service, store
from integrations.run_log import exclusions_of, mask_record
from connectors.base import RecordRejected
from integrations.store import SetupError

router = APIRouter(prefix="/api")


def errors(fn):
    """SetupError → 422 with HR-readable text, missing → 404, concurrent run → 409."""
    @wraps(fn)
    async def wrapper(*a, **kw):
        try:
            out = fn(*a, **kw)
            return await out if hasattr(out, "__await__") else out
        except (SetupError, RecordRejected, engine.CandidateNotReady) as ex:
            raise HTTPException(422, str(ex))
        except LookupError as ex:
            raise HTTPException(404, str(ex))
        except engine.AlreadyRunning as ex:
            raise HTTPException(409, str(ex))
    return wrapper


# ---------- integrations ----------

class CreateBody(BaseModel):
    name: str
    description: str = ""
    source_id: str
    target_id: str


class StartBody(BaseModel):
    message: str = Field(min_length=3, max_length=2000)
    timezone: str | None = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    timezone: str | None = None


@router.get("/integrations")
@errors
def list_integrations():
    return [service.summary(it) for it in store.all_integrations()]


@router.post("/integrations", status_code=201)
@errors
def create_integration(body: CreateBody):
    return service.detail(service.create(body.name, body.description, body.source_id, body.target_id)["id"])


@router.post("/integrations/start", status_code=201)
@errors
async def start_integration(body: StartBody):
    return await chat.start_from_chat(body.message.strip(), body.timezone)


@router.get("/integrations/{iid}")
@errors
def get_integration(iid: str):
    return service.detail(iid)


@router.post("/integrations/{iid}/chat")
@errors
async def chat_turn(iid: str, body: ChatBody):
    return await chat.run_chat_turn(iid, body.message.strip(), body.timezone)


class BasicsBody(BaseModel):
    name: str | None = None
    description: str | None = None
    source_id: str | None = None
    target_id: str | None = None


@router.put("/integrations/{iid}/basics")
@errors
def put_basics(iid: str, body: BasicsBody):
    service.save_basics(iid, body.name, body.description, body.source_id, body.target_id)
    return service.detail(iid)


class RecordsBody(BaseModel):
    statuses: list[str]
    departments: list[str] = []
    duplicate_policy: str = "update"


@router.put("/integrations/{iid}/records")
@errors
def put_records(iid: str, body: RecordsBody):
    service.save_records(iid, body.statuses, body.departments, body.duplicate_policy)
    return service.detail(iid)


class MappingBody(BaseModel):
    rules: list[dict]


@router.post("/integrations/{iid}/mapping/suggest")
@errors
async def suggest(iid: str):
    return await service.suggest_mapping(iid)


@router.put("/integrations/{iid}/mapping")
@errors
def put_mapping(iid: str, body: MappingBody):
    service.save_mapping(iid, body.rules)
    return service.detail(iid)


@router.post("/integrations/{iid}/mapping/preview")
@errors
def preview(iid: str):
    return service.preview_mapping(iid)


@router.post("/integrations/{iid}/mapping/approve")
@errors
def approve(iid: str):
    service.approve_mapping(iid)
    return service.detail(iid)


class NotificationsBody(BaseModel):
    members: list[str] = []
    emails: list[str] = []
    notify_on_created: bool = False
    notify_on_success: bool = True
    notify_on_failure: bool = True


@router.put("/integrations/{iid}/notifications")
@errors
def put_notifications(iid: str, b: NotificationsBody):
    service.save_notifications(iid, b.members, b.emails, b.notify_on_created, b.notify_on_success, b.notify_on_failure)
    return service.detail(iid)


class YesNo(BaseModel):
    enabled: bool


@router.put("/integrations/{iid}/documents")
@errors
def put_documents(iid: str, b: YesNo):
    service.save_documents(iid, b.enabled)
    return service.detail(iid)


@router.get("/integrations/{iid}/documents/options")
@errors
def document_options(iid: str, role: str = "*"):
    it = store.get(iid)
    if not it:
        raise LookupError("Integration not found")
    return service.document_options(it, role)


class RoleDocs(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    documents: list[str] | None = None


@router.put("/integrations/{iid}/documents/roles")
@errors
def put_role_documents(iid: str, b: RoleDocs):
    service.save_role_documents(iid, b.role, b.documents)
    return service.detail(iid)


@router.delete("/integrations/{iid}/documents/roles")
@errors
def delete_role_documents(iid: str, role: str):
    service.remove_role_documents(iid, role)
    return service.detail(iid)


class AccessBody(BaseModel):
    rules: list[dict] = Field(default_factory=list, max_length=50)


@router.put("/integrations/{iid}/access")
@errors
def put_access(iid: str, b: AccessBody):
    service.save_access_rules(iid, b.rules)
    return service.detail(iid)


class GrantBody(BaseModel):
    app_id: str
    action: str
    resource: str = ""
    mode: str = "direct"


@router.post("/integrations/{iid}/candidates/{item_id}/access")
@errors
def grant_access(iid: str, item_id: str, b: GrantBody):
    return service.grant_access(iid, item_id, b.app_id, b.action, b.resource, b.mode)


class ExpressionBody(BaseModel):
    expression: str = Field(max_length=500)
    transform: str = "expression"
    email_domain: str = ""


@router.post("/integrations/{iid}/mapping/test-expression")
@errors
def test_expression(iid: str, b: ExpressionBody):
    return service.test_expression(iid, b.expression, b.transform, b.email_domain)


@router.put("/integrations/{iid}/offer")
@errors
async def put_offer(iid: str, b: YesNo):
    await service.save_offer_choice(iid, b.enabled)
    return service.detail(iid)


@router.post("/integrations/{iid}/offer/generate")
@errors
async def offer_generate(iid: str):
    await service.generate_offer(iid)
    return service.detail(iid)


class TemplateBody(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


@router.put("/integrations/{iid}/offer/template")
@errors
def offer_template(iid: str, b: TemplateBody):
    service.save_offer_template(iid, b.content)
    return service.detail(iid)


class Decision(BaseModel):
    approve: bool
    reason: str | None = None


@router.post("/integrations/{iid}/offer/decision")
@errors
def offer_decision(iid: str, b: Decision):
    service.decide_offer(iid, b.approve)
    return service.detail(iid)


class ScheduleBody(BaseModel):
    kind: str
    interval_minutes: int | None = None
    time: str | None = None
    timezone: str | None = None


@router.put("/integrations/{iid}/schedule")
@errors
def put_schedule(iid: str, b: ScheduleBody):
    service.save_schedule(iid, b.kind, b.interval_minutes, b.time, b.timezone)
    return service.detail(iid)


class LogSettings(BaseModel):
    fields: list[str] = []
    sections: list[str] = []


@router.put("/integrations/{iid}/log-settings")
@errors
def put_log_settings(iid: str, b: LogSettings):
    service.save_log_settings(iid, b.fields, b.sections)
    return service.detail(iid)


@router.post("/integrations/{iid}/activate")
@errors
def post_activate(iid: str):
    service.activate(iid)
    return service.detail(iid)


@router.post("/integrations/{iid}/pause")
@errors
def post_pause(iid: str):
    service.pause(iid)
    return service.detail(iid)


@router.post("/integrations/{iid}/run")
@errors
async def post_run(iid: str):
    it = store.get(iid)
    if not it:
        raise LookupError("Integration not found")
    if it["status"] != "active":
        raise SetupError("Activate this integration before running it.")
    return await engine.run_integration(iid, "manual", "HR")


@router.get("/integrations/{iid}/candidates")
@errors
def candidates(iid: str):
    if not store.get(iid):
        raise LookupError("Integration not found")
    return sorted(store.items(iid), key=lambda i: i.get("name", ""))


@router.post("/integrations/{iid}/candidates/{item_id}/decision")
@errors
def candidate_decision(iid: str, item_id: str, b: Decision):
    return service.decide_candidate(iid, item_id, b.approve, b.reason)


@router.post("/integrations/{iid}/candidates/{item_id}/check-documents")
@errors
def check_documents(iid: str, item_id: str):
    """Re-read the AI document verdict now (the pipeline also does this the moment a document is checked)."""
    item = store.item(item_id)
    if not item or item["integration_id"] != iid:
        raise LookupError("Candidate not found in this integration")
    if not item.get("joiner_id"):
        raise SetupError("Documents haven't been requested for this candidate yet.")
    return {"result": engine.on_documents_changed(item["joiner_id"]), "item": store.item(item_id)}


@router.get("/integrations/{iid}/messages")
@errors
def sent_messages(iid: str):
    return delivery.messages(iid)


# ---------- approvals ----------

@router.get("/approvals")
def approvals():
    out = []
    for it in store.all_integrations():
        for i in store.items(it["id"]):
            if i["stage"] == "awaiting_hr_approval":
                out.append({"kind": "candidate_documents", "integration_id": it["id"], "integration_name": it["name"],
                            "item_id": i["id"], "candidate_name": i.get("name"), "joiner_id": i.get("joiner_id"),
                            "document_results": i["document_results"], "waiting_since": i.get("updated")})
    out.sort(key=lambda a: a["waiting_since"] or 0)
    for it in store.all_integrations():
        if it["offer_template_status"] == "pending_approval":
            out.append({"kind": "offer_format", "integration_id": it["id"], "integration_name": it["name"],
                        "offer_template": it["offer_template"], "waiting_since": it.get("created")})
    return out


# ---------- logs ----------

def _run(r: dict, it: dict | None) -> dict:
    run = engine.run_row(r)
    c = run["counts"]
    fields, sections = exclusions_of(it or {})
    hide = lambda sec, v: None if sec in sections else v  # noqa: E731
    return {"id": run["id"], "integration_id": run["integration_id"], "integration_name": (it or {}).get("name", run.get("integration_name")),
            "trigger": run["trigger"], "status": run["status"], "started": run["started"], "finished": run.get("finished"),
            "fetched_count": c.get("fetched", 0), "enrolled_count": c.get("enrolled", 0), "advanced_count": c.get("advanced", 0),
            "waiting_count": c.get("waiting", 0), "failed_count": c.get("failed", 0),
            "employees_created_count": c.get("employee_created", 0), "employees_updated_count": c.get("employee_updated", 0),
            "documents_requested_count": hide("documents", c.get("documents_requested", 0)),
            "documents_verified_count": hide("documents", c.get("documents_verified", 0)),
            "approvals_pending_count": hide("approvals", c.get("approvals_pending", 0)),
            "approvals_used_count": hide("approvals", c.get("approval_used", 0)),
            "offers_sent_count": hide("offers", c.get("offer_sent", 0)),
            "access_granted_count": hide("access", c.get("access_granted", 0)), "access_requested_count": hide("access", c.get("access_requested", 0)),
            "excluded_fields": sorted(fields), "excluded_sections": sorted(sections),
            "failures": [d for d in run["details"] if d.get("outcome") == "failed"]}


@router.get("/logs/integration")
def integration_log(integration_id: str = "", page: int = 1, page_size: int = 15):
    page_size = min(max(page_size, 1), 100)
    rows, total = store.runs(integration_id, page, page_size)
    its = {it["id"]: it for it in store.all_integrations()}
    return {"items": [_run(r, its.get(r["integration_id"])) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/logs/integration/{run_id}/records")
def run_records(run_id: str):
    run = store.run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    it = store.get(run["integration_id"]) or {}
    fields, sections = exclusions_of(it)
    return [mask_record(r, fields, sections) for r in store.log_records(run_id)]


@router.get("/logs/audit")
def audit_log(q_: str = Query("", alias="q"), page: int = 1, page_size: int = 25, action: str = "", entity_type: str = ""):
    return audit.list_audit(page, min(max(page_size, 1), 100), action, entity_type, q_)

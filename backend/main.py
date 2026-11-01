import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import audit  # noqa: E402
import chat  # noqa: E402
import logging_setup as ls  # noqa: E402
import checklist  # noqa: E402
import gmail  # noqa: E402
import ingest  # noqa: E402
import pipeline  # noqa: E402
import records  # noqa: E402
from graph import q, q1, event  # noqa: E402
from identity import perms, service as identity  # noqa: E402
from shared import bootstrap, db, jobs  # noqa: E402


ls.setup()
_log = ls.get("api")


async def _scheduler(interval: int = 30):
    """Runs integrations whose schedule is due."""
    from integrations.engine import run_due
    while True:
        try:
            await run_due()
        except Exception as e:
            ls.log_event(_log, "scheduler.error", error=type(e).__name__)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app):
    state = await asyncio.to_thread(bootstrap.run)  # migrations, users, audit import; exits with a clear message on bad config
    if state["graph"]:
        await ingest.seed()  # default rule set, templates, sample joiners on first run
        await asyncio.to_thread(bootstrap.ensure_dev_manager_reports)
        if not bootstrap.is_production():
            from policy.service import seed_demo
            from documents.seed import seed_demo_documents
            await asyncio.to_thread(seed_demo)
            await asyncio.to_thread(seed_demo_documents)
    tasks = [asyncio.create_task(jobs.worker()), asyncio.create_task(gmail.poll_forever(60)), asyncio.create_task(_scheduler())]
    ls.log_event(_log, "startup", gmail=gmail.configured(), graph=state["graph"])
    yield
    for t in tasks:
        t.cancel()
    db.close()


app = FastAPI(title="Azentio HR Onboarding Agent", lifespan=lifespan)
# The browser reaches the API through the Next.js rewrite (same origin), so CORS only matters for direct callers.
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def authenticate(request: Request, call_next):
    """The one permission check (identity/perms.py). Sets current_user for the request so audit entries name the actor."""
    path, method = request.url.path, request.method
    if method == "OPTIONS" or not path.startswith("/api/") or perms.is_public(method, path):
        return await call_next(request)
    try:
        user = await run_in_threadpool(identity.user_for_token, request.cookies.get(identity.COOKIE))
    except Exception:
        return JSONResponse({"detail": "Service unavailable: database unreachable"}, status_code=503)
    if not user:
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    if not perms.allowed(method, path, user["role"]):
        return JSONResponse({"detail": "Your role can't do this"}, status_code=403)
    token = identity.current_user.set(user)
    try:
        return await call_next(request)
    finally:
        identity.current_user.reset(token)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """One console line per API call; the request id ties together every line the call produces."""
    token = ls.request_id.set(ls.new_request_id())
    with ls.Timer() as t:
        try:
            response = await call_next(request)
        except Exception:
            ls.log_event(_log, "request", method=request.method, path=ls.safe_path(request.url.path), status=500, ms=t.ms)
            _log.exception("request.error")
            raise
    ls.log_event(_log, "request", method=request.method, path=ls.safe_path(request.url.path), status=response.status_code, ms=t.ms)
    ls.request_id.reset(token)
    return response


# ---------- helpers ----------

def _visible(e: dict) -> bool:
    """Managers see only their own reports; every other role sees everyone it is allowed to reach."""
    u = identity.current_user.get()
    return not u or u["role"] != "manager" or (e.get("manager_email") or "").lower() == u["email"]


def get_emp(eid: str) -> dict:
    e = records.employee(eid)
    if not e or not _visible(e):  # 404, not 403: don't reveal that someone else's report exists
        raise HTTPException(404, "Employee not found")
    return e


def emp_by_token(token: str) -> dict:
    e = records.employee_by(token=token)
    if not e:
        raise HTTPException(404, "Invalid portal link")
    return e


def items_with_state(eid: str) -> list[dict]:
    items = checklist.employee_items(eid)
    by_key = {i["key"]: i for i in items}
    from documents import store as docs
    by_item = docs.for_items([i["id"] for i in items])
    for i in items:
        i["state"] = checklist.view_state(i, by_key)
        i["blocked_by"] = [by_key[d]["name"] for d in i.get("depends", []) if d in by_key and by_key[d]["status"] not in checklist.DONE]
        i["documents"] = [_doc(d) for d in by_item.get(i["id"], [])]
    return items


def _doc(d: dict) -> dict:
    """A document as the API shows it: no server path."""
    return {k: v for k, v in d.items() if k != "path"}


def summarize(items: list[dict]) -> dict:
    c = {s: sum(1 for i in items if i["state"] == s) for s in ("completed", "pending", "blocked", "overdue")}
    c["total"] = len(items)
    c["progress"] = round(100 * c["completed"] / c["total"]) if c["total"] else 0
    c["needs_review"] = sum(1 for i in items if i["status"] in ("Under Review", "Submitted"))
    c["corrections"] = sum(1 for i in items if i["status"] == "Correction Requested")
    return c


def flags(eid: str, items: list[dict]) -> list[dict]:
    """Mismatches, missing docs close to joining, blocked chains, overdue owners."""
    from documents import store as docs
    out = [{"level": "error", "type": "mismatch", "message": f"{r['field'].replace('_', ' ').title()} mismatch on {r['filename']}: "
            f"profile '{r['expected']}' vs document '{r['found']}'"}
           for r in docs.mismatches(eid)]
    for i in items:
        if i["state"] == "overdue":
            out.append({"level": "warning", "type": "overdue", "message": f"'{i['name']}' overdue since {i['due']} — owner {i['owner_name'] or i['category']}"})
        if i["status"] == "Correction Requested":
            out.append({"level": "warning", "type": "correction", "message": f"'{i['name']}' sent back for correction"})
        if i["status"] in ("Under Review",):
            out.append({"level": "info", "type": "review", "message": f"'{i['name']}' needs a human review"})
    blocked = [i for i in items if i["state"] == "blocked"]
    if blocked:
        out.append({"level": "info", "type": "blocked", "message": f"{len(blocked)} task(s) blocked, e.g. '{blocked[0]['name']}' waits on {', '.join(blocked[0]['blocked_by'])}"})
    return out


# ---------- dashboard ----------

@app.get("/api/employees")
def list_employees(department: str = "", location: str = "", status: str = "", join_from: str = "", join_to: str = "", search: str = ""):
    out = []
    for e in records.employees():
        if not _visible(e):
            continue
        if department and e["department"] != department or location and e["location"] != location:
            continue
        if join_from and e["joining_date"] < join_from or join_to and e["joining_date"] > join_to:
            continue
        if search and search.lower() not in f"{e['name']} {e['role']} {e['email']}".lower():
            continue
        items = items_with_state(e["id"])
        s = summarize(items)
        overall = "ready" if s["progress"] == 100 else "overdue" if s["overdue"] else "blocked" if s["blocked"] and not s["pending"] else "in_progress"
        if status and status not in (overall, *[k for k in ("overdue", "blocked", "pending", "completed") if s[k]]):
            continue
        e.pop("token", None)
        out.append({**e, "summary": s, "overall": overall,
                    "days_to_join": (date.fromisoformat(e["joining_date"]) - date.today()).days,
                    "flags": len([f for f in flags(e["id"], items) if f["level"] != "info"])})
    return out


@app.get("/api/stats")
def stats():
    emps = list_employees()
    return {
        "joiners": len(emps),
        "ready": sum(1 for e in emps if e["overall"] == "ready"),
        "joining_7d": sum(1 for e in emps if 0 <= e["days_to_join"] <= 7),
        "overdue": sum(e["summary"]["overdue"] for e in emps),
        "blocked": sum(e["summary"]["blocked"] for e in emps),
        "needs_review": sum(e["summary"]["needs_review"] for e in emps),
        "avg_progress": round(sum(e["summary"]["progress"] for e in emps) / len(emps)) if emps else 0,
        "departments": sorted({e["department"] for e in emps}),
        "locations": sorted({e["location"] for e in emps}),
        "gmail": gmail.configured(),
    }


@app.get("/api/employees/{eid}")
def employee_detail(eid: str):
    e = get_emp(eid)
    items = items_with_state(eid)
    from documents import store as docs
    unmatched = [_doc(d) for d in docs.unmatched(eid)]
    events = q("MATCH (v:Event {employee_id:$e}) RETURN v ORDER BY v.ts DESC LIMIT 100", e=eid)
    manager = q1("MATCH (:Employee {id:$e})-[:REPORTS_TO]->(m) RETURN m", e=eid)
    accepted = {}
    for i in items:  # structured HR record populated from accepted document extractions
        for d in i["documents"]:
            if d["status"] == "Accepted":
                for k, v in d["extracted"].items():
                    if v and not k.startswith("_") and k not in ("confidence", "notes", "legible", "doc_type"):
                        accepted.setdefault(k, {"value": v, "source": i["name"]})
    return {"employee": e, "items": items, "summary": summarize(items), "flags": flags(eid, items),
            "unmatched_documents": unmatched, "events": [r["v"] for r in events],
            "manager": manager["m"] if manager else None, "hr_record": accepted}


@app.get("/api/employees/{eid}/graph")
def employee_graph(eid: str):
    get_emp(eid)
    nodes, edges = {}, []
    for r in q("""MATCH (e:Employee {id:$e}) OPTIONAL MATCH (e)-[:REPORTS_TO]->(m)
                  RETURN e.id AS id, e.name AS name, m.email AS mid, m.name AS mname""", e=eid):
        nodes[r["id"]] = {"id": r["id"], "label": r["name"], "type": "employee"}
        if r["mid"]:
            nodes[r["mid"]] = {"id": r["mid"], "label": r["mname"], "type": "manager"}
            edges.append({"from": r["id"], "to": r["mid"], "type": "REPORTS_TO"})
    items = items_with_state(eid)
    for i in items:
        nodes[i["id"]] = {"id": i["id"], "label": i["name"], "type": i["kind"], "state": i["state"], "status": i["status"], "category": i["category"]}
        edges.append({"from": eid, "to": i["id"], "type": "HAS_ITEM"})
        for d in i["documents"][:1]:
            nodes[d["id"]] = {"id": d["id"], "label": d["filename"], "type": "file", "status": d["status"]}
            edges.append({"from": i["id"], "to": d["id"], "type": "HAS_DOC"})
    by_key = {i["key"]: i["id"] for i in items}
    for i in items:
        for dk in i.get("depends", []):
            if dk in by_key:
                edges.append({"from": i["id"], "to": by_key[dk], "type": "DEPENDS_ON"})
    from documents import store as docs
    for m in docs.mismatches(eid):
        if m["document_id"] in nodes:
            edges.append({"from": m["document_id"], "to": eid, "type": "MISMATCH", "label": m["field"]})
    return {"nodes": list(nodes.values()), "edges": edges}


class EmployeePatch(BaseModel):
    joining_date: str | None = None
    role: str | None = None
    grade: str | None = None
    department: str | None = None
    location: str | None = None
    email: str | None = None
    manager_email: str | None = None


@app.patch("/api/employees/{eid}")
def patch_employee(eid: str, body: EmployeePatch):
    e = get_emp(eid)
    changes = {k: v for k, v in body.model_dump().items() if v is not None and v != e.get(k)}
    if not changes:
        return employee_detail(eid)
    if "joining_date" in changes and not ingest.norm_date(changes["joining_date"]):
        raise HTTPException(400, "Invalid joining date")
    records.update_employee(eid, **changes)
    desc = ", ".join(f"{k.replace('_', ' ')} {e.get(k)} → {v}" for k, v in changes.items())
    event(eid, "change", f"Profile updated: {desc}")
    audit.record_audit("employee.updated", "employee", eid, f"Changed {', '.join(changes)} for joiner {eid}")
    checklist.regenerate(eid, reason=desc)
    return employee_detail(eid)


class SendBody(BaseModel):
    template_id: str = "standard"


@app.post("/api/employees/{eid}/send-request")
async def send_request(eid: str, body: SendBody):
    get_emp(eid)
    if not gmail.configured():
        raise HTTPException(400, "Gmail is not configured")
    try:
        return await gmail.send_request(eid, body.template_id)
    except Exception as ex:
        raise HTTPException(502, f"Gmail send failed: {ex}")


@app.get("/api/employees/{eid}/preview-email")
def preview_email(eid: str, template_id: str = "standard"):
    e = get_emp(eid)
    docs = [i for i in records.items(eid) if i["kind"] == "document" and i["status"] != "Accepted"]
    doc_list = "<ul>" + "".join(f"<li><b>{d['name']}</b> — by {d['due']}</li>" for d in docs) + "</ul>"
    subject, html = gmail.render(template_id, {**gmail.employee_ctx(e), "doc_list": doc_list})
    return {"to": e["email"], "subject": subject, "html": html}


class ItemStatus(BaseModel):
    status: str
    note: str = ""


@app.post("/api/items/{item_id}/status")
async def set_item_status(item_id: str, body: ItemStatus):
    it = records.item(item_id)
    if not it or not _visible(records.employee(it["employee_id"]) or {}):
        raise HTTPException(404, "Item not found")
    if it["kind"] == "document":  # a document item's status is derived from a recorded decision, never set directly
        raise HTTPException(409, "Document items change only through a review decision: "
                                 "POST /api/documents/{id}/decision (or /override), with a reason")
    records.update_item(item_id, status=body.status)
    event(it["employee_id"], "review", f"HR set '{it['name']}' to {body.status}" + (f": {body.note}" if body.note else ""))
    if body.status == "Correction Requested" and gmail.configured():
        emp = get_emp(it["employee_id"])
        await gmail.send_correction(emp, it, [body.note or "Please re-submit a clearer, valid copy."])
    checklist.recompute(it["employee_id"])
    audit.record_audit("checklist.item_status", "item", item_id, f"Set '{it['name']}' to {body.status}", metadata={"note": body.note} if body.note else None)
    return employee_detail(it["employee_id"])


@app.post("/api/escalate")
async def escalate():
    """Email the owner of every overdue task that is blocking other work (Scenario 5)."""
    sent = []
    for e in records.employees():
        items = items_with_state(e["id"])
        for i in items:
            if i["state"] != "overdue" or i["kind"] == "document" or not i.get("owner_email"):
                continue
            blocked = [x["name"] for x in items if i["name"] in x["blocked_by"]]
            ctx = {**gmail.employee_ctx(e), "item_name": i["name"], "owner_name": i["owner_name"], "due": i["due"],
                   "blocked": ", ".join(blocked) or "none"}
            if gmail.configured():
                subject, html = gmail.render("escalation", ctx)
                await gmail.send(i["owner_email"], subject, html)
            event(e["id"], "escalation", f"Escalated overdue '{i['name']}' to {i['owner_name']} ({i['owner_email']})")
            sent.append({"employee": e["name"], "item": i["name"], "owner": i["owner_email"]})
    return {"escalations": sent}


@app.post("/api/gmail/poll")
async def poll_now():
    if not gmail.configured():
        raise HTTPException(400, "Gmail is not configured")
    return {"processed": await gmail.poll()}


@app.get("/api/events")
def events(limit: int = 50):
    rows = q("""MATCH (v:Event) OPTIONAL MATCH (e:Employee {id:v.employee_id})
                RETURN v, e.name AS name, e.manager_email AS mgr ORDER BY v.ts DESC LIMIT $l""", l=limit)
    return [{**r["v"], "employee_name": r["name"]} for r in rows if _visible({"manager_email": r["mgr"]})]


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str):
    from documents import store as docs
    d = docs.get(doc_id)
    if not d or not _visible(records.employee(d["employee_id"]) or {}):
        raise HTTPException(404)
    return FileResponse(d["path"], media_type=d["mime"], filename=d["filename"], content_disposition_type="inline")


# ---------- ingestion & configuration ----------

@app.post("/api/ingest")
async def ingest_file(kind: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    try:
        if kind == "employees":
            return ingest.import_employees(file.filename, data)
        if kind == "owners":
            return ingest.import_owners(file.filename, data)
        if kind == "requirements":  # becomes a draft rule set: it goes live only after approval
            from identity.service import current_user
            return ingest.import_requirements_draft(file.filename, data, current_user.get())
        if kind == "policies":
            if not file.filename.lower().endswith((".md", ".txt")):
                raise ValueError("Policies must be Markdown or text files")
            await chat.index_policy(file.filename, data.decode("utf-8"))
            return {"kind": "policies", "total": 1, "imported": 1, "errors": []}
    except (ValueError, KeyError, json.JSONDecodeError) as ex:
        raise HTTPException(400, str(ex))
    raise HTTPException(400, f"Unknown kind '{kind}'")


@app.get("/api/templates")
def templates():
    return [r["t"] for r in q("MATCH (t:EmailTemplate) RETURN t ORDER BY t.id")]


class Template(BaseModel):
    id: str
    name: str
    subject: str
    body: str


@app.put("/api/templates/{tid}")
def save_template(tid: str, t: Template):
    audit.record_audit("template.saved", "email_template", tid, f"Saved email template {t.name}")
    q("MERGE (x:EmailTemplate {id:$id}) SET x.name=$n, x.subject=$s, x.body=$b", id=tid, n=t.name, s=t.subject, b=t.body)
    return t


@app.get("/api/rules")
def rules():
    """The active rule-set version. Rules change only through /api/policy (draft -> approve -> publish)."""
    from policy import store
    act = store.ensure_active()
    return {"rules": checklist.load_rules(), "doc_types": checklist.config("doc_types"),
            "departments": checklist.config("departments"), "locations": checklist.config("locations"),
            "roles": checklist.config("roles") or [],
            "owners": [r["o"] for r in q("MATCH (o:Owner) RETURN o ORDER BY o.type")],
            "version": {"id": act["id"], "number": act["number"], "published_at": act["published_at"],
                        "author_email": act["author_email"]} if act else None}


@app.get("/api/policies")
def policies():
    return [r["p"] for r in q("MATCH (p:Policy) RETURN p ORDER BY p.source")]


# ---------- employee portal ----------

@app.get("/api/portal/{token}")
def portal(token: str):
    e = emp_by_token(token)
    items = items_with_state(e["id"])
    pol = {r["s"]: r["t"] for r in q("MATCH (p:Policy) RETURN p.source AS s, p.text AS t")}
    for i in items:
        i["policy_text"] = pol.get(i.get("policy_ref", ""), "") if i["kind"] == "ack" else ""
        i.pop("owner_email", None)
    manager = q1("MATCH (:Employee {id:$e})-[:REPORTS_TO]->(m) RETURN m.name AS n", e=e["id"])
    safe = {k: e[k] for k in ("id", "name", "role", "grade", "department", "location", "joining_date", "email")}
    return {"employee": {**safe, "manager": manager["n"] if manager else ""}, "items": items, "summary": summarize(items)}


@app.post("/api/portal/{token}/upload")
async def portal_upload(token: str, file: UploadFile = File(...), item_key: str = Form("")):
    e = emp_by_token(token)
    data = await file.read()
    return await pipeline.intake(e["id"], file.filename, data, file.content_type, "portal", item_key or None)


@app.post("/api/portal/{token}/ack/{item_id}")
def portal_ack(token: str, item_id: str):
    e = emp_by_token(token)
    it = records.item(item_id)
    if not it or it["employee_id"] != e["id"] or it["kind"] != "ack":
        raise HTTPException(404, "Item not found")
    records.update_item(item_id, status="Acknowledged", acknowledged_at=int(time.time() * 1000))
    event(e["id"], "acknowledgement", f"{e['name']} acknowledged '{it['name']}'")
    checklist.recompute(e["id"])
    return portal(token)


class ChatBody(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/portal/{token}/chat")
async def portal_chat(token: str, body: ChatBody):
    e = emp_by_token(token)
    items = checklist.employee_items(e["id"])
    return StreamingResponse(chat.answer_stream(e, items, body.message, body.history), media_type="text/plain")


@app.get("/api/employees/{eid}/portal-link")
def portal_link(eid: str):
    return {"token": get_emp(eid)["token"]}


# ---------- quick onboard & HR assistant ----------

class OnboardBody(BaseModel):
    name: str
    email: str
    role: str
    location: str
    joining_date: str
    template_id: str = "standard"
    send: bool = True
    grade: str | None = None
    department: str | None = None
    manager_email: str | None = None
    date_of_birth: str | None = None


@app.post("/api/onboard")
async def onboard(body: OnboardBody):
    """One step: create the joiner from a role preset, build the checklist, email the document request."""
    return await create_joiner(body.model_dump(), body.template_id, body.send, "quick onboard")


async def create_joiner(f: dict, template_id: str, send: bool, reason: str, candidate_record: str | None = None) -> dict:
    """Shared by quick onboard and connector 'Start onboarding': validate, create, build checklist, optionally email."""
    preset = next((r for r in checklist.config("roles") or [] if r["role"] == f.get("role")), {})
    department = f.get("department") or preset.get("department", "")
    manager = f.get("manager_email") or (q1("MATCH (o:Owner {type:'manager', department:$d}) RETURN o.email AS e", d=department) or {}).get("e", "")
    row = {"name": (f.get("name") or "").strip(), "email": (f.get("email") or "").strip(), "role": f.get("role", ""),
           "location": f.get("location", ""), "joining_date": f.get("joining_date", ""),
           "grade": f.get("grade") or preset.get("grade", ""), "department": department, "manager_email": manager,
           "date_of_birth": f.get("date_of_birth") or ""}
    managers = {r["m"] for r in q("MATCH (m:Owner) RETURN toLower(m.email) AS m")} | records.emails()
    valid, errors = ingest.validate_employees([row], checklist.config("departments") or [], checklist.config("locations") or {}, managers)
    if errors:
        raise HTTPException(400, {"message": "Please fix the highlighted fields", "errors": errors})
    eid = ingest.upsert_employee(valid[0], reason)
    audit.record_audit("employee.created", "employee", eid, f"Started onboarding for joiner {eid} ({reason})")
    if candidate_record:
        q("MATCH (e:Employee {id:$e}), (r:Record {id:$r}) MERGE (e)-[:FROM_CANDIDATE]->(r)", e=eid, r=candidate_record)
    out = {"id": eid, "sent": False}
    if send:
        if not gmail.configured():
            out["email_error"] = "Gmail is not configured"
        else:
            try:
                out.update(sent=True, **await gmail.send_request(eid, template_id))
            except Exception as ex:
                out["email_error"] = f"Gmail send failed: {ex}"
    return out


class HrChatBody(BaseModel):
    message: str
    history: list[dict] = []
    employee_id: str | None = None


@app.post("/api/hr/chat")
async def hr_chat(body: HrChatBody):
    return StreamingResponse(chat.hr_answer_stream(body.message, body.history, body.employee_id), media_type="text/plain")


@app.get("/health")
def health():
    """Process is up. Says nothing about dependencies; see /ready."""
    return {"ok": True}


@app.get("/ready")
def ready():
    """Can we actually serve? Postgres, graph and the job worker are required; the LLM is informational."""
    from llm import BASE_URL
    checks = {"postgres": db.ping(), "graph": bootstrap.graph_ok()}
    try:
        checks["worker"] = checks["postgres"] and jobs.worker_alive()
    except Exception:
        checks["worker"] = False
    body = {k: "ok" if v else "down" for k, v in checks.items()}
    from llm import unavailable_reason
    why = unavailable_reason() if BASE_URL else "No AI provider configured"
    body["llm"] = "degraded" if why else "configured"  # informational: documents fall back to human review
    if why:
        body["llm_reason"] = why
    return JSONResponse(body, status_code=200 if all(checks.values()) else 503)


from identity.routes import router as identity_router  # noqa: E402
import work_queue  # noqa: E402
from policy.routes import router as policy_router  # noqa: E402
import policy.service  # noqa: E402,F401  (registers policy.publish / notify.item_added job handlers)
import documents.service  # noqa: E402,F401  (registers doc.extract / doc.after_decision / notify.correction handlers)
from documents.routes import router as documents_router  # noqa: E402

app.include_router(identity_router)
app.include_router(work_queue.router)
app.include_router(policy_router)
app.include_router(documents_router)

import connector_routes  # noqa: E402

app.include_router(connector_routes.router)

import integration_routes  # noqa: E402

app.include_router(integration_routes.router)

import quality_routes  # noqa: E402

app.include_router(quality_routes.router)

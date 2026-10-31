import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import chat  # noqa: E402
import checklist  # noqa: E402
import gmail  # noqa: E402
import ingest  # noqa: E402
import pipeline  # noqa: E402
from graph import q, q1, event  # noqa: E402


@asynccontextmanager
async def lifespan(app):
    await ingest.seed()
    task = asyncio.create_task(gmail.poll_forever(60))
    yield
    task.cancel()


app = FastAPI(title="Azentio HR Onboarding Agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- helpers ----------

def get_emp(eid: str) -> dict:
    row = q1("MATCH (e:Employee {id:$e}) RETURN e", e=eid)
    if not row:
        raise HTTPException(404, "Employee not found")
    return row["e"]


def emp_by_token(token: str) -> dict:
    row = q1("MATCH (e:Employee {token:$t}) RETURN e", t=token)
    if not row:
        raise HTTPException(404, "Invalid portal link")
    return row["e"]


def items_with_state(eid: str) -> list[dict]:
    items = checklist.employee_items(eid)
    by_key = {i["key"]: i for i in items}
    docs = q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item)-[:HAS_DOC]->(d:Document)
                RETURN i.id AS item, d ORDER BY d.ts DESC""", e=eid)
    for i in items:
        i["state"] = checklist.view_state(i, by_key)
        i["blocked_by"] = [by_key[d]["name"] for d in i.get("depends", []) if d in by_key and by_key[d]["status"] not in checklist.DONE]
        i["documents"] = [_doc(r["d"]) for r in docs if r["item"] == i["id"]]
    return items


def _doc(d: dict) -> dict:
    return {**{k: v for k, v in d.items() if k != "path"}, "extracted": json.loads(d.get("extracted") or "{}"),
            "issues": json.loads(d.get("issues") or "[]")}


def summarize(items: list[dict]) -> dict:
    c = {s: sum(1 for i in items if i["state"] == s) for s in ("completed", "pending", "blocked", "overdue")}
    c["total"] = len(items)
    c["progress"] = round(100 * c["completed"] / c["total"]) if c["total"] else 0
    c["needs_review"] = sum(1 for i in items if i["status"] in ("Under Review", "Submitted"))
    c["corrections"] = sum(1 for i in items if i["status"] == "Correction Requested")
    return c


def flags(eid: str, items: list[dict]) -> list[dict]:
    """Graph evaluation: mismatches, missing docs close to joining, blocked chains, overdue owners."""
    out = [{"level": "error", "type": "mismatch", "message": f"{r['field'].replace('_', ' ').title()} mismatch on {r['file']}: "
            f"profile '{r['expected']}' vs document '{r['found']}'"}
           for r in q("""MATCH (e:Employee {id:$e})<-[m:MISMATCH]-(d:Document)
                         RETURN m.field AS field, m.expected AS expected, m.found AS found, d.filename AS file""", e=eid)]
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
    for row in q("MATCH (e:Employee) RETURN e ORDER BY e.joining_date"):
        e = row["e"]
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
    unmatched = [_doc(r["d"]) for r in q("""MATCH (:Employee {id:$e})-[:SUBMITTED]->(d:Document)
                                           WHERE NOT ()-[:HAS_DOC]->(d) RETURN d ORDER BY d.ts DESC""", e=eid)]
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
    for r in q("MATCH (:Employee {id:$e})<-[m:MISMATCH]-(d:Document) RETURN d.id AS d, m.field AS f", e=eid):
        if r["d"] in nodes:
            edges.append({"from": r["d"], "to": eid, "type": "MISMATCH", "label": r["f"]})
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
    q("MATCH (e:Employee {id:$id}) SET e += $p", id=eid, p=changes)
    desc = ", ".join(f"{k.replace('_', ' ')} {e.get(k)} → {v}" for k, v in changes.items())
    event(eid, "change", f"Profile updated: {desc}")
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
    docs = q("""MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item {kind:'document'}) WHERE i.status <> 'Accepted'
                RETURN i.name AS n, i.due AS d ORDER BY i.due""", e=eid)
    doc_list = "<ul>" + "".join(f"<li><b>{d['n']}</b> — by {d['d']}</li>" for d in docs) + "</ul>"
    subject, html = gmail.render(template_id, {**gmail.employee_ctx(e), "doc_list": doc_list})
    return {"to": e["email"], "subject": subject, "html": html}


class ItemStatus(BaseModel):
    status: str
    note: str = ""


@app.post("/api/items/{item_id}/status")
async def set_item_status(item_id: str, body: ItemStatus):
    row = q1("MATCH (i:Item {id:$id}) RETURN i", id=item_id)
    if not row:
        raise HTTPException(404, "Item not found")
    it = row["i"]
    q("MATCH (i:Item {id:$id}) SET i.status=$s", id=item_id, s=body.status)
    if it["kind"] == "document":
        q("MATCH (i:Item {id:$id})-[:HAS_DOC]->(d:Document) WITH d ORDER BY d.ts DESC LIMIT 1 SET d.status=$s",
          id=item_id, s=body.status)
    event(it["employee_id"], "review", f"HR set '{it['name']}' to {body.status}" + (f": {body.note}" if body.note else ""))
    if body.status == "Correction Requested" and gmail.configured():
        emp = get_emp(it["employee_id"])
        await gmail.send_correction(emp, it, [body.note or "Please re-submit a clearer, valid copy."])
    checklist.recompute(it["employee_id"])
    return employee_detail(it["employee_id"])


@app.post("/api/escalate")
async def escalate():
    """Email the owner of every overdue task that is blocking other work (Scenario 5)."""
    sent = []
    for row in q("MATCH (e:Employee) RETURN e"):
        e = row["e"]
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
                RETURN v, e.name AS name ORDER BY v.ts DESC LIMIT $l""", l=limit)
    return [{**r["v"], "employee_name": r["name"]} for r in rows]


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str):
    row = q1("MATCH (d:Document {id:$id}) RETURN d.path AS p, d.mime AS m, d.filename AS f", id=doc_id)
    if not row:
        raise HTTPException(404)
    return FileResponse(row["p"], media_type=row["m"], filename=row["f"], content_disposition_type="inline")


# ---------- ingestion & configuration ----------

@app.post("/api/ingest")
async def ingest_file(kind: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    try:
        if kind == "employees":
            return ingest.import_employees(file.filename, data)
        if kind == "owners":
            return ingest.import_owners(file.filename, data)
        if kind == "requirements":
            return ingest.import_requirements(file.filename, data)
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
    q("MERGE (x:EmailTemplate {id:$id}) SET x.name=$n, x.subject=$s, x.body=$b", id=tid, n=t.name, s=t.subject, b=t.body)
    return t


@app.get("/api/rules")
def rules():
    return {"rules": checklist.load_rules(), "doc_types": checklist.config("doc_types"),
            "departments": checklist.config("departments"), "locations": checklist.config("locations"),
            "roles": checklist.config("roles") or [],
            "owners": [r["o"] for r in q("MATCH (o:Owner) RETURN o ORDER BY o.type")]}


class Rule(BaseModel):
    id: str
    item: str
    name: str = ""
    kind: str
    category: str
    match: dict = {}
    offset_days: int = 0
    depends_on: list[str] = []
    description: str
    policy_ref: str = ""
    auto: str = ""


@app.put("/api/rules/{rid}")
def save_rule(rid: str, r: Rule):
    existing = q1("MATCH (x:Rule {id:$id}) RETURN x.ord AS o", id=rid)
    nxt = q1("MATCH (x:Rule) RETURN coalesce(max(x.ord), 0) + 1 AS o")["o"]
    ingest.save_rule(r.model_dump(), ord_=existing["o"] if existing else nxt)
    for e in q("MATCH (e:Employee) RETURN e.id AS id"):
        checklist.regenerate(e["id"], f"rule {rid} updated")
    return rules()


@app.delete("/api/rules/{rid}")
def delete_rule(rid: str):
    q("MATCH (r:Rule {id:$id}) DELETE r", id=rid)
    for e in q("MATCH (e:Employee) RETURN e.id AS id"):
        checklist.regenerate(e["id"], f"rule {rid} removed")
    return rules()


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
    notify = gmail.send_correction if gmail.configured() else None
    return await pipeline.intake(e["id"], file.filename, data, file.content_type, "portal", item_key or None, notify=notify)


@app.post("/api/portal/{token}/ack/{item_id}")
def portal_ack(token: str, item_id: str):
    e = emp_by_token(token)
    row = q1("MATCH (:Employee {id:$e})-[:HAS_ITEM]->(i:Item {id:$i, kind:'ack'}) RETURN i", e=e["id"], i=item_id)
    if not row:
        raise HTTPException(404, "Item not found")
    q("MATCH (i:Item {id:$i}) SET i.status='Acknowledged', i.acknowledged_at=timestamp()", i=item_id)
    event(e["id"], "acknowledgement", f"{e['name']} acknowledged '{row['i']['name']}'")
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
    managers = {r["m"] for r in q("MATCH (m) WHERE m:Owner OR m:Employee RETURN toLower(m.email) AS m")}
    valid, errors = ingest.validate_employees([row], checklist.config("departments") or [], checklist.config("locations") or {}, managers)
    if errors:
        raise HTTPException(400, {"message": "Please fix the highlighted fields", "errors": errors})
    eid = ingest.upsert_employee(valid[0], reason)
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


import connector_routes  # noqa: E402

app.include_router(connector_routes.router)

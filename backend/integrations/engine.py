"""Runs an integration. Each run moves every candidate as far as it can go without a person.

pending -> documents_requested -> (AI document checks) -> awaiting_hr_approval -> (HR) -> approved
approved -> offer_sent -> completed, where completed means the employee exists in the target.

The document step calls the platform's real document API: it creates the joiner and their
role-specific checklist, emails the request through Gmail, and replies are checked by the vision LLM
plus deterministic rules (pipeline.intake). `on_documents_changed` is the callback that moves the
candidate as soon as a verdict lands, like a document-service webhook.

Runs are safe to repeat: the stage is saved per candidate, each send is recorded per attempt, and the
target upserts on work email. A failure leaves the candidate at its last good stage for next time.
"""
import json
import re
import time
from collections import Counter

from fastapi import HTTPException

import audit
import records
import checklist
import gmail
import logging_setup as ls
from connectors import registry
from connectors.base import RecordRejected
from graph import q, q1, new_id, event
from . import delivery, store
from .mapping import apply_mapping, check_required
from .run_log import display_name, exclusions_of, snapshot_mapping, visible_actions

log = ls.get("engine")
STALE_RUN_AFTER = 30 * 60
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_WAITING_ON = {"documents_requested": ("documents", "Waiting for the candidate's documents and AI checks"),
               "awaiting_hr_approval": ("approvals", "Waiting for HR approval")}


class CandidateNotReady(Exception):
    """The candidate can't move yet, for a reason HR can fix."""


class AlreadyRunning(Exception):
    pass


def unknown_placeholders(template: str) -> list[str]:
    return sorted(set(_PLACEHOLDER.findall(template)) - set(store.OFFER_PLACEHOLDERS))


def render_offer(template: str, values: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(values.get(m[1], "")), template)


def _candidate(it: dict, item: dict) -> dict:
    found = registry.get(it["source_id"])
    rec = found[1].get_record(it["source_id"], item["record_id"]) if found else None
    if not rec:
        raise RecordRejected("The candidate is no longer in the source.")
    return rec["values"]


# ---------- document step: the real document API ----------

def document_results(eid: str) -> list[dict]:
    """The joiner's document items as verified / rejected / missing, with reasons from the AI checks."""
    out = []
    for i in checklist.employee_items(eid):
        if i["kind"] != "document":
            continue
        status = {"Accepted": "verified", "Correction Requested": "rejected"}.get(i["status"], "missing")
        reason = None
        if status == "rejected":
            from documents import store as docs
            doc = docs.latest_for_item(i["id"])
            issues = (doc or {}).get("issues") or []
            reason = "; ".join(issues) or "Sent back for correction"
        elif i["status"] in ("Submitted", "Under Review"):
            reason = "Received, being checked"
        out.append({"name": i["name"], "status": status, "reason": reason})
    return out


async def _ensure_joiner(it: dict, item: dict, cand: dict) -> str:
    if item.get("joiner_id") and records.employee(item["joiner_id"]):
        return item["joiner_id"]
    from main import create_joiner  # shared with quick onboard / connectors
    f = {"name": f"{cand.get('first_name', '')} {cand.get('last_name', '')}".strip(), "email": cand.get("email", ""),
         "role": cand.get("designation", ""), "department": cand.get("department_name", ""), "grade": cand.get("grade", ""),
         "location": cand.get("location", ""), "joining_date": cand.get("joining_date", ""), "date_of_birth": cand.get("date_of_birth", "")}
    try:
        out = await create_joiner(f, "standard", False, f"started by integration {it['name']}", candidate_record=item["record_id"])
    except HTTPException as ex:
        errs = ex.detail.get("errors", []) if isinstance(ex.detail, dict) else []
        raise CandidateNotReady("Can't request documents yet: " + "; ".join(f"{e['field']} — {e['message']}" for e in errs or [{"field": "candidate", "message": str(ex.detail)}]))
    eid = out["id"]
    records.update_employee(eid, integration_id=it["id"])
    scope = _documents_for(it, f["role"])
    if scope:  # ask this role only for the documents HR chose for it
        records.update_employee(eid, doc_scope=list(scope))
        checklist.regenerate(eid, f"document set for {f['role'] or 'this role'} chosen in integration {it['name']}")
    store.set_item(item["id"], joiner_id=eid)
    return eid


def _documents_for(it: dict, role: str) -> list[str] | None:
    from .service import documents_for
    return documents_for(it, role)


async def _request_documents(it: dict, item: dict) -> str:
    cand = _candidate(it, item)
    eid = await _ensure_joiner(it, item, cand)
    docs = [i for i in checklist.employee_items(eid) if i["kind"] == "document" and i["status"] != "Accepted"]
    if not docs:
        raise CandidateNotReady("No document requirement applies to this candidate. Add one under Requirement rules.")
    attempt = int(item.get("document_attempts") or 0) + 1
    name = f"{cand.get('first_name', '')} {cand.get('last_name', '')}".strip()
    if delivery.is_simulated():
        content = f"Dear {cand.get('first_name')}, {store.COMPANY} needs these documents: " + ", ".join(d["name"] for d in docs)
        delivery.record(it["id"], item["id"], "document_request", cand.get("email", ""), "Documents needed", content,
                        attempt, "simulated", f"sim-{new_id('x')[2:14]}", name)
        via = "recorded (Gmail not configured — simulated, not sent)"
    else:
        sent = await gmail.send_request(eid, "standard")
        preview = {"t": (records.employee(eid) or {}).get("thread_id", "")}
        delivery.record(it["id"], item["id"], "document_request", cand.get("email", ""), sent["subject"],
                        "Documents requested: " + ", ".join(d["name"] for d in docs), attempt, "gmail", preview["t"] if preview else "", name)
        via = "emailed through Gmail"
    store.set_item(item["id"], stage="documents_requested", document_attempts=attempt,
                   document_results=document_results(eid), last_error="")
    return f"Requested {len(docs)} document(s), {via}"


def _verdict(eid: str) -> tuple[str, list[dict]]:
    """verified when every document item is accepted, rejected when any was sent back, else waiting."""
    results = document_results(eid)
    if results and all(r["status"] == "verified" for r in results):
        return "verified", results
    if any(r["status"] == "rejected" for r in results):
        return "rejected", results
    return "waiting", results


def on_documents_changed(eid: str) -> str:
    """Callback from the document pipeline (and HR document decisions): move the candidate on at once."""
    item = store.item_for_joiner(eid)
    if not item or item["stage"] != "documents_requested":
        return "ignored"
    verdict, results = _verdict(eid)
    store.set_item(item["id"], document_results=results)
    if verdict == "waiting":
        return "waiting"
    it = store.get(item["integration_id"])
    stage = "awaiting_hr_approval" if verdict == "verified" else "documents_rejected"
    store.set_item(item["id"], stage=stage, last_error="")
    audit.record_audit(f"documents.{verdict}", "integration_candidate", item["id"],
                       f"AI document checks reported documents {verdict}: {it['name'] if it else ''}", actor="System")
    ls.log_event(log, "documents.verdict", item=item["id"], verdict=verdict)
    return "applied"


# ---------- later steps ----------

async def _send_offer(it: dict, item: dict) -> str:
    cand = _candidate(it, item)
    values = {f"candidate.{k}": v for k, v in cand.items() if not isinstance(v, (list, dict))}
    values.update({"candidate.designation": cand.get("designation", ""), "candidate.department": cand.get("department_name", ""),
                   "company.name": store.COMPANY})
    content = render_offer(it["offer_template"], values)
    html = "".join(f"<p>{line}</p>" if line.strip() else "" for line in content.split("\n"))
    name = f"{cand.get('first_name', '')} {cand.get('last_name', '')}".strip()
    await delivery.send_email(it["id"], item["id"], "offer_letter", cand.get("email", ""), f"Your offer from {store.COMPANY}", html, 1, name)
    store.set_item(item["id"], stage="offer_sent")
    return "Sent offer letter" + (" (simulated, not sent)" if delivery.is_simulated() else " through Gmail")


def _create_employee(it: dict, item: dict) -> tuple[str, str]:
    cand = _candidate(it, item)
    tgt = registry.get(it["target_id"])
    if not tgt:
        raise RecordRejected("The target connector no longer exists.")
    _, target = tgt
    from .service import target_email_lookup
    generated = json.loads(item.get("generated_values") or "{}")
    values = apply_mapping(it["mapping"], cand, email_taken=target_email_lookup(it), generated=generated)
    minted = {r["target_field"]: values[r["target_field"]] for r in it["mapping"]
              if r.get("transform") == "unique_email" and r["target_field"] in values}
    if minted != generated:
        store.set_item(item["id"], generated_values=json.dumps(minted))
    check_required(it["mapping"], target.describe_schema(it["target_id"]), values)
    rid, outcome = target.upsert_record(it["target_id"], values, it["duplicate_policy"])
    store.set_item(item["id"], stage="completed", employee_record_id=rid)
    if item.get("joiner_id"):
        from connectors import store as connectors
        connectors.link(item["joiner_id"], rid, "synced_to", replace=True)
    return {"created": "Created employee", "updated": "Updated existing employee",
            "skipped": "Employee already exists, left unchanged"}[outcome], outcome


async def _advance(it: dict, item_id: str) -> list[dict]:
    """Take one candidate through every step that needs no person. Returns what happened."""
    actions: list[dict] = []

    def did(section: str, kind: str, message: str):
        actions.append({"section": section, "kind": kind, "message": message})

    for _ in range(8):  # bounded: a candidate passes at most every stage once per run
        item = store.item(item_id)
        match item["stage"]:
            case "pending" | "documents_rejected":
                if item["stage"] == "documents_rejected" and actions:
                    return actions  # rejected in this run: ask again on the next run
                if it["doc_verification_enabled"]:
                    did("documents", "documents_requested", await _request_documents(it, item))
                    return actions  # wait for the candidate's reply and the AI checks
                store.set_item(item_id, stage="approved")
            case "documents_requested":
                verdict, results = _verdict(item["joiner_id"]) if item.get("joiner_id") else ("waiting", [])
                store.set_item(item_id, document_results=results)
                if verdict == "verified":
                    store.set_item(item_id, stage="awaiting_hr_approval")
                    did("documents", "documents_verified", f"All {len(results)} documents verified by AI checks")
                elif verdict == "rejected":
                    store.set_item(item_id, stage="documents_rejected")
                    bad = [r for r in results if r["status"] == "rejected"]
                    did("documents", "documents_rejected", "Rejected: " + "; ".join(f"{r['name']} ({r['reason']})" for r in bad))
                else:
                    return actions
            case "approved":
                if item.get("decided_at"):
                    did("approvals", "approval_used", "HR approval applied")
                if it["offer_enabled"]:
                    did("offers", "offer_sent", await _send_offer(it, item))
                else:
                    store.set_item(item_id, stage="offer_sent")
            case "offer_sent":
                message, outcome = _create_employee(it, item)
                did("employee", f"employee_{outcome}", message)
                for r in _provision_access(it, store.item(item_id)):
                    did("access", {"requested": "access_requested", "failed": "access_failed"}.get(r["status"], "access_granted"),
                        f"{r['app']}: {r['message']}" + (" (already done)" if r.get("repeat") else ""))
            case _:
                return actions
    return actions


# ---------- app access (Slack, Jira, ServiceNow, Google Workspace) ----------

def _access_email(it: dict, item: dict, cand: dict) -> str:
    """The joiner's company email once the employee step generated one, else their own address."""
    generated = json.loads(item.get("generated_values") or "{}")
    return generated.get("work_email") or next(iter(generated.values()), "") or cand.get("email", "")


def perform_access(it: dict, item: dict, rule: dict, actor: str) -> dict:
    """Run one access action for one candidate and remember it on the candidate. Repeat-safe."""
    found = registry.get(rule["app_id"])
    if not found:
        raise RecordRejected(f"The {rule.get('app', 'app')} connector no longer exists.")
    cand = _candidate(it, item)
    email = _access_email(it, item, cand)
    name = f"{cand.get('first_name', '')} {cand.get('last_name', '')}".strip()
    res = found[1].perform(rule["app_id"], rule["action"], name, email, rule.get("resource", ""), rule.get("mode", "direct"),
                           requested_by=actor)
    res["app"] = rule.get("app") or found[0]["name"]
    fresh = store.item(item["id"])  # earlier grants in this same pass are already saved
    results = [r for r in json.loads(fresh.get("access_results") or "[]") if r.get("id") != res["id"]]
    results.append({k: res.get(k) for k in ("id", "app", "action", "resource", "mode", "status", "reference", "message", "email")})
    store.set_item(item["id"], access_results=json.dumps(results))
    if item.get("joiner_id"):
        event(item["joiner_id"], "access", f"{res['app']}: {res['message']}")
    ls.log_event(log, "access.performed", item=item["id"], app=res["app"], action=rule["action"], status=res["status"])
    return res


def _provision_access(it: dict, item: dict) -> list[dict]:
    from .service import access_rule_applies
    cand = _candidate(it, item)
    done = []
    for rule in it.get("access_rules") or []:
        if access_rule_applies(rule, cand):
            try:
                done.append(perform_access(it, item, rule, f"Integration {it['name']}"))
            except RecordRejected as exc:  # one app failing mustn't undo the employee; HR can grant it by hand
                done.append({"app": rule.get("app", ""), "status": "failed", "message": str(exc)})
    return done


# ---------- runs ----------

async def notify(it: dict, ev: str, message: str) -> None:
    enabled = {"run_created": it["notify_on_created"], "run_succeeded": it["notify_on_success"], "run_failed": it["notify_on_failure"]}[ev]
    if not enabled:
        return
    recipients = sorted(set(it["notify_members"]) | set(it["notify_emails"]))
    ls.log_event(log, "notify", integration=it["id"], notify_event=ev, recipients=len(recipients))
    event(None, "integration", message)
    for to in recipients:
        try:
            await delivery.send_email(it["id"], None, "notification", to, f"[Azentio] {message[:80]}", f"<p>{message}</p>")
        except Exception:  # a missed notification must never fail the run it describes
            ls.log_event(log, "notify.failed", integration=it["id"])


def _enroll(it: dict) -> int:
    """Add candidates the source filter now matches. Existing progress is untouched."""
    found = registry.get(it["source_id"])
    if not found:
        return 0
    statuses = set(it["source_filter"].get("statuses") or [])
    depts = {d.lower() for d in it["source_filter"].get("departments") or []}
    existing = {r["r"] for r in q("MATCH (c:IntegrationCandidate {integration_id:$i}) RETURN c.record_id AS r", i=it["id"])}
    added = 0
    for rec in found[1].list_records(it["source_id"], 1, 100000)["items"]:
        v = rec["values"]
        if rec["id"] in existing or v.get("status") not in statuses or (depts and str(v.get("department_name", "")).lower() not in depts):
            continue
        q("CREATE (c:IntegrationCandidate) SET c = $p", p={
            "id": new_id("ic"), "integration_id": it["id"], "record_id": rec["id"], "candidate_ref": v.get("candidate_id", ""),
            "name": f"{v.get('first_name', '')} {v.get('last_name', '')}".strip(), "email": v.get("email", ""),
            "stage": "pending", "document_results": "[]", "document_attempts": 0, "created": time.time(), "updated": time.time()})
        added += 1
    return added


def _count_matching(it: dict) -> int:
    found = registry.get(it["source_id"])
    if not found:
        return 0
    statuses = set(it["source_filter"].get("statuses") or [])
    depts = {d.lower() for d in it["source_filter"].get("departments") or []}
    return sum(1 for r in found[1].list_records(it["source_id"], 1, 100000)["items"]
               if r["values"].get("status") in statuses and (not depts or str(r["values"].get("department_name", "")).lower() in depts))


def _start_run(it: dict, trigger: str) -> str:
    now = time.time()
    q("MATCH (r:WorkflowRun {integration_id:$i, status:'running'}) WHERE r.started < $t SET r.status='failed', r.finished=$n",
      i=it["id"], t=now - STALE_RUN_AFTER, n=now)
    if q1("MATCH (r:WorkflowRun {integration_id:$i, status:'running'}) RETURN r", i=it["id"]):
        raise AlreadyRunning("This integration is already running.")
    rid = new_id("run")
    q("CREATE (r:WorkflowRun) SET r = $p", p={"id": rid, "integration_id": it["id"], "integration_name": it["name"], "trigger": trigger,
                                              "status": "running", "started": now, "finished": 0, "details": "[]", "counts": "{}"})
    return rid


async def run_integration(iid: str, trigger: str = "manual", actor: str = "HR") -> dict:
    it = store.get(iid)
    if not it:
        raise LookupError("Integration not found")
    run_id = _start_run(it, trigger)
    ls.log_event(log, "run.started", run=run_id, integration=iid, trigger=trigger)
    await notify(it, "run_created", f"{'Scheduled' if trigger == 'scheduled' else 'Manual'} run started: {it['name']}")
    counts: Counter = Counter()
    details: list[dict] = []
    try:
        ex_fields, ex_sections = exclusions_of(it)
        counts["enrolled"] = _enroll(it)
        counts["fetched"] = _count_matching(it)
        live = [i for i in store.items(iid) if i["stage"] not in ("completed", "hr_rejected", "skipped")]
        for item in sorted(live, key=lambda i: i.get("name", "")):
            name = display_name(item.get("name"), item.get("candidate_ref"), ex_fields)
            try:
                source_values = _candidate(it, item)
            except RecordRejected:
                source_values = {}
            error, actions = None, []
            try:
                actions = await _advance(it, item["id"])
            except (RecordRejected, CandidateNotReady) as exc:
                error = str(exc)
            except Exception as exc:  # e.g. Gmail outage: keep the candidate where it is
                error = f"Couldn't complete this step: {type(exc).__name__}"
                log.exception("run.candidate_error run=%s item=%s", run_id, item["id"])
            if error:
                store.set_item(item["id"], last_error=error)
                details.append({"candidate": name, "outcome": "failed", "message": error})
            after = store.item(item["id"])
            stage = after["stage"]
            mapping = snapshot_mapping(it["mapping"], source_values, ex_fields, ex_sections, json.loads(after.get("generated_values") or "{}"))
            outcome = "failed" if error else "advanced" if actions else "waiting"
            counts[outcome] += 1
            counts.update(a["kind"] for a in actions)
            logged = list(actions)
            if outcome == "waiting" and stage in _WAITING_ON:
                section, message = _WAITING_ON[stage]
                logged.append({"section": section, "kind": "waiting", "message": message})
            q("CREATE (l:IntegrationLogRecord) SET l = $p", p={
                "id": new_id("log"), "integration_id": iid, "run_id": run_id, "candidate_record_id": item["record_id"],
                "candidate_ref": item.get("candidate_ref", ""), "candidate_name": name, "outcome": outcome,
                "stage_before": item["stage"], "stage_after": stage, "actions": json.dumps(visible_actions(logged, ex_sections)),
                "mapping": json.dumps(mapping), "error": error or "", "ts": time.time()})
            ls.log_event(log, "run.candidate", run=run_id, item=item["id"], outcome=outcome, stage_before=item["stage"],
                         stage_after=stage, actions=",".join(a["kind"] for a in actions) or "none", mapped_fields=len(mapping))
        counts["approvals_pending"] = sum(1 for i in store.items(iid) if i["stage"] == "awaiting_hr_approval")
    except Exception as exc:  # the run must always be closed, whatever went wrong
        ls.log_event(log, "run.crashed", run=run_id, error=type(exc).__name__)
        details = [{"candidate": "", "outcome": "failed", "message": "The run stopped unexpectedly."}]
        await _finish(it, run_id, "failed", Counter(), details)
        raise
    status = "succeeded" if counts["failed"] == 0 else "partial" if counts["advanced"] > 0 else "failed"
    run = await _finish(it, run_id, status, counts, details)
    audit.record_audit("integration.run", "integration", iid,
                       f"Run {status}: {counts['advanced']} advanced, {counts['waiting']} waiting, {counts['failed']} failed: {it['name']}",
                       actor=actor if trigger == "manual" else "Scheduler", metadata={"run_id": run_id, "trigger": trigger})
    ls.log_event(log, "run.finished", run=run_id, status=status, **dict(sorted(counts.items())))
    return run


async def _finish(it: dict, run_id: str, status: str, counts: Counter, details: list[dict]) -> dict:
    c = {k: counts[k] for k in ("enrolled", "advanced", "waiting", "failed", "fetched", "documents_requested", "documents_verified",
                                 "documents_rejected", "approvals_pending", "approval_used", "offer_sent", "employee_created",
                                 "employee_updated", "employee_skipped", "access_granted", "access_requested")}
    now = time.time()
    q("MATCH (r:WorkflowRun {id:$id}) SET r.status=$s, r.counts=$c, r.details=$d, r.finished=$f",
      id=run_id, s=status, c=json.dumps(c), d=json.dumps(details), f=now)
    store.update(it["id"], last_run_at=now)
    summary = f"{c['advanced']} advanced, {c['waiting']} waiting, {c['failed']} failed"
    if status == "succeeded":
        await notify(it, "run_succeeded", f"{it['name']} succeeded: {summary}")
    else:
        await notify(it, "run_failed", f"{it['name']} had failures: {summary}")
    return run_row(q1("MATCH (r:WorkflowRun {id:$id}) RETURN r", id=run_id)["r"])


def run_row(r: dict) -> dict:
    return {**r, "counts": json.loads(r.get("counts") or "{}"), "details": json.loads(r.get("details") or "[]")}


async def run_due() -> int:
    """Scheduler tick: run every active integration whose next run time has passed."""
    now = time.time()
    due = [it for it in store.all_integrations() if it["status"] == "active" and it.get("next_run_at") and it["next_run_at"] <= now]
    if due:
        ls.log_event(log, "scheduler.due", count=len(due))
    for it in due:
        store.update(it["id"], next_run_at=store.next_run_time(it["schedule"]))  # claim first so a slow run can't double-fire
        try:
            await run_integration(it["id"], "scheduled", "Scheduler")
        except AlreadyRunning:
            ls.log_event(log, "scheduler.skipped", integration=it["id"])
    return len(due)

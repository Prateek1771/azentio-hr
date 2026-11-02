"""Integrations in Postgres (Phase 3 batch D): setup state, per-candidate progress, runs, the run log, outbound and
agent messages. The only reader and writer of those tables; it also registers their graph migration specs.

An integration's settings live in one `config` JSON document (setup needs "not answered yet" states); the basics
(id, name, status, next_run_at) are columns. Rows come back without NULL columns, as the graph omitted absent
properties, so every caller and API response keeps its shape.
"""
import json
import time
from datetime import datetime, timedelta, timezone as tz
from zoneinfo import ZoneInfo

import psycopg

import graph_migration as gm
from graph import new_id
from shared import db
from connectors import registry
from connectors.candidates import CANDIDATE_FIELDS
from .run_log import LOG_SECTIONS

STEPS = ("records", "mapping", "notifications", "documents", "offer", "access", "schedule", "review")
CANDIDATE_STATUSES = ("applied", "interviewing", "offer_accepted", "hired", "rejected")
STAGES = ("pending", "documents_requested", "documents_rejected", "awaiting_hr_approval", "hr_rejected",
          "approved", "offer_sent", "completed", "skipped")
OFFER_PLACEHOLDERS = ("candidate.first_name", "candidate.last_name", "candidate.email", "candidate.designation",
                      "candidate.department", "candidate.grade", "candidate.location", "candidate.joining_date", "company.name")
COMPANY = "Azentio"

QUESTIONS = {
    "records": "Which candidates should this integration pick up, and what should happen if an employee with the same email already exists?",
    "mapping": "Next is the field mapping. I can suggest how candidate fields fill the employee record, and you can change anything before approving it.",
    "notifications": "Who should get a notification about this integration's runs, and for which events?",
    "documents": "Should candidates go through document verification? If yes, I email each candidate the documents their role needs through Gmail, "
                 "check every reply with AI and the requirement rules, and you approve the result before anything else happens.",
    "documents_roles": "Which role are these candidates joining as? I'll ask each role only for the documents the Requirement rules need "
                       "for it, and you can trim the list.",
    "access": "Which apps should new joiners get once their employee record exists? I can create Slack, Jira and Google Workspace "
              "accounts, add them to channels or projects (or ask the channel or project admin), and raise ServiceNow requests. "
              "My suggestion follows your Requirement rules.",
    "offer": "Should an offer letter be sent once a candidate is approved?",
    "schedule": "When should this integration run? You can also start it with Run now at any time.",
    "review": "Everything is configured. Review it and activate the integration when you're ready.",
}

DEFAULT_CONFIG = {
    "description": "", "setup_step": "records", "source_id": "", "target_id": "",
    "source_filter": {"statuses": ["offer_accepted"], "departments": []}, "duplicate_policy": "update",
    "mapping": [], "mapping_approved_at": None, "notify_members": [], "notify_emails": [],
    "notify_on_created": False, "notify_on_success": True, "notify_on_failure": True,
    "doc_verification_enabled": None, "offer_enabled": None, "offer_template": "", "offer_template_status": "none",
    "schedule": {"kind": "manual", "interval_minutes": None, "time": None, "timezone": "UTC"},
    "last_run_at": None, "activated_at": None, "records_saved": False, "notifications_saved": False, "schedule_saved": False,
    "log_exclusions": {"fields": [], "sections": []},
    "role_documents": {}, "access_rules": [], "access_saved": False,
}


def question(it: dict) -> str:
    if it["setup_step"] == "documents" and it["doc_verification_enabled"]:
        return QUESTIONS["documents_roles"]
    return QUESTIONS[it["setup_step"]]


class SetupError(Exception):
    """A setting HR asked for can't be applied; the message says why."""


def _present(row: dict) -> dict:
    """Drop NULL columns: FalkorDB had no such property, so callers never saw the key."""
    return {k: v for k, v in row.items() if v is not None}


def _load(row: dict) -> dict:
    cfg = {**DEFAULT_CONFIG, **(gm.jsonv(row.get("config"), {}) or {})}
    return {"id": row["id"], "name": row["name"], "status": row["status"], "created": row.get("created"),
            "next_run_at": row.get("next_run_at") or None, **cfg}


def get(iid: str) -> dict | None:
    row = db.fetch1("SELECT * FROM integrations WHERE id=%s", (iid,))
    return _load(row) if row else None


def all_integrations() -> list[dict]:
    return [_load(r) for r in db.fetch("SELECT * FROM integrations ORDER BY created DESC, id")]


def name_taken(name: str, except_id: str = "") -> bool:
    return bool(db.fetch1("SELECT 1 AS x FROM integrations WHERE lower(name)=lower(%s) AND id <> %s", (name, except_id)))


def _write(it: dict) -> dict:
    cfg = {k: it[k] for k in DEFAULT_CONFIG}
    try:
        db.execute("UPDATE integrations SET name=%s, status=%s, next_run_at=%s, config=%s WHERE id=%s",
                   (it["name"], it["status"], it.get("next_run_at") or 0, db.Json(cfg), it["id"]))
    except psycopg.errors.UniqueViolation:
        raise SetupError("An integration with that name exists.") from None
    return it


def create(name: str, description: str, source_id: str, target_id: str) -> dict:
    iid = new_id("int")
    try:
        db.execute("INSERT INTO integrations (id, name, status, created) VALUES (%s, %s, 'draft', %s)", (iid, name, time.time()))
    except psycopg.errors.UniqueViolation:  # also a concurrent create with the same name, any case
        raise SetupError(f"An integration called '{name}' already exists") from None
    it = get(iid)
    it.update(description=description, source_id=source_id, target_id=target_id)
    return _write(it)


def update(iid: str, **changes) -> dict:
    it = get(iid)
    if not it:
        raise LookupError("Integration not found")
    it.update(changes)
    it["setup_step"] = current_step(it)
    return _write(it)


# ---------- derived state ----------

def schemas(it: dict):
    src, tgt = registry.get(it["source_id"]), registry.get(it["target_id"])
    s_fields = src[1].describe_schema(it["source_id"]) if src else list(CANDIDATE_FIELDS)
    t_fields = tgt[1].describe_schema(it["target_id"]) if tgt else []
    return s_fields, t_fields, (src[0] if src else None), (tgt[0] if tgt else None)


def missing_required(it: dict) -> list:
    _, t_fields, _, _ = schemas(it)
    mapped = {r["target_field"] for r in it["mapping"]}
    return [f for f in t_fields if f.required and not f.generated and f.key not in mapped]


def current_step(it: dict) -> str:
    """The first step whose question hasn't been answered."""
    if not it["records_saved"]:
        return "records"
    if not it["mapping"] or it["mapping_approved_at"] is None or missing_required(it):
        return "mapping"
    if not it["notifications_saved"]:
        return "notifications"
    if it["doc_verification_enabled"] is None or (it["doc_verification_enabled"] and not it["role_documents"]):
        return "documents"
    if it["offer_enabled"] is None or (it["offer_enabled"] and it["offer_template_status"] != "approved"):
        return "offer"
    if not it["access_saved"]:
        return "access"
    if not it["schedule_saved"]:
        return "schedule"
    return "review"


def problems(it: dict) -> list[str]:
    """Activation blockers, in the order HR should fix them."""
    out = []
    if not it["source_id"] or not it["target_id"]:
        out.append("Choose a source and a target.")
    for f in missing_required(it):
        out.append(f"{f.label} is required in the target and has no mapping.")
    if it["mapping_approved_at"] is None:
        out.append("Approve the field mapping.")
    if it["doc_verification_enabled"] is None:
        out.append("Choose whether to use document verification.")
    elif it["doc_verification_enabled"] and not it["role_documents"]:
        out.append("Choose which documents each role needs.")
    if it["offer_enabled"] is None:
        out.append("Choose whether to send an offer letter.")
    elif it["offer_enabled"] and it["offer_template_status"] != "approved":
        out.append("Approve the offer letter format.")
    return out


def settle(it: dict) -> str | None:
    """A change to something HR approved pauses a live integration until it's approved again."""
    if it["status"] == "active":
        p = problems(it)
        if p:
            update(it["id"], status="paused", next_run_at=None)
            return f"I paused this integration. {p[0]}"
    return None


def quick_replies(it: dict) -> list[str]:
    if it["status"] == "active":
        return ["Run now", "Pause it"]
    if it["status"] == "paused":
        return ["Resume"]
    step = it["setup_step"]
    if step == "mapping" and it["mapping"] and it["mapping_approved_at"] is None:
        return (["Approve"] if not missing_required(it) else []) + ["Suggest again"]
    if step == "offer" and it["offer_enabled"]:
        return ["Approve", "Write it again", "No offer letter"]
    if step == "documents" and it["doc_verification_enabled"]:
        from .service import roles  # roles come from the source records and the rules
        return [*roles(it)[:4], "All roles"]
    return {
        "records": ["Yes, accepted offers in all departments", "Only Engineering candidates", "Hired candidates, leave existing employees unchanged"],
        "notifications": ["Notify HR Operations about everything", "Notify only about failures"],
        "documents": ["Yes, verify documents", "No, skip verification"],
        "offer": ["Yes, send an offer letter", "No offer letter"],
        "access": ["Use the suggested access", "No app access"],
        "schedule": ["Every 6 hours", "Daily at 9am", "Only when I run it"],
        "review": ["Activate"],
    }.get(step, [])


def connectors_locked(iid: str) -> bool:
    return bool(db.fetch1("SELECT 1 AS x FROM integration_candidates WHERE integration_id=%s LIMIT 1", (iid,)))


def next_run_time(schedule: dict, now: datetime | None = None) -> float | None:
    now = now or datetime.now(tz.utc)
    kind = schedule.get("kind")
    if kind == "interval" and schedule.get("interval_minutes"):
        return (now + timedelta(minutes=int(schedule["interval_minutes"]))).timestamp()
    if kind == "daily" and schedule.get("time"):
        zone = ZoneInfo(schedule.get("timezone") or "UTC")
        hh, mm = map(int, schedule["time"].split(":"))
        local = now.astimezone(zone)
        cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= local:
            cand += timedelta(days=1)
        return cand.timestamp()
    return None


def validate_log_exclusions(fields: list[str], sections: list[str], it: dict) -> None:
    s_fields, t_fields, _, _ = schemas(it)
    known = {f.key for f in [*s_fields, *t_fields]}
    bad = [f for f in fields if f not in known]
    if bad:
        raise SetupError(f"There's no field called {bad[0]}")
    bad = [s for s in sections if s not in LOG_SECTIONS]
    if bad:
        raise SetupError(f"There's no log section called {bad[0]}; use {', '.join(LOG_SECTIONS)}")


# ---------- per-candidate progress ----------

def items(iid: str) -> list[dict]:
    return [_item(r) for r in db.fetch("SELECT * FROM integration_candidates WHERE integration_id=%s ORDER BY created, id", (iid,))]


def item(item_id: str) -> dict | None:
    row = db.fetch1("SELECT * FROM integration_candidates WHERE id=%s", (item_id,))
    return _item(row) if row else None


def item_for_joiner(eid: str) -> dict | None:
    row = db.fetch1("SELECT * FROM integration_candidates WHERE joiner_id=%s ORDER BY created LIMIT 1", (eid,))
    return _item(row) if row else None


def _item(c: dict) -> dict:
    c = _present(c)
    return {**c, "document_results": json.loads(c.get("document_results") or "[]"), "access": json.loads(c.get("access_results") or "[]")}


ITEM_COLS = ("id", "integration_id", "record_id", "candidate_ref", "name", "email", "stage", "document_results",
             "document_attempts", "joiner_id", "last_error", "generated_values", "employee_record_id", "access_results",
             "decided_at", "rejection_reason", "created", "updated")
_ITEM_TEXT = {"candidate_ref", "name", "email", "stage", "document_results", "joiner_id", "last_error", "generated_values",
              "employee_record_id", "access_results", "rejection_reason"}


def set_item(item_id: str, **values) -> None:
    if "document_results" in values:
        values["document_results"] = json.dumps(values["document_results"])
    values["updated"] = time.time()
    bad = set(values) - set(ITEM_COLS)
    assert not bad, f"unknown candidate field(s): {bad}"
    values = {k: ("" if v is None and k in _ITEM_TEXT else v) for k, v in values.items()}  # as the graph stored them
    db.execute(f"UPDATE integration_candidates SET {', '.join(f'{k}=%({k})s' for k in values)} WHERE id=%(_id)s",
               {**values, "_id": item_id})


def enroll_candidate(iid: str, record_id: str, candidate_ref: str, name: str, email: str) -> bool:
    """Add one candidate to an integration; False if it's already enrolled (the unique key makes this race-safe)."""
    now = time.time()
    return db.execute("""INSERT INTO integration_candidates (id, integration_id, record_id, candidate_ref, name, email, stage,
                                                             document_results, document_attempts, created, updated)
                         VALUES (%s, %s, %s, %s, %s, %s, 'pending', '[]', 0, %s, %s)
                         ON CONFLICT (integration_id, record_id) DO NOTHING""",
                      (new_id("ic"), iid, record_id, candidate_ref, name, email, now, now)) == 1


# ---------- runs and the run log ----------

def start_run(iid: str, name: str, trigger: str, stale_after: float) -> str | None:
    """Close a crashed run left 'running' for longer than stale_after, then open a new one. None if one is running
    (the database allows one running run per integration)."""
    now = time.time()
    rid = new_id("run")
    with db.tx() as conn:
        db.execute("UPDATE workflow_runs SET status='failed', finished=%s WHERE integration_id=%s AND status='running' "
                   "AND started < %s", (now, iid, now - stale_after), conn=conn)
        inserted = db.execute("""INSERT INTO workflow_runs (id, integration_id, integration_name, trigger, status, started)
                                 VALUES (%s, %s, %s, %s, 'running', %s) ON CONFLICT DO NOTHING""",
                              (rid, iid, name, trigger, now), conn=conn)
    return rid if inserted == 1 else None


def finish_run(run_id: str, status: str, counts: dict, details: list) -> None:
    db.execute("UPDATE workflow_runs SET status=%s, counts=%s, details=%s, finished=%s WHERE id=%s",
               (status, db.Json(counts), db.Json(details), time.time(), run_id))


def run(run_id: str) -> dict | None:
    r = db.fetch1("SELECT * FROM workflow_runs WHERE id=%s", (run_id,))
    return _present(r) if r else None


def runs(integration_id: str = "", page: int = 1, page_size: int = 15) -> tuple[list[dict], int]:
    """Newest first, paged in SQL; '' = every integration."""
    where = "WHERE %(i)s = '' OR integration_id = %(i)s"
    total = db.fetch1(f"SELECT count(*) AS n FROM workflow_runs {where}", {"i": integration_id})["n"]
    rows = db.fetch(f"SELECT * FROM workflow_runs {where} ORDER BY started DESC, id LIMIT %(l)s OFFSET %(o)s",
                    {"i": integration_id, "l": page_size, "o": (max(page, 1) - 1) * page_size})
    return [_present(r) for r in rows], total


LOG_COLS = ("id", "integration_id", "run_id", "candidate_record_id", "candidate_ref", "candidate_name", "outcome",
            "stage_before", "stage_after", "actions", "mapping", "error", "ts")


def add_log_record(row: dict) -> None:
    """One candidate's line in a run. The caller has already applied the exclusions (excluded values are never
    written, excluded sections' actions are dropped), so the table holds only what HR allowed."""
    row = {k: row.get(k) for k in LOG_COLS}
    for k in ("actions", "mapping"):
        row[k] = db.Json(gm.jsonv(row[k], []))
    db.execute(f"INSERT INTO integration_log_records ({', '.join(LOG_COLS)}) VALUES ({', '.join(f'%({k})s' for k in LOG_COLS)})", row)


def log_records(run_id: str) -> list[dict]:
    return [_present(r) for r in db.fetch("SELECT * FROM integration_log_records WHERE run_id=%s ORDER BY candidate_name, id",
                                          (run_id,))]


# ---------- outbound and agent messages ----------

def _message(r: dict) -> dict:
    r = _present(r)
    r["to"] = r.pop("recipient")
    return r


def add_outbound(row: dict) -> None:
    db.execute("""INSERT INTO outbound_messages (id, integration_id, item_id, kind, recipient, subject, content, attempt,
                                                 delivered_by, provider_ref, candidate, ts)
                  VALUES (%(id)s, %(integration_id)s, %(item_id)s, %(kind)s, %(to)s, %(subject)s, %(content)s, %(attempt)s,
                          %(delivered_by)s, %(provider_ref)s, %(candidate)s, %(ts)s)""", row)


def outbound(iid: str) -> list[dict]:
    return [_message(r) for r in db.fetch("SELECT * FROM outbound_messages WHERE integration_id=%s ORDER BY ts DESC, id", (iid,))]


def say(iid: str, role: str, content: str) -> None:
    db.execute("INSERT INTO agent_messages (id, integration_id, role, content, ts) VALUES (%s, %s, %s, %s, %s)",
               (new_id("am"), iid, role, content, time.time()))


def agent_messages(iid: str) -> list[dict]:
    return db.fetch("SELECT * FROM agent_messages WHERE integration_id=%s ORDER BY ts, id", (iid,))


# ---------- Phase 3 batch D specs ----------
# Runtime labels, so this module (which registers the specs) isn't reported as a graph read path.
INT, CAND, RUN, LOG, OUT, MSG = ("Integration", "IntegrationCandidate", "WorkflowRun", "IntegrationLogRecord",
                                 "OutboundMessage", "AgentMessage")


def _f(v):
    return None if v in (None, "") else float(v)


def _props(row: dict) -> dict:
    """Graph properties for rollback: FalkorDB can't store null, so absent stays absent."""
    return {k: v for k, v in row.items() if v is not None}


def _epochs(row: dict, *keys) -> dict:
    return {**row, **{k: gm.epoch(row.get(k)) for k in keys}}


def _integration_row(p: dict) -> dict:
    return {"id": p["id"], "name": p.get("name") or "", "status": p.get("status") or "draft", "created": _f(p.get("created")) or 0.0,
            "next_run_at": _f(p.get("next_run_at")) or 0.0, "config": gm.json_strict(p.get("config"), {}, f"{INT} {p['id']} config")}


def _candidate_row(p: dict) -> dict:
    row = {k: p.get(k) for k in ITEM_COLS}
    for k in ("decided_at", "created", "updated"):
        row[k] = _f(row[k])
    row["document_attempts"] = None if row["document_attempts"] in (None, "") else int(row["document_attempts"])
    row["created"] = row["created"] or 0.0
    row["updated"] = row["updated"] or row["created"]
    return row


RUN_COLS = ("id", "integration_id", "integration_name", "trigger", "status", "started", "finished", "counts", "details")


def _run_row(p: dict) -> dict:
    return {"id": p["id"], "integration_id": p.get("integration_id") or "", "integration_name": p.get("integration_name") or "",
            "trigger": p.get("trigger") or "", "status": p.get("status") or "", "started": _f(p.get("started")) or 0.0,
            "finished": _f(p.get("finished")) or 0.0, "counts": gm.json_strict(p.get("counts"), {}, f"{RUN} {p['id']} counts"),
            "details": gm.json_strict(p.get("details"), [], f"{RUN} {p['id']} details")}


def _log_row(p: dict) -> dict:
    row = {k: p.get(k) for k in LOG_COLS}
    row.update(integration_id=row["integration_id"] or "", run_id=row["run_id"] or "", ts=_f(row["ts"]) or 0.0,
               actions=gm.json_strict(p.get("actions"), [], f"{LOG} {p['id']} actions"),
               mapping=gm.json_strict(p.get("mapping"), [], f"{LOG} {p['id']} mapping"))
    return row


OUT_KEYS = ("id", "integration_id", "item_id", "kind", "to", "subject", "content", "attempt", "delivered_by", "provider_ref",
            "candidate", "ts")


def _out_row(p: dict) -> dict:
    row = {k: p.get(k) for k in OUT_KEYS if k != "to"}
    row.update(recipient=p.get("to") or "", integration_id=row["integration_id"] or "", kind=row["kind"] or "",
               ts=_f(row["ts"]) or 0.0, attempt=None if row["attempt"] in (None, "") else int(row["attempt"]))
    return row


gm.register(gm.LabelSpec(
    label=INT, batch="D", table="integrations",
    to_row=_integration_row, canonical=lambda r: _epochs(dict(r), "created", "next_run_at"),
    from_row=lambda r: {**_props(r), "config": json.dumps(r["config"] or {})},
    mapped_keys=frozenset({"id", "name", "status", "created", "next_run_at", "config"})))

gm.register(gm.LabelSpec(
    label=CAND, batch="D", table="integration_candidates",
    to_row=_candidate_row, canonical=lambda r: _epochs(dict(r), "decided_at", "created", "updated"),
    from_row=_props, mapped_keys=frozenset(ITEM_COLS)))

gm.register(gm.LabelSpec(
    label=RUN, batch="D", table="workflow_runs",
    to_row=_run_row, canonical=lambda r: _epochs(dict(r), "started", "finished"),
    from_row=lambda r: {**_props(r), "counts": json.dumps(r["counts"]), "details": json.dumps(r["details"])},
    mapped_keys=frozenset(RUN_COLS)))

gm.register(gm.LabelSpec(
    label=LOG, batch="D", table="integration_log_records",
    to_row=_log_row, canonical=lambda r: _epochs(dict(r), "ts"),
    from_row=lambda r: {**_props(r), "actions": json.dumps(r["actions"]), "mapping": json.dumps(r["mapping"])},
    mapped_keys=frozenset(LOG_COLS)))

gm.register(gm.LabelSpec(
    label=OUT, batch="D", table="outbound_messages",
    to_row=_out_row, canonical=lambda r: _epochs(dict(r), "ts"),
    from_row=lambda r: _props({**{k: v for k, v in r.items() if k != "recipient"}, "to": r["recipient"]}),
    mapped_keys=frozenset(OUT_KEYS)))

gm.register(gm.LabelSpec(
    label=MSG, batch="D", table="agent_messages",
    to_row=lambda p: {"id": p["id"], "integration_id": p.get("integration_id") or "", "role": p.get("role") or "",
                      "content": p.get("content") or "", "ts": _f(p.get("ts")) or 0.0},
    canonical=lambda r: _epochs(dict(r), "ts"), from_row=_props,
    mapped_keys=frozenset({"id", "integration_id", "role", "content", "ts"})))

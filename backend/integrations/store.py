"""Integration records and setup state (FalkorDB).

An integration's settings live in one JSON `config` property, because a FalkorDB property can't hold
null and setup needs "not answered yet" states. Indexed basics (id, name, status, next_run_at) stay
top-level for queries.
"""
import json
import time
from datetime import datetime, timedelta, timezone as tz
from zoneinfo import ZoneInfo

from graph import q, q1, new_id
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


def _load(row: dict) -> dict:
    cfg = {**DEFAULT_CONFIG, **json.loads(row.get("config") or "{}")}
    return {"id": row["id"], "name": row["name"], "status": row["status"], "created": row.get("created"),
            "next_run_at": row.get("next_run_at") or None, **cfg}


def get(iid: str) -> dict | None:
    row = q1("MATCH (i:Integration {id:$id}) RETURN i", id=iid)
    return _load(row["i"]) if row else None


def all_integrations() -> list[dict]:
    return [_load(r["i"]) for r in q("MATCH (i:Integration) RETURN i ORDER BY i.created DESC")]


def _write(it: dict) -> dict:
    cfg = {k: it[k] for k in DEFAULT_CONFIG}
    q("MATCH (i:Integration {id:$id}) SET i.name=$n, i.status=$s, i.next_run_at=$nr, i.config=$c",
      id=it["id"], n=it["name"], s=it["status"], nr=it.get("next_run_at") or 0, c=json.dumps(cfg))
    return it


def create(name: str, description: str, source_id: str, target_id: str) -> dict:
    if q1("MATCH (i:Integration) WHERE toLower(i.name)=toLower($n) RETURN i", n=name):
        raise SetupError(f"An integration called '{name}' already exists")
    iid = new_id("int")
    q("CREATE (i:Integration {id:$id, name:$n, status:'draft', created:$t, next_run_at:0, config:'{}'})", id=iid, n=name, t=time.time())
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
    return bool(q1("MATCH (c:IntegrationCandidate {integration_id:$i}) RETURN c LIMIT 1", i=iid))


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
    rows = q("MATCH (c:IntegrationCandidate {integration_id:$i}) RETURN c ORDER BY c.created", i=iid)
    return [_item(r["c"]) for r in rows]


def item(item_id: str) -> dict | None:
    row = q1("MATCH (c:IntegrationCandidate {id:$id}) RETURN c", id=item_id)
    return _item(row["c"]) if row else None


def item_for_joiner(eid: str) -> dict | None:
    row = q1("MATCH (c:IntegrationCandidate {joiner_id:$e}) RETURN c", e=eid)
    return _item(row["c"]) if row else None


def _item(c: dict) -> dict:
    return {**c, "document_results": json.loads(c.get("document_results") or "[]"), "access": json.loads(c.get("access_results") or "[]")}


def set_item(item_id: str, **values) -> None:
    if "document_results" in values:
        values["document_results"] = json.dumps(values["document_results"])
    values["updated"] = time.time()
    q("MATCH (c:IntegrationCandidate {id:$id}) SET c += $p", id=item_id, p={k: ("" if v is None else v) for k, v in values.items()})

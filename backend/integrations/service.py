"""Every change to an integration goes through here: the UI buttons and the agent's tools share it,
so the agent can never do something the normal validation wouldn't allow."""
import re
import time
from zoneinfo import ZoneInfo

import audit
import checklist
import logging_setup as ls
from connectors import registry
from graph import q, q1, new_id
from . import delivery, store
from .agent import get_agent
from .engine import unknown_placeholders
from .mapping import TRANSFORMS
from .store import SetupError

log = ls.get("integrations")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- conversation ----------

def say(iid: str, role: str, content: str) -> None:
    if content:
        q("CREATE (m:AgentMessage {id:$id, integration_id:$i, role:$r, content:$c, ts:$t})",
          id=new_id("am"), i=iid, r=role, c=content, t=time.time())


def messages(iid: str) -> list[dict]:
    return [r["m"] for r in q("MATCH (m:AgentMessage {integration_id:$i}) RETURN m ORDER BY m.ts", i=iid)]


def _answered(iid: str, before_step: str, answer: str, speak: bool) -> None:
    """Record HR's answer from a button, and ask the next question if setup moved on."""
    if not speak:
        return
    it = store.get(iid)
    say(iid, "user", answer)
    if store.STEPS.index(it["setup_step"]) > store.STEPS.index(before_step):
        say(iid, "agent", store.QUESTIONS[it["setup_step"]])


def _settled(iid: str, speak: bool) -> None:
    msg = store.settle(store.get(iid))
    if msg and speak:
        say(iid, "agent", msg)


def _audit(action: str, it: dict, summary: str, actor: str = "HR", **meta):
    audit.record_audit(action, "integration", it["id"], f"{summary}: {it['name']}", actor=actor, metadata=meta or None)


# ---------- lookups ----------

def owners() -> dict[str, str]:
    """Workspace members who can be notified: owner name -> email."""
    return {r["n"]: r["e"] for r in q("MATCH (o:Owner) RETURN o.name AS n, o.email AS e ORDER BY o.name")}


def connectors_by_name() -> dict[str, dict]:
    rows = q("MATCH (i:ConnectorInstance)-[:OF]->(c:Connector) RETURN i.id AS id, i.name AS name, c.role AS role")
    return {r["name"].lower(): r for r in rows}


def departments() -> list[str]:
    return checklist.config("departments") or []


# ---------- create / basics ----------

def create(name: str, description: str, source_id: str, target_id: str, actor: str = "HR") -> dict:
    name = (name or "").strip()
    if not name:
        raise SetupError("Enter a name")
    _check_connectors(source_id, target_id)
    it = store.create(name[:120], (description or "")[:500], source_id, target_id)
    src, tgt = registry.get(source_id)[0], registry.get(target_id)[0]
    say(it["id"], "agent", f"I'll set up {it['name']}, moving records from {src['name']} to {tgt['name']}. {store.QUESTIONS['records']}")
    _audit("integration.created", it, "Created integration", actor)
    return it


def _check_connectors(source_id: str, target_id: str) -> None:
    if source_id == target_id:
        raise SetupError("Choose different source and target connectors.")
    src, tgt = registry.get(source_id), registry.get(target_id)
    if not src or src[0]["role"] != "source":
        raise SetupError("Choose a source connector.")
    if not tgt or tgt[0]["role"] != "target":
        raise SetupError("Choose a target connector.")


def save_basics(iid: str, name: str | None = None, description: str | None = None, source_id: str | None = None,
                target_id: str | None = None, speak: bool = True) -> dict:
    it = store.get(iid)
    changes: dict = {}
    if name:
        clash = q1("MATCH (i:Integration) WHERE toLower(i.name)=toLower($n) AND i.id <> $id RETURN i", n=name.strip(), id=iid)
        if clash:
            raise SetupError("An integration with that name exists.")
        changes["name"] = name.strip()[:120]
    if description is not None:
        changes["description"] = description[:500]
    if (source_id and source_id != it["source_id"]) or (target_id and target_id != it["target_id"]):
        if store.connectors_locked(iid):
            raise SetupError("Candidates are already moving through this integration, so its connectors can't change. Create a new integration instead.")
        s, t = source_id or it["source_id"], target_id or it["target_id"]
        _check_connectors(s, t)
        changes.update(source_id=s, target_id=t, mapping=[], mapping_approved_at=None)
    it = store.update(iid, **changes)
    _audit("integration.updated", it, "Changed details")
    _settled(iid, speak)
    return store.get(iid)


# ---------- records ----------

def save_records(iid: str, statuses: list[str], departments_: list[str], duplicate_policy: str, speak: bool = True) -> dict:
    before = store.get(iid)["setup_step"]
    if not statuses:
        raise SetupError("Choose at least one candidate status.")
    bad = [s for s in statuses if s not in store.CANDIDATE_STATUSES]
    if bad:
        raise SetupError(f"Unknown candidate status: {bad[0]}")
    known = {d.lower(): d for d in departments()}
    missing = [d for d in departments_ if d.lower() not in known]
    if missing:
        raise SetupError(f"{missing[0]} isn't a department here.")
    if duplicate_policy not in ("update", "skip"):
        raise SetupError("Choose update or skip for existing employees.")
    store.update(iid, source_filter={"statuses": list(statuses), "departments": [known[d.lower()] for d in departments_]},
                 duplicate_policy=duplicate_policy, records_saved=True)
    where = "selected departments" if departments_ else "all departments"
    _answered(iid, before, f"Pick up {len(statuses)} status(es), {where}; {duplicate_policy} existing employees.", speak)
    return store.get(iid)


# ---------- mapping ----------

def mapping_problems(rules: list[dict], it: dict, complete: bool = True) -> list[str]:
    s_fields, t_fields, _, _ = store.schemas(it)
    s_keys, t_keys = {f.key for f in s_fields}, {f.key for f in t_fields}
    out = []
    targets = [r["target_field"] for r in rules]
    if len(targets) != len(set(targets)):
        out.append("Each target field can be mapped only once.")
    for r in rules:
        if not r.get("source_field") and not r.get("default_value"):
            out.append("Each row needs a source field or a default.")
        if r.get("source_field") and r["source_field"] not in s_keys:
            out.append(f"{r['source_field']} isn't a field in the source.")
        if r["target_field"] not in t_keys:
            out.append(f"{r['target_field']} isn't a field in the target.")
        if r.get("transform", "none") not in TRANSFORMS:
            out.append(f"Unknown transform {r.get('transform')}.")
    if complete:
        mapped = set(targets)
        out += [f"{f.label} is required in the target and has no mapping." for f in t_fields
                if f.required and not f.generated and f.key not in mapped]
    return list(dict.fromkeys(out))


async def suggest_mapping(iid: str) -> list[dict]:
    it = store.get(iid)
    s_fields, t_fields, _, _ = store.schemas(it)
    s_keys, t_keys = {f.key for f in s_fields}, {f.key for f in t_fields}
    out = []
    for m in await get_agent().suggest_mappings(s_fields, t_fields):
        if m.target_field in t_keys and (m.source_field in s_keys or m.default_value):
            out.append({**m.model_dump(), "origin": "agent"})
    return list({r["target_field"]: r for r in out}.values())


def save_mapping(iid: str, rules: list[dict], speak: bool = True) -> dict:
    it = store.get(iid)
    clean = [{"source_field": r.get("source_field") or None, "target_field": r["target_field"],
              "transform": r.get("transform") or "none", "default_value": r.get("default_value") or None,
              "confidence": r.get("confidence"), "rationale": r.get("rationale"), "origin": r.get("origin") or "hr"} for r in rules]
    problems = mapping_problems(clean, it, complete=False)
    if problems:
        raise SetupError(problems[0])
    store.update(iid, mapping=clean, mapping_approved_at=None)
    _settled(iid, speak)
    return store.get(iid)


def approve_mapping(iid: str, speak: bool = True, actor: str = "HR") -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    if not it["mapping"]:
        raise SetupError("Add at least one mapping before approving.")
    problems = mapping_problems(it["mapping"], it)
    if problems:
        raise SetupError(problems[0])
    it = store.update(iid, mapping_approved_at=time.time())
    _audit("integration.mapping_approved", it, "Approved field mapping", actor)
    _answered(iid, before, f"Approved a mapping of {len(it['mapping'])} fields.", speak)
    return store.get(iid)


def preview_mapping(iid: str) -> list[dict]:
    from .mapping import apply_mapping, check_required
    from connectors.base import RecordRejected
    it = store.get(iid)
    src, tgt = registry.get(it["source_id"]), registry.get(it["target_id"])
    out = []
    for rec in src[1].list_records(it["source_id"], 1, 5)["items"]:
        v = rec["values"]
        values = apply_mapping(it["mapping"], v)
        try:
            check_required(it["mapping"], tgt[1].describe_schema(it["target_id"]), values)
            err = None
        except RecordRejected as ex:
            err = str(ex)
        out.append({"candidate": f"{v.get('first_name', '')} {v.get('last_name', '')}".strip(), "values": values, "error": err})
    return out


# ---------- notifications / documents / offer / schedule ----------

def save_notifications(iid: str, members: list[str], emails: list[str], on_created: bool, on_success: bool, on_failure: bool,
                       speak: bool = True) -> dict:
    before = store.get(iid)["setup_step"]
    known = set(owners().values())
    bad = [m for m in members if m not in known]
    if bad:
        raise SetupError(f"{bad[0]} isn't a workspace member.")
    clean = []
    for e in emails:
        if not _EMAIL.match(e.strip()):
            raise SetupError(f"{e} isn't a valid email address.")
        clean.append(e.strip().lower())
    store.update(iid, notify_members=sorted(set(members)), notify_emails=sorted(set(clean)), notify_on_created=on_created,
                 notify_on_success=on_success, notify_on_failure=on_failure, notifications_saved=True)
    _answered(iid, before, f"Notify {len(set(members)) + len(set(clean))} recipient(s) about its runs.", speak)
    return store.get(iid)


def save_documents(iid: str, enabled: bool, speak: bool = True) -> dict:
    before = store.get(iid)["setup_step"]
    store.update(iid, doc_verification_enabled=bool(enabled))
    _answered(iid, before, "Yes, verify documents." if enabled else "No document verification.", speak)
    return store.get(iid)


async def generate_offer(iid: str, speak: bool = True) -> dict:
    it = store.get(iid)
    content = await get_agent().draft_offer_letter(store.COMPANY, list(store.OFFER_PLACEHOLDERS))
    store.update(iid, offer_template=content, offer_template_status="pending_approval")
    _settled(iid, speak)
    return store.get(iid)


async def save_offer_choice(iid: str, enabled: bool, speak: bool = True) -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    store.update(iid, offer_enabled=bool(enabled))
    if enabled and not it["offer_template"]:
        await generate_offer(iid, speak=False)
        if speak:
            say(iid, "user", "Yes, send an offer letter.")
            say(iid, "agent", "Here's a draft format for this integration. Edit it if you need to, then approve it. Offers can't be sent until you do.")
    else:
        _answered(iid, before, "Yes, send an offer letter." if enabled else "No offer letter.", speak)
    _settled(iid, speak)
    return store.get(iid)


def save_offer_template(iid: str, content: str, speak: bool = True) -> dict:
    if not content.strip():
        raise SetupError("The offer letter can't be empty.")
    bad = unknown_placeholders(content)
    if bad:
        raise SetupError(f"Unknown placeholder: {bad[0]}.")
    store.update(iid, offer_template=content[:20000], offer_template_status="pending_approval")
    _settled(iid, speak)
    return store.get(iid)


def decide_offer(iid: str, approve: bool, speak: bool = True, actor: str = "HR") -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    if not it["offer_template"]:
        raise SetupError("There's no offer letter format to decide on.")
    it = store.update(iid, offer_template_status="approved" if approve else "rejected")
    _audit("integration.offer_approved" if approve else "integration.offer_rejected", it,
           "Approved the offer letter format" if approve else "Rejected the offer letter format", actor)
    if approve:
        _answered(iid, before, "Approved the offer letter format.", speak)
    _settled(iid, speak)
    return store.get(iid)


def save_schedule(iid: str, kind: str, interval_minutes: int | None = None, time_: str | None = None, timezone: str | None = None,
                  speak: bool = True) -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    if kind not in ("manual", "interval", "daily"):
        raise SetupError("Choose manual, interval or daily.")
    tzname = timezone or "UTC"
    try:
        ZoneInfo(tzname)
    except Exception:
        raise SetupError("Unknown time zone")
    if kind == "interval":
        if not interval_minutes:
            raise SetupError("Choose how often to run.")
        if not 15 <= int(interval_minutes) <= 10080:
            raise SetupError("Runs can be every 15 minutes up to once a week.")
    if kind == "daily":
        if not time_ or not re.fullmatch(r"\d{1,2}:\d{2}", time_):
            raise SetupError("Choose a time of day.")
        hh, mm = map(int, time_.split(":"))
        if hh > 23 or mm > 59:
            raise SetupError("Choose a time of day.")
        time_ = f"{hh:02d}:{mm:02d}"
    schedule = {"kind": kind, "interval_minutes": int(interval_minutes) if kind == "interval" else None,
                "time": time_ if kind == "daily" else None, "timezone": tzname}
    changes = {"schedule": schedule, "schedule_saved": True}
    if it["status"] == "active":
        changes["next_run_at"] = store.next_run_time(schedule)
    store.update(iid, **changes)
    answer = {"manual": "Run only when started by hand.", "interval": f"Run every {interval_minutes} minutes.",
              "daily": f"Run daily at {time_}."}[kind]
    _answered(iid, before, answer, speak)
    return store.get(iid)


def save_log_settings(iid: str, fields: list[str], sections: list[str], actor: str = "HR") -> dict:
    it = store.get(iid)
    store.validate_log_exclusions(fields, sections, it)
    it = store.update(iid, log_exclusions={"fields": sorted(set(fields)), "sections": sorted(set(sections))})
    _audit("integration.log_settings_changed", it, "Changed what the integration log records", actor)
    return store.get(iid)


# ---------- lifecycle ----------

def activate(iid: str, actor: str = "HR") -> dict:
    it = store.get(iid)
    p = store.problems(it)
    if p:
        raise SetupError(p[0])
    it = store.update(iid, status="active", activated_at=time.time(), next_run_at=store.next_run_time(it["schedule"]))
    _audit("integration.activated", it, "Activated integration", actor)
    return store.get(iid)


def pause(iid: str, actor: str = "HR") -> dict:
    it = store.get(iid)
    if it["status"] != "active":
        raise SetupError("This integration isn't active.")
    it = store.update(iid, status="paused", next_run_at=None)
    _audit("integration.paused", it, "Paused integration", actor)
    return store.get(iid)


def decide_candidate(iid: str, item_id: str, approve: bool, reason: str | None, actor: str = "HR") -> dict:
    item = store.item(item_id)
    if not item or item["integration_id"] != iid:
        raise LookupError("Candidate not found in this integration")
    if not approve and not (reason or "").strip():
        raise SetupError("Enter a reason for rejecting these documents.")
    if item["stage"] != "awaiting_hr_approval":
        raise SetupError("This candidate isn't waiting for HR approval.")
    store.set_item(item_id, stage="approved" if approve else "hr_rejected", decided_at=time.time(), rejection_reason=(reason or "")[:1000])
    audit.record_audit("documents.hr_approved" if approve else "documents.hr_rejected", "integration_candidate", item_id,
                       f"{'Approved' if approve else 'Rejected'} documents for {item.get('name', '')}", actor=actor,
                       metadata={"reason": reason} if reason else None)
    return store.item(item_id)


# ---------- views ----------

def detail(iid: str) -> dict:
    it = store.get(iid)
    if not it:
        raise LookupError("Integration not found")
    s_fields, t_fields, src, tgt = store.schemas(it)
    name_by_email = {v: k for k, v in owners().items()}
    agent = get_agent()
    return {**it, "source_name": src["name"] if src else "", "target_name": tgt["name"] if tgt else "",
            "source_fields": [f.dict() for f in s_fields], "target_fields": [f.dict() for f in t_fields],
            "recipients": [{"email": e, "name": name_by_email.get(e, e)} for e in it["notify_members"]],
            "quick_replies": store.quick_replies(it), "connectors_locked": store.connectors_locked(iid),
            "messages": messages(iid), "problems": store.problems(it), "offer_placeholders": list(store.OFFER_PLACEHOLDERS),
            "agent_name": agent.name, "agent_is_simulated": agent.is_simulated,
            "delivery_is_simulated": delivery.is_simulated(),
            "owners": [{"name": n, "email": e} for n, e in owners().items()], "departments": departments()}


def summary(it: dict) -> dict:
    src, tgt = registry.get(it["source_id"]), registry.get(it["target_id"])
    return {k: it.get(k) for k in ("id", "name", "description", "status", "setup_step", "schedule", "next_run_at", "last_run_at", "created")} | {
        "source_id": it["source_id"], "target_id": it["target_id"],
        "source_name": src[0]["name"] if src else "", "target_name": tgt[0]["name"] if tgt else "",
        "candidates": sum(1 for _ in store.items(it["id"]))}


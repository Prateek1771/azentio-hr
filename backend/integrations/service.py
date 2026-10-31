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
from .expr import EMAIL_PRESETS, HELP as EXPRESSION_HELP, ExpressionError, check as check_expression
from .mapping import TRANSFORMS
from .store import SetupError

log = ls.get("integrations")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$")


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
    if store.STEPS.index(it["setup_step"]) > store.STEPS.index(before_step) or (
            it["setup_step"] == "documents" and it["doc_verification_enabled"] and not it["role_documents"]):
        say(iid, "agent", store.question(it))


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
        kind = r.get("transform", "none")
        if kind in ("expression", "unique_email"):
            try:
                check_expression(r.get("expression") or "", s_keys)
            except ExpressionError as e:
                out.append(f"{r['target_field']}: {e}")
            if kind == "unique_email" and not _DOMAIN.match((r.get("email_domain") or "").strip().lower().lstrip("@")):
                out.append(f"{r['target_field']}: give the email domain, like azentio.com.")
        elif not r.get("source_field") and not r.get("default_value"):
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
              "expression": (r.get("expression") or "").strip() or None,
              "email_domain": (r.get("email_domain") or "").strip().lower().lstrip("@") or None,
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
    taken = target_email_lookup(it)
    minted: set[str] = set()  # addresses handed out earlier in this preview count as taken too
    for rec in src[1].list_records(it["source_id"], 1, 5)["items"]:
        v = rec["values"]
        values = {}
        try:
            values = apply_mapping(it["mapping"], v, email_taken=lambda e: taken(e) or e in minted)
            minted.update(str(x) for x in values.values() if "@" in str(x))
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


def target_email_lookup(it: dict):
    """taken(email) -> bool over every email-looking value already in the target connector."""
    tgt = registry.get(it["target_id"])
    used = set()
    if tgt:
        for rec in tgt[1].list_records(it["target_id"], 1, 100_000)["items"]:
            used.update(str(v).lower() for v in rec["values"].values() if isinstance(v, str) and "@" in v)
    return lambda e: e.lower() in used


def test_expression(iid: str, expression: str, transform: str = "expression", email_domain: str = "") -> list[dict]:
    """Run an expression on a few real source records so HR sees the result before saving it."""
    from .expr import evaluate, unique_email
    it = store.get(iid)
    s_fields, _, _, _ = store.schemas(it)
    keys = {f.key for f in s_fields}
    try:
        check_expression(expression, keys)
    except ExpressionError as e:
        raise SetupError(str(e))
    src = registry.get(it["source_id"])
    taken = target_email_lookup(it)
    out = []
    for rec in src[1].list_records(it["source_id"], 1, 4)["items"] if src else []:
        v = rec["values"]
        name = f"{v.get('first_name', '')} {v.get('last_name', '')}".strip()
        try:
            res = unique_email(expression, email_domain, v, taken, keys) if transform == "unique_email" else evaluate(expression, v, keys)
            out.append({"candidate": name, "value": res, "error": None})
        except ExpressionError as e:
            out.append({"candidate": name, "value": None, "error": str(e)})
    return out


# ---------- documents by role ----------

def roles(it: dict) -> list[str]:
    """Roles HR can pick: what the source's matching candidates are joining as, then the roles the rules know."""
    found = registry.get(it["source_id"])
    seen: dict[str, None] = {}
    depts = set(it["source_filter"].get("departments") or [])
    statuses = set(it["source_filter"].get("statuses") or [])
    if found:
        for rec in found[1].list_records(it["source_id"], 1, 10_000)["items"]:
            v = rec["values"]
            if v.get("designation") and (not depts or v.get("department_name") in depts) and (not statuses or v.get("status") in statuses):
                seen[v["designation"]] = None
    for r in checklist.config("roles") or []:
        if not depts or r.get("department") in depts:
            seen.setdefault(r["role"], None)
    return list(seen)


def _role_profile(it: dict, role: str) -> dict:
    """department and grade for a role, from the rules' role list or the source's candidates."""
    for r in checklist.config("roles") or []:
        if r["role"].lower() == role.lower():
            return {"department": r.get("department", ""), "grade": r.get("grade", "")}
    found = registry.get(it["source_id"])
    for rec in found[1].list_records(it["source_id"], 1, 10_000)["items"] if found else []:
        v = rec["values"]
        if str(v.get("designation", "")).lower() == role.lower():
            return {"department": v.get("department_name", ""), "grade": v.get("grade", "")}
    return {"department": "", "grade": ""}


def document_options(it: dict, role: str) -> list[dict]:
    """Every document type, marked with whether the Requirement rules ask this role for it, and why.

    Country/location rules can't be decided from the role alone, so they're suggested with that condition;
    at run time the rules still apply per candidate (a US joiner never gets the PAN card request)."""
    doc_types = checklist.config("doc_types") or {}
    prof = _role_profile(it, role) if role != "*" else {"department": "", "grade": ""}
    out = {k: {"key": k, "name": d.get("name", k), "suggested": False, "reason": "No rule asks this role for it"}
           for k, d in doc_types.items()}
    for r in checklist.load_rules():
        if r["kind"] != "document" or r["item"] not in out:
            continue
        m = r.get("match") or {}
        role_part = {k: v for k, v in m.items() if k not in ("country", "location")}
        if role == "*":
            ok, why = True, "any role"
        else:
            emp = {"department": prof["department"], "role": role, "location": "", "grade": prof["grade"] or "G0"}
            ok, why = checklist.match_rule({"match": role_part}, emp, "")
        if ok:
            cond = [str(x) for v in m.values() for x in (v if isinstance(v, list) else [v])] if any(k in m for k in ("country", "location")) else []
            where = f" — only for candidates in {', '.join(cond)}" if cond else ""
            out[r["item"]].update(suggested=True, reason=f"Rule {r['id']} ({why}){where}")
    return sorted(out.values(), key=lambda d: (not d["suggested"], d["name"]))


def save_role_documents(iid: str, role: str, documents: list[str] | None, speak: bool = True) -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    role = (role or "").strip()
    if role.lower() in ("all", "all roles", "any", "any role", "everyone", "*"):
        role = "*"
    if not role:
        raise SetupError("Say which role this document set is for.")
    known = {r.lower(): r for r in roles(it)}
    if role != "*":
        if role.lower() not in known:
            raise SetupError(f"{role} isn't a role here. Roles: {', '.join(list(known.values())[:8])}")
        role = known[role.lower()]
    opts = document_options(it, role)
    names = {o["key"]: o["name"] for o in opts}
    if documents is None:
        documents = [o["key"] for o in opts if o["suggested"]]
    else:
        by_name = {n.lower(): k for k, n in names.items()}
        documents = [d if d in names else by_name.get(d.lower(), d) for d in documents]
        bad = [d for d in documents if d not in names]
        if bad:
            raise SetupError(f"There's no document type called {bad[0]}.")
    if not documents:
        raise SetupError("Pick at least one document, or turn document verification off.")
    mapping = {**it["role_documents"], role: sorted(set(documents))}
    store.update(iid, role_documents=mapping)
    label = "Every role" if role == "*" else role
    _audit("integration.role_documents", it, f"Set {len(documents)} document(s) for {label}")
    _answered(iid, before, f"{label}: ask for {', '.join(names[d] for d in mapping[role])}.", speak)
    return store.get(iid)


def remove_role_documents(iid: str, role: str) -> dict:
    it = store.get(iid)
    store.update(iid, role_documents={k: v for k, v in it["role_documents"].items() if k.lower() != role.lower()})
    _settled(iid, True)
    return store.get(iid)


def documents_for(it: dict, role: str) -> list[str] | None:
    """The document keys this integration asks a candidate in `role` for (None = whatever the rules say)."""
    rd = it.get("role_documents") or {}
    for k, v in rd.items():
        if k.lower() == (role or "").lower():
            return v
    return rd.get("*")


# ---------- app access ----------

def apps() -> list[dict]:
    """App connectors (Slack, Jira, …) and the actions each can take."""
    from connectors.apps import ACTIONS
    rows = q("""MATCH (i:ConnectorInstance)-[:OF]->(c:Connector {role:'app'}) RETURN i.id AS id, i.name AS name, c.key AS key
                ORDER BY i.name""")
    return [{**r, "actions": [{"key": k, "label": lbl, "needs_resource": need} for k, (lbl, need) in ACTIONS[r["key"]].items()]}
            for r in rows]


# Requirement-rule task -> the app actions that fulfil it. ponytail: a fixed table; move it onto the Rule
# node (an `app_action` property) when HR needs to edit it.
_TASK_ACCESS = {
    "email_account": [("google_workspace", "create_account", "", "direct"), ("slack", "create_account", "", "direct")],
    "laptop": [("servicenow", "create_ticket", "Laptop", "direct")],
    "id_badge": [("servicenow", "create_ticket", "ID badge", "direct")],
    "dev_tools": [("jira", "create_account", "", "direct"), ("jira", "add_to_project", "ENG", "request"),
                  ("slack", "add_to_channel", "#engineering", "direct")],
    "crm_access": [("servicenow", "create_ticket", "CRM access", "direct"), ("slack", "add_to_channel", "#sales", "direct")],
}


def suggested_access(it: dict) -> list[dict]:
    by_key = {a["key"]: a for a in apps()}
    out = []
    for r in checklist.load_rules():
        for app, action, resource, mode in _TASK_ACCESS.get(r["item"], []):
            if app in by_key:
                out.append({"id": f"{r['id']}:{app}:{action}", "app_id": by_key[app]["id"], "app": by_key[app]["name"],
                            "action": action, "resource": resource, "mode": mode, "match": r.get("match") or {},
                            "from_rule": r["id"]})
    if "slack" in by_key:
        out.append({"id": "welcome:slack", "app_id": by_key["slack"]["id"], "app": "Slack", "action": "notify_channel",
                    "resource": "#new-joiners", "mode": "direct", "match": {}, "from_rule": ""})
    return out


def _clean_access_rule(r: dict, by_id: dict) -> dict:
    from connectors.apps import ACTIONS
    app = by_id.get(r.get("app_id"))
    if not app:
        raise SetupError("Choose one of the app connectors: " + ", ".join(a["name"] for a in by_id.values()))
    action = r.get("action")
    if action not in ACTIONS[app["key"]]:
        raise SetupError(f"{app['name']} can't {str(action).replace('_', ' ')}. It can: {', '.join(ACTIONS[app['key']])}")
    label, needs = ACTIONS[app["key"]][action]
    resource = (r.get("resource") or "").strip()
    if needs and not resource:
        raise SetupError(f"{label} in {app['name']} needs a channel, project or request name.")
    m = r.get("match") or {}
    match = {k: (v if isinstance(v, list) else [v]) for k, v in m.items() if k in ("role", "department", "location", "country") and v}
    for k in ("grade_min", "grade_max"):
        if m.get(k) not in (None, ""):
            match[k] = int(m[k])
    return {"id": r.get("id") or new_id("ar"), "app_id": app["id"], "app": app["name"], "action": action, "resource": resource,
            "mode": "request" if r.get("mode") == "request" else "direct", "match": match, "from_rule": r.get("from_rule") or ""}


def save_access_rules(iid: str, rules: list[dict], speak: bool = True, actor: str = "HR") -> dict:
    it = store.get(iid)
    before = it["setup_step"]
    by_id = {a["id"]: a for a in apps()}
    clean = [_clean_access_rule(r, by_id) for r in rules]
    store.update(iid, access_rules=clean, access_saved=True)
    _audit("integration.access_rules", it, f"Set {len(clean)} app access rule(s)", actor)
    _answered(iid, before, f"Give new joiners {len(clean)} app access item(s)." if clean else "No app access.", speak)
    return store.get(iid)


def access_rule_applies(rule: dict, cand: dict) -> bool:
    """Same matching as the Requirement rules: role, department, location/country, grade."""
    locations = checklist.config("locations") or {}
    emp = {"department": cand.get("department_name", ""), "role": cand.get("designation", ""),
           "location": cand.get("location", ""), "grade": cand.get("grade", "")}
    return checklist.match_rule({"match": rule.get("match") or {}}, emp, locations.get(emp["location"], ""))[0]


def find_item(iid: str, candidate: str) -> dict:
    """A candidate in this integration by (part of) their name or email."""
    needle = candidate.strip().lower()
    hits = [i for i in store.items(iid) if needle and (needle in str(i.get("name", "")).lower() or needle == str(i.get("email", "")).lower())]
    if not hits:
        raise SetupError(f"{candidate} isn't a candidate in this integration yet. Run it first so they're picked up.")
    if len(hits) > 1:
        raise SetupError(f"More than one candidate matches {candidate}: {', '.join(i['name'] for i in hits)}")
    return hits[0]


def grant_access(iid: str, item_id: str, app_id: str, action: str, resource: str = "", mode: str = "direct",
                 actor: str = "HR") -> dict:
    """Give one candidate one app access now, outside the rules (e.g. HR asked the agent for it)."""
    from .engine import perform_access
    it, item = store.get(iid), store.item(item_id)
    if not it or not item or item["integration_id"] != iid:
        raise LookupError("Candidate not found in this integration")
    rule = _clean_access_rule({"app_id": app_id, "action": action, "resource": resource, "mode": mode},
                              {a["id"]: a for a in apps()})
    result = perform_access(it, item, rule, actor)
    audit.record_audit("access.requested" if result["status"] == "requested" else "access.granted", "integration_candidate",
                       item_id, f"{result['message']} ({rule['app']}): {it['name']}", actor=actor)
    return {"item": store.item(item_id), "result": result}


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
            "owners": [{"name": n, "email": e} for n, e in owners().items()], "departments": departments(),
            "roles": roles(it), "doc_types": [{"key": k, "name": d.get("name", k)} for k, d in (checklist.config("doc_types") or {}).items()],
            "apps": apps(), "suggested_access": suggested_access(it),
            "expression_functions": [{"name": k, "example": v} for k, v in EXPRESSION_HELP.items()],
            "email_presets": [{"key": k, "expression": v} for k, v in EMAIL_PRESETS.items()]}


def summary(it: dict) -> dict:
    src, tgt = registry.get(it["source_id"]), registry.get(it["target_id"])
    return {k: it.get(k) for k in ("id", "name", "description", "status", "setup_step", "schedule", "next_run_at", "last_run_at", "created")} | {
        "source_id": it["source_id"], "target_id": it["target_id"],
        "source_name": src[0]["name"] if src else "", "target_name": tgt[0]["name"] if tgt else "",
        "candidates": sum(1 for _ in store.items(it["id"]))}


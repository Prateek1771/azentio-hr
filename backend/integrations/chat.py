"""The chat that drives integration setup. Every change the agent makes goes through a tool here."""
import re
from typing import Any

import llm
import logging_setup as ls
from . import service, store
from .agent import RUN_REQUEST, AgentUnavailable, SimulatedAgent, TurnContext, get_agent
from .engine import AlreadyRunning, run_integration
from .run_log import LOG_SECTIONS
from .expr import EMAIL_PRESETS, FUNCTIONS
from .store import SetupError

log = ls.get("agent")
_STATUS_LABELS = {s: s.replace("_", " ").capitalize() for s in store.CANDIDATE_STATUSES}
_NEGATED = re.compile(r"\b(not|don't|do not|never|no)\b")
# These change what happens to real people, so HR's own words must ask for them. The model's
# judgement isn't enough, even if a message tries to talk it into calling them.
_HUMAN_ONLY = {
    "approve_mapping": re.compile(r"\b(approve\w*|looks good|lgtm|confirm|yes|ok|okay)\b"),
    "approve_offer_letter": re.compile(r"\b(approve\w*|looks good|lgtm|confirm|yes|ok|okay)\b"),
    "activate": re.compile(r"\b(activate|go live|resume|start it|turn it on)\b"),
    "run_now": RUN_REQUEST,
    "grant_access": re.compile(r"\b(give|grant|add|invite|create|provision|access|notify|request|raise|onboard)\b"),
}


def _tool(name: str, description: str, properties: dict | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties or {}}}}


_TEXT, _FLAG = {"type": "string"}, {"type": "boolean"}
_LIST = {"type": "array", "items": _TEXT}
TOOLS = [
    _tool("set_candidates", "Choose which candidates this integration picks up, and how to treat existing employees.",
          {"statuses": {"type": "array", "items": {"enum": list(store.CANDIDATE_STATUSES)}},
           "departments": {**_LIST, "description": "Names. An empty list means all."}, "duplicate_policy": {"enum": ["update", "skip"]}}),
    _tool("suggest_mapping", "Propose a new field mapping, replacing the current one."),
    _tool("set_mapping_field", "Set how one target field is filled. Use transform 'expression' with an expression over source "
          "field names, e.g. lower(first_name) + \".\" + slug(last_name). Functions: " + ", ".join(FUNCTIONS) + ".",
          {"target_field": {**_TEXT, "description": "Target field key."}, "source_field": {**_TEXT, "description": "Source field key."},
           "default_value": _TEXT, "transform": {"enum": ["none", "trim", "lowercase", "uppercase", "first_word", "after_first_word", "expression"]},
           "expression": _TEXT}),
    _tool("generate_unique_email", "Fill a target email field with a generated company address that is unique in the target "
          "(john.doe, then john.doe2 on a clash).",
          {"target_field": {**_TEXT, "description": "Usually work_email."}, "pattern": {"enum": list(EMAIL_PRESETS)},
           "expression": {**_TEXT, "description": "Custom local-part expression instead of a pattern."},
           "domain": {**_TEXT, "description": "e.g. azentio.com"}}),
    _tool("remove_mapping_field", "Stop filling a target field.", {"target_field": _TEXT}),
    _tool("approve_mapping", "Approve the field mapping. Only when HR clearly says so."),
    _tool("set_notifications", "Change who gets notified about runs, and for which events.",
          {"add_members": {**_LIST, "description": "Workspace member (owner) names."}, "remove_members": _LIST,
           "add_emails": {**_LIST, "description": "Any email addresses."}, "remove_emails": _LIST,
           "on_created": _FLAG, "on_success": _FLAG, "on_failure": _FLAG}),
    _tool("set_document_verification", "Turn document verification (Gmail request + AI checks) on or off.", {"enabled": _FLAG}),
    _tool("set_role_documents", "Choose which documents candidates in one role are asked for. Leave documents out to use what the "
          "Requirement rules need for that role. role '*' means every role.",
          {"role": _TEXT, "documents": {**_LIST, "description": "Document type keys or names."}}),
    _tool("set_offer_letter", "Turn the offer letter on or off.", {"enabled": _FLAG}),
    _tool("set_app_access", "Choose the app access new joiners get after their employee record exists. "
          "use_suggested follows the Requirement rules; none gives no app access.",
          {"use_suggested": _FLAG, "none": _FLAG}),
    _tool("add_access_rule", "Add one app access item for matching new joiners, like a Requirement rule.",
          {"app": {**_TEXT, "description": "Slack, Jira, ServiceNow or Google Workspace."}, "action": _TEXT,
           "resource": {**_TEXT, "description": "Channel, project, group or request name."},
           "mode": {"enum": ["direct", "request"], "description": "direct adds them; request asks the channel or project admin."},
           "roles": _LIST, "departments": _LIST}),
    _tool("remove_access_rule", "Remove app access items for an app (and optionally one resource).", {"app": _TEXT, "resource": _TEXT}),
    _tool("grant_access", "Give one candidate app access now, e.g. add them to a Slack channel or Jira project, create an account, "
          "notify a channel/project admin or raise a ServiceNow request. Only when HR asks.",
          {"candidate": {**_TEXT, "description": "Candidate name or email."}, "app": _TEXT, "action": _TEXT, "resource": _TEXT,
           "mode": {"enum": ["direct", "request"]}}),
    _tool("redraft_offer_letter", "Write a new offer letter format."),
    _tool("update_offer_letter", "Replace the offer letter format. It then needs approving again.", {"content": _TEXT}),
    _tool("approve_offer_letter", "Approve the offer letter format. Only when HR clearly says so."),
    _tool("set_schedule", "Set when this integration runs.",
          {"kind": {"enum": ["manual", "interval", "daily"]}, "interval_minutes": {"type": "integer", "minimum": 15},
           "time": {**_TEXT, "description": "24-hour HH:MM, for daily."}}),
    _tool("update_details", "Change the name, description or connectors.",
          {"name": _TEXT, "description": _TEXT, "source": _TEXT, "target": _TEXT}),
    _tool("set_log_exclusions", "Keep fields or whole sections out of this integration's log, or bring them back. "
          "Fields are source or target field keys. The audit log can't be changed.",
          {"add_fields": _LIST, "remove_fields": _LIST, "add_sections": {"type": "array", "items": {"enum": list(LOG_SECTIONS)}},
           "remove_sections": {"type": "array", "items": {"enum": list(LOG_SECTIONS)}},
           "clear": {**_FLAG, "description": "Log everything again."}}),
    _tool("activate", "Activate or resume this integration. Only when HR clearly says so."),
    _tool("pause", "Pause this integration."),
    _tool("run_now", "Run this integration immediately. Only when HR clearly says so."),
]


class Tools:
    """Runs what the agent asks for. Each call validates and saves through the service layer."""

    def __init__(self, iid: str, message: str, timezone: str | None):
        self.id, self.message, self.timezone, self.used = iid, message.lower(), timezone, 0

    async def execute(self, name: str, args: dict[str, Any]) -> dict:
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            ls.log_event(log, "agent.tool", tool=name, integration=self.id, result="unknown")
            return {"ok": False, "error": f"There's no {name} action."}
        gate = _HUMAN_ONLY.get(name)
        if gate and (not gate.search(self.message) or _NEGATED.search(self.message)):
            ls.log_event(log, "agent.tool", tool=name, integration=self.id, result="needs_hr")
            return {"ok": False, "error": "That needs HR to ask for it directly. It wasn't done."}
        try:
            summary = await handler(**{k: v for k, v in args.items() if v is not None})
        except (SetupError, LookupError, AlreadyRunning) as exc:
            ls.log_event(log, "agent.tool", tool=name, integration=self.id, result="refused")
            return {"ok": False, "error": str(exc)}
        except (TypeError, ValueError) as exc:
            ls.log_event(log, "agent.tool", tool=name, integration=self.id, result="bad_arguments")
            return {"ok": False, "error": f"That couldn't be applied: {exc}"}
        ls.log_event(log, "agent.tool", tool=name, integration=self.id, result="ok")
        self.used += 1
        return {"ok": True, "summary": summary}

    def _it(self) -> dict:
        return store.get(self.id)

    async def _do_set_candidates(self, statuses=None, departments=None, duplicate_policy=None):
        cur = self._it()
        chosen = statuses or cur["source_filter"].get("statuses") or []
        depts = cur["source_filter"].get("departments", []) if departments is None else departments
        service.save_records(self.id, chosen, depts, duplicate_policy or cur["duplicate_policy"], speak=False)
        labels = ", ".join(_STATUS_LABELS.get(s, s) for s in chosen).lower()
        return f"I'll pick up candidates who are {labels} in {', '.join(depts) if depts else 'all departments'}."

    async def _do_suggest_mapping(self):
        rules = await service.suggest_mapping(self.id)
        service.save_mapping(self.id, rules, speak=False)
        return f"I've proposed a mapping for {len(rules)} fields."

    async def _do_set_mapping_field(self, target_field, source_field=None, default_value=None, transform=None, expression=None):
        if expression or transform == "expression":
            rules = [r for r in self._it()["mapping"] if r["target_field"] != target_field]
            rules.append({"target_field": target_field, "transform": "expression", "expression": expression or "", "origin": "hr"})
            service.save_mapping(self.id, rules, speak=False)
            return f"{target_field} is now calculated as {expression}."
        return await self._set_plain_field(target_field, source_field, default_value, transform)

    async def _set_plain_field(self, target_field, source_field=None, default_value=None, transform=None):
        it = self._it()
        rules = {r["target_field"]: r for r in it["mapping"]}
        rule = rules.get(target_field, {"target_field": target_field, "transform": "none"})
        rules[target_field] = {**rule, "source_field": source_field or rule.get("source_field"),
                               "default_value": default_value or rule.get("default_value"),
                               "transform": transform or (rule.get("transform", "none") if rule.get("transform") not in ("expression", "unique_email") else "none"),
                               "origin": "hr", "confidence": None, "rationale": None}
        service.save_mapping(self.id, list(rules.values()), speak=False)
        _, t_fields, _, _ = store.schemas(it)
        label = next((f.label for f in t_fields if f.key == target_field), target_field)
        return f"{label} now defaults to {default_value}." if default_value else f"{label} now comes from {source_field}."

    async def _do_generate_unique_email(self, target_field="work_email", pattern=None, expression=None, domain=None):
        expr = expression or EMAIL_PRESETS.get(pattern or "first.last", EMAIL_PRESETS["first.last"])
        domain = (domain or "azentio.com").strip().lower().lstrip("@")
        rules = [r for r in self._it()["mapping"] if r["target_field"] != target_field]
        rules.append({"target_field": target_field, "transform": "unique_email", "expression": expr, "email_domain": domain, "origin": "hr"})
        service.save_mapping(self.id, rules, speak=False)
        sample = service.test_expression(self.id, expr, "unique_email", domain)[:1]
        eg = f" For example {sample[0]['candidate']} gets {sample[0]['value']}." if sample and sample[0]["value"] else ""
        return f"{target_field} is now generated as a unique @{domain} address.{eg}"

    async def _do_set_role_documents(self, role, documents=None):
        it = service.save_role_documents(self.id, role, documents, speak=False)
        key = "*" if role.strip().lower() in ("all", "all roles", "any", "any role", "everyone", "*") else \
            next((k for k in it["role_documents"] if k.lower() == role.strip().lower()), role)
        names = {d["key"]: d["name"] for d in service.document_options(it, key)}
        docs = it["role_documents"].get(key, [])
        return f"{'Every role' if key == '*' else key} will be asked for {len(docs)} document(s): {', '.join(names.get(d, d) for d in docs)}."

    def _app(self, name):
        apps = service.apps()
        hit = next((a for a in apps if a["name"].lower() == (name or "").lower() or a["key"] == (name or "").lower().replace(" ", "_")), None)
        if not hit:
            raise SetupError(f"There's no app called {name}. Apps: {', '.join(a['name'] for a in apps)}")
        return hit

    def _action(self, app, action, resource):
        keys = [a["key"] for a in app["actions"]]
        act = (action or "").lower().replace(" ", "_")
        if act in keys:
            return act
        if any(w in act for w in ("account", "invite", "create", "mailbox")) and "create_account" in keys:
            return "create_account"
        if resource:
            return next(k for k in keys if k != "create_account")
        return keys[0]

    async def _do_set_app_access(self, use_suggested=None, none=None):
        it = self._it()
        rules = [] if none else service.suggested_access(it)
        service.save_access_rules(self.id, rules, speak=False, actor="HR (via agent)")
        if not rules:
            return "New joiners won't get any app access from this integration."
        return "New joiners will get: " + "; ".join(_describe_rule(r) for r in rules) + "."

    async def _do_add_access_rule(self, app, action=None, resource="", mode="direct", roles=None, departments=None):
        a = self._app(app)
        rule = {"app_id": a["id"], "action": self._action(a, action, resource), "resource": resource or "", "mode": mode,
                "match": {"role": roles or [], "department": departments or []}}
        it = self._it()
        service.save_access_rules(self.id, [*it["access_rules"], rule], speak=False, actor="HR (via agent)")
        return f"Added: {_describe_rule({**rule, 'app': a['name']})}."

    async def _do_remove_access_rule(self, app, resource=None):
        a = self._app(app)
        it = self._it()
        keep = [r for r in it["access_rules"] if not (r["app_id"] == a["id"] and (not resource or r["resource"].lstrip("#") == resource.lstrip("#")))]
        if len(keep) == len(it["access_rules"]):
            raise SetupError(f"There's no {a['name']} access item like that.")
        service.save_access_rules(self.id, keep, speak=False, actor="HR (via agent)")
        return f"Removed {len(it['access_rules']) - len(keep)} {a['name']} access item(s)."

    async def _do_grant_access(self, candidate, app, action=None, resource="", mode="direct"):
        item = service.find_item(self.id, candidate)
        a = self._app(app)
        out = service.grant_access(self.id, item["id"], a["id"], self._action(a, action, resource), resource or "", mode, actor="HR (via agent)")
        r = out["result"]
        return f"{a['name']}: {r['message']} (ref {r['reference']})." + (" It was already done before." if r.get("repeat") else "")

    async def _do_remove_mapping_field(self, target_field):
        service.save_mapping(self.id, [r for r in self._it()["mapping"] if r["target_field"] != target_field], speak=False)
        return f"I've removed the mapping for {target_field}."

    async def _do_approve_mapping(self):
        service.approve_mapping(self.id, speak=False)
        return "Mapping approved."

    async def _do_set_notifications(self, add_members=None, remove_members=None, add_emails=None, remove_emails=None,
                                    on_created=None, on_success=None, on_failure=None):
        it = self._it()
        members = service.owners()
        chosen = set(it["notify_members"])
        for name in add_members or []:
            match = next((e for n, e in members.items() if n.lower() == name.lower() or n.split()[0].lower() == name.lower()), None)
            if not match:
                raise SetupError(f"{name} isn't in this workspace.")
            chosen.add(match)
        for name in remove_members or []:
            chosen.discard(members.get(name))
        removed = {e.lower() for e in remove_emails or []}
        emails = {e for e in it["notify_emails"] if e not in removed} | set(add_emails or [])
        c = it["notify_on_created"] if on_created is None else on_created
        s = it["notify_on_success"] if on_success is None else on_success
        f = it["notify_on_failure"] if on_failure is None else on_failure
        service.save_notifications(self.id, sorted(chosen), sorted(emails), c, s, f, speak=False)
        events = [lbl for lbl, on in (("a run is created", c), ("a run succeeds", s), ("a run fails", f)) if on]
        return f"I'll notify {len(chosen) + len(emails)} recipient(s) when {' or '.join(events) or 'nothing happens'}."

    async def _do_set_document_verification(self, enabled):
        service.save_documents(self.id, bool(enabled), speak=False)
        return "Document verification is on — candidates get a Gmail request and replies are checked by AI." if enabled else "Document verification is off."

    async def _do_set_offer_letter(self, enabled):
        await service.save_offer_choice(self.id, bool(enabled), speak=False)
        return "I'll send an offer letter." if enabled else "No offer letter will be sent."

    async def _do_redraft_offer_letter(self):
        await service.generate_offer(self.id, speak=False)
        return "I've written a new draft, which needs your approval."

    async def _do_update_offer_letter(self, content):
        service.save_offer_template(self.id, content, speak=False)
        return "I've updated the format, which needs your approval."

    async def _do_approve_offer_letter(self):
        service.decide_offer(self.id, True, speak=False, actor="HR (via agent)")
        return "Offer letter format approved."

    async def _do_set_schedule(self, kind, interval_minutes=None, time=None):
        service.save_schedule(self.id, kind, interval_minutes, time, self.timezone or "UTC", speak=False)
        if kind == "interval":
            m = int(interval_minutes or 0)
            return ("It will run every hour." if m == 60 else f"It will run every {m // 60} hours.") if m % 60 == 0 else f"It will run every {m} minutes."
        return f"It will run daily at {time}." if kind == "daily" else "It will run only when you ask."

    async def _do_update_details(self, name=None, description=None, source=None, target=None):
        known = service.connectors_by_name()
        for v in (source, target):
            if v and v.lower() not in known:
                raise SetupError(f"There's no connector called {v}.")
        service.save_basics(self.id, name, description, known[source.lower()]["id"] if source else None,
                            known[target.lower()]["id"] if target else None, speak=False)
        return "I've updated the integration's details."

    async def _do_set_log_exclusions(self, add_fields=None, remove_fields=None, add_sections=None, remove_sections=None, clear=False):
        cur = self._it()["log_exclusions"]
        fields = set() if clear else set(cur.get("fields", []))
        sections = set() if clear else set(cur.get("sections", []))
        fields = (fields | set(add_fields or [])) - set(remove_fields or [])
        sections = (sections | set(add_sections or [])) - set(remove_sections or [])
        service.save_log_settings(self.id, sorted(fields), sorted(sections), actor="HR (via agent)")
        hidden = [*sorted(sections), *sorted(fields)]
        return "This integration's log now records everything." if not hidden else \
            f"This integration's log now leaves out: {', '.join(hidden)}. The audit log stays complete."

    async def _do_activate(self):
        service.activate(self.id, actor="HR (via agent)")
        return "The integration is active."

    async def _do_pause(self):
        service.pause(self.id, actor="HR (via agent)")
        return "The integration is paused."

    async def _do_run_now(self):
        if self._it()["status"] != "active":
            raise SetupError("Activate this integration before running it.")
        run = await run_integration(self.id, "manual", "HR (via agent)")
        c = run["counts"]
        return f"Run finished: {c['enrolled']} new, {c['advanced']} advanced, {c['waiting']} waiting, {c['failed']} failed."


def next_prompt(it: dict) -> str:
    """The agent's next question, worded from where this integration actually is."""
    if it["status"] == "active":
        return "It's active. Ask me to change anything, or say run now."
    step = it["setup_step"]
    if step == "mapping":
        missing = store.missing_required(it)
        count = f"I've mapped {len(it['mapping'])} fields."
        if missing:
            return f"{count} {missing[0].label} is required, but the source doesn't have it. What value should I use?"
        if it["mapping_approved_at"] is None:
            return f"{count} Check them below, tell me what to change, or say approve."
    if step == "offer" and it["offer_enabled"]:
        return "Here's the format. Tell me what to change, or say approve."
    if step == "review":
        p = store.problems(it)
        if p:
            return f"Before activating: {' '.join(p)}"
    return store.question(it)


def _state(it: dict) -> dict:
    return {"name": it["name"], "status": it["status"], "candidate_filter": it["source_filter"], "duplicate_policy": it["duplicate_policy"],
            "mapping": {r["target_field"]: r.get("source_field") or f"default: {r.get('default_value')}" for r in it["mapping"]},
            "mapping_approved": it["mapping_approved_at"] is not None, "missing_required": [f.key for f in store.missing_required(it)],
            "notify_members": it["notify_members"], "notify_emails": it["notify_emails"], "notify_on_created": it["notify_on_created"],
            "notify_on_success": it["notify_on_success"], "notify_on_failure": it["notify_on_failure"],
            "doc_verification_enabled": it["doc_verification_enabled"], "offer_enabled": it["offer_enabled"],
            "offer_template": it["offer_template"], "offer_template_status": it["offer_template_status"],
            "schedule": it["schedule"], "log_exclusions": it["log_exclusions"], "problems": store.problems(it),
            "roles": service.roles(it), "role_documents": it["role_documents"],
            "access_rules": [_describe_rule(r) for r in it["access_rules"]], "access_saved": it["access_saved"],
            "apps": {a["name"]: [x["key"] for x in a["actions"]] for a in service.apps()},
            "candidates": [i["name"] for i in store.items(it["id"])][:50],
            "mapping_rules": [{k: r.get(k) for k in ("target_field", "transform", "expression", "email_domain") if r.get(k)}
                              for r in it["mapping"] if r.get("transform") in ("expression", "unique_email")]}


def _describe_rule(r: dict) -> str:
    m = r.get("match") or {}
    who = " and ".join(", ".join(v) if isinstance(v, list) else str(v) for k, v in m.items() if v and k in ("role", "department", "country", "location"))
    what = r["action"].replace("_", " ") + (f" {r['resource']}" if r.get("resource") else "")
    how = " (ask the admin)" if r.get("mode") == "request" else ""
    return f"{r.get('app', '')} {what}{how}" + (f" for {who}" if who else "")


async def run_chat_turn(iid: str, message: str, timezone: str | None, opening: bool = False) -> dict:
    agent = get_agent()
    ls.log_event(log, "agent.turn", integration=iid, agent=agent.name, chars=len(message))
    before = store.get(iid)
    if not before:
        raise LookupError("Integration not found")
    if message.strip().lower().rstrip(".!") in ("retry", "try again", "again", "redo"):
        # "retry" means: do my last request again (e.g. after the AI was unavailable), not a new instruction.
        prev = next((m["content"] for m in reversed(service.messages(iid)) if m["role"] == "user"
                     and m["content"].strip().lower().rstrip(".!") not in ("retry", "try again", "again", "redo")), None)
        if prev or opening is False:
            message = prev or before.get("description") or message
    s_fields, t_fields, _, _ = store.schemas(before)
    ctx = TurnContext(message=message, step=before["setup_step"], state=_state(before), members=list(service.owners()),
                      departments=service.departments(), statuses=_STATUS_LABELS, source_fields=s_fields, target_fields=t_fields,
                      history=[{"role": "assistant" if m["role"] == "agent" else "user", "content": m["content"]} for m in service.messages(iid)],
                      tools=TOOLS)
    tools = Tools(iid, message, timezone)
    notice = ""
    try:
        reply = await agent.run_turn(ctx, tools.execute)
    except AgentUnavailable:
        ls.log_event(log, "agent.fallback", integration=iid, level_used="rules")
        why = llm.unavailable_reason() or "The free AI model didn't respond"
        notice = f"{why}, so I used simple rules for this one. "
        reply = await SimulatedAgent().run_turn(ctx, tools.execute)
    after = store.get(iid)
    if after["setup_step"] == "mapping" and not after["mapping"]:  # reaching mapping is the cue to propose one
        await tools.execute("suggest_mapping", {})
        after = store.get(iid)
    paused = store.settle(after)
    if paused:
        reply = f"{reply} {paused}".strip()
        after = store.get(iid)
    if not reply and tools.used == 0 and not opening:
        reply = "I didn't catch how to apply that."
    said = f"{notice}{reply}".strip()
    moved = after["setup_step"] != before["setup_step"] or tools.used == 0
    prompt = next_prompt(after)
    if (moved or after["setup_step"] in ("mapping", "offer")) and prompt not in said:
        said = f"{said} {prompt}".strip()
    service.say(iid, "user", message)
    service.say(iid, "agent", said)
    return service.detail(iid)


async def start_from_chat(message: str, timezone: str | None) -> dict:
    """Create an integration from a sentence, then act on anything else that sentence asked for."""
    conns = service.connectors_by_name()
    described = await get_agent().describe_new(message, [c["name"] for c in conns.values()])

    def pick(wanted: str | None, role: str) -> str:
        options = [c for c in conns.values() if c["role"] == role]
        if not options:
            raise SetupError("This workspace needs a source and a target connector first.")
        return conns.get((wanted or "").lower(), {}).get("id") if (wanted or "").lower() in conns and conns[wanted.lower()]["role"] == role \
            else next((c["id"] for c in options if c["name"] == "Candidate Portal"), options[0]["id"])

    taken = {it["name"].lower() for it in store.all_integrations()}
    name = (described.name or "").strip()[:110] or "New integration"
    candidate, n = name, 2
    while candidate.lower() in taken:
        candidate, n = f"{name} {n}", n + 1
    it = service.create(candidate, described.description or message[:500], pick(described.source, "source"), pick(described.target, "target"))
    return await run_chat_turn(it["id"], message, timezone, opening=True)

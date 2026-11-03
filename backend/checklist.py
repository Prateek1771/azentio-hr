"""Rules engine: which checklist items apply to an employee, why, when they're due, and what's blocked."""
import json
from datetime import date, timedelta

import records
from graph import q1, event

DONE = {"Accepted", "Completed", "Acknowledged"}
INITIAL = {"document": "Not Submitted", "task": "Pending", "ack": "Pending"}


def grade_num(grade: str) -> int:
    digits = "".join(ch for ch in str(grade) if ch.isdigit())
    return int(digits) if digits else 0


def match_rule(rule: dict, emp: dict, country: str) -> tuple[bool, str]:
    """Return (applies, human-readable criteria that matched)."""
    m = rule.get("match") or {}
    hits = []
    for key, emp_val in (("department", emp["department"]), ("role", emp["role"]),
                         ("location", emp["location"]), ("country", country)):
        if key in m:
            allowed = m[key] if isinstance(m[key], list) else [m[key]]
            if emp_val not in allowed:
                return False, ""
            hits.append(f"{key} = {emp_val}")
    g = grade_num(emp["grade"])
    if "grade_min" in m:
        if g < m["grade_min"]:
            return False, ""
        hits.append(f"grade {emp['grade']} ≥ G{m['grade_min']}")
    if "grade_max" in m:
        if g > m["grade_max"]:
            return False, ""
        hits.append(f"grade {emp['grade']} ≤ G{m['grade_max']}")
    return True, ", ".join(hits) if hits else "applies to all employees"


def build_items(emp: dict, rules: list[dict], doc_types: dict, locations: dict, owners: dict) -> list[dict]:
    """Pure: compute checklist items (no ids, no statuses) for one employee."""
    country = locations.get(emp["location"], "")
    joining = date.fromisoformat(emp["joining_date"])
    items = []
    for r in rules:
        ok, why = match_rule(r, emp, country)
        if not ok:
            continue
        owner = owners.get(r["category"], {})
        items.append({
            "key": r["item"],
            "name": r.get("name") or doc_types.get(r["item"], {}).get("name", r["item"]),
            "kind": r["kind"],
            "category": r["category"],
            "rule_id": r["id"],
            "reason": f"{r['description']} — rule {r['id']} ({why})",
            "policy_ref": r.get("policy_ref", ""),
            "due": (joining + timedelta(days=int(r.get("offset_days", 0)))).isoformat(),
            "auto": r.get("auto", ""),
            "depends": list(r.get("depends_on", [])),
            "owner_name": owner.get("name", ""),
            "owner_email": owner.get("email", ""),
        })
    scope = set(emp.get("doc_scope") or [])
    if scope:  # an integration asked this role for a limited document set
        items = [i for i in items if i["kind"] != "document" or i["key"] in scope]
    keys = {i["key"] for i in items}
    for i in items:  # a dependency that doesn't apply to this employee is simply dropped
        i["depends"] = [d for d in i["depends"] if d in keys]
    return items


def view_state(item: dict, by_key: dict, today: date | None = None) -> str:
    today = today or date.today()
    if item["status"] in DONE:
        return "completed"
    if any(by_key[d]["status"] not in DONE for d in item.get("depends", []) if d in by_key):
        return "blocked"
    if item.get("due") and date.fromisoformat(item["due"]) < today:
        return "overdue"
    return "pending"


# ---------- graph-backed ----------

def config(key: str):
    """Engine config comes from the active rule-set version; anything a version doesn't hold stays in the graph."""
    from policy import store
    if key in store.CONFIG_KEYS:
        act = store.ensure_active()
        if act:
            return (act["config"] or {}).get(key)
    row = q1("MATCH (c:Config {key:$k}) RETURN c.value AS v", k=key)
    return json.loads(row["v"]) if row else None


def load_rules() -> list[dict]:
    """The active rule-set version's rules (ADR 0009). Rules only reach joiners by publishing a version."""
    from policy import store
    act = store.ensure_active()
    return store.rules_of(act["id"]) if act else []


def category_owners() -> dict:
    import owners
    return owners.by_category()


def employee_items(eid: str) -> list[dict]:
    return records.items(eid)


def regenerate(eid: str, reason: str | None = None):
    """Bring one joiner's checklist in line with the active rule set: safe replay, nothing is ever deleted."""
    from policy.service import replay_one
    replay_one(eid, reason)


def recompute(eid: str):
    """Advance automatic tasks whose dependencies are all done (e.g. HR record -> raise laptop request)."""
    changed = True
    while changed:
        changed = False
        items = employee_items(eid)
        by_key = {i["key"]: i for i in items}
        for it in items:
            if it["kind"] != "task" or it["status"] in DONE:
                continue
            if any(by_key[d]["status"] not in DONE for d in it.get("depends", []) if d in by_key):
                continue
            if it.get("auto") == "complete":
                records.update_item(it["id"], status="Completed")
                event(eid, "auto", f"'{it['name']}' completed automatically — all prerequisites met")
                if it["key"] == "hr_record":  # onboarding → HR Portal target
                    from connectors.hr_portal import push_employee
                    push_employee(eid)
                changed = True
            elif it["status"] == "Pending":
                records.update_item(it["id"], status="Requested")
                event(eid, "auto", f"Request raised to {it['owner_name'] or it['category']}: '{it['name']}'")
                changed = True

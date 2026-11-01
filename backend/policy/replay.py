"""Safe in-flight replay: bring one joiner's checklist in line with a rule-set version without losing anything.

- added   -> new item (or a previously cancelled/superseded item reactivated, keeping its documents and status)
- kept    -> due / owner / reason / dependencies refreshed; status and documents never touched
- removed -> done or has documents: kept as evidence (superseded_by_version); otherwise cancelled (cancelled_by_version)
Nothing is ever deleted. `plan_diff` is pure, so the impact preview and the publish use the exact same logic.
"""
from checklist import DONE, INITIAL, view_state

PLAN_FIELDS = ("name", "kind", "category", "rule_id", "reason", "policy_ref", "due", "auto", "depends", "owner_name",
               "owner_email")


def _closed(i: dict) -> bool:
    return bool(i.get("cancelled_by_version") or i.get("superseded_by_version"))


def plan_diff(current: list[dict], plan: list[dict], has_docs: set[str] | None = None) -> dict:
    """current: every item row for the joiner (including closed ones); plan: build_items() output.
    Returns {added, reactivated, kept, cancel, keep_evidence, due_moved, newly_blocked} (lists of item dicts)."""
    has_docs = has_docs or set()
    cur = {i["key"]: i for i in current}
    want = {p["key"]: p for p in plan}
    out = {"added": [], "reactivated": [], "kept": [], "cancel": [], "keep_evidence": [], "due_moved": [], "newly_blocked": []}
    for key, p in want.items():
        old = cur.get(key)
        if old is None:
            out["added"].append(p)
        elif _closed(old):
            out["reactivated"].append({**p, "id": old["id"], "status": old["status"] if old["status"] != "Cancelled"
                                       else INITIAL[p["kind"]], "was": old})
        else:
            changes = {f: p[f] for f in PLAN_FIELDS if old.get(f) != p[f]}
            if changes:
                out["kept"].append({"id": old["id"], "key": key, "name": p["name"], "changes": changes})
            if old.get("due") != p["due"]:
                out["due_moved"].append({"key": key, "name": p["name"], "from": old.get("due"), "to": p["due"]})
    for key, old in cur.items():
        if key in want or _closed(old):
            continue
        if old["status"] in DONE or old["id"] in has_docs:
            out["keep_evidence"].append(old)
        else:
            out["cancel"].append(old)

    # Blocked state is judged with the joiner's current statuses, before and after.
    live_before = {k: i for k, i in cur.items() if not _closed(i)}
    after = {k: {**p, "status": (live_before.get(k) or {}).get("status") or
                 next((r["status"] for r in out["reactivated"] if r["key"] == k), INITIAL[p["kind"]])}
             for k, p in want.items()}
    for k, i in after.items():
        was = live_before.get(k)
        before_state = view_state(was, live_before) if was else None
        if view_state(i, after) == "blocked" and before_state != "blocked":
            out["newly_blocked"].append(k)
    return out


def counts(d: dict) -> dict:
    return {"added": len(d["added"]) + len(d["reactivated"]), "cancelled": len(d["cancel"]),
            "kept_as_evidence": len(d["keep_evidence"]), "due_moved": len(d["due_moved"]),
            "newly_blocked": len(d["newly_blocked"])}


def changed(d: dict) -> bool:
    return any(d[k] for k in ("added", "reactivated", "kept", "cancel", "keep_evidence"))


def apply(eid: str, d: dict, version_id: int, conn) -> None:
    """Write the diff for one joiner inside the caller's transaction. Caller refreshes the graph projection after."""
    import records
    from graph import new_id
    for p in d["added"]:
        records.save_item({**p, "id": new_id("it"), "employee_id": eid, "status": INITIAL[p["kind"]],
                           "rule_version_id": version_id}, conn=conn)
    for p in d["reactivated"]:
        fields = {f: p[f] for f in PLAN_FIELDS}
        conn.execute("UPDATE items SET cancelled_by_version=NULL, superseded_by_version=NULL WHERE id=%s", (p["id"],))
        records.update_item(p["id"], conn=conn, status=p["status"], rule_version_id=version_id, **fields)
    for k in d["kept"]:
        records.update_item(k["id"], conn=conn, rule_version_id=version_id, **k["changes"])
    for old in d["cancel"]:
        records.update_item(old["id"], conn=conn, status="Cancelled", cancelled_by_version=version_id)
    for old in d["keep_evidence"]:
        records.update_item(old["id"], conn=conn, superseded_by_version=version_id)


def describe(d: dict) -> list[str]:
    """Human-readable lines for the joiner's activity feed."""
    lines = [f"Added '{p['name']}'" for p in d["added"]] + [f"Restored '{p['name']}'" for p in d["reactivated"]]
    lines += [f"'{m['name']}' due date moved {m['from']} → {m['to']}" for m in d["due_moved"]]
    lines += [f"Cancelled '{o['name']}' (no longer required)" for o in d["cancel"]]
    lines += [f"'{o['name']}' no longer required; kept as evidence" for o in d["keep_evidence"]]
    return lines

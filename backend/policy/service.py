"""Policy workflow: draft -> preview -> submit -> approve (x1, or x2 for compliance) -> publish job -> active.

Humans act only at the gates (approve/reject, near-start apply/skip). Publishing runs as a job keyed on the version,
so a retry can never apply it twice; a dead publish job opens an `ops` exception through Phase 0's dead-job hook.
"""
import os
from datetime import date, timedelta

import audit
import checklist
import records
import work_queue
from graph import event
from policy import replay, store
from policy.store import PolicyError
from shared import db, jobs

NEAR_START_DAYS = int(os.getenv("NEAR_START_DAYS", "3"))
IN_FLIGHT_SINCE_DAYS = 30
BATCH = 50


# ---------- shared helpers ----------

def owners() -> dict:
    return checklist.category_owners()


def in_flight(conn=None) -> list[dict]:
    """Joiners whose onboarding is still live: not archived, joined within the last 30 days or joining later."""
    since = date.today() - timedelta(days=IN_FLIGHT_SINCE_DAYS)
    rows = db.fetch("SELECT * FROM employees WHERE status='active' AND joining_date >= %s ORDER BY joining_date, id",
                    (since,), conn=conn)
    return [records.employee_dict(r) for r in rows]


def docs_by_item(item_ids: list[str]) -> set[str]:
    """Items that have at least one document: those are kept as evidence, never cancelled."""
    from documents import store as docs
    return docs.has_docs(item_ids)


def plan_for(emp: dict, rules: list[dict], config: dict, own: dict) -> list[dict]:
    return checklist.build_items(emp, rules, config.get("doc_types") or {}, config.get("locations") or {}, own)


def near_start(emp: dict, today: date | None = None) -> bool:
    return (date.fromisoformat(emp["joining_date"]) - (today or date.today())).days <= NEAR_START_DAYS


def overall(items: list[dict]) -> str:
    by_key = {i["key"]: i for i in items}
    states = [checklist.view_state(i, by_key) for i in items]
    done = sum(s == "completed" for s in states)
    if items and done == len(items):
        return "ready"
    if "overdue" in states:
        return "overdue"
    if "blocked" in states and "pending" not in states:
        return "blocked"
    return "in_progress"


def employee_diff(emp: dict, rules: list[dict], config: dict, own: dict, conn=None) -> dict:
    current = records.items(emp["id"], include_closed=True, conn=conn)
    return replay.plan_diff(current, plan_for(emp, rules, config, own), docs_by_item([i["id"] for i in current]))


# ---------- preview ----------

def preview(vid: int, filters: dict | None = None) -> dict:
    """What publishing `vid` would do to each in-flight joiner. Same plan_diff the publish job uses."""
    f = {k: v for k, v in (filters or {}).items() if v}
    rules, config = store.load_version(vid)
    own = owners()
    summary = {"joiners": 0, "added": 0, "cancelled": 0, "kept_as_evidence": 0, "due_moved": 0, "newly_blocked": 0,
               "near_start": 0}
    rows = []
    for emp in in_flight():
        if any(f.get(k) and emp.get(k) != f[k] for k in ("department", "role", "location")):
            continue
        if f.get("join_from") and emp["joining_date"] < f["join_from"] or f.get("join_to") and emp["joining_date"] > f["join_to"]:
            continue
        if f.get("status") and overall(records.items(emp["id"])) != f["status"]:
            continue
        d = employee_diff(emp, rules, config, own)
        if not replay.changed(d):
            continue
        c = replay.counts(d)
        ns = near_start(emp)
        summary["joiners"] += 1
        summary["near_start"] += ns
        for k in ("added", "cancelled", "kept_as_evidence", "due_moved", "newly_blocked"):
            summary[k] += c[k]
        rows.append({"employee_id": emp["id"], "name": emp["name"], "department": emp["department"], "role": emp["role"],
                     "location": emp["location"], "joining_date": emp["joining_date"], "near_start": ns, **c,
                     "detail": {"added": [{"key": p["key"], "name": p["name"], "due": p["due"]} for p in d["added"] + d["reactivated"]],
                                "cancelled": [{"key": o["key"], "name": o["name"]} for o in d["cancel"]],
                                "kept_as_evidence": [{"key": o["key"], "name": o["name"], "status": o["status"]} for o in d["keep_evidence"]],
                                "due_moved": d["due_moved"], "newly_blocked": d["newly_blocked"]}})
    act = store.active_version()
    changes = store.doc_policy_diff(dict(act["config"]) if act else {}, config)
    out = {"summary": summary, "rows": rows, "doc_policy_changes": changes, "backtest": None}
    if changes:
        from documents.route import backtest, policy_for
        out["backtest"] = {**backtest(decision_history(), policy_for(config)), "days": BACKTEST_DAYS}
    return out


BACKTEST_DAYS = 90


def decision_history(days: int = BACKTEST_DAYS) -> list[dict]:
    """Each document's first automatic decision in the window, plus its spot-check verdict if it had one.
    Read-only input for the pure backtest: no live document is ever re-decided."""
    return db.fetch("""SELECT DISTINCT ON (dd.document_id) dd.document_id, d.doc_type, dd.kind, dd.reason_code, dd.confidence, dd.policy_version_id,
                              (SELECT CASE s.kind WHEN 'spot_check_pass' THEN 'pass' ELSE 'fail' END FROM doc_decisions s
                               WHERE s.document_id = dd.document_id AND s.kind LIKE 'spot_check_%%' ORDER BY s.at DESC LIMIT 1) AS spot_check
                       FROM doc_decisions dd JOIN documents d ON d.id = dd.document_id
                       WHERE dd.decided_by IS NULL AND dd.kind NOT IN ('backfill', 'demo_seed') AND dd.at > now() - make_interval(days => %s)
                       ORDER BY dd.document_id, dd.at""", (days,))


# ---------- workflow ----------

def _request(vid: int, conn=None) -> dict | None:
    return db.fetch1("""SELECT * FROM approval_requests WHERE subject_type='rule_set_version' AND subject_id=%s
                        ORDER BY id DESC LIMIT 1""", (str(vid),), conn=conn)


def approvals_for(vid: int) -> list[dict]:
    req = _request(vid)
    if not req:
        return []
    return db.fetch("""SELECT u.email, u.name, a.decision, a.reason, a.ts FROM approvals a JOIN users u ON u.id=a.approver_id
                       WHERE a.request_id=%s ORDER BY a.ts""", (req["id"],))


def version_detail(vid: int) -> dict:
    v = store.version(vid)
    if not v:
        raise PolicyError(404, "Version not found")
    rules = store.rules_of(vid)
    base = store.active_version()
    against = v["based_on"] if v["state"] in ("active", "superseded") or not base else base["id"]
    d = store.diff(store.rules_of(against), rules) if against else {"added": rules, "removed": [], "changed": []}
    from documents.route import policy_for
    base_cfg = dict(store.version(against)["config"]) if against else {}
    d["doc_policy"] = store.doc_policy_diff(base_cfg, dict(v["config"]))
    return {**_public(v), "rules": rules, "diff": d, "diff_against": against, "approvals": approvals_for(vid),
            "doc_policy": policy_for(dict(v["config"]))}


def _public(v: dict) -> dict:
    return {k: v.get(k) for k in ("id", "number", "state", "title", "reason", "based_on", "rollback_of", "required_approvals",
                                   "preview", "author_id", "author_email", "created_at", "submitted_at", "published_at", "config")}


def required_approvals(vid: int, conn=None) -> int:
    act = store.active_version(conn=conn)
    old = store.rules_of(act["id"], conn=conn) if act else []
    new = store.rules_of(vid, conn=conn)
    doc_changes = store.doc_policy_diff(dict(act["config"]) if act else {}, dict(store.version(vid, conn=conn)["config"]))
    return 2 if store.touches_compliance(store.diff(old, new), old, new) or store.loosens_controls(doc_changes) else 1


def submit(vid: int, user: dict, title: str, reason: str) -> dict:
    v = store.version(vid)
    if not v or v["state"] != "draft":
        raise PolicyError(409, "Only an open draft can be submitted")
    if v["author_id"] != user["id"] and v["rollback_of"] is None:
        raise PolicyError(403, "Only the author can submit this draft")
    act = store.active_version()
    d = store.diff(store.rules_of(act["id"]) if act else [], store.rules_of(vid))
    doc_changes = store.doc_policy_diff(dict(act["config"]) if act else {}, dict(v["config"]))
    if not (d["added"] or d["removed"] or d["changed"] or doc_changes):
        raise PolicyError(422, "This draft doesn't change any rule or document policy")
    pv = preview(vid)
    need = required_approvals(vid)
    with db.tx() as c:
        c.execute("""UPDATE rule_set_versions SET state='pending_approval', title=%s, reason=%s, preview=%s,
                     required_approvals=%s, submitted_at=now() WHERE id=%s""",
                  (title, reason, db.Json({**pv["summary"], "backtest": pv["backtest"]}), need, vid))
        rid = c.execute("""INSERT INTO approval_requests (subject_type, subject_id, requested_by, required, reason)
                           VALUES ('rule_set_version', %s, %s, %s, %s) RETURNING id""",
                        (str(vid), user["id"], need, reason)).fetchone()["id"]
        work_queue.open_item("policy.approval", "rule_set_version", str(vid),
                             f"Rule set v{v['number']} needs {need} approval{'s' if need > 1 else ''}: {title}",
                             assignee_role="approver", detail={"version_id": vid, "request_id": rid, "required": need},
                             dedupe_key=f"policy.approval:{vid}", conn=c)
        audit.record_audit("policy.submitted", "rule_set_version", f"v{v['number']}",
                           f"Submitted rule set v{v['number']} for approval ({need} needed): {title}", reason=reason, conn=c)
    return version_detail(vid)


def approve(vid: int, user: dict, reason: str) -> dict:
    return _decide(vid, user, "approve", reason)


def reject(vid: int, user: dict, reason: str) -> dict:
    if not reason.strip():
        raise PolicyError(422, "A reason is required to reject")
    return _decide(vid, user, "reject", reason)


def _decide(vid: int, user: dict, decision: str, reason: str) -> dict:
    with db.tx() as c:
        v = c.execute("SELECT * FROM rule_set_versions WHERE id=%s FOR UPDATE", (vid,)).fetchone()
        if not v or v["state"] != "pending_approval":
            raise PolicyError(409, "This version isn't waiting for approval")
        req = _request(vid, conn=c)
        if req["requested_by"] == user["id"] or v["author_id"] == user["id"]:
            raise PolicyError(403, "You authored this change, so someone else has to approve it")
        if c.execute("SELECT 1 FROM approvals WHERE request_id=%s AND approver_id=%s", (req["id"], user["id"])).fetchone():
            raise PolicyError(409, "You've already decided on this version")
        c.execute("INSERT INTO approvals (request_id, approver_id, decision, reason) VALUES (%s, %s, %s, %s)",
                  (req["id"], user["id"], decision, reason))
        label = f"v{v['number']}"
        if decision == "reject":
            c.execute("UPDATE rule_set_versions SET state='rejected' WHERE id=%s", (vid,))
            c.execute("UPDATE approval_requests SET status='rejected', decided_at=now() WHERE id=%s", (req["id"],))
            _close_queue(vid, f"Rejected by {user['email']}: {reason}", c)
            audit.record_audit("policy.rejected", "rule_set_version", label, f"Rejected rule set {label}", reason=reason, conn=c)
        else:
            n = c.execute("SELECT count(*) AS n FROM approvals WHERE request_id=%s AND decision='approve'", (req["id"],)).fetchone()["n"]
            audit.record_audit("policy.approved", "rule_set_version", label,
                               f"Approved rule set {label} ({n} of {v['required_approvals']})", reason=reason, conn=c)
            if n >= v["required_approvals"]:
                c.execute("UPDATE rule_set_versions SET state='approved' WHERE id=%s", (vid,))
                c.execute("UPDATE approval_requests SET status='approved', decided_at=now() WHERE id=%s", (req["id"],))
                _close_queue(vid, f"Approved ({n} of {v['required_approvals']}); publishing", c)
                jobs.enqueue("policy.publish", {"version_id": vid}, idempotency_key=f"policy.publish:{vid}", conn=c)
    return version_detail(vid)


def _close_queue(vid: int, resolution: str, conn) -> None:
    conn.execute("""UPDATE work_queue SET status='resolved', resolution=%s, resolved_at=now()
                    WHERE dedupe_key=%s AND status IN ('open','in_progress')""", (resolution, f"policy.approval:{vid}"))


def rollback(vid: int, user: dict, reason: str) -> dict:
    """Republish an older version: a new draft copied from it, submitted straight away (still needs approval)."""
    if not reason.strip():
        raise PolicyError(422, "A reason is required to roll back")
    src = store.version(vid)
    if not src or src["state"] not in ("active", "superseded"):
        raise PolicyError(409, "Only a version that was published can be rolled back to")
    act = store.active_version()
    if act and act["id"] == vid:
        raise PolicyError(409, "That version is already active")
    with db.tx() as c:
        if store.open_version(conn=c):
            raise PolicyError(409, "Only one draft at a time (a deliberate limit, so every change is reviewed against the active rule set). Finish or discard the open draft first.")
        new = store.create_version("draft", f"Roll back to v{src['number']}", dict(src["config"]), store.rules_of(vid, conn=c),
                                   user["id"], c, reason=reason, based_on=act["id"] if act else None, rollback_of=vid)
        audit.record_audit("policy.rollback_requested", "rule_set_version", f"v{src['number']}",
                           f"Requested roll back to v{src['number']}", reason=reason, conn=c)
    return submit(new, user, f"Roll back to v{src['number']}", reason)


# ---------- publish (job) ----------

def _near_start_exception(emp: dict, v: dict, d: dict, conn) -> None:
    joining = date.fromisoformat(emp["joining_date"])
    work_queue.open_item(
        "policy.near_start", "employee", emp["id"],
        f"{emp['name']} starts {joining:%d %b}; rule set v{v['number']} would change their checklist",
        assignee_role="hr_ops", sla_due=joining - timedelta(days=1), dedupe_key=f"policy.near_start:{emp['id']}:{v['id']}",
        detail={"version_id": v["id"], "version": v["number"], "employee_name": emp["name"], **replay.counts(d),
                "changes": replay.describe(d)}, conn=conn)


@jobs.handler("policy.publish")
def publish_job(job: dict) -> None:
    publish(int(job["payload"]["version_id"]))


def publish(vid: int) -> dict:
    """Replay the approved version onto in-flight joiners in batches, then flip it active. Safe to re-run."""
    v = store.version(vid)
    if v["state"] == "active":
        return {"already": True}
    if v["state"] != "approved":
        raise PolicyError(409, f"v{v['number']} is {v['state']}, not approved")
    rules, config = store.load_version(vid)
    own = owners()
    applied, held = [], []
    emps = in_flight()
    for start in range(0, len(emps), BATCH):
        with db.tx() as c:
            # Serialises concurrent runners: the next one recomputes its diffs after this batch commits and finds nothing to do.
            c.execute("SELECT 1 FROM rule_set_versions WHERE id=%s FOR UPDATE", (vid,))
            for emp in emps[start:start + BATCH]:
                d = employee_diff(emp, rules, config, own, conn=c)
                if not replay.changed(d):
                    continue
                if near_start(emp):
                    _near_start_exception(emp, v, d, c)
                    held.append(emp["id"])
                    continue
                replay.apply(emp["id"], d, vid, c)
                applied.append(emp["id"])
                audit.record_audit("policy.replay", "employee", emp["id"], f"Applied rule set v{v['number']} to {emp['name']}",
                                   metadata={"version": v["number"], **replay.counts(d)}, actor="system", conn=c)
                # Outbox: projection, recompute and the activity feed commit with the change, so a crash can't skip them.
                jobs.enqueue("policy.after_replay", {"employee_id": emp["id"], "lines": replay.describe(d), "version": v["number"]},
                             idempotency_key=f"policy.after_replay:{emp['id']}:{vid}", conn=c)
                if d["added"] or d["reactivated"]:
                    jobs.enqueue("notify.item_added", {"employee_id": emp["id"], "version_id": vid,
                                                       "items": [p["name"] for p in d["added"] + d["reactivated"]]},
                                 idempotency_key=f"notify.item_added:{emp['id']}:{vid}", conn=c)
    with db.tx() as c:
        if db.fetch1("SELECT state FROM rule_set_versions WHERE id=%s FOR UPDATE", (vid,), conn=c)["state"] != "approved":
            return {"already": True}  # a concurrent runner flipped it first
        c.execute("UPDATE rule_set_versions SET state='superseded' WHERE state='active'")
        c.execute("UPDATE rule_set_versions SET state='active', published_at=now() WHERE id=%s", (vid,))
        audit.record_audit("policy.published", "rule_set_version", f"v{v['number']}",
                           f"Published rule set v{v['number']}: {len(applied)} joiner(s) updated, {len(held)} held for HR review",
                           metadata={"applied": applied, "held": held}, actor="system", conn=c)
    return {"applied": applied, "held": held}


@jobs.handler("policy.after_replay")
def after_replay_job(job: dict) -> None:
    # ponytail: a crash mid-handler can repeat a feed line on retry; key events by job id if that ever matters
    p = job["payload"]
    records.project(p["employee_id"])
    checklist.recompute(p["employee_id"])
    for line in p["lines"]:
        event(p["employee_id"], "checklist", f"{line} (rule set v{p['version']})")


@jobs.handler("notify.item_added")
async def notify_job(job: dict) -> None:
    p = job["payload"]
    eid, vid = p["employee_id"], int(p["version_id"])
    inserted = db.fetch1("""INSERT INTO item_notices (employee_id, version_id) VALUES (%s, %s)
                            ON CONFLICT DO NOTHING RETURNING employee_id""", (eid, vid))
    if not inserted:
        return  # already told about this version
    names = ", ".join(p.get("items") or [])
    event(eid, "notification", f"New onboarding requirement(s) shown in the joiner portal: {names}")
    import gmail
    if os.getenv("POLICY_NOTIFY_EMAIL", "false").lower() == "true" and gmail.configured():
        emp = records.employee(eid)
        html = (f"<p>Hi {emp['name'].split()[0]},</p><p>Your onboarding checklist has new item(s): <b>{names}</b>. "
                f"You'll find them in your onboarding portal: {gmail.employee_ctx(emp)['portal_link']}</p>")
        await gmail.send(emp["email"], "Your onboarding checklist has been updated", html, emp.get("thread_id") or None)


# ---------- single joiner (profile edits, near-start resolution) ----------

def replay_one(eid: str, reason: str | None = None) -> dict:
    """Apply the active version to one joiner now. A direct HR action is itself the human decision: no near-start gate."""
    act = store.active_version()
    if not act:
        return {}
    rules, config = store.load_version(act["id"])
    emp = records.employee(eid)
    d = employee_diff(emp, rules, config, owners())
    if replay.changed(d):
        with db.tx() as c:
            replay.apply(eid, d, act["id"], c)
        records.project(eid)
        if reason:
            for line in replay.describe(d):
                event(eid, "checklist", f"{line} ({reason})")
    checklist.recompute(eid)
    return d


def resolve_near_start(item: dict, action: str, user: dict, reason: str) -> str:
    if action not in ("apply", "skip"):
        raise PolicyError(422, "Choose apply or skip")
    eid = item["entity_id"]
    if action == "apply":
        replay_one(eid, f"applied by {user['email']} after near-start review")
    audit.record_audit(f"policy.near_start_{action}", "employee", eid,
                       f"{'Applied' if action == 'apply' else 'Skipped'} rule set v{item['detail'].get('version')} for "
                       f"{item['detail'].get('employee_name', eid)}", reason=reason)
    return "resolved" if action == "apply" else "dismissed"


work_queue.RESOLVERS["policy.near_start"] = resolve_near_start


# ---------- dev demo data ----------

DEMO_NDA = {"id": "R-NDA", "item": "nda_ack", "name": "Confidentiality (NDA) acknowledgement", "kind": "ack", "category": "HR",
            "match": {}, "offset_days": -2, "depends_on": [], "policy_ref": "",
            "description": "Every joiner acknowledges the confidentiality agreement before day one"}


def seed_demo() -> None:
    """Dev only, once: v2 pending with one of its two approvals, so the governance screens aren't empty."""
    act = store.active_version()
    if not act or db.fetch1("SELECT 1 AS x FROM rule_set_versions WHERE number > 1"):
        return
    hr = db.fetch1("SELECT id, email, role::text AS role FROM users WHERE email='hrops@azentio.local'")
    ap = db.fetch1("SELECT id, email, role::text AS role FROM users WHERE email='approver@azentio.local'")
    if not hr or not ap:
        return
    vid = store.open_draft(hr["id"])
    rules = {r["id"]: r for r in store.rules_of(vid)}
    if "R-PAN" in rules:
        store.upsert_draft_rule(vid, {**rules["R-PAN"], "offset_days": -7})
    if "R-BGV" in rules:
        store.upsert_draft_rule(vid, {**rules["R-BGV"], "offset_days": -5})
    store.upsert_draft_rule(vid, DEMO_NDA)
    store.set_draft_doc_policy(vid, {"types": {"bank_details": {"auto_accept_min": 0.88}}})  # Phase 2: a threshold change too
    submit(vid, hr, "PAN deadline, NDA for everyone, earlier background checks",
           "Payroll needs PAN a few days later than we ask today; Legal wants an NDA acknowledgement from every joiner; "
           "background checks should start a day earlier.")
    approve(vid, ap, "PAN and NDA changes look right; Compliance should also sign off the BGV timing.")

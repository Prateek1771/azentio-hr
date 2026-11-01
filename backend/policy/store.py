"""Rule-set versions in Postgres (ADR 0009). A version is a snapshot of ALL rules plus the config the engine reads,
so preview = build_items(emp, draft) vs the joiner's current items, and rollback = publish an older snapshot again.

Owners are directory data, not policy: they stay live (graph Owner nodes) and aren't snapshotted.
"""
import json

from shared import db

CONFIG_KEYS = ("doc_types", "locations", "departments", "roles", "doc_policy")  # doc_policy: Phase 2 tiers/thresholds
RULE_FIELDS = ("id", "item", "name", "kind", "category", "match", "offset_days", "depends_on", "description",
               "policy_ref", "auto", "compliance")
COMPLIANCE_DEFAULT = {"R-ID", "R-I9", "R-BGV"}  # identity, work authorisation, background verification
OPEN_STATES = ("draft", "pending_approval", "approved")


class PolicyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def rule_dict(r: dict) -> dict:
    """A rules row -> the shape checklist.build_items expects (match as dict, depends_on as list)."""
    return {"id": r["id"], "ord": r["ord"], "item": r["item"], "name": r["name"], "kind": r["kind"],
            "category": r["category"], "match": dict(r["match"] or {}), "offset_days": r["offset_days"],
            "depends_on": list(r["depends_on"] or []), "description": r["description"], "policy_ref": r["policy_ref"],
            "auto": r["auto"], "compliance": r["compliance"]}


def normalise_rule(r: dict) -> dict:
    match = r.get("match") or {}
    if isinstance(match, str):
        match = json.loads(match or "{}")
    deps = r.get("depends_on") or []
    if isinstance(deps, str):
        deps = [d.strip() for d in deps.split(",") if d.strip()]
    return {"id": str(r["id"]).strip(), "item": str(r["item"]).strip(), "name": r.get("name") or "",
            "kind": r["kind"], "category": r["category"], "match": match, "offset_days": int(r.get("offset_days") or 0),
            "depends_on": list(deps), "description": r["description"], "policy_ref": r.get("policy_ref") or "",
            "auto": r.get("auto") or "", "compliance": bool(r.get("compliance"))}


# ---------- reads ----------

def version(vid: int, conn=None) -> dict | None:
    return db.fetch1("""SELECT v.*, u.email AS author_email FROM rule_set_versions v LEFT JOIN users u ON u.id = v.author_id
                        WHERE v.id=%s""", (vid,), conn=conn)


def active_version(conn=None) -> dict | None:
    return db.fetch1("""SELECT v.*, u.email AS author_email FROM rule_set_versions v LEFT JOIN users u ON u.id = v.author_id
                        WHERE v.state='active'""", conn=conn)


def open_version(conn=None) -> dict | None:
    return db.fetch1("""SELECT v.*, u.email AS author_email FROM rule_set_versions v LEFT JOIN users u ON u.id = v.author_id
                        WHERE v.state = ANY(%s)""", (list(OPEN_STATES),), conn=conn)


def rules_of(vid: int, conn=None) -> list[dict]:
    return [rule_dict(r) for r in db.fetch("SELECT * FROM rules WHERE version_id=%s ORDER BY ord, id", (vid,), conn=conn)]


def load_version(vid: int, conn=None) -> tuple[list[dict], dict]:
    v = version(vid, conn=conn)
    if not v:
        raise PolicyError(404, "Version not found")
    return rules_of(vid, conn=conn), dict(v["config"])


def versions() -> list[dict]:
    return db.fetch("""SELECT v.id, v.number, v.state, v.title, v.reason, v.based_on, v.rollback_of, v.required_approvals,
                              v.created_at, v.submitted_at, v.published_at, u.email AS author_email,
                              (SELECT count(*) FROM rules r WHERE r.version_id = v.id) AS rule_count,
                              coalesce((SELECT json_agg(json_build_object('email', au.email, 'decision', a.decision,
                                                                          'reason', a.reason, 'ts', a.ts) ORDER BY a.ts)
                                        FROM approval_requests ar JOIN approvals a ON a.request_id = ar.id
                                        JOIN users au ON au.id = a.approver_id
                                        WHERE ar.subject_type='rule_set_version' AND ar.subject_id = v.id::text), '[]') AS approvals
                       FROM rule_set_versions v LEFT JOIN users u ON u.id = v.author_id ORDER BY v.number DESC""")


# ---------- writes ----------

def create_version(state: str, title: str, config: dict, rules: list[dict], author_id: int | None, conn,
                   reason: str = "", based_on: int | None = None, rollback_of: int | None = None) -> int:
    """Insert a version with its rules. Rules can only be written while the version is a draft (see trigger),
    so it's created as a draft first and moved to `state` afterwards."""
    num = conn.execute("SELECT coalesce(max(number), 0) + 1 AS n FROM rule_set_versions").fetchone()["n"]
    vid = conn.execute("""INSERT INTO rule_set_versions (number, state, title, reason, config, author_id, based_on, rollback_of)
                          VALUES (%s, 'draft', %s, %s, %s, %s, %s, %s) RETURNING id""",
                       (num, title, reason, db.Json({k: config.get(k) for k in CONFIG_KEYS}), author_id, based_on,
                        rollback_of)).fetchone()["id"]
    for i, r in enumerate(rules, start=1):
        _insert_rule(vid, normalise_rule(r), r.get("ord") or i, conn)
    if state != "draft":
        conn.execute("UPDATE rule_set_versions SET state=%s, published_at=CASE WHEN %s='active' THEN now() END WHERE id=%s",
                     (state, state, vid))
    return vid


def _insert_rule(vid: int, r: dict, ord_: int, conn) -> None:
    conn.execute("""INSERT INTO rules (version_id, id, ord, item, name, kind, category, match, offset_days, depends_on,
                                       description, policy_ref, auto, compliance)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                 (vid, r["id"], ord_, r["item"], r["name"], r["kind"], r["category"], db.Json(r["match"]), r["offset_days"],
                  r["depends_on"], r["description"], r["policy_ref"], r["auto"], r["compliance"]))


def open_draft(author_id: int) -> int:
    """Copy the active version into a new draft. One open draft at a time."""
    with db.tx() as c:
        if open_version(conn=c):
            raise PolicyError(409, "Only one draft at a time (a deliberate limit, so every change is reviewed against the active rule set). Finish or discard the open draft first.")
        act = active_version(conn=c)
        if not act:
            raise PolicyError(409, "No active rule set to copy")
        return create_version("draft", f"Changes to v{act['number']}", dict(act["config"]), rules_of(act["id"], conn=c),
                              author_id, c, based_on=act["id"])


def _draft(vid: int, conn) -> dict:
    v = version(vid, conn=conn)
    if not v:
        raise PolicyError(404, "Draft not found")
    if v["state"] != "draft":
        raise PolicyError(409, f"Version v{v['number']} is {v['state'].replace('_', ' ')} and can't be edited")
    return v


def upsert_draft_rule(vid: int, rule: dict) -> dict:
    r = normalise_rule(rule)
    with db.tx() as c:
        _draft(vid, c)
        old = c.execute("SELECT ord FROM rules WHERE version_id=%s AND id=%s", (vid, r["id"])).fetchone()
        ord_ = old["ord"] if old else c.execute("SELECT coalesce(max(ord), 0) + 1 AS o FROM rules WHERE version_id=%s",
                                                (vid,)).fetchone()["o"]
        c.execute("DELETE FROM rules WHERE version_id=%s AND id=%s", (vid, r["id"]))
        _insert_rule(vid, r, ord_, c)
    return r


def delete_draft_rule(vid: int, rid: str) -> None:
    with db.tx() as c:
        _draft(vid, c)
        if not c.execute("DELETE FROM rules WHERE version_id=%s AND id=%s", (vid, rid)).rowcount:
            raise PolicyError(404, f"Rule {rid} isn't in this draft")


def discard_draft(vid: int, user: dict) -> None:
    with db.tx() as c:
        v = _draft(vid, c)
        if v["author_id"] != user["id"] and user["role"] != "admin":
            raise PolicyError(403, "Only the author can discard this draft")
        c.execute("DELETE FROM rules WHERE version_id=%s", (vid,))
        c.execute("DELETE FROM rule_set_versions WHERE id=%s", (vid,))


# ---------- diff ----------

def set_draft_doc_policy(vid: int, doc_policy: dict) -> dict:
    """Replace the draft's document policy (tiers, auto-accept thresholds, spot-check rate, extraction deadline).
    The legal floor (documents.route.ALWAYS_HIGH) can't be lowered: that needs a code change and an ADR."""
    from documents.route import ALWAYS_HIGH, policy_for
    dp = {"default": dict(doc_policy.get("default") or {}), "types": {k: dict(v or {}) for k, v in (doc_policy.get("types") or {}).items()}}
    for k in ("spot_check_rate", "extraction_deadline_minutes"):
        if k in doc_policy:
            dp[k] = doc_policy[k]
    for name, t in [("default", dp["default"]), *dp["types"].items()]:
        if "tier" in t and t["tier"] not in ("low", "high"):
            raise PolicyError(422, f"{name}: tier must be low or high")
        if "auto_accept_min" in t and not (isinstance(t["auto_accept_min"], (int, float)) and 0.5 <= t["auto_accept_min"] <= 1.0):
            raise PolicyError(422, f"{name}: auto_accept_min must be between 0.5 and 1.0")
        if name in ALWAYS_HIGH and t.get("tier") == "low":
            raise PolicyError(422, f"{name} is on the legal floor (always high risk) and can't be made low risk by a policy change")
    if "spot_check_rate" in dp and not (isinstance(dp["spot_check_rate"], (int, float)) and 0 <= dp["spot_check_rate"] <= 1):
        raise PolicyError(422, "spot_check_rate must be between 0 and 1")
    if "extraction_deadline_minutes" in dp and not (isinstance(dp["extraction_deadline_minutes"], (int, float))
                                                    and 1 <= dp["extraction_deadline_minutes"] <= 1440):
        raise PolicyError(422, "extraction_deadline_minutes must be between 1 and 1440")
    with db.tx() as c:
        _draft(vid, c)
        c.execute("UPDATE rule_set_versions SET config = config || %s WHERE id=%s", (db.Json({"doc_policy": dp}), vid))
    return policy_for({"doc_policy": dp})


def doc_policy_diff(old_cfg: dict, new_cfg: dict) -> list[dict]:
    """Changes to the *effective* document policy: [{type, field, from, to}]. `type` is a doc type, or '*' for
    policy-wide settings. Comparing effective values means restating a default is not a change."""
    from documents.route import policy_for, tier_for
    a, b = policy_for(old_cfg), policy_for(new_cfg)
    out = []
    for k in ("spot_check_rate", "extraction_deadline_minutes"):
        if a[k] != b[k]:
            out.append({"type": "*", "field": k, "from": a[k], "to": b[k]})
    types = sorted(set((old_cfg or {}).get("doc_types") or {}) | set((new_cfg or {}).get("doc_types") or {})
                   | set(a["types"]) | set(b["types"]))
    for t in types:
        (ta, ma), (tb, mb) = tier_for(t, a), tier_for(t, b)
        if ta != tb:
            out.append({"type": t, "field": "tier", "from": ta, "to": tb})
        if ma != mb:
            out.append({"type": t, "field": "auto_accept_min", "from": ma, "to": mb})
    return out


def loosens_controls(changes: list[dict]) -> bool:
    """Lowering a threshold, changing a tier, or sampling fewer automatic acceptances needs two approvers."""
    return any(c["field"] == "tier" or (c["field"] in ("auto_accept_min", "spot_check_rate") and c["to"] < c["from"])
               for c in changes)


def diff(old: list[dict], new: list[dict]) -> dict:
    """Rule-level diff by rule id: added, removed, and changed fields."""
    a, b = {r["id"]: r for r in old}, {r["id"]: r for r in new}
    changed = []
    for rid in sorted(a.keys() & b.keys()):
        fields = {f: [a[rid].get(f), b[rid].get(f)] for f in RULE_FIELDS if f != "id" and a[rid].get(f) != b[rid].get(f)}
        if fields:
            changed.append({"id": rid, "fields": fields})
    return {"added": [b[r] for r in b if r not in a], "removed": [a[r] for r in a if r not in b], "changed": changed}


def touches_compliance(d: dict, old: list[dict], new: list[dict]) -> bool:
    """True if any added/removed/changed rule is (or was) flagged compliance."""
    comp = {r["id"] for r in old + new if r.get("compliance")}
    ids = {r["id"] for r in d["added"]} | {r["id"] for r in d["removed"]} | {c["id"] for c in d["changed"]}
    return bool(ids & comp)


# ---------- v1 from the legacy graph ----------

def ensure_active() -> dict | None:
    """Create v1 (active, authored by system) from the graph's Rule nodes + Config if no version exists yet.
    This is the one-time migration of rules: the active version matches the old behaviour exactly."""
    act = active_version()
    if act:
        return act
    from graph import q, q1
    rules = []
    for row in q("MATCH (r:Rule) RETURN r ORDER BY r.ord"):
        r = row["r"]
        rules.append({**r, "match": json.loads(r.get("match") or "{}"), "depends_on": list(r.get("depends_on") or []),
                      "compliance": r["id"] in COMPLIANCE_DEFAULT})
    if not rules:
        return None  # nothing seeded yet; v1 is created on the first read after seeding
    config = {}
    for k in CONFIG_KEYS:
        row = q1("MATCH (c:Config {key:$k}) RETURN c.value AS v", k=k)
        config[k] = json.loads(row["v"]) if row else ({} if k in ("doc_types", "locations") else [])
    with db.tx() as c:
        c.execute("SELECT pg_advisory_xact_lock(7243010)")
        if active_version(conn=c):
            return active_version(conn=c)
        create_version("active", "Initial rule set (migrated)", config, rules, None, c,
                       reason="Created from the rules and configuration in use before versioning")
        import audit
        audit.record_audit("policy.published", "rule_set_version", "v1",
                           f"Rule set v1 created from {len(rules)} existing rules", actor="system", conn=c)
    return active_version()

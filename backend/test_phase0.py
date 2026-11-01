"""Phase 0 self-checks: auth, permissions, audit chain, job queue, bootstrap. Plain asserts.

Runs against a throwaway database `azentio_test` (created if missing, wiped each run).
Usage: cd backend && uv run python test_phase0.py
"""
import asyncio
import os
import threading
from datetime import datetime, timezone

import psycopg

from shared import db

db.use_test_database()

from identity import perms, service as idsvc  # noqa: E402
from audit_log import service as auditsvc  # noqa: E402
from shared import jobs, bootstrap, bridge  # noqa: E402
import audit  # noqa: E402
import work_queue  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1
    print(f"  ok  {name}")


# 1. permission matrix: every role x a representative route per group, plus default deny
A, H, P, M, U = "admin", "hr_ops", "approver", "manager", "auditor"
MATRIX = [  # method, path, roles allowed
    ("GET", "/api/users", {A}), ("POST", "/api/users", {A}), ("PATCH", "/api/users/3", {A}),
    ("GET", "/api/employees", {A, H, P, M, U}), ("GET", "/api/employees/E1/graph", {A, H, P, M, U}),
    ("PATCH", "/api/employees/E1", {H}), ("POST", "/api/employees/E1/send-request", {H}),
    ("GET", "/api/stats", {A, H, P, M, U}), ("GET", "/api/events", {A, H, P, M, U}),
    ("GET", "/api/documents/d1/file", {A, H, P, M, U}), ("GET", "/api/employees/E1/portal-link", {H}),
    ("GET", "/api/templates", {A, H, P, U}), ("PUT", "/api/templates/t1", {H}),
    ("GET", "/api/rules", {A, H, P, U}), ("PUT", "/api/rules/r1", {H}), ("DELETE", "/api/rules/r1", {H}),
    ("POST", "/api/items/i1/status", {H}), ("POST", "/api/escalate", {H}), ("POST", "/api/ingest", {H}),
    ("POST", "/api/onboard", {H}), ("POST", "/api/gmail/poll", {H}), ("POST", "/api/hr/chat", {A, H, P, U}),
    ("GET", "/api/approvals", {A, H, P, U}),
    ("POST", "/api/integrations/i1/candidates/c1/decision", {P}), ("POST", "/api/integrations/i1/offer/decision", {P}),
    ("GET", "/api/integrations", {A, H, P, U}), ("POST", "/api/integrations/i1/run", {A}),
    ("GET", "/api/connectors/c1", {A, H, P, U}), ("PUT", "/api/connectors/c1/connection", {A}),
    ("POST", "/api/connectors/c1/import", {A, H}), ("POST", "/api/connectors/c1/records/r1/onboard", {A, H}),
    ("GET", "/api/logs/audit", {A, H, P, U}), ("GET", "/api/quality", {A, H, P, U}), ("POST", "/api/quality/eval", {A}),
    ("GET", "/api/work-queue", {A, H, P, U}), ("POST", "/api/work-queue/1/resolve", {A, H, P}),
    ("GET", "/api/auth/me", {A, H, P, M, U}), ("GET", "/api/not-a-route", set()), ("DELETE", "/api/stats", set()), ("POST", "/api/policies", set()),
]
bad = [(m, p, r) for m, p, roles in MATRIX for r in perms.ROLES if perms.allowed(m, p, r) != (r in roles)]
check(f"perm_matrix ({len(MATRIX)} routes x {len(perms.ROLES)} roles)", not bad or print(bad))
check("perm_public_paths", perms.is_public("POST", "/api/auth/login") and perms.is_public("GET", "/api/portal/tok")
      and not perms.is_public("GET", "/api/employees"))
check("perm_no_session_denied", perms.allowed("GET", "/api/employees", None) is False)

# 2. passwords
h = idsvc.hash_password("correct horse battery")
check("password_hash_roundtrip", idsvc.verify_password("correct horse battery", h)
      and not idsvc.verify_password("wrong", h) and not idsvc.verify_password("x", "garbage$hash"))

# 3. users, sessions, revocation
admin = idsvc.create_user("Admin@Test.io", "Admin", "admin", "admin-pass-123", actor="test")
hr = idsvc.create_user("hr@test.io", "HR", "hr_ops", "hr-pass-1234", actor="test")
check("email_normalised", admin["email"] == "admin@test.io")
_, tok = idsvc.login("hr@test.io", "hr-pass-1234")
check("session_valid", idsvc.user_for_token(tok)["email"] == "hr@test.io")
idsvc.logout(tok)
check("session_revocation_immediate_logout", idsvc.user_for_token(tok) is None)
_, tok = idsvc.login("hr@test.io", "hr-pass-1234")
idsvc.update_user(hr["id"], active=False)
check("session_revocation_immediate_deactivate", idsvc.user_for_token(tok) is None)
try:
    idsvc.login("hr@test.io", "hr-pass-1234")
    check("inactive_cannot_login", False)
except idsvc.AuthError as e:
    check("inactive_cannot_login", e.status == 401)
try:
    idsvc.create_user("short@test.io", "S", "auditor", "short", actor="test")
    check("password_min_length", False)
except idsvc.AuthError as e:
    check("password_min_length", e.status == 422)

# 4. last admin guard
for kw in ({"role": "hr_ops"}, {"active": False}):
    try:
        idsvc.update_user(admin["id"], **kw)
        check(f"last_admin_guard {kw}", False)
    except idsvc.AuthError as e:
        check(f"last_admin_guard {kw}", e.status == 409)

# 5. login throttle
for _ in range(idsvc.MAX_FAILS):
    try:
        idsvc.login("admin@test.io", "nope-nope-nope")
    except idsvc.AuthError:
        pass
try:
    idsvc.login("admin@test.io", "admin-pass-123")
    check("login_throttle", False)
except idsvc.AuthError as e:
    check("login_throttle", e.status == 429)
idsvc._FAILS.clear()

# 6. audit chain
for i in range(20):
    auditsvc.record("test.entry", "thing", str(i), f"entry {i}", actor="tester")
check("audit_chain_valid", auditsvc.verify_chain() == (True, None))
try:
    db.execute("UPDATE audit_log SET summary='edited' WHERE seq=5")
    check("audit_update_blocked", False)
except psycopg.Error:
    check("audit_update_blocked", True)
with db.tx() as c:  # simulate an attacker with superuser rights who disables the trigger
    c.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update")
    c.execute("UPDATE audit_log SET summary='edited' WHERE seq=(SELECT min(seq)+4 FROM audit_log)")
    c.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update")
good, bad_seq = auditsvc.verify_chain()
check("audit_tamper_detected", not good and bad_seq is not None)

# 7. actor from request context
db.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update")
db.execute("DELETE FROM audit_log")  # start a clean chain for the remaining checks
db.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update")
t = idsvc.current_user.set({"id": admin["id"], "email": "admin@test.io", "role": "admin"})
audit.record_audit("ctx.test", "thing", "1", "inside a request")
idsvc.current_user.reset(t)
audit.record_audit("ctx.test", "thing", "2", "outside a request")
audit.record_audit("ctx.test", "thing", "3", "explicit agent", actor="Agent")
actors = [r["actor"] for r in db.fetch("SELECT actor FROM audit_log WHERE action='ctx.test' ORDER BY seq")]
check("actor_from_context", actors == ["admin@test.io", "system", "Agent"])
check("audit_chain_valid_after_wrapper", auditsvc.verify_chain()[0])

# 8. jobs
k1 = jobs.enqueue("noop", {}, idempotency_key="once:1")
k2 = jobs.enqueue("noop", {}, idempotency_key="once:1")
check("job_idempotent_enqueue", k1 is not None and k2 is None
      and db.fetch1("SELECT count(*) AS n FROM jobs WHERE idempotency_key='once:1'")["n"] == 1)
db.execute("DELETE FROM jobs")


@jobs.handler("always_fail")
def _fail(job):
    raise RuntimeError("boom")


jid = jobs.enqueue("always_fail", {}, max_attempts=3)
run_ats = []
for _ in range(3):
    db.execute("UPDATE jobs SET run_at=now() WHERE id=%s AND state='queued'", (jid,))
    asyncio.run(jobs.run_once())
    run_ats.append(db.fetch1("SELECT run_at, state FROM jobs WHERE id=%s", (jid,)))
check("job_retry_backoff", jobs.backoff(1) < jobs.backoff(2) < jobs.backoff(3) and run_ats[0]["state"] == "queued")
check("job_dead_after_max_attempts", run_ats[-1]["state"] == "dead")
dead = db.fetch("SELECT * FROM work_queue WHERE kind='ops' AND entity_id=%s", (str(jid),))
check("job_dead_opens_one_ops_exception", len(dead) == 1 and dead[0]["assignee_role"] == "admin")

# side effect happens once even when the handler fails after performing it
db.execute("CREATE TABLE side_effects (key text PRIMARY KEY)")
calls = {"n": 0}


@jobs.handler("send_once")
def _send(job):
    calls["n"] += 1
    db.execute("INSERT INTO side_effects (key) VALUES (%s) ON CONFLICT DO NOTHING", (job["idempotency_key"],))
    if calls["n"] == 1:
        raise RuntimeError("crash after the side effect")


sid = jobs.enqueue("send_once", {}, idempotency_key="mail:42")
asyncio.run(jobs.run_once())
db.execute("UPDATE jobs SET run_at=now() WHERE id=%s", (sid,))
asyncio.run(jobs.run_once())
check("job_side_effect_once", calls["n"] == 2 and db.fetch1("SELECT count(*) AS n FROM side_effects")["n"] == 1
      and db.fetch1("SELECT state FROM jobs WHERE id=%s", (sid,))["state"] == "done")

# no double claim under concurrency
db.execute("DELETE FROM jobs")
for i in range(40):
    jobs.enqueue("noop", {"i": i})
claimed, lock = [], threading.Lock()


def grab(w):
    while (j := jobs.claim(w)) is not None:
        with lock:
            claimed.append(j["id"])


threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
check("job_no_double_claim", len(claimed) == 40 and len(set(claimed)) == 40)

check("job_unknown_kind_fails_safely", (jobs.enqueue("no_such_kind", {}, max_attempts=1) is not None)
      and asyncio.run(jobs.run_once()) is not None)

# stuck job requeue
db.execute("DELETE FROM jobs")
jid = jobs.enqueue("noop", {})
db.execute("UPDATE jobs SET state='running', locked_at=now() - interval '1 hour' WHERE id=%s", (jid,))
check("job_stuck_requeued", jobs.requeue_stuck() == 1)

# 9. work queue dedupe
a = work_queue.open_item("ops", "job", "x", "r", "admin", dedupe_key="same")
b = work_queue.open_item("ops", "job", "x", "r", "admin", dedupe_key="same")
check("work_queue_dedupe", a is not None and b is None)
check("work_queue_role_scope", all(i["assignee_role"] == "hr_ops" for i in work_queue.list_items("hr_ops"))
      and len(work_queue.list_items("admin")) >= 1)

# 10. bridge parity gate
g = [{"id": "E1", "name": "A"}, {"id": "E2", "name": "B"}]
check("bridge_parity_ok", bridge.parity(g, list(reversed(g)))["ok"])
try:
    bridge.switch("Employee", g, [{"id": "E1", "name": "A"}, {"id": "E2", "name": "changed"}])
    check("bridge_switch_refused_without_parity", False)
except bridge.ParityError:
    check("bridge_switch_refused_without_parity", bridge.source("Employee") == "graph")

# 11. bootstrap idempotent + fail fast
os.environ.pop("ENV", None)


def counts():
    return {t: db.fetch1(f"SELECT count(*) AS n FROM {t}")["n"] for t in ("users", "schema_migrations")}


bootstrap.ensure_users()
first = counts()
db.migrate()
bootstrap.ensure_users()
check("bootstrap_idempotent", counts() == first and first["users"] >= 7)
os.environ["ENV"] = "production"
saved = os.environ.pop("ADMIN_PASSWORD", None)
try:
    bootstrap.check_config()
    check("bootstrap_missing_config_fails_fast", False)
except SystemExit as e:
    check("bootstrap_missing_config_fails_fast", "ADMIN_PASSWORD" in str(e))
finally:
    os.environ.pop("ENV")
    if saved:
        os.environ["ADMIN_PASSWORD"] = saved

# 12. manager scope (the predicate main.py applies in get_emp / list_employees / events / document file)
import main  # noqa: E402

t = idsvc.current_user.set({"id": 9, "email": "manager@azentio.local", "role": "manager"})
check("manager_scope", main._visible({"manager_email": "Manager@azentio.local".lower()})
      and not main._visible({"manager_email": "someone@else.io"}) and not main._visible({}))
idsvc.current_user.reset(t)
t = idsvc.current_user.set({"id": 1, "email": "hr@test.io", "role": "hr_ops"})
check("non_manager_sees_all", main._visible({"manager_email": "someone@else.io"}))
idsvc.current_user.reset(t)

print(f"\nphase 0: {ok} checks passed ({datetime.now(timezone.utc):%H:%M:%S} UTC)")
db.close()

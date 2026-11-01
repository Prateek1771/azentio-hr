# Phase 0: Trust + foundation

- **Status:** in progress
- **Branch:** `phase-0-trust-foundation`
- **Design:** [hardening design](2026-11-01-hardening-design.md)

## Goal
After this phase:
- Every API call (except the employee portal) needs a signed-in user with a role that allows it.
- Every human action is recorded under that user's name in an audit log that shows any tampering.
- The app sets itself up on start.
- The project has the Postgres foundation that Phases 1 to 6 build on: migrations, a transaction helper, a durable job queue, the exception queue and the migration bridge.

No HR behaviour changes for users beyond signing in and the screens filtered by role.

## Scope
- Postgres service, connection pool, `tx()` and a startup migration runner.
- `bootstrap.py`: config check, wait for the database, migrations, admin from env, dev seed users, default rules, graph ping and job worker. Safe to re-run.
- Identity: users, server-side sessions, scrypt password hashes, 5 roles, one permission table, a login throttle, and a manager filter so managers see only their own reports.
- Audit: hash-chained append-only log in Postgres. Every existing `audit.record_audit` call now records the signed-in user automatically. Old graph audit rows are copied over once.
- **Jobs table = the transactional outbox and short-task queue.** Enqueue with an idempotency key, workers claiming jobs with `SKIP LOCKED`, backoff, a dead state, and a handler registry. `enqueue(..., conn=c)` commits together with the business change. From Phase 4, the job kinds `temporal.start`/`temporal.signal` hand committed changes to Temporal, which runs the long-lived workflows; no schema change is needed ([ADR 0011](../adr/0011-temporal-orchestration.md)).
- `work_queue`: the one exceptions inbox table. In Phase 0 its only producer is a dead-lettered job, which opens an `ops` exception for Admin.
- `approval_requests` and `approvals` tables, created now and used from Phase 1.
- `shared/bridge.py`: one read source per record type, plus `parity()`.
- `/health` and `/ready`.
- Frontend:
  - the API is served through a Next.js rewrite, so it's same-origin and the cookie works
  - login screen
  - role-filtered navigation
  - user menu with sign-out
  - Users screen (admin)
- Tests: `backend/test_phase0.py` and `scripts/smoke.py`.

## Non-goals
- Moving any HR record (Employee, Item, Document) to Postgres. That's Phases 1 and 2.
- Rule drafts and approvals (Phase 1), document risk tiers (Phase 2), Temporal and moving the Gmail poller and scheduler onto schedules (Phase 4).
- SSO/OIDC, MFA and password reset by email. Passwords are set by an admin, and users can change their own.
- Multi-tenant isolation.

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `production` makes `ADMIN_EMAIL`, `ADMIN_PASSWORD` and `DATABASE_URL` required and turns off dev seed users |
| `DATABASE_URL` | `postgresql://azentio:azentio@localhost:5432/azentio` in dev | Matches the `postgres` service in `docker-compose.yml` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | dev: `admin@azentio.local` with a printed default | Creates the first admin only if that user doesn't exist yet. It never overwrites a password. |
| `SESSION_DAYS` | `7` | Session lifetime |
| `COOKIE_SECURE` | `false` in dev | Set `true` when serving over https |
| `BACKEND_URL` (frontend) | `http://localhost:8000` | Where the Next.js rewrite sends `/api/*` |

If a required variable is missing, startup stops with `bootstrap: missing required config: X`. It doesn't hang.

## Schema
`backend/migrations/001_identity.sql`
```sql
CREATE TYPE user_role AS ENUM ('admin','hr_ops','approver','manager','auditor');
CREATE TABLE users (
  id          bigserial PRIMARY KEY,
  email       text NOT NULL UNIQUE CHECK (email = lower(email)),
  name        text NOT NULL,
  role        user_role NOT NULL,
  pw_hash     text NOT NULL,              -- scrypt$n$r$p$salt_b64$hash_b64
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_login  timestamptz
);
CREATE TABLE sessions (
  token_hash  text PRIMARY KEY,           -- sha256 of the cookie value; the raw token is never stored
  user_id     bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz
);
CREATE INDEX ON sessions (user_id);
```

`002_audit.sql`
```sql
CREATE TABLE audit_log (
  seq          bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  actor        text NOT NULL,             -- user email, or 'system' / 'agent'
  actor_role   text NOT NULL DEFAULT '',
  action       text NOT NULL,
  entity_type  text NOT NULL,
  entity_id    text NOT NULL DEFAULT '',
  summary      text NOT NULL,
  metadata     jsonb NOT NULL DEFAULT '{}',
  reason       text NOT NULL DEFAULT '',
  prev_hash    text NOT NULL,
  hash         text NOT NULL UNIQUE
);
CREATE INDEX ON audit_log (entity_type, entity_id);
CREATE INDEX ON audit_log (action);
-- Append-only: refuse UPDATE and DELETE at the database, not just in app code.
CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
```
`hash = sha256(prev_hash + canonical_json(ts, actor, actor_role, action, entity_type, entity_id, summary, metadata, reason))`. The first row's `prev_hash` is 64 zeros. Inserts take `pg_advisory_xact_lock` so two concurrent writers can't both chain from the same previous row. Someone with superuser access could drop the trigger, but any edit, deletion or reordering still breaks `verify_chain()`.

We use a trigger rather than a separate INSERT-only database role. It enforces the same rule without a second connection pool.

`003_jobs.sql`
```sql
CREATE TYPE job_state AS ENUM ('queued','running','done','dead');
CREATE TABLE jobs (
  id               bigserial PRIMARY KEY,
  kind             text NOT NULL,
  payload          jsonb NOT NULL DEFAULT '{}',
  idempotency_key  text UNIQUE,            -- same key enqueued twice = one job
  state            job_state NOT NULL DEFAULT 'queued',
  attempts         int NOT NULL DEFAULT 0,
  max_attempts     int NOT NULL DEFAULT 5,
  run_at           timestamptz NOT NULL DEFAULT now(),
  locked_by        text,
  locked_at        timestamptz,
  last_error       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz
);
CREATE INDEX jobs_ready ON jobs (run_at) WHERE state = 'queued';
CREATE TABLE worker_heartbeat (worker text PRIMARY KEY, seen_at timestamptz NOT NULL);
```
- **Claiming:** `UPDATE jobs SET state='running', locked_by=$w, locked_at=now(), attempts=attempts+1 WHERE id = (SELECT id FROM jobs WHERE state='queued' AND run_at<=now() ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`.
- **On failure:** the job goes back to `queued` with `run_at = now() + 2^attempts × 5s`, capped at 1 hour. Once `attempts = max_attempts` it's marked `dead` and an `ops` exception is opened in `work_queue`.
- **Stuck jobs:** a job left `running` for more than 10 minutes (its worker crashed) is put back in the queue.

`004_approvals_work_queue.sql`
```sql
CREATE TABLE approval_requests (
  id            bigserial PRIMARY KEY,
  subject_type  text NOT NULL,             -- 'rule_version', 'threshold_version', 'document', ...
  subject_id    text NOT NULL,
  requested_by  bigint NOT NULL REFERENCES users(id),
  required      int NOT NULL DEFAULT 1,     -- 2 for compliance rules
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','withdrawn')),
  reason        text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz
);
CREATE TABLE approvals (
  request_id  bigint NOT NULL REFERENCES approval_requests(id),
  approver_id bigint NOT NULL REFERENCES users(id),
  decision    text NOT NULL CHECK (decision IN ('approve','reject')),
  reason      text NOT NULL DEFAULT '',
  ts          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (request_id, approver_id)    -- one vote per person
);
CREATE TABLE work_queue (
  id             bigserial PRIMARY KEY,
  kind           text NOT NULL,             -- 'ops', later 'doc_review', 'rule_approval', 'near_start_change', 'appeal'
  entity_type    text NOT NULL,
  entity_id      text NOT NULL,
  reason         text NOT NULL,
  detail         jsonb NOT NULL DEFAULT '{}',
  assignee_role  user_role NOT NULL,
  assignee_id    bigint REFERENCES users(id),
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','dismissed')),
  sla_due        timestamptz,
  resolution     text,
  resolved_by    bigint REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  resolved_at    timestamptz,
  dedupe_key     text UNIQUE                -- one open exception per cause
);
CREATE INDEX ON work_queue (status, assignee_role);
```

## Permissions
`backend/identity/perms.py` holds one ordered table of `(methods, path regex, roles)`, checked first match wins. Any `/api` path the table doesn't match is denied (`403`). The one middleware in `main.py` applies it; no route checks permissions itself.

`STAFF = admin, hr_ops, approver, auditor`. `manager` gets the read routes marked M, filtered to their own reports.

| Route group | GET | Writes |
|---|---|---|
| `/api/auth/login` | public | public (throttled) |
| `/api/auth/me`, `/logout`, `/password` | any signed-in user | any signed-in user |
| `/api/portal/*` | public (per-employee token) | public |
| `/api/users*` | admin | admin |
| `/api/employees*`, `/api/stats`, `/api/events`, `/api/documents/*/file` | STAFF + M | hr_ops |
| `/api/templates*`, `/api/rules*`, `/api/policies` | STAFF | hr_ops |
| `/api/items/*`, `/api/escalate`, `/api/gmail/poll`, `/api/ingest`, `/api/onboard` | n/a | hr_ops |
| `/api/hr/chat` | n/a | hr_ops, approver, auditor, admin |
| `/api/approvals` | STAFF | n/a |
| `/api/integrations/*/candidates/*/decision`, `/offer/decision` | n/a | approver |
| `/api/connectors/*/import`, `/records/*/onboard` | n/a | hr_ops, admin |
| `/api/connectors*`, `/api/integrations*` | STAFF | admin |
| `/api/logs/*` | admin, hr_ops, approver, auditor | n/a |
| `/api/quality` / `/eval` | STAFF | admin |
| `/api/work-queue` | STAFF | admin, hr_ops, approver (own role's items) |

**How the manager filter works:**
- It sits in `main.get_emp` (which every `/api/employees/{eid}` route goes through), `main.list_employees` (which `stats` reuses) and the document-file route.
- If the signed-in user is a manager and `employee.manager_email != user.email`, the request gets `404`, so it doesn't reveal that the employee exists.

## API
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/auth/login` | `{email, password}` | `{user}`, and sets the `session` cookie (httpOnly, SameSite=Lax, Secure if `COOKIE_SECURE`) |
| POST | `/api/auth/logout` | n/a | `204`; the session is revoked |
| GET | `/api/auth/me` | n/a | `{id, email, name, role}` |
| POST | `/api/auth/password` | `{current, new}` | `204`; the user's other sessions are revoked |
| GET | `/api/users` | n/a | users (no hashes) |
| POST | `/api/users` | `{email, name, role, password}` | the created user |
| PATCH | `/api/users/{id}` | `{name?, role?, active?, password?}` | the updated user; deactivating revokes their sessions |
| GET | `/api/work-queue` | `?status=open` | exceptions for the caller's role (all of them for admin and auditor) |
| POST | `/api/work-queue/{id}/resolve` | `{resolution}` | the updated item |
| GET | `/health` | n/a | `{"ok": true}` if the process is up |
| GET | `/ready` | n/a | `200` or `503` with `{postgres, graph, worker, llm}`. The worker is OK if its heartbeat is under 30s old. LLM status is informational and never causes a 503. |

**Guards**
- An admin can't deactivate or demote the last active admin (`409`).
- Passwords need at least 10 characters.
- **Login throttle:** 5 failed attempts per email per 15 minutes returns `429`. It's kept in memory.
  `ponytail: in-process counter; move to Postgres when there are several backend replicas.`

## Audit wiring
- `audit.py` keeps its public functions, `record_audit(...)` and `list_audit(...)`. Underneath, they now write to and read from `audit/service.py`.
- **Recording who acted:** the auth middleware sets a `current_user` context variable for each request.
  - `record_audit` uses it whenever the caller passes the old default actor `"HR"`.
  - Code running outside a request (jobs, the scheduler, the poller) records `system`.
  - Explicit actors such as `"Agent"` are kept.

  Result: all 13 existing call sites in `main.py`, `connector_routes.py` and `integrations/` record real users **with no changes at those call sites**.
- **Bootstrap import:** if `audit_log` is empty, bootstrap copies the graph's `AuditLog` rows into it once, oldest first, so the history continues in one chain.
- **New actions audited:** `auth.login`, `auth.login_failed`, `auth.logout`, `auth.password_changed`, `user.created`, `user.updated`, `work_queue.resolved`.

## Screens
**Login** (`/login`, public, outside the app shell)
- **Layout:** split screen. On the left, a brand panel with the sidebar's indigo-to-violet gradient, the `Sparkles` logo, the product name and three short value lines. It's hidden below `md`. On the right, a centred card with Email, Password (show/hide toggle) and Sign in.
- **States:**
  - while submitting, the button shows a spinner and is disabled
  - errors show inline under the form: "Wrong email or password", or "Too many attempts, try again in 15 minutes"
  - in dev, a hint lists the seed accounts
- After sign-in, it goes to `?next=` (same-origin paths only) or `/dashboard`.
- Works in dark mode.

**App shell** (`components/app-shell.tsx`)
- On mount, it loads `/api/auth/me`. While that's loading it shows a sidebar skeleton. A `401` sends the browser to `/login?next=<path>`.
- Each `NAV` entry lists the roles that can see it; the nav shows only entries for the current role.

  | Role | Sees |
  |---|---|
  | Admin | everything plus Users |
  | Manager | Dashboard only |
  | Auditor | read-only pages including Logs |

- **Footer:** a user chip with initials, name and a role badge. Its dropdown has Change password and Sign out.

**Users** (`/users`, admin only)
- **Table:** name and email, role badge, status (Active/Inactive), last login, created date.
- **Toolbar:** search and an Add user button.
- **Add user dialog:** name, email, role select (with a one-line description of each role), and a temporary password with a Generate button and a copy button.
- **Row actions:** change role (inline select), reset password, deactivate/reactivate, each with a confirmation. The last admin's row disables demote and deactivate, with a tooltip explaining why.
- **Empty and error states** use the same card style as the Approvals page.

**Viewer gating:** write buttons are hidden for roles that can't use them. The backend enforces this regardless; hiding the buttons just avoids dead clicks.

## File-by-file changes
| File | Change |
|---|---|
| `docker-compose.yml` | add a `postgres:17` service (port 5432, volume `pg-data`, healthcheck) |
| `.env.example` | add `ENV`, `DATABASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `COOKIE_SECURE`, `SESSION_DAYS` |
| `backend/pyproject.toml` | add `psycopg[binary,pool]` |
| `backend/shared/db.py` | new: pool, `tx()`, `fetch`/`fetch1`/`execute`, `migrate()` |
| `backend/shared/jobs.py` | new: `enqueue`, `@handler`, `run_once`, `worker()`, heartbeat, requeue of stuck jobs |
| `backend/shared/bridge.py` | new: `SOURCE`, `source()`, `parity()` |
| `backend/shared/bootstrap.py` | new: the startup steps, each safe to re-run |
| `backend/migrations/001–004_*.sql` | new: schema above |
| `backend/identity/{service,perms,routes}.py` | new |
| `backend/audit_log/service.py` | new: `record`, `verify_chain`, `list_entries`, `import_graph_rows`. Named `audit_log/` because `audit.py` already exists as a module. Bootstrap imports graph history **before** anything else writes audit rows, so the old history stays first in the chain. |
| `backend/work_queue.py` | new: `open_item`, `list_items`, `resolve`, plus routes |
| `backend/audit.py` | becomes a thin wrapper over `audit_log/service.py`; actor comes from `current_user` |
| `backend/main.py` | `lifespan` runs bootstrap; auth middleware; `/health`, `/ready`; manager filter in `get_emp`, `list_employees` and the document file route; includes the identity and work-queue routers; CORS restricted to `CORS_ORIGINS` |
| `frontend/next.config.ts` | `rewrites()` sending `/api/:path*` to `BACKEND_URL` |
| `frontend/lib/api.ts` | `API` defaults to `""`; a 401 redirects to login; `User`/`Role` types |
| `frontend/components/auth.tsx` | new: `MeProvider`, `useMe`, `can`, `ROLE_LABEL` |
| `frontend/app/login/page.tsx` | new |
| `frontend/app/(hr)/users/page.tsx` | new |
| `frontend/components/app-shell.tsx` | role-filtered nav, user chip, sign-out |
| `backend/test_phase0.py`, `scripts/smoke.py` | new |

## Seed data (dev only, `ENV != production`)
| Email | Role | Dev password |
|---|---|---|
| `admin@azentio.local` | admin | `ADMIN_PASSWORD`, or `admin-dev-pass` |
| `hrops@azentio.local` | hr_ops | `hrops-dev-pass` |
| `approver@azentio.local` | approver | `approver-dev-pass` |
| `manager@azentio.local` | manager | `manager-dev-pass` |
| `auditor@azentio.local` | auditor | `auditor-dev-pass` |

At startup, the dev log prints these accounts once. Bootstrap also makes the seed manager the `manager_email` of two seeded joiners, so the manager's view isn't empty.

## Tests (`backend/test_phase0.py`, plain asserts on database `azentio_test`)
1. `perm_matrix`: every role against a representative route in every group gets the allowed or denied result from the table above, including the default deny for an unknown path.
2. `password_hash_roundtrip`: a correct password verifies; a wrong one and a malformed hash don't.
3. `session_revocation_immediate`: after logout or deactivation, the same cookie gets `401` on the very next call.
4. `last_admin_guard`: demoting or deactivating the only admin returns `409`.
5. `audit_chain_valid` and `audit_tamper_detected`: write 20 entries and `verify_chain()` is True. Then disable the trigger in the test database, edit one row and `verify_chain()` is False.
6. `audit_update_blocked`: an UPDATE on `audit_log` raises.
7. `job_idempotent_enqueue`: enqueueing the same key twice gives one row.
8. `job_retry_then_dead`: a handler that always fails backs off (`run_at` increases) and reaches `dead` after `max_attempts`, and exactly one `ops` exception is opened.
9. `job_no_double_claim`: 4 threads claiming at once each get a different job or nothing; no job runs twice.
10. `job_side_effect_once`: a handler whose side effect is keyed fails after writing; the retry doesn't write a second time.
11. `bootstrap_idempotent`: running bootstrap twice gives the same table row counts as running it once.
12. `bootstrap_missing_config`: with `ENV=production` and no `ADMIN_PASSWORD`, startup raises `SystemExit` with a message naming the variable.
13. `actor_from_context`: a `record_audit` call made during a request records that user's email; one made outside a request records `system`.
14. `manager_scope`: a manager gets `404` for an employee they don't manage, and their list contains only their reports.

**`scripts/smoke.py`** (headless Chrome, run against the live dev stack)
- For each seed role: log in, check that the expected nav items are visible and forbidden ones aren't, open each allowed page and check it rendered (no Next.js error overlay), and screenshot it.
- Workflows:
  - **HR Ops:** open employee E1006 and preview the request email.
  - **Admin:** open Users and see 5 accounts.
  - **Manager:** sees only their reports.
  - **Auditor:** opens the audit log and sees the login entries.

## Exit criteria
- Signed out, every `/api/*` route except `auth/login`, `portal/*`, `/health` and `/ready` returns `401`.
- Every row of the permission table is covered by `perm_matrix`, and it passes.
- Changing a template as `hrops@` shows up in the audit log with actor `hrops@azentio.local`, and `verify_chain()` is True.
- Stopping Postgres makes `/ready` return `503 {postgres: down}`. Restarting it brings `/ready` back without restarting the app.
- A fresh clone plus `docker compose up -d` plus starting the backend gives a working app with no manual setup steps.
- `test_core.py`, `test_phase0.py` and `scripts/smoke.py` all pass.

## Risks and rollback
| Risk | Mitigation |
|---|---|
| Auth locks everyone out | The admin from env is ensured on every start, and an admin can reset passwords. As a last resort, setting `ADMIN_PASSWORD` and restarting creates the admin again. |
| The Next.js rewrite breaks streaming chat or uploads | Dev rewrites stream. Smoke-test chat and the portal upload. If it breaks, fall back to `NEXT_PUBLIC_API_URL` and a cookie with `SameSite=None; Secure`. |
| Postgres becomes a hard dependency | `/ready` reports it. Bootstrap fails fast with a clear message rather than serving half-working pages. |
| The audit import duplicates rows | It only runs when `audit_log` is empty. |
| Rollback | Revert the branch. Phase 0 adds new tables only and changes no graph data. |

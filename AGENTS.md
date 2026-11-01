# AGENTS.md

A brief for any coding agent (and human) working in this repo. Keep it current. Update it in the same commit as any change to structure, commands, conventions or phase status.

## What this is
Azentio Onboard is an HR onboarding agent. For each new joiner it works out which documents and tasks apply, collects documents over Gmail and a joiner portal, checks them (vision LLM plus deterministic rules), raises IT, Facilities and Compliance requests, and answers policy questions with citations.

We're hardening it from a demo into governed HR operations in 7 phases: see [docs/plans/2026-11-01-hardening-design.md](docs/plans/2026-11-01-hardening-design.md).

## Current phase
**Phases 0 and 1 are complete.** Next: **Phase 2: AI control + Document migration** ([plan](docs/plans/phase-2-ai-control.md)). References: [Phase 0](docs/plans/phase-0-trust-foundation.md), [Phase 1](docs/plans/phase-1-policy-governance.md) (including what changed from its plan).

| Phase | Status |
|---|---|
| 0 Trust + foundation (auth, 5 roles, audit chain, jobs/outbox, bootstrap) | **done** |
| 1 Policy governance + Employee/Item migration | **done** |
| 2 AI control + Document migration | planned |
| 3 Retire graph record storage | planned |
| 4 Ops backbone + **Temporal** | planned |
| 5 Lifecycle (transfer, offboarding, rechecks, contractor/rehire, leave) | planned |
| 6 HR ops platform (manager view, SLAs, escalations, appeals, analytics) | planned |

## Stack
- **Backend:** FastAPI on Python 3.12, managed with `uv` (`backend/`).
- **Postgres 17:** the system of record ([ADR 0005](docs/adr/0005-postgres-system-of-record.md)). Plain SQL through `psycopg`, no ORM.
- **FalkorDB:** a read model only: the dependency graph, the relationship view, and the policy chunks the assistant retrieves ([ADR 0006](docs/adr/0006-graph-as-read-model.md)). Until Phases 1 to 3 finish, it still holds Employee, Item, Document and connector records.
- **Next.js 16 / React 19 / Tailwind 4 / shadcn on base-ui** (`frontend/`). The browser calls the API through the Next.js rewrite of `/api/*` to `BACKEND_URL`, so the app and the API share one origin and the session cookie works.
- **Temporal:** arrives in **Phase 4**, for durable workflows on top of Postgres ([ADR 0011](docs/adr/0011-temporal-orchestration.md)). It isn't used yet.

## Run it
```bash
docker compose up -d                                   # FalkorDB (:6379, UI :3001) + Postgres (:5432)
cd backend && uv run uvicorn main:app --port 8000      # bootstraps itself: migrations, users, seed data
cd frontend && npm install && npm run dev              # http://localhost:3000
```
No manual setup. Startup runs migrations and creates the admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD`. In dev it also creates seed users and prints them:

| Email | Role | Dev password |
|---|---|---|
| admin@azentio.local | admin | `admin-dev-pass` (or `ADMIN_PASSWORD`) |
| hrops@azentio.local | hr_ops | `hrops-dev-pass` |
| approver@azentio.local | approver | `approver-dev-pass` |
| approver2@azentio.local | approver (second sign-off for compliance rules) | `approver2-dev-pass` |
| manager@azentio.local | manager | `manager-dev-pass` |
| auditor@azentio.local | auditor | `auditor-dev-pass` |

`ENV=production` makes `DATABASE_URL`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` required and turns off the seed users. `/health` reports whether the process is up; `/ready` checks Postgres, the graph and the job worker.

## Checks (run before every commit)
```bash
cd backend && uv run ruff check .             # F + E9 only: undefined names, unused imports, syntax
python scripts/check_boundaries.py            # architecture guard (add --selftest to prove each rule bites)
cd backend && uv run python test_core.py      # rules, validation, ingestion, connectors, integrations
cd backend && uv run python test_phase0.py    # auth, permission matrix, audit chain, jobs, bootstrap
cd backend && uv run python test_phase1.py    # rule-set versions, approvals, safe replay, rollback, parity, projection
```
- Both use throwaway databases (`azentio_test*`, plus FalkorDB graph `test`) and never touch live data.
- Each new phase adds `test_phaseN.py`.
- `backend/.venv/Scripts/python scripts/smoke.py --base http://localhost:3000` runs headless Chrome against the running stack. For every role it signs in through the real form, checks the navigation, checks that allowed pages render and forbidden ones are gated, runs one workflow, and refreshes `docs/screenshots/`.
- Frontend: `cd frontend && npx next typegen && npx tsc --noEmit && npx eslint app components`.

## CI/CD
`.github/workflows/ci.yml` runs all of the above on every PR and push to `main`: `backend` (ruff, guard, the three suites, with Postgres and FalkorDB service containers), `frontend` (tsc, eslint), `smoke` (a **production build**, every role, the policy flow and rollback; screenshots as an artifact), then `images`. Images build on every run and are pushed to `ghcr.io/prateek1771/azentio-hr-{backend,frontend}:{sha,latest}` only on a push to `main`. Nothing deploys automatically. The frontend image bakes `BACKEND_URL` in at build time (Next serialises rewrites), default `http://backend:8000`.

**The boundary guard** (`scripts/check_boundaries.py`, stdlib) fails the build when:
1. Something writes Employee/Item **properties** to the graph outside `records.py`. Relationship writes to graph-owned nodes (documents, connector records) stay allowed until Phases 2–3.
2. A user-role check appears outside `identity/perms.py`, beyond the allowlisted data-scoping spots (`ROLE_ALLOWLIST`).
3. An already-applied migration is modified, removed or renamed.
4. `work_queue.open_item` uses a kind not registered in `work_queue.KINDS`.
5. Code updates, deletes or truncates `audit_log`, or disables its trigger (tests excepted).

## Where things live
| Path | Owns |
|---|---|
| `backend/main.py` | App wiring, the single auth middleware, core onboarding routes, `/health`, `/ready` |
| `backend/shared/db.py` | Pool, `tx()`, `fetch`/`fetch1`/`execute`, `migrate()`, `use_test_database()` |
| `backend/shared/jobs.py` | **Transactional outbox and short-task queue**: `enqueue(..., idempotency_key, conn=)`, `@handler`, backoff, `dead`, heartbeat |
| `backend/shared/bridge.py` | Which store each record type is read from; `parity()`/`switch()` for migrations |
| `backend/shared/bootstrap.py` | Startup steps, each safe to re-run (incl. the one-time Employee/Item migration gate) |
| `backend/records.py` | **The only way to read or write employees and checklist items.** Refreshes the graph projection in place after each write |
| `backend/policy/` | Rule-set versions: `store` (versions, drafts, diff), `replay` (pure `plan_diff` + `apply`), `service` (preview, approvals, publish/notify jobs), `routes` |
| `backend/migrations/NNN_*.sql` | Schema, applied at startup |
| `backend/identity/` | Users, sessions, scrypt, **`perms.py` (every permission rule)**, auth routes |
| `backend/audit.py` → `backend/audit_log/service.py` | Hash-chained, append-only audit log |
| `backend/work_queue.py` | The one exceptions inbox: anything that needs a human |
| `backend/checklist.py`, `validate.py`, `extract.py`, `pipeline.py`, `gmail.py`, `chat.py` | Rules engine, document checks, LLM extraction, intake, email, assistant (moving into modules in Phases 1 to 5) |
| `backend/connectors/`, `backend/integrations/` | Connector catalog, integration engine and agent |
| `frontend/app/(hr)/` | HR app pages; `frontend/app/portal/[token]` is the joiner portal (public, token-scoped) |
| `frontend/app/login`, `frontend/app/(hr)/users` | Sign-in screen; user and role management (admin) |
| `frontend/app/(hr)/rules/{draft,draft/preview,history}`, `approvals` | Rule governance: draft editor, impact preview, version history/rollback; Approval Center tabs |
| `frontend/components/auth.tsx` | `MeProvider`/`useMe`, `can(user, action)`, `RoleGate`, role labels. Hide write buttons with `can()`; the backend still enforces every rule |
| `frontend/components/app-shell.tsx` | `NAV`, with the roles that can see each page; user menu; page-level gate |
| `frontend/next.config.ts` | Rewrites `/api/*` to `BACKEND_URL` (same-origin cookie) |
| `scripts/check_boundaries.py`, `scripts/smoke.py` | Architecture guard; headless-Chrome UI smoke test |
| `.github/workflows/ci.yml`, `backend/Dockerfile`, `frontend/Dockerfile` | CI and the GHCR images |
| `docs/` | HLD, LLD, the hardening design, phase plans, ADRs |

## Rules for changes
- **Permissions are defined in exactly one place:** `backend/identity/perms.py`. Any path it doesn't list is denied. Add a rule there and a row to the matrix in `test_phase0.py`; never check roles inside a route. The one exception is data scoping, such as managers seeing only their own reports (`main._visible`).
- **Audit every human action** with `audit.record_audit(...)`. The actor comes from the signed-in user automatically. Pass `conn=` so the audit row commits in the same transaction as the change.
- **Background work has one owner** ([ADR 0011](docs/adr/0011-temporal-orchestration.md)):
  - Single side effects and local operational tasks are Postgres jobs, with an idempotency key on every side effect.
  - Anything that waits on people or timers becomes a Temporal workflow from Phase 4.
  - Always enqueue inside the transaction of the change that caused it.
- **Employees and items only through `records.py`.** Never write `Employee`/`Item` with Cypher; documents, events and connector records may still be read by joining the graph projection.
- **Rules change only by publishing a version** (`policy/`). Replay never deletes an item: not-started items are cancelled, done items or items with documents are kept as evidence.
- **One open draft at a time is a deliberate limit**, not a bug: two drafts of the same rule set would need merging. Revisit only if HR asks for parallel drafts.
- **Every `work_queue` kind is a contract** in `work_queue.KINDS`: an SLA, an escalation role and resolution steps. Register a kind before opening items of it.
- **Postgres is the system of record.**
  - Read migrating record types only through `shared/bridge.py`.
  - A type may switch to Postgres only after `parity()` passes.
  - Graph writes for a type stop only after every read path has switched.
- **Migrations are append-only.** Add the next numbered file and never edit one that has been applied. Each must be safe to run inside a transaction.
- **Humans only at policy gates.** Routine work is automatic. Exceptions go to `work_queue` with a role, not into a dashboard that someone has to watch.
- **Fail fast on bad config** with a clear message, never hang. Every external dependency shows up in `/ready`.

## Gotchas
- **This root `AGENTS.md` is authoritative.** `frontend/AGENTS.md` is generated by `next dev` and only warns that this Next.js version differs from older ones; if the two ever conflict, this file wins.
- `frontend/AGENTS.md` is written by `next dev`: this Next.js version has breaking changes, so read `frontend/node_modules/next/dist/docs/` before using a Next API you haven't checked.
- base-ui `DropdownMenuLabel` must sit inside a `DropdownMenuGroup`, otherwise the page crashes when the menu opens.
- base-ui menus open on pointer events. In browser automation, use real mouse input (CDP `Input.dispatchMouseEvent`), not `element.click()`.
- On Windows, stopping a background `npx next start` can leave the node process holding the port. Check the port's owner before starting another.

## Conventions
- **Keep it simple:** stdlib or an already-installed dependency before a new one. No ORM, no test framework, no speculative abstractions. Mark deliberate shortcuts with a `ponytail:` comment that names the limit and the upgrade path.
- **Tests:** plain `assert` self-checks, with one runnable check left behind for any non-trivial logic.
- **Commits:** small and focused, written in the imperative mood. **No `Co-Authored-By` lines or AI attribution in commits or PR bodies.**
- **Docs ship with the change:**
  - Behaviour changes update the LLD and the phase doc.
  - Boundary changes (a new store, service, external dependency or trust boundary) add an ADR.
  - Changes to commands, structure or phase status update this file and `CLAUDE.md`.
- Branches are named `phase-N-<name>`. A phase merges to `main` only when its exit criteria pass.
- **Secrets:** never commit `.env`. Document every new variable in `.env.example`.

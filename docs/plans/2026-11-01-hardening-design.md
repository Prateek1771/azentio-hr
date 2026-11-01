# Hardening design: from onboarding demo to governed HR operations

- **Status:** approved
- **Date:** 2026-11-01
- **Owner:** Prateek
- **Phase docs:** [0](phase-0-trust-foundation.md) · [1](phase-1-policy-governance.md) · [2](phase-2-ai-control.md) · [3](phase-3-retire-graph-records.md) · [4](phase-4-ops-backbone.md) · [5](phase-5-lifecycle.md) · [6](phase-6-hr-ops-platform.md)

## 1. Why this exists

A senior FDE and HR review found the core logic strong (the rules engine in `backend/checklist.py`, document validation in `backend/validate.py`, a coherent end-to-end flow). It also found that the system is not yet safe for real HR data:
- no sign-in and no permission checks
- rule changes take effect immediately with no version history
- AI output feeds decisions with too little control
- no retries, idempotency or alerting around external calls
- covers onboarding only, not the rest of the employee lifecycle

We checked each finding against the code before planning:

| Finding | What the code shows | Verdict |
|---|---|---|
| No auth or RBAC | `README.md` states the app has no sign-in; `main.py` has `allow_origins=["*"]` and no auth dependency on any route | **Confirmed** |
| No audit log | `backend/audit.py` exists and is append-only, but every human action is recorded as actor `"HR"` and rows live in the graph with no tamper evidence | **Partly built** |
| AI decides with too little control | `validate.py:68` sends low-confidence extractions to `Under Review`, and an Approvals page exists. But high-confidence ID or visa documents are auto-accepted, and no decision records the model or threshold that produced it | **Partly built** |
| Rules ungoverned | `PUT /api/rules/{rid}` edits in place and every joiner is re-planned; there are no versions, approvals, diffs or rollback | **Confirmed** |
| No ops backbone | No retry, backoff or idempotency in our code. The Gmail poller and scheduler are bare `while True` loops in `main.py` | **Confirmed** |
| Onboarding only | Correct. Lifecycle is new product scope, not a fix | **Confirmed (scope)** |

## 2. Operating principle: minimal human intervention, not "no humans"

Routine work runs on its own. Humans are involved only where policy or risk requires it, and those points are deliberate gates, not manual monitoring.

**Automatic**
- **Setup:** schema migrations, default roles, the first admin from env, dev seed users, the default rule set, and the job worker. Startup runs it all with no wizard and no hand-run SQL.
- **Background work:** every external call (Gmail, LLM, connectors), reminders, escalations and document routing runs as a job, or from Phase 4 as a Temporal workflow or activity. Both retry with backoff, and work is dead-lettered only after repeated failure.
- **Routing:** each document goes to auto-accept or human review based on its risk tier. Joiners starting soon who are affected by a change get flagged automatically. Overdue work escalates through jobs.
- **Self-awareness:**
  - `/ready` fails when Postgres is down, so the app never claims to work when it can't.
  - An unavailable LLM sends documents to review mode rather than failing silently.
  - A stalled queue raises an alert.
  - Missing required config stops startup with a clear message.

**Human, by policy**
- Approving rule changes. Compliance-flagged rules need two approvers.
- Decisions on high-risk documents, overrides and rejections.
- Joiners who are close to their start date when a rule change hits them.
- Exceptions and appeals.

Every exception is logged, assigned to a role or person, given an SLA, and shown in one inbox (`work_queue`).

## 3. Decisions

Each decision lists what we chose and why. The reasoning comes from the review discussion.

### 3.1 Roles: five, with bounded scopes
| Role | Scope |
|---|---|
| `admin` | System maintenance and user management: users, connectors, integrations, system settings, the DLQ. Does not run onboarding day to day. |
| `hr_ops` | Runs onboarding: joiners, checklists, templates, rule drafts, the exception queue. |
| `approver` | Approves rule versions and high-risk documents. Cannot author the rules they approve. |
| `manager` | Read-only on their own reports, and can comment. |
| `auditor` | Read-only on everything, including the audit log. |

Employees keep using their unguessable per-employee portal token; they don't get accounts. Fine-grained custom permissions were rejected as premature. A 3-role model was rejected because it has no manager view and no separation between author and approver.

### 3.2 Rule changes: draft → preview → approve
Rules set employees' obligations, due dates and access, so a rule change is a policy change, not a field edit.
- **Draft:** HR Ops edits a new version, which has no live effect.
- **Preview:** shows which joiners gain or lose which items, which due dates move, and who becomes blocked or overdue. Filterable by department, role, location, joining window and status.
- **Approve:** someone other than the author publishes the draft. Rules flagged `compliance` need two distinct approvers.
- **Versions** are immutable and record author, approvers, timestamps, reason and diff. Rollback publishes the previous version again through the same approval path and replays the affected joiners.

Rejected alternatives:
- Versioning alone still lets live changes take effect immediately and unsafely.
- Approving only "risky" rules invites endless debate over what counts as risky.

### 3.3 In-flight joiners when a version is published
- New required items are added to the joiner's checklist, and the joiner is notified.
- Items the new version drops are cancelled only if they're not done. Completed items, accepted items and uploaded documents are kept as evidence. Evidence is never deleted.
- Joiners starting within `NEAR_START_DAYS` (default 5) aren't changed automatically. A `near_start_change` exception goes to HR Ops instead.
- Every replay is recorded: which version, who, when, which joiners, and the diff for each joiner.

### 3.4 AI autonomy: tiered by document risk
| Tier | Document types (default) | Auto-accept when |
|---|---|---|
| `low` | address proof, bank letter or cancelled cheque, education certificate | every deterministic check passes **and** confidence ≥ 0.90 |
| `high` | government ID, passport, visa, work authorisation, I-9, BGV evidence | never. A human decides, with the AI's findings shown side by side and a reason required |

- Tiers and thresholds are versioned and approved like rules.
- A random 10% of auto-accepted documents go to a spot-check queue, which measures AI accuracy over time. That's a quality signal, not the main control.
- Every automated decision records the model, prompt version, confidence, checks run and threshold version.
- Rejected alternatives:
  - "Human approves everything" doesn't scale, and teams start working around it.
  - "Sampling only" doesn't protect high-risk categories.

### 3.5 Lifecycle scope
In priority order:
1. Role or department transfer
2. Offboarding
3. Compliance rechecks for expiring documents
4. Contractor vs employee, and rehire
5. Leave and return to work

All five share one `lifecycle_case` model and reuse the policy engine.

### 3.6 Architecture: modular monolith, Postgres as system of record
One deployable FastAPI app, split into domain modules with explicit service interfaces. Each module owns its own tables; other modules call its `service.py`, never its tables directly.

```
backend/
  shared/        db (pool, tx, migrate), jobs, bridge, bootstrap, clock
  identity/      users, roles, sessions, permission table
  audit/         hash-chained append-only log, lineage queries
  policy/        rule and threshold versions, drafts, preview, approvals, rollback
  documents/     intake, extraction, validation, risk tiers, review
  lifecycle/     onboard, transfer, leave, offboard, rechecks, rehire
  notifications/ Gmail send and poll (job-backed), reminders, escalations
  integrations/  connectors and integrations, idempotent upserts
  analytics/     readiness, SLA, exceptions, AI quality
```

**Storage split**
- **Postgres** holds everything authoritative: users, sessions, audit, jobs, approvals, the exception queue, rule versions, employees, checklist items, documents and review decisions, and later connectors and integrations.
- **FalkorDB** holds only what it's good at: the dependency graph, the relationship view, and the policy chunks the assistant retrieves. It's a read model, never a source of truth.

Why not microservices: the hard problems here are governance, auditability and correctness, not throughput. Splitting into services would add distributed transactions, harder auditing, more failure points and slower policy rollout, for a single team. The module boundaries are where services would be split off later if team ownership or load ever justify it.

### 3.7 Postgres choices
- **Driver:** `psycopg[binary,pool]` with plain SQL, no ORM. Migrations are numbered `.sql` files applied at startup and tracked in `schema_migrations`. No Alembic.
- **Job queue:** a `jobs` table where workers claim jobs with `FOR UPDATE SKIP LOCKED`, so two workers never take the same job. It has a unique `idempotency_key`, exponential backoff, and a `dead` state.
  - A job is enqueued in the same transaction as the change that triggers it, so a crash can't lose the job or leave the change half-done.
  - No Redis Streams, Celery or broker.
- **Audit:** append-only, with each row storing `hash = sha256(prev_hash + canonical row)`.
  - Writes go through a database role that can only INSERT and SELECT the audit table.
  - `verify_chain()` detects any edited, deleted or reordered row.

### 3.8 Workflow orchestration: Temporal from Phase 4 ([ADR 0011](../adr/0011-temporal-orchestration.md))
Temporal sits **on top of** Postgres; it doesn't replace it. It's introduced in Phase 4, once the trust foundation is in place, to run the flows that wait on people and timers:
- rule draft → approval → publish
- lifecycle cases
- the escalation ladder
- compliance rechecks
- waiting on appeals

**What changes:**
- **The Phase 0 `jobs` table becomes the transactional outbox and short-task queue.** A business change and its "start or signal workflow" row commit in one transaction. The jobs worker hands that row to Temporal, and the idempotency key becomes the workflow ID.
- **Business state stays in Postgres.** Workflows and activities write it only through the module services.

Each type of background work has exactly one owner:

| Work | Owner |
|---|---|
| Waiting on a person, timers, multi-day state machines | Temporal workflow |
| A single side effect inside a workflow | Temporal activity |
| A single side effect outside any workflow | Postgres job |
| Handing a committed change to Temporal | Postgres outbox row, relayed by the jobs worker |
| Local operational tasks | Postgres job |

```
HR action ──tx──▶ Postgres: change + outbox row ──relay──▶ Temporal workflow
                                                              ├─ activities: Gmail, LLM, connectors
                                                              └─ timers, waits for approval or return signals
                                                       state written back via module services ──▶ Postgres
```

In Phases 1 and 2, approval waits are plain Postgres state. Phase 4 wraps them in workflows without changing the tables.

## 4. Moving data from the graph to Postgres

Each record type moves together with the feature that needs it in Postgres.

| Phase | Record types moved | Why then |
|---|---|---|
| 0 | none; new tables only (identity, audit, jobs, approvals, work_queue) | Foundation |
| 1 | Employee, Item, Rule | Policy preview has to compute the effect on joiners and checklists inside one transaction |
| 2 | Document, review decisions | The review queue and decision tracking need them |
| 3 | Connectors, integrations, runs; graph-backed record storage removed | The graph becomes a read model only |

**The bridge (`backend/shared/bridge.py`)**
- `SOURCE = {"Employee": "graph", "Item": "graph", ...}` sets one read source per record type. Modules read through the bridge, never from both stores.
- A record type may only be switched to Postgres **after `parity(type)` passes**: equal row counts and an identical checksum over the canonical rows.

**Steps for each record type**
1. Dual-write: every write goes to both stores.
2. Backfill script.
3. Switch the read source in the bridge once parity passes.
4. Stop graph writes, only after **every** read path for that type has switched.

Steps 1 to 3 can be undone by reverting a one-line change in the bridge.

**Rebuilding the graph.** The graph is rebuilt on purpose, never by background sync:
- `rebuild_graph()` rebuilds everything.
- `rebuild_graph(eid)` rebuilds one employee's subgraph inside the same request as the write that changed them.

That way the graph can't drift from Postgres unnoticed.

## 5. Testing strategy

Keep tests light and runnable: plain `assert` checks, no test framework.
- **Existing:** `backend/test_core.py` stays as the fast self-check.
- **One file per phase:** `backend/test_phaseN.py`. Each runs against a throwaway Postgres database (`azentio_test`) and graph (`FALKORDB_GRAPH=test`).
- **Must-have checks:**
  - every role against every route group
  - tampering with an audit row is detected
  - an author can't approve their own draft, and compliance rules need 2 approvers
  - **rollback restores the effective rule set without deleting evidence or losing approval history**
  - in-flight replay keeps completed items and documents
  - a retried job never repeats its side effect, and a job is dead-lettered after N failed attempts
  - two workers never claim the same job
  - a high-risk document is never auto-accepted, whatever its confidence
  - migration parity (row counts and checksum)
  - running bootstrap twice is the same as running it once
- **UI smoke test:** `scripts/smoke.py` drives headless Chrome. It logs in as each seed role, checks that the nav matches the role, confirms the allowed pages render and the forbidden ones are blocked, and runs **at least one representative workflow** for each role. In Phase 0 that's HR Ops opening an employee and previewing the request email. Screenshots go to `docs/screenshots/`.

## 6. Rollout
- Each phase gets its own branch, `phase-N-<name>`, with small commits for each sub-step. A phase merges to `main` only when its exit criteria pass.
- An env switch such as `POLICY_APPROVALS=on` is added only when a half-finished phase has to sit on `main`, and is removed when the phase completes. No feature-flag framework.
- **Seed data:**
  - one user per role (`admin@`, `hrops@`, `approver@`, `manager@`, `auditor@`, all `@azentio.local`). Dev passwords are printed at startup; in production they come from env.
  - rule history: v1 published, a pending v2 draft, and one compliance rule waiting on its second approval
  - one document in each review state
  - one of each lifecycle case (Phase 5)

## 7. Definition of done, per phase
- Exit criteria in the phase doc pass.
- `test_core.py` and the phase's checks pass.
- `scripts/smoke.py` passes for all five roles; screenshots are refreshed.
- Every new action shows up in the audit log under a named user.
- The docs (this file, the phase doc, LLD and ADRs) match what was built.

## 8. Decision records
New ADRs [0005](../adr/0005-postgres-system-of-record.md) to [0011](../adr/0011-temporal-orchestration.md) record these decisions. ADR 0005 supersedes the storage part of [0002](../adr/0002-graph-database.md).

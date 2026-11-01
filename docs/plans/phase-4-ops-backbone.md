# Phase 4: Ops backbone

- **Status:** planned
- **Depends on:** Phase 0 (`shared/db.py`, `shared/jobs.py`, `work_queue`, `/health`, `/ready`, `audit/service.record`). Phases 1–3 (Employee, Item, Document, and connector records in Postgres).
- **Design:** [2026-11-01-hardening-design.md](2026-11-01-hardening-design.md) · **ADRs:** [0007](../adr/0007-postgres-job-queue.md), [0011](../adr/0011-temporal-orchestration.md)

## Goal
All routine background work runs on its own and retries on its own. A person only gets involved when something has failed repeatedly, and then it reaches them as an assigned exception, not as something they had to go looking for.

This phase also brings in **Temporal** as the orchestration layer for durable, multi-step work. Temporal sits on top of Postgres and doesn't replace it:
- Postgres stays the system of record.
- The Phase 0 `jobs` table stays as the **transactional outbox and short-task queue**.
- Temporal runs workflows: schedules, timers, waits and multi-step flows.

The invariant this phase establishes: **every external call (Gmail, LLM, connectors) is job-backed or activity-backed and idempotent, so retries cannot duplicate side effects.**

## Who owns background work
Every piece of background work has exactly one owner. Neither Temporal nor the jobs table is an authority on HR state: business state always lives in Postgres, and workflows and activities read and write it only through the module services (`policy/`, `documents/`, `lifecycle/`, `notifications/`).

| Work | Owner | Examples |
|---|---|---|
| Durable, multi-step or waiting work: human approval waits, timers, multi-day state machines | **Temporal workflow** | Gmail poll schedule, per-integration run schedules (this phase); lifecycle cases (Phase 5); escalation ladder, appeals (Phase 6); rule draft → approval → publish (wrapped here) |
| A single side effect with retries | **Temporal activity** when it runs inside a workflow; otherwise a **Postgres job** | Gmail send, LLM extraction, connector push, access grant/revoke |
| Handing a committed change to Temporal | **Postgres outbox**: `jobs` kinds `temporal.start` / `temporal.signal`, relayed by the jobs worker | The job's `idempotency_key` becomes the Temporal workflow id, so a repeat is a no-op |
| Local operational tasks | **Postgres job** (`jobs.recurring`) | alert evaluation, backup, reminder sweep, graph rebuild, audit import |

Alert evaluation and backups stay on Postgres jobs on purpose. They must keep working while Temporal is down, because one of the things they report is that Temporal is down.

## The problem today
- `main.lifespan` starts two bare `asyncio` loops: `gmail.poll_forever(60)` and `main._scheduler()`. If either raises in a way their `try` doesn't catch, or the process restarts mid-run, work is lost silently.
- `gmail._fetch_new` adds the `azentio-processed` label to a message *while fetching it*, before `pipeline.intake` has run. A crash between labelling and intake loses that joiner's attachments for good, because the next poll skips labelled mail.
- `gmail.send` has no deduplication. `POST /api/escalate` re-sends every overdue escalation on every click.
- `pipeline.intake` calls `extract.extract` inline. If the LLM is down, the document gets confidence 0 and goes to Under Review, which is safe. But there's no retry once the LLM comes back, and no signal that it happened at scale.
- `integrations.engine.run_due` claims by moving `next_run_at`, which is safe within one process but can't be seen or retried. `notify` swallows failures (`notify.failed`), and nothing follows up.
- There are no alerts, no backups and no CI.

## Scope
1. Job handlers for every external side effect, each with an idempotency key.
2. An outbox for email, so a retried send can never become a second email.
3. Fixing the Gmail poll ordering: enqueue first, label after.
4. LLM extraction as a job that degrades to human review.
5. No more `while True` loops. `gmail.poll_forever` becomes a Temporal schedule, and `main._scheduler` becomes one Temporal schedule per active integration. Reminders, alert evaluation and backups become recurring Postgres jobs.
6. Temporal setup: the `temporal` and `temporal-ui` services, the `temporalio` SDK, a worker under `backend/workflows/` on task queue `azentio`, and the outbox relay (`temporal.start`, `temporal.signal`).
7. The approval waits from Phases 1 and 2 (rule draft → approval → publish, document review) get wrapped in workflows. Their Postgres tables don't change; see "Wrapping the Phase 1–2 approval waits" below.
8. DLQ screen: retry, discard, and inspect the payload and error history.
9. Dead-lettered jobs and failed workflows open `ops` exceptions in `work_queue` (the producer exists from Phase 0; this phase adds the screen and the remaining kinds).
10. Alerts: queue stall, poller stall, DLQ growth, workflow failures, Temporal unreachable, `/ready` failing. Sent by email and an optional webhook, deduplicated.
11. Metrics endpoint and an ops dashboard card.
12. Nightly `pg_dump` with retention, plus a restore runbook that has actually been tested.
13. GitHub Actions CI: self-checks, type check, lint, build.

## Non-goals
- No other queue or broker (Celery, Redis Streams, SQS). Postgres jobs handle the outbox and short tasks, and Temporal handles orchestration.
- Temporal never stores business state. A workflow's history is an execution log, not a record: if it's lost, the Postgres state is still complete, and workflows can be restarted from it.
- No Prometheus, Grafana or OpenTelemetry stack. A JSON metrics endpoint and structured logs come first; add a scraper when a second instance exists.
- No multi-instance deployment. The design is safe with several workers (`SKIP LOCKED`), but this phase runs one process.
- No point-in-time recovery or WAL archiving. A nightly dump fits the recovery target agreed below.
- No CD or deployment pipeline (deployment is out of scope for this round).

## Temporal setup

### Services
`docker-compose.yml` gains two services:
- **`temporal`:** the `temporalio/auto-setup` image, using the existing `postgres` container with its own database `temporal`. The visibility database is `temporal_visibility`. It's set up with `DB=postgres12`, `POSTGRES_SEEDS=postgres`, and the same credentials as the `postgres` service. It exposes port `7233` and depends on the `postgres` service being healthy.
- **`temporal-ui`:** the `temporalio/ui` image on port `8233`, pointed at `temporal:7233`. Only admins need it. The Ops → Health screen links to it.

**Production.** The target is chosen by env, with no code change:
- **Self-hosted:** the same images. `TEMPORAL_ADDRESS=temporal:7233`, `TEMPORAL_NAMESPACE=default`.
- **Temporal Cloud:** `TEMPORAL_ADDRESS=<ns>.<acct>.tmprl.cloud:7233`, `TEMPORAL_NAMESPACE=<ns>.<acct>`, plus `TEMPORAL_API_KEY`, or `TEMPORAL_TLS_CERT` / `TEMPORAL_TLS_KEY` for mTLS.

### Code
- **`backend/workflows/client.py`:** `get_client()` connects lazily from the env above and caches the client.
- **`backend/workflows/worker.py`:** runs a `temporalio.worker.Worker` on task queue `azentio` that registers every workflow and activity. In dev it starts as an asyncio task in `main.lifespan`, next to `jobs.worker()`. In production it runs as its own process: `uv run python -m workflows.worker`.
- **`backend/workflows/activities.py`:** thin wrappers that call the module services. A workflow never calls a service itself, because workflow code must stay deterministic. The wrappers:
  - `gmail_list_new`, `gmail_ingest_message`
  - `doc_intake`, `doc_extract`
  - `send_email` (goes through the outbox, as in "Making `email.send` truly exactly-once" below)
  - `run_integration`, `connector_push`, `access_perform`
- **`backend/workflows/gmail.py`:** `GmailPollWorkflow`. It runs `gmail_list_new`, then for each message runs `gmail_ingest_message`, and only then `mark_processed`.
- **`backend/workflows/integrations.py`:** `IntegrationRunWorkflow(iid)`, plus `sync_schedules()`, which creates, updates or deletes one Temporal schedule per active integration from its `schedule` settings. `sync_schedules()` is called when an integration's schedule is saved, activated or paused, and once at startup.
- **`backend/workflows/approvals.py`:** `RuleVersionApprovalWorkflow` and `DocumentReviewWorkflow`. See "Wrapping the Phase 1–2 approval waits" below.

### Activity retry policies
Activities use Temporal retry policies instead of the jobs table's backoff:

| Activity | Initial / max interval | Max attempts | Non-retryable |
|---|---|---|---|
| `send_email` | 1 min / 2 h | 8 | `gmail.InvalidRecipient` |
| `doc_extract` | 2 min / 1 h | 6 | none (`llm.Unavailable` retries; after the last attempt the workflow opens the `ai_unavailable` review exception) |
| `gmail_list_new`, `gmail_ingest_message` | 30 s / 10 min | 5 | none |
| `run_integration`, `connector_push`, `access_perform` | 1 min / 30 min | 6 | `RecordRejected` (a business rejection, not an outage) |

Each activity keeps the same idempotency key it would have had as a job (table below), and side effects are keyed on it. That makes an activity retry exactly as safe as a job retry. **Rate limits:** a Gmail 429 or an LLM limit raises `ApplicationError(next_retry_delay=…)`, which fills the role of the jobs table's `RetryLater`.

### Outbox relay
- A change that needs a workflow writes its business rows **and** `jobs.enqueue("temporal.start", {...}, idempotency_key=<workflow id>, conn=c)` in one `tx()`. The payload is `{workflow, args, task_queue}`. Signals work the same way: `temporal.signal` with payload `{workflow_id, signal, args}` and key `signal:<workflow id>:<signal>:<discriminator>`.
- The `temporal.start` handler calls `client.start_workflow(workflow, *args, id=job["idempotency_key"], task_queue="azentio", id_reuse_policy=REJECT_DUPLICATE)`. `WorkflowAlreadyStartedError` counts as **success**: that's how a retry or a duplicate becomes a no-op.
- The `temporal.signal` handler calls `get_workflow_handle(workflow_id).signal(...)`. If the workflow has already finished, that also counts as success, and the job records it.
- **If Temporal is down:** relay jobs retry with the normal job backoff. The business change is already committed, and the workflow starts as soon as Temporal is back. Nothing is lost and nothing runs twice.

No schema change is needed: the Phase 0 `jobs` table already provides the transactional enqueue, the unique key and the dead state.

### Wrapping the Phase 1–2 approval waits
In Phases 1 and 2, approval waits are plain Postgres state: `approval_requests`/`approvals`, and the `work_queue` rows. Phase 4 adds workflows on top **without changing those tables**:
- **`RuleVersionApprovalWorkflow(version_id)`:**
  - Started by the outbox when a draft is submitted.
  - Waits for the `decision` signal. The approve/reject endpoint sends that signal through the outbox, in the same `tx()` that writes `approvals`.
  - When enough approvals are in, it runs the `policy_publish` activity, which is the existing Phase 1 publish and replay logic.
  - A timer of `APPROVAL_SLA_HOURS` escalates a stale request through `work_queue`.
- **`DocumentReviewWorkflow(doc_id)`:** the same pattern for high-risk document reviews.
- **Backfill:** every pending `approval_requests` row gets a `temporal.start` enqueued, keyed by its subject. Because of the key, running the backfill twice is safe.

## Idempotency key scheme
Every job is enqueued with an `idempotency_key`, and every activity carries the same key in its input. The `jobs.idempotency_key` UNIQUE constraint from Phase 0 makes a second `enqueue` with the same key return the existing job instead of adding a new one.

| Job kind | Key | Side effect guarded |
|---|---|---|
| `temporal.start` | the workflow id, e.g. `integration-run:<id>:<scheduled epoch>`, `approval:rule_version:<id>`, `case:<case id>` | one workflow per intent |
| `temporal.signal` | `signal:<workflow id>:<signal>:<discriminator>` (e.g. `signal:approval:rule_version:7:decision:<approver id>`) | one signal per decision |
| `gmail.poll` (schedule run) | `gmail:poll:<minute bucket>` (the workflow id) | one poll per minute, however many workers |
| `gmail.ingest` | `gmail:msg:<gmail message id>` | one intake per received message |
| `doc.intake` | `gmail:att:<message id>:<attachment id>` or `portal:<sha256 of bytes>:<employee id>` | one Document per attachment |
| `doc.extract` | `doc:<document id>:extract:v<extract prompt version>` | one LLM extraction per document per prompt version |
| `email.send` | `email:<purpose>:<entity id>:<discriminator>` (e.g. `email:request:E1001:tpl_standard`, `email:correction:doc_ab12`, `email:escalation:it_99:2026-11-03`) | one email per purpose per entity (per day for escalations) |
| `integration.run` | `integration-run:<id>:<scheduled epoch>` (the workflow id) | one scheduled run per slot |
| `connector.push` | `connector:<instance>:<record id>:<payload sha256>` | one upsert per distinct payload |
| `access.perform` | `access:<app instance>:<action>:<email>:<resource>` | one grant or revoke (also repeat-safe in `AppConnector._existing`) |
| `reminder.joiner` | `reminder:<employee id>:<item id>:<date>` | at most one reminder per item per day |
| `escalation.check` | `escalation:tick:<minute bucket>` | one sweep per minute |
| `alert.send` | `alert:<kind>:<subject>:<hour bucket>` | at most one alert per kind per subject per hour |

A key describes the *intent*, not the attempt. A retry reuses the same job row, so the key never changes between attempts.

### Making `email.send` truly exactly-once
The Gmail API has no idempotency key, and a crash after Gmail accepted the message but before we recorded it would cause a duplicate on retry. Two layers prevent that:
1. **Outbox row first.** Inside the same `tx()` as the business change, insert `outbox(key, to, subject, html, thread_id, status='pending')` and enqueue `email.send` with the same key. The handler does nothing if `outbox.status='sent'`.
2. **Deterministic `Message-ID`.** `gmail._send_sync` sets `msg["Message-ID"] = f"<{sha256(key)[:32]}@azentio.local>"`. Before sending, a retry with `attempts > 1` searches `rfc822msgid:<that id>` with `in:sent` through `users().messages().list`. If it's found, it marks the outbox row sent with that Gmail id and does not send again.

The second layer covers the gap the first can't: Gmail accepted the message but the process died before the `UPDATE outbox`.

## Schema

```sql
-- 020_outbox.sql
CREATE TABLE outbox (
  id            bigserial PRIMARY KEY,
  key           text NOT NULL UNIQUE,              -- same as the job's idempotency_key
  channel       text NOT NULL DEFAULT 'gmail' CHECK (channel IN ('gmail','simulated','webhook')),
  purpose       text NOT NULL,                     -- request | correction | escalation | reminder | notification | alert
  employee_id   text,
  to_addr       text NOT NULL,
  subject       text NOT NULL,
  html          text NOT NULL,
  thread_id     text,
  message_id    text NOT NULL,                     -- the deterministic RFC 822 Message-ID
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','suppressed')),
  provider_ref  text,                              -- Gmail message id once sent
  provider_thread text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  sent_at       timestamptz
);
CREATE INDEX outbox_employee ON outbox (employee_id, created_at DESC);
CREATE INDEX outbox_pending ON outbox (status) WHERE status = 'pending';

-- 021_gmail_cursor.sql
CREATE TABLE gmail_messages (                      -- every message we have seen, so labels are an optimisation, not the truth
  gmail_id      text PRIMARY KEY,
  thread_id     text NOT NULL,
  from_addr     text NOT NULL,
  subject       text,
  employee_id   text,                              -- null = unmatched
  attachments   int NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','ingested','unmatched','failed')),
  seen_at       timestamptz NOT NULL DEFAULT now(),
  ingested_at   timestamptz
);

-- 022_heartbeats.sql
CREATE TABLE heartbeats (                          -- one row per loop; /ready and the stall alert read these
  name          text PRIMARY KEY,                  -- gmail.poll | temporal.worker | escalation.check | backup (the job worker keeps Phase 0's worker_heartbeat)
  last_ok_at    timestamptz,
  last_error_at timestamptz,
  last_error    text,
  meta          jsonb NOT NULL DEFAULT '{}'
);

-- 023_alerts.sql
CREATE TABLE alerts (
  id            bigserial PRIMARY KEY,
  kind          text NOT NULL,                     -- queue_stall | poller_stall | dlq_growth | not_ready | backup_failed | llm_down | temporal_down | workflow_failed
  subject       text NOT NULL DEFAULT '',          -- e.g. job kind or dependency name
  severity      text NOT NULL CHECK (severity IN ('warning','critical')),
  message       text NOT NULL,
  opened_at     timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz,
  notified      boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX alerts_one_open ON alerts (kind, subject) WHERE resolved_at IS NULL;

-- 024_jobs_ops.sql  (extends the Phase 0 jobs table)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_history jsonb NOT NULL DEFAULT '[]';   -- [{attempt, at, error, traceback_tail}]
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS discarded_by text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS discarded_reason text;
-- Phase 0's 003_jobs.sql already has: state job_state ('queued','running','done','dead') and index jobs_ready (run_at) WHERE state='queued'.
-- A discarded job stays 'dead' with discarded_by set; no new enum value.
CREATE INDEX IF NOT EXISTS jobs_dead ON jobs (kind, finished_at DESC) WHERE state = 'dead';

-- 025_backups.sql
CREATE TABLE backups (
  id            bigserial PRIMARY KEY,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  file          text,
  bytes         bigint,
  sha256        text,
  status        text NOT NULL CHECK (status IN ('running','ok','failed')),
  error         text,
  restore_tested_at timestamptz
);
```

`OutboundMessage` graph nodes written by `integrations/delivery.record` move into `outbox` (`purpose='notification'`, `channel` set from `delivered_by`). The integration "Sent messages" tab reads `outbox WHERE purpose IN (...) AND key LIKE 'email:%:<integration id>%'`.

## Job handlers and workflow steps
Job handlers live under `backend/jobs_handlers/`, one file per area, each registered with `@jobs.handler(kind)`. Workflow steps live in `backend/workflows/`. Both call the same service functions, so moving a step between a job and an activity changes only the wrapper. Every handler and activity must be safe to call twice with the same input.

| Kind | Runs as | Trigger | What it does | Max attempts / backoff | On final failure |
|---|---|---|---|---|---|
| `temporal.start` / `temporal.signal` | job (outbox relay) | written in the same `tx()` as the business change | Starts or signals the workflow, as described in "Outbox relay" above. An already-started workflow, or a signal to a finished one, counts as success. | 10 / 5 s × 2ⁿ, cap 10 min | `ops` exception "Workflow could not be started", plus alert `temporal_down` if many fail at once |
| `gmail.poll` | Temporal schedule `gmail-poll` (every 60 s, overlap policy `SKIP`) → `GmailPollWorkflow` | schedule | Activity `gmail_list_new` (the old `_fetch_new`, without fetching attachments or applying labels). For each message: upsert into `gmail_messages`, then run activity `gmail_ingest_message`. Heartbeat `gmail.poll`. | activity policy above | The workflow fails, which opens an `ops` exception "Gmail polling failing" and the `poller_stall` alert. The next scheduled run starts anyway. |
| `gmail.ingest` | activity in `GmailPollWorkflow` | per message | Match the employee by thread, then by sender (the logic from `gmail.poll`). Run `doc_intake` for each attachment. **Only after that commits**, label the message. Sets `gmail_messages.status`. | 5 | `ops` exception with the message link |
| `doc.intake` | activity (email path) or job (portal and HR uploads) | `gmail.ingest`, portal upload, HR upload | Downloads the attachment (email) or reads the staged file (portal). Then runs `pipeline.save_document` (split from `pipeline.intake`: save + insert Document, no extraction) and enqueues `doc.extract`. | 5 | `ops` exception; the document stays `Submitted` and visible |
| `doc.extract` | job, or an activity inside `DocumentReviewWorkflow` | `doc.intake`, reprocess button | Runs `extract.extract`, then `pipeline.decide` (the validate + risk-tier routing from Phase 2). If `llm.unavailable_reason()` is set, it raises `RetryLater` (it doesn't count as failure for the first 3 tries). | 6 / 2 min × 2ⁿ, cap 1 h | Opens a **`doc_review` exception** with reason `ai_unavailable` instead of an `ops` one: the human reviews the document by hand. No document is ever stuck. |
| `email.send` | job, or the `send_email` activity inside a workflow | outbox insert | Exactly-once send as described above. Simulated when `gmail.configured()` is false. | 8 / 1 min × 2ⁿ, cap 2 h | `ops` exception; `outbox.status='failed'`; shown on the employee timeline |
| `integration.run` | Temporal schedule `integration:<id>` → `IntegrationRunWorkflow`; "Run now" goes through the outbox with `temporal.start` | each integration's own schedule, which replaces `main._scheduler` and `integrations.engine.run_due` | Activity `run_integration`, which calls `integrations.engine.run_integration(iid, trigger)`. Its `AlreadyRunning` counts as success: another run already owns the slot. | activity policy above | `ops` exception on the integration, and `notify(..., "run_failed")` |
| `connector.push` | job, or an activity inside a workflow | engine `_create_employee`, HR record completion | `HrPortal.upsert_record`. The key includes a hash of the payload, so an identical retry is a no-op. | 6 | `ops` exception on the employee |
| `access.perform` | job, or an activity (lifecycle workflows in Phase 5) | `_provision_access`, Phase 5 offboarding | `AppConnector.perform(...)`, already repeat-safe through `_existing` | 6 | `ops` exception "grant/revoke X for Y failed"; the access result is marked `failed` |
| `reminder.sweep` | recurring job | daily 09:00 in the org's timezone | For every pending joiner item due within 3 days with no upload, enqueues `email.send` with purpose `reminder`, key per item per day. | 3 | alert |
| `escalation.check` | recurring job (Phase 6 replaces it with per-item workflows) | every 15 min | Replaces the body of `POST /api/escalate` (`main.escalate`). For every overdue blocking task with an owner, it enqueues `email.send` with key `email:escalation:<item>:<date>`, so an owner gets at most one email a day per item. Phase 6 extends this into the escalation ladder. | 3 | alert |
| `alert.evaluate` | recurring job (must work while Temporal is down) | every 60 s | Evaluates the alert rules below, opens and resolves `alerts` rows, and enqueues `alert.send` for new ones. | 3 | logged only (avoids an alert loop) |
| `alert.send` | job | `alert.evaluate` | Email to `ALERT_EMAILS` through the outbox (purpose `alert`), plus a POST to `ALERT_WEBHOOK_URL` if set (JSON, Slack-compatible `{"text": ...}`). | 5 | logged only |
| `backup.nightly` | recurring job | 02:00 UTC | `pg_dump -Fc` to `BACKUP_DIR`, sha256, keep 14 daily and 8 weekly backups, write a `backups` row. Snapshots the FalkorDB AOF file if it's reachable. | 3 / 10 min | alert `backup_failed` (critical) |

**Recurring jobs.** Local operational tasks are scheduled by `jobs.recurring(kind, every=…|cron=…)`. On each tick it enqueues the next occurrence with a time-bucketed key, so two processes can't both schedule it.

**Temporal schedules.** These replace the two `while True` loops in `main.lifespan`: `gmail-poll`, plus one schedule per integration. They're created idempotently by `workflows.integrations.sync_schedules()` and `workflows.gmail.ensure_schedule()` at startup.

**`RetryLater` vs failure.** A handler raises `jobs.RetryLater(seconds, reason)` for expected waits: LLM rate limits (`llm._note_limit` already detects them) and Gmail 429s. These don't add to `error_history`, and after 3 occurrences they start counting against `max_attempts`. Any other exception is a failure. The activity equivalent is `ApplicationError(next_retry_delay=…)`.

## Alert rules (`alert.evaluate`)
| Kind | Condition | Severity | Resolves when |
|---|---|---|---|
| `queue_stall` | oldest `queued` job with `run_at < now() - 5 min`, or `worker` heartbeat older than 2 min | critical | condition false for 2 consecutive evaluations |
| `poller_stall` | Gmail configured and `gmail.poll` heartbeat `last_ok_at` older than 10 min | critical | the next successful poll |
| `dlq_growth` | more than 5 jobs moved to `dead` in the last hour, or any `dead` job older than 24 h still unhandled | warning; critical above 20 | DLQ count back under threshold |
| `not_ready` | `/ready` check failing for more than 3 min (same function, called in-process) | critical | ready again |
| `llm_down` | `llm.unavailable_reason()` non-empty for more than 15 min | warning | LLM back |
| `backup_failed` | last `backups` row failed, or no `ok` backup in 26 h | critical | next ok backup |
| `temporal_down` | Temporal health check failing for more than 3 min, or more than 3 `temporal.start` jobs in retry | critical | check passes and the relay backlog drains |
| `workflow_failed` | any workflow on task queue `azentio` closed as `Failed` or `TimedOut` in the last hour (found with a visibility query). Each one also opens an `ops` exception, deduped by workflow id. | warning; critical above 5 per hour | no new failures for 1 h |

Alerts open once (the partial unique index `alerts_one_open`), notify once, and record the resolution. Each open and resolve also goes to `audit/service.record` with `actor=system`.

## `/ready` extended
The Phase 0 `/ready` checks Postgres, the graph, the worker heartbeat and the LLM. Phase 4 adds:
- **`gmail`:** configured → token refresh succeeds (cached 5 min), and the `gmail.poll` heartbeat is under 10 min old. Not configured → `"skipped"`, which is not a failure.
- **`queue`:** oldest ready job lag in seconds; failing if over 300.
- **`backup`:** last ok backup age; failing if over 26 h. Only checked when `BACKUP_DIR` is set.
- **`migrations`:** the applied version equals the newest file; failing if not.
- **`temporal`:** `client.service_client.check_health()` within 2 s, plus the worker's poller being seen on task queue `azentio` (`describe_task_queue`).

The response shape is `{"ready": bool, "checks": {name: {"ok": bool, "detail": str, "ms": int}}}`, and it returns 503 when any *required* check fails. Required checks: `db`, `worker`, `migrations`. Degraded only (200, but `ready_degraded=true`): graph, LLM, Gmail, backup, **temporal**. Temporal is degraded rather than required because the outbox buffers every start and signal: requests keep working, and workflows catch up once it's back. This follows the Phase 0 rule that an LLM outage moves work to human review instead of failing.

## Metrics
`GET /api/ops/metrics` returns JSON computed by SQL on request, with no in-process counters to lose on restart:
- `jobs`: counts by kind × `state` (`queued`, `running`, `done`, `dead`), p50/p95 run duration over the last 24 h (from `locked_at` to `finished_at`), and the oldest queued lag (`now() - min(run_at) WHERE state='queued'`).
- `workflows`: running, completed and failed per workflow type over the last 24 h, from Temporal visibility (`count_workflows`).
- `outbox`: sent, failed and pending over the last 24 h, by purpose.
- `gmail`: messages seen, ingested and unmatched over the last 24 h, and the last poll time.
- `documents`: extractions over the last 24 h, the share that hit `ai_unavailable`, and mean confidence.
- `alerts`: currently open.

The existing structured log lines (`logging_setup.log_event`) gain `job_id`, `job_kind` and `attempt` fields through a contextvar set by the worker, the same mechanism as `ls.request_id`.

## API endpoints
| Method & path | Purpose | Roles |
|---|---|---|
| `GET /health` | process alive (Phase 0) | public |
| `GET /ready` | dependency checks, extended (above) | public, detail redacted unless signed in as admin |
| `GET /api/ops/jobs?state=&kind=&page=` | job list with filters | admin, auditor (read) |
| `GET /api/ops/jobs/{id}` | payload, error history, linked entity | admin, auditor |
| `POST /api/ops/jobs/{id}/retry` | `dead` → `queued`, attempts reset, audit `job.retry` | admin |
| `POST /api/ops/jobs/{id}/discard` | stays `dead`, with `discarded_by` and `discarded_reason` set; `reason` required, audit `job.discard`, resolves the linked `ops` exception | admin |
| `POST /api/ops/jobs/retry-bulk` | `{kind, ids?}` retries every dead job of that kind (after a fixed outage) | admin |
| `GET /api/ops/metrics` | as above | admin, auditor |
| `GET /api/ops/workflows?status=&type=` | recent workflows from Temporal visibility (id, type, status, start and close time, linked entity), with a deep link to Temporal UI | admin, auditor |
| `POST /api/ops/workflows/{id}/retry` | starts a failed workflow again as a fresh run (same id, `ALLOW_DUPLICATE_FAILED_ONLY`), audit `workflow.retry` | admin |
| `GET /api/ops/alerts?open=true` | alerts | admin, auditor |
| `POST /api/ops/alerts/{id}/ack` | acknowledge (stops the reminder, doesn't resolve) | admin |
| `GET /api/ops/backups` | backup history | admin |
| `POST /api/ops/backups/run` | enqueue `backup.nightly` now (key `backup:manual:<minute>`) | admin |
| `GET /api/employees/{eid}/messages` | outbox rows for a joiner (timeline) | admin, hr_ops, approver, auditor; manager for own reports (Phase 6) |
| `POST /api/escalate` | **kept** as "Escalate now": enqueues `escalation.check` with a key per minute. Clicking twice in a minute is a no-op, and each owner still gets at most one email per item per day. | admin, hr_ops |
| `POST /api/gmail/poll` | **kept** as "Check inbox now": triggers the `gmail-poll` schedule immediately (`ScheduleHandle.trigger`) | admin, hr_ops |

All entries are added to the Phase 0 `identity/perms.py` table. `/api/ops/*` defaults to admin.

## Screens

### Ops → Jobs (`frontend/app/(hr)/ops/jobs/page.tsx`): admin, auditor read-only
- **Header:** four stat tiles (Queued, Running, Dead, Oldest lag), same component style as the dashboard tiles.
- **Tabs:** Dead (default when the count > 0), Queued, Running, Done (last 24 h), Discarded (`dead` with `discarded_by` set), and **Workflows**: failed and running workflows from `/api/ops/workflows`, with Retry and an "Open in Temporal UI" link.
- **Table:** kind, the linked entity (employee name or document or integration, linked), attempts/max, last error (one line, truncated), next run or died-at, and actions.
- **Row drawer:** the full payload as formatted JSON, with secrets masked by the same `mask_record` used for integration logs. It also shows the error history as a timeline (attempt, time, error, traceback tail) and the linked `work_queue` exception.
- **Actions:** Retry (one click with a toast); Discard (dialog with a required reason). The bulk "Retry all dead of kind X" appears when the Dead tab is filtered by kind. Auditors see no action buttons.
- **States:**
  - Empty Dead tab: "Nothing failed. Jobs that fail repeatedly land here."
  - Loading: skeleton rows.
  - Error: an inline card with a retry button.
  - Polling: refresh every 10 s while the tab is visible.

### Ops → Health (`frontend/app/(hr)/ops/page.tsx`): admin, auditor
- The `/ready` checks as a list of status pills with detail and latency, including `temporal`, with a link to Temporal UI (`TEMPORAL_UI_URL`).
- Open alerts, with an Ack button.
- The `metrics` summary: 24 h sparkline per job kind, built from the hourly buckets in the metrics JSON. Plain SVG; no chart library unless one is already installed by then.
- The last 5 backups with status, size and the restore-tested date.
- **States:**
  - Everything ok: a green banner, "All systems ready".
  - Degraded: an amber banner naming what's degraded and what the app does instead, e.g. "LLM unavailable: new documents go to human review", or "Temporal unreachable: workflow starts are queued and will run when it's back".

### Existing screens touched
- **App shell:** a red dot on the "Ops" nav item when an alert is open (admin only). The Gmail status line in the sidebar footer reads the `gmail` check from `/ready` instead of `/api/stats`.
- **Employee record timeline:** shows outbox rows with their status: sent, pending (retrying, attempt n), or failed (with an "Open job" link for admins).
- **Logs page:** gains a "Messages" tab backed by `outbox`.

## File-by-file changes
| File | Change |
|---|---|
| `backend/main.py` | `lifespan`:
  - remove the `gmail.poll_forever` and `_scheduler` tasks
  - register the recurring jobs (`reminder.sweep`, `escalation.check`, `alert.evaluate`, `backup.nightly`)
  - call `workflows.gmail.ensure_schedule()` and `workflows.integrations.sync_schedules()`
  - in dev, start the Temporal worker as an asyncio task

  Delete `_scheduler`. `escalate()` and `poll_now()` become enqueue calls. The portal upload and `/api/ingest` paths stage the file and enqueue `doc.intake` instead of awaiting `pipeline.intake` inline; the response returns `{document_id, status:'Submitted'}` and the UI polls. `set_item_status`'s correction email goes through the outbox. |
| `backend/gmail.py` | Split `_fetch_new` into `list_new(senders)` (ids, thread, from, subject, attachment ids; **no label**), `fetch_attachment(msg_id, att_id)` and `mark_processed(msg_id)`. `_send_sync` takes `message_id` and sets the header. Add `already_sent(message_id) -> dict|None` using an `rfc822msgid:` search. `send_request` and `send_correction` render, then call `notifications.queue_email(...)`. Delete `poll_forever`. `poll()` stays as a thin function the job calls, for tests. |
| `backend/pipeline.py` | Split `intake` into `save_document(eid, filename, data, mime, source, item_key) -> doc_id` and `decide(doc_id)`: extract result → validate → risk tier (Phase 2) → item status → `checklist.recompute` → `on_documents_changed`. The correction notification becomes `queue_email` with key `email:correction:<doc_id>`. |
| `backend/extract.py` | No change to the prompts. `extract()` records `_prompt_version` (already needed by Phase 2) so the job key can include it. |
| `backend/llm.py` | `complete()` raises `llm.Unavailable` when every provider has failed, rather than a generic exception, so `doc.extract` can map it to `RetryLater`. `unavailable_reason()` is reused by `/ready` and the `llm_down` alert. |
| `backend/integrations/engine.py` | `run_due` deleted; each integration's Temporal schedule replaces it. The schedule settings (`interval_minutes`, `time`, `timezone`) map to a Temporal `ScheduleSpec`. `next_run_at` and `last_run_at` stay in Postgres; `last_run_at` is written by `run_integration`, and `next_run_at` is read from the schedule's description. `notify()` enqueues `email.send` instead of awaiting `delivery.send_email` inside a try that swallows errors. `_provision_access` enqueues `access.perform` per rule. |
| `backend/integrations/delivery.py` | `send_email` becomes `queue_email(...)`, which writes an outbox row and enqueues. `record` and `messages` read and write `outbox`; the `OutboundMessage` graph node is retired (backfilled in the migration step). |
| `backend/notifications/service.py` (new, inside the Phase 0 module) | `queue_email(purpose, key, to, subject, html, employee_id=None, thread_id=None)` does the outbox insert plus enqueue in one `tx()`. It applies `EMAIL_REDIRECT_TO` (moved here from `gmail._send_sync`, so the outbox records the real intended recipient *and* the redirect). |
| `backend/jobs_handlers/{gmail,documents,email,integrations,access,reminders,escalations,alerts,backup}.py` (new) | Handlers from the table above. |
| `backend/shared/jobs.py` | Add `recurring()`, `RetryLater`, `error_history` append, a `heartbeat(name)` helper, `retry(job_id, actor)` and `discard(job_id, actor, reason)`. Both of the latter write audit entries. |
| `backend/ops/routes.py` (new) | The `/api/ops/*` endpoints, including `workflows`. |
| `backend/workflows/{client,worker,activities,gmail,integrations,approvals}.py` (new) | As described in "Temporal setup". Workflows are deterministic: no I/O, `workflow.now()` instead of the clock, and all side effects inside activities. |
| `backend/jobs_handlers/temporal.py` (new) | The `temporal.start` and `temporal.signal` relay handlers. |
| `backend/pyproject.toml` | add `temporalio`. |
| `docker-compose.yml` | the `temporal` service (`temporalio/auto-setup`, `DB=postgres12`, using the `postgres` service, databases `temporal` and `temporal_visibility`) and the `temporal-ui` service (`temporalio/ui`, port 8233). |
| `backend/ops/readiness.py` (new) | The check functions shared by `/ready` and `alert.evaluate`. |
| `backend/migrations/020–025_*.sql` | As above. |
| `frontend/app/(hr)/ops/page.tsx`, `ops/jobs/page.tsx` (new) | The screens above. |
| `frontend/components/app-shell.tsx` | Ops nav item (admin and auditor), alert dot, Gmail status from `/ready`. |
| `frontend/app/(hr)/employees/[id]/page.tsx` | Timeline shows outbox status. Upload shows "Processing…" until the document leaves `Submitted`, polling every 2 s and capped at 60 s, after which it reads "Still processing; you'll see it here when it's done". |
| `frontend/app/portal/[token]/page.tsx` | Same async upload state for joiners: "Received. We're checking it now." |
| `.github/workflows/ci.yml` (new) | See CI below. |
| `scripts/backup.sh`, `docs/runbooks/restore.md` (new) | See Backups below. |
| `.env.example` | `ALERT_EMAILS`, `ALERT_WEBHOOK_URL`, `BACKUP_DIR`, `BACKUP_KEEP_DAILY=14`, `BACKUP_KEEP_WEEKLY=8`, `ORG_TIMEZONE`, `TEMPORAL_ADDRESS=localhost:7233`, `TEMPORAL_NAMESPACE=default`, `TEMPORAL_API_KEY` (Cloud), `TEMPORAL_TLS_CERT` and `TEMPORAL_TLS_KEY` (mTLS), `TEMPORAL_UI_URL=http://localhost:8233`, `TEMPORAL_WORKER_INPROCESS=true` (dev). |

## Backups and restore
- **What:** Postgres is the system of record, so `pg_dump -Fc` covers HR data, audit, jobs and outbox. FalkorDB is a rebuildable read model after Phase 3, so its AOF snapshot is a convenience; restoring it is optional because `rebuild_graph()` recreates it. The `temporal` and `temporal_visibility` databases hold execution history only, not business state. They're included in the nightly dump for convenience. If they're lost, the runbook re-enqueues `temporal.start` for open cases and approvals from Postgres, and the keys make that safe. `backend/uploads/` holds the evidence documents and **must** be backed up too: the job tars new files since the last backup into `BACKUP_DIR/uploads-<date>.tar` (incremental by mtime) and writes a weekly full copy.
- **Targets:** RPO 24 h (nightly), RTO 1 h for a single-host restore.
- **Integrity:** sha256 is recorded per file. The `backup.nightly` job then runs `pg_restore --list` on the dump it just wrote, and a failure marks the backup failed.
- **Restore runbook** (`docs/runbooks/restore.md`):
  1. Stop the backend.
  2. `createdb azentio_restore && pg_restore -d azentio_restore <file>`.
  3. Row-count spot-check against the `backups` metadata.
  4. Swap `DATABASE_URL`.
  5. Untar uploads.
  6. Start the backend, which runs migrations (should be a no-op).
  7. Run `uv run python -c "import graph_rebuild; graph_rebuild.rebuild_graph()"`.
  8. `uv run python -c "from audit_log.service import verify_chain; assert verify_chain()[0]"`.
  9. If the Temporal databases weren't restored, run `uv run python -m workflows.reconcile`. It enqueues `temporal.start` for every open approval request, document review and lifecycle case (all keyed, so this is safe to repeat).
  10. Smoke-test with `scripts/smoke.py`.
- **Restore drill:** once a month, an admin clicks "Mark restore tested" on the Health screen after running the runbook against a scratch DB; this sets `backups.restore_tested_at`. The Health screen shows amber when the last tested restore is over 35 days old. This is the one routine human task in this phase, and it's deliberate: an untested backup isn't a backup.

## CI (`.github/workflows/ci.yml`)
Runs on every PR and on push to `main`. Two jobs run in parallel:
- **backend**
  - Services: `postgres:17` and `falkordb/falkordb:latest`. No Temporal service: the workflow tests use `temporalio.testing.WorkflowEnvironment.start_time_skipping()`, which runs an in-process test server and skips through timers instantly.
  - Steps: `astral-sh/setup-uv`, `uv sync --frozen`, then `uv run python test_core.py`, `uv run python test_phase0.py` … `test_phase4.py` (every `test_*.py` in `backend/`), then `uv run python -m compileall -q .` as a syntax gate.
  - Env: `DATABASE_URL` pointing at the service DB `azentio_test`, and `FALKORDB_GRAPH=test`. LLM keys unset, so the tests exercise the keyless/unavailable paths, which is the point.
- **frontend**
  - Node 22, `npm ci`, `npx tsc --noEmit`, `npm run lint`, `npm run build` with `NEXT_PUBLIC_API_URL=""`.
- **Gates:**
  - A required status check on `main` (set in branch protection by the repo owner).
  - `pip-audit` and `npm audit --omit=dev --audit-level=high` run as a third, non-blocking job that posts a summary. They become blocking once the current findings are triaged.

The headless-Chrome smoke script (`scripts/smoke.py`) is **not** in CI this phase: it needs a full stack with seeded data. It runs locally before each phase merge, as the definition of done requires. Add it to CI when a compose-based CI job is worth its run time.

## Migration steps
1. Apply migrations 020–025. They're additive, and the old code paths still work.
2. Backfill: `OutboundMessage` graph nodes go into `outbox` with `status='sent'` and `channel` from `delivered_by`, using deterministic `key = 'legacy:' || id`. Verify the row count matches.
3. Backfill `gmail_messages`: list messages carrying the `azentio-processed` label from the last 14 days and insert them with `status='ingested'`, so the new poller won't re-ingest them.
4. Deploy the handlers with the env var `JOBS_EXTERNAL=off`. Jobs are created and visible, but email, Gmail and connector handlers run in **dry-run** (they log what they'd do and mark done). Compare against the old loops for one day.
5. Switch on: `JOBS_EXTERNAL=on`, which removes the old loops from `lifespan` in the same deploy. Only one path is live at a time, so nothing is processed twice.
6. Bring up `temporal` and `temporal-ui` and start the worker. Create the schedules **paused** and compare one Gmail poll and one integration run started by hand with the old loop's output.
7. Unpause the schedules and remove the old loops in the same deploy, so only one path polls at a time.
8. Backfill approval workflows: enqueue `temporal.start` for every pending `approval_requests` row. It's keyed, so it's safe to re-run.
9. Delete the dry-run flag and the old code paths (`poll_forever`, `_scheduler`, `run_due`, the `OutboundMessage` writes) in the next PR.

Steps 1–4 can be undone by redeploying the previous build. Step 5 can be undone by setting `JOBS_EXTERNAL=off`, which restores the loops. Steps 6–7 can be undone by pausing the schedules and setting `TEMPORAL_ENABLED=false`, which restores `main._scheduler` and the poll loop until step 9 deletes them. Approval waits keep working without Temporal, because their state is Postgres rows.

## Seed data
- **Dev only:**
  - One `dead` `email.send` job with a realistic Gmail 400 error, so the DLQ screen isn't empty in demos. The key is `seed:dead:1`.
  - One resolved `queue_stall` alert in the history.
  - One `ok` backup row.
- **Recurring jobs:** registered by bootstrap in every environment, safe to re-run because their keys are time-bucketed.

## Tests (`backend/test_phase4.py`, plain asserts against `azentio_test`)
- `test_email_send_twice_one_outbox_row`: `queue_email` twice with the same key → one outbox row, one job.
- `test_email_retry_after_gmail_accepted_does_not_resend`: a fake Gmail client records sends, and the first attempt crashes after `send` but before the outbox update. On retry, `already_sent` finds the Message-ID → send count is still 1 and the outbox row is `sent`.
- `test_poll_labels_only_after_enqueue`: make `gmail.ingest` enqueue raise → `mark_processed` is never called and the message is picked up on the next poll.
- `test_same_gmail_message_ingested_once`: two polls return the same message id → one `gmail.ingest` job and one Document per attachment.
- `test_extract_llm_down_routes_to_review`: `llm.complete` raises `Unavailable` every time → after max attempts the job is `dead`, a `doc_review` work_queue row exists with reason `ai_unavailable`, and the Document is `Under Review`, not lost.
- `test_retry_later_does_not_burn_attempts`: two `RetryLater` raises → attempts are still 0 and `run_at` has moved forward.
- `test_escalation_once_per_item_per_day`: run `escalation.check` 3 times in one day → one escalation email per overdue item.
- `test_integration_run_now_twice_one_workflow`: "Run now" clicked twice within the same minute → one `temporal.start` job and one workflow.
- `test_dead_job_opens_ops_exception_once`: a job dies, is retried, and dies again → one open `ops` exception, not two.
- `test_discard_requires_reason_and_audits`: discard without a reason → 422; with a reason → audit row `job.discard` naming the admin.
- `test_alert_opens_once_and_resolves`: stale heartbeat → one alert and one `alert.send`; still stale on the next evaluation → no new alert; fresh heartbeat → resolved twice in a row → `resolved_at` set.
- `test_ready_503_when_db_down_200_degraded_when_llm_down`: patch the check functions → status codes and `ready_degraded` as specified.
- `test_backup_produces_restorable_dump`: run `backup.nightly` against `azentio_test` → the file exists, sha256 matches, `pg_restore --list` succeeds, and the `backups` row is ok.
- `test_outbox_start_same_tx`: a business write that raises after `enqueue("temporal.start", conn=c)` leaves no job row (rolled back together). A successful write leaves exactly one.
- `test_relay_already_started_is_success`: a fake client raises `WorkflowAlreadyStartedError` → the job is `done` and no exception is opened.
- `test_relay_temporal_down_retries_then_succeeds`: the fake client raises a connection error twice and then succeeds → the job is `done` after 3 attempts, and the business row is unchanged throughout.
- `test_workflow_id_equals_idempotency_key`: enqueue the same start twice → one job, and the started workflow id equals the key.
- `test_gmail_poll_workflow_labels_after_ingest` (time-skipping env, fake activities): `mark_processed` runs only after `gmail_ingest_message` succeeds, and an ingest failure leaves the message unlabelled.
- `test_integration_schedule_one_run_per_slot`: two triggers for the same scheduled time → one `run_integration` call.
- `test_rule_approval_workflow_waits_then_publishes` (time-skipping): starts on submit and waits; one `decision` signal on a compliance rule → still waiting; a second distinct approver → `policy_publish` called once. A duplicate signal from the same approver is ignored.
- `test_approval_tables_unchanged`: the Phase 1 rollback test (`test_rollback_restores_without_loss`) still passes with the workflow wrapper on.
- `test_alerts_work_without_temporal`: with the Temporal client unreachable, `alert.evaluate` still runs and opens `temporal_down`.
- `test_perms_ops_routes`: hr_ops, approver, manager → 403 on `/api/ops/jobs/*/retry`; auditor → 200 on GET and 403 on POST; admin → 200.

## Exit criteria
- `grep -n "while True" backend/` finds no business loops (only the job worker's own loop in `shared/jobs.py`).
- `docker compose up -d` brings up Postgres, FalkorDB, Temporal and Temporal UI with no manual steps. The schedules appear in Temporal UI after the backend starts.
- Stopping the `temporal` container for 10 minutes during a demo (submitting a rule draft, saving an integration schedule) loses nothing:
  - requests succeed and `/ready` reports degraded
  - `temporal_down` fires
  - after restarting it, the relay drains and every workflow starts exactly once
- Every `gmail.send`, `extract.extract`, `upsert_record` and `perform` call is reachable only from a job handler or an activity. A grep check in `test_phase4.py` verifies that by scanning for call sites outside `jobs_handlers/` and `workflows/activities.py`.
- Killing the backend at random points during a demo flow (send request → reply with an attachment → extraction → correction email) and restarting it produces **no duplicate emails and no lost documents**. Verified manually 5 times; the outbox and documents tables are the evidence.
- Gmail is unplugged (bad refresh token) for 15 min → `poller_stall` alert email received → token fixed → the alert resolves itself, and queued messages drain without anyone acting.
- A backup ran, the restore runbook was executed against a scratch DB, and `restore_tested_at` is set.
- CI is green on `main`, and a deliberately broken test fails the PR.
- All `test_*.py` pass, the smoke script passes for all roles, and the Ops screenshots are in `docs/screenshots/`.

## Risks and rollback
| Risk | Mitigation |
|---|---|
| Async intake changes the UX: an upload no longer returns the verdict immediately | Polling with clear "processing" copy. Most extractions finish in under 10 s, so the experience barely changes. |
| `rfc822msgid:` search is eventually consistent in Gmail | Only used on attempts > 1, which are at least 1 min after the first. If it's not found but `outbox.status` is still pending after attempt 3, the next attempt waits 10 min before sending. An extra delay is acceptable; a duplicate isn't. |
| Moving `EMAIL_REDIRECT_TO` changes demo behaviour | The outbox stores both the intended and the actual recipient; covered by a test. |
| Alert storms during an outage | One open alert per (kind, subject); `alert.send` is keyed per hour; ack silences reminders. |
| Backup disk fills | Retention pruning runs in the same job, and its failure raises `backup_failed`. |
| Two retry systems disagree about who owns a task | The ownership table above is the rule. A task has one owner, and `test_phase4.py`'s grep check fails if a side-effect function is called from anywhere else. |
| Temporal is one more system to run | `auto-setup` in compose, health in `/ready`, the `temporal_down` alert, and Postgres staying the source of truth means losing Temporal costs time, not data. Temporal Cloud is an env switch. |
| Non-deterministic workflow code breaks replay | Workflows only orchestrate. CI runs the replay test (`Replayer`) over recorded histories from the time-skipping tests. |
| Rollback | `TEMPORAL_ENABLED=false` brings back the loops, while approvals run on Postgres state alone. `JOBS_EXTERNAL=off` restores the old loops while the outbox and job tables stay in place (additive). The migrations aren't reversed; the new tables simply go unused. |

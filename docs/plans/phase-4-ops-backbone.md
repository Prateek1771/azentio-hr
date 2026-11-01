# Phase 4: Ops backbone

- **Status:** planned
- **Depends on:** Phase 0 (`shared/db.py`, `shared/jobs.py`, `work_queue`, `/health`, `/ready`, `audit/service.record`). Phases 1–3 (Employee, Item, Document, and connector records in Postgres).
- **Design:** [2026-11-01-hardening-design.md](2026-11-01-hardening-design.md)

## Goal
All routine background work runs as jobs that retry by themselves. A person only gets involved when something has failed repeatedly, and then it reaches them as an assigned exception, not as something they had to go looking for.

The invariant this phase establishes: **every external call (Gmail, LLM, connectors) is job-backed and idempotent, so retries cannot duplicate side effects.**

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
5. Scheduler, reminders and escalations as recurring jobs (no more `while True` loops).
6. DLQ screen: retry, discard, and inspect the payload and error history.
7. Dead-lettered jobs open `ops` exceptions in `work_queue` (the producer exists from Phase 0; this phase adds the screen and the remaining kinds).
8. Alerts: queue stall, poller stall, DLQ growth, `/ready` failing. Sent by email and an optional webhook, deduplicated.
9. Metrics endpoint and an ops dashboard card.
10. Nightly `pg_dump` with retention, plus a restore runbook that has actually been tested.
11. GitHub Actions CI: self-checks, type check, lint, build.

## Non-goals
- No external queue (Celery, Redis Streams, SQS). The Postgres queue from Phase 0 is enough: the app produces low volume, at most thousands of jobs a day.
- No Prometheus, Grafana or OpenTelemetry stack. A JSON metrics endpoint and structured logs come first; add a scraper when a second instance exists.
- No multi-instance deployment. The design is safe with several workers (`SKIP LOCKED`), but this phase runs one process.
- No point-in-time recovery or WAL archiving. A nightly dump fits the recovery target agreed below.
- No CD or deployment pipeline (deployment is out of scope for this round).

## Idempotency key scheme
Every job is enqueued with an `idempotency_key`. The `jobs.idempotency_key` UNIQUE constraint from Phase 0 makes a second `enqueue` with the same key return the existing job instead of adding a new one.

| Job kind | Key | Side effect guarded |
|---|---|---|
| `gmail.poll` | `gmail:poll:<minute bucket>` | one poll per minute, however many workers |
| `gmail.ingest` | `gmail:msg:<gmail message id>` | one intake per received message |
| `doc.intake` | `gmail:att:<message id>:<attachment id>` or `portal:<sha256 of bytes>:<employee id>` | one Document per attachment |
| `doc.extract` | `doc:<document id>:extract:v<extract prompt version>` | one LLM extraction per document per prompt version |
| `email.send` | `email:<purpose>:<entity id>:<discriminator>` (e.g. `email:request:E1001:tpl_standard`, `email:correction:doc_ab12`, `email:escalation:it_99:2026-11-03`) | one email per purpose per entity (per day for escalations) |
| `integration.run` | `integration:<id>:<scheduled_at epoch>` | one scheduled run per slot |
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
  name          text PRIMARY KEY,                  -- worker | gmail.poll | escalation.check | integration.tick | backup
  last_ok_at    timestamptz,
  last_error_at timestamptz,
  last_error    text,
  meta          jsonb NOT NULL DEFAULT '{}'
);

-- 023_alerts.sql
CREATE TABLE alerts (
  id            bigserial PRIMARY KEY,
  kind          text NOT NULL,                     -- queue_stall | poller_stall | dlq_growth | not_ready | backup_failed | llm_down
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
CREATE INDEX IF NOT EXISTS jobs_dead ON jobs (kind, updated_at DESC) WHERE status = 'dead';
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (run_at) WHERE status = 'queued';

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

## Job handlers
All live under `backend/jobs_handlers/`, one file per area. Each is registered with `@jobs.handler(kind)`. Every handler must be safe to call twice with the same payload.

| Kind | Trigger | What it does | Max attempts / backoff | On dead |
|---|---|---|---|---|
| `gmail.poll` | recurring, every 60 s | `gmail.list_new()` (the old `_fetch_new` without attachments or labels). Per message: upsert into `gmail_messages`, enqueue `gmail.ingest`. Heartbeat `gmail.poll`. | 5 / 30 s × 2ⁿ, cap 10 min | `ops` exception "Gmail polling failing", alert `poller_stall` |
| `gmail.ingest` | `gmail.poll` | Match employee by thread then sender (the logic from `gmail.poll`). Enqueue `doc.intake` per attachment. **Only after commit**, label the message. Sets `gmail_messages.status`. | 5 | `ops` exception with the message link |
| `doc.intake` | `gmail.ingest`, portal upload, HR upload | Downloads the attachment (email) or reads the staged file (portal). Then runs `pipeline.save_document` (split from `pipeline.intake`: save + insert Document, no extraction) and enqueues `doc.extract`. | 5 | `ops` exception; the document stays `Submitted` and visible |
| `doc.extract` | `doc.intake`, reprocess button | Runs `extract.extract`, then `pipeline.decide` (the validate + risk-tier routing from Phase 2). If `llm.unavailable_reason()` is set, it raises `RetryLater` (it doesn't count as failure for the first 3 tries). | 6 / 2 min × 2ⁿ, cap 1 h | Opens a **`doc_review` exception** with reason `ai_unavailable` instead of an `ops` one: the human reviews the document by hand. No document is ever stuck. |
| `email.send` | outbox insert | Exactly-once send as described above. Simulated when `gmail.configured()` is false. | 8 / 1 min × 2ⁿ, cap 2 h | `ops` exception; `outbox.status='failed'`; shown on the employee timeline |
| `integration.tick` | recurring, every 30 s | Replaces `main._scheduler`. For each due integration, enqueues `integration.run` with key `integration:<id>:<next_run_at>`, then advances `next_run_at` in the same `tx()`. | 3 | alert `queue_stall` subject `integration.tick` |
| `integration.run` | tick, "Run now" | `integrations.engine.run_integration(iid, trigger)`. Its `AlreadyRunning` maps to success: the job is done, the other run owns the slot. | 3 / 5 min | `ops` exception on the integration, and `notify(..., "run_failed")` |
| `connector.push` | engine `_create_employee`, HR record completion | `HrPortal.upsert_record`. The key includes a hash of the payload, so an identical retry is a no-op. | 6 | `ops` exception on the employee |
| `access.perform` | `_provision_access`, Phase 5 offboarding | `AppConnector.perform(...)`, already repeat-safe through `_existing` | 6 | `ops` exception "grant/revoke X for Y failed"; the access result is marked `failed` |
| `reminder.sweep` | recurring, daily 09:00 in the org's timezone | For every pending joiner item due within 3 days with no upload, enqueues `email.send` with purpose `reminder`, key per item per day. | 3 | alert |
| `escalation.check` | recurring, every 15 min | Replaces the body of `POST /api/escalate` (`main.escalate`). For every overdue blocking task with an owner, it enqueues `email.send` with key `email:escalation:<item>:<date>`, so an owner gets at most one email a day per item. Phase 6 extends this into the escalation ladder. | 3 | alert |
| `alert.evaluate` | recurring, every 60 s | Evaluates the alert rules below, opens and resolves `alerts` rows, and enqueues `alert.send` for new ones. | 3 | logged only (avoids an alert loop) |
| `alert.send` | `alert.evaluate` | Email to `ALERT_EMAILS` through the outbox (purpose `alert`), plus a POST to `ALERT_WEBHOOK_URL` if set (JSON, Slack-compatible `{"text": ...}`). | 5 | logged only |
| `backup.nightly` | recurring, 02:00 UTC | `pg_dump -Fc` to `BACKUP_DIR`, sha256, keep 14 daily and 8 weekly backups, write a `backups` row. Snapshots the FalkorDB AOF file if it's reachable. | 3 / 10 min | alert `backup_failed` (critical) |

Recurring jobs are scheduled by `jobs.recurring(kind, every=…|cron=…)`. On each tick it enqueues the next occurrence with a time-bucketed key, so two processes can't both schedule it. This replaces both `while True` loops in `main.lifespan`.

**`RetryLater` vs failure.** A handler raises `jobs.RetryLater(seconds, reason)` for expected waits: LLM rate limits (`llm._note_limit` already detects them) and Gmail 429s. These don't add to `error_history`, and after 3 occurrences they start counting against `max_attempts`. Any other exception is a failure.

## Alert rules (`alert.evaluate`)
| Kind | Condition | Severity | Resolves when |
|---|---|---|---|
| `queue_stall` | oldest `queued` job with `run_at < now() - 5 min`, or `worker` heartbeat older than 2 min | critical | condition false for 2 consecutive evaluations |
| `poller_stall` | Gmail configured and `gmail.poll` heartbeat `last_ok_at` older than 10 min | critical | the next successful poll |
| `dlq_growth` | more than 5 jobs moved to `dead` in the last hour, or any `dead` job older than 24 h still unhandled | warning; critical above 20 | DLQ count back under threshold |
| `not_ready` | `/ready` check failing for more than 3 min (same function, called in-process) | critical | ready again |
| `llm_down` | `llm.unavailable_reason()` non-empty for more than 15 min | warning | LLM back |
| `backup_failed` | last `backups` row failed, or no `ok` backup in 26 h | critical | next ok backup |

Alerts open once (the partial unique index `alerts_one_open`), notify once, and record the resolution. Each open and resolve also goes to `audit/service.record` with `actor=system`.

## `/ready` extended
The Phase 0 `/ready` checks Postgres, the graph, the worker heartbeat and the LLM. Phase 4 adds:
- **`gmail`:** configured → token refresh succeeds (cached 5 min), and the `gmail.poll` heartbeat is under 10 min old. Not configured → `"skipped"`, which is not a failure.
- **`queue`:** oldest ready job lag in seconds; failing if over 300.
- **`backup`:** last ok backup age; failing if over 26 h. Only checked when `BACKUP_DIR` is set.
- **`migrations`:** the applied version equals the newest file; failing if not.

The response shape is `{"ready": bool, "checks": {name: {"ok": bool, "detail": str, "ms": int}}}`, and it returns 503 when any *required* check fails. Required checks: `db`, `worker`, `migrations`. Degraded only (200, but `ready_degraded=true`): graph, LLM, Gmail, backup. This follows the Phase 0 rule that an LLM outage moves work to human review instead of failing.

## Metrics
`GET /api/ops/metrics` returns JSON computed by SQL on request, with no in-process counters to lose on restart:
- `jobs`: counts by kind × status, p50/p95 run duration over the last 24 h (from `started_at` / `finished_at`), and the oldest queued lag.
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
| `GET /api/ops/jobs?status=&kind=&page=` | job list with filters | admin, auditor (read) |
| `GET /api/ops/jobs/{id}` | payload, error history, linked entity | admin, auditor |
| `POST /api/ops/jobs/{id}/retry` | dead or failed → queued, attempts reset, audit `job.retry` | admin |
| `POST /api/ops/jobs/{id}/discard` | dead → discarded, `reason` required, audit `job.discard`, resolves the linked `ops` exception | admin |
| `POST /api/ops/jobs/retry-bulk` | `{kind, ids?}` retries every dead job of that kind (after a fixed outage) | admin |
| `GET /api/ops/metrics` | as above | admin, auditor |
| `GET /api/ops/alerts?open=true` | alerts | admin, auditor |
| `POST /api/ops/alerts/{id}/ack` | acknowledge (stops the reminder, doesn't resolve) | admin |
| `GET /api/ops/backups` | backup history | admin |
| `POST /api/ops/backups/run` | enqueue `backup.nightly` now (key `backup:manual:<minute>`) | admin |
| `GET /api/employees/{eid}/messages` | outbox rows for a joiner (timeline) | admin, hr_ops, approver, auditor; manager for own reports (Phase 6) |
| `POST /api/escalate` | **kept** as "Escalate now": enqueues `escalation.check` with a key per minute. Clicking twice in a minute is a no-op, and each owner still gets at most one email per item per day. | admin, hr_ops |
| `POST /api/gmail/poll` | **kept** as "Check inbox now": enqueues `gmail.poll` | admin, hr_ops |

All entries are added to the Phase 0 `identity/perms.py` table. `/api/ops/*` defaults to admin.

## Screens

### Ops → Jobs (`frontend/app/(hr)/ops/jobs/page.tsx`): admin, auditor read-only
- **Header:** four stat tiles (Queued, Running, Dead, Oldest lag), same component style as the dashboard tiles.
- **Tabs:** Dead (default when the count > 0), Queued, Running, Done (last 24 h), Discarded.
- **Table:** kind, the linked entity (employee name or document or integration, linked), attempts/max, last error (one line, truncated), next run or died-at, and actions.
- **Row drawer:** the full payload as formatted JSON, with secrets masked by the same `mask_record` used for integration logs. It also shows the error history as a timeline (attempt, time, error, traceback tail) and the linked `work_queue` exception.
- **Actions:** Retry (one click with a toast); Discard (dialog with a required reason). The bulk "Retry all dead of kind X" appears when the Dead tab is filtered by kind. Auditors see no action buttons.
- **States:**
  - Empty Dead tab: "Nothing failed. Jobs that fail repeatedly land here."
  - Loading: skeleton rows.
  - Error: an inline card with a retry button.
  - Polling: refresh every 10 s while the tab is visible.

### Ops → Health (`frontend/app/(hr)/ops/page.tsx`): admin, auditor
- The `/ready` checks as a list of status pills with detail and latency.
- Open alerts, with an Ack button.
- The `metrics` summary: 24 h sparkline per job kind, built from the hourly buckets in the metrics JSON. Plain SVG; no chart library unless one is already installed by then.
- The last 5 backups with status, size and the restore-tested date.
- **States:**
  - Everything ok: a green banner, "All systems ready".
  - Degraded: an amber banner naming what's degraded and what the app does instead, e.g. "LLM unavailable: new documents go to human review".

### Existing screens touched
- **App shell:** a red dot on the "Ops" nav item when an alert is open (admin only). The Gmail status line in the sidebar footer reads the `gmail` check from `/ready` instead of `/api/stats`.
- **Employee record timeline:** shows outbox rows with their status: sent, pending (retrying, attempt n), or failed (with an "Open job" link for admins).
- **Logs page:** gains a "Messages" tab backed by `outbox`.

## File-by-file changes
| File | Change |
|---|---|
| `backend/main.py` | `lifespan`: remove the `gmail.poll_forever` and `_scheduler` tasks and register the recurring jobs (`gmail.poll`, `integration.tick`, `reminder.sweep`, `escalation.check`, `alert.evaluate`, `backup.nightly`). Delete `_scheduler`. `escalate()` and `poll_now()` become enqueue calls. The portal upload and `/api/ingest` paths stage the file and enqueue `doc.intake` instead of awaiting `pipeline.intake` inline; the response returns `{document_id, status:'Submitted'}` and the UI polls. `set_item_status`'s correction email goes through the outbox. |
| `backend/gmail.py` | Split `_fetch_new` into `list_new(senders)` (ids, thread, from, subject, attachment ids; **no label**), `fetch_attachment(msg_id, att_id)` and `mark_processed(msg_id)`. `_send_sync` takes `message_id` and sets the header. Add `already_sent(message_id) -> dict|None` using an `rfc822msgid:` search. `send_request` and `send_correction` render, then call `notifications.queue_email(...)`. Delete `poll_forever`. `poll()` stays as a thin function the job calls, for tests. |
| `backend/pipeline.py` | Split `intake` into `save_document(eid, filename, data, mime, source, item_key) -> doc_id` and `decide(doc_id)`: extract result → validate → risk tier (Phase 2) → item status → `checklist.recompute` → `on_documents_changed`. The correction notification becomes `queue_email` with key `email:correction:<doc_id>`. |
| `backend/extract.py` | No change to the prompts. `extract()` records `_prompt_version` (already needed by Phase 2) so the job key can include it. |
| `backend/llm.py` | `complete()` raises `llm.Unavailable` when every provider has failed, rather than a generic exception, so `doc.extract` can map it to `RetryLater`. `unavailable_reason()` is reused by `/ready` and the `llm_down` alert. |
| `backend/integrations/engine.py` | `run_due` deleted; its logic moves to the `integration.tick` handler. `notify()` enqueues `email.send` instead of awaiting `delivery.send_email` inside a try that swallows errors. `_provision_access` enqueues `access.perform` per rule. |
| `backend/integrations/delivery.py` | `send_email` becomes `queue_email(...)`, which writes an outbox row and enqueues. `record` and `messages` read and write `outbox`; the `OutboundMessage` graph node is retired (backfilled in the migration step). |
| `backend/notifications/service.py` (new, inside the Phase 0 module) | `queue_email(purpose, key, to, subject, html, employee_id=None, thread_id=None)` does the outbox insert plus enqueue in one `tx()`. It applies `EMAIL_REDIRECT_TO` (moved here from `gmail._send_sync`, so the outbox records the real intended recipient *and* the redirect). |
| `backend/jobs_handlers/{gmail,documents,email,integrations,access,reminders,escalations,alerts,backup}.py` (new) | Handlers from the table above. |
| `backend/shared/jobs.py` | Add `recurring()`, `RetryLater`, `error_history` append, a `heartbeat(name)` helper, `retry(job_id, actor)` and `discard(job_id, actor, reason)`. Both of the latter write audit entries. |
| `backend/ops/routes.py` (new) | The `/api/ops/*` endpoints. |
| `backend/ops/readiness.py` (new) | The check functions shared by `/ready` and `alert.evaluate`. |
| `backend/migrations/020–025_*.sql` | As above. |
| `frontend/app/(hr)/ops/page.tsx`, `ops/jobs/page.tsx` (new) | The screens above. |
| `frontend/components/app-shell.tsx` | Ops nav item (admin and auditor), alert dot, Gmail status from `/ready`. |
| `frontend/app/(hr)/employees/[id]/page.tsx` | Timeline shows outbox status. Upload shows "Processing…" until the document leaves `Submitted`, polling every 2 s and capped at 60 s, after which it reads "Still processing; you'll see it here when it's done". |
| `frontend/app/portal/[token]/page.tsx` | Same async upload state for joiners: "Received. We're checking it now." |
| `.github/workflows/ci.yml` (new) | See CI below. |
| `scripts/backup.sh`, `docs/runbooks/restore.md` (new) | See Backups below. |
| `.env.example` | `ALERT_EMAILS`, `ALERT_WEBHOOK_URL`, `BACKUP_DIR`, `BACKUP_KEEP_DAILY=14`, `BACKUP_KEEP_WEEKLY=8`, `ORG_TIMEZONE`. |

## Backups and restore
- **What:** Postgres is the system of record, so `pg_dump -Fc` covers HR data, audit, jobs and outbox. FalkorDB is a rebuildable read model after Phase 3, so its AOF snapshot is a convenience; restoring it is optional because `rebuild_graph()` recreates it. `backend/uploads/` holds the evidence documents and **must** be backed up too: the job tars new files since the last backup into `BACKUP_DIR/uploads-<date>.tar` (incremental by mtime) and writes a weekly full copy.
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
  8. `uv run python -c "from audit.service import verify_chain; assert verify_chain()"`.
  9. Smoke-test with `scripts/smoke.py`.
- **Restore drill:** once a month, an admin clicks "Mark restore tested" on the Health screen after running the runbook against a scratch DB; this sets `backups.restore_tested_at`. The Health screen shows amber when the last tested restore is over 35 days old. This is the one routine human task in this phase, and it's deliberate: an untested backup isn't a backup.

## CI (`.github/workflows/ci.yml`)
Runs on every PR and on push to `main`. Two jobs run in parallel:
- **backend**
  - Services: `postgres:17` and `falkordb/falkordb:latest`.
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
6. Delete the dry-run flag and the old code paths (`poll_forever`, `_scheduler`, `run_due`, the `OutboundMessage` writes) in the next PR.

Steps 1–4 can be undone by redeploying the previous build. Step 5 can be undone by setting `JOBS_EXTERNAL=off`, which restores the loops.

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
- `test_integration_tick_no_double_run`: two concurrent ticks for the same due integration → one `integration.run` job.
- `test_dead_job_opens_ops_exception_once`: a job dies, is retried, and dies again → one open `ops` exception, not two.
- `test_discard_requires_reason_and_audits`: discard without a reason → 422; with a reason → audit row `job.discard` naming the admin.
- `test_alert_opens_once_and_resolves`: stale heartbeat → one alert and one `alert.send`; still stale on the next evaluation → no new alert; fresh heartbeat → resolved twice in a row → `resolved_at` set.
- `test_ready_503_when_db_down_200_degraded_when_llm_down`: patch the check functions → status codes and `ready_degraded` as specified.
- `test_backup_produces_restorable_dump`: run `backup.nightly` against `azentio_test` → the file exists, sha256 matches, `pg_restore --list` succeeds, and the `backups` row is ok.
- `test_perms_ops_routes`: hr_ops, approver, manager → 403 on `/api/ops/jobs/*/retry`; auditor → 200 on GET and 403 on POST; admin → 200.

## Exit criteria
- `grep -n "while True" backend/` finds no business loops (only the job worker's own loop in `shared/jobs.py`).
- Every `gmail.send`, `extract.extract`, `upsert_record` and `perform` call is reachable only from a job handler. A grep check in `test_phase4.py` verifies that by scanning for call sites outside `jobs_handlers/`.
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
| Rollback | `JOBS_EXTERNAL=off` restores the old loops while the outbox and job tables stay in place (additive). The migrations aren't reversed; the new tables simply go unused. |

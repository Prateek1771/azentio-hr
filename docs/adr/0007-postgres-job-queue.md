# ADR 0007: Run background work on a Postgres job queue

- **Status:** accepted; scope narrowed from Phase 4 by [ADR 0011](0011-temporal-orchestration.md). The jobs table becomes the transactional outbox and short-task queue, and Temporal runs the long-lived workflows.
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
All background work runs as rows in a `jobs` table: external calls (Gmail, LLM, connectors), reminders, escalations and document routing.
- Workers claim jobs with `FOR UPDATE SKIP LOCKED`, so two workers never take the same job.
- A unique `idempotency_key` means the same work enqueued twice runs once.
- Failures retry with exponential backoff. After `max_attempts` a job is marked `dead` and an `ops` exception opens in `work_queue`.
- A job is enqueued in the same transaction as the change that caused it.

## Consequences
- No broker to run. Jobs are backed up with everything else and visible with SQL.
- A crash can't lose a job or leave its triggering change half-applied.
- Retries can't duplicate side effects, provided handlers key their side effects on the job's idempotency key.
- Revisit if job volume reaches thousands per second, or if separate teams need their own queues.

## Alternatives considered
| Option | Why not |
|---|---|
| Redis Streams in FalkorDB | Doesn't share a transaction with Postgres data, so a job and its triggering change can get out of step |
| Celery or arq with a broker | Another service to run and monitor, for modest volume |

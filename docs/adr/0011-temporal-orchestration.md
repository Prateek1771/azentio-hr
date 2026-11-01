# ADR 0011: Orchestrate durable workflows with Temporal from Phase 4, on top of Postgres

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering
- **Narrows:** [ADR 0007](0007-postgres-job-queue.md), from Phase 4 onward

## Context
Lifecycle work in HR runs for a long time and holds state:
- rule draft → approval → publish
- document review
- joiners close to their start date who get flagged as exceptions
- transfer, offboarding and rehire
- compliance recheck reminders
- escalation ladders
- waiting on appeals

These flows wait days for people, set timers, and must survive restarts. Writing them by hand on top of a job table means building our own state machines, timers and signal handling.

[Temporal](https://temporal.io/) provides these as a platform: durable workflow state, retries, timeouts, waits for human input, schedules, signals, and visibility into each running flow. It's also another system to run. Phase 0 is about trust (auth, RBAC, audit, bootstrap), and adding infrastructure before those gaps are closed would only add risk.

## Decision
- **Temporal arrives in Phase 4** as the orchestration layer. It is not part of Phase 0.
- **Postgres stays the system of record.** Temporal orchestrates steps; it never holds the authoritative HR state.
  - Workflows and activities read and write state only through the module services (`policy/`, `documents/`, `lifecycle/`).
- **The Phase 0 `jobs` table becomes the transactional outbox and short-task queue.**
  - A business change and its "start or signal workflow" row commit in the same Postgres transaction.
  - The jobs worker relays those rows to Temporal, using the job kinds `temporal.start` and `temporal.signal`.
  - The job's idempotency key becomes the workflow ID, so a repeated start does nothing.
  - No schema change is needed.

Each type of background work has exactly one owner:

| Work | Owner |
|---|---|
| Waiting on a person, timers, multi-day state machines | Temporal workflow |
| A single side effect inside a workflow (Gmail send, LLM extraction, connector push) | Temporal activity, with a retry policy |
| A single side effect outside any workflow | Postgres job |
| Handing a committed change to Temporal | Postgres outbox row, relayed by the jobs worker |
| Local operational tasks (graph rebuild, audit import, alert evaluation) | Postgres job |

Deployment:
- Development uses `temporalio/auto-setup` with its own `temporal` database inside the existing Postgres container, plus the Temporal UI.
- Production runs either the same self-hosted setup or Temporal Cloud, chosen by env.

```
HR action ──tx──▶ Postgres: business change + outbox row (jobs)
                          │  jobs worker relays (kind temporal.start / temporal.signal)
                          ▼
                 Temporal: start / signal workflow  (workflow id = idempotency key)
                          │
             activities ──┼─ Gmail send, LLM extract, connector push   (retry policies)
                          ├─ timers, waits for approval or return-to-work signals
                          ▼
             module services ──▶ Postgres (state written back transactionally)
```

## Consequences
- **Easier:**
  - Waiting on people and timers become a few lines of workflow code instead of hand-built state machines.
  - Every running flow can be inspected in the Temporal UI.
  - Restarts never lose progress.
- **Unchanged:**
  - HR state, approvals and audit stay in Postgres, transactional and queryable.
  - A business change and its workflow start can never get out of step, because the outbox commits with the change.
- **Harder:**
  - One more system to run, upgrade and monitor. `/ready` checks Temporal, and an alert fires on workflow failures.
  - Workflow code must be deterministic. Temporal's replay tests in CI catch violations.
- **Watch:** keep the ownership rule strict. If the same work ever gets retried by both Temporal and a job, the boundary has been broken.
- **Revisit** if workflow volume or team structure changes, for example a separate platform team running Temporal for several products.

## Alternatives considered
| Option | Why not |
|---|---|
| Keep both queues independent (short tasks in jobs, long flows in Temporal, no outbox) | Two parallel retry systems and unclear authority. The DB change and the workflow start can get out of step. |
| Replace the jobs table entirely; start workflows straight from request handlers | A change can commit while the workflow start fails, or the reverse. Simple tasks still need somewhere to run. |
| Introduce Temporal in Phase 0 | Adds infrastructure before the trust gaps (auth, audit, governance) are closed. |
| Build durable workflows by hand on the jobs table | We'd end up rebuilding timers, signals and workflow history ourselves. |

# ADR 0005: Use PostgreSQL as the system of record, migrated by domain

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
Every authoritative record lives in Postgres: users, sessions, audit, jobs, approvals, the exception queue, rule versions, employees, checklist items, documents, review decisions, and later connectors and integrations. Each record type moves in the phase whose feature needs it in Postgres:
- Phase 1: Employee, Item, Rule
- Phase 2: Document
- Phase 3: everything else

The steps for each type are dual-write, backfill, switch the read source in the bridge once parity passes, then stop graph writes. The driver is `psycopg` with plain SQL, and migrations are numbered `.sql` files applied at startup. There's no ORM and no Alembic.

This decision carries out the production plan of [ADR 0002](0002-graph-database.md) early, and supersedes the storage part of 0002.

## Consequences
- Transactions, constraints and history make approvals and the audit trail defensible.
- One proven database to back up, restore and monitor.
- Harder: while a record type is being migrated, it's written to both stores. The bridge's rule that each type is read from exactly one source keeps that safe.
- Revisit if the relational schema starts duplicating graph traversals that Cypher expressed far more simply.

## Alternatives considered
| Option | Why not |
|---|---|
| Keep FalkorDB as the system of record | No transactions across records, weak constraints, and an awkward basis for an audit trail |
| Migrate everything in one cutover | Freezes the product for weeks with one huge, risky switch |
| Use an ORM with Alembic | More machinery than plain SQL needs at this size |

# ADR 0006: Keep FalkorDB as a rebuild-only read model

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
FalkorDB keeps only what it's good at: the item dependency graph, the relationship view, and the policy chunks the assistant retrieves. It is never the source of truth for HR records, audit, approvals, document decisions or jobs.

The graph is rebuilt on purpose, from Postgres:
- `rebuild_graph()` rebuilds everything.
- `rebuild_graph(eid)` rebuilds one employee and runs in the same request as the write that changed them.

There's no background sync.

## Consequences
- The graph can't drift from Postgres unnoticed, and it can be thrown away and rebuilt at any time.
- Graph reads stay fast for dependency walks and the graph view.
- Harder: writes that touch an employee pay for a small rebuild of that subgraph, so watch that write path's latency.

## Alternatives considered
| Option | Why not |
|---|---|
| Background sync from Postgres to the graph | Drift and race conditions that are hard to see |
| Drop the graph entirely | Loses the dependency view and the vector retrieval we rely on |

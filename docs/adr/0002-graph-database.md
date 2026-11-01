# ADR 0002: FalkorDB for the build; PostgreSQL as the production system of record

- **Status:** accepted for the build; storage part superseded by [ADR 0005](0005-postgres-system-of-record.md)
- **Date:** 2026-11-01
- **Deciders:** engineering

## Context
The onboarding plan is a graph: items depend on items, owners own items, documents belong to joiners and mismatch their profiles. FalkorDB gave us Cypher for dependency walks, a built-in vector index for policy retrieval, and a graph view HR can explore, all in one Redis-compatible container. That was the right trade for a fast build.

For production we need:
- multi-tenant isolation,
- managed high availability with point-in-time recovery,
- schema migrations and constraints,
- partitioning for event and audit tables that grow to about 100M rows a year at scale ([production §2](../production-architecture.md#2-scale-targets-and-load-model)).

## Decision
- **Now:** keep FalkorDB, and route all queries through a repository layer ([project architecture §5](../project-architecture.md#5-migration-plan)) so the store can change without touching business logic.
- **Before multi-tenant SaaS (P2):** move the system of record to PostgreSQL with row-level security on `tenant_id`, and pgvector for retrieval.
- **Dependencies:** model them as a join table. With about 25 items per joiner, recompute stays cheap with a recursive CTE.
- **Graph view:** FalkorDB may remain as a derived read model if HR uses it.

## Consequences
**What gets easier**
- Tenant isolation becomes a database guarantee.
- Operations use standard managed tooling.

**What gets harder**
- A migration project, and dependency walks become SQL instead of Cypher.
- 23 modules query the graph directly today, so the repository refactor must come first.

**Revisit if** cross-joiner graph queries (org-wide dependency analysis) become a core feature.

## Alternatives considered
| Option | Why not |
|---|---|
| Stay on FalkorDB in production | Weaker managed HA, PITR, migrations and row-level security story; tenancy enforced only in code |
| Neo4j | Strong graph features, but heavier to operate and license, and the same tenancy concerns |
| PostgreSQL from day one | Slower to build the graph-shaped features and the graph view in the time available |

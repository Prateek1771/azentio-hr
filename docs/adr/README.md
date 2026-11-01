# Architecture Decision Records

One decision per file, numbered in order. Copy [0000-template.md](0000-template.md) to start a new one. Never edit an accepted ADR's decision; supersede it with a new one and link both ways.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-llm-perceives-rules-decide.md) | The LLM perceives; deterministic rules decide | Accepted |
| [0002](0002-graph-database.md) | FalkorDB for the build; PostgreSQL as the production system of record | Storage part superseded by 0005 |
| [0003](0003-openai-compatible-llm.md) | One OpenAI-compatible LLM client, behind a gateway in production | Client accepted; gateway proposed |
| [0004](0004-guardrails-regex-first.md) | Heuristic guardrails first, classifier model when data says so | Accepted |
| [0005](0005-postgres-system-of-record.md) | Use PostgreSQL as the system of record, migrated by domain | Accepted |
| [0006](0006-graph-as-read-model.md) | Keep FalkorDB as a rebuild-only read model | Accepted |
| [0007](0007-postgres-job-queue.md) | Run background work on a Postgres job queue | Accepted; narrowed by 0011 |
| [0008](0008-five-role-rbac.md) | Use five fixed roles enforced by one permission table | Accepted |
| [0009](0009-governed-policy-versions.md) | Govern rule and threshold changes as approved, immutable versions | Accepted |
| [0010](0010-risk-tiered-ai-autonomy.md) | Let AI auto-accept only low-risk documents under versioned thresholds | Accepted |
| [0011](0011-temporal-orchestration.md) | Orchestrate durable workflows with Temporal from Phase 4, on top of Postgres | Accepted |
| [0012](0012-document-decisions-as-built.md) | Keep document thresholds in rule-set versions, behind a legal floor, with append-only decisions | Accepted |

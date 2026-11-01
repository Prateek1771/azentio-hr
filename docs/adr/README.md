# Architecture Decision Records

One decision per file, numbered in order. Copy [0000-template.md](0000-template.md) to start a new one. Never edit an accepted ADR's decision; supersede it with a new one and link both ways.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-llm-perceives-rules-decide.md) | The LLM perceives; deterministic rules decide | Accepted |
| [0002](0002-graph-database.md) | FalkorDB for the build; PostgreSQL as the production system of record | Accepted / migration proposed |
| [0003](0003-openai-compatible-llm.md) | One OpenAI-compatible LLM client, behind a gateway in production | Client accepted; gateway proposed |
| [0004](0004-guardrails-regex-first.md) | Heuristic guardrails first, classifier model when data says so | Accepted |

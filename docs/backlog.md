# Backlog

Every enhancement, deferred item and known limitation documented in this repo, tracked as a GitHub issue. This page is the index. The issue holds the detail: scope, checkpoints, the test cases it must pass, dependencies, risks and source references.

Generated on 2026-11-03 from the phase plans, `production-architecture.md`, the LLD's limitations table (§14/§15), ADR "revisit if" sections and `ponytail:` comments. When you add or close work, update the issue and this table in the same PR.

## How it's organised
- **Milestones** group issues by phase. Phases 4–6 each have an **epic** (label `epic`) whose checklist is the phase's work items and exit criteria.
- **Labels:** `phase-N`, `production` (needed before a real pilot), `backlog` (no phase yet), plus `security`, `tech-debt`, `ops`, `enhancement`, `documentation`, `decision` (needs a call before code) and `area:*`.
- **Every issue has** a Summary, Scope, Checkpoints (one commit/PR each), *Test cases it must pass* (named tests with given/when/then, plus the always-on gate: ruff, the boundary guard, every `test_phaseN.py`, the API contract and, for UI work, smoke for all roles), Acceptance criteria, Dependencies, Risks and References.
- Work happens on `phase-N-<name>` branches (see [AGENTS.md](../AGENTS.md)). Reference the issue in the PR so merging closes it.

## Phase 4 – Ops backbone + Temporal
Plan: [docs/plans/phase-4-ops-backbone.md](plans/phase-4-ops-backbone.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#102](https://github.com/Prateek1771/azentio-hr/issues/102) | **[P4] Epic: Ops backbone + Temporal: every background task runs, retries and alerts on its own** | `epic` `phase-4` | #13 |
| [#64](https://github.com/Prateek1771/azentio-hr/issues/64) | [P4] Temporal infrastructure: compose services, client, worker, activities and retry policies | `phase-4` `enhancement` `area:infra` `area:backend` |  |
| [#65](https://github.com/Prateek1771/azentio-hr/issues/65) | [P4] Transactional outbox relay to Temporal (`temporal.start` / `temporal.signal` jobs) | `phase-4` `enhancement` `area:backend` | #64 |
| [#66](https://github.com/Prateek1771/azentio-hr/issues/66) | [P4] Exactly-once email outbox (`outbox` table, deterministic Message-ID, `email.send` job) | `phase-4` `enhancement` `area:backend` `area:frontend` | #71 |
| [#67](https://github.com/Prateek1771/azentio-hr/issues/67) | [P4] Gmail intake: label only after ingest, `gmail_messages` cursor, `GmailPollWorkflow` schedule | `phase-4` `enhancement` `area:backend` | #64, #65, #68, #71 |
| [#68](https://github.com/Prateek1771/azentio-hr/issues/68) | [P4] Async document intake + LLM extraction job that degrades to human review | `phase-4` `enhancement` `area:backend` `area:frontend` `area:ai` | #71 |
| [#69](https://github.com/Prateek1771/azentio-hr/issues/69) | [P4] Integration runs as Temporal schedules; `connector.push` and `access.perform` as idempotent jobs | `phase-4` `enhancement` `area:backend` | #64, #65, #66 |
| [#70](https://github.com/Prateek1771/azentio-hr/issues/70) | [P4] Durable workflows for rule-version approval and high-risk document review (SLA timers, auto-publish) | `phase-4` `enhancement` `area:backend` | #64, #65 |
| [#71](https://github.com/Prateek1771/azentio-hr/issues/71) | [P4] Jobs framework: recurring jobs, `RetryLater`, error history, heartbeats, retry/discard | `phase-4` `enhancement` `area:backend` |  |
| [#72](https://github.com/Prateek1771/azentio-hr/issues/72) | [P4] Reminder sweep and job-backed escalation check (one email per item per day) | `phase-4` `enhancement` `area:backend` | #66, #71 |
| [#73](https://github.com/Prateek1771/azentio-hr/issues/73) | [P4] Alerting: `alerts` table, `alert.evaluate` rules, email + webhook, ack | `phase-4` `enhancement` `area:backend` | #66, #71, #74 |
| [#74](https://github.com/Prateek1771/azentio-hr/issues/74) | [P4] Extend `/ready`: shared readiness checks with required vs degraded | `phase-4` `enhancement` `area:backend` | #64, #71 |
| [#75](https://github.com/Prateek1771/azentio-hr/issues/75) | [P4] Ops metrics endpoint `GET /api/ops/metrics` (SQL-computed, no in-process counters) | `phase-4` `enhancement` `area:backend` | #71 |
| [#76](https://github.com/Prateek1771/azentio-hr/issues/76) | [P4] Ops API + Ops → Jobs (DLQ) screen: retry, discard with reason, bulk retry, workflows tab | `phase-4` `enhancement` `area:backend` `area:frontend` | #71, #73, #75 |
| [#77](https://github.com/Prateek1771/azentio-hr/issues/77) | [P4] Ops → Health screen, Ops nav + alert dot, outbox on the employee timeline, Logs → Messages tab | `phase-4` `enhancement` `area:frontend` | #74, #75, #76, #78 |
| [#78](https://github.com/Prateek1771/azentio-hr/issues/78) | [P4] Nightly backups (`pg_dump` + uploads + FalkorDB), retention, restore runbook and monthly drill | `phase-4` `enhancement` `area:infra` `area:backend` `ops` | #71, #73 |
| [#79](https://github.com/Prateek1771/azentio-hr/issues/79) | [P4] CI: `test_phase4.py` with time-skipping Temporal, Replayer test, `pip-audit` + `npm audit` | `phase-4` `enhancement` `area:infra` `ops` | #64 |
| [#80](https://github.com/Prateek1771/azentio-hr/issues/80) | [P4] Phase 4 migration + cutover: dry-run loops, paused schedules, backfills, delete old loops; dev seed | `phase-4` `enhancement` `area:backend` `ops` | #66, #67, #68, #69, #70, #71, #72, #73, #78 |

## Phase 5 – Employee lifecycle
Plan: [docs/plans/phase-5-lifecycle.md](plans/phase-5-lifecycle.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#103](https://github.com/Prateek1771/azentio-hr/issues/103) | **[P5] Epic: Employee lifecycle (transfer, offboarding, rechecks, contractor/rehire, leave)** | `epic` `phase-5` |  |
| [#81](https://github.com/Prateek1771/azentio-hr/issues/81) | [P5] Lifecycle case core: model, state machine, schema and rule-matching extensions | `phase-5` `enhancement` `area:backend` |  |
| [#82](https://github.com/Prateek1771/azentio-hr/issues/82) | [P5] Lifecycle Temporal workflows, activities and reconcile jobs | `phase-5` `enhancement` `area:backend` `area:infra` | #81, #64, #65, #71 |
| [#83](https://github.com/Prateek1771/azentio-hr/issues/83) | [P5] Case preview API, lifecycle dialogs and the employee Cases tab | `phase-5` `enhancement` `area:backend` `area:frontend` | #81, #82 |
| [#84](https://github.com/Prateek1771/azentio-hr/issues/84) | [P5] Transfer case: role/department/location/grade/manager change with access diff | `phase-5` `enhancement` `area:backend` `area:frontend` | #81, #82, #83, #90, #91, #69 |
| [#85](https://github.com/Prateek1771/azentio-hr/issues/85) | [P5] Offboarding case: timed access revokes, exit items, portal token revocation | `phase-5` `enhancement` `security` `area:backend` `area:frontend` | #81, #82, #83, #90, #69, #66 |
| [#86](https://github.com/Prateek1771/azentio-hr/issues/86) | [P5] Compliance rechecks for expiring documents + Compliance page | `phase-5` `enhancement` `security` `area:backend` `area:frontend` | #81, #82, #66, #65 |
| [#87](https://github.com/Prateek1771/azentio-hr/issues/87) | [P5] Worker type (employee/contractor/intern) as a match key; contractor conversion | `phase-5` `enhancement` `area:backend` `area:frontend` | #81, #84, #90 |
| [#88](https://github.com/Prateek1771/azentio-hr/issues/88) | [P5] Rehire detection and document reuse | `phase-5` `enhancement` `area:backend` `area:frontend` | #81, #90 |
| [#89](https://github.com/Prateek1771/azentio-hr/issues/89) | [P5] Leave and return-to-work cases | `phase-5` `enhancement` `area:backend` `area:frontend` | #81, #82, #83, #72 |
| [#90](https://github.com/Prateek1771/azentio-hr/issues/90) | [P5] Employee status, worker_type columns and employee_history | `phase-5` `enhancement` `area:backend` `area:frontend` | #81 |
| [#91](https://github.com/Prateek1771/azentio-hr/issues/91) | [P5] Block matching-attribute edits via PATCH; route them to Change role | `phase-5` `enhancement` `area:backend` `area:frontend` | #84 |
| [#92](https://github.com/Prateek1771/azentio-hr/issues/92) | [P5] Publish lifecycle rules through governance; seed data and rollout | `phase-5` `enhancement` `area:backend` | #81, #84, #85, #86, #87, #88, #89 |

## Phase 6 – HR ops platform
Plan: [docs/plans/phase-6-hr-ops-platform.md](plans/phase-6-hr-ops-platform.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#104](https://github.com/Prateek1771/azentio-hr/issues/104) | **[P6] Epic: HR ops platform (manager view, SLAs, escalations, exceptions/appeals, Inbox, analytics)** | `epic` `phase-6` |  |
| [#93](https://github.com/Prateek1771/azentio-hr/issues/93) | [P6] Reporting lines and server-side manager scoping | `phase-6` `enhancement` `security` `area:backend` | #90 |
| [#94](https://github.com/Prateek1771/azentio-hr/issues/94) | [P6] My team page and manager mode on the employee page | `phase-6` `enhancement` `area:backend` `area:frontend` | #93, #95 |
| [#95](https://github.com/Prateek1771/azentio-hr/issues/95) | [P6] Comments, @mentions and in-app notifications | `phase-6` `enhancement` `area:backend` `area:frontend` | #93, #66 |
| [#96](https://github.com/Prateek1771/azentio-hr/issues/96) | [P6] SLA policies, versioned through governance, and SLA clocks | `phase-6` `enhancement` `area:backend` `area:frontend` | #93, #64, #65, #89 |
| [#97](https://github.com/Prateek1771/azentio-hr/issues/97) | [P6] Escalation ladder as EscalationWorkflow (replaces escalation.check) | `phase-6` `enhancement` `area:backend` `area:infra` | #96, #95, #72, #66 |
| [#98](https://github.com/Prateek1771/azentio-hr/issues/98) | [P6] Exceptions and appeals: waiver, extension, override, appeal | `phase-6` `enhancement` `security` `area:backend` `area:frontend` | #96, #93, #64, #65 |
| [#99](https://github.com/Prateek1771/azentio-hr/issues/99) | [P6] Unified Inbox: one place for every human decision | `phase-6` `enhancement` `area:backend` `area:frontend` | #96, #98, #95, #76 |
| [#100](https://github.com/Prateek1771/azentio-hr/issues/100) | [P6] Analytics: overdue heatmap, time-to-ready, exceptions, SLA attainment, AI accuracy | `phase-6` `enhancement` `area:backend` `area:frontend` `area:ai` | #93, #96, #98 |
| [#101](https://github.com/Prateek1771/azentio-hr/issues/101) | [P6] Phase 6 seed data, migration and rollout | `phase-6` `enhancement` `area:backend` | #93, #94, #95, #96, #97, #98, #99, #100 |

## Phase 3 – Retire graph record storage
Plan: [docs/plans/phase-3-retire-graph-records.md](plans/phase-3-retire-graph-records.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#13](https://github.com/Prateek1771/azentio-hr/issues/13) | [P3] Merge the Phase 3 stack and run cutover + cleanup outside dev | `phase-3` `ops` `area:backend` |  |
| [#14](https://github.com/Prateek1771/azentio-hr/issues/14) | [P3] Delete Phase 3 BGSAVE snapshots and rotate any credential they held | `phase-3` `security` `ops` | #13 |

## Production readiness
Plan: [docs/production-architecture.md](production-architecture.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#15](https://github.com/Prateek1771/azentio-hr/issues/15) | [Prod] Store uploaded documents in object storage (S3/GCS) with KMS and presigned URLs | `production` `enhancement` `area:backend` `area:infra` | #78 |
| [#16](https://github.com/Prateek1771/azentio-hr/issues/16) | [Prod] OIDC single sign-on, IdP-enforced MFA and email password reset | `production` `security` `area:backend` `area:frontend` | #66 |
| [#17](https://github.com/Prateek1771/azentio-hr/issues/17) | [Prod] Map IdP groups to roles; decide on custom roles | `production` `security` `decision` `area:backend` | #16 |
| [#18](https://github.com/Prateek1771/azentio-hr/issues/18) | [Prod] Multi-tenancy with Postgres RLS (and connector customer_id) | `production` `security` `area:backend` `area:infra` | #38, #31 |
| [#19](https://github.com/Prateek1771/azentio-hr/issues/19) | [Prod] LLM gateway with a paid primary model under a DPA, fallback and budgets | `production` `area:ai` `area:backend` | #73 |
| [#20](https://github.com/Prateek1771/azentio-hr/issues/20) | [Prod] LLM cost levers: cheap classifier, file-hash cache, semantic answer cache | `production` `enhancement` `area:ai` `area:backend` | #19 |
| [#21](https://github.com/Prateek1771/azentio-hr/issues/21) | [Prod] Gmail push intake via Pub/Sub (or a dedicated inbound domain) | `production` `enhancement` `area:backend` `area:infra` | #67 |
| [#22](https://github.com/Prateek1771/azentio-hr/issues/22) | [Prod] Joiner portal tokens: hash, expire, rotate and sign links | `production` `security` `area:backend` `area:frontend` | #66 |
| [#23](https://github.com/Prateek1771/azentio-hr/issues/23) | [Prod] Move the login throttle to Postgres | `production` `security` `area:backend` |  |
| [#24](https://github.com/Prateek1771/azentio-hr/issues/24) | [Prod] Audit chain: scheduled verification, signed export, WORM archive and partitioning | `production` `security` `area:backend` | #73, #15 |
| [#25](https://github.com/Prateek1771/azentio-hr/issues/25) | [Prod] Personal data: encryption at rest, retention schedule and right to erasure | `production` `security` `decision` `area:backend` | #15, #38 |
| [#26](https://github.com/Prateek1771/azentio-hr/issues/26) | [Prod] Scan uploads for malware before extraction | `production` `security` `area:backend` `area:infra` | #68 |
| [#27](https://github.com/Prateek1771/azentio-hr/issues/27) | [Prod] Observability: OpenTelemetry, log shipping and self-hosted Langfuse with name masking | `production` `ops` `security` `area:infra` `area:backend` | #73, #75 |
| [#28](https://github.com/Prateek1771/azentio-hr/issues/28) | [Prod] Horizontal deployment: stateless API pods, separate job and Temporal workers, PgBouncer | `production` `ops` `area:infra` | #23, #15, #32, #64 |
| [#29](https://github.com/Prateek1771/azentio-hr/issues/29) | [Prod] Database HA and disaster recovery (managed Multi-AZ Postgres, PITR, second-region restore) | `production` `ops` `area:infra` | #78, #32 |
| [#30](https://github.com/Prateek1771/azentio-hr/issues/30) | [Prod] Hot-path performance: dashboard cache, portal read replica, per-joiner co-pilot digests | `production` `enhancement` `area:backend` `area:ai` | #31 |
| [#31](https://github.com/Prateek1771/azentio-hr/issues/31) | [Prod] Decide: policy and digest retrieval in FalkorDB vs pgvector; enable embeddings | `production` `decision` `area:ai` |  |
| [#32](https://github.com/Prateek1771/azentio-hr/issues/32) | [Prod] Decide hosting: Kubernetes vs ECS Fargate, plus CDN/WAF and Temporal hosting | `production` `decision` `ops` `area:infra` | #64 |
| [#33](https://github.com/Prateek1771/azentio-hr/issues/33) | [Prod] Compliance programme: SOC 2 Type II, ISO 27001, DPDP/GDPR processor terms, annual pen test | `production` `security` | #16, #24, #25 |
| [#34](https://github.com/Prateek1771/azentio-hr/issues/34) | [Prod] Define SLOs and enforce the P1/P2/P3 stage gates | `production` `ops` | #27, #75, #33, #18, #29 |
| [#35](https://github.com/Prateek1771/azentio-hr/issues/35) | [Prod] Preview, staging and production environments with automated deploys | `production` `ops` `area:infra` | #32, #36 |
| [#36](https://github.com/Prateek1771/azentio-hr/issues/36) | [Prod] Run the backend and frontend in docker-compose | `production` `ops` `area:infra` |  |
| [#38](https://github.com/Prateek1771/azentio-hr/issues/38) | [Prod] Connector and field secrets: KMS envelope encryption | `production` `security` `area:backend` `area:infra` | #32 |

## Backlog
Plan: [docs/lld.md](lld.md)

| # | Issue | Labels | Depends on |
|---|---|---|---|
| [#37](https://github.com/Prateek1771/azentio-hr/issues/37) | [Backlog] Guardrails: add a classifier model and optionally buffer output | `backlog` `security` `area:ai` `area:backend` | #56 |
| [#39](https://github.com/Prateek1771/azentio-hr/issues/39) | [Backlog] Gmail: narrow OAuth scopes, dedicated mailbox, secrets out of .env | `backlog` `security` `area:backend` | #32 |
| [#40](https://github.com/Prateek1771/azentio-hr/issues/40) | [Backlog] Default the keyless public LLM fallback to off in production | `backlog` `security` `area:ai` |  |
| [#41](https://github.com/Prateek1771/azentio-hr/issues/41) | [Backlog] CI security scans: gitleaks and semgrep (Phase 4 covers pip-audit and npm audit) | `backlog` `security` `ops` `area:infra` | #79 |
| [#42](https://github.com/Prateek1771/azentio-hr/issues/42) | [Backlog] Graph write guard can't see every label pattern | `backlog` `tech-debt` `area:backend` | #13 |
| [#43](https://github.com/Prateek1771/azentio-hr/issues/43) | [Backlog] A full read-model rebuild can overwrite a concurrent employee edit | `backlog` `tech-debt` `area:backend` | #13 |
| [#44](https://github.com/Prateek1771/azentio-hr/issues/44) | [Backlog] policy.after_replay retry can repeat activity-feed lines | `backlog` `tech-debt` `area:backend` |  |
| [#45](https://github.com/Prateek1771/azentio-hr/issues/45) | [Backlog] Convert epoch-double time columns to timestamptz | `backlog` `tech-debt` `area:backend` `area:frontend` | #13 |
| [#46](https://github.com/Prateek1771/azentio-hr/issues/46) | [Backlog] Drop legacy_rules, legacy_config and legacy_sync_runs archive tables | `backlog` `tech-debt` `decision` `area:backend` | #13, #14 |
| [#47](https://github.com/Prateek1771/azentio-hr/issues/47) | [Backlog] Move the task → app-action map onto the governed rule | `backlog` `enhancement` `area:backend` `area:frontend` | #81 |
| [#48](https://github.com/Prateek1771/azentio-hr/issues/48) | [Backlog] Index connector record JSON lookups | `backlog` `tech-debt` `area:backend` | #13 |
| [#49](https://github.com/Prateek1771/azentio-hr/issues/49) | [Backlog] Decouple the integration engine from FastAPI | `backlog` `tech-debt` `area:backend` | #69 |
| [#50](https://github.com/Prateek1771/azentio-hr/issues/50) | [Backlog] One settings module (shared/config) instead of 42 scattered os.getenv calls | `backlog` `tech-debt` `area:backend` |  |
| [#51](https://github.com/Prateek1771/azentio-hr/issues/51) | [Backlog] Move evals out of the app environment (ragas pins hold back the OpenAI SDK) | `backlog` `tech-debt` `area:ai` | #56 |
| [#52](https://github.com/Prateek1771/azentio-hr/issues/52) | [Backlog] Real vendor API calls for Slack, Jira, ServiceNow and Google Workspace | `backlog` `enhancement` `area:backend` | #69, #85 |
| [#53](https://github.com/Prateek1771/azentio-hr/issues/53) | [Backlog] Use a real Markdown renderer for policy answers (tables) | `backlog` `enhancement` `area:frontend` |  |
| [#54](https://github.com/Prateek1771/azentio-hr/issues/54) | [Backlog] Thread correction emails with In-Reply-To and References headers | `backlog` `enhancement` `area:backend` | #66 |
| [#55](https://github.com/Prateek1771/azentio-hr/issues/55) | [Backlog] Decide on AwaitVerify human-in-the-loop extraction | `backlog` `enhancement` `decision` `area:ai` |  |
| [#56](https://github.com/Prateek1771/azentio-hr/issues/56) | [Backlog] Evals: extraction gold set, scripted agent conversations, paid judge, evals in CI | `backlog` `enhancement` `area:ai` `area:backend` | #19 |
| [#57](https://github.com/Prateek1771/azentio-hr/issues/57) | [Backlog] Revisit the one-open-draft limit (parallel drafts, separate threshold cadence) | `backlog` `enhancement` `decision` `area:backend` |  |
| [#58](https://github.com/Prateek1771/azentio-hr/issues/58) | [Backlog] Editor for config values (doc types, locations, departments, roles) | `backlog` `enhancement` `area:backend` `area:frontend` |  |
| [#59](https://github.com/Prateek1771/azentio-hr/issues/59) | [Backlog] Finer approval policy than the single compliance flag | `backlog` `enhancement` `area:backend` |  |
| [#60](https://github.com/Prateek1771/azentio-hr/issues/60) | [Backlog] Tune the extraction prompt and model against the gold set | `backlog` `enhancement` `area:ai` | #56 |
| [#61](https://github.com/Prateek1771/azentio-hr/issues/61) | [Backlog] Model-assisted review suggestions (HR confirms, never auto-accept) | `backlog` `enhancement` `area:ai` `area:frontend` |  |
| [#62](https://github.com/Prateek1771/azentio-hr/issues/62) | [Backlog] Monitor the ADR revisit triggers | `backlog` `ops` `area:backend` | #75 |
| [#63](https://github.com/Prateek1771/azentio-hr/issues/63) | [Backlog] Fix docs drift and cross-doc inconsistencies | `backlog` `documentation` | #13 |

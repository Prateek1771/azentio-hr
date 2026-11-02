# Phase 3: Retire graph record storage

- **Status:** in progress. PR 0 (tooling), A1 (owners, email templates), A2 (policies), A3 (rules, config), B (activity) and C (connectors) are done; D (integrations) is in progress; E follows.
- **Depends on:** Phases 0–2 (Employee, Item, Rule and Document are in Postgres).
- **Branches:** `phase-3-pr0-tooling`, then `phase-3-<batch>` per batch.
- **Cutover checklist and results:** [docs/runbooks/phase-3-cutover.md](../runbooks/phase-3-cutover.md).

## As built so far (PR 0)
- `backend/graph_migration.py` is the engine. A `LabelSpec` per label gives the graph query, the table, the row
  mapper, a canonical form, a reverse mapper for rollback, its edges, deliberately dropped keys and the files allowed to
  keep reading the label. It provides `cutover(batch)`, `label_report`, `drift_since_cutover()`, `rollback(batch)` and
  `full_report()`. The CLI is `python -m graph_migration report|drift|rollback <batch> [--out file]`.
- The freeze: `cutover` holds a Postgres advisory lock on its own connection, and `check_freeze` watches other
  instances' heartbeats from the last 60 s for 15 s. A heartbeat that advances is a live instance, so cutover refuses
  ("stop other instances, then restart"). One that stands still is a stopped process, as on a restart, and is
  allowed. The evidence is stored in the label's report (`freeze`).
- The bridge rows hold the whole report: before counts and checksums, after parity, the schema diff, edge counts,
  `cutover_at` and `graph_checksum_at_cutover`. The engine upserts `bridge_state` itself, because `bridge.switch` inserts
  with `ON CONFLICT DO NOTHING`, which can't switch back after a rollback.
- `bootstrap.migrate_graph_batches()` runs after `migrate_documents()` and before the worker, poller and scheduler.
- `GET /api/admin/migration` (admin, auditor) and the **System** page show the report. `/ready` adds
  `graph_migration: drift` when a switched label's graph checksum has changed since its cutover.
- `scripts/api_contract.py --capture|--check` records the shape of 24 read endpoints on a deterministic fixture
  (`backend/tests_snapshots/api_contract.json`). `test_phase3` runs `--check`.
- The read-path scan matches any node pattern naming the label (`(x:A:Label`, also on continuation lines of a multi-line
  query, reads and writes) and label tests after `WHERE`/`AND`/`OR`/`NOT`. It ignores type hints and edge types. It can't see
  a label built at runtime (`f"(n:{label})"`); the runtime `graph.q()` check (PR E) covers that.
- `rollback` takes the same lock and freeze check as `cutover`: it refuses while any instance is live, because a running
  app would keep writing to Postgres after the switch back.
- `test_phase3.py` proves the engine on a synthetic label (`P3Probe`): schema diff, read-path scan, freeze (a live
  heartbeat, a stopped one, a held lock), refusal when not clean, cutover, idempotency, drift, the report, and a
  rollback round trip that includes rows written after cutover, and refusal to roll back while the app is live.

## As built: batch A1 (owners, email templates)
- **Changed from the plan:** `Config` moved from A1 to **A3**. Its only reader that matters is `policy.store.ensure_active`
  building v1 from graph `Rule` + `Config` on a fresh install, and its only writer is `ingest.import_requirements`.
  Retiring it means rewriting that import flow, which is A3, so A3 retires `Rule` and `Config` together.
- Migration `011_owners_templates.sql`: `owners` (email PK, lowercase CHECK) and `email_templates`. The columns are exactly
  the old graph properties, so `/api/templates` and `/api/rules.owners` keep their shape (the contract check passes).
- `owners.py` (`all`, `get`, `emails(type)`, `by_category`, `manager_for`, `upsert`) and `templates.py` (`all`, `get`,
  `upsert`) are the only readers and writers. Each registers its `LabelSpec`; `graph_migration.SPEC_MODULES` lists the
  modules and `load()` imports them on first use (bootstrap, CLI, `/api/admin/migration`).
- `Owner` stays in the graph as a **projection**: `records.project_owners()` MERGEs every owner from Postgres and deletes
  the rest. It runs after every `owners.upsert` and at the start of a full `rebuild_graph()`. It never wipes the graph
  from an empty table (a release before the cutover).
- The engine got two small additions: `LabelSpec.graph_query` defaults to `MATCH (n:<label>) RETURN n`, built in the
  engine, so the owning module isn't itself a read path. `LabelSpec.projection=True` makes drift compare the graph with
  Postgres *now*, because a projection legitimately changes after cutover.
- The CLI (`python -m graph_migration`) calls the imported module, not `__main__`, so it sees the registered specs.
- The template save now writes the row and its audit entry in one transaction.
- `test_phase3` A1 section: the old release's graph data, a clean cutover, reads from Postgres (`manager_for`,
  `by_category`, `gmail.render`), the projection following app writes, drift on a stray template write and on an
  out-of-step projection, `rebuild_graph` repairing it, then rollback (the new rows reach the graph) and a second cutover.
- Dev cutover, freeze and rollback drill: see the runbook's A1 section.

## As built: batch A2 (policies)
- Migration `012_policies.sql`: `policies (source PK, title, text)`, exactly the old graph properties (contract unchanged).
- `policies.py` (`all`, `texts`, `upsert`, `title_of`) is the only reader and writer, and registers the `Policy` spec.
- `chat.index_policy` stores the upload in `policies`, then `index_chunks` rebuilds that file's `PolicyChunk` nodes (the
  old chunk/embed code, unchanged). It no longer writes `Policy` nodes; the stale ones go in PR E's cleanup.
- `chat.reindex_policies()` rebuilds the whole index from the table. `ingest.seed` calls it when `policies` has rows
  but the graph has no chunks, so a wiped graph heals its index on restart. PR E's `rebuild_graph` will call it too.
- `/api/policies` and the portal's acknowledgement text read the table.
- `test_phase3` A2 section: cutover, reads, an upload that writes the table and chunks but no `Policy` node, drift on a
  stray write, a wiped index rebuilt with the same retrieval results, then rollback and a second cutover.
- **Found in depth testing: a wiped graph re-seeded over Postgres.** `ingest.seed` decided "first run" from a graph
  `Config` node. Once the graph stopped being the record, losing the graph reset HR's edited templates and owners
  (A1) and the seeded joiners' fields (since Phase 1) to the seed files. Now each record type is seeded only when its
  Postgres table is empty, rules only when there's no active version. A lost graph is detected *before* anything
  writes (a rule replay would project single employees and hide the loss), and the projection is rebuilt instead.
  `test_phase3` "wiped graph" section: HR edits kept, nothing re-seeded, `REPORTS_TO`→`Owner` and the policy index
  rebuilt, no drift. This was also run end to end on a throwaway instance: fresh install, then edits, a
  `GRAPH.DELETE`, and a restart. All 7 employees, 101 items, 20 documents and their edges came back from Postgres.

## As built: batch A3 (legacy rules and config)
- `ingest.import_requirements(filename, data, user=None)` is the one way in. With no active version (a fresh install), it
  creates v1 directly under the existing `pg_advisory_xact_lock(7243010)`, audited `policy.published` (actor `system`
  without a user). With an active version, it opens a governed draft (`import_requirements_draft`, unchanged), and it
  refuses without a user. Both share `_parse_requirements`.
- **Changed from before:** rows without a `compliance` value take `store.COMPLIANCE_DEFAULT` in both paths. Before, an
  imported draft cleared the flags, so re-importing the same file showed R-ID/R-I9/R-BGV as changed and needed two
  approvals.
- `policy.store.ensure_active` (v1 from graph `Rule` + `Config`) is deleted; its callers use `active_version()`.
  `checklist.config` reads only the active version. `seed` gates on `active_version()`, and the `Config roles` top-up is
  gone. `graph.ensure_indexes` no longer indexes `Rule`.
- Graph `Rule`/`Config` are **removed**, but archived first: migration 013 adds `legacy_rules (id, props jsonb)` and
  `legacy_config (key, value)`. The specs live in `policy/legacy.py`. The cutover copies the nodes there, and
  `rollback A3` writes them back for the previous release. Nothing reads the tables; PR E drops them.
- `test_core` creates v1 through the import instead of MERGEing `Config` nodes. `test_phase1`'s v1 check is now
  `v1_created_by_import`. `test_phase3` A3 section: cutover, v1 from the import (rules, compliance, config,
  audited as system), no graph writes, a draft with a user, a refusal without one or with a draft open, drift on a stray
  write, then rollback (a deleted node comes back) and a second cutover.

- **Deep test (A1–A3 together), fixes:** `ingest.seed_rules()` decides the first rule set: the archived graph rules on
  an upgrade from before versions, otherwise the sample file; the loser of a race does nothing. Every start refreshes the
  `Owner` projection. A projection label's cutover rebuilds the projection once. An emptied retired label isn't drift.
  `normalise_rule` parses a text `compliance` cell. `graph_migration.load()` is never silently partial. Details and the
  upgrade/rollback-to-`main`/two-instance results are in the runbook's "Deep test" section.

## Plan: batch D (integrations), written before coding
Status: **in progress** on branch `phase-3-d`, stacked on `phase-3-c`. The migration file is `016_integrations.sql`,
because B and C took 014 and 015.

**Dev graph at planning time** (2026-11-02):

| Label | Rows |
|---|---|
| `Integration` | 3 |
| `AgentMessage` | 34 |
| `IntegrationCandidate`, `WorkflowRun`, `IntegrationLogRecord`, `OutboundMessage` | 0 each |

There are no edges. `AuditLog` (34) stays for E.

**Graph users to move:**
- `integrations/store.py`: integrations and candidates
- `integrations/engine.py`: enrolment, run start and finish, log records
- `integrations/delivery.py`: outbound messages
- `integrations/service.py`: agent messages, the name clash
- `integration_routes.py`: the run log
- `test_core.py` setup

**Tables.** Columns are the old graph properties; times stay epoch doubles (the B and C pattern).

| Table | Notes |
|---|---|
| `integrations` | `id, name, status, created, next_run_at, config jsonb`; UNIQUE on `lower(name)` (case-insensitive duplicates refused by the DB) |
| `integration_candidates` | `id, integration_id, record_id, candidate_ref, name, email, stage, document_results, document_attempts, joiner_id, last_error, generated_values, employee_record_id, access_results, decided_at, rejection_reason, created, updated`; UNIQUE `(integration_id, record_id)`, so enrolment is idempotent under concurrency; index on `joiner_id` |
| `workflow_runs` | `id, integration_id, integration_name, trigger, status, started, finished, counts jsonb, details jsonb`; a partial unique index on `(integration_id) WHERE status = 'running'`, so the DB allows one running run per integration |
| `integration_log_records` | `id, integration_id, run_id, candidate_record_id, candidate_ref, candidate_name, outcome, stage_before, stage_after, actions jsonb, mapping jsonb, error, ts`; **masked before the insert** with the integration's current exclusions |
| `outbound_messages` | the graph's `to` becomes `recipient`, because `to` is an SQL keyword. The API keeps `to` |
| `agent_messages` | `id, integration_id, role, content, ts` |

`document_results`, `generated_values` and `access_results` stay text holding JSON, exactly as the graph stored them, so
every caller keeps its shapes.

**The store.** `integrations/store.py` becomes the only reader and writer of all six tables and registers their specs.
- **Reads:** rows drop `NULL`s on read, as FalkorDB omitted absent properties, so key sets and the API contract don't
  change.
- **Writes:**
  - `set_item` checks a column whitelist and keeps `None` → `""` for text.
  - `enroll_candidate` is `INSERT … ON CONFLICT DO NOTHING`.
  - `start_run` turns the unique violation into `AlreadyRunning`.
  - `create` and renames turn the `lower(name)` violation into `SetupError`.
- **Specs:** `from_row` never emits `None`, because FalkorDB can't store null.
- **Callers:** they keep their signatures (`engine`, `delivery`, `service`, `integration_routes`). The run log pages in
  SQL.

**Tests (`test_phase3` D section):**
- Old graph nodes are seeded at runtime:
  - a candidate without `joiner_id` and one with `generated_values`/`access_results`
  - a run, log records (one with excluded data), outbound and agent messages
- The cutover is clean.
- The store and run-log endpoints equal what the old graph reader returned, key sets included.
- **Constraints:**
  - two threads enrolling the same candidate → one row
  - a second running run → `AlreadyRunning`
  - a case-insensitive duplicate name → `SetupError`
- **Behaviour:**
  - exclusions are masked at rest
  - a simulated outbound message is recorded
  - `say` records an agent message
- **Drift and rollback:**
  - a stray graph write → drift
  - rollback: new rows come back as nodes without null properties
  - a second cutover is clean
- The API contract is unchanged.

**Gate:**
- ruff, the guard, every suite, smoke.
- **Dev drill:**
  - `BGSAVE`, cutover, feature smoke by API (list and open integrations, the setup chat, the run log)
  - **no integration run on dev**: it can send real Gmail
  - `rollback D`, restart: 21 labels at parity, no drift
- **Throwaway stack:**
  - `main` → D, six batches in one start
  - the setup chat, a run, an approval, masked logs and messages (simulated delivery)
  - a full rollback to `main`, then re-upgrade
- CI green on the PR stacked on #10.

## As built: batch C (connectors, secrets at rest)
- Migration `015_connectors.sql` (014 went to B): `connectors`, `connector_instances` (+ `secret_enc`, `secret_key_id`,
  `secret_hint`), `connector_records`, `employee_record_links`, `custom_fields`, `import_runs`, `legacy_sync_runs`.
  The record's field values are the column `data`, because `values` is an SQL keyword; the API still says `values`.
- `connectors/store.py` is the only reader and writer. The `Connector` interface is unchanged; `list_records` pages and
  searches in SQL (search matches field values, not key names). `registry`, `hr_portal`, `candidates`, `seed`,
  `connector_routes`, `integrations/service` and `integrations/engine` call it; `main.create_joiner` adds the
  `from_candidate` link. `connectors/sync.run`/`runs` are deleted; the pure mapping helpers stay.
- **Engine:** `EdgeSpec.backfill` copies edges that have no node of their own (the employee links) inside the cutover
  transaction. Rollback now also deletes graph nodes that lack the key property (`CustomField` had no `id`).
- **Secrets (ADR 0013):** `connectors/secrets.py`, Fernet via `MultiFernet` (`SECRETS_KEY`, `SECRETS_KEY_PREVIOUS`).
  The cutover encrypts the graph's plaintext; parity compares a sha256 of the plaintext, so no secret reaches the report.
  `check_at_startup` runs after the cutover. `python -m connectors.rotate_key` rotates in one transaction. The
  connection test returns 422 "secret can't be decrypted with the current key" when no key can read it. Saving a
  connection and testing it now write their audit rows in the same transaction.
- `test_phase3` C section (32 checks): the old graph with a plaintext secret, links, a custom field, an import run and an
  integration run; a clean cutover with edge counts; the API views; encryption, the hint and keep-on-blank; rotation;
  wrong-key, production-without-key and malformed-key refusals; an undecryptable secret; SQL paging and search; the HR
  Portal sync replacing `synced_to`; custom fields; imports; drift; rollback (plaintext secret and every edge back);
  a second cutover.
- Throwaway stack: `main` → C (five batches in one start) with a real key; an integration run; rotation; refusals;
  a full rollback to `main` (connection test still passes on decrypted plaintext); re-upgrade.

### Batch C deep test (an independent review plus my own probes; each finding reproduced, fixed and covered)
- **Plaintext secrets stayed in the graph after cutover.** `LabelSpec.after_cutover` now scrubs them (`ConnectorInstance`
  `secret` removed) once the batch is clean, and the drift baseline is taken after the scrub. Rollback writes them back.
- **`KeyConfigError` was a `SystemExit`.** Raised inside a request (a save in production without a key, or a malformed
  key), it could stop the server. It is now a `RuntimeError`: the routes answer 503 or 422, and `check_at_startup`,
  which now also validates the key before any secret is stored, turns it into an exit.
- **Engine, from PR 0:** `label_report` merged `**par` after `ok`, so edge-count and unmapped-key mismatches never
  blocked a cutover or rollback. The computed `ok` now wins. Every batch's report re-checks clean.
- **A re-cutover after a rollback kept stale links.** `_backfill_links` now replaces the table from the graph.
- **Bad graph data** (malformed JSON, a missing id, a dangling reference) stopped startup with a raw traceback, and
  malformed `values` silently became `{}`. Malformed JSON now refuses and names the node, and bootstrap reports any
  mapping error as "nothing was switched".
- **Search across fields** ("Ananya K") broke when search moved to SQL. Every word must now match some field value.
- **Rollback without the key half-switched the batch.** Rollback now builds every payload before writing anything.
- `rotate_key` refuses without `SECRETS_KEY`.
- **Checks and dev:** 6 new checks (`test_phase3` now 124). On dev: `rollback C`, then a restart; the graph holds no
  secret, and the hints and connection tests are unchanged.

## As built: batch B (activity, guardrail hits, eval runs)
- Migration `014_activity.sql`: `events`, `guardrail_events`, `eval_runs`, `eval_results`. Columns are the old graph
  properties. `ts`/`finished` stay epoch seconds (`double precision`) and `ms` stays an integer, so every API keeps its
  types (ponytail: timestamptz is a later cleanup).
- `events.py` (`record`, `for_employee`, `recent(limit, exclude_kind)`) is the only reader and writer. `graph.event()`
  keeps its signature and calls `events.record`, so its 27 callers didn't change. `recent` LEFT JOINs `employees` for
  the name and the manager; `/api/events` keeps its limit-then-`_visible` manager scoping in `main`.
- `guardrails.record` inserts a row; `by_rule` / `recent` serve the quality page. `evals.py` writes runs and results in
  SQL (`_set`, `_insert_result`); `runs()` / `results()` drop NULLs so the API shape matches the graph's (a metric the
  judge couldn't score is an absent key).
- `quality_routes.overview`, the employee timeline and the HR co-pilot's recent activity read the tables.
- `test_phase3` B section: the old release's nodes (including a run without `finished` and a result without metrics),
  a cutover, the quality overview equal to what the old graph reader returned, the event choke point, the joins and
  filters, guardrail counts, a failed eval run, the result shape, drift, rollback and a second cutover.
- On a throwaway stack, `main` was upgraded straight to B (four batches in one start). A full eval run (20/20) then
  succeeded on the new storage, and a full rollback to `main` showed the run, the guardrail hits and the activity.

## The approved plan

### Context
Phases 0 to 2 and the hardening work are merged, and CI is green on `main`. Employees, items, rule sets and documents live in Postgres. The graph still holds about 20 labels as authoritative records.

Phase 3 makes Postgres the only store of record. The graph becomes a rebuild-only read model:
- the projection: `Employee`, `Item`, `Document`, `Owner` and their edges
- the policy retrieval index: `PolicyChunk`

This also removes the last source-of-truth ambiguity.

The review approved the direction: Postgres authoritative, graph as projection plus index, rebuild as the recovery path, parity gates, an API snapshot, encrypted secrets. It called v1 "phase-ready, not ship-ready" and asked for these gates:
- a migration freeze and a rollback drill
- secret-key fail-fast and a rotation test
- a wipe-and-restart rebuild test
- runtime enforcement of the read model
- an explicit API contract comparison
- smaller batches, one migration report, and one cutover checklist

v2 builds all of them in.

### The fixed label inventory (the final gate checks this list)

| Label | Live rows | Target | Owner module (only writer after cutover) | Batch |
|---|---|---|---|---|
| Owner | 8 | `owners` table; graph `Owner` becomes a projection | `owners.py` | A1 |
| EmailTemplate | 4 | `email_templates` | `templates.py` | A1 |
| Config | 4 | removed: the rule-set version config already owns it | `policy/store.py` | A3 (moved from A1, see "As built: A1") |
| Policy | 7 | `policies` (uploads are authoritative) | `policies.py` | A2 |
| PolicyChunk | 31 | stays in the graph: derived index, rebuilt from `policies` | `chat.index_policy` | A2 |
| Rule (legacy) | 18 | removed: `import_requirements` writes rule-set versions | `ingest.py` → `policy/store.py` | A3 |
| Event | 306 | `events` | `graph.event()` (choke point) | B |
| GuardrailEvent | 1 | `guardrail_events` | `guardrails.record` | B |
| EvalRun / EvalResult | 2 / 25 | `eval_runs` / `eval_results` | `evals.py` | B |
| Connector | 7 | `connectors` (catalog) | `connectors/store.py` | C |
| ConnectorInstance | 7 | `connector_instances` (+ `secret_enc`) | `connectors/store.py` | C |
| Record | 20 | `connector_records` | `connectors/base.py` → `connectors/store.py` | C |
| SYNCED_TO / FROM_CANDIDATE edges | 3 / 3 | `employee_record_links` | `connectors/store.py` | C |
| CustomField / ImportRun | 0 / 1 | `custom_fields` / `import_runs` | `connectors/hr_portal.py`, `candidates.py` | C |
| IntegrationRun (dead) | 3 | archived to `legacy_sync_runs` as JSON, code deleted (no callers) | none | C |
| Integration / IntegrationCandidate | 3 / 0 | `integrations` / `integration_candidates` | `integrations/store.py` | D |
| WorkflowRun / IntegrationLogRecord | 0 / 0 | `workflow_runs` / `integration_log_records` | `integrations/engine.py` | D |
| OutboundMessage / AgentMessage | 0 / 34 | `outbound_messages` / `agent_messages` | `integrations/delivery.py` / `service.py` | D |
| AuditLog (legacy) | 34 | removed: already imported into `audit_log` (Phase 0) | none | E |

The known bypasses are closed in their batch:
- `integrations/service.py:102`
- `integrations/engine.py:196/314/320/391`
- `integration_routes.py:393/403/408`
- `connector_routes.py:34/53/66/101/131/142/159/161/180/210`
- `hr_portal.py:103/123`
- `main.py:434/441/450/521/526/533`
- `chat.py:197/200`

**Guiding principle:** the graph is not the source of truth any more. It is a recoverable projection, and rebuilding it is the restore path.

### Delivery: one PR per batch, each with its own gate
**PRs:** 0 (tooling) → A1 → A2 → A3 → B → C → D → E (read model and cleanup). Each batch PR merges only when its gate passes. Every item below is **required, not optional**:
1. **The batch's section of the migration report is attached to the PR:** parity per label, plus the extras listed below.
   - **Required artifact:** graph and Postgres counts and checksums from **before and after** the dev cutover, per label and per edge type. They're pasted into the PR body and saved in `docs/runbooks/phase-3-cutover.md`.
   - **The freeze is a requirement, not a step.** The PR records that the cutover ran with no other instance live (the single-writer check passed) and before any worker started.
   - **Live rollback drill on the dev stack, every batch:** cut over → write through the app → `graph_migration rollback <batch>` → parity → cut over again. Its output is attached, alongside the automated round-trip test.
2. **A smoke test of the affected features passes on a fresh build.** The feature list per batch is in the cutover checklist.
3. **The rollback drill for that batch passes** (see "Rollback").
4. **The API contract check passes,** and a failing diff is printed key by key.
5. **CI is green,** then the PR is merged, then the dev cutover is run and its report recorded in `docs/runbooks/phase-3-cutover.md`.

### PR 0: Tooling (no storage moves)
- **API contract snapshot:**
  - `scripts/api_contract.py --capture | --check` records the recursive key structure (with types) of about 15 endpoints:
    - connectors (list, detail, records, connection)
    - integrations (list, detail, messages, logs)
    - approvals, templates, quality, events
    - employee detail and links, rules, policies
  - Output is `backend/tests_snapshots/api_contract.json`.
  - `--check` prints a diff (missing, added and retyped keys per endpoint) and exits 1 on any change. It runs in `test_phase3` and in CI.
- **`backend/graph_migration.py`:** one engine for every batch. Each label has a spec: the graph read query, the target table, a row mapper, a canonical form, and its edges. It provides:
  - `backfill(labels)`, idempotent (`ON CONFLICT DO UPDATE`).
  - `report(labels)`, which writes **one** `migration_report.json` (sections per batch) holding, per label:
    - graph and Postgres counts, both checksums
    - first mismatching id, missing/extra ids (up to 20)
    - a **schema diff:** graph property keys seen versus mapped columns; unmapped keys must be empty or listed as intentionally dropped
    - **edge count** graph versus Postgres for the label's relationships
    - a **read-path-switched** section: the guard scan for remaining `MATCH (:Label` in code, which must be 0
    - the `cutover_at` timestamp and the `graph_checksum_at_cutover`
  - `rollback(batch)`, described below.
- **The cutover runs at startup** (`bootstrap.migrate_graph_batch`), like Phases 1 and 2, with an explicit freeze:
  - It runs **before** the job worker, Gmail poller and scheduler start, and before the API serves anything. In this process nothing writes during cutover.
  - **Single-writer check:** it takes a Postgres advisory lock and refuses to cut over if any other worker heartbeat is newer than 60 seconds, which means another replica is running. The message: "stop other instances, then restart".
  - It switches the bridge only when parity passes. Otherwise the app refuses to start, and the graph stays the source with nothing written to Postgres reads.
- **Diff since cutover:** the hourly `graph.verify` job (PR E), and `GET /api/admin/migration` from PR 0 onwards, recompute each migrated label's graph-side checksum and compare it with `graph_checksum_at_cutover`. Any change means something still writes the retired label: an `ops` work item opens and `/ready` reports `graph_migration: drift`.
- **`/api/admin/migration`** (admin, auditor) and a System page with a Migration card: per label, source, parity, counts, cutover time and drift-since-cutover.

### Batches
- **A1, owners/templates (migration 011; Config moved to A3):**
  - Tables: `owners`, `email_templates`.
  - New modules `owners.py` (`all`, `get`, `by_category`, `upsert`, `manager_for`) and `templates.py`.
  - The 13 Owner sites and the template sites switch over.
  - `checklist.config()`/`policy.store.ensure_active` drop the graph `Config` fallback (the rule-set config already owns it).
  - Graph `Owner` nodes become projection (`records.project` MERGEs the owners it links to from Postgres).
  - **Feature smoke:** dashboard and employee detail (manager shown, owners), template list/edit/preview email, manager scoping, rules page.
- **A2, policy retrieval (migration 012):**
  - Table: `policies`. `chat.index_policy` stores the text there and builds `PolicyChunk` from it, and the full rebuild re-indexes from it.
  - `main.py:441/450` read the table.
  - **Feature smoke:** an employee assistant question returns cited policy sections; the portal ack shows the policy text.
- **A3, the import flow:**
  - `ingest.import_requirements` creates v1 in Postgres on a fresh install, and opens a governed draft when an active version exists.
  - It stops writing `Rule`/`Config` nodes; the legacy graph-rule reader in `ensure_active` is removed.
  - `Config` retires here too: `checklist.config()` drops its graph fallback, and `ingest.seed` stops using
    `Config {key:'doc_types'}` to detect a first run.
  - **Feature smoke:** an upload on `/import` → a draft appears → preview.
- **B, activity (migration 013):**
  - Tables: `events` (no FK; indexed on (employee_id, ts) and ts), `guardrail_events`, `eval_runs`, `eval_results`.
  - `graph.event()` becomes `INSERT`, so its 28 callers don't change. The 3 reads become SQL joins, with manager scoping kept.
  - **Feature smoke:** the activity page, the employee timeline, quality guardrails, starting an eval run.
- **C, connectors (migration 014):**
  - Tables: `connectors`, `connector_instances` (+ `secret_enc`, `secret_key_id`), `connector_records`, `employee_record_links`, `custom_fields`, `import_runs`, `legacy_sync_runs`.
  - `connectors/store.py` owns them. The `Connector` interface is unchanged, and `list_records` paginates in SQL.
  - Secrets are covered below. `connectors/sync.run`/`runs` are deleted.
  - **Feature smoke:**
    - connectors list, records page, CSV import
    - the connection form shows the hint only; test-connection works
    - onboarding a candidate creates the `from_candidate` link
    - the HR Portal sync creates `synced_to`
- **D, integrations (migration 016; see "Plan: batch D" above):**
  - Tables: `integrations` (unique on lower(name)), `integration_candidates` (UNIQUE (integration_id, record_id)), `workflow_runs`, `integration_log_records` (masked before the insert), `outbound_messages`, `agent_messages`.
  - Store signatures and return dicts are unchanged: epoch floats; `None`/`""` exactly as before, checked by the contract.
  - **Feature smoke:**
    - admin creates an integration with the setup chat, approves the mapping, and runs it
    - the logs show the run with exclusions masked
    - a candidate reaches `documents_requested` with a simulated message
    - the approval decision works
- **E, the read model and cleanup:**
  - `rebuild_graph`, `graph_status`, `graph.verify`, admin rebuild, runtime enforcement, removing the legacy `AuditLog`, and the one-time cleanup rebuild.
  - Before the cleanup: `BGSAVE`, then a full rebuild, then a label scan that must show only the 5 read-model labels.

### Runtime enforcement of the read model (not just a convention)
1. **Static:** guard rule 8 refuses CREATE/MERGE/SET/DELETE of any label outside {Employee, Item, Document, Owner, PolicyChunk} anywhere in `backend/`. Projection labels may only be written in `records.py`, `documents/store.py` and the `graph.py` rebuild, and `PolicyChunk` only in `chat.py`. No `MATCH (:<retired label>` may remain anywhere, including in `scripts/` and tests (test setup moves to SQL helpers).
2. **Runtime:** `graph.q()` refuses (raises `ReadModelViolation`) any write query that names a label outside the read model. It's a regex on CREATE/MERGE/SET targets, active from PR E; before that, it's active per migrated label as each batch lands.
3. **Test:** `test_phase3` runs a write of each retired label through `q()` and expects the error, and the guard selftest has a bad snippet per rule.
4. **Startup:** after the cleanup, bootstrap scans the graph's labels. If any are unexpected, `/ready` shows `graph_projection: drift (unexpected labels: …)` and `graph.verify` rebuilds.

### Rebuild: proven recovery from a missing graph
- **`rebuild_graph(eid=None)`:**
  - The full rebuild sets `graph_state = rebuilding` (in Postgres), wipes the graph, projects every employee (with documents and owners), re-indexes `PolicyChunk` from `policies`, then sets `ready`.
  - **While rebuilding, no partial projection is served.** The graph-backed readers return 503 `{"detail": "The relationship view is rebuilding; try again in a moment"}`. Those readers are `employee_graph` and policy retrieval in `chat.retrieve`; the assistant answers "policy search is rebuilding" rather than answering without sources. Records and every other page are unaffected.
  - Per-employee projection stays in place and is small; it's the existing `records.project`.
- **Bootstrap self-heals:** at startup, if the graph is empty or the label scan or status shows drift, it enqueues a full rebuild. The app serves records immediately, and graph-backed views return the "rebuilding" 503 until it's done.
- **Wipe-and-restart drill:**
  - Automated in `test_phase3`: wipe → bootstrap → the rebuild job → `graph_status().ok`, equal per-label counts and a projection checksum equal to before the wipe; `chat.retrieve("notice period")` returns chunks; the graph view of an employee matches.
  - Also run by hand on the dev stack (stop, `GRAPH.DELETE hr`, start), recorded in the cutover runbook.
- **Drift repair:** `graph.verify` runs hourly. It rebuilds the affected employees, and opens an `ops` item only if drift persists. A test forces the rebuild to fail and expects exactly one item.

### Secrets at rest: key loss and rotation
- **Storage:** `secret_enc` (Fernet) plus `secret_key_id` (the first 8 hex characters of sha256 of the key). The key comes from `SECRETS_KEY`, with an optional `SECRETS_KEY_PREVIOUS` for the rotation window, read with `MultiFernet`.
- **Fail fast:**
  - If any stored secret exists and `SECRETS_KEY` is unset in production, bootstrap exits with "connector secrets are stored; set SECRETS_KEY".
  - If no configured key matches `secret_key_id`, bootstrap exits naming both fingerprints.
  - Dev derives a key from a fixed dev salt, with a logged warning, and never in production.
- **Behaviour without a usable key:** a connector call that needs the secret raises a clear `SecretUnavailable`. The connection test reports "secret can't be decrypted with the current key"; nothing crashes, and records stay readable.
- **The rotation rule:**
  1. Deploy with `SECRETS_KEY=new` and `SECRETS_KEY_PREVIOUS=old`.
  2. Run `python -m connectors.rotate_key`, which re-encrypts every secret with the new key in one transaction and updates `secret_key_id`.
  3. Remove `SECRETS_KEY_PREVIOUS`.

  After step 3, the old key can't decrypt anything, and the test asserts that. `_connection()` only ever returns `secret_set`/`secret_hint`.
- **Tests:** plaintext never in `secret_enc`; a missing key → fail-fast; a wrong key → fail-fast naming the fingerprints; rotation → readable with the new key, unreadable with the old; a connector with an undecryptable secret gives a clear error.
- A new ADR 0013 covers secrets at rest.

### Rollback (drilled per batch)
- **Before a batch's PR merges,** the batch's code is simply not deployed.
- **After its cutover:**
  1. `python -m graph_migration rollback <batch>` writes the Postgres rows (including any written since cutover) back to graph nodes and edges, using the same mapping in reverse.
  2. It runs parity graph↔Postgres.
  3. It resets the batch's `bridge_state` rows to `graph`.
  4. Then the previous release is deployed.
- **`test_phase3.test_rollback_round_trip`,** per batch: cut over → write new rows through the app → roll back → parity ok and the new rows are present in the graph → cut over again → parity ok. That proves no data is lost either way.
- **After PR E's cleanup,** the rollback point is the `BGSAVE` snapshot plus the rollback tool. The runbook gives the exact commands.

### Tests (essential list)
`test_phase3.py` on `azentio_test_p3` plus graph `test_phase3`:
- parity per label, including the schema diff and edge counts; a Postgres-only edit fails exactly that label
- the cutover refuses on a mismatch and while another worker heartbeat is live
- the cutover is idempotent
- diff-since-cutover catches a stray graph write
- rollback round-trip per batch
- read-model enforcement: the guard, the runtime `q()` error, and the startup label scan
- wipe-and-restart rebuild, with policy retrieval and no partial serving (a 503 during rebuild)
- per-employee rebuild, and drift auto-repair with one `ops` item on persistent failure
- the event choke point
- `_enroll` idempotent under two threads
- log exclusions masked at rest
- secrets: missing key, wrong key, rotation, undecryptable
- both `import_requirements` paths
- the API contract with no diff

`test_core` moves its raw Cypher setup to SQL helpers, with assertions unchanged. CI adds `test_phase3`, `api_contract --check` and `SECRETS_KEY`.

### Docs
- **`docs/runbooks/phase-3-cutover.md`:** the one cutover checklist. Per batch: pre-checks, freeze, cutover, report review, feature smoke, rollback commands, and sign-off, with the dev results filled in.
- The phase-3 doc rewritten as built.
- LLD: storage, data model, bridge, API, read model.
- ADR 0006 updated, and the new ADR 0013 (secrets).
- `.env.example`: `SECRETS_KEY`, `SECRETS_KEY_PREVIOUS`.
- AGENTS.md / CLAUDE.md: the phase, rule 8, runtime enforcement, `test_phase3`.

### Go/no-go for the phase
- Every label in the inventory is `postgres` (or removed), and its parity, schema diff and edge counts are clean in `migration_report.json`.
- Zero retired-label reads or writes in code (guard), at runtime (`q()` refuses), and in the graph (label scan).
- The wipe-and-restart drill passes automatically and by hand.
- Rollback round-trips pass for every batch.
- Secrets: fail-fast on a missing or wrong key, and the rotation test passes.
- The API contract has no diff, and smoke passes for all roles and features.
- CI is green on every batch PR and on `main`.

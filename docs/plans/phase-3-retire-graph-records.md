# Phase 3: Retire graph record storage

- **Status:** in progress. PR 0 (tooling) is done; batches A1 → E follow, one PR each.
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
- `test_phase3.py` proves the engine on a synthetic label (`P3Probe`): schema diff, read-path scan, freeze (a live
  heartbeat, a stopped one, a held lock), refusal when not clean, cutover, idempotency, drift, the report, and a
  rollback round trip that includes rows written after cutover.

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
| Config | 4 | removed: the rule-set version config already owns it | `policy/store.py` | A1 |
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
- **A1, config/owners/templates (migration 011):**
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
- **D, integrations (migration 015):**
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

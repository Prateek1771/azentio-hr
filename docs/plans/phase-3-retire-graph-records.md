# Phase 3: Retire graph-backed record storage

- **Status:** planned
- **Depends on:**
  - Phase 0: `shared/db.py`, `shared/bridge.py`, `audit/service.py`
  - Phase 1: Employee, Item and Rule are in Postgres
  - Phase 2: Document is in Postgres
- **Branch:** `phase-3-retire-graph-records`

## Goal
After Phases 1–2, the HR core lives in Postgres, but about 20 other graph labels are still written as authoritative records. `grep -rhoE "(CREATE|MERGE) \(\w*:\w+"` over `backend/` lists them:
- **Connectors:** `Connector`, `ConnectorInstance`, `Record`, `CustomField`, `ImportRun`, `IntegrationRun`
- **Integrations:** `Integration`, `IntegrationCandidate`, `WorkflowRun`, `IntegrationLogRecord`, `OutboundMessage`, `AgentMessage`
- **Configuration:** `Config`, `EmailTemplate`, `Owner`
- **Activity and quality:** `Event`, `EvalRun`, `EvalResult`, `GuardrailEvent`
- **Retrieval:** `Policy`, `PolicyChunk`, `Chunk`

After this phase:
1. **Every authoritative record lives in Postgres.** The graph holds only:
   - **the projection:** `Employee`, `Item`, `Document` and `Owner` nodes and their edges, rebuilt from Postgres
   - **the retrieval index:** `Policy`/`PolicyChunk`/`Chunk` with the vector index (still built from the approved policy files in `seed/policies/`)
2. **The graph is a deliberate, rebuild-only read model.**
   - `rebuild_graph()` rebuilds everything from Postgres and the policy files.
   - `rebuild_graph(eid)` rebuilds one employee's subgraph, called in the same request as a write.
   - There's no background sync, so drift can't build up unnoticed. Wiping the graph volume and running `rebuild_graph()` gives an identical graph, and a test proves it.
3. **Graph writes for each label stop only after every read path for that label has switched.** Each label's PR includes a grep check showing zero remaining graph reads.
4. **A one-off migration with a parity gate** moves the existing data. Row counts and checksums must match per label before the bridge switches.

## Scope
- Postgres tables for the labels listed above, except the retrieval index.
- Moving `integrations/store.py`, `integrations/engine.py`, `integrations/delivery.py`, `integrations/service.py`, `connectors/*.py`, `connector_routes.py`, `integration_routes.py`, `evals.py`, `guardrails.py` and the `Event`/`Config`/`EmailTemplate`/`Owner` call sites onto `shared/db.py`.
- Encrypting connector secrets at rest (`ConnectorInstance.secret` is plain text in the graph today).
- `rebuild_graph()` covering the whole projection, a `GET /api/admin/graph/status` drift check, and an admin "Rebuild graph" action.
- `backend/migrate_graph.py`, the one-off migration, with a parity report.

## Non-goals
- **Changing integration behaviour.** The setup steps, stages, mapping and log exclusions all stay the same. This is a storage move only. Any behaviour change found along the way is logged as an issue, not fixed here.
- **Moving retrieval to pgvector.** `PolicyChunk` stays in FalkorDB's vector index. It's derived data rebuilt from files, not a record. Revisit only if the graph would otherwise be needed only for retrieval.
- **Moving the Gmail poller and integration scheduler onto jobs.** That's Phase 4. This phase keeps `_scheduler()` and `gmail.poll_forever()` as they are and only changes where they read and write.
- **Deleting graph data.** The old nodes stay until the phase exit criteria pass. Clean-up is `rebuild_graph()` with a full wipe, run as the last step.

## Design

### Order of work
Labels move in an order where every reader of a label has moved before its writers stop:

| Batch | Labels | Readers to switch (non-exhaustive, from grep) |
|---|---|---|
| A: configuration | `Config`, `EmailTemplate`, `Owner` | `checklist.config()`/`category_owners()` (now reading the policy snapshot from Phase 1, but `Config` still feeds `main.rules()`), `gmail.render()`, `main.templates()`/`save_template()`, `ingest.py`, `integrations/service.py:61` |
| B: activity | `Event`, `GuardrailEvent`, `EvalRun`, `EvalResult` | `graph.event()` (called from about 40 places, one function), `main.events()`, `employee_detail()` activity, `guardrails.py`, `evals.py`, `quality_routes.py` |
| C: connectors | `Connector`, `ConnectorInstance`, `Record`, `CustomField`, `ImportRun`, `IntegrationRun` | `connectors/base.py`, `registry.instance()`/`get()`, `candidates.py`, `hr_portal.py` (`push_employee`, `instance_id_for`), `apps.py`, `sync.run()`/`runs()`, `seed.py`, `connector_routes.py` |
| D: integrations | `Integration`, `IntegrationCandidate`, `WorkflowRun`, `IntegrationLogRecord`, `OutboundMessage`, `AgentMessage` | `integrations/store.py` (`get`, `all_integrations`, `_write`, `create`, `update`, `items`, `item`, `item_for_joiner`, `set_item`), `engine.py` (`_enroll`, `run_row`, run creation at about lines 318/343/389/416), `delivery.record()`/`messages()`, `service.py:28/33`, `integration_routes.py` (approvals, logs) |

Within a batch, the 4 bridge steps run per label. Batches A and B are independent of each other. C must come before D because integrations read connector instances.

### `graph.event()`: one choke point
`event()` is the most-called write in the codebase and has a single implementation (`graph.py:40`), so it switches in one line: `INSERT INTO events`. Dual-write is skipped for `Event` because it's append-only history:
1. Backfill.
2. Point `event()` at Postgres and the readers at Postgres in the **same** PR.
3. Run parity on everything up to the backfill cut-off timestamp.

### Data shape: keep JSON where the code already uses JSON
`integrations/store.py` deliberately keeps settings in one JSON `config` property ("a FalkorDB property can't hold null"), so Postgres uses a `jsonb config` column with the same contents. The store's `_load()`/`_write()` signatures stay the same, so `service.py`, `chat.py`, `agent.py` and `engine.py` see the same dicts. The same applies to `Record.values` and `IntegrationCandidate.document_results`.

### Secrets
- `connector_instances.secret_enc bytea`, encrypted with `cryptography.fernet.Fernet`. `cryptography` is already in `uv.lock` as a transitive dependency; it's declared explicitly in `pyproject.toml`. The key comes from `SECRETS_KEY`.
- Bootstrap's config check (Phase 0) adds `SECRETS_KEY`, which is required when any connector has a saved secret.
- `connector_routes._connection()` keeps returning only `secret_set` and `secret_hint`, and the plaintext never reaches the API.
- Rotating the key: `uv run python -m connectors.rotate_key --old … --new …` re-encrypts the stored secrets in one transaction.

### Rebuild
`graph.rebuild_graph(eid: str | None = None)`:
- **For one employee (`eid` given):** run `MATCH (e:Employee {id:$e}) OPTIONAL MATCH (e)-[*1..2]-(n) WHERE n:Item OR n:Document DETACH DELETE e, n`, then recreate the employee's nodes and edges from Postgres: `HAS_ITEM`, `DEPENDS_ON`, `OWNED_BY→Owner`, `HAS_DOC`, `SUBMITTED`, `MISMATCH`, and `REPORTS_TO` (to an `Owner` or `Employee` by `manager_email`, the same logic as `ingest.py:97`). It runs in one Redis `MULTI` so readers never see half an employee.
- **For everything (`eid=None`):**
  1. Build into a scratch graph `hr_rebuild`.
  2. Ingest the policy files into it with the existing `ingest` policy loader.
  3. Run `ensure_indexes()` on it.
  4. Atomically swap it in: `GRAPH.COPY hr_rebuild hr` after `GRAPH.DELETE hr`, or point `FALKORDB_GRAPH` at the new name, whichever the FalkorDB version supports. Check this when implementing; the fallback is building in place during a maintenance window. The live graph is never half-built.
- **`graph_status()`:** compares node and edge counts per label, plus a checksum of the projection, against what Postgres says they should be. `GET /api/admin/graph/status` returns `{ok, labels: [{label, expected, actual}], checked_at}`. `/ready` (Phase 0) includes `graph: ok|drift|down`. Drift means `ready` reports **degraded**, not down: the graph is a read model, and the app's records are still correct.
- **Reaction to drift:** a `graph.verify` job runs hourly (Phase 0 jobs, `run_at`). If it finds drift it rebuilds the affected employees automatically, and only opens an `ops` `work_queue` item if drift remains after the rebuild. This follows the operating principle: routine repair is automatic, and only a persistent fault reaches a human.

## Schema
Migrations continue after Phase 2's `008`. Every table keeps the graph ids as `text PRIMARY KEY`, so links in old audit rows and URLs keep working.

**`backend/migrations/009_config_activity.sql`**
```sql
CREATE TABLE config (key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE email_templates (
  id text PRIMARY KEY, name text NOT NULL, subject text NOT NULL, body text NOT NULL,
  system boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(), updated_by bigint REFERENCES users(id));

CREATE TABLE owners (
  email text PRIMARY KEY, name text NOT NULL, type text NOT NULL,   -- 'owner:IT', 'manager', …
  extra jsonb NOT NULL DEFAULT '{}');

CREATE TABLE events (
  id text PRIMARY KEY, employee_id text REFERENCES employees(id), kind text NOT NULL, message text NOT NULL,
  ts timestamptz NOT NULL DEFAULT now());
CREATE INDEX events_employee_ts ON events (employee_id, ts DESC);
CREATE INDEX events_ts ON events (ts DESC);

CREATE TABLE guardrail_events (id text PRIMARY KEY, kind text NOT NULL, assistant text NOT NULL, detail jsonb NOT NULL DEFAULT '{}',
  ts timestamptz NOT NULL DEFAULT now());
CREATE TABLE eval_runs (id text PRIMARY KEY, status text NOT NULL, progress int NOT NULL DEFAULT 0, total int NOT NULL DEFAULT 0,
  scores jsonb NOT NULL DEFAULT '{}', error text, started timestamptz NOT NULL DEFAULT now(), finished timestamptz);
CREATE TABLE eval_results (id text PRIMARY KEY, run_id text NOT NULL REFERENCES eval_runs(id), question text NOT NULL,
  answer text NOT NULL, scores jsonb NOT NULL DEFAULT '{}');
```

**`backend/migrations/010_connectors.sql`**
```sql
CREATE TABLE connectors (key text PRIMARY KEY, name text NOT NULL, role text NOT NULL CHECK (role IN ('source','target','app')),
  description text NOT NULL DEFAULT '', demo boolean NOT NULL DEFAULT true, extra jsonb NOT NULL DEFAULT '{}');

CREATE TABLE connector_instances (
  id text PRIMARY KEY, key text NOT NULL REFERENCES connectors(key), name text NOT NULL,
  connection jsonb NOT NULL DEFAULT '{}',          -- tenant, auth_type, username, configured_at, last_test
  secret_enc bytea,                                -- Fernet(SECRETS_KEY); never returned by the API
  created timestamptz NOT NULL DEFAULT now());

CREATE TABLE connector_records (
  id text PRIMARY KEY, instance_id text NOT NULL REFERENCES connector_instances(id), kind text NOT NULL,
  values jsonb NOT NULL, created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now());
CREATE INDEX connector_records_instance ON connector_records (instance_id, created DESC);
CREATE INDEX connector_records_email ON connector_records (instance_id, lower(values->>'email'));

CREATE TABLE employee_sync (employee_id text REFERENCES employees(id), record_id text REFERENCES connector_records(id),
  PRIMARY KEY (employee_id, record_id));        -- replaces (:Employee)-[:SYNCED_TO]->(:Record)

CREATE TABLE custom_fields (instance_id text REFERENCES connector_instances(id), key text, label text NOT NULL,
  type text NOT NULL, required boolean NOT NULL DEFAULT false, created timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (instance_id, key));

CREATE TABLE import_runs (id text PRIMARY KEY, instance_id text NOT NULL REFERENCES connector_instances(id), filename text NOT NULL,
  counts jsonb NOT NULL DEFAULT '{}', errors jsonb NOT NULL DEFAULT '[]', ts timestamptz NOT NULL DEFAULT now(),
  actor_id bigint REFERENCES users(id));

CREATE TABLE sync_runs (                        -- graph label IntegrationRun (connectors/sync.py)
  id text PRIMARY KEY, source_id text NOT NULL, target_id text NOT NULL, policy text NOT NULL,
  counts jsonb NOT NULL DEFAULT '{}', rejected jsonb NOT NULL DEFAULT '[]', ts timestamptz NOT NULL DEFAULT now());
```

**`backend/migrations/011_integrations.sql`**
```sql
CREATE TABLE integrations (
  id text PRIMARY KEY, name text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft','active','paused','archived')),
  config jsonb NOT NULL DEFAULT '{}',              -- same contents as the graph's JSON `config` (store.DEFAULT_CONFIG keys)
  next_run_at timestamptz, created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now());
CREATE INDEX integrations_due ON integrations (next_run_at) WHERE status = 'active';

CREATE TABLE integration_candidates (
  id text PRIMARY KEY, integration_id text NOT NULL REFERENCES integrations(id),
  record_id text NOT NULL REFERENCES connector_records(id), candidate_ref text NOT NULL DEFAULT '',
  name text NOT NULL DEFAULT '', email text NOT NULL DEFAULT '',
  stage text NOT NULL,                             -- store.STAGES
  joiner_id text REFERENCES employees(id),
  document_results jsonb NOT NULL DEFAULT '[]', document_attempts int NOT NULL DEFAULT 0,
  extra jsonb NOT NULL DEFAULT '{}',
  created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now(),
  UNIQUE (integration_id, record_id));             -- makes _enroll() idempotent at the DB level
CREATE INDEX integration_candidates_stage ON integration_candidates (integration_id, stage);

CREATE TABLE workflow_runs (
  id text PRIMARY KEY, integration_id text NOT NULL REFERENCES integrations(id), trigger text NOT NULL,
  status text NOT NULL, counts jsonb NOT NULL DEFAULT '{}', mapping_snapshot jsonb,
  started timestamptz NOT NULL DEFAULT now(), finished timestamptz, extra jsonb NOT NULL DEFAULT '{}');
CREATE INDEX workflow_runs_integration ON workflow_runs (integration_id, started DESC);

CREATE TABLE integration_log_records (
  id text PRIMARY KEY, run_id text NOT NULL REFERENCES workflow_runs(id), integration_id text NOT NULL,
  record jsonb NOT NULL, actions jsonb NOT NULL DEFAULT '[]', ts timestamptz NOT NULL DEFAULT now());

CREATE TABLE outbound_messages (
  id text PRIMARY KEY, integration_id text, item_id text, kind text NOT NULL, "to" text NOT NULL, subject text NOT NULL,
  content text NOT NULL, attempt int NOT NULL DEFAULT 1, delivered_by text NOT NULL, provider_ref text NOT NULL DEFAULT '',
  candidate text NOT NULL DEFAULT '', ts timestamptz NOT NULL DEFAULT now());
CREATE INDEX outbound_messages_integration ON outbound_messages (integration_id, ts DESC);

CREATE TABLE agent_messages (id text PRIMARY KEY, integration_id text NOT NULL REFERENCES integrations(id),
  role text NOT NULL, content jsonb NOT NULL, ts timestamptz NOT NULL DEFAULT now());
CREATE INDEX agent_messages_integration ON agent_messages (integration_id, ts);
```

`integration_log_records.record` is stored **already masked**: `run_log.mask_record()` runs before the insert, as it does today, so log exclusions keep their guarantee that excluded values never land in storage.

## API
**No public endpoint changes shape.** The integration, connector, log, template and quality endpoints return the same JSON. Permissions (from `identity/perms.py`, unchanged from Phase 0):
- `/api/connectors*` and `/api/integrations*`: admin, plus `hr_ops` for running an integration and handling candidates
- logs: admin, hr_ops, auditor read

New endpoints:

| Method & path | Purpose | Roles |
|---|---|---|
| `GET /api/admin/graph/status` | Projection drift check | admin, auditor (read) |
| `POST /api/admin/graph/rebuild` | `{employee_id?}`. Enqueues `graph.rebuild` (key `graph.rebuild:<eid or all>:<minute>`) and returns the job id | admin |
| `GET /api/admin/migration/parity` | Per-label parity report from the last `migrate_graph.py` run, plus a live re-check | admin, auditor |

## Screens
- **Admin → System** (`frontend/app/(hr)/system/page.tsx`, admin and auditor; Phase 4 adds jobs and the dead-letter queue here):
  - A **Graph read model** card: status badge (In sync / Drift / Down), the time of the last check, and a table of labels showing expected versus actual counts.
  - A **Rebuild now** button (admin only) with confirmation and job progress.
  - States: loading skeleton; "Graph unreachable, records are unaffected; relationship views and the assistant's policy search are unavailable" when it's down.
- **A Migration card**, shown until Phase 3 is done: per-label rows with graph count, Postgres count, checksum match ✓/✕, `SOURCE` (graph/postgres), and whether graph writes have stopped. It's read-only, so the auditor can watch the cut-over.
- **No changes to the Connectors, Integrations, Logs, Templates or Quality screens.** The smoke test is the proof that nothing changed.

## File-by-file changes

| File | Change |
|---|---|
| `backend/graph.py` | `event()` → `INSERT INTO events` (batch B). `q`/`q1` stay for the projection and retrieval. Adds `rebuild_graph(eid=None)`, `graph_status()`, and `@jobs.handler("graph.rebuild")`/`("graph.verify")`. `ensure_indexes()` is kept for `Employee`, `Item`, `Document`, `Owner` and `PolicyChunk`; the `Rule`/`Event` indexes are removed |
| `backend/checklist.py` | `config()` reads `config` through `shared.db` (batch A); `category_owners()` reads `owners` |
| `backend/gmail.py` | `render()` reads `email_templates`. `poll()` reads employee addresses through the bridge (already done in Phase 1) |
| `backend/main.py` | `templates()`/`save_template()` → `email_templates`, recording `updated_by`. `events()` → the `events` table. `rules()` drops its `MATCH (o:Owner)` in favour of `owners`. `employee_graph()` still reads the graph (projection) |
| `backend/ingest.py` | Seeding writes `config`, `owners` and `email_templates` to Postgres. The policy-file loader still writes `Policy`/`PolicyChunk`/`Chunk` to the graph, and `rebuild_graph()` calls it |
| `backend/connectors/base.py` | Record storage (line ~69, `CREATE (r:Record …)-[:IN]->(i)`) → `connector_records`. The `Connector` interface (`list_records`, `get_record`, `upsert_record`, `schema`) is unchanged |
| `backend/connectors/registry.py` | `instance()`/`get()` read `connector_instances` |
| `backend/connectors/candidates.py` | `ImportRun` creation (line ~76) → `import_runs`, with `actor_id` |
| `backend/connectors/hr_portal.py` | `CustomField` (line ~77) → `custom_fields`. `push_employee()` (`SYNCED_TO` at line ~123) → `employee_sync`. `instance_id_for()` → SQL |
| `backend/connectors/apps.py`, `seed.py`, `sync.py` | Storage → SQL. `sync.run()`/`runs()` use `sync_runs` |
| `backend/connector_routes.py` | `_connection()`/`put_connection()` read and write `connection` + `secret_enc` (Fernet). Other handlers keep their bodies and switch their storage helpers |
| `backend/integrations/store.py` | `_load`/`_write`/`get`/`all_integrations`/`create`/`update`/`items`/`item`/`item_for_joiner`/`set_item` → SQL on `integrations` and `integration_candidates`, returning the same dicts. The module docstring is updated (the JSON-config reason now reads "kept for compatibility") |
| `backend/integrations/engine.py` | `_enroll()` uses `INSERT … ON CONFLICT (integration_id, record_id) DO NOTHING`. Run rows → `workflow_runs`. Log rows → `integration_log_records` (masked before the insert) |
| `backend/integrations/delivery.py` | `record()`/`messages()` → `outbound_messages` |
| `backend/integrations/service.py` | `AgentMessage` (lines ~28/33) → `agent_messages`. The `Owner` and `ConnectorInstance` reads (lines ~61/65) → SQL |
| `backend/integration_routes.py` | Logs (lines ~393/408) → SQL. `approvals()` is unchanged in shape |
| `backend/guardrails.py`, `evals.py`, `quality_routes.py` | `GuardrailEvent`/`EvalRun`/`EvalResult` → SQL |
| `backend/shared/bridge.py` | One `SOURCE` entry per label plus `parity()` checksums. After this phase every entry is `postgres`. The map then stays as a record of the migration. **Deleting the bridge is out of scope**; Phase 4 removes it once a release has gone out with everything on Postgres |
| `backend/migrate_graph.py` | new. The one-off migration (below) |
| `backend/connectors/rotate_key.py` | new. Re-encrypts connector secrets with a new key |
| `backend/admin_routes.py` | new. The graph status/rebuild and migration parity endpoints |
| `frontend/app/(hr)/system/page.tsx` | new. The System page (Graph + Migration cards) |
| `frontend/components/app-shell.tsx` | adds a "System" nav item (min role admin; auditor read) |

## Migration steps
Each label follows the standard 4 steps. **The bridge may switch a label only once `parity(label).ok`.**

1. **Dual-write.** For each label, every writer calls `bridge.write(label, …)`, which writes to Postgres and to the graph. Reads still go to the graph.
2. **Backfill with the one-off migration.** `uv run python migrate_graph.py --labels Config,EmailTemplate,… [--dry-run]`:
   - It's idempotent: `INSERT … ON CONFLICT (id) DO UPDATE`, ordered by dependency (connectors → instances → records → integrations → candidates → runs → log records → messages).
   - Graph-only properties that aren't mapped go into `extra`. Their key names are listed in the report so nothing is dropped silently.
   - Connector secrets are encrypted on the way in.
   - It writes `migration_report.json`: per label, the graph count, Postgres count, both checksums, the first mismatching id, and the unmapped keys.
   - An `audit.record(actor=system, action='migration.backfill', …)` row is written per label.
3. **Parity, then switch reads.**
   - The checksum is `sha256` over canonical JSON of the mapped columns, sorted by primary key. Timestamps are normalised to epoch seconds, since the graph stores floats; JSON strings in the graph are parsed so that `config`/`values`/`document_results` compare structurally.
   - When `ok`, flip `SOURCE[label] = "postgres"` in its own PR, with the parity output pasted in.
   - The same PR adds a check to `test_phase3.py` that greps `backend/` (excluding `graph.py`'s projection and `migrate_graph.py`) for any remaining `MATCH (x:<Label>` and requires zero hits. **No read path for the label may remain.**
4. **Stop graph writes** for the label: remove the graph half of `bridge.write`.

Clean-up after the last label:
- Run `rebuild_graph()` (full, into the scratch graph, then swap). This deletes every old record node and leaves only the projection and the retrieval index.
- `graph_status().ok` must be true.
- Take a FalkorDB `BGSAVE` snapshot first, and keep it for 30 days as a rollback point.

## Seed data
No new demo data. Bootstrap seeding (`connectors/seed.py:seed_connectors`/`seed_apps`, `ingest.seed`) writes to Postgres from now on. Fresh installs never create record nodes in the graph and only build the projection.

## Tests
`backend/test_phase3.py` (plain asserts, `azentio_test` plus the `test` graph):

| Check | Asserts |
|---|---|
| `test_no_graph_reads_left` | For every label in batches A–D, a grep of `backend/**/*.py` (excluding `graph.py`'s projection and rebuild code, `migrate_graph.py` and tests) finds no `MATCH (…:<Label>` and no `CREATE (…:<Label>` |
| `test_rebuild_from_empty_is_identical` | `graph_status()` on the live test graph → delete the graph → `rebuild_graph()` → `graph_status().ok`; node and edge counts per label and the projection checksum equal the values from before the wipe |
| `test_rebuild_one_employee` | Change one item's `depends` in Postgres → `rebuild_graph(eid)` → that employee's `DEPENDS_ON` edges match, and other employees' subgraphs are unchanged (their checksums are equal) |
| `test_drift_autorepairs` | Delete one `HAS_ITEM` edge directly in the graph → the `graph.verify` job detects drift, rebuilds that employee, and opens no work item. With the rebuild patched to fail → exactly one `ops` work item |
| `test_parity_per_label` | After `migrate_graph.py` on the seeded graph, every label in the report is `ok`. Changing one `connector_records.values` row → that label is `ok=False` with `first_mismatch_id` |
| `test_migration_idempotent` | Running `migrate_graph.py` twice gives the same row counts and checksums |
| `test_enroll_idempotent` | `engine._enroll()` twice for the same integration creates candidates once (unique constraint) |
| `test_secret_encrypted` | After `put_connection`, `secret_enc` doesn't contain the plaintext bytes; `_connection()` returns only `secret_set`/`secret_hint`; `rotate_key` keeps the secret readable with the new key and unreadable with the old |
| `test_log_exclusions_hold` | An integration excluding the `email` field → the stored `integration_log_records.record` has no email value (masked before the insert) |
| `test_api_shapes_unchanged` | The JSON keys from `/api/integrations`, `/api/integrations/{id}`, `/api/connectors`, `/api/approvals`, `/api/logs/runs` and `/api/templates` equal a snapshot taken before this phase (`tests/snapshots/phase3_api_keys.json`) |
| `test_event_choke_point` | `graph.event()` writes one `events` row and no graph node |
| Earlier phase tests | `test_core.py`, `test_phase0.py`, `test_phase1.py` and `test_phase2.py` all still pass |

**Smoke:**
- `admin@` creates an integration from the seeded Candidate Portal to the HR Portal, approves the mapping, and clicks Run now. The run shows in Logs and the candidate reaches `documents_requested` with a simulated message.
- System shows "In sync".

## Exit criteria
- `SOURCE` is `postgres` for every label; `test_no_graph_reads_left` passes.
- After the clean-up rebuild, the graph contains only projection labels (`Employee`, `Item`, `Document`, `Owner`) and retrieval labels (`Policy`, `PolicyChunk`, `Chunk`). `graph_status().ok` is true.
- Wiping the FalkorDB volume and restarting gives a working app. Bootstrap rebuilds the graph and no record is lost. This is checked manually once and covered by `test_rebuild_from_empty_is_identical`.
- Connector secrets are encrypted at rest, and `SECRETS_KEY` is part of the bootstrap config check.
- All phase tests and the smoke test pass. `migration_report.json` is attached to the phase PR. The LLD's storage section and ADR 0006 ("graph is a rebuild-only read model") match the code.

## Risks & rollback
| Risk | Mitigation |
|---|---|
| A missed read path still reads the graph after its writes stop | `test_no_graph_reads_left` greps for every label, and the switch PR must include it passing. Dual-write keeps the graph current until step 4 |
| JSON-in-graph properties compare unequal because of formatting | The parity check parses JSON strings and compares structurally; timestamps are normalised |
| `rebuild_graph()` on a large graph blocks readers | A full rebuild runs into a scratch graph and then swaps in; per-employee rebuilds are small and atomic (`MULTI`) |
| FalkorDB doesn't support the atomic swap | Fall back to rebuilding in place during a short maintenance window, with `/ready` reporting `graph: rebuilding` (degraded) |
| A lost `SECRETS_KEY` makes connector secrets unrecoverable | Bootstrap refuses to start without it once secrets exist; the key goes in the secret store; losing it only means re-entering the connector credentials, and no HR records are affected |
| `Record.values` gets large (imported candidate files) | It's `jsonb`; the records list is paginated (`connector_records` endpoint, page_size 25) as it is today, and the email index covers the duplicate lookup |
| **Rolling back the phase** | Per label before step 4: flip `SOURCE` back. After step 4: restore the FalkorDB snapshot taken before the clean-up rebuild, then flip `SOURCE` back. Postgres keeps every row either way, and nothing is deleted from Postgres by a rollback |

# Phase 3 cutover runbook

This is the single checklist for moving each graph label batch to Postgres. Follow it for every batch, in order:
A1 → A2 → A3 → B → C → D → E. Then fill in that batch's results section from the dev stack, and paste the same results
into the PR body.

The rule: **the graph is not the source of truth any more. It is a recoverable projection, and rebuilding it is the
restore path.**

## The checklist (every batch)

1. **Pre-checks**
   - CI is green on the batch PR. That covers `test_phase3`, the API contract and smoke.
   - Take a graph snapshot: `docker compose exec falkordb redis-cli BGSAVE`, then wait until `LASTSAVE` changes.
   - Save the "before" report: `cd backend && uv run python -m graph_migration report --out ../before-<batch>.json`.
2. **Freeze**
   - Stop **every** backend instance. The cutover refuses to run while another instance's heartbeat is still
     advancing, and holds an advisory lock so two starts can't both cut over.
   - Start one instance on the new release. The cutover runs in `bootstrap.migrate_graph_batches()`, before the job
     worker, the Gmail poller and the scheduler start, and before the API serves anything.
   - If the instance refuses to start, nothing was switched. Read the message: either another instance is live, the
     batch has a parity, schema or edge mismatch, or there's a remaining graph read path. Fix it, then restart.
3. **Report review**
   - `uv run python -m graph_migration report --out ../after-<batch>.json`.
   - For each label in the batch, check:
     - `source = postgres`
     - parity ok, with counts and checksums equal
     - `unmapped_keys` empty
     - edges equal
     - `read_paths_remaining` empty
     - `freeze.single_writer = true`
     - `drift_since_cutover = true`
   - The **System** page shows the same data.
4. **Feature smoke:** the batch's list is in the plan ([phase-3 doc](../plans/phase-3-retire-graph-records.md#batches)).
   Run `backend/.venv/Scripts/python scripts/smoke.py --base http://localhost:3000`, then click through the batch's
   features.
5. **Live rollback drill**
   1. Write through the app: create or edit at least one record of each label in the batch.
   2. Stop the backend. The rollback tool refuses while any instance's heartbeat is still advancing.
   3. Run `uv run python -m graph_migration rollback <batch>`. It writes the Postgres rows, including the new ones,
      back to the graph, checks parity, and switches the bridge back to `graph`.
   4. Confirm the new records are in the graph and parity is ok in the output.
   5. Start the backend again. It cuts over again, and the report shows parity ok with the new rows.
6. **Sign-off:** record the before and after reports, the freeze evidence and the drill output below.

## Rolling back for real

- **Before the batch's PR is deployed:** do nothing. The code isn't live.
- **After its cutover:**
  1. Stop every instance.
  2. Run `uv run python -m graph_migration rollback <batch>`.
  3. Deploy the previous release.
- **After PR E's cleanup:** restore the `BGSAVE` snapshot taken before the cleanup, then run the rollback tool for
  each batch you're reverting.

## Results per batch

| Batch | Labels | Cutover (dev) | Before → after (graph / pg, sha) | Freeze | Rollback drill | Sign-off |
|---|---|---|---|---|---|---|
| A1 | Owner (stays as projection), EmailTemplate | 2026-11-02 03:08 UTC | Owner 8/0 → 8/8, EmailTemplate 4/0 → 4/4; sha equal | single writer, 15 s watch | pass | dev |
| A2 | Policy (PolicyChunk stays as an index) | 2026-11-02 03:37 UTC | Policy 7/0 → 7/7; sha equal | single writer, 15 s watch | pass | dev |
| A3 | Rule and Config (legacy, removed; Config moved here from A1) | | | | | |
| B | Event, GuardrailEvent, EvalRun, EvalResult | | | | | |
| C | Connector, ConnectorInstance, Record, links, CustomField, ImportRun, IntegrationRun (archived) | | | | | |
| D | Integration, IntegrationCandidate, WorkflowRun, IntegrationLogRecord, OutboundMessage, AgentMessage | | | | | |
| E | AuditLog (legacy, removed), read-model cleanup | | | | | |

Each batch adds a `### <batch>` section here with the pasted report extract.

### A1: Owner, EmailTemplate (dev stack, 2026-11-02)

**Before** (graph / Postgres): Owner 8 / 0, graph sha `561f37e4…`; EmailTemplate 4 / 0, graph sha `831eaa95…`.
Snapshot: `BGSAVE` taken first.

**Cutover** at startup (`graph_migration.cutover batch=A1 labels=2`, 03:08:01 UTC), before the worker started:

| Label | source | graph | pg | parity | unmapped keys | read paths left | drift since cutover |
|---|---|---|---|---|---|---|---|
| Owner | postgres | 8 | 8 | ok | none | none (`records.py` is the projection writer) | ok (projection: graph = Postgres) |
| EmailTemplate | postgres | 4 | 4 | ok | none | none | ok |

**Freeze evidence** (from `bridge_state.report.freeze` after the drill's re-cutover): `single_writer: true`,
`watched_seconds: 15`, `recent_stopped_heartbeats: ["DESKTOP-968RPED:12956"]` (the instance stopped for the drill; its
heartbeat didn't advance, so cutover went ahead).

**Feature smoke:** `scripts/smoke.py` passes (every role; the Email templates page renders for each). By API:
templates list and edit (HR Ops), `/api/rules` lists the 8 owners, manager scoping (the dev manager sees E1006, E1002),
employee detail resolves owner-backed managers through `REPORTS_TO` (E1001 → Priya Menon), task items keep their owners.
(The first smoke run hit a race in the rules-rollback check, unrelated to A1: it accepted the open version while it was
still a draft. Fixed in `smoke.py`; the re-run passed.)

**Rollback drill:**
1. Through the app as HR Ops: imported owner `drill.owner@azentio.local`, edited the `intern` template subject.
   The Owner projection picked up the new owner at once.
2. Stopped the backend, ran `graph_migration rollback A1`: Owner ok 9 / 9, EmailTemplate ok 4 / 4, checksums equal. The
   graph's `intern` template carried the edited subject.
3. Restarted: cut over again at 03:09:56 UTC, Owner 9 / 9 and EmailTemplate 4 / 4, parity ok, drift ok.
4. Put the dev data back (template from the seed, drill owner removed and re-projected); drift ok afterwards.

**Sign-off:** dev ✓. CI on the PR covers `test_phase3` (A1 section and API contract).

### A2: Policy (dev stack, 2026-11-02)

**Before** (graph / Postgres): Policy 7 / 0, graph sha `7214fd3b…`; 31 `PolicyChunk` nodes (they stay). `BGSAVE` first.

**Cutover** at startup, 03:37:03 UTC:

| Label | source | graph | pg | parity | unmapped keys | read paths left | drift since cutover |
|---|---|---|---|---|---|---|---|
| Policy | postgres | 7 | 7 | ok | none | none | ok |

**Freeze evidence** (after the drill's re-cutover): `single_writer: true`, `watched_seconds: 15`,
`recent_stopped_heartbeats: ["DESKTOP-968RPED:23400"]` (the instance stopped for the drill).

**Feature smoke:** `scripts/smoke.py` passes for every role. Through the app (HR Ops): a policy upload on `/api/ingest`
lands in `policies` and `/api/policies`, with no new `Policy` node in the graph. `chat.retrieve("drill parking allowance")`
returns `a2-drill-policy.md § Drill section` first. The portal shows the policy text on E1001's acknowledgement items
(`infosec_ack`, `coc_ack`; `nda_ack` has no `policy_ref` in the seed rules, as before).

**Rollback drill:**
1. Uploaded `a2-drill-policy.md` through the app.
2. Stopped the backend and ran `graph_migration rollback A2`: Policy ok 8 / 8, checksums equal, and the drill policy is a
   graph node again.
3. Restarted: cut over again at 03:38:23 UTC, 8 / 8, parity ok, drift ok.
4. Removed the drill policy from `policies` and its chunks. Its stale graph node stays until PR E's cleanup, so drift
   stays ok.

**Sign-off:** dev ✓. CI on the PR covers `test_phase3` (A2 section and API contract).

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
| A1 | Owner, EmailTemplate, Config | | | | | |
| A2 | Policy (PolicyChunk stays as an index) | | | | | |
| A3 | Rule (legacy, removed) | | | | | |
| B | Event, GuardrailEvent, EvalRun, EvalResult | | | | | |
| C | Connector, ConnectorInstance, Record, links, CustomField, ImportRun, IntegrationRun (archived) | | | | | |
| D | Integration, IntegrationCandidate, WorkflowRun, IntegrationLogRecord, OutboundMessage, AgentMessage | | | | | |
| E | AuditLog (legacy, removed), read-model cleanup | | | | | |

Each batch adds a `### <batch>` section here with the pasted report extract.

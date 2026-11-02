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
     **The snapshot is sensitive.** It keeps every graph value as it was, including the plaintext connector secrets
     that batch C encrypts and then removes from the graph. Store it like a credential, delete it once its batch is
     signed off (after E it's the cleanup's rollback point; delete it once E is signed off), and rotate any real
     credential it held.
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
| B | Event, GuardrailEvent, EvalRun, EvalResult | 2026-11-02 09:46 UTC | Event 434/0 → 434/434, GuardrailEvent 1/1, EvalRun 2/2, EvalResult 25/25; sha equal | single writer | pass | dev |
| C | Connector, ConnectorInstance, Record, links, CustomField, ImportRun, IntegrationRun (archived) | 2026-11-02 14:43 UTC | Connector 7/7, ConnectorInstance 7/7 (OF 7), Record 20/20 (IN 20, SYNCED_TO 3, FROM_CANDIDATE 3), CustomField 0/0, ImportRun 1/1, IntegrationRun 3/3; sha equal | single writer | pass | dev |
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

### Fresh install and wiped graph (throwaway stack, 2026-11-02)
Run on a separate database and graph (`azentio_fresh` / `fresh`, port 8001, Gmail off), after A2:
- **Fresh install:** A1 and A2 cut over on empty stores (0 / 0). The seed then wrote 8 owners, 4 templates and 7 policies to
  Postgres, projected 8 `Owner` nodes and built 31 `PolicyChunk` nodes, with no `EmailTemplate` or `Policy` nodes. 22 API and
  state checks passed: role gates, the audited template save, and quick onboard picking the manager from `owners`.
- **Wiped graph:** edited a template, an owner and a joiner's name, stopped, ran `GRAPH.DELETE fresh`, and restarted.
  Every edit survived. The projection was rebuilt from Postgres (7 employees, 101 items, 20 documents, matching
  `HAS_DOC`/`SUBMITTED` and `REPORTS_TO` edges), the index was rebuilt (31 chunks), drift was ok, and the same 22
  checks passed. Before the seed fix, this reset all three edits to the seed values.

### Deep test of A1 + A2 + A3 together (throwaway stack, 2026-11-02)
Database `azentio_deep`, graph `deep`; the previous release ran from a `main` worktree.

| Step | Result |
|---|---|
| Baseline: ruff, guard + selftest, `test_core`, `test_phase0..3`, `tsc`, `eslint` | pass |
| Upgrade `main` → A3 with HR edits made on `main` (template, mixed-case owner email, policy) | A1, A2, A3 cut over in one start, before the worker; parity, schema and read paths clean; edits served; new joiner `REPORTS_TO` the `main`-era owner; retrieval finds the `main`-era policy |
| Writes on the new release (template, owner, policy, a two-approver rule version published) | pass |
| Full rollback (`rollback A3`, `A2`, `A1`), then start `main` on the same data | each rollback at parity; `main` serves every new-release edit and rule set v2 |
| Re-upgrade after an edit on `main` | clean second cutover of all three; the edit carried over |
| Two instances started at once | one cut over; the other refused ("another instance is cutting over… nothing was switched"); restarted, it found nothing pending |
| Wiped graph after an upgrade | edits kept, projection rebuilt, no false drift |
| Dev stack: `smoke.py`, drift, `/ready` | pass |

**Independent review:** 8 findings; 7 fixed with regression tests in `test_phase3`, 1 noted:
1. An upgrade from before rule-set versions would have published the sample rules over HR's own: `seed_rules` now
   builds v1 from the archived graph rules (`policy.legacy.archived_requirements`).
2. A lost graph with no employees never re-projected owners: every start now refreshes the `Owner` projection.
3. A legacy mixed-case `Owner` node passed parity (case-folded), and the next owner write would drop it and its
   edges: a projection label's cutover now rebuilds the projection once.
4. A wiped graph reported drift on retired labels forever: an emptied retired label isn't drift.
5. A CSV `compliance` cell of "false", "no" or "0" counted as true: it's parsed as text now.
6. Noted, not changed: an owner import commits, then refreshes the graph. With FalkorDB down it returns 500 after
   saving. That's the same pattern as every employee write; the next start's owner refresh heals the projection.
7. One failing spec module left a silently partial registry: `load()` now marks itself loaded only after every module
   imports.
8. The loser of a first-run import race reported rows imported and re-ran checklists: it now returns `imported: 0` and
   does nothing.

### B: Event, GuardrailEvent, EvalRun, EvalResult (dev stack, 2026-11-02)

**Before** (graph / Postgres): Event 434 / 0 (sha `d4a74973…`), GuardrailEvent 1 / 0, EvalRun 2 / 0, EvalResult 25 / 0.
`BGSAVE` first.

**Cutover** at startup, 09:46:39 UTC:

| Label | source | graph | pg | parity | unmapped keys | read paths left | drift since cutover |
|---|---|---|---|---|---|---|---|
| Event | postgres | 434 | 434 | ok | none | none | ok |
| GuardrailEvent | postgres | 1 | 1 | ok | none | none | ok |
| EvalRun | postgres | 2 | 2 | ok | none | none | ok |
| EvalResult | postgres | 25 | 25 | ok | none | none | ok |

**Feature smoke:**
- `scripts/smoke.py` passes for every role, including `/activity` and the AI quality page.
- By API:
  - HR Ops sees 50 activity rows with names. The dev manager sees only E1002 and E1006.
  - E1001's timeline has 75 events, newest first.
  - `/api/quality` lists the runs and the guardrail counts.
- Writes through the app:
  - A prompt-injection message to E1001's portal assistant was blocked and counted (`prompt_injection` 1 → 2).
  - An NDA acknowledgement added an event. Postgres went to 435; the graph stayed at 434.

**Rollback drill:**
1. Stopped the backend and ran `graph_migration rollback B`: Event 435 / 435, GuardrailEvent 2 / 2, EvalRun 2 / 2,
   EvalResult 25 / 25, all ok. The new event and guardrail hit are in the graph.
2. Restarted: cut over again at 09:48:36 UTC, parity ok, drift ok.
3. All nine Phase 3 labels so far are on `postgres` at parity. Freeze evidence: `single_writer: true`. The stopped
   instance's heartbeat was already older than 60 s, so there was nothing to watch.

**Throwaway deep run:**
- `main` → B: A1, A2, A3 and B cut over in one start. The `main`-era events and guardrail hit carried over.
- An eval run started through `/api/quality/eval` finished 20/20 on the new storage and became the latest run.
  (The throwaway instance still reached a free-tier model with the API keys blanked. Eval runs belong on a throwaway
  stack anyway, never on the dev data.)
- A full rollback (B, A3, A2, A1) followed, then `main` on the same data. `main` showed the run with its 20 results, the
  run's guardrail hits and all the activity.
- Re-upgrade: four clean cutovers.

**Sign-off:** dev ✓. CI on the PR covers `test_phase3` (B section and API contract).

### C: connectors and secrets (dev stack, 2026-11-02)

**Before:** Connector 7, ConnectorInstance 7 (2 with plaintext secrets: Slack `••••abcd`, Jira `••••ksdf`), Record 20,
CustomField 0, ImportRun 1, IntegrationRun 3; edges OF 7, IN 20, SYNCED_TO 3, FROM_CANDIDATE 3. `BGSAVE` first.

**Secrets first.** Production: set `SECRETS_KEY` before deploying C, because the cutover encrypts while it backfills and
refuses without a key. Dev without a key uses the built-in dev key and logs a warning. Keep the key apart from database
backups; `rollback C` needs it to write plaintext back for the previous release.

**Cutover** at startup, 14:43:18 UTC: all six labels `postgres`, parity ok, no unmapped keys, no read paths left, edge
counts equal. Both secrets became Fernet tokens (`secret_key_id d8f1b5e4`, the dev key) with the same hints.

**Feature smoke:**
- `scripts/smoke.py` passes for every role, including `/connectors`.
- By API as admin:
  - the connector list (record counts; Slack and Jira `connected`)
  - records page and search
  - Slack's connection shows `••••abcd` and never the secret; test-connection ok; saving with a blank token kept it
  - a CSV import into Candidate Import (a second import run)
  - starting onboarding from that record added `FROM_CANDIDATE` (E74957)
  - the HR Portal sync (`push_employee`) added `SYNCED_TO`
- Integration runs send mail on the dev stack, so the run was done on the throwaway stack.
- Drift ok after the writes.

**Rollback drill:**
1. Stopped the backend and ran `graph_migration rollback C`: Record 22/22 (IN 22, SYNCED_TO 4, FROM_CANDIDATE 4),
   ImportRun 2/2, everything ok. The secrets are plaintext again, and E74957's two links are edges.
2. Restarted: cut over again at 14:45:34 UTC. All 15 Phase 3 labels so far are `postgres` at parity with no drift.

**Throwaway deep run:**
- `main` saved a Slack token (plaintext in the graph) and onboarded a candidate.
- `main` → C with a real `SECRETS_KEY`: A1, A2, A3, B and C cut over in one start; the secret was encrypted with that
  key, the hint and test-connection were unchanged; onboarding added a link; an integration run succeeded.
- Rotation: `SECRETS_KEY=new`, `SECRETS_KEY_PREVIOUS=old`, `python -m connectors.rotate_key` ("re-encrypted 1"), then
  the new key alone: test-connection ok.
- Refusals: the old key alone → "connector secrets were encrypted with key 7cdb184f, but the configured keys are
  40ee0d0b"; `ENV=production` with no key → "connector secrets are stored; set SECRETS_KEY".
- Full rollback (C, B, A3, A2, A1) with the key, then `main`: connectors, the Slack connection (test passes) and both
  onboarding links. Re-upgrade: five clean cutovers.

**Sign-off:** dev ✓. CI on the PR covers `test_phase3` (C section and API contract) with a throwaway `SECRETS_KEY`.

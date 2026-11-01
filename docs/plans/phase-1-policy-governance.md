# Phase 1: Policy governance + Employee/Item/Rule migration

- **Status:** done (2026-11-01). `test_phase1.py`: 38 checks; `scripts/smoke.py` runs the policy flow end to end through the UI
- **Deviations from this plan (decided while building, after reading the code):**
  1. **Graph projection is updated in place, never dropped and recreated.** Documents stay in the graph until Phase 2 and hang off `Item`/`Employee` nodes (`HAS_DOC`, `SUBMITTED`, `MISMATCH`); recreating nodes would cut those edges. `records.project(eid)` `MERGE`s by id and only rebuilds `HAS_ITEM`, `DEPENDS_ON`, `OWNED_BY`, `REPORTS_TO`.
  2. **No dual read path.** Every write goes to Postgres and then to the graph projection in the same request, so the stores stay identical. Startup backfills once, switches only if parity passes and records the report in `bridge_state`; if parity fails, startup stops instead of serving graph reads. Rollback of the migration is `git revert` (the graph stays complete because every write still projects to it).
  3. **Owners aren't snapshotted in versions.** They're directory data, so they stay live; versions hold `doc_types`, `locations`, `departments`, `roles`.
  4. **Notifications are portal/activity events; email is opt-in** (`POLICY_NOTIFY_EMAIL=true`), so publishing demo data doesn't email seeded joiners.
  5. **Seed:** the near-start exception for Kabir isn't fabricated; it appears naturally when the seeded v2 is published. A second dev approver (`approver2@azentio.local`) exists so the two-approver flow can be demonstrated. `author_id` is nullable (NULL = system, for v1).
  6. **`PUT/DELETE /api/rules/*` return 403**, not 404: the permission table refuses writes to that path before routing.
- **Depends on:** Phase 0 (`shared/db.py`, `shared/jobs.py`, `shared/bridge.py`, `audit/service.py`, `identity/perms.py`, `work_queue`, `approval_requests`/`approvals` tables)
- **Branch:** `phase-1-policy-governance`
- **Orchestration note:** in this phase, waiting for an approval is plain Postgres state: the `approval_requests` and `approvals` tables plus the `work_queue` row, with publishing done by a job. Phase 4 wraps this in a Temporal `RuleVersionApprovalWorkflow` ([ADR 0011](../adr/0011-temporal-orchestration.md)) **without changing these tables**, so nothing built here has to be redone.

## Goal
Today, a requirement rule can be edited or deleted in one step, and the change goes live at once. `PUT /api/rules/{rid}` (`main.py:save_rule`) and `DELETE /api/rules/{rid}` (`main.py:delete_rule`) write the rule, then call `checklist.regenerate()` for **every** employee in the same request. `regenerate()` hard-deletes items that no longer apply (`DETACH DELETE i`), even items that are done and have documents attached.

After this phase:
1. Rule changes are **policy changes**. A draft goes to an impact preview, someone other than the author approves it, and only then is it published as an immutable version. Rules marked `compliance` need two approvers.
2. Publishing updates joiners already in progress safely:
   - new items are added and the joiner is notified
   - an item is cancelled only if it isn't done yet
   - evidence is never deleted
   - joiners starting within `NEAR_START_DAYS` get a `work_queue` exception instead of a change
3. Every version can be rolled back in one click. A rollback is just another published version, so it follows the same safe in-flight policy.
4. `Employee`, `Item` and `Rule` live in Postgres, which the preview needs. The graph keeps only a rebuilt projection of the dependency edges.

## Scope
- Rule versioning: draft, submit, preview, approve or reject, publish, rollback.
- Dual approval for rules flagged `compliance`.
- Safe in-flight replay that replaces the destructive part of `checklist.regenerate()`.
- Postgres tables for `employees`, `items` and `rule_sets`/`rules`, moved with the 4-step bridge migration.
- Notifications on publish (joiner email or portal message) as jobs.
- Screens: rules list with version badge, draft editor, impact preview, approval inbox, version history with diff and rollback.

## Non-goals
- Document storage and review decisions. `Document` stays in the graph until Phase 2.
- Threshold policy for document risk tiers (Phase 2). It reuses the same version/approval machinery built here.
- Connectors, integrations and runs (Phase 3).
- Editing `Config` (`doc_types`, `locations`, `departments`, `roles`). These are read from a published rule set's `config` snapshot from now on, but there's no editor yet.
- A per-rule approval policy beyond the `compliance` flag. Anything more is out of scope until someone asks for it.

## Design

### Versions are whole rule sets
A version is a snapshot of **all** rules plus the config the engine reads (`doc_types`, `locations`, `owners` by category). It's not a per-rule history.

Why: `checklist.build_items(emp, rules, doc_types, locations, owners)` is a pure function over the whole set. Snapshotting the set means:
- the preview is just `build_items(emp, draft)` compared with `build_items(emp, active)`
- rollback is just "publish version N again"

Exactly one version has `state='active'` at any moment. A partial unique index enforces this.

### States
```
draft ──submit──▶ pending_approval ──approve(×1 or ×2)──▶ approved ──publish job──▶ active
  ▲                     │                                                      │
  └──── reject ─────────┘                                         superseded ◀─┘ (when a newer one goes active)
```
- **Who can do what:**
  - Only `hr_ops` (and `admin`) can create or edit drafts.
  - Only `approver` (and `admin`) can approve.
  - The author can never approve their own version. This is enforced in SQL with a CHECK on `approvals` and again in the service.
- **How many approvals:** if any rule in the version has `compliance = true`, **and** that rule was added, removed or changed compared with the active version, the version needs 2 distinct approvers. Otherwise 1.
- **Publishing:**
  - Publishing is automatic once the approval count is reached. The service enqueues `policy.publish` with `idempotency_key = 'policy.publish:<version_id>'`. No human clicks "publish".
  - The publish job applies the safe replay (below) to every in-flight joiner, then flips `active`, all in one transaction per batch of joiners.
  - The version only becomes `active` after all batches succeed. A failure retries; once the job is dead it opens an `ops` exception through Phase 0's dead-job hook.

### Safe in-flight replay
This replaces the body of `checklist.regenerate()`. The pure `build_items()` stays as it is.

For each joiner in progress (`joining_date >= today - 30` and `status <> 'archived'`):
- **Added items** (key in the new plan, not the old): insert with `INITIAL[kind]` status. Enqueue `notify.item_added`, keyed `notify.item_added:<eid>:<version_id>`. One email or portal message per joiner per version, listing every added item.
- **Kept items:** update `due`, `owner_*`, `reason`, `depends`, `rule_id` and `rule_version_id`. Status and documents are never touched.
- **Removed items** (key in the old plan, not the new):
  - if `status IN DONE` or the item has any linked document, keep it and set `superseded_by_version = <version_id>`, so it's kept as historical evidence
  - otherwise set `status = 'Cancelled'` and `cancelled_by_version = <version_id>`

  Nothing is deleted.
- **Joiners close to their start date** (`joining_date - today <= NEAR_START_DAYS`, default 3, env-configurable): no changes are applied. One `work_queue` row is opened instead (`kind='policy.near_start'`, `assignee_role='hr_ops'`, `sla_due = joining_date - 1 day`) carrying the computed diff. HR Ops resolves it with **apply** (runs the same replay for that one joiner) or **skip**, plus a reason. Both are audited.
- Every change writes one `audit.record()` row per joiner (`action='policy.replay'`, with the before/after item keys).

  This keeps today's useful `event(eid, "checklist", ...)` lines, which feed the Activity page. They're written into the `events` table, which stays in the graph until Phase 3 and goes through the bridge.

### Impact preview
`policy.preview(version_id, filters)`: for each joiner matching the filters, compute `old = build_items(emp, active)` and `new = build_items(emp, draft)`, then diff them. The result:

```json
{"summary": {"joiners": 42, "added": 18, "cancelled": 3, "kept_as_evidence": 2, "due_moved": 11, "newly_blocked": 4, "near_start": 1},
 "rows": [{"employee_id": "E1006", "name": "Kabir Singh", "joining_date": "2026-11-02", "near_start": true,
           "added": [{"key": "visa_copy", "due": "2026-10-23"}], "cancelled": [], "kept_as_evidence": [],
           "due_moved": [{"key": "bgv", "from": "2026-10-29", "to": "2026-10-28"}], "newly_blocked": ["bgv"]}]}
```

- **"Newly blocked"** means `view_state()` was not `blocked` under the old plan and is `blocked` under the new one, evaluated against the joiner's current item statuses.
- **Filters:** department, role, location, `join_from`/`join_to`, current overall status (`ready`/`overdue`/`blocked`/`in_progress`, the same values `list_employees` uses).
- **Speed:** it's pure Python over rows loaded in one query each (employees, items). At 1,000 joiners × 20 rules it's well under a second, so it isn't cached.
- **Saved preview:** the preview is stored on the version (`preview` JSONB) when it's submitted, so approvers see exactly what the author saw. Approving recomputes it, and if the counts changed since submission (because joiners moved), the UI shows a "preview changed since submission" banner.

## Schema
Migrations continue after Phase 0's `001`–`004`.

**`backend/migrations/005_core_records.sql`**
```sql
CREATE TABLE employees (
  id              text PRIMARY KEY,                -- keeps graph ids (E1001, E50448…)
  name            text NOT NULL,
  email           text NOT NULL,
  role            text NOT NULL,
  grade           text NOT NULL DEFAULT '',
  department      text NOT NULL,
  location        text NOT NULL,
  manager_email   text NOT NULL DEFAULT '',
  joining_date    date NOT NULL,
  date_of_birth   date,
  worker_type     text NOT NULL DEFAULT 'employee', -- used by Phase 5; default keeps today's behaviour
  token           text NOT NULL UNIQUE,             -- portal token, never returned by list endpoints
  thread_id       text NOT NULL DEFAULT '',
  doc_scope       text[] NOT NULL DEFAULT '{}',
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  extra           jsonb NOT NULL DEFAULT '{}',      -- any graph property not mapped above (lossless backfill)
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX employees_email_lower ON employees (lower(email));
CREATE INDEX employees_joining ON employees (joining_date);
CREATE INDEX employees_manager ON employees (lower(manager_email));

CREATE TABLE items (
  id                     text PRIMARY KEY,         -- keeps graph ids (it_…)
  employee_id            text NOT NULL REFERENCES employees(id),
  key                    text NOT NULL,
  name                   text NOT NULL,
  kind                   text NOT NULL CHECK (kind IN ('document','task','ack')),
  category               text NOT NULL,
  status                 text NOT NULL,
  due                    date,
  reason                 text NOT NULL DEFAULT '',
  policy_ref             text NOT NULL DEFAULT '',
  auto                   text NOT NULL DEFAULT '',
  depends                text[] NOT NULL DEFAULT '{}',
  owner_name             text NOT NULL DEFAULT '',
  owner_email            text NOT NULL DEFAULT '',
  rule_id                text NOT NULL DEFAULT '',
  rule_version_id        bigint,                   -- set once Phase 1 publishes; NULL for backfilled rows
  superseded_by_version  bigint,                   -- removed by policy but kept as evidence
  cancelled_by_version   bigint,                   -- removed by policy before completion
  extra                  jsonb NOT NULL DEFAULT '{}',
  updated_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (employee_id, key)
);
CREATE INDEX items_employee ON items (employee_id);
CREATE INDEX items_status_due ON items (status, due);
```

**`backend/migrations/006_policy_versions.sql`**
```sql
CREATE TABLE rule_set_versions (
  id              bigserial PRIMARY KEY,
  number          int NOT NULL UNIQUE,              -- human version number: v1, v2…
  state           text NOT NULL CHECK (state IN ('draft','pending_approval','approved','active','superseded','rejected')),
  based_on        bigint REFERENCES rule_set_versions(id),
  rollback_of     bigint REFERENCES rule_set_versions(id), -- set when this version republishes an older one
  title           text NOT NULL,
  reason          text NOT NULL DEFAULT '',
  config          jsonb NOT NULL,                   -- {doc_types, locations, departments, roles, owners}
  required_approvals int NOT NULL DEFAULT 1 CHECK (required_approvals IN (1,2)),
  preview         jsonb,                            -- stored at submit time
  author_id       bigint NOT NULL REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  submitted_at    timestamptz,
  published_at    timestamptz
);
CREATE UNIQUE INDEX one_active_rule_set ON rule_set_versions ((true)) WHERE state = 'active';
CREATE UNIQUE INDEX one_open_draft ON rule_set_versions ((true)) WHERE state IN ('draft','pending_approval','approved');

CREATE TABLE rules (
  version_id   bigint NOT NULL REFERENCES rule_set_versions(id),
  id           text NOT NULL,                        -- R-ID, R-PAN…
  ord          int NOT NULL,
  item         text NOT NULL,
  name         text NOT NULL DEFAULT '',
  kind         text NOT NULL CHECK (kind IN ('document','task','ack')),
  category     text NOT NULL,
  match        jsonb NOT NULL DEFAULT '{}',
  offset_days  int NOT NULL DEFAULT 0,
  depends_on   text[] NOT NULL DEFAULT '{}',
  description  text NOT NULL,
  policy_ref   text NOT NULL DEFAULT '',
  auto         text NOT NULL DEFAULT '',
  compliance   boolean NOT NULL DEFAULT false,
  PRIMARY KEY (version_id, id)
);

-- Versions other than the open draft are immutable.
CREATE FUNCTION rules_frozen() RETURNS trigger AS $$
BEGIN
  IF (SELECT state FROM rule_set_versions WHERE id = COALESCE(OLD.version_id, NEW.version_id)) <> 'draft' THEN
    RAISE EXCEPTION 'rule set version % is frozen', COALESCE(OLD.version_id, NEW.version_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;
CREATE TRIGGER rules_frozen BEFORE INSERT OR UPDATE OR DELETE ON rules
  FOR EACH ROW EXECUTE FUNCTION rules_frozen();
```

**Reusing `approval_requests`/`approvals` from Phase 0** (`004_approvals_reviews.sql`):
- One `approval_requests` row per submitted version: `subject_type='rule_set_version'`, `subject_id=<id>`, `required=<required_approvals>`.
- `approvals` rows record `(request_id, approver_id, decision, reason, at)`. Its Phase 0 constraints are `UNIQUE(request_id, approver_id)`, plus a CHECK that `approver_id <> author_id`, with `author_id` copied onto the request.

**`backend/migrations/007_items_seen.sql`**: `notify.item_added` jobs record which versions a joiner has been told about:
```sql
CREATE TABLE item_notices (employee_id text REFERENCES employees(id), version_id bigint REFERENCES rule_set_versions(id),
                           sent_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (employee_id, version_id));
```

## API
Permissions are added to `identity/perms.py`. The roles are `admin`, `hr_ops`, `approver`, `manager` and `auditor`. `admin` can do everything; it's left out of the table below except where it's the only role allowed.

| Method & path | Purpose | Roles |
|---|---|---|
| `GET /api/rules` | The active version's rules + config (same response shape as today, plus `version: {id, number, published_at}`) | hr_ops, approver, auditor, manager (read) |
| `GET /api/policy/versions` | Version history: number, state, author, approvers, reason, counts | hr_ops, approver, auditor |
| `GET /api/policy/versions/{id}` | One version, with all its rules | hr_ops, approver, auditor |
| `GET /api/policy/versions/{id}/diff?against={id}` | Rule-level diff (added/removed/changed fields), default against the active version | hr_ops, approver, auditor |
| `POST /api/policy/drafts` | Open a draft by copying the active version (409 if a draft already exists) | hr_ops |
| `PUT /api/policy/drafts/{id}/rules/{rid}` | Upsert one rule in the draft (body = today's `Rule` model + `compliance`) | hr_ops |
| `DELETE /api/policy/drafts/{id}/rules/{rid}` | Remove a rule from the draft | hr_ops |
| `DELETE /api/policy/drafts/{id}` | Discard the draft | hr_ops (author) |
| `GET /api/policy/versions/{id}/preview?department=&role=&location=&join_from=&join_to=&status=` | Impact preview | hr_ops, approver, auditor |
| `POST /api/policy/drafts/{id}/submit` | `{title, reason}` → `pending_approval`, stores the preview, opens the approval request | hr_ops |
| `POST /api/policy/versions/{id}/approve` | `{reason}` → counts an approval; when the count is reached, sets `approved` and enqueues `policy.publish` | approver (≠ author) |
| `POST /api/policy/versions/{id}/reject` | `{reason}` (required) → `rejected`; the draft slot is freed | approver |
| `POST /api/policy/versions/{id}/rollback` | `{reason}` → creates a new draft copied from version `{id}` with `rollback_of` set, and submits it straight away. Goes through the normal approval flow | hr_ops |
| `GET /api/work-queue?kind=policy.near_start` | Near-start exceptions | hr_ops |
| `POST /api/work-queue/{id}/resolve` | `{resolution: 'apply'|'skip', reason}` | hr_ops |

`PUT /api/rules/{rid}` and `DELETE /api/rules/{rid}` are **removed**. The frontend switches to the draft endpoints in the same PR.

**Rollback needs approval too.** Rollback is just another policy change, and skipping approval would let one person undo a compliance rule alone. To keep it fast, rollback submits the draft automatically, so the approver gets it straight away.

## Screens

**Rules list** (`frontend/app/(hr)/rules/page.tsx`, rebuilt from the current table)
- **Header:** "Requirement rules · v7 active since 28 Oct by priya@ (approved by sam@)", plus a **Version history** link.
- If a draft exists, a sticky banner: "Draft v8 by priya@ · 3 changes · pending approval (1 of 2)", with a button to open it.
- **Table:** same columns as today, plus a `Compliance` badge. Edit and delete buttons appear only for `hr_ops`; they open or extend the draft rather than editing live rules.
- **States:**
  - loading: skeleton rows
  - empty: impossible after bootstrap, since bootstrap seeds v1
  - error: inline card with a retry button

**Draft editor** (`frontend/app/(hr)/rules/draft/page.tsx`)
- The left column lists rules, with change markers: added (green dot), changed (amber), removed (struck through).
- The right panel holds the rule form. It's the existing form from `rules/page.tsx`, extracted into `components/rule-form.tsx`, with a `compliance` toggle that explains "requires two approvers".
- The footer shows a change count and **Preview impact**.

**Impact preview** (`frontend/app/(hr)/rules/draft/preview/page.tsx`, also embedded read-only in the approval view)
- **Top:** 7 stat tiles (joiners affected, added, cancelled, kept as evidence, due dates moved, newly blocked, near start).
- **Filters:** department, role, location, joining window (two `<input type="date">`), status. These are the same controls as the dashboard filter bar.
- **Table:** one row per joiner, with chips per change type. Expanding a row shows item-level detail. Near-start rows carry an "HR review" badge and the note "won't change automatically".
- **Footer** (`hr_ops`): title + reason textarea (required), then **Submit for approval**.
- **States:**
  - zero joiners affected: "No in-flight joiners change; new joiners will get this version"
  - loading: skeleton tiles

**Approval inbox** (`frontend/app/(hr)/approvals/page.tsx`)
- Adds a **Policy changes** tab next to the existing candidate-documents and offer-format items (which keep coming from `integration_routes.approvals`).
- **Each card:** version number, author, submitted time, reason, "needs 2 approvals · 1 received (sam@)", the rule diff, and the embedded preview.
- **Actions:** Approve / Reject (reason required). They're disabled, with the tooltip "You authored this", when the current user is the author.
- The "preview changed since submission" banner appears when the recomputed counts differ from the stored ones.

**Version history** (`frontend/app/(hr)/rules/history/page.tsx`)
- **Timeline:** number, state badge, title, author → approvers, published time, and "rolled back from v5" where it applies.
- **Diff drawer:** compare any version against the active one or its predecessor, side by side by rule id, with changed fields highlighted.
- **Roll back to this version** (`hr_ops`): opens a reason dialog, then confirms "This creates v9 = copy of v5 and sends it for approval."

**Near-start queue** (`frontend/app/(hr)/approvals/page.tsx`, **Joiners needing review** tab): each card shows the joiner, their start date, the version, and the diff, with **Apply** / **Skip** and a required reason.

The nav stays the same. "Requirement rules" shows a dot when a draft is pending. Who sees what:
- `approver` sees Approvals and Rules, read-only.
- `auditor` sees Rules and History, read-only.
- `manager` doesn't see rule pages.

## File-by-file changes

| File | Change |
|---|---|
| `backend/policy/__init__.py` | new, empty |
| `backend/policy/store.py` | new. `active_version()`, `load_version(id)` (returns `(rules, config)` in the exact shape `build_items` expects: `match` as dict, `depends_on` as list), `open_draft(author)`, `upsert_draft_rule()`, `delete_draft_rule()`, `diff(a, b)` |
| `backend/policy/service.py` | new. `preview(version_id, filters)`, `submit()`, `approve()`, `reject()`, `rollback()`, `required_approvals(draft, active)`. `@jobs.handler("policy.publish")` runs `replay_all(version_id)` in batches of 50 joiners per transaction. `@jobs.handler("notify.item_added")` |
| `backend/policy/replay.py` | new. `plan_diff(emp, items, old_plan, new_plan)` is **pure** and returns `{added, kept, cancel, keep_evidence, due_moved}`. `apply(eid, diff, version_id, actor)` does the writes. Both are unit-tested without a database |
| `backend/policy/routes.py` | new. The endpoints above |
| `backend/checklist.py` | `match_rule`, `build_items`, `view_state` and `grade_num` are **unchanged**. `load_rules()`/`config()` delegate to `policy.store.load_version(active)` through the bridge. `regenerate(eid, reason)` becomes `replay.apply(eid, plan_diff(...), active_version, actor)`, so a profile edit (`patch_employee`) goes through the same safe path, minus the near-start gate: a direct HR edit **is** the human decision. `recompute()` keeps its logic but writes through `employees`/`items` storage via the bridge. `employee_items()` reads through the bridge |
| `backend/main.py` | `save_rule`/`delete_rule` and the `Rule` model move to `policy/routes.py`. The loop in `save_rule` (`for e in q("MATCH (e:Employee)…"): checklist.regenerate(...)`) is removed; publishing is the only way rules reach joiners. `get_emp`, `emp_by_token`, `items_with_state`, `list_employees`, `employee_detail`, `patch_employee`, `set_item_status`, `portal`, `portal_ack` and `create_joiner` read and write through `shared.bridge`. `employee_graph` stays on the graph (a projection) |
| `backend/ingest.py` | `save_rule()` is used only by bootstrap seeding. `seed()` creates rule set **v1** (`state='active'`, `author=system`) from `seed/requirements.json` when no version exists. Employee creation (line ~93) goes through the bridge, and dual-writes during steps 1–3 of the migration |
| `backend/shared/bridge.py` | adds `employees`, `items` and `rules` to `SOURCE`, plus `parity()` checksums for them (below) |
| `backend/graph.py` | adds `rebuild_graph(eid=None)`, which drops and recreates `(:Employee)`, `(:Item)`, `HAS_ITEM`, `DEPENDS_ON`, `OWNED_BY` and `REPORTS_TO` from Postgres for one employee or all. It's called at the end of every bridge write for that employee, in the same request. `employee_graph()` and the HR assistant's graph queries keep working |
| `backend/integrations/engine.py` | the `MATCH (e:Employee…)` reads at lines ~96/99/194/195 go through the bridge |
| `backend/connectors/hr_portal.py` | `push_employee(eid)` reads the employee through the bridge |
| `backend/chat.py`, `backend/gmail.py` | employee and item reads through the bridge |
| `frontend/app/(hr)/rules/page.tsx` | rebuilt as described in Screens. The form is extracted to `components/rule-form.tsx` |
| `frontend/app/(hr)/rules/draft/page.tsx`, `draft/preview/page.tsx`, `history/page.tsx` | new |
| `frontend/components/policy-diff.tsx`, `impact-preview.tsx` | new, shared by the draft, approval and history screens |
| `frontend/app/(hr)/approvals/page.tsx` | adds tabs (Integrations · Policy changes · Joiners needing review) and reads `/api/work-queue` |
| `frontend/lib/api.ts` | types `RuleSetVersion`, `PolicyPreview`, `WorkItem` |

## Migration steps (Employee, Item, Rule)
Each record type goes through the four Phase 0 steps. **The bridge may only switch a type to Postgres after `parity(type)` passes.**

1. **Dual-write.** Every write to `Employee`/`Item` goes to both stores, wrapped in `bridge.write(type, …)`. These are the writes in `ingest.py`, `checklist.regenerate/recompute`, `patch_employee`, `set_item_status`, `pipeline.intake` (item status), `portal_ack` and `integrations/engine.py`. Reads still use the graph (`SOURCE = {"employees": "graph", "items": "graph"}`). Rules don't need dual-write: they move as a one-time creation of v1 from the current graph `Rule` nodes plus `Config`, so the active version matches today's behaviour exactly.
2. **Backfill.** `uv run python -m policy.backfill` copies every graph `Employee`/`Item` into Postgres with `INSERT … ON CONFLICT (id) DO UPDATE`. Unmapped properties go into `extra`. It's idempotent and safe to re-run.
3. **Parity.**
   - **Row counts:** they must match.
   - **Checksum:** both stores compute `sha256` over rows sorted by `id`, using a canonical JSON of the mapped columns (dates as ISO strings, arrays sorted where the order doesn't matter: `depends`, `doc_scope`).
   - **Gate:** `parity()` returns `{type, graph_count, pg_count, graph_sha, pg_sha, ok, first_mismatch_id}`. The switch to Postgres is refused unless `ok`.
   - **Then switch:** `SOURCE["employees"] = "postgres"` and `SOURCE["items"] = "postgres"`. It's a one-line change in its own PR, and the PR description includes the parity output.
4. **Stop graph writes** for `Employee`/`Item` properties. Writes go to Postgres only, then `rebuild_graph(eid)` refreshes the projection in the same request. `ensure_indexes()` keeps the `Employee`/`Item` indexes, which the projection still uses.

**Rollback:**
- Steps 1–3 can be undone by flipping `SOURCE` back. Dual-write keeps the graph current until step 4.
- Undoing step 4 means running `rebuild_graph()` for everything, then flipping `SOURCE` back.

## Seed data
Bootstrap creates it if missing, and it's safe to re-run.
- **v1 `active`:** the 13 rules from `seed/requirements.json`, published by `system`. Three are marked `compliance=true`: `R-ID`, `R-I9`, `R-BGV`.
- **v2 `pending_approval`:** authored by `hrops@`. It changes `R-PAN` `offset_days` from -10 to -7 and adds `R-NDA` (ack, all employees). It isn't compliance-related, so it needs 1 approval. This seeds the preview and approval screens.
- **v3 cannot exist alongside v2**, because only one draft is allowed at a time (`one_open_draft`). The "waiting for a second approval" demo is therefore v2 with one compliance edit (`R-BGV` `offset_days` -4 → -5) and one approval already recorded from `approver@`. So v2 shows "1 of 2".
- **One near-start `work_queue` row:** Kabir Singh (joins 2 Nov) against v2.

## Tests
`backend/test_phase1.py`: plain `assert` checks against `azentio_test`, run with `uv run python test_phase1.py`.

| Check | Asserts |
|---|---|
| `test_plan_diff_pure` | `plan_diff` on hand-built old and new plans returns the expected added, kept, cancel, keep_evidence and due_moved sets |
| `test_done_item_kept_as_evidence` | removing the rule for an `Accepted` item with a document → the item is still there, `status='Accepted'`, `superseded_by_version` set, and the document link is untouched |
| `test_pending_item_cancelled_not_deleted` | removing the rule for a `Not Submitted` item → `status='Cancelled'`, and the row still exists |
| `test_added_item_notifies_once` | publishing with an added rule → exactly one `notify.item_added` job per joiner; re-running `policy.publish` adds no new jobs (idempotency key) |
| `test_near_start_not_changed` | a joiner starting in 2 days is unchanged after publish, and exactly one `work_queue` row with `kind='policy.near_start'` exists |
| `test_author_cannot_approve` | approving as the author → 403, and the DB CHECK also rejects a direct insert |
| `test_compliance_needs_two` | changing a `compliance` rule → `required_approvals=2`; after one approval the state is still `pending_approval`; a second distinct approver → `approved` + a publish job |
| `test_noncompliance_needs_one` | changing a non-compliance rule → one approval publishes it |
| `test_frozen_versions` | an `UPDATE rules` on an active version raises |
| `test_single_active` | after two publishes, exactly one `active` row exists |
| `test_rollback_restores_without_loss` | **(the explicit rollback test)** v1 → publish v2 (adds `R-NDA`, removes `R-PAN`) → a joiner completes `R-NDA`, and `pan_card` already had an accepted document → roll back to v1 and approve. Asserts: (a) the effective rule set equals v1 (same rule ids and fields); (b) the `pan_card` item is active again with its accepted document still linked; (c) the completed `R-NDA` item still exists, kept as evidence (`superseded_by_version` = the rollback version); (d) the approvals on v1, v2 and the rollback version are all still in `approvals`; (e) v2 is `superseded`, not deleted; (f) the audit chain verifies |
| `test_preview_matches_publish` | the preview's counts for a version equal the actual changes made by publishing it, for joiners not near their start |
| `test_preview_filters` | a department filter returns only joiners in that department |
| `test_parity_detects_drift` | after backfill `parity('items').ok`; changing one item in Postgres only → `ok=False`, with `first_mismatch_id` set |
| `test_bridge_refuses_switch` | switching `SOURCE` while parity fails raises |
| `test_rebuild_graph_matches` | after `rebuild_graph(eid)`, the graph's `HAS_ITEM`/`DEPENDS_ON` edges equal the `items.depends` in Postgres |
| `test_core.py` (existing) | still passes: the `build_items`/`view_state` checks are unchanged |

**Smoke** (`scripts/smoke.py`, extended):
1. `hrops@` opens the draft, edits a rule, previews it, and submits.
2. `approver@` approves.
3. Waiting on `/api/policy/versions` until the new version is `active`, then checking the rules page shows the new version number.

## Exit criteria
- `PUT/DELETE /api/rules/*` are gone. The only way a rule reaches a joiner is a published version.
- `parity()` passes for `employees` and `items`; `SOURCE` points both at Postgres; graph writes for both have stopped.
- Every check in `test_phase1.py` and `test_core.py` passes, and the smoke test passes for all 5 roles.
- Publishing the seeded v2 changes 0 completed items and deletes 0 documents, and Kabir gets a near-start exception instead of changes.
- Every publish, approval, rejection, rollback, replay and near-start resolution has an audit row with a named actor, and `verify_chain()` is true.
- The screenshots in `docs/screenshots/` include the draft, preview, approval and history pages. The LLD's rules section describes versioned rule sets.

## Risks & rollback
| Risk | Mitigation |
|---|---|
| A publish replay fails halfway through | The batch transactions are idempotent: re-running `plan_diff` against current state produces no further changes. The version only flips to `active` after the last batch. A dead job opens an `ops` exception |
| The preview drifts from what publishing actually does | Both call the same `plan_diff`, and `test_preview_matches_publish` guards it |
| One open draft at a time slows HR down | This is deliberate: two drafts editing the same set would need merging. Revisit if HR asks for parallel drafts |
| The near-start gate holds up a legally required change | The exception's `sla_due` is the day before joining, so HR Ops sees it in time. "Apply" is one click. The same Phase 6 escalation ladder applies |
| The graph projection goes stale | It's rebuilt in the same request as every write; `rebuild_graph()` for everything is the repair; `test_rebuild_graph_matches` covers it |
| The backfill misses graph-only properties | Unmapped properties land in `extra`, and the parity checksum covers the mapped columns. Review the backfill report's list of `extra` keys before switching |
| **Rolling back the phase itself** | Before step 4: flip `SOURCE` back and restore the `/api/rules` PUT/DELETE handlers from git. After step 4: run `rebuild_graph()`, then flip back. Rule set versions stay in Postgres in both cases, so their history is never lost |

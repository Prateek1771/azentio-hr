# Phase 2: AI control + Document migration

- **Status:** done (2026-11-02). See **As built** below for what changed from this plan.
- **Depends on:**
  - Phase 0: `shared/jobs.py`, `work_queue`, `audit/service.py`, `approval_requests`/`approvals`
  - Phase 1: `employees`/`items` in Postgres, and the version + approval machinery reused for thresholds
- **Branch:** `phase-2-ai-control`
- **Orchestration note:** in this phase, waiting for a human document review is plain Postgres state: the `work_queue` review row plus the review decision records, and extraction runs as a job. Phase 4 wraps it in a Temporal `DocumentReviewWorkflow` ([ADR 0011](../adr/0011-temporal-orchestration.md)) **without changing these tables**.

## As built (what changed from this plan)
The goal, the routing table, provenance, the LLM-down behaviour and the exit criteria are as planned. These parts changed, each approved in review ([ADR 0012](../adr/0012-document-decisions-as-built.md)):

| Planned | Built | Why |
|---|---|---|
| A separate `doc_policy_versions` table, `/api/doc-policy/*` and a Thresholds tab | Tiers and thresholds live in the rule-set version as `config.doc_policy`, edited with `PUT /api/policy/drafts/{id}/doc-policy` and governed by the Phase 1 flow (draft, preview, approve, publish, rollback, history) | One governance flow; no duplicated lifecycle or screens |
| 2 approvals when lowering a threshold, changing a tier, or raising the spot-check rate above 0.5 | 2 approvals when lowering a threshold, changing a tier, or **lowering** the spot-check rate (fewer acceptances checked) | Lowering the sample is what weakens oversight |
| Dual-write, then backfill, parity, switch, stop graph writes | One cutover at startup (`bootstrap.migrate_documents`): backfill, switch only if parity passes, else refuse to start. Graph writes stopped at once; `documents.store.project` keeps the view | Same approach as Phase 1; no second write path to keep in step |
| Backfilled accepted high-risk documents recorded as `human_accept` by a system user | Every backfilled document gets one decision of kind **`backfill`**, `decided_by` NULL; accepted high-risk ones are listed in the backfill report | The history never claims a review that didn't happen |
| `doc_decisions` protected by REVOKE | An append-only trigger (UPDATE/DELETE refused), plus `idempotency_key UNIQUE` and `documents.current_decision_id` | Holds for every role, and makes retries and double clicks no-ops |
| Item status written next to the document status | Item status is derived from the decision by `documents.service.item_status_for` only; `POST /api/items/{id}/status` returns 409 for document items | One source of truth |
| — | `work_queue` gained one serializer for every API, `claim()` with takeover after 30 minutes, and `claimed_at` (migration 009) | The queue contract is enforced, not a generic inbox |
| — | Guard rule 6: no graph writes of `Document` or its `HAS_DOC`/`SUBMITTED`/`MISMATCH` edges outside `documents/store.py` | The migration stays safe |
| `GET /api/review-queue` reads; doc-policy endpoints | The review detail leads with the policy facts (tier, threshold, version, who decided, why), ahead of confidence | Reviewers are accountable for the policy, not the score |
| Portal polls every 3 s | Backoff: 2, 4, 8, then every 15 s, up to 2 minutes | Less load, steadier UX |
| Seed: separate v2 doc-policy draft | The existing demo draft also lowers `bank_details` to 0.88 (one open draft at a time) | The one-draft limit is deliberate |

**Exit check results (dev stack, 2026-11-02):** document parity passed at cutover (7 documents, 0 missing files); `test_phase2.py` 64 checks; smoke passes for every role, including a portal upload that reached review as `ai_unavailable` while extraction failed, HR Ops read-only on a high-risk document, and an approver acceptance shown on the employee page.

## Goal
Today the AI's output can turn straight into a decision. In `pipeline.intake()`, `validate.evaluate()` marks a document `Accepted` whenever there are no issues and `confidence >= LOW_CONFIDENCE` (0.6, hard-coded in `validate.py`). That's true for every document type, passports and I-9 included. A human sees a document only if it's below 0.6 or something failed. When they do act (`POST /api/items/{id}/status`, `main.py:set_item_status`), the result records the new status but not who decided, why, or what evidence they saw.

After this phase:
1. Every document type has a **risk tier**.
   - **Low-risk** types are accepted automatically **only** when every deterministic check passes **and** `confidence >= auto_accept_min` (default 0.9).
   - **High-risk** types (`photo_id`, `i9_documents`, plus `visa`/`work_permit`/`bgv_*` when added) are **never** accepted automatically. They always open a `work_queue` review for an Approver.
2. Thresholds and tiers are a **versioned policy**, using the Phase 1 draft → preview → approve flow. Changing a threshold is a policy change.
3. Reviewers decide on a side-by-side screen: the original file, the extracted fields, the profile values, highlighted mismatches, and the reason codes. Approving or rejecting requires a reason.
4. Every decision, automatic or human, records its **provenance**: model, prompt version, confidence, threshold policy version, check results, and who decided.
5. **A random 10% of automatic acceptances** go to a spot-check queue, so the AI's precision is measured rather than assumed.
6. **When the LLM is down, documents go to review mode.** Extraction runs as a retrying job. If it's still failing at the deadline, the document goes to human review with `reason='ai_unavailable'` instead of sitting in limbo.
7. `Document` and the review decisions live in Postgres.

## Scope
- Postgres tables: `documents`, `document_mismatches`, `doc_decisions`, `doc_policy_versions`.
- The `documents` module: intake, extraction as a job, validation, routing, the review queue, spot checks.
- The review screen, a spot-check screen, and an AI accuracy panel on the existing Quality page.
- The Document migration (4 steps).
- Replacing the direct status endpoint for document items with a review decision endpoint.

## Non-goals
- New document types such as `visa`. The tier list includes them so adding one is a single config change, but they're seeded in Phase 5 (compliance rechecks).
- Moving files to object storage. Files stay on the uploads volume (`backend/uploads`). `documents.path` stays the reference.
- Changing the extraction prompt or model choice. `prompt_version` is recorded so a later change can be compared, but this phase doesn't tune anything.
- AwaitVerify (`extract.human_verify`). It stays optional. When it's on, its result counts as a *system* check before routing, never as the human decision for high-risk types.

## Design

### Routing decision
`documents/route.py: decide(check, policy, doc_type) -> Route` is **pure** and deterministic, and is unit-tested:

| Condition (evaluated in order) | Outcome | Human? |
|---|---|---|
| File checks failed (`validate.check_file` issues) | `Correction Requested` + correction email job | No: the joiner fixes it |
| Content issues (`validate.evaluate` issues: illegible, missing field, name/DOB mismatch, expired) | `Correction Requested` + correction email job | No |
| Extraction unavailable (`ai_unavailable`) | `Under Review`, `work_queue(kind='doc.review', reason='ai_unavailable')` | Yes |
| Classified type not on the joiner's checklist | `Under Review`, `work_queue(reason='unexpected_type')` | Yes |
| `tier == 'high'` | `Under Review`, `work_queue(reason='high_risk')` | **Always** |
| `tier == 'low'` and `confidence < auto_accept_min` | `Under Review`, `work_queue(reason='low_confidence')` | Yes |
| `tier == 'low'` and confidence ≥ threshold, no issues | `Accepted` (auto); `random() < spot_check_rate` → `work_queue(kind='doc.spot_check')` | Only the sample |

Notes:
- **Correction requests don't need a human.** The joiner gets a specific, deterministic list of problems, and that's routine work. If HR disagrees with a correction request, they can override it from the employee page, which is an audited `doc_decisions` row.
- **`validate.LOW_CONFIDENCE` (0.6) is replaced by the per-tier `auto_accept_min`.** `validate.evaluate()` stops deciding the status. It returns `{issues, mismatches, checks: [{name, ok, detail}]}`, and the status comes from `decide()`. This gets ADR 0001 ("the LLM perceives; rules decide") right: tiers and thresholds are rules too.
- **The spot-check sampling is reproducible:** `int(sha256(doc_id).hexdigest()[:8], 16) / 2**32 < rate`. An auditor can recompute which documents should have been sampled.

### Threshold policy (versioned)
The `doc_policy_versions.config` JSON looks like this:
```json
{"default": {"tier": "low", "auto_accept_min": 0.9},
 "spot_check_rate": 0.10,
 "types": {"photo_id": {"tier": "high"}, "i9_documents": {"tier": "high"},
           "address_proof": {"tier": "low", "auto_accept_min": 0.9}, "bank_details": {"tier": "low", "auto_accept_min": 0.92},
           "pan_card": {"tier": "low"}, "education_certificate": {"tier": "low"}, "experience_letter": {"tier": "low"},
           "w4_form": {"tier": "low"}},
 "extraction_deadline_minutes": 30}
```

**Validation on submit:**
- `auto_accept_min` must be in [0.5, 1.0], and `spot_check_rate` in [0, 1].
- A type in the hard-coded `ALWAYS_HIGH = {"photo_id", "i9_documents", "visa", "work_permit", "bgv_report"}` can't be set to `low`. This floor lives in code, so a policy edit can't remove a legal requirement. Changing it needs a code review and an ADR.

**Approval:**
- Lowering any threshold, raising `spot_check_rate` above 0.5, or changing any tier needs **2 approvals**. Anything else needs 1. This uses the same `approval_requests` path and author ≠ approver rule as Phase 1.
- **Preview:** this phase's preview is a backtest. It replays the last 90 days of `doc_decisions` (their stored `confidence`, `tier` and check results) through `decide()` with the draft policy, and reports how many decisions would change outcome. It also lists the spot-check failures that would have been auto-accepted. No live documents are touched.
- **Publishing** applies only to documents arriving afterwards. Documents already decided are never re-decided.

### Extraction as a job (LLM-down behaviour)
- `pipeline.intake()` is split in two:
  1. **`documents.service.receive()`** runs synchronously in the request. It saves the file, inserts the `documents` row (`status='Submitted'`), runs `check_file`, and enqueues `doc.extract` with the key `doc.extract:<doc_id>`. It returns straight away, so portal uploads and Gmail polling stop waiting on the LLM.
  2. **The `@handler("doc.extract")` job** calls `extract.extract()`, then `validate.evaluate()`, then `decide()`, then `apply_route()`.
- **On an exception**, which includes `llm.unavailable_reason()` being non-empty, the job is retried with backoff.
- **The deadline** is `received_at + extraction_deadline_minutes`. When the job runs after the deadline, or when it dead-letters, it routes to `ai_unavailable` instead of failing. The document is never stuck in `Submitted`.
- **`/ready`** (Phase 0) reports `llm: degraded` with `unavailable_reason()`, and the review queue header shows "AI extraction unavailable: documents are coming to review".

### Provenance
`doc_decisions` is the append-only history of every decision on a document. The current status lives on `documents.status`, which is written in the same transaction as the decision row. A decision row stores:
- `decided_by`: `'system'` or a user id
- `kind`: `auto_accept`, `auto_correction`, `routed_review`, `human_accept`, `human_reject`, `human_override`, `spot_check_pass` or `spot_check_fail`
- `reason_code` and `reason_text` (required for human decisions)
- `model`, `prompt_version`, `confidence`, `policy_version_id`, `checks` (the JSON list from `evaluate`), `extracted` (a snapshot)

Supporting changes:
- **`extract.py` gets `PROMPT_VERSION = "2026-11-01"`.** Bump it whenever the prompt text changes. A check in `test_phase2.py` hashes the prompt template and fails if the hash changed without a bump.
- **A spot-check fail does more than record a row.** It also re-opens the document (`status='Under Review'`, a new `doc.review` row with `reason='spot_check_failed'`), and it's counted in the AI accuracy metric.

## Schema
Migrations continue after Phase 1's `005`–`007`.

**`backend/migrations/008_documents.sql`**
```sql
CREATE TABLE doc_policy_versions (
  id                 bigserial PRIMARY KEY,
  number             int NOT NULL UNIQUE,
  state              text NOT NULL CHECK (state IN ('draft','pending_approval','approved','active','superseded','rejected')),
  config             jsonb NOT NULL,
  required_approvals int NOT NULL DEFAULT 1 CHECK (required_approvals IN (1,2)),
  backtest           jsonb,
  title              text NOT NULL,
  reason             text NOT NULL DEFAULT '',
  author_id          bigint NOT NULL REFERENCES users(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  published_at       timestamptz
);
CREATE UNIQUE INDEX one_active_doc_policy ON doc_policy_versions ((true)) WHERE state = 'active';

CREATE TABLE documents (
  id                text PRIMARY KEY,                 -- keeps graph ids (doc_…)
  employee_id       text NOT NULL REFERENCES employees(id),
  item_id           text REFERENCES items(id),        -- replaces (:Item)-[:HAS_DOC]->(:Document)
  filename          text NOT NULL,
  path              text NOT NULL,
  mime              text NOT NULL,
  size              int NOT NULL,
  sha256            text,                             -- set on receive; NULL for backfilled rows
  source            text NOT NULL,                    -- portal | email | integration
  status            text NOT NULL CHECK (status IN ('Submitted','Under Review','Accepted','Correction Requested','Rejected')),
  doc_type          text NOT NULL DEFAULT '',
  tier              text CHECK (tier IN ('low','high')),
  confidence        real NOT NULL DEFAULT 0,
  extracted         jsonb NOT NULL DEFAULT '{}',
  issues            jsonb NOT NULL DEFAULT '[]',
  model             text,
  prompt_version    text,
  policy_version_id bigint REFERENCES doc_policy_versions(id),
  received_at       timestamptz NOT NULL DEFAULT now(),
  decided_at        timestamptz,
  extra             jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX documents_employee ON documents (employee_id, received_at DESC);
CREATE INDEX documents_item ON documents (item_id, received_at DESC);
CREATE INDEX documents_status ON documents (status);

CREATE TABLE document_mismatches (                    -- replaces (:Document)-[:MISMATCH]->(:Employee)
  document_id text NOT NULL REFERENCES documents(id),
  field       text NOT NULL,
  expected    text NOT NULL,
  found       text NOT NULL,
  PRIMARY KEY (document_id, field)
);

CREATE TABLE doc_decisions (
  id                bigserial PRIMARY KEY,
  document_id       text NOT NULL REFERENCES documents(id),
  kind              text NOT NULL CHECK (kind IN ('auto_accept','auto_correction','routed_review','human_accept',
                                                  'human_reject','human_override','spot_check_pass','spot_check_fail')),
  decided_by        bigint REFERENCES users(id),      -- NULL = system
  reason_code       text NOT NULL,
  reason_text       text NOT NULL DEFAULT '',
  status_after      text NOT NULL,
  model             text,
  prompt_version    text,
  confidence        real,
  tier              text,
  policy_version_id bigint REFERENCES doc_policy_versions(id),
  checks            jsonb NOT NULL DEFAULT '[]',
  extracted         jsonb NOT NULL DEFAULT '{}',
  work_item_id      bigint REFERENCES work_queue(id),
  at                timestamptz NOT NULL DEFAULT now(),
  CHECK (kind NOT LIKE 'human_%' OR (decided_by IS NOT NULL AND length(reason_text) >= 3)),
  CHECK (kind NOT LIKE 'spot_check_%' OR decided_by IS NOT NULL)
);
CREATE INDEX doc_decisions_doc ON doc_decisions (document_id, at);
REVOKE UPDATE, DELETE ON doc_decisions FROM PUBLIC;   -- app role: INSERT/SELECT only (granted in migration)

-- Hard floor: a high-risk document can never be auto-accepted, whatever the app code does.
CREATE FUNCTION no_auto_accept_high() RETURNS trigger AS $$
BEGIN
  IF NEW.kind = 'auto_accept' AND NEW.tier = 'high' THEN
    RAISE EXCEPTION 'high-risk document % cannot be auto-accepted', NEW.document_id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER no_auto_accept_high BEFORE INSERT ON doc_decisions FOR EACH ROW EXECUTE FUNCTION no_auto_accept_high();
```

`work_queue` (from Phase 0) is used as it is, with these kinds:
- `doc.review`: `assignee_role='approver'`, `sla_due = least(item.due, now() + 2 days)`
- `doc.spot_check`: `assignee_role='approver'`, `sla_due = now() + 5 days`

`entity_type='document'`, `entity_id=<doc id>`.

## API
| Method & path | Purpose | Roles |
|---|---|---|
| `GET /api/review-queue?kind=doc.review|doc.spot_check&reason=&assignee=me|unassigned|all` | Open review items with the document summary, the employee, and SLA status | approver, hr_ops (read), auditor (read) |
| `POST /api/review-queue/{id}/claim` | Assign to me (stops two reviewers working on the same document) | approver |
| `GET /api/documents/{id}` | Document + extraction + checks + mismatches + profile values + decision history | approver, hr_ops, auditor; manager only for their own reports (Phase 6 scoping, read) |
| `GET /api/documents/{id}/file` | The file (unchanged route, now reads `documents.path`) | as above |
| `POST /api/documents/{id}/decision` | `{decision: 'accept'|'reject'|'request_correction', reason_code, reason_text, corrected_fields?}` → a `doc_decisions` row, document and item status, closes the work item, enqueues the correction email if one is needed, runs `checklist.recompute` and `on_documents_changed` | approver for `tier='high'`; approver or hr_ops for `low` |
| `POST /api/documents/{id}/override` | Reverse an automatic correction request or acceptance, with a reason (`human_override`) | approver |
| `POST /api/review-queue/{id}/spot-check` | `{verdict: 'pass'|'fail', reason_text}` | approver |
| `GET /api/doc-policy/versions`, `/{id}`, `/{id}/backtest` | Threshold policy versions and the backtest | approver, hr_ops, auditor |
| `POST /api/doc-policy/drafts`, `PUT /api/doc-policy/drafts/{id}`, `POST …/submit`, `POST /api/doc-policy/versions/{id}/approve|reject|rollback` | Same lifecycle as Phase 1 rule versions | drafts: hr_ops; approve/reject: approver (≠ author) |
| `GET /api/quality/ai-accuracy?days=90` | Spot-check precision, auto-accept rate, review rate by reason, median time to decision, by doc type and model | admin, auditor, approver |

`POST /api/items/{item_id}/status` (`main.py:set_item_status`) changes in two ways:
- For `kind='document'` items it returns **409**, pointing the caller to `/api/documents/{id}/decision`, so no document status changes without a decision row.
- `task` and `ack` items keep using it (`hr_ops`), with an audit row naming the actor.

## Screens

**Review queue** (`frontend/app/(hr)/review/page.tsx`, new nav item "Review", shown to approver, hr_ops and auditor)
- **Tabs:** Needs review · Spot checks · Done (last 7 days).
- **Rows:** joiner, document type, tier badge (High risk = rose, Low = slate), reason chip (`High risk`, `Low confidence 0.71 < 0.90`, `AI unavailable`, `Unexpected type`, `Spot check failed`), received time, SLA countdown (amber under 24h, red when overdue), and the assignee avatar.
- **Filters:** reason, document type, assigned to me / unassigned.
- **States:**
  - empty: "Nothing to review. Low-risk documents are being accepted automatically (n this week)."
  - loading: skeleton rows
  - a banner when `/ready` reports `llm: degraded`

**Review detail** (`frontend/app/(hr)/review/[id]/page.tsx`)
- **Left 60%:** the document viewer. It reuses the `<iframe>`/`<img>` preview from `employees/[id]/page.tsx` (`${API}/api/documents/${id}/file`), with zoom and page controls for PDFs.
- **Right 40%, top to bottom:**
  1. A **field table**: Field | On document | On profile | Check. A mismatch row is rose with a ✕; a missing field shows "not found".
  2. **Check results** from `checks` (legible, expiry versus joining date, required fields), each with a ✓ or ✕ and detail.
  3. **AI facts**: model, prompt version, confidence bar with the threshold marker, policy version.
  4. **Decision history**.
- **Sticky footer:**
  - **Accept**, **Request correction** (opens the issue list, pre-filled from `issues`, editable; this text goes into the correction email) and **Reject**.
  - A reason code select (`verified_against_original`, `name_variant_acceptable`, `document_expired`, `wrong_document`, `illegible`, `other`) plus a free-text reason, required (at least 3 characters).
  - Optional **Correct fields** for fixing a misread value; this is stored as `corrected_fields` on the decision.
- Claiming happens automatically when the page opens. If someone else has claimed it, the page is read-only and shows the banner "Being reviewed by sam@ (claimed 4 min ago)", with a **Take over** button after 30 minutes.

**Spot check** (the same detail layout)
- The footer is **Correct** / **Wrong** plus a reason.
- **Wrong** asks one more question, "What should it have been?" (accept / correction / reject), then re-opens the document for review.

**Employee page** (`frontend/app/(hr)/employees/[id]/page.tsx`)
- The document cards get a status badge with "auto-accepted · 0.94 · policy v2" or "accepted by sam@ · reason".
- The direct status dropdown for document items is replaced by **Open review**.

**Approvals page:** adds a **Thresholds** tab for pending `doc_policy_versions`, showing the diff of the config JSON as a table (type, tier, threshold old → new) and the backtest summary ("would have auto-accepted 12 more documents, 2 of which failed spot checks").

**Quality page** (`frontend/app/(hr)/quality/page.tsx`): adds an **AI decisions** card: auto-accept rate, spot-check precision with a 95% Wilson interval (labelled "n too small" when n < 30), review reasons as a bar chart, and median time to decision. It's split by document type and model.

## File-by-file changes

| File | Change |
|---|---|
| `backend/documents/__init__.py` | new |
| `backend/documents/route.py` | new. `ALWAYS_HIGH`, `tier_for(doc_type, policy)`, and the pure `decide(file_issues, check, extraction_ok, on_checklist, tier, confidence, threshold) -> Route(status, kind, reason_code, review, spot_check)`. `sampled(doc_id, rate)` |
| `backend/documents/service.py` | new. `receive(eid, filename, data, mime, source, item_key)` takes the first half of `pipeline.intake` (save, insert, `check_file`, enqueue). `@handler("doc.extract")` takes the second half. `apply_route()` writes the document, mismatches, decision and item status in one `tx()`, then the follow-up jobs. `decide_human()`, `spot_check()`, `override()` |
| `backend/documents/policy.py` | new. Threshold policy versions, using `policy/store.py`'s version helpers from Phase 1 with `kind='doc_policy'`. `backtest(draft, days=90)` |
| `backend/documents/routes.py` | new. The endpoints above |
| `backend/pipeline.py` | `intake()` becomes a thin wrapper, `return await documents.service.receive(...)`, so the callers (`gmail.poll`, `main.portal_upload`, `integrations/engine.py`) keep working. The return value changes from the full result to `{document_id, status: 'Submitted'}`. Callers that showed the result immediately (the portal upload toast) poll `GET /api/portal/{token}` instead; the portal already refreshes after an upload |
| `backend/validate.py` | `evaluate()` returns `{issues, mismatches, checks}` and no longer returns `status`. `LOW_CONFIDENCE` is removed. `check_file`, `names_match`, `norm_name` and `parse_date` are unchanged |
| `backend/extract.py` | adds `PROMPT_VERSION`. `extract()` is unchanged otherwise. `human_verify()` is called from the job **before** `decide()` for low-tier types only |
| `backend/gmail.py` | `send_correction()` is called from a `notify.correction` job (key `notify.correction:<doc_id>`) instead of inline. Phase 4 moves the rest of Gmail onto jobs |
| `backend/main.py` | `document_file()` reads `documents` through the bridge. `set_item_status()` returns 409 for document items. `_doc()` and `employee_detail()` add `tier`, `decision` and `decided_by` |
| `backend/integrations/engine.py` | line ~74 (`MATCH (:Item {id:$i})-[:HAS_DOC]->(d:Document)…`) becomes a bridge read of the latest document for the item. `on_documents_changed()` is unchanged |
| `backend/integration_routes.py` | `approvals()` keeps its candidate and offer items, and the frontend merges it with the review queue |
| `backend/quality_routes.py` | `GET /api/quality/ai-accuracy` |
| `backend/shared/bridge.py` | adds `documents` to `SOURCE` and `parity()` |
| `backend/graph.py` | `rebuild_graph(eid)` also projects `(:Document)`, `HAS_DOC`, `SUBMITTED` and `MISMATCH` from Postgres (the employee graph view shows them) |
| `frontend/app/(hr)/review/page.tsx`, `review/[id]/page.tsx` | new |
| `frontend/components/doc-viewer.tsx` | new. The preview code extracted from `employees/[id]/page.tsx` |
| `frontend/components/app-shell.tsx` | adds the "Review" nav item with an open-count badge (`min` role: approver/hr_ops/auditor) |
| `frontend/app/(hr)/employees/[id]/page.tsx`, `approvals/page.tsx`, `quality/page.tsx` | as described in Screens |
| `frontend/app/portal/[token]/page.tsx` | after an upload, shows "Received, checking now" and re-fetches until the status leaves `Submitted`, polling every 3s for up to 2 minutes and then showing "We'll email you if anything needs fixing" |

## Migration steps (Document)
1. **Dual-write.**
   - `documents.service` writes Postgres and, during this step, also creates the graph `Document` node plus `SUBMITTED`/`HAS_DOC`/`MISMATCH` edges through `bridge.write("documents", …)`.
   - `SOURCE["documents"] = "graph"`.
   - New `doc_decisions` rows are Postgres-only from the start (the table is new).
2. **Backfill.** `uv run python -m documents.backfill`:
   - Copies every graph `Document` into `documents`. `item_id` comes from the `HAS_DOC` edge; mismatches come from `MISMATCH` edges.
   - Computes `sha256` of each file when it's still on disk. A missing file is listed in the backfill report, never skipped silently.
   - Writes one `doc_decisions` row per backfilled document with `kind = 'auto_accept'` when its status is Accepted, otherwise `routed_review`; `reason_code='backfill'`; `decided_by = NULL`.
   - This gives every document a decision history, honestly labelled as backfill.
   - `tier` is set from the active policy, **except** that the trigger forbids `auto_accept` + `high`. Backfilled Accepted high-risk documents therefore get `kind='human_accept'` with `decided_by` = the bootstrap system user and `reason_text='backfill: accepted before Phase 2; reviewer not recorded'`, and they're also listed in the backfill report so HR can re-review if they want.
3. **Parity.** Row counts match, and a `sha256` checksum over `(id, employee_id, item_id, status, doc_type, confidence, filename)` sorted by id matches, plus the mismatch-row count. The bridge refuses the switch until `parity('documents').ok`. Then `SOURCE["documents"] = "postgres"`.
4. **Stop graph writes** for documents. The projection comes from `rebuild_graph(eid)`.

Rollback: before step 4, flip `SOURCE` back. After step 4, run `rebuild_graph()`, then flip back. `doc_decisions` stays in Postgres either way.

## Seed data
Bootstrap seeds this when missing:
- **Doc policy v1 `active`:** the config above.
- **One document in each state,** using small generated PNGs in `seed/docs/`:
  - Kabir: `photo_id` → `Under Review` (`high_risk`)
  - Meera: `address_proof` → `Accepted` (auto, 0.95), sampled for a spot check
  - Diya: `bank_details` → `Under Review` (`low_confidence` 0.72)
  - Rohan: `pan_card` → `Correction Requested` (DOB mismatch)
  - Aarav: `education_certificate` → `Accepted` by `approver@` with a reason
  - one document `Under Review` with `ai_unavailable`
- **Doc policy v2 `pending_approval`** by `hrops@`: lowers `bank_details` to 0.88, so it needs 2 approvals, with a backtest attached.

## Tests
`backend/test_phase2.py` (plain asserts, against `azentio_test`):

| Check | Asserts |
|---|---|
| `test_high_risk_never_auto` | `decide()` with tier `high`, confidence 1.0 and no issues → `Under Review`, review=True; plus inserting `doc_decisions(kind='auto_accept', tier='high')` raises (DB trigger) |
| `test_low_risk_threshold` | low tier at 0.95 → Accepted; at 0.89 → Under Review (`low_confidence`) |
| `test_issues_go_to_correction` | a name mismatch → `Correction Requested` + one `notify.correction` job; re-running the job doesn't add a second (idempotency key) |
| `test_always_high_floor` | submitting a policy draft that sets `photo_id` to `low` → 422 |
| `test_threshold_change_needs_two` | lowering a threshold → `required_approvals=2` |
| `test_human_decision_requires_reason` | a decision with an empty reason → 422, and a direct insert with no reason violates the CHECK |
| `test_high_risk_requires_approver` | `hr_ops` deciding a high-tier document → 403 |
| `test_spot_check_rate` | over 10,000 synthetic ids, `sampled(id, 0.1)` falls between 9% and 11%, and gives the same answer twice for the same id |
| `test_spot_check_fail_reopens` | a fail verdict → the document goes back to `Under Review` with a new `doc.review` work item |
| `test_llm_down_routes_to_review` | with `extract.extract` patched to raise, the job retries; after the deadline the document is `Under Review` with `reason_code='ai_unavailable'` and one work item; it's never left `Submitted` |
| `test_provenance_complete` | every non-human decision row has model, prompt_version, confidence, policy_version_id and checks set |
| `test_prompt_version_bumped` | the prompt template hash equals the hash recorded for the current `PROMPT_VERSION` |
| `test_backtest_counts` | a seeded decision history plus a draft at a lower threshold → the backtest reports the expected number of changed outcomes |
| `test_item_status_409_for_documents` | `POST /api/items/{doc item}/status` → 409 |
| `test_parity_documents` | parity passes after backfill; changing one row in Postgres only → `ok=False` |
| `test_claim_exclusive` | two approvers claiming the same item → one succeeds, the other gets 409 |

**Smoke:**
- `approver@` opens the review queue, opens Kabir's photo ID, accepts it with a reason, and the item shows Accepted on the employee page.
- `hrops@` opening the same review page gets read-only actions for high-risk documents.

## Exit criteria
- No path can auto-accept a high-risk document: the code (`decide`), the DB trigger and the tests all enforce it.
- Every document status change has a `doc_decisions` row with provenance. Human rows have a named user and a reason.
- An LLM outage for longer than `extraction_deadline_minutes` puts documents in review with `ai_unavailable`, and none stay in `Submitted`. This was tested by unsetting all LLM keys in dev.
- `parity('documents')` passes, `SOURCE` points at Postgres, and graph writes for documents have stopped.
- `test_phase2.py`, `test_phase1.py` and `test_core.py` pass, and the smoke test passes.
- The Quality page shows spot-check precision. The docs (LLD document flow and ADR 0010) match the code.

## Risks & rollback
| Risk | Mitigation |
|---|---|
| More documents go to humans, and HR is swamped | Only the high-risk types (2 in the seed) always need review. The low-risk threshold is tunable through governed policy. The queue shows the reason mix on the Quality page, so a change can be justified by data |
| An async extraction is slower for the joiner | Most extractions finish in seconds. The portal polls and shows progress, and the email reply flow was always asynchronous |
| Two reviewers decide the same document | The claim lock (`work_queue.assignee_id` set with `UPDATE … WHERE assignee_id IS NULL`), plus `test_claim_exclusive` |
| The backfill mislabels history | Backfilled rows are labelled `reason_code='backfill'`, and high-risk ones are listed for optional re-review. Nothing is claimed that didn't happen |
| A missing file on disk during backfill | It's reported; the row is still created with `sha256 NULL`; the review screen shows "file missing" |
| A spot-check sample that's too small to mean anything | The UI shows n and the confidence interval, and labels n < 30 as too small to judge |
| **Rolling back the phase** | Flip `SOURCE["documents"]` back (or `rebuild_graph()` first if past step 4). Revert `pipeline.intake` to synchronous; it's one function. `doc_decisions` and the policy versions stay, so no decision history is lost |

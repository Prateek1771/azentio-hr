# ADR 0012: Keep document thresholds in rule-set versions, behind a legal floor, with append-only decisions

- **Status:** accepted
- **Date:** 2026-11-02
- **Deciders:** product owner, engineering (Phase 2 review)

## Context
[ADR 0010](0010-risk-tiered-ai-autonomy.md) set the policy: tiers, versioned thresholds, a 10% spot-check sample, and full provenance. The Phase 2 plan proposed a separate `doc_policy_versions` table with its own draft, approve and rollback lifecycle. Phase 1 had already built that lifecycle for rule-set versions ([ADR 0009](0009-governed-policy-versions.md)), including the screens, two-approver rules, history and rollback.

Three decisions remained open:
- **Where the thresholds live.**
- **What stops a policy edit from removing a legal requirement.**
- **How a decision stays the single source of truth.** A decision has to win over a status someone sets directly, and over a job that retries.

## Decision
1. **Thresholds live inside the rule-set version**, as `config.doc_policy`: tiers, `auto_accept_min`, `spot_check_rate` and `extraction_deadline_minutes`.
   - They go through the same flow as rules: draft, preview, approve, publish, rollback and history.
   - Lowering a threshold, changing a tier, or sampling fewer acceptances needs two approvers.
   - The preview adds a pure 90-day backtest over stored decisions.
   - Published thresholds apply only to documents that arrive afterwards.
2. **A legal floor that no policy edit can lower.**
   - `documents.route.ALWAYS_HIGH` (photo ID, I-9, visa, work permit, BGV) is enforced in the router and refused at the API (422).
   - The database refuses any `auto_accept` decision with `tier = 'high'` (trigger `no_auto_accept_high`).
   - Changing the floor needs a code review and a new ADR.
3. **Decisions are authoritative.**
   - `doc_decisions` is append-only: a trigger refuses UPDATE and DELETE.
   - `documents.current_decision_id` points at the one active decision, and `documents.status` is that decision's result.
   - A document item's status is derived from it through `documents.service.item_status_for` and is never set directly; `POST /api/items/{id}/status` returns 409 for document items.
   - **Hardening (migration 010):** the database enforces the derivation too. Trigger `items_status_from_document` lets a document item change status only to `Not Submitted`, `Cancelled`, or a status some document of that item maps to (`doc_item_status` mirrors `item_status_for`). Demo data is recorded as decision kind `demo_seed` through `documents.seed.accept_for_demo`, never as an AI or human decision, and is excluded from metrics.
   - Every write locks the document and checks the state it expects. An idempotency key on each decision (`doc.extract:<id>:route`, or the client's `decision_key`) turns a retry or a double click into a no-op.
4. **The AI's role ends at evidence.**
   - `extract` returns facts and a confidence score, and `validate.evaluate` returns checks; neither returns a status.
   - The pure `route.decide` picks the outcome. `ai_unavailable` is a first-class route, taken after the extraction retries or the governed deadline.
   - AwaitVerify, when enabled, is an input check for low-tier documents only.

## Consequences
- **Easier:**
  - HR learns one governance flow.
  - The approvers, history, rollback and audit trail cover thresholds without new code.
  - The legal floor holds even against a bug in application code.
- **Harder:**
  - Thresholds and rules share one open-draft slot. That's acceptable because the one-draft limit is deliberate.
  - The rule-set config is now frozen after draft by a trigger (`rule_set_config_frozen`), the same as its rules.
- **Watch:**
  - Review-queue SLA breaches.
  - Spot-check precision: the Quality page shows the Wilson interval and labels it "n too small" when n < 30.
  - Revisit if thresholds need a release cadence independent of rules.

## Alternatives considered
| Option | Why not |
|---|---|
| A separate `doc_policy_versions` table and API (the original Phase 2 plan) | Duplicates Phase 1's lifecycle and screens; a second governance flow for HR to learn |
| The floor only in policy data | An approved edit could remove a legal requirement |
| Item status set directly alongside the decision | Two sources of truth; a later status write could contradict the recorded decision |

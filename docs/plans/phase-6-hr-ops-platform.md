# Phase 6: HR ops platform

- **Status:** planned
- **Depends on:**
  - Phase 0: identity with the `manager` role, audit, `work_queue`
  - Phase 1: Employee and Item in Postgres, governed policy
  - Phase 2: review decisions and the 10% spot-check sample
  - Phase 4: jobs, outbox, recurring jobs, `escalation.check`, the Temporal worker and the `temporal.start`/`temporal.signal` relay
  - Phase 5: lifecycle cases, `employee_history`
- **Design:** [2026-11-01-hardening-design.md](2026-11-01-hardening-design.md) · **ADR:** [0011 Temporal orchestration](../adr/0011-temporal-orchestration.md)

## Goal
Turn the workflow tool into something an HR operations team runs on day to day:
- managers see and comment on their own people, and nothing else
- every kind of work has an SLA
- late work escalates up a ladder by itself
- exceptions and appeals have one governed path
- leaders can see where onboarding is slow and how much to trust the AI

Same operating principle. The system watches the clock and escalates. People act only when a work item lands in their queue, and every queue item has an owner, a deadline and a resolution.

## Scope
1. **Manager view:** scoped to the manager's own reports, with comments on employees, items and cases.
2. **SLAs** per item type (and per `work_queue` kind), versioned as policy.
3. **Escalation ladder:** owner → owner's manager → HR lead. It runs as one Temporal workflow per SLA clock, using durable timers, and each step is recorded in Postgres.
4. **Exceptions and appeals:** waivers, extensions, overrides and joiner appeals, all as `work_queue` items with approval, using the Phase 1 rule that the requester can't approve their own request.
5. **Analytics:**
   - overdue heatmap
   - time-to-ready
   - exception rate
   - SLA attainment
   - AI accuracy from the Phase 2 spot checks
6. One unified **Inbox** for every `work_queue` item assigned to the user or their role.

## Non-goals
- Free-form org-chart management. The reporting line is `employees.manager_email`, plus `owners` rows with a `manager_email` column added below. A real org chart comes from an HRIS connector later.
- Business-hours or holiday calendars for SLAs. SLAs count in calendar hours this phase. The column for a calendar id exists so business hours can be added without a migration.
- Notifications beyond email and in-app (no Slack DMs or push). The outbox `channel` can grow later.
- A BI tool or data warehouse. Analytics are SQL views over Postgres, read by the app.
- Managers editing records. They comment, acknowledge, and complete items they own (e.g. handover); everything else stays with HR Ops.

## Data model

```sql
-- 040_people_lines.sql
ALTER TABLE owners ADD COLUMN manager_email text;          -- owners.csv gains a column; owner → their manager for the ladder
ALTER TABLE users  ADD COLUMN employee_email text;         -- links a manager login to the people they manage (manager_email match)
CREATE INDEX employees_manager ON employees (lower(manager_email));

-- 041_sla.sql
CREATE TABLE sla_policies (                                  -- versioned like rules: rows belong to a policy version
  id              bigserial PRIMARY KEY,
  version_id      bigint NOT NULL REFERENCES rule_versions(id),
  target_kind     text NOT NULL CHECK (target_kind IN ('item','work_queue')),
  match           jsonb NOT NULL DEFAULT '{}',               -- item: {kind, category, key, case_type}; work_queue: {kind}
  respond_hours   int,                                       -- first touch (assignment/acknowledge/comment)
  resolve_hours   int NOT NULL,                              -- done
  anchor          text NOT NULL DEFAULT 'created'            -- created | due   (items with a rule due date anchor to 'due')
                  CHECK (anchor IN ('created','due')),
  ladder          jsonb NOT NULL DEFAULT '[]',               -- [{after_hours, to: owner|owner_manager|employee_manager|hr_lead|role:<r>}]
  calendar_id     text,                                       -- reserved; null = calendar hours
  priority        int NOT NULL DEFAULT 100                    -- most specific match wins; ties → lowest priority value
);

CREATE TABLE sla_clocks (                                     -- one live clock per tracked thing
  id              bigserial PRIMARY KEY,
  target_kind     text NOT NULL,
  target_id       text NOT NULL,
  policy_id       bigint NOT NULL REFERENCES sla_policies(id),
  started_at      timestamptz NOT NULL,
  respond_by      timestamptz,
  resolve_by      timestamptz NOT NULL,
  responded_at    timestamptz,
  resolved_at     timestamptz,
  paused_seconds  int NOT NULL DEFAULT 0,                    -- leave pauses (Phase 5) stop the clock
  paused_at       timestamptz,
  breached        boolean NOT NULL DEFAULT false,
  UNIQUE (target_kind, target_id)
);
CREATE INDEX sla_open ON sla_clocks (resolve_by) WHERE resolved_at IS NULL;

CREATE TABLE escalations (                                    -- one row per ladder step taken; the ladder's audit trail
  id              bigserial PRIMARY KEY,
  clock_id        bigint NOT NULL REFERENCES sla_clocks(id),
  step            int NOT NULL,
  to_kind         text NOT NULL,                             -- owner | owner_manager | employee_manager | hr_lead | role
  to_addr         text NOT NULL,
  outbox_key      text NOT NULL,                             -- links to the email sent
  at              timestamptz NOT NULL DEFAULT now(),
  UNIQUE (clock_id, step)                                    -- a step fires once, ever
);

-- 042_comments.sql
CREATE TABLE comments (
  id              bigserial PRIMARY KEY,
  entity_type     text NOT NULL CHECK (entity_type IN ('employee','item','case','work_queue','document')),
  entity_id       text NOT NULL,
  employee_id     text NOT NULL REFERENCES employees(id),    -- denormalised for scoping and the employee feed
  author_id       text NOT NULL,
  body            text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
  visibility      text NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal','hr_only')),
  mentions        text[] NOT NULL DEFAULT '{}',              -- user ids; each gets an in-app notification
  created_at      timestamptz NOT NULL DEFAULT now(),
  edited_at       timestamptz,
  deleted_at      timestamptz                                -- soft delete; body kept in audit
);
CREATE INDEX comments_entity ON comments (entity_type, entity_id, created_at);
CREATE INDEX comments_employee ON comments (employee_id, created_at DESC);

CREATE TABLE notifications (                                  -- in-app bell; email copies go through the outbox
  id              bigserial PRIMARY KEY,
  user_id         text NOT NULL,
  kind            text NOT NULL,                             -- mention | assigned | escalated | decision | sla_breach
  title           text NOT NULL,
  link            text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  read_at         timestamptz
);
CREATE INDEX notifications_user ON notifications (user_id, read_at, created_at DESC);

-- 043_exceptions.sql  (work_queue from Phase 0 gains the request/decision fields exceptions need)
ALTER TABLE work_queue ADD COLUMN requested_by text;           -- null for system-raised items
ALTER TABLE work_queue ADD COLUMN request jsonb NOT NULL DEFAULT '{}';
   -- waiver:    {item_id, justification, expires_on?}
   -- extension: {item_id, new_due, justification}
   -- override:  {document_id, from_status, to_status, justification}
   -- appeal:    {item_id|document_id, joiner_message, attachments[]}
ALTER TABLE work_queue ADD COLUMN approvals_required int NOT NULL DEFAULT 1;
ALTER TABLE work_queue ADD COLUMN decision text CHECK (decision IN ('approved','rejected','withdrawn'));
ALTER TABLE work_queue ADD COLUMN decided_at timestamptz;
CREATE TABLE work_queue_approvals (
  work_queue_id   bigint NOT NULL REFERENCES work_queue(id),
  approver_id     text NOT NULL,
  decision        text NOT NULL CHECK (decision IN ('approve','reject')),
  reason          text NOT NULL CHECK (length(reason) >= 5),
  at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (work_queue_id, approver_id)
);

-- 044_analytics_views.sql
CREATE VIEW v_item_facts AS                                   -- one row per item with timing facts; every chart reads from here
SELECT i.id, i.employee_id, i.case_id, c.type AS case_type, i.kind, i.category, i.key, i.status,
       e.department, e.location, e.worker_type, e.manager_email,
       i.created_at, i.due::date AS due, i.done_at,
       (i.done_at IS NULL AND i.due::date < current_date AND i.paused_at IS NULL) AS overdue_now,
       (i.done_at IS NOT NULL AND i.done_at::date > i.due::date) AS done_late,
       s.breached AS sla_breached
FROM items i JOIN employees e ON e.id = i.employee_id
LEFT JOIN lifecycle_cases c ON c.id = i.case_id
LEFT JOIN sla_clocks s ON s.target_kind = 'item' AND s.target_id = i.id;

CREATE VIEW v_time_to_ready AS                                -- onboarding cases: open → completed, and vs joining date
SELECT c.id AS case_id, c.employee_id, e.department, e.location, e.worker_type,
       c.opened_at, c.closed_at, c.effective_date AS joining_date,
       EXTRACT(EPOCH FROM (c.closed_at - c.opened_at))/86400.0 AS days_to_ready,
       (c.closed_at::date <= c.effective_date) AS ready_by_day_one
FROM lifecycle_cases c JOIN employees e ON e.id = c.employee_id
WHERE c.type = 'onboarding' AND c.state = 'completed';

CREATE VIEW v_ai_accuracy AS                                  -- Phase 2 spot checks: human verdict vs AI auto-accept
SELECT date_trunc('week', r.decided_at) AS week, d.doc_type,
       count(*) AS sampled,
       count(*) FILTER (WHERE r.decision = 'confirm') AS ai_correct,
       count(*) FILTER (WHERE r.decision = 'overturn') AS ai_wrong
FROM review_decisions r JOIN documents d ON d.id = r.document_id
WHERE r.source = 'spot_check'
GROUP BY 1, 2;
```

`items` already has `created_at`, and gains `done_at` in Phase 1. If it hasn't by then, 040 adds it, and `checklist.recompute` and the status setter fill it in.

## SLAs and the escalation ladder

### Defaults (seeded as the first SLA policy version, published through the governed flow)
| Target | Respond | Resolve | Anchor | Ladder |
|---|---|---|---|---|
| Item, kind `task`, category IT / Facilities | 8 h | due date | due | +0 h owner, +24 h owner_manager, +72 h hr_lead |
| Item, kind `task`, category Compliance (BGV) | 8 h | due date | due | +0 h owner, +24 h hr_lead, +48 h role:admin |
| Item, kind `document` | (none) | due date | due | +0 h the joiner (reminder through the outbox), +48 h employee_manager, +96 h hr_lead |
| Item, offboarding `access_revoke` | (none) | 4 h after effective | due | +0 h role:admin, +2 h hr_lead |
| work_queue `doc_review` (high-risk) | 4 h | 24 h | created | +24 h role:approver (all approvers), +48 h hr_lead |
| work_queue `rule_approval` | 24 h | 72 h | created | +72 h hr_lead |
| work_queue `appeal` | 24 h | 72 h | created | +72 h hr_lead |
| work_queue `ops` | 1 h | 8 h | created | +1 h role:admin, +8 h hr_lead |
| work_queue `lifecycle_gate` (protected window) | 2 h | 12 h | created | +12 h hr_lead |

`hr_lead` resolves from `HR_LEAD_EMAIL`, or the `owners` row with `type='hr_lead'`. `owner_manager` comes from `owners.manager_email`, and `employee_manager` from `employees.manager_email`. If a ladder target can't be resolved, that step falls through to the next one, and the fallthrough is recorded in `escalations.to_kind`.

### Mechanics
- **The clock starts** in the same `tx()` that creates the item or `work_queue` row. `sla.start(target_kind, target_id)` resolves the most specific matching policy from the *currently published* SLA version.
- **The clock responds** on the first assignment, comment or acknowledgement by a human, and **resolves** when the item is done or the work_queue item is decided.
- **Paused items** (Phase 5 leave) pause the clock: `paused_at`, then add the paused time back on resume.
- **One `EscalationWorkflow` per clock, using timers.** It replaces the Phase 4 `escalation.check` sweep.
  - `sla.start` writes the clock row and enqueues `temporal.start` with key `sla:<clock id>` in the same `tx()`.
  - The workflow reads the ladder through the activity `sla_due_steps`, then for each step **sleeps until `anchor + after_hours`** (`workflow.sleep`, which survives restarts and deploys).
  - When it wakes, it runs the activity `escalate_step(clock_id, step)`. That inserts into `escalations` and enqueues `email.send` with key `email:escalation:<clock id>:<step>`, plus a `notifications` row for in-app users.
- **Each step fires once, ever.** Two things guarantee it:
  - The workflow id is deterministic (`sla:<clock id>`), so there's never a second ladder for the same clock.
  - `UNIQUE (clock_id, step)` on `escalations` means a retried or replayed `escalate_step` is a no-op.
- **Signals.** `respond`, `resolve`, `pause` and `resume` (from `sla.service`) write Postgres first, then send `temporal.signal` through the outbox in the same `tx()`. The workflow re-reads the clock through an activity:
  - **resolve:** the workflow ends.
  - **pause:** the timers stop, and on resume they're rescheduled with the paused time added back.
  - **due date change** (an extension): the remaining timers are rescheduled.
- **Safety net.** The daily `sla.reconcile` job enqueues `temporal.start` for any open clock with no running workflow. It's keyed, so it does nothing when all is well.
- **Breach:** `resolve_by` passed and not resolved → `breached = true`, recorded once with an audit row `sla.breach` and an analytics counter.
- **Email content:** the existing `escalation` email template, extended with the ladder context ("Second reminder; this was due 2 days ago and is blocking: BGV, laptop"). The blocking list is the same `blocked_by` calculation `main.escalate` uses today, through `checklist.view_state`.
- **Delegation:** an owner can set an out-of-office delegate (`users.delegate_id`, `delegate_until`). Ladder step 0 then goes to the delegate and records that it did.

## Exceptions and appeals
All four kinds are `work_queue` rows. They share one inbox, one decision API and one audit trail.

| Kind | Raised by | Approvals | Effect on approve |
|---|---|---|---|
| `waiver` (don't require this item for this person) | hr_ops, manager (own reports) | 1 approver, **2 if the item's rule is compliance-flagged**; requester excluded | item → `Waived` (counts as done, shown distinctly), reason on the item, expiry optional (it comes back as a recheck after `expires_on`) |
| `extension` (move a due date) | hr_ops, manager, item owner | 1 approver; extensions of ≤ 3 days on non-compliance items auto-approve (system actor, still recorded) | `due` updated, SLA clock `resolve_by` moved, `case_events.due_shifted` |
| `override` (change an AI or human document decision) | hr_ops, approver | 1 approver other than the original decider; **2** for high-risk document types | document status changed; `review_decisions` row with `source='override'`; if it overturns an auto-accept, it counts as AI wrong in `v_ai_accuracy` |
| `appeal` (joiner disputes a correction request or rejection) | joiner, from the portal ("This is correct, please review") | 1 approver who did *not* make the original decision | same as override, plus an email to the joiner through the outbox with the outcome |

- **Decisions** go through `exceptions.service.decide(wq_id, actor, decision, reason)`. It enforces that the requester ≠ the approver and that an approver can't approve twice (primary key). Each decision also writes an audit entry and a notification to the requester.
- **Waiting for approval is a workflow.** Raising an exception or appeal enqueues `temporal.start` for `ExceptionWorkflow` with key `exception:<wq_id>`, in the same `tx()` as the `work_queue` row.
  - Each `decide` writes `approvals` and then sends the `decision` signal through the outbox.
  - The workflow waits for `approvals_required` approvals by re-reading them through an activity, so a duplicate signal can't count twice. Any single reject closes the item as rejected.
  - Once approved, it runs the per-kind effect as an activity: waive the item, shift the due date, change the document decision, send the appeal outcome email.
  - Its timer is the item's SLA, so a stale appeal escalates through the ladder above instead of being forgotten.
  - Approval state stays in `approvals` and `work_queue`, so the decision history is complete even without Temporal.
- **Withdraw:** the requester can withdraw a request while it's open.
- **Rate limit on appeals:** one open appeal per item per joiner. A rejected appeal can't be re-appealed for 24 h (409 with the reason).

## Manager view

### Scoping, enforced on the server
- A `manager` user sees an employee if and only if `lower(employees.manager_email) = lower(users.employee_email)`, either now or within the last 30 days (from `employee_history`) so a handover still works.
- This is one SQL predicate, `scope.visible_employees(user)`, applied in the employee list, detail, cases, comments and analytics queries. It's never trusted from the client.
- The Phase 0 perms table says *which routes* a manager can call; `scope` says *which rows*. A test hits every manager-allowed route with another manager's employee id and expects 404, not 403, so the response doesn't reveal that the employee exists.
- Sensitive offboarding cases (Phase 5) are hidden until their effective date.

### What a manager can do
- See their people's onboarding and lifecycle progress, items, due dates and SLA state.
- Comment (internal visibility), and @mention HR Ops users.
- Complete items they own (handover, return-to-work meeting).
- Request a waiver or extension for their reports.
- **Can't:** see documents' content (only status and type, because personal data is minimised), see other employees, change records, or approve anything about their own reports. A manager can't also be an approver for their own team, enforced in `decide`.

## API endpoints
| Method & path | Purpose | Roles |
|---|---|---|
| `GET /api/me/team` | manager's visible employees with readiness, overdue and SLA counts | manager (scoped); admin/hr_ops can pass `?manager=<email>` |
| `GET /api/employees/{eid}/comments` | comment thread (all entities for that employee) | admin, hr_ops, approver, auditor; manager scoped; `hr_only` comments hidden from manager |
| `POST /api/comments` | `{entity_type, entity_id, body, visibility, mentions}` | admin, hr_ops, approver; manager scoped (internal only) |
| `PATCH /api/comments/{id}` / `DELETE` | edit within 15 min / soft delete, author only | author |
| `GET /api/inbox?status=open&kind=` | `work_queue` assigned to me or my role, with SLA due | every signed-in role (each sees their own) |
| `POST /api/work-queue/{id}/claim` | assign to self (stops the respond clock) | roles allowed for that kind |
| `POST /api/exceptions` | `{kind: waiver|extension|override, request}` | per the table above |
| `POST /api/portal/{token}/appeal` | `{item_id|document_id, message, file?}` | joiner (token) |
| `POST /api/work-queue/{id}/decide` | `{decision: approve|reject, reason}` | approver, hr_ops (only for kinds they're allowed); admin |
| `POST /api/work-queue/{id}/withdraw` | withdraw own request | requester |
| `GET /api/sla/policies` / `POST /api/sla/drafts` … | SLA policy versions through the same draft/preview/approve endpoints as rules (Phase 1), with `target='sla'` | read: admin, hr_ops, approver, auditor; draft: hr_ops; approve: approver |
| `GET /api/employees/{eid}/escalations` | ladder history | admin, hr_ops, approver, auditor; manager scoped |
| `GET /api/notifications` / `POST /api/notifications/read` | in-app bell | own |
| `GET /api/analytics/overdue-heatmap?dim=department&period=week&from=&to=` | matrix of dimension × week → overdue count and rate | admin, hr_ops, approver, auditor; manager (own team only) |
| `GET /api/analytics/time-to-ready?group=department` | p50/p90 days to ready, % ready by day one | same |
| `GET /api/analytics/exceptions?group=kind` | exception rate per 100 joiners, approval rate, mean decision time | admin, hr_ops, approver, auditor |
| `GET /api/analytics/sla` | attainment % by target kind and category, breach list | admin, hr_ops, approver, auditor |
| `GET /api/analytics/ai-accuracy?doc_type=` | weekly sampled / correct / wrong, with a Wilson 95% interval | admin, hr_ops, approver, auditor |

Every analytics endpoint takes the same filters (department, location, worker_type, case_type, from, to) and goes through `scope`, so a manager's analytics cover their team only.

## Screens

### Inbox (`frontend/app/(hr)/inbox/page.tsx`, new): the default landing page for every role except auditor
- **Left column:** filter chips by kind (Reviews, Approvals, Exceptions, Appeals, Ops, Gates) with counts; "Assigned to me" vs "My role".
- **List:** each row shows a kind icon, title, employee, an SLA countdown pill (green over 50% of the time left, amber, red when breached, showing "2 h over"), and the requester.
- **Detail panel:**
  - context: the item and employee summary, the document preview for reviews and overrides, the request justification
  - the comment thread
  - decision buttons (Approve or Reject, reason required)
  - approvals so far ("1 of 2 approvals: Priya, 2 h ago")
  - Withdraw for the requester
  - Disabled with an explanation when the user is the requester or already approved: "You raised this request, so someone else needs to approve it".
- **States:**
  - Empty: "You're all caught up. New reviews and requests assigned to you appear here."
  - Loading: skeleton.
  - Error: inline retry.
- The dashboard's "Needs review" tile and the Phase 2 approvals page link here with a filter.

### My team (`frontend/app/(hr)/team/page.tsx`, new): manager (and admin or hr_ops when viewing a manager)
- Cards per report: avatar, role, status badge, readiness bar, next due item, an "N overdue" chip, and the latest comment snippet.
- Click-through opens the employee record in **manager mode**: no document images (only status and type), no edit actions, the comment box, "Request extension/waiver" on items, and "Mark done" only on items the manager owns.
- Empty state: "No one reports to you in Azentio yet. If that's wrong, ask HR to check the manager email on your team's records."

### Employee record additions (`employees/[id]/page.tsx`)
- A **Comments** side panel, toggled in the header, with the thread across every entity, entity chips ("on BGV item"), @mention autocomplete of HR users, and an `hr_only` toggle for HR roles.
- Each item row gets an SLA pill and an escalation history tooltip ("Escalated to Priya Menon (owner's manager) 1 Nov 10:05").
- An item's "…" menu adds Request waiver and Request extension, with a dialog for justification and the new date.

### Portal (`portal/[token]/page.tsx`)
- On a correction request or rejected document: an "I think this is correct" button opens the appeal form (message plus optional re-upload). The appeal's status is shown inline.

### Analytics (`frontend/app/(hr)/analytics/page.tsx`, new): admin, hr_ops, approver, auditor; manager sees a team-scoped version
- **Filter bar:** date range, department, location, worker type, case type.
- **Overdue heatmap:** department × week, cells shaded by overdue rate (sequential palette, readable in light and dark). Hover shows the count and rate; clicking opens the dashboard filtered to that department and week.
- **Time-to-ready:** a box/strip plot per department of `days_to_ready`, plus a "% ready by day one" stat tile.
- **SLA attainment:** a bar per target category with the breach count; a click lists the breaches.
- **Exceptions:** a rate per 100 joiners by kind, a 12-week trend line, and approval rate.
- **AI accuracy:** a weekly line of AI-correct % on sampled auto-accepts with the 95% interval band, per document type. If a document type has fewer than 20 samples, it reads "Not enough samples yet (n=7)" instead of a misleading percentage.
- Charts are inline SVG built in-house, following the existing visual style. A chart library is added only if one is already in `package.json` by then; there isn't one today.
- **States:**
  - Each chart has its own skeleton and "No data for these filters".
  - An analytics query error shows on that chart only, not the whole page.

### SLA policies (`frontend/app/(hr)/rules/page.tsx` gets an "SLAs" tab)
- Reuses the Phase 1 draft → preview → approve UI. Here the preview shows which open items would change `resolve_by`, and which would become breached immediately. Publishing a version that would breach more than 10 open items at once needs a second approver.

### App shell
- A notification bell with an unread count; the dropdown lists the last 20 with links.
- Nav: Inbox (everyone), My team (manager), Analytics (as above).

## File-by-file changes
| File | Change |
|---|---|
| `backend/sla/service.py` (new) | `match_policy(target_kind, attrs)`, `start()` (also enqueues `temporal.start` `sla:<clock id>` in the same `tx()`), `respond()`, `resolve()`, `pause()`, `resume()` (each also enqueues the matching `temporal.signal`), `due_steps(clock, now)` (pure: the ladder steps due, with self-checks), and `resolve_target(to_kind, item, employee)`. |
| `backend/workflows/sla.py` (new) | `EscalationWorkflow(clock_id)` (timers per ladder step; signals `changed`/`resolved`) and `ExceptionWorkflow(wq_id)` (waits for the `decision` signals, applies the effect). Deterministic: no I/O, `workflow.now()` only. |
| `backend/workflows/activities.py` | Adds `sla_due_steps`, `escalate_step` (insert into `escalations` with `ON CONFLICT (clock_id, step) DO NOTHING`, then `queue_email`), `read_clock`, `count_approvals`, `apply_exception_effect`. |
| `backend/jobs_handlers/sla.py` (new) | `sla.reconcile` (daily safety net, as described). The Phase 4 `escalation.check` is deleted and its recurring registration removed. |
| `backend/exceptions/service.py` and `routes.py` (new) | `raise_exception`, `decide`, `withdraw`, the effects per kind (calling `checklist`/`lifecycle`/`documents` services, never touching their tables directly), and auto-approval of short extensions. |
| `backend/collab/service.py` and `routes.py` (new) | Comments, mention parsing (`@email` resolved against users), and notifications (in-app plus an email copy for mentions through the outbox, keyed `email:mention:<comment id>:<user>`). |
| `backend/identity/scope.py` (new) | `visible_employees(user) -> SQL fragment, params` and `assert_visible(user, eid)` raising 404. Called by every employee-scoped query in `main.py`, `lifecycle/routes.py` and `analytics/`. |
| `backend/identity/perms.py` | Rows for every new route. Manager gains `GET /api/me/team`, comments, and exceptions POST (waiver/extension only). |
| `backend/analytics/service.py` and `routes.py` (new) | SQL over the views, with scope applied, and a Wilson interval helper (pure, tested). |
| `backend/main.py` | Employee list and detail go through `scope`. `items_with_state` adds the SLA state. `set_item_status` stops the SLA clock and sets `done_at`. Remove the old `escalate()` body; the route now enqueues `sla.reconcile`, which starts any missing ladder workflows. |
| `backend/checklist.py` | `view_state` returns `waived` for the `Waived` status, which counts as done in `DONE` for dependencies. |
| `backend/documents/review.py` (Phase 2 module) | Overrides and appeals call `review.record_decision(source='override'|'appeal')`. Spot-check decisions use `source='spot_check'` and a `decision` of `confirm` or `overturn`, which `v_ai_accuracy` reads. |
| `backend/seed/owners.csv` | Add a `manager_email` column and an `hr_lead` row (Anita Desai). |
| `backend/seed/` (dev bootstrap) | Seed users `manager@` linked to `priya.menon@azentio.example`, so the manager sees Aarav, Diya, Rohan and the other Engineering joiners. |
| `backend/migrations/040–044_*.sql` | As above. |
| `frontend/app/(hr)/inbox/page.tsx`, `team/page.tsx`, `analytics/page.tsx` (new) | Screens. |
| `frontend/components/comments-panel.tsx`, `sla-pill.tsx`, `notification-bell.tsx`, `charts/heatmap.tsx`, `charts/strip-plot.tsx`, `charts/line-band.tsx` (new) | Shared UI. |
| `frontend/app/(hr)/employees/[id]/page.tsx` | Comments panel, SLA pills, waiver/extension actions, manager mode via `useMe().role`. |
| `frontend/app/portal/[token]/page.tsx` | Appeal button and status. |
| `frontend/components/app-shell.tsx` | Bell, nav items, and landing redirect (`/` → `/inbox` for every role except auditor, `/dashboard` for auditor). |

## Migration steps
1. Apply 040–044 (additive).
2. Backfill `owners.manager_email` from the seed CSV. In production, admins fill it on the Users and owners screen; missing values make the ladder fall through to `hr_lead`, so it's safe with gaps.
3. Publish SLA policy v1 through the governed flow. The preview shows how many open items would already be breached (expected: the currently overdue ones). The approver accepts that knowingly.
4. Backfill `sla_clocks` for every open item and `work_queue` row, from their creation time under v1. Rows that are already breached get `breached=true` **without** firing past ladder steps: the first tick sends only the *current* step, so turning SLAs on doesn't flood inboxes with historic escalations. This is done by pre-inserting the earlier `escalations` rows marked `to_kind='backfill_skipped'`.
5. Register `EscalationWorkflow` with `ESCALATION_DRY_RUN=true`: `escalate_step` logs and records `escalations` rows marked `dry_run` without sending email. Run it for one day, compare against `escalation.check`, then switch to live and remove `escalation.check` in the same deploy, so only one escalation path runs. Backfilled clocks get their workflows from `sla.reconcile`.
6. Ship the Inbox, then manager accounts, then analytics. Each can be undone by hiding its nav item and route; the tables stay.

## Seed data (dev)
- The SLA v1 policy above.
- Users:
  - `manager@` → Priya Menon (Engineering)
  - a second manager `manager2@` → Sarah Kim, to prove isolation
  - `hrops@`, `approver@`, `admin@`, `auditor@` from Phase 0
- Work queue:
  - one open waiver for Diya's education certificate requested by `manager@`, compliance-flagged → needs 2 approvals
  - one auto-approved 2-day extension (system actor)
  - one joiner appeal from Aarav ("my name is written in full on the passport")
  - one breached `doc_review`
- Comments: a short thread on Kabir's BGV item between hr_ops and the IT owner, with an @mention.
- History for analytics: 12 weeks of synthetic completed onboarding cases across 4 departments with realistic spread, plus 60 spot-check decisions (54 confirm, 6 overturn) across photo ID, PAN and address proof. They're seeded with a fixed random seed, so the charts look the same every demo and screenshots stay stable.

## Tests (`backend/test_phase6.py`, plain asserts)
- `test_manager_sees_only_own_reports`: `manager@` gets the Engineering joiners under Priya; GET on Sarah Kim's report → 404 on every manager-allowed route (looped over the perms table).
- `test_manager_cannot_view_document_content`: `GET /api/documents/{id}/file` as manager → 404.
- `test_manager_cannot_approve_own_teams_exception`.
- `test_requester_cannot_approve_own_request` and `test_same_approver_twice_rejected`.
- `test_compliance_waiver_needs_two_approvals`: after one approval, still open and the item unchanged; after the second, the item is `Waived`, the dependencies unblock, and there are 2 audit rows.
- `test_short_extension_auto_approved_long_needs_human`.
- `test_override_of_auto_accept_counts_as_ai_wrong`: `v_ai_accuracy` changes by one.
- `test_appeal_decider_must_differ_from_original`.
- `test_ladder_steps_fire_once` (time-skipping env): one clock, time skipped past every threshold, `escalate_step` retried with a forced failure after its insert, and `temporal.start` for the clock enqueued 3 times → one workflow, exactly one `escalations` row and one email per step.
- `test_ladder_resolve_signal_stops_timers` (time-skipping): resolving before step 2 → steps 2+ never fire.
- `test_ladder_pause_shifts_timers` (time-skipping): a 10-day pause between step 0 and step 1 → step 1 fires 10 days later than it would have.
- `test_exception_workflow_waits_for_two_approvals` (time-skipping): a compliance waiver plus one `decision` signal → still waiting and the item unchanged. The same approver signalling again → still waiting. A second approver → the effect runs once.
- `test_appeal_waits_on_signal_and_escalates_when_stale` (time-skipping): no decision within 72 h → the `hr_lead` escalation fires once.
- `test_ladder_fallthrough_when_owner_manager_missing`: goes to `hr_lead`, recorded as such.
- `test_sla_pauses_during_leave`: a 10-day pause → `resolve_by` effectively shifted, no breach.
- `test_sla_backfill_does_not_flood`: a pre-breached item → its workflow skips the steps marked `backfill_skipped` and sends only the current step.
- `test_most_specific_sla_policy_wins`: the Compliance BGV policy beats the generic task policy.
- `test_wilson_interval_bounds`: 54/60 gives roughly (0.80, 0.95); 0/0 → None, so the UI shows "not enough samples".
- `test_heatmap_scoped_for_manager`: the manager's heatmap only counts their team.
- `test_mention_creates_notification_and_one_email`.

## Exit criteria
- Every role lands on a page that shows *their* work. The Inbox is the single place where human decisions happen; no decision path bypasses `exceptions.service.decide` or the Phase 1/2 decision services (verified with a grep test over write routes).
- Demo: with the clock moved forward in dev, an overdue IT task escalates owner → owner's manager → HR lead over 3 days with exactly one email per step. The ladder history is visible on the item.
- Logged in as `manager2@`, none of `manager@`'s people are visible through the UI or API (the isolation test loop passes).
- The compliance waiver needed two different approvers, and each approval is in the audit log with its reason.
- Analytics render from seed data, and the AI accuracy chart matches `v_ai_accuracy` exactly (54/60, 90%).
- `scripts/smoke.py` adds a manager walkthrough and an approver Inbox decision. Screenshots updated in `docs/screenshots/`.

## Risks and rollback
| Risk | Mitigation |
|---|---|
| Wrong `manager_email` data leaks a person to the wrong manager | Scoping only ever narrows (a manager sees a subset of what HR sees), and manager mode has no document content. A Users screen warning lists employees with no matching manager login and managers with no reports. Changes to `manager_email` are audited. |
| Thousands of long-running timers | A timer costs Temporal next to nothing. One workflow per open clock is well within a single Temporal deployment at HR scale. The workflow ends when the clock resolves. |
| Escalation spam (bad SLA config) | SLA policies are governed: preview shows the breach impact, and over 10 immediate breaches needs a second approver. One email per step per clock, ever. Owners can see and act on everything from one digest link. |
| The Inbox becomes a dumping ground | Every producer sets `assignee_role` and an SLA. The `ops` kind goes to admin only. Analytics show exception rate per kind, so a noisy producer stands out. |
| Analytics queries slow down on large data | The views are plain SQL with indexes on `items(due)` and `sla_clocks(resolve_by)`. Turn them into materialised views refreshed by a job once p95 is over 500 ms (measured from the request log). |
| Auto-approved extensions hide real delays | Limited to ≤ 3 days on non-compliance items, recorded with the system actor, and counted separately in exception analytics. |
| Rollback | Each feature is separable: hide Inbox, Team or Analytics nav and routes; terminate the `EscalationWorkflow`s (the `escalations` rows remain) and re-register the Phase 4 `escalation.check` (code kept until Phase 6 exit). The migrations are additive. |

# Azentio Onboard: Architecture & Agentic Workflow

This document describes how the HR Onboarding Agent is built and how the agent behaves. It covers the system as it exists in this repository: what each part does, how data moves, what the agent decides on its own, and where the design is deliberately simplified.

---

## 1. Overview

### Problem
Onboarding a new joiner involves HR, IT, Facilities and Compliance. Documents arrive late or wrong, provisioning waits on records that wait on documents, and nobody has one view of what is blocking day one. When a joining date or role changes, the plan usually gets rebuilt by hand.

### What the system does
- Works out which documents, tasks and policy acknowledgements apply to each joiner from their role, department, grade and location, and records **why** each one applies.
- Requests documents by email, reads the replies, and checks every attachment with a vision model followed by deterministic validation.
- Flags missing, expired, illegible or mismatched documents and emails the joiner a specific correction request.
- Builds the HR record from accepted documents, then raises IT, Facilities and Compliance requests automatically when their prerequisites are met.
- Recalculates due dates, owners and required items when the joining date or role changes.
- Escalates overdue tasks to the owner and says what each one is blocking.
- Answers joiner and HR questions from approved policies and live onboarding data, with citations.
- Brings candidates in from source systems and writes employees to a target HR system.

### Scope against the brief
| Brief | Status |
|---|---|
| Level 1: ingestion, checklist with reasons, document tracking, validation, grounded assistant, dashboard | Built |
| Scenario 1: document collection and correction | Built (email and portal) |
| Scenario 2: HR record and IT provisioning | Built (automatic, dependency-driven) |
| Scenario 3: role-specific templates and policy acknowledgement | Built |
| Scenario 4: joining-date and role changes | Built (automatic recalculation with an audit trail) |
| Scenario 5: questions and overdue approvals | Built (two assistants, escalation emails) |
| Extra: connectors, integrations, HR co-pilot, knowledge-graph view | Built |

### Design principles
1. **The LLM perceives; rules decide.** The model reads documents and writes answers. Whether a document is accepted, a task is blocked or a record is written is decided by deterministic code.
2. **Every item explains itself.** Each checklist item stores the rule that created it and the criteria that matched.
3. **The graph is the plan.** Dependencies are edges in FalkorDB, so recalculation means walking edges, not rebuilding spreadsheets.
4. **Fail toward a human.** If extraction fails or confidence is low, the document goes to *Under Review*. It is never accepted silently and never lost.
5. **Cheap by default.** Free models, capped tokens, one fallback, and the spend of every call logged.

---

## 2. System context

```mermaid
flowchart LR
    HR["HR team<br/>(browser)"]
    EMP["New joiner<br/>(email + portal link)"]
    subgraph App["Azentio Onboard"]
        FE["Next.js 16 frontend<br/>HR app + employee portal"]
        BE["FastAPI backend<br/>rules · pipeline · assistants · connectors"]
        DB[("FalkorDB<br/>graph 'hr'")]
        FS[["uploads/<br/>document files"]]
    end
    GM["Gmail API<br/>(HR mailbox)"]
    LLM["OpenRouter<br/>vision + chat models"]
    AV["AwaitVerify<br/>(optional human check)"]

    HR --> FE
    EMP --> FE
    EMP <-->|"request · reply with attachments · corrections"| GM
    FE <-->|"REST + streamed text"| BE
    BE <--> DB
    BE <--> FS
    BE <-->|"send · poll · label"| GM
    BE -->|"extract · answer"| LLM
    BE -.->|"low-confidence fields"| AV
```

---

## 3. Containers and components

### 3.1 Runtime
| Container | Technology | Notes |
|---|---|---|
| Frontend | Next.js 16 (App Router, Turbopack), React 19, Tailwind v4, shadcn/ui on Base UI, lucide icons | All pages are client components fetching live data. Cache Components and PPR are off. |
| Backend | Python 3.12, FastAPI, uvicorn, managed by `uv` | Single process. A background asyncio task polls Gmail every 60 s. |
| Database | FalkorDB (Redis-compatible graph, Cypher) in Docker | One graph, `hr`. A separate `test` graph is used by the self-checks. |
| File store | Local `backend/uploads/` | Original document files, served back for preview. |
| LLM | Any OpenAI-compatible endpoint; OpenRouter by default | Free vision model with a free fallback. |
| Email | Gmail API with an OAuth refresh token | One HR mailbox sends and receives. |

### 3.2 Backend modules
| File | Responsibility | Key functions |
|---|---|---|
| `main.py` | App setup, lifespan (seed, start Gmail poller), HR, portal, ingestion, template, rule and assistant routes | `items_with_state`, `summarize`, `flags`, `create_joiner` |
| `graph.py` | FalkorDB client and Cypher helpers | `q`, `q1`, `event`, `ensure_indexes` |
| `checklist.py` | Rules engine: applicability, reasons, due dates, blocking, automatic task progression | `match_rule`, `build_items`, `view_state`, `regenerate`, `recompute` |
| `validate.py` | Deterministic document checks | `check_file`, `names_match`, `evaluate` |
| `llm.py` | Provider-agnostic client, model fallback, reasoning controls | `complete` |
| `extract.py` | Document classification and field extraction (vision, JSON), PDF to image, optional human verification | `to_images`, `extract`, `human_verify` |
| `pipeline.py` | Shared document intake for email and portal | `intake` |
| `gmail.py` | Templated sending, reply polling, attachment download, correction emails | `send_request`, `send_correction`, `poll`, `poll_forever` |
| `chat.py` | Policy chunking and retrieval, employee assistant, HR co-pilot | `index_policy`, `retrieve`, `answer_stream`, `hr_context`, `hr_answer_stream` |
| `ingest.py` | CSV, Excel and JSON parsing and validation, employee upsert, seeding | `read_table`, `validate_employees`, `upsert_employee`, `import_requirements`, `seed` |
| `connector_routes.py` | Connector and integration API | router mounted under `/api` |
| `connectors/base.py` | Connector interface, `FieldSpec`, `RecordRejected` | `Connector` |
| `connectors/candidates.py` | Candidate Portal and Candidate Import sources | `normalise`, `CandidateImport.import_file` |
| `connectors/hr_portal.py` | HR Portal target: write rules, custom fields, onboarding push | `validate`, `HrPortal.upsert_record`, `add_field`, `push_employee` |
| `connectors/registry.py` | Connector key → implementation | `_REGISTRY`, `get` |
| `connectors/sync.py` | Field-mapping suggestions and integration runs | `suggest_mapping`, `apply_mapping`, `run` |
| `connectors/seed.py` | Catalog, instances and demo records | `seed_connectors` |
| `seed/` | Sample joiners, owners, requirement rules, roles, email templates, policies | — |
| `test_core.py` | Assert-based self-checks | — |

### 3.3 Frontend
| Route | Audience | Purpose |
|---|---|---|
| `/dashboard` | HR | KPIs, filters (department, location, status, joining range, search), readiness per joiner, inbox check, escalation |
| `/employees/[id]` | HR | Checklist with reasons, documents with extracted-vs-profile comparison, HR record, knowledge graph, activity; send request, change date or role, accept or reject |
| `/import` ("Add joiners") | HR | One-step "Onboard a candidate" form plus bulk import of joiners, owners, requirements and policies |
| `/templates` | HR | Email template editor with live preview |
| `/rules` | HR | Requirement rules editor; saving recalculates every checklist |
| `/connectors`, `/connectors/[id]` | HR | Sources and targets; Records, Fields and Import history tabs; custom fields; start onboarding from a candidate |
| `/integrations` | HR | Source → target mapping, duplicate policy, run results and history |
| `/activity` | HR | Global event feed, refreshed every 10 s |
| `/portal/[token]` | Joiner | Progress and countdown, checklist timeline, drag-and-drop upload, policy acknowledgement, assistant |

| Shared component | Purpose |
|---|---|
| `app-shell.tsx` | Sidebar, theme toggle, Gmail status; mounts the HR co-pilot |
| `hr-assistant.tsx` | Floating co-pilot; becomes page-aware on an employee record |
| `chat-panel.tsx` | Streaming chat used by both assistants; parses the `__SOURCES__` header |
| `graph-view.tsx` | Hand-drawn SVG radial view of an employee's subgraph |
| `quick-onboard.tsx` | Create the joiner, build the checklist and send the request in one step |
| `status.tsx` | State and status badges, progress ring, segment bar, source and target role badge |
| `timeline.tsx`, `markdown.tsx`, `simple-select.tsx` | Activity timeline, Markdown subset renderer, select wrapper |

---

## 4. Data model (FalkorDB graph `hr`)

```mermaid
flowchart LR
    E((Employee)) -->|HAS_ITEM| I((Item))
    I -->|DEPENDS_ON| I
    I -->|OWNED_BY| O((Owner))
    E -->|REPORTS_TO| O
    E -->|SUBMITTED| D((Document))
    I -->|HAS_DOC| D
    D -->|MISMATCH| E
    E -->|FROM_CANDIDATE| R((Record))
    E -->|SYNCED_TO| R
    R -->|IN| CI((ConnectorInstance))
    CI -->|OF| C((Connector))
    RU((Rule)) -.->|"creates (by rule_id)"| I
    PC((PolicyChunk)) -.->|source| P((Policy))
```

| Label | Key properties | Purpose |
|---|---|---|
| `Employee` | `id, name, email, role, grade, department, location, manager_email, joining_date, date_of_birth, token, thread_id` | A joiner. `token` grants portal access; `thread_id` links their Gmail thread. |
| `Item` | `id, employee_id, key, name, kind (document, task, ack), category, rule_id, reason, policy_ref, due, status, owner_name, owner_email, auto, depends[]` | One checklist entry, with the reason it exists |
| `Rule` | `id, item, name, kind, category, match (json), offset_days, depends_on[], description, policy_ref, auto, ord` | A requirement rule, editable by HR |
| `Document` | `id, employee_id, filename, path, mime, size, source (email, portal), status, extracted (json), issues (json), confidence, doc_type, ts` | One received file and its verification result |
| `Owner` | `email, name, department, type (manager, owner:HR, owner:IT, owner:Facilities, owner:Compliance)` | Department owners and managers |
| `Event` | `id, employee_id, kind, message, ts` | Audit and activity log, including AI model and token usage |
| `Config` | `key (departments, locations, doc_types, roles), value (json)` | Reference data |
| `EmailTemplate` | `id, name, subject, body` | HR-editable Jinja templates |
| `Policy` / `PolicyChunk` | `source, title, text` / `source, section, text, embedding?` | Approved policy text, chunked by `##` section |
| `Connector` | `key, name, role (source, target), record_kind, demo, description` | Connector catalog |
| `ConnectorInstance` | `id, key, name` | This workspace's use of a connector |
| `Record` | `id, instance_id, kind, values (json), created, updated` | A candidate or HR Portal employee |
| `CustomField` | `instance_id, key, label, type, required` | HR Portal custom fields |
| `ImportRun` / `IntegrationRun` | counts, errors or results, mapping, policy, `ts` | Import and sync history |

Relationship properties: `MISMATCH {field, expected, found}`.

### Status vocabularies
- **Document item:** Not Submitted → Submitted → Under Review → Accepted or Correction Requested
- **Task item:** Pending → Requested → Completed
- **Acknowledgement item:** Pending → Acknowledged
- **Derived view state** (computed, never stored): `completed` if the status is done; otherwise `blocked` if any dependency isn't done; otherwise `overdue` if the due date has passed; otherwise `pending`.

---

## 5. Agentic workflow

### 5.1 The agent loop
The agent is a **workflow agent with deterministic guardrails**. It doesn't plan open-endedly or call tools freely. Each stage has a fixed contract, and the LLM is used only where reading or writing language is required.

```mermaid
flowchart LR
    P["1 · Perceive<br/>Gmail poll every 60 s<br/>portal uploads<br/>ingestion, connectors"]
    U["2 · Understand<br/>vision LLM classifies<br/>and extracts fields<br/>(JSON only)"]
    D["3 · Decide<br/>validate.py rules<br/>checklist rules engine<br/>graph dependencies"]
    A["4 · Act<br/>set statuses · email corrections<br/>auto-complete HR record<br/>raise requests · push to HR Portal<br/>escalate overdue"]
    X["5 · Explain<br/>reason on every item<br/>event log · flags<br/>grounded assistants"]
    P --> U --> D --> A --> X
    A -->|"state change"| D
```

### 5.2 Autonomy boundaries
| The agent does this on its own | A human does this | The LLM never does this |
|---|---|---|
| Classify and extract documents | Accept or reject a document under review | Accept or reject a document |
| Reject documents that fail rules, and email the reason | Mark manual tasks done (BGV, laptop, badge) | Decide which documents a role needs |
| Complete the HR record when its prerequisites are accepted | Change joining date, role or rules | Change due dates, owners or dependencies |
| Raise IT, Facilities and Compliance requests when unblocked | Trigger escalations (button, with confirmation) | Write to the HR Portal |
| Recalculate the plan after a change | Send document requests | Send email |
| Write the employee to the HR Portal when the HR record completes | Run integrations | |

Prompt injection is contained by this split. A document or email that contains instructions can only influence the extracted JSON fields, and those fields still have to pass deterministic checks against the profile.

### 5.3 Email document intake (scenario 1)

```mermaid
sequenceDiagram
    autonumber
    participant HR
    participant BE as Backend
    participant GM as Gmail
    participant J as Joiner
    participant LLM as Vision model
    participant G as FalkorDB

    HR->>BE: POST /employees/{id}/send-request (template)
    BE->>G: outstanding document items
    BE->>GM: send rendered template (doc list + portal link)
    GM-->>BE: threadId
    BE->>G: Employee.thread_id, Event
    J->>GM: reply with attachments
    loop every 60 s (or "Check inbox now")
        BE->>GM: list from:{employee emails} has:attachment -label:azentio-processed
        BE->>GM: download attachments, add label
        BE->>G: match by thread_id, else by sender
    end
    BE->>BE: check_file (type, size)
    BE->>LLM: page images + doc-type catalogue (JSON reply)
    LLM-->>BE: doc_type, name, DOB, number, dates, legible, confidence
    BE->>BE: evaluate(): required fields, name and DOB vs profile, expiry vs joining date, confidence
    alt passes
        BE->>G: Item = Accepted
    else fails
        BE->>G: Item = Correction Requested, MISMATCH edges
        BE->>GM: correction email in the same thread
    else low confidence or extraction failed
        BE->>G: Item = Under Review (optional AwaitVerify)
    end
    BE->>BE: recompute() (may unlock tasks)
```

### 5.4 Portal upload
The same `pipeline.intake` runs, with `source = portal` and the item key supplied by the page. The browser gets the result immediately (status and issues), and the correction email goes out only if Gmail is configured.

### 5.5 Dependency-driven provisioning (scenario 2)

```mermaid
sequenceDiagram
    autonumber
    participant G as FalkorDB
    participant R as recompute()
    participant H as HR Portal connector

    Note over G: ID, address, PAN, bank (and W-4 for US) Accepted
    R->>G: hr_record deps all done? yes, auto=complete
    R->>G: hr_record = Completed, Event
    R->>H: push_employee() (match on work email, policy=update)
    H->>G: Record created or updated, SYNCED_TO, Event
    R->>G: laptop, email account, badge deps done? yes
    R->>G: status = Requested, Event "Request raised to IT Service Desk"
    Note over R: loops until nothing changes, so chains resolve in one pass
```

### 5.6 Joining-date or role change (scenario 4)
`PATCH /employees/{id}` → `checklist.regenerate(eid, reason)`:
1. Re-run every rule against the updated profile.
2. Remove items that no longer apply, add new ones, recalculate due dates and owners. Each change is logged as an event that names the reason (for example, "due date moved 2026-11-06 → 2026-11-27 (joining date 2026-11-16 → 2026-12-07)").
3. Items that still apply keep their progress (accepted documents stay accepted).
4. Rebuild the `DEPENDS_ON` and `OWNED_BY` edges, then run `recompute()`.

Editing a rule on `/rules` runs the same regeneration for every joiner.

### 5.7 Overdue escalation (scenario 5)
`POST /escalate` finds every task in the derived `overdue` state that has an owner email, works out which items it is blocking (reverse dependencies), and sends the escalation template to the owner. The seed routes owner emails to `+it`, `+facilities`, `+compliance` and `+hr` aliases of the HR mailbox, so escalations can be seen in one inbox during a demo.

### 5.8 Employee assistant (grounded RAG)

```mermaid
sequenceDiagram
    participant J as Joiner (portal)
    participant BE as chat.answer_stream
    participant G as FalkorDB
    participant LLM

    J->>BE: question + last 6 turns
    BE->>G: top-4 policy chunks (vector index if an embedding model is set, else keyword overlap)
    BE->>G: this joiner's checklist (status, due, owner, reason)
    BE-->>J: __SOURCES__ [policy sections]
    BE->>LLM: system: answer only from context, cite [policy § section] or [checklist], else say "I don't know" + HR contact
    LLM-->>J: streamed answer
    BE->>G: Event (model, tokens)
```

### 5.9 HR co-pilot (whole-organisation context)
`POST /hr/chat` builds a compact digest with `chat.hr_context`:
- One line per joiner: readiness %, overdue, blocked and correction counts, request sent or not.
- Their open items with owner and blocker.
- Mismatch flags.
- The owner directory.
- The last 15 events.
- Retrieved policy chunks.

When HR is viewing an employee, that joiner's documents, extracted fields and issues are added, and the prompt tells the model who "they" refers to. About 4k tokens for six joiners. The answer is streamed with `[joiner: Name]`, `[activity]` and policy citations.

### 5.10 Candidate → onboarding → HR Portal

```mermaid
sequenceDiagram
    participant HR
    participant C as Candidate Portal / Import
    participant BE as create_joiner()
    participant G as FalkorDB
    participant H as HR Portal

    HR->>C: open candidate, "Start onboarding"
    C->>BE: name, email, designation→role, department, grade, location, joining date
    BE->>BE: validate_employees() (same rules as CSV)
    BE->>G: Employee + checklist, FROM_CANDIDATE
    opt send email
        BE->>BE: gmail.send_request()
    end
    Note over G: documents accepted → HR record auto-completes
    G->>H: push_employee() → SYNCED_TO
```

### 5.11 Integration sync
`POST /integrations/run`:
1. Load the source records, filtered by candidate status.
2. Apply the mapping (target ← source).
3. Call `target.upsert_record(values, policy)` for each record.

The HR Portal rules (§8) either clean the record or raise `RecordRejected`. Outcomes are counted as `created`, `updated`, `skipped` or `rejected`, with reasons, and stored as an `IntegrationRun`. An identical re-run produces only `skipped`.

### 5.12 State machines

```mermaid
stateDiagram-v2
    [*] --> NotSubmitted
    NotSubmitted --> Submitted: file received
    Submitted --> UnderReview: extraction starts
    UnderReview --> Accepted: all checks pass
    UnderReview --> CorrectionRequested: a check fails
    UnderReview --> UnderReview: low confidence / extraction error (human)
    CorrectionRequested --> Submitted: new file
    UnderReview --> Accepted: HR accepts
    Accepted --> [*]
```

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Requested: dependencies done (auto=request or manual task)
    Pending --> Completed: dependencies done and auto=complete (HR record)
    Requested --> Completed: owner / HR marks done
    Completed --> [*]
```

A later upload never downgrades an item that is already *Accepted*.

---

## 6. Rules engine

**Rule shape** (`seed/requirements.json`, editable on `/rules`):
```json
{"id": "R-EXP", "item": "experience_letter", "kind": "document", "category": "HR",
 "match": {"grade_min": 3}, "offset_days": -7, "depends_on": [],
 "description": "Lateral hires at grade G3 and above must provide a relieving letter",
 "policy_ref": "document-submission-policy.md", "auto": ""}
```

| Concept | Behaviour |
|---|---|
| Match keys | `department`, `role`, `location`, `country` (derived from location), `grade_min`, `grade_max`. A value may be a list. An empty match means everyone. |
| Reason | `"{description} — rule {id} ({matched criteria})"`, e.g. "… — rule R-EDU (department = Engineering)" |
| Due date | `joining_date + offset_days` (negative means before joining) |
| Owner | The `Owner` whose type is `owner:{category}` |
| Dependencies | `depends_on` item keys. Keys that don't apply to this joiner are dropped, so a US hire's HR record doesn't wait on a PAN card. |
| `auto` | `complete`: finishes itself when unblocked (HR record). `request`: moves to Requested when unblocked (laptop, email account, badge, access). |
| `regenerate` vs `recompute` | `regenerate` re-derives *which* items exist (profile or rule change). `recompute` only advances *statuses* (any state change). |

Seeded rules cover identity and address (everyone), PAN and bank (India), education (Engineering), experience letter (G3+), I-9 and W-4 (US), BGV, HR record, laptop, email account, badge, orientation, infosec and code-of-conduct acknowledgements, developer tools (Engineering) and CRM (Sales).

---

## 7. LLM layer

| Aspect | Design |
|---|---|
| Client | `llm.py`: one `AsyncOpenAI` client pointed at `LLM_BASE_URL` (default OpenRouter). Switching to Groq, Mistral or OpenAI is an `.env` change. |
| Models | `LLM_MODEL=nvidia/nemotron-3.5-super-vl:free` (vision, 1M context); `LLM_FALLBACK_MODEL=google/gemma-4-31b-it:free`. A paid model is never chosen automatically. |
| Reasoning control | On OpenRouter, `reasoning: {effort: low, exclude: true}` stops reasoning models from spending the whole token budget on hidden thinking. |
| Fallback | An error *or an empty reply* moves to the fallback model. |
| Extraction | Up to 2 PDF pages rendered to JPEG (PyMuPDF), images downscaled to ≤2000 px (Pillow), `response_format=json_object`, and a flat example skeleton in the prompt. The parser strips code fences and unwraps a model that echoes the schema's `properties` wrapper. |
| Grounding | Context-only system prompts, mandatory citations, an explicit "say you don't know" rule, and the HR contact in the refusal. |
| Retrieval | FalkorDB vector index (1536-d, cosine) when `LLM_EMBED_MODEL` is set; otherwise bag-of-words overlap over the policy chunks. |
| Cost caps | 1,500 tokens for extraction, 2,500 for chat, the last 6 turns of history, top 4 chunks. Every call's model and token count is written to the activity feed. |
| Failure handling | Extraction exception → document *Under Review* plus an error event. Chat outage → a polite fallback message with the HR contact. |
| Human verification | `extract.human_verify` calls AwaitVerify's `verify_document` for low-confidence results when `AWAITVERIFY_ENABLED=true`. It's off by default and the package is optional. |

Typical usage measured in testing: extraction about 2.3k tokens, an employee answer about 1.6–3.3k, the HR co-pilot context about 4k input.

---

## 8. Connectors and integrations

| Connector | Key | Role | Holds | Notes |
|---|---|---|---|---|
| Candidate Portal | `candidate_portal` | Source | Candidates | Demo; 10 seeded |
| Candidate Import | `candidate_import` | Source | Candidates | CSV, XLSX or JSON; upsert by `candidate_id`; import history |
| HR Portal | `hr_portal` | Target | Employees | Demo; custom fields |

**Interface** (`connectors/base.py`): `describe_schema`, `list_records(page, page_size, q)`, `get_record`, `count_records`, and the optional `upsert_record(values, duplicate_policy) → (id, created | updated | skipped)`. The base implementation refuses writes.

**HR Portal write rules** (`hr_portal.validate`, raising `RecordRejected` with an actionable reason):
- Required: `first_name`, `last_name`, `work_email`, `job_title`, `start_date`. `employee_number` (`EMP-#####`) and `employment_status` (`active`) are generated when unmapped.
- Matched on work email. `update` merges changes (an identical write counts as `skipped`, so runs are safe to repeat). `skip` leaves existing employees alone. The existing employee number is always kept.
- Department must exist in the workspace (case-insensitive), the start date must be valid, and status must be `active`, `on_leave` or `terminated`.
- Custom fields: types string, number, boolean or date. The key is slugified from the label ("Badge number" → `badge_number`). A clash with any existing field returns **409**. Values are stored under `values.custom`.

**Mapping suggestions** (`sync.suggest_mapping`): aliases (`email→work_email`, `designation→job_title`, `joining_date→start_date`, …), then exact key matches. List values such as education are joined into text.

**Adding a connector:** subclass `Connector`, implement the read methods (plus `upsert_record` for a target), register it in `_REGISTRY`, and add a catalog row and an instance in `connectors/seed.py`.

---

## 9. API reference

All routes are under `/api`. JSON in and out; the chat routes stream `text/plain`, with the first line `__SOURCES__[...]`.

**Dashboard and joiners**
| Method | Path | Purpose |
|---|---|---|
| GET | `/employees?department&location&status&join_from&join_to&search` | Joiners with summary, overall state, days to join, flag count |
| GET | `/stats` | KPI tiles, filter options, Gmail status |
| GET | `/employees/{eid}` | Profile, items with state and blockers, documents, flags, events, manager, HR record |
| GET | `/employees/{eid}/graph` | Nodes and edges for the graph view |
| PATCH | `/employees/{eid}` | Change date, role, grade, department, location or email → recalculate |
| GET | `/employees/{eid}/preview-email?template_id` | Rendered request email |
| POST | `/employees/{eid}/send-request` | Send the request via Gmail |
| GET | `/employees/{eid}/portal-link` | Portal token |
| GET | `/employees/{eid}/links` | Connector links (from candidate, synced to HR) |
| POST | `/items/{item_id}/status` | HR sets a status; a correction triggers an email |
| POST | `/onboard` | One-step create (+ optional send) |
| POST | `/escalate` | Email the owners of overdue tasks |
| POST | `/gmail/poll` | Check the inbox now |
| GET | `/events?limit` | Activity feed |
| GET | `/documents/{doc_id}/file` | Original file for preview |

**Configuration and ingestion**
| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` (multipart `kind`, `file`) | Import employees, owners, requirements or policies |
| GET / PUT | `/templates`, `/templates/{tid}` | Email templates |
| GET / PUT / DELETE | `/rules`, `/rules/{rid}` | Rules plus doc types, departments, locations, roles, owners |
| GET | `/policies` | Policy texts |

**Employee portal** (token-scoped)
| Method | Path | Purpose |
|---|---|---|
| GET | `/portal/{token}` | Joiner view (no owner emails) |
| POST | `/portal/{token}/upload` | Upload a document → pipeline |
| POST | `/portal/{token}/ack/{item_id}` | Acknowledge a policy |
| POST | `/portal/{token}/chat` | Employee assistant |

**Assistants**
| Method | Path | Purpose |
|---|---|---|
| POST | `/hr/chat` (`message, history, employee_id?`) | HR co-pilot |

**Connectors and integrations**
| Method | Path | Purpose |
|---|---|---|
| GET | `/connectors` | Workspace connectors with record counts |
| GET | `/connectors/{cid}` | One connector |
| GET | `/connectors/{cid}/schema` | Field specs |
| GET | `/connectors/{cid}/records?page&page_size&q` | Records (searchable), with onboarding links |
| GET | `/connectors/{cid}/records/{rid}` | One record (404 if it's in another instance) |
| POST | `/connectors/{cid}/attributes` | Add a custom field (201, 409 on a duplicate, 400 if not supported) |
| POST | `/connectors/{cid}/import` | Candidate Import upload |
| GET | `/connectors/{cid}/imports` | Import history |
| POST | `/connectors/{cid}/records/{rid}/onboard` | Start onboarding from a candidate |
| GET | `/integrations/mapping?source&target` | Schemas plus suggested mapping |
| POST | `/integrations/run` | Run a sync |
| GET | `/integrations/runs` | Run history |

---

## 10. Security and privacy

| Area | Current posture | Gap or next step |
|---|---|---|
| Authentication | None for HR pages (single workspace, demo) | Add SSO and roles; enforce `connectors.manage` |
| Joiner access | Unguessable portal token (`secrets.token_urlsafe(16)`); the portal never returns owner emails | Expiry and rotation; signed links |
| Gmail | OAuth refresh token in `.env`. The poller only searches mail from known employee addresses and labels only what it processes | Narrow scopes; a dedicated mailbox |
| Secrets | `.env` is git-ignored | A secrets manager |
| Personal data | Document files on local disk, extracted fields in the graph, sent to the LLM provider for extraction | Encryption at rest, retention policy, a provider with a DPA |
| LLM safety | The LLM can't change state; outputs are schema-parsed and checked by rules | Moderation on chat inputs if exposed publicly |
| CORS | `*` | Restrict to the frontend origin |

---

## 11. Limitations and upgrade paths

| Simplification | Ceiling | Upgrade path |
|---|---|---|
| Single workspace; connector methods take no `conn` or `customer_id` | One organisation | Add a workspace id to every node and query; reinstate per-customer instances |
| Keyword retrieval when no embedding model is set | Fine for dozens of policy chunks | Set `LLM_EMBED_MODEL` (the vector index already exists) |
| HR co-pilot loads every joiner's open items | About 40 joiners (then trims to named, soon-joining or focused) | Per-joiner digests retrieved by embedding |
| Connector records matched by scanning JSON values | Demo-sized connectors | Promote `work_email` and `candidate_id` to indexed properties |
| Gmail polling every 60 s | Up to a minute of latency | Gmail push via Pub/Sub (needs a public URL) |
| Only FalkorDB runs in Docker | Local development | Containerise backend and frontend |
| Markdown subset renderer | No tables in policies | A real Markdown parser |
| Free models only | Free-tier rate limits, occasional empty replies | Set a paid model in `.env` for production |
| Correction emails thread by `threadId` only | Gmail may start a new thread | Set `In-Reply-To` and `References` headers |
| AwaitVerify optional and off | No human-in-the-loop extraction | Install `awaithumans`, set `AWAITVERIFY_ENABLED=true` |

---

## 12. Operations

**Run** (see the README for full steps): `docker compose up -d` → `cd backend && uv run uvicorn main:app --port 8000` → `cd frontend && npm run dev`. On first start the backend seeds joiners, owners, rules, roles, templates, policies and connectors. Seeding is additive and skips anything already present.

**Configuration** (`.env`): `OPENROUTER_API_KEY`, optional `LLM_BASE_URL`, `LLM_MODEL`, `LLM_FALLBACK_MODEL`, `LLM_EMBED_MODEL`; `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `HR_MAILBOX`; `AWAITVERIFY_ENABLED`, `AWAITVERIFY_API_KEY`; `FALKORDB_URL`, `PUBLIC_BASE_URL`.

**Tests:** `cd backend && FALKORDB_GRAPH=test uv run python test_core.py` covers:
- Rule matching for India, US, intern and engineer profiles, plus dependency pruning.
- Blocked and overdue states.
- Name and DOB matching, expiry, legibility and confidence routing.
- Ingestion validation.
- HR Portal write rules.
- Mapping suggestions.
- Candidate normalisation.
- Idempotent upserts (four writes, one employee).
- Duplicate custom-field refusal.

The frontend is checked with `npx tsc --noEmit` and `npx eslint`.

**Observability:** every decision and side effect is an `Event`: emails, verifications, automatic actions, AI calls with model and token count, checklist changes with their reason, escalations, connector syncs and errors. It's visible per joiner and globally on `/activity`.

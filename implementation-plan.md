# HR Onboarding Agent: Plan (continued: OpenRouter switch + remaining build)

## Context
This is a 1-day hackathon build of the brief in `HR Onboarding Agent.md` (Level 1) plus the email flow in `note.md`. The full design is in `implementation-plan.md` in the repo.
The backend is written, but the OpenAI key has a $0 balance (429 "no credits remaining" on both embeddings and responses). The user has added `OPENROUTER_API_KEY` to `.env`. I checked it: it's valid, on the paid tier, with a $50 limit and $0 used. This plan moves the LLM layer to OpenRouter, then finishes the remaining work.

## Done so far (backend/, working)
- `graph.py`: FalkorDB helpers `q`/`q1`/`event`/`ensure_indexes`. Maps are created with `CREATE (n) SET n = $p`, because inline map params aren't supported.
- `checklist.py`: rules engine (`build_items`, `match_rule`, `view_state`, `regenerate`, `recompute`), with reasons, due dates, DEPENDS_ON blocking and auto-provisioning.
- `validate.py`: file type and size, legibility, required fields, name/DOB mismatch, expiry, low-confidence → Under Review.
- `ingest.py`: CSV/XLSX/JSON parsing and validation, owners, requirements, `seed()`, and `demo_progress()` (added but not yet run).
- `pipeline.py`: shared intake for email and portal → extract → validate → graph → correction email.
- `gmail.py`: send, render templates, poll with a label, match by thread then by sender.
- `chat.py`: chunking, vector retrieval with keyword fallback, streamed grounded answers.
- `main.py`: all HR, portal, ingest, template, rule, escalation and graph routes.
- Seed data and 7 policies. `test_core.py` passes.
- Verified live: startup seeds 6 joiners with 13–15 items each, and `/api/stats` and `/api/employees` respond.

## Step 1: Switch the LLM to OpenRouter (OpenAI-compatible)
OpenRouter speaks the Chat Completions API, not the Responses API. So both LLM call sites move to `client.chat.completions` on one shared client.
- New `backend/llm.py` (tiny, about 20 lines):
  - `client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)`, falling back to `OPENROUTER_API_KEY` and then `OPENAI_API_KEY`.
  - Defaults: `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=nvidia/nemotron-3.5-super-vl:free` (free, vision, structured outputs, 1M context). `LLM_FALLBACK_MODEL=google/gemma-4-31b-it:free` is used on a 429 or error.
  - Switching to Groq, Mistral or OpenAI later is an `.env`-only change.
- **Cost rules (user asked for cheap models):**
  - Only `:free` models by default. Never fall back to a paid model automatically; a paid model is used only if the user sets one in `.env`.
  - Cap `max_tokens` at 800 for extraction and 1000 for chat. Send chat history as at most the last 6 turns, and the context as the top 4 policy chunks plus the checklist.
  - Testing spends at most 3–4 LLM calls (one extraction, one chat answer, one refusal check). No load tests, no repeated runs.
  - Log the model and token usage of each call to the event feed, so spend stays visible.
- `extract.py`:
  - Replace `responses.parse` with `chat.completions.create(..., response_format={"type":"json_schema","json_schema":{"name":"extraction","schema":Extraction.model_json_schema(),"strict":True}})` → `Extraction.model_validate_json`.
  - On failure, retry once with `{"type":"json_object"}` plus the schema written into the prompt.
  - Images go as `{"type":"image_url","image_url":{"url":"data:…;base64,…"}}`.
  - PDFs: render pages 1–2 to PNG with PyMuPDF (`fitz`, already installed) so every provider can read them. Downscale large images (longest side ≤ 2000px) with Pillow (already installed).
- `chat.py`:
  - Streaming switches to `chat.completions.create(stream=True)`, yielding `chunk.choices[0].delta.content`.
  - Embeddings: try `LLM_EMBED_MODEL` if it's set; otherwise skip and use the existing `keyword_retrieve`, since OpenRouter lists no embedding models. That's fine for 7 policies.
- `.env.example` and `implementation-plan.md` get updated for `LLM_*`.

## Step 2: Verify the backend (no destructive commands unless you approve)
The user rejected the earlier command that killed the port-8000 process and ran `GRAPH.DELETE hr`. Rules from now on:
- Before restarting, ask the user to stop the old server themselves, or confirm it's already down. It has already exited.
- To re-seed with `demo_progress`: run `ingest.demo_progress()` once as a script against the existing graph, which adds data without deleting anything. A graph reset happens only if the user approves it.
- Smoke tests: start uvicorn, then check `/api/employees/E1006` (auto-provisioning events), PATCH the joining date (due-date events) and the role change (items added/removed), `/graph`, and a portal upload with a generated sample ID image (Pillow) carrying a mismatched name → Correction Requested + MISMATCH flag, and a portal chat answer with citations.

## Step 3: Frontend (`frontend/`, Next.js App Router + Tailwind + shadcn/ui)
- `create-next-app` (TypeScript, Tailwind, src-less), `shadcn init`, plus the components button, card, badge, table, input, select, dialog, sheet, tabs, textarea, sonner, progress, tooltip, separator, scroll-area, avatar, label, skeleton, dropdown-menu. Use `lucide-react` icons.
- `lib/api.ts`: fetch wrapper on `NEXT_PUBLIC_API_URL=http://localhost:8000`.
- HR app (sidebar layout in the Rippling/Deel style):
  - `/dashboard`: KPI cards from `/api/stats` (joiners, ready, joining in 7 days, overdue, blocked, needs review); a filter bar (department, location, status, joining date range, search); a table with a progress bar, state chips, days-to-join and a flags badge; and buttons for "Check inbox now" (`/api/gmail/poll`) and "Send escalations".
  - `/employees/[id]`:
    - Header with profile, progress ring and actions: send request (template select + HTML preview from `/preview-email`), edit joining date/role (PATCH, toast lists the recalculated items), copy portal link.
    - Tabs:
      - Checklist: grouped by kind, showing the "why" reason, owner, due, blocked-by, and accept/reject/complete controls.
      - Documents: inline file preview, extracted fields vs profile, issues.
      - HR record: built from accepted extractions.
      - Graph: a hand-rolled SVG radial layout of `/graph`, coloured by state, with no new dependency.
      - Activity: events.
    - A flags banner across the top.
  - `/import`: drag-drop for each kind (employees, owners, requirements, policies), showing an error table with row, field and message.
  - `/templates`: list plus an editor (subject, HTML body, live preview with sample variables).
  - `/rules`: a table of rules with an edit dialog (match JSON fields, offset, depends_on, description), add and delete.
  - `/activity`: a global feed.
- Employee portal `/portal/[token]`: welcome hero with progress and joining countdown, a checklist timeline (before joining / day one / after), drag-drop upload per document item with the live verification result, a policy-acknowledgement dialog (markdown → "I acknowledge"), and a chat panel that streams answers and shows source chips (parses the `__SOURCES__` first line).

## Step 4: Wrap-up
`README.md` run steps (docker compose up → `uv run uvicorn main:app` → `npm run dev`); update `implementation-plan.md`; commit only if the user asks.

## Skipped
Real auth/SSO, Pub/Sub push, real IT integrations, containerising the backend and frontend (only FalkorDB runs in Docker), and embeddings on OpenRouter (keyword retrieval covers it).

## Verification
- `uv run python test_core.py` passes.
- Backend smoke tests from Step 2: auto-provisioning events, date and role change recalculation, an upload with a mismatch flagged, chat returning a cited answer and refusing an out-of-policy question ("Do I need a drug test?").
- Real Gmail loop: send a request to a test address → reply with an attachment → click "Check inbox now" → the doc shows a status within seconds, and a correction email arrives on rejection.
- UI: dashboard filters change rows and counts; the employee page shows reasons, blocked-by and the graph; import of a broken CSV shows per-row errors; the portal upload and ack update progress live.

# ADR 0004: Heuristic guardrails first, classifier model when data says so

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** engineering

## Context
The employee portal assistant and the HR co-pilot take free text. Risks:
- prompt injection,
- invented citations,
- leaking contact details that aren't in the context.

Each assistant only ever sees its own user's context, which limits what an injection can reach.

## Decision
`guardrails.py` applies:
- **Input checks before any model call:** injection phrasing and a 2,000-character limit, across the message and its history.
- **Output checks after streaming:**
  - every policy citation must match a retrieved section,
  - every email address must appear in the context,
  - no system-prompt echo.

  A failed check appends a visible "confirm with HR" note.

Hits are stored as `GuardrailEvent` (rule and surface only) and shown on the AI quality page. The eval suite checks that attack prompts are blocked.

## Consequences
**What gets easier**
- Zero latency and cost; deterministic and testable.
- Hit data tells us whether a stronger control is needed.

**What gets harder**
- Regexes catch common phrasings, not a determined attacker.
- Output is annotated, not withheld, because it has already streamed.

**Revisit if** hit rates rise, a red-team finds bypasses, or the portal is exposed to a much wider audience. Then add a classifier model (for example Llama Guard through the gateway), and consider buffering answers before sending.

## Alternatives considered
| Option | Why not (yet) |
|---|---|
| A classifier model on every message | Adds latency and cost before we have evidence it's needed |
| Buffer every answer and check before sending | Loses streaming, which makes the assistant feel slow |

# ADR 0010: Let AI auto-accept only low-risk documents under versioned thresholds

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
Each document type has a risk tier.
- **Low-risk** documents are auto-accepted only when every deterministic check passes and confidence is at or above the threshold (default 0.90).
- **High-risk** documents (ID, passport, visa or work authorisation, I-9, BGV) always need a human decision. The reviewer sees the AI's findings side by side and must give a reason.
- Thresholds are versioned and approved like rules.
- A random 10% of auto-accepted documents go to spot-checks.
- Every automated decision records the model, prompt version, confidence and threshold version.
- If the LLM is unavailable, documents go to human review.

This builds on [ADR 0001](0001-llm-perceives-rules-decide.md).

## Consequences
- Fast handling of routine documents. Legal and compliance exposure stays with humans.
- Every decision can be traced and explained.
- Harder: review workload for high-risk documents. Watch the queue's SLA and the spot-check accuracy.

## Alternatives considered
| Option | Why not |
|---|---|
| A human approves every document | Too much work for HR, so teams start working around it |
| Keep auto-accept and add sampling | Doesn't protect high-risk categories |

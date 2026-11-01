# ADR 0001: The LLM perceives; deterministic rules decide

- **Status:** accepted
- **Date:** 2026-10-15
- **Deciders:** engineering

## Context
The system reads untrusted documents and messages from joiners and acts on them: accepting documents, unblocking provisioning, writing HR records, sending email. An LLM is needed to read scans and write answers, but its output can be wrong, inconsistent between runs, or steered by text inside a document (prompt injection).

## Decision
- The LLM only turns unstructured input into structured data (extraction JSON) or writes natural-language answers.
- Every state change is decided by deterministic code: checklist rules, `validate.evaluate`, dependency recompute, and the integration stage machine.
- Low confidence or any error routes to a human (*Under Review*).
- The integration setup agent may only call tools that go through the same service-layer validation as the UI.
- Approvals happen only when HR says so.

## Consequences
**What gets easier**
- Prompt injection in a document can only change extracted fields, which still have to match the profile.
- Behaviour is testable without a model; a worse model degrades into more human review, not wrong decisions.

**What gets harder**
- Some decisions a model could make (judging an unusual document) wait for HR.
- New document types need rule changes, not just prompt changes.

**Revisit if** human review volume becomes the bottleneck. Then we'd add model-assisted suggestions that HR confirms, still without autonomous acceptance.

## Alternatives considered
| Option | Why not |
|---|---|
| Let the model accept or reject documents | Unexplainable decisions, injection risk, inconsistent between runs |
| A fully autonomous agent with free tool use | Hard to bound, audit or test; one bad step can email every candidate |

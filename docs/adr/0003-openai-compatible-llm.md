# ADR 0003: One OpenAI-compatible LLM client, behind a gateway in production

- **Status:** client accepted (built); gateway proposed
- **Date:** 2026-10-15
- **Deciders:** engineering

## Context
We use LLMs for vision extraction, two chat assistants, the integration setup agent and the eval judge. Providers differ in price, data terms, regions and rate limits, and free tiers are capped (50 requests a day on OpenRouter's free tier, which a single eval run exceeds).

## Decision
- **Client:** every call goes through `llm.py`, a single `AsyncOpenAI` client pointed at any OpenAI-compatible base URL, with one fallback model. An empty reply counts as a failure.
- **Tracing:** optional Langfuse, with document scans redacted.
- **Production:** put a gateway (LiteLLM) between the app and providers. It handles routing, failover, per-tenant budgets, rate limits and caching. The primary provider has a DPA, zero data retention and an India region (for example Azure OpenAI or Bedrock).

## Consequences
**What gets easier**
- Switching providers is configuration.
- Budgets and failover live in one place.
- The app code doesn't change when the gateway arrives, because it speaks the same API.

**What gets harder**
- We're limited to features the OpenAI-compatible API exposes.
- The gateway is one more service to run.

**Revisit if** a provider-specific feature (for example native document parsing) beats general vision models on accuracy or cost by a wide margin.

## Alternatives considered
| Option | Why not |
|---|---|
| A provider SDK per model | Lock-in and duplicated code paths |
| LangChain as the abstraction | Heavy for our needs. It's now present only as a dependency of Ragas |

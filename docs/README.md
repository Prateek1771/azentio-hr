# Azentio Onboard: Design Docs

Azentio Onboard is an AI HR onboarding agent. It works out what each new joiner needs, collects and checks their documents, raises IT, Facilities and Compliance requests, answers questions from approved policies, and moves candidates from source systems into the HR system.

| Doc | Read it when you want to know | Audience |
|---|---|---|
| [HLD](hld.md) | What the system is, its parts, and the decisions that shape it | Everyone |
| [LLD](lld.md) | How each module, data type, workflow, prompt and API works today | Engineers |
| [User flows](user-flows.md) | What HR and joiners do, screen by screen | Product, design, QA |
| [Project architecture](project-architecture.md) | How the code is organised, the layering rules, and the modular structure we're moving to | Engineers |
| [Production architecture](production-architecture.md) | How we would ship and scale it: topology, scaling per component, data, security, cost, rollout phases | Engineering leads, ops, security |
| [SDLC](sdlc.md) | How work moves from idea to production: branching, reviews, tests, CI/CD, releases, incidents | Everyone who ships |
| [Hardening roadmap](plans/2026-11-01-hardening-design.md) | How we take the app from demo to governed HR operations, in 7 phases | Everyone |
| [Decision records](adr/) | Why we chose what we chose, one decision per file | Engineers |

## How to keep these docs true
- A change that alters behaviour updates the LLD in the same pull request.
- A change that alters a boundary (a new service, store, external dependency or trust boundary) updates the HLD and adds an ADR.
- The production architecture is a plan. Each part says whether it's **built**, **planned** or **proposed**, and moves to built when it ships.
- Mermaid diagrams render on GitHub and in most editors. Keep them in the docs rather than as images, so they can be reviewed in a diff.

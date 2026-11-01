# ADR 0008: Use five fixed roles enforced by one permission table

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
There are five roles:
- `admin`: system and users
- `hr_ops`: runs onboarding
- `approver`: policy and high-risk document decisions
- `manager`: own reports, read-only plus comments
- `auditor`: read-only on everything

One ordered table maps (method, path) to the roles allowed, and a single middleware enforces it. Any path the table doesn't list is denied. Sessions are stored server-side behind an httpOnly cookie, and passwords are hashed with scrypt. Employees keep their per-employee portal tokens.

## Consequences
- Every route is covered in one place, and a test exercises the whole table.
- Revoking a session or deactivating a user takes effect immediately.
- Authors and approvers are always different people.
- Harder: a custom role means a code change. Revisit when a customer needs custom roles or SSO group mapping.

## Alternatives considered
| Option | Why not |
|---|---|
| 3 roles (admin, HR, viewer) | No manager view, and nothing stops an author approving their own rule change |
| Fine-grained custom permissions | Premature; more UI and more ways to misconfigure |
| Stateless signed tokens | Can't be revoked immediately |

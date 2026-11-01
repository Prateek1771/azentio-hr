# ADR 0009: Govern rule and threshold changes as approved, immutable versions

- **Status:** accepted
- **Date:** 2026-11-01
- **Deciders:** product owner, engineering (from the [hardening design](../plans/2026-11-01-hardening-design.md))

## Context
A senior FDE and HR review found the app had no trust boundary, ungoverned rules, too little control over AI decisions and no ops backbone. The [hardening design](../plans/2026-11-01-hardening-design.md) sets out the full reasoning and the operating principle: routine work is automatic, and humans act only at policy gates and on exceptions.

## Decision
Rule and threshold changes go through draft, impact preview, then approval by someone other than the author. Compliance-flagged rules need two approvers. Versions are immutable and record author, approvers, reason and diff. Rollback publishes an earlier version again through the same approval path.

When a version is published, for joiners already in progress:
- New items are added and the joiner is notified.
- Items the new version drops are cancelled only if they're not done.
- Evidence is never deleted.
- Joiners starting soon are sent to HR as exceptions instead of being changed.

## Consequences
- Policy changes are reviewable, explainable and reversible. Nobody is surprised by new tasks appearing without explanation.
- Harder: a change takes at least two people. The dual-approval flag adds a second approver only where compliance requires it.

## Alternatives considered
| Option | Why not |
|---|---|
| Versioning only | Changes still take effect immediately, unsafely |
| Approve only "risky" rules | Endless debate over what counts as risky |
| Approver chooses the in-flight behaviour on each publish | Inconsistent outcomes and more chances for human error |

# Architecture Decision Records

One file per decision, named `NNN-short-slug.md`.

Anything not specified in the design documents that had to be decided goes here.
The point is to stop decisions being re-litigated silently.

`S-0.2` requires the first seven:

| ADR | Decision |
|---|---|
| 001 | Implementation language and why |
| 002 | LLM SDK and provider strategy, including the different-vendor requirement for the Adversary |
| 003 | Persistence — checkpoint store and persistent store |
| 004 | Sandboxing approach |
| 005 | First target framework |
| 006 | How the tool tests itself |
| 007 | The refusal list and its rationale |

## Format

```markdown
# NNN — Title

**Status:** proposed | accepted | superseded by NNN
**Date:** YYYY-MM-DD

## Context
What forced a decision.

## Decision
What was decided.

## Consequences
What this makes easy, what it makes hard, and what it rules out.
```

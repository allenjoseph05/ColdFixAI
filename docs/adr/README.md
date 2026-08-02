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

Decisions found by the E0 spikes, numbered from 008 so the seven above stay
reserved:

| ADR | Decision | Came from |
|---|---|---|
| 008 | Query counting uses `force_debug_cursor`, never `settings.DEBUG` | S-0.3 |
| 009 | Grounding is a staged pipeline, and every stage has a machine-checkable predicate | S-0.3 |
| 010 | Environments are anchored to the repository's own date | S-0.3 |
| 011 | Development target, holdout, and reserve | S-0.6 |

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

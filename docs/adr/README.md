# Architecture Decision Records

One file per decision, named `NNN-short-slug.md`.

Anything not specified in the design documents that had to be decided goes here.
The point is to stop decisions being re-litigated silently.

`S-0.2` requires the first seven. All written 2026-08-02:

| ADR | Decision | Headline |
|---|---|---|
| 001 | Implementation language | Python 3.12+ — the instrumentation hooks do not survive a process boundary |
| 002 | LLM SDK and provider strategy | Anthropic SDK, `claude-opus-5`. **The Adversary's different-vendor requirement is deferred and recorded as a known limitation**, not dropped |
| 003 | Persistence | Two stores. SQLite checkpoints in dev, Postgres for concurrency; persistent data always separate |
| 004 | Sandboxing | Docker, plus a separate worktree so a diagnostic run *cannot* produce a patch |
| 005 | First target framework | Django + Postgres — grounded 3/3 in S-0.3, and the reset primitive is Postgres-specific |
| 006 | How the tool tests itself | Four layers. Every defect fixture carries a control, or the detector learns to say yes |
| 007 | The refusal list | Four categories declined permanently; two need detection built before they can be refused |

**ADR-006 was written from S-0.7's outcome rather than before it.** S-0.7 depends
on S-0.2, and S-0.2 requires an ADR describing how the tool tests itself — which
is what S-0.7 decides. The fixture repository was built first and the record
followed.

Decisions found by the E0 spikes, numbered from 008 so the seven above stay
reserved:

| ADR | Decision | Came from |
|---|---|---|
| 008 | Query counting uses `force_debug_cursor`, never `settings.DEBUG` | S-0.3 |
| 009 | Grounding is a staged pipeline, and every stage has a machine-checkable predicate | S-0.3 |
| 010 | Environments are anchored to the repository's own date | S-0.3 |
| 011 | Development target, holdout, and reserve | S-0.6 |

Decisions found while building the lab bench:

| ADR | Decision | Came from |
|---|---|---|
| 012 | `time()` records samples and changes nothing to get them | S-1.2 |
| 013 | Counters are named hooks, and an unknown name raises | S-1.3 |

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

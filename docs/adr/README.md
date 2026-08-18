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
| 014 | `diff()` is strict by default, and every loosening is opt-in | S-1.4 |
| 015 | The rank test is written out, and the statistics stay standard-library | S-1.5 |
| 016 | Malformed input must not produce a well-formed answer | E1 audit |
| 017 | An instrument must survive the input it cannot summarize | E1 audit |
| 018 | A comparison owns the order its samples were taken in | S-1.6 |
| 019 | The noise floor is simulated against the test that will be used | S-1.7 |

Decisions found while building the execution environment:

| ADR | Decision | Came from |
|---|---|---|
| 020 | A container is destroyed by name, and its status read from the daemon | S-2.1 |
| 021 | Worktrees are detached, and the clean-tree guard is asymmetric | S-2.2 |
| 022 | A diagnostic session has no method that returns a diff | S-2.3 |
| 023 | The patch filter parses the diff, and uses git only to check itself | S-2.4 |
| 024 | The production guard is a constructor, and configuration cannot disable it | S-2.5 |
| 025 | The rollback strategy restores sequences, and is named for it | S-2.6 |
| 026 | Cache state is checked by process identity, not by output | S-2.7 |
| 027 | The real-time screening is tuned against its control, not its defect | S-2.8 |
| 028 | A refused category and an uncovered one are not the same thing | S-2.9 |
| 029 | A sandbox may join a network that has been proved internal | E2 composition |

Decisions found while building the primitives:

| ADR | Decision | Came from |
|---|---|---|
| 030 | An applicability predicate has three answers, and a tool list cannot move | S-3.1 |
| 031 | A scaling sweep prevents a warm cache rather than detecting one, and always measures N=0 | S-3.2 |
| 032 | A fixture shape is generated here so the volume cannot move with it | S-3.3 |
| 033 | A replay stub that is not size-representative is an empty stub in disguise | S-3.4 |
| 034 | A threshold oracle has a third answer, and needs it | S-3.5 |
| 035 | Five percent of what: the counter budget needs a denominator | S-3.6 |
| 036 | Wall minus CPU is exact; attribution by category is not, and says so | S-3.7 |
| 037 | An envelope check needs a ratio and a floor, and the floor is measured | S-3.8 |
| 038 | The divergence point is a suffix comparison, and a sample suffices | S-3.9 |
| 039 | A sweep is a search, a plan is an opinion, and a revert is checked | S-3.10 |
| 040 | A skipped revision is not a cheap one, and one threshold oracle serves both searches | S-3.11 |
| 041 | A fitted coefficient needs a floor, and a peak needs a measured range | S-3.12 |
| 042 | A gap inside the noise names an innocent neighbour | S-3.13 |
| 043 | Sensitivity is not cost, and the gate is the reason it is a separate primitive | S-3.14 |
| 044 | A ramp is a trend, not a power law, and this primitive inverts the invariant | S-3.15 |
| 045 | Amplification is a multiple, because every retry has a limit and its curve is a step | S-3.16 |
| 046 | The fuzzer we wrap is Hypothesis, because AFL guides on the wrong thing | S-3.17 |
| 047 | A payload that costs ten times as much is withheld, not printed | S-3.17 |
| 048 | The circular question is refused by a constructor, not by a convention | S-3.18 |
| 049 | The deterministic unit is a bytecode instruction, and two corrections make it one | S-3.19 |
| 050 | A toolkit is what was imported, and a floor measures one dimension | Epic 3 composition |
| 051 | Verifying work must not require an N+1 — F6's first condition corrected | S-4.1 |
| 052 | The N+1 is linear, so "superlinear" is the wrong test | S-4.3 |
| 053 | A screen with no entry point, and four things only a project shows | Epic 4 composition |
| 054 | The commit sha is the wrong repo identity, and the machine is not in the key | S-5.1 |
| 055 | Determinism is a claim about the answer, and replay is a mode not a branch | S-5.2 |
| 056 | "Cached tokens" is two numbers with opposite signs | S-5.3 |
| 057 | Only the global ceiling halts | S-5.4 |
| 058 | "Creative" is a property, and a tier is what it costs | S-5.5 |
| 059 | The never-cascade rule is a consequence, not a special case | S-5.6 |
| 060 | The cheap tier is the hardest to cache | S-5.7 |
| 061 | Pruning is a rendering decision, and retrieval is not free | S-5.8 |
| 062 | The vendor comparison is built, and the second column is left empty | S-5.9 (partial) |
| 063 | Two append-only logs, and a cache nobody told about the router | Epic 5 composition |
| 064 | The state is a model, and the framework validates nothing | S-6.1 |
| 065 | Append-only is a trigger, and the replay cache is not a table | S-6.2 |
| 066 | Bounded is a guarantee, not a measurement | S-6.3 |
| 067 | Unrecorded is not untouched | S-6.4 |
| 068 | A correct answer with nowhere to go | Epic 6 composition |
| 069 | The double is built against the real response | S-0.7b |
| 070 | A declared version is not an installed one | S-7.1 |
| 071 | Two probes, not one error message | S-7.2 |

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

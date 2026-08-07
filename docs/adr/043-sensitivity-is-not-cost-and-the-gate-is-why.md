# 043 — Sensitivity is not cost, and the gate is the reason it is a separate primitive

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.14 asks for a known fractional slowdown injected into a target, an
applicability predicate that returns **false for single-threaded synchronous
code**, and a sensitivity curve rather than a single point.

`01-primitives.md` §8's decisive datum: the function responsible for SQLite's
25% gain accounted for about **0.15% of runtime**. A profiler ranks by cost and
would never surface it. Its worked example is starker — two functions of similar
profile weight where optimizing one yields at most 4.5% and the other yields
exactly zero. `08-audit.md` F7 then found the limit: Coz's virtual speedup works
by pausing *concurrently running* threads, and in single-threaded code there is
nothing to pause.

## Decision

**The slowdown is proportional to what the call took, not a constant.** A fixed
delay perturbs a fast call out of all proportion and a slow one barely at all,
and the slope of that is a fact about the constant.

**The gate is not a caveat on this primitive; it is the reason the primitive
exists separately from ablation.** In serial code the sensitivity *is* the share
of runtime — every millisecond added to a component is a millisecond added to the
total — so the curve reproduces ablation's answer more slowly. In concurrent code
the slope is smaller than the share, because other threads absorb part of the
delay, and that gap is the whole of what Coz's method adds. So the primitive
declares `RUNS_CONCURRENT_CODE` and S-3.1's registry withholds it where the fact
is false **and where nobody has established it** — the three-answer applicability
from ADR 030 doing the job it was built for, on the first primitive that needed
it. The test goes through `REGISTRY.select(...)` rather than asserting a
docstring, because the selection is what actually decides whether an agent is
offered this.

Writing the test for a genuinely insensitive component made the same point from
the other side: **the case cannot be constructed without concurrency.** The
target has to run alongside something longer for its delay to be absorbed rather
than added.

**The speedup is an extrapolation and says so, with r² beside it.** Slowdowns are
what can be injected; the question is about a speedup, which is the same line
read on the other side of zero. That is sound while the response is linear, and
r² is what that assumption is worth — the same rule S-3.12 applies to a peak
beyond the concurrency actually driven.

**The workload must reach the target through the attribute**, and the failure
mode is bad enough to be documented at length. A reference captured before the
substitution — a bound method passed as the workload, a `from module import
target` — calls the original and never sees the delay. `calls_to` documents the
same limitation for counting, and here it is worse: the curve comes back flat,
and a flat curve reads as *optimizing this would gain nothing*. The wrong answer,
in the direction of doing nothing. This was found by writing a test that passed
`pipeline.bulk` directly and measuring a slope of -0.0002 for a component that is
the entire workload.

**The injection reuses S-3.10's substitution** rather than patching attributes a
sixth time, so the target is restored *and the restoration is verified*.

## Consequences

**Makes easy.** Prioritization, which nothing else here does: ablation says what
a component costs, and this says what optimizing it would return.

**Makes hard.** Using it at all on a synchronous subject — deliberately, and with
ablation named as the alternative in the refusal.

**Rules out.** Presenting sensitivity as generally applicable, and reading a
speedup prediction without the fit quality next to it.

## Provenance

Three sabotage runs, each asserting the edit landed: gating on the wrong project
fact fails 2 tests; a constant delay instead of a proportional one fails 2;
treating any positive slope as sensitivity fails 1.

**S-3.13's contention tests flaked in the full suite and the cause is a property
of locks worth recording.** Measured directly, the contended samples were
`[0.856, 0.050, 0.050, 0.050, 0.050]` — only the *first* acquisition contended.
Python's locks are not fair, so a foreground thread that releases and
immediately re-acquires barges ahead of the neighbours already queued, and five
back-to-back measurements contend once. The median then reports the uncontended
cost, which is the statistic behaving correctly on a fixture that was not
representative: a component that does any work at all before entering its
critical section gives the queue a chance. With that, every sample contends and
the gap is 0.39 against a noise floor of 0.015.

Two things came out of it. The fixture now has work either side of the lock,
which is also what a real component looks like. And `context_cost` now says
outright that a median understates contention that lives in the tail — tail
latency amplification is on §3's detection list, and a median is exactly the
statistic that discards it, so the docstring points at S-1.5's rank test for
that question.

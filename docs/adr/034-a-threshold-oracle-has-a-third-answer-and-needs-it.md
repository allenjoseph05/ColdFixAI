# 034 — A threshold oracle has a third answer, and needs it

**Status:** accepted
**Date:** 2026-08-06

## Context

S-3.5 asks for `ddmin` driven by a threshold oracle rather than a boolean crash
oracle, the `dd` variant that isolates the difference between a fast and a slow
case, localization among 40 candidates in materially fewer than 40 ablations, and
handling of a subset whose removal breaks the workload entirely.

The motivation is arithmetic. Each ablation is a reset, a reseed and a workload
run; asking forty candidates one at a time is forty of those. Delta debugging
solved this search problem in 2002 and `01-primitives.md` §7 records the headline
result — 896 lines of HTML reduced to the single causative line in 139 automated
runs, with no understanding of the input's syntax.

What is not inherited from the literature is the oracle. Zeller and Hildebrandt's
test outcome is a crash: unambiguous, reproducible, free of measurement error.
Ours is *cost exceeds X*, which is none of those things.

## Decision

**`UNRESOLVED` carries the weight of the adaptation, and it does two jobs.**

The first is the story's AC 4: a subset whose ablation breaks the workload
entirely. The exception is caught, recorded against the configuration that caused
it, and the search continues — a measured failure to measure. Both algorithms
already know how to make progress when neither branch resolves, because
`UNRESOLVED` is in the original.

The second is not in the story and matters as much. **A measurement near the
threshold is a coin flip.** S-0.4 put the timing noise floor at roughly 20 ms,
about 6% of a 350 ms endpoint, so a configuration whose cost lands inside that
band decides a branch of the search on noise — and the branch taken changes which
component is named. The oracle therefore takes a `resolution`, and inside that
band it answers `UNRESOLVED` rather than guessing. This is not a new state
invented for the purpose; it is the state the algorithm already has, used for the
thing it is for. Zero is the right value for counts, which are exact.

**The 1-minimality guarantee is weakened, and the weakening is stated rather than
inherited silently.** Delta debugging's result assumes monotonicity — any
superset of an expensive configuration is expensive. Cost is *usually* monotone
in the active set, because a component either does work or does not, but not
always: a component that populates a cache another reads makes the second cheaper
by being present. Where that happens the algorithms still terminate and still
return a set that is 1-minimal *as measured*, and that is the claim to make.

**Both ends are checked before the search runs.** Two measurements, and each
failure is a finding in its own right rather than only an error:

- Everything active is not expensive → either the threshold is above what the
  workload ever costs, or the cost is not in this candidate set at all. An
  exclusion worth recording rather than a search worth running.
- Everything ablated is *still* expensive → no candidate owns the cost and no
  subset of them will. The residual after ablation is the finding, and it belongs
  to a different set of candidates. S-0.4 hit exactly this shape: ablating the
  dominant component left 507 queries, of which 504 were a second, independent
  N+1 that had been invisible underneath the first.

Without these, a search runs to completion and names an arbitrary innocent subset
with full confidence.

**Outcomes are cached, and the count reported is measurements taken.** `dd` asks
the same question repeatedly by construction — its first and third moves probe
the same configuration — so a cache is what makes the run count what it is. On 40
candidates with one culprit, `dd` asks 15 questions and runs 11 ablations;
`ddmin` runs 11. Against 40 for one-at-a-time.

**Stubs are recorded once, before the search, not per configuration.** Every
configuration would otherwise get a stub taken under different conditions, and
the search would be comparing measurements that differ by more than the thing it
varies.

**A target never called during the baseline is refused, not skipped.** It owns
none of the cost, and including it spends measurements establishing that.
Skipping it silently would make the result's candidate set differ from the one
the caller asked about.

## Consequences

**Makes easy.** Pointing the Diagnostician at a large candidate set instead of
making it choose one target at a time — which is the difference between an agent
that guesses well and one that does not need to.

**Makes hard.** Interpreting a search over a non-monotone cost. The result
records every probe, its margin from the threshold, and the closest call any
decision rested on, so a search decided by a hair does not read as confidently as
one decided by an order of magnitude — but nothing here makes a non-monotone
subject safe to search blindly.

**Rules out.** Running a search without knowing that its two ends are what it
assumes, and reporting a run count that a broken cache would flatter.

## Provenance

Five sabotage runs, each asserting the edit was detected: disabling the outcome
cache fails 1 test; removing the resolution band fails 1; narrowing the oracle's
exception handling fails 3; removing either precondition check fails 1 each.

**The cache sabotage found a defect in the instrumentation rather than the
code.** `measurements` was derived from the number of distinct configurations
seen, so with the cache disabled the real ablations doubled while the reported
figure stayed exactly the same — and the acceptance criterion this whole story is
judged on is a count of ablations. It now counts calls into the workload. Fourth
story running where a sabotage found something review had not, and the second
where what it found was in the measuring rather than the measured.

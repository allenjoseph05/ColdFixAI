# 099 — A count and a duration do not diverge alike

**Status:** accepted
**Story:** S-9.6 — reproducibility check
**Date:** 2026-08-17

## Context

*Re-runs one key experiment, bypassing the replay cache. Compares against the
recorded result. Material divergence produces `unsound`.*

The acceptance criteria hand over one undefined word — **material** — and getting
it wrong in either direction breaks something.

## Decision

### The two rules were already written down

`MetricKind` records the whole argument:

> A count is exact and reproduces to the integer. A duration here is **one
> sample**: S-0.4 measured the timing noise floor at roughly 20 ms, about 6% of a
> 350 ms endpoint, so a duration column is context for a shape, never evidence of
> a small difference.

So a comparator with one rule is wrong in one direction or the other:

- a **count** that moved at all is material. ADR 052 makes counts what raises a
  flag *because* they reproduce exactly, so seven queries becoming eight is not
  noise, it is a different run.
- a **duration** that moved is expected, and only a move beyond the floor is
  evidence of anything.

The rule comes from the kind, and the kind is **supplied by the primitive that
produced the metric** — every Epic 3 result artifact carries a `kinds` mapping.
Guessing it from the metric's name is refused, because `seconds_ablated` and
`render.calls_baseline` are not distinguishable by spelling and the wrong guess
picks the wrong rule silently.

### The control is the most important test in this epic

If a duration moving *within* the noise floor counted as divergence, every
reproducibility check would fail, every finding would be `unsound`, and the
amended S-9.8 would route every investigation back for more experiments — for
ever. That is ADR 094's hazard reached through the most mechanical attack in the
epic, and it is the one sabotage worth naming: *timing noise counts as
divergence* is the loop-forever failure.

### A metric that vanished is material, and is not a small divergence

If the recording holds `db.query` and the re-run does not, the two runs measured
different things and no comparison is possible. Reporting that as *unchanged*
because there is no difference to compute would be the S-3.1 failure — silence
read as agreement.

A duration recorded as zero that moved is material too, and the reason it gets
its own branch is that dividing by it to say so would be arithmetic nobody can
check. `relative_change` returns `None` there rather than `inf`, because a report
carrying `inf` is carrying a number that means *undefined*.

### Nothing here measures anything

The re-run arrives as a callable and there is **no parameter through which a
number could be supplied instead of executed** — `CLAUDE.md` puts the measuring
in the harness, and an auditor that produced its own figures would be the one
place that rule could not be enforced. Asserted by signature, and by the absence
of any clock in the module.

The cache is bypassed with `ReplayMode.OFF`, which S-5.2 built for exactly this
and recorded as a mode rather than an `if use_cache:` at every call site.

### What this costs is stated rather than hidden

A re-run is a real execution against a real subject, so auditing a
`longitudinal.soak` doubles an hours-long experiment. The audit does not choose
to spend that — it is handed an executor and the caller decides — and
`CostClass` on the primitive says in advance what the bill will be.

## Sabotage

Thirteen properties, all caught, no survivors — though the first pass **skipped
the most important one** on a pattern that did not match, which is the failure
mode the runner's harness-fault detector does not cover: a `SKIP` is not a
`CAUGHT`, and a pass reporting twelve of thirteen is not a pass. Re-run
individually and caught.

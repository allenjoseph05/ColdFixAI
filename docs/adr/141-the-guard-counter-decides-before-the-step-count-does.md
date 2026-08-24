# 141 — The guard counter decides before the step count does

**Status:** accepted
**Date:** 2026-08-24

## Context

S-13.5 measures whether the playbook actually makes grounding cheaper —
`00-BRIEF.md` §6's learning curve, and §5 step 13's acceptance that *the tenth
Django project takes materially fewer Explorer steps than the first*.

It was filed as blocked on 2026-08-23 and unblocked the same day by S-7.14 and
S-13.7. That history is the reason the module exists in the shape it does: until
S-7.14 there was no such thing as an Explorer step, and until S-13.7 nothing read
a playbook entry, so both arms of the ablation were the same run twice. A harness
built then could only ever have measured zero.

This is also a study of the project's own memory, written by the party hoping it
works. Every decision below is against that.

## Decisions

### 1. The curve reports; the ablation concludes

The backlog note is explicit: the series *declines if the playbook works, and also
if later projects happen to be easier*, and nothing longitudinal separates those.

So `Curve` has no verdict that claims causation. `direction` says what the series
did, `describe()` states in the output that this is **not evidence the playbook
caused it**, and it names the ablation as the measurement that answers the
question. A reader who quotes the curve as proof has been told not to, by the
curve.

### 2. No rank test, because `rank_test`'s own docstring forbids it here

This is the finding that changed the design, and it came from reading the
instrument before reaching for it:

> on heavily tied data — a metric taking three distinct values — it runs about
> 30% the other way, **which is the unsafe direction**. **Counts are the tied
> case, and counts do not need this test: they are deterministic, and a
> difference in them is read directly.**

Steps-to-ground is a count. Using the nearest available comparison would have
produced p-values biased towards *inventing* a difference — in the one study
whose whole job is to be able to report there is none.

What replaces it is the shape the interleaving already provides. Rounds are
**paired** — the same repository, both ways, adjacent in time — so the statistic
is a sign test over rounds and the interval is Wilson's over that proportion,
reusing `eval/ablation.py`'s `wilson` rather than adding a second one.

The lower bound must clear a half rather than the point estimate merely leading.
Six wins in ten is a lead whose interval runs from 26% to 88%, and a corpus that
could as easily have produced the opposite ordering has not shown one. That is
the Adversary study's third test in the form a paired study takes.

### 3. The guard is checked before the step count, and returns before it

`CLAUDE.md`'s non-negotiable — *guard counters on every metric; queries down while
rows explode is not an improvement* — has an exact reading here. **A run that took
fewer steps because it gave up three stages earlier has not learned anything.**

So stage completion is compared first, in both the curve and the ablation, and a
fallen guard returns `GUARD_FELL` before any step arithmetic happens. Reporting it
*alongside* a step delta would put a number next to the reason that number means
nothing.

Both tests for this shape are written so the step count **improves**: the curve
case halves its steps and grounds four stages instead of nine, and the ablation
case wins every round on steps. A study reading AC 1's figure alone would call
each of them its best result.

### 4. Both arms are run here, and a stored measurement is unrepresentable

`ablate` takes two callables, exactly as `bench.compare` does, and for a sharper
version of the same reason: comparing a fresh retrieved run against a step count
recorded last week is the stored-baseline false positive S-1.6 removed — and here
the recorded number would predate the very entries under test.

`rounds` is floored at `MINIMUM_GROUP_SIZE` and checked **before anything runs**,
because each round is two full groundings of a real repository.

### 5. The schedule moved into `bench.interleaving`, where the module says it lives

That module's docstring already claims the order as the thing it owns — *what it
owns is the order the samples are taken in, which is the part that decides
whether the comparison means anything.* A second study needing the same
interleaving could copy the loop or take it from there; a copy is a second answer
to *what a fair schedule is*, and the two would drift the first time either moved.

`schedule()` is extracted and `compare()` now uses it, so there is one owner and
one set of tests for the property. Sabotaging it to a block design fails three
bench tests and one of this story's.

### 6. Process state is recorded and never acted on

The backlog note is specific: **do not copy S-0.4's fixed warm-up discard.**
Barrett et al. found at most 43.5% of VM/benchmark pairs reach steady state at
all, so *discard the first N* is an assumption that is wrong more often than not.

`Grounding.process_state` is a column. Nothing in this module filters on it.

### 7. An observation has to be one

Four refusals in the constructor, and the third is the load-bearing one:

- a negative step count is not a count;
- a completion outside ADR 009's nine is outside the pipeline;
- a run recorded as **ground with a stage incomplete** did not come from a run
  this system finished — `GroundingRun.finish` refuses exactly that;
- a **withheld arm that was offered entries** is the confound the ablation exists
  to remove, and a study carrying it would compare the playbook against itself.

## Consequences

- **This harness has no production caller, and that is the category rather than a
  gap.** `eval/ablation.py` established it one epic earlier: an evaluation study
  is driven by a person running it, not by the graph, and `study()` there is
  imported only by its tests. That is a different thing from `ExperimentRef` and
  `gates_for`, which are machinery the *pipeline* would use and does not.
- `observed()` is the join to S-7.14's loop, so the harness reads what the
  Explorer produced rather than a second definition of *a step*.
- **Epic 13 is complete** — S-13.1 through S-13.7.
- **Sabotage: 5 properties, all 5 caught.** Removing either guard check, weakening
  the interval to a point estimate, dropping the withheld-arm confound check, and
  replacing the interleaving with a block design each fail a test that names what
  was lost.

# 158 — The vantage rides on the numbers it describes

**Status:** accepted
**Date:** 2026-08-27

## Context

ADR 157 closed S-17.5 with a decision and an obligation. The decision: the
subject measures itself and the harness receives the numbers. The obligation:
*"What the follow-on work needs is a way to say **this measurement came from the
subject** — not permission to overwrite."*

S-17.6 is that type. The question it actually turned on was not what to record —
`Vantage.HARNESS` / `Vantage.SUBJECT` was never in doubt — but **what carries
it**.

## The obvious answer, and why it is wrong twice

The obvious shape is a keyword argument, and the backlog note assumes it:
*declared rather than observed, which is `distribution`'s precedent in the same
function*. So the first implementation was

```python
def measure_once(invoke, counters=(), extra_counters=None, *, vantage=Vantage.HARNESS): ...
def scale_volume(..., vantage: Vantage = Vantage.HARNESS) -> ScalingResult: ...
```

It type-checked, it read correctly, and 14 tests passed against it. Two separate
checks found it wrong.

### 1. The sabotage pass: a forwarded parameter can be un-forwarded

Deleting `vantage=bound.vantage` from `screen_growth`'s call to `scale_volume`
broke nothing. Every existing binding is `HARNESS`, so a test that screens one
and asserts `HARNESS` passes whether the value travelled or was defaulted. The
AC-4 test was asserting the default.

That is a property of the design, not of the test. A value that defaults to the
common case and is forwarded by hand across three layers is a value that is
correct until someone adds a fourth call site — and the failure is silent,
because the wrong answer is also the usual answer.

### 2. `diagnosis.schema`: an enum parameter is a *design* parameter

`test_the_harness_half_and_the_design_half_are_separated` failed, and this was
the finding. `schema_of` splits a primitive's parameters by asking whether a JSON
value can express the annotation — that is what makes `scales`, `distribution`
and `counters` answerable by a model and `invoke`, `reset` and `extra_counters`
the harness's. `Vantage` is a `StrEnum`, so it landed on the model's side, by
exactly the branch of `_describe` that makes `distribution` answerable.

**A design would have been offered the question *should the harness trust its own
clock*.** S-17.5 measured the cost of the wrong answer: a linear workload fitted
`CONSTANT`, published as an exclusion. Of the two failures this is the worse one,
and neither the story nor the note anticipated it — S-8.2's schema test did,
nine stories earlier, because it asserts a partition rather than a list.

## Decision

**The vantage is not a parameter. It is read off `extra_counters`.**

```python
@dataclass(frozen=True)
class Reported:
    counters: Callable[[], Mapping[str, float]]

Counters = Callable[[], Mapping[str, float]] | Reported

def vantage_of(extra_counters: Counters | None) -> Vantage: ...
```

`measure_once` branches on `isinstance(extra_counters, Reported)`. A plain
callable is the harness's own vantage and stays the ordinary case, so every
existing call site is unchanged and the default the note demanded is preserved —
it is simply no longer spelled as a default.

This is S-8.12's `Measured` at the `Executor` boundary again, which ADR 157 named
as the precedent: widen the boundary with a type rather than smuggle a value
through a seam meant for something else. Three things follow from putting the
declaration and the numbers in one object:

1. **Nothing can forget to forward it.** `scale_volume` already forwards
   `extra_counters`; the vantage arrives because the numbers do. The sabotage
   that survived is not expressible against this shape — there is no separate
   thing to drop.
2. **A subject-vantage run with nothing reported is unrepresentable.** The
   parameter version raised `MetricSetError` for it. `Reported` holds the
   supplier, so the case is gone rather than caught, and one refusal was deleted.
3. **The design half cannot reach it.** Both arms of `Counters` bottom out in a
   callable, so `_describe` returns `None` and the parameter stays bound. A test
   now asserts that directly, including that `extra_counters` must *stay*
   unspecifiable, since a describable union would hand the vantage back.

`Vantage` survives as what a result records — `ScalingResult.vantage`,
`ScreenedWorkload.vantage`, `Conditions.vantage` in a published null result. It
is derived at exactly one place and never passed.

## What is refused from a subject

`HARNESS_ONLY_METRICS` is `materialized`, `cpu_seconds`, `blocked_seconds`:
`materialized` counts what was drained *here*, and the two rusage figures come
from reading *this* interpreter. A subject reporting them is describing the
harness while the reader believes it is reading the subject, which is worse than
reporting nothing.

It is a strict subset of `RESERVED_METRICS`, and that distinction is the story.
`seconds` is reserved and a subject *can* honestly measure it — refusing every
reserved metric would leave the subject vantage unable to report the one number
it exists to report. Under `Reported` the harness never takes `seconds` itself,
so there is no second record for it to collide with; a `Reported` that omits it
is refused, because nobody else looked.

## Consequences

**`Binder`, `Executor` and `Measurer` are unblocked.** ADR 157 held all three
behind *"neither should be written before the answer above is a type"*. It is a
type. A `Binder` that drives a containerised subject returns a `BoundWorkload`
whose `extra_counters` is a `Reported`, and every layer above it publishes that
fact without being taught to.

**The adapters are unchanged and still correct.** `explorer.work.drive` already
times inside the subject; what it lacked was a way to say so. Wrapping its return
is the whole of the adapter-side change, and it belongs to `Binder`.

**Two mechanisms found one defect each, and neither could have found the
other's.** The sabotage pass found the un-forwardable value — a missing test, in
the usual shape. It could not have found the schema leak, because that one had no
missing test: S-8.2's partition assertion already existed and failed the moment
the parameter appeared. And the full suite could not have found the forwarding
gap, because a defaulted value forwards correctly in every composition that
exists today; nothing was red.

The general point is about S-8.2's test rather than about this story. It asserts
that `scale_volume`'s parameters *partition* into a design half and a harness
half, listing both sides — so it fails when a new parameter lands on the wrong
one. A test that had listed only what a model may specify would have passed here,
and a model would now be choosing whether the harness trusts its own clock.
Assert the partition, not the side you care about.

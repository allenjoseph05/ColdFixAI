# 163 — Thirteen results, one boundary, and a fit that cannot be chosen

**Status:** accepted
**Date:** 2026-08-28

## Context

S-17.11 produces `Resources.executor`, the thing that turns a designed experiment
into a measurement. The call is uniform — resolve the primitive, merge the
design's arguments with the bound half `PrimitiveSchema.bound` names, run it — and
the *conversion* is not: thirteen primitives return thirteen distinct result types
and none of them exposes a common accessor.

## Where the readers live

**In `diagnosis/readings.py`, beside `Measured`.** `Measured` is a diagnosis type
and primitives are the layer below, so a reader living on the registry entry would
point `primitives/` at `diagnosis/`.

What that costs is a table the registry can outgrow, so the test is a **partition
over `REGISTRY.names`** rather than a list: `set(READERS) == set(REGISTRY.names)`,
asserted in both directions. Listing only the readers would pass while a
fourteenth primitive arrived without one — and that failure is not an import error
at startup. The agent selects the instrument, the design is written and paid for,
and the executor raises on a turn that has already spent a frontier call. A second
test checks each reader's parameter annotation against its primitive's return
type, because a table keyed by name has nothing structural stopping a reader being
filed against the wrong entry.

## Not every primitive measures a mapping of numbers

`Experiment.measurement` has a validator: *an experiment with no measurement is a
conclusion drawn from reading code, which the first non-negotiable exists to
prevent.* An empty mapping is refused by schema.

Five results already are mappings of numbers. Four are searches that return a
**decision** — a commit, a set of culprits, an input, a growth class — and for
those the honest numbers are **the probe values the search took along the way**.
*Six probes taken* is a fact about the search; the cost measured at the bad commit
is a fact about the subject, and only the second is a measurement. A search whose
every probe failed or was served from its cache is refused rather than logged with
its own counters standing in for numbers about the subject.

A cached probe is excluded from the summary, because the primitive records
`cached` for exactly this reason: the count that matters is measurements taken,
not questions asked, and a cache hit is not an ablation.

## The narrowing this story records rather than papers over

**`Measured.fit` is singular and a volume sweep fits every metric it measured.**

`audit/scales.py` reads `fit.exponent` and `fit.power_r_squared` and raises
`FIT_TOO_POOR` from them, so the choice is metric-specific: a noisy `seconds` fit
would object to a finding whose claim is about `db.query`. And nothing in an
`ExperimentSpec` names which metric the finding will rest on — `target` is what the
instrument is pointed at, spelled like `shop.books.list`, and the metric is chosen
by the interpretation, which runs *after* this.

So a fit travels only when the sweep fitted exactly one metric, and is absent
otherwise. Absence is already meaningful here — S-9.2 refuses to judge a rejection
that came from no sweep — and it errs toward *unjudged* rather than *wrongly
judged*, which is S-2.9's bias for S-2.9's reason.

**This leaves S-8.12's widening inert for the common case**, and that is a real
cost rather than a tidy resolution: a volume sweep is the instrument the loop
reaches for most, and it will now always record its fit as absent. The repair is
to widen `Measured.fit` to a mapping and let the interpretation select, which
touches `Measured`, `Experiment`, and `audit/compose.py`'s `fits_from` and
`_fit_for`. It is filed as its own story rather than folded in here, because it
changes an append-only log's schema and that is a decision about what a checkpoint
holds.

## Two defects this story found in its own first draft

**`stats()` refuses fewer than two samples.** The first readers called it on probe
costs and on per-fraction samples, so a bisection that took one probe, or a
sensitivity point sampled once, would have raised `StatsError` from inside a
reader — a crash where a measurement existed. `_median` reports the single value
directly: not a weaker summary, the only one there is.

**One guard was unreachable.** `read_input_search` refused an empty candidate
tuple, and `input_search` already refuses to *construct* a `Campaign` with none —
*a campaign with no candidates is a failed run rather than a null result*. That is
S-7.4's redundant condition, reading as protection while protecting nothing, and
S-7.4's remedy is the one applied: collapse it, and verify the intent from the
other side.

## Consequences

**Four of the six are real**: `hands`, `ground`, `bind`, `executor`. `measure` and
`probe` remain, and nothing assembles a `Resources`.

**Eleven readers are tested against hand-built results and two against real runs**,
and the file says which is which. `scaling.volume` and `ablation.stub` run
in-process against the planted fixtures; the others need a container, a load
generator or a git history. The hand-built ones use values that could not be
produced by reading the wrong field — every number distinct — so a reader that took
`first` where it wanted `last` fails on the value rather than on the shape.

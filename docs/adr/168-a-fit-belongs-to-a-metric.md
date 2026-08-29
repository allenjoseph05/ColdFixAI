# 168 — A fit belongs to a metric

**Status:** accepted
**Date:** 2026-08-29
**Amends:** ADR 163

## Context

S-17.11 built the executor and could not fill `Measured.fit`. The reason is in
that story's own note: a volume sweep fits **every** metric it measured;
`audit/scales.py` reads `exponent` and `power_r_squared` off a single `Fit` and
raises `FIT_TOO_POOR` from them; and nothing in an `ExperimentSpec` names the
metric a finding will rest on, because the interpretation picks that and runs
*after* the executor.

So a fit travelled only when a sweep fitted exactly one metric. **No real sweep
does.** The consequence was not a narrowing — it was that S-9.4's scale-adequacy
check never ran on anything, and S-8.12's widening was inert for the instrument
the loop reaches for most.

## Decision

**`Measured.fits` and `Experiment.fits` are mappings from metric to fit**, and
the audit selects by the metric the finding cites.

`_fit_for` was *the most recent fit recorded* — the only rule available when an
experiment carried one, and recency is not the claim. It now takes a metric and
returns the most recent experiment that fitted **that** metric. `audit_finding`
takes `metric`, which `Resources.metric` already documents as exactly this:
*which of the workload's measurements the symptom quotes*.

The pair of tests is the point: a deliberately unusable curve (r² of 0.01) filed
under `seconds` beside the real one, and the audit judging the cited metric's fit
rather than the recent one. Both driven through a real investigation, because the
mapping is built by the loop from what the primitive produced — supplying one by
hand would test the selection against a shape nothing produces.

## Exclusions get the span check and not the fit-quality one

An `Exclusion` declares no metric. Its claim is *not the database, queries flat
across 100x scale* and the metric lives in the prose, so there is no name to
select a curve by. Passing the finding's metric would judge one hypothesis's
exclusion against another hypothesis's claim.

`_single` gives `audit_exclusion` the one fit per experiment where an experiment
fitted exactly one metric: with one curve there is no choice to get wrong, and
with several there is no basis to choose. An absent fit still leaves the **span**
objection, which is metric-independent, and drops only the r² one — which is what
already happened for every multi-metric sweep. Nothing regresses; declaring an
exclusion's metric is follow-on work.

## Two things this got wrong first

**A validation that encoded the wrong invariant.** The first version refused a
fit for a metric absent from `measurement`, mirroring the `kinds` check. The
thesis fixture failed it, and the fixture was right: `kinds` describes **one
measured number**, so it must name one; a fit describes a **series across scale
points**, and a reader may record the points under derived names — the fixture
reports `db.query.n10`, `db.query.n20`, `db.query.n40` for a curve fitted on
`db.query`. Requiring the namespaces to coincide refused a correct reading. There
is no such check, and the docstring says why.

**A stale claim, corrected rather than pinned.** `fits_from` said experiments
that fitted nothing are *absent rather than present-and-empty*, which was a real
distinction when the value was `Fit | None`. With a mapping it is not — an empty
mapping *is* the absence — and sabotage confirmed that keeping the empty entries
changes no outcome. The filter stays as tidiness and the docstring now says so,
which is S-2.6's `--volumes` lesson: a comment claiming a protection that is no
longer there is worse than no comment.

## Consequences

**S-9.4's scale-adequacy check can run for the first time on a real sweep.** It
was reachable only through a hand-supplied fit before, and the composed path
supplied none.

**The golden evidence chain changed by two lines** — `"fit": null` became
`"fits": {}` — regenerated from `a_chain()` rather than hand-edited, and the diff
is those two lines and nothing else.

**Twelve `audit_finding` call sites now name their metric.** That is the honest
cost of the change: the metric was always required to select correctly and the
old signature let a caller omit what it could not do without.

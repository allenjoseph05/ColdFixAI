# 015 — The rank test is written out, and the statistics stay standard-library

**Status:** accepted
**Date:** 2026-08-03

## Context

S-1.5 asks for four things: summary statistics, a curve fit against a scale
variable, a growth classification, and a rank-based significance test. The
note says use a rank test rather than a t-test, because timing distributions
are not normal.

Three of the four are already in the standard library. `statistics` provides
`fmean`, `median`, `stdev`, `linear_regression` and `correlation` — enough for
both fits and both r² values with no arithmetic of our own. The gap is the
hypothesis test: the standard library has none.

That leaves a dependency decision. `scipy.stats.mannwhitneyu` is the obvious
answer, it is the reference implementation, and it selects between an exact and
an asymptotic test automatically. Against that: numpy and scipy are a
hundred-odd megabytes in every sandbox container, for one function.

## Decision

**Standard library only. The rank test is written out here.**

Roughly forty lines: average ranks with the tie correction, U from the rank
sum, a continuity correction, and a normal tail from `math.erf`. It is the one
piece of real statistics in the project, and it is tested against its own
definition — U is *defined* as the number of pairs where an observation from
one sample beats one from the other, and the test computes that directly and
compares.

**Below eight observations per group it refuses.** The normal approximation is
not trustworthy there, and returning a p-value that looks like every other
p-value would be worse than returning nothing. The refusal costs nothing the
project actually wants: S-1.7 certifies a noise floor from 20–30 baseline runs
before an experiment may start, so a comparison arriving with fewer than eight
per group has skipped a step rather than found a case this cannot serve.

**A flat metric is handled before either fit is attempted.** "Queries constant
at 7, 7, 7 across 100x scale" is the canonical exclusion this whole system
exists to be able to publish — and it is also the input a log-log fit
degenerates on, since zero variance in the metric makes r² a division of zero
by zero. It returns `CONSTANT` with an r² of 1.0. A constant is exactly what a
constant explains.

**Both fits are returned, each with its own r².** `slope` answers "how much per
item" and is the number to quote; `exponent` answers "what shape" and is what
the classification rests on. Their disagreement is signal: a perfectly
quadratic metric has a power r² of 1.0 and a poor linear r², and a caller
reading only the slope would quote a cost per item that is wrong at every scale
but one. The growth thresholds are recorded on every `Fit`, so a finding cites
the threshold it was classified under rather than leaving a reader to work out
which version of the file was running.

## Consequences

**Makes easy.** No dependency, no container weight, no version pinning for a
scientific stack. Every number in the module can be read off the source.

**Makes hard.** We own the correctness. The mitigation is that the two pieces
that could be silently wrong are checked against their definitions rather than
against themselves: U against pairwise counting, and the p-value against an
exact permutation test that enumerates all 12,870 relabellings of two groups of
eight.

That cross-check produced the honest limits, which are worth stating because
they are measured rather than assumed:

| Regime | Approximate vs exact |
|---|---|
| Body of the distribution | agrees within a few percent |
| Far tail | conservative by roughly 10×; understates a real difference |
| Heavily tied data (three distinct values) | about 30% low — the unsafe direction |

The tied case is the one to watch, and it is tolerable for a specific reason: a
metric taking a handful of values is a count, and counts are deterministic —
`01-primitives.md` §2 says so, and a difference in them is read directly rather
than tested. Timings, which this function exists for, carry almost no ties. The
tie correction *is* applied and is the exact null variance under ties; it is
not the cause. Coarse discreteness is, and no variance correction fixes that.

**Rules out.** Nothing permanently. If a later story needs an exact test at
small n, or the USL fit in the load primitive (three coefficients, non-linear
least squares) needs `scipy.optimize`, that story takes the dependency and
supersedes this. Deferring it until something needs more than one function is
the point — this decision is "not yet", not "never".

## Provenance

`docs/10-BACKLOG.md`, S-1.5 note (rank test, not a t-test; timing distributions
are not normal). The accuracy table was measured while writing the tests, and it
corrected a claim this file nearly shipped with: that the tie correction stops
ties inflating significance. It does the opposite — it lowers the variance and
therefore raises significance. The test that asserted the wrong thing was
rewritten to pin the measured behaviour instead.

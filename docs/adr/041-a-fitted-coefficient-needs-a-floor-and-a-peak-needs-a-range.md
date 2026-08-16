# 041 — A fitted coefficient needs a floor, and a peak needs a measured range

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.12 asks for concurrent load at increasing levels at a fixed data size,
throughput fitted against concurrency returning contention (α), coherency (β) and
Nmax, a Little's Law cross-check for self-consistency, and findings marked
diagnose-only.

The story's note is the design brief: *the fitted coefficients are diagnostic,
not just descriptive — high α points at a shared resource, high β at coordination
cost. Surface them to the agent, not just the curve.*

## Decision

**The fit is ordinary least squares, because the model linearizes.** Dividing the
USL through gives `(γN/X(N) - 1)/(N-1) = α + βN`, a straight line in N whose
intercept is contention and whose slope is coherency. ADR 015 keeps the
statistics in the standard library and this needs nothing beyond
`statistics.linear_regression`.

**Two guards were added because the tests demanded them, and both are about the
same thing: a fit will always return numbers.**

*A sign test on a fitted coefficient needs a tolerance.* Least squares never
returns an exact zero. A curve generated with β = 0 fits β = -8.6e-08 and one
generated with α = 0 fits α = -1.2e-06 — neither is negative contention, they are
zero with arithmetic dust on them, and a strict `>= 0` declares a perfectly
ordinary Amdahl-shaped system unfittable. The floor is 1e-3, chosen against what
a load measurement can resolve rather than against the arithmetic: α = 0.001 says
one part in a thousand of the work is serialized, and no timing-based load test
separates that from zero. This is the same rule S-3.8 needed for its envelope, at
the other end of the scale.

*A peak beyond the measured range is an extrapolation.* β is never exactly zero,
and `sqrt((1-α)/β)` turns a tiny β into an enormous peak — measured here, a curve
generated with β = 0 and rounded to whole completions produced β = 6.5e-5 and a
confident peak at N=118 from data that stopped at 16. `Nmax` is withheld when it
lands more than twice the largest concurrency actually driven. The whole value of
this primitive is that its numbers came from running the thing.

**A materially negative coefficient is reported as measured and the fit is marked
as not fitting.** Replacing it with zero would hide the finding behind a curve
that looks fitted; withholding the number would hide it entirely. The ceiling and
the peak are withheld because they are derived from it.

**Little's Law is a validity check, not a result.** In a closed system `N = X × R`,
so comparing observed concurrency against throughput times residence time costs
one multiplication and catches the failure nothing else here sees: a load
generator that never sustained the concurrency it was asked for still produces a
smooth, plausible, meaningless USL fit. The finding says to fix that *before*
reading the coefficients, because the order matters.

**Diagnose-only is enforced twice, and the second one is the real one.** The
finding carries the disposition, and its mechanism sentence is written so S-2.9
refuses it independently — `RepairableFinding` runs the classification in its
constructor, so a mechanism naming contention has no route to repair whatever
this module remembers about itself. `00-BRIEF.md` §3: output equivalence cannot
detect an introduced race, so no falsification test this system writes makes a
contention fix safe.

**Threads, and the GIL is stated rather than hidden.** For CPU-bound Python the
pool does not produce real concurrency — and the Little's Law check is exactly
what notices, because a level that never had N in flight fails `N = X × R`. Real
subjects here wait on a database, and a thread waiting on a socket has released
the GIL.

## Consequences

**Makes easy.** Handing an agent α, β and a sentence about what each points at,
which is what the story's note asks for and what a chart cannot do. S-3.13's
isolation work, which is the natural next instrument when α is high.

**Makes hard.** Getting a peak out of a short curve — deliberately. And fitting
anything on a subject whose load generator is not honest, which is now a reported
failure rather than a silent one.

**Rules out.** Reporting a peak nobody measured near, a negative coefficient as a
quantity, and any contention fix at all.

## Provenance

Four sabotage runs, each asserting the edit landed: accepting negative
coefficients as physical fails 2 tests; removing the extrapolation limit fails 1;
making Little's Law always agree fails 2; marking the finding repairable fails 2.

**The extrapolation sabotage initially passed**, which showed the branch was
unreachable from any test: with the coefficient floor in place, every curve in
the suite had either a real peak inside the range or a β below the floor, so
nothing exercised the limit. A test was added for the case the rule exists for —
a real but small β measured only to N=8, peaking at 22 — along with its control.
A guard no test reaches is a guard nobody has checked.

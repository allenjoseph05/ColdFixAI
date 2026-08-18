# 044 — A ramp is a trend, not a power law, and this primitive inverts the invariant

**Status:** accepted
**Date:** 2026-08-08

## Context

S-3.15 asks for a workload run repeatedly at a fixed size over an extended
period, metrics fitted against **elapsed time** rather than input size, an
applicability predicate requiring a long-running deployment model, and a
configurable duration with a hard cap.

`01-primitives.md` §5 gives the shape it catches: error rates creeping over
hours and then spiking overnight, from a leak that surfaced only after sustained
traffic — *while a thirty-minute load test the week before passed cleanly*. It
also calls this the most expensive primitive and says never to run it on a CLI
tool.

## Decision

**This primitive inverts the invariant every other one enforces, and that is
stated rather than left to be noticed.** ADR 026 and S-3.2 require a fresh
process or an explicit clear between measurements, because state carried from one
run to the next makes the second look cheaper. Here the carried state **is the
subject**. A soak that reset between iterations would find nothing, always, and
report that as *no ramp*.

**A ramp is read from the straight line, not from the growth class**, and this
was a real defect rather than a preference. `fit_growth` returns both a linear
fit and a power-law one, and the power law is the right model against input size
and the wrong one against elapsed time: the first sample sits at t≈0, where a
logarithm is undefined, so the exponent is either withheld — making `ramping`
false for every soak ever run — or computed across an axis distorted by a first
point at 10⁻⁶ seconds. What a ramp actually is, cost drifting upward as the
process stays up, is a positive slope.

Three conditions on it, each removing a way of being confidently wrong: the slope
is positive, the line explains the data (r² ≥ 0.5, or a noisy flat series
produces a slope by accident), and the modelled climb across the window is at
least a tenth of the level the metric started at (or a soak reports a leak from a
drift of a millisecond an hour).

**Nothing is discarded, including the first sample.** S-1.2's rule, inherited:
Barrett et al. found at most 43.5% of VM/benchmark pairs reach a steady state at
all, so dropping the first N is wrong more often than right — and here the first
sample is often the interesting one.

**A rising line is not a finding without a control.** Four hours of soaking is
four hours during which the machine also changed: another process arrived, a disk
filled, a CPU throttled. Every one produces the same rising line as a leak. So a
reference workload can be measured in the same loop — alongside rather than
afterwards, because a control run after the subject measures a different hour —
and a rise they share is not reported. Without one, the result says outright that
its trends are about the subject *and* the machine together.

**Exceeding the cap is refused, not clamped**, and the sabotage run made the case
better than the argument did. Clamping does not merely produce a shorter soak
reported as a full one: the refusal test, with the clamp in place, **started an
actual six-hour run** and had to be killed. A silent clamp turns a rejected
argument into a commitment of the cap's entire duration, on the most expensive
primitive in the set.

**The exclusion is qualified by its own duration.** *No ramp over this period* is
only as strong as the period is long, and §5's example is precisely a leak that a
shorter test missed.

## Consequences

**Makes easy.** The one defect class nothing else here can see: cost that grows
with uptime rather than with input.

**Makes hard.** Running it at all without establishing the deployment model —
`LONG_RUNNING_PROCESS` is required, and ADR 030's third answer means an
unestablished fact withholds it too. On the most expensive primitive in the set,
that is the highest-value place for that rule.

**Rules out.** A soak that resets, a ramp claimed from two endpoints, and a run
that quietly lasts less — or more — than it was asked to.

## Provenance

Three sabotage runs, each asserting the edit landed: clamping the duration
instead of refusing fails 1 test (and started a six-hour soak); dropping the
control subtraction fails 1; treating any positive slope as a ramp fails 2.

The power-law defect was found by the tests, not by the sabotage: five of them
failed at once because `growth` came back `None` for every constructed series
whose first sample sat at elapsed 0. Real runs happened to pass, because
`perf_counter` differences are never exactly zero — which is worse, not better:
the primitive would have worked by accident on a first sample at 10⁻⁶ seconds and
computed its exponent across fourteen orders of magnitude of x.

# 045 — Amplification is a multiple, because every retry has a limit

**Status:** accepted
**Story:** S-3.16 — fault injection
**Date:** 2026-08-08

## Context

S-3.16's fourth acceptance criterion reads:

> **Detects retry amplification** — outbound call count rising superlinearly under injected latency

Implemented literally, that means fitting outbound call count against injected
latency and reporting amplification when the fit comes back worse than linear.

It does not work, and the measurement says why. A four-attempt client, measured
against latencies of 0, 80, 120 and 160ms with a 40ms timeout:

```
retrying  [(0.0, 1), (0.08, 4), (0.12, 4), (0.16, 4)]   growth=None   factor=4.0
patient   [(0.0, 1), (0.08, 1), (0.12, 1), (0.16, 1)]   growth=constant factor=1.0
```

The retrying client's curve is a step, not an exponential. It cannot be anything
else: a client with a retry limit hits that limit and stops, so the curve
saturates by construction and `fit_growth` returns no class at all. A
superlinearity test would have reported **nothing** for the textbook amplifying
case — the one shape this check exists to catch — while the well-behaved control
returned a clean `constant` and looked identical in kind.

The AC's wording describes what amplification does to a *fleet*, where the
saturating step of each client multiplies across every client and the aggregate
curve really does bend upward. A single container measuring a single client
cannot see that curve, which is the same limit `08-audit.md` F1 cited when it
downgraded the metastability gate in the first place.

## Decision

`amplifying` is a **multiple of the undegraded call count**, not a fitted
exponent: `max(calls) / calls_at_zero_latency >= 2.0`.

The undegraded level is measured in the same run, with the same interposition in
place and counting, so the denominator is a measurement rather than an
assumption. Levels are sorted before measuring, so `responses[0]` is the
undegraded one however the caller listed them — an unsorted list would otherwise
divide every count by a degraded baseline, which is the direction that hides
amplification.

`growth` is still fitted and still reported. It is just not what decides, and
when it comes back `None` the explanation says what that means — "the count fits
no growth class, which is what a retry limit looks like from outside: it steps up
to the ceiling and stays there" — rather than printing `None` at whoever reads
it.

**Threshold: 2.0, with a stated denominator.** One extra call on a slow
dependency is a retry doing its job, and flagging it would flag every subject
that has any retry policy at all; a check that fires on everything gets switched
off, and then the amplifying ones go through too. Two means the request was sent
at least twice for every one that arrived.

## Consequences

The check catches the common case: a client whose retry policy multiplies
outbound load as its dependency slows. That is what §15 says is still executable
after F1's downgrade.

It does not prove safety, and every path out of this module says so. A subject
that does not amplify over the measured range has passed one check at one scale
in one container. The report for the non-amplifying case carries that sentence
verbatim, and a test asserts it is there — because a check that quietly reads as
a clearance is worse than no check, which is exactly what F1 downgraded the gate
to avoid.

It also cannot see amplification that begins beyond the latencies measured. The
range is the caller's, and the finding is bounded by it in the same way S-1.7's
exclusions carry their preconditions.

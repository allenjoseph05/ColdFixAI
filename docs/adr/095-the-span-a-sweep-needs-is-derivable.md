# 095 — The span a sweep needs is derivable, not a round number

**Status:** accepted
**Story:** S-9.4 — scale adequacy attack
**Date:** 2026-08-17

## Context

*Checks whether tested scales were large enough to separate linear from
superlinear; flags fits with poor r² or too few points.*

## Decision

### Nothing here calls a model

S-9.4 sits in an epic of *attacks*, which reads as adversary calls. `CLAUDE.md`
is explicit: *do not add a model call where a function would do; counting, curve
fitting, stack grouping and byte comparison are code.* Point counts, spans and r²
are arithmetic. An audit that asked a model whether three points were enough
would pay for an opinion about a number it could compute, and get a less reliable
one. A test asserts the module imports no client and no session.

### The threshold is derived from two figures this project already measured

The obvious implementation picks a round span — *ten times* — and cannot say why.
The number falls out:

- `SUPERLINEAR_ABOVE` is **1.15**, so the gap a sweep must resolve is **0.15**;
- S-0.4 measured **12%** run-to-run drift.

A power fit is a straight line in log space, so the exponent is
`log(metric ratio) / log(scale ratio)`, and relative error `e` in the metric
becomes `e / ln(span)` in the exponent. Requiring that to be `sigma` times
smaller than the gap gives `span >= exp(sigma * e / gap)` — **11.0** at 12% and
3σ, which is why this project's fixtures sweep 10× and 100× rather than 2×.

The consequence worth having runs the other way: a harness with a **certified**
noise floor needs far less. At S-1.7's 2% the requirement falls to **1.5×**. So
`required_span` takes the noise as a parameter and a caller holding a
`Certification` passes what it measured, rather than being held to a constant
derived from somebody else's machine.

The number that makes this non-arbitrary: at 12% noise a 2× sweep determines the
exponent to **±0.17**, and the entire gap between linear and superlinear is 0.15.
Such a sweep cannot tell the classes apart at all.

### Two failures that look alike and are not

A tight fit over a narrow span is **confidently wrong** — r² near 1.0 because
few points define a line, and an exponent that means nothing. A loose fit over a
wide span is **honestly uncertain**. They are separate objections because the
remedies are opposite: widen the sweep, against reduce the noise or add points.
An audit reporting only *inadequate* would send somebody to do the wrong one, so
`describe` states the remedy and a test asserts each appears only for its own
failure.

### The audit's bar is one point above the instrument's

S-3.2 refuses below three because two points define a line through themselves.
Three is what it takes to **fit**; four is what it takes to **check** — at three
a power fit has one residual degree of freedom, so one outlier moves the exponent
without moving r² much, and there is no point that can be dropped and re-fitted
as a test. **An audit whose bar equals the instrument's bar is not auditing
anything.** Distinct scales are counted, not measurements, so a caller cannot
clear the bar by re-running one point.

### A narrow sweep can still support the weaker claim

The gap between constant and linear is the whole of 1.0, not 0.15, so a sweep too
narrow to separate linear from superlinear may still be ample to show a metric
does not grow. `resolves_growth` answers per claim rather than per sweep, because
refusing such a sweep outright would throw away the exclusions `00-BRIEF.md` §9
ships as answers.

### Order matters when reading the fit

S-1.5 sets `exponent`, `power_r_squared` and `growth` to `None` **together** when
a power law could not be fitted at all. Reading r² first would report *the power
law does not describe these measurements* about a power law nobody managed to
fit, which is a different objection with a different remedy.

## Sabotage

Seventeen properties, all caught, no survivors. The pairing that matters is
*a narrow sweep is never flagged* against *every sweep is flagged as narrow* —
this module can fail in both directions, and an auditor that objects to
everything passes every negative test while making the epic a machine for
rejecting sound findings.

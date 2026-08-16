# 090 — A reseed is the only thing that moves a condition

**Status:** accepted
**Story:** S-8.8 — reseed tool
**Date:** 2026-08-16

## Context

`08-audit.md` records this as a **capability gap** rather than a defect:

> The Diagnostician cannot request new fixtures. If it suspects a skew-dependent
> defect, it has no way to ask for skewed data. Add `reseed(shape_spec)` calling
> back into the Explorer's fixture machinery.

Three acceptance criteria — the Diagnostician can request new fixtures with a
specified shape; reseeding invalidates affected exclusions per S-8.5; the cost is
counted against the experiment budget.

Almost everything needed already existed. S-7.7 builds a fixture at a chosen
shape, S-8.5 decides what a changed condition reopens, and S-5.4 has both halves
of a step cap. What was missing was the doorway between them — and the guards
that stop it being a way around the other two.

## Decision

### This is the other half of S-8.5

S-8.5 made an exclusion conditional so that it *could* be reopened. Until this
story nothing in the system moved a condition on purpose: `Conditions` changed
only when somebody rebuilt the world by hand, so *may be reopened* was a property
nothing exercised. A reseed is that thing, and it is the only one.

The composition test asserts it through `Investigation` rather than through the
function alone, because AC 1 is *the Diagnostician can request new fixtures* — a
capability reachable only from a helper would satisfy the criterion without
closing the gap. That distinction is Epic 7's composition finding, restated: *the
criterion is met* and *the criterion is reachable* are different claims.

### Two guards the acceptance criteria do not ask for

**A reseed that would move no condition is refused.** It reopens nothing,
establishes nothing, and costs one of the forty experiments a finding gets. This
is S-8.5's `reopen` guard pointing the other way: there, an exclusion may not be
set aside without a condition having moved; here, a condition may not be
*claimed* to have moved without moving. The control matters as much — reseeding
to the same shape at a **wider scale** does move a condition, and refusing that
would make the guard a prohibition on reseeding at all.

**The conditions change only after the seeding succeeded**, and the order is the
whole correctness argument. Adopting them first and failing second would reopen
every exclusion mentioning the old shape *against a fixture that was never
built*. The agent would then re-run an experiment believing the world had
changed, get the same answer, and record it as new evidence — which is worse than
the gap this story closes, because it manufactures a reason to disbelieve a
correct exclusion. Found by asking what S-8.4's build-then-append finding looks
like one module over.

### The cost needed no new machinery

AC 3 is `authorize` before and `record_step` after. `authorize(phase,
finding_id)` with a zero worst-case is already a pure step-cap check, and S-5.4's
argument for calling it first applies unchanged: *cost is known once a call
returns, so a check afterwards reports a breach rather than preventing one.* No
money changes hands; what a reseed spends is one of the forty experiments.

A failed reseed is **not** charged, following `Session.run`'s precedent of
recording after the call. That is safe here only because the refusal propagates —
nothing retries a broken seeder in a loop — and it is stated rather than assumed.

**The recorded conclusion carries the recipe digest**, which a sabotage proved is
load-bearing: S-5.4 escalates a phase whose last three steps concluded the same
thing, so a reseed always reporting `"reseed"` would stall the investigation on
its third genuinely different fixture.

### What is not decided here

Who decides to reseed. This story builds the tool and its guards; wiring it to a
model's tool call is E12's, and S-8.7 drew the same line one story earlier. The
capability is callable and enforced, and nothing in Epic 8 makes a model choose
to call it.

This module also seeds nothing itself — S-7.6 and S-7.7 own that, and a second
seeding path here would be a second statement of how data is made.

## Sabotage

Sixteen properties, all caught — after two survived and one run hit a harness
fault that the detector added in S-8.7 caught immediately.

*The harness fault worked.* Sabotage 11 produced a file that could not parse, so
pytest exited non-zero with no failure line — which under S-8.7's runner would
have read as a clean catch. The `!!! HARNESS FAULT` branch reported it instead,
which is the first time that guard has paid for itself and it did so one story
after being added.

*Two survivors, and the first is the recurring shape.* Dropping the carried-forward
concurrency to the literal `1` changed nothing, because **every fixture in the
file ran at concurrency 1**. A load experiment establishes exclusions at
concurrency 8, and a reseed during one must not quietly reset the recorded load
to serial — every exclusion held under it would reopen for a reason nobody
caused. Seventh instance of a fixture that could not discriminate.

*The second was a threshold nobody crossed.* The stall test performed two reseeds
and asserted the budget counted two, which cannot distinguish a per-fixture
conclusion from a constant one: `DEFAULT_STALL_AFTER` is three. The test now runs
three genuinely different fixtures and asserts no stall, with a **control** that
runs the same recipe three times and asserts one — because a conclusion that
never repeats makes the stall check unreachable, which is the opposite defect and
just as silent.

# 097 — The audit can ask, and cannot do

**Status:** accepted
**Story:** S-9.3 — fixture adequacy attack
**Date:** 2026-08-17

## Context

*Assesses whether fixture shape could have hidden the real cause. **Can request a
re-run under different fixture shape.***

Every audit story before this one can only object. This is the first that can
cause work — which makes it the story where ADR 094's warning applies most
directly: an audit whose lever is *run more experiments* worsens the one failure
S-0.8 actually measured.

## Decision

### The capability is split: request and execute are different modules

This module produces a `ReseedRequest`. It has no seeder, no budget, and no call
into S-8.8. Executing a request goes through `reseed`, which authorizes against
the experiment cap **before** it seeds anything.

An auditor that could seed directly would be doing the harness's job and spending
budget nobody authorized — and it would be doing so from the one component whose
entire purpose is to be sceptical of the run it is examining. The split is
asserted by inspection: the module imports no `Seeder`, no `Budget`, and
`assess_fixture` takes neither.

### A request is only made when it would change something

S-8.8 refuses a reseed that moves no condition. This refuses to **ask** for one.
Two guards on the same waste at different layers, and this is the cheaper one:
it costs nothing, whereas S-8.8's refusal arrives after a caller has already
decided to spend a round of the audit's budget producing an instruction that is
going to be rejected.

### Which shape to ask for is derived

`LONG_TAIL` first, because S-3.3 records it as *the deliberate worst case for any
per-parent cost — the one that turns milliseconds into minutes for a single
request while every other request stays fast*, and its signature is bimodal
rather than smooth. `POWER_LAW` second, as the smooth spectrum a long tail does
not cover. `UNIFORM` last, because `Σ k²` makes it the blindest and an
investigation has almost always already run it.

**The request changes only the distribution** — same entity, same size, same
source. S-3.3's `allocate` spends the same total over the same parents, so shape
is the only difference between the two measurements. A request that also changed
the size would produce a number that differs for two reasons, which is the thing
a controlled comparison exists to avoid.

### How this differs from S-9.2

S-9.2 audits an *exclusion*: was this particular thing ruled out under adequate
conditions? This audits the *investigation's fixture* against the cause it claims
or failed to find, and produces something executable.

They agree that uniform is blind, and they should — both read it off the same
`Σ k²` proof. Two modules agreeing because they consult one argument is not
duplication; two modules agreeing because each contains its own copy of the
argument would be.

## Sabotage

Thirteen properties, all caught, no survivors. The pair that matters is
*a request is made even when every shape was swept* against *no request is ever
made*: this module can fail in both directions, and the first is the one ADR 094
was written about.

## Two test defects worth recording, both my own

*A negative assertion over formatted text is a substring check.* The control test
asserted `"could have hidden" not in describe()` — and the adequate rendering
says *"Nothing about the shape of the data **could have hidden** a per-parent
cost"*, so the assertion failed on the negation. This project had already
recorded that hazard at S-7.11 and it was walked into again. Re-anchored on the
positive sentence.

*A substring check over source text cannot tell an explanation from an action.*
The isolation test asserted `"budget" not in source.lower()`, and the module
docstring discusses the budget at length. Re-anchored on what the module
**imports and takes** rather than on words it contains — which is the property
that was meant all along.

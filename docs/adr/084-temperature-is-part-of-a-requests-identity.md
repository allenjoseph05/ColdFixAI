# 084 — Temperature is part of a request's identity

**Status:** accepted
**Story:** S-8.1 — hypothesis generation
**Date:** 2026-08-15

## Context

Four acceptance criteria — a separate call at temperature 0.8; receiving the
experiment log, exclusions, source under suspicion and applicable instruments; a
structured hypothesis plus the primitive that would test it; routed to the
frontier tier with no cascading.

This is **the first call to a model in the system.** Everything before it —
fourteen primitives, a sandbox, a screening pass, an entire Explorer — is
deterministic, which is the shape `00-BRIEF.md` §1 argues for: the methods are
mechanizable, and *choosing which one applies to a given program* is the part the
literature names as requiring expertise.

Two of the four criteria turned out to be already enforced elsewhere, and one
required a change to S-0.7b.

## Decision

### Temperature joins the request digest

`ModelClient.complete` had no temperature at all, and AC 1 requires 0.8 while
S-8.3 requires 0.0. Adding the parameter is obvious; **adding it to the digest is
the decision.**

S-0.7b's own argument for including the model applies unchanged: *two models
answer the same prompt differently, so a recording made against one must never
serve the other — S-5.5 routes the same step to different tiers, which is exactly
when that would happen.* Temperature is the same shape one axis over.
`03-agents.md` §2.4 sends the Diagnostician's two calls at 0.8 and 0.0, and those
are frequently **the same question about the same log**. Without the temperature
in the digest, the recording made for the call that *must not vary* would answer
the call that is supposed to, and nothing would fail.

It is required rather than defaulted, on S-5.4's argument about `max_output_tokens`:
a default makes a guarantee depend on a number nobody at the call site chose.

### The tier and the cascade are enforced by Epic 5, not restated here

`CLAUDE.md`: *never cascade to a cheap model on hypothesis generation or attack
design — no deterministic validator exists for those.*

S-5.5 already refuses to route a creative step below the frontier, and derives
*creative* from `04-cost.md` §3's table rather than from a caller's declaration.
S-5.6 cascades only a step whose caller supplies a validator. So AC 4 needed no
new mechanism — it needed this module to use `StepType.HYPOTHESIS_GENERATION`
and to have **nowhere to pass a validator**.

`generate` therefore has no `validate` parameter, and a test asserts that by
inspection. This is the third instance of the same construction: S-7.8's `accept`
has no `force`, S-7.10's run has one exit, and here the unsafe request has no
argument to arrive through.

### The instrument the model names is checked against the instruments it was offered

A hypothesis proposing a primitive S-3.1 withheld is not a hypothesis; it is a
step that will fail when S-8.2 tries to design an experiment for it, **with the
reason already lost**. The `Selection` is the authority and is consulted at the
point of parsing.

The control matters as much as the check: a test asserts an *offered* instrument
is accepted, because a validator that refused everything would pass the negative
test and leave the agent unable to propose anything at all.

### Nothing repairs a malformed answer

A reply with no JSON object is reported with the text, not patched. *The model
answered something else* and *the model was wrong* are different problems needing
different fixes, and a parser that guesses turns the first into the second.

A refusal is checked before the text is read — S-0.7b established that a decline
is a successful response with an **empty content list**, so a caller reading
`text` reads emptiness as brevity. A truncated reply is refused for the adjacent
reason: a half-written JSON object parses as nothing, and a hypothesis assembled
from half a sentence is a guess about what the model was going to say.

## Consequences

**Makes easy.** S-8.3 gets the temperature axis it needs to be a genuinely
separate call. S-8.2 receives a hypothesis whose primitive is known to exist.
S-8.7's instrument switch becomes observable, because the primitive is part of
the structured answer rather than prose.

**Makes hard.** Every recording in the store is now keyed on one more field, so
recordings made before this story do not replay. That is the correct direction —
a recording that answers a request it was not made for is the failure the digest
exists to prevent — and there were none outside S-0.7b's own tests.

**Rules out.** Cascading a creative step from this call site. Proposing an
instrument the project was not offered. Reading a refusal as a short answer.

**Sabotage-verified on fourteen properties, all caught, no survivors** — including
all three non-negotiables: the call made at the interpretation temperature, the
step declared mechanical so that it may cascade, and the temperature removed from
the request's identity.

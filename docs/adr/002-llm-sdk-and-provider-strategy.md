# 002 — LLM SDK and provider strategy

**Status:** accepted, with one requirement deliberately deferred
**Date:** 2026-08-02

## Context

Five agents make model calls: Explorer, Diagnostician, Surgeon, and two auditors
(finding audit E9, patch audit E11). Two constraints shape the choice.

**The Adversary should not share a failure mode with the Surgeon.** S-0.2's note
is explicit: *"ADR-002 must record that Surgeon and Adversary should run on
different model vendors where possible. If that is deferred, record it as a
known limitation rather than dropping it."* The reasoning is that a model
reviewing work produced by the same model may share the blind spot that produced
it.

**Cost is a first-class constraint.** `04-cost.md` and E5 exist because an
agentic loop over an append-only log is expensive by construction.

## Decision

**SDK: the official Anthropic Python SDK (`anthropic`).** Not an abstraction
layer over several providers — the project's own rule is no speculative
abstraction until a second case exists, and a provider-neutral wrapper is
exactly that.

**Default model: `claude-opus-5`** for hypothesis generation, attack design, and
patch authorship. Cheaper tiers (`claude-sonnet-5`, `claude-haiku-4-5`) are
available to E5's routing for tasks with a deterministic validator, subject to
the standing rule that hypothesis generation and attack design **never** cascade
to a cheap model, because no validator exists for either.

Indicative first-party rates at time of writing — treat as a planning input,
not a contract, and re-check before publishing any cost figure:

| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| `claude-opus-5` | 5.00 | 25.00 |
| `claude-sonnet-5` | 3.00 | 15.00 |
| `claude-haiku-4-5` | 1.00 | 5.00 |

**Different-vendor requirement for the Adversary: deferred, and recorded here as
a known limitation** rather than dropped. See below.

## The append-only log is a caching decision, not just a discipline

`CLAUDE.md` states the experiment log is append-only and that reordering or
re-summarizing it mid-investigation multiplies cost. That is now grounded rather
than asserted: **prompt caching is a prefix match, and any byte change
invalidates every cached breakpoint at or after that position.** Render order is
`tools` → `system` → `messages`.

The consequences bind several stories at once:

- **Re-summarizing the log rewrites the prefix**, so every subsequent request
  pays full input price instead of the ~0.1× cache-read rate. The append-only
  rule is what keeps the investigation on the cheap path.
- **Cache writes cost more than uncached reads** — ~1.25× at the 5-minute TTL,
  ~2× at one hour — so a breakpoint that is written and never read is a loss.
- **Caches are model-scoped.** E5's routing and cascade stories (S-5.5, S-5.6)
  must not switch models mid-investigation on a cached path; a cheaper model for
  a sub-task belongs in a separate call with its own prefix.
- **Tools render at position 0.** Adding or reordering a tool invalidates
  everything. The primitive registry (S-3.1) must serialize its tool list
  deterministically, and the Diagnostician must not gain or lose tools mid-run.

S-5.7 ("cache-friendly context assembly") is therefore not an optimization
story. It is the story that makes the append-only invariant affordable.

## The deferred requirement, stated plainly

Running the Adversary on a different vendor means a second SDK, a second
credential path, a second set of error semantics, and a second prompt dialect —
for one agent, before the Adversary has been shown to catch anything. That is
real cost against an unquantified benefit, so it is deferred to E9/E11 rather
than built now.

**What actually carries the weight in the meantime** is the control `CLAUDE.md`
already mandates and which does not depend on vendor at all:

> **The Adversary never sees the Surgeon's reasoning.** Enforced by constructing
> a fresh message list, not by instructing the model to ignore it.

That is a structural guarantee. Vendor diversity is defence in depth *on top of*
it — protection against correlated model failure, not the primary mechanism.

Two weaker forms of independence are available now and should be used:

1. **Different model families** — e.g. Surgeon on `claude-opus-5`, Adversary on
   `claude-fable-5`. Different training and different refusal behaviour, same
   SDK and same billing.
2. **A fresh message list per audit**, already required.

**Recorded as a known limitation for S-17.2:** the adversarial verification
numbers this project publishes are, until this is revisited, measured with both
sides on one vendor. Any claim about catching a model's own blind spot is
bounded by that, and must be reported that way rather than quietly generalized.

## Consequences

**Makes easy.** One SDK, one auth path, one error taxonomy. Anthropic-hosted
tool use and structured outputs are available without a compatibility layer,
which matters for S-4.1's validated artifacts.

**Makes hard.** Every prompt and every routing decision becomes Anthropic-shaped.
The cost of adding the second vendor later is higher than adding it now — that
is the trade being made knowingly.

**Rules out.** A provider-neutral abstraction layer built before a second
provider exists.

## Provenance

`docs/10-BACKLOG.md` S-0.2 notes and E5; `docs/04-cost.md`; `CLAUDE.md`
non-negotiables. Model IDs, pricing, and caching mechanics verified against
current Anthropic API documentation on 2026-08-02.

# 069 — The double is built against the real response

**Status:** accepted
**Story:** S-0.7b — mock LLM client
**Date:** 2026-08-11

## Context

`CLAUDE.md`: *agent logic is tested against a mock LLM client replaying recorded
responses. No test hits a real API.* Epic 7 is the first epic with agent logic,
so nothing in it can be tested as the project requires until this exists.

`10-BACKLOG.md` deferred it with a precise reason — *the SDK and provider
strategy are undecided, and writing a mock against a guessed interface is the
speculative abstraction `CLAUDE.md` forbids* — and recommended resequencing it as
**S-0.7b**, depending on S-0.2, E1 and S-4.1. All three are done. The guess is no
longer necessary, so the deferral has expired rather than been overridden.

There is a second reason to build it now. Epic 5 is routing, cascade, budgets, a
ledger and context assembly with **no real caller**; its own composition check
recorded that as a limit. `Session.run` already takes a callable handed a model
id and returning a result with its usage, which is exactly the shape of a
completion.

## Decision

### A recording is a real API response, validated by the vendor's own model

The store holds the JSON that `anthropic.types.Message` parses, and building a
recording runs it through that model — so a payload the API could not have
returned **fails to load** rather than being replayed. This is the project's own
recorded lesson applied to what will become its most-used double: *a test double
more forgiving than the real thing turns a structural assertion into a
decoration.*

It also costs nothing: the SDK's response types are Pydantic models that validate
offline, so fidelity here needs no network and no key.

### Both clients share one translation

`translate` is the only place an API response becomes an artifact of this system.
A test that passes against the replaying client therefore exercised the same
parsing the real client uses. A double with its own translation would be testing
the double.

### An unrecorded request is refused, never answered

A mock that returns a plausible default is the most dangerous kind available:
every agent test would pass, and what they would all be testing is the default.
The refusal names the four things that look identical from the call site — a
different model, a changed prompt, a recording never made, one made under a
different `max_tokens`.

**The model is part of the request identity.** S-5.5 routes the same step to
different tiers, which is precisely when a recording made against one model would
otherwise serve another — and the two answer differently and bill differently.

### `text` is never `content[0].text`

Two real shapes break that reading: a refusal carries an **empty** content list
(HTTP 200, `stop_reason: "refusal"`), and adaptive thinking puts a `thinking`
block ahead of the answer. `stop_reason` is carried through so `refused` and
`truncated` are answerable without re-reading the response, because a double that
always reports `end_turn` hides a decline until production.

### The replaying client holds no vendor client

*No test hits a real API* is structural rather than a rule: there is nothing in
the object to call with, and a test asserts that none of its attributes come from
the vendor's package.

## Consequences

**Epic 5 has its first real caller.** A test drives `Session.run` with a replayed
completion and gets a priced ledger entry and a run report ending in euros per
confirmed finding — the arithmetic exercised against something API-shaped rather
than hand-built.

**Makes hard.** `anthropic` is now a project dependency, and recordings are
coupled to the SDK's response model: an SDK upgrade that changes it invalidates
the store. That is the correct trade — the alternative is a store that keeps
parsing after the API has moved.

**Scope.** Only the mock client half of the backlog's S-0.7b recommendation was
built. The other half — lab-bench unit tests and golden files — is E1-era, and E1
already has its own suite; it is left for the user to call rather than silently
absorbed.

**Sabotage-verified on eleven properties, all caught — after two survived and
both were weak fixtures rather than weak code.** The refusal fixture had an
*empty* content list, so `content[0]` behaved identically and the
first-block-reading sabotage passed; the usage fixture had a **zero** cache
write, so collapsing the two cache figures changed nothing. Both now have cases
that discriminate: a response with a `thinking` block first, and one with a cold
cache write. Seventh time in this project a passing sabotage has meant a weak
test — and the first time it was the *fixture* rather than the assertion.

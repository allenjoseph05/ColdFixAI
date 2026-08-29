# 170 — One session per step, one budget per stall regime

**Status:** accepted
**Date:** 2026-08-29
**Amends:** ADR 169, and the `Sessions` contract in `orchestrator/adapters.py`

## Context

`Sessions` has documented itself as *"one cost session per agent step, keyed by
that step's system prompt"* since it was written, and adds that *"a factory that
returned one session for the whole run would defeat the boundary rather than
serve it."* One caller did not honour it: `adapters.py` opened a single session
with `_INVESTIGATION_PROMPT = hypothesis._SYSTEM` and drove all three
Diagnostician steps through it.

While each agent passed its own `_SYSTEM` to the client, that was a billing and
caching mismatch — exactly what `repair/sessions.refuse_foreign_session` was
written for, and which was applied to the two Surgeon steps and to neither of
these. S-17.16 found what it becomes the moment the session's string is what gets
*sent*: `design` and `interpret` receive the hypothesis prompt, and are told to
answer with a statement, a primitive and a rationale when they owe a
specification and a verdict. **The full gate stayed green while that was true**,
because every fixture built its recordings from the same session it drove the
agent with.

## Decision

**The investigate node passes the factory, not one session out of it.**
`Investigation` holds `sessions: SessionFor` and derives three — one per step's
`_SYSTEM` — in `__post_init__`, where ADR 169's log join and source check now run
for each of them.

**`refuse_foreign_session` guards every call site that shapes its request from a
session.** `generate`, `design`, `interpret`, `explain` and `propose` join
`patch` and `falsification`. A step handed another step's session raises rather
than quietly billing against the wrong prefix.

The type alias lives in `diagnosis/loop.py` rather than being imported from
`orchestrator`, because `diagnosis` sits below `orchestrator` and a module that
reached upwards for a type would invert the layering.

## A second defect, measured while scoping the first

`Session` built its own `Budget` in `__post_init__`. `Ledger` was shared across a
campaign's sessions and `Budget` was not — so **a phase driven by two sessions
counted its cap twice.** Measured before the fix: `Phase.REPAIR` caps at three
attempts, the repair node opens two sessions, and after three steps on the first
the second still authorized a fourth. The audit node has the same shape. The
stall history split identically, so S-8.9's *three identical conclusions* could
be reached six times without tripping.

This was live on `main` and is not caused by the change above — but the change
takes the investigate loop from one session to three, so it is fixed here.

The euro ceiling was always right, which is why nothing noticed: `spent_eur`
reads the shared ledger.

## Why per stall regime rather than per campaign

`Budget._used` and `_conclusions` are both keyed by `(phase, finding_id)`, so one
budget serves every phase correctly on both counts. `stall_after` is the
exception — a single number per budget, where `STALL_AFTER` gives grounding 15,
an investigation 8, and everything else the default. Those three numbers are
themselves refusals: `GroundingRun` will not start unless its budget stalls after
15, and `check_stall_configuration` will not accept an investigation at anything
but 8.

So a single campaign-wide budget cannot exist. Budgets are keyed by the stall
value, which is the grouping those three numbers already describe, and
`Session.__post_init__` refuses a `shared_budget` whose `stall_after` differs
from its own — otherwise one phase runs silently under another's rule.

Both halves are asserted, because the dangerous direction is the tidy-looking one:
a single budget for the campaign.

## The fixtures were part of the defect

`tests/fixtures/thesis.py` built a `Session` by hand with a generic system
string and handed it to three steps. That is a shape the campaign does not
produce, and it is why S-17.16's regression passed 3307 tests. The fixture now
calls `sessions_for` — the production factory — so the suites drive the real
arrangement and inherit the stall regimes and the shared budget rather than
restating them.

**The guard found four more the moment it was added.** `test_hypothesis.py`,
`test_design.py`, `test_interpretation.py` and the session `test_explain.py`
borrowed all built a `Session` with the same generic string — 21 failures, every
one a suite asserting against a session whose system text is not what the agent
under test sends. They were not wrong about the agents; they were wrong about the
session, in the same way the production caller was. That is the guard doing its
job on its first run, and it is why this was worth adding rather than relying on
the one call site being fixed.

`recording_sessions` builds its **own** factory rather than wrapping the
investigation's. The request is identical either way; what must not be shared is
the log the cached prompt renders, since the walk drives a log forward to build
each recording and the run needs its own.

## Consequences

Cache cost: none. Caching is a prefix match with render order `tools` → `system`
→ `messages`, so the system parameter is part of the cached prefix and three
steps sending three system prompts have had three cache entries all along. The
first version of ADR 169 said a session per step would cost three write premiums
instead of one; that was wrong and is corrected there.

What remains open is `04-cost.md` §4's diagram, which shows one stable prefix per
run. For the Diagnostician it is one per step, and the correction commit says so
in §12.3 rather than redrawing §4.

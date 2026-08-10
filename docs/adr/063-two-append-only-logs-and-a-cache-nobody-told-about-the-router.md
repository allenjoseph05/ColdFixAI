# 063 — Two append-only logs, and a cache nobody told about the router

**Status:** accepted
**Story:** Epic 5 composition check
**Date:** 2026-08-10

## Context

Epic 5 finished with nine stories, 294 passing tests and no way to spend money
through it. Performing the epic's own sentence — *make development fast and
production affordable* — took seven objects called in the right order, and the
suite could not have noticed a wrong order: no test file in the epic touched more
than three of the nine modules, and always a module plus its direct dependency.

That is the same shape Epics 2, 3 and 4 were in before their composition checks,
which found respectively an architecture that could not run a Django app, three
defects, and five described as *none reachable from a test of one stage*.

Four defects here, and the first two share a property that made them invisible:
**both wrong joins keep the cache working.** Prompt caching is a prefix match, so
a log that is wrong in content but still append-only reports full hits and a
rising bill. There is no failing request to notice.

## Decision

### 1. A log has one owner, and the other object refuses to hold one

S-5.7's `Investigation.append` takes a line; S-5.8's `PrunedLog.render` returns
the whole block — notice plus one summary per experiment. A caller holding both
has two append-only logs and two ways to join them, both wrong:

| Join | What breaks | Why nothing notices |
|---|---|---|
| `investigation.append(record.summary())` | S-5.8's retrieval notice never reaches the prompt, so the agent is never told `read_experiment(n)` exists and never asks. The detail is preserved and lost at once — the exact failure S-5.8 was written to prevent. | The prefix is byte-identical and the log is append-only. Every check either module makes still passes. |
| `investigation.append(pruned.render())` | Each append re-appends every earlier experiment. At S-5.4's cap of 40 the log is carried 40 times, on the prompt whose whole purpose is to be small. | Also append-only. The cache hits on a prompt that has quietly gone quadratic. |

`Investigation` gains an optional `log_source`. With one set, `log_text()`
delegates and **`append` and `entries` are refused by name**. `entries` refuses
rather than returning an empty tuple, because an empty tuple would let
`is_append_only` compare two empty lists and report the property holding over a
log it never saw — a guard that cannot fail, which is S-3.12's finding for the
fifth time in this project.

The one test that joined the two modules before this appended `render()` **once**,
after all 40 experiments were already logged. A static snapshot passes; the
incremental case is the only case a real investigation has.

### 2. One prompt per model, because a cache is scoped to a model

S-5.9 already recorded the fact, in `CachePolicy.scope`: *model-scoped means
switching model within a run discards the cache, which S-5.5's routing has to
respect.* Nothing respected it. `Investigation` binds one model at construction;
`Router` picks a model per step; `cascade` escalates to a third mid-step. The
obvious composition sends one prompt to three models and calls the result a cache.

`Session` holds **one `Investigation` per model** and reports hit rates per model.
A blended figure is an average over caches that never share an entry, and it
flatters the second model by crediting it with the first one's warm calls.

This is not a workaround. Cache entries genuinely are per model, so a run that
uses three tiers has three caches, each paying its own cold write — and that is
a cost the report should state rather than hide.

### 3. Authorization belongs inside the attempt, not before the cascade

S-5.4's argument is that a ceiling checked after the call is a report rather than
a ceiling. `cascade` makes up to three calls; `authorize` was built to price one.
A caller who authorized the step and then cascaded it spent three times what the
ceiling was asked about — and **worse than three times**, because the third
attempt runs a tier dearer, so even multiplying by the attempt count under-prices
it.

`Session.run` authorizes inside the `attempt` callable S-5.6 already takes, at
the model that attempt actually uses. No change to `cascade`: its parameterised
`attempt` was the right seam, and nothing had used it.

### 4. The frontier share is measured, not derived from routes

`frontier_share` maps (phase, class) through the router. Escalation is not a
routing decision, so the one path S-5.6 guarantees exists — mechanical work
reaching the frontier tier after failing its check twice — is the one path the
metric cannot see. The figure exists to catch frontier use drifting upward, and
escalation is exactly how it drifts.

`Session.observed_frontier_share()` counts the models the ledger recorded.
`call_counts(ledger)` is also provided so `frontier_share`'s hand-kept argument
has one source rather than two that can disagree — S-5.3's `reconciles` argument
applied to the routing figure.

### 5. S-5.7's routing hazard is priced rather than described

S-5.7 recorded that routing a step down a tier can raise its effective cost,
because the minimum cacheable prefix is not monotonic and the cheap tier's is the
largest. That was a sentence in a docstring with nothing able to check it, and
S-5.9 had built the arithmetic and acquired no caller.

`route_economics` prices the routed tier against the tier above it through
S-5.9's model. At §12.3's engineered grounding — a 2k prompt at 85% cached —
`claude-haiku-4-5` caches nothing (its minimum is 4096) and bills $1.0000/MTok,
while `claude-sonnet-5` clears its 1024 minimum and bills $0.7316. **The cheap
tier is 37% dearer.** The demonstration carries a control: at 5k tokens haiku
caches and wins by 3x, without which the function would pass for one that simply
always preferred the dearer tier.

## Consequences

**Epic 5 has an entry point.** `Session.run` is one call that routes a step,
authorizes it at the model it will use, assembles a cacheable pruned prompt,
cascades it where a validator exists, bills it to the ledger and the right cache,
counts it against the cap, and checks for a stall.

**The seam E7 fills is explicit.** `call` is handed a model id and returns what
the API returned — the result and its `TokenUsage`. Nothing here estimates a
token count: `CLAUDE.md` forbids an agent reporting a measurement, and a token
count is one.

**Sabotage-verified on fourteen properties, all caught.** The first sabotage run
was itself invalid and is worth recording: the runner restored via
`git checkout -- <paths>`, and a pathspec naming an untracked file makes git
error and restore **nothing** — so the new module kept every sabotage and the
later results were confounded by earlier ones. The runner now snapshots contents
in memory. A sabotage harness is code, and code that has not been checked against
its own failure mode is exactly what these checks exist to find.

**Two limits are recorded rather than closed.** `ModelCall` carries the step
*class* while `EscalationLog` is keyed on step *type*, so §3's promotion rule —
*above ~30% escalation, starting dear is cheaper* — is decided on rate alone and
cannot be priced, even though the ledger now holds every number the comparison
needs. Adding `step_type` to S-5.3's artifact for a rule S-5.6 already implements
was judged scope creep for a composition check. And **nothing in Epic 5 has met a
real API**: the arithmetic is right and the guards fire, which is not the same as
working in the system.

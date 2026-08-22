# 137 — The shape is measured, so nobody has to say where wide begins

**Status:** accepted
**Date:** 2026-08-22

## Context

S-13.4 is the trust ledger, and `08-audit.md` F15 is why it is not simply a
counter per fix category:

> A `select_related` fix approved 50 times may have been on projects with narrow
> tables. Applied to a project with a wide parent table, it trades queries for
> enormous payloads.
>
> **Fix:** ledger keys include project shape characteristics, not just fix
> category. A new project starts at level 0 for every category until it has its
> own history, with cross-project history shown as advisory context rather than
> as earned autonomy.

Two of the terms in that fix were undefined anywhere in the document set: **fix
category** and **project shape characteristics**. Both are this story's content.

## Decisions

### 1. The shape is measured, and the order of magnitude is the unit

F15's worry is payload per row. `RESPONSE_BYTES` is a metric the harness already
takes at every scale point — so *wide parent table* is a number this system has,
not a label somebody applies.

`payload_magnitude` is `floor(log10(bytes per unit of scale))`, and the choice of
an order of magnitude over a threshold is deliberate: **nobody has to decide where
*wide* begins.** A narrow/wide cut would need a number defended in a docstring and
wrong for some project; a decade is coarse enough that the question becomes *is
this the same kind of project*, which is what F15 is actually asking.

It reads the **widest** scale point, because a wide parent table at ten rows looks
like a narrow one — the trade only becomes enormous at volume, which is the whole
mechanism F15 describes.

Deriving a shape from nothing measured is refused. *A shape guessed here would be
the label it replaces.*

### 2. Fix category is a supplied string, not an invented enum

Nothing in the document set enumerates fix categories. Inventing a closed set —
`QUERY_BATCHING`, `CACHING`, `ALGORITHMIC` — would be a taxonomy with no source,
wrong the first time a fix did not fit, and `CLAUDE.md` refuses speculative
abstraction until a second case exists.

So `ledger_key(category, shape)` takes a string and refuses an empty one, the
construction `note_use` uses for `project`. The refusal message says why: filing
every kind of change under one level is *the autonomy F15 says was being
transferred unsafely, one axis over*.

### 3. Level is derived, and the two histories are never added

`Standing` carries `accepted` and `demotions` for **this** project, and
`elsewhere` — accepted counts from other projects — as a separate field that
`level` does not read.

That separation is the whole of F15's second sentence. A test populates
`elsewhere` with fifty approvals across ten projects, exactly F15's example, and
asserts the newcomer is still `GATED`. **Every level test carries a non-zero
`elsewhere`**, because a fixture where nobody else has any history cannot tell
*advisory* from *unused* — the shape of weak test this project has now recorded
eight times.

### 4. Three accepted per level, and F15 fixes only the demotion

F15 says *any revert or rejection demotes one level* and says nothing about
promotion. Three, for the reason S-13.2 chose three: it is the smallest number
that survives one coincidence. It also means level 2 — the most autonomy this
ledger grants — costs six clean fixes in one category on one project shape, which
is the right price for the thing F15 says was being given away.

`Outcome.demotes` names `REJECTED` and `REVERTED` together rather than deriving
from `not ACCEPTED`, so a fourth outcome added later has to state which it is
instead of inheriting an answer.

### 5. `state` does not reach up

`state` imports only `cost`, `replay` and `sandbox`. A ledger that pulled in a
`Fingerprint` and a `Workload` to build its own key would invert that boundary,
so `payload_magnitude` takes measured values and `Shape` takes plain strings.
Whichever caller already holds both assembles them.

## Consequences

The ledger exists and **nothing consults it yet.** S-12.4 and S-12.5 both hardcode
their gates to *always*, on the correct reasoning (ADR 130) that level 0 was the
only reachable value while no ledger existed. That is no longer true, and wiring
the gates to `standing(...).level` is now possible — but it is a change to a
safety property and belongs in its own story rather than as a side effect of
building the ledger.

Recording that plainly matters more than the code: a ledger that exists and is
not read is exactly as safe as no ledger, and exactly as useless.

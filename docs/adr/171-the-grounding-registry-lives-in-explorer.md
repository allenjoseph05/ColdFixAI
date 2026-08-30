# 171 — The grounding registry lives in `explorer/`, and adapters push into it

**Status:** accepted
**Date:** 2026-08-30
**Amends:** ADR 148 §1

## Context

ADR 148 §1 named three places where core still knew Django — `compose.py` called
`enumerate_entry_points` directly, `stages.PREDICATES` held one entry, and
`Framework.supported` was `self is Framework.DJANGO` — and filed the fix on
S-14.5 with a design: *the registry cannot live in `explorer/`, because adapters
import `explorer.fingerprint`; the decision has to move up to the campaign, the
only layer allowed to know both.*

**Two of that paragraph's premises turned out not to hold.**

**The cycle does not follow.** Adapters import `explorer.fingerprint`,
`explorer.entrypoints`, `explorer.stages` and four more; **nothing in
`explorer/` imports `adapters/`**, and a test already enforces that. A registry
that adapters *push into* is imported by them and reaches nothing back, so
`explorer/registry.py` ← `adapters/django.py` and `explorer/fingerprint.py` →
`explorer/registry.py` is a tree, not a cycle. The cycle ADR 148 feared would
need the registry to *pull* from adapters, which is a different design.

**The crash it warned about is not there.** *"Partial is not a midpoint here; it
converts a clear refusal into a crash"* rests on `PREDICATES[Framework.FLASK]`
raising `KeyError`. `predicates_for` reads `PREDICATES.get(...)` and raises a
typed `StageError` naming the framework — verified by constructing a
`Fingerprint` on `Framework.FLASK` and calling it, not by reading. The
correction is recorded in ADR 148 itself.

The conclusion still stood for a different reason, and that reason is what this
ADR keeps: a refusal at `predicates_for` says *this framework has no predicates*
when the honest answer is *nothing has been taught to ground it*.

## Decision

**`explorer/registry.py` holds what grounding needs from a framework**: its
entry-point enumerator and ADR 009's nine stage predicates. Adapters register at
import. `Framework.supported` is deleted; `fingerprint` asks `groundable()`.

**Six of the nine predicates were never Django's.** `_DJANGO_PREDICATES` was
named for a framework, and `_dependencies`, `_connect`, `_migrate`, `_auth`,
`_seed` and `_work` read a payload the subject was probed for and answer in terms
nothing framework-specific appears in. Only `_clone` and `_endpoint` (which reach
for Django's enumerator) and `_configure` (which runs `manage.py check`) are the
adapter's. They moved; the other six stayed as `FRAMEWORK_NEUTRAL_PREDICATES` and
every adapter builds on them. Moving all nine would have made each future adapter
restate six identical functions and drift.

**`register` refuses an incomplete table.** `evaluate` measures all nine stages,
so a partial mapping is a `KeyError` mid-run rather than partial support — caught
where the adapter's author is present to read the message.

## What the push design costs, stated plainly

**The registry's contents depend on what a process imported.** This is ADR 050's
finding for the primitive registry, arriving a second time, and it is the reason
option A existed. A framework whose adapter nobody imported is not *withheld* —
it does not exist, and *absent* reads exactly like *unsupported*.

Mitigations, both ADR 050's:

- `adapters/__init__.py` imports every adapter module.
- A test parses that directory for a top-level `register(` call and asserts each
  module it finds is named in the package's imports — read from the filesystem,
  because a list in a test is forgotten at the same moment as the import.

**And one thing ADR 050 did not have to say.** `coldfix.primitives` is core, so
core can guarantee it is populated. `coldfix.adapters` is not: the invariant
*adapters import the core; the core must never import an adapter* is enforced by
`test_no_core_module_imports_an_adapter`, so **no module under `src/` outside
`adapters/` may trigger the registration.** Whoever assembles a run does, one
layer further out, exactly as `campaign_for` already takes adapter-supplied
values rather than the adapter.

The visible consequence: `fingerprint` refuses every repository in a process that
never imported `coldfix.adapters`, and says `registered so far: none`. That is
the true statement about such a process, it is loud rather than silent — seven
test modules failed the moment the registry landed and every one was a real
instance of it — and there is a test pinning the sentence, because *refuses
everything* is a bad state to reach without a diagnosis.

## The refusal now names what is absent

`Unsupported.reason` said Flask was *"not a framework this system supports yet"*
and blamed the adapter, the reset strategies and the query counter, pointing at
S-14.3 as the story that would add a second adapter. S-14.3 landed in August, so
the sentence was telling readers to wait for something that had already happened.

It now says nothing has taught the system to ground the framework, names what
grounding needs, and lists what **is** registered — which turns a dead end into a
comparison.

## Consequences

MCP (S-14.5's actual AC) is untouched and still last. What this closes is the
boundary Epic 14 claimed: no module under `src/` outside `adapters/` evaluates
`Framework.DJANGO`, asserted by a test that walks the tree with `ast` rather than
grepping, so the prose in this ADR and in the modules it describes does not trip
it.

The one exception is `explorer/fingerprint.py`, and it is a real one: *detecting*
Django means knowing that `manage.py` exists and that a requirement named
`django` is a signal. That knowledge has to live somewhere, and it cannot live in
an adapter that is only reachable once the framework is known.

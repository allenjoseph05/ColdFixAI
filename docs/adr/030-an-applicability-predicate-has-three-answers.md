# 030 — An applicability predicate has three answers, and a tool list cannot move

**Status:** accepted
**Date:** 2026-08-06

## Context

S-3.1 asks for a registry where every primitive declares a name, its required
capabilities, a cost class and an applicability predicate; where the
Diagnostician receives only the applicable ones; and where adding the fifteenth
primitive touches no agent code.

Three of those four declarations are bookkeeping. The predicate is not, because
the type it obviously wants is `Callable[[ProjectProfile], bool]` and that type
cannot express the answer this system will actually have most of the time.

Before grounding finishes — and sometimes after it — nobody knows whether the
subject runs as a long-lived process, parses user-controlled input, or executes
concurrent code within one request. A boolean predicate must turn that ignorance
into a `True` or a `False`, and both are wrong in a way this project has already
paid for once:

- **Ignorance as `True`.** The Diagnostician runs an instrument that does not
  apply. It does not fail. `08-audit.md` F7 is the worked case: proportional
  perturbation on single-threaded code degenerates into ablation and returns
  numbers. Longitudinal on a CLI tool runs for hours and fits a flat line, which
  reads as *no ramp*, which is an exclusion — and `00-BRIEF.md` §9 ships
  exclusions as findings. That is ADR 013's failure with a different instrument:
  a measurement that was never valid, presented as a measurement that came back
  empty, and indistinguishable from a real one at review.
- **Ignorance as `False`.** The instrument silently leaves the list. The agent
  works through what it was given, exhausts it, and concludes there is nothing
  left to try. `08-audit.md` closes on exactly this: *the agent cannot know what
  it does not know.*

A second constraint arrives from outside the story. ADR 002 established that
tools render at position 0 of a request and that prompt caching is a prefix
match, so **a tool list that gains or loses an entry mid-investigation
invalidates every cached breakpoint behind it** — the mechanism `CLAUDE.md`
names as multiplying cost by roughly twenty.

## Decision

**Applicability has four states, not two:** `APPLICABLE`, `UNSUPPORTED` (this
environment lacks a declared capability), `UNDETERMINED` (a fact the predicate
needs was never established), `NOT_APPLICABLE` (a fact is known and rules the
primitive out). Four rather than three because the reader's next action differs
for each: run it, supply the capability, go and measure the fact, never ask
again for this subject.

**A fact absent from a profile means *not known*, and `ProjectProfile.check()`
is the only way to read one.** It returns a verdict rather than a boolean, so
the tri-state cannot be flattened by a predicate author reaching for
`facts.get(fact, False)`. `requires()` and `all_of()` exist so that the ordinary
case is declared rather than written.

**Where conditions combine, the most decisive answer wins:** `NOT_APPLICABLE`
over `UNSUPPORTED` over `UNDETERMINED`. If one required fact is known false, the
primitive never applies to this subject whatever the unknown one turns out to
be, and answering `UNDETERMINED` there would dispatch somebody to establish a
fact that cannot change the outcome. The natural implementation — return the
first condition that fails — gets this right only when the author happens to
have listed the decisive condition first, which is why the test runs both
declaration orders.

**Withholding is recorded, never silent.** A `Selection` carries `available`
*and* `withheld`, each withheld entry with its verdict and reason, and
`withheld_notice()` renders them with the sentence that matters: *an instrument
withheld as undetermined is one whose applicability was never established, not
one that was tried and found irrelevant. Nothing it would have measured has been
ruled out.* Visibility is not callability — `Selection.get()` raises
`PrimitiveUnavailableError` for a withheld name and `UnknownPrimitiveError` for
one nobody registered, because a typo and an unsupportable experiment send the
reader to different places.

**Capabilities and facts gate independently and are kept apart.** A capability
is a property of this environment and adapter; a fact is a property of the
subject. Load needs a load generator *and* a subject that serves concurrent
requests. Capabilities are checked mechanically before any predicate runs, so
every primitive gets that check identically and no predicate has to remember to.

**A selection is a snapshot.** `select()` copies what it read; the profile
copies its inputs on construction; registering a primitive afterwards cannot
alter a `Selection` already handed out. The consequence is deliberate and is
stated in the module docstring rather than left to be discovered: **learning a
fact partway through an investigation does not unlock an instrument for that
investigation.** It unlocks it for the next one. ADR 002 makes that the cheap
choice as well as the analysable one.

**Cost class names its unit — `seconds`, `minutes`, `tens of minutes`,
`hours`.** The obvious vocabulary is unusable here because this project's own
documents already use "cheap" and "expensive" on two different scales:
`01-primitives.md` §2 calls scaling the cheapest primitive on grounds of
*measurement validity* (counts need no warmup, interleaving or statistical
test), while §5 and §14 each call a different primitive the most expensive on
grounds of *wall clock*. Naming the band for the unit makes the ambiguity
impossible to inherit. Rendering is ordered by cost band then name — both static,
so the bytes are stable, and cheapest-first is the same advice `01-primitives.md`
§17 gives the agent.

**Signatures are read from the callable and rendered with annotations
resolved.** Two statements of one signature drift, and the one the agent reads
would be the one that is not executed. Resolution matters for a reason specific
to this system: a module with `from __future__ import annotations` renders
`workload: 'str'` and one without renders `workload: str`, which would make a
cached prompt prefix depend on an import in a file nobody would connect to
prompt cost.

**This module ships the mechanism and no primitives**, the same split ADR 013
made for counters. The fourteen arrive from S-3.2 onward.

## Consequences

**Makes easy.** A fifteenth primitive is a registration: it appears in the
instrument list, in the selection and at dispatch with no edit to any agent — the
property S-3.1 exists for, tested by calling through a stand-in agent written
before the primitive it calls. Guard-style questions the agent could not ask
before become answerable: *which experiments were not run here, and why.*

**Makes hard.** Every fact a primitive gates on needs a member on `ProjectFact`
and an Explorer that establishes it, and an unestablished fact now withholds the
instrument rather than quietly defaulting. That is the cost of the decision and
is the intended direction: E7's grounding stories acquire a consumer with a
stated appetite. A primitive whose applicability genuinely cannot be reduced to
facts must write its own predicate and can still get this wrong — the
combinators make the safe path the short one, they do not make the unsafe path
unreachable.

**Rules out.** Gaining or losing an instrument mid-investigation, which also
rules out the design where the Diagnostician asks the Explorer for a missing
fact and is handed a new tool in the same run. It can record the need; the
instrument arrives in the next investigation.

## Provenance

Six sabotage runs, each asserting the edit was actually detected: collapsing
`UNDETERMINED` into `APPLICABLE` fails 6 tests; rendering in registration order
fails 3; letting `Selection.get()` return a withheld primitive fails 1; letting
`register()` overwrite a duplicate fails 1; storing the caller's facts mapping
instead of copying it fails 1; dropping annotation resolution fails 2.

The seventh found a defect in the tests rather than the code. `all_of()`
returning the first failing condition instead of the most decisive one passed
the whole suite, because the test happened to list the decisive condition first —
the same shape of hole S-2.9's controls were written against. The test now runs
both orders and fails the sabotage.

# 091 — A partial chain is not an evidence chain with fields missing

**Status:** accepted
**Story:** S-8.9 — budget and progress
**Date:** 2026-08-16

## Context

Three acceptance criteria — a 40-experiment cap; escalation after 8 experiments
with no narrowing; and on exhaustion, a partial chain containing the exclusions,
*because a proven negative is a result*.

Two of the three turned out to be mostly built already, and running the third
against the first found a defect S-5.4 had predicted in its own docstring.

## Decision

### The cap existed; running a loop against it found it was a third of its size

`Cap(40, StepUnit.EXPERIMENT, Scope.FINDING, Disposition.PARTIAL)` has been
compiled since S-5.4, so the tests assert it rather than a reimplementation —
the third Epic 8 criterion to turn out already enforced, after S-8.1's
no-cascade and S-8.6's attached measurement.

But `Session.run` called `budget.record_step` once per **model call**, and the
investigate loop makes three calls per experiment. S-5.4's own docstring names
this exactly:

> §12.1 budgets 120 model calls per finding in investigate against a cap of 40
> experiments — so an experiment is about three calls, and **a cap counted in
> calls would halt investigation at a third of its intended budget.**

The forty-experiment cap was a thirteen-experiment cap. It survived because
nothing ran a whole loop against it until now: Epic 5's composition test drove
one `session.run` per iteration, which made a call and an experiment look like
the same thing. `Session.run` now records a step only where the phase's cap is
counted in steps; a phase counted in experiments, attempts or rounds has its unit
counted by whoever owns that unit, which for investigate is this story's loop.

**The module that predicted the error is the one that had it**, and the test that
should have caught it encoded it instead. Two of Epic 5's composition tests were
updated, with the reason recorded in them.

### The progress check needed a number, and the default is nobody's answer

`03-agents.md` §4.5 puts the investigate check at **8 experiments with no
narrowing**; `DEFAULT_STALL_AFTER` is three. S-7.10 met the same shape for
grounding and set the rule: **a budget with the wrong value is refused, not
corrected**, because silently substituting the right one hides that the caller
asked for something else.

S-7.10 also wrote, in a comment, that three *is* right for an investigation. It
is not, and that comment is corrected: every phase that has looked has needed its
own value, so the default is a default rather than any phase's answer. `Session`
gained a `stall_after` pass-through, because it is the only thing that constructs
a `Budget` and a phase had no way to ask.

**"No narrowing" is decided by the harness**, which is S-5.4's own rule recording
F6's finding that a self-judged criterion is one the agent is incentivised to
claim. A rejection is not narrowing — `02-architecture.md` §2.2: *reject → new
hypothesis informed by the exclusion; narrow → new hypothesis, one level deeper*
— so a rejection records a constant conclusion that extends S-5.4's run of
repeats, and anything else records `None`, which clears it.

### A partial chain is a separate artifact with the opposite requirement

This is the story's substance. `EvidenceChain` requires at least one
**confirming** localization link, and relaxing that so it could represent an
investigation that confirmed nothing would destroy the guarantee S-8.6 exists
for: a chain would stop meaning *a cause was established*.

So the two partition:

- an `EvidenceChain` requires at least one confirming experiment;
- a `PartialChain` refuses to hold any.

Neither can impersonate the other, and a consumer that wants a finding cannot be
handed the other by accident. A partial chain carrying a confirmation would
report an established finding as an absent one, which **loses a result** rather
than merely mis-typing one — the mirror of the failure S-8.6's check prevents.

**It has no `mechanism`, no `site` and no `confidence`, and their absence is the
artifact's meaning.** Those are the claims a stopped investigation cannot make,
and a field for one is somewhere a reader could put a guess. §9 ships null
results as answers; it does not ship them as findings with the interesting parts
blank.

Empty exclusions are legitimate — forty experiments that all narrowed exclude
nothing while still having learned something — so the rendering says which case
it is rather than printing an empty section.

### Stopping is not failing, and there are three ways to stop

`Stopped` has three members because a reader's next action differs for each, the
argument S-3.1 makes for four applicability states. Collapsing them into *it
failed* would lose the one saying the subject may simply have no more applicable
experiments. Each carries §7.2's disposition: the cap's is `PARTIAL`, the other
two escalate.

`run_investigation` returns in every case rather than raising, and `max_steps` is
gone — S-8.7 carried it because the caps were another story's, and there is now
exactly one number that stops this loop: the forty `04-cost.md` costed.

**A euro-ceiling breach still propagates.** S-5.4: *the halt is the global
ceiling's alone.* Reporting one as *the experiment cap was reached* would be a
false statement about why the run ended, so the disposition is read and `HALT` is
re-raised.

## Sabotage

Seventeen properties, all caught — after five survived, **every one of them in the
loop wiring rather than in the module**. The unit tests exercised `progress.py`
thoroughly and the loop's use of it not at all, which is the same asymmetry S-8.7
hit: a criterion asserted where it is defined and not where it has to hold.

Two of the five were not test gaps but code that could not matter:

- *The loop's `authorize` before each step.* Removing it changed no outcome,
  because `Session.run` authorizes inside its first attempt against the same cap,
  before any spend. S-7.4's redundant condition, collapsed — the fifth in Epic 8.
- *`Investigation.confirmed`.* A property with **no caller at all**, duplicating
  what `PartialChain`'s validator already refuses. Deleted.

The other three needed loop-level tests: running out of instruments must not be
reported as the cap, a stopped investigation must hand over the exclusions it
bought, and a running one must have no partial chain to give.

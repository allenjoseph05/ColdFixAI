# 033 — A replay stub that is not size-representative is an empty stub in disguise

**Status:** accepted
**Date:** 2026-08-06

## Context

S-3.4 asks ablation to record a real return value during a baseline run, replay
it during ablation, fall back to a minimal valid value where replay is
impossible, record which strategy was used, and run only in diagnostic mode.

The story's note gives the reason the strategy has to be recorded: an
empty-collection stub measures the component *plus* all the downstream work that
consumed its output, while a replayed real value measures the component alone.
S-0.4 was run specifically to confirm that before this story was built, and it
returned three things that bind the implementation:

1. The two strategies were **statistically indistinguishable on timing**
   (434.64 ms against 438.14 ms, p = 0.64) while differing **six-fold in
   payload**. A spike measuring only wall time — the obvious thing to measure —
   would have concluded the strategy does not matter and deleted this story's
   recording requirement.
2. Its first run recorded a replay value of **one** followup, because the first
   ticket with any was a demo row sorting ahead of the synthesized ones. The
   replay payload was then nearly as small as the empty stub's and the two
   strategies looked interchangeable — the correct final conclusion, reached by
   accident, for entirely the wrong reason.
3. Replaying one fixed value does not preserve per-instance cardinality: the
   ablated run emitted **600 followups where the baseline emitted 586**, so it
   was charged *more* downstream work than the baseline ever did.

## Decision

**The value replayed is the one whose size is closest to the median of every
size observed, and the distribution it was chosen from is recorded beside it.**
This is finding 2 turned into a mechanism. The obvious implementation — keep the
first value seen — is what the spike did, and it silently converts the replay
strategy into the empty one: a stub carrying a sixth of the median payload
measures the component plus five sixths of the downstream work, under a label
saying it measures the component alone. Recording the distribution matters as
much as choosing from it, because a reader given only the chosen size cannot see
this happening and a reader given the population can.

**The cardinality gap is computed and recorded**, not assumed small. It was 0.8%
on the spike's endpoint and the spike said plainly it would not be harmless if
the component fed something expensive — the ablated run is then charged more
downstream work than the baseline ever did, and the delta **understates** the
component's cost, which is the direction that loses a real finding.

**A single-use iterator is passed through untouched and marked unreplayable.**
Capturing a generator means consuming it, and consuming it breaks the very run
being measured — the workload that asked for it receives nothing, and the
baseline becomes a measurement of a workload that did no work. So iterators
select the fallback rather than being read.

**Recorded values are deep-copied once, at record time.** A recording that
aliased the returned object would replay whatever the workload later did to it —
and the common case, a list the consumer drains or clears, replays as empty.
That is the same failure as finding 2 arriving by a different route. The copy is
charged to the baseline condition alone, which makes the delta *conservative*: it
can overstate the component by one `deepcopy` per call and never understate it.

**The stub returns the same object to every call, never a copy.** Copying per
call would charge deep-copy cost to the ablated condition only, which is the
distortion S-0.4 avoided by installing its patch once and switching it with a
module-level flag. The consequence — a workload that mutates what the target
returned sees a value an earlier call already mutated — is stated rather than
fixed.

**Where neither strategy is available, the ablation is refused.** A stub
returning `None` where the consumer expects a collection does not measure the
component; it measures how long the workload takes to raise an `AttributeError`,
and that number looks exactly like a very fast component. Minimal values are
constructed from the observed *type*, with a subclass fallback, so an uncopyable
`list` subclass still gets an empty list rather than a refusal.

**Ablation requires a `DiagnosticSession` object, not a mode flag.** `CLAUDE.md`
requires this to be structural. A `DiagnosticSession` can be obtained only from
`Workbench.open(mode=DIAGNOSTIC)`, has no method that returns a diff (ADR 022),
and has its worktree destroyed on exit; a `CandidateSession` is a sibling type,
so passing one fails type-checking, and it is refused at runtime as well. There
is no value of `mode=True` to pass. What this does *not* do is stop a caller
monkeypatching a target without going through this module at all — the same
qualification ADR 024 and ADR 028 carry, and it is why the guarantee is stated as
"ablation cannot be invoked on the object that can emit a diff" rather than
something broader.

**The shared measurement machinery moved to `primitives/measurement.py`.** This
is the second caller — the point `CLAUDE.md` permits an abstraction to appear —
and the alternative was an ablation module reaching into a scaling module for a
private helper. Nothing in it is new.

## Consequences

**Makes easy.** S-3.5's delta-debugging search, which needs to run many ablations
and compare them: the stub choice, the mode check and the cycle are already one
call. Reporting *what disappeared* per metric rather than only in wall time,
which is what let S-0.4 see the strategies differ at all.

**Makes hard.** Ablating a target whose return value is a live resource. That is
refused rather than approximated, because the approximation measures an
exception. Ablating a target that is never called during the baseline is also
refused: a component that does not run cannot own any of the cost, and a delta of
zero for it would read as *measured and cheap*.

**Rules out.** Interpreting a delta without knowing which stub produced it, and
installing a stub anywhere a diff can be read back.

## Provenance

Four sabotage runs, each asserting the edit was detected: replaying the first
value instead of the median fails 3 tests; removing the diagnostic-session check
fails 2; consuming the iterator during recording fails 3; recording the live
object instead of a copy fails 3.

**The last of those first reported 2, and the missing one was a weak test.**
`test_the_recorded_value_is_a_copy_not_the_live_object` asserted the recorded
*size*, which is computed eagerly at capture and survives aliasing intact. The
property that matters is the recorded *value*, because that is what gets
replayed — an aliased recording of a list the consumer later clears replays as
empty, which is this ADR's whole subject arriving through a third door. The test
now asserts the value and the resulting stub. Third story running where a passing
sabotage found a defective test rather than defective code.

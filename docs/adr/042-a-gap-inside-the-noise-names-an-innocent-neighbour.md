# 042 — A gap inside the noise names an innocent neighbour

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.13 asks for a component run standalone and in full context with the gap
reported, and for findings marked diagnose-only. Two acceptance criteria, and the
smaller of them is the one with a trap in it.

`01-primitives.md` §11: the gap *is* the interference, because it exists only
when something else is running. §17 places this in a composition —
*Load → Isolation → Substitution* — where the USL fit says contention is the
limit, isolation says which neighbour, and substitution finds the setting.
S-3.12's contention message names this module for that reason.

## Decision

**A gap smaller than the spread of the isolated runs is not interference.** Two
runs of the same thing differ, so a primitive that reports any positive gap
reports interference for every component it is ever pointed at. What makes that
worse than an ordinary false positive is that **the finding names a real
neighbour**: a vague wrong answer wastes time, and a specific wrong answer sends
someone to change a queue their component never touched. So the isolated
condition is run repeatedly, its own range is the floor, and a gap inside it is
reported as *no interference detectable* — an exclusion worth recording rather
than a search that failed.

The range rather than a standard deviation, for S-1.5's reason: timing
distributions do not have the shape a standard deviation assumes.

**Attribution is a separate step, and it is the one §17 needs.** A gap against
the whole context says a component is interfered with; a gap measured against
each neighbour on its own says by what, and only the second can be acted on.
Where the whole context interferes and no neighbour does alone, that is reported
as such — it is the combination, which is a different and harder finding, and
naming one of them would be inventing an answer.

**The gap is reported as both a difference and a ratio.** 200ms of contention
means one thing on a four-second job and another on a twenty-millisecond one.

**The magnitude is a search result.** Same discipline as S-3.10's sweep: the gap
is real, its size is a median per condition against a noise floor, and the
explanation says to confirm it with S-1.6's interleaved comparison before quoting
a number.

**Diagnose-only, enforced twice.** The disposition is on the finding, and the
mechanism sentence is written so S-2.9 refuses it in its own constructor —
independently of what this module remembers about itself. §11 states the
restriction and what it buys: *this is what allows the claim "faster without
breaking anything" to be true.*

**The context stops in a `finally`.** A measurement that failed is exactly when
background load is most likely to be left running, and everything measured
afterwards would be measured against it.

## Consequences

**Makes easy.** The middle step of §17's composition, which S-3.12 already points
at by name. Recording "these two do not contend" as a real result.

**Makes hard.** Detecting interference smaller than a component's own run-to-run
variation. That is deliberate: at that size the instrument cannot tell the
difference between a neighbour and a scheduler.

**Rules out.** Naming a neighbour on a difference the component produces on its
own, and patching any of it.

## Provenance

Four sabotage runs, each asserting the edit landed: treating any positive gap as
interference fails 3 tests; marking the finding repairable fails 2; wording the
mechanism so S-2.9 does not catch it fails 2; stopping the context outside a
`finally` fails 1 — **but only after a test was added for it**.

That last one passed at first, for the third distinct reason a sabotage can pass:
the branch was unreachable. Nothing in the suite raised inside a live context, so
the `finally` was never exercised, and the case it exists for — neighbours left
running after a failed measurement, taxing everything afterwards — had no test at
all.

**The contended test was also flaky and the fix is a platform fact worth
recording.** At a 20ms lock hold it measured 0.0203 alone against 0.0205
contended for a workload that holds the lock its whole life. Windows' timer
granularity is about 15.6ms — the same number S-3.9 hit from the other
direction — so a 20ms sleep is one or two quanta and the two conditions land on
the same one often enough to matter. The hold is now 50ms, comfortably above the
quantum, and the file passed four consecutive runs.

# 130 — The gate is on because level zero is the only level

**Status:** accepted
**Date:** 2026-08-21

## Context

S-12.4 asks for `interrupt_before=["ship"]` **at trust level 0**, and the trust
ledger is S-13.4 — a whole epic away. `state/checkpoint.py` leaves it out
deliberately: `08-audit.md` F5 moves it to the persistent store, because a rewind
that restored the trust level preceding the failure that caused the rewind would
make the agent re-earn the same lesson.

So the story names a condition nothing can evaluate. That was flagged as a design
question three sessions running before this story was started.

## Decisions

### 1. There is no trust-level parameter, and the absence is the enforcement

S-13.4's third criterion answers it: *new projects start at level 0 regardless of
cross-project history.* **Level 0 is therefore the only value any project can be
at until that ledger exists**, and a gate conditioned on it is a gate that is
always on.

A `trust: int` parameter would have exactly one reachable value. The danger is not
that nobody could flip it — it is that somebody **could**, turning the gate off
with no ledger to justify it, in a system where the ledger is the only thing that
would ever have earned the right. `repair/slack.py` made this argument first and
for the same reason: *there is no trust-level parameter, and that is the
enforcement.*

What `assemble` gained instead is `gated: bool = True`. It is named for what it
is for — a test that needs a graph which runs to completion — rather than for a
policy it does not implement.

### 2. The default flipped, and nineteen call sites moved

`gated=True` means every existing caller of `assemble` had to say otherwise. That
is the right direction for a safety property: the dangerous configuration is the
one that has to be asked for. Three of S-12.3's resume tests were genuinely
changed by it — those runs reach `ship` and now park there — and they compile
ungated with a note, because they test a crash and what survives it, not the gate.

**A gated graph with no checkpointer is refused.** `interrupt_before` parks the
run *in the checkpoint*; with nowhere to park, the run stops at `ship` and can
never be resumed, and the approval a human gives on Thursday has nothing to
return to. The two arguments are related and the error says so.

### 3. Parking is visible in the pending task, not in the channels

`progress_of` reads channel values — what a run has *written*. Parking before
`ship` writes nothing, so a state-only view cannot tell a run stopped at the gate
from one that ran `ship` and happened to change nothing. `waiting_at` asks the
graph for its next task instead, which is the only place the pause exists.

### 4. An incomplete approval is refused, not rendered with blanks

`pending` raises when the parked patch has no chain, or no recorded audit
verdict. **Blanks are worse than an error here**: a person shown an approval with
an empty evidence section reads it as *no evidence* rather than as *the report is
broken*, and the first of those is a reason to reject a good patch.

`NotAtTheGateError` is separate from `MissingInputError` because *this run has not
reached ship* and *this run reached ship with nothing to show* send a reader to
two different places — one is a run still working, the other is a defect.

**The verdict shown is the last one, not the first.** S-11.7 sends a broken patch
back to the Surgeon and the second round appends another flag; showing the
earliest would present a human with the verdict on a patch already replaced.

### 5. Two rendering rules that stop a flattering report

A metric measured on only one side is rendered as *measured on only one side*,
never as a fall to zero. A metric that vanished is a measurement nobody took, and
showing it as a delta would invent the most flattering number available.

A slack-reducing patch says so **first**. `00-BRIEF.md` §4 requires the warning
prominently, and a label under four screens of diff is not prominent. The flag
rides on the handover rather than being recomputed at the gate — recomputing it
would be a second classifier that disagrees with S-10.6 the first time either
moves.

### 6. The measurements are taken once and read twice

`audit_patch` measures for the attacks, and the gate shows the same numbers.
Measuring again for the second reader would put two different sets of figures
under one patch — the run is not deterministic enough for them to agree — and a
human comparing the report against the verdict would be right to distrust both.
It would also pay for the sweep twice.

## Consequences

AC 3 is tested by making the delay irrelevant rather than by simulating one: the
run parks, every object it used is dropped, and a second `for_development` opens
the same file from scratch. Nothing carries over but the checkpoint, so an hour
and a fortnight are the same test.

S-12.5 wants the same gate after the finding audit, *enabled at trust level 0 and
skipped at higher levels*. When the ledger lands, both gates want the same
condition, and this is where it goes — one place, asked once. Until then the
honest reading of "at trust level 0" is "always", and saying so in code is better
than a parameter that pretends otherwise.

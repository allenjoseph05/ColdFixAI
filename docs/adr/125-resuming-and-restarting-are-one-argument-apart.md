# 125 — Resuming and restarting are one argument apart

**Status:** accepted
**Story:** S-12.3 — crash resume
**Date:** 2026-08-20

## Context

S-12.2 gave a run somewhere to write its state. This story is the reason that
mattered: a campaign runs for hours across containers and databases, and the
machine it runs on will be rebooted.

The acceptance criteria are unusually specific about method — *tested by killing
the process at three different nodes* — and that specificity is the story.

## Decisions

### 1. The processes are really killed

`crashing_run.py` calls `os._exit` inside the node. An exception would unwind,
run every `finally`, flush every buffer and close the SQLite connection — a
graceful shutdown wearing the word crash, and a test that proved nothing about
what a reboot leaves behind.

`os._exit` skips all of it, so what the checkpoint file holds afterwards is what a
real kill would have left — and what it held, at first, was nothing. See the
correction below.

The three nodes are chosen as three different *kinds* of point — after the first
write, mid-investigation, and inside the repair cycle — and a test asserts the
kills land progressively further along, so the parametrised cases are three cases
rather than one run three times.

### 2. `invoke(None, ...)` resumes and `invoke(state, ...)` restarts

LangGraph reads `None` as *continue from where this thread left off* and anything
else as *begin with this*. Handing the initial state back after a crash is
therefore not a resume: it is a second run that repeats every node, bills every
phase again, and returns a plausible final state.

**The mistake is invisible.** Both calls return a final state, and the wrong one is
detectable only by the bill. `start` and `resume` are two names for that one
argument, and `resume` takes the checkpointer so it can refuse a thread nothing
was ever written for — because `invoke(None, ...)` against an unknown thread starts
a new run rather than failing.

### 3. A missing channel is not an empty one

S-12.2 found that a checkpoint holds only the channels written so far. `progress_of`
returns what is there and fills nothing, because a default would hand a resumed run
a value it never had and the run would proceed on it.

### 4. At-least-once is reported, not fixed

A checkpoint is written *after* a node, so a crash inside one re-runs it. That is
not a defect to correct here — it is the property every node has to be written
against, and the bite is on the append-only channels: `experiments` gaining the
same entry twice is S-8.4's guarantee broken by a crash rather than by a rewrite.

`duplicated` reports it and does not deduplicate. A deduplication would hide the
fact that a node was not idempotent.

**What makes this safe today is where the appends happen**: in the node's return
value, after the work. An interrupted node therefore contributes nothing rather
than half. A node that grew a side effect before its return would break this, and
the test is what would say so.

### 5. The comparison names the channel

`same_outcome` returns the disagreeing channels rather than a boolean, because
*the resumed run produced a different answer* is only actionable with the channel
named. `ignoring` is empty by default, so each excused channel has to be named —
a default that excused anything would make the function agree with itself.

## Correction — checkpoints were not durable, and the tests passed anyway

**The first version of this story did not meet AC 1, and its tests could not have
said so.**

LangGraph submits each checkpoint write to a **background executor** and does not
wait. `os._exit` takes the process before those threads run, so a kill at any of
the first four nodes left exactly one checkpoint holding nothing but `__start__` —
however far the run had got.

`SqliteSaver` was not at fault: its cursor commits in a `finally`. The claim above
that it *commits per write* was read off S-12.2's test, which closes the connection
cleanly, and a clean close is not a kill.

**Why every test still passed.** With nothing durable, a resume restarts from the
beginning and reaches the same final answer. So *resumes with full state* (AC 1)
and *the same final result as an uninterrupted run* (AC 3) both held — vacuously.
The only test that could see the difference was the one asserting the three kills
land at progressively different depths, and it was flaky rather than failing:
sometimes the last node's write raced through, giving `[1, 1, 2]`, and sometimes it
did not, giving `[1, 1, 1]`.

**The fix is `durability="sync"`**, applied in `start` and `resume` as a module
constant rather than per call site — a run started durable and resumed
asynchronously would be half-protected in a way nothing reports. Measured, kills at
five successive nodes now leave 2, 3, 4, 5 and 6 checkpoints against 9 for a
completed run.

**The tests were strengthened to be able to fail.** `checkpoints >= 1` is satisfied
by the empty `__start__` checkpoint, so the assertion is now that a channel the run
actually wrote survived; and the three depths must be *distinct*, because equal
counts are exactly what an asynchronous write produces.

A second claim went with it: *a channel absent from a checkpoint has not been
written* was also an artifact of the async default. With durable writes the initial
state is checkpointed in full, defaults and all. S-12.2's observation still holds
for the first checkpoint — the one holding `__start__` — but not for the one a
resume reads.

## Consequences

**The test suite gained about 95 seconds**, all of it subprocess launches. That is
the cost of AC 2 being about processes rather than about exceptions, and it is
worth paying — but the fast subset now runs well past the 10-minute mark, and at
some point that needs a decision rather than another story's worth of drift.

**Three stories in this epic have now been about a library's defaults rather than
about our code.** S-12.1 found that LangGraph hands a node the schema instance;
S-12.2 that `with_config(checkpointer=...)` silently does nothing; this one that
`invoke(None, ...)` is the difference between resuming and paying twice. All three
are the kind of thing a passing test suite would never mention, and all three were
found by writing the demonstration the acceptance criterion asked for instead of
assuming the library did what its name suggested.

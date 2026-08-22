# 133 — A rewind to a gate counted as approving it

**Status:** accepted
**Date:** 2026-08-22

## Context

Epic 12 finished seven stories: a graph, a checkpointer, crash resume, two human
gates, time travel, and the adapters binding it to the epics. Its own sentence is
**durable execution across hours, crashes, and multi-day human gates**, and every
story proved its piece against its own fixture.

Five previous epics ended the same way and every one of their composition checks
found a defect at a join. This is the sixth, and it did too.

## The defect

`interrupt_before` parks a run **at** a node: the checkpoint it stops on is the
one whose next step is that node. `invoke(None, config)` from that checkpoint is
exactly what a human approving the gate does — S-12.4's `resume` is that call.

S-12.6's `before(graph, run_id, node)` returned that same checkpoint.

So the two calls were indistinguishable, and a rewind ran the node it was supposed
to be reconsidering. Measured:

| | parked at |
|---|---|
| a fresh gated run | `repair` |
| a rewind to the identical checkpoint | **`ship`** |

The rewind ran `repair` *and* `audit_patch` and stopped at the next gate. Somebody
rewinding to reconsider the direction got the repair re-run instead, unasked —
which is the inverse of what a gate is for, reached through two modules that are
each correct.

**Neither story could have found it alone.** S-12.4 and S-12.5 test gates on runs
that were never rewound; S-12.6 tests rewinds on graphs compiled `gated=False`,
because a run that parks never reaches the nodes whose writes those tests check.
The defect lives only where both are true.

## Decisions

### 1. `before` targets one checkpoint earlier for a gated node

Re-entering the node is what makes the interrupt fire — it fires on entry, and a
resume from the parked checkpoint is not an entry. So the rewind target becomes
the checkpoint whose next step is the *preceding* node, and running forward from
there re-enters the gated one and parks again.

Verified: the run parks at `repair`, with `target` and `project` intact and
`attempts` empty on the new branch.

### 2. Only where a gate would otherwise be skipped

Re-entering costs the preceding phase, which is a model call. An ungated node has
no gate to skip, so it keeps the cheap target — the parked checkpoint itself.

`interrupt_before_nodes` is **asked of the compiled graph** rather than passed in.
The answer is a property of how the graph was compiled, and a caller repeating it
would be a second statement of the same fact, wrong the first time somebody
assembles with different gates.

### 3. The crash and the gate meet in one test

`crashing_run.py` gained a fourth argument. S-12.3's tests want an ungated run —
a run that parks never reaches the nodes whose writes they check — and the
composition wants the opposite, because *crashes* and *multi-day human gates* are
one sentence and a campaign that dies between an approval and the next gate is
the ordinary case. Defaulted, so every existing caller is unchanged.

### 4. The three readers of a run are compared

`progress_of` counts writes, `waiting_at` asks the graph for its pending task, and
`history` lists the checkpoints. Three stories built three of them and nothing had
compared them. They agree — which is worth a test rather than an assumption,
because a run described two ways at once is how the next join goes wrong.

## Consequences

Epic 12 is complete: seven stories and a composition check that found a real
defect, which is now six epics for six.

**The pattern is worth naming because it is not the same one as before.** The
previous five checks found *a value one story produces and another consumes, with
neither story's tests holding both ends*. This one is different: no value crosses
between S-12.4 and S-12.6 at all. What they share is a **call** —
`invoke(None, config)` — that means two different things depending on which
checkpoint the config names, and neither story's fixture could contain the other's
configuration. S-12.3 already recorded two members of that family (`invoke(None)`
resumes, `invoke(state)` restarts, both returning a final state); this is the
third, and the tell is the same: **both calls succeed, and only the outcome
differs.**

# 132 — A rewind forks the history; it does not append to it

**Status:** accepted
**Date:** 2026-08-22

## Context

S-12.6 is time travel, and its second and third criteria are `08-audit.md` F5
almost verbatim:

> Time travel restores state at checkpoint T. But the reason for rewinding is a
> failure discovered at T+n — and that failure record lives in the state being
> discarded. We rewind and the agent repeats the same attempt. **This inverts the
> intent.** We want to rewind the *code* and keep the *learning*.

F5's prescribed fix is to split the state, and S-6.1 and S-6.2 built that split.
The question this story had to answer is whether the split is *sufficient*.

## Decisions

### 1. F5 was measured before it was fixed, and it reproduced

A probe drove a run to completion and inspected its history. At the checkpoint
whose next step is `repair`, `attempts` is `[]`. Resuming from there produced the
same approach the completed run had already recorded as failed.

The test asserts that repetition rather than describing it. A fix whose defect was
never demonstrated is a fix nobody can check — and this project has now found
three tests in two days that stayed green while measuring less than they claimed.

### 2. The first framing of the measurement was wrong

The helper was written as *approaches appearing more than once in the attempts
list*. It reported nothing, and the reason is worth keeping:

**A rewind forks the history. It does not carry the later branch's writes forward
and add to them.** The new branch starts from the checkpoint's values, so
`attempts` holds one entry afterwards, not two. Nothing is repeated *within*
either branch — the repetition is *between* them, which is precisely why F5 says
nobody notices.

`already_failed(discarded, retried)` takes both sides. An approach in both is
knowledge that was paid for and then bought again.

### 3. Splitting the state is necessary and not sufficient

The split stops the knowledge being **destroyed**. It does not put it back in
front of the Surgeon, and `repair` began every call with no prior attempts — so a
rewound run got the earlier code state and none of the later knowledge, which is
the inversion F5 names.

`repair` gained `remembered: Sequence[Attempt] = ()`. It feeds two things and
deliberately not a third:

| Reads `remembered` | Reads `attempts` alone |
|---|---|
| what the Surgeon is shown (`prior=`) | `authorize_attempt` — the cap |
| the repeat check (`retry.repeats`) | `temperature_for` — the ramp |
| | `escalate` — the report |

The counters are facts about *this* repair. Charging a rewound one for attempts it
is only being told about would let two rewinds exhaust a budget without the
Surgeon writing a line.

The repeat check compares **diffs**, not the `approach` label, which is F12's
finding: the label is self-judged and the agent can rename the same idea. So the
test remembers a patch and generates the identical edit.

### 4. AC 2 is proven on both arms, and only one of them is interesting

The persistent store is unreachable from a rewind because it is a different
database. For ADR 003's development checkpointer that is trivial — a SQLite file
is not a database, and `refuse_shared_store` returns early on a `Path`.

The arm worth testing is Postgres, which S-12.2 supports for concurrent
campaigns and which is the configuration where the two *could* collide.
`refuse_shared_store` raises there. Testing only the SQLite arm would have proven
the trivial case and left the reachable one open.

### 5. Rewind points are addressed by node, not by checkpoint id

`before(graph, run_id, Node.REPAIR)` rather than a hex id, because that is how the
decision is actually made: somebody rewinds *to before the repair*. An id is a
fact about one run; the node is the thing two runs have in common.

The **first** such point, not the last — a graph with a cycle visits `repair` more
than once, and rewinding to the second keeps the attempt the rewind is presumably
about.

`history` reverses LangGraph's newest-first ordering once, here. Newest-first is
right for *what happened last* and wrong for choosing a rewind, which is read
forwards.

## Consequences

Epic 12's stories are done. The composition check remains, and every epic that has
ended without one has found a defect at exactly that join — five for five.

**S-13.3 is now load-bearing rather than merely next.** `remembered` is a
parameter nothing fills: the adapters do not read `Collection.FAILURE_MEMORY`,
and nothing writes it. Until that story lands, a real rewound run still repeats
itself — the mechanism is in place and the wiring is not. That is recorded on
S-12.6 rather than rounded up, and the control test
(`test_without_the_memory_the_same_run_ships_what_already_failed`) is what a
reader should look at to see the gap is real.

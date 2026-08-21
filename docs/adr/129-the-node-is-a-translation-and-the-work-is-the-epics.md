# 129 — The node is a translation, and the work is the epic's

**Status:** accepted
**Date:** 2026-08-21

## Context

S-12.1 wired seven nodes, four routers and a compiled graph, and left AC 3's
second half — *runs end to end on the target repo* — undone, because `Wiring`
takes seven `Step`s and nothing built them. Two attempts at that binding have now
been made. The first, on 2026-08-20, was reverted without a commit: it found that
two of the seven nodes had nothing in `src/` to call. ADR 127 and ADR 128 built
those two producers. This is the third attempt and the one that stands.

## Decisions

### 1. A node is a rehydrate, call, serialize sandwich, and it owns only the bread

`CheckpointedState` is JSON because ADR 003 puts it in SQLite. Every epic entry
point takes live objects. That difference is the entire job of this module — the
call in the middle belongs to a composition that already has its own check.

So the tests here are about the slices, not the filling: what survives a round
trip, and what a node refuses to invent when a channel is empty.

### 2. Two types are named `Session` and conflating them is the first mistake

Every compose entry point takes a `cost.session.Session` **positionally** — the
prompt, the budget, the cached prefix — and takes a `sandbox.modes`
`DiagnosticSession` or `CandidateSession` by *keyword*, which is a worktree bound
to a container. They share a name and nothing else. The reverted first draft
passed the second where the first was wanted, in five places.

`Sessions` builds the cost session, **keyed by the step's system prompt**,
because that is what `refuse_shared_session` compares: *the isolation is the
fresh message list **and** the fresh prompt*, and a shared session undoes the
second silently. Every prompt the adapters ask for is one `agents/roles.py`
independently asserts belongs to the role making the call — so a session built
here cannot belong to an agent that is not the one spending.

### 3. `repaired` is a channel §1.1 never had

`03-agents.md` §1.1 lists eleven channels and none carries a patch from `repair`
to `audit_patch`. The list was written when the two were adjacent boxes in a
diagram rather than two nodes with a checkpoint between them. Same shape as all
four defects Epic 11's composition check found: a value one story produces and
another consumes, with neither story's tests holding both ends.

It replaces rather than appends, like `chain` and `target`. **Not `attempts`**,
which is append-only history — reading the current handover off its last entry
conflates *what happened* with *what is being audited now*, and breaks the moment
S-11.7 sends a patch back for a second round.

It carries the falsification proof as well as the patch, because `Falsified`'s
constructor refuses to describe a failure as a success and re-deriving it on the
far side of a checkpoint would build that proof from something other than the run
that actually failed.

### 4. The experiment log is stored without `detail`

S-6.3 designed `ExperimentRef` for this: state holds a key and a one-line
summary, and the full result lives in the replay cache. **That path is not
reachable** — building a reference needs a `Recall`, which only the cache
produces, and `run_investigation` never sees the cache.

What is stored instead is the `Experiment` artifact minus `detail`. The
arithmetic works out the same: `MAX_REFERENCE_BYTES` is 1 KiB and a record
without its raw output is under that, so forty experiments still fit the budget
F13 set. `detail` is the field S-8.4 holds *always and renders never* — stdout,
stacks, per-call timings — and it is exactly what would turn a checkpoint into
the megabytes-per-node write F13 exists to prevent.

The log is rebuilt by replaying `append` rather than by construction, because the
log assigns indices and `read_experiment(7)` has to mean the seventh experiment.

### 5. `ship` does F14 and does not pretend to do S-16.2

The pull request — before and after on every varied axis, the evidence chain, the
guard metrics, the Adversary verdict — is S-16.2, two epics away. What exists is
the half the graph reads: F14's per-workload invalidation, which
`state.staleness.screening_plan` computes and `graph.after_ship` consumes. A stub
PR here would be a second, worse answer to a question another epic owns.

The patch's touched files come from `touched_paths(diff)` and not from a field,
because `Patch` deliberately has none: *the agent would be restating what the
diff already says, and a list that disagreed with its own diff is a scope check
passing against a claim rather than against the change.*

### 6. S-6.3's named-parameter prediction, caught the third time

A closure typed `Callable[[CheckpointedState], Mapping[str, object]]` does not
satisfy `Step`, whose `__call__` declares a *named* parameter. S-12.1 hit this,
the reverted draft hit it again, and S-6.3 wrote it down before either existed.
`_step` is annotated `-> Step`, and a test compiles the real graph over the real
closures so a fourth time fails in a test rather than in a run.

## Consequences

The graph can be assembled over real work. **What is still not proven is the
seven-node drive itself**: it needs a container, a database, and a recording per
model call, and S-17.1 owns that. AC 2 of S-12.7 is therefore met for the
translation and open for the drive, which is recorded on the story rather than
quietly rounded up.

Two things this makes visible rather than fixes. `ExperimentRef` is designed and
unreachable until an executor writes through the replay cache. And `Resources`
has sixteen fields — the length is the finding, not a smell: `graph.py` recorded
that the seven entry points want eleven kinds of argument between them, and this
is that inventory written down once instead of threaded through the graph.

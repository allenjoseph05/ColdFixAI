# 124 — The checkpointer is a compile argument

**Status:** accepted
**Story:** S-12.2 — checkpointing
**Date:** 2026-08-20

## Context

S-12.1 compiled a graph. This story gives it somewhere to write its state:
SQLite in development, Postgres for concurrent campaigns, a checkpoint after every
node, and the whole thing inside S-6.3's size bound.

## Decisions

### 1. Two packaging defects came first

**`langgraph` was a dev dependency and `src/` imports it.** S-12.1 introduced that:
`orchestrator/graph.py` imports `langgraph.graph`, so a wheel would fail at import.
It sat in the dev group beside a comment citing it as an example of something
`src/` does not import — a comment that stopped being true the day the graph was
written. Moved to the project dependencies.

**Neither checkpointer backend was installed.** `langgraph` ships only the
in-memory saver, which persists nothing across a process;
`langgraph-checkpoint-sqlite` and `langgraph-checkpoint-postgres` are separate
packages, and AC 1 names both.

Both were found by checking before building, which is now the third time in three
stories that reading what exists changed what got written.

### 2. AC 2 needs no mechanism, only a demonstration

LangGraph writes a checkpoint after every node when a graph is compiled with a
checkpointer — that is what a checkpointer is. What this story owes is a test that
walks a real graph against a real SQLite file and reads the checkpoints back.

A criterion satisfied by a library's default is still a criterion. Taking it on
trust is what would make it worthless, and the test caught two things trust would
have missed (below).

### 3. The backends are named for the question, not the technology

`Backend.DEVELOPMENT` is *one run at a time, in a file*; `CONCURRENT_CAMPAIGNS` is
*many runs at once, in a server*. SQLite serialises writers, so a file is a lock —
the choice is about how many runs share the store, and naming the members `sqlite`
and `postgres` would invite picking one for familiarity.

`for_campaigns` returns a **DSN**, not a saver, because `PostgresSaver` wants a
live connection and opening one here would make importing this module a thing that
talks to a database. The caller owns the connection's lifetime; this owns the
refusal.

### 4. The bound is S-6.3's, checked in the encoding that is written

That story size-checks every `ExperimentRef` at 1 KiB and fixes the state limit at
64 KiB, with the arithmetic behind it. It measured the **JSON** encoding as a
proxy, because ADR 003 says the checkpointer stores JSON — and recorded that
LangGraph's msgpack is about 15% smaller, which is the safe direction.

`measure` reads the real serialiser, so the bound is checked against what lands on
disk, and a test pins the proxy-to-actual relationship rather than assuming it
survives a serialiser change.

`refuse_oversized` names the cost in its message: **a checkpoint is written after
every node**, so an oversized state costs one bad write per transition and the
first symptom is a slow campaign rather than an error.

## Consequences

**`with_config(checkpointer=...)` is accepted, changes nothing, and writes no
checkpoints.** That was the first attempt: five tests failed with an empty list of
saved states. The checkpointer is a **compile-time** argument, so `assemble` grew a
parameter — and a graph compiled without one keeps no history, which is right for a
unit test of the shape and wrong for anything that has to resume.

This is exactly the failure AC 2's demonstration exists to catch: a run that looks
persisted and is not.

**A checkpoint holds only the channels written so far, not the whole schema.** The
earliest ones have no `project` key at all rather than an empty one. That is
S-12.3's problem arriving early — a resume reads these, and code that assumes every
channel is present will fail on the first checkpoint of every run.

**Sabotage: 15 properties, 14 caught, one survivor that is genuinely equivalent.**
Deleting the clause *S-6.3's bound holds only while the experiment log stores* from
the refusal leaves both the cost (*after every node*) and the remedy (*references
rather than results*) stated, and both are asserted. Pinning the connective prose
word-for-word would be testing the wording rather than the content, so it is left
uncovered deliberately and recorded here.

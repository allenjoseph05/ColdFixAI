# 064 — The state is a model, and the framework validates nothing

**Status:** accepted
**Story:** S-6.1 — checkpointed state schema
**Date:** 2026-08-11

## Context

`03-agents.md` §1.1 specifies the graph state as a `TypedDict` whose
`experiments`, `attempts` and `flags` carry `Annotated[list, add]`, and is
unusually direct about why: **without the annotation the agent loses its own
history, re-tests rejected hypotheses, and loops while appearing to work.**

Two things had to be settled before that could be built.

**No ADR had ever chosen the orchestrator.** Every document assumes LangGraph —
`02-architecture.md` §8, `03-agents.md` §1.1, S-12.2's "SQLite checkpointer" —
but it was not a recorded decision and not a dependency. The backlog's own
decisions table assigns "LLM SDK and vendor strategy" to S-0.2 and lists
orchestration nowhere. This is the first story that cannot be written without
taking a position.

**AC 2 is only meaningful against the real runtime.** *A test proves a node
returning a single experiment appends rather than replaces* is a claim about the
framework's merge step. Against a reducer we call ourselves it would pass whether
or not the schema is annotated, which is the one thing worth knowing.

So a spike compiled real graphs before any of this was designed. Three of its
findings decided the module, and one of them contradicted what I had assumed:

| Behaviour | Measured |
|---|---|
| `StateGraph` accepts a Pydantic `BaseModel` schema | yes |
| `Annotated[list, reducer]` appends; an unannotated list field replaces | yes |
| State validated on a node transition | **no** |
| A node returning an **unknown key** | **silently ignored** — no error, no write |
| `extra="forbid"` catches that unknown key | **no** |
| A custom reducer runs on every write and may raise | yes |

## Decision

### A Pydantic model, superseding §1.1's `TypedDict`

`CLAUDE.md` requires a Pydantic model for every artifact that crosses a node
boundary, and the checkpointed state is the artifact that crosses *every* node
boundary — a `TypedDict` would break the project's own code style. It also gives
`dict[str, Any]` under `mypy --strict`, cannot be validated at construction, and
hands nodes a mapping to index instead of typed fields to read.

The reducers survive the change: `Annotated[list[X], AppendOnly(...)]` is
`typing` plus a callable, and LangGraph reads it on a model field exactly as on
a `TypedDict` field. Verified, not assumed.

This does **not** rescue AC 3. My initial reading — that a Pydantic schema would
give per-transition validation for free — was wrong, and the spike is what said
so. LangGraph validates the graph's *input* and nothing after it.

### `langgraph` is a dev dependency, and the schema imports nothing from it

Nothing under `src/` imports LangGraph. The schema is a Pydantic model carrying
stdlib annotations, so it stays framework-independent while being read correctly
by the framework, and the conformance tests that prove AC 2 run against a real
compiled `StateGraph`.

It becomes a project dependency at S-12.1, where the graph is actually
assembled. Until then a dependency no shipped module imports has no business in
the project's dependency list.

**This ADR is also the record that LangGraph is the orchestrator**, which no
earlier ADR made.

### The reducer is checked, not `operator.add`

`add` appends, which is most of the job. What it does not do is notice the
mirror-image bug: a node that returns the whole accumulated channel instead of
its delta **doubles** the history rather than losing it, silently. Nothing
downstream survives that — S-5.7's cached prefix requires the log's earlier bytes
never move, and S-5.8's `read_experiment(7)` has to mean the seventh experiment.

`AppendOnly` refuses three things by name: a bare entry returned instead of a
list, a string (which is a `Sequence`, so the obvious check admits it and appends
its characters one at a time), and a write that begins with the entries the
channel already holds. A test asserts `operator.add` accepts exactly the last of
these, so the choice is shown rather than argued.

### AC 3 is a wrapper, because the framework leaves the hole

`check_update` refuses unknown channels and type-checks each value against the
field it is written to; `node` applies it on every transition by construction.
The unknown-key case is the one that matters: LangGraph drops it silently, so a
node returning `{"experiements": [...]}` writes nothing, reports nothing, and
leaves an investigation that ran to its cap having recorded no experiments —
the story's *loops while appearing to work*, reached through a typo.

### The trust ledger is not in the checkpointed state

`08-audit.md` F5 supersedes §1.1's `ledger` field. Keeping it here **is** the F5
defect: a rewind would restore the trust level that preceded the failure that
caused the rewind, and the agent would re-earn the lesson it rewound to keep. It
belongs to S-6.2's persistent store, and a test asserts its absence.

### Every field is JSON-representable

ADR 003 puts checkpoints in SQLite or Postgres, so a state that cannot serialize
is a state that cannot checkpoint — `JsonValue` is a constraint, not a
placeholder. The artifacts these channels carry belong to epics that do not exist
yet (a workload is S-7.9's, an experiment S-8.4's, a chain S-8.6's), and
inventing their schemas here is the guess S-5.4 explicitly declined to make when
it left the checkpoint schema to this story.

## Consequences

**Makes easy.** S-12.1 registers nodes through `node` and gets AC 3 everywhere
for free. S-6.3 can narrow `experiments` to references without touching the
reducer. Nodes read typed fields.

**Makes hard.** Two type-level traps in LangGraph's API, both recorded here so
S-12.1 does not rediscover them. `add_node` infers `NodeInputT` from its
`input_schema=` argument, and without it that variable solves to `Never`, so
every *correctly annotated* node fails to type-check while an unannotated lambda
passes. And `_Node` is a `Protocol` whose `__call__(self, state: ...)` takes a
**named** parameter, so a plain `Callable[[State], ...]` — which has
positional-only parameters — can never satisfy it; `node` is therefore
signature-preserving (`def node[F: ...](function: F) -> F`) rather than typed as
a bare callable.

**Rules out.** A `TypedDict` state, and any claim that the framework validates
what a node returns.

**Sabotage-verified on thirteen properties, all caught**, including dropping the
annotation from each of the three channels independently. The baseline was
re-run green *after* the pass — the check the Epic 5 composition run skipped, and
the reason its first run was invalid.

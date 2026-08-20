# 122 — The node decides and the edge reads

**Status:** accepted
**Story:** S-12.1 — graph assembly
**Date:** 2026-08-20

## Context

Seven nodes, four routing functions, a graph that compiles and runs end to end.
Everything the nodes do already exists — five epics of compose entry points — so
this story is about shape rather than about work.

## Decisions

### 1. Two of the four routing functions already existed

S-9.8's `verdict.route` decides where a finding audit sends an investigation.
S-11.7's `patchverdict.route` decides where a patch audit sends a patch. Writing
either again here would be a second answer to a question those stories answer, and
the two would disagree the first time a cap moved.

Checking before building is what found this. It is the third time in two epics that
reading the existing code changed the design.

### 2. The decision is made in the node and read at the edge

Both existing routers take a `Budget`. Its caps live in the object and cannot be
reconstructed from the state's `budget`, which is a projection — and a LangGraph
conditional edge sees only state.

So a node calls the router where the budget is and writes the answer to a `route`
channel; the edge reads it. `CheckpointedState` gained that channel for this, and
`decided()` is the one place a route becomes a string, because *which spelling goes
in the channel* is the sort of thing two nodes come to disagree about — and the
disagreement shows up as a run that ends early rather than as an error.

The two routing functions this story does own — after screening and after shipping
— are exactly the two that need nothing but state.

### 3. A route nobody wrote ends the run

`_decision` returns `None` for anything it does not recognise, and every caller
sends `None` to `END`. A run that stops is a run somebody looks at; one that
guessed a destination would carry on with a decision nobody made.

### 4. The graph owns the shape and the epics own the work

The seven compose entry points want eleven different kinds of argument between
them. Threading those through this module would make it the one place every epic's
signature is repeated. `Wiring` is the seam — and it is what lets the whole graph be
compiled and **executed** in a unit test, so a routing mistake shows up as a wrong
path rather than as an expensive one.

### 5. Nothing found is an answer, not a dead end

`00-BRIEF.md` §9 makes *screened nine workloads, nothing found* shippable output.
The edge after screening goes to `END`. An orchestrator that treated an empty
screen as an error would turn the project's own non-negotiable into a crash.

### 6. Only three edges do not decide, and one of them is F2

`investigate` goes to `audit_finding` and never straight to `repair` —
`08-audit.md` F2 is *nobody audits the diagnosis, only the patch*, and this edge is
what makes the fix unavoidable rather than conventional.

The other three nodes get plain edges. A conditional edge with a single destination
is a decision nobody is making, written as though somebody were.

### 7. Re-screening after a ship reads what the ship node invalidated

`08-audit.md` F14: re-screen only the workloads whose files the patch touched. The
invalidation belongs to the ship node, which is the thing that knows what the patch
touched; the router reads what is left.

**This is only expressible because of an earlier composition check.** Epic 6's
changed `screening` from a sequence to a mapping keyed by workload id for exactly
this reason — a flat sequence of opaque entries cannot be filtered per workload, so
F14's policy had a correct answer with nowhere to go.

## Consequences

**S-6.3 predicted the type error this story made, in writing, before it existed.**
The `node` decorator's docstring says LangGraph's node protocol declares
`__call__(self, state: ...)` with a *named* parameter, so a plain
`Callable[[State], ...]` — positional-only — fails at `add_node`. `Step` was written
as exactly that alias, and mypy failed at exactly that line. It is a `Protocol` now.

The same check then found the second half: with a Pydantic state schema, LangGraph
hands a node the **schema instance**, not a mapping. Both the nodes and the routers
take `CheckpointedState`.

**The declared reachable set was the only sabotage survivor.** `_destinations`
returning `[]` changed no assertion, because nothing looked at the *drawn* graph —
and the drawn graph is the only place an unreachable node is visible. A conditional
edge whose destinations LangGraph must infer draws no edges at all, so a node
nothing can reach looks identical to a correctly wired one, which is precisely the
mistake a diagram is read to catch.

**Sabotage: 22 properties, all caught, zero skipped, after one survived.**

**AC 3 is met for the graph and not for the target repo.** *Compiles and runs end to
end* is demonstrated with recording steps: every node executes, every channel joins
up, and every routed path is walked. Running it against a real repository needs the
seven adapters that bind `Wiring` to the epics, a container, and a database — none
of which this story builds. That binding is the honest next piece of work.

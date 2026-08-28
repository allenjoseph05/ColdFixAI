# 166 — The campaign assembles, and the order is forced

**Status:** accepted
**Date:** 2026-08-28

## Context

*Nothing in `src/` constructs a `Resources`* has been the recorded reason S-17.1
could not run since 2026-08-27. Twenty-three fields, six of them the layer that
reaches a live subject. All six now have producers. This is the function that puts
them together.

## The order is not a choice

`DjangoAdapter.reset_state`'s docstring named this function before it existed:
*"`choose_reset` takes an iterable, so **a campaign** holding those facts appends
its own candidate after these two."* The campaign is the only layer holding the
database URL, the container's name and the seed SQL, so it is the only layer that
can pick a reset — and `bind`, `ground` and `measure` all take a `VerifiedReset`
they cannot produce.

So: verify the database, ask the adapter for its candidates, choose a reset against
the live subject, open the session, build the six. Nothing in that sequence can be
reordered without something taking a value that does not exist yet.

**Constructing a `VerifiedDatabase` is S-2.5's check**, and it happens before the
workbench opens anything. That ordering is the only one that makes the refusal
worth having: a guard firing after a container is running is reporting a rule it
has already broken. Sabotage confirms — moving the construction inside the `with`
fails the test that asserts nothing was opened.

**A context manager, not a function returning a value.** `Resources` holds *"the
live objects no checkpoint can hold"*, and a diagnostic session owns a worktree
S-2.2 destroys. A `campaign_for` that returned a `Resources` would be one whose
caller has no way to know what to close, and the failure is a stranded checkout per
run rather than an error anybody sees.

## What the composer found

Three things, and finding them is the reason to write composers.

**`Metrics` refuses an empty `kinds`.** The first draft passed `{}` and
`Metrics.__post_init__` said why: *no kind declared, so there is no rule for what a
move in them means — a count is exact and a duration is one sample, and the two
disagree about every small move.* Nothing anywhere filled it, and the two attacks
that reason over numbers both take a `Metrics`.

The fix is `metric_kind` over the cost metric and the adapter's declared hooks, and
**the argument for using it is specifically that these are catalogue names**.
S-8.12 forbids reading a kind off a *spelling*, and it is right — `metric_kind`
defaults to `COUNT` and the thesis ablation reports `seconds.share_removed`, a
share of a duration it would call a count. But nothing here is derived: these are
the metric the campaign was given and the hooks `register_counter` refuses to
invent. The catalogue is the authority on those. A metric produced *by* a primitive
still travels as that primitive's own `kinds`, through `Measured`, and this path
does not touch it.

**`Workbench.open` returned a union its own docstring contradicted.** *"The
argument selects which class is returned, so a caller who has not decided cannot
proceed"* — but the type said `DiagnosticSession | CandidateSession`, so a caller
that asked for a diagnostic session had to narrow something that could not be the
other type, and the narrowing would read as a check rather than as the fact it is.
`measurer_for` takes a `DiagnosticSession` and mypy caught the mismatch.
`@overload` on the `Literal` mode makes the type say what the docstring already
did.

## A third finding: the layering held, and it cost a parameter list

The first draft took a `FrameworkAdapter` and imported `coldfix.adapters.interface`
for it. `test_no_core_module_imports_an_adapter` refuses that outright: *adapters
import the core; the core must never import an adapter.*

**ADR 148 §1 says the campaign is "the only layer allowed to know both" — and files
that widening on S-14.5**, which owns the boundary question. The test is also
coarser than its own rationale: what it warns against is *a framework-specific
problem solved in a framework-agnostic file*, and `FrameworkAdapter` is a Protocol
that names no framework.

Both of those are arguments for widening it. Neither is an argument for widening it
**here**. A discipline test relaxed as a side effect of a story about something else
is how a layering invariant erodes, and this one has an owner. So `campaign_for`
takes what an adapter supplies — `framework`, `reset_candidates`, `capabilities`,
`counters`, `workload` — and the caller unpacks. Five parameters instead of one, and
the invariant intact.

## A note on the test's fake adapter

mypy refused the first version, and that refusal was worth more than the test.
A fake answering only the four operations this assembly happens to call would pass
every test in the file while proving nothing about what `campaign_for` accepts. The
fake now implements the whole `FrameworkAdapter` protocol, annotated so mypy
checks it, with the unreached operations raising rather than returning something
plausible — an empty `Enumeration` is a subject with no routes, which is a result
rather than an absence.

## Consequences

**S-17.1's recorded blocker is gone.** A `Resources` can be constructed, which
means a run can be started — and therefore can overspend. The other blockers on
that story stand: it needs a live subject, a database, an API key, and Allen's
decision. Nothing here changes any of those.

**The assembly is not the graph.** `gated_graph(resources, checkpointer)` compiles
the seven nodes and is S-17.4's; this produces the argument it takes. A run is
still two calls, and neither has one.

# 083 — One log, and the artifact is validated before the summary is taken

**Status:** accepted
**Story:** S-8.4 — append-only experiment log
**Date:** 2026-08-14

## Context

Three acceptance criteria — every experiment appended with hypothesis, primitive,
design, measurement and verdict; never reordered or re-summarized; serialization
stable and cache-friendly.

Two things already existed and the story is mostly about not duplicating them.
S-5.7's `Investigation` has an `append` and no `reorder`. S-5.8's `PrunedLog`
holds records, renders summaries, defers detail behind `read_experiment`, and
states that *the rendered text at call N is a byte prefix of the text at call
N+1*.

**Epic 5's own composition check found "two append-only logs" as a defect**, and
recorded why it was invisible: caching is a prefix match, so a log wrong in
*content* but still append-only reports full cache hits and a rising bill with
nothing failing.

## Decision

### This module owns the artifact and delegates the rendering

`ExperimentLog` wraps `PrunedLog`. There is one log, one rendering, and one way
in. What S-8.4 adds is the **record** — S-5.8 holds `(primitive, target, outcome,
detail)`, which is the cost view, and AC 1 requires hypothesis, design and
measurement as well, because S-8.6 assembles an evidence chain out of these and a
chain built from records missing their measurements is exactly the *conclusion
drawn from reading code* the first non-negotiable exists to prevent.

### The artifact is validated before the summary is taken, and this was a defect

The first version appended to the pruned log, took the index it assigned, and
then built the `Experiment`. **An append-only log cannot retract an entry — that
is what append-only means** — so a record that passed S-5.8's summary rules and
then failed AC 1's left a summary in the *rendered prompt* with no experiment
behind it.

Found by running it: a record with an empty hypothesis produced `artifacts: 0,
pruned records: 1`, and the orphan was in the rendered log. Nothing raised at the
point where the two collections stopped agreeing; the prompt simply showed an
experiment this log could not produce a measurement for.

The order is now build-then-append, and every way of being refused is
parametrised in the tests — the two collections are validated by different rules,
and only some of those fired first.

### Append-only is expressed as an absence

There is no `reorder`, no `summarize`, no `replace`, no `forget`, no `truncate`.
A test asserts the surface by inspection, so the guarantee fails the moment
somebody adds one for a demo. `experiments` hands back a tuple, so a caller
sorting what it was given is not sorting the log.

### "Cache-friendly" is tested as the property the cache needs

The rendered log at N entries is a **byte prefix** of the rendered log at N+1.
That is the only thing S-5.7's prefix cache requires, and it is checkable —
anything weaker would be asserting that the output looks tidy.

Stability of the record itself is canonical JSON, and the digest test runs in a
**fresh interpreter**, because the guarantee a digest actually has is that a
second process computes the same one. S-4.1 recorded a sabotage walking straight
through a digest test that constructed the model twice in one process.

### `Verdict` is defined here

S-8.3 produces verdicts; the log stores them, and a record needs the vocabulary
before the story that produces it exists. The three values are the backlog's own
words. `NARROWED` is the one worth keeping separate: an experiment that neither
confirms nor refutes has usually narrowed, and collapsing that into `REJECTED`
throws away the half of the search space it bought.

## Consequences

**Makes easy.** S-8.1 receives a typed log rather than rendered text. S-8.5 has
somewhere to hang exclusion preconditions. S-8.7's instrument switch is visible
as two consecutive records naming different primitives — no extra bookkeeping.

**Makes hard.** A caller must build a complete record before logging it. That is
the point: the alternative is a log that accepts an incomplete experiment and an
evidence chain that discovers it three stories later.

**Rules out.** A second append-only log. Retracting an entry. Recording an
experiment with no measurement.

**Sabotage-verified on thirteen properties, all caught, no survivors** — including
the ordering defect above, which is now a sabotage in its own right: reinstating
append-then-validate is caught by the parametrised refusal tests.

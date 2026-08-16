# 087 — An exclusion is invalid exactly when a condition it was established under moved

**Status:** accepted
**Story:** S-8.5 — conditional exclusions
**Date:** 2026-08-16

## Context

`08-audit.md` F3 is a *wrong answer* flaw rather than a missing feature:

> "Not the database — queries flat at 7, 7, 7" holds *at the scales tested, with
> the fixtures used, on this platform*. If the fixtures were uniform and the real
> defect is skew-dependent, the exclusion is false — and it sits in the prompt as
> established fact, permanently blocking the correct hypothesis.

Four acceptance criteria — every exclusion records fixture shape, platform,
concurrency and scales tested; a later experiment that changes a condition
surfaces affected exclusions as stale; the agent may re-test a stale one; and a
test proves a uniform-fixture exclusion is reopened when skewed fixtures arrive.

F3 also sketches the record, with both a `conditions` field and an
`invalidated_if` field.

## Decision

### `invalidated_if` is derived, not stored

The sketch's two fields are two statements of one fact: an exclusion is invalid
exactly when a condition it was established under no longer holds. Two fields
that can disagree eventually will, and the one that would drift is the one nobody
reads until it matters — S-7.12's argument for refusing an override flag beside
an override value, and S-8.5's application of it.

So conditions are recorded and staleness is **computed on every read**. A stored
flag would also be wrong for exactly as long as nobody recomputed it.

### Two kinds of condition, because the four the story names are two kinds

*Categorical* — fixture shape, platform. Coverage is membership. Multi-valued is
ordinary rather than hypothetical: S-3.3's `compare_shapes` sweeps all three
distributions in one experiment, so that exclusion genuinely covers three.

*Numeric* — concurrency, scale. Coverage is the **envelope**, min to max. An
exclusion that saw 10, 100 and 1000 covers 500 and does not cover 10 000.

That asymmetry is the model rather than a convenience. A defect invisible at 10,
100 and 1000 but present at 500 would have to be non-monotonic; a defect that
appears past 1000 is an ordinary threshold — a cache that stops fitting, a page
that splits, an index the planner abandons. The risk is at the boundary, not in
the interior. Treating scales as a set instead would reopen every exclusion on
every intermediate point, which is Epic 4's *a caveat attached to everything is
one nobody reads* with the opposite sign.

The kind is a property of the **dimension**, declared once, so one exclusion
cannot decide that scale is categorical while another treats it as a range.

### All four conditions are required

AC 1 says *every exclusion records its preconditions* and lists four. Read
literally that leaves no room for an exclusion recording three, and requiring all
four also removes a tri-state: with an optional dimension, a later experiment
reporting a condition the exclusion never recorded is neither a change nor a
non-change, and S-3.1's whole lesson is that collapsing that third answer into
either of the others produces a specific wrong behaviour.

### An exclusion can only be made from a rejection, and carries its measurement

`Exclusion` takes an `Experiment`. There is no constructor that takes a sentence,
so *no finding without a measurement* comes free — S-8.4 already refuses an
experiment with no measurement, and an exclusion is a finding (`00-BRIEF.md` §9
ships null results as answers).

A confirmed experiment is a finding and a narrowed one is a hypothesis that
survived, so neither excludes anything. Both are refused, because an exclusion
assembled from either would tell the agent a live branch was closed.

### Reopening a *live* exclusion is refused, which AC 3 does not ask for

F3 names one danger and fixing it creates the opposite one. If an exclusion may
be set aside whenever the agent finds it inconvenient, exclusions stop being
exclusions and the loop revisits dead branches — which `02-architecture.md` §2.2
names as what recording them is for.

So `reopen` refuses anything the recorded conditions do not actually reopen, and
the refusal states what was established and under what, so the reader can see
what would have to change. Reopening an exclusion the register never recorded is
refused too: a reopening is a statement about evidence this register holds.

### What this deliberately cannot do

Nothing here decides whether a *new* hypothesis is the same as an excluded one.
That is a semantic judgement with no deterministic check, so a live exclusion
blocks by being rendered into the prompt as settled, and not by any refusal in
this module.

`RESIDUE` states the other bound in words, for the reason S-7.12's
`Anchor.residue` does: four dimensions are modelled, and an experiment that
varied a fifth — a database version, a cache setting, a feature flag — produces
an exclusion that **looks fully conditioned and is not**.

### The rendering is where a live exclusion carries its scope

`00-BRIEF.md` §9's example is *not the database, queries flat across 100× scale*
— the scale is part of the claim, not a footnote to it. So a live exclusion
renders with its conditions, and a stale one renders as reopened **and names
which condition moved**, since an agent told only that something is stale cannot
tell whether it is worth re-testing.

`render()` returns sentences, which closes S-8.1's open note: that story recorded
that it took `exclusions: Sequence[str]` because *S-8.5 owns what an exclusion
is*, and fixing a structure there would have been guessing at this design. The
structure now exists and still hands over sentences.

## Consequences

S-8.8's reseed tool has the API its AC needs — *reseeding invalidates affected
exclusions per S-8.5* is `register.stale(new_conditions)`.

Conditions are canonicalised — deduplicated and ordered — because they render
into a cached prompt, which is S-8.3's finding one module across.

## Sabotage

Twenty-two properties, all caught — after two survived and one sabotage was a
no-op I had written badly.

*A decoy that matched a different part of the same output — the second story
running to hit this.* `test_a_uniform_fixture_exclusion_is_reopened...` asserted
`"fixture shape" in exclusion.describe(skewed)`, and the **settled half of that
same string** already says *under fixture shape uniform* — so a rendering that
named no drifted condition at all passed. Re-anchored on *established at … went
to …*. S-8.3's survivor was the identical shape one module earlier, which makes
it worth stating as a habit rather than an incident: **when asserting that a
rendering mentions X, check that X does not already appear elsewhere in it.**

*A sabotage that was a no-op.* *Fixture shape stops being a condition* was
written as a comment change and tested nothing. Rewritten to skip the dimension
in `drift_from`, where it is caught — and the gap it exposed was real, since no
test had covered all four dimensions individually. `test_a_change_to_any_single_
dimension_is_enough_to_reopen` now does, parametrised, so no dimension can
quietly stop counting.

The two that matter most are a matched pair and were caught from the start:
*nothing is ever stale* restores F3 exactly, and *everything is always stale*
passes AC 2 and AC 4 while making every exclusion worthless. A module can fail
this story in either direction, so every staleness test here has a control
beside it.

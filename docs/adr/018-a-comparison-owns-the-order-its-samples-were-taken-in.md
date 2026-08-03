# 018 — A comparison owns the order its samples were taken in

**Status:** accepted
**Date:** 2026-08-04

## Context

S-1.6 asks for `compare(variant_a, variant_b, n)` that alternates conditions in
randomized order within one session, and never compares against a previously
stored measurement.

The second half is a safety property, and `CLAUDE.md` is explicit about what
that requires: *if you find yourself relying on this file to prevent something
dangerous, that rule needs code instead.* A function that accepts two lists of
durations cannot enforce it — passing yesterday's baseline is an ordinary call
and nothing distinguishes it from a legitimate one.

The cost of getting this wrong is quantified rather than assumed. Laaber et al.
ran 4.5 million microbenchmark data points across three clouds and found naive
comparison against a stored number produces high false-positive rates: reported
changes where neither benchmark nor code changed (`05-research.md` §10.3).

## Decision

**`compare()` takes two callables and runs both itself.** A stored measurement
is a list of numbers, and there is no parameter here a list of numbers fits.
The dangerous call is not discouraged, it is unrepresentable — the same
construction as `execute()` making `timeout` required and keyword-only. A
runtime `TypeError` backs the annotation for callers that are not type-checked,
which is the interesting half: an agent assembling a comparison from an
artifact it read.

**Randomized within each round, not shuffled across the session.** The story
asks for both alternation and randomization, and a single shuffle of `n` A's and
`n` B's gives up the first — it can deal one condition into the first half by
chance, which is the block design interleaving exists to replace. Drawing a
fresh order per pair keeps the conditions balanced across every prefix, so a
monotonically drifting machine drifts under both equally, while leaving no fixed
phase for a periodic disturbance to lock onto.

**The seed is recorded on the result, drawn if not supplied**, along with the
schedule as it actually ran. An experiment that cannot be re-run in its original
order is not reproducible, and the append-only experiment log has to be able to
say what happened rather than what was intended.

**`Sample.index` is the position in the session, not in the variant's own run.**
`run_a` therefore carries indices like 0, 3, 4, 7. Plotting duration against it
is how drift becomes visible, and drift is the thing being cancelled; renumbering
each run 0..n-1 would discard exactly the ordering this operation is about.

**No verdict field.** The result carries both distributions and the rank test.
Which of `p_value` and `effect` matters, and at what threshold, belongs to
whoever reads it — an `improved: bool` would make that call for them with none
of the context. This follows the Epic 1 rule that instruments decide nothing.

**`n` has a floor of `MINIMUM_GROUP_SIZE`, checked before anything runs.** The
rank test refuses smaller groups, and discovering that after taking every sample
would waste the session.

## Consequences

**Makes easy.** A caller cannot accidentally construct the comparison that
produces false positives. A session is reproducible from one integer.

**Makes hard.** Comparing a variant against a measurement taken in an earlier
session — deliberately, with the caveats understood — has no path through this
function. That is intended, and a caller who genuinely wants it can call
`rank_test` directly and own the claim.

**Rules out.** A cached baseline as an optimization. If a comparison is wanted,
both sides are measured again, in the same session. This is the one place the
project accepts paying twice on purpose.

## Provenance

The decision to randomize within rounds rather than across the session is not in
the backlog note, which says only "alternates between conditions in randomized
order". Both readings satisfy that sentence and they differ in what they protect
against, so the choice is recorded here.

The pair of tests is the evidence. `test_interleaving_cancels_a_drifting_machine`
compares a function against *itself* on a machine whose cost grows with every
call, across twelve seeds, and requires every one to find no difference.
`test_a_block_design_manufactures_a_difference_on_the_same_work` runs the same
workload in the ordering this module refuses to use and asserts p < 0.001 — the
Laaber false positive, reproduced. Sabotage-verified: replacing the schedule
with a block design makes the first test fail at **p = 0.0000 for a function
against itself**. Removing only the shuffle, keeping strict alternation, fails
the randomization test and correctly leaves the drift test passing, since strict
alternation does still cancel monotonic drift.

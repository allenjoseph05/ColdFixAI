# 076 — The shape is a parameter, and the heaviest parent is the number

**Status:** accepted
**Story:** S-7.7 — skewed fixture generation
**Date:** 2026-08-14

## Context

Three acceptance criteria — generate power-law and long-tail distributions across
relationships; make the distribution a parameter of `scale()`; record it in every
measurement taken with those fixtures.

S-3.3 already generates the three shapes, deterministically, with every
distribution returning exactly the same number of children over exactly the same
parents. Nothing here re-implements that. What this story is actually about is
the wiring and the bookkeeping — and the bookkeeping turned out to be the part
with a decision in it.

**`FixtureRecipe.per_parent` is a single `int`.** A skewed fixture has no single
children-per-parent, and that field is not decoration: it feeds `digest()`, which
keys S-5.1's replay cache.

## Decision

### `per_parent` is the heaviest parent, and `parents` is new

S-4.1's field is widened rather than reinterpreted, and the alternative readings
were both worse:

- **The mean** is the one reading that is never the interesting one. The whole
  reason to build a long tail is the request that takes minutes while every other
  request stays fast, and that request is made by the heaviest parent. Recording
  the mean would name the shape in `distribution` and then describe it with the
  number that shape exists to avoid.
- **The nominal divisor** keeps the field constant but makes it mean two
  different things depending on `distribution`, and stops S-4.1's own
  documentation — *the number S-3.3's `Σk²` argument is about* — from being true
  for two of the three shapes.

So `per_parent` is `Allocation.largest`, which under `UNIFORM` is simply children
per parent, and a new optional `parents: int | None` records the parent count.
With `entity`, `per_parent` and `distribution` that makes the fixture
**reproducible**: `allocate` is deterministic, so the same shape over the same
parent count is the same fixture on every machine.

`parents` is optional because recipes predating this story exist and because a
mechanism with no parent population is ordinary. **`None` means *not recorded*,
never *one parent*.**

### The shape applies to the target's own parents, not to the whole chain

A shape applied at every level of a `Publisher → Author → Book` chain compounds
into one nobody asked for, and the cost S-3.3 is about is paid where the request
walks — not three joins up. Levels above the target stay uniform.

### The assignment is computed in the planner, not in the subject

The allocation becomes a per-row list of parent positions that the subject
indexes. The arithmetic is S-3.3's, already tested where it lives, and a subject
that decided its own spread would be a second generator to keep in step with the
first. It also puts the shape in the plan, and therefore in the replay key,
rather than in a decision made at write time.

### A shape that will not fit is refused, never flattened

`allocate` guarantees every parent at least one child, so twenty parents and
twenty children leave nothing to skew with — asking for a long tail at
`per_parent=1` can only produce a uniform fixture. Building it anyway would write
`LONG_TAIL` into the one field that exists to stop a fixture being described as a
shape it does not have. The refusal names the knob: raise `per_parent` to make
fewer, heavier parents, or ask for more rows.

### The blindness note says the opposite thing under a skew

S-7.6 emits a sentence about what a fixture cannot show. Under a skewed shape
that sentence inverts: a flat result under a long tail is a **much stronger
exclusion** than a flat result under uniform data, because the cost was given
somewhere to hide and did not hide there. Keeping one sentence for both would
make the note boilerplate.

## Consequences

**Makes easy.** `07-use-cases.md` §5's admission — *synthetic data has uniform
shape, and uniform shape hides exactly the problems worth finding* — stops being
true of this tool. S-3.3's `compare_shapes` gains a fixture generator that can
feed it, and S-7.8 can verify work under the shape that actually stresses a
per-parent cost.

**Makes hard.** Two fixtures of the same size and different shape now have
different digests, which is correct and means a replay cache warmed under uniform
data has nothing for a long-tail run. That is the point: if the digests agreed, a
cached uniform measurement would be replayed for a skewed one and the cache would
lie faster than the experiment could correct it.

**Rules out.** Describing a skewed fixture by its mean. Naming a shape the data
does not have.

**Sabotage-verified on fourteen properties, all caught.** No survivors — but two
patterns did not apply on the first run and were rewritten before they meant
anything, which is the failure ADR 072 recorded: *a sabotage that changes nothing
is not evidence of anything.* One genuine gap surfaced that way: the parent count
recorded by S-7.5's discovered recipe had no test at all, because the pass ran
only the two files this story touched most.

**One unrelated flake seen and confirmed as one.**
`tests/primitives/test_perturbation.py::test_in_serial_code_the_sensitivity_is_just_the_share_of_runtime`
failed once in a full fast-subset run and passed alone, passed with this story's
changes stashed, and passed on a second full run. S-3.14 measures timing
sensitivity and CPU contention distorts it — the same class as the recorded
`test_isolation.py` flake. Not investigated further; recorded so a single failure
of it is not read as a regression.

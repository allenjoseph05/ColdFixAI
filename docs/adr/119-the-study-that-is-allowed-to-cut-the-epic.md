# 119 — The study that is allowed to cut the epic

**Status:** accepted
**Story:** S-11.8 — Adversary ablation study
**Date:** 2026-08-19

## Context

`00-BRIEF.md` §5 calls Step 11 the contribution of the whole project, and in the
same breath says: **if the delta is small, cut it — it would be theatre.**

This is the study that decides that, written by the same process that built the
thing being judged.

## Decisions

### 1. `CUT` is a first-class return of the same function

There is no separate "negative" path anybody could forget to call. `recommendation`
returns `KEEP`, `CUT` or `NOT_ESTABLISHED`, and the negative outcomes have more
tests than the positive one.

### 2. Blocking everything is a perfect catch rate and is worthless

**The measurement this study exists to get right.** AC 2 asks for *bad patches
reaching a human*, and an Adversary that objects to every patch scores perfectly on
it while being a wall rather than an audit.

So the sound patches are counted too, and an arm whose over-blocking rate is at
least its catch rate is `CUT` however many bad patches it stopped. The AC's number
alone cannot tell an audit from a wall.

### 3. An edge the corpus cannot establish is not an edge

The lower bound of the catch rate must clear the over-blocking **rate**, not merely
the point estimate. Twelve of twenty caught against ten of twenty blocked leads by
ten points and has an interval from 39% to 78% — a corpus that could as easily have
produced the opposite ordering has shown nothing. The same rates over two hundred
cases per label do establish it.

### 4. The counterfactual is structural, not measured

Without the Adversary, every patch that satisfied the Surgeon's own gate reaches a
human unflagged — nothing else is in the way. So the *without* arm is the count of
bad cases by construction, and running the pipeline a second time with the
Adversary disabled would buy a number already known at the cost of the corpus.

`suspicious` is **not** counted as reaching a human unflagged. It reaches one — §4.4
says so — but with the concern stated, and counting it as unflagged would score the
escalation as a failure to catch anything.

### 5. Ground truth comes from outside

`Label` is what is *actually* true of a patch. A corpus whose labels came from the
Adversary would be the Adversary marking its own work and every number here would
be a tautology.

A bad case must name the attack class that should catch it, because AC 4's question
— does the mid tier miss a class — cannot be asked of a corpus that only counts.

### 6. AC 4's answer is coverage, not counts

Two arms can stop the same number of bad patches while one is blind to an entire
class. `missed_classes` reads that off directly, and it is the output that would
change what gets routed where.

### 7. Wilson, in stdlib arithmetic

The normal approximation is wrong exactly where this study lives: at rates near 0
or 1 and at small n it puts bounds outside the unit interval, and it claims
certainty from twenty clean runs. ADR 015's stdlib-only rule holds — the formula is
four lines of `math`.

## Consequences

**Two branches of `recommendation` were unreachable, and the sabotage pass is what
found it.**

- *the interval reaches zero* → a Wilson lower bound is zero only when nothing was
  caught, and nothing caught is a catch rate of zero, which the over-blocking test
  has already returned `CUT` for. No input could reach the branch. It was replaced
  by the meaningful version in decision 3 rather than deleted, because the intent
  behind it was right and the expression of it was not;
- *a rate came back `None`* → both empty denominators are already `underpowered`.
  The guard existed only to narrow `Optional` for the type checker, so the counts
  are now divided directly and the branch is gone.

Writing a dead branch to express a real intention is a specific failure worth
naming: the rule *a catch rate indistinguishable from zero* is a good rule, and
tested against the wrong quantity it became decoration.

**Two survivors were overdetermined fixtures**, the pattern this epic has ended on
five times: the small-corpus test was thin on *both* labels, so removing either
clause of `underpowered` left the other firing; and the interval tests asserted
only shape properties that the normal approximation also satisfies. The fix was a
corpus thin on one side only, and pinning `wilson(8, 10)` to the published
(0.4902, 0.9433).

**`tests/eval/test_ablation.py` collided with `tests/primitives/test_ablation.py`**
under mypy — same basename, no `__init__.py` in the tree. `pyproject.toml` already
carries a note about this exact class of failure for the spike checkouts. Renamed
to `test_adversary_ablation.py`.

**Sabotage: 45 properties, all caught, zero skipped, after four survived.**

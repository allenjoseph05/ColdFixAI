# 144 — What the primitive knew travels with what it measured

**Status:** accepted
**Date:** 2026-08-25

## Context

`Executor` returned `Mapping[str, float]`. Epic 3's results carry more than
numbers — `scale_volume` produces a `kinds` mapping saying what each metric is
made of, and a `Fit` per metric — and all of it was discarded at the loop
boundary. An `Experiment` could hold neither.

Epic 9's composition check recorded this in 2026-08-17 and it never had a story.
Three of the six attacks needed inputs the log could not carry, so
`audit/compose.py` took them as arguments instead: `fits`, `kinds` and `rerun`.
A caller that had to supply them was a caller that could forget to, and
forgetting read as `NOT_RUN` — an attack that did not run, indistinguishable at a
glance from one that passed.

## Decisions

### 1. One result type at the boundary, not a wider mapping

`Measured` carries the measurement, the kinds and the fit. `Executor` returns it.

A union of `Mapping[str, float] | Measured` would have avoided touching
seventeen call sites and would have left two ways to say the same thing — and,
worse, made *a primitive that fitted nothing* and *a caller that did not bother*
the same value. With one type, absence is a statement: an ablation sets `fit` to
`None` because it drew no curve, and S-9.2 already refuses to judge a curve
nobody drew.

`kinds` is checked against the measurement in `__post_init__`. A kind describes a
number, and one describing a number the experiment did not take is a claim about
a measurement that does not exist.

### 2. The loop carries; it still measures nothing

AC 4 is the one worth stating as an absence. `diagnosis/loop.py` contains no call
to `fit_growth` and none to `metric_kind`, asserted by inspection — a loop that
could produce a fit would be the one place `CLAUDE.md`'s rule about measurement
is unenforceable.

### 3. Deriving kinds from the metric name stays forbidden

`metric_kind` is a pure function of spelling whose default is `COUNT`. The thesis
ablation reports `seconds.share_removed` — a **share of a duration** — and it
reads as a count. S-9.6 holds that a count moving at all is material, so a re-run
would diverge every time, every finding would come back `unsound`, and S-9.8
would route every investigation back for more experiments for ever.

So the kind travels from the primitive that produced the number. The thesis
fixture is where this becomes visible rather than theoretical: it now labels
`seconds.share_removed` with the kind of the metric it is a share *of*, taken
from `result.kinds["seconds"]`.

### 4. The log is the source, so `fits` and `kinds` stop being parameters

`audit_finding` reads both off the log through `fits_from` and `kinds_from`.
Keeping them as optional overrides would have left two answers to *what is the
kind of this metric*, and the two would disagree the first time one moved.

`rerun` stays a parameter, and the asymmetry is the point: re-running an
experiment is the harness's and not the record's.

`kinds_from` merges across the log with the latest winning. A metric measured by
two primitives is the same quantity — `db.query` is a count wherever it came from
— so a disagreement would be a defect in a primitive rather than something to
reconcile in an audit, and taking the most recent keeps this a fold rather than a
judgement.

### 5. A checkpoint carries them or a resumed run audits weaker

`_stored` keeps them (it excludes only `detail`) and `_log_of` replays them. Both
halves are needed: a resumed run that dropped them would report `NOT_RUN` where
the original reported a verdict.

A `Fit` is a stdlib frozen dataclass held in a pydantic model and round-trips
through JSON unchanged, so AC 3 needed no change to Epic 1. A stored experiment
stays under S-6.3's ~1 KiB budget — eight numbers and one entry per metric is not
the megabytes-per-node write F13 exists to prevent, and the budget is what says
so rather than the intuition.

**The evidence chain's golden file changed** and was regenerated deliberately, as
its own test asks. The diff is four lines: `"fit": null` and `"kinds": {}` on each
embedded experiment. The chain is the Surgeon's input, the Adversary's input and
the pull-request body, so the shape moving is worth recording here.

## Consequences

- **Three attacks that were silently absent now run.** The most visible effect is
  in the project's own showcase: `test_the_thesis_diagnosis_does_not_survive_its_own_audit`
  gained a third objector without changing its verdict, because the scale attack
  had been answering `NOT_RUN` and the thesis sweep is deliberately narrow.
- **An audit can now reach `sound` on what the log alone carries.** The
  well-swept control passes with no `fits` or `kinds` supplied by hand.
- `INCONCLUSIVE` is still reachable, and its test now withholds the one input the
  log genuinely cannot carry — the re-run — rather than two the log now holds.
- Two tests that asserted the gap were inverted. Their previous docstrings were
  the specification for this story, which is the third time a test written to
  record a gap has been the thing that closed it.
- **Sabotage: 3 properties, all caught** — the loop dropping them into the log
  fails five tests, the audit reading empty mappings instead of the log fails
  four, and a checkpoint dropping them fails the round-trip.

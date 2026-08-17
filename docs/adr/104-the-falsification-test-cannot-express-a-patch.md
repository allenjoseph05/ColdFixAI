# 104 — The falsification test cannot express a patch

**Status:** accepted
**Story:** S-10.1 — falsification test generation
**Date:** 2026-08-17

## Context

`03-agents.md` §5.3 states the Surgeon's mandatory ordering — write the test, run
it against unpatched code, require it to fail, and only then write the patch —
and §5.4 gives both artifacts their schemas. S-10.1's first acceptance criterion
is the first line of that ordering: **the first output is a test, not a patch.**

## Decisions

### 1. AC 1 is enforced by absence, not by ordering

*First* is a claim about sequence, and a sequence enforced by convention is one
an agent can reorder. `FalsificationTest` has no `diff`, no `patch`, no `files`
and no `approach`; `generate` has no parameter through which any of them could
arrive. A type that cannot express a patch cannot emit one.

This is the construction S-8.1 used for `validate` and S-9.1 for `chain`, and it
is the only form of this rule that holds when the model is doing something
unexpected.

### 2. §5.4's `failed_on_unpatched: bool` is not a field, and that is a correction

The schema in `03-agents.md` has the model returning the outcome of a run it did
not perform. `CLAUDE.md` forbids that in as many words — *do not let an agent
report a measurement; agents reason about measurements the harness took* — and
S-4.1 already closed the identical hole for `work_verified`.

Whether the test fails on unpatched code is the single fact that makes it worth
anything, and it is the harness's to observe. S-10.2 owns both the run and the
gate. `describe()` says the test has not been run, because a reader handed a
falsification test could reasonably assume somebody had run it.

### 3. A cost claim with no guard counter is refused

`CLAUDE.md`: *guard counters on every metric — queries down while rows explode is
not an improvement.* This is the first artifact in the system where that
invariant becomes checkable **before anything runs**, because it is the first
that states what *would* count as improvement. A falsification test naming only
the metric it wants to move is a test a cheat passes by moving it.

Two failure modes are refused in opposite directions: a guard pointed at the cost
metric guards nothing, and a guard demanding an improvement fails the patch for
succeeding somewhere nobody claimed. `at_most` above the baseline is how a
bounded, accepted regression is stated in the open.

A cost threshold at or above its own baseline is refused for §5.3's reason: any
unchanged run satisfies it, which is *a test that passes before you change
anything* written down.

### 4. The baselines are checked against the evidence chain

The judgement of what to assert is the model's; the figures under it are not. A
threshold quoted from a number nobody measured is the first non-negotiable broken
at the top of the repair phase — the discipline S-8.3 applies to a verdict and
S-9.5 to an alternative.

**The chain's exclusions count as measurements, not only its confirmations.** A
cost baseline legitimately comes from the sweep that ruled the database out as
well as from the ablation that found the cause; a checker reading only the
confirmations would call `db.query = 7` a fabrication. `measured_pairs` was
widened from taking an `ExperimentLog` to taking a `Sequence[Experiment]` so that
S-9.5 and this ask one question of two artifacts — the alternative was a second
copy differing only in how it reached the records.

### 5. The cheat classes are an enum, not §5.4's `list[str]`

`02-architecture.md` §210, `03-agents.md` §412 and S-11.3's acceptance criteria
list the same five — cached state, deferred work, over-fetch, stubbed response,
shape-specific special-casing — so `Cheat` is a transcription rather than a
judgement.

AC 3 says the test *enumerates* the classes it catches, and a free string cannot
be enumerated: S-11.3 has to ask *could a cheat of class X pass this test* and
needs the same vocabulary to ask in. A name nobody defined is **refused rather
than dropped**, because silently discarding it leaves a shorter list reading as a
complete one.

### 6. Unlike hypothesis generation, this step may cascade

`04-cost.md` §3 gives `FALSIFICATION_TEST` a real check — *fails on unpatched
code* — so a cheap model's answer can be falsified deterministically. S-8.1's
`generate` has **no** `validate` parameter and asserts its absence by inspection;
this one has it, and the asymmetry is the table's rather than a preference.

**This module cannot perform that check.** It has no runner, no worktree and no
way to execute a script, so the validator comes from S-10.2 or from nowhere. The
cascade path is exercised here rather than deferred, because a parameter with no
tested path is the dead code this project deletes.

## Consequences

**One real defect, found by a test written for the criterion rather than for the
field.** `Field(min_length=1)` is satisfied by a single space, so an
`equivalence` of `"  "` passed every schema check while asserting nothing — and
the same hole was open on `claim` and `script`, where a blank script would have
reached S-10.2's runner. AC 2 requires the test to *assert* correctness
preservation, and a space is not an assertion.

**Sabotage: 24 properties, all caught, zero skipped, after two survived.** Both
survivors were untested properties rather than weak code:

- **the step type was interchangeable with `PATCH`** and nothing failed, because
  both are mechanical and both route to the same tier today. The only visible
  difference is *which* documented check a cascade validates against — *fails on
  unpatched code* against *test suite passes* — so it is now asserted through the
  call rather than about the table;
- **a truncated reply was accepted.** A truncated *script* is one whose assertions
  may be missing, and it would reach S-10.2's runner looking complete — a test
  that passes because the part which would have failed was cut off at the token
  limit.

**The substring-over-source trap, walked into for the third time.** An isolation
test asserted `"worktree" not in source.lower()` against a docstring that uses
the word to explain why there is no worktree. S-7.11 recorded it, S-9.3 recorded
it again with the same remedy, and this session repeated it: the check now asserts
what the module imports and what its functions take.

## What this does not decide

**Where the metastability gate sits.** `00-BRIEF.md` §4 requires it *before the
Surgeon can emit its first patch*, and `10-BACKLOG.md` places it at S-10.6,
after S-10.4 generates one. Nothing in this story emits a patch, so the question
does not arise yet — but it has to be settled before S-10.4, and the resolution
is not obviously the backlog's ordering.

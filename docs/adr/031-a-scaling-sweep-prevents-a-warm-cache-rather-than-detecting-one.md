# 031 — A scaling sweep prevents a warm cache rather than detecting one, and always measures N=0

**Status:** accepted
**Date:** 2026-08-06

## Context

S-3.2 asks for a sweep that runs a workload at three or more volumes with a reset
between each, fits every recorded metric against volume, subtracts the framework
baseline at N=0, forces lazy results to materialize, and clears caches between
points. Its note says the last three each silently produce a wrong answer.

*Silently* is the operative word, and it is worth being precise about what the
wrong answer is, because it is the same one in all three cases. Each of them
**flattens a metric that really grows**. A flat metric is not a crash or an empty
result — it is *queries flat at 7, 7, 7 across 100× scale*, which `00-BRIEF.md`
§9 ships as a finding, which `02-architecture.md` requires be recorded as an
exclusion, and which a human is expected to act on by looking somewhere else.
This is ADR 013's failure mode arriving through three new doors: a measurement
that never happened, wearing the shape of a measurement that came back empty.

The three doors:

- **The framework baseline.** A fixed per-request cost — sessions, permissions,
  middleware — that has nothing to do with data volume. S-0.3 measured about 35
  such queries on netbox's interface endpoint, which is what a mature system
  actually looks like. Against one query per row at volumes 1, 2, 3 that reads
  36, 37, 38, 39.
- **Lazy evaluation.** A queryset, generator or streaming response has done
  nothing when it is returned. Counters read zero and the clock stops early.
- **A warm cache.** The second volume reads what the first one warmed, so cost
  per item falls as volume rises.

## Decision

**The baseline is measured at N=0 on every sweep and subtracted from every
point, and it is not optional.** The reason it is easy to skip is that
subtracting a constant does not change the slope of a straight line, so the
"cost per item" number looks right either way. It changes the **exponent**, and
the exponent is what the growth classification rests on: 36, 37, 38, 39 has a
power-law exponent of 0.05 and classifies `CONSTANT`; the same measurements with
the baseline removed are 1, 2, 3 with an exponent of 1.0 and classify `LINEAR`.
One of those is *not the database* and the other is an N+1. A workload that
cannot run at N=0 raises `BaselineError` rather than skipping the subtraction — a
sweep without a baseline is not a weaker sweep, it is one whose exponents are
wrong by an unknown amount in a known direction.

**A negative adjusted metric is left negative.** It means the baseline is not a
constant offset for that metric, and `fit_growth` already handles that honestly
by declining the power fit and leaving `growth` unset (ADR of S-1.5's behaviour,
recorded in `stats.py`). Clamping to zero would replace "the model does not fit
here" with a number that looks measured.

**The measured window closes only after the result has been drained**, and how
many items that took is recorded as a metric of its own. A lazy result that
yields nothing at every volume and a workload that legitimately returns nothing
are the same query count and different findings; recording the count is what
separates them. Draining is one level deep — a mapping's values, because a view's
context is the shape this meets most often — and that limit is stated rather than
papered over. Strings and bytes are not item sources: iterating one yields
characters, which is expense without information and a materialized count that
overstates the work.

**The warm cache is prevented, not detected.** ADR 026 established that it cannot
be detected after the fact:

> A workload with a stale cache returns the same value every cycle. A workload
> with no cache also returns the same value every cycle, because a correct reset
> makes every cycle identical. The two are indistinguishable by output.

So a sweep requires one of two guarantees before it will run: a process identity
that **differs** at every scale point — the same construction ADR 026 used, since
a process that does not outlive a point cannot carry rows into the next one — or
an explicit clear the caller performs. **A sweep given neither refuses to
start.** ADR 026 deliberately left the equivalent hole open, because verification
concerns a reset a caller may reasonably be unable to observe; this is a
measurement, and an unqualifiable measurement is worth less than none.

**Which guarantee was held is recorded on the result**, because `CLAUDE.md`
requires exclusions to carry their preconditions. *Queries flat across 100×
scale* means one thing when every point ran in its own container and something
much weaker when a caller's own hook was trusted to empty the caches it knew
about.

**Seeding happens inside the reset cycle, and the reset is a `VerifiedReset`
rather than a callable.** Seeding inside the cycle is what makes "reset between
each" structural: the data for one volume is undone by the same mechanism that
undoes the workload, so it cannot reach the next point. Taking S-2.7's verified
type means a sweep cannot run on a reset nobody proved works — the sixth use of
that construction, after `VerifiedDatabase`, the session types, `VerifiedReset`
itself, `ScreenedRepository` and `RepairableFinding`.

**Every metric carries a kind, and a duration is one sample.** Counts are exact
and reproduce to the integer; the `seconds` column is a single observation
against a noise floor S-0.4 measured at roughly 20 ms, about 6% of a 350 ms
endpoint. Interleaved statistical timing is S-1.6's job and instruction counting
is S-3.19's. Labelling the column is what stops a reader taking a 2% difference
off the one number here that cannot carry it.

**The parameters stay flat.** Five of the eight describe the subject, and
grouping them would be defining the workload artifact S-4.1 owns — the argument
S-2.9 recorded for taking two strings instead of a finding object.

## Consequences

**Makes easy.** Screening: this is the primitive S-4.2 drives across every
workload, and it needs no model call. Adding a metric — every recorded metric is
fitted, so a new guard counter appears in the fits without touching the fitting
code.

**Makes hard.** Running a sweep against a subject that can report neither a
process identity nor a cache clear. That is intended: the alternative is numbers
nobody can qualify. Also, a workload whose laziness is nested more than one level
deep must force it itself, and the materialized count is the evidence of whether
anything was found.

**Rules out.** Trusting a reset because it exists, reporting growth without a
baseline, and treating "the results were identical every cycle" as evidence
about caches.

## Provenance

Six sabotage runs, each asserting the edit was detected: dropping the baseline
subtraction fails 1 test — the one built on S-0.3's netbox floor, and only that
one, because a defect without a floor still fits as linear either way; dropping
materialization fails 3; removing the cache-control requirement fails 1;
disabling the process-identity check fails 1; fitting only the named counters
fails 2; draining strings as item sources fails 1.

**A seventh sabotage passed, and the fault was in the test double.** Moving
`seed()` outside the reset cycle changed nothing, because `RecordingReset`
emptied the subject unconditionally and so cleaned up after the very leak the
test existed to catch. A real rollback restores the state as of `begin()` and
undoes nothing written before it. The double now snapshots at `begin()` and
restores at `reset()`, and the sabotage fails with `[0, 0, 1, 2]` — data from
each volume arriving at the next. The lesson generalizes past this story: **a
test double that is more forgiving than the real thing turns a structural
assertion into a decoration**, and only sabotage finds it, because the test
passes either way.

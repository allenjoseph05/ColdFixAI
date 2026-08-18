# 103 — Epic 9 could not audit anything the system produces

**Status:** accepted
**Story:** Epic 9 composition check
**Date:** 2026-08-17

## Context

Nine stories, six attacks, a verdict vocabulary and a routing rule. Every story
passed its own tests and its own sabotage pass. After all of them, **nothing
could take an investigation and audit it.**

Each attack was reachable from inputs a test built by hand; not one was reachable
from what the system actually produces. Epic 7 recorded that shape, Epic 8
repeated it, and this is the third consecutive epic to end the same way:
*the criterion is met* and *the criterion is reachable* are different claims, and
only a composition tests the second.

## The defects

### 1. There was no path

Six attacks with six different input shapes, and nothing that assembled them,
counted the audit's round, or checked its call ceiling. `src/coldfix/audit/
compose.py` is that path — the same role `emit.py` played for Epic 8.

Two enforcement functions had **no caller at all** until it existed:

- `refuse_shared_session` (S-9.1) — the isolation the whole epic rests on, and it
  only fires if somebody calls it;
- `authorize_round`/`record_round` (S-9.8) — which is how a cap compiled at S-5.4
  stayed decorative through nine stories.

### 2. The log cannot say which metrics are counts

`diagnosis/loop.py` types its `Executor` as
`Callable[[ExperimentSpec], Mapping[str, float]]`, so everything the primitive
knew *about* those numbers is discarded at the loop boundary. Every Epic 3 result
carries a `kinds` mapping; an `Experiment` carries none. S-9.6 requires one — its
whole argument is that a count and a duration do not diverge alike.

**The obvious repair is worse than the gap.** `metric_kind` is a pure function of
spelling whose default is `COUNT`. The thesis ablation reports
`seconds.share_removed` — a *share of a duration* — and it classifies as a count.
S-9.6 calls any count that moved material, so a re-run would report divergence
every time, every finding would be `unsound`, and the amended S-9.8 would route
every investigation back for more experiments for ever. That is precisely the
failure S-9.6's control test exists to prevent, reached through the join instead
of through the module.

So `kinds` is **supplied**, and its absence produces `Outcome.NOT_RUN` rather
than a guess. This is the construction S-9.2 already chose for a missing fit —
*inventing a fit to judge would be auditing a curve nobody drew* — and the
composition is the first real caller of the outcome S-9.8 added so that an attack
which did not run could not read as one that passed.

### 3. A growth fit does not survive into the log either

`measurement` is `Mapping[str, float]` and a `Fit` is not a float, so S-9.4 and
S-9.2's scale axis had no reachable input. Same treatment, same reason.

### 4. The conditions must be taken, not derived

Found while wiring the third. The obvious implementation calls
`emit.conditions_for(workload)` — the producer Epic 8's composition added for
exactly this shape. It is wrong here: a `FixtureRecipe` holds **one**
distribution, the one the run started with, and S-8.8 moves the conditions on the
`Investigation` when it reseeds. An audit rebuilding them from the recipe reports
a single fixture shape after a reseed swept two, so S-9.2 and S-9.3 object to a
narrowness that was already fixed — **and the remedy they name is the reseed that
just happened.** F3's shape once more: a condition read from the wrong place, and
the reader cannot tell.

## Two honest results, asserted rather than worked around

**The thesis diagnosis does not survive its own audit.** The finding is real —
stubbing the renderer removes essentially all the wall time — but its exclusion
*not the database* was established under a uniform fixture driven serially, and
S-9.2 proves uniform is the blindest shape there is. The epic's own showcase run
audits `unsound` and routes back to investigate. The audit objecting to the run
the project uses as its demo is the first evidence that it objects to anything
real.

**The thesis sweep is too narrow to support a growth claim.** 10/20/40 is a 4x
span across three points; S-9.4 requires 11x at the 12% drift S-0.4 measured. The
moment a fit is supplied, scale adequacy objects — and so does exclusion
validity, because S-9.2 delegates the scale axis to S-9.4 rather than asking the
same question twice. That delegation showing up through the composed path is the
design working, not a duplicate.

Neither is a defect. The thesis run exists to demonstrate an instrument *switch*
and never needed to separate linear from superlinear.

## One line deleted

`audit_finding` had an `authorize_round` and removing it changed no outcome: the
path's first attack is a model call, `Session.run` authorizes against the same
cap before any spend, and a second check could refuse nothing the first would
not. S-7.4's redundant condition, which S-8.9 collapsed in the investigate loop
on the same evidence — so it is deleted here too.

`audit_partial` **keeps** it, and the asymmetry is the point: that path makes no
model call at all, so without the check the two-round cap would be decorative
again on the one path with nothing else watching it.

## Recorded and not fixed

Two defects need their own stories and are noted so nobody rediscovers them or
quotes the affected numbers as achieved.

1. **The loop discards `kinds` and `Fit`** (defects 2 and 3 above). The composed
   path works around it by taking them from the caller; the real repair is that
   `Executor` should carry what the primitive measured *about* its numbers. That
   touches S-8.2, S-8.7, S-8.9, the `Experiment` schema and every caller.
2. **Carried from the Epic 8 composition and still true:** `Session.run` assembles
   S-5.7's blocks and the request never carries them, so `cache_control`
   breakpoints are never sent and Epic 5's prompt-caching design is inert. **Do
   not quote §12.3's cost as achieved.**

## Consequences

**Sabotage: 20 properties, all caught, zero skipped, zero harness faults** — after
four survived and two were skipped on the first pass. Three of the four survivors
were real test gaps and one was a redundant line, now deleted:

- the mid-audit ceiling check could not be told from the final one, because the
  test handed the client both recordings; it now gets **only the first**, so a
  check that lands too late spends the call the ceiling exists to prevent;
- `key_experiment` preferring a confirmation could not be tested against the
  thesis log, where the last confirmation and the last settled experiment are the
  same record — **the tenth fixture in this project that could not tell the right
  answer from the wrong one**;
- the partial-chain path's authorization had no test at all.

Both skips were sabotage patterns matching two call sites, surfaced by the skip
count S-9.7 added to the runner.

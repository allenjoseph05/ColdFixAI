# 150 — The reason a metric was not flagged belongs to the flagger

**Status:** accepted
**Date:** 2026-08-27

## Context

S-16.3 asks for a structured null-result report naming the workloads screened,
the thresholds applied, and **why nothing was flagged**, distinguishing *healthy*
from *insufficient data to tell*.

Most of it already existed. S-4.5 built `screening/null.py` around exactly the
distinction the second criterion asks for: `healthy`, `unverified` (with the
`touched_no_data` case called out separately), and `unclassified` for growth that
could not be fitted, each with its own sentence and a test asserting the healthy
sentence is absent from the other two. The conditions and thresholds travel on
the artifact. That criterion was met before this story started.

The first criterion was not, in one clause. The artifact recorded *that* nothing
was flagged and *against what thresholds* — and not what any metric actually did.
A `Flag` carries the observed growth, the expected growth and the magnitude, and
explains itself. A healthy workload carried its name. So a reader asking *why was
`/tickets` not flagged* got a claim, where the flagged case gets a measurement —
in a project whose own rule is that an exclusion carries its preconditions, and
whose own docstring calls a null result *the largest exclusion this system
produces*.

## Decisions

### 1. The evidence goes on the artifact, not into the prose

`Unflagged` records, per covered metric: what it did, what it may do, the ratio
across the sweep, and its level at the largest scale point. The level is there
because *within expectations* is two conditions for a flat metric and not one —
it has to fit its expectation **and** sit under the flat-cost threshold — so
reporting only the shape explains half of why a flat metric at 7 was left alone
and none of why a flat metric at 119 was.

It is built only for workloads in `healthy`. Publishing a growth basis for a
workload nothing showed does real work would be the collapse the module is
arranged to prevent: the measurements of an empty endpoint and a fast one are the
same numbers.

### 2. The reason comes from `flagging`, because inferring it produced a lie

The first draft derived the reason from the shapes: *observed equals expected* →
"where X is expected", otherwise → "within the X it may be". Rendered against a
real screen it said:

> `seconds superlinear, within the linear it may be`

which is **false**. That metric grew 9.6x beyond its expectation and raised no
flag for a completely different reason: it is a duration below S-0.4's noise
floor, where a fit over one sample per scale point is a fit to noise. The
sentence was not merely imprecise — it reported an unresolvable measurement as a
metric that behaved, which is the direction that reassures.

So `flagging` gained `WithheldReason` and `withheld_reason()`: the negative half
of `flag`'s own decision, consulting the same two predicates rather than
restating them. Two reasons, and they are different kinds of statement — one is
about the code, the other about the instrument.

**`test_flagging.py` asserts the two functions partition every fitted metric**:
exactly one of *flagged* and *withheld with a reason* is true of each. Without
that, a change to `_above_the_noise` moves one and not the other, and the null
result starts explaining flags that were raised or staying silent about metrics
that were not.

### 3. The closing sentence had the same defect one layer out

It read *"none of them grew beyond what its metrics may"* — false on any screen
where a duration was held back by the floor rather than by behaving. It now says
*nothing measured qualified as a finding*, which is what actually happened, and a
test asserts the old wording is gone.

Finding the same error twice at two altitudes is the useful part: a summary
sentence written before the evidence existed will describe the evidence it
imagined.

### 4. The report stays in `screening/`, and no `report/` module was added

Memory said this story belonged beside `report/pullrequest.py`. It does not.
`NullResult.report()` already existed and had one owner; `Ranking.report()` and
`Plan.report()` sit beside it in the same package. Adding a second rendering is
precisely what ADR 145 warns against — *two renderings is how a gate report and a
pull request come to disagree* — and moving one of three artifacts' renderings
out would be inconsistent churn for no behavioural gain. The report grew a
section; it did not grow a second home.

### 5. A dead filter, found by sabotage

The builder filtered candidates against `ranking.unclassified` before asking for
a reason. Deleting that filter changed no test outcome, and the reason is that it
never did anything: every pair in `unclassified` has `growth is None`, and
`withheld_reason` already refuses those. Two guards for one condition is a place
they can come to disagree, so the filter is gone and the comment says why. One
genuinely equivalent mutant, said so rather than pinned with prose.

## Consequences

**Epic 16 is complete** — S-16.1 (already built, marked), S-16.2 (the pull
request), S-16.3 (the null result). Its composition check is due, and on this
project's record — six epics for six — it will find something.

**S-17.1's expected branch now carries its evidence.** The holdout was chosen
because the correct answer on it is *nothing found*; that answer now arrives with
the measurements behind it rather than as an assertion, which is the difference
between a null result somebody can check and one they have to trust.

**Sabotage: 5 properties, 5 caught**, plus the equivalent mutant above — every
unflagged metric reported as having behaved, an unverified workload contributing
a measured basis, an unfittable metric given a reason, the basis never reaching
the report, and the contradicting closing sentence restored.

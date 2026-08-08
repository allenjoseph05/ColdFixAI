# 053 — A screen with no entry point, and four things only a project shows

**Status:** accepted
**Story:** Epic 4 composition check
**Date:** 2026-08-08

## Context

Epic 4 finished with five stories, 93 passing tests and no way to use it. Its
purpose, from the backlog, is *find what is worth investigating using zero model
calls* — and performing that sentence took four calls in the right order plus a
branch that lived in no module.

Composing it found five defects. All are in shipped, individually tested,
sabotage-verified code, and none was reachable from a test of one stage.

## Decision

### 1. `assess` — one call, and the branch lives once

A caller had to run `screen`, `rank`, and then either `plan` or `null_result`
depending on whether the ranking came back empty. That branch was in no module,
so every caller would reimplement it, and **getting it wrong was silent**: asking
for a plan when nothing was flagged returned `investigate=(), deferred=(),
within_budget=True`, which reads as *nothing to investigate and everything fitted
the budget* and is indistinguishable from a healthy plan. S-4.5's null result —
the one that names what was screened, the thresholds applied, and which workloads
the answer does *not* cover — was never produced at all.

`assess` returns `Plan | NullResult`, and the two are now exclusive by
construction: `plan` refuses an empty ranking and `null_result` already refused a
flagged one, so neither can be reached down the wrong path.

### 2. Guard counters belong to the workload, not to the screen

`screen` took one `extra_counters` callable and applied it to every workload in
the project, so a screen of six workloads read one subject's counters six times
and attributed them to the other five. Never exercised, because no test had run
more than one workload with guard counters at once — which is a thing only a
composition does. A guard counter is a fact about a particular subject exactly as
its invocation and its reset are, so it moved onto `BoundWorkload` alongside
them.

### 3. A duration needs both ends above the noise floor

S-4.3 required a duration flag to clear S-0.4's ~20ms floor. `cpu_seconds` comes
from `process_time`, which on Windows moves in steps of about **15.6ms** — the
same granularity that bit S-3.7 and S-3.13. A sub-millisecond workload records
zero ticks at the small scale and two at the large one, so a quantisation
artefact of 31ms cleared a 20ms floor and flagged the batched control, the
fixture's clean counterpart. Requiring the *smaller* measurement to be above the
floor as well is what makes the comparison a comparison: below it the denominator
is rounding.

### 4. A caveat attached to everything is one nobody reads

S-4.5 reports metrics whose growth could not be fitted, so *could not tell* stays
apart from *nothing there*. `blocked_seconds` is elapsed minus CPU, so on any
workload fast enough for the two clocks to agree it is zero or negative and has
no exponent — unfittable on essentially every healthy fixture. Recording it made
**every** null result on a healthy workload say it did not cover everything it
screened.

Only metrics that could have been flagged are recorded now. A duration below the
timing floor at both ends could not have flagged whatever its exponent, so its
missing exponent tells a reader nothing, and saying so anyway trains them to skip
the line that matters.

## Consequences

**Epic 4 catches one of the three planted defects, and that is now a test.** The
N+1 is flagged. The over-fetch is not, and cannot be: every metric on it grows
linearly with volume and so does every metric on its projected control, because
the defect is that the payload is five times wider than the response needs — a
comparison against a floor, which is S-3.18's `fields_required_by` and belongs to
diagnosis. `render_with_expensive_downstream` is not flagged either: its cost is
CPU downstream of a two-query fetch, and a duration cannot flag below the noise
floor, which is why S-3.19 exists. Both are recorded as assertions so that a
later change to screening which does catch them fails and gets read.

**One property could not be sabotaged and the attempt is worth recording.** The
screen produces the same decision twice, which ADR 002 requires of anything
rendered into a cached prefix. Two mutations were tried — shuffling before the
sort, and ordering by `id()` — and both passed, because every input to the sort
is already deterministic and CPython reuses freed addresses. Determinism here is
over-determined, like S-3.19's three-step teardown. The test stays as the written
form of the requirement; it is not a sabotage target.

**The file had to be renamed.** `tests/screening/test_epic_composed.py` collides
with Epic 3's under pytest and mypy, for the same reason `test_end_to_end.py` did
in Epic 3 — the second time this exact trap has been hit, and both times while
writing a composition check.

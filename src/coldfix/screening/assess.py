"""Epic 4 in one call: workloads in, a plan or an honest nothing out.

The epic's goal, from the backlog, is *find what is worth investigating using
zero model calls*. Four stages do that — sweep, flag, rank, cap — and until this
module existed a caller had to run all four by hand and branch on whether the
ranking came back empty.

**That branch was the defect Epic 4's composition check found.** It lived in no
module, so every caller had to reimplement it, and getting it wrong was silent:
asking for a plan when nothing was flagged returned `investigate=(), deferred=(),
within_budget=True`, which reads as *nothing to investigate and everything fitted
the budget* and is indistinguishable from a healthy plan. S-4.5's null result —
the one that names what was screened, the thresholds applied, and which workloads
the answer does not cover — was never produced at all.

So the branch is here, once, and the two outcomes are exclusive by construction:
`plan` now refuses an empty ranking and `null_result` already refused a flagged
one, which means neither can be reached down the wrong path.

Nothing here decides anything a model would. It sweeps, fits, compares against
thresholds and sorts.
"""

from __future__ import annotations

from collections.abc import Sequence

from coldfix.screening.budget import DEFAULT_FINDINGS_CAP, Plan, plan
from coldfix.screening.flagging import rank
from coldfix.screening.growth import SCREENING_SCALES, ScreenedWorkload, screen
from coldfix.screening.null import NullResult, null_result
from coldfix.screening.workload import BoundWorkload

Assessment = Plan | NullResult
"""What a screen concludes: what to investigate, or why there is nothing to.

A union rather than one type with an empty list, because the two carry different
things and call for different next actions. A plan names workloads and a budget;
a null result names thresholds, conditions, and the workloads it does *not*
cover — and that last list has no place to live on a plan.
"""


def assess(
    workloads: Sequence[BoundWorkload],
    *,
    scales: Sequence[int] = SCREENING_SCALES,
    counters: Sequence[str] = (),
    cap: int = DEFAULT_FINDINGS_CAP,
) -> Assessment:
    """Screen a project and decide what, if anything, this run investigates.

    Guard counters come off each binding, so a project of six workloads reads six
    different subjects rather than one of them six times.

    Raises:
        ScreeningError: no workloads, or one that could not be screened. A
            workload silently absent from a screen is indistinguishable from one
            screened and found healthy, so the error travels.
        BudgetError: a cap outside what this system will run in one pass.
        MeasurementError: as `scale_volume` — no cache control, a metric set that
            changed between points, an unregistered counter name.
    """
    screened = screen(workloads, scales=scales, counters=counters)
    return conclude(screened, cap=cap)


def conclude(
    screened: Sequence[ScreenedWorkload], *, cap: int = DEFAULT_FINDINGS_CAP
) -> Assessment:
    """The decision half, for a caller that already has screening results.

    Separate from `assess` because the replay cache E5 builds keys on the
    measurements, not on the conclusion drawn from them: re-deciding a cached
    screen has to be possible without re-running it.
    """
    ranking = rank(screened)
    if ranking.flagged:
        return plan(ranking, cap=cap)
    return null_result(screened, ranking)

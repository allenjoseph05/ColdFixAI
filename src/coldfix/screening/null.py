"""Nothing found, said in a way that distinguishes it from nothing looked at.

Epic 4, S-4.5. `CLAUDE.md` lists this among the project invariants: *null results
are valid output — "screened 9 workloads, nothing found" ships as an answer.
Never manufacture a finding.* So this is a value returned, not an exception
raised, and the whole of its design is about the sentences it is **not** allowed
to produce.

**Three ways of finding nothing, and only one of them is good news.**

| what happened | what may be said |
|---|---|
| screened, shown to do real work, nothing above expectations | nothing found |
| screened, and nothing established that it does real work | **not covered by this result** |
| screened, and it touched no data at all | **the harness measured an empty workload** |

`02-architecture.md` §1.5 names the third as a failure mode with a required
response — *report honestly and stop; never report "no issues found"* — and the
second is F6's whole subject. Collapsing any of them into the first is how a tool
tells somebody their code is fine when it has not looked at their code.

**The thresholds travel with the answer.** `CLAUDE.md` requires exclusions to
carry their preconditions, and a null result is the largest exclusion this system
produces. *Nothing found* means one thing across a sixteenfold sweep of uniform
fixtures with a fresh process per point, and something much weaker otherwise, so
the conditions and every threshold applied are on the artifact rather than in the
prose around it.

**It refuses to be a null result when something was flagged.** Constructing one
from a ranking with findings in it raises, because the failure this guards is a
caller reporting "nothing found" from a screen that found something.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from coldfix.primitives.counters import CATALOGUE, Reading
from coldfix.primitives.measurement import MATERIALIZED
from coldfix.screening.flagging import (
    FLAT_COST_THRESHOLD,
    TIMING_FLOOR_SECONDS,
    Ranking,
    expected_growth,
    withheld_reason,
)
from coldfix.screening.growth import MetricGrowth, ScreenedWorkload
from coldfix.screening.workload import MINIMUM_SCALE_RATIO

_STRICT = ConfigDict(frozen=True, extra="forbid")


class NullResultError(Exception):
    """A null result was asked for where there was something to report."""


class Conditions(BaseModel):
    """What every statement in this result is true *of*.

    Recorded per workload rather than once, because a screen may have swept two
    workloads at different scales and a single set of conditions at the top would
    be true of neither.
    """

    model_config = _STRICT

    workload_id: str
    scales: tuple[int, ...]
    distribution: str
    reset_strategy: str
    cache_control: str


class Unverified(BaseModel):
    """A workload the screen ran and could not show does real work."""

    model_config = _STRICT

    workload_id: str
    reason: str
    """S-4.1's `work_evidence`, which names the next action rather than a code."""

    touched_no_data: bool
    """Whether every amount it produced was zero at the largest scale point.

    The specific case `02-architecture.md` §1.5 requires a specific message for.
    Zero rather than *almost zero*: a workload returning three rows might be a
    stub and might be a correct aggregate, and no measurement here separates
    those — inventing a threshold would put a number where a judgement belongs.
    """


class Unflagged(BaseModel):
    """One metric that was measured, fitted, and found to be within expectations.

    **The evidence for a negative, which S-16.3 added because the artifact had
    the conclusion and not the numbers.** A `Flag` carries what a metric did and
    what it may do; a healthy workload carried its name and nothing else, so a
    reader asking *why was this not flagged* got a claim where the flagged case
    gets a measurement. `CLAUDE.md`'s rule that an exclusion carries its
    preconditions applies most to the largest exclusion this system produces.

    Only ever built for a workload in `healthy`. Publishing a growth basis for a
    workload nothing showed does real work would be the collapse this whole
    module is arranged to prevent — a measurement of an empty endpoint reads
    exactly like a measurement of a fast one.
    """

    model_config = _STRICT

    workload_id: str
    metric: str
    observed: str
    """What the metric did, as `Growth`. Never `None` here — a metric that could
    not be fitted is `unclassified`, which is a different answer."""

    expected: str
    """What this metric may do as data grows without that being a defect."""

    ratio: float | None
    """Largest over smallest, before the framework baseline is subtracted.

    `None` where the smallest measurement was zero, which `MetricGrowth` is
    careful to distinguish from a large number: nothing grew *by a factor* when
    it started at nothing.
    """

    largest: float | None
    """The value at the biggest scale point.

    Carried because *within expectations* is two conditions for a flat metric,
    not one: it has to fit its expectation **and** sit under the flat-cost
    threshold. Reporting only the shape would explain half of why a flat metric
    at 7 was left alone and none of why a flat metric at 119 was.
    """

    reason: str
    """`WithheldReason`, taken from `flagging` rather than inferred here.

    **The first draft inferred it from the shapes and was wrong.** A duration
    that grew 9.6x and was held back by S-0.4's noise floor came out as
    *"superlinear, within the linear it may be"*, which is false and false in the
    direction that reassures a reader. The module that decides what is worth
    flagging is the only one that can say why something was not.
    """

    def describe(self) -> str:
        """The measurement. The reason is grouped by the caller, not repeated."""
        level = f" at {self.largest:g}" if self.largest is not None else ""
        span = f" ({self.ratio:.1f}x)" if self.ratio is not None else ""
        return f"{self.metric} {self.observed}{level}{span}, where {self.expected} is expected"


class NullResult(BaseModel):
    """Screened, and nothing to investigate. A successful terminal state.

    Returned rather than raised, and there is deliberately no error type that
    means *nothing found*: an exception is something a caller handles and moves
    past, and this is the answer.
    """

    model_config = _STRICT

    screened: tuple[str, ...]
    healthy: tuple[str, ...]
    """Shown to do real work, and nothing above what its metrics may do."""

    unverified: tuple[Unverified, ...]
    unclassified: tuple[tuple[str, str], ...]
    """`(workload, metric)` pairs whose growth could not be fitted at all."""

    unflagged: tuple[Unflagged, ...]
    """Every metric this result *does* cover, and what it measured.

    The answer to *why was nothing flagged*, per metric rather than per report.
    """

    conditions: tuple[Conditions, ...]
    thresholds: Mapping[str, float]

    @property
    def covers_everything_screened(self) -> bool:
        """Whether *nothing found* is a statement about every workload looked at."""
        return not self.unverified and not self.unclassified

    def report(self) -> str:
        head = (
            f"Screened {len(self.screened)} workloads and flagged none: {', '.join(self.screened)}."
        )
        applied = ", ".join(f"{name} {value:g}" for name, value in sorted(self.thresholds.items()))
        shapes = ", ".join(
            f"{item.workload_id} at {item.scales} under {item.distribution} fixtures, "
            f"reset by {item.reset_strategy}, {item.cache_control}"
            for item in self.conditions
        )
        basis = f"Thresholds applied: {applied}. Measured: {shapes}."
        closing = (
            "Every workload here was shown to do real work, and nothing measured qualified as a "
            "finding. **That is the answer, not a failure to find one.**"
            if self.covers_everything_screened
            else self._caveats()
        )
        return "\n".join(
            part for part in [f"{head} {basis}", self._why_nothing_was_flagged(), closing] if part
        )

    def _why_nothing_was_flagged(self) -> str:
        """AC 1's third clause, and the one the artifact could not answer before.

        **Grouped by workload and then by reason**, because the two reasons are
        not the same kind of statement: one says the code did what it may, the
        other says the instrument could not resolve what it did. Listing them
        together would bury the second, and the second is the one a reader needs
        to see, because it is growth that was measured and not acted on.
        """
        if not self.unflagged:
            return ""

        grouped: dict[tuple[str, str], list[str]] = {}
        for item in self.unflagged:
            grouped.setdefault((item.workload_id, item.reason), []).append(item.describe())

        lines = [
            f"  {workload} [{reason}]: " + "; ".join(items)
            for (workload, reason), items in grouped.items()
        ]
        heading = "Nothing was flagged, and this is what was measured:"
        return "\n".join([heading, *lines])

    def _caveats(self) -> str:
        parts: list[str] = []
        empty = [item for item in self.unverified if item.touched_no_data]
        if empty:
            names = ", ".join(item.workload_id for item in empty)
            parts.append(
                f"**{names} ran and touched no data at all** — every amount measured was zero at "
                "the largest scale point. The harness measured an empty workload, so nothing "
                "here is a statement about the code it was supposed to exercise. This is "
                "`02-architecture.md` §1.5's failure mode and it is reported rather than "
                "counted as healthy."
            )
        unshown = [item for item in self.unverified if not item.touched_no_data]
        if unshown:
            parts.append(
                "**Not covered by this result:** "
                + "; ".join(f"{item.workload_id} — {item.reason}" for item in unshown)
            )
        if self.unclassified:
            pairs = ", ".join(f"{workload}/{metric}" for workload, metric in self.unclassified)
            parts.append(
                f"Growth could not be fitted for {pairs}, so those are *could not tell* rather "
                "than *nothing there*."
            )
        parts.append(
            f"Nothing was found in the {len(self.healthy)} workloads this result does cover."
        )
        return " ".join(parts)


def null_result(screened: Sequence[ScreenedWorkload], ranking: Ranking) -> NullResult:
    """Describe a screen that flagged nothing, in terms of what it can support.

    Raises:
        NullResultError: the ranking has flags in it, so this is not a null
            result; or nothing was screened, which is not one either.
    """
    if ranking.flagged:
        message = (
            f"{len(ranking.flagged)} flags were raised, so this is not a null result. Reporting "
            "*nothing found* from a screen that found something is the one failure this "
            "artifact exists to make impossible"
        )
        raise NullResultError(message)
    if not screened:
        message = (
            "nothing was screened, so there is nothing to report a null result about. Nothing "
            "found and nothing looked at are different answers"
        )
        raise NullResultError(message)

    unverified = tuple(
        Unverified(
            workload_id=result.workload.id,
            reason=result.workload.work_evidence,
            touched_no_data=_touched_no_data(result),
        )
        for result in screened
        if not result.workload.work_verified
    )
    unshown = {item.workload_id for item in unverified}

    return NullResult(
        screened=tuple(result.workload.id for result in screened),
        healthy=tuple(
            result.workload.id for result in screened if result.workload.id not in unshown
        ),
        unverified=unverified,
        unclassified=ranking.unclassified,
        unflagged=tuple(
            entry
            for result in screened
            if result.workload.id not in unshown
            for metric, measured in sorted(result.growth.items())
            if (entry := _unflagged(result, metric, measured)) is not None
        ),
        conditions=tuple(
            Conditions(
                workload_id=result.workload.id,
                scales=result.scales,
                distribution=result.distribution.value,
                reset_strategy=result.reset_strategy.value,
                cache_control=result.cache_control.value,
            )
            for result in screened
        ),
        thresholds={
            "flat cost (queries)": FLAT_COST_THRESHOLD,
            "timing floor (seconds)": TIMING_FLOOR_SECONDS,
            "minimum scale ratio": MINIMUM_SCALE_RATIO,
        },
    )


def _unflagged(result: ScreenedWorkload, metric: str, measured: MetricGrowth) -> Unflagged | None:
    """One metric's basis for not being flagged, or `None` because it was flagged.

    Both judgements come from `flagging`: `expected_growth` is the function
    `flag` tested against, and `withheld_reason` is the negative half of `flag`'s
    own decision. A table written here would be a second opinion about a thing
    that has already been decided once.
    """
    # A metric with no fitted growth is refused inside `withheld_reason`, which
    # is why there is no second filter against `ranking.unclassified` here. The
    # first draft had one and a sabotage proved it dead: every pair in that list
    # has `growth is None`, so the guard below already covers it, and two guards
    # for one condition is a place they can come to disagree.
    reason = withheld_reason(metric, measured, result)
    if reason is None or measured.growth is None:
        return None

    largest = result.workload.observations[-1] if result.workload.observations else None
    return Unflagged(
        workload_id=result.workload.id,
        metric=metric,
        observed=str(measured.growth),
        expected=str(expected_growth(metric)),
        ratio=measured.ratio,
        largest=largest.metrics.get(metric) if largest is not None else None,
        reason=str(reason),
    )


def _touched_no_data(result: ScreenedWorkload) -> bool:
    """Whether every amount this workload produced was zero at the largest scale.

    Only metrics whose meaning is known are consulted: `materialized`, which
    `measure_once` records on every run, and the catalogue's `TOTAL` counters,
    which are sums of amounts. An adapter's own metric could be a rate, a share
    or a flag, and reading a zero there as *touched no data* would be guessing.
    """
    largest = result.workload.observations[-1] if result.workload.observations else None
    if largest is None:
        return False

    amounts = [
        value
        for name, value in largest.metrics.items()
        if name == MATERIALIZED or (name in CATALOGUE and CATALOGUE[name].reads is Reading.TOTAL)
    ]
    return bool(amounts) and not any(amounts)

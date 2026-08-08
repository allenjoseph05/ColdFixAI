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
)
from coldfix.screening.growth import ScreenedWorkload
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

        if self.covers_everything_screened:
            return (
                f"{head} {basis} Every workload here was shown to do real work and none of them "
                "grew beyond what its metrics may. **That is the answer, not a failure to find "
                "one.**"
            )
        return " ".join(part for part in [head, basis, self._caveats()] if part)

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

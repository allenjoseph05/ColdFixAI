"""Which measurements are worth an investigation, and an ordering that admits what it is.

Epic 4, S-4.3. Screening measured; this decides. Both halves stay deterministic —
`04-cost.md` §9 counts screening as the largest cost gate in the system precisely
because no model is involved in either.

**"Flags superlinear growth" would miss the N+1.** Read literally, AC 1 flags a
metric whose fitted exponent exceeds one — and a textbook N+1 is *linear* in
query count. It is linear in this project's own planted fixture, and it is linear
in the unplanted defect ADR 011 pinned the development target for. A screen that
flagged only superlinear growth would walk past the single defect this whole
system was built around.

The reason is that *superlinear* is the wrong comparison. What matters is growth
above what a metric **can be expected to do** as data grows, and that differs by
metric:

- **A round-trip count** — queries, requests, file opens — is expected to stay
  **constant**. One batched round trip serves any number of rows, and producing
  exactly that is what a fix for an N+1 does.
- **An amount** — rows, bytes, allocations — and **a duration** are expected to
  grow **linearly**. More data is more data, and that is not a defect.
- **Anything unrecognised** gets the linear expectation, so it is flagged only
  when superlinear: AC 1 read literally, kept as the safe default for a metric
  nothing is known about.

ADR 052 records this. Superlinear growth in an amount is still flagged, so
nothing AC 1 asked for is lost.

**High flat cost is a weaker claim and is kept visibly separate.** AC 1 also asks
for *unexplained* high flat cost, and the word doing the work is "unexplained" —
screening has no way to know whether an explanation exists. S-0.3 measured a
**~35-query floor** on a real mature system's endpoint, and this project's own
fixture ships a 37-query decoy that must never be flagged as a defect, because a
fix there is the metastability trap `00-BRIEF.md` §4 warns about: an optimization
that improves every metric measured while removing slack. So flat cost is a
separate flag kind, ranked below every growth flag, with a threshold well above
the floor a mature system genuinely has, and an explanation that says a fix may
be removing headroom rather than waste.

**The ranking states what it cannot express.** `08-audit.md` §6: a 10x win on a
monthly batch job outranks a 2x win on the hottest endpoint under magnitude
ordering, and there is no call-frequency information anywhere in this system. The
report says so every time, because an ordering that looks like a priority *is* a
priority to whoever reads it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.stats import Growth
from coldfix.primitives.counters import CATALOGUE, Reading
from coldfix.primitives.measurement import MetricKind
from coldfix.screening.growth import MetricGrowth, ScreenedWorkload

# What a metric may do as data grows without that being a defect. Ordered by
# `_SEVERITY` below; a metric doing more than this is what gets flagged.
_EXPECTED_UNRECOGNISED = Growth.LINEAR

_SEVERITY: Mapping[Growth, int] = {
    Growth.CONSTANT: 0,
    Growth.LINEAR: 1,
    Growth.SUPERLINEAR: 2,
}

# A constant cost this far above the ~35-query floor S-0.3 measured on a real
# mature system's endpoint. Set well clear of it on purpose: the planted decoy
# sits at 37 and is *correct*, and a threshold that flagged it would teach a
# reader that a mature system's ordinary shape is a defect. Three times the
# measured floor is the smallest number that cannot be one.
FLAT_COST_THRESHOLD = 120.0

# S-0.4's measured wall-clock noise floor, ~20ms on a 350ms endpoint. A duration
# that rose by less than this across the whole sweep has not been shown to have
# risen at all, whatever exponent fits four single samples.
TIMING_FLOOR_SECONDS = 0.020

# A rise is measured between the two ends of the sweep, so there have to be two.
_ENDS = 2

# The sentence `08-audit.md` §6 requires on every ordering this module produces.
FREQUENCY_UNKNOWN = (
    "**Ranked by measured magnitude, and nothing here knows how often any of these run.** "
    "A tenfold win on a monthly batch job sorts above a twofold win on the busiest endpoint "
    "in the system, and this ordering cannot tell the two apart. Where the project has logs "
    "or metrics, read them before choosing; where it does not, treat this as a list of what "
    "was measured rather than a list of what matters."
)


class FlaggingError(Exception):
    """A screening result could not be turned into a ranking."""


class FlagKind(StrEnum):
    """Why a workload is on the list, and they are not equally strong.

    `GROWTH` is a measurement: the metric was watched across a sixteenfold
    increase and did more than it can be expected to. `FLAT_COST` is a threshold
    somebody chose, applied to a workload that did not grow at all.
    """

    GROWTH = "grows faster than this metric can be expected to"
    FLAT_COST = "high constant cost, with no way to tell whether it is explained"


@dataclass(frozen=True)
class Flag:
    """One metric on one workload, and why it was flagged."""

    workload_id: str
    metric: str
    kind: FlagKind
    observed: Growth | None
    expected: Growth
    magnitude: float
    """The ratio across the sweep for a growth flag, the constant cost for a flat one.

    Two different units, deliberately not reconciled — see `rank`, which orders by
    kind first for that reason.
    """

    def explanation(self) -> str:
        if self.kind is FlagKind.FLAT_COST:
            return (
                f"{self.workload_id}: {self.metric} is flat at {self.magnitude:g} across the "
                f"sweep, above the {FLAT_COST_THRESHOLD:g} this screen treats as worth a look. "
                "**It did not grow, and it may be correct** — S-0.3 measured a ~35-query floor "
                "on a real mature system's endpoint, and a workload answering many separate "
                "questions legitimately costs many separate queries. Nothing measured here can "
                "tell an unnecessary constant cost from a necessary one, and a fix that removes "
                "slack rather than waste is the metastability trap `00-BRIEF.md` §4 is about."
            )
        return (
            f"{self.workload_id}: {self.metric} grew {self.magnitude:.1f}x across the sweep and "
            f"fits {self.observed}, where this metric can be expected to be {self.expected}. "
            + _why_expected(self.metric, self.expected)
        )


@dataclass(frozen=True)
class Ranking:
    """What screening found, in order, with what the order cannot say attached."""

    flagged: tuple[Flag, ...]
    healthy: tuple[str, ...]
    """Workloads screened and skipped. Named, because S-4.5 reports on them."""

    unclassified: tuple[tuple[str, str], ...]
    """`(workload, metric)` pairs whose growth could not be fitted at all.

    Not flagged and not healthy: a metric that was zero at some scale point has
    no exponent (`log(0)` is undefined), and calling that *flat* would publish an
    exclusion nobody measured. S-4.5 needs this list to tell "healthy" from
    "could not tell".

    **Only metrics that could have been flagged appear here**, which Epic 4's
    composition check corrected. `blocked_seconds` is elapsed minus CPU and is
    zero or negative on any workload fast enough that the two clocks agree, so it
    is unfittable almost always — and it is also below the timing floor, so its
    exponent could not have changed anything. Recording it made every null result
    on a healthy workload say it did not cover everything, which is a caveat
    attached to everything and therefore a caveat nobody reads.
    """

    @property
    def screened(self) -> tuple[str, ...]:
        seen = [flag.workload_id for flag in self.flagged]
        return tuple(dict.fromkeys(seen + list(self.healthy)))

    def report(self) -> str:
        if not self.flagged:
            return f"Screened {len(self.healthy)} workloads and flagged none. {_caveat(self)}"
        lines = [flag.explanation() for flag in self.flagged]
        return "\n".join([FREQUENCY_UNKNOWN, *lines, _caveat(self)])


def flag(screened: ScreenedWorkload) -> tuple[Flag, ...]:
    """Every metric on one workload that is worth an investigation.

    A workload with nothing above its expectations produces no flags, which is
    AC 4: healthy workloads are skipped rather than reported with a low score.
    """
    found: list[Flag] = []
    for metric, measured in sorted(screened.growth.items()):
        expected = expected_growth(metric)
        if measured.growth is None:
            continue
        if _SEVERITY[measured.growth] > _SEVERITY[expected] and _above_the_noise(
            metric, measured, screened
        ):
            found.append(
                Flag(
                    workload_id=screened.workload.id,
                    metric=metric,
                    kind=FlagKind.GROWTH,
                    observed=measured.growth,
                    expected=expected,
                    magnitude=measured.ratio if measured.ratio is not None else float("nan"),
                )
            )
        elif _is_high_and_flat(measured, screened):
            found.append(
                Flag(
                    workload_id=screened.workload.id,
                    metric=metric,
                    kind=FlagKind.FLAT_COST,
                    observed=measured.growth,
                    expected=expected,
                    magnitude=screened.workload.observations[-1].metrics[metric],
                )
            )
    return tuple(found)


def rank(screened: Sequence[ScreenedWorkload]) -> Ranking:
    """Flag every workload and order what came back.

    **Growth flags come first as a class, then magnitude within each class.** The
    two kinds are measured in different units — a ratio and a count — and there
    is no honest exchange rate between them. Ordering by kind says the thing that
    is true: a metric watched across a sixteenfold increase and found to grow is
    stronger evidence than a number that crossed a threshold somebody chose.

    Raises:
        FlaggingError: nothing to rank, which is not the same as nothing found.
    """
    if not screened:
        message = (
            "no screening results to rank. Nothing found and nothing looked at are different "
            "answers, and S-4.5 reports the first one only"
        )
        raise FlaggingError(message)

    flags: list[Flag] = []
    healthy: list[str] = []
    unclassified: list[tuple[str, str]] = []
    for result in screened:
        found = flag(result)
        flags.extend(found)
        if not found:
            healthy.append(result.workload.id)
        unclassified.extend(
            (result.workload.id, metric)
            for metric, measured in sorted(result.growth.items())
            if measured.growth is None and _above_the_noise(metric, measured, result)
        )

    ordered = sorted(
        flags,
        key=lambda item: (
            0 if item.kind is FlagKind.GROWTH else 1,
            -item.magnitude if item.magnitude == item.magnitude else 0.0,
            item.workload_id,
            item.metric,
        ),
    )
    return Ranking(
        flagged=tuple(ordered),
        healthy=tuple(healthy),
        unclassified=tuple(unclassified),
    )


def expected_growth(metric: str) -> Growth:
    """What this metric may do as data grows without that being a defect.

    A round-trip count is expected to stay **constant**: one batched query serves
    a hundred rows as easily as ten, and that is what a fix for an N+1 produces.
    An amount and a duration are expected to grow **linearly** — more data is more
    data. Anything unrecognised gets the linear expectation, so an unknown metric
    is flagged only when it is superlinear, which is AC 1 read literally and the
    right default for something nothing is known about.
    """
    counter = CATALOGUE.get(metric)
    if counter is not None and counter.reads is Reading.EVENTS:
        return Growth.CONSTANT
    return _EXPECTED_UNRECOGNISED


def _above_the_noise(metric: str, measured: MetricGrowth, screened: ScreenedWorkload) -> bool:
    """A duration must also clear S-0.4's floor. A count needs nothing extra.

    **Found by this screen flagging its own control.** The batched workload — the
    fixture's clean counterpart, the shape a fix produces — came back
    `SUPERLINEAR` in `seconds` with a ratio of 8.7 across a sixteenfold sweep,
    on a workload that runs in under a millisecond. Screening takes one sample
    per scale point, and S-0.4 measured wall-clock timings drifting 12% between
    runs minutes apart, so a fitted exponent over four single samples of a
    sub-millisecond workload is a fit to noise.

    So a duration flag needs the two tests S-3.8's envelope already applies to a
    candidate: the shape *and* an absolute difference bigger than the ~20ms floor
    S-0.4 measured. Counts are exempt because they reproduce to the integer.
    Nothing is dropped from the report — the duration is still measured, still
    fitted, and still shown; it just cannot raise a flag on its own.

    **Both ends have to be measurable, and Epic 4's composition check is why.**
    `cpu_seconds` comes from `process_time`, which on Windows moves in steps of
    about 15.6ms — S-3.7 and S-3.13 both hit that granularity before this did.
    A sub-millisecond workload therefore records zero ticks at the small scale
    and one or two at the large one, so a *quantisation artefact* of 31ms clears
    a 20ms floor and flags a workload that did nothing. Requiring the smaller
    measurement to be above the floor as well is what makes the comparison a
    comparison: below it, the denominator is rounding.
    """
    if measured.kind is not MetricKind.DURATION:
        return True
    observations = screened.workload.observations
    if len(observations) < _ENDS:
        return False

    smallest = observations[0].metrics[metric]
    rise = observations[-1].metrics[metric] - smallest
    return smallest >= TIMING_FLOOR_SECONDS and rise >= TIMING_FLOOR_SECONDS


def _is_high_and_flat(measured: MetricGrowth, screened: ScreenedWorkload) -> bool:
    """A constant count above the threshold, and only a count.

    Durations are excluded: a flat 200ms is a fact about one machine on one day,
    and S-0.4 measured wall-clock timings drifting 12% between runs minutes apart
    while counters reproduced to the byte. A threshold on the noisy one would
    flag a workload for having been measured on a slow afternoon.
    """
    if measured.growth is not Growth.CONSTANT or measured.kind is not MetricKind.COUNT:
        return False
    if not screened.workload.observations:
        return False
    return screened.workload.observations[-1].metrics[measured.metric] >= FLAT_COST_THRESHOLD


def _why_expected(metric: str, expected: Growth) -> str:
    if expected is Growth.CONSTANT:
        counter = CATALOGUE.get(metric)
        event = counter.event if counter is not None else "one round trip"
        return (
            f"Each unit here is {event}, and one batched round trip serves any number of rows — "
            "so a count that climbs with the data is the shape a fix removes."
        )
    return "More data is more data, so linear growth here is not a defect; this is above that."


def _caveat(ranking: Ranking) -> str:
    if not ranking.unclassified:
        return ""
    pairs = ", ".join(f"{workload}/{metric}" for workload, metric in ranking.unclassified)
    return (
        f"Growth could not be fitted for {pairs} — a metric that was zero at some scale point "
        "has no exponent, so those are neither flagged nor cleared. That is *could not tell*, "
        "not *nothing there*."
    )

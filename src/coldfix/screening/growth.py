"""Sweep every workload, fit every metric, decide nothing.

Epic 4, S-4.2. Screening is the largest cost gate in the system — `04-cost.md`
§9 puts it at roughly 70% of workloads eliminated before any agent runs — and it
buys that by being **arithmetic**. This module measures and fits. What to do
about what it found belongs to S-4.3, and keeping the two apart is what lets a
reader check the numbers without also having to agree with a threshold.

**Zero model calls, and the assertion is structural.** Nothing here imports an
LLM SDK, and the test that says so walks the transitive import graph of
`coldfix.screening` in a clean interpreter rather than asserting that no call
happened to be made. Written that way because the obvious test — *run screening
with no client configured* — passes today for the wrong reason: no client exists
until E7, so it would assert nothing and would go on passing after one did.

**Three scale points, not the two the story asks for.** `fit_growth` needs three:
two points define a line through themselves and say nothing about whether it is
the right line, which is why S-3.2 refuses a two-point sweep outright. The
default spread is 16x, which also clears S-4.1's four-fold minimum for F6's
work-verification test — so a screened workload arrives at S-4.3 with its
observations filled in and the question of whether it does real work already
answerable.

**Both numbers are reported: the ratio and the fit.** A ratio is what a reader
checks by hand and what S-4.3 ranks on. An exponent is what the growth
classification rests on, and the two disagree in the case that matters — a
metric can double across a 16x sweep and still be constant-with-noise, or rise
2.4x and be genuinely quadratic over the part of the curve that was measured.
Publishing only one of them would make a screening result impossible to argue
with.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from coldfix.bench.stats import Fit, Growth
from coldfix.primitives.measurement import CacheControl, MetricKind
from coldfix.primitives.scaling import Distribution, ScalingResult, scale_volume
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import BoundWorkload, Observation, Workload

# Three points because `fit_growth` needs three, geometric because the power fit
# runs over logarithms and evenly spaced points in log space are what it can use.
# The 16x spread is the other constraint: S-4.1 declines to judge work
# verification below 4x, so a screen that swept 10 to 30 would hand S-4.3 a
# workload it could say nothing about.
SCREENING_SCALES = (10, 40, 160)


class ScreeningError(Exception):
    """A workload could not be screened, so nothing is known about it."""


@dataclass(frozen=True)
class MetricGrowth:
    """What one metric did across the sweep, both ways of asking.

    `ratio` is the raw largest-over-smallest, before the framework baseline is
    subtracted — the number somebody can check against the two measurements. The
    fit is taken over the adjusted points, because S-3.2 found the baseline
    changes the *exponent* rather than shifting the line.
    """

    metric: str
    kind: MetricKind
    fit: Fit
    ratio: float | None
    """`None` where the smallest measurement was zero.

    Not infinity, and not a large number: nothing grew by an amount here, and a
    metric that starts at zero is a fact about the small end of the sweep — a
    cache covering the empty case, a queryset that never fired.
    """

    @property
    def growth(self) -> Growth | None:
        """The classification, or `None` where a power law could not be fitted."""
        return self.fit.growth


@dataclass(frozen=True)
class ScreenedWorkload:
    """One workload, measured at every scale point, with nothing concluded.

    Carries the conditions as well as the numbers. `01-primitives.md` and
    `CLAUDE.md` agree that an exclusion carries its preconditions, and *queries
    flat across a sixteenfold increase* means one thing under a uniform fixture
    with a fresh container per point and something much weaker otherwise.
    """

    workload: Workload
    """The artifact with this sweep's observations recorded on it.

    Which is what makes `work_verified` answerable: S-4.1's F6 test needs two
    scale points at a four-fold spread, and screening is the step that produces
    them.
    """

    growth: Mapping[str, MetricGrowth]
    result: ScalingResult

    @property
    def scales(self) -> tuple[int, ...]:
        return self.result.scales

    @property
    def distribution(self) -> Distribution:
        return self.result.distribution

    @property
    def reset_strategy(self) -> ResetStrategy:
        return self.result.reset_strategy

    @property
    def cache_control(self) -> CacheControl:
        return self.result.cache_control

    def metric(self, name: str) -> MetricGrowth:
        """One metric's growth, refusing a name that was not measured.

        ADR 013's rule: an unmeasured name raises rather than reading as flat,
        because a typo that returned "no growth" would become an exclusion.
        """
        try:
            return self.growth[name]
        except KeyError:
            measured = ", ".join(sorted(self.growth)) or "nothing"
            message = (
                f"{self.workload.id} has no measurement of {name!r}; it measured {measured}. "
                "A metric that was not measured is not a metric that stayed flat"
            )
            raise ScreeningError(message) from None


def screen_growth(
    bound: BoundWorkload,
    *,
    scales: Sequence[int] = SCREENING_SCALES,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
) -> ScreenedWorkload:
    """Measure one workload at every scale point and fit each metric against volume.

    Everything the sweep needs comes off the workload: the fixture recipe
    declares the distribution, the binding supplies seeding, invocation and a
    verified reset, and cache control comes from whichever of the two guarantees
    the adapter could give. Nothing is defaulted on the workload's behalf —
    S-3.2 refuses a sweep with no cache control, and screening inherits the
    refusal rather than restating it.

    Raises:
        ScaleSweepError, CacheControlError, MeasurementError: as `scale_volume`.
    """
    result = scale_volume(
        seed=bound.scale,
        invoke=bound.invoke,
        reset=bound.reset,
        scales=scales,
        distribution=bound.descriptor.fixture.distribution,
        counters=counters,
        extra_counters=extra_counters,
        clear_caches=bound.clear_caches,
        process_identity=bound.process_identity,
    )

    return ScreenedWorkload(
        workload=bound.descriptor.model_copy(update={"observations": _observed(result)}),
        growth=_growth_of(result),
        result=result,
    )


def screen(
    workloads: Sequence[BoundWorkload],
    *,
    scales: Sequence[int] = SCREENING_SCALES,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
) -> tuple[ScreenedWorkload, ...]:
    """Screen every workload, in the order given.

    A workload that cannot be screened stops the screen. It is tempting to skip
    it and carry on with the rest — a screening pass that returned nine of ten
    results looks like progress — but the tenth would be recorded nowhere, and a
    workload silently absent from a screen is indistinguishable from one that was
    screened and found healthy. That is the shape of a missed finding, so the
    error travels.

    Raises:
        ScreeningError: no workloads, which is not a screen.
    """
    if not workloads:
        message = (
            "no workloads to screen. An empty screen is not a null result — a null result names "
            "what it looked at, and S-4.5 cannot report on nothing"
        )
        raise ScreeningError(message)

    return tuple(
        screen_growth(
            bound,
            scales=scales,
            counters=counters,
            extra_counters=extra_counters,
        )
        for bound in workloads
    )


def _observed(result: ScalingResult) -> tuple[Observation, ...]:
    """The sweep's raw points, as the artifact records them.

    Raw rather than adjusted, because S-4.1's observations are measurements and
    the baseline subtraction belongs to the fit. Sorted by scale, since a sweep
    may deliberately run in a randomized order — `01-primitives.md` §10's defence
    against ordering bias — while the artifact must render identically every time
    for ADR 002's cached prefix.
    """
    return tuple(
        Observation(scale=point.scale, metrics=dict(point.raw))
        for point in sorted(result.points, key=lambda point: point.scale)
    )


def _growth_of(result: ScalingResult) -> Mapping[str, MetricGrowth]:
    points = sorted(result.points, key=lambda point: point.scale)
    smallest, largest = points[0], points[-1]

    return {
        name: MetricGrowth(
            metric=name,
            kind=result.kinds[name],
            fit=fit,
            ratio=_ratio(smallest.raw[name], largest.raw[name]),
        )
        for name, fit in sorted(result.fits.items())
    }


def _ratio(at_smallest: float, at_largest: float) -> float | None:
    if at_smallest == 0:
        return None
    return at_largest / at_smallest

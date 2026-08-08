"""Cost that grows with elapsed time rather than with input size.

Epic 3, S-3.15. `01-primitives.md` §5: run the same workload at a fixed size for
an extended period and fit metrics against **elapsed time**. It detects the Ramp
— memory leaks, cache pollution, connection exhaustion, fragmentation, index
bloat — and the real-world shape it catches is specific: error rates creeping
over hours and then spiking overnight, from a leak that surfaces only after
sustained traffic, *while a thirty-minute load test the week before passed
cleanly*.

**This primitive inverts the invariant every other one here enforces.** ADR 026
and S-3.2 require a fresh process or an explicit clear between measurements,
because state carried from one run to the next makes the second look cheaper.
Here the carried state **is the subject**. Resetting between samples, or running
each in its own container, would destroy exactly the thing being measured — so
this primitive does not reset, and that is a property to state rather than an
omission to notice later.

**Nothing is discarded, including the first sample.** S-1.2 refuses warmup
discarding on Barrett et al.'s finding that at most 43.5% of VM/benchmark pairs
reach a steady state at all, so "drop the first N" is wrong more often than it is
right and deletes exactly the samples that show it. The same rule holds here,
where the first sample is often the most interesting one.

**A rising line is not a finding on its own.** Four hours of soaking is four
hours during which the machine also changed: another process arrived, a disk
filled, a CPU thermally throttled. Every one of those produces the same rising
line as a leak. So a reference workload can be measured alongside the subject,
and what is reported is the subject's trend **against** the reference's. Run
without one, the result says so — a ramp with no control is a ramp that cannot be
attributed, and after four hours that is an expensive thing to discover late.

**The duration has a hard cap and asking past it is refused, not clamped.**
Silently running four hours when eight were asked for produces a soak that may
simply not have reached the ramp, reported as *no ramp* — a manufactured
exclusion, which is the failure mode ADR 013 exists to prevent.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from coldfix.bench.stats import Fit, fit_growth
from coldfix.primitives.measurement import MeasurementError, measure_once
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
    ProjectFact,
    requires,
)

# Six hours. Long enough for the shape §5 describes — a leak that surfaces after
# sustained traffic — and short enough that a run cannot quietly become a
# permanent fixture of somebody's afternoon. Asking for more is refused rather
# than clamped; see the module docstring.
MAXIMUM_DURATION_SECONDS = 6 * 60 * 60

# `fit_growth` needs three points at two or more distinct scales, and a soak that
# managed two iterations has a line through two points rather than a trend.
MINIMUM_SAMPLES = 3

# **A ramp is read from the straight line, not from the growth class.** The
# power-law fit `fit_growth` also returns is the right model against input size
# and the wrong one against elapsed time: the first sample sits at t≈0, where a
# log is undefined, so the exponent is either unavailable or computed across a
# distorted axis. What a ramp actually is — cost drifting upward as the process
# stays up — is a positive slope, so that is what is read.
#
# Two guards on it, for the reasons every other threshold here has one. The fit
# has to explain the data, or a noisy flat series produces a slope by accident;
# and the rise across the window has to be material against the metric's own
# level, or a soak reports a leak from a drift of a millisecond an hour.
RAMP_FIT_QUALITY = 0.5
RAMP_MINIMUM_RISE = 0.10


class LongitudinalError(MeasurementError):
    """A soak could not be run, or its series could not be fitted."""


@dataclass(frozen=True)
class Sample:
    """One iteration: when it happened and what it cost."""

    index: int
    elapsed: float
    """Seconds since the soak began. The x-axis."""

    metrics: Mapping[str, float]


@dataclass(frozen=True)
class Trend:
    """How one metric moved over the run."""

    metric: str
    fit: Fit
    first: float
    last: float
    window: float
    """Seconds from the first sample to the last. What the slope is read over."""

    @property
    def modelled_rise(self) -> float:
        """How much the fitted line climbed across the window."""
        return self.fit.slope * self.window

    @property
    def ramping(self) -> bool:
        """Whether the metric drifted upward as the process stayed up.

        Read from the fitted line over every sample rather than from the first
        and last, because two endpoints of a noisy series say nothing — and from
        the *linear* fit rather than the growth class, because a power law is the
        wrong model against an axis that starts at zero.

        Three conditions, and each removes a way of being confidently wrong: the
        slope is positive, the line explains the data, and the climb is worth
        something against the level the metric started at.
        """
        if self.fit.slope <= 0 or self.fit.linear_r_squared < RAMP_FIT_QUALITY:
            return False
        floor = abs(self.first) if self.first else 1.0
        return self.modelled_rise / floor >= RAMP_MINIMUM_RISE

    @property
    def change(self) -> float:
        if self.first == 0:
            return float("inf") if self.last > 0 else 1.0
        return self.last / self.first


@dataclass(frozen=True)
class Soak:
    """A workload run at a fixed size for a period, and what moved while it ran."""

    samples: tuple[Sample, ...]
    trends: Mapping[str, Trend]
    duration: float
    reference: Mapping[str, Trend] | None = field(default=None)
    """The same fit for a control workload, where one was supplied.

    `None` means no control was run, which is not the same as a control that
    found nothing. Every trend below is then a statement about the subject *and*
    the machine it ran on, and after several hours that distinction is expensive
    to discover late.
    """

    @property
    def controlled(self) -> bool:
        return self.reference is not None

    def ramping(self) -> tuple[Trend, ...]:
        """Metrics that grew with time, worst first — net of the control.

        A metric that ramps in the subject *and* in the reference is the machine
        changing under both, so it is not reported: after four hours the thing
        that rose may simply be the afternoon.
        """
        found = [
            trend
            for trend in self.trends.values()
            if trend.ramping and not self._control_ramps(trend.metric)
        ]
        return tuple(sorted(found, key=lambda trend: trend.change, reverse=True))

    def _control_ramps(self, metric: str) -> bool:
        """Whether the control workload grew in the same metric.

        With no control there is nothing to subtract, so this is false and every
        trend is reported — qualified by `controlled`, which says the trends are
        about the subject and the machine together.
        """
        if self.reference is None:
            return False
        control = self.reference.get(metric)
        return control is not None and control.ramping

    def explanation(self) -> str:
        head = f"{len(self.samples)} iterations over {self.duration:.0f}s at a fixed input size."
        ramping = self.ramping()

        if not ramping:
            body = (
                " Nothing grew with elapsed time, so there is no ramp over this period — an "
                "exclusion that is only as strong as the period is long, and §5's example is a "
                "leak that a thirty-minute test missed and hours of traffic found."
            )
        else:
            listed = "\n".join(
                f"  - {trend.metric}: rising {trend.fit.slope:.4g} per second against elapsed "
                f"time (r²={trend.fit.linear_r_squared:.2f}), {trend.change:.2f}x from first "
                "sample to last"
                for trend in ramping
            )
            body = f" These grew with elapsed time rather than with input size:\n{listed}"

        if not self.controlled:
            return (
                f"{head}{body}\n\n**No control workload was run**, so anything above is a "
                "statement about this subject and the machine together: another process "
                "arriving, a disk filling and a CPU throttling all produce the same rising "
                "line as a leak."
            )
        return (
            f"{head}{body}\n\nA control workload ran alongside, and trends it shares are excluded."
        )


def soak(
    workload: Callable[[], object],
    *,
    duration: float,
    counters: Sequence[str] = (),
    reference: Callable[[], object] | None = None,
    minimum_samples: int = MINIMUM_SAMPLES,
) -> Soak:
    """Run `workload` repeatedly for `duration` seconds and fit every metric against time.

    **Nothing is reset between iterations.** The accumulated state is what this
    primitive measures, and every other primitive here goes to some trouble to
    prevent exactly that — see the module docstring.

    `reference` is the control: a workload that should *not* ramp, measured in
    the same loop, so that a rise shared by both can be attributed to the machine
    rather than the subject.

    Raises:
        LongitudinalError: the duration is not positive or exceeds the hard cap,
            or the run produced too few samples to fit.
    """
    if duration <= 0:
        message = f"a soak needs a positive duration, got {duration}"
        raise LongitudinalError(message)
    if duration > MAXIMUM_DURATION_SECONDS:
        message = (
            f"{duration:.0f}s exceeds the hard cap of {MAXIMUM_DURATION_SECONDS}s. This is "
            "refused rather than shortened: a soak that quietly ran for less than it was "
            "asked for may not have reached the ramp, and would report that as no ramp"
        )
        raise LongitudinalError(message)

    samples: list[Sample] = []
    control: list[Sample] = []
    started = time.perf_counter()

    while True:
        elapsed = time.perf_counter() - started
        if elapsed >= duration:
            break
        index = len(samples)
        samples.append(
            Sample(index=index, elapsed=elapsed, metrics=measure_once(workload, counters))
        )
        if reference is not None:
            control.append(
                Sample(
                    index=index,
                    elapsed=time.perf_counter() - started,
                    metrics=measure_once(reference, counters),
                )
            )

    if len(samples) < minimum_samples:
        message = (
            f"the soak completed {len(samples)} iteration(s) in {duration:.0f}s, and fitting a "
            f"trend needs at least {minimum_samples}. Either give it longer or measure "
            "something quicker — a trend through two points is a line drawn through them"
        )
        raise LongitudinalError(message)

    return Soak(
        samples=tuple(samples),
        trends=_fit_series(samples),
        duration=duration,
        reference=_fit_series(control) if reference is not None else None,
    )


def _fit_series(samples: Sequence[Sample]) -> Mapping[str, Trend]:
    """Fit every recorded metric against elapsed time.

    The same `fit_growth` S-3.2 uses for input size, with elapsed seconds as the
    x-axis. That is the whole of what makes this a different primitive rather
    than a different function: *what varies* is time, and the arithmetic of
    finding out how something grows against it does not care which.
    """
    if not samples:
        return {}

    elapsed = [sample.elapsed for sample in samples]
    window = elapsed[-1] - elapsed[0]
    return {
        metric: Trend(
            metric=metric,
            fit=fit_growth(elapsed, [sample.metrics[metric] for sample in samples]),
            first=samples[0].metrics[metric],
            last=samples[-1].metrics[metric],
            window=window,
        )
        for metric in sorted(samples[0].metrics)
        if all(metric in sample.metrics for sample in samples)
    }


REGISTRY.register(
    Primitive(
        name="longitudinal.soak",
        summary=(
            "Run a workload at a fixed size for an extended period and fit every metric "
            "against elapsed time, to find cost that grows as the system runs rather than as "
            "its input grows. The most expensive primitive here."
        ),
        cost=CostClass.HOURS,
        run=soak,
        required_capabilities={Capability.EVENT_COUNTERS},
        applies=requires(
            ProjectFact.LONG_RUNNING_PROCESS,
            because=(
                "a ramp is cost accumulating in a process that stays up, so there is nothing "
                "for it to accumulate in on something that exits after each run. "
                "`01-primitives.md` §5 is explicit: never run this on a CLI tool"
            ),
        ),
    )
)

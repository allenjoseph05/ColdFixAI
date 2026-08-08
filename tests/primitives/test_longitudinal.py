"""Cost that grows with elapsed time, and the machine that grew alongside it.

S-3.15. `01-primitives.md` §5's example is the whole reason this primitive is
worth its cost: error rates creeping over hours and then spiking overnight, from
a leak that surfaced only after sustained traffic — *while a thirty-minute load
test the week before passed cleanly*.

Two things get the attention here.

**The inverted invariant.** Every other primitive goes to trouble to prevent
state carrying between measurements; this one requires it, because the carried
state is what it measures. A soak that reset between iterations would find
nothing, always, and report that as no ramp.

**The control.** Four hours of soaking is four hours during which the machine
also changed, and a rising line is what both a leak and a busy afternoon look
like. So the subject is measured against a reference that should not ramp, and
a rise they share is not reported.

The subject is a counter that grows a list on every call — a leak in the
smallest form that is really one — against a control that does the same amount
of work and keeps nothing.
"""

from __future__ import annotations

import time

import pytest

from coldfix.primitives.longitudinal import (
    MAXIMUM_DURATION_SECONDS,
    LongitudinalError,
    Sample,
    Soak,
    Trend,
    _fit_series,
    soak,
)
from coldfix.primitives.measurement import MATERIALIZED, SECONDS
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    Capability,
    PrimitiveUnavailableError,
    ProjectFact,
    ProjectProfile,
)

DURATION = 0.4


class Leaky:
    """Each call keeps a little more than the last, and does a little more work.

    A leak in the smallest form that is really one: the retained list is walked
    on every call, so the cost grows with how long the process has been up and
    not at all with the size of its input.
    """

    def __init__(self, growth: int = 400) -> None:
        self.retained: list[int] = []
        self.growth = growth

    def __call__(self) -> int:
        self.retained.extend(range(self.growth))
        return sum(1 for _ in self.retained)


class Steady:
    """The control: the same shape of work, keeping nothing."""

    def __init__(self, size: int = 400) -> None:
        self.size = size

    def __call__(self) -> int:
        return sum(1 for _ in range(self.size))


def series(costs: list[float]) -> Soak:
    """A soak stated directly, for the tests about what it reports."""
    samples = tuple(
        Sample(index=index, elapsed=float(index), metrics={SECONDS: cost})
        for index, cost in enumerate(costs)
    )
    return Soak(samples=samples, trends=_fit_series(samples), duration=float(len(costs)))


# ------------------------------ AC 1 and 2: fixed size, fitted against time


def test_a_leak_shows_as_growth_against_elapsed_time() -> None:
    """AC 1 and 2. The input never changes size; what changes is how long the
    process has been running, which is the axis §5 fits against."""
    result = soak(Leaky(), duration=DURATION)

    ramping = result.ramping()

    assert len(result.samples) >= 3
    assert any(trend.metric == MATERIALIZED for trend in ramping) or any(
        trend.metric == SECONDS for trend in ramping
    )


def test_a_workload_that_keeps_nothing_shows_no_ramp() -> None:
    """The control, and the more important of the two: a primitive that finds a
    ramp in everything would find one after every soak it ever ran, and each one
    costs hours."""
    result = soak(Steady(), duration=DURATION)

    assert not result.ramping()
    assert "no ramp over this period" in result.explanation()


def test_the_exclusion_is_only_as_strong_as_the_period_is_long() -> None:
    """§5's example is a leak a thirty-minute test missed and hours of traffic
    found. Reporting *no ramp* without that qualification would be reporting a
    stronger result than the run supports."""
    result = soak(Steady(), duration=DURATION)

    assert "only as strong as the period is long" in result.explanation()


def test_every_recorded_metric_is_fitted_against_time() -> None:
    """Metrics, plural — the same reason S-3.2 fits all of them: the one left
    out is the one the next leak uses."""
    result = soak(Leaky(), duration=DURATION)

    assert SECONDS in result.trends
    assert MATERIALIZED in result.trends
    assert all(isinstance(trend, Trend) for trend in result.trends.values())


def test_the_series_is_reported_and_nothing_is_discarded() -> None:
    """S-1.2's rule, inherited: Barrett et al. found at most 43.5% of
    VM/benchmark pairs reach a steady state at all, so dropping the first N is
    wrong more often than it is right — and here the first sample is often the
    interesting one."""
    result = soak(Leaky(), duration=DURATION)

    assert result.samples[0].index == 0
    assert [sample.index for sample in result.samples] == list(range(len(result.samples)))
    assert result.samples[0].elapsed < result.samples[-1].elapsed


def test_a_trend_is_read_from_the_fit_rather_than_from_two_endpoints() -> None:
    """Two endpoints of a noisy series say nothing; the fit uses every sample."""
    noisy_but_flat = series([1.0, 1.4, 0.7, 1.3, 0.8, 1.2, 0.9, 1.1])

    assert not noisy_but_flat.trends[SECONDS].ramping
    assert noisy_but_flat.trends[SECONDS].fit.linear_r_squared < 0.5


def test_a_rising_series_is_reported_as_a_ramp() -> None:
    rising = series([1.0, 2.0, 3.1, 4.2, 5.0, 6.1])

    assert rising.trends[SECONDS].ramping
    assert rising.trends[SECONDS].change == pytest.approx(6.1)


# --------------------------------------------- the inverted invariant


def test_nothing_is_reset_between_iterations() -> None:
    """The property that makes this primitive work, and the one every other
    primitive here forbids. Resetting would destroy the accumulated state, so a
    soak would find nothing — always — and report it as no ramp."""
    leaky = Leaky()

    soak(leaky, duration=DURATION)

    # The state survived every iteration, which is what was being measured.
    assert len(leaky.retained) >= 3 * leaky.growth


# ---------------------------------------------------- the control


def test_a_rise_the_control_shares_is_not_reported() -> None:
    """Four hours of soaking is four hours during which the machine also
    changed. A rise in both is the afternoon, not the subject."""
    samples = tuple(
        Sample(index=index, elapsed=float(index), metrics={SECONDS: 1.0 + index})
        for index in range(6)
    )
    both_rising = Soak(
        samples=samples,
        trends=_fit_series(samples),
        duration=6.0,
        reference=_fit_series(samples),
    )

    assert both_rising.trends[SECONDS].ramping
    assert not both_rising.ramping()
    assert both_rising.controlled


def test_a_rise_only_the_subject_shows_is_reported() -> None:
    """The other side of the same rule, so the control is not simply a way of
    never reporting anything."""
    rising = tuple(
        Sample(index=index, elapsed=float(index), metrics={SECONDS: 1.0 + index})
        for index in range(6)
    )
    flat = tuple(
        Sample(index=index, elapsed=float(index), metrics={SECONDS: 1.0}) for index in range(6)
    )
    result = Soak(
        samples=rising,
        trends=_fit_series(rising),
        duration=6.0,
        reference=_fit_series(flat),
    )

    assert [trend.metric for trend in result.ramping()] == [SECONDS]


def test_a_run_without_a_control_says_so() -> None:
    """A ramp with no control is a ramp that cannot be attributed, and after
    four hours that is an expensive thing to discover late."""
    result = soak(Leaky(), duration=DURATION)

    assert not result.controlled
    assert "**No control workload was run**" in result.explanation()
    assert "the same rising line as a leak" in result.explanation()


def test_a_control_workload_is_measured_in_the_same_loop() -> None:
    """Alongside rather than afterwards: a control run after the subject would
    be measuring a different hour."""
    result = soak(Leaky(), duration=DURATION, reference=Steady())

    assert result.controlled
    assert result.reference is not None
    assert SECONDS in result.reference


# ------------------------------------------- AC 4: the duration and its cap


def test_a_duration_beyond_the_cap_is_refused_rather_than_shortened() -> None:
    """AC 4. Quietly running six hours when eight were asked for produces a soak
    that may not have reached the ramp, reported as *no ramp* — which is the
    manufactured exclusion ADR 013 exists to prevent."""
    with pytest.raises(LongitudinalError, match="refused rather than shortened"):
        soak(Steady(), duration=MAXIMUM_DURATION_SECONDS + 1)


def test_a_duration_at_the_cap_is_allowed() -> None:
    """The boundary, so the cap is a limit rather than an off-by-one."""
    assert MAXIMUM_DURATION_SECONDS == 6 * 60 * 60


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_a_soak_needs_a_positive_duration(duration: float) -> None:
    with pytest.raises(LongitudinalError, match="positive duration"):
        soak(Steady(), duration=duration)


def test_a_soak_too_short_to_fit_a_trend_is_refused() -> None:
    """A trend through two points is a line drawn through them. Refusing is
    better than reporting the slope of one."""
    slow = lambda: time.sleep(0.05)  # noqa: E731

    with pytest.raises(LongitudinalError, match="at least 3"):
        soak(slow, duration=0.06)


# --------------------------------- AC 3: the deployment-model gate


def test_the_primitive_is_withheld_from_something_that_exits() -> None:
    """AC 3. A ramp is cost accumulating in a process that stays up, so there is
    nothing for it to accumulate in on a CLI tool — which §5 names explicitly."""
    primitive = REGISTRY.get("longitudinal.soak")
    cli_tool = ProjectProfile(
        capabilities={Capability.EVENT_COUNTERS},
        facts={ProjectFact.LONG_RUNNING_PROCESS: False},
    )

    verdict = primitive.verdict(cli_tool)

    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert "never run this on a CLI tool" in verdict.reason


def test_the_primitive_is_offered_to_a_long_running_process() -> None:
    primitive = REGISTRY.get("longitudinal.soak")
    server = ProjectProfile(
        capabilities={Capability.EVENT_COUNTERS},
        facts={ProjectFact.LONG_RUNNING_PROCESS: True},
    )

    assert primitive.verdict(server).applicability is Applicability.APPLICABLE


def test_an_unestablished_deployment_model_does_not_get_it_either() -> None:
    """The most expensive primitive here, so the cost of running it on a guess is
    the highest of any of them."""
    primitive = REGISTRY.get("longitudinal.soak")

    verdict = primitive.verdict(ProjectProfile(capabilities={Capability.EVENT_COUNTERS}))

    assert verdict.applicability is Applicability.UNDETERMINED


def test_the_selection_refuses_it_by_name_with_the_reason() -> None:
    selection = REGISTRY.select(
        ProjectProfile(
            capabilities=frozenset(Capability),
            facts={ProjectFact.LONG_RUNNING_PROCESS: False},
        )
    )

    with pytest.raises(PrimitiveUnavailableError, match="never run this on a CLI tool"):
        selection.get("longitudinal.soak")


def test_it_is_declared_the_most_expensive_cost_class() -> None:
    """`01-primitives.md` §5 calls it the most expensive primitive, and the
    registry's cost band is what an agent reads before choosing it."""
    assert REGISTRY.get("longitudinal.soak").cost.value == "hours"

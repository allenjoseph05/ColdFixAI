"""Cost and gain are different quantities, and only one says what to work on.

S-3.14. `01-primitives.md` §8's decisive datum: the function responsible for
SQLite's 25% gain accounted for about **0.15% of runtime**. A profiler ranks by
cost and would never have surfaced it. Its worked example is starker — two
functions of similar profile weight where optimizing one yields at most 4.5% and
the other yields exactly zero.

So the tests here are built around that shape rather than around the arithmetic:
a component that takes almost no time but that everything waits behind, and a
component that takes real time and that nothing waits behind. A primitive that
ranked them by cost would get both backwards.

The gate gets its own attention, because `08-audit.md` F7 is a finding about this
primitive rather than a caveat on it: in single-threaded code the sensitivity is
just the share of runtime, which is ablation's answer reached more slowly, and
presenting it as more than that is the failure. S-3.1's registry is what
withholds it, and the test goes through the registry rather than asserting a
docstring.
"""

from __future__ import annotations

import threading
import time

import pytest

from coldfix.primitives.perturbation import (
    INSENSITIVE_BELOW,
    PerturbationError,
    Point,
    Sensitivity,
    perturb,
    sensitivity_curve,
)
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    Capability,
    PrimitiveUnavailableError,
    ProjectFact,
    ProjectProfile,
)

# Well above Windows' ~15.6ms timer granularity, which made S-3.13 flaky at 20ms.
UNIT = 0.05


class Pipeline:
    """Two stages behind one gate, which is the shape §8's datum has.

    `gate` costs almost nothing and everything queues behind it. `bulk` costs
    real time and nothing waits on it. A profiler ranks `bulk` first; the useful
    answer is the other one.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()

    def gate(self) -> None:
        with self.lock:
            time.sleep(UNIT / 20)

    def bulk(self) -> None:
        time.sleep(UNIT)


def run_bulk(pipeline: Pipeline) -> None:
    """Reach the target through the attribute, not through a captured reference.

    Passing `pipeline.bulk` directly binds the method *before* the substitution,
    so the workload calls the original and the curve comes back flat — which
    reads as "optimizing this would gain nothing". The module's docstring says
    so; this is the shape that says it in code.
    """
    pipeline.bulk()


def stable(cost: float) -> tuple[float, ...]:
    return (cost, cost, cost)


def curve_from(slope: float) -> Sensitivity:
    """A `Sensitivity` stated directly, for the tests about what it reports."""
    baseline = 1.0
    points = tuple(
        Point(fraction=fraction, samples=stable(baseline * (1 + slope * fraction)))
        for fraction in (0.0, 0.5, 1.0)
    )
    return Sensitivity(target="Pipeline.gate", points=points, slope=slope, r_squared=1.0)


# ------------------------------------------- AC 1: a known fractional slowdown


@pytest.mark.timing
def test_the_target_is_slowed_by_the_fraction_asked_for() -> None:
    """AC 1. Proportional to what the call took, not a constant: a fixed delay
    perturbs a fast call out of all proportion, and the slope of that would be a
    fact about the constant."""
    pipeline = Pipeline()

    unperturbed = perturb(Pipeline, "bulk", lambda: run_bulk(pipeline), 0.0, repetitions=3)
    doubled = perturb(Pipeline, "bulk", lambda: run_bulk(pipeline), 1.0, repetitions=3)

    assert unperturbed.cost == pytest.approx(UNIT, rel=0.4)
    assert doubled.cost == pytest.approx(2 * UNIT, rel=0.4)


def test_correctness_is_preserved_while_the_target_is_perturbed() -> None:
    """The row of §8's table that separates this from ablation, and the reason
    this primitive needs no diagnostic worktree: the target still runs and still
    returns what it returned."""

    class Serializer:
        def render(self, rows: int) -> list[int]:
            return list(range(rows))

    seen: list[list[int]] = []
    serializer = Serializer()

    perturb(Serializer, "render", lambda: seen.append(serializer.render(4)), 1.0, repetitions=2)

    assert seen == [[0, 1, 2, 3], [0, 1, 2, 3]]


def test_the_target_is_restored_afterwards_and_the_restoration_is_verified() -> None:
    """S-3.10's substitution does this, which is why this module does not do it
    again: a perturbation left installed slows every measurement taken
    afterwards, silently."""
    original = Pipeline.bulk
    pipeline = Pipeline()

    perturb(Pipeline, "bulk", pipeline.bulk, 1.0, repetitions=2)

    assert Pipeline.bulk is original


def test_a_negative_slowdown_is_refused() -> None:
    """Speeding a component up is the question, not the operation. It is
    answered by extrapolating the curve, not by injecting a negative delay."""
    pipeline = Pipeline()

    with pytest.raises(PerturbationError, match="cannot be negative"):
        perturb(Pipeline, "bulk", pipeline.bulk, -0.5)


def test_an_inherited_attribute_is_refused() -> None:
    """S-1.3's rule again: patching where a name is found rather than where it
    is stored changes which objects are affected."""

    class Subclass(Pipeline):
        pass

    with pytest.raises(PerturbationError, match="not defined on"):
        perturb(Subclass, "bulk", lambda: None, 0.5)


# ---------------------------------------------- AC 3: a curve, not a point


def test_the_result_is_a_curve_of_several_points() -> None:
    """AC 3. One perturbation gives a difference with no way to tell a linear
    response from a threshold — and the extrapolation to a speedup is only
    meaningful if the response is linear."""
    pipeline = Pipeline()

    result = sensitivity_curve(
        Pipeline, "bulk", lambda: run_bulk(pipeline), (0.0, 0.5, 1.0), repetitions=3
    )

    assert [point.fraction for point in result.points] == [0.0, 0.5, 1.0]
    assert result.r_squared > 0.9


@pytest.mark.timing
def test_a_component_the_whole_workload_waits_on_has_a_slope_near_one() -> None:
    """The serial case, which is also F7's point: every millisecond added to it
    is a millisecond added to the total, so the sensitivity is its share of
    runtime and this told us nothing ablation would not have."""
    pipeline = Pipeline()

    result = sensitivity_curve(
        Pipeline, "bulk", lambda: run_bulk(pipeline), (0.0, 0.5, 1.0), repetitions=3
    )

    assert result.slope == pytest.approx(1.0, abs=0.25)
    assert result.sensitive


@pytest.mark.timing
def test_a_component_whose_delay_is_absorbed_is_reported_as_insensitive() -> None:
    """§8's worked example, where optimizing one of two similarly-weighted
    functions yields **exactly zero**.

    Note what it takes to build this case: the target has to run *alongside*
    something longer, so its delay is absorbed rather than added. In serial code
    it cannot exist — every millisecond added to a component is a millisecond
    added to the total, so the sensitivity is the share of runtime and nothing
    else. That impossibility is `08-audit.md` F7 stated from the other side, and
    it is why the primitive is gated on concurrency rather than merely warned
    about.
    """
    pipeline = Pipeline()

    def workload() -> None:
        # The gate runs in parallel with work that takes far longer, so making
        # the gate slower does not move the finish at all.
        worker = threading.Thread(target=pipeline.gate)
        worker.start()
        time.sleep(UNIT * 2)
        worker.join()

    result = sensitivity_curve(Pipeline, "gate", workload, (0.0, 0.5, 1.0), repetitions=3)

    assert not result.sensitive
    assert "would gain nothing measurable" in result.explanation()


@pytest.mark.timing
def test_in_serial_code_the_sensitivity_is_just_the_share_of_runtime() -> None:
    """F7's finding, measured rather than asserted.

    The gate is a twentieth of this workload and its sensitivity comes back as
    about a twentieth. That is ablation's answer reached more slowly, which is
    exactly why presenting this primitive as generally applicable would be
    wrong — and why the registry withholds it from synchronous projects.
    """
    pipeline = Pipeline()

    def serial_workload() -> None:
        pipeline.gate()
        time.sleep(UNIT)

    result = sensitivity_curve(Pipeline, "gate", serial_workload, (0.0, 0.5, 1.0), repetitions=3)

    share_of_runtime = (UNIT / 20) / (UNIT + UNIT / 20)
    assert result.slope == pytest.approx(share_of_runtime, abs=0.03)


@pytest.mark.timing
def test_a_cheap_component_can_be_more_worth_optimizing_than_an_expensive_one() -> None:
    """§8's decisive datum, in the smallest form that is really it.

    `gate` costs a twentieth of what `bulk` costs. Under concurrency everything
    queues behind `gate`, so a delay to it reaches the finish and a delay to
    `bulk` is partly absorbed. Ranking by cost puts `bulk` first; ranking by
    sensitivity puts `gate` first, and only the second answers *what should I
    work on*.
    """
    pipeline = Pipeline()

    def concurrent_workload() -> None:
        threads = [threading.Thread(target=pipeline.gate) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    gate = sensitivity_curve(
        Pipeline, "gate", concurrent_workload, (0.0, 0.5, 1.0), repetitions=3, name="the gate"
    )

    assert gate.slope > INSENSITIVE_BELOW
    assert gate.sensitive


def test_a_curve_needs_more_than_two_points() -> None:
    with pytest.raises(PerturbationError, match="rather than a line drawn through them"):
        sensitivity_curve(Pipeline, "bulk", lambda: None, (0.0, 1.0))


def test_a_curve_needs_an_unperturbed_point() -> None:
    """There is nothing for the perturbed points to be a fraction of otherwise."""
    with pytest.raises(PerturbationError, match="no unperturbed point"):
        sensitivity_curve(Pipeline, "bulk", lambda: None, (0.25, 0.5, 1.0))


# --------------------------- the speedup is an extrapolation and says so


def test_the_predicted_gain_is_the_slope_read_on_the_other_side_of_zero() -> None:
    """Which is Coz's method, and is an extrapolation past the measured range —
    the same thing S-3.12 withholds a peak for."""
    result = curve_from(slope=0.4)

    assert result.predicted_gain(0.5) == pytest.approx(0.2)
    assert "**That last number is an extrapolation**" in result.explanation()


def test_the_explanation_carries_the_fit_quality_next_to_the_prediction() -> None:
    """The extrapolation is sound while the response is linear, and r² is how
    much that assumption is worth."""
    result = curve_from(slope=0.4)

    assert "r²=" in result.explanation()


def test_an_insensitive_component_is_a_finding_a_profiler_cannot_produce() -> None:
    """A profiler ranks by cost, and this component may have plenty of it."""
    result = curve_from(slope=0.0)

    assert not result.sensitive
    assert "a profiler cannot produce" in result.explanation()


# ------------------------------- AC 2: the gate, through the registry


def test_the_primitive_is_withheld_from_single_threaded_code() -> None:
    """AC 2, and it is checked through S-3.1's registry rather than by reading a
    docstring — the selection is what actually decides whether an agent is
    offered this."""
    primitive = REGISTRY.get("perturbation.sensitivity")
    synchronous = ProjectProfile(
        capabilities={Capability.STATE_RESET},
        facts={ProjectFact.RUNS_CONCURRENT_CODE: False},
    )

    verdict = primitive.verdict(synchronous)

    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert "collapses into ablation" in verdict.reason


def test_the_primitive_is_offered_to_concurrent_code() -> None:
    """The control. A gate that withheld it from everything would be a primitive
    nobody can use."""
    primitive = REGISTRY.get("perturbation.sensitivity")
    concurrent = ProjectProfile(
        capabilities={Capability.STATE_RESET},
        facts={ProjectFact.RUNS_CONCURRENT_CODE: True},
    )

    assert primitive.verdict(concurrent).applicability is Applicability.APPLICABLE


def test_a_project_whose_concurrency_is_unknown_does_not_get_it_either() -> None:
    """S-3.1's third answer doing its job. Nobody established whether this
    subject runs concurrent code, and offering the primitive on that basis would
    produce a sensitivity that is ablation's answer wearing another name."""
    primitive = REGISTRY.get("perturbation.sensitivity")

    verdict = primitive.verdict(ProjectProfile(capabilities={Capability.STATE_RESET}))

    assert verdict.applicability is Applicability.UNDETERMINED


def test_the_selection_withholds_it_rather_than_the_module() -> None:
    """The registry is where this is enforced, so an agent asking for the
    primitive by name on a synchronous project gets a refusal that carries the
    reason."""
    selection = REGISTRY.select(
        ProjectProfile(
            capabilities=frozenset(Capability),
            facts={ProjectFact.RUNS_CONCURRENT_CODE: False},
        )
    )

    with pytest.raises(PrimitiveUnavailableError, match="collapses into ablation"):
        selection.get("perturbation.sensitivity")

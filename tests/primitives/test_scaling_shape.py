"""The same volume, spread three ways, and a defect only the third one finds.

S-3.3. The story's note is the whole test file in one sentence: *an N+1 that
costs milliseconds at three related rows and minutes at three thousand is
invisible if every generated parent has exactly three children.*

Two things have to be true for the second axis to be worth anything, and both
are easy to ship without:

**The volume must actually be held constant.** A comparison where the shape
changed *and* the row count changed attributes nothing. Every allocation here is
asserted to have the same total and the same number of parents, and the sweeps
assert that the workload returned the same rows under all three shapes.

**The three shapes must be three shapes.** Three names for one distribution
would pass every test that only checks the machinery, so they are compared on
where their mass sits, and a defect is required to be visible under one and
invisible under another.

The subject is `fixtures/planted/skew.py`, whose defect costs `Σ k(k-1)/2` and
whose control costs `Σ k`. The second is *provably* shape-independent — `Σ k` is
the volume — which is what makes it a control rather than another example.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.primitives.measurement import (
    CacheControl,
    CacheControlError,
    MetricKind,
)
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.primitives.scaling import (
    Allocation,
    Distribution,
    ScaleSweepError,
    ScalingResult,
    ShapeComparison,
    ShapeMeasurement,
    allocate,
    compare_shapes,
)
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted import skew
from fixtures.planted.store import Store

COMPARISONS = "skew.titles_match"
PER_CHILD = "skew.normalize"

# Twenty parents holding two hundred children, which is ten each when the
# generator is uniform. Small enough to run in milliseconds, uneven enough that
# the shapes separate — and the separation grows with the parent count, so a real
# subject with thousands of parents shows far more than this.
GROUPS = 20
TOTAL = 200


@pytest.fixture
def skew_counters() -> Iterator[None]:
    register_hook(COMPARISONS, calls_to(skew, "titles_match"))
    register_hook(PER_CHILD, calls_to(skew, "normalize"))
    try:
        yield
    finally:
        unregister_hook(COMPARISONS)
        unregister_hook(PER_CHILD)


class RecordingReset(ResetMechanism):
    """Restores the state as of `begin()`, as a real rollback does."""

    strategy: ClassVar[ResetStrategy] = ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self.events: list[str] = []
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        self.events.append("prepare")

    def begin(self) -> None:
        self.events.append("begin")
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        self.events.append("reset")
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)


@dataclass
class Subject:
    """A store seeded from an allocation, running the quadratic defect."""

    store: Store = field(default_factory=Store)
    shapes: list[Distribution] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)

    def seed(self, allocation: Allocation) -> None:
        self.shapes.append(allocation.distribution)
        self.store = skew.build_shaped_store(allocation.counts)

    def invoke(self) -> object:
        return skew.deduplicate_pairwise(self.store)

    def process_identity(self) -> str:
        identity = f"container-{len(self.processes)}"
        self.processes.append(identity)
        return identity

    def guard_counters(self) -> Mapping[str, float]:
        return {"rows_returned": float(self.store.rows_returned)}


@dataclass
class ControlSubject(Subject):
    """The same data, deduplicated with a key instead of a pairwise scan."""

    def invoke(self) -> object:
        return skew.deduplicate_by_key(self.store)


def compare(subject: Subject, **overrides: Any) -> ShapeComparison:
    arguments: dict[str, Any] = {
        "seed": subject.seed,
        "invoke": subject.invoke,
        "reset": VerifiedReset(
            mechanism=RecordingReset(subject),
            report=VerificationReport(
                strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES, cycles=10
            ),
        ),
        "groups": GROUPS,
        "total": TOTAL,
        "counters": [COMPARISONS, PER_CHILD],
        "extra_counters": subject.guard_counters,
        "process_identity": subject.process_identity,
    }
    arguments.update(overrides)
    result: ShapeComparison = compare_shapes(**arguments)
    return result


# ------------------------------------------------- AC 1: the three distributions


@pytest.mark.parametrize("distribution", list(Distribution))
def test_every_shape_spends_the_whole_volume_and_no_more(distribution: Distribution) -> None:
    """AC 2, at the source. A comparison where the volume also moved attributes
    nothing to shape, and the arithmetic that guarantees it is here rather than
    in the caller."""
    allocation = allocate(distribution, groups=GROUPS, total=TOTAL)

    assert allocation.total == TOTAL
    assert allocation.groups == GROUPS


@pytest.mark.parametrize("distribution", list(Distribution))
@pytest.mark.parametrize(("groups", "total"), [(3, 4), (7, 100), (13, 1000), (99, 100)])
def test_the_totals_hold_for_awkward_divisions(
    distribution: Distribution, groups: int, total: int
) -> None:
    """Largest-remainder apportionment, tested where rounding is worst: totals
    that do not divide, and a spread of exactly one child per parent."""
    allocation = allocate(distribution, groups=groups, total=total)

    assert allocation.total == total
    assert allocation.groups == groups
    assert min(allocation.counts) >= 1


@pytest.mark.parametrize("distribution", list(Distribution))
def test_no_shape_may_empty_a_parent(distribution: Distribution) -> None:
    """A shape that leaves parents childless varies the parent count too, which
    is a second variable nobody asked for."""
    allocation = allocate(distribution, groups=10, total=10)

    assert allocation.counts == (1,) * 10


def test_fewer_children_than_parents_is_refused() -> None:
    with pytest.raises(ScaleSweepError):
        allocate(Distribution.UNIFORM, groups=10, total=9)


def test_the_three_shapes_are_actually_three_shapes() -> None:
    """The test that stops this story shipping three names for one distribution.

    Separated two ways. On mass: the largest tenth of parents holds a tenth of
    the children under uniform, most of them under a power law, and nearly all of
    them under a long tail. On spectrum: a power law decays smoothly through
    middling parents, a long tail is bimodal — a few enormous and the rest at the
    floor.
    """
    uniform = allocate(Distribution.UNIFORM, groups=GROUPS, total=TOTAL)
    power_law = allocate(Distribution.POWER_LAW, groups=GROUPS, total=TOTAL)
    long_tail = allocate(Distribution.LONG_TAIL, groups=GROUPS, total=TOTAL)

    assert uniform.head_mass < power_law.head_mass < long_tail.head_mass
    assert uniform.largest < power_law.largest < long_tail.largest
    # Smooth against bimodal: the power law has middling parents between its
    # largest and its smallest, and the long tail has none.
    assert len(set(power_law.counts)) > len(set(long_tail.counts)) == 2


def test_a_uniform_allocation_is_as_even_as_the_arithmetic_allows() -> None:
    """Not "roughly even" — no two parents differ by more than one child, which
    is what makes it the reference the others are measured against."""
    allocation = allocate(Distribution.UNIFORM, groups=7, total=100)

    assert max(allocation.counts) - min(allocation.counts) <= 1


def test_the_allocation_is_the_same_on_every_run() -> None:
    """No random number generator anywhere. A measurement taken today has to be
    comparable with one taken next month, and S-5.1 will key a replay cache on
    the fixture."""
    first = allocate(Distribution.POWER_LAW, groups=GROUPS, total=TOTAL)
    second = allocate(Distribution.POWER_LAW, groups=GROUPS, total=TOTAL)

    assert first.counts == second.counts


# ---------------------------------------- AC 4: the defect uniform data hides


def test_a_skew_dependent_defect_is_invisible_under_uniform_data(
    skew_counters: None,
) -> None:
    """AC 4, and the reason the second axis exists.

    Under uniform data every parent holds ten children and costs 45 comparisons,
    for 900 in total — an unremarkable number that no volume sweep at any size
    would flag, because it stays 45 per parent however much data arrives. The
    same 200 children cost more than twice that under a power law and nine times
    it under a long tail, where two parents hold 91 children each and one request
    pays 4,095 comparisons on its own.
    """
    result = compare(Subject())

    uniform = result.under(Distribution.UNIFORM).adjusted[COMPARISONS]
    power_law = result.under(Distribution.POWER_LAW).adjusted[COMPARISONS]
    long_tail = result.under(Distribution.LONG_TAIL).adjusted[COMPARISONS]

    assert uniform == 900
    assert power_law > 2 * uniform
    assert long_tail > 8 * uniform
    assert result.sensitivity(COMPARISONS) > 8


def test_uniform_is_the_cheapest_shape_for_this_defect_class(skew_counters: None) -> None:
    """Not a lucky parameterization. `Σ k(k-1)/2` is minimized when every parent
    holds the same number, so a uniform fixture is provably the blindest shape
    for any per-parent superlinear cost."""
    result = compare(Subject())

    costs = {m.distribution: m.adjusted[COMPARISONS] for m in result.measurements}

    assert costs[Distribution.UNIFORM] == min(costs.values())


def test_a_shape_independent_workload_is_not_flagged(skew_counters: None) -> None:
    """The control, and the reason the test above means anything.

    Deduplicating by key costs one operation per child, so its total is the
    volume itself — identical under all three shapes, by construction rather than
    by measurement. A comparison that reported skew sensitivity here would be
    reporting the skew, not a defect.
    """
    result = compare(ControlSubject())

    costs = {m.adjusted[PER_CHILD] for m in result.measurements}

    assert costs == {float(TOTAL)}
    assert result.sensitivity(PER_CHILD) == 1.0


def test_a_framework_floor_dilutes_the_ratio_until_it_is_subtracted(
    skew_counters: None,
) -> None:
    """The baseline matters differently on this axis, and still matters.

    A volume sweep loses its *exponent* to an unsubtracted floor. A shape
    comparison loses its *ratio*: a fixed 2,000 comparisons charged by something
    unrelated to the data drags a 9.1x difference down to 3.5x — always in the
    direction that makes a real skew sensitivity look survivable.
    """

    @dataclass
    class FloorSubject(Subject):
        def invoke(self) -> object:
            for index in range(2000):
                skew.titles_match(f"floor-{index}", "floor")
            return skew.deduplicate_pairwise(self.store)

    result = compare(FloorSubject())

    raw = (
        max(m.raw[COMPARISONS] for m in result.measurements)
        / result.under(Distribution.UNIFORM).raw[COMPARISONS]
    )

    assert result.baseline[COMPARISONS] == 2000
    assert result.sensitivity(COMPARISONS) > 2 * raw


def test_a_cost_that_is_zero_under_uniform_data_is_reported_as_infinite() -> None:
    """The strongest form the finding takes, and the one that would otherwise be
    a division by zero.

    "Invisible under uniform data" is sometimes literal: a per-parent cost
    triggered only above a threshold — a chunked fetch, a pagination boundary —
    charges nothing at all until one parent is large enough.
    """
    comparison = ShapeComparison(
        groups=GROUPS,
        total=TOTAL,
        baseline={COMPARISONS: 0.0},
        measurements=(
            ShapeMeasurement(
                allocation=allocate(Distribution.UNIFORM, groups=GROUPS, total=TOTAL),
                raw={COMPARISONS: 0.0},
                adjusted={COMPARISONS: 0.0},
            ),
            ShapeMeasurement(
                allocation=allocate(Distribution.LONG_TAIL, groups=GROUPS, total=TOTAL),
                raw={COMPARISONS: 12.0},
                adjusted={COMPARISONS: 12.0},
            ),
        ),
        kinds={COMPARISONS: MetricKind.COUNT},
        reset_strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        cache_control=CacheControl.FRESH_PROCESS,
    )

    assert comparison.sensitivity(COMPARISONS) == float("inf")


def test_a_metric_nothing_charges_under_any_shape_is_not_sensitive() -> None:
    """Zero against zero is not infinite sensitivity. It is a metric this
    workload does not spend, and reporting it as a finding would manufacture
    one."""
    flat = ShapeComparison(
        groups=GROUPS,
        total=TOTAL,
        baseline={COMPARISONS: 0.0},
        measurements=tuple(
            ShapeMeasurement(
                allocation=allocate(distribution, groups=GROUPS, total=TOTAL),
                raw={COMPARISONS: 0.0},
                adjusted={COMPARISONS: 0.0},
            )
            for distribution in (Distribution.UNIFORM, Distribution.LONG_TAIL)
        ),
        kinds={COMPARISONS: MetricKind.COUNT},
        reset_strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        cache_control=CacheControl.FRESH_PROCESS,
    )

    assert flat.sensitivity(COMPARISONS) == 1.0


def test_the_volume_really_was_held_constant(skew_counters: None) -> None:
    """Measured, not just allocated. The workload returned the same rows under
    every shape, which is what makes the difference in comparisons attributable
    to shape at all."""
    result = compare(Subject())

    rows = {m.adjusted["rows_returned"] for m in result.measurements}

    assert len(rows) == 1


# --------------------------------------------- AC 3: the shape is recorded


def test_every_measurement_records_the_shape_it_was_taken_under(
    skew_counters: None,
) -> None:
    """AC 3. A cost that depends on shape is not comparable with one measured
    under a different shape, and a result that does not say which cannot be
    checked."""
    result = compare(Subject())

    assert [m.distribution for m in result.measurements] == list(Distribution)
    assert all(m.allocation.total == TOTAL for m in result.measurements)


def test_the_counts_themselves_are_recorded_not_just_the_name(
    skew_counters: None,
) -> None:
    """The name is a label; the allocation is the fixture. `largest` is where a
    per-parent cost is actually paid, and it is the number that explains why one
    shape cost more than another."""
    result = compare(Subject())

    power_law = result.under(Distribution.POWER_LAW).allocation

    assert sum(power_law.counts) == TOTAL
    assert (
        power_law.largest > 4 * allocate(Distribution.UNIFORM, groups=GROUPS, total=TOTAL).largest
    )


def test_the_subject_was_seeded_once_per_shape_plus_the_baseline(
    skew_counters: None,
) -> None:
    subject = Subject()

    compare(subject)

    assert subject.shapes == [Distribution.UNIFORM, *list(Distribution)]


def test_a_volume_sweep_records_its_shape_too(skew_counters: None) -> None:
    """S-3.2's result gained the field in this story, because AC 3 says *every*
    measurement — and a growth curve measured under uniform data is exactly the
    result the note warns is blind."""
    assert "distribution" in ScalingResult.__dataclass_fields__


# --------------------------------------------------- shared machinery, reused


def test_a_comparison_needs_something_to_compare(skew_counters: None) -> None:
    with pytest.raises(ScaleSweepError):
        compare(Subject(), distributions=[Distribution.UNIFORM])


def test_a_shape_cannot_be_measured_twice(skew_counters: None) -> None:
    with pytest.raises(ScaleSweepError):
        compare(Subject(), distributions=[Distribution.UNIFORM, Distribution.UNIFORM])


def test_the_baseline_is_measured_with_no_rows_at_all(skew_counters: None) -> None:
    """With nothing to shape, the three distributions agree — so one baseline
    serves all of them, and the framework's fixed cost is out of every ratio."""
    result = compare(Subject())

    assert result.baseline[COMPARISONS] == 0.0
    assert result.under(Distribution.UNIFORM).raw[COMPARISONS] == 900


def test_a_shape_comparison_refuses_without_cache_control(skew_counters: None) -> None:
    """The same refusal a volume sweep makes, for the same reason: a cache
    carried from one measurement to the next flattens the difference between
    them, and no comparison of results can detect it."""
    with pytest.raises(CacheControlError):
        compare(Subject(), process_identity=None)


def test_the_shape_primitive_is_registered() -> None:
    primitive = REGISTRY.get("scaling.shape")

    assert primitive.required_capabilities == {
        Capability.FIXTURE_SHAPING,
        Capability.STATE_RESET,
    }
    assert primitive.run is compare_shapes

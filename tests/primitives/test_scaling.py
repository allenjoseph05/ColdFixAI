"""Three ways a scaling sweep reports "not the database" about a database defect.

S-3.2. The story's note names baseline offset, lazy evaluation and a warm cache,
and says to test each. They are one failure wearing three hats: every one of them
flattens a metric that really grows, and a flat metric is not an error message —
it is `queries flat across 100x scale`, which this system publishes as a finding
and a human acts on.

So each failure mode gets two tests here: one that reproduces the wrong answer
from the same measurements, and one that shows the mechanism producing the right
one. The pair is the point. A test that only shows the correct result passes
just as well against an implementation where the failure mode was never possible,
and this file needs to be evidence that it was.

The subject is the in-memory store from `tests/fixtures/planted`, whose counts
are exact by construction. Its `DECOY_FIXED_QUERIES` floor is the shape S-0.3
measured on netbox — a real mature system's real fixed cost — which is what makes
the baseline-offset case a realistic measurement rather than a contrived one.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from coldfix.bench.counting import calls_to, count, register_hook, unregister_hook
from coldfix.bench.stats import Growth, fit_growth
from coldfix.primitives.measurement import (
    BLOCKED_SECONDS,
    CPU_SECONDS,
    MATERIALIZED,
    SECONDS,
    TOTAL_SUFFIX,
    BaselineError,
    CacheControl,
    CacheControlError,
    MetricKind,
    MetricSetError,
)
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.primitives.scaling import (
    Distribution,
    ScaleSweepError,
    scale_volume,
)
from coldfix.primitives.scaling import ScalingResult as Result
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.queries import (
    DECOY_FIXED_QUERIES,
    list_books_n_plus_one,
    list_titles_over_fetching,
)
from fixtures.planted.store import Row, Store, build_store

QUERIES = "store.select"
SCALES = (1, 2, 3)


@pytest.fixture
def query_counter() -> Iterator[None]:
    """Attach the store's `select` as a counted hook for the duration of a test."""
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


# --------------------------------------------------------------- the test double


class RecordingReset(ResetMechanism):
    """A reset that restores the state as it stood when its cycle opened.

    Standing in for `RollbackReset` so these tests need no database, and
    **restoring rather than emptying** is what makes it a faithful double. A real
    rollback undoes work done inside its transaction and nothing else, so
    anything written before `begin()` survives it. A double that wiped the
    subject unconditionally would clean up after a sweep that seeded outside the
    cycle, and the test asserting that seeding happens inside it would pass
    against an implementation where it does not — which is what happened, and is
    why this class looks like this.
    """

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


def verified(mechanism: ResetMechanism) -> VerifiedReset:
    """A `VerifiedReset` around a mechanism, with the passing report it requires."""
    return VerifiedReset(
        mechanism=mechanism,
        report=VerificationReport(strategy=mechanism.strategy, cycles=10),
    )


@dataclass
class Subject:
    """A store that is rebuilt at each volume, and remembers what it was seeded on.

    `seeded_onto` records how many authors were already present each time `seed`
    was called. Every entry must be zero: a non-zero one is data from the
    previous scale point that the reset did not take away, which is the defect
    "reset between each" exists to prevent.
    """

    store: Store = field(default_factory=Store)
    seeded_onto: list[int] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)

    def seed(self, scale: int) -> None:
        self.seeded_onto.append(len(self.store.tables.get("author", [])))
        self.store = build_store(authors=scale, books_per_author=2)

    def invoke(self) -> object:
        """Typed as the sweep sees it — an opaque observation it will drain."""
        return list_books_n_plus_one(self.store)

    def process_identity(self) -> str:
        """A different process for every scale point, as a container would give."""
        identity = f"container-{len(self.processes)}"
        self.processes.append(identity)
        return identity

    def guard_counters(self) -> Mapping[str, float]:
        return {"cells_returned": float(self.store.cells_returned)}


def sweep(subject: Subject, **overrides: Any) -> Result:
    """`scale_volume` with this file's defaults, so a test states only its subject."""
    arguments: dict[str, Any] = {
        "seed": subject.seed,
        "invoke": subject.invoke,
        "reset": verified(RecordingReset(subject)),
        "scales": SCALES,
        # Declared, because `build_store` gives every author the same number of
        # books. S-3.3 is what makes that admission load-bearing: growth measured
        # under uniform data is a statement about uniform data.
        "distribution": Distribution.UNIFORM,
        "counters": [QUERIES],
        "process_identity": subject.process_identity,
    }
    arguments.update(overrides)
    result: Result = scale_volume(**arguments)
    return result


# ------------------------------------------------- AC 1: scale points, with reset


def test_a_sweep_needs_three_points(query_counter: None) -> None:
    """Two points define a line through themselves and say nothing about whether
    it is the right line."""
    with pytest.raises(ScaleSweepError):
        sweep(Subject(), scales=(10, 100))


@pytest.mark.parametrize("scales", [(1, 1, 2), (1, 2, 0), (1, 2, -5)])
def test_a_sweep_refuses_repeated_or_non_positive_volumes(
    scales: tuple[int, ...], query_counter: None
) -> None:
    """A repeat measures the same volume twice and fits nothing extra; zero is
    the baseline, which is measured separately and subtracted."""
    with pytest.raises(ScaleSweepError):
        sweep(Subject(), scales=scales)


def test_every_scale_point_is_seeded_onto_an_empty_subject(query_counter: None) -> None:
    """AC 1. The reset is not a step between points that could be skipped —
    seeding happens inside the cycle, so one point's data cannot reach the next."""
    subject = Subject()

    sweep(subject)

    assert subject.seeded_onto == [0, 0, 0, 0]  # the N=0 baseline, then three points


def test_seeding_happens_inside_the_reset_cycle(query_counter: None) -> None:
    """Ordering, not just outcome: begin, work, reset — four times over."""
    subject = Subject()
    mechanism = RecordingReset(subject)

    sweep(subject, reset=verified(mechanism))

    assert mechanism.events == ["begin", "reset"] * 4


def test_the_strategy_that_reset_the_state_is_recorded(query_counter: None) -> None:
    """Two runs reset by different strategies are not the same experiment."""
    result = sweep(Subject())

    assert result.reset_strategy is ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES


# --------------------------------------------------------- AC 2: every metric fits


def test_every_recorded_metric_is_fitted(query_counter: None) -> None:
    """AC 2. Including the ones this module produces itself, and the guard
    counters the caller supplied."""
    subject = Subject()

    result = sweep(subject, extra_counters=subject.guard_counters)

    # Every counter contributes two metrics: its event count and the sum of the
    # amounts recorded (S-3.6). For a hook that only counts they agree, and that
    # agreement is a fact about the hook rather than a duplicate.
    assert set(result.metric_names()) == {
        SECONDS,
        CPU_SECONDS,
        BLOCKED_SECONDS,
        MATERIALIZED,
        QUERIES,
        f"{QUERIES}{TOTAL_SUFFIX}",
        "cells_returned",
    }
    assert all(name in result.fits for name in result.metric_names())


def test_a_duration_is_marked_as_one_sample_and_a_count_is_not(query_counter: None) -> None:
    """S-0.4 put the timing noise floor at ~20 ms, about 6% of a 350 ms endpoint.
    A reader who cannot tell which column is which will read a 2% difference off
    the one that cannot carry it."""
    result = sweep(Subject())

    assert result.kinds[SECONDS] is MetricKind.DURATION
    assert result.kinds[QUERIES] is MetricKind.COUNT


def test_a_defect_the_query_count_cannot_see_is_still_fitted(query_counter: None) -> None:
    """`CLAUDE.md`: guard counters on every metric — which is only worth anything
    if every metric is fitted rather than the interesting one.

    Over-fetching issues exactly one query at every volume, identical to the
    control, while dragging back a payload that grows with the data. A sweep that
    fitted the query count alone would report this workload as flat and clean.
    """

    @dataclass
    class OverFetchSubject(Subject):
        def invoke(self) -> list[str]:
            return list_titles_over_fetching(self.store)

    subject = OverFetchSubject()

    result = sweep(subject, extra_counters=subject.guard_counters)

    assert result.fits[QUERIES].growth is Growth.CONSTANT
    assert result.fits["cells_returned"].growth is Growth.LINEAR


def test_a_metric_present_at_one_volume_and_absent_at_another_is_refused(
    query_counter: None,
) -> None:
    """Dropping it would publish a sweep that silently covered less than it claims."""
    seen: list[int] = []

    def unstable() -> Mapping[str, float]:
        seen.append(1)
        return {} if len(seen) == 1 else {"appears_later": 1.0}

    with pytest.raises(MetricSetError):
        sweep(Subject(), extra_counters=unstable)


def test_an_extra_counter_cannot_overwrite_a_measured_metric(query_counter: None) -> None:
    with pytest.raises(MetricSetError):
        sweep(Subject(), extra_counters=lambda: {SECONDS: 0.0})


# ------------------------------------------------ AC 3: the framework baseline


def endpoint_behind_a_framework_floor(store: Store) -> list[Row]:
    """An N+1 endpoint carrying a realistic fixed cost.

    The floor is S-0.3's netbox measurement — around 35 queries before the
    endpoint's own work begins, for sessions, permissions and middleware. It is
    exactly what makes the N+1 underneath it invisible to a sweep that does not
    subtract it.
    """
    for decade in range(DECOY_FIXED_QUERIES):
        store.select("author", where=("born", 1900 + decade))
    return list_books_n_plus_one(store)


@dataclass
class FloorSubject(Subject):
    def invoke(self) -> list[Row]:
        return endpoint_behind_a_framework_floor(self.store)


def test_a_framework_floor_hides_linear_growth_when_it_is_not_subtracted(
    query_counter: None,
) -> None:
    """The failure mode, reproduced from the sweep's own raw numbers.

    36, 37, 38, 39 queries across volumes 1, 2, 3 has a power-law exponent of
    0.05 — constant, published as *not the database*. The endpoint issues one
    query per row.
    """
    subject = FloorSubject()

    result = sweep(subject)
    raw = fit_growth(
        [float(p.scale) for p in result.points], [p.raw[QUERIES] for p in result.points]
    )

    assert raw.growth is Growth.CONSTANT


def test_subtracting_the_baseline_recovers_the_growth_underneath_it(
    query_counter: None,
) -> None:
    """AC 3, and the same measurements as the test above.

    The subtraction does not change the slope of the straight line — which is
    why skipping it looks harmless — it changes the exponent, and the exponent is
    what the growth classification rests on.
    """
    result = sweep(FloorSubject())

    assert result.baseline[QUERIES] == DECOY_FIXED_QUERIES + 1
    assert result.fits[QUERIES].growth is Growth.LINEAR
    assert result.fits[QUERIES].slope == pytest.approx(1.0)


def test_the_baseline_is_measured_the_same_way_as_every_other_point(
    query_counter: None,
) -> None:
    """A baseline taken by a different route is a measurement of a different
    program. It gets its own reset cycle and its own seeding call at N=0."""
    subject = Subject()
    mechanism = RecordingReset(subject)

    sweep(subject, reset=verified(mechanism))

    assert len(subject.seeded_onto) == len(SCALES) + 1
    assert mechanism.events[:2] == ["begin", "reset"]


def test_a_workload_that_cannot_run_empty_fails_loudly(query_counter: None) -> None:
    """Rather than skipping the subtraction. A sweep with no baseline is not a
    weaker sweep — its exponents are wrong by an unknown amount."""

    @dataclass
    class NeedsData(Subject):
        def invoke(self) -> list[Row]:
            authors = self.store.select("author")
            return [authors[0]]

    with pytest.raises(BaselineError) as raised:
        sweep(NeedsData())

    assert isinstance(raised.value.__cause__, IndexError)


# ------------------------------------------------------- AC 4: lazy results


def lazy_titles(store: Store) -> Iterator[Row]:
    """The same N+1, deferred. Nothing runs until something iterates it."""
    for author in store.select("author"):
        yield from store.select("book", where=("author_id", author["id"]))


@dataclass
class LazySubject(Subject):
    def invoke(self) -> Iterator[Row]:
        return lazy_titles(self.store)


def test_an_undrained_lazy_result_counts_nothing_at_all(query_counter: None) -> None:
    """The failure mode, on its own, without the sweep.

    The workload returns, the counter has seen nothing, and the clock stops
    before any work happens. Every scale point reads zero, which fits as
    constant, which publishes as an exclusion.
    """
    store = build_store(authors=5, books_per_author=2)

    with count(QUERIES) as tally:
        lazy_titles(store)

    assert tally.events == 0


def test_a_lazy_result_is_forced_before_the_measurement_stops(query_counter: None) -> None:
    """AC 4. The same workload, measured by the sweep."""
    result = sweep(LazySubject())

    assert result.fits[QUERIES].growth is Growth.LINEAR
    assert [point.raw[QUERIES] for point in result.points] == [2.0, 3.0, 4.0]


def test_how_many_items_the_forcing_produced_is_itself_recorded(
    query_counter: None,
) -> None:
    """A lazy result that yields nothing and a workload that returns nothing are
    the same number of queries and different findings."""
    result = sweep(LazySubject())

    assert result.baseline[MATERIALIZED] == 0.0
    assert [point.raw[MATERIALIZED] for point in result.points] == [2.0, 4.0, 6.0]


def test_a_mapping_of_lazy_values_is_forced_one_level_deep(query_counter: None) -> None:
    """A view's context is the shape this meets most often."""

    @dataclass
    class ContextSubject(Subject):
        def invoke(self) -> Mapping[str, object]:
            return {"rows": lazy_titles(self.store), "title": "a string, not an item source"}

    result = sweep(ContextSubject())

    assert result.fits[QUERIES].growth is Growth.LINEAR
    # The string in the context is already materialized. Iterating it would add
    # one item per character — expense without information, and a materialized
    # count that says more work happened than did.
    assert [point.raw[MATERIALIZED] for point in result.points] == [2.0, 4.0, 6.0]


# ---------------------------------------------------------- AC 5: warm caches


@dataclass
class CachingSubject(Subject):
    """A workload with a per-author cache that outlives a scale point.

    The cache is keyed on author id, and every volume seeds ids from zero, so the
    entries the smaller volume warmed are still valid at the larger one. Nothing
    about it is unusual — it is an ordinary memoization, which is exactly why it
    is dangerous to a sweep.
    """

    cache: dict[int, list[Row]] = field(default_factory=dict)

    def invoke(self) -> list[Row]:
        rows: list[Row] = []
        for author in self.store.select("author"):
            if author["id"] not in self.cache:
                self.cache[author["id"]] = self.store.select(
                    "book", where=("author_id", author["id"])
                )
            rows.extend(self.cache[author["id"]])
        return rows

    def clear_caches(self) -> None:
        self.cache.clear()


def test_a_sweep_with_no_cache_control_refuses_to_run(query_counter: None) -> None:
    """AC 5, structurally. The absence of a guarantee is the refusal, not a note
    on the result."""
    subject = CachingSubject()

    with pytest.raises(CacheControlError) as raised:
        sweep(subject, process_identity=None)

    assert "the second looks cheaper than it is" in str(raised.value)


def test_a_process_that_outlives_a_scale_point_stops_the_sweep(query_counter: None) -> None:
    """ADR 026's check, at measurement time. A process that survives can hold
    rows no database reset will clear, and no comparison of results can see it."""
    subject = CachingSubject()

    with pytest.raises(CacheControlError) as raised:
        sweep(subject, process_identity=lambda: "the same container every time")

    assert "already ran scale" in str(raised.value)


def test_a_cache_left_warm_flattens_growth_that_is_really_linear(
    query_counter: None,
) -> None:
    """The failure mode, with a clear that does not clear anything.

    Two queries at every volume — one for the authors, one for the single author
    the cache had not yet seen — which fits as constant. The endpoint issues one
    query per author.
    """
    subject = CachingSubject()

    result = sweep(subject, clear_caches=lambda: None)

    assert [point.raw[QUERIES] for point in result.points] == [2.0, 2.0, 2.0]
    assert result.fits[QUERIES].growth is Growth.CONSTANT


def test_clearing_between_points_recovers_the_growth(query_counter: None) -> None:
    """AC 5. Same workload, same volumes, the cache emptied after seeding."""
    subject = CachingSubject()

    result = sweep(subject, clear_caches=subject.clear_caches)

    assert [point.raw[QUERIES] for point in result.points] == [2.0, 3.0, 4.0]
    assert result.fits[QUERIES].growth is Growth.LINEAR


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, CacheControl.FRESH_PROCESS),
        ({"process_identity": None, "clear_caches": lambda: None}, CacheControl.EXPLICIT_CLEAR),
        ({"clear_caches": lambda: None}, CacheControl.BOTH),
    ],
)
def test_which_cache_guarantee_was_held_is_recorded(
    overrides: dict[str, Any], expected: CacheControl, query_counter: None
) -> None:
    """`CLAUDE.md`: exclusions carry their preconditions. *Queries flat across
    100x scale* means one thing when every point ran in its own process and
    something much weaker when a caller's own hook was trusted."""
    result = sweep(Subject(), **overrides)

    assert result.cache_control is expected


# ------------------------------------------------------------- the registration


def test_the_primitive_is_registered_and_declares_what_it_needs() -> None:
    """S-3.1's extension point, used for the first time. No agent code changed."""
    primitive = REGISTRY.get("scaling.volume")

    assert primitive.required_capabilities == {
        Capability.FIXTURE_SEEDING,
        Capability.STATE_RESET,
    }
    assert primitive.run is scale_volume

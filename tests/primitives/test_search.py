"""Forty candidates, nine measurements, and a threshold that can say "don't know".

S-3.5. Delta debugging's value is entirely in the count: the same answer as
asking each candidate in turn, for a fraction of the runs, and each run here is a
reset, a reseed and a workload. So the tests assert the count as well as the
answer — a search that finds the right culprit in forty measurements has not done
its job.

Three things beyond the algorithms need holding down:

**The oracle is a threshold, and near it the answer is noise.** A configuration
whose cost lands within the noise floor of the threshold decides a branch on a
coin flip. `UNRESOLVED` is the state the algorithm already has for that, and the
tests check the search still makes progress around it rather than crashing or
guessing.

**A subset that breaks the workload is unresolved, not fatal.** Ablation breaks
correctness deliberately; some combinations break it far enough to raise.

**Both ends have to be what the search assumes.** If everything ablated is still
expensive, no candidate owns the cost — and that is a finding, not a search.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.primitives.ablation import ExecutionModeError
from coldfix.primitives.measurement import CacheControlError
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.primitives.search import (
    AblationTarget,
    Oracle,
    Outcome,
    SearchError,
    ablation_measure,
    dd,
    ddmin,
)
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.store import Row, Store

QUERIES = "store.select"


def costs(**per_candidate: float) -> Any:
    """A measure function where each active candidate adds its own cost."""

    def measure(active: frozenset[str]) -> float:
        return sum(per_candidate[name] for name in active)

    return measure


# ---------------------------------------------------------------- the oracle


def test_the_oracle_is_a_threshold_not_a_crash() -> None:
    """AC 1. The adaptation to performance: expensive is a number being
    exceeded, not an exception being raised."""
    oracle = Oracle(measure=costs(a=10.0, b=1.0), threshold=5.0)

    assert oracle(frozenset({"a"})) is Outcome.EXPENSIVE
    assert oracle(frozenset({"b"})) is Outcome.CHEAP


def test_a_cost_inside_the_noise_band_is_unresolved() -> None:
    """S-0.4 put the timing noise floor at ~20 ms, about 6% of a 350 ms
    endpoint. A configuration landing inside that band of the threshold decides
    a branch of the search on noise, and `UNRESOLVED` is the honest name for it —
    the alternative is the same coin flip without the label."""
    oracle = Oracle(measure=costs(a=102.0), threshold=100.0, resolution=20.0)

    assert oracle(frozenset({"a"})) is Outcome.UNRESOLVED


def test_a_measurement_that_raises_is_unresolved_and_recorded() -> None:
    """AC 4. Ablation breaks correctness on purpose and some subsets break it far
    enough that nothing can be measured. Recorded against the configuration that
    caused it, not swallowed."""

    def explodes(active: frozenset[str]) -> float:
        message = "the workload cannot run without its serializer"
        raise RuntimeError(message)

    oracle = Oracle(measure=explodes, threshold=1.0)

    assert oracle(frozenset({"a"})) is Outcome.UNRESOLVED
    assert oracle.probes[0].failure is not None
    assert "cannot run without its serializer" in oracle.probes[0].failure


def test_a_repeated_configuration_is_not_a_second_ablation() -> None:
    """The count that matters is measurements taken, not questions asked, and
    delta debugging asks the same question repeatedly."""
    oracle = Oracle(measure=costs(a=10.0), threshold=5.0)

    oracle(frozenset({"a"}))
    oracle(frozenset({"a"}))

    assert oracle.measurements == 1
    assert oracle.configurations == 1
    assert len(oracle.probes) == 2
    assert oracle.probes[1].cached


# ------------------------------------------------------------------- ddmin


def test_ddmin_reduces_to_the_single_expensive_candidate() -> None:
    """AC 1. Nine cheap candidates and one that carries all the cost."""
    per_candidate = {f"c{index}": 1.0 for index in range(10)}
    per_candidate["c7"] = 500.0
    oracle = Oracle(measure=costs(**per_candidate), threshold=100.0)

    result = ddmin(per_candidate, oracle)

    assert result.culprits == {"c7"}


def test_ddmin_finds_a_pair_that_only_costs_together() -> None:
    """The case granularity doubling exists for: no single candidate exceeds the
    threshold, and halving therefore finds nothing until the parts get small
    enough to isolate both."""
    names = [f"c{index}" for index in range(8)]

    def measure(active: frozenset[str]) -> float:
        return 200.0 if {"c2", "c5"} <= active else 1.0

    oracle = Oracle(measure=measure, threshold=100.0)

    result = ddmin(names, oracle)

    assert result.culprits == {"c2", "c5"}


def test_ddmin_keeps_going_when_a_subset_cannot_be_measured() -> None:
    """AC 4, inside the algorithm rather than only in the oracle."""
    per_candidate = {f"c{index}": 1.0 for index in range(8)}
    per_candidate["c3"] = 500.0

    def measure(active: frozenset[str]) -> float:
        if active == frozenset({"c0", "c1", "c2", "c3"}):
            message = "this combination leaves the workload with nothing to serialize"
            raise ValueError(message)
        return sum(per_candidate[name] for name in active)

    oracle = Oracle(measure=measure, threshold=100.0)

    result = ddmin(per_candidate, oracle)

    assert result.culprits == {"c3"}
    assert result.unresolved >= 1


# ---------------------------------------------------------------------- dd


def test_dd_isolates_the_difference_between_the_cheap_and_expensive_cases() -> None:
    """AC 2, and the story's note: we always have both ends, because everything
    ablated is the cheap case and nothing ablated is the expensive one."""
    per_candidate = {f"c{index}": 1.0 for index in range(10)}
    per_candidate["c4"] = 500.0
    oracle = Oracle(measure=costs(**per_candidate), threshold=100.0)

    result = dd(per_candidate, oracle)

    assert result.culprits == {"c4"}
    assert result.largest_cheap is not None
    assert result.cheapest_expensive is not None
    assert result.largest_cheap < result.cheapest_expensive


def test_dd_reports_both_ends_it_narrowed_to() -> None:
    """The pair is the result, not just the difference: it says *this
    configuration is cheap and adding these makes it expensive*, which is a
    stronger claim than naming a set."""
    per_candidate = {f"c{index}": 1.0 for index in range(6)}
    per_candidate["c2"] = 500.0
    oracle = Oracle(measure=costs(**per_candidate), threshold=100.0)

    result = dd(per_candidate, oracle)

    assert result.cheapest_expensive is not None
    assert result.largest_cheap is not None
    assert result.culprits == result.cheapest_expensive - result.largest_cheap


def test_dd_survives_unresolved_configurations() -> None:
    per_candidate = {f"c{index}": 1.0 for index in range(8)}
    per_candidate["c6"] = 500.0

    def measure(active: frozenset[str]) -> float:
        # Breaks whenever c1 runs without c6, which is a shape dd is guaranteed
        # to probe: its first move is the first half of the difference.
        if "c1" in active and "c6" not in active:
            message = "c1 cannot run without what c6 sets up"
            raise RuntimeError(message)
        return sum(per_candidate[name] for name in active)

    oracle = Oracle(measure=measure, threshold=100.0)

    result = dd(per_candidate, oracle)

    assert "c6" in result.culprits
    assert result.unresolved >= 1


# ------------------------------------------------------- AC 3: the run count


@pytest.mark.parametrize("algorithm", [ddmin, dd])
def test_forty_candidates_localize_in_far_fewer_than_forty_ablations(
    algorithm: Any,
) -> None:
    """AC 3. The entire justification for the story: each measurement is a reset,
    a reseed and a workload run, so the count is the cost.

    Asking each candidate in turn is 40 measurements. Both algorithms do it in
    single figures, and the assertion is deliberately loose — what matters is the
    order of magnitude, not the exact number, which depends on where in the set
    the culprit happens to sit.
    """
    per_candidate = {f"c{index:02d}": 1.0 for index in range(40)}
    per_candidate["c29"] = 5000.0
    oracle = Oracle(measure=costs(**per_candidate), threshold=1000.0)

    result = algorithm(per_candidate, oracle)

    assert result.culprits == {"c29"}
    assert result.measurements < 20
    assert result.candidates == frozenset(per_candidate)


def test_the_closest_call_is_reported() -> None:
    """A search whose decisions all sat a hair from the threshold was decided by
    that hair, and the result should not look as confident as one whose margins
    were enormous."""
    per_candidate = {f"c{index}": 1.0 for index in range(8)}
    per_candidate["c5"] = 101.0
    oracle = Oracle(measure=costs(**per_candidate), threshold=100.0)

    result = dd(per_candidate, oracle)

    measured = [
        abs(probe.cost - result.threshold)
        for probe in result.probes
        if not probe.cached and probe.cost is not None
    ]

    closest = result.closest_call()

    assert closest == min(measured)
    # Every decision here rested on a few units against a threshold of 100. A
    # result that looks as confident as one whose margins were in the hundreds
    # is the thing this number exists to prevent.
    assert closest is not None
    assert closest < 0.1 * result.threshold


# -------------------------------------------------------- the two preconditions


def test_a_search_refuses_when_nothing_is_expensive() -> None:
    """Either the threshold is above what this workload ever costs, or the cost
    is not here — an exclusion worth recording rather than a search worth
    running."""
    oracle = Oracle(measure=costs(a=1.0, b=1.0), threshold=100.0)

    with pytest.raises(SearchError) as raised:
        dd(["a", "b"], oracle)

    assert "no expensive case to reduce" in str(raised.value)


def test_a_search_refuses_when_ablating_everything_changes_nothing() -> None:
    """The residual after ablation is the finding, and it belongs to a different
    set of candidates. Reducing this set would return an arbitrary subset of
    innocent components with full confidence."""

    def elsewhere(active: frozenset[str]) -> float:
        return 500.0

    oracle = Oracle(measure=elsewhere, threshold=100.0)

    with pytest.raises(SearchError) as raised:
        ddmin(["a", "b"], oracle)

    assert "none of them owns the cost" in str(raised.value)


def test_an_empty_candidate_set_is_refused() -> None:
    with pytest.raises(SearchError):
        dd([], Oracle(measure=costs(), threshold=1.0))


# ------------------------------------------------- wired to real ablation


class Loader:
    """Four components, one of which does nearly all the querying."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def header(self, author_id: int) -> list[Row]:
        return self.store.select("author", where=("id", author_id))

    def body(self, author_id: int) -> list[Row]:
        rows: list[Row] = []
        for _ in range(6):
            rows += self.store.select("book", where=("author_id", author_id))
        return rows

    def footer(self, author_id: int) -> list[Row]:
        return self.store.select("author", where=("id", author_id))

    def sidebar(self, author_id: int) -> list[Row]:
        return self.store.select("author", where=("id", author_id))

    def unused(self, author_id: int) -> list[Row]:
        """Reachable, ablatable, and never called by the workload."""
        return self.store.select("author", where=("id", author_id))


class FakeDiagnosticSession(DiagnosticSession):
    """Mode without a container, as in the S-3.4 tests."""

    def __init__(self) -> None:
        pass


class FakeCandidateSession(CandidateSession):
    def __init__(self) -> None:
        pass


class RecordingReset(ResetMechanism):
    strategy: ClassVar[ResetStrategy] = ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self._snapshot: Store | None = None
        self.cycles = 0

    def prepare(self) -> None:
        pass

    def begin(self) -> None:
        self.cycles += 1
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)


@dataclass
class Subject:
    store: Store = field(default_factory=Store)
    loader: Loader | None = None
    processes: list[str] = field(default_factory=list)

    def seed(self) -> None:
        self.store = Store()
        self.store.add("author", [{"id": index} for index in range(3)])
        self.store.add(
            "book",
            [
                {"id": index * 10 + n, "author_id": index, "title": f"b{index}{n}"}
                for index in range(3)
                for n in range(2)
            ],
        )
        self.loader = Loader(self.store)

    def invoke(self) -> object:
        assert self.loader is not None
        rows: list[Row] = []
        for author in self.store.select("author"):
            rows += self.loader.header(author["id"])
            rows += self.loader.body(author["id"])
            rows += self.loader.footer(author["id"])
            rows += self.loader.sidebar(author["id"])
        return rows

    def process_identity(self) -> str:
        identity = f"container-{len(self.processes)}"
        self.processes.append(identity)
        return identity


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


TARGETS = [
    AblationTarget(name=name, owner=Loader, attribute=name)
    for name in ("header", "body", "footer", "sidebar")
]


def build_measure(subject: Subject, *, targets: Any = None, **overrides: Any) -> Any:
    subject.seed()
    arguments: dict[str, Any] = {
        "invoke": subject.invoke,
        "reset": VerifiedReset(
            mechanism=RecordingReset(subject),
            report=VerificationReport(
                strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES, cycles=10
            ),
        ),
        "session": FakeDiagnosticSession(),
        "metric": QUERIES,
        "seed": subject.seed,
        "counters": [QUERIES],
        "process_identity": subject.process_identity,
    }
    arguments.update(overrides)
    return ablation_measure(TARGETS if targets is None else targets, **arguments)


def test_the_search_localizes_a_real_ablation_target(query_counter: None) -> None:
    """The wiring, end to end: record a stub per target, ablate everything
    outside the active set, and let `dd` narrow it down."""
    subject = Subject()
    measure = build_measure(subject)
    # Three authors: the body issues six queries each, the other three one each.
    # Everything active is 3 + 3*(1+6+1+1) = 30; only the body is above 12.
    oracle = Oracle(measure=measure, threshold=12.0)

    result = dd([target.name for target in TARGETS], oracle)

    assert result.culprits == {"body"}
    assert result.measurements < len(TARGETS) + 2


def test_every_configuration_is_measured_in_its_own_reset_cycle(
    query_counter: None,
) -> None:
    """A configuration measured on the state another left is measuring two
    changes at once."""
    subject = Subject()
    mechanism = RecordingReset(subject)
    measure = build_measure(
        subject,
        reset=VerifiedReset(
            mechanism=mechanism,
            report=VerificationReport(strategy=mechanism.strategy, cycles=10),
        ),
    )
    oracle = Oracle(measure=measure, threshold=12.0)

    result = dd([target.name for target in TARGETS], oracle)

    # One cycle for the recording pass, then one per distinct configuration.
    assert mechanism.cycles == result.measurements + 1


def test_the_stubs_are_recorded_before_the_search_and_not_again(
    query_counter: None,
) -> None:
    """Re-recording per configuration would give each one a stub taken under
    different conditions, and the search would then be comparing measurements
    that differ by more than the thing it varies.

    The recording pass runs when the measure function is built — before any
    configuration is measured — which is what the cycle count above shows and
    what this shows from the other side: building it costs one cycle, and the
    search has not started.
    """
    subject = Subject()
    mechanism = RecordingReset(subject)

    build_measure(
        subject,
        reset=VerifiedReset(
            mechanism=mechanism,
            report=VerificationReport(strategy=mechanism.strategy, cycles=10),
        ),
    )

    assert mechanism.cycles == 1


def test_a_target_that_never_runs_is_refused(query_counter: None) -> None:
    """It owns none of the cost, and including it would spend measurements
    proving that."""
    subject = Subject()
    unused = AblationTarget(name="unused", owner=Loader, attribute="unused")

    with pytest.raises(SearchError) as raised:
        build_measure(subject, targets=[*TARGETS, unused])

    assert "never called" in str(raised.value)


def test_the_search_refuses_a_candidate_session(query_counter: None) -> None:
    """S-3.4 owns the refusal and it applies here too: a search installs stubs,
    which is the thing a candidate session must never do."""
    subject = Subject()
    candidate: Any = FakeCandidateSession()

    with pytest.raises(ExecutionModeError):
        build_measure(subject, session=candidate)


def test_the_search_refuses_without_cache_control(query_counter: None) -> None:
    subject = Subject()

    with pytest.raises(CacheControlError):
        build_measure(subject, process_identity=None)


def test_the_primitive_is_registered() -> None:
    primitive = REGISTRY.get("ablation.search")

    assert primitive.required_capabilities == {
        Capability.DIAGNOSTIC_WORKTREE,
        Capability.STATE_RESET,
    }
    assert primitive.run is dd

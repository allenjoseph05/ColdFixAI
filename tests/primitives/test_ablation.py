"""What a stub returns decides what the delta means, so the stub is recorded.

S-3.4. The story's note states the principle and S-0.4 measured it: an empty
stub measures the component *plus* everything downstream that consumed its
output, a replayed real value measures the component alone. On the spike's real
Django endpoint the two were indistinguishable on timing (p = 0.64) while
differing six-fold in payload — so the difference is real and wall time cannot
see it.

Two of the spike's findings are load-bearing here and are tested directly rather
than trusted:

**A replay value that is not size-representative is the empty stub in disguise.**
S-0.4's first run recorded a one-followup value out of a population whose median
was six, and the two strategies then looked interchangeable — the right
conclusion, by accident, for the wrong reason. The value replayed here is the one
closest to the median, and the distribution it came from is recorded beside it.

**Replaying one value does not preserve per-instance cardinality**, so the
ablated run can be charged more downstream work than the baseline ever did, and
the delta then understates the component. The gap is computed, not assumed small.

The subject is the planted store, whose counts are exact by construction.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.primitives.ablation import (
    AblationError,
    AblationResult,
    ExecutionModeError,
    MinimalValueError,
    StubStrategy,
    TargetError,
    ablate,
    choose_stub,
    record_returns,
    stubbed,
)
from coldfix.primitives.measurement import SECONDS, BaselineError, CacheControlError
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.store import Row, Store

QUERIES = "store.select"


class Loader:
    """The component under ablation: fetches a parent's children.

    A class rather than a function so the target is an attribute something owns,
    which is what both the recorder and the stub replace.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def children(self, author_id: int) -> list[Row]:
        return self.store.select("book", where=("author_id", author_id))

    def stream(self, author_id: int) -> Iterator[Row]:
        """A single-use iterator. Capturing one means consuming it."""
        yield from self.store.select("book", where=("author_id", author_id))

    def handle(self, author_id: int) -> object:
        """Something with no minimal value this can construct."""
        return _Resource(author_id)


class _Resource:
    """Stands in for a live connection: not copyable, not a known container."""

    def __init__(self, owner: int) -> None:
        self.owner = owner

    def __deepcopy__(self, memo: dict[int, object]) -> _Resource:
        message = "a live resource cannot be copied"
        raise TypeError(message)


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


# ------------------------------------------------------------ the test doubles


class FakeDiagnosticSession(DiagnosticSession):
    """A diagnostic session without a container behind it.

    Subclassed rather than constructed because what is under test is the *type*
    and its `mode`, which is exactly what a real `Workbench.open` decides. Giving
    this a real worktree and sandbox would test docker, not the refusal.
    """

    # Deliberately does not call `Session.__init__`: there is no worktree.
    def __init__(self) -> None:
        pass


class FakeCandidateSession(CandidateSession):
    """The session that must be refused. Same construction, opposite mode."""

    def __init__(self) -> None:
        pass


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
    """Authors with uneven numbers of books, loaded one author at a time.

    Uneven on purpose: a target that returns the same size every call has no
    median to be representative of, and the choice this story turns on would be
    untestable.
    """

    sizes: tuple[int, ...] = (1, 2, 6, 6, 7, 20)
    store: Store = field(default_factory=Store)
    loader: Loader | None = None
    processes: list[str] = field(default_factory=list)

    def seed(self) -> None:
        self.store = Store()
        self.store.add("author", [{"id": index} for index in range(len(self.sizes))])
        self.store.add(
            "book",
            [
                {"id": index * 100 + n, "author_id": index, "title": f"b-{index}-{n}"}
                for index, size in enumerate(self.sizes)
                for n in range(size)
            ],
        )
        self.loader = Loader(self.store)

    def invoke(self) -> object:
        assert self.loader is not None
        titles: list[str] = []
        for author in self.store.select("author"):
            for book in self.loader.children(author["id"]):
                titles.append(book["title"])
        return titles

    def process_identity(self) -> str:
        identity = f"container-{len(self.processes)}"
        self.processes.append(identity)
        return identity


@dataclass
class StreamingSubject(Subject):
    """The same workload, reaching the children through a generator."""

    def invoke(self) -> object:
        assert self.loader is not None
        titles: list[str] = []
        for author in self.store.select("author"):
            for book in self.loader.stream(author["id"]):
                titles.append(book["title"])
        return titles


def run_ablation(subject: Subject, **overrides: Any) -> AblationResult:
    subject.seed()
    assert subject.loader is not None
    arguments: dict[str, Any] = {
        "owner": Loader,
        "attribute": "children",
        "invoke": subject.invoke,
        "reset": VerifiedReset(
            mechanism=RecordingReset(subject),
            report=VerificationReport(
                strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES, cycles=10
            ),
        ),
        "session": FakeDiagnosticSession(),
        "seed": subject.seed,
        "counters": [QUERIES],
        "process_identity": subject.process_identity,
    }
    arguments.update(overrides)
    result: AblationResult = ablate(**arguments)
    return result


# ------------------------------------------------- AC 1 and 2: record and replay


def test_a_real_return_value_is_recorded_during_the_baseline(query_counter: None) -> None:
    """AC 1. The recorder does not change what the workload does — it calls
    through and keeps a copy of what came back."""
    subject = Subject()
    subject.seed()
    assert subject.loader is not None

    with record_returns(Loader, "children") as recording:
        observed = subject.invoke()

    assert isinstance(observed, list)
    assert len(observed) == sum(subject.sizes)
    assert recording.calls == len(subject.sizes)
    assert sorted(recording.sizes) == sorted(subject.sizes)


def test_the_recorded_value_is_a_copy_not_the_live_object(query_counter: None) -> None:
    """A recording that aliased the returned object would record whatever the
    workload did to it afterwards, and replay that instead."""
    subject = Subject()
    subject.seed()

    with record_returns(Loader, "children") as recording:
        returned = Loader(subject.store).children(2)
        returned.clear()

    # The size is taken eagerly and survives either way, so it is the *value*
    # that has to be asserted: an aliased recording would replay the emptied
    # list, which is the empty stub wearing the replay strategy's name.
    assert len(recording.values[0]) == 6  # type: ignore[arg-type]
    assert choose_stub(recording).size == 6


def test_the_target_is_restored_afterwards(query_counter: None) -> None:
    """Instrumentation that outlives its block taxes everything measured after
    it — S-1.3's rule, and the same `finally`."""
    original = Loader.children

    with record_returns(Loader, "children"):
        assert Loader.children is not original

    assert Loader.children is original


def test_the_replayed_value_is_returned_during_ablation(query_counter: None) -> None:
    """AC 2, and the shape of the whole primitive: the component's own queries
    disappear while everything downstream still runs."""
    subject = Subject()

    result = run_ablation(subject)

    assert result.stub.strategy is StubStrategy.REPLAY
    # One query per author, plus the one that listed them.
    assert result.baseline[QUERIES] == len(subject.sizes) + 1
    assert result.ablated[QUERIES] == 1
    assert result.share(QUERIES) == pytest.approx(6 / 7)


def test_the_component_is_called_the_same_number_of_times_either_way(
    query_counter: None,
) -> None:
    """The stub replaces what the component *returns*, not whether it is called.
    A different call count between conditions would mean the two runs did
    different work for a reason other than the ablation."""
    subject = Subject()

    result = run_ablation(subject)

    assert result.calls_baseline == result.calls_ablated == len(subject.sizes)


# --------------------------------------- the spike's finding: size representativeness


def test_the_replayed_value_is_the_one_closest_to_the_median_size(
    query_counter: None,
) -> None:
    """S-0.4's methodological trap, and the reason this is not just "the first
    value we saw".

    Its first run recorded a one-followup value because the first ticket with any
    was a demo row that sorted ahead of the synthesized ones. The replay payload
    was then nearly as small as the empty stub's and the two strategies looked
    interchangeable — for entirely the wrong reason.
    """
    subject = Subject(sizes=(1, 2, 6, 6, 7, 20))

    result = run_ablation(subject)

    assert result.stub.size == 6
    assert result.stub.size_representative()


def test_the_distribution_the_value_came_from_is_recorded(query_counter: None) -> None:
    """A reader given only the chosen size cannot see the trap happening. One
    given the distribution can."""
    subject = Subject()

    result = run_ablation(subject)

    assert result.stub.recorded_sizes == subject.sizes
    assert "median 6" in result.stub.reason


def test_a_first_value_stub_would_not_be_representative(query_counter: None) -> None:
    """The failure mode itself, asserted against the same recording.

    Had the first observed value been replayed, the stub would have carried one
    child where the median author has six — a sixth of the downstream work, which
    is the empty stub's semantics under another name.
    """
    subject = Subject()
    subject.seed()

    with record_returns(Loader, "children") as recording:
        subject.invoke()

    first = recording.sizes[0]
    chosen = choose_stub(recording)

    assert first == 1
    assert chosen.size == 6


def test_the_cardinality_gap_is_recorded(query_counter: None) -> None:
    """S-0.4 measured +0.8% and said it would not be harmless if the component
    fed something expensive: the ablated run is charged more downstream work
    than the baseline ever did, so the delta understates the component."""
    subject = Subject(sizes=(1, 2, 6, 6, 7, 20))

    result = run_ablation(subject)

    # Six calls replaying a six-child value, against 42 children really loaded.
    assert result.stub.recorded_total_size == sum(subject.sizes)
    assert result.cardinality_gap == pytest.approx((6 * 6 - 42) / 42)


def test_a_uniform_target_has_no_cardinality_gap(query_counter: None) -> None:
    """The control. Where every call returns the same size, replay is exact and
    the gap is zero — so a non-zero gap elsewhere is about the data, not about
    the mechanism."""
    subject = Subject(sizes=(5, 5, 5, 5))

    result = run_ablation(subject)

    assert result.cardinality_gap == 0.0


# ------------------------------------------------------- AC 3: the fallback


def test_a_single_use_iterator_falls_back_to_a_minimal_value(query_counter: None) -> None:
    """AC 3. Capturing a generator means consuming it, which would break the run
    being measured — so it is passed through untouched and the fallback is used."""
    subject = StreamingSubject()

    result = run_ablation(subject, attribute="stream")

    assert result.stub.strategy is StubStrategy.MINIMAL
    assert "consumed by reading it" in result.stub.reason


def test_the_iterator_the_baseline_returned_is_not_consumed(query_counter: None) -> None:
    """The recorder must not read what it cannot replay. A drained generator
    hands the workload nothing, which would measure a workload that did no work
    and call it a baseline."""
    subject = Subject()
    subject.seed()
    assert subject.loader is not None

    with record_returns(Loader, "stream"):
        rows = list(subject.loader.stream(2))

    assert len(rows) == 6


def test_a_value_that_cannot_be_copied_falls_back(query_counter: None) -> None:
    """The other half of AC 3: a stateful object holding a live resource."""
    subject = Subject()
    subject.seed()
    assert subject.loader is not None

    with record_returns(Loader, "handle") as recording:
        subject.loader.handle(1)

    with pytest.raises(MinimalValueError):
        choose_stub(recording)


def test_a_list_returning_target_that_cannot_be_copied_gets_an_empty_list() -> None:
    """A minimal value is constructed from the *type*, so a container that
    refuses to be copied still has an ablation — an empty one of its own kind."""

    class Uncopyable(list[int]):
        def __deepcopy__(self, memo: dict[int, object]) -> Uncopyable:
            message = "no"
            raise TypeError(message)

    class Owner:
        def load(self) -> Uncopyable:
            return Uncopyable([1, 2, 3])

    with record_returns(Owner, "load") as recording:
        Owner().load()

    stub = choose_stub(recording)

    assert stub.strategy is StubStrategy.MINIMAL
    assert stub.value == []


def test_the_strategy_is_recorded_on_every_result(query_counter: None) -> None:
    """AC 4, and the whole reason S-0.4's second half exists. The two strategies
    measure different things, so a delta that does not say which was used cannot
    be interpreted."""
    replayed = run_ablation(Subject())
    minimal = run_ablation(StreamingSubject(), attribute="stream")

    assert replayed.stub.strategy is StubStrategy.REPLAY
    assert minimal.stub.strategy is StubStrategy.MINIMAL
    assert replayed.stub.reason and minimal.stub.reason


def test_a_target_that_never_ran_is_not_an_ablation(query_counter: None) -> None:
    """A component that does not run cannot own any of the cost, and a stub for
    it would produce a delta of zero that reads as "measured and cheap"."""
    subject = Subject()
    subject.seed()

    with record_returns(Loader, "children") as recording:
        pass

    with pytest.raises(AblationError):
        choose_stub(recording)


# ------------------------------------------- AC 5: diagnostic sessions only


def test_a_stub_cannot_be_installed_from_a_candidate_session(query_counter: None) -> None:
    """AC 5, structurally. Ablation deliberately breaks correctness; a candidate
    session is the one that can hand back a diff, and numbers taken from broken
    code must not become the evidence for a patch."""
    subject = Subject()
    subject.seed()

    with record_returns(Loader, "children") as recording:
        subject.invoke()
    stub = choose_stub(recording)

    candidate: Any = FakeCandidateSession()
    with pytest.raises(ExecutionModeError), stubbed(Loader, "children", stub, session=candidate):
        pass


def test_an_ablation_run_refuses_a_candidate_session(query_counter: None) -> None:
    """The check is on the orchestration too, so it fails before the baseline
    runs rather than after the measurements have been taken."""
    with pytest.raises(ExecutionModeError):
        run_ablation(Subject(), session=FakeCandidateSession())


def test_the_session_requirement_is_a_type_not_a_flag() -> None:
    """There is no `mode=True` to pass. The argument is an object obtainable only
    from `Workbench.open(mode=DIAGNOSTIC)`, whose session has no method that
    returns a diff (ADR 022) and whose worktree is destroyed on exit."""
    assert not issubclass(CandidateSession, DiagnosticSession)
    assert "diff" not in dir(DiagnosticSession)


# ------------------------------------------------- shared measurement machinery


def test_both_conditions_start_from_the_same_state(query_counter: None) -> None:
    """One reset cycle per condition. The ablated run must start where the
    baseline started, not where it finished."""
    subject = Subject()
    subject.seed()
    mechanism = RecordingReset(subject)

    run_ablation(
        subject,
        reset=VerifiedReset(
            mechanism=mechanism,
            report=VerificationReport(strategy=mechanism.strategy, cycles=10),
        ),
    )

    assert mechanism.events == ["begin", "reset", "begin", "reset"]


def test_an_ablation_refuses_without_cache_control(query_counter: None) -> None:
    """It bites harder here than in a scaling sweep: the baseline warms exactly
    the caches the ablated run would otherwise skip, which inflates the delta and
    makes the component look more expensive than it is."""
    with pytest.raises(CacheControlError):
        run_ablation(Subject(), process_identity=None)


def test_a_process_that_ran_the_baseline_cannot_run_the_ablation(
    query_counter: None,
) -> None:
    with pytest.raises(CacheControlError):
        run_ablation(Subject(), process_identity=lambda: "one container for both")


def test_a_workload_that_fails_with_the_component_intact_fails_loudly(
    query_counter: None,
) -> None:
    """There is nothing for an ablated run to be compared against, and a delta
    against a missing baseline is not a weaker result — it is a wrong one."""

    @dataclass
    class Broken(Subject):
        def invoke(self) -> object:
            message = "the workload does not run here"
            raise RuntimeError(message)

    with pytest.raises(BaselineError) as raised:
        run_ablation(Broken())

    assert isinstance(raised.value.__cause__, RuntimeError)


def test_the_duration_is_still_marked_as_one_sample(query_counter: None) -> None:
    """S-0.4's delta was fifty times the noise floor, and that is why it was
    reportable. The kind is recorded so a two-percent delta cannot be read off
    the same column."""
    result = run_ablation(Subject())

    assert result.kinds[SECONDS].value == "duration"


def test_an_inherited_attribute_is_refused(query_counter: None) -> None:
    """S-1.3's rule. Patching a name where it is *found* rather than where it is
    *stored* changes which objects are affected, and restoring it afterwards
    would write an attribute onto a class that never had one."""

    class Subclass(Loader):
        pass

    with pytest.raises(TargetError), record_returns(Subclass, "children"):
        pass


def test_the_primitive_is_registered() -> None:
    primitive = REGISTRY.get("ablation.stub")

    assert primitive.required_capabilities == {
        Capability.DIAGNOSTIC_WORKTREE,
        Capability.STATE_RESET,
    }
    assert primitive.run is ablate

"""Three ways of finding nothing, and only one of them is good news.

S-4.5. `CLAUDE.md` makes null results a project invariant — *"screened 9
workloads, nothing found" ships as an answer* — so the tests that matter here are
the ones that stop the other two cases being reported as that sentence.

A workload nothing has shown to do real work is not healthy; it is uncovered. A
workload that ran and touched no data is `02-architecture.md` §1.5's named
failure mode, with a required response: *report honestly and stop, never report
"no issues found"*. Both have their own message, and a test asserts the healthy
sentence is absent from each.

The fourth test in this file is the guard in the other direction: constructing a
null result from a screen that flagged something raises, because that is the one
sentence this artifact exists to make impossible.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.measurement import MATERIALIZED, SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.flagging import Ranking, rank
from coldfix.screening.growth import screen_growth
from coldfix.screening.null import NullResult, NullResultError, null_result
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    BoundWorkload,
    FixtureRecipe,
    Observation,
    Workload,
)
from fixtures.planted.queries import (
    list_books_batched,
    list_books_n_plus_one,
    list_titles_narrow,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(DB_QUERY, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(DB_QUERY)


class StoreReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def begin(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)


@dataclass
class Subject:
    call: Any
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def scale(self, n: int) -> None:
        self.store = build_store(authors=n, books_per_author=2)

    def invoke(self) -> object:
        return self.call(self.store)

    def process_identity(self) -> str:
        self.processes.append(f"container-{len(self.processes)}")
        return self.processes[-1]

    def payload(self) -> Mapping[str, float]:
        return {
            CELLS: float(self.store.cells_returned),
            DB_ROWS: float(self.store.rows_returned),
            RESPONSE_BYTES: float(self.store.cells_returned * 8),
        }


def screened(name: str, call: Any) -> Any:
    subject = Subject(call)
    descriptor = Workload(
        id=name,
        description=f"the planted {name} workload",
        entry_point=f"fixtures.planted.queries.{call.__name__}",
        fixture=FixtureRecipe(
            entity="author",
            per_parent=2,
            distribution=Distribution.UNIFORM,
            source="fixtures.planted.store.build_store",
            seed=0,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )
    bound = BoundWorkload(
        descriptor,
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        extra_counters=subject.payload,
    )
    return screen_growth(bound, counters=[DB_QUERY])


def emptied(result: Any) -> Any:
    """The same screening result with every amount zero at the largest scale.

    The shape `02-architecture.md` §1.5 describes: the workload ran, the harness
    measured it, and it touched nothing. Stated rather than measured because the
    planted fixtures all do real work — a fixture that touched no data would be
    testing the fixture rather than the check.
    """
    observations = list(result.workload.observations)
    zeroed = dict(observations[-1].metrics)
    for metric in (DB_ROWS, MATERIALIZED, CELLS, RESPONSE_BYTES):
        if metric in zeroed:
            zeroed[metric] = 0.0
    observations[-1] = Observation(scale=observations[-1].scale, metrics=zeroed)
    return replace(
        result,
        workload=result.workload.model_copy(update={"observations": tuple(observations)}),
    )


# ----------------------------- AC 1 and 2: the answer, and it is not an error


def test_a_clean_screen_produces_a_null_result_rather_than_raising(
    query_counter: None,
) -> None:
    """AC 2. There is deliberately no error type meaning *nothing found*: an
    exception is something a caller handles and moves past, and this is the
    answer."""
    results = [screened("batched", list_books_batched), screened("narrow", list_titles_narrow)]

    outcome = null_result(results, rank(results))

    assert isinstance(outcome, NullResult)
    assert outcome.screened == ("batched", "narrow")
    assert outcome.healthy == ("batched", "narrow")


def test_the_null_result_names_every_workload_it_screened(query_counter: None) -> None:
    """AC 1. *Screened 9 workloads, nothing found* is only an answer if it says
    which nine."""
    results = [screened("batched", list_books_batched), screened("narrow", list_titles_narrow)]

    report = null_result(results, rank(results)).report()

    assert "Screened 2 workloads and flagged none: batched, narrow" in report


def test_the_thresholds_that_were_applied_travel_with_the_answer(
    query_counter: None,
) -> None:
    """`CLAUDE.md`: exclusions carry their preconditions, and a null result is
    the largest exclusion this system produces."""
    results = [screened("batched", list_books_batched)]

    outcome = null_result(results, rank(results))

    assert set(outcome.thresholds) == {
        "flat cost (queries)",
        "timing floor (seconds)",
        "minimum scale ratio",
    }
    assert "Thresholds applied:" in outcome.report()


def test_the_conditions_are_recorded_per_workload(query_counter: None) -> None:
    """*Nothing found* means one thing across a sixteenfold sweep of uniform
    fixtures with a fresh process per point and something much weaker otherwise —
    and a screen may have swept two workloads differently, so one set of
    conditions at the top would be true of neither."""
    results = [screened("batched", list_books_batched), screened("narrow", list_titles_narrow)]

    outcome = null_result(results, rank(results))

    assert {item.workload_id for item in outcome.conditions} == {"batched", "narrow"}
    assert all(item.distribution == "uniform" for item in outcome.conditions)
    assert all(item.scales == (10, 40, 160) for item in outcome.conditions)
    assert "under uniform fixtures" in outcome.report()


def test_a_result_covering_everything_says_so_plainly(query_counter: None) -> None:
    results = [screened("batched", list_books_batched)]

    outcome = null_result(results, rank(results))

    assert outcome.covers_everything_screened
    assert "That is the answer, not a failure to find one" in outcome.report()


# ------------------- AC 3: the workload that ran and touched nothing


def test_a_workload_that_touched_no_data_gets_its_own_message(
    query_counter: None,
) -> None:
    """AC 3, and `02-architecture.md` §1.5's named failure mode. The required
    response is *report honestly and stop*, never "no issues found"."""
    results = [emptied(screened("batched", list_books_batched))]

    outcome = null_result(results, rank(results))

    assert [item.touched_no_data for item in outcome.unverified] == [True]
    assert "ran and touched no data at all" in outcome.report()
    assert "measured an empty workload" in outcome.report()


def test_a_duration_does_not_count_as_data_touched(query_counter: None) -> None:
    """An empty workload still takes time — it ran.

    So *touched no data* is decided over amounts and not over everything
    measured: consulting the duration would mean a workload that returned
    nothing in four milliseconds reads as having done something, which is the
    false negative that turns §1.5's failure mode back into "no issues found".
    Only metrics whose meaning is known are consulted at all, since an adapter's
    own metric could be a rate, a share or a flag.
    """
    empty = emptied(screened("batched", list_books_batched))

    assert empty.workload.observations[-1].metrics[SECONDS] > 0

    outcome = null_result([empty], rank([empty]))

    assert [item.touched_no_data for item in outcome.unverified] == [True]


def test_an_empty_workload_is_never_called_healthy(query_counter: None) -> None:
    """The sentence this whole story exists to prevent. A workload the harness
    could not exercise is not a workload with nothing wrong with it."""
    results = [emptied(screened("batched", list_books_batched))]

    outcome = null_result(results, rank(results))

    assert outcome.healthy == ()
    assert not outcome.covers_everything_screened
    assert "That is the answer, not a failure to find one" not in outcome.report()


def test_a_workload_whose_work_was_not_verified_is_uncovered_rather_than_healthy(
    query_counter: None,
) -> None:
    """The second of the three ways of finding nothing. S-4.1's F6 test did not
    pass, so the null result says what it does not cover."""
    result = screened("batched", list_books_batched)
    one_point = replace(
        result,
        workload=result.workload.model_copy(
            update={"observations": result.workload.observations[:1]}
        ),
    )

    outcome = null_result([one_point], rank([one_point]))

    assert outcome.healthy == ()
    assert "Not covered by this result" in outcome.report()
    assert "fewer than two scale points" in outcome.report()


def test_the_uncovered_case_and_the_empty_case_read_differently(
    query_counter: None,
) -> None:
    """§1.5 asks for a *specific* message, not a general one. "We could not show
    this does real work" and "this touched no data" call for different next
    actions."""
    empty = emptied(screened("batched", list_books_batched))
    result = screened("narrow", list_titles_narrow)
    one_point = replace(
        result,
        workload=result.workload.model_copy(
            update={"observations": result.workload.observations[:1]}
        ),
    )

    report = null_result([empty, one_point], rank([empty, one_point])).report()

    assert "batched ran and touched no data" in report
    assert "narrow — Not verified: fewer than two scale points" in report


def test_a_result_that_covers_some_workloads_says_how_many(query_counter: None) -> None:
    """Never "nothing found" flat when part of the screen came back unusable."""
    results = [
        emptied(screened("batched", list_books_batched)),
        screened("narrow", list_titles_narrow),
    ]

    report = null_result(results, rank(results)).report()

    assert "Nothing was found in the 1 workloads this result does cover" in report


def test_could_not_tell_is_carried_and_not_folded_into_nothing_found(
    query_counter: None,
) -> None:
    """S-4.3 kept the distinction and this is where it would be lost."""
    results = [screened("batched", list_books_batched)]
    ranking = Ranking(flagged=(), healthy=("batched",), unclassified=(("batched", CELLS),))

    outcome = null_result(results, ranking)

    assert outcome.unclassified == (("batched", CELLS),)
    assert "could not tell" in outcome.report()
    assert not outcome.covers_everything_screened


# ------------------------------------- the guard in the other direction


def test_a_screen_that_flagged_something_cannot_produce_a_null_result(
    query_counter: None,
) -> None:
    """The one sentence this artifact exists to make impossible."""
    results = [screened("n.plus.one", list_books_n_plus_one)]

    with pytest.raises(NullResultError, match="not a null result"):
        null_result(results, rank(results))


def test_screening_nothing_is_not_a_null_result() -> None:
    """Nothing found and nothing looked at are different answers."""
    with pytest.raises(NullResultError, match="different answers"):
        null_result([], Ranking(flagged=(), healthy=(), unclassified=()))


def test_the_artifact_survives_a_round_trip_through_json(query_counter: None) -> None:
    """It crosses a node boundary like every other artifact here: S-16.3 renders
    it and the orchestrator carries it as a terminal state."""
    results = [screened("batched", list_books_batched)]

    outcome = null_result(results, rank(results))

    assert NullResult.model_validate_json(outcome.model_dump_json()) == outcome


def test_a_hallucinated_field_is_refused(query_counter: None) -> None:
    """The same `extra="forbid"` S-4.1 needed: Pydantic's default drops an
    unrecognised key silently, and a caller adding `nothing_found=True` to a
    result that does not cover its workloads would have every reason to think it
    had been accepted."""
    with pytest.raises(ValueError, match="nothing_found"):
        NullResult(
            screened=("one",),
            healthy=("one",),
            unverified=(),
            unclassified=(),
            conditions=(),
            thresholds={},
            nothing_found=True,
        )

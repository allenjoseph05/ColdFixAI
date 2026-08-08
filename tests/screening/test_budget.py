"""Thirty flagged, five investigated, twenty-five listed and not dropped.

S-4.4. The story's third acceptance criterion is a whole test on its own, and
`04-cost.md` §12.4 is why it exists: *without this cap, every figure above is
meaningless — the worst case is simply unbounded.*

Two things here are less obvious than the arithmetic.

The cap counts **workloads**. One workload flagged on three metrics is one
investigation — grounding, seeding and the baseline are per workload and shared —
so counting flags would let a single workload with five flagged metrics eat a
whole run's budget while nine others were never looked at.

And the caveat S-4.3 attaches to every ranking becomes load-bearing here. In a
report, "magnitude ordering cannot express which finding matters more" costs a
reader a moment's care. At the cap it decides what gets investigated at all, so
the deferral list has to carry it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Growth
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.budget import (
    DEFAULT_FINDINGS_CAP,
    MAXIMUM_FINDINGS_CAP,
    BudgetError,
    plan,
)
from coldfix.screening.flagging import Flag, FlagKind, Ranking, rank
from coldfix.screening.growth import screen_growth
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    BoundWorkload,
    FixtureRecipe,
    Workload,
)
from fixtures.planted.queries import (
    list_books_batched,
    list_books_n_plus_one,
    list_titles_narrow,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"


def a_flag(workload: str, metric: str = DB_QUERY, magnitude: float = 4.0) -> Flag:
    return Flag(
        workload_id=workload,
        metric=metric,
        kind=FlagKind.GROWTH,
        observed=Growth.LINEAR,
        expected=Growth.CONSTANT,
        magnitude=magnitude,
    )


def ranking_of(*flags: Flag, healthy: tuple[str, ...] = ()) -> Ranking:
    """A ranking stated directly, for the tests about the cap's arithmetic.

    Thirty screened workloads is thirty sweeps, and what is under test here is
    what happens to a list once it exists. The composition with a real screen is
    the last test in this file.
    """
    ordered = sorted(flags, key=lambda item: (-item.magnitude, item.workload_id, item.metric))
    return Ranking(flagged=tuple(ordered), healthy=healthy, unclassified=())


# ---------------------------------------------- AC 1 and 3: the cap, and the list


def test_thirty_flagged_workloads_produce_five_investigations_and_a_list_of_25() -> None:
    """AC 3, verbatim. The number this story exists for."""
    flags = [a_flag(f"workload.{index:02d}", magnitude=30.0 - index) for index in range(30)]

    result = plan(ranking_of(*flags))

    assert len(result.investigate) == DEFAULT_FINDINGS_CAP
    assert len(result.deferred) == 25
    assert not result.within_budget


def test_the_deferred_workloads_are_named_and_positioned() -> None:
    """AC 2. *Listed for the human, not silently dropped* — and a list without
    positions cannot be resumed from, because nothing says where the line was."""
    flags = [a_flag(f"workload.{index:02d}", magnitude=30.0 - index) for index in range(8)]

    result = plan(ranking_of(*flags), cap=3)

    assert [deferral.position for deferral in result.deferred] == [4, 5, 6, 7, 8]
    assert result.deferred[0].workload_id == "workload.03"
    assert "workload.03" in result.report()


def test_the_deferral_list_repeats_that_magnitude_is_not_importance() -> None:
    """The caveat that changes meaning at the cap.

    In a report it costs a reader a moment's care. Here it decided which five of
    thirty workloads anybody will ever look at, and a list of the other
    twenty-five that did not say so would read as *these matter less*.
    """
    flags = [a_flag(f"workload.{index:02d}", magnitude=30.0 - index) for index in range(30)]

    report = plan(ranking_of(*flags)).report()

    assert "not investigated and are listed below rather than dropped" in report
    assert "monthly batch job" in report
    assert "list of what matters" in report


def test_the_cap_is_configurable() -> None:
    flags = [a_flag(f"workload.{index:02d}", magnitude=30.0 - index) for index in range(10)]

    assert len(plan(ranking_of(*flags), cap=1).investigate) == 1
    assert len(plan(ranking_of(*flags), cap=9).investigate) == 9


def test_the_investigated_workloads_are_the_highest_ranked() -> None:
    """The cap takes from the top of S-4.3's ordering rather than re-deciding."""
    flags = [a_flag(f"workload.{index:02d}", magnitude=float(index)) for index in range(6)]

    result = plan(ranking_of(*flags), cap=2)

    assert result.investigate == ("workload.05", "workload.04")


def test_nothing_is_deferred_when_everything_fits() -> None:
    result = plan(ranking_of(a_flag("one"), a_flag("two")))

    assert result.within_budget
    assert result.deferred == ()
    assert "Nothing was deferred" in result.report()


# -------------------------------------- the unit is a workload, not a flag


def test_a_workload_flagged_on_three_metrics_is_one_investigation() -> None:
    """Grounding, seeding and the baseline are per workload and shared across
    everything flagged on it. Counting flags would let one workload with five
    flagged metrics consume a whole run while nine others went unlooked at."""
    busy = [
        a_flag("noisy.endpoint", DB_QUERY, magnitude=9.0),
        a_flag("noisy.endpoint", DB_ROWS, magnitude=8.0),
        a_flag("noisy.endpoint", CELLS, magnitude=7.0),
    ]
    others = [a_flag(f"other.{index}", magnitude=6.0 - index) for index in range(4)]

    result = plan(ranking_of(*busy, *others), cap=2)

    assert result.investigate == ("noisy.endpoint", "other.0")
    assert len(result.deferred) == 3


def test_a_workload_keeps_its_best_position_rather_than_its_worst() -> None:
    """Taking the last place a workload occupies would push it down the list for
    having *more* evidence against it, which is backwards."""
    result = plan(
        ranking_of(
            a_flag("strong", DB_QUERY, magnitude=10.0),
            a_flag("strong", DB_ROWS, magnitude=1.1),
            a_flag("middling", DB_QUERY, magnitude=5.0),
        ),
        cap=1,
    )

    assert result.investigate == ("strong",)


def test_every_flag_on_an_investigated_workload_travels_with_it() -> None:
    """A deferred workload carries what was found on it, so the list can be acted
    on without re-screening."""
    result = plan(
        ranking_of(
            a_flag("first", magnitude=9.0),
            a_flag("second", DB_QUERY, magnitude=5.0),
            a_flag("second", DB_ROWS, magnitude=4.0),
        ),
        cap=1,
    )

    assert {item.metric for item in result.deferred[0].flags} == {DB_QUERY, DB_ROWS}


# ------------------------------------------- the ceiling that keeps it a cap


def test_a_cap_above_the_ceiling_is_refused() -> None:
    """A cap a caller can set to a thousand is not a cap, and §12.4's whole point
    is that an unbounded run makes every cost figure meaningless."""
    with pytest.raises(BudgetError, match="no ceiling on it"):
        plan(ranking_of(a_flag("one")), cap=MAXIMUM_FINDINGS_CAP + 1)


def test_a_ranking_with_nothing_flagged_is_not_a_plan() -> None:
    """Found by Epic 4's composition check.

    Without this, a caller that always asked for a plan got `investigate=(),
    deferred=(), within_budget=True` — which reads as *nothing to investigate and
    everything fitted the budget*, is indistinguishable from a healthy plan, and
    means S-4.5's null result is never produced. `assess` branches correctly, so
    this is the guard for a caller that does not go through it.
    """
    with pytest.raises(BudgetError, match="no plan to make"):
        plan(Ranking(flagged=(), healthy=("batched",), unclassified=()))


def test_a_cap_of_zero_is_refused_rather_than_treated_as_a_dry_run() -> None:
    """It investigates nothing having already paid for the screen. If that is the
    intention it belongs at the call site, not in a budget set to zero."""
    with pytest.raises(BudgetError, match="investigates nothing"):
        plan(ranking_of(a_flag("one")), cap=0)


def test_the_ceiling_leaves_room_for_the_default() -> None:
    assert DEFAULT_FINDINGS_CAP < MAXIMUM_FINDINGS_CAP


# ------------------------------ what the plan carries besides the two lists


def test_healthy_and_unclassified_workloads_survive_the_cap() -> None:
    """Four lists rather than two, because they ask four different things of
    whoever reads them. A cap that dropped *could not tell* would turn it into
    *nothing there*, which is the distinction S-4.3 kept."""
    ranking = Ranking(
        flagged=(a_flag("flagged"),),
        healthy=("batched", "narrow"),
        unclassified=(("batched", CELLS),),
    )

    result = plan(ranking)

    assert result.healthy == ("batched", "narrow")
    assert result.unclassified == (("batched", CELLS),)


def test_a_plan_cannot_be_extended_after_it_is_made() -> None:
    """The cap is a guarantee, not a suggestion. A plan somebody can append to is
    a budget somebody can spend past."""
    result = plan(ranking_of(a_flag("one"), a_flag("two")), cap=1)

    with pytest.raises(AttributeError):
        result.investigate = ("one", "two")  # type: ignore[misc]

    assert isinstance(result.investigate, tuple)


# ------------------------------------------------- composed with a real screen


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


def test_the_cap_composes_with_a_real_screen(query_counter: None) -> None:
    """Epic 4 end to end at this point: measure, flag, rank, cap.

    The defect is investigated, the two controls are named as healthy and never
    reach the budget at all, and nothing about the cap had to know what a query
    counter is.
    """
    ranking = rank(
        [
            screened("n.plus.one", list_books_n_plus_one),
            screened("batched", list_books_batched),
            screened("narrow", list_titles_narrow),
        ]
    )

    result = plan(ranking, cap=1)

    assert result.investigate == ("n.plus.one",)
    assert set(result.healthy) == {"batched", "narrow"}
    assert result.within_budget

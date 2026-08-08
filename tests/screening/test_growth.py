"""Screening the planted defects, and proving the screen cannot call a model.

S-4.2. Two of the three acceptance criteria are arithmetic and the third is a
safety property, and the third is the one that needed thought.

*Zero model calls — asserted by a test that runs screening with no LLM client
configured.* Written literally that test passes today for the wrong reason:
there is no LLM client anywhere in this codebase until E7, so "none configured"
is the only state that exists, and the test would assert nothing and keep passing
after a client arrived. So the assertion here is structural — the transitive
import graph of `coldfix.screening`, walked in a clean interpreter, contains no
LLM SDK — plus a run of the whole screen with the socket layer removed, which no
model call of any kind can survive.

The rest is the planted fixture screened as an investigation would screen it: the
N+1, its batched control, the over-fetch invisible to query counting, and the
decoy that costs more than the defect at every scale a small dataset has.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Growth
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import CacheControlError, MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.growth import (
    SCREENING_SCALES,
    ScreeningError,
    screen,
    screen_growth,
)
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
    list_titles_over_fetching,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store, build_store

# Every package name that would mean a model call is reachable from screening.
# A screen that imported one of these could make a call whether or not it did,
# and "did not this time" is not a property a test can hold onto.
LLM_SDKS = frozenset(
    {
        "anthropic",
        "openai",
        "langchain",
        "langgraph",
        "litellm",
        "cohere",
        "google.generativeai",
        "mistralai",
        "ollama",
        "transformers",
    }
)

CELLS = "cells_returned"

# Big enough that the smallest scale point stays under it, so a metric counting
# what lies past it is genuinely zero there. The N+1 returns 10 authors and their
# 20 books at the smallest scale, which is exactly a page.
PAGE_SIZE = 30


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
    """A planted workload and the store it runs against."""

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
        """The response-size proxy this fixture can offer, plus F6's metric."""
        return {
            CELLS: float(self.store.cells_returned),
            RESPONSE_BYTES: float(self.store.cells_returned * 8),
        }

    def beyond_first_page(self) -> Mapping[str, float]:
        """Rows past a page of 20 — zero at the smallest scale, and not by accident.

        A metric absent at the small end is an ordinary shape, not an edge case:
        a page that never overflowed, a queryset that never fired, a cache that
        covered the empty case. It is also the one that breaks a growth ratio.
        """
        return {"rows_beyond_page": float(max(0, self.store.rows_returned - PAGE_SIZE))}


def make(name: str, call: Any, **overrides: Any) -> tuple[BoundWorkload, Subject]:
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
    arguments: dict[str, Any] = {
        "invoke": subject.invoke,
        "scale": subject.scale,
        "reset": VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        "process_identity": subject.process_identity,
    }
    arguments.update(overrides)
    return BoundWorkload(descriptor, **arguments), subject


def bound(name: str, call: Any, **overrides: Any) -> BoundWorkload:
    return make(name, call, **overrides)[0]


def sweep(name: str, call: Any) -> Any:
    workload, subject = make(name, call)
    return screen_growth(workload, counters=[DB_QUERY], extra_counters=subject.payload)


# ------------------------------------ AC 1 and 2: every workload, every metric


def test_every_workload_is_measured_at_three_scale_points(query_counter: None) -> None:
    """AC 1 asks for two or more. Three is the floor, because `fit_growth` needs
    three and two points define a line through themselves — S-3.2 refuses a
    two-point sweep outright rather than fitting one."""
    screened = sweep("n.plus.one", list_books_n_plus_one)

    assert screened.scales == SCREENING_SCALES
    assert len(screened.workload.observations) == len(SCREENING_SCALES)


def test_a_growth_ratio_is_computed_for_every_metric(query_counter: None) -> None:
    """AC 2. The ratio is the raw largest-over-smallest — the number a reader can
    check against two measurements without re-deriving a fit."""
    screened = sweep("n.plus.one", list_books_n_plus_one)

    queries = screened.metric(DB_QUERY)
    smallest, largest = screened.workload.observations[0], screened.workload.observations[-1]

    assert queries.ratio == pytest.approx(largest.metrics[DB_QUERY] / smallest.metrics[DB_QUERY])
    assert queries.kind is MetricKind.COUNT
    assert set(screened.growth) >= {DB_QUERY, CELLS, RESPONSE_BYTES}


def test_the_ratio_and_the_fit_are_both_reported(query_counter: None) -> None:
    """They answer different questions and disagree in the case that matters: a
    16x sweep can move a metric by a factor that reads alarming while the fitted
    exponent says constant, and only publishing both makes the result arguable.
    """
    screened = sweep("n.plus.one", list_books_n_plus_one)
    queries = screened.metric(DB_QUERY)

    assert queries.ratio is not None
    assert queries.growth is Growth.LINEAR
    assert queries.fit.exponent is not None


def test_the_screen_separates_the_defect_from_its_control(query_counter: None) -> None:
    """The whole point of screening, on the pair the fixture ships for it."""
    defect = sweep("n.plus.one", list_books_n_plus_one)
    control = sweep("batched", list_books_batched)

    assert defect.metric(DB_QUERY).growth is Growth.LINEAR
    assert control.metric(DB_QUERY).growth is Growth.CONSTANT
    assert (control.metric(DB_QUERY).ratio or 0) == pytest.approx(1.0)


def test_the_decoy_is_measured_as_constant_however_expensive_it_is(
    query_counter: None,
) -> None:
    """`summarize_with_fixed_floor` issues 37 queries at every volume. A screen
    that ranked on cost would investigate it; this one records that it did not
    grow and leaves the ranking to S-4.3."""
    decoy = sweep("fixed.floor", summarize_with_fixed_floor)

    assert decoy.metric(DB_QUERY).growth is Growth.CONSTANT
    assert (decoy.metric(DB_QUERY).ratio or 0) == pytest.approx(1.0)


def test_the_over_fetch_is_flat_in_queries_and_not_in_payload(query_counter: None) -> None:
    """The workload the leading instrument cannot see. Both it and its control
    issue one query at every volume; the guard counter is the only thing that
    separates them, and screening measures every metric for that reason."""
    defect = sweep("over.fetch", list_titles_over_fetching)
    control = sweep("narrow", list_titles_narrow)

    assert defect.metric(DB_QUERY).growth is control.metric(DB_QUERY).growth
    assert (defect.metric(CELLS).ratio or 0) > (control.metric(CELLS).ratio or 0) * 0.99
    assert (
        defect.workload.observations[-1].metrics[CELLS]
        > (control.workload.observations[-1].metrics[CELLS])
    )


def test_screening_fills_in_the_observations_that_answer_f6(query_counter: None) -> None:
    """The composition with S-4.1. Work verification needs two scale points at a
    four-fold spread, and screening is the step that produces them — so a
    workload arrives at S-4.3 with the question already answerable."""
    screened = sweep("n.plus.one", list_books_n_plus_one)

    assert screened.workload.scale_ratio == pytest.approx(16.0)
    assert screened.workload.work_verified


def test_observations_are_recorded_by_scale_however_the_sweep_ran(
    query_counter: None,
) -> None:
    """S-3.2 lets a caller randomize the order of scale points, and
    `01-primitives.md` §10 is why: measuring smallest-to-largest every time
    confounds volume with whatever drifts over the session. The artifact still
    has to render identically between runs for ADR 002's cached prefix, so the
    sweep order and the recorded order are two different things.
    """
    workload, subject = make("n.plus.one", list_books_n_plus_one)

    screened = screen_growth(
        workload,
        scales=(160, 10, 40),
        counters=[DB_QUERY],
        extra_counters=subject.payload,
    )

    assert screened.scales == (160, 10, 40)
    assert [point.scale for point in screened.workload.observations] == [10, 40, 160]


def test_a_metric_that_starts_at_zero_has_no_ratio(query_counter: None) -> None:
    """Not infinity, and not a large number.

    A metric absent at the small end is a fact about the small end — a page that
    never overflowed, a cache that covered the empty case, a queryset that never
    fired — and dividing by it would turn that into the largest growth figure in
    the screen, which is exactly where S-4.3's ranking would put it.
    """
    workload, subject = make("overflow", list_books_n_plus_one)

    screened = screen_growth(
        workload,
        counters=[DB_QUERY],
        extra_counters=subject.beyond_first_page,
    )

    assert screened.workload.observations[0].metrics["rows_beyond_page"] == 0
    assert screened.metric("rows_beyond_page").ratio is None
    assert screened.metric("rows_beyond_page").fit.slope > 0


def test_a_metric_nobody_measured_raises_rather_than_reading_as_flat(
    query_counter: None,
) -> None:
    """ADR 013's rule. A typo that returned "no growth" would become an
    exclusion, which is the expensive direction."""
    screened = sweep("n.plus.one", list_books_n_plus_one)

    with pytest.raises(ScreeningError, match="not a metric that stayed flat"):
        screened.metric("db.querys")


def test_the_conditions_travel_with_the_numbers(query_counter: None) -> None:
    """`CLAUDE.md`: exclusions carry their preconditions. *Queries flat across a
    sixteenfold increase* means one thing under a uniform fixture with a fresh
    process per point and something much weaker otherwise."""
    screened = sweep("batched", list_books_batched)

    assert screened.distribution is Distribution.UNIFORM
    assert screened.reset_strategy is ResetStrategy.SNAPSHOT_RESTORE
    assert screened.cache_control.value


def test_screening_many_workloads_returns_one_result_each(query_counter: None) -> None:
    subjects = [
        bound("n.plus.one", list_books_n_plus_one),
        bound("batched", list_books_batched),
    ]

    screened = screen(subjects, counters=[DB_QUERY])

    assert [item.workload.id for item in screened] == ["n.plus.one", "batched"]


def test_an_empty_screen_is_an_error_and_not_a_null_result() -> None:
    """A null result names what it looked at. S-4.5 cannot report on nothing, and
    a screen of no workloads reporting "nothing found" is the exact shape of a
    missed finding."""
    with pytest.raises(ScreeningError, match="not a null result"):
        screen([])


def test_a_workload_that_cannot_be_screened_stops_the_screen(query_counter: None) -> None:
    """Skipping it and carrying on looks like progress and is not: a workload
    absent from a screen is indistinguishable from one screened and found
    healthy."""
    unmeasurable = bound("no.cache.control", list_books_batched, process_identity=None)

    with pytest.raises(CacheControlError):
        screen([bound("fine", list_books_batched), unmeasurable], counters=[DB_QUERY])


# -------------------------------------- AC 3: zero model calls, structurally


def test_no_llm_sdk_is_reachable_from_the_screening_package() -> None:
    """AC 3, and it holds after E7 rather than only before it.

    The literal test the story describes — run screening with no client
    configured — passes today because no client exists anywhere, so it asserts
    nothing and would keep passing once one did. This walks the transitive import
    graph in a clean interpreter instead: a screen that *imported* a model client
    could call one whether or not it happened to, and "did not this time" is not
    a property a test can hold onto.
    """
    script = "import sys\nimport coldfix.screening.growth\nprint('\\n'.join(sorted(sys.modules)))\n"
    loaded = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.split()

    roots = {name.split(".")[0] for name in loaded} | set(loaded)
    reachable = sorted(LLM_SDKS & roots)

    assert not reachable, f"screening can reach {reachable}, so it can make a model call"


def test_a_whole_screen_runs_with_the_socket_layer_removed(
    query_counter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half, and the one that covers a client reached by any route.

    No model call survives the loss of `socket.socket`, whichever SDK makes it.
    The workload here needs no network of its own, so what this establishes is
    that **the screening layer** adds none — a real subject would open a database
    connection, and that is the workload's socket rather than the screen's.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        message = "screening opened a socket, which it has no reason to do"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    screened = screen([bound("n.plus.one", list_books_n_plus_one)], counters=[DB_QUERY])

    assert screened[0].metric(DB_QUERY).growth is Growth.LINEAR

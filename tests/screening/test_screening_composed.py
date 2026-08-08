"""Epic 4 composed: six workloads in, a plan or a null result out, no model touched.

Every other file here tests one stage. This one runs the epic as a whole, for the
reason Epic 2's composition established and Epic 3's confirmed: per-module
verification says nothing about composition, and both of those epics had defects
that no single-module test could reach.

Epic 4's purpose, from the backlog, is *find what is worth investigating using
zero model calls*. So the composition is the whole of that sentence — a caller
hands over the workloads a project has and gets back either what to investigate
or an honest statement that there is nothing, and nothing in between decides
anything a model would.

The subject is the planted set, screened as an investigation would screen it: the
N+1 and its batched control, the over-fetch invisible to query counting and its
projected control, the decoy that must never be called a defect, and the
downstream-cost workload. Six workloads, and the epic has to get all six right at
once — which is different from getting each right in its own file.
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
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.assess import Assessment, assess
from coldfix.screening.budget import DEFAULT_FINDINGS_CAP, Plan
from coldfix.screening.growth import screen
from coldfix.screening.null import NullResult
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
    render_with_expensive_downstream,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"

# Every package name that would mean a model call is reachable. Asserted over the
# whole screening package here rather than over one module, which is what S-4.2
# checked.
LLM_SDKS = frozenset(
    {
        "anthropic",
        "openai",
        "langchain",
        "langgraph",
        "litellm",
        "cohere",
        "mistralai",
        "ollama",
        "transformers",
    }
)


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


def bound(name: str, call: Any) -> BoundWorkload:
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
    return BoundWorkload(
        descriptor,
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        # Each workload's own, which is the correction this composition forced:
        # one callable for the whole project read one subject's store six times.
        extra_counters=subject.payload,
    )


DEFECTS = {
    "n.plus.one": list_books_n_plus_one,
    "over.fetch": list_titles_over_fetching,
    "downstream": render_with_expensive_downstream,
}
CONTROLS = {
    "batched": list_books_batched,
    "narrow": list_titles_narrow,
    "fixed.floor": summarize_with_fixed_floor,
}


def assessed(*names: str, cap: int = DEFAULT_FINDINGS_CAP) -> Assessment:
    """The whole epic, in one call, the way a caller has to be able to use it."""
    catalogue = {**DEFECTS, **CONTROLS}
    workloads = [bound(name, catalogue[name]) for name in names]
    return assess(workloads, counters=[DB_QUERY], cap=cap)


# ------------------------------------------- the epic performing its own purpose


def test_screening_a_whole_project_produces_a_plan(query_counter: None) -> None:
    """One call in, a decision out.

    Before this, a caller had to run four stages by hand and branch on whether
    the ranking came back empty — and getting that branch wrong meant asking for
    a plan when the answer was a null result, which returned an empty plan
    silently.
    """
    outcome = assessed(*DEFECTS, *CONTROLS)

    assert isinstance(outcome, Plan)
    assert "n.plus.one" in outcome.investigate
    assert set(outcome.healthy) >= {"batched", "narrow"}


def test_screening_a_clean_project_produces_a_null_result(query_counter: None) -> None:
    """The other branch, and the one a caller is most likely to get wrong: a
    screen that flags nothing has to produce S-4.5's answer rather than a plan
    that investigates nothing."""
    outcome = assessed(*CONTROLS)

    assert isinstance(outcome, NullResult)
    assert set(outcome.screened) == set(CONTROLS)
    assert "flagged none" in outcome.report()


def test_a_plan_and_a_null_result_are_never_both_possible(query_counter: None) -> None:
    """The two outcomes are exclusive by construction, which is what makes the
    branch safe to remove from every caller."""
    flagged = assessed(*DEFECTS, *CONTROLS)
    clean = assessed(*CONTROLS)

    assert isinstance(flagged, Plan) != isinstance(flagged, NullResult)
    assert isinstance(clean, NullResult) != isinstance(clean, Plan)


# --------------------------------------- the six workloads, all at once


def test_the_defects_are_flagged_and_the_controls_are_not(query_counter: None) -> None:
    """The whole planted set screened together, which is different from each one
    screened in its own file: a detector that reports N+1 unconditionally passes
    three of these and fails the other three."""
    outcome = assessed(*DEFECTS, *CONTROLS, cap=6)
    assert isinstance(outcome, Plan)

    investigated = set(outcome.investigate)

    assert "n.plus.one" in investigated
    assert {"batched", "narrow"} <= set(outcome.healthy)
    assert "batched" not in investigated
    assert "narrow" not in investigated


def test_the_decoy_is_never_investigated(query_counter: None) -> None:
    """`summarize_with_fixed_floor` issues 37 queries at any volume and costs more
    than the real N+1 at every scale a small dataset has. The fixture README is
    explicit that a fix here is the metastability trap, so the epic has to reach
    the end without putting it on the list."""
    outcome = assessed(*DEFECTS, *CONTROLS, cap=6)
    assert isinstance(outcome, Plan)

    assert "fixed.floor" not in outcome.investigate
    assert "fixed.floor" in outcome.healthy


def test_the_screen_eliminates_most_of_the_project_before_any_agent_runs(
    query_counter: None,
) -> None:
    """`04-cost.md` §9 counts screening as the largest cost gate in the system,
    at roughly 70% of workloads eliminated. Measured on the planted set rather
    than assumed — and the number is the fixture's, not a claim about repositories
    in general."""
    outcome = assessed(*DEFECTS, *CONTROLS, cap=6)
    assert isinstance(outcome, Plan)

    screened = len(DEFECTS) + len(CONTROLS)
    eliminated = len(outcome.healthy) / screened

    assert eliminated >= 0.5


def test_the_screen_catches_one_of_the_three_planted_defects(query_counter: None) -> None:
    """Epic 4's actual coverage, measured rather than assumed — and the number is
    one of three.

    **The over-fetch is invisible to this screen and that is not a bug in it.**
    Every metric on `list_titles_over_fetching` grows linearly with volume, and
    so does every metric on its projected control; the defect is that the payload
    is five times wider than the response needs, which is a comparison against a
    *floor* rather than against a growth curve. S-3.18's `fields_required_by` is
    the instrument, and it belongs to diagnosis.

    **`render_with_expensive_downstream` is invisible for a different reason.**
    Its cost is CPU downstream of a two-query fetch, and a duration cannot flag
    below the noise floor — S-3.19 exists because timing cannot resolve there.

    Recorded as a test rather than a comment so that a later change to screening
    which does catch them fails here and gets read.
    """
    outcome = assessed(*DEFECTS, *CONTROLS, cap=6)
    assert isinstance(outcome, Plan)

    assert set(outcome.investigate) == {"n.plus.one"}
    assert {"over.fetch", "downstream"} <= set(outcome.healthy)


def test_the_cap_holds_across_the_whole_project(query_counter: None) -> None:
    """S-4.4 composed with the rest: more flagged than the budget allows, and the
    remainder listed rather than dropped.

    Two N+1 workloads, because the planted set contains exactly one shape this
    screen flags — which the test above records. A cap needs something to cap.
    """
    workloads = [
        bound("tickets.list", list_books_n_plus_one),
        bound("followups.list", list_books_n_plus_one),
        bound("batched", list_books_batched),
    ]

    outcome = assess(workloads, counters=[DB_QUERY], cap=1)
    assert isinstance(outcome, Plan)

    assert len(outcome.investigate) == 1
    assert outcome.deferred
    assert not outcome.within_budget
    assert "listed below rather than dropped" in outcome.report()


def test_each_workload_is_measured_with_its_own_guard_counters(
    query_counter: None,
) -> None:
    """The correction this composition forced, asserted where it shows.

    Guard counters began as one callable passed to `screen` for the whole
    project, so a screen of six workloads read one subject's store six times and
    attributed it to the other five. Never exercised until a screen ran more than
    one workload with guard counters at once, which is a thing only a composition
    does. The N+1 drags back every column of every book; the projected control
    returns one column — so identical payload numbers here would mean the
    counters came from the wrong subject.
    """
    workloads = [bound("n.plus.one", list_books_n_plus_one), bound("narrow", list_titles_narrow)]

    screened = screen(workloads, counters=[DB_QUERY])
    payloads = {
        item.workload.id: item.workload.observations[-1].metrics[CELLS] for item in screened
    }

    assert payloads["n.plus.one"] > payloads["narrow"]
    assert payloads["narrow"] > 0


def test_the_same_project_screens_to_the_same_decision_twice(
    query_counter: None,
) -> None:
    """ADR 002 needs anything rendered into a cached prefix to be stable between
    runs. Timings differ every time; what must not is which workloads a screen
    decides to investigate, or the order it puts them in.

    Two flagged workloads, because an ordering of one is stable however it is
    produced — the first version of this test shuffled and passed.
    """
    workloads = [
        bound("tickets.list", list_books_n_plus_one),
        bound("followups.list", list_books_n_plus_one),
        bound("batched", list_books_batched),
    ]
    again = [
        bound("tickets.list", list_books_n_plus_one),
        bound("followups.list", list_books_n_plus_one),
        bound("batched", list_books_batched),
    ]

    first = assess(workloads, counters=[DB_QUERY], cap=6)
    second = assess(again, counters=[DB_QUERY], cap=6)
    assert isinstance(first, Plan)
    assert isinstance(second, Plan)

    assert len(first.investigate) == 2
    assert first.investigate == second.investigate
    assert first.healthy == second.healthy


# ----------------------------------------------- zero model calls, epic-wide


def test_no_llm_sdk_is_reachable_from_any_of_the_screening_package() -> None:
    """S-4.2 asserted this over one module. The epic's claim is about the layer,
    so the walk covers everything the package imports."""
    script = "import sys\nimport coldfix.screening.assess\nprint('\\n'.join(sorted(sys.modules)))\n"
    loaded = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.split()

    roots = {name.split(".")[0] for name in loaded}
    reachable = sorted(LLM_SDKS & roots)

    assert not reachable, f"screening can reach {reachable}, so it can make a model call"


def test_the_whole_epic_runs_with_the_socket_layer_removed(
    query_counter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No model call survives losing `socket.socket`, whichever SDK makes it.
    The planted workloads need no network of their own, so what this establishes
    is that the screening layer adds none."""

    def refuse(*args: object, **kwargs: object) -> None:
        message = "screening opened a socket, which it has no reason to do"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    outcome = assessed(*DEFECTS, *CONTROLS, cap=6)

    assert isinstance(outcome, Plan)
    assert outcome.investigate

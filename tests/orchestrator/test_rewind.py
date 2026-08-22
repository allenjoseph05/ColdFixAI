"""Rewinding the code and keeping the learning.

S-12.6, and `08-audit.md` F5. The story's three criteria are a mechanism, a
guarantee, and a behaviour, and only the third needed anything built: the split
that protects the learning is S-6.1's and S-6.2's, and this checks it holds
rather than reimplementing it.

**F5 is reproduced before it is fixed.** The first test here drives a run,
rewinds it, and asserts the agent repeats itself — because a fix whose defect was
never demonstrated is a fix nobody can check. Every later test is that same run
with the memory reconnected.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.graph import Node, Wiring, assemble
from coldfix.orchestrator.resume import start
from coldfix.orchestrator.rewind import (
    NoSuchCheckpointError,
    already_failed,
    before,
    history,
    rewind,
)
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.persistent import SharedStoreError, refuse_shared_store

PREFETCH = {"approach": "prefetch the author", "failure": "still 1001 queries"}

UPDATES: Mapping[str, Mapping[str, object]] = {
    "ground": {"project": {"adapter": "django"}},
    "screen": {"screening": {"shop.books.list": {"flagged": True, "growth": {}}}},
    "investigate": {"target": "shop.books.list"},
    "audit_finding": {"route": "REPAIR"},
    "repair": {"attempts": [PREFETCH]},
    "audit_patch": {"route": "SHIP"},
    "ship": {"screening": {}, "route": None},
}


def build() -> Wiring:
    """A run whose repair always reaches for the same approach.

    **Deliberately not adaptive**, because the question is what the *harness*
    remembers across a rewind. A step that varied its answer would pass the test
    for a reason that has nothing to do with F5.
    """

    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            return dict(UPDATES.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


def a_completed_run(store: Path, run_id: str) -> Any:
    saver_context = for_development(store)
    saver = saver_context.__enter__()
    graph = assemble(build(), saver, gated=False)
    start(graph, run_id)
    return graph, saver, saver_context


# ============================================ AC 1 — the rewind restores state


def test_the_history_reads_oldest_first(tmp_path: Path) -> None:
    """LangGraph yields newest first, which is right for *what happened last* and
    wrong for choosing a rewind: somebody scanning for *the point before repair*
    is reading a story forwards."""
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "forwards")
    try:
        points = history(graph, "forwards")

        assert points[0].next_nodes == ("__start__",)
        assert points[-1].next_nodes == ()
        assert len(points) == len(Node) + 2, "one before each node, one for start, one for the end"
    finally:
        context.__exit__(None, None, None)


def test_rewinding_restores_the_state_as_it_was_at_that_point(tmp_path: Path) -> None:
    """**AC 1.** The checkpoint before `repair` holds what the phases before it
    wrote and nothing the phases after it did."""
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "restore")
    try:
        point = before(graph, "restore", Node.REPAIR)

        assert point.values["target"] == "shop.books.list", "investigation survived"
        assert point.values["attempts"] == [], "and the repair had not happened yet"
    finally:
        context.__exit__(None, None, None)


def test_a_node_the_run_never_reached_has_no_checkpoint(tmp_path: Path) -> None:
    """A caller asking for a point in a story that did not happen is told which
    points there **are**, rather than handed the nearest one.

    The run here screens nothing, so `after_screen` sends it to `END` as a null
    result and `repair` never happens — which is a real shape (`00-BRIEF.md` §9),
    not a contrived one.
    """
    healthy = dict(UPDATES) | {"screen": {"screening": {}}}

    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            return dict(healthy.get(name, {}))

        return step

    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(Wiring(**{i.value: make(i.value) for i in Node}), saver, gated=False)
        start(graph, "nothing-found")

        with pytest.raises(NoSuchCheckpointError, match="never stood before 'repair'"):
            before(graph, "nothing-found", Node.REPAIR)


def test_a_thread_that_was_never_started_says_so(tmp_path: Path) -> None:
    """Distinct from the above: no history at all, rather than a history that
    does not include the node. The message names which it is."""
    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(build(), saver, gated=False)

        with pytest.raises(NoSuchCheckpointError, match="nothing was ever checkpointed"):
            before(graph, "never-ran", Node.REPAIR)


# ============================================ F5, reproduced


def test_a_rewind_makes_the_agent_repeat_itself(tmp_path: Path) -> None:
    """**F5, measured rather than described.**

    *Time travel restores state at checkpoint T. But the reason for rewinding is
    a failure discovered at T+n — and that failure record lives in the state being
    discarded. We rewind and the agent repeats the same attempt.*

    This is the defect, and it is the reason `remembered` exists. The test asserts
    the repetition, so a change that quietly fixed it here would fail and have to
    be explained.
    """
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "f5")
    try:
        discarded = history(graph, "f5")[-1].values["attempts"]
        assert discarded, "the completed run learned something"

        point = before(graph, "f5", Node.REPAIR)
        assert point.values["attempts"] == [], "which the rewind point does not hold"

        retried = rewind(graph, point)["attempts"]

        assert already_failed(discarded, retried) == ("prefetch the author",)
    finally:
        context.__exit__(None, None, None)


def test_a_rewind_forks_the_history_rather_than_appending_to_it(tmp_path: Path) -> None:
    """**Measured, because the first version of this assumed otherwise.**

    A rewound run does not carry the later branch's writes forward and then add
    to them — it starts from the checkpoint's values. So `attempts` holds one
    entry afterwards, not two, and nothing is repeated *within* either branch.
    The repetition is between them, which is why F5 says nobody notices.
    """
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "fork")
    try:
        original = history(graph, "fork")[-1].values["attempts"]

        retried = rewind(graph, before(graph, "fork", Node.REPAIR))["attempts"]

        assert len(original) == 1
        assert len(retried) == 1, "the new branch starts from the checkpoint, not from the end"
        assert already_failed([], retried) == (), "so neither branch contains a repeat of itself"
    finally:
        context.__exit__(None, None, None)


def test_a_run_that_was_not_rewound_repeats_nothing(tmp_path: Path) -> None:
    """The control. Without it the test above would pass on a wiring that appends
    twice for reasons of its own."""
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "once")
    try:
        attempts = history(graph, "once")[-1].values["attempts"]
        assert already_failed([], attempts) == (), "nothing was discarded, so nothing was rebought"
    finally:
        context.__exit__(None, None, None)


# ============================================ AC 2 — the store is out of reach


def test_a_sqlite_checkpointer_is_trivially_not_the_store(tmp_path: Path) -> None:
    """**AC 2's first arm.** ADR 003's development checkpointer is a file, and a
    file is not a database — so a rewind restoring it cannot restore the journal
    the learning lives in."""
    refuse_shared_store(
        VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state"),
        tmp_path / "run.sqlite",
    )


def test_a_postgres_checkpointer_sharing_the_store_is_refused() -> None:
    """**AC 2's second arm, and the one worth testing.**

    S-12.2 supports Postgres for concurrent campaigns, which is the configuration
    where the two *could* collide. Testing only the SQLite arm would prove the
    trivial case and leave the reachable one open — and dropping checkpoints is
    routine, done with a `DROP` that knows nothing about the store.
    """
    with pytest.raises(SharedStoreError, match="ADR 003 keeps them apart"):
        refuse_shared_store(
            VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state"),
            "postgresql://u:p@localhost:5432/coldfix_state",
        )


def test_the_store_is_not_among_what_a_rewind_addresses(tmp_path: Path) -> None:
    """A rewind is a graph and a checkpoint id, and there is no third argument
    through which the persistent store could arrive. The guarantee is the absence
    rather than a check inside `rewind`."""
    graph, _saver, context = a_completed_run(tmp_path / "run.sqlite", "reach")
    try:
        point = before(graph, "reach", Node.REPAIR)

        assert set(point.config["configurable"]) >= {"thread_id", "checkpoint_id"}
        assert "store" not in str(point.config)
        assert set(inspect.signature(rewind).parameters) == {"graph", "point"}
    finally:
        context.__exit__(None, None, None)

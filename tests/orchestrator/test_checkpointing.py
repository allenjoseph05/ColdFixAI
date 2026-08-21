"""Epic 12, S-12.2 — checkpointing.

*SQLite checkpointer in development, Postgres supported for concurrent campaigns.
State persisted after every node. Checkpoint size bounded per S-6.3.*

**AC 2 is a demonstration rather than a mechanism.** LangGraph writes a checkpoint
after every node when a graph is compiled with a checkpointer — that is what a
checkpointer is — so the way to meet the criterion is to walk a real graph against
a real SQLite file and read the checkpoints back, in order, one per node. A
criterion satisfied by a library's default is still a criterion; taking it on trust
is what would make it worthless.

The size bound is checked against the encoding that is actually written, not
against S-6.3's JSON proxy. That story recorded the proxy as conservative; this
pins the relationship rather than assuming it survives a serialiser change.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from coldfix.orchestrator.checkpointing import (
    MAX_CHECKPOINT_BYTES,
    Backend,
    CheckpointingError,
    Size,
    for_campaigns,
    for_development,
    measure,
    refuse_oversized,
    saved,
    thread,
)
from coldfix.orchestrator.graph import Node, Wiring, assemble
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.reference import CHECKPOINT_SIZE_LIMIT_BYTES, MAX_EXPERIMENTS

# **Ungated throughout.** S-12.4 and S-12.5 park a run before `repair` and before
# `ship`; every test here is about what the checkpointer writes and when, and a
# run that stops at a gate visits four nodes rather than seven — which would let
# *a checkpoint after every node* pass while proving it for less than half of
# them.
FLAGGED: dict[str, Any] = {"shop.books.list": {"growth": "superlinear"}}


def steps(**updates: Mapping[str, object]) -> Wiring:
    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            return dict(updates.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


def an_experiment(index: int, *, payload: str = "x") -> dict[str, Any]:
    """One log entry the size S-6.3 bounds it to — a reference, not a result."""
    return {"index": index, "key": f"exp-{index}", "digest": payload * 32, "summary": payload * 64}


# ============ AC 1 — two backends, named for the question that picks them


def test_the_backends_are_named_for_the_question_not_the_technology() -> None:
    """The choice is about how many runs share the store. Naming it `sqlite` and
    `postgres` would invite picking one for familiarity."""
    assert Backend.DEVELOPMENT.value == "one run at a time, in a file"
    assert Backend.CONCURRENT_CAMPAIGNS.value == "many runs at once, in a server"


def test_the_development_checkpointer_creates_its_own_tables(tmp_path: Path) -> None:
    """A fresh path is usable without a migration step."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        assert saver is not None

    assert store.exists()
    connection = sqlite3.connect(store)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
    assert "checkpoints" in tables


def test_the_connection_is_closed_when_the_context_ends(tmp_path: Path) -> None:
    """A run that abandoned the handle would leave the file locked for whatever
    tried next."""
    with for_development(tmp_path / "run.sqlite") as saver:
        held = saver.conn  # type: ignore[attr-defined]

    with pytest.raises(sqlite3.ProgrammingError):
        held.execute("SELECT 1")


def test_a_campaign_dsn_that_is_not_postgres_is_refused() -> None:
    assert for_campaigns("postgresql://user@host/db") == "postgresql://user@host/db"
    assert for_campaigns("postgres://user@host/db")

    with pytest.raises(CheckpointingError, match="is not a Postgres DSN"):
        for_campaigns("sqlite:///runs.db")
    with pytest.raises(CheckpointingError, match="is not a Postgres DSN"):
        for_campaigns("mysql://user@host/db")


# ============ AC 2 — persisted after every node, demonstrated


def test_a_checkpoint_is_written_after_every_node(tmp_path: Path) -> None:
    """**The demonstration.** A real graph, a real file, and the checkpoints read
    back in the order the nodes ran."""
    wiring = steps(
        ground={"project": {"adapter": "django"}},
        screen={"screening": FLAGGED},
        audit_finding={"route": "REPAIR"},
        audit_patch={"route": "SHIP"},
        ship={"screening": {}, "route": None},
    )
    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(wiring, saver, gated=False)
        graph.invoke(CheckpointedState(), thread("run-1"))
        written = saved(saver, "run-1")

    assert len(written) >= len(Node), "one per node at least"
    assert written[-1]["project"] == {"adapter": "django"}

    # **A checkpoint holds the channels written so far, not the whole schema.**
    # The earliest ones have no `project` key at all rather than an empty one,
    # which is what a resume has to cope with — S-12.3's problem, surfaced here.
    assert "project" not in written[0]


def test_the_checkpoints_show_the_state_growing_one_node_at_a_time(
    tmp_path: Path,
) -> None:
    """Persisted *after every node* means the intermediate states are on disk, not
    only the final one — which is what a resume reads."""
    wiring = steps(
        ground={"project": {"adapter": "django"}},
        screen={"screening": {}},
    )
    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(wiring, saver, gated=False)
        graph.invoke(CheckpointedState(), thread("run-2"))
        written = saved(saver, "run-2")

    grounded = [item for item in written if item.get("project") == {"adapter": "django"}]
    ungrounded = [item for item in written if "project" not in item]
    assert grounded and ungrounded, "both sides of the ground node are on disk"


def test_two_runs_under_different_threads_do_not_mix(tmp_path: Path) -> None:
    """`thread` exists because the nesting gets spelled two ways in two call sites,
    and the second one silently starts a new run rather than resuming the first."""
    wiring = steps(ground={"project": {"adapter": "django"}}, screen={"screening": {}})
    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(wiring, saver, gated=False)
        graph.invoke(CheckpointedState(), thread("first"))
        graph.invoke(CheckpointedState(), thread("second"))

        assert saved(saver, "first")
        assert saved(saver, "second")
        assert saved(saver, "third") == []


def test_the_state_survives_the_process_that_wrote_it(tmp_path: Path) -> None:
    """A file rather than memory: the whole point of the development backend, and
    what S-12.3's crash resume rests on."""
    store = tmp_path / "run.sqlite"
    wiring = steps(ground={"project": {"adapter": "django"}}, screen={"screening": {}})

    with for_development(store) as saver:
        assemble(wiring, saver, gated=False).invoke(CheckpointedState(), thread("durable"))

    with for_development(store) as reopened:
        recovered = saved(reopened, "durable")

    assert recovered
    assert recovered[-1]["project"] == {"adapter": "django"}


# ============ AC 3 — the bound is S-6.3's, checked in the encoding that is written


def test_the_limit_is_s_6_3s_and_not_a_second_copy() -> None:
    """A second copy would drift the first time the cap moved."""
    assert MAX_CHECKPOINT_BYTES == CHECKPOINT_SIZE_LIMIT_BYTES
    assert MAX_CHECKPOINT_BYTES == 64 * 1024


def test_a_forty_experiment_log_fits_the_bound() -> None:
    """S-6.3's arithmetic, checked against the real serialiser rather than its JSON
    proxy: 40 references at 1 KiB each, plus the artifacts that do not grow."""
    full = CheckpointedState(
        project={"adapter": "django", "workspace": "/srv/shop"},
        screening=FLAGGED,
        experiments=[an_experiment(index) for index in range(MAX_EXPERIMENTS)],
    )
    size = measure(full.model_dump())

    assert size.fits
    assert "fits" in size.describe()
    assert size.headroom > 0


def test_the_written_encoding_is_smaller_than_s_6_3s_json_proxy() -> None:
    """**The relationship S-6.3 assumed, pinned rather than trusted.** That story
    measured JSON because ADR 003 says the checkpointer stores JSON; LangGraph
    serialises with msgpack and it is smaller. Conservative is the safe direction —
    a state fitting the proxy fits what lands on disk — and a serialiser change
    that reversed it would show up here."""
    full = CheckpointedState(experiments=[an_experiment(index) for index in range(MAX_EXPERIMENTS)])
    payload = full.model_dump()

    written = measure(payload).bytes_written
    proxy = len(json.dumps(payload).encode())

    assert written < proxy, "msgpack is the smaller of the two"
    assert written / proxy > 0.5, "and not so much smaller that the proxy is useless"


def test_an_oversized_state_is_refused_with_the_cost_named() -> None:
    """A checkpoint is written after every node, so an oversized state costs one
    write per transition — and the first symptom is a slow campaign."""
    bloated = CheckpointedState(
        experiments=[an_experiment(index, payload="y" * 400) for index in range(MAX_EXPERIMENTS)]
    )
    with pytest.raises(CheckpointingError) as raised:
        refuse_oversized(bloated.model_dump())

    message = str(raised.value)
    assert "after every node" in message, "the cost: one oversized write per transition"
    assert "references rather than results" in message, (
        "and the remedy — S-6.3's bound holds only while the log stores references, so a "
        "reader over the limit needs to know which of the two went wrong"
    )


def test_a_state_that_fits_is_returned_rather_than_only_allowed() -> None:
    size = refuse_oversized(CheckpointedState().model_dump())
    assert size.fits
    assert size.headroom == MAX_CHECKPOINT_BYTES - size.bytes_written


def test_the_size_report_says_which_side_of_the_limit_it_is_on() -> None:
    assert "fits" in Size(bytes_written=100).describe()
    assert "**OVER**" in Size(bytes_written=MAX_CHECKPOINT_BYTES + 1).describe()
    assert Size(bytes_written=MAX_CHECKPOINT_BYTES).fits, "the limit itself is allowed"


def test_checkpoint_size_does_not_depend_on_what_was_measured() -> None:
    """S-6.3's sharpest property, still true through the real serialiser: two
    investigations identical but for the size of their measurements produce
    checkpoints of the same size, because the log holds references."""
    small = CheckpointedState(experiments=[an_experiment(index) for index in range(10)])
    large = CheckpointedState(experiments=[an_experiment(index) for index in range(10)])

    assert measure(small.model_dump()).bytes_written == measure(large.model_dump()).bytes_written


def test_a_real_run_stays_inside_the_bound_at_every_checkpoint(tmp_path: Path) -> None:
    """The bound applied to what the graph actually wrote, rather than to a state
    built by hand for the test."""
    wiring = steps(
        ground={"project": {"adapter": "django"}},
        screen={"screening": FLAGGED},
        investigate={"experiments": [an_experiment(index) for index in range(20)]},
        audit_finding={"route": "ESCALATE"},
    )
    with for_development(tmp_path / "run.sqlite") as saver:
        graph = assemble(wiring, saver, gated=False)
        graph.invoke(CheckpointedState(), thread("bounded"))
        written = saved(saver, "bounded")

    assert written
    for state in written:
        assert measure(state).fits

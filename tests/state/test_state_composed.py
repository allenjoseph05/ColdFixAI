"""Epic 6 composed: one investigation, crashed, resumed, shipped and rewound.

Every other file here tests one story. This one performs the epic's own sentence
— *state that survives crashes, and knowledge that survives rewinds* — for the
reason Epics 2 through 5 all established: a suite where each file tests one
import says nothing about whether the parts fit together, and every composition
check so far has found defects no single-module test could reach.

The Postgres-backed ones are marked; the rest run in the fast subset.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, JsonValue

from coldfix.bench.execute import execute
from coldfix.replay.cache import Determinism, ExperimentKey, ExperimentSpec, ReplayCache
from coldfix.sandbox import docker_available
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import wait_until_ready
from coldfix.state.checkpoint import CheckpointedState, check_update, node
from coldfix.state.investigation import (
    CheckpointTooLargeError,
    InvestigationError,
    UnreferencedResultError,
    apply_ship,
    check_state,
    coverage_from_state,
    experiments_of,
    learn,
    read_experiment,
    record_experiment,
)
from coldfix.state.persistent import Collection, PersistentStore
from coldfix.state.reference import (
    CHECKPOINT_SIZE_LIMIT_BYTES,
    ExperimentRef,
    checkpoint_size_bytes,
)
from coldfix.state.staleness import Coverage, FindingAction, Patch


class Measurement(BaseModel):
    workload: str
    stdout: str


def key_for(index: int, workload: str = "api.books.list") -> ExperimentKey:
    return ExperimentKey(
        repo_sha="9c68122+deadbeef",
        workload_id=workload,
        experiment_spec=ExperimentSpec(primitive="scale_volume", parameters={"run": index}),
        fixture_hash="f" * 64,
    )


def measured(size: int = 30_000) -> Measurement:
    return Measurement(workload="api.books.list", stdout="x" * size)


# ============================ the join that had no correct form: the log's contents


def test_a_node_can_append_a_whole_measurement_and_nothing_objects() -> None:
    """The defect, reproduced against the shipped modules.

    S-6.3 bounds a checkpoint by storing references; S-6.1's channel is
    `list[JsonValue]`. A node that appends the measurement itself satisfies the
    schema, the reducer and `check_update` — every check either story makes —
    and F13's guarantee holds only as long as every caller remembers.
    """
    state = CheckpointedState(experiments=[measured().model_dump(mode="json")])

    # Nothing in S-6.1 or S-6.3 has anything to say about this.
    assert len(state.experiments) == 1
    assert check_update({"experiments": [measured().model_dump(mode="json")]})


def test_the_composed_check_refuses_a_log_holding_a_measurement() -> None:
    state = CheckpointedState(experiments=[measured().model_dump(mode="json")])

    with pytest.raises(UnreferencedResultError, match="is not a reference"):
        check_state(state)


def test_going_through_record_experiment_cannot_produce_that_state(tmp_path: Path) -> None:
    """The one way in, so the guarantee is enforced rather than remembered."""
    cache = ReplayCache(tmp_path / "recordings")
    update = record_experiment(
        CheckpointedState(),
        cache=cache,
        key=key_for(1),
        result_type=Measurement,
        compute=measured,
        outcome="quadratic in queries",
    )

    state = CheckpointedState(experiments=list(update["experiments"]))  # type: ignore[arg-type]

    check_state(state)
    assert experiments_of(state)[0].outcome == "quadratic in queries"


def test_the_index_comes_from_the_log_rather_than_the_caller(tmp_path: Path) -> None:
    """`read_experiment(7)` has to mean the seventh, and a caller-chosen index
    can collide, skip or restart."""
    cache = ReplayCache(tmp_path / "recordings")
    state = CheckpointedState()

    for index in (1, 2, 3):
        update = record_experiment(
            state,
            cache=cache,
            key=key_for(index),
            result_type=Measurement,
            compute=measured,
            outcome=f"run {index}",
        )
        state = CheckpointedState(experiments=[*state.experiments, *update["experiments"]])  # type: ignore[misc]

    assert [ref.index for ref in experiments_of(state)] == [1, 2, 3]


def ref_dump(index: int) -> JsonValue:
    """A valid reference, so a size test is about size and nothing else."""
    stored = ExperimentRef(
        index=index,
        key=key_for(index),
        outcome="quadratic in queries; 87% of cost localized",
        recorded_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        determinism=Determinism.DETERMINISTIC,
        hit=False,
    )
    return cast("JsonValue", stored.model_dump(mode="json"))


def test_a_state_over_the_stated_limit_is_refused() -> None:
    """S-6.3 proved the bound for 40 references and S-5.4 caps investigation at
    40, but the two live in different modules and nothing joined them — so an
    investigation that never consulted the budget runs past both.

    Built from **valid references**, so the refusal is about size. An earlier
    version used raw measurements and passed on the reference check instead,
    which meant removing the size check broke nothing. Found by sabotage.
    """
    state = CheckpointedState(experiments=[ref_dump(index) for index in range(1, 201)])

    experiments_of(state)  # every entry is a reference; only the size is wrong

    with pytest.raises(CheckpointTooLargeError, match="supposed to stay under"):
        check_state(state)


def test_a_log_within_the_cap_is_not_refused() -> None:
    """The control: the guard above must not be refusing every log."""
    check_state(CheckpointedState(experiments=[ref_dump(index) for index in range(1, 41)]))


# ================================== the join that had no correct form: the ship


def test_the_staleness_policy_can_now_be_applied_to_the_state() -> None:
    """The headline defect.

    S-6.1 held `screening` as a flat sequence of opaque entries and S-6.4
    invalidates *per workload*. Nothing said which workload an entry belonged to,
    so a correct answer had nowhere to go — the caller had to rebuild the channel
    from a shape that did not carry the identity the rebuild needs.
    """
    state = CheckpointedState(
        screening={
            "api.books.list": {"growth": "quadratic"},
            "api.authors.list": {"growth": "linear"},
        }
    )

    update, report, _ = apply_ship(
        state,
        patch=Patch.of("F1", ["src/api/serializers.py"]),
        workloads=[
            Coverage.of("api.books.list", ["src/api/serializers.py"]),
            Coverage.of("api.authors.list", ["src/api/authors.py"]),
        ],
    )

    assert update == {"screening": {"api.authors.list": {"growth": "linear"}}}
    assert report.invalidated == ("api.books.list",)
    assert report.retained == ("api.authors.list",)


def test_a_pending_finding_in_the_patched_file_is_re_investigated() -> None:
    _, _, findings = apply_ship(
        CheckpointedState(),
        patch=Patch.of("F1", ["src/api/serializers.py"]),
        workloads=[],
        pending=[
            Coverage.of("F2", ["src/api/serializers.py"]),
            Coverage.of("F3", ["src/api/tags.py"]),
        ],
    )

    assert findings == {"F2": FindingAction.REINVESTIGATE, "F3": FindingAction.REPAIR}


def test_the_shipped_finding_is_not_triaged_with_the_pending_ones() -> None:
    """It shipped; what the policy decides is what happens to the ones waiting.

    The patched finding is deliberately *in* the list here — an earlier version
    left it out, so removing the filter changed nothing and the sabotage passed.
    """
    _, _, findings = apply_ship(
        CheckpointedState(),
        patch=Patch.of("F1", ["src/api/serializers.py"]),
        workloads=[],
        pending=[
            Coverage.of("F1", ["src/api/serializers.py"]),
            Coverage.of("F2", ["src/api/tags.py"]),
        ],
    )

    assert findings == {"F2": FindingAction.REPAIR}


def test_read_experiment_refuses_an_index_that_does_not_exist(tmp_path: Path) -> None:
    """The caller is a model, and a model that guesses an index must be told
    rather than handed the nearest record."""
    cache = ReplayCache(tmp_path / "recordings")
    state = CheckpointedState(experiments=[ref_dump(1)])

    with pytest.raises(InvestigationError, match="there is no experiment 7"):
        read_experiment(state, 7, cache=cache, result_type=Measurement)


def test_the_investigation_can_say_which_workloads_it_exercised(tmp_path: Path) -> None:
    """S-6.4 leaves everything invalidated for want of a record; the log already
    holds part of one, because a reference carries the key and the key names the
    workload."""
    cache = ReplayCache(tmp_path / "recordings")
    state = CheckpointedState()
    for index, workload in ((1, "api.books.list"), (2, "api.tags.list")):
        update = record_experiment(
            state,
            cache=cache,
            key=key_for(index, workload),
            result_type=Measurement,
            compute=measured,
            outcome="linear",
        )
        state = CheckpointedState(experiments=[*state.experiments, *update["experiments"]])  # type: ignore[misc]

    assert [c.subject for c in coverage_from_state(state)] == [
        "api.books.list",
        "api.tags.list",
    ]


# ========================================================= a real Postgres server

IMAGE = "postgres:16-alpine"
USER = PASSWORD = "coldfix_test"
PORT = 55442


@pytest.fixture(scope="module")
def _server() -> Iterator[str]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")
    container = f"coldfix-epic6-{uuid.uuid4().hex[:8]}"
    execute(
        [
            "docker", "run", "--detach", "--name", container,
            "--publish", f"{PORT}:5432",
            "--env", f"POSTGRES_USER={USER}", "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", "POSTGRES_DB=postgres", "--", IMAGE,
        ],
        timeout=180.0,
    )  # fmt: skip
    try:
        yield container
    finally:
        execute(["docker", "rm", "--force", "--volumes", container], timeout=180.0)


@pytest.fixture
def store(_server: str, tmp_path: Path) -> PersistentStore:
    url = f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/"
    wait_until_ready(VerifiedDatabase(url + "coldfix_bootstrap"), "postgres")
    name = f"coldfix_state_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url + "postgres", autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    built = PersistentStore(
        database=VerifiedDatabase(url + name), replay_root=tmp_path / "recordings"
    )
    built.initialize()
    return built


# ============================== the epic's sentence, performed once


@pytest.mark.postgres
@pytest.mark.slow
def test_an_investigation_survives_a_crash_and_a_rewind(
    store: PersistentStore, tmp_path: Path
) -> None:
    """Both halves of the epic at once, through a real graph and a real store.

    Four experiments recorded by reference, a failure learned into the persistent
    store, a crash mid-run resumed from its checkpoint, and a rewind that takes
    the state back while leaving the learning in place.
    """
    cache = ReplayCache(tmp_path / "recordings")
    saver = InMemorySaver()
    graph: StateGraph[CheckpointedState, Any, Any, Any] = StateGraph(CheckpointedState)

    @node
    def investigate(state: CheckpointedState) -> Mapping[str, JsonValue]:
        index = len(state.experiments) + 1
        return record_experiment(
            state,
            cache=cache,
            key=key_for(index),
            result_type=Measurement,
            compute=measured,
            outcome=f"experiment {index}: quadratic in queries",
            determinism=Determinism.DETERMINISTIC,
        )

    @node
    def fail(state: CheckpointedState) -> Mapping[str, JsonValue]:
        learn(store, finding_id="F1", entry={"tried": "select_related", "outcome": "rejected"})
        return {"flags": ["the adversary rejected the patch"]}

    graph.add_node("investigate", investigate, input_schema=CheckpointedState)
    graph.add_node("fail", fail, input_schema=CheckpointedState)
    graph.add_edge(START, "investigate")
    graph.add_edge("investigate", "fail")
    graph.add_edge("fail", END)
    app = graph.compile(checkpointer=saver)

    config: RunnableConfig = {"configurable": {"thread_id": "run-1"}}
    finished = app.invoke(CheckpointedState(), config)

    # The state holds a reference, not a measurement, and stays small.
    state = CheckpointedState.model_validate(finished)
    check_state(state)
    assert checkpoint_is_small(state)
    assert experiments_of(state)[0].index == 1

    # The full result is still fetchable.
    assert read_experiment(state, 1, cache=cache, result_type=Measurement).value.stdout.startswith(
        "x"
    )

    # Crash and resume: the run continues from its checkpoint with full state.
    resumed = app.invoke(None, config)
    assert CheckpointedState.model_validate(resumed).experiments

    # Rewind: the state goes back, the learning does not.
    history = list(app.get_state_history(config))
    before = next(s for s in history if not s.values.get("flags"))
    assert app.get_state(before.config).values["flags"] == []
    assert len(store.read(Collection.FAILURE_MEMORY, "F1")) == 1


def checkpoint_is_small(state: CheckpointedState) -> bool:
    return checkpoint_size_bytes(state) < CHECKPOINT_SIZE_LIMIT_BYTES


@pytest.mark.postgres
@pytest.mark.slow
def test_the_knowledge_a_rewind_keeps_is_readable_by_the_resumed_run(
    store: PersistentStore,
) -> None:
    """F5's actual purpose: the resumed run must be able to *use* what was
    learned after the checkpoint it resumed from, or it repeats the attempt."""
    learn(store, finding_id="F1", entry={"tried": "select_related"})

    remembered = store.read(Collection.FAILURE_MEMORY, "F1")

    assert [entry.entry["tried"] for entry in remembered] == ["select_related"]

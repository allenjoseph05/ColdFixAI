"""S-6.2 — the store a rewind must not touch.

The parts that need no server come first. Everything after `_server` runs
against a real Postgres in a container, for the reason Epic 2's tests give: the
acceptance criteria here are claims about what a database actually does, and a
fake would assert only what this file already believes. That matters more than
usual for the append-only guarantee, which is a trigger — a Python double would
be testing the method list, which is exactly the thing the trigger exists to
stop mattering.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import JsonValue

from coldfix.bench.execute import execute
from coldfix.sandbox import docker_available
from coldfix.sandbox.production import ProductionDatabaseError, VerifiedDatabase
from coldfix.sandbox.reset import wait_until_ready
from coldfix.state.checkpoint import CheckpointedState, node
from coldfix.state.persistent import (
    JOURNAL,
    MEMBERS,
    Collection,
    PersistentStore,
    PersistentStoreError,
    SharedStoreError,
    refuse_shared_store,
)

# =========================================================== no server needed


def test_the_store_holds_the_four_things_f5_names() -> None:
    """AC 1's list, checkable rather than partly implicit."""
    assert {member.name for member in MEMBERS} == {
        "failure_memory",
        "playbooks",
        "trust_ledger",
        "replay_cache",
    }


def test_the_replay_cache_is_recorded_as_living_outside_this_database() -> None:
    """It is a member of the store and not a table, and the list says so.

    ADR 054 put it on a filesystem, partitioned by machine, and two properties
    depend on that: a recording can be opened by hand when it produced a
    surprising answer (S-5.2's debugging method), and a foreign machine's
    recording misses rather than matching. What F5 requires is that a checkpoint
    restore cannot reach it, which a directory outside the checkpoint database
    satisfies at least as completely as a row would.
    """
    replay = next(member for member in MEMBERS if member.name == "replay_cache")

    assert "filesystem" in replay.stored_in
    assert replay.story == "S-5.1"
    assert "replay_cache" not in {collection.value for collection in Collection}


def test_the_checkpoint_database_cannot_also_be_the_persistent_store() -> None:
    """ADR 003's decision, enforced rather than described.

    Sharing one database makes *dropping checkpoints* — routine, and done with a
    `DROP` that knows nothing about this module — able to destroy the playbook.
    """
    persistent = VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state")

    with pytest.raises(SharedStoreError, match="ADR 003"):
        refuse_shared_store(persistent, "postgresql://u:p@localhost:5432/coldfix_state")


def test_a_different_database_on_the_same_server_is_allowed() -> None:
    """The control. Without it the guard would pass for one that refused every
    Postgres checkpointer, which ADR 003 explicitly permits for campaigns."""
    persistent = VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state")

    refuse_shared_store(persistent, "postgresql://u:p@localhost:5432/coldfix_checkpoints")


def test_a_sqlite_checkpoint_file_is_never_the_same_store() -> None:
    """ADR 003's development checkpointer is a file, so it cannot collide."""
    persistent = VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state")

    refuse_shared_store(persistent, Path("checkpoints.sqlite"))


def test_a_production_database_cannot_be_named_let_alone_stored_in() -> None:
    """The store takes a `VerifiedDatabase`, so the refusal happens upstream of it.

    Worth being precise about what this proves: the exception is raised while
    *naming* the database, before `PersistentStore` is reached at all. That is
    S-2.5's construction working as intended — there is no unverified handle to
    hand over — and the reason this store's field is typed rather than a string.
    Our own store is not the subject's database, but it is still a database this
    system writes to, and the guard's default patterns already include
    `coldfix_*`, so it was built expecting to cover this one.
    """
    with pytest.raises(ProductionDatabaseError):
        VerifiedDatabase("postgresql://u:p@prod.internal:5432/orders")


def test_a_store_database_is_named_the_way_the_guard_expects() -> None:
    """The control: `coldfix_*` passes, so the test above is about the URL and
    not about the guard refusing everything."""
    store = PersistentStore(
        database=VerifiedDatabase("postgresql://u:p@localhost:5432/coldfix_state"),
        replay_root=Path("recordings"),
    )

    assert store.database.name == "coldfix_state"
    assert "coldfix_state" in store.describe()


# ====================================================== a real Postgres server

IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"

# Not 5432, and not the port S-2.6's reset tests pinned. A store pointed at the
# wrong database would still appear to work, which is the worst way to fail.
PORT = 55441


@pytest.fixture(scope="module")
def _server() -> Iterator[str]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    container = f"coldfix-persistent-test-{uuid.uuid4().hex[:8]}"
    execute(
        [
            "docker", "run", "--detach", "--name", container,
            "--publish", f"{PORT}:5432",
            "--env", f"POSTGRES_USER={USER}",
            "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", "POSTGRES_DB=postgres",
            "--", IMAGE,
        ],
        timeout=180.0,
    )  # fmt: skip
    try:
        yield container
    finally:
        execute(["docker", "rm", "--force", "--volumes", container], timeout=180.0)


def url_for(name: str) -> str:
    return f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/{name}"


@pytest.fixture
def store(_server: str, tmp_path: Path) -> PersistentStore:
    """A fresh, initialized store per test, named so the guard permits it."""
    wait_until_ready(VerifiedDatabase(url_for("coldfix_bootstrap")), "postgres")

    name = f"coldfix_state_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url_for("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')

    built = PersistentStore(
        database=VerifiedDatabase(url_for(name)),
        replay_root=tmp_path / "recordings",
    )
    built.initialize()
    return built


pytestmark_postgres = [pytest.mark.postgres, pytest.mark.slow]


# ------------------------------------------------------------------ the journal


@pytest.mark.postgres
@pytest.mark.slow
def test_an_entry_round_trips(store: PersistentStore) -> None:
    written = store.append(
        Collection.FAILURE_MEMORY,
        key="F1",
        entry={"tried": "select_related", "outcome": "rejected"},
    )

    read = store.read(Collection.FAILURE_MEMORY)

    assert len(read) == 1
    assert read[0].id == written.id
    assert read[0].entry == {"tried": "select_related", "outcome": "rejected"}
    assert read[0].key == "F1"


@pytest.mark.postgres
@pytest.mark.slow
def test_entries_come_back_oldest_first(store: PersistentStore) -> None:
    """A journal: what was learned and in what order is the thing preserved."""
    for index in range(3):
        store.append(Collection.PLAYBOOKS, key="django", entry={"step": index})

    assert [entry.entry["step"] for entry in store.read(Collection.PLAYBOOKS)] == [0, 1, 2]


@pytest.mark.postgres
@pytest.mark.slow
def test_a_collection_is_read_per_key(store: PersistentStore) -> None:
    store.append(Collection.TRUST_LEDGER, key="wide-parent", entry={"level": 0})
    store.append(Collection.TRUST_LEDGER, key="narrow-parent", entry={"level": 3})

    assert [e.entry["level"] for e in store.read(Collection.TRUST_LEDGER, "wide-parent")] == [0]


@pytest.mark.postgres
@pytest.mark.slow
def test_collections_do_not_leak_into_one_another(store: PersistentStore) -> None:
    store.append(Collection.FAILURE_MEMORY, key="F1", entry={"a": 1})
    store.append(Collection.PLAYBOOKS, key="F1", entry={"b": 2})

    assert len(store.read(Collection.FAILURE_MEMORY)) == 1
    assert len(store.read(Collection.PLAYBOOKS)) == 1


@pytest.mark.postgres
@pytest.mark.slow
def test_an_unkeyed_entry_is_refused(store: PersistentStore) -> None:
    """Written and never read again, since every reader looks up by key."""
    with pytest.raises(PersistentStoreError, match="needs a key"):
        store.append(Collection.PLAYBOOKS, key="   ", entry={"a": 1})


@pytest.mark.postgres
@pytest.mark.slow
def test_initialize_runs_twice_without_complaint(store: PersistentStore) -> None:
    """A run against an existing store is the normal case, not a special one."""
    store.append(Collection.PLAYBOOKS, key="django", entry={"a": 1})

    store.initialize()

    assert len(store.read(Collection.PLAYBOOKS)) == 1


# ------------------------------------- AC 2: append-only, enforced by the database


@pytest.mark.postgres
@pytest.mark.slow
def test_an_entry_cannot_be_updated_even_from_raw_sql(store: PersistentStore) -> None:
    """The adversarial form: attempt the violation, assert it fails.

    Through `psycopg` rather than through this module, because a class with no
    `update` method is append-only only for callers who go through the class.
    """
    store.append(Collection.PLAYBOOKS, key="django", entry={"a": 1})

    with (
        psycopg.connect(store.database.dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        connection.execute(f"UPDATE {JOURNAL} SET key = 'rewritten'")


@pytest.mark.postgres
@pytest.mark.slow
def test_an_entry_cannot_be_deleted_even_from_raw_sql(store: PersistentStore) -> None:
    store.append(Collection.PLAYBOOKS, key="django", entry={"a": 1})

    with (
        psycopg.connect(store.database.dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        connection.execute(f"DELETE FROM {JOURNAL}")


@pytest.mark.postgres
@pytest.mark.slow
def test_the_journal_cannot_be_truncated(store: PersistentStore) -> None:
    """`TRUNCATE` is the verb ADR 003 names when it says dropping checkpoints
    must not reach the playbook, and it is not a row-level operation — a trigger
    written `FOR EACH ROW` would let it through while looking correct."""
    store.append(Collection.PLAYBOOKS, key="django", entry={"a": 1})

    with (
        psycopg.connect(store.database.dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        connection.execute(f"TRUNCATE {JOURNAL}")


@pytest.mark.postgres
@pytest.mark.slow
def test_the_refusal_survives_the_transaction(store: PersistentStore) -> None:
    """The control on the three above: the entry is still there afterwards, so
    the refusal is a refusal rather than a rollback that lost the write too."""
    store.append(Collection.PLAYBOOKS, key="django", entry={"a": 1})

    with (
        psycopg.connect(store.database.dsn, autocommit=True) as connection,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        connection.execute(f"DELETE FROM {JOURNAL}")

    assert len(store.read(Collection.PLAYBOOKS)) == 1


# ------------------------------- AC 3: a rewind keeps the knowledge that caused it


@pytest.mark.postgres
@pytest.mark.slow
def test_a_rewind_discards_the_state_and_keeps_the_failure_memory(
    store: PersistentStore,
) -> None:
    """AC 3, and the whole point of F5.

    The reason to rewind is a failure discovered *after* the checkpoint being
    rewound to. If that failure record lived in checkpointed state, the rewind
    would discard it and the agent would repeat the attempt it rewound to avoid.
    So: run a graph that learns something late, rewind to before it learned, and
    assert the state went back while the learning stayed.
    """
    saver = InMemorySaver()
    graph: StateGraph[CheckpointedState, Any, Any, Any] = StateGraph(CheckpointedState)

    @node
    def investigate(state: CheckpointedState) -> Mapping[str, JsonValue]:
        return {"experiments": [{"index": 1, "primitive": "ablation"}]}

    @node
    def learn(state: CheckpointedState) -> Mapping[str, JsonValue]:
        store.append(
            Collection.FAILURE_MEMORY,
            key="F1",
            entry={"tried": "select_related", "outcome": "the adversary rejected it"},
        )
        return {"flags": ["adversary rejected the patch"]}

    graph.add_node("investigate", investigate, input_schema=CheckpointedState)
    graph.add_node("learn", learn, input_schema=CheckpointedState)
    graph.add_edge(START, "investigate")
    graph.add_edge("investigate", "learn")
    graph.add_edge("learn", END)
    app = graph.compile(checkpointer=saver)

    config: RunnableConfig = {"configurable": {"thread_id": "run-1"}}
    finished = app.invoke(CheckpointedState(), config)

    assert finished["flags"] == ["adversary rejected the patch"]
    assert len(store.read(Collection.FAILURE_MEMORY)) == 1

    # Rewind to a checkpoint from before `learn` ran.
    history = list(app.get_state_history(config))
    before_learning = next(snapshot for snapshot in history if not snapshot.values.get("flags"))
    rewound = app.get_state(before_learning.config)

    # The state went back...
    assert rewound.values["flags"] == []
    # ...and the knowledge that motivated the rewind did not go with it.
    remembered = store.read(Collection.FAILURE_MEMORY)
    assert len(remembered) == 1
    assert remembered[0].entry["outcome"] == "the adversary rejected it"


@pytest.mark.postgres
@pytest.mark.slow
def test_resuming_from_the_rewound_checkpoint_still_sees_the_memory(
    store: PersistentStore,
) -> None:
    """The stronger form: not just readable after a rewind, but readable *by the
    resumed run* — which is what stops it repeating the rejected attempt."""
    saver = InMemorySaver()
    graph: StateGraph[CheckpointedState, Any, Any, Any] = StateGraph(CheckpointedState)
    seen: list[int] = []

    @node
    def attempt(state: CheckpointedState) -> Mapping[str, JsonValue]:
        seen.append(len(store.read(Collection.FAILURE_MEMORY, "F1")))
        return {"attempts": [{"n": len(state.attempts) + 1}]}

    graph.add_node("attempt", attempt, input_schema=CheckpointedState)
    graph.add_edge(START, "attempt")
    graph.add_edge("attempt", END)
    app = graph.compile(checkpointer=saver)

    config: RunnableConfig = {"configurable": {"thread_id": "run-2"}}
    app.invoke(CheckpointedState(), config)
    assert seen == [0]

    store.append(Collection.FAILURE_MEMORY, key="F1", entry={"tried": "prefetch_related"})

    history = list(app.get_state_history(config))
    app.invoke(None, history[-1].config)

    # The rewound run reads what was learned after the checkpoint it resumed from.
    assert seen[-1] == 1

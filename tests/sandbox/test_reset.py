"""Reset returns the database to its baseline, and row counts cannot prove it.

S-0.5 is the reason this file is shaped the way it is. Plain transaction
rollback failed all ten cycles of that spike **while passing the check the story
specified** — every row count, every content hash and every `max(id)` identical,
and the sequences 250 higher than they started. So the central test here is not
"does reset work" but `test_rollback_alone_leaves_sequences_advanced`, which
reproduces the defect on a live server, and its partner asserting that the
strategy this module actually ships does not have it.

Most of these need a real Postgres. There is no mock: sequence
non-transactionality is a property of the database, and a fake would only assert
what this file already believes. They are marked `postgres` and skipped when no
daemon is listening, for the same reason the docker tests are — a machine that
cannot run them must not read as a passing suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from coldfix.bench.execute import execute
from coldfix.sandbox import docker_available
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import (
    ContainerRestartReset,
    ResetMechanism,
    ResetNotPreparedError,
    ResetStrategy,
    RollbackReset,
    SnapshotRestoreReset,
    capture_sequences,
    wait_until_ready,
)

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

IMAGE = "postgres:16-alpine"
PASSWORD = "coldfix_test"
USER = "coldfix_test"

# Not 5432, and not the three ports the E0 spikes pinned. A reset run pointed at
# the wrong database would still appear to work, which is the worst way for this
# to fail.
PORT = 55440

SEED_SQL = """
CREATE TABLE ticket (id serial PRIMARY KEY, title text NOT NULL);
INSERT INTO ticket (title) VALUES ('first'), ('second'), ('third');
"""


def _run(*args: str) -> None:
    execute(["docker", *args], timeout=180.0)


@pytest.fixture(scope="module")
def _server() -> Iterator[str]:
    """One Postgres container for the module, on a port nothing else uses."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    container = f"coldfix-reset-test-{uuid.uuid4().hex[:8]}"
    _run(
        "run",
        "--detach",
        "--name",
        container,
        "--publish",
        f"{PORT}:5432",
        "--env",
        f"POSTGRES_USER={USER}",
        "--env",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "--env",
        "POSTGRES_DB=postgres",
        "--",
        IMAGE,
    )
    try:
        yield container
    finally:
        _run("rm", "--force", "--volumes", container)


def url_for(name: str) -> str:
    return f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/{name}"


@pytest.fixture
def database(_server: str) -> Iterator[VerifiedDatabase]:
    """A freshly seeded subject database, named so the production guard permits it.

    A new database per test rather than a new container: creating one is
    milliseconds and starting Postgres is seconds, and the isolation is the same
    because nothing here crosses a database boundary except the maintenance
    connection, which owns no state.
    """
    maintenance = url_for("postgres")
    wait_until_ready(VerifiedDatabase(url_for("coldfix_bootstrap")), "postgres")

    name = f"coldfix_subject_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(maintenance, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')

    subject = VerifiedDatabase(url_for(name))
    with psycopg.connect(subject.dsn, autocommit=True) as conn:
        conn.execute(SEED_SQL)

    try:
        yield subject
    finally:
        with psycopg.connect(maintenance, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def sequence_value(database: VerifiedDatabase) -> int:
    with psycopg.connect(database.dsn) as conn:
        row = conn.execute("SELECT last_value FROM ticket_id_seq").fetchone()
    assert row is not None
    return int(str(row[0]))


def row_count(database: VerifiedDatabase) -> int:
    with psycopg.connect(database.dsn) as conn:
        row = conn.execute("SELECT count(*) FROM ticket").fetchone()
    assert row is not None
    return int(str(row[0]))


# ------------------------------------------------- the defect S-0.5 found


def test_rollback_alone_leaves_sequences_advanced(database: VerifiedDatabase) -> None:
    """The failure the acceptance criterion's own check would have passed.

    Ten cycles of insert-and-rollback. Row counts come back every time — this is
    what the story asked to be asserted, and it is satisfied perfectly. The
    sequence does not come back, because `nextval` is non-transactional by
    design: two concurrent transactions must never receive the same id, so
    rolling numbers back into the pool is not an option Postgres has.

    This test asserts the *broken* behaviour on purpose. It is the control. If
    it ever stops failing to reset, the sequel below proves nothing.
    """
    before_rows = row_count(database)
    before_sequence = sequence_value(database)

    with psycopg.connect(database.dsn) as conn:
        for _ in range(10):
            conn.execute("INSERT INTO ticket (title) VALUES ('workload')")
            conn.rollback()

    assert row_count(database) == before_rows
    assert sequence_value(database) > before_sequence


def test_rollback_with_sequence_restore_returns_everything(
    database: VerifiedDatabase,
) -> None:
    """The strategy this module ships, against the same ten cycles.

    Row counts *and* sequences identical. The second assertion is the whole
    story: it is the one the original acceptance criterion did not make, and the
    one that separates a reset from something that merely looks like one.
    """
    mechanism = RollbackReset(database=database)
    mechanism.prepare()
    before_rows = row_count(database)
    before_sequence = sequence_value(database)

    for _ in range(10):
        with mechanism.cycle():
            mechanism.connection.execute("INSERT INTO ticket (title) VALUES ('workload')")

    mechanism.close()

    assert row_count(database) == before_rows
    assert sequence_value(database) == before_sequence


def test_a_never_used_sequence_is_restored_without_skipping_a_number(
    database: VerifiedDatabase,
) -> None:
    """`pg_sequences.last_value` is NULL until a sequence issues a value.

    NULL cannot be given to `setval`, so an implementation that passed it
    through would crash, and one that defaulted to 1 with `is_called` true would
    make the first real `nextval` return 2. Both are the same defect this
    strategy exists to fix, one row smaller.
    """
    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("CREATE SEQUENCE untouched_seq START 42")

    mechanism = RollbackReset(database=database)
    mechanism.prepare()

    with mechanism.cycle():
        mechanism.connection.execute("SELECT nextval('untouched_seq')")

    with psycopg.connect(database.dsn) as conn:
        first = conn.execute("SELECT nextval('untouched_seq')").fetchone()

    mechanism.close()
    assert first is not None
    assert first[0] == 42


# --------------------------------------------------- the other two strategies


def test_snapshot_restore_undoes_a_committed_change(database: VerifiedDatabase) -> None:
    """What rollback cannot do: undo work another connection committed.

    This is the case a containerised workload actually produces, and the reason
    the heavier strategy exists rather than being a historical curiosity.
    """
    mechanism = SnapshotRestoreReset(database=database)
    mechanism.prepare()
    before = row_count(database)

    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO ticket (title) VALUES ('committed elsewhere')")
    assert row_count(database) == before + 1

    mechanism.reset()

    assert row_count(database) == before
    mechanism.discard_snapshot()


def test_snapshot_restore_undoes_a_schema_change(database: VerifiedDatabase) -> None:
    """Neither rollback nor sequence restore reaches a dropped table."""
    mechanism = SnapshotRestoreReset(database=database)
    mechanism.prepare()

    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE ticket")

    mechanism.reset()

    assert row_count(database) == 3
    mechanism.discard_snapshot()


def test_snapshot_restore_also_returns_the_sequence(database: VerifiedDatabase) -> None:
    mechanism = SnapshotRestoreReset(database=database)
    mechanism.prepare()
    before = sequence_value(database)

    with psycopg.connect(database.dsn, autocommit=True) as conn:
        for _ in range(5):
            conn.execute("INSERT INTO ticket (title) VALUES ('committed')")

    mechanism.reset()

    assert sequence_value(database) == before
    mechanism.discard_snapshot()


def test_container_restart_rebuilds_the_server_and_reseeds(_server: str) -> None:
    """The heaviest strategy, and the only one that survives a corrupted server.

    Runs on its own container rather than the module's, because it destroys what
    it resets and every other test in this file would lose its database.
    """
    container = f"coldfix-restart-test-{uuid.uuid4().hex[:8]}"
    port = PORT + 1
    url = f"postgresql://{USER}:{PASSWORD}@localhost:{port}/coldfix_restart"
    environment = {
        "POSTGRES_USER": USER,
        "POSTGRES_PASSWORD": PASSWORD,
        "POSTGRES_DB": "coldfix_restart",
    }

    database = VerifiedDatabase(url)
    mechanism = ContainerRestartReset(
        database=database,
        container=container,
        image=IMAGE,
        seed_sql=SEED_SQL,
        environment=environment,
    )
    try:
        mechanism.prepare()
        # There is no container yet; the first reset is what builds one.
        mechanism.reset()
        assert row_count(database) == 3
        with psycopg.connect(database.dsn, autocommit=True) as conn:
            conn.execute("INSERT INTO ticket (title) VALUES ('survives?')")
            conn.execute("DROP TABLE IF EXISTS ticket CASCADE")

        mechanism.reset()

        assert row_count(database) == 3
        assert sequence_value(database) == 3
    finally:
        _run("rm", "--force", "--volumes", container)


def test_a_container_restart_does_not_strand_its_storage(_server: str) -> None:
    """`--volumes` is about disk, not about correctness, and only a test says which.

    The Postgres image declares its data directory as a volume, so every
    container gets an anonymous one and a rebuilt container gets a fresh one
    whether or not the old was removed. The reset is correct either way — which
    is exactly why dropping `--volumes` broke no other test in this file.

    What it does break is storage. Each cycle would strand a full Postgres data
    directory that nothing reclaims, and an investigation resetting a few
    hundred times fills the disk. That is a slower failure than a bad reset and
    a much harder one to attribute, so it gets its own assertion.
    """
    container = f"coldfix-volume-test-{uuid.uuid4().hex[:8]}"
    port = PORT + 2
    url = f"postgresql://{USER}:{PASSWORD}@localhost:{port}/coldfix_volumes"
    mechanism = ContainerRestartReset(
        database=VerifiedDatabase(url),
        container=container,
        image=IMAGE,
        seed_sql=SEED_SQL,
        environment={
            "POSTGRES_USER": USER,
            "POSTGRES_PASSWORD": PASSWORD,
            "POSTGRES_DB": "coldfix_volumes",
        },
    )
    try:
        mechanism.prepare()
        mechanism.reset()
        after_first = dangling_volumes()

        mechanism.reset()

        assert dangling_volumes() == after_first
    finally:
        _run("rm", "--force", "--volumes", container)


def dangling_volumes() -> int:
    result = execute(
        ["docker", "volume", "ls", "--quiet", "--filter", "dangling=true"], timeout=120.0
    )
    return len(result.stdout.split())


# ------------------------------------------------------------- the contract


@pytest.mark.parametrize(
    "strategy",
    [
        ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        ResetStrategy.SNAPSHOT_RESTORE,
        ResetStrategy.CONTAINER_RESTART,
    ],
)
def test_all_three_strategies_exist_and_are_recorded(strategy: ResetStrategy) -> None:
    """AC 1 and AC 2. A result that does not say how it was reset cannot be compared."""
    implementations = {
        m.strategy: m for m in (RollbackReset, SnapshotRestoreReset, ContainerRestartReset)
    }

    assert strategy in implementations
    assert issubclass(implementations[strategy], ResetMechanism)
    assert strategy.value == str(strategy)


def test_the_rollback_strategy_is_not_named_for_rollback_alone() -> None:
    """The backlog calls it "transaction rollback"; shipping that would ship the defect.

    S-0.5 recorded the rename as a required change to this epic. The name is
    asserted so that a future simplification back to "rollback" has to argue
    with a failing test.
    """
    assert RollbackReset.strategy.value == "rollback_and_restore_sequences"


@pytest.mark.parametrize(
    "mechanism_factory",
    [
        lambda db: RollbackReset(database=db),
        lambda db: SnapshotRestoreReset(database=db),
        lambda db: ContainerRestartReset(database=db, container="unused", image=IMAGE, seed_sql=""),
    ],
)
def test_resetting_before_a_baseline_is_captured_is_refused(
    database: VerifiedDatabase, mechanism_factory: object
) -> None:
    """A baseline captured after the workload is a baseline of the wrong state.

    Every cycle after that would faithfully restore what the first workload
    left, and every measurement would be taken from it.
    """
    mechanism = mechanism_factory(database)  # type: ignore[operator]

    with pytest.raises(ResetNotPreparedError):
        mechanism.reset()


def test_the_cycle_resets_even_when_the_workload_raises(database: VerifiedDatabase) -> None:
    """The case where the database is most likely left somewhere nobody predicted."""
    mechanism = RollbackReset(database=database)
    mechanism.prepare()
    before = sequence_value(database)

    with pytest.raises(RuntimeError):  # noqa: SIM117
        with mechanism.cycle():
            mechanism.connection.execute("INSERT INTO ticket (title) VALUES ('doomed')")
            message = "the workload failed"
            raise RuntimeError(message)

    mechanism.close()
    assert sequence_value(database) == before
    assert row_count(database) == 3


def test_capture_reports_every_sequence_in_the_database(database: VerifiedDatabase) -> None:
    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("CREATE SEQUENCE extra_seq")

    with psycopg.connect(database.dsn) as conn:
        captured = capture_sequences(conn)

    names = {s.name for s in captured}
    assert {"ticket_id_seq", "extra_seq"} <= names

"""Return the database to a known state, three ways, none of them assumed to work.

Epic 2, S-2.6. Every experiment this system runs assumes it starts from the same
place as the last one. S-0.5 measured what that costs and, more usefully, found
that the obvious implementation is silently wrong.

**Plain transaction rollback failed all ten cycles of the spike, and the
acceptance criterion's own check passed it.** Row counts were identical every
cycle. So were content hashes and every `max(id)`. The sequences were not:
`helpdesk_ticket_id_seq` went 509 → 759 over ten cycles, exactly the workload's
insert count accumulated and never given back. Postgres sequences are
non-transactional by design — `nextval()` must not be rolled back, or two
concurrent transactions could receive the same id — so this is correct database
behaviour and precisely why it defeats a naive reset.

The consequence for measurement is not cosmetic. The next experiment inserts
rows with different primary keys than the last one, and anything ordered by id,
keyed on id, or paginated by id behaves differently. For a system whose entire
method is *measure, change one thing, measure again*, a starting state that
silently differs between measurements is the failure most likely to produce a
confident wrong answer.

So the strategy named "transaction rollback" in the backlog is implemented here
as **rollback followed by an explicit sequence restore**. The spike's
recommendation was explicit about this and recorded it as a required change to
E2. Shipping the strategy under its original name would ship the defect.

Three strategies, from the spike's measurements on a 37-table subject:

| Strategy | Correct | Median | Scope | Exclusive access |
|---|---|---|---|---|
| rollback alone | **no** | 0.4 ms | current transaction | no |
| `ROLLBACK_AND_RESTORE_SEQUENCES` | yes | 19.2 ms | transaction + sequences | no |
| `SNAPSHOT_RESTORE` | yes | 163.3 ms | whole database | **yes** |
| `CONTAINER_RESTART` | yes | seconds | whole server | **yes** |

**No strategy here resets process state, and one of them does not need to.**
S-0.5 found a Django `QuerySet` still reporting a row that had been rolled back,
because the rows are cached in a Python object no database-side reset can reach.
That is unfixable from the database — but in this architecture it is already
fixed elsewhere: S-2.1 destroys the container after every run, so the process
holding the cache does not survive to the next experiment. The reset contract is
the database half of a guarantee whose other half is the container lifecycle.

**Rollback has a precondition this module cannot check.** It undoes work done on
*its own connection*. A workload driven inside a container connects separately,
commits separately, and is not undone by a rollback issued here. That makes
`ROLLBACK_AND_RESTORE_SEQUENCES` correct only for in-process workloads, and the
way it is caught when it is wrong is S-2.7 running ten cycles and finding the
state did not come back. This is exactly why the story says each strategy is
verified before use rather than trusted.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

import psycopg
from psycopg import sql

from coldfix.bench.execute import ExecutionStartError, execute
from coldfix.sandbox.production import VerifiedDatabase

# A local daemon answers in milliseconds; this bounds a wedged one.
_DOCKER_TIMEOUT_SECONDS = 120.0

# How long a restarted database server gets to begin accepting connections.
# Generous because a cold Postgres container initialises its data directory on
# first start, and that is slower than any subsequent boot.
_READY_TIMEOUT_SECONDS = 60.0
_READY_POLL_SECONDS = 0.2


class ResetStrategy(StrEnum):
    """Which mechanism returns the database to its baseline.

    Recorded on every reset so that a workload artifact carries not only its
    measurements but the means by which the state they were taken from was
    restored. Two runs reset by different strategies are not the same
    experiment, and a result that does not say which was used cannot be
    compared with one that does.
    """

    ROLLBACK_AND_RESTORE_SEQUENCES = "rollback_and_restore_sequences"
    """Undo the transaction, then put every sequence back. Cheapest correct option.

    Named for what it does rather than for the backlog's "transaction rollback",
    because rollback alone failed 10/10 in S-0.5 while passing the check the
    story specified. The sequence restore is not an enhancement; without it this
    strategy is the defect.
    """

    SNAPSHOT_RESTORE = "snapshot_restore"
    """Drop the database and recreate it from a template taken at baseline.

    Resets everything a transaction cannot, including schema changes. Requires
    terminating every other connection, so it cannot run concurrently with
    anything.
    """

    CONTAINER_RESTART = "container_restart"
    """Destroy the database server and its storage, then rebuild and reseed.

    The only strategy that survives the server itself being corrupted, and the
    only one that resets state living outside any database — on-disk files the
    server wrote, extensions loaded at boot, configuration set at runtime.
    """


class ResetError(Exception):
    """The database could not be returned to its baseline."""


class ResetNotPreparedError(ResetError):
    """`reset()` was called before a baseline was captured.

    Raised rather than capturing one lazily. A baseline captured after a
    workload has run is a baseline of the state the workload left, and every
    subsequent cycle would faithfully restore the wrong thing.
    """

    def __init__(self, strategy: ResetStrategy) -> None:
        self.strategy = strategy
        super().__init__(
            f"{strategy.value} has no baseline to restore to; call prepare() before the "
            "first workload, not after it"
        )


class DatabaseNotReadyError(ResetError):
    """The database did not accept connections within the allowed time."""

    def __init__(self, database: VerifiedDatabase, seconds: float) -> None:
        self.database = database
        self.seconds = seconds
        super().__init__(f"{database} did not accept connections within {seconds}s")


@dataclass(frozen=True)
class SequenceValue:
    """One sequence's position, as it stood at baseline.

    `is_called` distinguishes a sequence that has issued a value from one that
    never has. `pg_sequences.last_value` is NULL in the second case and cannot
    be handed to `setval`, so those restore to `start_value` with `is_called`
    false — otherwise the first `nextval` after a reset skips a number, which
    is the same defect this strategy exists to fix, one row smaller.
    """

    schema: str
    name: str
    value: int
    is_called: bool


class ResetMechanism(ABC):
    """One way of getting back to the baseline.

    The three implementations differ enormously in cost and in what they can
    undo, and not at all in how they are driven: capture a baseline once, then
    open a cycle around each workload. S-2.7 drives this interface ten times and
    decides whether the strategy actually works here.
    """

    strategy: ClassVar[ResetStrategy]

    @abstractmethod
    def prepare(self) -> None:
        """Capture the baseline that `reset()` will restore to."""

    @abstractmethod
    def begin(self) -> None:
        """Open a cycle, before the workload runs."""

    @abstractmethod
    def reset(self) -> None:
        """Close the cycle, restoring the baseline."""

    @contextmanager
    def cycle(self) -> Iterator[None]:
        """Run a workload with the reset guaranteed on the way out.

        The reset runs in a `finally`, because a workload that raised part-way
        through is the case where the database is most likely to be left in a
        state nobody predicted, and the case where skipping the reset would
        corrupt every measurement taken after it.
        """
        self.begin()
        try:
            yield
        finally:
            self.reset()


@dataclass
class RollbackReset(ResetMechanism):
    """Roll the transaction back, then put every sequence back where it was.

    The cheapest correct strategy at 19.2 ms, and the only one that needs no
    exclusive access, so it is the only one that composes with a concurrent
    experiment design.

    **It undoes work done on this object's own connection.** A workload driven
    inside a container has its own connection and commits independently; nothing
    here can roll that back. The precondition is not checkable from here — a
    connection cannot see what another connection committed and attribute it —
    so it is left to S-2.7, which runs ten cycles and finds the state did not
    return.
    """

    strategy: ClassVar[ResetStrategy] = ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES

    database: VerifiedDatabase
    _baseline: tuple[SequenceValue, ...] | None = field(default=None, init=False)
    _connection: psycopg.Connection[tuple[object, ...]] | None = field(default=None, init=False)

    @property
    def connection(self) -> psycopg.Connection[tuple[object, ...]]:
        """The connection the workload must share for a rollback to reach it."""
        if self._connection is None:
            self._connection = psycopg.connect(self.database.dsn)
        return self._connection

    def prepare(self) -> None:
        self._baseline = capture_sequences(self.connection)
        self.connection.commit()

    def begin(self) -> None:
        if self._baseline is None:
            raise ResetNotPreparedError(self.strategy)
        # psycopg opens a transaction on the first statement and holds it until
        # commit or rollback, so there is nothing to issue here. Rolling back
        # first discards anything left over from a previous cycle that failed
        # between its workload and its reset.
        self.connection.rollback()

    def reset(self) -> None:
        if self._baseline is None:
            raise ResetNotPreparedError(self.strategy)
        self.connection.rollback()
        restore_sequences(self.connection, self._baseline)
        self.connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


@dataclass
class SnapshotRestoreReset(ResetMechanism):
    """Drop the database and recreate it from a template taken at baseline.

    Resets everything a transaction cannot, schema included, at 163 ms, 8.5 times
    the cost of a rollback. It must terminate every other connection to the
    database before dropping it, which makes it unusable alongside any
    concurrent experiment, and is why it is not the default despite being more
    thorough.

    The maintenance connection goes to a *different* database, because
    `DROP DATABASE` cannot run from a connection to the database being dropped.
    """

    strategy: ClassVar[ResetStrategy] = ResetStrategy.SNAPSHOT_RESTORE

    database: VerifiedDatabase
    maintenance_database: str = "postgres"
    snapshot_suffix: str = "_coldfix_snapshot"
    _prepared: bool = field(default=False, init=False)

    @property
    def snapshot_name(self) -> str:
        return f"{self.database.name}{self.snapshot_suffix}"

    def prepare(self) -> None:
        with self._maintenance() as cur:
            _terminate_connections(cur, self.database.name)
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(self.snapshot_name))
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(self.snapshot_name), sql.Identifier(self.database.name)
                )
            )
        self._prepared = True

    def begin(self) -> None:
        if not self._prepared:
            raise ResetNotPreparedError(self.strategy)

    def reset(self) -> None:
        if not self._prepared:
            raise ResetNotPreparedError(self.strategy)
        with self._maintenance() as cur:
            _terminate_connections(cur, self.database.name)
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(self.database.name))
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(self.database.name), sql.Identifier(self.snapshot_name)
                )
            )

    def discard_snapshot(self) -> None:
        """Drop the template. Not part of a reset — cleanup for whoever owns the run."""
        with self._maintenance() as cur:
            _terminate_connections(cur, self.snapshot_name)
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(self.snapshot_name))
            )
        self._prepared = False

    @contextmanager
    def _maintenance(self) -> Iterator[psycopg.Cursor[tuple[object, ...]]]:
        """A cursor on another database, in autocommit — DDL here cannot be wrapped.

        `CREATE DATABASE` and `DROP DATABASE` cannot run inside a transaction
        block, so this connection is autocommit rather than psycopg's default.
        """
        with (
            psycopg.connect(
                self.database.dsn_for(self.maintenance_database), autocommit=True
            ) as conn,
            conn.cursor() as cur,
        ):
            yield cur


@dataclass
class ContainerRestartReset(ResetMechanism):
    """Destroy the database server and its storage, rebuild it, and reseed.

    The heaviest strategy and the only one that resets anything living outside
    the database itself. It is the fallback when the other two are found
    unreliable, and the one to reach for when a workload has corrupted the
    server rather than merely its data.

    Reseeding is SQL text rather than a dump archive. `pg_restore` needs a
    client binary whose version must match the server, which S-0.5 found is its
    own source of errors it then ignores; and the environment that owns dumps is
    standup, not reset.
    """

    strategy: ClassVar[ResetStrategy] = ResetStrategy.CONTAINER_RESTART

    database: VerifiedDatabase
    container: str
    image: str
    seed_sql: str
    environment: dict[str, str] = field(default_factory=dict)
    maintenance_database: str = "postgres"
    _prepared: bool = field(default=False, init=False)

    def prepare(self) -> None:
        # Nothing to capture. The baseline is the seed, which the caller
        # supplied, and which is by definition already recorded.
        self._prepared = True

    def begin(self) -> None:
        if not self._prepared:
            raise ResetNotPreparedError(self.strategy)

    def reset(self) -> None:
        if not self._prepared:
            raise ResetNotPreparedError(self.strategy)
        self._destroy()
        self._create()
        wait_until_ready(self.database, self.maintenance_database)
        self._seed()

    def _destroy(self) -> None:
        # `--volumes` is about storage, not about correctness, and the
        # distinction is worth stating because the obvious reading is wrong.
        # The Postgres image declares its data directory as a volume, so each
        # container gets an *anonymous* one and a rebuilt container gets a
        # fresh one whether or not the old is removed — the reset works either
        # way. What `--volumes` prevents is every cycle stranding a full
        # Postgres data directory that nothing will ever reclaim. An
        # investigation that resets a few hundred times fills the disk, which
        # is a slower failure than a bad reset and a harder one to attribute.
        _docker("rm", "--force", "--volumes", self.container)

    def _create(self) -> None:
        port = self.database.port or 5432
        argv = [
            "run",
            "--detach",
            "--name",
            self.container,
            "--publish",
            f"{port}:5432",
        ]
        for name, value in sorted(self.environment.items()):
            argv += ["--env", f"{name}={value}"]
        argv += ["--", self.image]

        result = _docker(*argv)
        if result != 0:
            message = f"could not start {self.image} as {self.container}"
            raise ResetError(message)

    def _seed(self) -> None:
        with psycopg.connect(self.database.dsn, autocommit=True) as conn:
            conn.execute(self.seed_sql)


def capture_sequences(
    connection: psycopg.Connection[tuple[object, ...]],
) -> tuple[SequenceValue, ...]:
    """Every sequence's position, for restoring later.

    `last_value` is NULL for a sequence that has never issued a value, and NULL
    cannot be given to `setval`. Those are recorded at `start_value` with
    `is_called` false, which is the state a fresh sequence is actually in.
    """
    rows = connection.execute(
        "SELECT schemaname, sequencename, last_value, start_value FROM pg_sequences"
    ).fetchall()

    captured = []
    for schema, name, last_value, start_value in rows:
        never_used = last_value is None
        captured.append(
            SequenceValue(
                schema=str(schema),
                name=str(name),
                value=int(str(start_value if never_used else last_value)),
                is_called=not never_used,
            )
        )
    return tuple(captured)


def restore_sequences(
    connection: psycopg.Connection[tuple[object, ...]],
    baseline: tuple[SequenceValue, ...],
) -> None:
    """Put every sequence back to its captured position.

    The identifiers are passed as parameters to Postgres's own `format('%I.%I')`
    rather than interpolated here, so a schema or sequence named something
    hostile is quoted by the server rather than by this function.

    Two pieces of syntax here are not decoration. The doubled `%%` is psycopg's
    escape — it reads `%` as the start of a placeholder and rejects `%I`
    outright, so the percent signs `format` needs have to survive that parse
    first. And the `::text` casts are required because `format` takes variadic
    `"any"`, which leaves Postgres unable to infer a parameter's type and
    refusing the statement rather than guessing.
    """
    for sequence in baseline:
        connection.execute(
            "SELECT setval(format('%%I.%%I', %s::text, %s::text)::regclass, %s::bigint, %s::bool)",
            (sequence.schema, sequence.name, sequence.value, sequence.is_called),
        )


def wait_until_ready(
    database: VerifiedDatabase,
    maintenance_database: str = "postgres",
    timeout: float = _READY_TIMEOUT_SECONDS,
) -> None:
    """Block until the server accepts a connection, or give up loudly.

    Polls the maintenance database rather than the subject, because after a
    container restart the subject may not exist until the seed has run, and
    "the database is missing" and "the server is not up" would otherwise be the
    same observation.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(database.dsn_for(maintenance_database), connect_timeout=2):
                return
        except psycopg.OperationalError:
            time.sleep(_READY_POLL_SECONDS)
    raise DatabaseNotReadyError(database, timeout)


def _drop_database(cur: psycopg.Cursor[tuple[object, ...]], name: str) -> None:
    """Identifier quoting is the server's job, not an f-string's."""
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def _terminate_connections(cur: psycopg.Cursor[tuple[object, ...]], database: str) -> None:
    """Disconnect everything else, so the database can be dropped.

    This is the reason `SNAPSHOT_RESTORE` cannot run concurrently with anything:
    it does not wait for other work to finish, it ends it.
    """
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (database,),
    )


def _docker(*args: str) -> int:
    try:
        return execute(["docker", *args], timeout=_DOCKER_TIMEOUT_SECONDS).exit_code
    except ExecutionStartError as error:
        message = f"docker could not be started: {error.cause}"
        raise ResetError(message) from error

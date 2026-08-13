"""The harness catches the reset that row counting certifies.

S-0.5 ran plain rollback ten times: every row count identical, every content
hash identical, every `max(id)` identical, sequences 250 higher than they
started. That is the case this file exists for, and
`test_the_harness_rejects_the_reset_that_row_counting_would_certify` is the test
that matters — it hands the harness the exact defect the spike found and
requires it to be rejected, with the sequence named in the diagnostic.

The rest split into two halves. One half plants a specific kind of drift —
content, max id, sequence, in-process cache — and asserts the harness sees that
one and says which. The other asserts it does *not* cry wolf: a working reset
verifies, and a working reset whose rows come back in a different physical order
still verifies, because a restore does not preserve row order and a harness that
required it would reject every correct strategy.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest

from coldfix.bench.execute import execute
from coldfix.sandbox import docker_available
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import (
    ResetMechanism,
    ResetNotPreparedError,
    ResetStrategy,
    RollbackReset,
    SnapshotRestoreReset,
    restore_sequences,
    wait_until_ready,
)
from coldfix.sandbox.verification import (
    NoReliableResetError,
    VerificationError,
    VerificationReport,
    VerifiedReset,
    capture_fingerprint,
    choose_reset,
    verify,
)

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"
PORT = 55445

SEED_SQL = """
CREATE TABLE ticket (id serial PRIMARY KEY, title text NOT NULL, status text NOT NULL);
INSERT INTO ticket (title, status) VALUES ('first', 'open'), ('second', 'open');
"""


@pytest.fixture(scope="module")
def _server() -> Iterator[str]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    container = f"coldfix-verify-test-{uuid.uuid4().hex[:8]}"
    execute(
        [
            "docker",
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
        ],
        timeout=180.0,
    )
    try:
        yield container
    finally:
        execute(["docker", "rm", "--force", "--volumes", container], timeout=180.0)


def url_for(name: str) -> str:
    return f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/{name}"


@pytest.fixture
def database(_server: str) -> Iterator[VerifiedDatabase]:
    maintenance = url_for("postgres")
    wait_until_ready(VerifiedDatabase(url_for("coldfix_bootstrap")), "postgres")

    name = f"coldfix_verify_{uuid.uuid4().hex[:8]}"
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


class BrokenRollback(RollbackReset):
    """Rollback without the sequence restore — the defect S-0.5 measured.

    Subclassed rather than described, so the harness is handed the real failure
    rather than a stand-in for it.
    """

    def reset(self) -> None:
        if self._baseline is None:
            raise ResetNotPreparedError(self.strategy)
        self.connection.rollback()
        self.connection.commit()


def insert_a_ticket(mechanism: RollbackReset) -> Callable[[], object]:
    """A workload, returning the ids it created as its observation."""

    def run() -> object:
        rows = mechanism.connection.execute(
            "INSERT INTO ticket (title, status) VALUES ('workload', 'open') RETURNING id"
        ).fetchall()
        return [int(str(row[0])) for row in rows]

    return run


# ------------------------------------------------- the case the spike found


def test_the_harness_rejects_the_reset_that_row_counting_would_certify(
    database: VerifiedDatabase,
) -> None:
    """The whole reason this story exists.

    Ten cycles of rollback without the sequence restore. Every row count comes
    back — which is what S-2.6's acceptance criterion asked to be asserted, and
    is satisfied perfectly. The harness must reject it anyway, and must say the
    sequence is what moved.
    """
    mechanism = BrokenRollback(database=database)

    report = verify(mechanism, database, insert_a_ticket(mechanism))
    mechanism.close()

    assert not report.reliable
    kinds = {item.kind for item in report.drift}
    assert "sequence" in kinds
    assert "ticket_id_seq" in report.diagnostic()

    # Row counts, content and max ids all came back on every cycle — the three
    # checks S-2.6's criterion asked for are satisfied by the broken strategy,
    # which is the entire point.
    assert kinds <= {"sequence", "observation"}
    assert "row count" not in report.diagnostic()
    assert "content hash" not in report.diagnostic()
    assert "max id" not in report.diagnostic()

    # The observation moved too, and only because the sequence did: shifted ids
    # mean the workload reports different ids. It is a second witness to the
    # same defect, not an independent one.
    assert "observation" in kinds


def test_the_working_strategy_verifies_over_ten_cycles(database: VerifiedDatabase) -> None:
    """The same workload, the same ten cycles, with the sequence restore present."""
    mechanism = RollbackReset(database=database)

    report = verify(mechanism, database, insert_a_ticket(mechanism))
    mechanism.close()

    assert report.reliable
    assert report.cycles == 10
    assert report.drift == ()
    assert "reliable over 10 cycles" in report.diagnostic()


# ------------------------------------------------ each check earns its place


def test_an_update_is_caught_by_the_content_hash(database: VerifiedDatabase) -> None:
    """A row count cannot see an `UPDATE`, and neither can `max(id)`."""
    mechanism = RollbackReset(database=database)

    def commit_an_update() -> object:
        with psycopg.connect(database.dsn, autocommit=True) as conn:
            conn.execute("UPDATE ticket SET status = 'closed' WHERE id = 1")
        return None

    report = verify(mechanism, database, commit_an_update, cycles=2)
    mechanism.close()

    assert not report.reliable
    assert {item.kind for item in report.drift} == {"content hash"}
    assert "ticket" in report.diagnostic()


def test_a_delete_and_reinsert_is_caught_by_the_max_id(database: VerifiedDatabase) -> None:
    """Row count identical, content changed, and the id is what names it clearly."""
    mechanism = RollbackReset(database=database)

    def churn() -> object:
        with psycopg.connect(database.dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM ticket WHERE id = (SELECT min(id) FROM ticket)")
            conn.execute("INSERT INTO ticket (title, status) VALUES ('replacement', 'open')")
        return None

    report = verify(mechanism, database, churn, cycles=2)
    mechanism.close()

    assert not report.reliable
    assert "max id" in {item.kind for item in report.drift}


def test_a_surviving_process_is_caught_and_output_comparison_cannot_catch_it(
    database: VerifiedDatabase,
) -> None:
    """The cache check, and the demonstration that the obvious one does not work.

    S-0.5's cached `QuerySet` survives every database-side reset because the
    rows are in a Python object. The tempting way to catch it is by comparing
    the workload's output — and the first half of this test shows that cannot
    work: a workload that memoises its first answer returns the *same* value
    every cycle, and so does a correct one, because a correct reset makes every
    cycle identical. The database is clean, the output is constant, and the
    cache is invisible.

    So the harness checks the condition that makes a cache possible instead. A
    process that survives from one cycle to the next is one that can hold a
    cached row no reset will clear, and that is checkable without knowing
    anything about the framework.
    """
    mechanism = RollbackReset(database=database)
    cache: list[object] = []

    def memoising_workload() -> object:
        rows = mechanism.connection.execute(
            "INSERT INTO ticket (title, status) VALUES ('workload', 'open') RETURNING id"
        ).fetchall()
        if not cache:
            cache.append([int(str(row[0])) for row in rows])
        return list(cache)

    # A stale cache is indistinguishable from a correct reset by output alone.
    blind = verify(mechanism, database, memoising_workload, cycles=3)
    assert blind.reliable

    # The same run, with the process identity offered. One process throughout,
    # so every cycle after the first reports the same identity and is flagged.
    one_process = verify(
        mechanism,
        database,
        memoising_workload,
        cycles=3,
        process_identity=lambda: "the same interpreter every time",
    )
    mechanism.close()

    assert not one_process.reliable
    assert {item.kind for item in one_process.drift} == {"process"}
    assert "the same one as cycle 1" in one_process.diagnostic()


def test_a_fresh_process_each_cycle_verifies(database: VerifiedDatabase) -> None:
    """What S-2.1 actually provides, asserted rather than assumed.

    ADR 025 claims container destruction after every run is the other half of
    the reset contract. This is the shape of that claim being checked: a
    distinct identity per cycle passes, so the check is not simply always-fail.
    """
    mechanism = RollbackReset(database=database)
    cycle_count = iter(range(100))

    report = verify(
        mechanism,
        database,
        insert_a_ticket(mechanism),
        cycles=3,
        process_identity=lambda: f"container-{next(cycle_count)}",
    )
    mechanism.close()

    assert report.reliable


def test_offering_no_process_identity_skips_the_cache_check(
    database: VerifiedDatabase,
) -> None:
    """Documented, and asserted, because opting out is how the defect returns."""
    mechanism = RollbackReset(database=database)

    report = verify(mechanism, database, insert_a_ticket(mechanism), cycles=3)
    mechanism.close()

    assert report.reliable
    assert not any(item.kind == "process" for item in report.drift)


def test_row_order_changing_is_not_drift(database: VerifiedDatabase) -> None:
    """A restore does not preserve physical row order.

    A content hash that folded rows in table order would report every correct
    strategy as broken, so the hash orders by the row's own text first. This
    rewrites the table's physical order between cycles and requires silence.
    """
    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO ticket (title, status) VALUES ('third', 'open')")

    before = capture_fingerprint(database)

    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE reordered AS SELECT * FROM ticket ORDER BY id DESC")
        conn.execute("DELETE FROM ticket")
        conn.execute("INSERT INTO ticket SELECT * FROM reordered ORDER BY id DESC")
        conn.execute("DROP TABLE reordered")

    after = capture_fingerprint(database)

    assert after.content_hashes["ticket"] == before.content_hashes["ticket"]


# ------------------------------------------------------------- the fallback


def test_the_first_working_strategy_is_chosen(database: VerifiedDatabase) -> None:
    """AC 4. A broken candidate is skipped and the next one is tried."""
    broken = BrokenRollback(database=database)
    working = SnapshotRestoreReset(database=database)

    def commit_something() -> object:
        with psycopg.connect(database.dsn, autocommit=True) as conn:
            conn.execute("INSERT INTO ticket (title, status) VALUES ('committed', 'open')")
        return None

    chosen = choose_reset([broken, working], database, commit_something, cycles=3)
    broken.close()
    working.discard_snapshot()

    assert chosen.strategy is ResetStrategy.SNAPSHOT_RESTORE
    assert chosen.report.reliable


def test_running_out_of_strategies_reports_all_of_them(database: VerifiedDatabase) -> None:
    """ "Nothing worked" is not actionable; "each failed like this" is."""
    first = BrokenRollback(database=database)
    second = BrokenRollback(database=database)

    with pytest.raises(NoReliableResetError) as raised:
        choose_reset([first, second], database, insert_a_ticket(first), cycles=2)

    first.close()
    second.close()
    assert len(raised.value.reports) == 2
    assert "ticket_id_seq" in str(raised.value)


def test_a_strategy_that_cannot_run_is_a_report_not_a_crash(
    database: VerifiedDatabase,
) -> None:
    """A candidate failing to start is an ordinary finding.

    The caller has other strategies; raising here would deny them the chance to
    be tried, and would turn a recoverable situation into a stopped run.
    """

    class Unusable(RollbackReset):
        def prepare(self) -> None:
            message = "this server does not permit it"
            raise RuntimeError(message)

    report = verify(Unusable(database=database), database, lambda: None, cycles=2)

    assert not report.reliable
    assert report.failure is not None
    assert "could not run" in report.diagnostic()


# -------------------------------------------------------- verified before use


def test_an_unverified_strategy_cannot_be_used(database: VerifiedDatabase) -> None:
    """S-2.6's "verified before use", made into something a caller cannot skip.

    The thing needed to run experiments is the thing only verification produces,
    so there is no path that reaches a reset which was never checked.
    """
    failed = VerificationReport(
        strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        cycles=10,
        drift=(),
        failure="the server went away",
    )

    with pytest.raises(VerificationError, match="did not verify"):
        VerifiedReset(mechanism=RollbackReset(database=database), report=failed)


def test_a_verified_reset_carries_its_evidence(database: VerifiedDatabase) -> None:
    mechanism = RollbackReset(database=database)
    report = verify(mechanism, database, insert_a_ticket(mechanism))
    mechanism.close()

    verified = VerifiedReset(mechanism=mechanism, report=report)

    assert verified.strategy is ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES
    assert verified.report.cycles == 10


def test_the_number_of_cycles_is_ten_by_default(database: VerifiedDatabase) -> None:
    """AC 1. S-0.5 ran ten, and the defect it found accumulates one unit a cycle."""
    mechanism = RollbackReset(database=database)

    report = verify(mechanism, database, insert_a_ticket(mechanism))
    mechanism.close()

    assert report.cycles == 10


def test_a_non_positive_cycle_count_is_refused(database: VerifiedDatabase) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        verify(RollbackReset(database=database), database, lambda: None, cycles=0)


def test_offering_no_candidates_is_an_error_not_a_silent_pass(
    database: VerifiedDatabase,
) -> None:
    with pytest.raises(VerificationError, match="no candidate"):
        choose_reset([], database, lambda: None)


# ------------------------------------------------------------ the fingerprint


def test_the_fingerprint_covers_all_four_parts(database: VerifiedDatabase) -> None:
    """Row counts alone certified a reset that failed 10/10. All four, or none."""
    fingerprint = capture_fingerprint(database)

    assert fingerprint.row_counts["ticket"] == 2
    assert fingerprint.content_hashes["ticket"]
    assert fingerprint.max_ids["ticket"] == 2
    assert any(s.name == "ticket_id_seq" for s in fingerprint.sequences)


def test_a_table_without_an_id_column_has_no_max_id(database: VerifiedDatabase) -> None:
    """Asked of the catalogue, because a failed query would abort the transaction."""
    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE settings (key text PRIMARY KEY, value text)")

    fingerprint = capture_fingerprint(database)

    assert fingerprint.max_ids["settings"] is None
    assert fingerprint.row_counts["settings"] == 0


def test_the_fingerprint_ignores_the_system_catalogues(database: VerifiedDatabase) -> None:
    """Otherwise every capture reports drift in tables nobody touched."""
    fingerprint = capture_fingerprint(database)

    assert not any(name.startswith("pg_") for name in fingerprint.row_counts)


def test_sequences_restored_by_hand_match_the_baseline(database: VerifiedDatabase) -> None:
    """The fingerprint's sequence comparison is exact, not approximate."""
    before = capture_fingerprint(database)

    with psycopg.connect(database.dsn, autocommit=True) as conn:
        conn.execute("SELECT nextval('ticket_id_seq')")
        assert capture_fingerprint(database).sequences != before.sequences
        restore_sequences(conn, before.sequences)

    assert capture_fingerprint(database).sequences == before.sequences


def test_the_reset_mechanism_interface_is_what_the_harness_drives(
    database: VerifiedDatabase,
) -> None:
    """The harness drives the abstract interface, not any one implementation."""
    assert isinstance(RollbackReset(database=database), ResetMechanism)
    assert isinstance(SnapshotRestoreReset(database=database), ResetMechanism)

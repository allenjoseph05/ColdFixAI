"""S-7.2 — standing a project up, and the two states that look identical.

The story's note is the target: *the two failure states look identical without
log access. The agent needs those tools or it guesses.* So the tests that matter
most are the ones that put something on a port and check the diagnosis changes.

Most of these need no Docker at all — a plain socket that accepts and hangs up is
a perfectly good *listening but not speaking Postgres*, which is the harder half
of the distinction.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator

import pytest

from coldfix.bench.execute import execute
from coldfix.explorer import standup
from coldfix.explorer.standup import (
    Diagnosis,
    ServiceState,
    Standup,
    StandupError,
    accepts_connections,
    diagnose,
    logs,
    ps,
    stand_up,
)
from coldfix.sandbox import docker_available
from coldfix.sandbox.production import VerifiedDatabase

USER = PASSWORD = "coldfix_test"


def url_for(port: int, name: str = "coldfix_subject") -> VerifiedDatabase:
    """`127.0.0.1`, not `localhost`, and the difference is four seconds a probe.

    `localhost` resolves to `::1` first on this machine, and the fixtures below
    bind to `127.0.0.1` — so every probe paid a full IPv6 timeout before trying
    the address something was actually on. The timeout is per resolved address,
    which is worth knowing for the product and not only for the tests.
    """
    return VerifiedDatabase(f"postgresql://{USER}:{PASSWORD}@127.0.0.1:{port}/{name}")


@pytest.fixture
def dead_port() -> int:
    """A port nothing is listening on: bound, read, released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A port that accepts a connection and says nothing.

    This is what makes the distinction testable without a database: something is
    listening, and it is not Postgres. A real server mid-initialisation looks the
    same from the socket's side.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = int(listener.getsockname()[1])

    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            accepted.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()


# ================== AC 2: the two states that look identical without help


def test_nothing_listening_is_not_listening(dead_port: int) -> None:
    found = diagnose(url_for(dead_port))

    assert found.state is ServiceState.NOT_LISTENING
    assert found.socket_error
    assert found.protocol_error is None


def test_something_listening_that_is_not_a_database_is_refusing(silent_port: int) -> None:
    """The other half, and the one an error message alone cannot give you.

    A server still initialising looks exactly like this from the socket's side —
    and the repair is *wait*, where the repair for a bad password is not.
    """
    found = diagnose(url_for(silent_port))

    assert found.state is ServiceState.REFUSING
    assert found.protocol_error
    assert found.socket_error is None


def test_the_two_states_prescribe_different_repairs(dead_port: int, silent_port: int) -> None:
    """The point of separating them. One says wait or check the port; the other
    says read the server's message, because waiting will not fix credentials."""
    down = diagnose(url_for(dead_port))
    up_but_refusing = diagnose(url_for(silent_port))

    assert down.state.action != up_but_refusing.state.action
    assert "not accepting yet" in down.state.action
    assert "Waiting longer will not fix" in up_but_refusing.state.action


def test_the_diagnosis_carries_the_probes_that_produced_it(silent_port: int) -> None:
    """A two-step inference a reader may disagree with, so the steps travel."""
    described = diagnose(url_for(silent_port)).describe()

    assert "REFUSING" in described
    assert "server said:" in described
    assert "next:" in described


def test_the_socket_probe_says_nothing_about_postgres(silent_port: int, dead_port: int) -> None:
    """Kept separate on purpose: it answers *is something there*, and that is
    what makes the two failure states distinguishable at all."""
    assert accepts_connections("127.0.0.1", silent_port) is None
    assert accepts_connections("127.0.0.1", dead_port) is not None


def test_a_missing_container_is_its_own_state(dead_port: int) -> None:
    """*Exited* is a different repair from *still initialising*, and both look
    identical at the socket."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    found = diagnose(url_for(dead_port), container=f"absent-{uuid.uuid4().hex[:8]}")

    assert found.state is ServiceState.NO_CONTAINER
    assert "start the service" in found.state.action


def test_an_unreachable_docker_is_unknown_rather_than_a_verdict(
    dead_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-3.1's rule: ignorance flattened into a verdict is worse than ignorance.

    Reporting `NO_CONTAINER` here would send the agent to restart a service it
    cannot see. Forced rather than waited for, because Docker is running on this
    machine and the branch is about what `diagnose` does with *cannot say*.
    """
    monkeypatch.setattr(standup, "container_running", lambda _name: None)

    found = diagnose(url_for(dead_port), container="whatever")

    assert found.state is ServiceState.UNKNOWN
    assert "check that Docker is running" in found.state.action


def test_every_state_prescribes_something_different() -> None:
    """Four states exist because there are four repairs; if two shared one, the
    split would be decoration."""
    actions = {state.action for state in ServiceState}

    assert len(actions) == len(ServiceState)


# =========================================================== AC 3: the tools


@pytest.mark.docker
def test_logs_reads_a_container_s_output() -> None:
    """The tool that turns `REFUSING` into a repair."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    name = f"coldfix-logs-{uuid.uuid4().hex[:8]}"
    execute(
        ["docker", "run", "--detach", "--name", name, "alpine", "sh", "-c", "echo standing up"],
        timeout=120.0,
    )
    try:
        assert "standing up" in logs(name)
    finally:
        execute(["docker", "rm", "--force", name], timeout=120.0)


@pytest.mark.docker
def test_logs_refuses_a_container_that_does_not_exist() -> None:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    with pytest.raises(StandupError, match="could not read the logs"):
        logs(f"absent-{uuid.uuid4().hex[:8]}")


@pytest.mark.docker
def test_ps_lists_what_is_running() -> None:
    """So *no container* can be told from *the name is wrong*."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    name = f"coldfix-ps-{uuid.uuid4().hex[:8]}"
    execute(["docker", "run", "--detach", "--name", name, "alpine", "sleep", "30"], timeout=120.0)
    try:
        assert name in ps()
    finally:
        execute(["docker", "rm", "--force", name], timeout=120.0)


# =============================== AC 1: the three steps, in the order that works


def test_standup_stops_at_the_first_failure_and_says_which(dead_port: int) -> None:
    report = stand_up(
        database=url_for(dead_port),
        start_database=["python", "-c", "raise SystemExit(1)"],
        install_dependencies=["python", "-c", "pass"],
        run_migrations=["python", "-c", "pass"],
    )

    assert not report.succeeded
    assert report.stopped_at is not None
    assert report.stopped_at.name == "start the database"
    assert "stopped at: start the database" in report.describe()
    # The steps after it were not attempted. Asserted on the list rather than on
    # the verdict: continuing past a failure leaves `stopped_at` and `succeeded`
    # both unchanged, so a test of those alone passes either way — found by
    # sabotage.
    assert [step.name for step in report.steps] == ["start the database"]


def test_migrations_are_not_attempted_against_a_database_that_is_not_up(
    dead_port: int,
) -> None:
    """The common way this goes wrong, and the error it produces is the one the
    story's note is about — so the database is diagnosed between starting it and
    migrating."""
    report = stand_up(
        database=url_for(dead_port),
        start_database=["python", "-c", "pass"],
        install_dependencies=["python", "-c", "pass"],
        run_migrations=["python", "-c", "raise SystemExit('should never run')"],
    )

    assert not report.succeeded
    assert [step.name for step in report.steps] == ["start the database", "wait for the database"]
    assert report.diagnosis is not None
    assert report.diagnosis.state is ServiceState.NOT_LISTENING


def test_the_diagnosis_travels_with_a_failed_standup(dead_port: int) -> None:
    report = stand_up(
        database=url_for(dead_port),
        start_database=["python", "-c", "raise SystemExit(1)"],
        install_dependencies=["python", "-c", "pass"],
        run_migrations=["python", "-c", "pass"],
    )

    assert report.diagnosis is not None
    assert "next:" in report.describe()


def test_a_report_with_no_steps_is_not_a_success() -> None:
    """An empty run is not a stood-up environment, and `all(())` is `True` —
    which would make *nothing happened* indistinguishable from *it worked*."""
    assert not Standup().succeeded


def test_the_failed_step_reports_what_was_attempted(dead_port: int) -> None:
    """S-7.10 wants *which stage never completed and what was attempted there*;
    this is the record that makes it answerable."""
    report = stand_up(
        database=url_for(dead_port),
        start_database=["python", "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
        install_dependencies=["python", "-c", "pass"],
        run_migrations=["python", "-c", "pass"],
    )

    failed = report.stopped_at
    assert failed is not None
    assert failed.exit_code == 2
    assert "boom" in failed.output
    assert "ran: python" in report.describe()


# ================================================= a real database, end to end


@pytest.mark.postgres
@pytest.mark.slow
def test_a_real_database_reads_as_ready_only_once_it_is() -> None:
    """The whole distinction against a real server: not listening, then ready.

    A container that has just been created is not yet accepting, which is the
    state that would otherwise be reported as a failure and sent to a human.
    """
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    port = 55443
    name = f"coldfix-standup-{uuid.uuid4().hex[:8]}"
    database = url_for(port, "coldfix_standup")

    assert diagnose(database).state is ServiceState.NOT_LISTENING

    execute(
        [
            "docker", "run", "--detach", "--name", name,
            "--publish", f"{port}:5432",
            "--env", f"POSTGRES_USER={USER}", "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", "POSTGRES_DB=coldfix_standup", "--", "postgres:16-alpine",
        ],
        timeout=180.0,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if diagnose(database, container=name).state is ServiceState.READY:
                break
            time.sleep(0.5)

        found = diagnose(database, container=name)
        assert found.state is ServiceState.READY
        assert found.state.ready
        assert "nothing; it is up" in found.state.action
    finally:
        execute(["docker", "rm", "--force", "--volumes", name], timeout=180.0)


@pytest.mark.postgres
@pytest.mark.slow
def test_a_wrong_password_reads_as_refusing_not_as_down() -> None:
    """The fork the note is about, against a real server: waiting will never fix
    this, and `NOT_LISTENING` would have told the agent to wait."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    port = 55444
    name = f"coldfix-standup-auth-{uuid.uuid4().hex[:8]}"
    execute(
        [
            "docker", "run", "--detach", "--name", name,
            "--publish", f"{port}:5432",
            "--env", f"POSTGRES_USER={USER}", "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", "POSTGRES_DB=coldfix_standup", "--", "postgres:16-alpine",
        ],
        timeout=180.0,
    )  # fmt: skip
    try:
        correct = url_for(port, "coldfix_standup")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if diagnose(correct).state is ServiceState.READY:
                break
            time.sleep(0.5)

        wrong = VerifiedDatabase(f"postgresql://{USER}:wrong@127.0.0.1:{port}/coldfix_standup")
        found: Diagnosis = diagnose(wrong, container=name)

        assert found.state is ServiceState.REFUSING
        assert found.protocol_error
        assert "Waiting longer will not fix" in found.state.action
    finally:
        execute(["docker", "rm", "--force", "--volumes", name], timeout=180.0)

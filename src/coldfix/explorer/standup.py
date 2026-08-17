"""Standing a project up, and telling apart the two ways a database is not there.

Epic 7, S-7.2. The acceptance criteria are three, and the note under them is the
whole story: *the two failure states look identical without log access. The agent
needs those tools or it guesses.*

**The two states are told apart by two probes, not by reading one error message.**
`psycopg` raises `OperationalError` for both *nothing is listening* and *something
is listening and refused you*, and the text differs by driver version, locale and
server release. Matching on it is a guess dressed as a check. So the socket is
probed first, on its own:

| Socket | Protocol | State | What to do about it |
|---|---|---|---|
| refused | — | `NOT_LISTENING` | the server is not up yet, or the port is wrong |
| accepts | fails | `REFUSING` | it is up: credentials, a missing database, or still initialising |
| accepts | succeeds | `READY` | nothing |

That is a measurement rather than an interpretation, and it is the difference
between *wait longer* and *fix your password* — which is exactly the fork the
note says an agent cannot see without help.

**`NO_CONTAINER` is a third state, not a flavour of the first.** Nothing
listening because the container exited is a different repair from nothing
listening because Postgres is still initialising, and both look identical at the
socket. Docker is asked before the socket is, so the answer names the cause it
can name.

**`UNKNOWN` is a fourth, and it is not a failure.** If Docker itself is
unreachable, this cannot tell which of the others holds — and reporting
`NOT_LISTENING` in that case would send the agent to restart a service it cannot
see. S-3.1's rule: ignorance flattened into a verdict is worse than ignorance
reported.

**`logs` and `ps` exist because a diagnosis is not a repair.** The state says
which class of thing is wrong; the log line says which one. They are the tools AC
3 asks for, and they are deliberately read-only.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import psycopg

from coldfix.bench.execute import execute
from coldfix.sandbox.production import VerifiedDatabase

# Long enough for a loaded machine to answer, short enough that a wrong port is
# diagnosed rather than waited on. The reset harness uses a much longer deadline
# because it is *waiting*; this is *asking*.
#
# **It is per resolved address, not per probe.** A host that resolves to both
# `::1` and `127.0.0.1` with a server on only one of them pays this timeout once
# before reaching the other — measured at four seconds a diagnosis against
# `localhost` on Windows. Prefer a literal address where the caller knows one.
PROBE_TIMEOUT_SECONDS = 2.0
DOCKER_TIMEOUT_SECONDS = 60.0


class StandupError(Exception):
    """The environment could not be stood up, or could not be diagnosed."""


class ServiceState(StrEnum):
    """What is between us and the database. Four answers, four repairs."""

    READY = "accepting connections and answering"
    NO_CONTAINER = "no container is running for this service"
    NOT_LISTENING = "a container is running and nothing is accepting on the port"
    REFUSING = "something is accepting on the port and refused the connection"
    UNKNOWN = "Docker could not be reached, so which of the others holds is not known"

    @property
    def ready(self) -> bool:
        return self is ServiceState.READY

    @property
    def action(self) -> str:
        """What an agent should do next. The reason the states are separate."""
        return {
            ServiceState.READY: "nothing; it is up",
            ServiceState.NO_CONTAINER: "start the service — it is not running at all",
            ServiceState.NOT_LISTENING: (
                "wait, or check the port — the container is up but the server inside it is not "
                "accepting yet"
            ),
            ServiceState.REFUSING: (
                "read the server's message: this is credentials, a missing database, or an "
                "initialisation still in progress. Waiting longer will not fix the first two"
            ),
            ServiceState.UNKNOWN: "check that Docker is running; nothing here could be measured",
        }[self]


@dataclass(frozen=True)
class Diagnosis:
    """What was probed, what each probe said, and the conclusion.

    The probes are carried because the conclusion is a two-step inference and a
    reader who disagrees with it needs to see the steps.
    """

    state: ServiceState
    socket_error: str | None = None
    protocol_error: str | None = None
    container: str | None = None

    def describe(self) -> str:
        lines = [f"{self.state.name}: {self.state.value}"]
        if self.container:
            lines.append(f"  container: {self.container}")
        if self.socket_error:
            lines.append(f"  socket: {self.socket_error}")
        if self.protocol_error:
            lines.append(f"  server said: {self.protocol_error}")
        lines.append(f"  next: {self.state.action}")
        return "\n".join(lines)


def accepts_connections(
    host: str, port: int, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> str | None:
    """Whether anything is accepting TCP here. `None` means it is.

    Deliberately the whole of this probe: it answers *is something there*, and
    nothing about whether that something is Postgres. Keeping it separate is what
    makes the two failure states distinguishable at all.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as error:
        return str(error)


def speaks_postgres(
    database: VerifiedDatabase, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> str | None:
    """Whether a connection completes. `None` means it does.

    Takes a `VerifiedDatabase` for S-2.5's reason — there is no unverified
    handle to probe with — and returns the server's own words rather than a
    classification of them, because the words are what a human acts on.
    """
    try:
        with psycopg.connect(database.dsn, connect_timeout=int(timeout)):
            return None
    except psycopg.Error as error:
        return str(error).strip()


def container_running(name: str) -> bool | None:
    """Whether a container by this name is running. `None` if Docker cannot say."""
    result = execute(
        ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
        timeout=DOCKER_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        return None
    return name in result.stdout.split()


def diagnose(database: VerifiedDatabase, *, container: str | None = None) -> Diagnosis:
    """Which of the four states holds, measured rather than inferred from a message.

    The order matters. Docker is asked first because *the container exited* is a
    cause the socket cannot report; the socket second because it separates *not
    up* from *up and refusing*; the protocol last because its error means one
    thing when the socket answered and another when it did not.
    """
    if container is not None:
        running = container_running(container)
        if running is None:
            return Diagnosis(ServiceState.UNKNOWN, container=container)
        if not running:
            return Diagnosis(ServiceState.NO_CONTAINER, container=container)

    port = database.port or 5432
    refused = accepts_connections(database.host, port)
    if refused is not None:
        return Diagnosis(ServiceState.NOT_LISTENING, socket_error=refused, container=container)

    spoke = speaks_postgres(database)
    if spoke is not None:
        return Diagnosis(ServiceState.REFUSING, protocol_error=spoke, container=container)

    return Diagnosis(ServiceState.READY, container=container)


def logs(container: str, *, lines: int = 50) -> str:
    """AC 3. The tool that turns `REFUSING` into a repair.

    Read-only, and bounded: S-1.1's `execute` caps captured output, and a
    container that has been failing for an hour would otherwise return more than
    a prompt can hold.
    """
    result = execute(
        ["docker", "logs", "--tail", str(lines), container], timeout=DOCKER_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        said = result.stderr.strip() or "no such container"
        message = (
            f"could not read the logs for {container!r}: {said}. Without them, REFUSING and "
            "NOT_LISTENING are two names for the same observation"
        )
        raise StandupError(message)
    return result.stdout + result.stderr


def ps() -> str:
    """AC 3. What is running, so *no container* can be told from *wrong name*."""
    result = execute(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
        timeout=DOCKER_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        message = f"could not list containers: {result.stderr.strip()}"
        raise StandupError(message)
    return result.stdout


@dataclass(frozen=True)
class Step:
    """One thing standup did, and whether it worked."""

    name: str
    command: tuple[str, ...]
    exit_code: int
    output: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class Standup:
    """What standing this project up did, in order, and where it stopped.

    Stops at the first failure and keeps the steps that ran. `08-audit.md` and
    S-7.10 both want *which stage never completed and what was attempted there*;
    this is the record that makes it answerable, though the stage machinery and
    its budgets are S-7.11's and S-7.10's.
    """

    steps: list[Step] = field(default_factory=list)
    diagnosis: Diagnosis | None = None

    @property
    def succeeded(self) -> bool:
        return bool(self.steps) and all(step.succeeded for step in self.steps)

    @property
    def stopped_at(self) -> Step | None:
        return next((step for step in self.steps if not step.succeeded), None)

    def describe(self) -> str:
        lines = [f"Standup: {'ready' if self.succeeded else 'did not complete'}"]
        for step in self.steps:
            lines.append(f"  {'ok  ' if step.succeeded else 'FAIL'} {step.name}")
        failed = self.stopped_at
        if failed is not None:
            lines.append(f"  stopped at: {failed.name}")
            lines.append(f"    ran: {' '.join(failed.command)}")
            lines.append(f"    said: {failed.output.strip()[:400]}")
        if self.diagnosis is not None:
            lines.append("  " + self.diagnosis.describe().replace("\n", "\n  "))
        return "\n".join(lines)


def run_step(name: str, command: Sequence[str], *, timeout: float) -> Step:
    result = execute(list(command), timeout=timeout)
    return Step(
        name=name,
        command=tuple(command),
        exit_code=result.exit_code,
        output=result.stdout + result.stderr,
    )


def stand_up(  # noqa: PLR0913 - the three commands are AC 1's three steps and
    # cannot be bundled without inventing a config object with one implementation;
    # the database is what gets diagnosed between them, and the container is what
    # separates *exited* from *still starting*.
    *,
    database: VerifiedDatabase,
    start_database: Sequence[str],
    install_dependencies: Sequence[str],
    run_migrations: Sequence[str],
    container: str | None = None,
    timeout: float = 600.0,
) -> Standup:
    """AC 1, in the one order that works, stopping at the first failure.

    Migrations before the database is accepting connections is the common way
    this goes wrong and the error it produces is the one the note is about — so
    the database is diagnosed *between* starting it and migrating, and the
    diagnosis travels with the report either way.

    The commands are supplied rather than derived: S-7.1's fingerprint says what
    the project is, and what stands *this* project up is a fact about its
    tooling that E14's adapter owns.
    """
    report = Standup()

    report.steps.append(run_step("start the database", start_database, timeout=timeout))
    if not report.steps[-1].succeeded:
        report.diagnosis = diagnose(database, container=container)
        return report

    report.diagnosis = diagnose(database, container=container)
    if not report.diagnosis.state.ready:
        report.steps.append(
            Step(
                name="wait for the database",
                command=("<probe>",),
                exit_code=1,
                output=report.diagnosis.describe(),
            )
        )
        return report

    for name, command in (
        ("install dependencies", install_dependencies),
        ("run migrations", run_migrations),
    ):
        step = run_step(name, command, timeout=timeout)
        report.steps.append(step)
        if not step.succeeded:
            break

    return report

"""Epic 2 composed: a sandboxed workload, a real database, reset between cycles.

Every other file here tests one module. This one runs the epic as a whole, and
exists because the modules were individually verified and never put together —
the reset tests connected to Postgres from the host, and the sandbox tests ran
workloads that talked to nothing. Nothing proved a containerised workload could
reach a database and be reset afterwards, which is the only thing Epic 2 is for.

**Two things had to change before this could be written**, and both were
invisible while the modules were tested apart.

`Sandbox` hardcoded `--network none`, so a container had loopback and nothing
else and could not reach a sibling Postgres by any route. That was not a gap in
the tests; it was the architecture refusing to run a Django application. ADR 029
records the fix and `InternalNetwork` is it.

Then the reset would not run either. `SnapshotRestoreReset` connects from the
**host**, and a host cannot reach a network created `--internal`. So the
database sits on two networks — the internal one the workload uses, and the
default bridge with a published port for the harness — while the workload
container is on the internal one alone. The subject's code keeps no route off
the host; the database, which runs no subject code, is reachable by the thing
that has to reset it. That asymmetry is the topology real standup will need and
it took composing the epic to find.

What one run asserts: a sandboxed workload reaches its database; the same
container still cannot reach the internet; the container sees the worktree and
not the repository; ten cycles of workload-and-reset return every part of the
starting state; and every cycle ran in a container of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from coldfix.bench.execute import execute
from coldfix.sandbox import docker_available
from coldfix.sandbox.modes import ExecutionMode, Workbench
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import SnapshotRestoreReset, wait_until_ready
from coldfix.sandbox.runner import InternalNetwork, ResourceLimits
from coldfix.sandbox.verification import verify
from coldfix.sandbox.worktrees import Repository

pytestmark = [pytest.mark.postgres, pytest.mark.docker, pytest.mark.slow]

# One image for both roles. The database is Postgres and the workload driver is
# `psql`, which ships in the same image — and has to, because a workload
# container on an internal network cannot install anything.
IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"
DATABASE = "coldfix_e2e"
HOST_PORT = 55471

# The alias the database answers to on the internal network. `db` rather than
# the container's unique name because the production guard's default host
# allowlist contains `db` and not a random suffix — a network alias keeps the
# container name unique and the hostname conventional.
INTERNAL_ALIAS = "db"

SEED_SQL = (
    "CREATE TABLE ticket (id serial PRIMARY KEY, title text NOT NULL); "
    "INSERT INTO ticket (title) VALUES ('first'), ('second'), ('third');"
)


@dataclass(frozen=True)
class Environment:
    """The two databases are the same server seen from two places."""

    network: InternalNetwork
    container: str
    from_host: VerifiedDatabase
    from_container: VerifiedDatabase


@pytest.fixture(scope="module")
def environment() -> Iterator[Environment]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    suffix = uuid.uuid4().hex[:8]
    network = InternalNetwork.create(f"coldfix-e2e-net-{suffix}")
    container = f"coldfix-e2e-db-{suffix}"

    # Created on the default bridge with a published port, then additionally
    # attached to the internal network under a stable alias. The order matters:
    # a container created directly on an internal network cannot publish a port,
    # because there is no NAT to publish through.
    execute(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            f"{HOST_PORT}:5432",
            "--env",
            f"POSTGRES_USER={USER}",
            "--env",
            f"POSTGRES_PASSWORD={PASSWORD}",
            "--env",
            f"POSTGRES_DB={DATABASE}",
            "--pull",
            "never",
            "--",
            IMAGE,
        ],
        timeout=300.0,
    )
    execute(
        ["docker", "network", "connect", "--alias", INTERNAL_ALIAS, network.name, container],
        timeout=120.0,
    )

    environment = Environment(
        network=network,
        container=container,
        from_host=VerifiedDatabase(
            f"postgresql://{USER}:{PASSWORD}@localhost:{HOST_PORT}/{DATABASE}"
        ),
        from_container=VerifiedDatabase(
            f"postgresql://{USER}:{PASSWORD}@{INTERNAL_ALIAS}:5432/{DATABASE}"
        ),
    )

    wait_until_ready(environment.from_host, "postgres")
    psql(environment, SEED_SQL)

    try:
        yield environment
    finally:
        execute(["docker", "rm", "--force", "--volumes", container], timeout=300.0)
        network.destroy()


def psql(environment: Environment, sql: str) -> str:
    """Run SQL on the database from the host, for the test's own inspection.

    Not a shortcut around the network. This is how the *test* reads state; the
    workload under test reaches the database over the internal network, which is
    the thing being proved.
    """
    result = execute(
        [
            "docker",
            "exec",
            "--env",
            f"PGPASSWORD={PASSWORD}",
            environment.container,
            "psql",
            "-U",
            USER,
            "-d",
            DATABASE,
            "-tAc",
            sql,
        ],
        timeout=120.0,
    )
    if result.exit_code != 0:
        message = f"psql failed: {result.stderr}"
        raise AssertionError(message)
    return result.stdout.strip()


@pytest.fixture
def workbench(environment: Environment, repo: Path, tmp_path: Path) -> Workbench:
    return Workbench(
        repository=Repository(root=repo),
        image=IMAGE,
        worktree_root=tmp_path / "sessions",
        limits=ResourceLimits(memory_bytes=512 * 1024 * 1024),
        network=environment.network,
    )


# --------------------------------------------------- the composition itself


def test_a_sandboxed_workload_reaches_its_database(
    workbench: Workbench, environment: Environment
) -> None:
    """The thing Epic 2 exists to do, and could not do before ADR 029.

    A workload in a container with a read-only root, capped memory, one bind
    mount and no route off the host, querying a database in another container.
    """
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        result = session.run(
            [
                "psql",
                environment.from_container.dsn,
                "-tAc",
                "SELECT 'ROWS=' || count(*) FROM ticket",
            ],
            timeout=180.0,
        )

    assert result.exit_code == 0, result.stderr
    assert "ROWS=3" in result.stdout


def test_the_same_container_still_cannot_reach_the_internet(workbench: Workbench) -> None:
    """AC 3 survived the widening, tested on the network that made it necessary.

    This is the test that would catch the mistake if `InternalNetwork` ever
    accepted an arbitrary name: a bridged network reaches both the database and
    the internet, and only this half distinguishes them.
    """
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        result = session.run(
            ["sh", "-c", "timeout 5 nc -z 1.1.1.1 53 && echo REACHED || echo BLOCKED"],
            timeout=180.0,
        )

    assert "BLOCKED" in result.stdout
    assert "REACHED" not in result.stdout


def test_the_workload_sees_the_worktree_and_the_repository_is_untouched(
    workbench: Workbench, repo: Path
) -> None:
    """S-2.2 and S-2.3, from inside the container this time."""
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        (session.worktree.path / "subject.py").write_text("VERSION = 99\n")
        result = session.run(["cat", "subject.py"], timeout=180.0)
        ablated = session.worktree.path

    assert "VERSION = 99" in result.stdout
    assert (repo / "subject.py").read_text() == "VERSION = 2\n"
    assert not ablated.exists()


def test_ten_cycles_of_sandboxed_workload_and_reset_return_the_starting_state(
    workbench: Workbench, environment: Environment
) -> None:
    """The whole epic in one loop, driven by the S-2.7 harness.

    Each cycle: open a session, run a workload in a fresh container that writes
    to a database in another container over a network with no route off the
    host, then reset. The harness checks row counts, content hashes, max ids and
    sequence positions, the workload's own observation, and the process
    identity.

    `SNAPSHOT_RESTORE` rather than rollback, and this run is the demonstration
    of why: a containerised workload commits on its own connection, which is
    exactly the precondition ADR 025 said rollback could not satisfy and that
    nothing short of composing the epic could show.
    """
    containers: list[str] = []

    def workload() -> object:
        with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
            result = session.run(
                [
                    "psql",
                    environment.from_container.dsn,
                    "-tAc",
                    "INSERT INTO ticket (title) VALUES ('workload') RETURNING id",
                ],
                timeout=180.0,
            )
        assert result.exit_code == 0, result.stderr
        containers.append(result.command[result.command.index("--name") + 1])
        return result.stdout.strip()

    mechanism = SnapshotRestoreReset(database=environment.from_host)
    before = psql(environment, "SELECT count(*) FROM ticket")

    report = verify(
        mechanism,
        environment.from_host,
        workload,
        cycles=10,
        process_identity=lambda: containers[-1],
    )
    mechanism.discard_snapshot()

    assert report.reliable, report.diagnostic()
    assert psql(environment, "SELECT count(*) FROM ticket") == before

    # Every cycle ran in a container of its own, so no cache could have survived
    # from one to the next. That is the half of the reset contract no database
    # strategy provides, and here it is observed rather than assumed.
    assert len(set(containers)) == 10


def test_the_workload_reaches_the_database_only_through_the_internal_network(
    workbench: Workbench, environment: Environment
) -> None:
    """The control, and the reason the two DSNs are kept apart.

    The host-side URL names `localhost`, which inside a container means the
    container itself. If a test accidentally handed the workload that URL and
    it still passed, the workload would be reaching something other than the
    database under test — so this asserts it fails.
    """
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        result = session.run(
            [
                "sh",
                "-c",
                f"psql '{environment.from_host.dsn}' -tAc 'SELECT 1' "
                "&& echo REACHED || echo UNREACHABLE",
            ],
            timeout=180.0,
        )

    assert "UNREACHABLE" in result.stdout

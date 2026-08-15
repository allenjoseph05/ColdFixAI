"""The flags do what docker documents. Needs a daemon; skipped without one.

`test_runner.py` proves the isolation policy is always constructed. It cannot
prove the policy works, because that is a claim about docker rather than about
this code — `--network none` is only "no egress" if a container under it really
cannot open a socket, and `--memory` is only a limit if exceeding it really ends
the run. Each test here attempts the thing the criterion forbids and asserts it
fails.

These are marked `docker` rather than `slow` on purpose. A slow test is one you
choose not to wait for; these are ones a machine without a daemon cannot run at
all, and folding that into `slow` would let an absent daemon read as a passing
fast subset.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from coldfix.bench.execute import ExecutionTimeoutError, execute
from coldfix.sandbox import (
    WORKSPACE_MOUNTPOINT,
    InternalNetwork,
    MemoryLimitExceededError,
    NotAnInternalNetworkError,
    ResourceLimits,
    Sandbox,
    SandboxStartError,
    docker_available,
)
from coldfix.sandbox.runner import docker_run_argv

pytestmark = [pytest.mark.docker, pytest.mark.slow]

IMAGE = "python:3.12-slim"

# Long enough to cover a cold container start on a loaded machine, short enough
# that a hung daemon fails the suite instead of stalling it.
TIMEOUT = 120.0


@pytest.fixture(scope="module", autouse=True)
def _requires_image() -> None:
    """Skip unless a daemon is listening and the image is already local.

    The image is not pulled here. `--pull never` is part of the policy under
    test, so a fixture that quietly fetched the image would be arranging for the
    one criterion it cannot verify.
    """
    if not docker_available():
        pytest.skip("no Docker daemon is listening")
    present = execute(["docker", "image", "inspect", IMAGE], timeout=TIMEOUT)
    if present.exit_code != 0:
        pytest.skip(f"image {IMAGE} is not present locally; run `docker pull {IMAGE}`")


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(image=IMAGE, workspace=tmp_path)


def coldfix_containers() -> set[str]:
    """Every container this project has ever named, running or exited.

    Deliberately not narrowed to the runner's own `coldfix-<uuid>` shape. The
    tests below assert that a run leaves this set *unchanged*, which is a
    stronger claim than "no container of the expected shape survived": it also
    fails if a leaked container is named something the filter did not predict.
    The E0 spikes left several exited `coldfix-*` containers on the development
    machine, and comparing against a baseline rather than against empty is what
    keeps those from being read as this run's leak — or from hiding one.
    """
    result = execute(
        ["docker", "ps", "--all", "--filter", "name=coldfix-", "--format", "{{.Names}}"],
        timeout=TIMEOUT,
    )
    return set(result.stdout.split())


# ------------------------------------------------------------------ it runs


def test_a_workload_runs_and_reports_its_output(sandbox: Sandbox) -> None:
    result = sandbox.run(["python", "-c", "print('measured')"], timeout=TIMEOUT)

    assert result.stdout.strip() == "measured"
    assert result.exit_code == 0


def test_a_non_zero_exit_is_a_result_not_an_exception(sandbox: Sandbox) -> None:
    assert sandbox.run(["python", "-c", "raise SystemExit(3)"], timeout=TIMEOUT).exit_code == 3


def test_the_workload_starts_in_the_workspace(sandbox: Sandbox) -> None:
    result = sandbox.run(["python", "-c", "import os; print(os.getcwd())"], timeout=TIMEOUT)

    assert result.stdout.strip() == WORKSPACE_MOUNTPOINT


# ---------------------------------------------------------------- AC 3, egress


def test_the_container_cannot_reach_the_network(sandbox: Sandbox) -> None:
    """Attempts a connection off the host and asserts it cannot be made.

    A literal address rather than a hostname, so this is a statement about
    routing and not merely about DNS being absent.
    """
    result = sandbox.run(
        [
            "python",
            "-c",
            "import socket;"
            "s=socket.socket();s.settimeout(5);"
            "\ntry:\n s.connect(('1.1.1.1',53));print('REACHED')\n"
            "except OSError as e:\n print('BLOCKED')",
        ],
        timeout=TIMEOUT,
    )

    assert "REACHED" not in result.stdout
    assert "BLOCKED" in result.stdout


def test_dns_resolution_fails_too(sandbox: Sandbox) -> None:
    result = sandbox.run(
        [
            "python",
            "-c",
            "import socket\ntry:\n socket.gethostbyname('example.com');print('RESOLVED')\n"
            "except OSError:\n print('NO DNS')",
        ],
        timeout=TIMEOUT,
    )

    assert "RESOLVED" not in result.stdout


def test_loopback_still_works(sandbox: Sandbox) -> None:
    """ "Localhost only" is the other half of AC 3, and it has to actually work.

    A workload that cannot bind a port on itself cannot be driven at all, which
    would make the isolation complete and the system useless.
    """
    result = sandbox.run(
        [
            "python",
            "-c",
            "import socket;s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1);"
            "print('BOUND',s.getsockname()[1]>0)",
        ],
        timeout=TIMEOUT,
    )

    assert "BOUND True" in result.stdout


# ------------------------------------------------------------ AC 4, filesystem


def test_writes_outside_the_workspace_are_refused(sandbox: Sandbox) -> None:
    result = sandbox.run(
        [
            "python",
            "-c",
            "\ntry:\n open('/escaped','w').write('x');print('WROTE')\n"
            "except OSError as e:\n print('REFUSED',e.errno)",
        ],
        timeout=TIMEOUT,
    )

    assert "WROTE" not in result.stdout
    assert "REFUSED" in result.stdout


def test_the_image_cannot_be_modified(sandbox: Sandbox) -> None:
    """State left in the image layer would be inherited by the next run."""
    result = sandbox.run(
        [
            "python",
            "-c",
            "\ntry:\n open('/usr/lib/planted','w').write('x');print('WROTE')\n"
            "except OSError:\n print('REFUSED')",
        ],
        timeout=TIMEOUT,
    )

    assert "WROTE" not in result.stdout


def test_writes_to_the_workspace_reach_the_host(sandbox: Sandbox) -> None:
    sandbox.run(
        ["python", "-c", f"open('{WORKSPACE_MOUNTPOINT}/result.txt','w').write('42')"],
        timeout=TIMEOUT,
    )

    assert (sandbox.workspace / "result.txt").read_text() == "42"


def test_the_host_workspace_is_visible_inside(sandbox: Sandbox) -> None:
    (sandbox.workspace / "seed.txt").write_text("planted")

    result = sandbox.run(
        ["python", "-c", f"print(open('{WORKSPACE_MOUNTPOINT}/seed.txt').read())"],
        timeout=TIMEOUT,
    )

    assert "planted" in result.stdout


def test_temp_is_writable_so_ordinary_toolchains_still_work(sandbox: Sandbox) -> None:
    """A read-only root with no writable temp fails pip and most build backends."""
    result = sandbox.run(
        [
            "python",
            "-c",
            "import tempfile;f=tempfile.NamedTemporaryFile();f.write(b'x');print('OK')",
        ],
        timeout=TIMEOUT,
    )

    assert "OK" in result.stdout


def test_temp_does_not_survive_the_container(sandbox: Sandbox) -> None:
    sandbox.run(["python", "-c", "open('/tmp/left','w').write('x')"], timeout=TIMEOUT)

    result = sandbox.run(
        [
            "python",
            "-c",
            "import os;print('SURVIVED' if os.path.exists('/tmp/left') else 'GONE')",
        ],
        timeout=TIMEOUT,
    )

    assert "GONE" in result.stdout


# --------------------------------------------------------------- AC 2, limits


def test_exceeding_the_memory_limit_ends_the_run(sandbox: Sandbox) -> None:
    """Allocates past the cap and asserts the run is killed, not merely slowed.

    Written as a growing list of chunks rather than one large allocation so that
    the pages are actually touched — a lazy allocation would be granted and
    never charged against the limit.
    """
    capped = Sandbox(
        image=IMAGE,
        workspace=sandbox.workspace,
        limits=ResourceLimits(memory_bytes=64 * 1024 * 1024),
    )

    with pytest.raises(MemoryLimitExceededError):
        capped.run(
            ["python", "-c", "b=[]\nwhile True:\n b.append(bytearray(8*1024*1024))"],
            timeout=TIMEOUT,
        )


def test_a_workload_within_its_limit_is_untouched(sandbox: Sandbox) -> None:
    """The limit must bind on the run above it and on nothing else."""
    capped = Sandbox(
        image=IMAGE,
        workspace=sandbox.workspace,
        limits=ResourceLimits(memory_bytes=256 * 1024 * 1024),
    )

    result = capped.run(
        ["python", "-c", "b=bytearray(16*1024*1024);b[0]=1;print('HELD',len(b))"],
        timeout=TIMEOUT,
    )

    assert "HELD" in result.stdout


# ------------------------------------------------------- AC 5, destruction


def test_no_container_survives_a_successful_run(sandbox: Sandbox) -> None:
    before = coldfix_containers()

    sandbox.run(["python", "-c", "print('done')"], timeout=TIMEOUT)

    assert coldfix_containers() == before


def test_no_container_survives_a_timeout(sandbox: Sandbox) -> None:
    """The failure this module was written for.

    Killing `docker run` kills a client; the container runs on under the daemon,
    holding the workspace and competing for the CPU that every later measurement
    is taken against. Nothing about the client's death is visible to it.
    """
    before = coldfix_containers()

    with pytest.raises(ExecutionTimeoutError):
        sandbox.run(["python", "-c", "import time;time.sleep(600)"], timeout=10)

    assert coldfix_containers() == before


def test_no_container_survives_an_out_of_memory_kill(sandbox: Sandbox) -> None:
    capped = Sandbox(
        image=IMAGE,
        workspace=sandbox.workspace,
        limits=ResourceLimits(memory_bytes=64 * 1024 * 1024),
    )
    before = coldfix_containers()

    with pytest.raises(MemoryLimitExceededError):
        capped.run(
            ["python", "-c", "b=[]\nwhile True:\n b.append(bytearray(8*1024*1024))"],
            timeout=TIMEOUT,
        )

    assert coldfix_containers() == before


# ---------------------------------------------------------------- reproducibility


def test_an_absent_image_is_refused_rather_than_fetched(sandbox: Sandbox) -> None:
    """`--pull never`. A mid-experiment download is both a stall and a network."""
    absent = Sandbox(image="coldfix-nonexistent:v0", workspace=sandbox.workspace)
    before = coldfix_containers()

    with pytest.raises(SandboxStartError):
        absent.run(["python", "-c", "print(1)"], timeout=TIMEOUT)

    assert coldfix_containers() == before


# ------------------------------------------------- the one widening (ADR 029)


def test_a_bridged_network_is_refused(sandbox: Sandbox) -> None:
    """The check that keeps AC 3 true after the network field was added.

    `bridge` is docker's default and has a route off the host. If
    `InternalNetwork` accepted it, a workload could reach its database and the
    internet, and the two are indistinguishable from inside a container.
    """
    with pytest.raises(NotAnInternalNetworkError, match="not internal"):
        InternalNetwork(name="bridge")


def test_a_network_that_does_not_exist_is_refused() -> None:
    with pytest.raises(NotAnInternalNetworkError):
        InternalNetwork(name=f"coldfix-absent-{uuid.uuid4().hex[:8]}")


def test_an_internal_network_is_accepted_and_reaches_the_argv(tmp_path: Path) -> None:
    """Created, verified, and carried into the invocation as its name."""
    network = InternalNetwork.create(f"coldfix-net-test-{uuid.uuid4().hex[:8]}")
    try:
        attached = Sandbox(image=IMAGE, workspace=tmp_path, network=network)

        argv = docker_run_argv(attached, "c1", ["true"])

        network_values = [argv[i + 1] for i, part in enumerate(argv[:-1]) if part == "--network"]
        assert network_values == [network.name]
    finally:
        network.destroy()


def test_creation_verifies_rather_than_trusting_the_command(tmp_path: Path) -> None:
    """`create` re-inspects, because a name already taken is not an error.

    Attaching to a pre-existing *bridged* network of the same name would
    succeed silently if creation were trusted.
    """
    name = f"coldfix-net-collide-{uuid.uuid4().hex[:8]}"
    execute(["docker", "network", "create", name], timeout=TIMEOUT)
    try:
        with pytest.raises(NotAnInternalNetworkError):
            InternalNetwork.create(name)
    finally:
        execute(["docker", "network", "rm", name], timeout=TIMEOUT)

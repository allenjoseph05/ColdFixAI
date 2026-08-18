"""The isolation policy is always applied, and cannot be argued out of.

These tests never start a container. That is the point: every acceptance
criterion of S-2.1 is a statement about the `docker run` invocation, and an
invocation can be asserted against exhaustively, on any machine, in
milliseconds. `test_runner_docker.py` proves the flags mean what docker
documents; this file proves they are always there.

Three of the tests below are adversarial in the shape `CLAUDE.md` asks for.
Two attempt to widen the isolation — through the workload's own arguments, and
through the environment — and assert they cannot. The third asserts that the
configuration objects have no field by which a caller could ask for a network
or a second mount, so that adding one is a change to this file and fails a test
rather than passing quietly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.bench.execute import ExecutionResult, ExecutionStartError, ExecutionTimeoutError
from coldfix.sandbox import runner
from coldfix.sandbox.runner import (
    WORKSPACE_MOUNTPOINT,
    ContainerNotDestroyedError,
    DockerUnavailableError,
    MemoryLimitExceededError,
    ResourceLimits,
    Sandbox,
    SandboxError,
    SandboxStartError,
    WorkspaceError,
    docker_run_argv,
)

IMAGE = "python:3.12-slim"


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(image=IMAGE, workspace=tmp_path)


def policy_argv(argv: Sequence[str]) -> list[str]:
    """The part docker itself parses — everything before the `--` separator.

    Every assertion about the policy is made against this rather than against
    the whole vector. The distinction is the substance of one of the tests
    below: a workload argument that reads `--network host` appears in `argv`,
    and a helper that could not tell the two regions apart would report the
    isolation as widened when it was not, or as intact when it had been.
    """
    return list(argv[: argv.index("--")])


def flag_values(argv: Sequence[str], flag: str) -> list[str]:
    """Every value the *policy* gives to `flag`, so a test can count them."""
    policy = policy_argv(argv)
    return [policy[i + 1] for i, part in enumerate(policy[:-1]) if part == flag]


def workload_argv(argv: Sequence[str]) -> list[str]:
    """Everything docker will hand to the container, image included."""
    return list(argv[argv.index("--") + 1 :])


# --------------------------------------------------------------- the policy


def test_the_command_runs_in_a_container(sandbox: Sandbox) -> None:
    argv = docker_run_argv(sandbox, "c1", ["pytest", "-q"])

    assert argv[:2] == ["docker", "run"]
    assert workload_argv(argv) == [IMAGE, "pytest", "-q"]


def test_there_is_no_route_off_the_host(sandbox: Sandbox) -> None:
    """AC 3. `none` leaves loopback and nothing else — no bridge, no DNS."""
    assert flag_values(docker_run_argv(sandbox, "c1", ["true"]), "--network") == ["none"]


def test_cpu_memory_and_process_limits_are_all_set(sandbox: Sandbox) -> None:
    limits = ResourceLimits(cpus=1.5, memory_bytes=512 * 1024 * 1024, pids=64)
    argv = docker_run_argv(dataclasses.replace(sandbox, limits=limits), "c1", ["true"])

    assert flag_values(argv, "--cpus") == ["1.5"]
    assert flag_values(argv, "--memory") == [str(512 * 1024 * 1024)]
    assert flag_values(argv, "--pids-limit") == ["64"]


def test_swap_cannot_be_used_to_exceed_the_memory_limit(sandbox: Sandbox) -> None:
    """The subtlest half of AC 2.

    Docker's default is swap at twice `--memory`. Left alone, a workload over
    its limit gets slow instead of getting killed — and a slow workload is a
    measurement this system would go on to report as a finding.
    """
    argv = docker_run_argv(sandbox, "c1", ["true"])

    assert flag_values(argv, "--memory-swap") == flag_values(argv, "--memory")


def test_the_root_filesystem_is_read_only(sandbox: Sandbox) -> None:
    assert "--read-only" in docker_run_argv(sandbox, "c1", ["true"])


def test_the_workspace_is_the_only_writable_path_that_outlives_the_run(
    sandbox: Sandbox,
) -> None:
    """AC 4. One bind mount, and /tmp in memory so a read-only root stays usable."""
    argv = docker_run_argv(sandbox, "c1", ["true"])

    mounts = flag_values(argv, "--mount")
    assert mounts == [f"type=bind,source={sandbox.workspace},target={WORKSPACE_MOUNTPOINT}"]

    tmpfs = flag_values(argv, "--tmpfs")
    assert len(tmpfs) == 1
    assert tmpfs[0].startswith("/tmp:")
    assert f"size={sandbox.limits.tmpfs_bytes}" in tmpfs[0]


def test_the_workload_starts_in_the_workspace(sandbox: Sandbox) -> None:
    assert flag_values(docker_run_argv(sandbox, "c1", ["true"]), "--workdir") == [
        WORKSPACE_MOUNTPOINT
    ]


def test_an_absent_image_fails_rather_than_being_fetched(sandbox: Sandbox) -> None:
    """A mid-experiment pull spends the run's timeout on a download.

    It also reintroduces the network on the host side, where `--network none`
    cannot see it, which makes the first measurement of a session both slower
    and less isolated than the ones it will be compared against.
    """
    assert flag_values(docker_run_argv(sandbox, "c1", ["true"]), "--pull") == ["never"]


def test_the_container_is_named_so_it_can_be_found_again(sandbox: Sandbox) -> None:
    assert flag_values(docker_run_argv(sandbox, "run-42", ["true"]), "--name") == ["run-42"]


def test_orphaned_children_are_reaped(sandbox: Sandbox) -> None:
    """Without an init, zombies accumulate against the pids limit.

    The workload then dies of a cause with nothing to do with what was measured.
    """
    assert "--init" in docker_run_argv(sandbox, "c1", ["true"])


def test_environment_variables_reach_the_container(sandbox: Sandbox) -> None:
    argv = docker_run_argv(sandbox, "c1", ["true"], {"DATABASE_URL": "postgres:///t"})

    assert flag_values(argv, "--env") == ["DATABASE_URL=postgres:///t"]


# ---------------------------------------------------------- attacking it


def test_the_workload_cannot_rewrite_the_policy_through_its_own_arguments(
    sandbox: Sandbox,
) -> None:
    """The one way this argv could be turned against itself.

    A workload invoked with something that looks like a docker flag must be
    parsed as an argument to the workload, never as an option to `docker run`.
    The `--` separator is what guarantees it, and this test is the reason it is
    there.
    """
    hostile = ["--network", "host", "--privileged", "-v", "/:/host"]

    argv = docker_run_argv(sandbox, "c1", hostile)

    assert workload_argv(argv) == [IMAGE, *hostile]
    assert flag_values(argv, "--network") == ["none"]
    assert "--privileged" not in policy_argv(argv)
    assert len(flag_values(argv, "--mount")) == 1


def test_the_environment_cannot_smuggle_in_a_second_mount(sandbox: Sandbox) -> None:
    """Every env var becomes one `--env` argument, never a bare flag."""
    argv = docker_run_argv(
        sandbox, "c1", ["true"], {"X": "y --mount type=bind,source=/,target=/host"}
    )

    assert len(flag_values(argv, "--mount")) == 1
    assert flag_values(argv, "--mount") != ["type=bind,source=/,target=/host"]


def test_there_is_no_field_by_which_isolation_could_be_requested_away() -> None:
    """Structural, not behavioural — and deliberately brittle.

    Every criterion in this story is a constant in `docker_run_argv`. The way
    that stops being true is someone adding a `mounts` or `privileged` field to
    `Sandbox` because one caller needed it, at which point the policy becomes a
    default rather than a property. This test fails when that happens, so the
    widening is reviewed rather than merged.

    **`network` is here because this test did its job.** It was added after the
    epic, when the end-to-end test showed that `--network none` makes a Django
    subject unrunnable — its Postgres is a sibling container. This assertion
    failed, which is what forced the widening to be argued for rather than
    slipped in, and the argument is recorded in ADR 029.
    """
    assert {f.name for f in dataclasses.fields(Sandbox)} == {
        "image",
        "workspace",
        "limits",
        "network",
    }
    assert {f.name for f in dataclasses.fields(ResourceLimits)} == {
        "cpus",
        "memory_bytes",
        "pids",
        "tmpfs_bytes",
    }


def test_the_network_field_cannot_hold_an_arbitrary_name() -> None:
    """The widening is by type, and this is the part that keeps AC 3 true.

    A `str` here would let any caller attach a workload to the default bridge
    and restore the egress this story removes — quietly, because a workload
    that can reach a database and one that can reach the internet look
    identical from inside the container. `InternalNetwork` refuses to exist for
    a network docker does not report as internal, so there is no string to
    pass.
    """
    annotation = {f.name: f.type for f in dataclasses.fields(Sandbox)}["network"]

    assert "InternalNetwork" in str(annotation)
    assert "str" not in str(annotation)


# ------------------------------------------------------------- validation


def test_a_workspace_that_does_not_exist_is_rejected(tmp_path: Path) -> None:
    """`docker run -v` would create it and measure a workload against nothing."""
    with pytest.raises(WorkspaceError, match="does not exist"):
        Sandbox(image=IMAGE, workspace=tmp_path / "absent")


def test_a_workspace_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "notadir"
    target.write_text("")

    with pytest.raises(WorkspaceError, match="not a directory"):
        Sandbox(image=IMAGE, workspace=target)


def test_a_workspace_path_that_docker_cannot_quote_is_rejected(tmp_path: Path) -> None:
    """A comma is legal in a directory name and is an option separator to `--mount`.

    Left through, the tail of the path is parsed as further mount options —
    directory characters becoming arguments to docker. There is nothing to
    escape it with, so the path is refused instead.
    """
    awkward = tmp_path / "repo,v2"
    awkward.mkdir()

    with pytest.raises(WorkspaceError, match="comma"):
        Sandbox(image=IMAGE, workspace=awkward)


def test_the_workspace_is_resolved_to_an_absolute_path(tmp_path: Path) -> None:
    """`--mount` rejects a relative source, and a lazily resolved one can move."""
    nested = tmp_path / "repo"
    nested.mkdir()

    sandbox = Sandbox(image=IMAGE, workspace=tmp_path / "repo" / ".")

    assert sandbox.workspace == nested.resolve()
    assert sandbox.workspace.is_absolute()


def test_an_empty_image_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="image"):
        Sandbox(image="  ", workspace=tmp_path)


@pytest.mark.parametrize(
    "limits",
    [
        {"cpus": 0},
        {"memory_bytes": -1},
        {"pids": 0},
        {"tmpfs_bytes": 0},
    ],
)
def test_a_non_positive_limit_is_rejected(limits: Mapping[str, float]) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ResourceLimits(**limits)  # type: ignore[arg-type]


def test_an_empty_command_is_rejected(sandbox: Sandbox) -> None:
    with pytest.raises(ValueError, match="command is empty"):
        sandbox.run([], timeout=30)


# ------------------------------------------------- the run, against a fake daemon


class FakeDocker:
    """Replays docker responses, and records what was asked of it.

    The alternative — a real daemon — cannot be made to produce an OOM kill, a
    failed removal and an absent container on demand, and would make the fast
    subset depend on a machine that may not have Docker installed.
    """

    def __init__(
        self,
        *,
        run: ExecutionResult | Exception,
        inspect: tuple[int, str] | None = (0, "false"),
        rm: ExecutionResult | Exception | None = None,
    ) -> None:
        self._run = run
        self._inspect = inspect
        self._rm = rm
        self.calls: list[list[str]] = []

    def __call__(self, command: Sequence[str], *, timeout: float, **_: object) -> ExecutionResult:
        argv = [str(part) for part in command]
        self.calls.append(argv)
        subcommand = argv[1]
        if subcommand == "run":
            return self._reply(self._run, argv)
        if subcommand == "inspect":
            if self._inspect is None:
                return self._result(argv, exit_code=1, stderr="Error: No such object: c1")
            exit_code, oom = self._inspect
            return self._result(argv, stdout=f"{exit_code} {oom}\n")
        if subcommand == "rm":
            if self._rm is None:
                return self._result(argv)
            return self._reply(self._rm, argv)
        unexpected = f"unexpected docker subcommand: {subcommand}"
        raise AssertionError(unexpected)

    @property
    def subcommands(self) -> list[str]:
        return [call[1] for call in self.calls]

    def _reply(self, canned: ExecutionResult | Exception, argv: list[str]) -> ExecutionResult:
        if isinstance(canned, Exception):
            raise canned
        return dataclasses.replace(canned, command=tuple(argv))

    def _result(
        self, argv: list[str], *, exit_code: int = 0, stdout: str = "", stderr: str = ""
    ) -> ExecutionResult:
        return ExecutionResult(
            command=tuple(argv),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            wall_seconds=0.01,
        )


def ok(stdout: str = "", stderr: str = "", exit_code: int = 0) -> ExecutionResult:
    return ExecutionResult(
        command=("docker", "run"),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=0.5,
    )


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeDocker) -> FakeDocker:
    monkeypatch.setattr(runner, "execute", fake)
    return fake


def test_a_successful_run_reports_the_workloads_output(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeDocker(run=ok(stdout="42 passed")))

    result = sandbox.run(["pytest"], timeout=30)

    assert result.stdout == "42 passed"
    assert result.exit_code == 0


def test_the_exit_code_comes_from_the_daemon_not_the_client(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docker run` exits 125 for its own failures, which a workload may also use.

    Reading the status back from the container means the two are never confused:
    here the client reports 125 and the container actually exited 3.
    """
    install(monkeypatch, FakeDocker(run=ok(exit_code=125), inspect=(3, "false")))

    assert sandbox.run(["pytest"], timeout=30).exit_code == 3


def test_a_container_that_never_existed_is_a_start_error(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        FakeDocker(run=ok(exit_code=125, stderr="Unable to find image locally"), inspect=None),
    )

    with pytest.raises(SandboxStartError, match="Unable to find image locally"):
        sandbox.run(["pytest"], timeout=30)


def test_an_out_of_memory_kill_is_raised_rather_than_returned(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated run is not a measurement.

    Returned as a plain non-zero exit, it would be indistinguishable from a
    workload that failed on its own terms, and its timing would be compared
    against a complete run.
    """
    install(
        monkeypatch,
        FakeDocker(run=ok(stdout="loading", exit_code=137), inspect=(137, "true")),
    )

    with pytest.raises(MemoryLimitExceededError) as raised:
        sandbox.run(["pytest"], timeout=30)

    assert raised.value.memory_bytes == sandbox.limits.memory_bytes
    assert raised.value.partial_stdout == "loading"


def test_a_missing_docker_cli_is_a_typed_error(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    cause = FileNotFoundError("docker")
    install(
        monkeypatch,
        FakeDocker(run=ExecutionStartError(["docker", "run"], None, cause)),
    )

    with pytest.raises(DockerUnavailableError):
        sandbox.run(["pytest"], timeout=30)


# ------------------------------------------------------------- destruction


def test_the_container_is_destroyed_after_a_successful_run(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeDocker(run=ok()))

    sandbox.run(["pytest"], timeout=30)

    assert fake.subcommands == ["run", "inspect", "rm"]


def test_the_container_is_destroyed_after_a_timeout(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure mode this module exists for.

    Killing `docker run` kills a client. The container goes on running, holding
    the workspace and competing for the CPU that every later measurement is
    taken against — and `--rm` does not help, because it fires only when the
    client exits cleanly, which is the case that was never in doubt.
    """
    fake = install(
        monkeypatch,
        FakeDocker(run=ExecutionTimeoutError(["docker", "run"], 30, "partial", "")),
    )

    with pytest.raises(ExecutionTimeoutError):
        sandbox.run(["pytest"], timeout=30)

    assert fake.subcommands == ["run", "rm"]


def test_the_container_is_destroyed_after_an_out_of_memory_kill(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeDocker(run=ok(exit_code=137), inspect=(137, "true")))

    with pytest.raises(MemoryLimitExceededError):
        sandbox.run(["pytest"], timeout=30)

    assert fake.subcommands == ["run", "inspect", "rm"]


def test_destruction_is_forced_and_takes_the_anonymous_volumes_with_it(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeDocker(run=ok()))

    sandbox.run(["pytest"], timeout=30)

    rm = next(call for call in fake.calls if call[1] == "rm")
    assert "--force" in rm
    assert "--volumes" in rm
    assert rm[-1].startswith("coldfix-")


def test_each_run_gets_its_own_container(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reuse would carry one experiment's state into the next one's measurement."""
    fake = install(monkeypatch, FakeDocker(run=ok()))

    sandbox.run(["pytest"], timeout=30)
    sandbox.run(["pytest"], timeout=30)

    names = [call[call.index("--name") + 1] for call in fake.calls if call[1] == "run"]
    assert len(set(names)) == 2


def test_a_container_that_cannot_be_removed_is_never_silent(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        FakeDocker(run=ok(), rm=ok(exit_code=1, stderr="permission denied")),
    )

    with pytest.raises(ContainerNotDestroyedError, match="permission denied"):
        sandbox.run(["pytest"], timeout=30)


def test_removing_a_container_that_never_existed_is_not_a_failure(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is already in the state removal was trying to reach.

    Docker's exit code for this has moved between versions, so the check is on
    what it says rather than on what it returns.
    """
    install(
        monkeypatch,
        FakeDocker(
            run=ok(exit_code=125, stderr="no such image"),
            inspect=None,
            rm=ok(exit_code=1, stderr="Error response from daemon: No such container: c1"),
        ),
    )

    with pytest.raises(SandboxStartError):
        sandbox.run(["pytest"], timeout=30)


def test_a_wedged_daemon_is_reported_as_a_container_that_may_still_be_running(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(
        monkeypatch,
        FakeDocker(run=ok(), rm=ExecutionTimeoutError(["docker", "rm"], 60, "", "")),
    )

    with pytest.raises(ContainerNotDestroyedError):
        sandbox.run(["pytest"], timeout=30)


def test_a_missing_docker_cli_does_not_mask_itself_during_cleanup(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CLI means no container was created, so cleanup has nothing to report.

    Without this, the `finally` would replace `DockerUnavailableError` with a
    worse description of the same broken machine.
    """
    cause = FileNotFoundError("docker")
    install(
        monkeypatch,
        FakeDocker(
            run=ExecutionStartError(["docker", "run"], None, cause),
            rm=ExecutionStartError(["docker", "rm"], None, cause),
        ),
    )

    with pytest.raises(DockerUnavailableError):
        sandbox.run(["pytest"], timeout=30)


def test_an_unreadable_inspect_result_is_not_guessed_at(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A format docker changed under us must not be read as "exited 0, no OOM"."""

    class Garbled(FakeDocker):
        def __call__(
            self, command: Sequence[str], *, timeout: float, **_: object
        ) -> ExecutionResult:
            argv = [str(part) for part in command]
            if argv[1] == "inspect":
                self.calls.append(argv)
                return self._result(argv, stdout="<nil>\n")
            return super().__call__(command, timeout=timeout)

    install(monkeypatch, Garbled(run=ok()))

    with pytest.raises(SandboxError, match="not an exit code"):
        sandbox.run(["pytest"], timeout=30)

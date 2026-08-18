"""Run a command inside a container that cannot reach the network or the host.

Epic 2, S-2.1. The subject repository is code we did not write, which this
system then deliberately breaks in order to measure it. ADR 004 fixed the
mechanism — Docker — and gave the reason: a container is an isolation boundary
against *accidents*, not a security boundary against hostile code. The subject
is assumed to be the user's own repository.

Everything about running a process well was solved in S-1.1 and is not solved
again here: `execute()` owns the timeout, the bounded output capture, and the
process-tree kill. This module's whole contribution is the argument vector — a
policy expressed as `docker run` flags — plus the two things the container
boundary adds that a bare subprocess does not have.

**The first is that killing `docker run` does not stop the container.** The CLI
is a client; the workload runs under the daemon. When `execute()` times out and
kills the client process tree, the container keeps running, keeps holding the
workspace, and keeps consuming the CPU that every later measurement is taken
against. That is the orphan problem `execute()` was careful about, transposed
one level up, and `--rm` does not fix it because `--rm` fires when the client
exits cleanly. Removal is therefore forced, by name, in a `finally`.

**The second is that a container can be killed for exceeding its limits, and the
exit code does not say so.** An out-of-memory kill arrives as SIGKILL and looks
identical to any other SIGKILL. `docker inspect` knows the difference, so the
container is inspected before it is destroyed, and an OOM kill is raised rather
than returned — a truncated run is not a measurement, and a caller reading
`wall_seconds` off one would be reading noise.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from coldfix.bench.execute import (
    DEFAULT_MAX_OUTPUT_CHARS,
    ExecutionResult,
    ExecutionStartError,
    ExecutionTimeoutError,
    execute,
)

# Where the workspace appears inside the container. Fixed rather than
# configurable: a workload artifact that records paths is only comparable across
# runs if the paths are the same, and every caller wanting a different mountpoint
# is really asking for a second bind mount, which is the thing AC 4 forbids.
WORKSPACE_MOUNTPOINT = "/workspace"

_CONTAINER_NAME_PREFIX = "coldfix-"

# Bound on `docker inspect` and `docker rm`, which talk to a local daemon and
# return in milliseconds. Long enough that a busy daemon is not mistaken for a
# broken one; short enough that a wedged daemon does not hang the run.
_HOUSEKEEPING_TIMEOUT_SECONDS = 60.0

_MIB = 1024**2
_GIB = 1024**3


class SandboxError(Exception):
    """The sandbox itself failed, as distinct from the workload inside it."""


class DockerUnavailableError(SandboxError):
    """The `docker` CLI is not on PATH, so no container can be started.

    Separate from `SandboxStartError` because the remedy is different and the
    distinction is not visible in a docker exit code: this is a machine that
    cannot run the system at all, not a run that failed.
    """

    def __init__(self, cause: OSError) -> None:
        self.cause = cause
        super().__init__(f"the docker CLI could not be started: {cause}")


class SandboxStartError(SandboxError):
    """The container never ran, so there is no result of any kind.

    A missing image, a daemon that is not listening, a flag the installed docker
    does not accept. `docker run` reports all of these on stderr and exits 125,
    but 125 is also a legal exit code for the workload, so the distinction is not
    drawn from the exit code — it is drawn from whether the container exists
    afterwards. Docker's own stderr is carried through verbatim rather than
    classified, because docker states the cause better than a guess at its
    wording would.
    """

    def __init__(self, image: str, stderr: str) -> None:
        self.image = image
        self.stderr = stderr
        detail = stderr.strip() or "docker reported nothing on stderr"
        super().__init__(f"no container was created from image {image!r}: {detail}")


class MemoryLimitExceededError(SandboxError):
    """The container was killed for exceeding its memory limit.

    Raised rather than returned. An OOM kill stops the workload part-way through
    whatever it was doing, so its timing, its query counts and its output are all
    measurements of a partial run — and none of them look partial. Returning this
    as an ordinary non-zero exit would let a truncated run be compared against a
    complete one, which is the exact failure the guard-counter rule exists to
    prevent elsewhere.

    Carries the output captured before the kill, for the same reason
    `ExecutionTimeoutError` does: it is usually the only evidence of what the
    workload was doing when it ran out of room.
    """

    def __init__(
        self,
        command: Sequence[str],
        memory_bytes: int,
        partial_stdout: str,
        partial_stderr: str,
    ) -> None:
        self.command = tuple(command)
        self.memory_bytes = memory_bytes
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        super().__init__(
            f"container exceeded its {memory_bytes} byte memory limit and was killed: "
            f"{' '.join(self.command)}"
        )


class ContainerNotDestroyedError(SandboxError):
    """A container outlived its run and could not be removed.

    The loudest failure in this module. AC 5 is that the container is destroyed
    after each run, and a container still running holds the workspace and
    competes for the CPU that every subsequent measurement is taken against. It
    is never swallowed, including when it happens while another exception is
    already propagating — there the two are chained and both are reported.
    """

    def __init__(self, container: str, stderr: str) -> None:
        self.container = container
        self.stderr = stderr
        detail = stderr.strip() or "docker reported nothing on stderr"
        super().__init__(f"container {container} could not be removed: {detail}")


class WorkspaceError(SandboxError):
    """The directory to be mounted is missing, or is not a directory.

    Checked before the run rather than left to docker, because `docker run -v`
    creates a missing source directory instead of failing, and a workload
    measured against an empty directory produces numbers that look real. The
    `--mount` form used here fails instead, but the error it gives names a host
    path the caller never typed, so the check stays.
    """


@dataclass(frozen=True)
class ResourceLimits:
    """What a single container is allowed to consume.

    Defaults are finite, not generous, and they are constants rather than
    something derived from the host: a limit that varies with the machine makes
    two runs of the same experiment incomparable, and this system's whole output
    is comparisons.

    `cpus` is a quota, not a pinning. Quotas are what AC 2 asks for and what
    bounds damage; they also introduce scheduler variance that pinning would
    avoid, which matters for timing reproducibility and is a question for
    whoever certifies a noise floor against a real workload, not for this story.
    """

    cpus: float = 2.0
    memory_bytes: int = 2 * _GIB
    pids: int = 512
    tmpfs_bytes: int = 256 * _MIB

    def __post_init__(self) -> None:
        for name in ("cpus", "memory_bytes", "pids", "tmpfs_bytes"):
            value = getattr(self, name)
            if value <= 0:
                message = f"{name} must be positive, got {value}"
                raise ValueError(message)


DEFAULT_LIMITS = ResourceLimits()


class NotAnInternalNetworkError(SandboxError):
    """The named docker network has a route off the host, so it is refused.

    The distinction is `Internal` in `docker network inspect`. A network without
    it is bridged to the host's, and attaching a sandbox to one would restore
    exactly the egress AC 3 removes — quietly, because a workload that can reach
    a database and a workload that can reach the internet look identical from
    inside.
    """

    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        super().__init__(
            f"the docker network {name!r} is not internal, so a container on it can reach "
            f"the network beyond this host: {detail}"
        )


@dataclass(frozen=True)
class InternalNetwork:
    """A docker network proven to have no route off the host.

    Exists because a subject needs its database and must still not reach the
    internet. `--network none` gives loopback and nothing else, which is
    airtight and also makes a Django application impossible to run: its Postgres
    lives in a sibling container. A network created `--internal` resolves both —
    containers on it reach each other by name through docker's embedded DNS, and
    nothing on it reaches anything else. Measured rather than assumed: a
    container on one of these fails to open a socket to `1.1.1.1` and succeeds
    in querying a sibling database in the same breath.

    **Constructing one is the check**, the same construction as
    `VerifiedDatabase`. `Sandbox` accepts an `InternalNetwork` and not a name, so
    there is no string a caller could pass that attaches a workload to the
    default bridge. AC 3 is therefore still enforced by type rather than
    weakened by a parameter — what widened is "localhost only" to "this internal
    network only", and nothing about egress.
    """

    name: str

    def __post_init__(self) -> None:
        result = execute(
            ["docker", "network", "inspect", "--format", "{{.Internal}}", self.name],
            timeout=_HOUSEKEEPING_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise NotAnInternalNetworkError(self.name, result.stderr.strip() or "no such network")
        if result.stdout.strip().lower() != "true":
            raise NotAnInternalNetworkError(self.name, "Internal is false")

    @classmethod
    def create(cls, name: str) -> InternalNetwork:
        """Create the network, then verify it — never trusting the creation.

        The verification is not ceremony over a command that just ran. A daemon
        configured with a different default driver, or a name already taken by
        a bridged network, both produce a successful-looking `create` and a
        network with egress.
        """
        execute(
            ["docker", "network", "create", "--internal", name],
            timeout=_HOUSEKEEPING_TIMEOUT_SECONDS,
        )
        return cls(name=name)

    def destroy(self) -> None:
        execute(["docker", "network", "rm", self.name], timeout=_HOUSEKEEPING_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class Sandbox:
    """A container configuration. Constructing one is the only way to run.

    There is deliberately no module-level `run_in_sandbox(...)` convenience
    function taking an image and a path. AC 1 is that *every* workload and
    experiment executes inside a container, and the way to make that hold is for
    the object that carries the isolation policy to be the object you call —
    not an argument you may forget to pass.

    The policy is not parameterised. There is no argument that adds a second
    bind mount and none that lifts the read-only root, because each is an
    acceptance criterion rather than a preference.

    `network` is the one thing that was widened, and it was widened by type
    rather than by string. It defaults to `None`, meaning `--network none` —
    loopback and nothing else. Supplying an `InternalNetwork` lets the workload
    reach sibling containers, which is what makes a Django subject runnable at
    all, and cannot let it reach anything beyond the host, because
    `InternalNetwork` refuses to exist for a network that is not internal.
    """

    image: str
    workspace: Path
    limits: ResourceLimits = DEFAULT_LIMITS
    network: InternalNetwork | None = None

    def __post_init__(self) -> None:
        if not self.image.strip():
            message = "image must be a non-empty image reference"
            raise ValueError(message)

        # Resolved once, here, so that the mount source is absolute and the same
        # string every run. `--mount` rejects a relative source outright, and a
        # path resolved lazily at each call could follow a symlink that changed
        # underneath the investigation.
        resolved = self.workspace.resolve()
        if not resolved.exists():
            message = f"workspace does not exist: {resolved}"
            raise WorkspaceError(message)
        if not resolved.is_dir():
            message = f"workspace is not a directory: {resolved}"
            raise WorkspaceError(message)

        # `--mount` takes comma-separated `key=value` options, and docker gives
        # no way to quote a value containing one. A workspace path with a comma
        # in it would have its tail parsed as further mount options — legal
        # characters in a directory name turning into arguments to docker. The
        # path is rejected rather than escaped, because there is nothing to
        # escape it with and a silently mis-parsed mount is the failure this
        # whole module exists to prevent.
        if "," in str(resolved):
            message = f"workspace path contains a comma, which --mount cannot quote: {resolved}"
            raise WorkspaceError(message)

        object.__setattr__(self, "workspace", resolved)

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        """Run `command` inside a fresh container and report what it did.

        `command` runs with the workspace as its working directory. `timeout`
        bounds the whole call, container startup included, and is required for
        the reason S-1.1 gives: the caller knows how long the work should take
        and this function never does.

        `env` **adds to** the image's environment rather than replacing it —
        the opposite of `execute()`, and the asymmetry is worth stating because
        the two functions otherwise look alike. Replacing is impossible here:
        the image's own `PATH` and `LANG` are what make its interpreter
        runnable, and docker offers no way to clear them. Note also that these
        variables reach the container, while the environment `execute()` sees
        is the harness's own, which is what the docker *client* needs in order
        to find the daemon.

        The container is destroyed before this returns, on every path including
        timeout and failure.

        Returns:
            The workload's own result. `exit_code` is read back from the daemon
            rather than taken from the `docker run` client, so it is the
            container's exit status and not a report about the client.

        Raises:
            DockerUnavailableError: the docker CLI could not be started.
            SandboxStartError: no container was created — bad image, bad flag,
                or no daemon listening.
            MemoryLimitExceededError: the container was killed by the memory cap.
            ContainerNotDestroyedError: the container outlived the run.
            ExecutionTimeoutError: the run outlived `timeout`. The container is
                destroyed first, so the partial output on the error describes a
                workload that is no longer running.
            ValueError: `command` is empty, or `timeout` is not positive.
        """
        argv = [str(part) for part in command]
        if not argv:
            message = "command is empty; there is nothing to run"
            raise ValueError(message)

        container = f"{_CONTAINER_NAME_PREFIX}{uuid.uuid4()}"
        try:
            return self._run_in(container, argv, timeout, env, max_output_chars)
        finally:
            _destroy(container)

    def _run_in(
        self,
        container: str,
        argv: list[str],
        timeout: float,
        env: Mapping[str, str] | None,
        max_output_chars: int,
    ) -> ExecutionResult:
        try:
            result = execute(
                docker_run_argv(self, container, argv, env),
                timeout=timeout,
                max_output_chars=max_output_chars,
            )
        except ExecutionStartError as error:
            raise DockerUnavailableError(error.cause) from error
        except ExecutionTimeoutError:
            # Deliberately not swallowed and not enriched. The `finally` in
            # `run` destroys the container before this leaves the module, which
            # is what makes the partial output on the error safe to read: by the
            # time a caller sees it, nothing is still writing.
            raise

        state = _inspect(container)
        if state is None:
            raise SandboxStartError(self.image, result.stderr)
        exit_code, oom_killed = state
        if oom_killed:
            raise MemoryLimitExceededError(
                argv, self.limits.memory_bytes, result.stdout, result.stderr
            )

        # The command on the result is the docker invocation, which is the honest
        # record of what ran — the workload was never a process on this host.
        return replace(result, exit_code=exit_code)


def docker_run_argv(
    sandbox: Sandbox,
    container: str,
    command: Sequence[str],
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Build the `docker run` invocation. Pure, and the real subject of the tests.

    Split out and made public because it is where every acceptance criterion in
    this story actually lives, and it can be asserted against without a daemon,
    a network, or a machine willing to be filled with memory. The integration
    tests prove the flags do what docker documents; these tests prove the flags
    are always there.
    """
    limits = sandbox.limits
    argv = [
        "docker",
        "run",
        # Removal is forced by name after the run instead. `--rm` fires when the
        # client exits cleanly, which is exactly the case that was never in
        # doubt, and the daemon must still be asked about the exit status and
        # the OOM flag before the container is allowed to disappear.
        "--name",
        container,
        # AC 3. Without a network this is `none`: a loopback interface and
        # nothing else — no bridge, no DNS, no route off the host. With one it
        # is a docker network that `InternalNetwork` has proved carries no
        # route off the host either, so the workload reaches its database and
        # still reaches nothing beyond. The value is never a caller's string;
        # it comes off a type whose constructor checked it.
        "--network",
        "none" if sandbox.network is None else sandbox.network.name,
        # AC 2. `--memory-swap` is set equal to `--memory` because otherwise
        # docker grants swap at twice the limit and the memory cap does not bite
        # — the workload gets slow instead of getting killed, and a slow
        # workload is a measurement this system would report.
        "--memory",
        str(limits.memory_bytes),
        "--memory-swap",
        str(limits.memory_bytes),
        "--cpus",
        str(limits.cpus),
        # Not named in the AC, and the cheapest of the limits to justify: a
        # runaway fork is the one resource exhaustion that takes the host down
        # with it regardless of how little memory or CPU each child uses.
        "--pids-limit",
        str(limits.pids),
        # AC 4, first half. Everything the image ships is immutable, so a
        # workload cannot leave state behind in the image layer that the next
        # run would inherit — which would break reproducibility long before it
        # broke anything else.
        "--read-only",
        # AC 4, second half. A read-only root with no writable temp directory
        # fails almost every real toolchain, so /tmp is granted explicitly, in
        # memory, sized, and destroyed with the container. `noexec` is left off:
        # pip and several build backends execute from the temp directory, and
        # ADR 004's threat model is accidents rather than hostile code.
        "--tmpfs",
        f"/tmp:rw,nosuid,size={limits.tmpfs_bytes}",
        # The only bind mount, and the only writable path that outlives the
        # container. There is no parameter by which a second one is added.
        "--mount",
        f"type=bind,source={sandbox.workspace},target={WORKSPACE_MOUNTPOINT}",
        "--workdir",
        WORKSPACE_MOUNTPOINT,
        # Never pull mid-experiment. An image fetched on first use turns one
        # run's timeout into a download and makes the first measurement of a
        # session incomparable with the rest; worse, it silently reintroduces
        # the network this story exists to remove, on the host side where
        # `--network none` cannot see it.
        "--pull",
        "never",
        # PID 1 in a container does not reap orphans, so a workload that spawns
        # children accumulates zombies against the pids limit until it dies of
        # a cause that has nothing to do with what was being measured.
        "--init",
        # Not required by the AC. Both are free, neither constrains a
        # measurement, and they narrow the accident radius that ADR 004 names as
        # the actual threat model.
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]

    for name, value in sorted((env or {}).items()):
        argv += ["--env", f"{name}={value}"]

    # `--` stops docker parsing the workload's own arguments as its flags. A
    # workload invoked with `-v` or `--network` would otherwise silently rewrite
    # the isolation policy above it, which is the one way this argv could be
    # turned against itself.
    argv += ["--", sandbox.image, *(str(part) for part in command)]
    return argv


def _inspect(container: str) -> tuple[int, bool] | None:
    """The container's exit code and whether the memory cap killed it.

    `None` means no such container, which is how a `docker run` that never
    started one is told apart from a workload that genuinely exited 125.
    """
    result = execute(
        [
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            "{{.State.ExitCode}} {{.State.OOMKilled}}",
            container,
        ],
        timeout=_HOUSEKEEPING_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        return None

    fields = result.stdout.split()
    expected_fields = 2
    if len(fields) != expected_fields:
        message = f"docker inspect returned {result.stdout!r}, which is not an exit code and a flag"
        raise SandboxError(message)
    try:
        exit_code = int(fields[0])
    except ValueError as error:
        message = f"docker inspect reported a non-numeric exit code: {fields[0]!r}"
        raise SandboxError(message) from error
    return exit_code, fields[1] == "true"


def _destroy(container: str) -> None:
    """Remove the container, and refuse to be quiet if it cannot be removed."""
    try:
        result = execute(
            ["docker", "rm", "--force", "--volumes", container],
            timeout=_HOUSEKEEPING_TIMEOUT_SECONDS,
        )
    except ExecutionStartError:
        # No docker CLI means no container was ever created. Raising here would
        # replace `DockerUnavailableError` — already propagating from the run —
        # with a worse description of the same machine.
        return
    except ExecutionTimeoutError as error:
        # A daemon that will not answer is the case where a container most
        # likely *is* still running, so this is the loud path, not the quiet one.
        raise ContainerNotDestroyedError(container, error.partial_stderr) from error

    if result.exit_code == 0:
        return
    # A container that was never created is already in the desired state.
    # Docker's exit code for this has moved between versions, so the check is on
    # what it says rather than on what it returns.
    if "no such container" in result.stderr.lower():
        return
    raise ContainerNotDestroyedError(container, result.stderr)


def docker_available() -> bool:
    """Whether a daemon is listening. For skipping tests, never for control flow.

    A sandbox that cannot start is an error, not a condition to route around. If
    this were consulted before a run, the fallback would be running the workload
    somewhere else, and there is nowhere else.
    """
    try:
        result = execute(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            timeout=_HOUSEKEEPING_TIMEOUT_SECONDS,
        )
    except (ExecutionStartError, ExecutionTimeoutError):
        return False
    return result.exit_code == 0

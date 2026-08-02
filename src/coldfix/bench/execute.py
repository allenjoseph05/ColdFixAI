"""Run a command and report what happened. Decides nothing.

The first operation of the lab bench, and the one every other operation is
built on. Its whole job is to hand back four facts — output, error output,
exit code, elapsed time — without interpreting any of them.

Three of the four acceptance criteria are about failure rather than success,
which is the right emphasis: a measurement harness that hangs, deadlocks, or
leaks orphaned processes corrupts every number taken after it, and does so
silently.

**The target runtime is a Linux container** (ADR 004). Windows is supported
because development happens there, and the two platforms need genuinely
different code to kill a process tree — the POSIX path uses process groups and
signals, the Windows path shells out to `taskkill /T`. Both are exercised by
the same tests.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_POSIX = os.name == "posix"

# How long a process group gets to exit after being asked politely, before it
# is killed outright. Short on purpose: anything being killed for exceeding a
# timeout is, by definition, already not responding on schedule.
_TERM_GRACE_SECONDS = 0.5


class ExecutionError(Exception):
    """A command did not produce a result.

    Distinct from a command that ran and failed. A non-zero exit code is a
    *result* — it is reported in `ExecutionResult.exit_code` and is not raised,
    because "the tests failed" is an observation the caller may well be
    expecting. Only the absence of a usable result is an exception.
    """


class ExecutionTimeoutError(ExecutionError):
    """The command exceeded its timeout and its process group was killed.

    Carries whatever output was captured before the kill. That partial output
    is usually the only evidence of where the command got stuck, so discarding
    it would throw away the most useful thing about the failure.
    """

    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        partial_stdout: str,
        partial_stderr: str,
    ) -> None:
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        super().__init__(
            f"command exceeded {timeout_seconds}s and was killed: {' '.join(self.command)}"
        )


@dataclass(frozen=True)
class ExecutionResult:
    """What a command did. Four facts, no judgement."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float

    @property
    def ok(self) -> bool:
        """Convenience only. The caller decides whether a non-zero exit matters."""
        return self.exit_code == 0


def _process_group_kwargs() -> dict[str, Any]:
    """Platform arguments that put the child in its own killable group.

    Without this the child's own children survive a kill, and a measurement
    harness slowly fills the machine with orphans that go on consuming CPU
    while later measurements are taken. That is not a tidiness concern — it
    corrupts every subsequent number.
    """
    if _POSIX:
        # setsid: the child becomes a session and process-group leader, so its
        # descendants share a process-group id we can signal as a unit.
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def _kill_process_group(popen: subprocess.Popen[str]) -> None:
    """Terminate the child and everything it started.

    Escalates rather than going straight to a hard kill, so a child that can
    flush its output gets the chance to. Anything still alive after the grace
    period is killed outright.
    """
    if _POSIX:
        try:
            pgid = os.getpgid(popen.pid)  # type: ignore[attr-defined]
        except ProcessLookupError:
            return
        # `SIGKILL` and `killpg` exist only on POSIX, and mypy analyses for
        # the platform it runs on — Windows during development. The runtime
        # guard above is the real check; the ignores stop a Windows type-check
        # from failing on code that never executes there.
        for sig in (signal.SIGTERM, signal.SIGKILL):  # type: ignore[attr-defined]
            try:
                os.killpg(pgid, sig)  # type: ignore[attr-defined]
            except ProcessLookupError:
                return
            try:
                popen.wait(timeout=_TERM_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            else:
                return
        return

    # Windows has no process groups in the POSIX sense. `taskkill /T` walks the
    # child tree by parent-pid, which is why the child was created in its own
    # group above — without that, /T can reach beyond the subtree we started.
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(popen.pid)],
        capture_output=True,
        check=False,
    )
    try:
        popen.wait(timeout=_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        popen.kill()


def execute(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ExecutionResult:
    """Run `command` to completion and report the result.

    `timeout` is **required and keyword-only**. A subprocess with no deadline
    can hang an entire investigation with no diagnostic, and the caller is
    always in a better position than this function to know how long the work
    should take. Making it required forces that decision to be made once,
    visibly, rather than defaulted to infinity.

    `env` **replaces** the environment rather than extending it, matching
    `subprocess` semantics. A merge would be friendlier and less reproducible:
    a variable leaking in from the parent shell is exactly the kind of thing
    that makes two runs of the same measurement differ. Callers wanting to add
    one variable pass `{**os.environ, "X": "y"}` and say so.

    Raises:
        ExecutionTimeoutError: the command outlived `timeout`. A non-zero exit code
            does **not** raise — it is returned in the result.
    """
    argv = [str(part) for part in command]
    started = time.perf_counter()

    popen = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        # A stray non-UTF-8 byte in a subprocess's stderr should not take down
        # a measurement run. Replacement characters are visible in the output;
        # a raised UnicodeDecodeError would lose the whole result.
        errors="replace",
        **_process_group_kwargs(),
    )

    try:
        # communicate() drains both pipes concurrently. Reading them in
        # sequence — or calling wait() before reading — deadlocks as soon as
        # the child writes more than a pipe buffer (~64 KB) to the stream that
        # is not being read.
        stdout, stderr = popen.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(popen)
        # Drain again, without a timeout, to collect whatever was buffered
        # before the kill and to close the pipes rather than leak them.
        stdout, stderr = popen.communicate()
        raise ExecutionTimeoutError(argv, timeout, stdout or "", stderr or "") from None

    elapsed = time.perf_counter() - started
    return ExecutionResult(
        command=tuple(argv),
        exit_code=popen.returncode,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=elapsed,
    )

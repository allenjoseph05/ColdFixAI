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

import codecs
import contextlib
import io
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_POSIX = os.name == "posix"

# How long a process group gets to exit after being asked politely, before it
# is killed outright. Short on purpose: anything being killed for exceeding a
# timeout is, by definition, already not responding on schedule.
_TERM_GRACE_SECONDS = 0.5

# Characters kept per stream before output starts being elided. A timeout
# bounds how long a command may run; this bounds how much it may say, which is
# the other way a workload from an unfamiliar repository takes the harness down
# with it. Eight million characters is far more than a test suite emits and far
# less than a debug loop does.
DEFAULT_MAX_OUTPUT_CHARS = 8 * 1024 * 1024

_READ_CHUNK_BYTES = 64 * 1024


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


class ExecutionStartError(ExecutionError):
    """The command never started, so there is no result of any kind.

    A missing binary, a working directory that does not exist, an empty
    command. Distinct from both a non-zero exit and a timeout: those describe
    something that ran. This describes the environment being wrong, which for
    an agent driving an unfamiliar repository is the common case — the wrong
    interpreter, a virtualenv that was never created, a path that only exists
    on the machine the workload was written on.

    Raised in place of the `OSError` the operating system supplies, because
    that arrives as `FileNotFoundError`, `NotADirectoryError` or a bare
    `OSError` depending on which argument was wrong and on which platform, and
    a caller cannot reasonably catch all three.
    """

    def __init__(self, command: Sequence[str], cwd: Path | None, cause: OSError) -> None:
        self.command = tuple(command)
        self.cwd = cwd
        self.cause = cause
        location = f" in {cwd}" if cwd is not None else ""
        super().__init__(
            f"could not start {' '.join(self.command) or '<empty command>'}{location}: {cause}"
        )


@dataclass(frozen=True)
class ExecutionResult:
    """What a command did. Four facts, no judgement.

    The two `dropped_chars` counts are the fifth and sixth facts, and they exist
    so that elision can never be silent. A truncated stream is still a usable
    observation; a truncated stream that looks complete is not.
    """

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float
    stdout_dropped_chars: int = 0
    stderr_dropped_chars: int = 0

    @property
    def ok(self) -> bool:
        """Convenience only. The caller decides whether a non-zero exit matters."""
        return self.exit_code == 0

    @property
    def truncated(self) -> bool:
        """Whether either stream exceeded `max_output_chars` and lost its middle."""
        return bool(self.stdout_dropped_chars or self.stderr_dropped_chars)


class _BoundedCapture:
    """Read one pipe to EOF on its own thread, keeping at most `limit` chars.

    Reading has to happen concurrently with the process running — that is what
    stops a child which fills a pipe buffer from blocking forever on a write
    nobody is draining. `subprocess.communicate` does exactly this and is what
    this replaces; the reason for replacing it is that it keeps *everything*,
    so a workload stuck in a print loop grows the harness until it dies, and it
    dies before the timeout can fire.

    **The head and the tail are kept, and the middle is dropped**, half the
    budget each. Which end matters depends on the failure: a compilation error
    is at the top, a test summary and a traceback are at the bottom, and a
    stream long enough to be elided is a stream nobody will read in full. The
    join is marked, so the gap is visible rather than inferred.

    Bytes are decoded here rather than by `subprocess` in text mode because a
    chunk boundary can fall inside a multi-byte character. An incremental
    decoder holds the partial sequence until the rest of it arrives; decoding
    each chunk independently would corrupt one character per chunk.
    """

    def __init__(self, stream: io.BufferedReader, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._head_budget = limit // 2
        self._tail_budget = limit - self._head_budget
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._head: list[str] = []
        self._head_chars = 0
        self._tail: deque[str] = deque()
        self._tail_chars = 0
        self._dropped = 0
        # `text()` runs on the main thread and can be called while this one is
        # still reading — the partial-output-after-a-kill path. Without the
        # lock, joining the deque while the reader rotates it raises.
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_to_eof, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, seconds: float) -> bool:
        """Wait up to `seconds` for EOF. False means the pipe is still open."""
        self._thread.join(max(seconds, 0.0))
        return not self._thread.is_alive()

    def text(self) -> tuple[str, int]:
        """The captured text, and how many characters were elided from it."""
        with self._lock:
            head = "".join(self._head)
            tail = "".join(self._tail)
            dropped = self._dropped

        # The reader keeps whole chunks, so the tail overshoots its budget by up
        # to one chunk. Trimming here rather than there keeps the hot path a
        # single append.
        overflow = max(len(tail) - self._tail_budget, 0)
        tail = tail[overflow:]
        dropped += overflow

        if dropped == 0:
            return head + tail, 0
        marker = f"\n[coldfix elided {dropped} characters; limit is {self._limit} per stream]\n"
        return head + marker + tail, dropped

    def _read_to_eof(self) -> None:
        try:
            # `read1` returns what is available rather than blocking until the
            # buffer is full, so a command that hangs after printing one line
            # still leaves that line as evidence.
            while chunk := self._stream.read1(_READ_CHUNK_BYTES):
                self._absorb(self._decoder.decode(chunk))
        except (OSError, ValueError):
            # The pipe went away underneath us. Whatever was read before that
            # is still the useful part, so this ends the read rather than
            # propagating onto a thread nobody is watching.
            pass
        finally:
            self._absorb(self._decoder.decode(b"", final=True))
            with contextlib.suppress(OSError):
                self._stream.close()

    def _absorb(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            if self._head_chars < self._head_budget:
                room = self._head_budget - self._head_chars
                self._head.append(text[:room])
                self._head_chars += len(text[:room])
                text = text[room:]
                if not text:
                    return

            self._tail.append(text)
            self._tail_chars += len(text)
            while self._tail and self._tail_chars - len(self._tail[0]) >= self._tail_budget:
                oldest = self._tail.popleft()
                self._tail_chars -= len(oldest)
                self._dropped += len(oldest)


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


def _kill_process_group(popen: subprocess.Popen[bytes]) -> None:
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


def _join_all(captures: tuple[_BoundedCapture, ...], seconds: float) -> bool:
    """Wait for every capture to reach EOF within `seconds` between them."""
    deadline = time.perf_counter() + max(seconds, 0.0)
    finished = True
    for capture in captures:
        if not capture.join(deadline - time.perf_counter()):
            finished = False
    return finished


def execute(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
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

    Output is decoded as UTF-8 text with undecodable bytes replaced. A command
    whose output is genuinely binary cannot be measured through this function —
    the original bytes are not recoverable from the result. Nothing in the
    project needs them yet, and adding a bytes mode before something does would
    be guessing at its shape.

    `max_output_chars` bounds each stream. Past it the middle of the stream is
    dropped, the head and tail are kept, and the count of elided characters is
    on the result. There is no unlimited setting: an unbounded capture is how a
    workload from an unfamiliar repository exhausts memory before its own
    timeout can fire, and the timeout is no protection because the harness dies
    first.

    `stdin` is `/dev/null`. A command that reads from it gets EOF immediately
    instead of blocking on a terminal the harness may not have, which turns a
    repository whose test suite asks a question into a fast failure rather than
    a spent timeout.

    Raises:
        ValueError: `command` is empty, `timeout` is not positive, or
            `max_output_chars` is below one.
        ExecutionStartError: the command could not be started at all.
        ExecutionTimeoutError: the command outlived `timeout`. A non-zero exit code
            does **not** raise — it is returned in the result.
    """
    argv = [str(part) for part in command]
    if not argv:
        message = "command is empty; there is nothing to run"
        raise ValueError(message)
    if timeout <= 0:
        # Without this, a negative timeout starts the process and immediately
        # kills it, and the caller gets a timeout error for a command that was
        # never given a chance to run.
        message = f"timeout must be positive, got {timeout}"
        raise ValueError(message)
    if max_output_chars < 1:
        message = f"max_output_chars must be at least 1, got {max_output_chars}"
        raise ValueError(message)

    started = time.perf_counter()
    deadline = started + timeout

    try:
        popen = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_process_group_kwargs(),
        )
    except OSError as error:
        raise ExecutionStartError(argv, cwd, error) from error

    # Both streams were requested as pipes and the process is in binary mode, so
    # both are buffered readers. The check is what lets the reader use `read1`,
    # which the general `IO[bytes]` protocol does not promise.
    if not isinstance(popen.stdout, io.BufferedReader) or not isinstance(
        popen.stderr, io.BufferedReader
    ):  # pragma: no cover
        message = "subprocess did not provide the buffered pipes it was asked for"
        raise ExecutionError(message)

    # Both pipes are drained concurrently, on threads. Reading them in sequence
    # — or calling wait() before reading — deadlocks as soon as the child writes
    # more than a pipe buffer (~64 KB) to the stream that is not being read.
    captures = (
        _BoundedCapture(popen.stdout, max_output_chars),
        _BoundedCapture(popen.stderr, max_output_chars),
    )
    for capture in captures:
        capture.start()

    timed_out = False
    try:
        popen.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    else:
        # The process is gone, but a grandchild that inherited the pipes can
        # still hold them open, and then EOF never comes. Reading is bounded by
        # the same deadline as running, because `timeout` means a bound on this
        # call and not merely on the child.
        if not _join_all(captures, deadline - time.perf_counter()):
            timed_out = True

    if timed_out:
        _kill_process_group(popen)
        # Bounded on purpose. If the kill did not reach a grandchild holding the
        # pipe, waiting for EOF here would block forever inside the timeout
        # handler of the one function whose purpose is to bound how long a
        # command may take. Losing the partial output is much cheaper, and the
        # reader threads are daemons, so a pipe that never closes leaks a thread
        # rather than stopping the run.
        _join_all(captures, _TERM_GRACE_SECONDS)
        stdout, _ = captures[0].text()
        stderr, _ = captures[1].text()
        raise ExecutionTimeoutError(argv, timeout, stdout, stderr) from None

    elapsed = time.perf_counter() - started
    stdout, stdout_dropped = captures[0].text()
    stderr, stderr_dropped = captures[1].text()
    return ExecutionResult(
        command=tuple(argv),
        exit_code=popen.returncode,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=elapsed,
        stdout_dropped_chars=stdout_dropped,
        stderr_dropped_chars=stderr_dropped,
    )

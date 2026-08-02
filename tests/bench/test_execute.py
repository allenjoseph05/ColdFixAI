"""`execute()` reports faithfully, and fails safely.

Four of the tests below check what happens when a command misbehaves rather
than when it succeeds, because that is where the acceptance criteria are. A
harness that deadlocks on a chatty subprocess, or that leaves an orphan running
after a timeout, corrupts every measurement taken afterwards — and does it
without raising anything.

The process-group test is the one that matters most. It deliberately tries to
leave an orphan behind and asserts that it cannot, which is the adversarial
shape `CLAUDE.md` requires for a safety property.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError, execute

PY = sys.executable


def run_python(code: str, **kwargs: object) -> ExecutionResult:
    """Run a snippet in a fresh interpreter, so tests are platform-neutral."""
    return execute([PY, "-c", code], **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ reporting


def test_reports_stdout_stderr_exit_code_and_wall_time() -> None:
    result = run_python(
        "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)",
        timeout=30,
    )

    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.exit_code == 3
    assert result.wall_seconds > 0
    assert not result.ok


def test_a_non_zero_exit_is_a_result_not_an_exception() -> None:
    """ "The tests failed" is an observation the caller may be expecting.

    Only the *absence* of a usable result is exceptional. If this raised, every
    caller would have to catch an exception to read an exit code.
    """
    result = run_python("raise SystemExit(1)", timeout=30)

    assert result.exit_code == 1
    assert not result.ok


def test_wall_time_covers_the_whole_command() -> None:
    result = run_python("import time; time.sleep(0.3)", timeout=30)

    # Bounded below by the sleep, and generously above it — process spawn is
    # included on purpose, since that is time the caller really waited.
    assert 0.3 <= result.wall_seconds < 20


def test_cwd_is_respected(tmp_path: Path) -> None:
    result = run_python("import os; print(os.getcwd())", timeout=30, cwd=tmp_path)

    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


def test_env_replaces_rather_than_extends() -> None:
    """A variable in the parent environment must not leak into the child.

    Silent inheritance is how two runs of the same measurement come to differ.
    """
    os.environ["COLDFIX_LEAK_PROBE"] = "leaked"
    try:
        # A minimal environment the interpreter can still start in. Windows
        # needs SYSTEMROOT; POSIX needs PATH. Neither includes the probe.
        minimal = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "SystemRoot", "LD_LIBRARY_PATH")
            if key in os.environ
        }
        result = run_python(
            "import os; print(os.environ.get('COLDFIX_LEAK_PROBE', 'ABSENT'))",
            timeout=30,
            env=minimal,
        )
        assert result.stdout.strip() == "ABSENT"
    finally:
        del os.environ["COLDFIX_LEAK_PROBE"]


# -------------------------------------------------------------- deadlock


def test_large_output_on_both_streams_does_not_deadlock() -> None:
    """Half a megabyte per stream, far past the ~64 KB pipe buffer.

    The classic implementation of this function — `wait()` then read, or read
    one stream fully before the other — hangs here forever rather than
    failing. If this test times out under pytest rather than failing, that is
    the bug it exists to catch.
    """
    payload = 512 * 1024
    result = run_python(
        f"import sys;sys.stdout.write('o' * {payload});sys.stderr.write('e' * {payload})",
        timeout=60,
    )

    assert len(result.stdout) == payload
    assert len(result.stderr) == payload
    assert result.exit_code == 0


# --------------------------------------------------------------- timeout


def test_timeout_raises_a_typed_error() -> None:
    with pytest.raises(ExecutionTimeoutError) as caught:
        run_python("import time; time.sleep(30)", timeout=0.4)

    assert caught.value.timeout_seconds == 0.4
    assert "sleep" in " ".join(caught.value.command)


def test_timeout_is_distinguishable_from_a_failing_command() -> None:
    """The two failure modes must not be confused for one another.

    A command that exits 1 has told us something. A command that was killed
    told us nothing, and any measurement taken from it is void.
    """
    failed = run_python("raise SystemExit(1)", timeout=30)
    assert isinstance(failed, ExecutionResult)

    with pytest.raises(ExecutionTimeoutError):
        run_python("import time; time.sleep(30)", timeout=0.4)


def test_timeout_preserves_output_written_before_the_kill() -> None:
    """Partial output is usually the only clue about where it got stuck."""
    with pytest.raises(ExecutionTimeoutError) as caught:
        run_python(
            "import sys, time; sys.stdout.write('reached-stage-1'); "
            "sys.stdout.flush(); time.sleep(30)",
            timeout=0.6,
        )

    assert "reached-stage-1" in caught.value.partial_stdout


# ------------------------------------------------- the safety property


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    """A grandchild must not survive its parent being timed out.

    This is the adversarial test: the command deliberately spawns a detached
    child that would outlive it, and the assertion is that the child is gone.

    Killing only the direct child leaves that grandchild running — consuming
    CPU while later measurements are taken, with nothing raised and nothing
    logged. The failure is silent, and it corrupts numbers rather than
    producing an error, which is why it is tested rather than assumed.
    """
    sentinel = tmp_path / "orphan-survived.txt"

    # Written as real files rather than nested `-c` snippets. The first draft
    # inlined the child's source inside the parent's, and the sentinel path
    # (`C:\Users\...`) contained `\U` — a valid escape in the *outer* string
    # literal, consumed before the inner `r''` prefix could apply. The parent
    # died of a SyntaxError in 0.09s, no timeout fired, and the test failed for
    # a reason that had nothing to do with process groups.
    child = tmp_path / "child.py"
    child.write_text(
        f"import time\ntime.sleep(1.5)\nopen({str(sentinel)!r}, 'w').write('alive')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    with pytest.raises(ExecutionTimeoutError):
        execute([PY, str(parent)], timeout=1.0)

    # Wait past the grandchild's sleep. If the group kill worked it never wakes
    # up to write the file.
    time.sleep(2.5)

    assert not sentinel.exists(), (
        "a grandchild outlived the timeout — the kill reached the direct child "
        "only, so orphaned processes will accumulate and skew later measurements"
    )

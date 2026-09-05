"""The one place `os.wait4` is called.

Split out so the mypy override it needs covers a couple of dozen lines that do
nothing but start a child and reap it, rather than the module where the
arithmetic lives. `resource` is POSIX-only: on a Windows developer machine mypy
resolves the module and reports every attribute missing, and silencing that
where the measurement logic lives would silence real mistakes too.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from coldfix.collect.usage import ChildResult, PlatformUnsupportedError, Usage

DARWIN = "darwin"


class PosixChildRunner:
    """Start a child, reap it with `wait4`, and return that child's own usage."""

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        if not hasattr(os, "wait4"):
            raise PlatformUnsupportedError

        merged = dict(os.environ)
        if env:
            merged.update(env)

        # Temporary files rather than pipes: reading two pipes in sequence before
        # reaping can deadlock when the second fills, and `communicate()` reaps
        # the child itself, which is exactly what `wait4` needs to do.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(
                list(command), cwd=str(cwd), env=merged, stdout=out, stderr=err
            )
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode(errors="replace")
            stderr = err.read().decode(errors="replace")

        # `ru_maxrss` is kilobytes on Linux and bytes on macOS.
        scale = 1 if sys.platform == DARWIN else 1024
        return ChildResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            usage=Usage(
                cpu_s=usage.ru_utime + usage.ru_stime,
                peak_rss_bytes=usage.ru_maxrss * scale,
                read_blocks=usage.ru_inblock,
                write_blocks=usage.ru_oublock,
            ),
        )

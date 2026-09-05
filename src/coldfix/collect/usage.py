"""What one child process cost, and the seam that runs it.

The seam is *"run this and tell me what it cost"*, not *"read a counter"*, and
that is the correction the corpus forced on 2026-09-05.

`getrusage(RUSAGE_CHILDREN)` looks like the obvious source and is the wrong one:
`ru_maxrss` there is a high-water mark across **every** child the process has
ever reaped, so it cannot be differenced and cannot be attributed to one run.
Measured across the eight-subject corpus it reported an identical 97MB peak for
all of them, including a subject that does nothing but an import. `os.wait4`
returns usage for one named child, which is the number we actually wanted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class MeasurementError(Exception):
    """Something about the measurement itself is wrong."""


class PlatformUnsupportedError(MeasurementError):
    """The host cannot supply per-child resource usage.

    Windows is the case that matters. `psutil` reads `GetProcessTimes` for a
    child and returns exactly one clock tick regardless of real usage --
    measured 2026-09-05: a 1.27s busy loop reported 0.0156s. That is not a
    degraded measurement, it is a wrong one, and a wrong number is worse than a
    refusal. The container is Linux; run there.
    """

    def __init__(self) -> None:
        super().__init__(
            "per-child resource usage cannot be read on this platform, and a wrong "
            "number is worse than a refusal; run the measurement inside the Linux container"
        )


@dataclass(frozen=True)
class Usage:
    """What one child consumed, attributable to that child alone."""

    cpu_s: float
    peak_rss_bytes: int
    read_blocks: int
    write_blocks: int


@dataclass(frozen=True)
class ChildResult:
    """One completed run: what it printed, how it exited, what it cost."""

    returncode: int
    stdout: str
    stderr: str
    usage: Usage


@runtime_checkable
class ChildRunner(Protocol):
    """The seam. Real on POSIX, injected in tests.

    A protocol rather than a direct `wait4` call so the arithmetic above it --
    spreads, modes, repeatability, the workload floor -- is testable on any
    machine, while the one platform-specific implementation stays small enough
    to read in a sitting.
    """

    def run(
        self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None
    ) -> ChildResult: ...

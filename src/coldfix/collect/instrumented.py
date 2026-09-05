"""Running under an instrument, which yields counts and never timings.

S-18.2. `measure()` is the only source of truth for how long something took.
This is the other half: a run under a profiler, an allocation tracker or a span
exporter, which returns what that instrument counted and **no timing at all**.

**Counts need no repeats; timings do.** A query count, a call count, a sample
count is the same integer every run for the same input, so one run is enough and
a second would only cost time. That asymmetry is why the two functions have
different shapes rather than one function with a flag.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from coldfix.collect._child_posix import PosixChildRunner
from coldfix.collect.measurement import InstrumentedMeasurement, Unmeasured, WorkloadFailedError
from coldfix.collect.usage import ChildResult, ChildRunner, MeasurementError


class InstrumentFailedError(MeasurementError):
    """The instrument itself failed, which is not the subject failing.

    Observed 2026-09-05 across the corpus: `py-spy` intermittently exits 1 with
    *"No child process (os error 10)"* when several profiling runs follow each
    other closely. The subject was fine. Reporting that as a broken workload
    sends the caller to fix a program that has nothing wrong with it, so the two
    failures are different types.
    """

    def __init__(self, instrument: str, attempts: int, output: str) -> None:
        super().__init__(
            f"{instrument} failed {attempts} time(s) and produced nothing to harvest; "
            f"the subject is not implicated. Last output: {output[-300:]}"
        )
        self.instrument = instrument


Harvest = Callable[[ChildResult], Mapping[str, int]]
"""Turns a finished run into the counts its instrument produced. Supplied by the
caller because only the caller knows what the instrument writes and where."""


def measure_under(  # noqa: PLR0913 - which instrument, what to run, where, how to
    # harvest its output, what environment, which runner, and what it could not see.
    # Every one is a decision the caller has to make; a config object would hide them.
    instrument: str,
    command: Sequence[str],
    *,
    cwd: Path,
    harvest: Harvest,
    env: Mapping[str, str] | None = None,
    runner: ChildRunner | None = None,
    not_measured: Sequence[Unmeasured] = (),
) -> InstrumentedMeasurement:
    """Run `command` under `instrument` and return what it counted.

    The returned artifact has no `wall`, no `cpu_s` and no `mode`, because the
    elapsed time of a run under a profiler is a fact about the profiler. There
    is nowhere to put such a number, which is the enforcement.
    """
    runner = runner or PosixChildRunner()
    result = runner.run(command, cwd, env)
    if result.returncode != 0:
        raise WorkloadFailedError(result.returncode, result.stderr or result.stdout)

    return InstrumentedMeasurement(
        measurement_id=f"m-{uuid.uuid4().hex[:8]}",
        command=tuple(command),
        repeats=1,
        output_digest=hashlib.sha256(result.stdout.encode()).hexdigest(),
        output_bytes=len(result.stdout.encode()),
        instrument=instrument,
        counts=dict(harvest(result)),
        not_measured=tuple(not_measured),
    )

"""What a run cost, taken from outside the process.

S-18.1 and S-18.2. Nothing here reads the subject's source, and nothing here
asks the subject to report on itself. Every number comes from the operating
system, which was already counting.

**Two measurement types, not one with optional fields.** A bare run yields
timings; a run under an instrument yields counts and *no timing keys at all*.
`InstrumentedMeasurement` does not have a `wall` attribute -- not `None`, not
documented as unreliable, absent -- because the failure this prevents is a
caller comparing 8.4 seconds under a profiler against 2.1 seconds without one
and reporting a 75% regression. `primitives/instructions.py` learned this the
same way and states it the same way: *"not a documented caveat but a missing
key, so no caller can accidentally compare an instrumented time against a clean
one."* mypy rejects the mistake before a test has to.

**A field the platform could not supply is `not_measured`, never `0`.** Zero
block reads is a real, meaningful measurement -- it means the run was served
from the page cache. Reporting an unavailable counter as zero makes those two
indistinguishable, which is how a system reports "nothing found" about
something it never looked at.
"""

from __future__ import annotations

import hashlib
import statistics
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from coldfix.collect._child_posix import PosixChildRunner
from coldfix.collect.usage import ChildResult, ChildRunner, MeasurementError

MINIMUM_REPEATS = 2
"""Below this there is no spread, and the spread is the whole point."""

DEFAULT_REPEATS = 5
"""Five, because the spread across repeats *is* the noise floor and three points
make a spread nobody should trust. S-1.7 measured the floor at roughly 20ms on a
350ms workload; a claim smaller than that is not a claim."""

JITTER_MULTIPLE = 2.0
"""How far a workload's spread must exceed this machine's own spread before the
variation can be attributed to the workload rather than to starting a process.

There is no fixed number of milliseconds here on purpose. A constant chosen on
one laptop is a constant that is wrong on a loaded CI box, and the corpus is a
check on the design rather than the thing the design is fitted to. What the
machine can resolve is measured, on the machine, at the moment it matters."""

Clock = Callable[[], float]
"""Where elapsed time comes from. Injectable so a test can exercise the
decision rather than whatever the machine happened to do -- a test that
sleeps is a test the machine can still fail under load."""

JITTER_PROBE = "pass"
"""A program that does nothing. Its spread across repeats is the cost of
starting a process here and now, and therefore the floor of what any measurement
on this machine can distinguish."""

REPEATABILITY_LIMIT = 0.20
"""A relative spread above this means the workload is not measuring the same
thing twice, and every comparison built on it would be noise. Refuse early and
say why rather than produce numbers that look fine."""

BLOCK_BYTES = 512
"""`ru_inblock` and `ru_oublock` count 512-byte block-device operations."""


class SingleRunError(MeasurementError):
    """One run has no spread, and the spread is the noise floor."""

    def __init__(self) -> None:
        super().__init__(
            "a single run has no spread, and the spread is the noise floor -- "
            "without it there is nothing to say a difference is larger than the noise"
        )


class WorkloadFailedError(MeasurementError):
    """The subject exited non-zero, so any number taken is about the failure."""

    def __init__(self, returncode: int, output: str) -> None:
        super().__init__(f"the workload exited {returncode}: {output[-400:]}")
        self.returncode = returncode


class NoBaselineError(MeasurementError):
    """A share of zero is not a share."""

    def __init__(self) -> None:
        super().__init__("the baseline measured no elapsed time, so no share of it can be removed")


class WorkloadTooShortError(MeasurementError):
    """The variation is this machine, not this program.

    Found by the corpus on 2026-09-05: a `print('done')` workload takes about
    30ms, of which starting the interpreter is most, so the relative spread
    exceeds any sensible limit while nothing is actually wrong. Reporting that as
    *not repeatable* would name four causes -- shared state, a warm cache, a busy
    machine, input-dependent work -- none of which is true.

    The distinction is made by measuring, not by a threshold: a program that does
    nothing is run here and now, and if the workload's spread is no larger than
    that, the spread belongs to the machine.
    """

    def __init__(self, median: float, jitter: float) -> None:
        detectable = jitter / median if median else 1.0
        super().__init__(
            f"the workload ran for {median * 1000:.0f}ms and varied by "
            f"{jitter * 1000:.0f}ms, which is no more than this machine varies by when it "
            f"runs a program that does nothing. The spread is the machine, not the program, "
            f"and nothing smaller than a {detectable:.0%} improvement could be detected. "
            "Increase the driver's scale until the work outweighs starting a process."
        )
        self.median = median
        self.jitter = jitter


class NotRepeatableError(MeasurementError):
    """Two runs of the same workload did not measure the same thing.

    Names the four causes rather than reporting a spread, because the spread is
    not actionable and the causes are.
    """

    def __init__(self, spread: float, command: Sequence[str]) -> None:
        super().__init__(
            f"the workload is not repeatable: runs of {' '.join(command)} differ by "
            f"{spread * 100:.0f}%, above the {REPEATABILITY_LIMIT * 100:.0f}% limit.\n"
            "  - shared state between runs      the reset may not be clearing it\n"
            "  - a warm cache on later runs     the first run paid a cost the rest did not\n"
            "  - the machine is busy            something else is competing for the CPU\n"
            "  - the work depends on input      it may not be doing the same thing twice\n"
            "Fix the driver and measure again -- this costs no model tokens."
        )
        self.spread = spread


class IncomparableMeasurementError(MeasurementError):
    """A timing from an instrumented pass was compared with a bare one.

    Cannot normally happen -- `compare_wall` is typed to `BareMeasurement`, so
    mypy rejects it first. This exists for the dynamic path, and for the test
    that attempts the violation and asserts it fails.
    """

    def __init__(self, position: str, instrument: str) -> None:
        super().__init__(
            f"{position} was taken under {instrument!r}; a timing measured under an instrument "
            "describes the instrument. Compare counts across instrumented passes, and timings "
            "only across bare ones."
        )
        self.instrument = instrument


class Mode(StrEnum):
    """Whether the program computed or waited. Different fixes entirely."""

    COMPUTING = "computing"
    WAITING = "waiting"
    UNKNOWN = "unknown"


COMPUTING_THRESHOLD = 0.70
"""Processor time above this share of elapsed time means it was working. Below,
it spent its time waiting and no algorithm will help it."""


class Unmeasured(BaseModel, frozen=True):
    what: str
    why: str


class Spread(BaseModel, frozen=True):
    """The distribution across repeats. The spread is the noise floor."""

    median: float
    low: float
    high: float

    @property
    def absolute(self) -> float:
        return self.high - self.low

    @property
    def relative(self) -> float:
        return self.absolute / self.median if self.median else 0.0


class _Recorded(BaseModel, frozen=True):
    """What both kinds of measurement carry."""

    measurement_id: str
    command: tuple[str, ...]
    repeats: int
    output_digest: str
    output_bytes: int
    not_measured: tuple[Unmeasured, ...] = ()


class BareMeasurement(_Recorded, frozen=True):
    """A run with no instrument attached. The only source of truth for timing."""

    pass_kind: Literal["bare"] = "bare"
    wall: Spread
    cpu_s: float
    mode: Mode
    peak_rss_bytes: int | None
    read_bytes: int | None
    write_bytes: int | None

    @property
    def noise_floor_s(self) -> float:
        return self.wall.absolute


class InstrumentedMeasurement(_Recorded, frozen=True):
    """A run under an instrument. Counts only.

    **There is deliberately no timing field on this model.** Adding one would
    let a caller compare a profiled second against a clean one, which is the
    exact mistake the two-type split exists to make unrepresentable.
    """

    pass_kind: Literal["instrumented"] = "instrumented"
    instrument: str
    counts: Mapping[str, int] = Field(default_factory=dict)


Measurement = BareMeasurement | InstrumentedMeasurement


def measure(  # noqa: PLR0913 - what to run, where, how many times, how to reset,
    # what environment, which reader, and whether to insist on repeatability. Every
    # one is a decision the caller has to make; a config object would only hide them.
    command: Sequence[str],
    *,
    cwd: Path,
    repeats: int = DEFAULT_REPEATS,
    reset: Callable[[], None] | None = None,
    env: Mapping[str, str] | None = None,
    runner: ChildRunner | None = None,
    require_repeatable: bool = True,
    clock: Clock = time.perf_counter,
) -> BareMeasurement:
    """Run the command `repeats` times and return one artifact.

    `reset` is a callable, not a command string, for S-1.6's reason: a stored
    baseline can be stale and a callable cannot. It runs *before* every repeat
    including the first, so run one and run five measure the same thing.
    """
    if repeats < MINIMUM_REPEATS:
        raise SingleRunError
    runner = runner or PosixChildRunner()

    runs: list[ChildResult] = []
    walls: list[float] = []
    for _ in range(repeats):
        if reset is not None:
            reset()
        start = clock()
        run = runner.run(command, cwd, env)
        walls.append(clock() - start)
        if run.returncode != 0:
            raise WorkloadFailedError(run.returncode, run.stderr or run.stdout)
        runs.append(run)

    walls.sort()
    wall = Spread(median=statistics.median(walls), low=walls[0], high=walls[-1])
    if require_repeatable and wall.relative > REPEATABILITY_LIMIT:
        # Something is too variable to build on -- but the machine and the program
        # are different diagnoses with different fixes, and only measuring tells
        # them apart. The probe runs only when a run has already failed, so the
        # common path pays nothing for it.
        floor = baseline_jitter(cwd=cwd, repeats=repeats, env=env, runner=runner, clock=clock)
        if wall.absolute <= floor * JITTER_MULTIPLE:
            raise WorkloadTooShortError(wall.median, floor)
        raise NotRepeatableError(wall.relative, command)

    # Every field belongs to one named child, so nothing here is a difference
    # of counters shared with other runs.
    cpu = sum(r.usage.cpu_s for r in runs) / len(runs)
    peak = max(r.usage.peak_rss_bytes for r in runs)
    read_blocks = sum(r.usage.read_blocks for r in runs) // len(runs)
    write_blocks = sum(r.usage.write_blocks for r in runs) // len(runs)

    outputs = {r.stdout for r in runs}
    not_measured: list[Unmeasured] = []
    if len(outputs) > 1:
        not_measured.append(
            Unmeasured(
                what="output_digest",
                why="the workload produced different output across repeats, "
                "so no single digest describes it",
            )
        )
    last = runs[-1].stdout

    return BareMeasurement(
        measurement_id=f"m-{uuid.uuid4().hex[:8]}",
        command=tuple(command),
        repeats=repeats,
        wall=wall,
        cpu_s=cpu,
        mode=_mode(cpu, wall.median),
        peak_rss_bytes=peak or None,
        read_bytes=read_blocks * BLOCK_BYTES,
        write_bytes=write_blocks * BLOCK_BYTES,
        output_digest=hashlib.sha256(last.encode()).hexdigest(),
        output_bytes=len(last.encode()),
        not_measured=tuple(not_measured),
    )


def baseline_jitter(
    *,
    cwd: Path,
    repeats: int = DEFAULT_REPEATS,
    env: Mapping[str, str] | None = None,
    runner: ChildRunner | None = None,
    clock: Clock = time.perf_counter,
) -> float:
    """What this machine's spread is when the program does nothing.

    Measured rather than assumed, because it is a property of the machine at the
    moment of measuring -- a loaded container and an idle laptop do not share a
    floor, and a constant fitted to one of them is wrong on the other.
    """
    runner = runner or PosixChildRunner()
    walls: list[float] = []
    for _ in range(repeats):
        start = clock()
        runner.run([sys.executable, "-c", JITTER_PROBE], cwd, env)
        walls.append(clock() - start)
    return max(walls) - min(walls)


def _mode(cpu_s: float, wall_s: float) -> Mode:
    if wall_s <= 0:
        return Mode.UNKNOWN
    return Mode.COMPUTING if cpu_s / wall_s >= COMPUTING_THRESHOLD else Mode.WAITING


def compare_wall(before: BareMeasurement, after: BareMeasurement) -> float:
    """The share of elapsed time removed between two measurements.

    Typed to `BareMeasurement`, so mypy rejects an instrumented argument before
    the program runs. The runtime checks below cover the dynamic path -- a value
    deserialized from a checkpoint, or a caller that silenced the type error --
    and give S-18.2's test something to assert against.
    """
    if not isinstance(before, BareMeasurement):
        raise IncomparableMeasurementError("before", before.instrument)
    if not isinstance(after, BareMeasurement):
        raise IncomparableMeasurementError("after", after.instrument)
    if before.wall.median <= 0:
        raise NoBaselineError
    return (before.wall.median - after.wall.median) / before.wall.median

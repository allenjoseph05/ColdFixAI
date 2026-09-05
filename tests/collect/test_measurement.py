"""S-18.1 and S-18.2.

The arithmetic is tested with an injected `UsageReader` so it runs anywhere; the
one test that reads the real operating system is marked and skipped off POSIX.
That split is deliberate -- `PosixUsageReader` is four lines and a platform
check, and everything worth getting wrong is above it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.collect._child_posix import PosixChildRunner
from coldfix.collect.measurement import (
    BareMeasurement,
    IncomparableMeasurementError,
    InstrumentedMeasurement,
    Mode,
    NotRepeatableError,
    Spread,
    Unmeasured,
    WorkloadTooShortError,
    compare_wall,
    measure,
)
from coldfix.collect.usage import (
    ChildResult,
    MeasurementError,
    PlatformUnsupportedError,
    Usage,
)


class SteadyRunner:
    """Really runs the child, then reports usage we control.

    Real subprocesses so elapsed time, exit codes and output are genuine; a
    fabricated `Usage` so the mode arithmetic can be driven from the test rather
    than from whatever the machine happened to do.
    """

    def __init__(self, cpu_per_run: float = 0.5, peak_bytes: int = 8_000_000) -> None:
        self.cpu_per_run = cpu_per_run
        self.peak_bytes = peak_bytes
        self.runs = 0

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        self.runs += 1
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        return ChildResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            usage=Usage(
                cpu_s=self.cpu_per_run,
                peak_rss_bytes=self.peak_bytes,
                read_blocks=0,
                write_blocks=0,
            ),
        )


def script(body: str) -> list[str]:
    return [sys.executable, "-c", body]


QUIET = "print('done')"


def test_a_measurement_carries_an_id_the_harness_minted(tmp_path: Path) -> None:
    """Every claim downstream cites one of these, so the tool must mint it."""
    result = measure(
        script(QUIET), cwd=tmp_path, repeats=2, runner=SteadyRunner(), require_repeatable=False
    )
    assert result.measurement_id.startswith("m-")
    assert result.command == tuple(script(QUIET))


def test_a_single_run_is_refused_because_the_spread_is_the_noise_floor(tmp_path: Path) -> None:
    with pytest.raises(MeasurementError, match="no spread"):
        measure(script(QUIET), cwd=tmp_path, repeats=1, runner=SteadyRunner())


def test_the_noise_floor_is_the_spread_across_repeats(tmp_path: Path) -> None:
    result = measure(
        script(QUIET), cwd=tmp_path, repeats=3, runner=SteadyRunner(), require_repeatable=False
    )
    assert result.noise_floor_s == result.wall.absolute
    assert result.wall.low <= result.wall.median <= result.wall.high


def test_reset_runs_before_every_repeat_including_the_first(tmp_path: Path) -> None:
    """Otherwise run-one and run-five measure different things, and the growth
    curve fitted across them is a curve about the reset."""
    calls: list[int] = []
    measure(
        script(QUIET),
        cwd=tmp_path,
        repeats=4,
        reset=lambda: calls.append(1),
        runner=SteadyRunner(),
        require_repeatable=False,
    )
    assert len(calls) == 4


def test_mostly_processor_time_reads_as_computing(tmp_path: Path) -> None:
    result = measure(
        script("s=0\nfor i in range(400000): s+=i\nprint(s)"),
        cwd=tmp_path,
        repeats=2,
        runner=SteadyRunner(cpu_per_run=10.0),
        require_repeatable=False,
    )
    assert result.mode is Mode.COMPUTING


def test_little_processor_time_reads_as_waiting(tmp_path: Path) -> None:
    """The measurement that separates 'needs a better algorithm' from 'needs an
    index'. A busy program and an idle one take the same elapsed time."""
    result = measure(
        script("import time; time.sleep(0.2); print('slept')"),
        cwd=tmp_path,
        repeats=2,
        runner=SteadyRunner(cpu_per_run=0.0),
        require_repeatable=False,
    )
    assert result.mode is Mode.WAITING


class Scripted:
    """A runner and a clock that agree on exactly how long each run took.

    Nothing sleeps. A test that sleeps still asks the machine how long it slept,
    and under load the answer is not the one the test wrote down -- so the test
    ends up agreeing with the machine instead of checking the decision. The
    jitter probe is told apart from the workload by its command, which is always
    a no-op.
    """

    def __init__(self, workload: Sequence[float], probe: Sequence[float]) -> None:
        self.workload = list(workload)
        self.probe = list(probe)
        self.now = 0.0
        self.pending = 0.0

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        queue = self.probe if command[-1] == "pass" else self.workload
        self.pending = queue.pop(0) if queue else 0.0
        return ChildResult(
            returncode=0,
            stdout="done\n",
            stderr="",
            usage=Usage(cpu_s=0.001, peak_rss_bytes=1024, read_blocks=0, write_blocks=0),
        )

    def clock(self) -> float:
        """Advances by exactly what the last run was scripted to take."""
        self.now += self.pending
        self.pending = 0.0
        return self.now


def test_when_the_spread_is_no_bigger_than_the_machines_own_it_blames_the_machine(
    tmp_path: Path,
) -> None:
    """A short run whose variation matches a program that does nothing has told
    us about the machine, not the program. Reporting it as "not repeatable" would
    name four causes -- shared state, a warm cache, a busy machine, input-dependent
    work -- every one of them false."""
    runner = Scripted(workload=[0.01, 0.05, 0.03], probe=[0.01, 0.06, 0.02])
    with pytest.raises(WorkloadTooShortError) as caught:
        measure(script(QUIET), cwd=tmp_path, repeats=3, runner=runner, clock=runner.clock)
    message = str(caught.value)
    assert "this machine varies by when it runs a program that does nothing" in message
    assert "Increase the driver's scale" in message


def test_when_the_spread_dwarfs_the_machines_own_it_blames_the_workload(
    tmp_path: Path,
) -> None:
    """Same relative spread, same refusal to proceed -- and a completely different
    diagnosis, because the machine was steady and the program was not."""
    runner = Scripted(workload=[0.30, 1.50, 0.60], probe=[0.01, 0.012, 0.011])
    with pytest.raises(NotRepeatableError) as caught:
        measure(script(QUIET), cwd=tmp_path, repeats=3, runner=runner, clock=runner.clock)
    for cause in ("shared state", "warm cache", "machine is busy", "depends on input"):
        assert cause in str(caught.value)


def test_a_short_run_that_is_steady_is_not_refused(tmp_path: Path) -> None:
    """The floor is not a minimum duration. A 30ms run whose spread is 2ms can
    show a 10% improvement, and refusing it would throw away a real measurement
    because of a constant somebody picked."""
    runner = Scripted(workload=[0.030, 0.031, 0.032], probe=[])
    result = measure(script(QUIET), cwd=tmp_path, repeats=3, runner=runner, clock=runner.clock)
    assert result.wall.median < 0.05


def test_the_jitter_probe_only_runs_when_something_has_already_failed(
    tmp_path: Path,
) -> None:
    """The common path is a measurement that passes, and it must not pay for a
    diagnosis nobody needed."""
    runner = Scripted(workload=[0.30, 0.305, 0.31], probe=[9.0])
    measure(script(QUIET), cwd=tmp_path, repeats=3, runner=runner, clock=runner.clock)
    assert runner.probe == [9.0], "the probe ran when the measurement was fine"


@pytest.mark.timing
def test_a_workload_that_will_not_repeat_is_refused_with_its_causes(
    tmp_path: Path,
) -> None:
    """A spread this wide makes every downstream comparison noise. The message
    names the four causes because a percentage is not actionable.

    Marked `timing` because it is the one test here that sleeps: the logic is
    covered deterministically above, and this exists to confirm the same decision
    is reached against a real clock and a real varying workload. It belongs on a
    quiet machine, which is what the marker means.
    """
    body = (
        "import sys, time, pathlib\n"
        "p = pathlib.Path('counter')\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "time.sleep(0.01 + n * 0.25)\n"
        "print('run', n)\n"
    )
    with pytest.raises(NotRepeatableError) as caught:
        measure(script(body), cwd=tmp_path, repeats=3, runner=SteadyRunner())
    message = str(caught.value)
    for cause in ("shared state", "warm cache", "machine is busy", "depends on input"):
        assert cause in message


def test_a_failing_workload_raises_rather_than_returning_a_measurement(tmp_path: Path) -> None:
    """A number taken from a run that crashed is a number about the crash."""
    with pytest.raises(MeasurementError, match="exited"):
        measure(script("raise SystemExit(3)"), cwd=tmp_path, repeats=2, runner=SteadyRunner())


def test_output_that_differs_across_repeats_is_recorded_as_not_measured(tmp_path: Path) -> None:
    """Not as a digest of whichever run happened to be last. A caller comparing
    that digest to an ablation's would read nondeterminism as a behaviour change."""
    result = measure(
        script("import random; print(random.random())"),
        cwd=tmp_path,
        repeats=3,
        runner=SteadyRunner(),
        require_repeatable=False,
    )
    assert any(u.what == "output_digest" for u in result.not_measured)


# ----------------------------------------------------------------- S-18.2


def instrumented() -> InstrumentedMeasurement:
    return InstrumentedMeasurement(
        measurement_id="m-instr",
        command=("python", "app.py"),
        repeats=1,
        output_digest="0" * 64,
        output_bytes=4,
        instrument="py-spy",
        counts={"samples": 8140},
    )


def bare(median: float) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=f"m-{median}",
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=4,
        wall=Spread(median=median, low=median * 0.98, high=median * 1.02),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=1024,
        read_bytes=0,
        write_bytes=0,
    )


def test_an_instrumented_measurement_has_no_timing_field_at_all() -> None:
    """The enforcement is the absent key, not a docstring. `None` would still
    let a caller reach for it and get an answer; absence does not."""
    assert not hasattr(instrumented(), "wall")
    assert "wall" not in InstrumentedMeasurement.model_fields
    assert "cpu_s" not in InstrumentedMeasurement.model_fields
    assert "mode" not in InstrumentedMeasurement.model_fields


def test_comparing_an_instrumented_timing_against_a_bare_one_is_refused() -> None:
    """The violation this whole split exists to prevent: 8.4s under a profiler
    against 2.1s without one, reported as a 75% regression. Attempted here and
    asserted to fail."""
    with pytest.raises(IncomparableMeasurementError, match="py-spy"):
        compare_wall(bare(8.4), instrumented())  # type: ignore[arg-type]
    with pytest.raises(IncomparableMeasurementError, match="py-spy"):
        compare_wall(instrumented(), bare(2.1))  # type: ignore[arg-type]


def test_two_bare_measurements_compare_normally() -> None:
    assert compare_wall(bare(8.0), bare(2.0)) == pytest.approx(0.75)


def test_a_zero_and_an_unmeasured_value_are_distinguishable() -> None:
    """Zero block reads means the run was served from the page cache, which is a
    real finding. An unavailable counter is not. Collapsing them is how a system
    reports 'nothing found' about something it never looked at."""
    measured = bare(1.0)
    assert measured.read_bytes == 0
    assert measured.not_measured == ()

    unknown = measured.model_copy(
        update={
            "read_bytes": None,
            "not_measured": (Unmeasured(what="read_bytes", why="unavailable on this platform"),),
        }
    )
    assert unknown.read_bytes is None
    assert unknown.not_measured[0].what == "read_bytes"
    assert measured.read_bytes != unknown.read_bytes


# ----------------------------------------------------------------- platform


@pytest.mark.skipif(sys.platform != "win32", reason="the refusal only applies on Windows")
def test_windows_is_refused_rather_than_measured_wrongly() -> None:
    """Measured 2026-09-05: psutil reports a 1.27s busy loop as 0.0156s of
    processor time -- exactly one clock tick, every time. A wrong number is
    worse than a refusal."""
    with pytest.raises(PlatformUnsupportedError, match="Linux container"):
        PosixChildRunner().run(["true"], Path(), None)


@pytest.mark.skipif(sys.platform == "win32", reason="needs getrusage")
def test_the_real_reader_sees_a_child_burn_processor_time(tmp_path: Path) -> None:
    result = measure(
        script("s=0\nfor i in range(3_000_000): s+=i*i\nprint(s)"),
        cwd=tmp_path,
        repeats=2,
        require_repeatable=False,
    )
    assert result.cpu_s > 0
    assert result.mode is Mode.COMPUTING
    assert result.peak_rss_bytes is not None and result.peak_rss_bytes > 0

"""S-18.2 and S-18.3.

The speedscope parsing is a pure function and is tested directly, so grouping,
stripping and divergence are covered on any machine. The py-spy launch itself is
covered by the corpus run, which is where a real profiler and a real subject
exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.collect.instrumented import InstrumentFailedError, measure_under
from coldfix.collect.measurement import InstrumentedMeasurement, WorkloadFailedError
from coldfix.collect.profiling import (
    ProfileMeasurement,
    Site,
    profile,
    sites_from_speedscope,
)
from coldfix.collect.usage import ChildResult, Usage


class RealRunner:
    """Runs the child for real and reports usage we control."""

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
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
            usage=Usage(cpu_s=1.0, peak_rss_bytes=1024, read_blocks=0, write_blocks=0),
        )


def speedscope(frames: list[dict[str, object]], samples: list[list[int]]) -> dict[str, object]:
    return {
        "shared": {"frames": frames},
        "profiles": [{"type": "sampled", "samples": samples, "weights": [0.01] * len(samples)}],
    }


APP = {"name": "handler", "file": "/app/views.py", "line": 41}
MODEL = {"name": "books", "file": "/app/models.py", "line": 112}
RENDER = {"name": "render", "file": "/app/render.py", "line": 88}
FROZEN = {"name": "_load", "file": "<frozen importlib._bootstrap>", "line": 935}
VENDOR = {"name": "execute", "file": "/site-packages/orm/query.py", "line": 700}


# ------------------------------------------------------------------ S-18.2


def test_an_instrumented_pass_returns_counts_and_no_timing(tmp_path: Path) -> None:
    """The whole point of the split: this artifact has nowhere to put a second."""
    result = measure_under(
        "fake-instrument",
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        harvest=lambda run: {"lines": len(run.stdout.splitlines())},
        runner=RealRunner(),
    )
    assert isinstance(result, InstrumentedMeasurement)
    assert result.counts == {"lines": 1}
    assert result.instrument == "fake-instrument"
    assert not hasattr(result, "wall")
    assert not hasattr(result, "cpu_s")


def test_counts_need_one_run_where_timings_need_five(tmp_path: Path) -> None:
    """A count is the same integer every run for the same input. Repeating it
    would buy nothing and cost the run time, so the asymmetry is in the shape of
    the two functions rather than a flag on one."""
    result = measure_under(
        "fake-instrument",
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        harvest=lambda _: {"n": 1},
        runner=RealRunner(),
    )
    assert result.repeats == 1


def test_an_instrumented_run_that_fails_raises_rather_than_reporting_counts(
    tmp_path: Path,
) -> None:
    with pytest.raises(WorkloadFailedError):
        measure_under(
            "fake-instrument",
            [sys.executable, "-c", "raise SystemExit(4)"],
            cwd=tmp_path,
            harvest=lambda _: {"n": 0},
            runner=RealRunner(),
        )


# ------------------------------------------------------------------ S-18.3


def test_the_leaf_of_each_stack_is_the_site_the_sample_is_charged_to() -> None:
    document = speedscope([APP, MODEL], [[0, 1], [0, 1], [0]])
    sites, counts = sites_from_speedscope(document)
    assert counts["samples"] == 3
    charged = {(s.file, s.symbol): s.self_samples for s in sites}
    assert charged == {("/app/models.py", "books"): 2, ("/app/views.py", "handler"): 1}


def test_interpreter_scaffolding_is_stripped_without_being_asked() -> None:
    """`<frozen importlib._bootstrap>` appears in every profile of every program
    and is never the answer."""
    document = speedscope([FROZEN, APP], [[0, 1], [0, 1]])
    sites, _ = sites_from_speedscope(document)
    assert [s.file for s in sites] == ["/app/views.py"]


def test_a_sample_with_nothing_left_after_stripping_is_recorded_not_dropped() -> None:
    """A C extension the profiler cannot see into is a gap in the answer, and a
    gap has to be visible. Dropping the sample would quietly inflate every
    remaining site's share."""
    document = speedscope([FROZEN], [[0], [0], [0]])
    sites, counts = sites_from_speedscope(document)
    assert sites == ()
    assert counts["unattributable_samples"] == 3
    assert counts["attributed_samples"] == 0


def test_shares_are_of_what_could_be_attributed_not_of_everything() -> None:
    document = speedscope([FROZEN, APP], [[0], [1], [1]])
    sites, counts = sites_from_speedscope(document)
    assert counts["attributed_samples"] == 2
    assert sites[0].self_share == pytest.approx(1.0)


def test_frameworks_are_declared_by_the_caller_never_guessed() -> None:
    """The harness has no idea what a framework is, and acquiring that knowledge
    is exactly what v3 exists to remove."""
    document = speedscope([APP, VENDOR], [[0, 1], [0, 1]])
    unstripped, _ = sites_from_speedscope(document)
    assert unstripped[0].file == "/site-packages/orm/query.py"

    stripped, _ = sites_from_speedscope(document, strip_prefixes=["/site-packages/"])
    assert stripped[0].file == "/app/views.py"


def test_identical_stacks_collapse_to_one_site_with_the_path_that_reached_it() -> None:
    """Two hundred identical stacks are one finding, not two hundred."""
    document = speedscope([APP, RENDER, MODEL], [[0, 2]] * 200)
    sites, _ = sites_from_speedscope(document)
    assert len(sites) == 1
    assert sites[0].self_samples == 200
    assert sites[0].call_path == ("/app/views.py:41",)


def test_the_call_path_stops_where_the_stacks_stop_agreeing() -> None:
    """Reached from two different places, the shared prefix is the answer -- that
    is where one call path became many."""
    document = speedscope([APP, RENDER, MODEL], [[0, 1, 2], [0, 2]])
    sites, _ = sites_from_speedscope(document)
    site = next(s for s in sites if s.symbol == "books")
    assert site.call_path == ("/app/views.py:41",)


def test_the_reported_line_is_the_one_most_samples_landed_on() -> None:
    document = speedscope(
        [
            {"name": "books", "file": "/app/models.py", "line": 112},
            {"name": "books", "file": "/app/models.py", "line": 118},
        ],
        [[0], [0], [0], [1]],
    )
    sites, _ = sites_from_speedscope(document)
    assert len(sites) == 1
    assert sites[0].line == 112
    assert sites[0].self_samples == 4


def test_top_limits_how_many_sites_come_back() -> None:
    frames = [{"name": f"f{i}", "file": f"/app/{i}.py", "line": i} for i in range(30)]
    sites, _ = sites_from_speedscope(speedscope(frames, [[i] for i in range(30)]), top=5)
    assert len(sites) == 5


def test_an_empty_profile_yields_no_sites_and_says_so() -> None:
    sites, counts = sites_from_speedscope({})
    assert sites == ()
    assert counts == {"samples": 0, "attributed_samples": 0, "unattributable_samples": 0}


def test_a_profile_measurement_is_an_instrumented_one_and_carries_no_timing() -> None:
    measurement = ProfileMeasurement(
        measurement_id="m-1",
        command=("py-spy",),
        repeats=1,
        output_digest="0" * 64,
        output_bytes=0,
        instrument="py-spy",
        counts={"samples": 10},
        sites=(
            Site(
                file="/app/models.py",
                line=112,
                symbol="books",
                self_samples=10,
                self_share=1.0,
                call_path=(),
            ),
        ),
    )
    assert "wall" not in ProfileMeasurement.model_fields
    assert measurement.sites[0].symbol == "books"


class FlakyInstrumentRunner:
    """Fails the first time without writing a profile, then behaves."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        self.calls += 1
        if self.calls <= self.failures:
            return ChildResult(
                returncode=1,
                stdout="",
                stderr="Error: No child process (os error 10)",
                usage=Usage(cpu_s=0.0, peak_rss_bytes=0, read_blocks=0, write_blocks=0),
            )
        target = next(a for a in command if a.endswith("coldfix-profile.json"))
        Path(target).write_text(
            json.dumps(
                {
                    "shared": {"frames": [APP]},
                    "profiles": [{"samples": [[0]], "weights": [0.01]}],
                }
            ),
            encoding="utf-8",
        )
        return ChildResult(
            returncode=0,
            stdout="",
            stderr="",
            usage=Usage(cpu_s=1.0, peak_rss_bytes=0, read_blocks=0, write_blocks=0),
        )


def test_an_instrument_that_loses_its_child_is_retried_not_blamed_on_the_subject(
    tmp_path: Path,
) -> None:
    """Observed across the corpus: py-spy intermittently exits 1 with "No child
    process" when profiling runs follow each other closely. The subject was
    fine, and a retry costs no model tokens."""
    runner = FlakyInstrumentRunner(failures=1)
    result = profile([sys.executable, "-c", "pass"], cwd=tmp_path, runner=runner)
    assert runner.calls == 2
    assert result.sites[0].file == "/app/views.py"


def test_an_instrument_that_never_starts_is_reported_as_the_instrument_failing(
    tmp_path: Path,
) -> None:
    """`WorkloadFailedError` would send somebody to fix a program that has
    nothing wrong with it."""
    with pytest.raises(InstrumentFailedError, match="py-spy"):
        profile([sys.executable, "-c", "pass"], cwd=tmp_path, runner=FlakyInstrumentRunner(9))


def test_a_subject_that_really_fails_under_the_profiler_still_blames_the_subject(
    tmp_path: Path,
) -> None:
    """The distinction is whether a profile was produced. If py-spy managed to
    profile the run, a non-zero exit belongs to the program."""

    class ProfiledButFailing:
        def run(
            self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None
        ) -> ChildResult:
            target = next(a for a in command if a.endswith("coldfix-profile.json"))
            Path(target).write_text('{"shared":{"frames":[]},"profiles":[]}', encoding="utf-8")
            return ChildResult(
                returncode=3,
                stdout="",
                stderr="the subject raised",
                usage=Usage(cpu_s=1.0, peak_rss_bytes=0, read_blocks=0, write_blocks=0),
            )

    with pytest.raises(WorkloadFailedError):
        profile([sys.executable, "-c", "pass"], cwd=tmp_path, runner=ProfiledButFailing())

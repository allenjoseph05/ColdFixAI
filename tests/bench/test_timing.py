"""`time()` returns every sample it took, and lies about none of them.

The acceptance criteria here are mostly prohibitions, so most of these tests
try to catch the function doing something helpful. The one that matters is
`test_nothing_is_discarded_when_the_first_sample_is_an_outlier`: it makes the
first call dramatically slower than the rest — the exact shape a warmup discard
is designed to remove — and asserts that it comes back.

That failure mode is silent by construction. A function that drops the first
sample returns a tidier distribution, a smaller standard deviation, and a
confident answer, with nothing anywhere reporting that samples went missing.
"""

from __future__ import annotations

import os
import sys
import time as stdlib_time
from collections.abc import Callable

import pytest

from coldfix.bench.execute import execute
from coldfix.bench.timing import ProcessState, TimingError, TimingRun, time

PY = sys.executable

# Bound to a name so the failure test can assert the original exception survived
# intact, rather than trusting that a wrapper preserved it.
WORKLOAD_FAILURE = "workload broke"


def sleeper(seconds: float) -> Callable[[], None]:
    """A callable whose cost is known in advance and is mostly not CPU."""

    def call() -> None:
        stdlib_time.sleep(seconds)

    return call


# ------------------------------------------------------------------ reporting


def test_returns_one_duration_per_repetition() -> None:
    run = time(sleeper(0.02), 5)

    assert isinstance(run, TimingRun)
    assert len(run) == 5
    assert len(run.durations) == 5
    assert [sample.index for sample in run.samples] == [0, 1, 2, 3, 4]


def test_durations_are_wall_clock_not_cpu_time() -> None:
    """`perf_counter`, per the AC — which counts time spent blocked.

    A CPU clock reports this sleep as roughly free. So does `process_time`,
    which is the plausible wrong choice: a workload waiting on a database is
    doing precisely what we are here to measure, and calling that wait free
    would make every I/O-bound finding invisible.
    """
    run = time(sleeper(0.2), 2)

    # A hair under the sleep, for clock granularity — `sleep` guarantees the
    # duration against its own clock, not against `perf_counter`.
    assert all(duration >= 0.19 for duration in run.durations)
    assert all(duration < 5 for duration in run.durations)


def test_a_single_repetition_is_allowed() -> None:
    run = time(sleeper(0.01), 1)

    assert len(run) == 1
    assert run.samples[0].process_state is ProcessState.FRESH


@pytest.mark.parametrize("repetitions", [0, -1])
def test_fewer_than_one_repetition_is_rejected(repetitions: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        time(sleeper(0.0), repetitions)


# ------------------------------------------------- the prohibition that matters


def test_nothing_is_discarded_when_the_first_sample_is_an_outlier() -> None:
    """The first call is 20x the rest. It must still be in the output.

    This is the adversarial test for the AC "discards nothing automatically".
    It builds the case a warmup discard exists to remove, and asserts the
    sample survives — so an implementation that quietly drops the first N
    fails here rather than returning a cleaner-looking distribution.

    Barrett et al. found at most 43.5% of VM/benchmark pairs reach a steady
    state, so a slow first sample is as likely to be the real behaviour of the
    workload as it is to be warmup. Deciding that here would be guessing.
    """
    calls = 0

    def uneven() -> None:
        nonlocal calls
        calls += 1
        stdlib_time.sleep(0.2 if calls == 1 else 0.01)

    run = time(uneven, 5)

    assert calls == 5
    assert len(run.durations) == 5, "samples went missing"
    assert run.durations[0] == max(run.durations), "the slow first sample was dropped"
    assert run.durations[0] >= 0.19
    # And the cheap samples are all still distinguishable from it, so the run
    # really did contain both populations rather than five slow calls.
    assert max(run.durations[1:]) < 0.15


def test_samples_are_returned_in_the_order_they_were_taken() -> None:
    """Sorting would be the other way to lose the shape of a run.

    A rising or falling trend across a run is a finding — it is how a leak, a
    growing cache, or a degrading index shows up. Order carries that.
    """
    delays = [0.05, 0.01, 0.03, 0.01]
    remaining = list(delays)

    def descending() -> None:
        stdlib_time.sleep(remaining.pop(0))

    run = time(descending, len(delays))

    slowest = max(range(len(delays)), key=lambda i: run.durations[i])
    assert slowest == 0
    assert run.durations[1] < run.durations[2]


# ---------------------------------------------------------- process provenance


def test_in_process_samples_are_fresh_then_reused() -> None:
    """Only the first sample of a run gets a process no earlier sample touched."""
    run = time(sleeper(0.01), 4)

    states = [sample.process_state for sample in run.samples]
    assert states == [
        ProcessState.FRESH,
        ProcessState.REUSED,
        ProcessState.REUSED,
        ProcessState.REUSED,
    ]


def test_fresh_process_per_sample_is_true_of_the_processes_that_ran() -> None:
    """The flag's claim is checked against reality, not just echoed back.

    `time()` cannot observe that `fn` spawned a process — `fn` is opaque from
    the inside. So this test does what the docstring tells a caller to do,
    composing with `execute()`, and then verifies the thing the label asserts:
    every sample ran somewhere no earlier sample had run.
    """
    pids: list[int] = []

    def in_a_new_interpreter() -> None:
        result = execute([PY, "-c", "import os; print(os.getpid())"], timeout=60)
        pids.append(int(result.stdout.strip()))

    run = time(in_a_new_interpreter, 3, fresh_process_per_sample=True)

    assert all(sample.process_state is ProcessState.FRESH for sample in run.samples)
    assert len(set(pids)) == 3, f"samples shared a process: {pids}"
    assert os.getpid() not in pids


# ------------------------------------------------------------------- failure


def test_a_raising_callable_stops_the_run_and_keeps_what_it_had() -> None:
    """A shorter list returned quietly would later read as a complete run."""
    calls = 0

    def fails_on_the_third() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError(WORKLOAD_FAILURE)
        stdlib_time.sleep(0.01)

    with pytest.raises(TimingError) as caught:
        time(fails_on_the_third, 10)

    assert calls == 3, "the run continued past the failure"
    assert caught.value.index == 2
    assert caught.value.repetitions == 10
    assert len(caught.value.completed) == 2
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(caught.value.__cause__) == WORKLOAD_FAILURE

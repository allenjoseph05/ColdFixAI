"""Call something repeatedly and hand back every duration. Discards nothing.

The second operation of the lab bench. Its acceptance criteria are almost
entirely about what it must *not* do: no warmup discard, no batching, no
sample filtering, no tuning of the runtime to make numbers look calmer. Every
one of those is standard practice in benchmarking libraries, and every one is a
decision made on the caller's behalf about which samples count.

The load-bearing one is warmup. Barrett et al. (2017) measured VM/benchmark
pairs and found at most 43.5% reached a steady state at all — so "discard the
first N" is an assumption that is wrong more often than it is right, and when it
is wrong it deletes exactly the samples that show it. This function therefore
returns every sample it took, labelled with whether the process that ran it had
already run an earlier sample, and leaves the analysis to decide.

The module is named `timing` rather than `time` because the function is named
`time`. A module named `time.py` containing `def time` cannot import the standard
library's `time` under its own name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter


class ProcessState(StrEnum):
    """Whether a sample shared a process with an earlier sample of the same run.

    Scoped to one run, on purpose. `FRESH` means *no earlier sample in this run
    executed in this process*. It does not claim the interpreter was newly
    started, that no unrelated code had warmed a cache, or that the machine was
    quiet — none of which this function can observe. Overclaiming there would be
    worse than saying nothing, because the analysis downstream would trust it.
    """

    FRESH = "fresh"
    REUSED = "reused"


@dataclass(frozen=True)
class Sample:
    """One measured call. A duration and where it happened, no judgement."""

    index: int
    seconds: float
    process_state: ProcessState


@dataclass(frozen=True)
class TimingRun:
    """Every sample taken, in the order taken.

    There is no `mean` or `stdev` here. Summarising is `stats()` (S-1.5), and
    keeping it out of this type is what stops a caller reaching for an average
    without first looking at whether the samples were drawn from one population.
    """

    samples: tuple[Sample, ...]

    @property
    def durations(self) -> tuple[float, ...]:
        """The seconds column alone, in order, for handing to `stats()`."""
        return tuple(sample.seconds for sample in self.samples)

    def __len__(self) -> int:
        return len(self.samples)


class TimingError(Exception):
    """The callable raised, so the run is incomplete.

    Carries the samples taken before the failure, for the same reason
    `ExecutionTimeoutError` carries partial output: the point at which a
    workload starts failing is frequently the finding. A run that dies on
    sample 40 of 50 says something a run that dies on sample 1 does not.

    The original exception is the `__cause__`. Nothing is swallowed — a failing
    workload stops the measurement rather than quietly producing a shorter list
    that later looks like a complete one.
    """

    def __init__(self, index: int, repetitions: int, completed: tuple[Sample, ...]) -> None:
        self.index = index
        self.repetitions = repetitions
        self.completed = completed
        super().__init__(
            f"callable raised on sample {index} of {repetitions}; "
            f"{len(completed)} sample(s) completed before it"
        )


def time(
    fn: Callable[[], object],
    repetitions: int,
    *,
    fresh_process_per_sample: bool = False,
) -> TimingRun:
    """Call `fn` `repetitions` times and return the duration of each call.

    Durations come from `perf_counter` — wall clock, including time spent
    blocked. A workload that waits on a database is doing exactly what we are
    trying to measure, and a CPU-time clock would report that wait as free.

    Set `fresh_process_per_sample` when `fn` starts a new process for the work
    it measures, typically by calling `execute()`. This function cannot detect
    that: from here, `fn` is an opaque callable. The flag is the caller
    declaring what it built, and a wrong declaration mislabels every sample,
    which is why the default is the conservative reading — samples after the
    first share this process.

        # each sample in its own interpreter
        time(lambda: execute([sys.executable, "bench.py"], timeout=60), 20,
             fresh_process_per_sample=True)

    Three things this deliberately does not do:

    **No warmup discard.** See the module docstring. All `repetitions` samples
    are returned.

    **No batching.** One sample is one call. `timeit` runs an inner loop and
    divides, which trades away the per-sample variance — the thing a rank test
    (S-1.5) needs, and the thing that reveals a bimodal distribution.

    **No garbage-collection control.** `timeit` disables the collector while
    timing. That would make a patch which increases allocation pressure appear
    free, and allocation pressure is a defect class this project exists to
    find.

    The value `fn` returns is dropped as soon as the call returns. Retaining
    `repetitions` results would hold memory that the untimed program would not,
    changing collection behaviour partway through the run and charging the cost
    to the later samples.

    Raises:
        ValueError: `repetitions` is below 1.
        TimingError: `fn` raised. The samples taken before it are on the error.
    """
    if repetitions < 1:
        message = f"repetitions must be at least 1, got {repetitions}"
        raise ValueError(message)

    samples: list[Sample] = []
    for index in range(repetitions):
        state = (
            ProcessState.FRESH if fresh_process_per_sample or index == 0 else ProcessState.REUSED
        )

        started = perf_counter()
        try:
            fn()
        except Exception as error:
            raise TimingError(index, repetitions, tuple(samples)) from error
        elapsed = perf_counter() - started

        samples.append(Sample(index=index, seconds=elapsed, process_state=state))

    return TimingRun(samples=tuple(samples))

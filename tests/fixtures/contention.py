"""Put the machine under deliberate load, so a fragile test fails on purpose.

S-0.9 AC 3: *a test that would flake is failed by a deliberate load rather than
discovered by chance.* Until this existed, the only way to learn that a timing
test was load-sensitive was to watch it fail in a full subset run and then re-run
it standalone to find out whether the change in hand had broken it — three
seven-minute runs to distinguish a flake from a regression, paid on every story.

**Processes, not threads, and the first attempt at this got it wrong.** Spinning
Python threads all contend for one GIL, so thirty-two of them consume roughly one
core: measured against the four known-fragile tests, that load produced one
failure in sixty runs. Separate processes actually occupy cores. At twice the core
count the same four tests failed 0/5, 2/5, 4/5 and 5/5 — and a sweep of the whole
primitives suite turned up **thirteen** load-sensitive tests where three months of
chance sightings had surfaced four.

**A full heap is not the mechanism, which was worth ruling out.** The flakes
appear in the long single-process subset run and not standalone, so accumulated
objects and a generational collection landing inside a timed measurement was the
obvious suspect — `bench.timing` deliberately does no garbage-collection control.
Carrying six thousand live blocks through a session produced one failure in
twenty. Oversubscribed cores produced eleven in twenty on the same tests.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Iterator
from contextlib import contextmanager

DEFAULT_OVERSUBSCRIPTION = 2
"""Spinners per core, **for hunting rather than for gating.**

At one per core the four known-fragile tests still mostly passed; at two per core
every one of them failed at least once. That makes two the right setting for
finding load-sensitive tests — and the wrong one for deciding which tests are
allowed in the gate.

**At 2× the tail does not terminate.** Seven sweeps each surfaced roughly one
more test somewhere, at one failure in three to five runs, and the last one found
was a `docker ps` call rather than a timing assertion: twice the core count is
harsh enough that anything with a subprocess timeout eventually fails. The
acceptance bar is therefore **one spinner per core** — a fully busy machine, which
is what a parallel build produces and what every real sighting happened under.

The list of quarantined tests is a reading of this number. Changing it changes
the list, which is why the harness rather than the list is the thing worth
keeping."""


def _burn() -> None:  # pragma: no cover - runs in a child process
    while True:
        sum(index * index for index in range(5000))


def spinners(count: int) -> int:
    """How many processes to start. Exposed so a test can say what it asked for."""
    return max(1, count)


@contextmanager
def under_load(processes: int | None = None) -> Iterator[int]:
    """Saturate the CPU for the duration of the block, and yield how hard.

    **Every process is terminated on the way out, including on an exception.**
    A leaked spinner does not stop — it is an infinite loop — and one escaping
    into the rest of a suite run would turn this from an instrument for finding
    load-sensitive tests into a machine for creating them.
    """
    wanted = spinners(processes or (os.cpu_count() or 4) * DEFAULT_OVERSUBSCRIPTION)
    started = [multiprocessing.Process(target=_burn, daemon=True) for _ in range(wanted)]
    for worker in started:
        worker.start()
    try:
        # **What is yielded is what is alive, not what was asked for.** One sweep
        # came back clean because it had been launched from stdin, where Windows
        # cannot re-import the main module to spawn a child — every spinner died
        # instantly with `OSError: Errno 22` and three runs measured a quiet
        # machine. A load generator that silently generates no load turns every
        # sweep into a false negative.
        yield sum(1 for worker in started if worker.is_alive())
    finally:
        for worker in started:
            worker.terminate()
        for worker in started:
            worker.join(timeout=10)

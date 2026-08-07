"""What a candidate cost that nobody thought to ask about.

Epic 3, S-3.8. `08-audit.md` F10 is the whole reason this exists: **guard
counters are a denylist**. A guard pair catches the trade somebody predicted —
queries against rows returned, because someone knew that halving one can explode
the other — and catches nothing else. The trades that matter are the ones nobody
listed, and a denylist cannot have them in it by construction.

So the pair check stays and a second check is added beside it, shaped the other
way round: **every candidate is measured against a fixed envelope of global
resources, and any of them moving outside tolerance is flagged whether or not
that trade was predicted.** Peak memory, CPU, wall clock, bytes written, open
file descriptors, processes. A patch that halves the query count by holding the
whole result set in memory is not a query-and-rows trade, is not on anyone's
list, and shows up here as peak memory outside tolerance.

**Increases flag; decreases never do.** The point of a candidate is that
something goes down, so a one-sided check is not a simplification — a two-sided
one would flag every successful patch for the improvement it was written to
make.

**Unavailable is not zero, and unavailable is not "within tolerance".** Three of
these metrics need `resource` or `/proc`, which exist in the Linux sandbox where
every real measurement is taken and not on the Windows host where this is
developed. A metric that could not be read is reported as unmeasured and named,
because a guard check that quietly passed on the metrics it could not see would
be worth less than no guard check — it would carry the same reassurance with none
of the coverage. Two portable siblings — allocated blocks and thread count —
exist so that the memory and process questions have *some* answer everywhere,
not to stand in for the real ones.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

if sys.platform == "win32":

    def _rusage() -> object | None:
        return None

else:
    import resource

    def _rusage() -> object | None:
        return resource.getrusage(resource.RUSAGE_SELF)


# `ru_oublock` counts 512-byte blocks, which is the unit the kernel reports
# rather than a choice made here.
_BLOCK_BYTES = 512

# Linux keeps both of these in `/proc`. Read at the envelope's two sample points
# and nowhere else, because a directory scan per event would be an instrument
# that costs more than what it measures.
_FD_DIRECTORY = "/proc/self/fd"
_PROC = "/proc"

WALL_SECONDS = "wall_seconds"
CPU_SECONDS = "cpu_seconds"
PEAK_RSS_BYTES = "peak_rss_bytes"
ALLOCATED_BLOCKS = "allocated_blocks"
BYTES_WRITTEN = "bytes_written"
OPEN_FILE_DESCRIPTORS = "open_file_descriptors"
THREAD_COUNT = "thread_count"
PROCESS_COUNT = "process_count"

# How much a resource may rise before it is flagged. Deliberately generous:
# this is a check for *explosions*, and a tolerance tight enough to catch a few
# percent would flag ordinary run-to-run variation on every candidate and be
# switched off within a week. Wall clock and CPU are tighter because a candidate
# that made either materially worse has failed at the thing it was for.
DEFAULT_TOLERANCE = 0.25
TIMING_TOLERANCE = 0.10

DEFAULT_TOLERANCES: Mapping[str, float] = {
    WALL_SECONDS: TIMING_TOLERANCE,
    CPU_SECONDS: TIMING_TOLERANCE,
    PEAK_RSS_BYTES: DEFAULT_TOLERANCE,
    ALLOCATED_BLOCKS: DEFAULT_TOLERANCE,
    BYTES_WRITTEN: DEFAULT_TOLERANCE,
    OPEN_FILE_DESCRIPTORS: DEFAULT_TOLERANCE,
    THREAD_COUNT: DEFAULT_TOLERANCE,
    PROCESS_COUNT: DEFAULT_TOLERANCE,
}

# **A ratio alone flags noise, and a tolerance loose enough not to would miss
# real trades.** Both tests have to pass: a rise must exceed the tolerance *and*
# be bigger than this, or it is not evidence of anything.
#
# The timing floor is S-0.4's measured number — roughly 20ms, about 6% of a 350ms
# endpoint — so any wall-clock difference smaller than that is inside the noise
# whatever percentage it works out to. This was found by the check failing on its
# own control: two identical runs, 2.4ms and 2.7ms, an 11% rise past a 10%
# tolerance, and nothing whatsoever had happened.
#
# The count floors exist for the same reason at the other end of the scale: two
# file descriptors becoming three is a 50% rise and is nothing at all.
_ABSOLUTE_FLOOR: Mapping[str, float] = {
    WALL_SECONDS: 0.020,
    CPU_SECONDS: 0.020,
    # A thousand small objects retained is ordinary bookkeeping. The trade this
    # metric exists to catch — a result set held instead of streamed — retains
    # tens of thousands.
    ALLOCATED_BLOCKS: 1000,
    OPEN_FILE_DESCRIPTORS: 4,
    THREAD_COUNT: 4,
    PROCESS_COUNT: 4,
}


class Availability(StrEnum):
    """Whether a metric could be read here, and if not, what would be needed."""

    MEASURED = "measured"
    NEEDS_RUSAGE = "needs getrusage, which this platform does not have"
    NEEDS_PROC = "needs /proc, which this platform does not have"


@dataclass(frozen=True)
class EnvelopeMetric:
    """One global resource, what it means, and why it is worth watching."""

    name: str
    unit: str
    why: str
    portable: bool = True
    """Whether it can be read on every platform, or only in the sandbox."""


ENVELOPE: Mapping[str, EnvelopeMetric] = {
    metric.name: metric
    for metric in (
        EnvelopeMetric(
            WALL_SECONDS, "seconds", "a candidate that takes longer has failed at its job"
        ),
        EnvelopeMetric(CPU_SECONDS, "seconds", "work moved onto the CPU rather than removed"),
        EnvelopeMetric(
            PEAK_RSS_BYTES,
            "bytes",
            "the classic unpredicted trade: fewer queries because the whole result set is "
            "now held in memory",
            portable=False,
        ),
        EnvelopeMetric(
            ALLOCATED_BLOCKS,
            "blocks retained",
            "memory the run finished still holding, which is what a cache is and what peak "
            "RSS cannot separate from memory that was borrowed and given back",
        ),
        EnvelopeMetric(
            BYTES_WRITTEN,
            "bytes",
            "a cache that spilled to disk, or logging added on a hot path",
            portable=False,
        ),
        EnvelopeMetric(
            OPEN_FILE_DESCRIPTORS,
            "descriptors",
            "connection reuse that stopped closing what it opened",
            portable=False,
        ),
        EnvelopeMetric(
            THREAD_COUNT, "threads", "concurrency introduced to hide a cost rather than remove it"
        ),
        EnvelopeMetric(
            PROCESS_COUNT,
            "processes",
            "work pushed into subprocesses, which leaves this process's metrics looking better",
            portable=False,
        ),
    )
}


@dataclass
class EnvelopeSample:
    """Every global resource at one instant. `None` where the platform cannot say.

    Mutable and filled when its block ends, like S-1.3's `Count` and S-3.7's
    profile: the deltas that matter are between two of these, and there is
    nothing to read until the second one is taken.
    """

    metrics: dict[str, float | None] = field(default_factory=dict)
    unavailable: dict[str, Availability] = field(default_factory=dict)

    def __getitem__(self, name: str) -> float | None:
        return self.metrics.get(name)


@dataclass(frozen=True)
class Breach:
    """One resource that rose further than it was allowed to."""

    metric: str
    before: float
    after: float
    tolerance: float

    @property
    def ratio(self) -> float:
        """How many times larger. Infinite where it rose from nothing."""
        if self.before == 0:
            return float("inf")
        return self.after / self.before

    def __str__(self) -> str:
        entry = ENVELOPE[self.metric]
        scale = f"{self.ratio:.1f}x" if self.before else "from nothing"
        return (
            f"{self.metric} rose {scale} ({self.before:g} to {self.after:g} {entry.unit}), "
            f"past the {self.tolerance:.0%} allowed — {entry.why}"
        )


@dataclass(frozen=True)
class GuardReport:
    """Whether a candidate cost something it was not supposed to.

    Carries what could *not* be checked as well as what failed, because a guard
    report that names only its breaches reads identically whether it checked
    eight metrics or two.
    """

    breaches: tuple[Breach, ...]
    checked: tuple[str, ...]
    unmeasured: Mapping[str, Availability]

    @property
    def flagged(self) -> bool:
        return bool(self.breaches)

    def explanation(self) -> str:
        lines = []
        if self.flagged:
            lines.append(
                "This candidate improved what it was aimed at and made something else worse. "
                "The envelope is checked whether or not a trade was predicted, which is the "
                "point of it — a guard pair only catches trades somebody thought of."
            )
            lines += [f"  - {breach}" for breach in self.breaches]
        else:
            lines.append(
                f"No resource in the envelope rose materially ({len(self.checked)} checked)."
            )

        if self.unmeasured:
            unread = ", ".join(
                f"{name} ({why.value})" for name, why in sorted(self.unmeasured.items())
            )
            lines.append(
                f"Not checked here: {unread}. These are measurable in the Linux sandbox and "
                "were not measurable on this host, so this report covers less than a sandbox "
                "run would."
            )
        return "\n".join(lines)


@contextmanager
def envelope() -> Iterator[EnvelopeSample]:
    """Measure the global resource envelope across a block.

    Yields a sample that holds the *deltas* once the block ends — peak memory and
    descriptor counts are levels rather than counters, so what is recorded is the
    level at the end for those and the difference for the ones that accumulate.
    """
    sample = EnvelopeSample()
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    before = _read()
    try:
        yield sample
    finally:
        after = _read()
        sample.metrics = {
            WALL_SECONDS: time.perf_counter() - started_wall,
            CPU_SECONDS: time.process_time() - started_cpu,
            # Peaks and levels are read at the end; only written bytes
            # accumulate and so are differenced.
            PEAK_RSS_BYTES: after[PEAK_RSS_BYTES],
            # A *difference*, unlike peak RSS, and the distinction is the whole
            # usefulness of it. `sys.getallocatedblocks()` is a level for the
            # entire interpreter, so its ratio is diluted by everything else the
            # process happens to be holding — measured under pytest, a run
            # retaining 24,000 blocks against a 200,000-block interpreter reads
            # as a 12% rise and passes. Differenced, it is what this block
            # retained, which is exactly what a cache is.
            ALLOCATED_BLOCKS: _difference(before[ALLOCATED_BLOCKS], after[ALLOCATED_BLOCKS]),
            BYTES_WRITTEN: _difference(before[BYTES_WRITTEN], after[BYTES_WRITTEN]),
            OPEN_FILE_DESCRIPTORS: after[OPEN_FILE_DESCRIPTORS],
            THREAD_COUNT: after[THREAD_COUNT],
            PROCESS_COUNT: after[PROCESS_COUNT],
        }
        sample.unavailable = {
            name: _why_unavailable(name) for name, value in sample.metrics.items() if value is None
        }


def compare(
    baseline: EnvelopeSample,
    candidate: EnvelopeSample,
    *,
    tolerances: Mapping[str, float] = DEFAULT_TOLERANCES,
) -> GuardReport:
    """Flag every resource the candidate raised beyond tolerance.

    **Every metric in the envelope, not a list of expected trades.** That is the
    whole difference between this and a guard pair: the trades worth catching are
    the ones nobody predicted, so nothing here consults a prediction.

    Only rises are flagged. A candidate exists to make something smaller, and a
    two-sided check would flag every successful patch for the improvement it was
    written to make.
    """
    breaches: list[Breach] = []
    checked: list[str] = []
    unmeasured: dict[str, Availability] = {}

    for name in ENVELOPE:
        before = baseline[name]
        after = candidate[name]
        if before is None or after is None:
            unmeasured[name] = _why_unavailable(name)
            continue

        checked.append(name)
        tolerance = tolerances.get(name, DEFAULT_TOLERANCE)
        if _exceeds(name, before, after, tolerance):
            breaches.append(Breach(metric=name, before=before, after=after, tolerance=tolerance))

    return GuardReport(
        breaches=tuple(breaches),
        checked=tuple(checked),
        unmeasured=unmeasured,
    )


def _exceeds(name: str, before: float, after: float, tolerance: float) -> bool:
    """Whether a rise is both proportionally and absolutely worth reporting.

    Both, deliberately. A ratio on its own flags run-to-run noise on anything
    small, and an absolute threshold on its own misses a tenfold rise in
    something that was tiny to begin with.
    """
    rise = after - before
    if rise <= 0:
        return False
    if rise <= _ABSOLUTE_FLOOR.get(name, 0.0):
        return False
    if before == 0:
        return True
    return rise / before > tolerance


def _difference(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return after - before


def _why_unavailable(name: str) -> Availability:
    if ENVELOPE[name].portable:  # pragma: no cover - a portable metric always reads
        return Availability.MEASURED
    return Availability.NEEDS_PROC if name in _PROC_METRICS else Availability.NEEDS_RUSAGE


_PROC_METRICS = frozenset({OPEN_FILE_DESCRIPTORS, PROCESS_COUNT})


def _read() -> dict[str, float | None]:
    """Every level this platform can report, right now."""
    usage = _rusage()
    return {
        PEAK_RSS_BYTES: _peak_rss(usage),
        ALLOCATED_BLOCKS: float(sys.getallocatedblocks()),
        BYTES_WRITTEN: (
            None if usage is None else float(getattr(usage, "ru_oublock", 0) * _BLOCK_BYTES)
        ),
        OPEN_FILE_DESCRIPTORS: _count_entries(_FD_DIRECTORY),
        THREAD_COUNT: float(threading.active_count()),
        PROCESS_COUNT: _child_processes(),
    }


def _peak_rss(usage: object | None) -> float | None:
    """Peak resident set, in bytes.

    `ru_maxrss` is kilobytes on Linux and bytes on macOS, which is a documented
    difference between platforms rather than an ambiguity — converting on the
    wrong one would report a thousand-fold memory explosion on every candidate.
    """
    if usage is None:
        return None
    raw = float(getattr(usage, "ru_maxrss", 0))
    return raw if sys.platform == "darwin" else raw * 1024


def _count_entries(directory: str) -> float | None:
    try:
        return float(sum(1 for _ in Path(directory).iterdir()))
    except OSError:
        return None


def _child_processes() -> float | None:
    """Direct children of this process, from `/proc`.

    One directory scan and a small read per numeric entry. Affordable because it
    happens twice per candidate rather than per event, and there is no
    dependency-free way to ask the question otherwise.
    """
    try:
        entries = [entry for entry in Path(_PROC).iterdir() if entry.name.isdigit()]
    except OSError:
        return None

    ours = str(os.getpid())
    children = 0
    for entry in entries:
        try:
            # The command name can contain spaces and parentheses, so the fields
            # after it are found by splitting on the last `)` rather than by
            # counting columns from the left.
            fields = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[-1].split()
        except OSError:
            # The process exited between the listing and the read, which is
            # ordinary rather than exceptional on a live system.
            continue
        if len(fields) > 1 and fields[1] == ours:
            children += 1
    return float(children)

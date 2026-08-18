"""Whether it computed or waited, which are the same number and different fixes.

Epic 3, S-3.7. `01-primitives.md` §12 states the gap this closes: without off-CPU
instrumentation *the entire saturation column of the USE Method is
unmeasurable*, and the story's note says what that costs in practice — an
ablation tells you a component is expensive and never whether it computed or
waited, and those have nothing in common as fixes. A component burning CPU wants
a better algorithm. A component waiting on a database wants an index, a batch, or
a different query. Telling them apart is one subtraction that nothing else in the
system was doing.

**Wall clock minus CPU time is the whole of the total measurement**, and it is
exact rather than sampled: `perf_counter` is elapsed time and `process_time` is
CPU time charged to this process, so the difference is time the process existed
and was not running. No profiler, no platform-specific tracing, no overhead worth
naming.

**Attribution by category is a different problem and this module is honest about
how far it gets.** Timing *which* blocking call waited means instrumenting the
call, and the real ones are not reachable from Python: `io.BufferedReader.read`,
`socket.socket.recv` and `_thread.LockType.acquire` are C types whose attributes
cannot be replaced. So attribution comes from two places, and neither is
complete:

- **What the adapter declares.** `blocking()` wraps a callable the adapter knows
  is a waiting point — a database cursor's `execute`, an HTTP client's `send` —
  and records the seconds it took under a category. This reuses S-3.6's
  magnitude-carrying record exactly: the events are the calls and the total is
  the seconds. It is the same construction, so a blocked-time counter is
  read by every primitive that reads any other counter.
- **What the operating system already counted.** On POSIX, `getrusage` reports
  voluntary context switches (the process gave up the CPU to wait for
  something), involuntary ones (**the scheduler took it away**, which is the only
  measurement of queueing available at this level), and block I/O operation
  counts. These are counts rather than seconds and they are the coarse answer
  where the fine one is unreachable.

**Where a signal is unavailable it is absent, not zero.** `resource` does not
exist on Windows, so the context-switch fields come back `None` there rather than
`0`. This is ADR 013's rule in its original form: zero involuntary context
switches is a publishable finding — *nothing was preempted, so it is not
queueing* — and a platform that cannot measure must not be able to produce it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import Any

from coldfix.bench.counting import Hook, HookError, Record

# `resource` is absent on Windows, which is a development platform here and
# never the one a measurement is taken on — the sandbox is Linux (S-2.1). Both
# the import and the reader that uses it sit under one platform check, so a type
# checker resolves the right one for the platform it is checking rather than
# taking the module on trust.
if sys.platform == "win32":

    def _rusage() -> Any | None:  # noqa: ANN401 - the shape is the platform's
        """No such measurement here. `None` is what says so; see `_switch_deltas`."""
        return None

else:
    import resource

    def _rusage() -> Any | None:  # noqa: ANN401 - `resource.struct_rusage`
        return resource.getrusage(resource.RUSAGE_SELF)


# Above this share of the wall clock, a run is called one thing rather than
# both. Chosen so that "mostly waiting" and "mostly computing" mean something a
# reader can act on, and anything in between is reported as mixed rather than
# rounded to whichever side is larger.
DOMINANT_SHARE = 0.7

# CPU time can exceed wall clock only by running on more than one core, and a
# little slack absorbs clock granularity rather than admitting real parallelism.
PARALLEL_TOLERANCE = 1.05

BLOCKED_DISK = "blocked.disk"
BLOCKED_NETWORK = "blocked.network"
BLOCKED_LOCK = "blocked.lock"

# Present only where the platform has `resource`. Never fabricated.
_HAS_RUSAGE = sys.platform != "win32"


class OffCpuCategory(StrEnum):
    """What a blocking call was waiting for.

    The four the story names. `SCHEDULER` has no hook and never will — being
    preempted is not a call anything can wrap — so it is measured from the
    operating system's involuntary context-switch count instead, and it is listed
    here so that the gap is visible rather than implied.
    """

    DISK = "disk"
    NETWORK = "network"
    LOCK = "lock"
    SCHEDULER = "scheduler"


_COUNTER_FOR = {
    OffCpuCategory.DISK: BLOCKED_DISK,
    OffCpuCategory.NETWORK: BLOCKED_NETWORK,
    OffCpuCategory.LOCK: BLOCKED_LOCK,
}

BLOCKED_COUNTERS = frozenset(_COUNTER_FOR.values())
"""Counters whose recorded amount is seconds rather than a count of things."""


class Boundedness(StrEnum):
    """What a run spent its time doing, said in one word.

    The distinction the story exists for. A `COMPUTE_BOUND` finding and a
    `BLOCKED` finding with the same wall clock lead to entirely different fixes,
    and before this they were the same number.
    """

    COMPUTE_BOUND = "compute-bound"
    BLOCKED = "blocked"
    MIXED = "mixed"
    PARALLEL = "parallel"
    """More CPU time than wall clock, so this decomposition does not apply.

    Not an error and not a rounding artefact: it means the work ran on more than
    one core, and *wall minus CPU* stops being time spent waiting. Reported as
    its own answer because a run that hits it needs the load primitive (S-3.12),
    not a subtraction.
    """


@dataclass
class OffCpuProfile:
    """How a run divided its elapsed time between working and waiting.

    Mutable and filled when the block ends, the same way S-1.3's `Count` is
    handed over before it has anything in it. Until the block ends there is no
    elapsed time to divide, so every number here is zero and the boundedness is
    `MIXED` — read it afterwards.
    """

    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0
    voluntary_switches: int | None = None
    """Times the process gave up the CPU to wait. `None` where unmeasurable.

    The blocking signal, and it does not say what was waited for. On POSIX it
    counts every wait — disk, network, lock — which is why it is coarse and why
    `blocking()` exists for the cases an adapter can name.
    """

    involuntary_switches: int | None = None
    """Times the scheduler took the CPU away. `None` where unmeasurable.

    The queueing signal, and the only one available without a tracer. A run with
    a large count spent time ready and not running, which is saturation in the
    USE Method's sense and is invisible to every other instrument here.
    """

    block_input_operations: int | None = None
    block_output_operations: int | None = None

    @property
    def blocked_seconds(self) -> float:
        """Elapsed time this process was not running.

        Negative when CPU time exceeds the wall clock, which is real information
        rather than a fault — see `Boundedness.PARALLEL` — so it is not clamped.
        A zero here would say *never waited*, which is a finding, and this must
        not be able to manufacture one.
        """
        return self.wall_seconds - self.cpu_seconds

    @property
    def boundedness(self) -> Boundedness:
        if self.wall_seconds <= 0:
            return Boundedness.MIXED
        if self.cpu_seconds > self.wall_seconds * PARALLEL_TOLERANCE:
            return Boundedness.PARALLEL
        if self.blocked_seconds / self.wall_seconds >= DOMINANT_SHARE:
            return Boundedness.BLOCKED
        if self.cpu_seconds / self.wall_seconds >= DOMINANT_SHARE:
            return Boundedness.COMPUTE_BOUND
        return Boundedness.MIXED

    @property
    def scheduler_signal_available(self) -> bool:
        """Whether queueing could be measured at all on this platform.

        Read this before reading `involuntary_switches`. A caller that treats
        `None` as zero has turned "we cannot see" into "there is none", which is
        the one substitution this project refuses everywhere it appears.
        """
        return self.involuntary_switches is not None

    def explanation(self) -> str:
        """What the numbers mean, for someone deciding what to fix."""
        share = self.blocked_seconds / self.wall_seconds if self.wall_seconds else 0.0
        head = {
            Boundedness.BLOCKED: (
                f"This spent {share:.0%} of its {self.wall_seconds * 1000:.0f}ms waiting rather "
                "than computing, so a faster algorithm would change almost nothing. Look at "
                "what it is waiting for."
            ),
            Boundedness.COMPUTE_BOUND: (
                f"This spent {1 - share:.0%} of its {self.wall_seconds * 1000:.0f}ms on the CPU, "
                "so the cost is work being done rather than something being waited for."
            ),
            Boundedness.MIXED: (
                f"This split its {self.wall_seconds * 1000:.0f}ms between computing and waiting "
                "with neither dominant, so a fix aimed at one addresses part of the cost."
            ),
            Boundedness.PARALLEL: (
                f"This used {self.cpu_seconds * 1000:.0f}ms of CPU in "
                f"{self.wall_seconds * 1000:.0f}ms of wall clock, so it ran on more than one "
                "core and elapsed-minus-CPU is not time spent waiting."
            ),
        }[self.boundedness]

        if not self.scheduler_signal_available:
            return (
                f"{head} Scheduler queueing could not be measured on this platform, which is "
                "not the same as none having occurred."
            )
        return f"{head} The scheduler preempted it {self.involuntary_switches} time(s)."


@contextmanager
def off_cpu() -> Iterator[OffCpuProfile]:
    """Measure how much of a block's elapsed time was spent off the CPU.

    Yields the profile before it has anything in it, and fills it on the way
    out. Read it after the block.

    The measurement costs two clock reads and, on POSIX, one `getrusage` call at
    each end. It does not sample, does not trace, and does not need a profiler,
    which is what makes it usable on every run rather than only when off-CPU time
    is already the hypothesis.
    """
    profile = OffCpuProfile()

    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    before = _rusage()
    try:
        yield profile
    finally:
        # In a `finally`: a workload that raised part-way through still spent
        # its elapsed time somewhere, and which side it spent it on is often the
        # most useful thing known about a failed run.
        profile.wall_seconds = time.perf_counter() - started_wall
        profile.cpu_seconds = time.process_time() - started_cpu
        for name, value in _switch_deltas(before, _rusage()).items():
            setattr(profile, name, value)


def blocking(owner: object, attribute: str, category: OffCpuCategory) -> Hook:
    """A hook that records how long each call to `owner.attribute` waited.

    The adapter's half of attribution. Given a callable the adapter knows is a
    waiting point — a cursor's `execute`, an HTTP client's `send` — this records
    the elapsed seconds of every call under the category's counter, so the events
    are the calls and the total is the seconds spent blocked in them.

    **It records elapsed time, not blocked time, and the difference matters.** A
    call that computes for a millisecond and waits for ten is recorded as eleven.
    That is the right measurement for a waiting point chosen because it waits,
    and the wrong one for a callable that mostly computes — so this is for points
    an adapter declares deliberately, never for wrapping whatever is convenient.

    Raises:
        HookError: the attribute is missing, not callable, or a descriptor that
            cannot be wrapped without changing how it binds.
        ValueError: the category has no counter, which is `SCHEDULER` — being
            preempted is not a call, and no wrapper will ever see it.
    """
    if category not in _COUNTER_FOR:
        message = (
            f"{category.value} has no hook and cannot have one: it is not a call anything "
            "can wrap. It is measured from the operating system's involuntary context-switch "
            "count instead, which `off_cpu()` reports"
        )
        raise ValueError(message)

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        original = _stored_callable(owner, attribute)

        @wraps(original)
        def timed(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                # In a `finally`, so a call that raises part-way through still
                # reports the time it spent waiting before it failed. A workload
                # whose database call times out has waited for exactly as long
                # as the timeout, and that is the finding.
                record(time.perf_counter() - started)

        setattr(owner, attribute, timed)
        try:
            yield
        finally:
            setattr(owner, attribute, original)

    return install


def counter_for(category: OffCpuCategory) -> str:
    """The counter name a category's blocked time is recorded under.

    Raises:
        ValueError: `SCHEDULER`, which has no counter. See `blocking`.
    """
    try:
        return _COUNTER_FOR[category]
    except KeyError:
        message = f"{category.value} is measured from context switches, not from a counter"
        raise ValueError(message) from None


def _switch_deltas(before: Any, after: Any) -> dict[str, int | None]:  # noqa: ANN401
    """The counters the operating system kept, or `None` where it kept none.

    `None` rather than zero, everywhere. Zero involuntary context switches is a
    real and publishable result — nothing was preempted, so the cost is not
    queueing — and a platform that cannot measure must not be able to produce it.
    """
    if before is None or after is None:
        return {
            "voluntary_switches": None,
            "involuntary_switches": None,
            "block_input_operations": None,
            "block_output_operations": None,
        }
    return {
        "voluntary_switches": after.ru_nvcsw - before.ru_nvcsw,
        "involuntary_switches": after.ru_nivcsw - before.ru_nivcsw,
        "block_input_operations": after.ru_inblock - before.ru_inblock,
        "block_output_operations": after.ru_oublock - before.ru_oublock,
    }


def _stored_callable(owner: object, attribute: str) -> Callable[..., Any]:
    """`calls_to`'s rules, applied to the blocking-time constructor."""
    stored: object
    try:
        stored = vars(owner)[attribute]
    except TypeError as error:
        message = f"{owner!r} has no attribute dictionary to patch"
        raise HookError(message) from error
    except KeyError as error:
        message = (
            f"{owner!r} does not define {attribute!r} itself; "
            "name the owner where the attribute is stored"
        )
        raise HookError(message) from error

    if isinstance(stored, (classmethod, staticmethod, property)):
        message = (
            f"{attribute!r} is a {type(stored).__name__}, which cannot be wrapped "
            "without changing how it binds"
        )
        raise HookError(message)

    if not callable(stored):
        message = f"{attribute!r} is not callable"
        raise HookError(message)

    return stored

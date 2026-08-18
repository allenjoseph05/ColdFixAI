"""The only instrument here that reproduces to the integer.

Epic 3, S-3.19. S-0.4 measured the timing noise floor at roughly 20ms, about 6%
of a 350ms endpoint, at 20 repetitions — so **a real 2% improvement is invisible
to timing no matter how many times it is run**. Every other measurement in this
project lives above that floor and phrases its findings accordingly.
`01-primitives.md` §12 names the way out: *search against instruction count, then
validate the single winner with proper interleaved statistical timing.* That
makes this the enabling primitive for any optimization search, not another
instrument in the drawer.

**The unit is a CPython bytecode instruction.** Not an x86 one: callgrind counts
retired machine instructions and does not exist on this platform, while the
subject is Python. What matters is the property `01-primitives.md` §12 wants —
independent of machine and load, reproducible run to run — and PEP 669's
`INSTRUCTION` event has it exactly. Measured on this machine, `for i in
range(n): total += i` costs `24 + 7n` instructions: the same number every run,
the same number in a fresh process, the same number under a different hash seed.

**Reproducibility tolerance: zero.** Counts are equal to the integer or the
instrument is broken. That is the whole reason for it.

**Instrumented runs have no timings, by construction.** Counting costs about 33×
the run, so a `seconds` recorded under monitoring is a number about the
instrument. `InstructionCount.metrics` therefore contains no duration at all —
not a documented caveat but a missing key, so no caller can accidentally compare
an instrumented time against a clean one.

**It cannot see work that is not Python.** `sorted(range(1000, 0, -1))` retires
23 bytecode instructions, because the sort happens in C. The same blindness
covers I/O and lock waiting, which §12 gives to S-3.7 instead. This is the
guard-counter problem in a new place — a metric that reads *cheap* because the
instrument cannot see the work is the silent wrong answer this project is built
to refuse — so every count is checked against how long the workload actually took
and reports `hidden_work` when the two do not agree.

**Two corrections, both measured rather than argued for.**

*The harness's own drain does not count as the subject's work.* Forcing a lazy
result means iterating it, and `drain`'s loop costs bytecode per item: counting
`sorted(range(50_000, 0, -1))` naively gives 300,096 instructions for a sort that
happens entirely in C. The same drain over a filler list of the same length costs
300,087, so the subject's share is **9** — and the guard above works again. The
correction is recorded on the result rather than folded away, because a
subtraction nobody can see is a number nobody can check.

*The interpreter is warmed first.* Measured on this machine, the same workload
counted 1311 then 787, 787, 787, 787: `isinstance(result, Iterable)` runs
`__subclasshook__` — Python code — once per result type and caches the answer
forever after. That one-time work belongs to the process, not to the workload,
and uncorrected it lands on whichever variant of a comparison ran first, which is
a systematic bias in favour of the second. So the workload runs once untouched
before it is counted. **The consequence is that the count is of a warm subject**:
if the subject caches its own work, that cache is warm too, and keeping it out is
the caller's business exactly as it is in S-3.2.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

from coldfix.primitives.measurement import (
    INSTRUCTIONS,
    MATERIALIZED,
    MeasurementError,
    materialize,
)
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
)

# PEP 669 assigns 0, 1, 2 and 5 to debuggers, coverage, profilers and optimizers
# and leaves 3 and 4 for everything else. Taking a reserved one would work and
# would break whatever it belongs to, so this claims only the unassigned pair.
_FREE_TOOL_IDS = (4, 3)
_TOOL_NAME = "coldfix-instructions"

# Uninstrumented CPython retires on the order of 10^8 bytecode instructions per
# second — measured here at 96 million on a counting loop. A workload retiring
# fewer than a hundredth of a percent of that spent essentially all of its time
# somewhere this instrument cannot see: inside a C function, blocked on I/O, or
# waiting on a lock. Two orders of magnitude of slack, because the threshold only
# has to separate *the interpreter ran this* from *the interpreter watched this*.
VISIBLE_INSTRUCTIONS_PER_SECOND = 1e6

# Below this the reference timing is one clock tick on Windows, whose granularity
# S-3.7 and S-3.13 both measured at about 15.6ms for the coarse clocks; a ratio
# taken against it would be a ratio against rounding.
_MEASURABLE_SECONDS = 1e-6


class InstructionError(MeasurementError):
    """Instructions could not be counted, or the count could not be trusted."""


class CounterUnavailableError(InstructionError):
    """No monitoring tool id was free, so nothing could be counted.

    Raised rather than returning zero. A count of zero is a valid answer for a
    workload that does nothing, and an instrument that produced one when it had
    failed to start would report every subject as free.
    """


@dataclass
class Tally:
    """Instructions seen so far. Mutable, and filled while the block runs."""

    count: int = 0


@dataclass(frozen=True)
class InstructionCount:
    """What one workload retired, and whether the instrument could see it.

    `metrics` carries no duration on purpose — see the module docstring. The
    reference timing is on this object, named so that it cannot be mistaken for a
    measurement of the subject's speed.
    """

    instructions: int
    """The subject's own, with the harness's drain already subtracted."""

    drain_instructions: int
    """What the harness spent reading the result, recorded rather than folded away.

    A subtraction nobody can see is a number nobody can check. Draining
    `sorted(range(50_000, 0, -1))` costs 300,087 of the 300,096 counted, and a
    reader who does not know that cannot tell a sort in C from a sort in Python.
    """

    materialized: int
    reference_seconds: float
    """One uninstrumented sample, for the visibility check and nothing else.

    Below S-0.4's ~20ms floor by design on most workloads, and one sample either
    way. Never compare two of these; that is S-1.6's job.
    """

    decided_by: str = field(default=INSTRUCTIONS, init=False)
    """AC 5: the metric any conclusion drawn from this rests on."""

    @property
    def metrics(self) -> Mapping[str, float]:
        """The instrumented run as a metric mapping, with no duration in it.

        Shaped to be handed to S-3.18's `screen`, so an instruction floor from a
        reference implementation can be compared against what the subject
        actually retired.
        """
        return {INSTRUCTIONS: float(self.instructions), MATERIALIZED: float(self.materialized)}

    @property
    def instructions_per_second(self) -> float | None:
        """How much of the elapsed time the interpreter was actually running.

        `None` where the reference run was too short to divide by, which is not
        the same as zero.
        """
        if self.reference_seconds < _MEASURABLE_SECONDS:
            return None
        return self.instructions / self.reference_seconds

    @property
    def hidden_work(self) -> bool:
        """Whether most of what this workload did is invisible to this instrument.

        True for a workload that sorts in C, talks to a database, or waits on a
        lock. The count is still exact; it is exact about a small part of the
        work, and a comparison of two such counts says nothing about which
        implementation is faster.
        """
        rate = self.instructions_per_second
        return rate is not None and rate < VISIBLE_INSTRUCTIONS_PER_SECOND

    def explanation(self) -> str:
        head = (
            f"Retired {self.instructions} bytecode instructions, exactly and reproducibly "
            f"— independent of machine and load, which is what makes it usable below the ~20ms "
            "timing floor S-0.4 measured."
        )
        if not self.hidden_work:
            return head
        rate = self.instructions_per_second or 0.0
        return (
            f"{head} **But it retired only {rate:,.0f} instructions per second of elapsed "
            "time**, against the ~10^8 an interpreter running flat out manages, so almost all "
            "of this workload's time went somewhere this instrument cannot see — a C function, "
            "I/O, or a lock. The count is exact about a small part of the work. Use S-3.7's "
            "off-CPU split to find out which, and do not read a smaller count here as faster."
        )


@dataclass(frozen=True)
class Separation:
    """Two implementations, told apart by the metric that can tell them apart.

    AC 5 is `decided_by`, and it is not decoration: this object exists precisely
    because the timing difference between its two subjects may be unreportable,
    so a reader has to know which number the conclusion rests on.
    """

    label_a: str
    label_b: str
    a: InstructionCount
    b: InstructionCount
    decided_by: str = field(default=INSTRUCTIONS, init=False)

    @property
    def difference(self) -> int:
        return self.b.instructions - self.a.instructions

    @property
    def ratio(self) -> float | None:
        """`b` over `a`. `None` where `a` retired nothing to divide by."""
        if self.a.instructions == 0:
            return None
        return self.b.instructions / self.a.instructions

    @property
    def separated(self) -> bool:
        """Whether the two differ at all. One instruction is a difference here."""
        return self.difference != 0

    @property
    def cheaper(self) -> str | None:
        if not self.separated:
            return None
        return self.label_a if self.a.instructions < self.b.instructions else self.label_b

    @property
    def trustworthy(self) -> bool:
        """Whether the instrument saw enough of either subject to be compared."""
        return not (self.a.hidden_work or self.b.hidden_work)

    def explanation(self) -> str:
        head = (
            f"{self.label_a} retired {self.a.instructions} instructions, {self.label_b} "
            f"{self.b.instructions}."
        )
        if not self.trustworthy:
            return (
                f"{head} **Neither number is a statement about speed here**: at least one of "
                "these workloads spends almost all of its time outside the interpreter, so what "
                "was counted is not what it costs. "
                f"{self.a.explanation() if self.a.hidden_work else self.b.explanation()}"
            )
        if not self.separated:
            return (
                f"{head} They are identical, and identical here means identical — this metric "
                "reproduces to the integer, so there is no difference too small for it to have "
                "found. Any difference between these two is outside the interpreter."
            )

        ratio = self.ratio
        margin = f"{abs(ratio - 1):.1%}" if ratio is not None else "an unmeasurable share"
        return (
            f"{head} {self.cheaper} is cheaper by {abs(self.difference)} instructions "
            f"({margin}), and this comparison rests on {self.decided_by} — a count that "
            "reproduces to the integer, which is why it can separate a difference the ~20ms "
            "timing floor cannot. **That makes it a search result, not a verified improvement.** "
            "`01-primitives.md` §12: search against instruction count, then validate the single "
            "winner with S-1.6's interleaved statistical timing."
        )


@contextmanager
def counting_instructions() -> Iterator[Tally]:
    """Count every bytecode instruction executed while the block runs.

    Global, not scoped to one code object: whatever Python runs inside the block
    is counted, including anything the harness itself does there. Draining a lazy
    result happens inside for that reason — the work being drained is the
    subject's, and leaving it outside would count a generator's creation and
    none of its cost.

    The callback's own bytecode is *not* counted; PEP 669 disables a tool's
    events while that tool's callback runs, which is what makes `24 + 7n` come
    out exactly.

    Raises:
        CounterUnavailableError: both unassigned monitoring tool ids are taken,
            most likely by a nested count, which cannot work — one tool cannot
            hold two independent tallies.
    """
    monitoring = sys.monitoring
    tool = _claim_tool()
    tally = Tally()

    def on_instruction(code: object, offset: int) -> None:
        tally.count += 1

    try:
        monitoring.register_callback(tool, monitoring.events.INSTRUCTION, on_instruction)
        monitoring.set_events(tool, monitoring.events.INSTRUCTION)
        try:
            yield tally
        finally:
            monitoring.set_events(tool, 0)
            monitoring.register_callback(tool, monitoring.events.INSTRUCTION, None)
    finally:
        monitoring.free_tool_id(tool)


def measure_instructions(workload: Callable[[], object]) -> InstructionCount:
    """Count what one workload retires, and check the instrument could see it.

    The workload runs **twice** and a filler drain runs once more. The first run
    is untouched: it supplies the reference timing and, more importantly, warms
    the one-time work — `isinstance` subclass caches, lazy imports — that would
    otherwise be counted as the subject's and would land on whichever variant of
    a comparison went first. The third run drains a list of the same length as
    the result, which is what the harness's own iteration costs, so it can be
    taken back off.

    A mapping result drains through a different shape, so the correction there is
    close rather than exact and is never allowed to make the count negative.

    Raises:
        CounterUnavailableError: no monitoring tool id was free.
    """
    started = perf_counter()
    materialize(workload())
    reference_seconds = perf_counter() - started

    with counting_instructions() as tally:
        materialized = materialize(workload())

    filler = [None] * materialized
    materialize(filler)  # warmed for the same reason the workload was
    with counting_instructions() as drained:
        materialize(filler)

    return InstructionCount(
        instructions=max(tally.count - drained.count, 0),
        drain_instructions=drained.count,
        materialized=materialized,
        reference_seconds=reference_seconds,
    )


def separate(
    variant_a: Callable[[], object],
    variant_b: Callable[[], object],
    *,
    label_a: str = "a",
    label_b: str = "b",
) -> Separation:
    """Tell two implementations apart by what they retire rather than by clock.

    Both arguments are callables and are invoked here, for S-1.6's reason: a
    comparison that accepted a number measured earlier is a comparison against a
    stored baseline, and the whole value of this metric is that it can be
    re-measured exactly.

    Raises:
        TypeError: a variant is not callable — most likely counts measured
            earlier, which is the one thing this must not accept.
    """
    # Widened to `object` for S-1.6's reason: the annotations say both are
    # callable, so a guard written against them is typed out of existence, and
    # the callers this protects are the ones nobody type-checked — an agent
    # assembling a comparison from an artifact it read.
    candidates: tuple[tuple[str, object], ...] = ((label_a, variant_a), (label_b, variant_b))
    for name, variant in candidates:
        if not callable(variant):
            message = (
                f"variant {name!r} is a {type(variant).__name__}, not a callable. This counts "
                "what a workload retires by running it, and a count measured earlier is a "
                "stored baseline"
            )
            raise TypeError(message)

    return Separation(
        label_a=label_a,
        label_b=label_b,
        a=measure_instructions(variant_a),
        b=measure_instructions(variant_b),
    )


def _claim_tool() -> int:
    monitoring = sys.monitoring
    for tool in _FREE_TOOL_IDS:
        if monitoring.get_tool(tool) is None:
            monitoring.use_tool_id(tool, _TOOL_NAME)
            return tool

    holders = ", ".join(f"{tool}: {monitoring.get_tool(tool)}" for tool in _FREE_TOOL_IDS)
    message = (
        f"no monitoring tool id is free, so nothing could be counted ({holders}). A count "
        "already running is the likely cause, and nesting two cannot work — one tool cannot "
        "hold two independent tallies"
    )
    raise CounterUnavailableError(message)


REGISTRY.register(
    Primitive(
        name="observation.instructions",
        summary=(
            "Count the bytecode instructions a workload retires — exact, reproducible, and "
            "independent of machine and load, so it separates differences the ~20ms timing "
            "floor cannot. Search against it, then validate the winner by timing."
        ),
        cost=CostClass.SECONDS,
        run=separate,
        required_capabilities={Capability.INSTRUCTION_COUNTING},
    )
)

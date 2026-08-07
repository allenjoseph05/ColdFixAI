"""What a component costs alone, what it costs among its neighbours, and which neighbour.

Epic 3, S-3.13. `01-primitives.md` §11: run a component alone, then in its normal
context, and **the gap is interference**. It detects the things that only exist
because something else is running — lock contention, pool exhaustion, cache
thrash between components, starvation from a background job, queue buildup — none
of which any single-component measurement can see, because in isolation they are
not there.

**This is the instrument S-3.12 points at.** The composition in §17 is
*Load → Isolation → Substitution*: the USL fit says contention is the limit,
isolation says *which* neighbour, substitution finds the setting. S-3.12's
contention message names this module by number for that reason, and
`attribute_interference` is the step it is naming — a gap against the whole
context says a component is being interfered with, and a gap measured against
each neighbour in turn says by what.

**A gap smaller than the spread of the isolated runs is not interference.** Two
runs of the same thing differ; that is what S-0.4's noise floor is about. An
isolation primitive that reported a 3% gap as contention would manufacture a
finding on every component it ever measured, and the finding would name a real
neighbour, which makes it worse than a vague one. So the isolated condition is
run repeatedly, its own spread is the floor, and a gap inside that floor is
reported as *no interference detectable* — a result, not a failure.

**Diagnose only, and that is a standing restriction rather than a caution.**
`01-primitives.md` §11 states it: output equivalence cannot detect an introduced
race, so no falsification test this system can write makes a contention patch
sound. §11 also says what the restriction buys — *this is what allows the claim
"faster without breaking anything" to be true*. The mechanism sentence emitted
here is written so S-2.9 refuses it in its own constructor, exactly as S-3.12's
is.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from coldfix.primitives.measurement import MeasurementError
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.sandbox.scope import Disposition

# Enough repetitions for the isolated condition to have a spread worth calling a
# floor. Below three, "the range of the samples" is one difference.
MINIMUM_REPETITIONS = 3

# How long the background context is given to actually be running before the
# foreground measurement starts. Without it the first samples are taken against
# a context that has not begun, which understates the gap — and understating it
# is the direction that loses a finding.
CONTEXT_SETTLE_SECONDS = 0.05


class IsolationError(MeasurementError):
    """An isolation measurement could not be taken, or could not be trusted."""


@dataclass(frozen=True)
class Interference:
    """What one component cost alone and among neighbours, and whether that differs."""

    component: str
    context: str
    alone: tuple[float, ...]
    in_context: tuple[float, ...]

    disposition: Disposition = Disposition.DIAGNOSE_ONLY
    """Never anything else. `01-primitives.md` §11's standing restriction."""

    @property
    def alone_cost(self) -> float:
        return statistics.median(self.alone)

    @property
    def context_cost(self) -> float:
        """The typical cost under contention. **Contention in the tail is
        understated by this**, deliberately and at a cost worth naming.

        A median is what a component typically costs, which is the right thing
        for a gap. It is the wrong thing for contention that shows up only
        occasionally — tail-latency amplification is on `01-primitives.md` §3's
        detection list, and a median is exactly the statistic that discards it.
        Where the tail is the question, compare the distributions with S-1.5's
        rank test rather than reading this number.
        """
        return statistics.median(self.in_context)

    @property
    def gap(self) -> float:
        """What the neighbours cost this component. The finding, when there is one."""
        return self.context_cost - self.alone_cost

    @property
    def ratio(self) -> float:
        if self.alone_cost == 0:
            return float("inf") if self.context_cost > 0 else 1.0
        return self.context_cost / self.alone_cost

    @property
    def noise(self) -> float:
        """The spread of the isolated runs, which is the floor for the gap.

        The range rather than a standard deviation: it needs no assumption about
        the shape of the distribution, and timing distributions do not have the
        shape a standard deviation assumes (S-1.5's docstring says why at
        length).
        """
        return max(self.alone) - min(self.alone)

    @property
    def detectable(self) -> bool:
        """Whether the gap is bigger than the difference between two identical runs.

        A gap inside the noise is not a small finding. It is the same measurement
        twice, and reporting it would name a neighbour that did nothing.
        """
        return self.gap > self.noise

    @property
    def mechanism(self) -> str:
        """Written so S-2.9 refuses it, rather than relying on this module to remember."""
        if not self.detectable:
            return (
                f"{self.component} showed no measurable interference from {self.context}; the "
                "gap is inside the spread of the isolated runs"
            )
        return (
            f"{self.component} is slower under contention from {self.context}: it costs "
            f"{self.ratio:.2f}x as much when they run together"
        )

    def explanation(self) -> str:
        if not self.detectable:
            return (
                f"{self.component} cost {self.alone_cost:.4g} alone and "
                f"{self.context_cost:.4g} alongside {self.context}. The difference "
                f"({self.gap:.4g}) is inside the spread of the isolated runs "
                f"({self.noise:.4g}), so there is no interference to report — which is an "
                "exclusion worth recording rather than a search that failed."
            )
        return (
            f"{self.component} costs {self.ratio:.2f}x as much when {self.context} is running "
            f"({self.alone_cost:.4g} alone, {self.context_cost:.4g} together; the isolated "
            f"runs spread by {self.noise:.4g}). That gap exists only because something else "
            "is running, so it is contention for something they share.\n\n"
            "This is a search result: the gap is real and its size is one sample per "
            "condition against a noise floor, so confirm the magnitude with an interleaved "
            "comparison (S-1.6) before quoting it.\n\n"
            "Diagnosed and never patched. Output equivalence cannot detect an introduced "
            "race, so no test this system writes makes a contention fix safe — and that "
            "restriction is what lets the rest of its patches claim to be safe "
            "(`01-primitives.md` §11)."
        )


@dataclass(frozen=True)
class Attribution:
    """Which neighbour a component actually contends with.

    The step §17's *Load → Isolation → Substitution* composition needs. A gap
    against the whole context says a component is interfered with; a gap measured
    against each neighbour in turn says by what, and only the second is
    actionable.
    """

    component: str
    against: tuple[Interference, ...]
    disposition: Disposition = Disposition.DIAGNOSE_ONLY

    @property
    def culprits(self) -> tuple[Interference, ...]:
        """Every neighbour whose gap cleared the noise, worst first."""
        return tuple(
            sorted(
                (item for item in self.against if item.detectable),
                key=lambda item: item.ratio,
                reverse=True,
            )
        )

    @property
    def worst(self) -> Interference | None:
        found = self.culprits
        return found[0] if found else None

    def explanation(self) -> str:
        if not self.culprits:
            return (
                f"{self.component} was run against {len(self.against)} neighbour(s) "
                "individually and none of them cost it anything outside the noise. If the "
                "whole context does interfere, it is the combination rather than any single "
                "neighbour — which is a different and harder finding."
            )
        ranked = "\n".join(f"  - {item.context}: {item.ratio:.2f}x" for item in self.culprits)
        return (
            f"{self.component} contends with {len(self.culprits)} of "
            f"{len(self.against)} neighbour(s):\n{ranked}\n\n"
            "Substituting the shared resource's configuration (S-3.10) is the next step for "
            "the worst of them. The finding itself is diagnosed and never patched."
        )


@contextmanager
def running(work: Callable[[], object], workers: int) -> Iterator[None]:
    """Keep `work` looping in the background for the duration of the block.

    The context, in the sense §11 means it: the neighbours that are normally
    running when the component under test runs. The threads are started and given
    a moment to actually be working before the block begins, because a foreground
    measurement taken against a context that has not started yet understates the
    gap — and understating it is the direction that loses a finding.

    Exceptions raised inside the background work are swallowed by design: the
    context is scenery, not the subject, and a neighbour that fails under load is
    S-3.16's finding rather than this one's. What matters here is that it keeps
    occupying whatever the component contends for.
    """
    if workers < 1:
        message = f"a context needs at least one worker to be a context, got {workers}"
        raise IsolationError(message)

    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                work()
            except Exception:  # noqa: BLE001 - the context is scenery, not the subject
                continue

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    time.sleep(CONTEXT_SETTLE_SECONDS)
    try:
        yield
    finally:
        # In a `finally`: a measurement that failed is exactly when the load is
        # most likely to be left running, and every measurement taken afterwards
        # would be taken against it.
        stop.set()
        for thread in threads:
            thread.join(timeout=5.0)


def measure_interference(  # noqa: PLR0913 - see the note on scale_volume
    component: Callable[[], object],
    context: Callable[[], object],
    *,
    name: str = "the component",
    context_name: str = "its context",
    repetitions: int = 5,
    workers: int = 2,
) -> Interference:
    """Time the component alone, then with the context running, and report the gap.

    The isolated condition is measured `repetitions` times because its spread is
    what decides whether the gap means anything. One isolated sample gives a
    difference with nothing to compare it against, and every difference looks
    like a finding.

    Raises:
        IsolationError: too few repetitions to have a spread.
    """
    if repetitions < MINIMUM_REPETITIONS:
        message = (
            f"the isolated condition needs at least {MINIMUM_REPETITIONS} runs for its spread "
            f"to be a noise floor, got {repetitions}. With fewer, every difference looks like "
            "a finding"
        )
        raise IsolationError(message)

    alone = tuple(_time(component) for _ in range(repetitions))
    with running(context, workers):
        together = tuple(_time(component) for _ in range(repetitions))

    return Interference(
        component=name,
        context=context_name,
        alone=alone,
        in_context=together,
    )


def attribute_interference(
    component: Callable[[], object],
    neighbours: Mapping[str, Callable[[], object]],
    *,
    name: str = "the component",
    repetitions: int = 5,
    workers: int = 2,
) -> Attribution:
    """Measure the component against each neighbour on its own.

    *Isolate to find the shared resource*, which is the middle step of §17's
    composition. Running every neighbour at once says something interferes;
    running them one at a time says which, and only the second can be acted on.

    Raises:
        IsolationError: no neighbours, which is a measurement of nothing.
    """
    if not neighbours:
        message = "no neighbours were given, so there is nothing for the component to contend with"
        raise IsolationError(message)

    return Attribution(
        component=name,
        against=tuple(
            measure_interference(
                component,
                neighbour,
                name=name,
                context_name=label,
                repetitions=repetitions,
                workers=workers,
            )
            for label, neighbour in neighbours.items()
        ),
    )


def _time(work: Callable[[], object]) -> float:
    started = time.perf_counter()
    work()
    return time.perf_counter() - started


REGISTRY.register(
    Primitive(
        name="isolation.interference",
        summary=(
            "Run a component alone and alongside its neighbours, report the gap, and measure "
            "against each neighbour to say which one it contends with. Diagnose-only."
        ),
        cost=CostClass.TENS_OF_MINUTES,
        run=measure_interference,
        required_capabilities={Capability.LOAD_GENERATION, Capability.STATE_RESET},
    )
)

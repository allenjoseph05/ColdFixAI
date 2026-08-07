"""What optimizing a component would gain, which is not what it costs.

Epic 3, S-3.14. `01-primitives.md` §8 draws the distinction this primitive
exists for, and it is not a subtlety:

| | Ablation | Proportional perturbation |
|---|---|---|
| Operation | remove entirely | slow by a fraction |
| Answers | what does it cost? | what would optimizing gain? |
| Correctness | broken | preserved |
| Concurrent systems | misleading | accurate |

**The decisive datum is Coz's**: the function responsible for SQLite's 25% gain
accounted for about **0.15% of runtime**. A profiler ranks by cost and would
never have surfaced it. Its worked example is starker still — two functions with
similar profile weight, where optimizing one yields at most 4.5% and the other
yields exactly zero. Cost and gain are different quantities, and only one of them
tells you what to work on.

**How this measures it.** §8 offers two directions — slow the component, or slow
everything else — and this takes the first: inject a known fractional delay into
the target and watch what the whole workload does. The slope of that line is the
component's *sensitivity*: how much of a delay to it reaches the finish.

**Which is exactly why it is gated.** In serial code the slope is the component's
share of runtime, because every millisecond added to it is a millisecond added to
the total. That is ablation's answer arrived at more slowly, which is
`08-audit.md` F7's finding: *in single-threaded code there is nothing to pause,
slowing everything else simply slows everything, and the primitive collapses back
into ablation.* In concurrent code the slope is **less** than the share, because
other threads absorb part of the delay — and that gap is the whole of the
information Coz's method provides. So the primitive declares
`RUNS_CONCURRENT_CODE` and S-3.1 withholds it from projects where the fact is
false, and from projects where nobody has established it.

**The curve is measured; the speedup is an extrapolation and says so.** Slowdowns
are what can be injected; the useful question is about a speedup, which is the
same line read on the other side of zero. That is a linear extrapolation past the
measured range, so the fit's r² is reported with it — the same rule S-3.12
applies to a peak beyond the concurrency actually driven.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from coldfix.primitives.measurement import MeasurementError
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
    ProjectFact,
    requires,
)
from coldfix.primitives.substitution import substitute

# A slope needs three points to be a fit rather than a construction, and one of
# them is the unperturbed baseline.
MINIMUM_FRACTIONS = 2

# Below this slope, a delay injected into the component does not reach the
# finish at all. Coz's own worked example has a function whose optimization
# yields *exactly zero*, and reporting a tiny positive number for it would be
# reporting the residual of the fit.
INSENSITIVE_BELOW = 0.02


class PerturbationError(MeasurementError):
    """A perturbation could not be injected, or its curve could not be fitted."""


@dataclass(frozen=True)
class Point:
    """The workload's cost with a known fractional delay in the target."""

    fraction: float
    """How much slower the target was made. 0.5 means it took half again as long."""

    samples: tuple[float, ...]

    @property
    def cost(self) -> float:
        return statistics.median(self.samples)


@dataclass(frozen=True)
class Sensitivity:
    """How much of a delay to one component reaches the finish.

    The curve rather than a point, which AC 3 asks for and which is also the only
    honest form: a single perturbation gives a difference with no way to tell a
    linear response from a threshold.
    """

    target: str
    points: tuple[Point, ...]
    slope: float
    """Fraction of the injected delay that reached the total. The sensitivity."""

    r_squared: float

    @property
    def baseline(self) -> float:
        """The unperturbed cost, which is the point at fraction zero."""
        return self.points[0].cost

    @property
    def sensitive(self) -> bool:
        """Whether optimizing this component would gain anything measurable."""
        return self.slope > INSENSITIVE_BELOW

    def predicted_gain(self, speedup: float) -> float:
        """Fraction of total cost saved by making the target `speedup` faster.

        **An extrapolation.** The line was measured on the slowdown side of zero
        and this reads it on the other, which is what Coz's method does and is
        only sound while the response stays linear. `r_squared` is how much that
        assumption is worth here; it is reported next to this number everywhere
        it is shown.
        """
        return self.slope * speedup

    def explanation(self) -> str:
        measured = ", ".join(f"+{point.fraction:.0%}→{point.cost:.4g}" for point in self.points)
        head = (
            f"{self.target}: sensitivity {self.slope:.3f} (r²={self.r_squared:.3f}) over "
            f"{len(self.points)} points [{measured}]."
        )
        if not self.sensitive:
            return (
                f"{head} A delay injected into this component does not reach the finish, so "
                "making it faster would gain nothing measurable — which is a finding, and one "
                "a profiler cannot produce: it ranks by cost, and this component may well "
                "have plenty of that."
            )
        return (
            f"{head} About {self.slope:.0%} of any time saved in this component reaches the "
            f"total, so halving it would gain roughly {self.predicted_gain(0.5):.1%} overall. "
            "**That last number is an extrapolation**: the line was measured by slowing the "
            "component down and read back on the speedup side, which holds while the response "
            "is linear and no further than r² supports."
        )


def perturb(
    owner: object,
    attribute: str,
    workload: Callable[[], object],
    fraction: float,
    *,
    repetitions: int = 5,
) -> Point:
    """Make `owner.attribute` take `fraction` longer, and time the whole workload.

    The delay is proportional to what the call actually took, not a constant:
    a fixed delay would perturb a fast call out of all proportion and a slow one
    barely at all, and the slope of that would be a fact about the constant.

    Correctness is preserved — the target still runs and still returns what it
    returned — which is the row of §8's table that separates this from ablation
    and is why this primitive needs no diagnostic worktree.

    The substitution is S-3.10's, so the target is restored **and the restoration
    is verified**: a perturbation left installed would slow every measurement
    taken afterwards, silently.

    **The workload must reach the target through the attribute.** This replaces
    what `owner.attribute` resolves to, so a caller holding a reference captured
    before the substitution — a bound method passed as the workload, a
    `from module import target` at the top of a file — calls the original and
    never sees the delay. `calls_to` documents the same limitation for counting,
    and here it is worse: the perturbation appears to work, the curve comes back
    flat, and a flat curve reads as *optimizing this would gain nothing*. The
    wrong answer, in the direction of doing nothing.

    Raises:
        PerturbationError: the fraction is negative, or the attribute cannot be
            wrapped.
    """
    if fraction < 0:
        message = f"a slowdown fraction cannot be negative, got {fraction}"
        raise PerturbationError(message)

    try:
        original = vars(owner)[attribute]
    except (TypeError, KeyError) as error:
        message = (
            f"{attribute!r} is not defined on {owner!r} itself, so it cannot be perturbed "
            "without changing which objects are affected"
        )
        raise PerturbationError(message) from error

    with substitute(owner, attribute, _slowed(original, fraction)):
        samples = tuple(_time(workload) for _ in range(repetitions))

    return Point(fraction=fraction, samples=samples)


def sensitivity_curve(  # noqa: PLR0913 - see the note on scale_volume
    owner: object,
    attribute: str,
    workload: Callable[[], object],
    fractions: Sequence[float] = (0.0, 0.25, 0.5, 1.0),
    *,
    repetitions: int = 5,
    name: str | None = None,
) -> Sensitivity:
    """Perturb the target at several fractions and fit the response.

    A curve rather than a point, because a single perturbation cannot tell a
    linear response from a threshold — and the extrapolation to a speedup is only
    meaningful if the response is linear, which is what r² is here to say.

    Raises:
        PerturbationError: fewer than two fractions, no zero point to measure
            against, or a baseline of zero.
    """
    if len(fractions) <= MINIMUM_FRACTIONS:
        message = (
            f"a sensitivity curve needs more than {MINIMUM_FRACTIONS} points to be a fit "
            f"rather than a line drawn through them, got {len(fractions)}"
        )
        raise PerturbationError(message)
    if 0.0 not in fractions:
        message = (
            "the curve has no unperturbed point, so there is nothing for the perturbed ones "
            "to be a fraction of"
        )
        raise PerturbationError(message)

    points = tuple(
        perturb(owner, attribute, workload, fraction, repetitions=repetitions)
        for fraction in sorted(fractions)
    )
    baseline = points[0].cost
    if baseline <= 0:
        message = "the unperturbed workload cost nothing, so there is no scale to fit against"
        raise PerturbationError(message)

    # Relative cost against injected fraction. The slope is the share of an
    # injected delay that reaches the total — sensitivity, in Coz's sense.
    injected = [point.fraction for point in points]
    relative = [(point.cost - baseline) / baseline for point in points]

    line = statistics.linear_regression(injected, relative)
    r_squared = statistics.correlation(injected, relative) ** 2 if len(set(relative)) > 1 else 1.0

    return Sensitivity(
        target=name or f"{_name(owner)}.{attribute}",
        points=points,
        slope=line.slope,
        r_squared=r_squared,
    )


def _slowed(original: Callable[..., object], fraction: float) -> Callable[..., object]:
    """The target, taking `fraction` longer than whatever it took."""

    def perturbed(*args: object, **kwargs: object) -> object:
        started = time.perf_counter()
        result = original(*args, **kwargs)
        elapsed = time.perf_counter() - started
        if fraction > 0:
            time.sleep(elapsed * fraction)
        return result

    return perturbed


def _time(work: Callable[[], object]) -> float:
    started = time.perf_counter()
    work()
    return time.perf_counter() - started


def _name(owner: object) -> str:
    return str(getattr(owner, "__name__", None) or type(owner).__name__)


REGISTRY.register(
    Primitive(
        name="perturbation.sensitivity",
        summary=(
            "Inject a known fractional delay into a component and measure how much of it "
            "reaches the finish — what optimizing it would gain, rather than what it costs."
        ),
        cost=CostClass.MINUTES,
        run=sensitivity_curve,
        required_capabilities={Capability.STATE_RESET},
        applies=requires(
            ProjectFact.RUNS_CONCURRENT_CODE,
            because=(
                "in single-threaded code every millisecond added to a component is a "
                "millisecond added to the total, so the sensitivity is just the component's "
                "share of runtime and this collapses into ablation (`08-audit.md` F7). Use "
                "ablation there, and say so"
            ),
        ),
    )
)

"""Vary how much data there is, and — separately — how unevenly it is spread.

Epic 3, S-3.2 and S-3.3. One primitive with two axes, which is how
`01-primitives.md` §2 states it: *vary two axes, not one. Volume and shape.*
`scale_volume` sweeps the first and fits growth against it; `compare_shapes`
holds volume still and varies only the second. They share every mechanism below
because the failure modes they have to survive are the same ones.

The second axis is not a refinement of the first. **Uniform synthetic data hides
skew-dependent defects at every volume**, so no amount of scaling finds them: a
per-parent cost of `k(k-1)/2` under a generator that gives every parent three
children is six comparisons per parent at three rows and six comparisons per
parent at three million. `Σ k²` is minimized exactly when every parent has the
same count, which makes the uniform fixture provably the blindest shape for that
whole class, and it is the shape almost every fixture generator produces.

The cheapest primitive: `01-primitives.md` §2
notes that counts are deterministic, so no warmup, no interleaving and no
statistical test are needed to compare them. That cheapness is what makes this
the instrument screening reaches for first.

**The story's note names three failure modes, and what they have in common is
that each produces a wrong answer that looks exactly like a right one.** All
three end in the same published sentence — *queries flat across 100x scale, so
not the database* — which `00-BRIEF.md` §9 ships as a finding and a human is
expected to act on. So each gets a mechanism here rather than a caution:

**Baseline offset.** A framework charges a fixed cost per request — session,
auth, permissions, middleware — that has nothing to do with data volume. Fifty
of those against one query per row reads as 51, 52, 53 across scales 1, 2, 3,
whose power-law exponent is 0.03: constant. The same measurement with the
constant removed is 1, 2, 3, exponent 1.0: linear, one query per row, an N+1.
The offset does not change the slope of a straight line, which is why measuring
it is easy to skip — it changes the *exponent*, and the exponent is what growth
classification rests on. Every run therefore measures at N=0 first and subtracts.

**Lazy evaluation.** A workload that returns a queryset, a generator or a
streaming response has not done its work when it returns. The counters read
zero, the clock stops early, and both numbers are confidently wrong in the
direction of "nothing here". The measured window is closed only after the result
has been drained, and how many items that took is itself recorded — a lazy
result that yields nothing at every scale is a different finding from a workload
that legitimately does nothing.

**Warm cache.** The second scale point reads what the first one warmed, so cost
per item falls as volume rises and superlinear growth reads as sublinear.
ADR 026 already settled that this cannot be detected after the fact: a workload
with a stale cache returns the same thing every cycle, and so does a correct one,
because a correct reset makes every cycle identical. So it is not detected here
either. It is *prevented*, by the same construction ADR 026 used — requiring a
process that cannot survive from one scale point to the next, proved by its
identity differing — or by an explicit clear the caller supplies. A sweep with
neither refuses to run rather than producing numbers nobody can qualify.

**Reset is a `VerifiedReset`, not a callable.** Seeding happens *inside* the
reset cycle, so each scale point's data cannot leak into the next one, and the
strategy that returned the state is recorded on the result. S-2.7 already made
the verified state a type; taking that type here means a sweep cannot be run on
a reset nobody proved works.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.stats import Fit, fit_growth
from coldfix.primitives.measurement import (
    BaselineError,
    CacheControl,
    IdentityLedger,
    MeasurementError,
    MetricKind,
    check_same_metrics,
    measure_once,
    metric_kind,
    require_cache_control,
)
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerifiedReset

# The framework baseline. Measured like any other point — same reset, same
# seeding call, same workload — because a baseline taken a different way is a
# measurement of a different program.
BASELINE_SCALE = 0

# `fit_growth` needs three points at two or more distinct scales, and so does
# the story: two points define a line through themselves and say nothing about
# whether it is the right line.
MINIMUM_SCALE_POINTS = 3

# A shape comparison needs something to compare against. One distribution
# measured alone is a measurement, not a comparison, and would let a caller
# report skew sensitivity from a single number.
MINIMUM_DISTRIBUTIONS = 2


class ScaleSweepError(MeasurementError):
    """The requested scale points or distributions cannot support a comparison."""


class Distribution(StrEnum):
    """How a fixed number of children is spread across a fixed number of parents.

    Three shapes, and they have to be genuinely different or the second axis is
    decoration. What separates them is **where the mass sits**, which is what
    decides whether a per-parent cost is ever paid at a punishing size:

    `UNIFORM` gives every parent the same count, so the largest parent is the
    average parent and no per-parent cost is ever paid at an interesting size.

    `POWER_LAW` is Zipf: the second parent holds half what the first does, the
    third a third, and so on. Its signature is a **smooth spectrum** — one large
    parent, several middling ones, many small ones — and it is what naturally
    occurring popularity looks like.

    `LONG_TAIL` is the shape data engineers mean by the phrase: a handful of
    parents holding almost everything, and a very long tail of parents holding
    the minimum. *Most customers have one order; one customer has fifty
    thousand.* Its signature is **bimodal** rather than smooth, and it is the
    deliberate worst case for any per-parent cost — the one that turns
    milliseconds into minutes for a single request while every other request
    stays fast.

    At twenty parents the first two are already 4x apart on the largest parent;
    the separation grows with the parent count, and real subjects have thousands.
    """

    UNIFORM = "uniform"
    POWER_LAW = "power_law"
    LONG_TAIL = "long_tail"


# Zipf's exponent. 1.0 is the classic form — the second parent holds half what
# the first does — and is what makes the head steep enough to be worth naming.
POWER_LAW_EXPONENT = 1.0

# The long tail's head: a tenth of the parents take everything above the floor,
# and the other nine tenths hold exactly one child each. Deliberately the
# extreme, because the point of offering this shape is to construct the request
# that takes minutes while every other request stays fast — the case the story's
# note is about and the case no uniform generator ever produces.
LONG_TAIL_HEAD_GROUPS = 0.1


@dataclass(frozen=True)
class Allocation:
    """A fixture recipe: how many children each parent gets, and under what shape.

    Carried into the seeding call and recorded on the measurement, because a
    result that does not say what shape it was taken under is not comparable with
    one that does — and, more to the point, an exclusion drawn from uniform data
    is only true of uniform data.
    """

    distribution: Distribution
    counts: tuple[int, ...]

    @property
    def groups(self) -> int:
        return len(self.counts)

    @property
    def total(self) -> int:
        return sum(self.counts)

    @property
    def largest(self) -> int:
        """The worst single parent — where a per-parent cost is actually paid."""
        return max(self.counts, default=0)

    @property
    def head_mass(self) -> float:
        """The share of all children held by the largest tenth of parents.

        The statistic that separates the three shapes: uniform lands on a tenth
        by definition, a power law well above it, a long tail near everything. If
        two distributions agree here they are one distribution with two names,
        and the second axis is decoration.
        """
        if not self.counts:
            return 0.0
        head = max(1, round(len(self.counts) * LONG_TAIL_HEAD_GROUPS))
        return sum(sorted(self.counts, reverse=True)[:head]) / self.total


def allocate(distribution: Distribution, *, groups: int, total: int) -> Allocation:
    """Spread `total` children over `groups` parents in the named shape.

    **Every distribution returns exactly `groups` counts summing to exactly
    `total`.** That is the whole point of generating them here rather than
    letting each shape decide its own size: a comparison across distributions
    where the volume also moved is a comparison of two things at once, and
    neither can be attributed.

    Deterministic, with no random number generator anywhere. The same arguments
    give the same counts on every machine and every Python version, which is what
    lets a measurement taken today be compared with one taken next month — and
    what will let S-5.1 key a replay cache on the fixture.

    Every parent gets at least one child, so the three shapes cover the same
    parents as well as the same volume. A shape that quietly emptied half the
    parents would be varying the parent count too.

    Raises:
        ScaleSweepError: fewer than one parent, or fewer children than parents.
    """
    if groups < 1:
        message = f"need at least one parent to allocate across, got {groups}"
        raise ScaleSweepError(message)
    if total < groups:
        message = (
            f"cannot give each of {groups} parents at least one child out of {total}; "
            "a shape that empties parents varies the parent count as well as the shape"
        )
        raise ScaleSweepError(message)

    spread = total - groups
    if distribution is Distribution.UNIFORM:
        weights = [1.0] * groups
    elif distribution is Distribution.POWER_LAW:
        weights = [1.0 / (rank**POWER_LAW_EXPONENT) for rank in range(1, groups + 1)]
    else:
        head = max(1, round(groups * LONG_TAIL_HEAD_GROUPS))
        # Everything above the floor goes to the head; the tail keeps the one
        # child every parent is guaranteed. Bimodal on purpose — a smooth decay
        # here would be a second power law wearing a different name, and the
        # comparison would be measuring one shape twice.
        weights = [1.0] * head + [0.0] * (groups - head)

    return Allocation(distribution=distribution, counts=_apportion(spread, weights))


def _apportion(amount: int, weights: Sequence[float]) -> tuple[int, ...]:
    """Split `amount` in proportion to `weights`, plus one each, summing exactly.

    Largest remainder: floor every share, then hand the shortfall to whichever
    groups were rounded down hardest. Rounding each share independently would
    lose or gain items, and a fixture that is 199 rows under one shape and 201
    under another is not a controlled comparison.
    """
    if amount < 0:  # pragma: no cover - callers subtract one per group first
        message = f"cannot apportion a negative amount ({amount})"
        raise ScaleSweepError(message)

    weight_total = sum(weights)
    exact = [amount * weight / weight_total for weight in weights]
    counts = [int(share) for share in exact]

    shortfall = amount - sum(counts)
    order = sorted(
        range(len(weights)), key=lambda index: exact[index] - counts[index], reverse=True
    )
    for index in order[:shortfall]:
        counts[index] += 1

    return tuple(count + 1 for count in counts)


@dataclass(frozen=True)
class ScalePoint:
    """One workload run at one data volume.

    `raw` is what the instruments read. `adjusted` is `raw` with the N=0 baseline
    removed, and is what the fits are taken over.

    **An adjusted metric may be negative**, and it is left negative. It means the
    baseline is not a constant offset for that metric — a cache that covered the
    empty case, a code path only taken when there is data — and clamping it to
    zero would hide that under a number that looks measured. `fit_growth` already
    handles it correctly by declining the power fit, so `growth` comes back unset
    rather than wrong.
    """

    scale: int
    raw: Mapping[str, float]
    adjusted: Mapping[str, float]


@dataclass(frozen=True)
class ScalingResult:
    """What every metric did as volume grew, and the conditions it was measured under."""

    baseline: Mapping[str, float]
    """The framework's own cost, measured at N=0 and subtracted from every point."""

    points: tuple[ScalePoint, ...]
    fits: Mapping[str, Fit]
    kinds: Mapping[str, MetricKind]
    reset_strategy: ResetStrategy
    cache_control: CacheControl

    distribution: Distribution
    """The fixture shape every point was measured under.

    Declared by the caller rather than observed, the way `time()` takes
    `fresh_process_per_sample`: this function is handed a seeding callable and
    cannot see what it generated. It is recorded because a growth result is only
    true of the shape it was measured under — *queries flat across 100x volume*
    from a fixture giving every parent three children says nothing about the
    parent with three thousand.
    """

    @property
    def scales(self) -> tuple[int, ...]:
        return tuple(point.scale for point in self.points)

    def metric_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.fits))


def scale_volume(  # noqa: PLR0913
    # Five of these describe the subject — seed it, run it, clear it, identify
    # its process, read its guard counters — and grouping them into one object
    # would be defining the workload artifact S-4.1 owns. Inventing that shape
    # here would have the story that owns it inherit a decision it did not make,
    # which is the argument S-2.9 recorded for taking two strings instead of a
    # finding object. The parameters stay flat until there is a second caller.
    *,
    seed: Callable[[int], object],
    invoke: Callable[[], object],
    reset: VerifiedReset,
    scales: Sequence[int],
    distribution: Distribution,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
    clear_caches: Callable[[], object] | None = None,
    process_identity: Callable[[], object] | None = None,
) -> ScalingResult:
    """Measure a workload at each of `scales`, and fit every metric against volume.

    Each point is one cycle of the verified reset: seed inside the cycle, clear,
    run the workload with the counters attached, drain whatever it returned, and
    let the reset undo all of it on the way out. The framework baseline at N=0 is
    measured the same way and subtracted from every point before fitting.

    Args:
        seed: populates the subject at a given volume. Called inside the reset
            cycle, so what it writes is undone with everything else.
        invoke: runs the workload once. Its return value is drained, not kept.
        reset: a strategy S-2.7 proved returns this project to its baseline.
        scales: three or more distinct positive volumes, measured in the order
            given — which need not be ascending, and a randomized order is a
            defence against the ordering bias `01-primitives.md` §10 describes.
        distribution: the fixture shape `seed` produces. Declared, not observed,
            and recorded on the result: growth measured under one shape is a
            statement about that shape only. `01-primitives.md` §2 is explicit
            that the two axes are volume *and* shape, and a sweep that does not
            say which shape it held has answered half the question.
        counters: names of hooks registered with S-1.3's `count`. An unregistered
            name raises rather than counting zero, which is ADR 013's rule and
            the reason a typo cannot become an exclusion.
        extra_counters: further **deterministic counters** read after the
            workload, inside the cycle — guard counters such as rows or bytes
            returned, which are sums rather than call counts and so cannot come
            from a hook. S-3.8 is where this becomes a requirement rather than a
            seam.
        clear_caches: called after seeding and before the measured run.
        process_identity: read inside each cycle. It must **differ** at every
            scale point; a repeat means a process survived and could be holding
            rows no database reset will clear.

    Raises:
        CacheControlError: neither cache control was supplied, or the process
            identity repeated.
        ScaleSweepError: fewer than three points, or a repeated or non-positive
            volume.
        BaselineError: the workload could not be measured at N=0.
        MetricSetError: the metrics differ between points, or an extra counter
            collides with one this module produces.
    """
    control = require_cache_control(clear_caches, process_identity)
    _check_scales(scales)

    ledger = IdentityLedger()

    def measure(scale: int) -> Mapping[str, float]:
        with reset.mechanism.cycle():
            seed(scale)
            if clear_caches is not None:
                clear_caches()
            if process_identity is not None:
                ledger.record(process_identity(), f"scale {scale}")
            return measure_once(invoke, counters, extra_counters)

    try:
        baseline = measure(BASELINE_SCALE)
    except MeasurementError:
        # Already specific. A cache control that was never supplied or an extra
        # counter that collides is not "the baseline could not be measured", and
        # restating it as that would hide the sentence that says what to fix.
        raise
    except Exception as error:
        message = (
            f"the workload could not be measured at N={BASELINE_SCALE}, so the framework's "
            "own fixed cost is unknown and every exponent computed from this sweep would be "
            "wrong by an unknown amount"
        )
        raise BaselineError(message) from error

    points: list[ScalePoint] = []
    for scale in scales:
        raw = measure(scale)
        check_same_metrics(baseline, raw, f"scale {scale}")
        points.append(
            ScalePoint(
                scale=scale,
                raw=raw,
                adjusted={name: value - baseline[name] for name, value in raw.items()},
            )
        )

    measured_scales = [float(point.scale) for point in points]
    fits = {
        name: fit_growth(measured_scales, [point.adjusted[name] for point in points])
        for name in sorted(baseline)
    }

    return ScalingResult(
        baseline=baseline,
        points=tuple(points),
        fits=fits,
        kinds={name: metric_kind(name) for name in sorted(baseline)},
        reset_strategy=reset.strategy,
        cache_control=control,
        distribution=distribution,
    )


@dataclass(frozen=True)
class ShapeMeasurement:
    """One workload run under one fixture shape, at the volume they all share."""

    allocation: Allocation
    raw: Mapping[str, float]
    adjusted: Mapping[str, float]

    @property
    def distribution(self) -> Distribution:
        return self.allocation.distribution


@dataclass(frozen=True)
class ShapeComparison:
    """The same volume, spread three ways, and what each cost."""

    groups: int
    total: int
    baseline: Mapping[str, float]
    measurements: tuple[ShapeMeasurement, ...]
    kinds: Mapping[str, MetricKind]
    reset_strategy: ResetStrategy
    cache_control: CacheControl

    def under(self, distribution: Distribution) -> ShapeMeasurement:
        for measurement in self.measurements:
            if measurement.distribution is distribution:
                return measurement
        message = f"{distribution.value} was not one of the shapes measured"
        raise ScaleSweepError(message)

    def sensitivity(self, metric: str, *, against: Distribution = Distribution.UNIFORM) -> float:
        """How much worse the worst shape is than `against`, for one metric.

        The number a skew-dependent defect announces itself with. 1.0 means the
        metric does not care how the data is shaped; anything well above it means
        the same volume costs more when it arrives unevenly, which no volume
        sweep at any size would have found.

        Returns infinity when the reference shape charged nothing at all and
        another shape charged something — a real answer rather than a division
        error, and the strongest form the finding takes.
        """
        reference = self.under(against).adjusted[metric]
        worst = max(measurement.adjusted[metric] for measurement in self.measurements)
        if reference == 0:
            return 1.0 if worst == 0 else float("inf")
        return worst / reference


def compare_shapes(  # noqa: PLR0913 - see the note on scale_volume
    *,
    seed: Callable[[Allocation], object],
    invoke: Callable[[], object],
    reset: VerifiedReset,
    groups: int,
    total: int,
    distributions: Sequence[Distribution] = tuple(Distribution),
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
    clear_caches: Callable[[], object] | None = None,
    process_identity: Callable[[], object] | None = None,
) -> ShapeComparison:
    """Hold the volume still and vary only how it is distributed.

    The second axis `01-primitives.md` §2 insists on. A volume sweep answers *how
    does cost grow as there is more data*; this answers *how does cost change
    when the same amount of data arrives unevenly*, and there are defects that
    only the second question reaches. An N+1 costing six comparisons per parent
    under a generator that gives everyone three children costs six comparisons
    per parent at every volume forever.

    Every distribution is allocated over the same `groups` parents and the same
    `total` children, so the only difference between the measurements is shape.
    Everything else — reset cycle, seeding inside it, cache control, the N=0
    baseline, draining lazy results — is what `scale_volume` does, for the same
    reasons.

    Args:
        seed: populates the subject from an allocation, which carries both the
            per-parent counts and the name of the shape they came from.
        groups: how many parents. Constant across every distribution.
        total: how many children in all. Constant across every distribution.
        distributions: which shapes to measure, all three by default.

    Raises:
        ScaleSweepError: fewer than two shapes, a repeated shape, or an
            allocation that cannot be made.
        CacheControlError, BaselineError, MetricSetError: as `scale_volume`.
    """
    control = require_cache_control(clear_caches, process_identity)
    if len(distributions) < MINIMUM_DISTRIBUTIONS:
        message = (
            f"comparing shapes needs at least {MINIMUM_DISTRIBUTIONS} of them, got "
            f"{[d.value for d in distributions]}"
        )
        raise ScaleSweepError(message)
    if len(set(distributions)) != len(distributions):
        message = f"each shape may be measured once, got {[d.value for d in distributions]}"
        raise ScaleSweepError(message)

    ledger = IdentityLedger()

    def measure(allocation: Allocation) -> Mapping[str, float]:
        with reset.mechanism.cycle():
            seed(allocation)
            if clear_caches is not None:
                clear_caches()
            if process_identity is not None:
                ledger.record(process_identity(), allocation.distribution.value)
            return measure_once(invoke, counters, extra_counters)

    # With no rows there is nothing to shape, and all three distributions agree
    # on the empty case — so the framework baseline is measured once and
    # subtracted from every shape, exactly as a volume sweep subtracts it from
    # every point.
    empty = Allocation(distribution=Distribution.UNIFORM, counts=())
    try:
        baseline = measure(empty)
    except MeasurementError:
        raise
    except Exception as error:
        message = (
            f"the workload could not be measured at N={BASELINE_SCALE}, so the framework's "
            "own fixed cost is unknown and every shape ratio computed from this comparison "
            "would be diluted by an unknown constant"
        )
        raise BaselineError(message) from error

    measurements: list[ShapeMeasurement] = []
    for distribution in distributions:
        allocation = allocate(distribution, groups=groups, total=total)
        raw = measure(allocation)
        check_same_metrics(baseline, raw, allocation.distribution.value)
        measurements.append(
            ShapeMeasurement(
                allocation=allocation,
                raw=raw,
                adjusted={name: value - baseline[name] for name, value in raw.items()},
            )
        )

    return ShapeComparison(
        groups=groups,
        total=total,
        baseline=baseline,
        measurements=tuple(measurements),
        kinds={name: metric_kind(name) for name in sorted(baseline)},
        reset_strategy=reset.strategy,
        cache_control=control,
    )


def _check_scales(scales: Sequence[int]) -> None:
    if len(scales) < MINIMUM_SCALE_POINTS:
        message = (
            f"need at least {MINIMUM_SCALE_POINTS} scale points to fit growth, got {len(scales)}"
        )
        raise ScaleSweepError(message)
    if len(set(scales)) != len(scales):
        message = f"scale points must be distinct, got {list(scales)}"
        raise ScaleSweepError(message)
    if any(scale <= BASELINE_SCALE for scale in scales):
        message = (
            f"every scale point must be above the N={BASELINE_SCALE} baseline, got "
            f"{sorted(scale for scale in scales if scale <= BASELINE_SCALE)}"
        )
        raise ScaleSweepError(message)


REGISTRY.register(
    Primitive(
        name="scaling.shape",
        summary=(
            "Hold the data volume constant and vary how it is distributed across parents — "
            "uniform, power law, long tail — to find costs that depend on skew rather than "
            "on size."
        ),
        cost=CostClass.MINUTES,
        run=compare_shapes,
        required_capabilities={Capability.FIXTURE_SHAPING, Capability.STATE_RESET},
    )
)

REGISTRY.register(
    Primitive(
        name="scaling.volume",
        summary=(
            "Run a workload at three or more data volumes with a verified reset between "
            "each, subtract the framework's cost at N=0, and fit every metric against volume."
        ),
        cost=CostClass.MINUTES,
        run=scale_volume,
        required_capabilities={Capability.FIXTURE_SEEDING, Capability.STATE_RESET},
    )
)

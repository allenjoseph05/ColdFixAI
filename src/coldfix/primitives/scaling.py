"""Run a workload at several data volumes and fit every metric against volume.

Epic 3, S-3.2. The first primitive, and the cheapest one: `01-primitives.md` §2
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

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter

from coldfix.bench.counting import count
from coldfix.bench.stats import Fit, fit_growth
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

# Metric names this module produces itself. An extra counter colliding with one
# of these would overwrite a measurement with another measurement, silently.
SECONDS = "seconds"
MATERIALIZED = "materialized"
RESERVED_METRICS = frozenset({SECONDS, MATERIALIZED})


class ScalingError(Exception):
    """A scaling sweep could not be run, or could not be trusted."""


class ScaleSweepError(ScalingError):
    """The requested scale points cannot support a fit."""


class CacheControlError(ScalingError):
    """Nothing guarantees the second scale point did not read the first one's cache.

    A refusal rather than a warning, because the wrong answer this produces is
    *sublinear growth* — which is the shape that closes an investigation rather
    than opening one.
    """


class BaselineError(ScalingError):
    """The framework baseline at N=0 could not be measured.

    Raised rather than skipping the subtraction. A sweep with no baseline is not
    a slightly weaker sweep; it is one whose exponents are wrong by an unknown
    amount in a known direction.
    """


class MetricSetError(ScalingError):
    """The metrics recorded at one scale point are not the ones recorded at another."""


class MetricKind(StrEnum):
    """What a metric's numbers are made of, which decides what may be read into them.

    Recorded because the two kinds do not deserve the same confidence. A count
    is exact and reproduces to the integer. A duration here is **one sample**:
    S-0.4 measured the timing noise floor at roughly 20 ms, about 6% of a 350 ms
    endpoint, so a duration column is context for a shape, never evidence of a
    small difference. Interleaved statistical timing is S-1.6's job and
    instruction counting is S-3.19's.
    """

    COUNT = "count"
    DURATION = "duration"


class CacheControl(StrEnum):
    """How this sweep kept one scale point's cache out of the next one's numbers.

    On the result because `CLAUDE.md` requires exclusions to carry their
    preconditions. *Queries flat across 100x scale* means one thing when every
    point ran in its own process and something much weaker when a caller's own
    hook was trusted to empty the caches it knew about.
    """

    FRESH_PROCESS = "a process that did not outlive the previous scale point"
    EXPLICIT_CLEAR = "a clear the caller performed between scale points"
    BOTH = "a fresh process, and a clear the caller performed"


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
    control = _cache_control(clear_caches, process_identity)
    _check_scales(scales)

    seen: dict[str, int] = {}

    def measure(scale: int) -> Mapping[str, float]:
        with reset.mechanism.cycle():
            seed(scale)
            if clear_caches is not None:
                clear_caches()
            if process_identity is not None:
                _record_identity(repr(process_identity()), scale, seen)
            return _measure_once(invoke, counters, extra_counters)

    try:
        baseline = measure(BASELINE_SCALE)
    except ScalingError:
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
        _check_same_metrics(baseline, raw, scale)
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
        kinds={name: _kind(name) for name in sorted(baseline)},
        reset_strategy=reset.strategy,
        cache_control=control,
    )


def _cache_control(
    clear_caches: Callable[[], object] | None,
    process_identity: Callable[[], object] | None,
) -> CacheControl:
    """Which guarantee this sweep has, refusing if it has none.

    ADR 026 left the equivalent hole open deliberately — supplying no
    `process_identity` there skips the check — because verification is about a
    reset a caller may reasonably not be able to observe. This is a measurement,
    and an unqualifiable measurement is worth less than none, so the same hole is
    closed rather than documented.
    """
    if clear_caches is not None and process_identity is not None:
        return CacheControl.BOTH
    if process_identity is not None:
        return CacheControl.FRESH_PROCESS
    if clear_caches is not None:
        return CacheControl.EXPLICIT_CLEAR

    message = (
        "a scaling sweep needs either a process identity that changes at every scale point "
        "or a way to clear caches between them, and was given neither. Without one of the "
        "two, work the first point warmed is free for the second, cost per item falls as "
        "volume rises, and growth that is superlinear reads as sublinear — which is the "
        "shape that ends an investigation rather than starting one"
    )
    raise CacheControlError(message)


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


def _record_identity(identity: str, scale: int, seen: dict[str, int]) -> None:
    if identity in seen:
        message = (
            f"the process at scale {scale} is the one that already ran scale {seen[identity]} "
            f"({identity}), so anything it cached at the earlier volume is still there. A "
            "cache cannot be detected from the results — a stale one and a correct one both "
            "report the same thing every cycle (ADR 026) — so the sweep stops here"
        )
        raise CacheControlError(message)
    seen[identity] = scale


def _measure_once(
    invoke: Callable[[], object],
    counters: Sequence[str],
    extra_counters: Callable[[], Mapping[str, float]] | None,
) -> Mapping[str, float]:
    """One measured run, with the window closed only after the result is drained."""
    with ExitStack() as stack:
        tallies = {name: stack.enter_context(count(name)) for name in counters}

        started = perf_counter()
        materialized = _materialize(invoke())
        seconds = perf_counter() - started

    metrics: dict[str, float] = {SECONDS: seconds, MATERIALIZED: float(materialized)}
    metrics.update({name: float(tally.events) for name, tally in tallies.items()})

    if extra_counters is not None:
        extra = extra_counters()
        collisions = sorted(set(extra) & (RESERVED_METRICS | set(metrics)))
        if collisions:
            message = (
                f"extra counters {collisions} would overwrite metrics this sweep already "
                "measured; name them differently"
            )
            raise MetricSetError(message)
        metrics.update({name: float(value) for name, value in extra.items()})

    return metrics


def _materialize(result: object) -> int:
    """Force a lazy result and report how many items that took.

    A queryset, a generator and a streaming response have all done nothing when
    they are returned. Draining them inside the measured window is what makes the
    counters and the clock about the workload rather than about the object it
    handed back.

    **One level deep, and that is a real limit.** A mapping's values are drained
    because a view's context is the shape this meets most often; anything lazy
    nested deeper than that is the workload's own to force, and the count is what
    says whether something was found — a sweep reporting zero materialized items
    at every scale has either measured a workload that returns nothing lazy, or
    failed to reach the laziness. Those look identical here and are separated by
    the counters, which is why both numbers are recorded.

    Strings and bytes are already materialized; iterating one yields characters,
    which is expense without information.
    """
    if result is None or isinstance(result, (str, bytes, bytearray)):
        return 0
    if isinstance(result, Mapping):
        return sum(_drain(value) for value in result.values())
    return _drain(result)


def _drain(value: object) -> int:
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return 0
    if not isinstance(value, Iterable):
        return 0
    # `sum(1 for ...)` rather than `len()`: a queryset has a length only after
    # it has run its query, and a generator never has one. Iterating is the
    # operation that forces the work in every case.
    items: Iterator[object] = iter(value)
    return sum(1 for _ in items)


def _check_same_metrics(
    baseline: Mapping[str, float], measured: Mapping[str, float], scale: int
) -> None:
    if set(baseline) != set(measured):
        missing = sorted(set(baseline) - set(measured))
        extra = sorted(set(measured) - set(baseline))
        message = (
            f"scale {scale} recorded a different metric set from the N={BASELINE_SCALE} "
            f"baseline; missing {missing}, unexpected {extra}. A metric present at one "
            "volume and absent at another cannot be fitted, and dropping it would publish "
            "a sweep that silently covered less than it claims"
        )
        raise MetricSetError(message)


def _kind(name: str) -> MetricKind:
    return MetricKind.DURATION if name == SECONDS else MetricKind.COUNT


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

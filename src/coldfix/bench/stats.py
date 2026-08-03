"""Summarize samples, fit growth, and test whether two sets of timings differ.

The fifth and last operation of the lab bench. Everything here is arithmetic on
numbers the other four produced — no measurement is taken in this module, and
nothing in it decides what a result means.

**The significance test is rank-based, and that is not a preference.** Timing
distributions are not normal: they are bounded below by the fastest possible
execution, unbounded above, and routinely multi-modal because a run either did
or did not hit a cache. A t-test assumes a shape these samples do not have, and
it is the mean it compares — one 300ms outlier in fifty 4ms samples moves a mean
by 6% and a median by nothing. Mann-Whitney U uses only the order of the
observations, so it is unmoved by that outlier and unchanged by any monotone
transformation of the data.

Everything is standard-library. `statistics` covers means, medians and least
squares; the rank test is written out here because the standard library has no
hypothesis tests, and it is the one piece of real statistics in the file.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

# Below this many observations per group the normal approximation the rank test
# rests on is not trustworthy, and the honest options are an exact permutation
# test or a refusal. This refuses, because the project's own methodology already
# requires more samples than this: S-1.7 certifies a noise floor from 20-30
# baseline runs before an experiment is allowed to start, so any comparison
# reaching this function with fewer than eight has skipped a step rather than
# found a case the approximation cannot serve.
MINIMUM_GROUP_SIZE = 8

# A summary needs two observations to have any dispersion at all. A fit needs
# three points at two or more distinct scales — two points define a line
# through themselves and say nothing about whether it is the right line.
MINIMUM_SAMPLES = 2
MINIMUM_SCALE_POINTS = 3
MINIMUM_DISTINCT_SCALES = 2

# Growth thresholds, on the exponent of the power-law fit. Recorded on every
# `Fit` so that a finding can cite the threshold it was classified under rather
# than leaving a reader to guess which version of this file was running.
CONSTANT_BELOW = 0.15
SUPERLINEAR_ABOVE = 1.15


class StatsError(Exception):
    """A statistic could not be computed from what was given."""


class Growth(StrEnum):
    """How a metric grows with the scale variable.

    Three classes because those are the three the backlog names and the three
    a screening step acts on. Note what that vocabulary cannot express:
    genuinely sublinear growth — a binary search, a cache that gets warmer —
    lands in `CONSTANT` or `LINEAR` depending on its exponent, and is not
    distinguished. The exponent itself is on the `Fit` for anyone who cares.
    """

    CONSTANT = "constant"
    LINEAR = "linear"
    SUPERLINEAR = "superlinear"


@dataclass(frozen=True)
class Summary:
    """What a set of samples looks like. No verdict."""

    n: int
    mean: float
    median: float
    stdev: float
    coefficient_of_variation: float


@dataclass(frozen=True)
class Fit:
    """How a metric grows against a scale variable, both ways of asking.

    `slope` answers "how much per item" and is the number to quote in a
    finding. `exponent` answers "what shape" and is the number the growth
    classification rests on. They come from two different fits, so each carries
    its own r²: a metric can be perfectly quadratic — `power_r_squared` of 1.0 —
    while the straight line through it is poor, and that disagreement is
    precisely the signal that something grows faster than it should.
    """

    slope: float
    intercept: float
    linear_r_squared: float
    exponent: float
    power_r_squared: float
    growth: Growth
    constant_below: float
    superlinear_above: float


@dataclass(frozen=True)
class RankTest:
    """The result of a Mann-Whitney U test between two independent samples."""

    u: float
    z: float
    p_value: float
    effect: float
    """Probability that a randomly drawn observation from `a` exceeds one from
    `b`, counting ties as half. 0.5 means the two are interchangeable. Reported
    because a p-value says only that a difference is detectable, never that it
    is worth anything — and with the sample sizes this project takes, a
    difference far too small to act on is still detectable."""

    n_a: int
    n_b: int


def stats(samples: Sequence[float]) -> Summary:
    """Summarize samples.

    The standard deviation is the sample one, dividing by n-1: these are
    observations drawn from the many runs the workload could have had, not the
    complete population of them.

    Raises:
        StatsError: fewer than two samples, which have no dispersion to
            report, or a sample that is NaN or infinite.
    """
    if len(samples) < MINIMUM_SAMPLES:
        message = f"need at least {MINIMUM_SAMPLES} samples to summarize, got {len(samples)}"
        raise StatsError(message)
    _require_finite(samples, "sample")

    mean = statistics.fmean(samples)
    stdev = statistics.stdev(samples)

    return Summary(
        n=len(samples),
        mean=mean,
        median=statistics.median(samples),
        stdev=stdev,
        coefficient_of_variation=_relative_dispersion(mean, stdev),
    )


def _relative_dispersion(mean: float, stdev: float) -> float:
    if mean != 0:
        return stdev / mean
    # Every sample zero is a real and common measurement — a workload that
    # issues no queries at all — and it is perfectly stable rather than
    # undefined. A mean of zero with spread around it has no meaningful
    # relative dispersion, and saying so is better than dividing.
    return 0.0 if stdev == 0 else math.inf


def fit_growth(
    scales: Sequence[float],
    metrics: Sequence[float],
    *,
    constant_below: float = CONSTANT_BELOW,
    superlinear_above: float = SUPERLINEAR_ABOVE,
) -> Fit:
    """Fit a metric against the scale it was measured at.

    Two fits are taken: a straight line, giving cost per item, and a line
    through log-log space, giving the exponent of `metric ∝ scale**exponent`.
    The classification uses the exponent.

    A flat metric is handled before either fit is attempted. "Queries constant
    at 7, 7, 7 across 100x scale" is the canonical exclusion this whole system
    is built to be able to publish, and it is also the input on which a log-log
    fit degenerates — zero variance in y, so r² is 0/0. It returns `CONSTANT`
    with an r² of 1.0, because a constant is exactly what a constant explains.

    Raises:
        StatsError: fewer than three points, fewer than two distinct scales, a
            non-finite value, or a non-positive value where the log-log fit
            needs one.
    """
    if len(scales) != len(metrics):
        message = f"got {len(scales)} scales and {len(metrics)} metrics"
        raise StatsError(message)
    if len(scales) < MINIMUM_SCALE_POINTS:
        message = (
            f"need at least {MINIMUM_SCALE_POINTS} scale points to fit growth, got {len(scales)}"
        )
        raise StatsError(message)
    if len(set(scales)) < MINIMUM_DISTINCT_SCALES:
        message = "every measurement was taken at the same scale; there is nothing to fit"
        raise StatsError(message)
    _require_finite(scales, "scale")
    _require_finite(metrics, "metric")

    if len(set(metrics)) == 1:
        return Fit(
            slope=0.0,
            intercept=metrics[0],
            linear_r_squared=1.0,
            exponent=0.0,
            power_r_squared=1.0,
            growth=Growth.CONSTANT,
            constant_below=constant_below,
            superlinear_above=superlinear_above,
        )

    line = statistics.linear_regression(scales, metrics)
    linear_r_squared = statistics.correlation(scales, metrics) ** 2

    _require_positive(scales, "scale")
    _require_positive(metrics, "metric")
    log_scales = [math.log(scale) for scale in scales]
    log_metrics = [math.log(metric) for metric in metrics]
    power = statistics.linear_regression(log_scales, log_metrics)
    power_r_squared = statistics.correlation(log_scales, log_metrics) ** 2

    return Fit(
        slope=line.slope,
        intercept=line.intercept,
        linear_r_squared=linear_r_squared,
        exponent=power.slope,
        power_r_squared=power_r_squared,
        growth=_classify(power.slope, constant_below, superlinear_above),
        constant_below=constant_below,
        superlinear_above=superlinear_above,
    )


def rank_test(a: Sequence[float], b: Sequence[float]) -> RankTest:
    """Two-sided Mann-Whitney U test for whether `a` and `b` differ.

    Ranks both samples together, sums the ranks belonging to `a`, and asks how
    unlikely that sum is if the two came from the same distribution. Only the
    ordering of the observations is used, which is what makes the result
    immune to the outliers and the skew that timing data always carries.

    Ties take average ranks, and the variance carries the standard tie
    correction — which is the exact null variance of U when ties are present,
    and is smaller than the untied formula rather than larger. A continuity
    correction is applied because a discrete statistic is being read off a
    continuous curve.

    Both samples must hold at least `MINIMUM_GROUP_SIZE` observations. Below
    that the normal approximation is not trustworthy and this refuses rather
    than returning a p-value that looks like the others.

    **What the p-value is worth**, measured against an exact permutation test
    at the smallest sample size this accepts: in the body of the distribution
    the two agree within a few percent; in the far tail the approximation is
    conservative by roughly an order of magnitude, understating a real
    difference rather than inventing one; and on heavily tied data — a metric
    taking three distinct values — it runs about 30% the other way, which is
    the unsafe direction. Counts are the tied case, and counts do not need this
    test: they are deterministic, and a difference in them is read directly.
    Timings, which this exists for, carry almost no ties.

    Read `effect` for how much, and `p_value` only for whether.

    Raises:
        StatsError: either sample is too small, or holds a non-finite value.
    """
    if len(a) < MINIMUM_GROUP_SIZE or len(b) < MINIMUM_GROUP_SIZE:
        message = (
            f"need at least {MINIMUM_GROUP_SIZE} observations per group, got {len(a)} and {len(b)}"
        )
        raise StatsError(message)
    _require_finite(a, "observation in the first sample")
    _require_finite(b, "observation in the second sample")

    n_a, n_b = len(a), len(b)
    ranks, tie_correction = _rank([*a, *b])

    rank_sum_a = sum(ranks[:n_a])
    u = rank_sum_a - n_a * (n_a + 1) / 2

    pairs = n_a * n_b
    expected = pairs / 2
    total = n_a + n_b
    variance = (pairs / 12) * ((total + 1) - tie_correction / (total * (total - 1)))

    if variance <= 0:
        # Every observation in both samples is identical. There is no evidence
        # of a difference and no scale on which to measure one.
        return RankTest(u=u, z=0.0, p_value=1.0, effect=0.5, n_a=n_a, n_b=n_b)

    # Continuity correction, applied toward the mean so it can only ever make
    # the result less significant.
    deviation = abs(u - expected)
    z = max(deviation - 0.5, 0.0) / math.sqrt(variance)
    p_value = 2 * (1 - _standard_normal_cdf(z))

    return RankTest(
        u=u,
        z=math.copysign(z, u - expected),
        p_value=min(p_value, 1.0),
        effect=u / pairs,
        n_a=n_a,
        n_b=n_b,
    )


def _require_finite(values: Sequence[float], name: str) -> None:
    """Refuse NaN and infinity before any of them reach a comparison.

    NaN is the dangerous one, and it is dangerous quietly. Every comparison
    against it is false, so sorting produces an arbitrary order, no ties are
    detected, ranks come out meaningless — and the rank test then returns a
    confident, well-formed, entirely fictional p-value. Measured before this
    check existed: eight NaNs against eight ones reported p = 0.0004.

    That is the failure this project forbids by name. A missing measurement is
    recoverable; a manufactured one is not.
    """
    for index, value in enumerate(values):
        if not math.isfinite(value):
            message = (
                f"{name} at position {index} is {value}, which cannot be ranked or "
                "summarized; a non-finite measurement is a failed measurement"
            )
            raise StatsError(message)


def _classify(exponent: float, constant_below: float, superlinear_above: float) -> Growth:
    if exponent < constant_below:
        return Growth.CONSTANT
    if exponent < superlinear_above:
        return Growth.LINEAR
    return Growth.SUPERLINEAR


def _require_positive(values: Sequence[float], name: str) -> None:
    for index, value in enumerate(values):
        if value <= 0:
            message = (
                f"{name} {value} at position {index} is not positive, so no power law "
                "can be fitted through it; subtract the baseline or measure at a scale "
                "where the metric is non-zero"
            )
            raise StatsError(message)


def _rank(values: Sequence[float]) -> tuple[list[float], float]:
    """Average ranks for `values`, plus the tie correction term Σ(t³-t).

    Ties have to share a rank rather than being broken arbitrarily. Breaking
    them by position would make the statistic depend on which sample happened
    to be passed first, which for quantized timings is most of the data.
    """
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    correction = 0.0

    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1

        tied = end - position + 1
        average = (position + end + 2) / 2  # ranks are 1-based
        for index in order[position : end + 1]:
            ranks[index] = average
        correction += tied**3 - tied

        position = end + 1

    return ranks, correction


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

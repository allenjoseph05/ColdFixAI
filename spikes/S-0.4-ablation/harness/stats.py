"""Statistics for the S-0.4 ablation spike.

Pure functions, no dependencies beyond the standard library, deliberately.
Adding scipy for a spike would be a dependency the project then has to justify
keeping, and the project rule is that counting and curve fitting are code rather
than an integration. Everything here is small enough to check by eye.

The test is **Mann-Whitney U**, not Welch's t. Response latency is bounded below,
right-skewed, and occasionally spiked by a garbage collection or a page fault;
the mean and its standard error are the wrong summary for that shape, and a
single 200 ms outlier moves a t-statistic much further than it moves reality.
Mann-Whitney asks only whether one sample tends to rank above the other, which
is exactly the question "is this delta separable" is asking.

**Cliff's delta reports effect size**, because separability alone is a weak
claim: with enough repetitions a 0.3 ms difference separates cleanly and means
nothing. A p-value says the delta is real; Cliff's delta says whether it is
large enough to act on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist, fmean, median, stdev

# Romano et al.'s conventional thresholds for interpreting Cliff's delta.
NEGLIGIBLE = 0.147
SMALL = 0.33
MEDIUM = 0.474

# Separability threshold. Deliberately stricter than the customary 0.05: this
# spike's own calibration produced p-values under 0.01 on shifts the injection
# could not have caused, so the bar is set where a spurious result had to work
# for it.
ALPHA = 0.01

# A standard deviation is undefined below this many samples.
MIN_SAMPLES = 2


@dataclass(frozen=True)
class Summary:
    """Descriptive statistics for one condition."""

    n: int
    mean: float
    median: float
    stdev: float
    cv: float
    minimum: float
    maximum: float

    def line(self, label: str) -> str:
        return (
            f"{label:<28} n={self.n:<3} "
            f"median={self.median * 1000:8.2f} ms  "
            f"mean={self.mean * 1000:8.2f} ms  "
            f"sd={self.stdev * 1000:7.2f} ms  "
            f"CV={self.cv * 100:5.2f}%  "
            f"min={self.minimum * 1000:8.2f}  max={self.maximum * 1000:8.2f}"
        )


def summarize(samples: list[float]) -> Summary:
    """Descriptive statistics, including the coefficient of variation.

    CV is the sample standard deviation over the mean. It is the AC's requested
    figure because it is scale-free: a 5 ms spread means something different on a
    20 ms endpoint than on a 2 s one, and CV lets the two be compared.
    """
    if len(samples) < MIN_SAMPLES:
        raise ValueError("need at least two samples for a standard deviation")
    mean = fmean(samples)
    sd = stdev(samples)
    if mean <= 0:
        raise ValueError("non-positive mean; CV is undefined")
    return Summary(
        n=len(samples),
        mean=mean,
        median=median(samples),
        stdev=sd,
        cv=sd / mean,
        minimum=min(samples),
        maximum=max(samples),
    )


def _ranks(values: list[float]) -> list[float]:
    """Ranks, 1-based, with ties assigned their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j + 2) / 2  # +2 converts two 0-based bounds to 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def _tie_correction(values: list[float]) -> float:
    """Sum of (t^3 - t) over tied groups, for the variance correction."""
    counts: dict[float, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sum(t**3 - t for t in counts.values() if t > 1)


@dataclass(frozen=True)
class Comparison:
    """Result of comparing two conditions."""

    u: float
    z: float
    p_value: float
    cliffs_delta: float
    median_shift: float
    median_ratio: float

    @property
    def separable(self) -> bool:
        """Separable at p < 0.01 *and* with a non-negligible effect.

        Both halves matter. Requiring only significance would call a 1%
        difference separable given enough repetitions; requiring only effect size
        would call noise separable when the samples happen to sit apart.
        """
        return self.p_value < ALPHA and abs(self.cliffs_delta) >= MEDIUM

    @property
    def effect_label(self) -> str:
        """Romano et al.'s conventional thresholds for Cliff's delta."""
        d = abs(self.cliffs_delta)
        if d < NEGLIGIBLE:
            return "negligible"
        if d < SMALL:
            return "small"
        if d < MEDIUM:
            return "medium"
        return "large"


def compare(baseline: list[float], treatment: list[float]) -> Comparison:
    """Mann-Whitney U (two-sided, normal approximation, tie-corrected).

    The normal approximation is sound here: it is conventionally acceptable from
    about n=8 per group and this spike uses 20.

    A continuity correction of 0.5 is applied toward the null, which makes the
    p-value slightly conservative — the right direction to err in for a spike
    whose conclusion is "you may trust this instrument".
    """
    n1, n2 = len(baseline), len(treatment)
    if n1 == 0 or n2 == 0:
        raise ValueError("both samples must be non-empty")

    pooled = baseline + treatment
    ranks = _ranks(pooled)
    rank_sum_baseline = sum(ranks[:n1])
    u1 = rank_sum_baseline - n1 * (n1 + 1) / 2

    mu = n1 * n2 / 2
    n = n1 + n2
    correction = _tie_correction(pooled)
    variance = (n1 * n2 / 12) * ((n + 1) - correction / (n * (n - 1)))
    if variance <= 0:
        # Every value identical — no evidence of any difference.
        return Comparison(u1, 0.0, 1.0, 0.0, 0.0, 1.0)

    sigma = math.sqrt(variance)
    z = (abs(u1 - mu) - 0.5) / sigma
    z = max(z, 0.0)
    p = 2 * (1 - NormalDist().cdf(z))

    # Signed so that a treatment faster than baseline reads negative.
    signed_z = -z if u1 > mu else z

    median_baseline = median(baseline)
    median_treatment = median(treatment)

    return Comparison(
        u=u1,
        z=signed_z,
        p_value=min(p, 1.0),
        # 2*U/(n1*n2) - 1 is Cliff's delta; negated so the sign convention
        # matches z above (negative = treatment faster).
        cliffs_delta=-((2 * u1) / (n1 * n2) - 1),
        median_shift=median_treatment - median_baseline,
        median_ratio=median_treatment / median_baseline if median_baseline else math.inf,
    )

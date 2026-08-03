"""The arithmetic is checked against definitions, not against itself.

Two tests carry the story's note that the significance test must be rank-based
rather than a t-test:

- `test_the_result_depends_only_on_the_ordering` — the defining property of a
  rank test, and one no mean-based test has.
- `test_a_single_outlier_barely_moves_the_verdict` — the practical consequence,
  on data shaped the way timings actually are.

`test_the_u_statistic_matches_counting_the_pairs_by_hand` is the correctness
proof for the one piece of real statistics in the module: U is *defined* as the
number of pairs where an observation from the first sample beats one from the
second, and that definition is computed here directly and compared against the
rank-sum formula the implementation uses.
"""

from __future__ import annotations

import itertools
import math
import statistics

import pytest

from coldfix.bench.stats import (
    MINIMUM_GROUP_SIZE,
    Growth,
    StatsError,
    fit_growth,
    rank_test,
    stats,
)

STEADY = [10.0, 10.2, 9.8, 10.1, 9.9, 10.0, 10.3, 9.7, 10.1, 9.9]
SLOWER = [12.0, 12.2, 11.8, 12.1, 11.9, 12.0, 12.3, 11.7, 12.1, 11.9]


def brute_force_u(a: list[float], b: list[float]) -> float:
    """U by its definition: pairwise wins, with ties counting half."""
    return sum(1.0 if left > right else 0.5 if left == right else 0.0 for left in a for right in b)


def exact_permutation_p(a: list[float], b: list[float]) -> float:
    """The p-value by its definition, enumerating every relabelling.

    What the normal approximation in `rank_test` approximates: of all the ways
    these observations could have been split into two groups of this size, what
    fraction give a U at least this far from the middle. Feasible only because
    the groups are small — this is C(16, 8) = 12,870 splits.
    """
    pool = [*a, *b]
    group = len(a)
    middle = group * len(b) / 2
    observed = abs(brute_force_u(a, b) - middle)

    extreme = 0
    total = 0
    for picked in itertools.combinations(range(len(pool)), group):
        chosen = set(picked)
        left = [pool[index] for index in picked]
        right = [pool[index] for index in range(len(pool)) if index not in chosen]
        if abs(brute_force_u(left, right) - middle) >= observed - 1e-12:
            extreme += 1
        total += 1

    return extreme / total


# ------------------------------------------------------------------- summary


def test_summarizes_against_the_standard_library() -> None:
    summary = stats(STEADY)

    assert summary.n == 10
    assert summary.mean == pytest.approx(statistics.fmean(STEADY))
    assert summary.median == pytest.approx(statistics.median(STEADY))
    assert summary.stdev == pytest.approx(statistics.stdev(STEADY))
    assert summary.coefficient_of_variation == pytest.approx(summary.stdev / summary.mean)


def test_the_standard_deviation_is_the_sample_one() -> None:
    """Divided by n-1. These are observations, not the population of all runs."""
    samples = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]

    assert stats(samples).stdev == pytest.approx(2.13809, abs=1e-5)  # n-1
    assert stats(samples).stdev != pytest.approx(2.0)  # n, the population value


def test_a_metric_that_is_always_zero_is_perfectly_stable() -> None:
    """A workload issuing no queries at all is a real and common measurement."""
    summary = stats([0.0, 0.0, 0.0, 0.0])

    assert summary.mean == 0
    assert summary.coefficient_of_variation == 0.0


def test_one_sample_has_no_dispersion_to_report() -> None:
    with pytest.raises(StatsError, match="at least 2 samples"):
        stats([1.0])


# ----------------------------------------------------------------- the fits


def test_a_linear_metric_is_classified_linear() -> None:
    fit = fit_growth([10, 20, 40, 80], [21.0, 41.0, 81.0, 161.0])

    assert fit.growth is Growth.LINEAR
    assert fit.slope == pytest.approx(2.0)
    assert fit.exponent == pytest.approx(1.0, abs=0.05)
    assert fit.linear_r_squared == pytest.approx(1.0)


def test_a_quadratic_metric_is_classified_superlinear() -> None:
    scales = [10, 20, 40, 80]
    fit = fit_growth(scales, [float(scale**2) for scale in scales])

    assert fit.growth is Growth.SUPERLINEAR
    assert fit.exponent == pytest.approx(2.0)
    assert fit.power_r_squared == pytest.approx(1.0)


def test_a_flat_metric_is_the_exclusion_the_system_exists_to_publish() -> None:
    """ "Queries constant at 7, 7, 7 across 100x scale" — a shippable result.

    It is also the input a log-log fit degenerates on: zero variance in the
    metric makes r² a division of zero by zero. Handled before either fit is
    attempted, because the answer is not undefined — a constant is exactly what
    a constant explains.
    """
    fit = fit_growth([10, 100, 1000], [7.0, 7.0, 7.0])

    assert fit.growth is Growth.CONSTANT
    assert fit.exponent == 0.0
    assert fit.slope == 0.0
    assert fit.power_r_squared == 1.0


def test_the_two_fits_disagree_when_growth_is_not_linear() -> None:
    """Which disagreement is itself the signal.

    A quadratic is perfectly explained by a power law and poorly by a straight
    line, so a caller reading only `slope` would quote a cost per item that is
    wrong at every scale but one.
    """
    scales = [1, 2, 4, 8, 16, 32]
    fit = fit_growth(scales, [float(scale**2) for scale in scales])

    assert fit.power_r_squared == pytest.approx(1.0)
    assert fit.linear_r_squared < 0.98


def test_the_thresholds_are_recorded_on_the_result() -> None:
    """An exclusion carries its preconditions, and a classification is one."""
    fit = fit_growth([10, 20, 40], [1.0, 2.0, 4.0])

    assert (fit.constant_below, fit.superlinear_above) == (0.15, 1.15)


def test_the_thresholds_can_be_stated_per_fit() -> None:
    scales = [10, 20, 40]
    metrics = [10.0, 21.0, 44.0]  # exponent just above 1

    assert fit_growth(scales, metrics).growth is Growth.LINEAR
    assert fit_growth(scales, metrics, superlinear_above=1.02).growth is Growth.SUPERLINEAR


def test_two_points_are_not_enough_to_fit() -> None:
    with pytest.raises(StatsError, match="at least 3 scale points"):
        fit_growth([10, 100], [1.0, 10.0])


def test_measuring_everything_at_one_scale_is_refused() -> None:
    with pytest.raises(StatsError, match="same scale"):
        fit_growth([10, 10, 10], [1.0, 2.0, 3.0])


def test_a_zero_metric_keeps_the_linear_fit_and_drops_only_the_exponent() -> None:
    """Zero at the smallest scale is an ordinary count, not a broken measurement.

    A cache that covers the small case, or a queryset that never fires, gives
    exactly this shape. No power law passes through zero — but least squares
    does, and refusing the whole call threw away a computable fit.
    """
    fit = fit_growth([10, 20, 40], [0.0, 2.0, 6.0])

    assert fit.slope == pytest.approx(0.2)
    assert fit.intercept == pytest.approx(-2.0)
    assert fit.linear_r_squared == pytest.approx(1.0)
    assert not fit.power_law_fitted
    assert fit.exponent is None
    assert fit.power_r_squared is None
    assert fit.growth is None


def test_growth_is_never_guessed_from_the_line_when_the_exponent_is_missing() -> None:
    """The thresholds are defined on the exponent, so nothing else may set it.

    A perfectly straight line through a zero would classify as LINEAR under any
    reasonable second rule, and that is exactly why it must not: two findings
    reading `LINEAR` have to have been decided the same way.
    """
    straight = fit_growth([1, 2, 3], [0.0, 1.0, 2.0])

    assert straight.linear_r_squared == pytest.approx(1.0)
    assert straight.growth is None


def test_a_negative_metric_also_only_disables_the_power_fit() -> None:
    """A delta against a baseline is signed, and is still a real measurement."""
    fit = fit_growth([10, 20, 40], [-1.0, 2.0, 8.0])

    assert fit.slope > 0
    assert fit.growth is None


def test_a_flat_metric_at_zero_is_still_constant() -> None:
    """The flat path predates the logarithm and must not have been caught by it.

    Zero queries at every scale is a publishable exclusion, and it is the one
    all-zero input that still has an answer.
    """
    fit = fit_growth([1, 10, 100], [0.0, 0.0, 0.0])

    assert fit.growth is Growth.CONSTANT
    assert fit.exponent == 0.0


# -------------------------------------------------------------- the rank test


def test_the_u_statistic_matches_counting_the_pairs_by_hand() -> None:
    """U is defined as pairwise wins. The implementation computes it from ranks.

    Those are the same number, and this asserts it on data with ties in it,
    where the rank-sum shortcut is easiest to get wrong.
    """
    a = [1.0, 2.0, 2.0, 3.0, 5.0, 8.0, 8.0, 9.0, 13.0]
    b = [2.0, 4.0, 4.0, 6.0, 6.0, 7.0, 8.0, 10.0, 11.0]

    assert rank_test(a, b).u == pytest.approx(brute_force_u(a, b))


def test_identical_distributions_are_not_declared_different() -> None:
    assert rank_test(STEADY, list(STEADY)).p_value > 0.5


def test_a_clear_shift_is_detected() -> None:
    result = rank_test(STEADY, SLOWER)

    assert result.p_value < 0.001
    assert result.effect == 0.0, "every observation in the first sample is the smaller"


def test_the_result_depends_only_on_the_ordering() -> None:
    """The defining property of a rank test, and the reason the note asks for one.

    Exponentiating every observation changes every mean, every variance and
    every t-statistic, while leaving the ordering untouched. A rank test cannot
    notice. Timing data is skewed and heavy-tailed, so a test that survives a
    monotone transformation is a test that survives the shape of the data.
    """
    plain = rank_test(STEADY, SLOWER)
    transformed = rank_test(
        [math.exp(value) for value in STEADY],
        [math.exp(value) for value in SLOWER],
    )

    assert transformed.p_value == pytest.approx(plain.p_value)
    assert transformed.u == plain.u


def test_a_single_outlier_does_not_reverse_the_verdict() -> None:
    """The practical form of the same property, and the sharper one.

    One observation thirty times the median — a garbage collection, a cold
    cache, a noisy neighbour — is enough to reverse a comparison of means: it
    drags the faster sample's mean to three times the slower sample's, so
    anything reading means concludes the opposite of the truth. The rank test
    moves by one rank and still answers in the right direction.

    Its p-value does move, by an order of magnitude, and that is honest rather
    than a defect: with ten observations per group, one of them crossing the
    whole of the other group is real evidence. What does not happen is the
    conclusion flipping.
    """
    with_outlier = [*STEADY[:-1], 300.0]

    # A mean-based comparison now has the faster sample looking three times
    # slower than the slower one.
    assert statistics.fmean(with_outlier) > statistics.fmean(SLOWER) * 3

    clean = rank_test(STEADY, SLOWER)
    noisy = rank_test(with_outlier, SLOWER)

    assert clean.effect < 0.5, "the first sample is the faster one"
    assert noisy.effect < 0.5, "and still is, with the outlier in it"
    assert noisy.p_value < 0.01


def test_the_test_is_symmetric() -> None:
    forward = rank_test(STEADY, SLOWER)
    backward = rank_test(SLOWER, STEADY)

    assert forward.p_value == pytest.approx(backward.p_value)
    assert forward.effect + backward.effect == pytest.approx(1.0)
    assert math.copysign(1, forward.z) != math.copysign(1, backward.z)


def test_two_samples_of_the_same_constant_are_not_significant() -> None:
    """No difference, and no scale on which to measure one."""
    result = rank_test([5.0] * 10, [5.0] * 10)

    assert result.p_value == 1.0
    assert result.effect == 0.5


def test_heavily_tied_data_is_where_the_approximation_is_weakest() -> None:
    """Pinned because it is a limitation, not because it is a feature.

    With only three distinct values the statistic moves in steps far larger
    than the half-unit continuity correction assumes, and a normal curve fits
    it badly. Measured against enumeration the reported p-value comes out
    around 30% low — the unsafe direction, overstating the evidence.

    The tie correction is applied and is the exact null variance under ties;
    it is not what causes this, and it makes the value smaller rather than
    larger. The discreteness does.

    This is tolerable because of what the tied case *is*: metrics that take a
    handful of values are counts, and counts are deterministic — a difference
    in them is read directly rather than tested. Timings, which this function
    exists for, carry almost no ties.
    """
    tied_a = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0]
    tied_b = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0]

    approximate = rank_test(tied_a, tied_b).p_value
    exact = exact_permutation_p(tied_a, tied_b)

    assert approximate < exact, "the known direction of the error"
    assert approximate > 0.05, "and far enough from any threshold to change nothing here"


def test_the_p_value_agrees_with_the_exact_test_in_the_body() -> None:
    """Checked against enumeration, which is what the normal curve stands in for.

    Two samples that overlap heavily, where the answer is "no evidence of a
    difference" and the exact fraction of relabellings that are more extreme
    can be counted directly.
    """
    a = STEADY[:8]
    b = [value + 0.1 for value in a]

    assert rank_test(a, b).p_value == pytest.approx(exact_permutation_p(a, b), rel=0.1)


def test_the_p_value_is_conservative_in_the_tail() -> None:
    """Where the approximation is at its worst, it errs toward saying nothing.

    At eight observations per group the normal curve puts roughly six times
    more probability in this tail than enumeration does, so a real difference
    is understated rather than a spurious one manufactured. That is the
    acceptable direction for a number that gates a finding, and it is a reason
    to read `effect` for magnitude rather than reading `p_value` as a
    probability.
    """
    a, b = STEADY[:8], SLOWER[:8]

    approximate = rank_test(a, b).p_value
    exact = exact_permutation_p(a, b)

    assert approximate > exact
    assert approximate < 0.01, "still decisive, just less so than the truth"


def test_a_nan_cannot_produce_a_confident_answer() -> None:
    """The sharpest failure this module can have, and it is silent by nature.

    Every comparison against NaN is false. So sorting puts the values in an
    arbitrary order, the tie detector sees no ties, ranks come out meaningless,
    and the arithmetic downstream runs to completion and returns a well-formed
    p-value. Measured before the guard existed: eight NaNs against eight ones
    reported **p = 0.0004** — a decisive, entirely fictional finding.

    Nothing about that output looks wrong. It is the exact shape of a real
    result, which is why the guard is at the door rather than in the caller.
    """
    with pytest.raises(StatsError, match="non-finite"):
        rank_test([math.nan] * 8, [1.0] * 8)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_no_entry_point_accepts_a_non_finite_value(bad: float) -> None:
    """A failed measurement must not be summarized, fitted, or tested."""
    with pytest.raises(StatsError, match="non-finite"):
        stats([1.0, bad, 3.0])
    with pytest.raises(StatsError, match="non-finite"):
        fit_growth([10, 20, 40], [1.0, bad, 4.0])
    with pytest.raises(StatsError, match="non-finite"):
        rank_test([1.0] * 8, [*([2.0] * 7), bad])


def test_too_few_observations_is_refused_rather_than_approximated() -> None:
    """The normal approximation is not trustworthy here, and neither is a p-value.

    S-1.7 certifies a noise floor from 20-30 baseline runs before an experiment
    may start, so a comparison arriving with fewer than eight per group has
    skipped a step rather than found a case this cannot serve.
    """
    small = [1.0] * (MINIMUM_GROUP_SIZE - 1)

    with pytest.raises(StatsError, match="at least 8 observations"):
        rank_test(small, [2.0] * MINIMUM_GROUP_SIZE)

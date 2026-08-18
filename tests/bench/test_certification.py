"""Certification has to mean something, not merely return a number.

The number `minimum_detectable_effect` returns is only worth having if it
predicts what a real comparison will actually find. Two tests carry that
weight: `test_the_detectable_effect_holds_up_against_fresh_resamples`, which
checks it was not overfitted to its own seed, and
`test_the_certified_floor_predicts_what_compare_finds`, which runs `compare()`
at effects either side of the certified floor and requires the floor to have
been right about both.

Most tests here work on synthetic samples rather than real timings. The shape
of a timing distribution is what the estimate depends on, and a lognormal with
a stated coefficient of variation reproduces that shape deterministically —
where busy-waiting reproduces it slowly and with the machine's own noise mixed
in, which is the thing under test rather than an input to it.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from time import perf_counter

import pytest

from coldfix.bench.certification import (
    ALPHA,
    LARGEST_EFFECT,
    MINIMUM_SAMPLES,
    TARGET_POWER,
    Certification,
    NoiseFloorTooHighError,
    certify,
    minimum_detectable_effect,
)
from coldfix.bench.interleaving import compare
from coldfix.bench.stats import rank_test


def lognormal_samples(n: int, median: float, cv: float, seed: int) -> tuple[float, ...]:
    """Samples shaped like timings: positive, right-skewed, no upper bound."""
    rng = random.Random(seed)
    sigma = math.sqrt(math.log(1 + cv**2))
    return tuple(median * math.exp(rng.gauss(0.0, sigma)) for _ in range(n))


def detection_rate(
    samples: tuple[float, ...], effect: float, seed: int, trials: int = 400
) -> float:
    """How often a shift of `effect` is actually caught, on fresh draws.

    Deliberately re-derived here rather than imported. It is the definition the
    module's estimate is supposed to satisfy, and running it from a different
    seed with more trials is what turns "the estimate agrees with itself" into
    a claim about the estimate.
    """
    rng = random.Random(seed)
    n = len(samples)
    detected = 0
    for _ in range(trials):
        control = rng.choices(samples, k=n)
        treatment = [value * (1 - effect) for value in rng.choices(samples, k=n)]
        if rank_test(control, treatment).p_value < ALPHA:
            detected += 1
    return detected / trials


def busy(seconds: float) -> None:
    end = perf_counter() + seconds
    while perf_counter() < end:
        pass


# --------------------------------------------------- the estimate means something


def test_the_detectable_effect_holds_up_against_fresh_resamples() -> None:
    """At the reported floor the test really does fire as often as promised.

    Checked from a different seed and with twice the trials, so an estimate
    that had merely memorised its own resampling would not survive.
    """
    samples = lognormal_samples(25, median=0.100, cv=0.05, seed=7)

    floor = minimum_detectable_effect(samples, seed=7)
    achieved = detection_rate(samples, floor, seed=9001)

    assert achieved >= TARGET_POWER - 0.10, (
        f"floor of {floor:.1%} only detected {achieved:.0%} of the time on fresh draws"
    )


def test_an_effect_well_below_the_floor_is_not_reliably_detected() -> None:
    """The complement. A floor that everything clears is not a floor.

    Without this, returning a very small number would satisfy the test above
    trivially and certify every harness on the planet.
    """
    samples = lognormal_samples(25, median=0.100, cv=0.05, seed=7)

    floor = minimum_detectable_effect(samples, seed=7)
    achieved = detection_rate(samples, floor / 3, seed=9001)

    assert achieved < TARGET_POWER, (
        f"a third of the floor ({floor / 3:.1%}) was still detected {achieved:.0%} of the time"
    )


def test_a_noisier_baseline_raises_the_floor() -> None:
    """The relationship the whole idea rests on, asserted rather than assumed."""
    quiet = minimum_detectable_effect(lognormal_samples(25, 0.1, cv=0.02, seed=3), seed=3)
    noisy = minimum_detectable_effect(lognormal_samples(25, 0.1, cv=0.20, seed=3), seed=3)

    assert noisy > quiet * 2, f"cv 2% gave {quiet:.1%} and cv 20% gave {noisy:.1%}"


def test_more_samples_lower_the_floor_on_the_same_distribution() -> None:
    """Certification has to reward paying for more evidence."""
    few = minimum_detectable_effect(lognormal_samples(20, 0.1, cv=0.10, seed=11), seed=5)
    many = minimum_detectable_effect(lognormal_samples(80, 0.1, cv=0.10, seed=11), seed=5)

    assert many < few, f"20 samples gave {few:.1%} and 80 gave {many:.1%}"


def test_a_workload_too_fast_to_time_cannot_be_certified() -> None:
    """Every sample identical at the clock's resolution.

    Nothing can be separated from anything, so no effect is detectable and the
    honest answer is the cap rather than a small number that reads like a good
    result.
    """
    assert minimum_detectable_effect((0.0,) * 25, seed=1) == LARGEST_EFFECT


def test_the_estimate_is_reproducible_from_its_seed() -> None:
    samples = lognormal_samples(25, 0.1, cv=0.08, seed=2)

    assert minimum_detectable_effect(samples, seed=42) == minimum_detectable_effect(
        samples, seed=42
    )


# ------------------------------------------------------------------ certify()


def test_a_quiet_workload_certifies_against_a_loose_target() -> None:
    result = certify(lambda: busy(0.002), workload="quiet", target_effect=0.50, seed=1)

    assert result.certified
    assert result.refusal is None
    assert result.n == MINIMUM_SAMPLES + 5
    assert len(result.samples) == result.n
    assert result.coefficient_of_variation >= 0.0
    assert 0 < result.minimum_detectable_effect <= 0.50


def test_a_target_below_the_floor_is_refused_with_a_message_that_says_what_to_do() -> None:
    """AC 3. The refusal has to be actionable, not merely correct."""
    with pytest.raises(NoiseFloorTooHighError) as caught:
        certify(lambda: busy(0.002), workload="checkout", target_effect=0.0001, seed=1)

    refusal = caught.value.certification.refusal
    assert refusal is not None
    assert "checkout" in refusal
    assert "smallest change this harness can detect" in refusal
    assert "coefficient of variation" in refusal
    assert "take more samples" in refusal


def test_the_refusal_carries_the_certification_so_it_can_still_be_recorded() -> None:
    """A refusal is a result, and a result that cannot be logged did not happen."""
    with pytest.raises(NoiseFloorTooHighError) as caught:
        certify(lambda: busy(0.002), workload="checkout", target_effect=0.0001, seed=1)

    certification = caught.value.certification
    assert not certification.certified
    assert certification.target_effect == 0.0001
    assert len(certification.samples) == 25


def test_fewer_than_twenty_baseline_runs_is_refused() -> None:
    """AC 1. A floor estimated from too little evidence would justify a refusal."""
    calls = 0

    def counted() -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(ValueError, match="at least 20 baseline runs"):
        certify(counted, workload="x", target_effect=0.1, n=19)

    assert calls == 0, "the baseline ran before the refusal"


def test_more_than_thirty_runs_is_allowed() -> None:
    """The 20-30 range is guidance about sufficiency, not a ceiling.

    Refusing a caller who can afford more samples would be refusing better
    evidence. Recorded as a deliberate reading of the AC.
    """
    result = certify(lambda: busy(0.001), workload="generous", target_effect=0.9, n=40)

    assert result.n == 40


def test_a_nonsense_target_effect_is_refused() -> None:
    with pytest.raises(ValueError, match="fraction between 0 and 1"):
        certify(lambda: None, workload="x", target_effect=15.0)


# ------------------------------------------------ recordable in the experiment log


def test_the_certification_round_trips_through_json() -> None:
    """AC 4, as far as S-1.7 owns it. S-8.4 does the appending."""
    original = certify(lambda: busy(0.002), workload="checkout", target_effect=0.9, seed=77)

    restored = Certification.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.samples == original.samples
    assert restored.seed == 77


def test_serialization_is_byte_stable_across_dumps() -> None:
    """S-8.4 needs entries to render identically or the prompt cache misses."""
    result = certify(lambda: busy(0.002), workload="checkout", target_effect=0.9, seed=77)

    assert result.model_dump_json() == result.model_dump_json()


def test_the_recorded_parameters_explain_the_verdict_without_the_code() -> None:
    """Someone reading the log a year later must not have to guess the settings."""
    result = certify(lambda: busy(0.002), workload="checkout", target_effect=0.9, seed=5)
    written = result.model_dump()

    assert written["alpha"] == ALPHA
    assert written["power"] == TARGET_POWER
    assert written["bootstrap_trials"] > 0
    assert written["seed"] == 5


# ---------------------------------------------- the floor predicts the real thing


def jittery(base: float, cv: float, rng: random.Random) -> Callable[[], None]:
    """A workload with a realistic amount of run-to-run variation.

    A bare busy-wait is a near-perfect instrument — its certified floor comes
    out around 0.02%, which is true and useless for this test, because then no
    effect is below the floor. Real workloads carry noise from the scheduler,
    the allocator and the machine, so the noise is put in deliberately and at a
    known size.
    """

    def work() -> None:
        busy(max(base * (1 + rng.gauss(0.0, cv)), base / 10))

    return work


def test_the_certified_floor_predicts_what_compare_finds() -> None:
    """End to end: certify a workload, then hold `compare()` to the promise.

    An effect well above the certified floor must be found every time, and one
    well below it must almost never be. This is the only test that closes the
    loop between S-1.7's estimate and S-1.6's measurement, and it is why the
    estimate simulates the rank test rather than using a formula from a
    distribution timings do not follow.

    Both claims are probabilistic, so both are checked across several sessions
    rather than one. A single run either side of the floor would be asserting
    that an 80%-power test fires 100% of the time, which is a flaky test
    dressed up as a strict one.
    """
    base = 0.002
    noise = 0.10
    rng = random.Random(4242)

    certification = certify(
        jittery(base, noise, rng), workload="jittery", target_effect=0.9, seed=13
    )
    floor = certification.minimum_detectable_effect
    assert 0.01 < floor < 0.5, f"the injected noise should give a usable floor, got {floor:.1%}"

    detected_above = sum(
        compare(
            jittery(base, noise, rng),
            jittery(base * (1 - floor * 4), noise, rng),
            20,
            seed=seed,
        ).rank.p_value
        < ALPHA
        for seed in range(5)
    )
    assert detected_above == 5, (
        f"an effect four times the certified floor of {floor:.1%} was missed "
        f"in {5 - detected_above} of 5 sessions"
    )

    detected_below = sum(
        compare(
            jittery(base, noise, rng),
            jittery(base * (1 - floor / 6), noise, rng),
            20,
            seed=seed,
        ).rank.p_value
        < ALPHA
        for seed in range(5)
    )
    assert detected_below <= 1, (
        f"an effect a sixth of the certified floor of {floor:.1%} was reported as real "
        f"in {detected_below} of 5 sessions"
    )

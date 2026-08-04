"""Prove the harness can see the effect it is hunting, before it hunts.

The seventh operation of the lab bench and the last of Epic 1. Everything
before it measures; this one measures *the measuring*, and refuses to let an
investigation start on an instrument too noisy to answer the question being
asked of it.

**Why this exists.** An optimizer that cannot detect a 5% improvement will
still report results on a search for 5% improvements — it will report noise,
confidently, in exactly the shape of a finding. S-0.4 measured this project's
own floor at roughly 20ms on a 350ms endpoint, so a real 2% improvement there
is invisible no matter how many times it is run. Certification is the step that
turns that from a thing someone might remember into a thing the harness knows.

No evolve-style framework certifies its evaluator before optimizing against it
(`10-BACKLOG.md`, S-1.7). This is the novel part of Epic 1.

**The minimum detectable effect is simulated, not derived from a formula.**
The textbook expression for a minimum detectable effect assumes normally
distributed samples, and `stats.py` explains at length why timing distributions
are not: bounded below by the fastest possible execution, unbounded above, and
routinely bimodal on whether a cache was hit. Using that formula here would
reintroduce the assumption the rank test was chosen to avoid. Instead the
baseline is resampled, scaled by a candidate effect, and put through
`rank_test()` — the same test the real comparison will use — and the effect is
tightened until the test detects it as often as `TARGET_POWER` requires. What
comes back is a statement about the instrument that will actually be used.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from coldfix.bench.stats import rank_test, stats
from coldfix.bench.timing import time

# The certification runs the baseline this many times unless told otherwise.
# The story asks for 20-30; 25 sits in the middle of it.
DEFAULT_SAMPLES = 25

# Below this the estimate is not worth having. There is deliberately **no upper
# bound**: the 20-30 range in the story is guidance about what is enough, and
# refusing a caller who can afford 60 samples would be refusing better evidence.
MINIMUM_SAMPLES = 20

# Fixed rather than exposed as arguments. They are recorded on every
# certification, so a result can be read without knowing what was passed, and a
# second case can add the knob if one ever appears.
ALPHA = 0.05
TARGET_POWER = 0.80
BOOTSTRAP_TRIALS = 200

# The search for the smallest detectable effect runs over (0, LARGEST_EFFECT].
# An instrument that cannot see a workload getting 99% faster cannot see
# anything, and saying so is more useful than searching further.
LARGEST_EFFECT = 0.99
_SEARCH_STEPS = 12
_SEED_BITS = 63


class CertificationError(Exception):
    """The noise floor could not be established, or was too high to work with."""


class Certification(BaseModel):
    """What a harness can and cannot see, and the evidence for it.

    A Pydantic model rather than a frozen dataclass like the rest of the bench,
    because this artifact is meant to outlive the process: S-8.4 appends it to
    the experiment log, and S-8.4's own criteria require serialization to be
    stable and cache-friendly. Field order here is the serialization order and
    should not be shuffled — the append-only log's prompt-cache prefix depends
    on byte-identical rendering of entries that have not changed.

    `minimum_detectable_effect` and `target_effect` are both **relative**: 0.05
    means five percent of the baseline median, not fifty milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    workload: str
    n: int
    samples: tuple[float, ...]

    mean_seconds: float
    median_seconds: float
    stdev_seconds: float
    coefficient_of_variation: float

    minimum_detectable_effect: float
    target_effect: float
    certified: bool
    refusal: str | None = None

    alpha: float = ALPHA
    power: float = TARGET_POWER
    bootstrap_trials: int = BOOTSTRAP_TRIALS
    seed: int = Field(description="Reproduces the resampling behind the detectable effect.")


class NoiseFloorTooHighError(CertificationError):
    """The harness cannot see an effect the size of the one being looked for.

    Carries the full `Certification`, for the same reason
    `ExecutionTimeoutError` carries partial output: the refusal is a result, and
    a result that cannot be recorded may as well not have happened. A caller
    catching this has everything it needs to append the failure to the log and
    explain itself.
    """

    def __init__(self, certification: Certification) -> None:
        self.certification = certification
        super().__init__(certification.refusal)


def certify(
    baseline: Callable[[], object],
    *,
    workload: str,
    target_effect: float,
    n: int = DEFAULT_SAMPLES,
    seed: int | None = None,
) -> Certification:
    """Measure the baseline and decide whether it can answer the question.

    Runs `baseline` `n` times, computes the coefficient of variation and the
    smallest relative effect the rank test would detect at `TARGET_POWER`, and
    compares that against `target_effect` — the size of the change the
    investigation is looking for.

    Args:
        baseline: the unmodified workload. A callable, and called here, for the
            same reason `compare()` takes callables: a certification computed
            from numbers measured earlier certifies a machine that no longer
            exists.
        workload: what was measured, for the experiment log.
        target_effect: the relative change the investigation intends to detect,
            as a fraction — `0.05` for five percent.
        n: baseline repetitions. At least `MINIMUM_SAMPLES`.
        seed: recorded on the result whether supplied or drawn.

    Returns:
        The certification, when the harness can see `target_effect`.

    Raises:
        ValueError: `n` is below `MINIMUM_SAMPLES`, or `target_effect` is not
            between zero and one.
        NoiseFloorTooHighError: the harness cannot see an effect that size. The
            certification is on the error.
        TimingError: the workload raised.
    """
    if n < MINIMUM_SAMPLES:
        message = (
            f"certification needs at least {MINIMUM_SAMPLES} baseline runs, got {n}; "
            "a noise floor estimated from fewer is not worth the refusal it would justify"
        )
        raise ValueError(message)
    if not 0 < target_effect < 1:
        message = (
            f"target_effect must be a fraction between 0 and 1, got {target_effect}; "
            "0.05 means five percent"
        )
        raise ValueError(message)

    if seed is None:
        seed = random.getrandbits(_SEED_BITS)

    samples = time(baseline, n).durations
    summary = stats(samples)
    detectable = minimum_detectable_effect(samples, seed=seed)

    certified = detectable <= target_effect
    refusal = None
    if not certified:
        refusal = (
            f"noise floor too high for {workload!r}: the smallest change this harness can "
            f"detect is {detectable:.1%} (at {TARGET_POWER:.0%} power, alpha {ALPHA}), but the "
            f"investigation is looking for {target_effect:.1%}. Measured over {n} runs: "
            f"median {summary.median:.4f}s, coefficient of variation "
            f"{summary.coefficient_of_variation:.1%}. A {target_effect:.1%} change would be "
            "indistinguishable from noise here — quieten the machine, take more samples, or "
            "measure a larger unit of work before drawing any conclusion from it."
        )

    certification = Certification(
        workload=workload,
        n=n,
        samples=samples,
        mean_seconds=summary.mean,
        median_seconds=summary.median,
        stdev_seconds=summary.stdev,
        coefficient_of_variation=summary.coefficient_of_variation,
        minimum_detectable_effect=detectable,
        target_effect=target_effect,
        certified=certified,
        refusal=refusal,
        seed=seed,
    )

    if not certified:
        raise NoiseFloorTooHighError(certification)
    return certification


def minimum_detectable_effect(samples: Sequence[float], *, seed: int) -> float:
    """The smallest relative speed-up the rank test would reliably see.

    Resamples `samples` with replacement into a control group and a treatment
    group, scales the treatment by a candidate effect, and asks `rank_test()`
    how often it notices. The effect is tightened by bisection until the
    detection rate meets `TARGET_POWER`.

    Bisection assumes detection rate rises with effect size, which is true of
    the statistic and approximately true of this estimate of it — the residual
    wobble is resampling noise, bounded by `BOOTSTRAP_TRIALS`. The returned
    value is always one whose power was measured at or above target, never an
    interpolated midpoint, so the error is on the conservative side.

    Returns `LARGEST_EFFECT` when even that is undetectable, which is what a
    workload too fast to time looks like: every sample identical at the clock's
    resolution, and no scaling of it separable from any other.
    """
    rng = random.Random(seed)

    if _detection_rate(samples, LARGEST_EFFECT, rng) < TARGET_POWER:
        return LARGEST_EFFECT

    low, high = 0.0, LARGEST_EFFECT
    for _ in range(_SEARCH_STEPS):
        middle = (low + high) / 2
        if _detection_rate(samples, middle, rng) >= TARGET_POWER:
            high = middle
        else:
            low = middle
    return high


def _detection_rate(samples: Sequence[float], effect: float, rng: random.Random) -> float:
    """How often the rank test sees a shift of `effect` in data shaped like this."""
    n = len(samples)
    scale = 1 - effect
    detected = 0

    for _ in range(BOOTSTRAP_TRIALS):
        control = rng.choices(samples, k=n)
        treatment = [value * scale for value in rng.choices(samples, k=n)]
        if rank_test(control, treatment).p_value < ALPHA:
            detected += 1

    return detected / BOOTSTRAP_TRIALS

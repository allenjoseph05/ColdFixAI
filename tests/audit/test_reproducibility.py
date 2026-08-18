"""S-9.6 — running one experiment again, past the cache, to see whether it agrees.

The whole story is what *material* means, and the control is the most important
test in this epic: **a duration that moved within the noise floor must not count
as divergence.** If it did, every reproducibility check would fail, every finding
would be `unsound`, and the amended S-9.8 would route every investigation back
for more experiments — for ever. That is ADR 094's hazard reached through the
most mechanical attack here.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping

import pytest

from coldfix.audit import reproducibility
from coldfix.audit.reproducibility import (
    Divergence,
    ReproducibilityError,
    check,
    classify,
)
from coldfix.audit.scales import MEASURED_DRIFT
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.primitives.measurement import MetricKind
from coldfix.replay.cache import ReplayMode

KINDS = {
    "db.query": MetricKind.COUNT,
    "seconds": MetricKind.DURATION,
}


def an_experiment(**measurement: float) -> Experiment:
    log = ExperimentLog()
    return log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        rationale="queries are flat, so the cost is above the database",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement=measurement or {"db.query": 7.0, "seconds": 8.24},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing removed most of the wall time",
    )


def rerunning(**measurement: float) -> Callable[[Experiment], Mapping[str, float]]:
    def rerun(_: Experiment) -> Mapping[str, float]:
        return dict(measurement)

    return rerun


# ==================== the control: ordinary timing noise is not divergence


def test_a_duration_that_moved_within_the_floor_is_not_material() -> None:
    """**The most important test in this epic.** `MetricKind`: *a duration here
    is one sample.* If this counted, every finding would be unsound and the
    amended S-9.8 would loop for ever."""
    audit = check(
        an_experiment(),
        rerunning(**{"db.query": 7.0, "seconds": 8.9}),  # +8%, inside 12%
        kinds=KINDS,
    )

    assert not audit.unsound
    assert audit.comparisons[1].divergence is Divergence.DURATION_WITHIN_NOISE
    assert not Divergence.DURATION_WITHIN_NOISE.material


def test_a_duration_that_moved_beyond_the_floor_is_material() -> None:
    """The other side. A 3x move is not a sample of the same thing."""
    audit = check(
        an_experiment(),
        rerunning(**{"db.query": 7.0, "seconds": 25.0}),
        kinds=KINDS,
    )

    assert audit.unsound
    assert audit.comparisons[1].divergence is Divergence.DURATION_BEYOND_NOISE


def test_the_floor_is_the_measured_one_and_can_be_tightened() -> None:
    """A caller holding S-1.7's certified floor passes it, exactly as S-9.4 does —
    a quiet harness should not be held to a noisy machine's tolerance."""
    moved = rerunning(**{"db.query": 7.0, "seconds": 8.9})

    assert not check(an_experiment(), moved, kinds=KINDS).unsound
    assert check(an_experiment(), moved, kinds=KINDS, relative_noise=0.02).unsound
    assert MEASURED_DRIFT == 0.12


# ============================== counts reproduce to the integer, so any move is real


def test_a_count_that_moved_at_all_is_material() -> None:
    """ADR 052 makes counts what raises a flag *because* they reproduce exactly.
    Seven queries becoming eight is not noise, it is a different run."""
    audit = check(
        an_experiment(),
        rerunning(**{"db.query": 8.0, "seconds": 8.24}),
        kinds=KINDS,
    )

    assert audit.unsound
    assert audit.comparisons[0].divergence is Divergence.COUNT_MOVED


def test_a_count_that_did_not_move_is_unchanged() -> None:
    audit = check(an_experiment(), rerunning(**{"db.query": 7.0, "seconds": 8.24}), kinds=KINDS)

    assert not audit.unsound
    assert all(item.divergence is Divergence.UNCHANGED for item in audit.comparisons)


def test_the_same_movement_is_material_for_a_count_and_not_for_a_duration() -> None:
    """**The two rules, isolated.** The identical relative change gets opposite
    answers, which is `MetricKind`'s whole purpose and the thing a single-rule
    comparator would get wrong in one direction or the other."""
    as_count = classify(
        kind=MetricKind.COUNT, recorded=100.0, rerun=105.0, relative_noise=MEASURED_DRIFT
    )
    as_duration = classify(
        kind=MetricKind.DURATION, recorded=100.0, rerun=105.0, relative_noise=MEASURED_DRIFT
    )

    assert as_count is Divergence.COUNT_MOVED
    assert as_duration is Divergence.DURATION_WITHIN_NOISE
    assert as_count.material
    assert not as_duration.material


# ================================= a metric that vanished is not "no difference"


def test_a_metric_the_rerun_did_not_measure_is_material() -> None:
    """**Silence read as agreement is the S-3.1 failure.** If the recording holds
    `db.query` and the re-run does not, the two runs measured different things
    and no comparison is possible."""
    audit = check(an_experiment(), rerunning(**{"seconds": 8.24}), kinds=KINDS)

    assert audit.unsound
    assert audit.comparisons[0].divergence is Divergence.NOT_REMEASURED
    assert "not measured on the re-run" in audit.describe()


def test_a_metric_with_no_declared_kind_is_refused() -> None:
    """Supplied by the primitive that produced it, because `seconds_ablated` and
    `render.calls_baseline` are not distinguishable by spelling and a wrong guess
    picks the wrong rule silently."""
    with pytest.raises(ReproducibilityError, match="no metric kind was declared"):
        check(
            an_experiment(),
            rerunning(**{"db.query": 7.0}),
            kinds={"seconds": MetricKind.DURATION},
        )


def test_a_duration_recorded_as_zero_that_moved_is_material() -> None:
    """Any movement away from zero is beyond every relative floor, and dividing
    by it to say so would be arithmetic nobody can check."""
    experiment = an_experiment(**{"seconds": 0.0})

    audit = check(experiment, rerunning(**{"seconds": 3.0}), kinds=KINDS)

    assert audit.unsound
    assert audit.comparisons[0].divergence is Divergence.DURATION_BEYOND_NOISE
    assert audit.comparisons[0].relative_change is None


# ========================================================== reporting and reach


def test_a_reproducing_experiment_says_so() -> None:
    audit = check(an_experiment(), rerunning(**{"db.query": 7.0, "seconds": 8.24}), kinds=KINDS)

    assert "It reproduced" in audit.describe()
    assert "12% noise floor" in audit.describe()


def test_a_failure_says_what_a_finding_can_no_longer_rest_on() -> None:
    audit = check(an_experiment(), rerunning(**{"db.query": 8.0, "seconds": 8.24}), kinds=KINDS)

    described = audit.describe()

    assert "It did not reproduce" in described
    assert "does not survive being taken twice" in described
    assert "7.0 -> 8.0" in described


def test_nothing_here_measures_anything() -> None:
    """`CLAUDE.md` puts the measuring in the harness. An auditor that produced its
    own numbers would be the one place that rule could not be enforced — so the
    re-run arrives as a callable and there is no parameter for a number."""
    parameters = inspect.signature(check).parameters

    assert "rerun" in parameters
    assert not {"measurement", "measured", "result"} & set(parameters)

    source = inspect.getsource(reproducibility)
    assert "perf_counter" not in source
    assert "time.time" not in source


def test_the_cache_is_bypassed_by_the_mode_that_exists_for_it() -> None:
    """S-5.2 built `ReplayMode.OFF` for exactly this, and records why it is a mode
    rather than an `if use_cache:` at every call site. The re-run is the caller's
    to perform, and `OFF` is what it performs it under."""
    assert ReplayMode.OFF.value == "off"

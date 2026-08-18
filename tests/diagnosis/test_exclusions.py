"""S-8.5 — what was ruled out, and when that stops being true.

`08-audit.md` F3 is a *wrong answer* flaw rather than a missing feature: an
exclusion recorded as fact sits in the prompt and permanently blocks the correct
hypothesis. So the tests come in pairs — a condition that moved must reopen, and
a condition that did not must **not**, because a module that reopened everything
would satisfy AC 2 and AC 4 while making exclusions worthless.
"""

from __future__ import annotations

import pytest

import coldfix.primitives  # noqa: F401 - registers the thirteen
from coldfix.diagnosis.exclusions import (
    RESIDUE,
    Conditions,
    Dimension,
    Exclusion,
    ExclusionError,
    ExclusionRegister,
    Observed,
    current_platform,
)
from coldfix.diagnosis.hypothesis import render_question
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.primitives.registry import REGISTRY, ProjectProfile, Selection
from coldfix.primitives.scaling import Distribution

UNIFORM = Distribution.UNIFORM.value
POWER_LAW = Distribution.POWER_LAW.value
LONG_TAIL = Distribution.LONG_TAIL.value

PLATFORM = "x86_64-linux"


def uniform_at(*scales: float, concurrency: float = 1, platform: str = PLATFORM) -> Conditions:
    """The conditions a plain screening sweep runs under."""
    return Conditions.of(
        fixture_shape=UNIFORM,
        platform=platform,
        concurrency=concurrency,
        scales=list(scales) or [10, 100, 1000],
    )


def rejected(log: ExperimentLog | None = None, **overrides: object) -> Experiment:
    """One rejected experiment — the only kind that excludes anything."""
    fields: dict[str, object] = {
        "hypothesis": "the database is the bottleneck",
        "primitive": "scaling.volume",
        "target": "shop.books.list",
        "design": "scaling.volume(scales=[10, 100, 1000]) on shop.books.list",
        "measurement": {"db.query": 7.0},
        "verdict": Verdict.REJECTED,
        "outcome": "queries flat at 7, 7, 7 across a 100x sweep",
    }
    fields.update(overrides)
    return (log or ExperimentLog()).append(**fields)  # type: ignore[arg-type]


# ================================ AC 1: every exclusion records its preconditions


def test_an_exclusion_records_all_four_conditions() -> None:
    exclusion = Exclusion(experiment=rejected(), conditions=uniform_at(10, 100, 1000))

    described = exclusion.conditions.describe()

    assert "fixture shape uniform" in described
    assert "platform x86_64-linux" in described
    assert "concurrency 1" in described
    assert "scale 10 to 1000" in described


@pytest.mark.parametrize("dropped", list(Dimension))
def test_conditions_recording_only_three_of_the_four_are_refused(dropped: Dimension) -> None:
    """AC 1 read literally, and parametrised so no dimension is the one that
    happens to be checked: an exclusion recording three of four cannot be
    reopened by a change to the fourth, which is F3's failure with one fewer
    axis."""
    complete = uniform_at(10, 100).observed
    partial = {key: value for key, value in complete.items() if key is not dropped}

    with pytest.raises(ExclusionError, match="requires every exclusion"):
        Conditions(partial)


def test_an_exclusion_carries_the_measurement_that_made_it() -> None:
    """The first non-negotiable, and it comes free: an exclusion is built from an
    `Experiment`, and S-8.4 already refuses one with no measurement. There is no
    constructor here that takes a sentence."""
    exclusion = Exclusion(experiment=rejected(), conditions=uniform_at())

    assert exclusion.experiment.measurement == {"db.query": 7.0}


@pytest.mark.parametrize("verdict", [Verdict.CONFIRMED, Verdict.NARROWED])
def test_only_a_rejection_excludes_anything(verdict: Verdict) -> None:
    """A confirmed experiment is a finding and a narrowed one is a hypothesis
    that survived. An exclusion built from either would tell the agent a live
    branch was closed."""
    experiment = rejected(verdict=verdict, outcome="something happened")

    with pytest.raises(ExclusionError, match="only a rejection"):
        Exclusion(experiment=experiment, conditions=uniform_at())


def test_a_dimension_recorded_with_no_values_is_refused() -> None:
    with pytest.raises(ExclusionError, match="no values at all"):
        Observed(Dimension.SCALE, ())


def test_a_numeric_condition_given_text_is_refused() -> None:
    with pytest.raises(ExclusionError, match="no envelope over text"):
        Observed(Dimension.CONCURRENCY, ("lots",))


def test_a_categorical_condition_given_a_number_is_refused() -> None:
    with pytest.raises(ExclusionError, match="compared by membership"):
        Observed(Dimension.FIXTURE_SHAPE, (4.0,))


def test_true_is_not_a_concurrency_of_one() -> None:
    """`bool` is a subclass of `int`, so a permissive numeric check turns
    `concurrency=True` into a concurrency of 1 that nobody wrote."""
    with pytest.raises(ExclusionError, match="no envelope over text"):
        Observed(Dimension.CONCURRENCY, (True,))


def test_conditions_filed_under_the_wrong_dimension_are_refused() -> None:
    """The mistake `Conditions.of` exists to make impossible, checked on the path
    that does not go through it."""
    complete = dict(uniform_at().observed)
    complete[Dimension.PLATFORM] = Observed(Dimension.FIXTURE_SHAPE, (UNIFORM,))

    with pytest.raises(ExclusionError, match="wrong dimension"):
        Conditions(complete)


# ======================= AC 2 and AC 4: a changed condition reopens what it covers


def test_a_uniform_fixture_exclusion_is_reopened_when_skew_is_introduced() -> None:
    """**AC 4, the story's headline.** F3's worked example: the fixtures were
    uniform, the real defect is skew-dependent, and *not the database* is false
    the moment somebody seeds a long tail."""
    register = ExclusionRegister()
    exclusion = register.record(rejected(), uniform_at(10, 100, 1000))

    skewed = Conditions.of(
        fixture_shape=LONG_TAIL, platform=PLATFORM, concurrency=1, scales=[10, 100, 1000]
    )

    assert register.stale(skewed) == (exclusion,)
    assert register.live(skewed) == ()

    # Asserted on the drift sentence, not on `"fixture shape"` — the settled half
    # of this same string already says *under fixture shape uniform*, so the
    # obvious check passes against a rendering that names no condition at all.
    # Second story running to hit that shape; see ADR 087.
    reopened = exclusion.describe(skewed)
    assert "REOPENED" in reopened
    assert "established at fixture shape uniform" in reopened
    assert "went to fixture shape long_tail" in reopened


def test_the_same_fixture_shape_does_not_reopen_anything() -> None:
    """**The control, and it is the test that gives AC 4 its meaning.** A module
    that reported everything stale would pass the test above and make every
    exclusion worthless — which is the opposite of F3 and just as wrong."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, 1000))

    again = uniform_at(10, 100, 1000)

    assert register.stale(again) == ()
    assert len(register.live(again)) == 1
    assert "REOPENED" not in register.render(again)[0]


def test_an_exclusion_that_swept_every_shape_is_not_reopened_by_any_of_them() -> None:
    """Multi-valued categorical is ordinary rather than hypothetical: S-3.3's
    `compare_shapes` sweeps all three distributions in one experiment, and that
    exclusion genuinely covers three."""
    register = ExclusionRegister()
    register.record(
        rejected(),
        Conditions.of(
            fixture_shape=[UNIFORM, POWER_LAW, LONG_TAIL],
            platform=PLATFORM,
            concurrency=1,
            scales=[10, 100],
        ),
    )

    for shape in (UNIFORM, POWER_LAW, LONG_TAIL):
        now = Conditions.of(fixture_shape=shape, platform=PLATFORM, concurrency=1, scales=[10, 100])
        assert register.stale(now) == ()


def test_a_scale_beyond_the_tested_range_reopens_an_exclusion() -> None:
    """The asymmetry that makes the envelope the right model: a defect appearing
    past the largest scale tested is an ordinary threshold — a cache that stops
    fitting, a page that splits, an index the planner abandons."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, 1000))

    assert register.stale(uniform_at(10, 100, 10_000))


def test_a_scale_inside_the_tested_range_does_not() -> None:
    """The other half of the envelope. A defect invisible at 10, 100 and 1000 but
    present at 500 would have to be non-monotonic, and reopening on every
    intermediate point would reopen everything forever."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, 1000))

    assert register.stale(uniform_at(500)) == ()


def test_raising_concurrency_reopens_an_exclusion_established_serially() -> None:
    """F3's own `invalidated_if: concurrency > 1`, derived rather than stored."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, concurrency=1))

    stale = register.stale(uniform_at(10, 100, concurrency=8))

    assert len(stale) == 1
    assert "concurrency" in stale[0].describe(uniform_at(10, 100, concurrency=8))


def test_a_different_platform_reopens_an_exclusion() -> None:
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, platform="x86_64-linux"))

    assert register.stale(uniform_at(10, 100, platform="arm64-darwin"))


def test_the_drift_says_what_moved_and_where_it_went() -> None:
    """An agent told only that something is stale cannot tell whether it is worth
    re-testing."""
    exclusion = Exclusion(experiment=rejected(), conditions=uniform_at(10, 100))
    now = Conditions.of(fixture_shape=POWER_LAW, platform=PLATFORM, concurrency=1, scales=[10, 100])

    (drift,) = exclusion.stale_against(now)

    assert drift.dimension is Dimension.FIXTURE_SHAPE
    assert "established at fixture shape uniform" in drift.describe()
    assert "went to fixture shape power_law" in drift.describe()


def test_every_dimension_that_moved_is_reported_not_just_the_first() -> None:
    exclusion = Exclusion(experiment=rejected(), conditions=uniform_at(10, 100))
    now = Conditions.of(
        fixture_shape=LONG_TAIL, platform="arm64-darwin", concurrency=16, scales=[100_000]
    )

    drifts = exclusion.stale_against(now)

    assert {drift.dimension for drift in drifts} == set(Dimension)
    assert len(drifts) == len(Dimension)


def test_a_change_to_any_single_dimension_is_enough_to_reopen() -> None:
    """Parametrised over all four rather than asserted on one, so that no
    dimension can quietly stop being a condition. A `drift_from` that skipped
    fixture shape would still pass every *other* staleness test here."""
    exclusion = Exclusion(experiment=rejected(), conditions=uniform_at(10, 100))
    moved = {
        Dimension.FIXTURE_SHAPE: Conditions.of(
            fixture_shape=LONG_TAIL, platform=PLATFORM, concurrency=1, scales=[10, 100]
        ),
        Dimension.PLATFORM: Conditions.of(
            fixture_shape=UNIFORM, platform="arm64-darwin", concurrency=1, scales=[10, 100]
        ),
        Dimension.CONCURRENCY: Conditions.of(
            fixture_shape=UNIFORM, platform=PLATFORM, concurrency=16, scales=[10, 100]
        ),
        Dimension.SCALE: Conditions.of(
            fixture_shape=UNIFORM, platform=PLATFORM, concurrency=1, scales=[100_000]
        ),
    }

    for dimension, now in moved.items():
        drifts = exclusion.stale_against(now)
        assert [drift.dimension for drift in drifts] == [dimension], dimension


# ================================= AC 3: a stale exclusion may be re-tested, a live one may not


def test_a_stale_exclusion_can_be_reopened_and_hands_back_what_to_retest() -> None:
    register = ExclusionRegister()
    exclusion = register.record(rejected(), uniform_at(10, 100))
    skewed = Conditions.of(
        fixture_shape=LONG_TAIL, platform=PLATFORM, concurrency=1, scales=[10, 100]
    )

    assert register.reopen(exclusion, skewed) == "the database is the bottleneck"


def test_an_exclusion_nothing_has_moved_against_cannot_be_reopened() -> None:
    """**The half F3 does not ask for.** F3 names the danger of an exclusion
    treated as permanent fact; fixing it introduces the opposite one, an agent
    setting aside an inconvenient result by calling it stale."""
    register = ExclusionRegister()
    exclusion = register.record(rejected(), uniform_at(10, 100, 1000))

    with pytest.raises(ExclusionError, match="cannot be reopened"):
        register.reopen(exclusion, uniform_at(10, 100, 1000))


def test_the_refusal_says_what_would_have_to_change() -> None:
    register = ExclusionRegister()
    exclusion = register.record(rejected(), uniform_at(10, 100, 1000))

    with pytest.raises(ExclusionError) as raised:
        register.reopen(exclusion, uniform_at(10, 100, 1000))

    assert "fixture shape uniform" in str(raised.value)
    assert "scale 10 to 1000" in str(raised.value)


def test_an_exclusion_this_register_never_recorded_cannot_be_reopened() -> None:
    """Reopening is a statement about evidence this register holds, and an
    exclusion it never saw is one whose conditions it cannot vouch for."""
    stranger = Exclusion(experiment=rejected(), conditions=uniform_at(10, 100))
    skewed = Conditions.of(
        fixture_shape=LONG_TAIL, platform=PLATFORM, concurrency=1, scales=[10, 100]
    )

    with pytest.raises(ExclusionError, match="not in this register"):
        ExclusionRegister().reopen(stranger, skewed)


# ================================================= rendering, determinism, composition


def test_a_live_exclusion_carries_its_scope_into_the_prompt() -> None:
    """`00-BRIEF.md` §9's example is *not the database, queries flat across 100x
    scale* — the scale is part of the claim, not a footnote to it."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, 1000))

    (rendered,) = register.render(uniform_at(10, 100, 1000))

    assert "the database is the bottleneck" in rendered
    assert "queries flat at 7, 7, 7" in rendered
    assert "scale 10 to 1000" in rendered


def test_conditions_render_the_same_however_they_were_assembled() -> None:
    """S-8.3's finding one module across: these go into a cached prompt, and two
    equal conditions built in two orders would be two prompts."""
    one = Conditions.of(
        fixture_shape=[LONG_TAIL, UNIFORM], platform=PLATFORM, concurrency=1, scales=[1000, 10, 100]
    )
    other = Conditions.of(
        fixture_shape=[UNIFORM, LONG_TAIL], platform=PLATFORM, concurrency=1, scales=[10, 1000, 100]
    )

    assert one.describe() == other.describe()


def test_a_repeated_value_is_recorded_once() -> None:
    assert Observed(Dimension.SCALE, (10, 10, 100)).values == (10, 100)


def test_the_register_feeds_the_sentences_s81_asks_for() -> None:
    """Composition. S-8.1 takes `exclusions: Sequence[str]` and recorded that it
    needed only the sentence because *S-8.5 owns what an exclusion is*. This is
    that story, and it still hands over sentences."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100, 1000))
    skewed = Conditions.of(
        fixture_shape=LONG_TAIL, platform=PLATFORM, concurrency=1, scales=[10, 100, 1000]
    )

    question = render_question(
        log=ExperimentLog(),
        exclusions=register.render(skewed),
        source="shop/views.py",
        instruments=Selection(
            profile=ProjectProfile(),
            available=(REGISTRY.get("scaling.shape"),),
            withheld=(),
        ),
    )

    assert "REOPENED" in question
    assert "the database is the bottleneck" in question


def test_the_report_names_what_it_does_not_cover() -> None:
    """S-7.12's `Anchor.residue` construction: a bound nobody can read is one
    somebody will quote past. Four dimensions are modelled and an experiment that
    varied a fifth has an exclusion that looks fully conditioned and is not."""
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100))

    assert RESIDUE in register.report(uniform_at(10, 100))


def test_an_empty_register_says_so_rather_than_rendering_nothing() -> None:
    assert "Nothing has been ruled out yet" in ExclusionRegister().report(uniform_at())


def test_the_register_hands_back_a_copy() -> None:
    register = ExclusionRegister()
    register.record(rejected(), uniform_at(10, 100))

    assert isinstance(register.exclusions, tuple)


def test_this_machine_has_a_platform_string_nobody_typed() -> None:
    """A wrong platform on an exclusion is invisible: it looks recorded, compares
    equal to the next wrong one, and never reopens anything."""
    assert current_platform()
    assert current_platform() == current_platform()


def test_comparing_two_different_dimensions_is_a_caller_mistake() -> None:
    with pytest.raises(ExclusionError, match="cannot compare"):
        Observed(Dimension.SCALE, (10,)).covers(Observed(Dimension.CONCURRENCY, (10,)))

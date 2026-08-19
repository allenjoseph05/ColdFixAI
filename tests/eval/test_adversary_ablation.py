"""Epic 11, S-11.8 — the Adversary ablation study.

*Runs a set of findings with and without the Adversary. Counts bad patches
reaching a human in each condition. Reports the delta. Repeats at two model tiers
to test whether the mid tier misses attack classes.*

`00-BRIEF.md` §5: **if the delta is small, cut it — it would be theatre.** Step 11
is called the contribution of the whole project, and this is the study allowed to
conclude the contribution is not one.

So the tests that matter most are the ones proving the negative results are
reachable: an Adversary that objects to everything must come back `CUT` despite a
perfect catch rate, and a corpus too small to separate the two must come back
`NOT_ESTABLISHED` rather than flattering whichever way the counts fell.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from coldfix.audit.patchverdict import Attack, Verdict
from coldfix.cost.routing import Tier
from coldfix.eval.ablation import (
    MINIMUM_PER_LABEL,
    Ablation,
    AblationError,
    Arm,
    Case,
    Label,
    Observation,
    Recommendation,
    study,
    wilson,
)

CLASSES = list(Attack)


def bad(index: int, attack: Attack | None = None) -> Case:
    return Case(
        id=f"bad-{index}",
        label=Label.BAD,
        exemplifies=attack if attack is not None else CLASSES[index % len(CLASSES)],
    )


def sound(index: int) -> Case:
    return Case(id=f"sound-{index}", label=Label.SOUND)


def corpus(*, bads: int = MINIMUM_PER_LABEL, sounds: int = MINIMUM_PER_LABEL) -> list[Case]:
    return [bad(index) for index in range(bads)] + [sound(index) for index in range(sounds)]


def saw(  # noqa: PLR0913 - the corpus, the tier, and four independent ways of
    # saying what that tier stopped. Collapsing them would make the fixture
    # decide which condition a test is exercising.
    cases: list[Case],
    *,
    tier: Tier = Tier.FRONTIER,
    stops_bad: int | None = None,
    stops_sound: int = 0,
    stops_classes: set[Attack] | None = None,
    eur: str = "0.40",
) -> list[Observation]:
    """What one tier said. `stops_bad` counts from the front; `stops_classes`
    overrides it with *which* classes were caught, for the coverage question."""
    bads = [case for case in cases if case.label is Label.BAD]
    sounds = [case for case in cases if case.label is Label.SOUND]
    caught = stops_bad if stops_bad is not None else len(bads)

    seen: list[Observation] = []
    for index, case in enumerate(bads):
        stopped = case.exemplifies in stops_classes if stops_classes is not None else index < caught
        seen.append(
            Observation(
                case=case,
                tier=tier,
                verdict=Verdict.BROKEN if stopped else Verdict.CLEAN,
                eur=Decimal(eur),
            )
        )
    for index, case in enumerate(sounds):
        seen.append(
            Observation(
                case=case,
                tier=tier,
                verdict=Verdict.SUSPICIOUS if index < stops_sound else Verdict.CLEAN,
                eur=Decimal(eur),
            )
        )
    return seen


# ============ AC 1 and AC 2 — the two conditions and the count


def test_the_without_condition_needs_no_run() -> None:
    """**Structural, not measured.** Without the Adversary there is nothing between
    a patch that satisfied the Surgeon's gate and a human, so the count is the
    number of bad cases — and spending the corpus a second time to observe it would
    buy a number already known."""
    cases = corpus(bads=7, sounds=3)
    result = study(cases, saw(cases))
    assert result.without_adversary == 7


def test_a_suspicious_patch_did_not_reach_a_human_unflagged() -> None:
    """§4.4 escalates `suspicious` to a human — but with the concern stated, which
    is the difference this study measures. Counting it as unflagged would score the
    escalation as a failure to catch anything."""
    cases = [bad(0), sound(0)]
    seen = [
        Observation(case=cases[0], tier=Tier.FRONTIER, verdict=Verdict.SUSPICIOUS),
        Observation(case=cases[1], tier=Tier.FRONTIER, verdict=Verdict.CLEAN),
    ]
    result = study(cases, seen)
    arm = result.arm(Tier.FRONTIER)
    assert arm.bad_stopped == 1
    assert arm.bad_reaching_a_human == 0
    assert arm.sound_stopped == 0


def test_only_clean_reaches_a_human_unflagged() -> None:
    cases = [bad(0)]
    for verdict, stopped in ((Verdict.CLEAN, False), (Verdict.BROKEN, True)):
        seen = [Observation(case=cases[0], tier=Tier.FRONTIER, verdict=verdict)]
        assert study(cases, seen).arm(Tier.FRONTIER).bad_stopped == int(stopped)


# ============ AC 3 — the delta


def test_the_delta_is_what_the_adversary_removed() -> None:
    cases = corpus(bads=10, sounds=10)
    result = study(cases, saw(cases, stops_bad=6))
    assert result.without_adversary == 10
    assert result.arm(Tier.FRONTIER).bad_reaching_a_human == 4
    assert result.delta(Tier.FRONTIER) == 6
    assert "Delta at frontier: 6" in result.describe()


def test_an_adversary_that_catches_nothing_has_a_delta_of_zero() -> None:
    cases = corpus()
    result = study(cases, saw(cases, stops_bad=0))
    assert result.delta(Tier.FRONTIER) == 0
    assert result.recommendation is Recommendation.CUT


# ============ the negative results, which are the point


def test_an_adversary_that_objects_to_everything_is_cut() -> None:
    """**The measurement this study exists to get right.** It catches every bad
    patch — a perfect rate on AC 2's number — and it is a wall, not an audit. A
    study that counted only bad patches reaching a human would show it in the best
    possible light."""
    cases = corpus(bads=20, sounds=20)
    result = study(cases, saw(cases, stops_bad=20, stops_sound=20))
    arm = result.arm(Tier.FRONTIER)

    assert arm.catch_rate == 1.0, "perfect on the number AC 2 asks for"
    assert arm.overblock_rate == 1.0
    assert result.recommendation is Recommendation.CUT
    assert "cut it" in result.describe()


def test_an_adversary_that_blocks_sound_work_as_often_as_bad_is_cut() -> None:
    """Not discriminating: it stops bad patches because it stops patches."""
    cases = corpus(bads=20, sounds=20)
    result = study(cases, saw(cases, stops_bad=12, stops_sound=12))
    assert result.arm(Tier.FRONTIER).catch_rate == 0.6
    assert result.arm(Tier.FRONTIER).overblock_rate == 0.6
    assert result.recommendation is Recommendation.CUT


def test_a_working_adversary_is_kept() -> None:
    cases = corpus(bads=20, sounds=20)
    result = study(cases, saw(cases, stops_bad=17, stops_sound=1))
    assert result.recommendation is Recommendation.KEEP
    assert "over-blocking does not explain" in result.describe()


def test_a_corpus_too_small_establishes_nothing() -> None:
    """**Whichever way the counts fall.** A study reporting `KEEP` from a sample
    that could not have said otherwise is the epic marking its own homework."""
    flattering = corpus(bads=3, sounds=3)
    assert study(flattering, saw(flattering, stops_bad=3, stops_sound=0)).recommendation is (
        Recommendation.NOT_ESTABLISHED
    )

    damning = corpus(bads=3, sounds=3)
    assert study(damning, saw(damning, stops_bad=0, stops_sound=3)).recommendation is (
        Recommendation.NOT_ESTABLISHED
    )

    # **Each label counts on its own**, and a corpus thin on one side only is the
    # case that separates the two clauses: plenty of sound patches, three bad ones,
    # all caught. Every rate looks perfect and the study still concludes nothing.
    lopsided = corpus(bads=3, sounds=20)
    result = study(lopsided, saw(lopsided, stops_bad=3, stops_sound=0))
    assert result.arm(Tier.FRONTIER).catch_rate == 1.0
    assert result.arm(Tier.FRONTIER).overblock_rate == 0.0
    assert result.underpowered
    assert result.recommendation is Recommendation.NOT_ESTABLISHED


def test_a_corpus_with_no_sound_patches_cannot_see_over_blocking() -> None:
    """The one-sided corpus that makes a wall look perfect."""
    cases = corpus(bads=20, sounds=0)
    result = study(cases, saw(cases, stops_bad=20))
    assert result.arm(Tier.FRONTIER).overblock_rate is None
    assert result.underpowered
    assert result.recommendation is Recommendation.NOT_ESTABLISHED


def test_a_corpus_with_no_bad_patches_cannot_see_catching() -> None:
    cases = corpus(bads=0, sounds=20)
    result = study(cases, saw(cases))
    assert result.arm(Tier.FRONTIER).catch_rate is None
    assert result.recommendation is Recommendation.NOT_ESTABLISHED


def test_the_report_calls_a_barely_powered_study_weak() -> None:
    cases = corpus(bads=MINIMUM_PER_LABEL, sounds=MINIMUM_PER_LABEL)
    described = study(cases, saw(cases, stops_bad=9, stops_sound=0)).describe()
    assert "This is a weak study" in described
    assert "Thirty cases per label" in described


def test_an_edge_too_small_for_the_corpus_to_establish_is_not_an_edge() -> None:
    """**The third test, and it replaced a dead one.** *The interval reaches zero*
    cannot happen — a Wilson lower bound is zero only when nothing was caught, and
    that is already `CUT` by the over-blocking rule — so a sabotage deleting the
    branch changed nothing. What is real is a lead the corpus is too small to have
    established: 12 of 20 caught against 10 of 20 blocked leads by ten points, and
    the interval runs from 39% to 78%."""
    thin = corpus(bads=20, sounds=20)
    close = study(thin, saw(thin, stops_bad=12, stops_sound=10))
    arm = close.arm(Tier.FRONTIER)
    assert arm.catch_rate == 0.6
    assert arm.overblock_rate == 0.5, "ahead on the point estimate"
    assert (arm.catch_interval or (1.0, 1.0))[0] < 0.5
    assert close.recommendation is Recommendation.CUT

    # The same rates, ten times the corpus. Now the lead is established.
    wide = corpus(bads=200, sounds=200)
    settled = study(wide, saw(wide, stops_bad=120, stops_sound=100))
    assert settled.arm(Tier.FRONTIER).catch_rate == 0.6
    assert (settled.arm(Tier.FRONTIER).catch_interval or (0.0, 0.0))[0] > 0.5
    assert settled.recommendation is Recommendation.KEEP


def test_catching_nothing_is_cut_by_the_over_blocking_rule() -> None:
    cases = corpus(bads=40, sounds=40)
    result = study(cases, saw(cases, stops_bad=0, stops_sound=0))
    assert (result.arm(Tier.FRONTIER).catch_interval or (1.0, 1.0))[0] == 0.0
    assert result.recommendation is Recommendation.CUT


# ============ AC 4 — two tiers, and coverage rather than counts


def test_the_mid_tier_being_blind_to_a_class_is_reported() -> None:
    """**The question AC 4 actually asks.** Two arms can stop the same number of bad
    patches while one is blind to a whole class, and only coverage reads that off."""
    cases = [bad(index, attack) for index, attack in enumerate(CLASSES)] + [
        sound(index) for index in range(5)
    ]
    everything = set(CLASSES)
    seen = [
        *saw(cases, tier=Tier.FRONTIER, stops_classes=everything),
        *saw(cases, tier=Tier.MID, stops_classes=everything - {Attack.TEST_QUALITY}),
    ]
    result = study(cases, seen)

    assert result.best.tier is Tier.FRONTIER
    assert result.missed_classes[Tier.MID] == frozenset({Attack.TEST_QUALITY})
    assert "mid is blind to test_quality" in result.describe()
    assert "routing decision, not a score" in result.describe()


def test_a_mid_tier_that_misses_nothing_says_so() -> None:
    cases = [bad(index, attack) for index, attack in enumerate(CLASSES)] + [sound(0)]
    everything = set(CLASSES)
    seen = [
        *saw(cases, tier=Tier.FRONTIER, stops_classes=everything),
        *saw(cases, tier=Tier.MID, stops_classes=everything),
    ]
    result = study(cases, seen)
    assert result.missed_classes[Tier.MID] == frozenset()
    assert "caught every class" in result.describe()


def test_two_tiers_can_agree_on_the_count_and_differ_on_coverage() -> None:
    """The reason the delta alone does not answer AC 4."""
    cases = [bad(index, attack) for index, attack in enumerate(CLASSES)] + [sound(0)]
    seen = [
        *saw(cases, tier=Tier.FRONTIER, stops_classes={Attack.EQUIVALENCE, Attack.CHEAT}),
        *saw(cases, tier=Tier.MID, stops_classes={Attack.EQUIVALENCE, Attack.TRADE}),
    ]
    result = study(cases, seen)

    assert result.arm(Tier.FRONTIER).bad_stopped == result.arm(Tier.MID).bad_stopped
    assert result.delta(Tier.FRONTIER) == result.delta(Tier.MID)
    assert result.missed_classes[Tier.MID] == frozenset({Attack.CHEAT})


def test_the_recommendation_is_about_the_strongest_tier_that_ran() -> None:
    cases = corpus(bads=20, sounds=20)
    seen = [
        *saw(cases, tier=Tier.FRONTIER, stops_bad=17, stops_sound=1),
        *saw(cases, tier=Tier.MID, stops_bad=2, stops_sound=15),
    ]
    result = study(cases, seen)
    assert result.best.tier is Tier.FRONTIER
    assert result.recommendation is Recommendation.KEEP


# ============ the corpus must be able to answer the questions asked of it


def test_a_bad_case_must_name_the_class_that_should_catch_it() -> None:
    with pytest.raises(AblationError, match="only answer how many, never which"):
        Case(id="x", label=Label.BAD)


def test_a_sound_case_may_not_name_a_class() -> None:
    with pytest.raises(AblationError, match="exemplifies no cheat"):
        Case(id="x", label=Label.SOUND, exemplifies=Attack.CHEAT)


def test_an_empty_corpus_is_refused() -> None:
    """Every count is zero and the delta is zero, which is the shape of an Adversary
    that does nothing."""
    with pytest.raises(AblationError, match="an Adversary that does nothing"):
        study([], [])


def test_two_cases_sharing_an_id_are_refused() -> None:
    doubled = [bad(0), Case(id="bad-0", label=Label.SOUND)]
    with pytest.raises(AblationError, match="Which label applies is undefined"):
        study(doubled, saw(doubled))


def test_an_arm_that_does_not_cover_the_corpus_is_refused() -> None:
    """Arms over different corpora cannot be compared, and a tier that skipped the
    hard cases would look better for it."""
    cases = corpus(bads=4, sounds=4)
    partial = [item for item in saw(cases) if item.case.id != "bad-3"]
    with pytest.raises(AblationError, match="Arms over different corpora"):
        study(cases, partial)


def test_two_arms_for_one_tier_are_refused() -> None:
    cases = corpus(bads=2, sounds=2)
    arm = Arm(tier=Tier.MID, observations=tuple(saw(cases, tier=Tier.MID)))
    with pytest.raises(AblationError, match="two arms for the same tier"):
        Ablation(cases=tuple(cases), arms=(arm, arm))


def test_an_ablation_with_no_arms_is_refused() -> None:
    with pytest.raises(AblationError, match="measured nothing"):
        Ablation(cases=(bad(0),), arms=())


def test_asking_for_a_tier_that_did_not_run_is_an_error() -> None:
    cases = corpus(bads=2, sounds=2)
    result = study(cases, saw(cases, tier=Tier.MID))
    with pytest.raises(AblationError, match="no arm at frontier"):
        result.arm(Tier.FRONTIER)


# ============ cost, and the interval


def test_cost_per_bad_patch_stopped_is_undefined_when_none_was() -> None:
    """Reporting the whole spend as the price of zero catches reads as a number."""
    cases = corpus(bads=4, sounds=4)
    none_caught = study(cases, saw(cases, stops_bad=0, eur="1.00"))
    assert none_caught.arm(Tier.FRONTIER).spent == Decimal("8.00")
    assert none_caught.arm(Tier.FRONTIER).eur_per_bad_patch_stopped is None
    assert "no bad patch stopped" in none_caught.describe()

    some = study(cases, saw(cases, stops_bad=4, eur="1.00"))
    assert some.arm(Tier.FRONTIER).eur_per_bad_patch_stopped == Decimal("2.00")


def test_the_wilson_interval_stays_inside_the_unit_interval() -> None:
    """**Wilson rather than the normal approximation**, because the normal one puts
    bounds outside [0, 1] at exactly the rates this study lives at — and a lower
    bound below zero would make the *interval reaches zero* test, the one that can
    return `CUT`, impossible to fail."""
    for successes, trials in ((0, 5), (5, 5), (1, 100), (99, 100)):
        low, high = wilson(successes, trials)
        assert 0.0 <= low <= high <= 1.0

    assert wilson(0, 20)[0] == 0.0, "no catches: the lower bound is zero"
    assert wilson(20, 20)[0] > 0.0


def test_a_wider_interval_comes_from_fewer_trials() -> None:
    narrow = wilson(80, 100)
    wide = wilson(8, 10)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_the_interval_is_the_published_wilson_one() -> None:
    """**Pinned to the reference values, not to this implementation.** The 95%
    Wilson score interval for 8 successes in 10 trials is (0.4902, 0.9433), which
    is the figure a statistics text prints for that pair — and it is what separates
    Wilson from every half-way variant. The normal approximation gives
    (0.5521, 1.0479), and dropping the `1 + z^2/n` denominator alone gives
    (0.6785, 1.0)."""
    low, high = wilson(8, 10)
    assert round(low, 4) == 0.4902
    assert round(high, 4) == 0.9433


def test_no_successes_does_not_prove_a_rate_of_zero() -> None:
    """The property the normal approximation gets wrong: it puts both bounds at
    zero for 0 of 20, claiming certainty from twenty observations."""
    low, high = wilson(0, 20)
    assert low == 0.0
    assert high > 0.15, "twenty clean runs do not bound the rate below 15%"


def test_an_interval_over_nothing_is_refused() -> None:
    with pytest.raises(AblationError, match="over no trials"):
        wilson(0, 0)
    with pytest.raises(AblationError, match="3 successes in 2 trials"):
        wilson(3, 2)


def test_the_report_states_the_counterfactual_rather_than_implying_it() -> None:
    cases = corpus(bads=12, sounds=12)
    described = study(cases, saw(cases, stops_bad=10, stops_sound=1)).describe()
    assert "because nothing else is in the way" in described
    assert "Without the Adversary: 12 bad patches" in described

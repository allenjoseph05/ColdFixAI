"""S-13.5 — the learning curve, and the ablation that is allowed to say it is flat.

The study measures the project's own memory, which is the failure mode every test
here is written against: a harness built by the people hoping the playbook works.
So the cases that matter are the ones where it reports **no effect** and where it
**refuses to report an effect at all**, and both are reachable from the same
functions that report a win.

**Two of them are the whole story.** A run that takes fewer steps because it gave
up three stages earlier has not learned anything, and `CLAUDE.md`'s guard-counter
non-negotiable says so — *queries down while rows explode is not an improvement*.
The curve and the ablation each have a test for that shape, and in both the step
count improves.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from coldfix.bench.interleaving import schedule
from coldfix.bench.stats import MINIMUM_GROUP_SIZE
from coldfix.bench.timing import ProcessState
from coldfix.eval.learning import (
    MINIMUM_PROJECTS,
    STAGES,
    Condition,
    Effect,
    Grounding,
    LearningError,
    PlaybookAblation,
    Round,
    ablate,
    curve,
    observed,
)
from coldfix.explorer.loop import Exploration
from coldfix.explorer.stages import Outcome, Progress, Stage, Verdict

KEY = "Django/5"


def a_grounding(  # noqa: PLR0913 - six fields of one record, each of which a
    # default would silently decide: the arm, the step count and the completion
    # are exactly what the tests vary.
    project: str = "shop",
    *,
    steps: int = 10,
    stages: int = STAGES,
    ground: bool = True,
    condition: Condition = Condition.RETRIEVED,
    entries: int = 0,
) -> Grounding:
    return Grounding(
        project=project,
        fingerprint=KEY,
        steps=steps,
        stages_completed=stages,
        ground=ground,
        condition=condition,
        entries_offered=entries,
    )


# ================================================ an observation has to be one


def test_a_run_recorded_as_ground_with_a_stage_incomplete_is_refused() -> None:
    """`GroundingRun.finish` refuses a run with any stage incomplete, so an
    observation claiming both did not come from a run this system finished — and
    it would put a success in the numerator of a completion figure it never
    earned."""
    with pytest.raises(LearningError, match="did not come from a run this system finished"):
        a_grounding(stages=STAGES - 1, ground=True)


def test_a_withheld_arm_that_was_offered_entries_is_refused() -> None:
    """**The confound the ablation exists to remove.** A withheld arm that saw the
    playbook is the playbook compared against itself, and every figure downstream
    would be a difference between two retrieved runs."""
    with pytest.raises(LearningError, match="confound the ablation exists to remove"):
        a_grounding(condition=Condition.WITHHELD, entries=2)


def test_a_withheld_arm_with_nothing_offered_is_the_ordinary_case() -> None:
    """The control. Refusing the arm rather than the confound would make the study
    unbuildable."""
    assert a_grounding(condition=Condition.WITHHELD, entries=0).condition is Condition.WITHHELD


@pytest.mark.parametrize("stages", [-1, STAGES + 1])
def test_a_completion_outside_the_pipeline_is_refused(stages: int) -> None:
    with pytest.raises(LearningError, match="outside the pipeline"):
        a_grounding(stages=stages, ground=False)


def test_negative_steps_are_refused() -> None:
    with pytest.raises(LearningError, match="not a count"):
        a_grounding(steps=-1)


# ================================================ the join to the Explorer loop


def progress_with(completed: int) -> Progress:
    """A stage report with the first `completed` stages holding."""
    return Progress(
        outcomes=tuple(
            Outcome(
                stage,
                Verdict.HOLDS if index < completed else Verdict.FAILS,
                "measured",
            )
            for index, stage in enumerate(Stage)
        )
    )


def an_exploration(*, steps: int, completed: int, ground: bool) -> Exploration:
    """An `Exploration` carrying a stage report, built without a repository.

    The two halves are stand-ins holding the one attribute `observed` reads.
    `Exploration` refuses to be neither ground nor failed, so the shape is real
    even where the objects are not.
    """
    carrier = cast(Any, type("Carrier", (), {"progress": progress_with(completed)})())
    if ground:
        return Exploration(steps=steps, attempts=(), tried=(), grounded=carrier, emitted=carrier)
    return Exploration(steps=steps, attempts=(), tried=(), failure=carrier)


def test_a_grounded_exploration_records_its_steps_and_full_completion() -> None:
    """**AC 1's number, read off the thing that produced it.** Until S-7.14 this
    was a constant for every repository in the world."""
    sample = observed(
        an_exploration(steps=7, completed=STAGES, ground=True),
        project="shop",
        fingerprint=KEY,
    )

    assert sample.steps == 7
    assert sample.stages_completed == STAGES
    assert sample.ground


def test_a_failed_exploration_records_where_it_stopped() -> None:
    """A repository that would not ground is an observation, not a gap. Dropping
    it would silently restrict the curve to the projects that worked — which is
    the sample most likely to look like learning."""
    sample = observed(
        an_exploration(steps=12, completed=4, ground=False),
        project="blog",
        fingerprint=KEY,
        condition=Condition.WITHHELD,
        process_state=ProcessState.REUSED,
    )

    assert sample.steps == 12
    assert sample.stages_completed == 4
    assert not sample.ground
    assert sample.process_state is ProcessState.REUSED


# ================================================ AC 1 to AC 3: the curve


def series(steps: list[int], stages: list[int] | None = None) -> tuple[Grounding, ...]:
    heights = stages or [STAGES] * len(steps)
    return tuple(
        a_grounding(f"project-{index}", steps=cost, stages=height, ground=height == STAGES)
        for index, (cost, height) in enumerate(zip(steps, heights, strict=True))
    )


def test_the_series_is_reported_in_the_order_the_projects_were_seen() -> None:
    """**AC 1 and AC 2.** The x-axis is *the number of projects with this
    fingerprint*, which is a count over history — sorting by anything else here
    would invent a different axis."""
    plotted = curve(KEY, series([20, 14, 11, 6]))

    assert plotted.steps == (20, 14, 11, 6)
    assert plotted.completion == (STAGES,) * 4


def test_a_curve_whose_later_projects_cost_less_reports_a_decline() -> None:
    """**AC 3**, and `00-BRIEF.md` §5 step 13's acceptance: the tenth project of a
    kind should take materially fewer steps than the first."""
    assert curve(KEY, series([20, 18, 8, 6])).direction is Effect.IMPROVED


def test_a_curve_that_does_not_fall_says_the_playbook_is_not_bending_it() -> None:
    """**The result this study has to be able to report.** A harness that could
    only say *improved* would be the epic marking its own homework."""
    assert curve(KEY, series([12, 12, 12, 12])).direction is Effect.NO_EFFECT


def test_steps_falling_while_stage_completion_falls_is_not_a_learning_curve() -> None:
    """**The guard counter, and the step count improves in this case.**

    `CLAUDE.md`: *queries down while rows explode is not an improvement.* Here the
    later projects took half the steps — and ground four stages instead of nine,
    which is a run giving up sooner rather than one that learned. A study reading
    only AC 1's number would call this the best result it had ever seen.
    """
    plotted = curve(KEY, series([20, 18, 6, 5], stages=[STAGES, STAGES, 4, 4]))

    assert plotted.steps[-1] < plotted.steps[0], "the step count did improve"
    assert plotted.direction is Effect.GUARD_FELL


def test_too_few_projects_establishes_no_direction() -> None:
    """The series still plots. Its direction is not worth quoting."""
    plotted = curve(KEY, series([20, 6]))

    assert plotted.direction is Effect.NOT_ESTABLISHED
    assert plotted.steps == (20, 6), "and the data is still there"
    assert f"Fewer than {MINIMUM_PROJECTS} projects" in plotted.describe()


def test_the_extra_project_of_an_odd_count_goes_to_the_earlier_half() -> None:
    """The direction being looked for is a decline, and padding the half expected
    to be lower is how a study flatters itself."""
    earlier, later = curve(KEY, series([20, 18, 16, 8, 6])).halves

    assert len(earlier) == 3
    assert len(later) == 2


def test_the_curve_says_plainly_that_it_is_not_causal_evidence() -> None:
    """**The note on this story is the specification.** The series declines if
    memory works and also if the later projects were easier, and nothing
    longitudinal separates those. A reader who quotes the curve as proof has been
    told not to, by the curve."""
    rendered = curve(KEY, series([20, 18, 8, 6])).describe()

    assert "not evidence the playbook caused it" in rendered
    assert "ablation is the causal measurement" in rendered


def test_a_curve_pooling_two_fingerprints_is_refused() -> None:
    """An entry is filed per fingerprint, so pooling averages a learned playbook
    with a cold one."""
    mixed = (a_grounding("shop"), Grounding("blog", "Flask/3", 5, STAGES, ground=True))

    with pytest.raises(LearningError, match="averages a learned"):
        curve(KEY, mixed)


def test_the_same_project_twice_in_one_curve_is_refused() -> None:
    """AC 2 plots against the *number of projects*, and a repeat counts one twice."""
    with pytest.raises(LearningError, match="the same project appears twice"):
        curve(KEY, (a_grounding("shop"), a_grounding("shop", steps=3)))


def test_an_empty_curve_is_refused() -> None:
    with pytest.raises(LearningError, match="no projects in it"):
        curve(KEY, ())


# ================================================ AC 4: the ablation


def paired(saved: list[int], *, stages: list[tuple[int, int]] | None = None) -> PlaybookAblation:
    """Rounds assembled directly, for the arithmetic rather than the running."""
    heights = stages or [(STAGES, STAGES)] * len(saved)
    return PlaybookAblation(
        rounds=tuple(
            Round(
                retrieved=a_grounding(
                    "shop", steps=20 - gain, stages=with_, ground=with_ == STAGES
                ),
                withheld=a_grounding(
                    "shop",
                    steps=20,
                    stages=without,
                    ground=without == STAGES,
                    condition=Condition.WITHHELD,
                ),
            )
            for gain, (with_, without) in zip(saved, heights, strict=True)
        ),
        order=(),
        seed=1,
    )


def test_a_playbook_that_saves_steps_on_almost_every_round_is_an_improvement() -> None:
    """**AC 4.** Ten rounds, nine of them won: the interval's lower bound clears a
    half and the guard held."""
    study = paired([3, 2, 4, 3, 5, 2, 3, 4, 2, -1])

    assert study.helped == 9
    assert study.hurt == 1
    assert study.effect is Effect.IMPROVED
    assert study.steps_saved > 0


def test_a_narrow_lead_the_corpus_cannot_establish_is_not_an_improvement() -> None:
    """**The third test, in the form a paired study takes.** Six of ten is a lead
    whose interval runs from 26% to 88%; a corpus that could as easily have
    produced the opposite ordering has not shown one."""
    study = paired([1, 1, 1, 1, 1, 1, -1, -1, -1, -1])

    assert study.helped == 6
    assert study.effect is Effect.NO_EFFECT

    interval = study.interval
    assert interval is not None
    assert interval[0] < 0.5 < interval[1]


def test_rounds_that_all_come_out_identical_report_no_effect() -> None:
    """**The most likely early result, and the one the blocked version of this
    story would have produced for a completely different reason.** Nothing read a
    playbook entry then, so the arms were the same run twice; reporting *the
    playbook adds nothing* from that would have been a finding about the wiring in
    the costume of a finding about the playbook."""
    study = paired([0] * 10)

    assert study.decisive == 0
    assert study.interval is None
    assert study.effect is Effect.NO_EFFECT


def test_fewer_steps_with_less_of_the_repository_ground_is_refused() -> None:
    """**The guard, and the playbook wins every round on steps.**

    Nine rounds saved three steps each — and ground four stages instead of nine.
    That is an arm giving up sooner, and no claim about steps survives it. A study
    reporting AC 4's first figure alone would call this a triumph.
    """
    study = paired([3] * 10, stages=[(4, STAGES)] * 10)

    assert study.helped == 10, "it won every round on steps"
    assert study.steps_saved > 0
    assert study.completion_delta < 0
    assert study.effect is Effect.GUARD_FELL
    assert "ground less of the repository" in study.describe()


def test_too_few_rounds_establishes_nothing() -> None:
    study = paired([3] * (MINIMUM_GROUP_SIZE - 1))

    assert study.effect is Effect.NOT_ESTABLISHED
    assert f"Fewer than {MINIMUM_GROUP_SIZE} rounds" in study.describe()


def test_an_ablation_spanning_two_repositories_is_refused() -> None:
    """AC 4 grounds *the same unseen repository* both ways; two repositories differ
    by more than the condition."""
    rounds = (
        Round(
            retrieved=a_grounding("shop"),
            withheld=a_grounding("blog", condition=Condition.WITHHELD),
        ),
    )

    with pytest.raises(LearningError, match="spans"):
        PlaybookAblation(rounds=rounds, order=(), seed=1)


def test_a_round_holding_an_observation_from_the_other_arm_is_refused() -> None:
    """The arm an observation is filed under and the condition it was taken under
    have to be the same fact, or the study is measuring the labelling."""
    rounds = (
        Round(
            retrieved=a_grounding("shop", condition=Condition.WITHHELD),
            withheld=a_grounding("shop", condition=Condition.WITHHELD),
        ),
    )

    with pytest.raises(LearningError, match="labelled for the other arm"):
        PlaybookAblation(rounds=rounds, order=(), seed=1)


# ================================================ running the two arms


def counting_arm(condition: Condition, steps: int, calls: list[str]) -> object:
    def run() -> Grounding:
        calls.append(condition.name)
        return a_grounding("shop", steps=steps, condition=condition)

    return run


def test_both_arms_are_run_here_and_interleaved() -> None:
    """**S-1.6's construction, borrowed wholesale.** Each round runs both arms, and
    the order comes from `bench.interleaving.schedule` so that *what a fair
    schedule is* has one owner."""
    calls: list[str] = []
    study = ablate(
        cast(Any, counting_arm(Condition.RETRIEVED, 8, calls)),
        cast(Any, counting_arm(Condition.WITHHELD, 12, calls)),
        MINIMUM_GROUP_SIZE,
        seed=7,
    )

    assert len(calls) == 2 * MINIMUM_GROUP_SIZE
    assert calls.count("RETRIEVED") == calls.count("WITHHELD") == MINIMUM_GROUP_SIZE
    assert len(study.rounds) == MINIMUM_GROUP_SIZE
    assert study.seed == 7
    assert study.order == schedule(MINIMUM_GROUP_SIZE, "retrieved", "withheld", seed=7).order


def test_a_stored_measurement_cannot_be_passed_as_an_arm() -> None:
    """**The dangerous call is unrepresentable rather than discouraged.** A
    recorded step count would predate the very entries under test, which is worse
    than the stored-baseline problem `compare` exists to remove."""
    with pytest.raises(TypeError, match="cannot accept measurements taken earlier"):
        ablate(cast(Any, [8, 9, 8]), cast(Any, a_grounding), MINIMUM_GROUP_SIZE)


def test_too_few_rounds_are_refused_before_anything_is_ground() -> None:
    """Each round is two full groundings of a real repository. Discovering the
    floor after taking every sample would waste all of them."""
    calls: list[str] = []

    with pytest.raises(ValueError, match="at least"):
        ablate(
            cast(Any, counting_arm(Condition.RETRIEVED, 8, calls)),
            cast(Any, counting_arm(Condition.WITHHELD, 12, calls)),
            MINIMUM_GROUP_SIZE - 1,
        )

    assert calls == [], "nothing was ground"


def test_an_arm_returning_the_other_conditions_label_is_refused() -> None:
    """Caught when the study is assembled rather than when it is read, so a
    mislabelled run cannot reach a figure."""
    calls: list[str] = []

    with pytest.raises(LearningError, match="labelled for the other arm"):
        ablate(
            cast(Any, counting_arm(Condition.WITHHELD, 8, calls)),
            cast(Any, counting_arm(Condition.WITHHELD, 12, calls)),
            MINIMUM_GROUP_SIZE,
        )


# ================================================ the schedule, now shared


def test_the_schedule_balances_both_conditions_in_every_round() -> None:
    """A single shuffle of n A's and n B's can deal all the A's into the first
    half, which is the block design interleaving exists to replace."""
    drawn = schedule(20, "retrieved", "withheld", seed=3)

    pairs = [drawn.order[index : index + 2] for index in range(0, len(drawn.order), 2)]
    assert all(set(pair) == {"retrieved", "withheld"} for pair in pairs)


def test_the_seed_reproduces_the_order() -> None:
    """An experiment that cannot be re-run in the order it originally ran in is
    not reproducible."""
    first = schedule(12, "a", "b")

    assert schedule(12, "a", "b", seed=first.seed).order == first.order

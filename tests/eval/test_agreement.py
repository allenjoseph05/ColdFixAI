"""Ten runs of one repository, and what the agreement figure must not do.

S-15.1. `00-BRIEF.md` §6 calls this the honest form of "reliable", so the tests
that matter are the ones that stop it being reported as something better than it
is. Three of them are the module's own docstring, turned into failures:

- measured over cached runs it is 100% and is a measurement of the cache;
- measured with the null results dropped it is 100% over a single run;
- measured over a tie it has no primary finding, and naming one invents a winner.
"""

from __future__ import annotations

import inspect

import pytest

from coldfix.eval.agreement import (
    MINIMUM_RUNS,
    Agreement,
    AgreementError,
    Run,
    agreement,
)

SITE = "shop/serializers.py:42"
OTHER = "shop/views.py:17"


def runs(*outcomes: str | None, cached: bool = False) -> list[Run]:
    """One run per outcome, ids generated so no two collide."""
    return [
        Run(run_id=f"run-{index}", finding=outcome, served_from_cache=cached)
        for index, outcome in enumerate(outcomes)
    ]


def ten(*, agreeing: int, outcome: str | None = SITE, rest: str | None = OTHER) -> list[Run]:
    return runs(*([outcome] * agreeing), *([rest] * (MINIMUM_RUNS - agreeing)))


# ================================================== what the figure reports


def test_it_reports_agreement_on_the_primary_finding_as_a_percentage() -> None:
    """AC 2."""
    study = agreement("django-helpdesk", ten(agreeing=8))

    assert study.primary == SITE
    assert study.agreeing == 8
    assert study.rate == pytest.approx(0.8)


def test_the_point_estimate_carries_an_interval() -> None:
    """Ten runs is not many, and 80% reads like a measurement of a system.

    The Wilson interval says what ten runs can support, which at 8/10 is roughly
    half to nearly all.
    """
    study = agreement("django-helpdesk", ten(agreeing=8))

    low, high = study.interval
    assert low < study.rate < high
    assert low < 0.6, "ten runs cannot support a tight bound"
    assert "95% CI" in study.render()


def test_it_reports_the_distribution_of_alternatives() -> None:
    """AC 3. Not just how often the winner won, but what else was said."""
    study = agreement("django-helpdesk", runs(SITE, SITE, SITE, OTHER, OTHER, *([SITE] * 5)))

    assert study.distribution == {SITE: 8, OTHER: 2}
    assert "2 x " + OTHER in study.render()


def test_ten_runs_is_the_floor() -> None:
    """AC 4, refused rather than reported with a caveat.

    At five runs a single flip moves the rate twenty points, which cannot
    distinguish a reliable tool from an unreliable one — and that is the only
    question this figure exists to answer.
    """
    with pytest.raises(AgreementError, match="below the 10"):
        agreement("django-helpdesk", runs(*([SITE] * 9)))

    assert agreement("django-helpdesk", runs(*([SITE] * 10))).rate == 1.0


# ============================================= the three ways it is quietly wrong


def test_a_cached_run_is_refused_rather_than_counted() -> None:
    """**Ten cached runs are one run reported ten times.**

    S-5.1's replay cache returns the recorded answer, so an agreement study over
    cached runs measures the cache. AC 1 says *with cache disabled*; this refuses
    rather than noting it, because a caveat on a 100% figure is read by nobody
    who wanted the 100%.
    """
    served = runs(*([SITE] * MINIMUM_RUNS), cached=True)

    with pytest.raises(AgreementError, match="replay cache"):
        agreement("django-helpdesk", served)


def test_one_cached_run_among_ten_is_enough_to_refuse() -> None:
    """The study is over the set, so a single cached member contaminates it."""
    mixed = runs(*([SITE] * MINIMUM_RUNS))
    mixed[3] = Run(run_id="run-3", finding=SITE, served_from_cache=True)

    with pytest.raises(AgreementError, match="run-3"):
        agreement("django-helpdesk", mixed)


def test_runs_that_found_nothing_are_an_outcome_not_an_exclusion() -> None:
    """**The arithmetic that would report 100% over a single run.**

    Nine runs finding nothing and one finding a cause do not agree about that
    cause. They agree that there is nothing to find, and the disagreement is the
    interesting half.
    """
    study = agreement("healthy-repo", runs(*([None] * 9), SITE))

    assert study.distribution == {None: 9, SITE: 1}
    assert study.primary is None
    assert study.rate == pytest.approx(0.9)
    assert "nothing found" in study.render()


def test_nothing_found_reads_as_an_answer_in_the_report() -> None:
    """A repository where every run agrees there is nothing is a *good* result."""
    study = agreement("healthy-repo", runs(*([None] * MINIMUM_RUNS)))

    assert study.primary is None
    assert study.rate == 1.0
    assert not study.flipped
    assert "primary: nothing found" in study.render()


def test_a_tie_has_no_primary_finding() -> None:
    """Five and five. Naming either would invent a winner from an accident.

    `None` cannot carry this, because `None` already means *nothing found was
    the modal answer* — so the tie is a third state and reading `primary` on one
    raises rather than answering.
    """
    study = agreement("coin-flip", runs(*([SITE] * 5), *([OTHER] * 5)))

    assert study.undecided
    assert set(study.modal_outcomes) == {SITE, OTHER}
    with pytest.raises(AgreementError, match="no primary finding"):
        _ = study.primary


def test_a_tie_says_so_in_the_report_rather_than_showing_a_rate() -> None:
    """A reader who saw only "50%" would not know there was no answer at all."""
    rendered = agreement("coin-flip", runs(*([SITE] * 5), *([OTHER] * 5))).render()

    assert "no primary finding" in rendered
    assert "That is the result" in rendered


# =================================================== the seam S-15.4 consumes


def test_a_flip_is_reported_separately_from_a_low_rate() -> None:
    """S-15.4 records *diagnoses that flipped between runs*, and this is that fact.

    Nine-to-one is 90% and has flipped. A catalogue reading only the rate would
    record the repository as reliable and lose the one run that disagreed.
    """
    flipped = agreement("django-helpdesk", ten(agreeing=9))
    steady = agreement("django-helpdesk", runs(*([SITE] * MINIMUM_RUNS)))

    assert flipped.flipped
    assert flipped.rate == pytest.approx(0.9)
    assert not steady.flipped
    assert "the diagnosis flipped" in flipped.render()
    assert "every run reached the same outcome" in steady.render()


# ======================================================== the run set itself


def test_a_run_recorded_twice_is_refused() -> None:
    """It raises the denominator *and* the count that agrees with itself.

    So the figure improves for a reason that is not about the tool, which is the
    direction an evaluation number must never move by accident.
    """
    duplicated = runs(*([SITE] * MINIMUM_RUNS))
    duplicated.append(Run(run_id="run-0", finding=SITE))

    with pytest.raises(AgreementError, match="appears twice"):
        agreement("django-helpdesk", duplicated)


def test_a_run_without_an_id_is_refused() -> None:
    """Two runs that cannot be told apart cannot be counted."""
    with pytest.raises(AgreementError, match="needs an id"):
        Run(run_id="   ", finding=SITE)


def test_the_study_runs_nothing() -> None:
    """`CLAUDE.md`: a study does not take its own measurements.

    Asserted by construction — `agreement` takes recorded outcomes and there is
    no parameter through which a pipeline could be handed to it. The same shape
    `eval/ablation.py`'s `study` has, and the reason both can be re-run against
    recorded results without spending the corpus again.
    """
    parameters = set(inspect.signature(agreement).parameters)

    assert parameters == {"repository", "runs"}


def test_the_artifact_cannot_be_built_around_the_refusals() -> None:
    """`Agreement` is constructible directly, and that is deliberate — the
    refusals live in `agreement()` where the run set is assembled.

    Recorded rather than defended: a dataclass that validated its own tuple would
    duplicate the checks, and the entry point is the only way a caller gets one
    without writing the tuple by hand. This test exists so the split is a
    decision somebody made rather than an omission.
    """
    direct = Agreement(repository="anything", runs=tuple(runs(SITE, OTHER)))

    assert len(direct.runs) < MINIMUM_RUNS
    assert direct.undecided

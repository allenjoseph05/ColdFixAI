"""The failure catalogue, and the two ways it lies.

S-15.4. `00-BRIEF.md` calls this more credible than the success rate, which is
only true if its entries are real. So the tests here are about the two failure
modes of a document whose value is that it is uncomfortable:

- **omission**, which is why it exists;
- **padding** — recording a cheat nobody caught, or a diagnosis that did not
  flip. A catalogue that invents discomfort is as useless as one that hides it,
  and this is the half nobody guards.

The third is the empty case. An empty catalogue reads as good news and is the
least credible artifact here, so it says which of the two things it means.
"""

from __future__ import annotations

import pytest

from coldfix.audit.patchverdict import Attack, AttackResult, Outcome, Reproduction
from coldfix.eval.agreement import Agreement, Run, agreement
from coldfix.eval.catalogue import (
    CatalogueError,
    CaughtCheat,
    FailedGrounding,
    FlippedDiagnosis,
    NothingFound,
    catalogue,
)
from coldfix.explorer.run import Attempt, Failure
from coldfix.explorer.stages import Outcome as StageOutcome
from coldfix.explorer.stages import Progress, Stage, Verdict
from coldfix.screening.null import Conditions, NullResult

DIFF = """\
--- a/shop/views.py
+++ b/shop/views.py
@@ -1,3 +1,3 @@
-    return Ticket.objects.all()
+    return Ticket.objects.all()[:1]
"""

SITE = "shop/serializers.py:42"
OTHER = "shop/views.py:17"


def a_null_result(*, covers_everything: bool = True) -> NullResult:
    return NullResult(
        screened=("tickets",),
        healthy=("tickets",),
        unverified=(),
        unclassified=() if covers_everything else (("tickets", "seconds"),),
        unflagged=(),
        conditions=(
            Conditions(
                workload_id="tickets",
                scales=(10, 40, 160),
                distribution="uniform",
                reset_strategy="snapshot_restore",
                cache_control="a fresh process",
            ),
        ),
        thresholds={"flat cost (queries)": 120.0},
    )


def a_landed_attack(*, outcome: Outcome = Outcome.SUSPECT) -> AttackResult:
    return AttackResult(
        attack=Attack.CHEAT,
        outcome=outcome,
        detail="rows returned fell 97% while queries fell 40% — the payload changed",
        reproduction=(
            Reproduction(
                attack=Attack.CHEAT,
                shows="the patched revision returns one ticket where the original returned all",
                how="pytest -k tickets",
            )
            if outcome is Outcome.BROKE_IT
            else None
        ),
    )


def a_failure() -> Failure:
    stopped = StageOutcome(
        stage=Stage.CONNECT,
        verdict=Verdict.FAILS,
        detail="could not reach postgres on localhost:5432",
    )
    return Failure(
        reason="the database never accepted a connection",
        progress=Progress(outcomes=(stopped,)),
        attempts=(
            Attempt(step=1, stage=Stage.CONNECT, what="installed psycopg2", outcome=stopped),
        ),
        stopped_at=stopped,
    )


def a_flipped_study() -> Agreement:
    return agreement(
        "django-helpdesk",
        [Run(run_id=f"run-{index}", finding=SITE if index < 8 else OTHER) for index in range(10)],
    )


# ============================================== each kind carries its evidence


def test_nothing_found_carries_the_null_result_not_a_sentence() -> None:
    """AC 1. *Nothing found* means one thing at a sixteenfold sweep and another
    otherwise, so the thresholds and conditions travel with it."""
    entry = NothingFound(repository="quiet-repo", result=a_null_result())

    described = entry.describe()

    assert "nothing found across 1 workload(s)" in described
    assert "Thresholds applied:" in described


def test_a_null_result_that_covers_less_than_it_screened_says_so() -> None:
    """The distinction S-4.5 built and this catalogue must not flatten."""
    entry = NothingFound(repository="partial", result=a_null_result(covers_everything=False))

    assert not entry.covers_everything
    assert "does not cover all of them" in entry.describe()


def test_a_caught_cheat_carries_the_diff_and_the_attack() -> None:
    """AC 2, read literally. *The Adversary caught three cheats* is a claim."""
    entry = CaughtCheat(
        repository="django-helpdesk",
        finding="n+1 on /api/tickets/",
        diff=DIFF,
        caught_by=a_landed_attack(),
    )

    described = entry.describe()

    assert "cheat: is the improvement real" in described
    assert "Ticket.objects.all()[:1]" in described


def test_a_failed_grounding_carries_the_stage_that_never_completed() -> None:
    """AC 4. *Grounding failed* is not actionable; the stage and its predicate are."""
    entry = FailedGrounding(repository="unstandable", failure=a_failure())

    described = entry.describe()

    assert "never completed: connect" in described
    assert "installed psycopg2" in described


def test_a_flipped_diagnosis_carries_the_distribution() -> None:
    """AC 3. The alternatives are the point — 80% with nothing else shown is a
    number, and 8 of 10 here and 2 there is a finding about the tool."""
    entry = FlippedDiagnosis(repository="django-helpdesk", study=a_flipped_study())

    described = entry.describe()

    assert "the diagnosis flipped" in described
    assert OTHER in described


# ================================================== padding is refused as well


def test_an_attack_that_passed_is_not_a_caught_cheat() -> None:
    """**The failure mode nobody guards.**

    A catalogue whose credibility is that its entries are uncomfortable is
    destroyed as thoroughly by inventing discomfort as by hiding it — and an
    attack reporting *ran, and found nothing* caught nothing.
    """
    with pytest.raises(CatalogueError, match="caught nothing"):
        CaughtCheat(
            repository="shop",
            finding="n+1",
            diff=DIFF,
            caught_by=AttackResult(attack=Attack.CHEAT, outcome=Outcome.PASSED),
        )


def test_an_attack_that_never_ran_is_not_a_caught_cheat() -> None:
    """`NOT_RUN` is the quieter version of the same padding.

    An attack that could not see enough to answer is a gap in the audit, and
    recording it as a catch would turn a missing measurement into a success
    story about the Adversary.
    """
    with pytest.raises(CatalogueError, match="caught nothing"):
        CaughtCheat(
            repository="shop",
            finding="n+1",
            diff=DIFF,
            caught_by=AttackResult(
                attack=Attack.CHEAT,
                outcome=Outcome.NOT_RUN,
                detail="no baseline measurement was available",
            ),
        )


def test_a_cheat_without_its_diff_is_refused() -> None:
    """AC 2 asks for the diff, and a cheat nobody can read is a claim."""
    with pytest.raises(CatalogueError, match="no diff"):
        CaughtCheat(repository="shop", finding="n+1", diff="   ", caught_by=a_landed_attack())


def test_a_study_that_agreed_is_not_a_flipped_diagnosis() -> None:
    """An agreement study that agreed is a result and it is not a failure."""
    steady = agreement(
        "django-helpdesk", [Run(run_id=f"run-{index}", finding=SITE) for index in range(10)]
    )

    with pytest.raises(CatalogueError, match="reached the same outcome"):
        FlippedDiagnosis(repository="django-helpdesk", study=steady)


# ===================================================== the empty catalogue


def test_an_empty_catalogue_does_not_read_as_good_news() -> None:
    """**The whole point of `runs_covered`.**

    Empty over twenty runs is a claim that wants explaining; empty over one is an
    evaluation that has barely started. The entries cannot tell those apart, so
    the render says which and refuses to congratulate anybody.
    """
    rendered = catalogue(runs_covered=20).render()

    assert "not a result to be pleased about" in rendered
    assert "20 run(s)" in rendered
    assert "or that nobody recorded them" in rendered


def test_a_catalogue_over_no_runs_is_refused() -> None:
    """Nothing catalogued and nothing run are different answers.

    S-4.5's rule one layer out: an empty catalogue over zero runs is not an
    encouraging result, it is not a result.
    """
    with pytest.raises(CatalogueError, match="no runs"):
        catalogue(runs_covered=0)


# ================================================= published alongside results


def test_every_kind_appears_in_one_document() -> None:
    """AC 5. Four epics' negative results, in the thing a reader is handed."""
    report = catalogue(
        runs_covered=12,
        nothing_found=[NothingFound(repository="healthy", result=a_null_result())],
        cheats=[
            CaughtCheat(repository="shop", finding="n+1", diff=DIFF, caught_by=a_landed_attack())
        ],
        flipped=[FlippedDiagnosis(repository="flippy", study=a_flipped_study())],
        groundings=[FailedGrounding(repository="unstandable", failure=a_failure())],
    )

    rendered = report.render()

    assert report.entries == 4
    assert not report.empty
    assert "Repositories where nothing was found (1)" in rendered
    assert "Cheats that were caught (1)" in rendered
    assert "Diagnoses that flipped between runs (1)" in rendered
    assert "Groundings that failed (1)" in rendered
    assert "more credible than the success rate" in rendered


def test_a_section_with_no_entries_is_absent_rather_than_empty() -> None:
    """An empty heading reads as *we looked and found none*, which is a claim.

    The catalogue makes no such claim per category: it reports what it was given.
    """
    rendered = catalogue(
        runs_covered=3,
        groundings=[FailedGrounding(repository="unstandable", failure=a_failure())],
    ).render()

    assert "Groundings that failed (1)" in rendered
    assert "Cheats that were caught" not in rendered

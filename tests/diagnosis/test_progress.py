"""S-8.9 — stopping, and having something to show for it.

Two of the three criteria were already built. The 40-experiment cap has existed
since S-5.4, so the tests here **assert** it rather than a reimplementation of it;
the progress check needed a number S-5.4's default gets wrong.

The third criterion is the story: *on exhaustion, emits a partial chain
containing the exclusions — a proven negative is a result.* The pairing that
makes it work is that `EvidenceChain` requires a confirming experiment and
`PartialChain` refuses to hold one, so the two partition and neither can
impersonate the other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import (
    DEFAULT_STALL_AFTER,
    PHASE_CAPS,
    Budget,
    Disposition,
    ProgressStalledError,
    Scope,
    StepUnit,
)
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.diagnosis.progress import (
    INVESTIGATION_STALL_AFTER,
    NO_NARROWING,
    PartialChain,
    ProgressError,
    Stopped,
    check_stall_configuration,
    partial_chain,
    progress_conclusion,
)
from coldfix.primitives.scaling import Distribution

CONDITIONS = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=[10, 100, 1000],
)

SYMPTOM = Symptom(metric="seconds", magnitude=8.24, at_scale=1000)


def a_log(*verdicts: Verdict) -> ExperimentLog:
    log = ExperimentLog()
    for index, verdict in enumerate(verdicts):
        log.append(
            hypothesis=f"hypothesis {index}",
            primitive="scaling.volume",
            rationale=f"reason {index}",
            target="shop.books.list",
            design=f"scaling.volume() attempt {index}",
            measurement={"db.query": 7.0},
            verdict=verdict,
            outcome=f"outcome {index}",
        )
    return log


def exclusions_from(log: ExperimentLog) -> tuple[Exclusion, ...]:
    return tuple(
        Exclusion(experiment=item, conditions=CONDITIONS)
        for item in log.experiments
        if item.verdict is Verdict.REJECTED
    )


# ============================================ AC 1: the cap exists, and it is 40


def test_the_investigate_cap_is_forty_experiments_per_finding() -> None:
    """**Asserted, not built.** S-5.4 compiled this and nothing here
    re-implements it — the third Epic 8 criterion to turn out already enforced,
    after S-8.1's no-cascade and S-8.6's attached measurement."""
    cap = PHASE_CAPS[Phase.INVESTIGATE]

    assert cap.limit == 40
    assert cap.unit is StepUnit.EXPERIMENT
    assert cap.scope is Scope.FINDING
    assert cap.on_exhaustion is Disposition.PARTIAL


def test_a_model_call_does_not_spend_an_experiment() -> None:
    """**The defect S-5.4 predicted and Epic 5 then shipped.** Its docstring:
    *§12.1 budgets 120 model calls per finding against a cap of 40 experiments —
    so an experiment is about three calls, and a cap counted in calls would halt
    investigation at a third of its intended budget.*

    `Session.run` counted every call, so the forty-experiment cap was a
    thirteen-experiment cap until a whole loop was run against it. Three calls
    here, and the budget must still say nothing has been spent.
    """
    session = a_session()

    for _ in range(3):
        session.budget.authorize(Phase.INVESTIGATE, "F1")

    assert session.budget.used(Phase.INVESTIGATE, "F1") == 0


def test_grounding_still_counts_its_own_steps() -> None:
    """The control. Grounding's cap *is* counted in steps, so the fix above must
    not have stopped it counting — S-7.10's whole budget rests on it."""
    assert PHASE_CAPS[Phase.GROUND].unit is StepUnit.STEP


# ======================== AC 2: eight experiments with no narrowing, not three


def test_an_investigation_stalls_after_eight_rather_than_the_default_three() -> None:
    """`03-agents.md` §4.5. An investigation that has rejected three hypotheses
    has ruled out three things, which `00-BRIEF.md` §9 ships as an answer —
    stopping it there throws away the exclusions it was buying."""
    assert INVESTIGATION_STALL_AFTER == 8
    assert DEFAULT_STALL_AFTER != INVESTIGATION_STALL_AFTER


def test_a_budget_with_another_progress_check_is_refused_not_corrected() -> None:
    """S-7.10's construction and its argument: silently substituting the right
    value hides that the caller asked for something else."""
    wrong = Budget(
        ledger=Ledger(),
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)),
        stall_after=DEFAULT_STALL_AFTER,
    )

    with pytest.raises(ProgressError, match="progress check is 8"):
        check_stall_configuration(wrong)


def test_the_right_budget_is_accepted() -> None:
    """The control. A check that refused every budget would pass the test above
    and make an investigation impossible to configure."""
    check_stall_configuration(a_session().budget)  # must not raise


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (Verdict.REJECTED, NO_NARROWING),
        (Verdict.NARROWED, None),
        (Verdict.CONFIRMED, None),
    ],
)
def test_only_a_rejection_counts_as_no_narrowing(verdict: Verdict, expected: str | None) -> None:
    """`02-architecture.md` §2.2's distinction, not this module's: *reject → new
    hypothesis informed by the exclusion; narrow → new hypothesis, one level
    deeper.* Only the second goes deeper, and `None` clears S-5.4's run of
    repeats rather than extending it."""
    assert progress_conclusion(verdict) == expected


def test_eight_rejections_in_a_row_stall_the_phase() -> None:
    """The threshold reached through the real mechanism rather than asserted as a
    constant."""
    budget = a_session().budget

    with pytest.raises(ProgressStalledError):
        for _ in range(INVESTIGATION_STALL_AFTER):
            budget.record_step(Phase.INVESTIGATE, "F1", progress_conclusion(Verdict.REJECTED))


def test_a_narrowing_resets_the_run_so_the_phase_does_not_stall() -> None:
    """**The control that gives the test above its meaning.** A conclusion that
    never cleared would stall an investigation that was converging, which is the
    opposite defect and the one that loses a finding rather than wasting budget.
    """
    budget = a_session().budget

    for index in range(INVESTIGATION_STALL_AFTER * 2):
        verdict = Verdict.NARROWED if index % 4 == 3 else Verdict.REJECTED
        budget.record_step(Phase.INVESTIGATE, "F1", progress_conclusion(verdict))

    assert budget.used(Phase.INVESTIGATE, "F1") == INVESTIGATION_STALL_AFTER * 2


def a_session() -> Session:
    return Session(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries.",
        source="shop/views.py",
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)),
        stall_after=INVESTIGATION_STALL_AFTER,
    )


# ================= AC 3: a partial chain, and what makes it not an evidence chain


def test_a_partial_chain_carries_the_exclusions_it_bought() -> None:
    """`00-BRIEF.md` §9: a proven negative is a result. The exclusions are the
    result, and they arrive with the conditions that make them conditional —
    a partial chain read without those is F3 in a report again."""
    log = a_log(Verdict.REJECTED, Verdict.REJECTED, Verdict.NARROWED)

    chain = partial_chain(
        symptom=SYMPTOM,
        stopped=Stopped.CAP,
        conditions=CONDITIONS,
        experiments=log.experiments,
        exclusions=exclusions_from(log),
    )

    assert len(chain.exclusions) == 2
    assert len(chain.narrowed) == 1
    assert "fixture shape uniform" in chain.describe()
    assert "NO CAUSE ESTABLISHED" in chain.describe()


def test_a_partial_chain_may_not_carry_a_confirmation() -> None:
    """**The mirror of `EvidenceChain`'s requirement, and the pairing is the
    point.** That artifact needs at least one confirming experiment; this one
    refuses to hold any. Together they partition, so neither can impersonate the
    other — and a partial chain carrying a confirmation would report an
    established finding as an absent one, which loses a result."""
    log = a_log(Verdict.REJECTED, Verdict.CONFIRMED)

    with pytest.raises(ProgressError, match="owes an evidence chain"):
        partial_chain(
            symptom=SYMPTOM,
            stopped=Stopped.CAP,
            conditions=CONDITIONS,
            experiments=log.experiments,
            exclusions=exclusions_from(log),
        )


def test_a_partial_chain_has_nowhere_to_put_a_cause() -> None:
    """Their absence is the artifact's meaning. A field for a mechanism, a site or
    a confidence is somewhere a reader could put a guess about an investigation
    that established none of them."""
    fields = set(PartialChain.model_fields)

    assert not fields & {"mechanism", "site", "confidence", "localization"}


def test_an_investigation_that_ran_nothing_is_not_a_result() -> None:
    """It has not learned that there is nothing to find — it has not looked."""
    with pytest.raises(ProgressError):
        partial_chain(
            symptom=SYMPTOM,
            stopped=Stopped.CAP,
            conditions=CONDITIONS,
            experiments=(),
            exclusions=(),
        )


def test_a_run_that_narrowed_but_never_rejected_still_reports_what_it_bought() -> None:
    """Empty exclusions are legitimate: forty experiments that all narrowed
    exclude nothing while still having learned something. Reporting that as an
    empty section would read as a missing input."""
    log = a_log(Verdict.NARROWED, Verdict.NARROWED)

    chain = partial_chain(
        symptom=SYMPTOM,
        stopped=Stopped.STALL,
        conditions=CONDITIONS,
        experiments=log.experiments,
        exclusions=(),
    )

    assert chain.exclusions == ()
    assert "Nothing was ruled out" in chain.describe()
    assert "2 experiment(s) ran, 2 of which narrowed" in chain.describe()


@pytest.mark.parametrize(
    ("stopped", "disposition"),
    [
        (Stopped.CAP, Disposition.PARTIAL),
        (Stopped.STALL, Disposition.ESCALATE),
        (Stopped.INSTRUMENTS, Disposition.ESCALATE),
    ],
)
def test_each_way_of_stopping_carries_what_to_do_about_it(
    stopped: Stopped, disposition: Disposition
) -> None:
    """§7.2's table. Three ways to run out because a reader's next action differs
    for each — the argument S-3.1 makes for four applicability states, and
    collapsing them into *it failed* would lose the one saying the subject may
    have no more applicable experiments."""
    assert stopped.disposition is disposition


def test_the_partial_chain_is_frozen() -> None:
    log = a_log(Verdict.REJECTED)
    chain = partial_chain(
        symptom=SYMPTOM,
        stopped=Stopped.CAP,
        conditions=CONDITIONS,
        experiments=log.experiments,
        exclusions=exclusions_from(log),
    )

    with pytest.raises(Exception, match=r"frozen|immutable"):
        chain.stopped = Stopped.STALL  # type: ignore[misc]


def test_a_confirmed_experiment_and_a_partial_chain_cannot_both_be_true() -> None:
    """The partition, checked from both sides in one test: the same log that
    `EvidenceChain` would accept as localization is the one `PartialChain`
    refuses, and vice versa."""
    confirming = a_log(Verdict.CONFIRMED)
    unconfirming = a_log(Verdict.REJECTED)

    with pytest.raises(ProgressError):
        partial_chain(
            symptom=SYMPTOM,
            stopped=Stopped.CAP,
            conditions=CONDITIONS,
            experiments=confirming.experiments,
            exclusions=(),
        )

    assert partial_chain(
        symptom=SYMPTOM,
        stopped=Stopped.CAP,
        conditions=CONDITIONS,
        experiments=unconfirming.experiments,
        exclusions=exclusions_from(unconfirming),
    )

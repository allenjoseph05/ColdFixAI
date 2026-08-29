"""S-9.9 — whether *nothing found* is an answer or an interruption.

Two controls decide whether this module is worth anything.

**An audit that always says sufficient ships every truncated run as a result** —
forty experiments cut off mid-search reported as *screened and nothing found*.
So a `CAP` stop must never be sufficient.

**An audit that never says sufficient makes `negative_sound` unreachable**, which
puts `00-BRIEF.md` §9's shippable null result back where ADR 094 found it. So a
run that ran out of questions with adequate exclusions must reach it.

The two are the same test with one field changed, which is the only arrangement
that can tell them apart.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import sufficiency as sufficiency_module
from coldfix.audit.exclusions import Narrowness, audit_all
from coldfix.audit.sufficiency import (
    RAN_OUT_OF_QUESTIONS,
    RESIDUE,
    Insufficiency,
    assess_sufficiency,
    verdict_for_partial,
)
from coldfix.audit.verdict import Route, Subject, Verdict, route
from coldfix.bench.stats import CONSTANT_BELOW, SUPERLINEAR_ABOVE, Fit, Growth
from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import PHASE_CAPS, Budget
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.diagnosis.log import Verdict as LogVerdict
from coldfix.diagnosis.loop import Investigation, Measured, run_investigation
from coldfix.diagnosis.progress import PartialChain, Stopped, partial_chain
from coldfix.llm.client import ReplayingClient
from coldfix.primitives.scaling import Distribution
from fixtures.thesis import (
    CONDITIONS as THESIS_CONDITIONS,
)
from fixtures.thesis import (
    SCALES,
    _repeat_recordings,
    _synthetic_recordings,
    an_investigation,
    instruments,
)

FINDING = "n.plus.one"
PLATFORM = "x86_64-linux"
SYMPTOM = Symptom(metric="seconds", magnitude=8.24, at_scale=1000)

WIDE = Conditions.of(
    fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
    platform=PLATFORM,
    concurrency=[1, 8],
    scales=[10.0, 100.0, 1000.0],
)
UNIFORM_ONLY = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform=PLATFORM,
    concurrency=[1, 8],
    scales=[10.0, 100.0, 1000.0],
)
SINGLE_PLATFORM = Conditions.of(
    fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
    platform=PLATFORM,
    concurrency=[1, 8],
    scales=[10.0, 100.0, 1000.0],
)


def a_budget() -> Budget:
    return Budget(ledger=Ledger(), rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 17)))


def a_log(*verdicts: LogVerdict) -> ExperimentLog:
    log = ExperimentLog()
    for index, verdict in enumerate(verdicts):
        log.append(
            hypothesis=f"hypothesis {index}",
            primitive="scaling.volume",
            rationale=f"reason {index}",
            target="shop.books.list",
            design=f"scaling.volume(scales=[10, 100, 1000]) attempt {index}",
            measurement={"db.query": 7.0},
            verdict=verdict,
            outcome=f"outcome {index}",
        )
    return log


def a_chain(
    *,
    stopped: Stopped = Stopped.INSTRUMENTS,
    conditions: Conditions = WIDE,
    verdicts: tuple[LogVerdict, ...] = (LogVerdict.REJECTED,),
) -> PartialChain:
    log = a_log(*verdicts)
    exclusions = tuple(
        Exclusion(experiment=item, conditions=conditions)
        for item in log.experiments
        if item.verdict is LogVerdict.REJECTED
    )
    return partial_chain(
        symptom=SYMPTOM,
        stopped=stopped,
        conditions=conditions,
        experiments=log.experiments,
        exclusions=exclusions,
    )


def a_fit(*, exponent: float = 0.02) -> Fit:
    return Fit(
        slope=0.0,
        intercept=7.0,
        linear_r_squared=0.99,
        exponent=exponent,
        power_r_squared=0.98,
        growth=Growth.CONSTANT,
        constant_below=CONSTANT_BELOW,
        superlinear_above=SUPERLINEAR_ABOVE,
    )


# =========================================== the decision is the harness's


def test_sufficiency_is_decided_without_asking_a_model() -> None:
    """**S-0.8 measured a model asked *should we stop?* answering no, 60 times
    out of 60** — on curated evidence with the right answer already worked out.
    Routing the same question through a second model and hoping a different frame
    saves it is the same question asked of the same kind of thing.

    F6's rule carried to its end: what counts as enough is decided by the harness,
    because a self-judged criterion is one the judge is incentivised to claim.
    Every input here is a fact the run already recorded.
    """
    imported = set(vars(sufficiency_module))
    assert not imported & {"ModelClient", "Session", "invoke", "StepOutcome"}

    parameters = {
        name
        for _, function in inspect.getmembers(sufficiency_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"client", "session", "question", "log"}


# ============ AC 1: were the exclusions established under adequate conditions?


def test_the_exclusion_audit_is_s_9_2s_and_not_a_second_copy() -> None:
    """**AC 1 needed no new machinery.** *Were the exclusions established under
    adequate conditions* is S-9.2's question word for word, and a `PartialChain`
    carries the same `Exclusion` type it already attacks. Two modules holding two
    copies of one argument is the duplication S-9.3 avoided by having both consult
    a single proof."""
    chain = a_chain(conditions=UNIFORM_ONLY)
    audit = assess_sufficiency(chain)
    assert audit.exclusions == audit_all(chain.exclusions)


def test_a_narrow_exclusion_makes_the_negative_insufficient() -> None:
    """Uniform-only is the provably blindest shape (`Σ k²` is minimized when every
    parent is equal), so a negative resting on it does not hold as widely as it
    reads."""
    audit = assess_sufficiency(a_chain(conditions=UNIFORM_ONLY))
    assert Insufficiency.EXCLUSIONS_TOO_NARROW in audit.shortfalls
    assert not audit.sufficient
    assert Narrowness.UNIFORM_ONLY in audit.inadequate[0].objections


def test_a_wide_exclusion_leaves_nothing_to_object_to() -> None:
    """The control. An audit objecting to every set of conditions makes §9's null
    results unreachable — S-9.2's load-bearing test, one layer up."""
    audit = assess_sufficiency(a_chain(conditions=WIDE))
    assert audit.inadequate == ()
    assert Insufficiency.EXCLUSIONS_TOO_NARROW not in audit.shortfalls


def test_a_single_platform_does_not_make_a_negative_insufficient() -> None:
    """S-9.2 records a single platform as a **bound**, not a defect: demanding a
    second architecture is not a remedy anybody can apply. Counting it here would
    make every run on one machine insufficient, which is every run."""
    audit = assess_sufficiency(a_chain(conditions=SINGLE_PLATFORM))
    assert audit.sufficient
    assert Narrowness.SINGLE_PLATFORM in audit.exclusions[0].objections


def test_one_narrow_exclusion_among_several_is_enough_to_object() -> None:
    """**A fixture with one exclusion cannot tell `any` from `all`**, and this
    project has now recorded eight cases where the right answer and the wrong
    answer coincided because nothing varied.

    The rule is `any`: a negative resting on four exclusions is only as good as
    its weakest, because the hypothesis that narrow one failed to rule out is
    still live.
    """
    log = a_log(LogVerdict.REJECTED, LogVerdict.REJECTED)
    first, second = log.experiments
    chain = partial_chain(
        symptom=SYMPTOM,
        stopped=Stopped.INSTRUMENTS,
        conditions=WIDE,
        experiments=log.experiments,
        exclusions=[
            Exclusion(experiment=first, conditions=WIDE),
            Exclusion(experiment=second, conditions=UNIFORM_ONLY),
        ],
    )

    audit = assess_sufficiency(chain)
    assert len(audit.inadequate) == 1
    assert Insufficiency.EXCLUSIONS_TOO_NARROW in audit.shortfalls
    assert not audit.sufficient


def test_a_fit_is_passed_through_to_the_scale_audit() -> None:
    """S-9.4 judges the sweep behind a growth claim, and a narrow span is an
    objection there. Dropping the parameter would silently un-audit the scale
    axis while every other test still passed."""
    narrow = a_chain(
        conditions=Conditions.of(
            fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
            platform=PLATFORM,
            concurrency=[1, 8],
            scales=[10.0, 20.0],
        )
    )
    index = narrow.exclusions[0].experiment.index
    audit = assess_sufficiency(narrow, fits={index: a_fit()})
    assert Narrowness.NARROW_SCALE_SPAN in audit.exclusions[0].objections
    assert not audit.sufficient


def test_a_missing_fit_is_not_an_objection() -> None:
    """The control for the one above, and S-9.2's rule rather than this module's:
    not every rejection came from a sweep, and inventing a curve to judge would be
    auditing one nobody drew."""
    assert assess_sufficiency(a_chain()).sufficient


# ====== AC 2: a result, or a run that stopped too early


@pytest.mark.parametrize("stopped", [Stopped.INSTRUMENTS, Stopped.STALL])
def test_a_run_that_ran_out_of_questions_with_wide_exclusions_is_a_result(
    stopped: Stopped,
) -> None:
    """**The control that makes `negative_sound` reachable.** `INSTRUMENTS` means
    the run ran out of applicable questions; `STALL` means S-5.4 has already
    concluded that more steps of the same kind will not change the answer — a
    sufficiency judgement the budget module makes before this one does."""
    assert assess_sufficiency(a_chain(stopped=stopped)).sufficient


def test_a_run_stopped_by_the_cap_is_never_a_result() -> None:
    """**The control that makes this module worth having.** A `CAP` stop ran out
    of *money* with something still being proposed, and a negative from an
    interrupted search is not a negative. An audit missing this ships every
    truncated run as *screened and nothing found*."""
    audit = assess_sufficiency(a_chain(stopped=Stopped.CAP))
    assert not audit.sufficient
    assert Insufficiency.STOPPED_ON_BUDGET in audit.shortfalls


def test_the_two_ways_of_running_out_of_questions_are_named_as_data() -> None:
    assert set(RAN_OUT_OF_QUESTIONS) == {Stopped.INSTRUMENTS, Stopped.STALL}
    assert Stopped.CAP not in RAN_OUT_OF_QUESTIONS


def test_sufficiency_and_the_disposition_answer_different_questions() -> None:
    """**They deliberately diverge, and folding one into the other would answer
    one question with the other.** §7.2 gives the cap `PARTIAL` — emit the chain —
    and the stall `ESCALATE`; that is what the *run* does next. Whether the
    negative is **believable** is a different question, and the `CAP` stop is at
    once the one that ships a partial chain and the one worth least."""
    cap = a_chain(stopped=Stopped.CAP)
    stall = a_chain(stopped=Stopped.STALL)

    assert cap.stopped.disposition is not stall.stopped.disposition
    assert not assess_sufficiency(cap).sufficient
    assert assess_sufficiency(stall).sufficient


@pytest.mark.parametrize("stopped", list(Stopped))
def test_the_audit_reports_the_reason_the_run_actually_stopped(stopped: Stopped) -> None:
    """**The survivor of the sabotage pass.** Hardcoding the stop reason left every
    verdict correct and every report *wrong*: a cap-stopped run whose audit says
    *every applicable instrument had already answered* tells a human the search
    was finished when it was cut off.

    A verdict a reader cannot check against the run it describes is worth less
    than no verdict, and nothing here was asserting the two agreed.
    """
    audit = assess_sufficiency(a_chain(stopped=stopped))
    assert audit.stopped is stopped
    assert stopped.value in audit.describe()


def test_a_run_that_ruled_nothing_out_has_no_result_to_report() -> None:
    """`PartialChain` says the exclusions **are** the result and allows the tuple
    to be empty — correctly, since forty narrowings that never rejected have still
    learned something. *Learned something* and *established a trustworthy
    negative* are different claims, and §9 ships the second."""
    audit = assess_sufficiency(a_chain(verdicts=(LogVerdict.NARROWED,)))
    assert Insufficiency.NOTHING_RULED_OUT in audit.shortfalls
    assert not audit.sufficient


def test_there_is_no_minimum_experiment_count() -> None:
    """Deliberate. Any floor would be a guess, and S-9.4's precedent is that a
    threshold is derived or it does not belong — a subject supporting one
    applicable instrument that came back rejected has answered the question in one
    experiment. The exclusion rule does the work without inventing a number."""
    source = inspect.getsource(sufficiency_module.assess_sufficiency)
    assert "len(chain.experiments)" not in source
    assert assess_sufficiency(a_chain(verdicts=(LogVerdict.REJECTED,))).sufficient


def test_every_shortfall_is_reported_not_just_the_first() -> None:
    """A reader acting on one and re-running would meet the next. Three
    shortfalls, three different next actions."""
    audit = assess_sufficiency(a_chain(stopped=Stopped.CAP, conditions=UNIFORM_ONLY))
    assert set(audit.shortfalls) == {
        Insufficiency.STOPPED_ON_BUDGET,
        Insufficiency.EXCLUSIONS_TOO_NARROW,
    }


def test_every_shortfall_carries_a_remedy_and_the_report_prints_it() -> None:
    """**S-9.2's recorded lesson: the reader gets the report, not the enum.** That
    story's one sabotage survivor deleted the remedy text and nothing failed,
    because the tests asserted each objection *has* one and never that the
    rendering prints it."""
    for shortfall in Insufficiency:
        assert shortfall.remedy.strip()

    written = assess_sufficiency(a_chain(stopped=Stopped.CAP, conditions=UNIFORM_ONLY)).describe()
    assert "stopped too early" in written
    assert Insufficiency.STOPPED_ON_BUDGET.remedy in written
    assert Insufficiency.EXCLUSIONS_TOO_NARROW.remedy in written


def test_a_sufficient_report_says_it_is_a_result_and_carries_its_bound() -> None:
    """`negative_sound` means *this run's exclusions hold and it ran out of
    questions*, never *there is no performance problem here* — the audit sees the
    hypotheses that were attempted and not the ones nobody thought of."""
    written = assess_sufficiency(a_chain()).describe()
    assert "This is a result" in written
    assert RESIDUE in written


# ==================== AC 3: a sufficient run is not returned to investigate


def test_a_sufficient_run_is_reported_and_not_re_investigated() -> None:
    """AC 3, with the budget completely untouched — forty experiments available
    and the audit sends it to `REPORT` anyway."""
    verdict = verdict_for_partial(assess_sufficiency(a_chain()))
    budget = a_budget()

    assert verdict.verdict is Verdict.NEGATIVE_SOUND
    assert budget.remaining(Phase.INVESTIGATE, FINDING) == PHASE_CAPS[Phase.INVESTIGATE].limit
    assert route(verdict, budget, FINDING).route is Route.REPORT


def test_an_insufficient_run_escalates_rather_than_asking_for_experiments() -> None:
    """Right for a reason rather than by default: a run stopped by the cap has no
    experiments left to answer with, and one stopped by the stall has just been
    told more steps of the same kind will not change the answer. In both, *run
    more experiments* is unavailable — the lever ADR 094 says an audit must not
    reach for."""
    verdict = verdict_for_partial(assess_sufficiency(a_chain(stopped=Stopped.CAP)))
    assert verdict.verdict is Verdict.INCONCLUSIVE
    assert route(verdict, a_budget(), FINDING).route is Route.ESCALATE


@pytest.mark.parametrize("stopped", list(Stopped))
@pytest.mark.parametrize("conditions", [WIDE, UNIFORM_ONLY])
def test_no_partial_chain_verdict_can_reach_investigate_or_repair(
    stopped: Stopped, conditions: Conditions
) -> None:
    """**AC 3 as a property of the type rather than a branch.** The verdict is
    about a `Subject.PARTIAL_CHAIN`, and S-9.8 refuses `sound`, `unsound` and
    `unrepresentative` about one — those three presuppose a claimed cause. So the
    only constructible answers route to `REPORT` or `ESCALATE`, whatever the
    budget holds, and there is no exception anybody could add."""
    verdict = verdict_for_partial(
        assess_sufficiency(a_chain(stopped=stopped, conditions=conditions))
    )
    assert verdict.subject is Subject.PARTIAL_CHAIN

    for spent in (0, PHASE_CAPS[Phase.INVESTIGATE].limit):
        budget = a_budget()
        for index in range(spent):
            budget.record_step(Phase.INVESTIGATE, FINDING, conclusion=f"step-{index}")
        assert route(verdict, budget, FINDING).route not in (Route.INVESTIGATE, Route.REPAIR)


def test_the_insufficient_verdict_names_what_is_missing() -> None:
    """S-9.8's schema refuses an `inconclusive` with nothing said, so this is
    enforced twice — but the text has to be the shortfalls rather than a
    placeholder, or the escalation carries no instruction."""
    verdict = verdict_for_partial(
        assess_sufficiency(a_chain(stopped=Stopped.CAP, conditions=UNIFORM_ONLY))
    )
    assert Insufficiency.STOPPED_ON_BUDGET.value in verdict.detail
    assert Insufficiency.EXCLUSIONS_TOO_NARROW.value in verdict.detail


def test_a_sufficient_verdict_carries_no_objection() -> None:
    """`negative_sound` attaches no payload in S-9.8's vocabulary, and one that
    carried an objection would be saying two things at once."""
    assert verdict_for_partial(assess_sufficiency(a_chain())).detail == ""


# ============ AC 4: an investigation the agent would have continued, stopped


def a_run_the_agent_would_have_continued() -> tuple[Investigation, Budget]:
    """A real `run_investigation` that ends with the model still proposing.

    One instrument, one rejection, then **three proposals of the instrument that
    has already answered** — which is `propose` running out rather than the cap
    doing it. The agent had not decided to stop; S-0.8 measured that it does not.
    """
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})
    investigation.instruments = instruments("scaling.volume")
    first = _synthetic_recordings(investigation, [LogVerdict.REJECTED])
    repeats = _repeat_recordings(investigation, "scaling.volume")

    result = run_investigation(
        investigation.sessions,
        ReplayingClient([*first, *repeats]),
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=THESIS_CONDITIONS,
        execute=lambda spec: Measured(measurement={"db.query": 2.0}),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )
    return result, investigation.hypothesis_session.budget


def test_a_real_run_stops_with_the_agent_still_proposing_and_budget_to_spare() -> None:
    """The premise of AC 4, established before anything is audited. If the run
    had exhausted its budget there would be nothing for the audit to overrule."""
    result, budget = a_run_the_agent_would_have_continued()

    assert result.stopped is Stopped.INSTRUMENTS
    assert budget.remaining(Phase.INVESTIGATE, FINDING) > 0


def test_the_audit_stops_an_investigation_the_agent_would_have_continued() -> None:
    """**AC 4.** The experiments and the stop reason come from a real
    `run_investigation`; only the conditions are widened, because the thesis
    fixture is uniform-only and serial-only by construction and S-9.2 objects to
    both — correctly.

    With 39 of 40 experiments still available, the audit sends this to `REPORT`.
    Nothing asked the agent whether it was finished, which is the point: it had
    just proposed a fourth experiment three times running.
    """
    result, budget = a_run_the_agent_would_have_continued()
    assert result.stopped is not None
    rejected = [item for item in result.log.experiments if item.verdict is LogVerdict.REJECTED]
    chain = partial_chain(
        symptom=SYMPTOM,
        stopped=result.stopped,
        conditions=WIDE,
        experiments=result.log.experiments,
        exclusions=[Exclusion(experiment=item, conditions=WIDE) for item in rejected],
    )

    verdict = verdict_for_partial(assess_sufficiency(chain))

    assert budget.remaining(Phase.INVESTIGATE, FINDING) == 39
    assert verdict.verdict is Verdict.NEGATIVE_SOUND
    assert route(verdict, budget, FINDING).route is Route.REPORT


def test_the_same_run_under_the_conditions_it_actually_had_is_not_a_result() -> None:
    """**The control for AC 4, and it is the honest reading of the thesis
    fixture.** Its conditions are uniform-only and serial-only, so the exclusion
    that run bought does not hold as widely as it reads — and the audit says so
    rather than shipping a null result off the back of one narrow sweep.

    It still does not go back to investigate: it escalates.
    """
    result, budget = a_run_the_agent_would_have_continued()
    chain = result.partial_chain(SYMPTOM)

    audit = assess_sufficiency(chain)
    assert not audit.sufficient
    assert Insufficiency.EXCLUSIONS_TOO_NARROW in audit.shortfalls

    verdict = verdict_for_partial(audit)
    assert route(verdict, budget, FINDING).route is Route.ESCALATE


def test_the_experiments_audited_are_the_ones_the_run_actually_performed() -> None:
    """The join. An audit reading a hand-built chain would satisfy every criterion
    above while never seeing what a run produces — Epic 7's *the criterion is met*
    and *the criterion is reachable* are different claims."""
    result, _ = a_run_the_agent_would_have_continued()
    chain = result.partial_chain(SYMPTOM)

    assert chain.experiments
    assert all(isinstance(item, Experiment) for item in chain.experiments)
    assert assess_sufficiency(chain).exclusions
    assert str(SCALES[0]) in chain.exclusions[0].experiment.design

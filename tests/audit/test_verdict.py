"""S-9.8 — what six attacks add up to, and where the run goes next.

Two controls carry this file.

**An audit that never returns `sound` is a machine for rejecting findings**, and
it passes every test that only checks objections land. So the complete, clean
audit has to reach repair.

**An audit that never returns `inconclusive` reads silence as agreement** — the
S-3.1 failure this epic has now found in three separate places. So an attack that
did not run must not pass, *and* an attack that does not apply must not be
counted against the finding. Those two are one test apart and they pull in
opposite directions.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from coldfix.audit import verdict as verdict_module
from coldfix.audit.alternatives import Alternative, AlternativeAudit
from coldfix.audit.exclusions import ExclusionAudit, Narrowness
from coldfix.audit.fixtures import FixtureAudit, Hiding
from coldfix.audit.representativeness import Representativeness, RepresentativenessAudit
from coldfix.audit.reproducibility import Divergence, MetricComparison, ReproducibilityAudit
from coldfix.audit.scales import Inadequacy, ScaleAudit
from coldfix.audit.verdict import (
    ABOUT_A_FINDING,
    AUDIT_CALL_CEILING,
    SOFT_ATTACK,
    Attack,
    AttackResult,
    AuditVerdict,
    Outcome,
    Route,
    Subject,
    Verdict,
    VerdictError,
    authorize_round,
    calls_made,
    from_alternatives,
    from_exclusions,
    from_fixture,
    from_representativeness,
    from_reproducibility,
    from_scales,
    inapplicable,
    not_run,
    record_round,
    refuse_overspend,
    route,
    verdict_for,
)
from coldfix.cost.accounting import (
    Agent,
    ExchangeRate,
    Ledger,
    ModelCall,
    Phase,
    StepClass,
    TokenUsage,
)
from coldfix.cost.budget import (
    PHASE_CAPS,
    Budget,
    BudgetExhaustedError,
    Disposition,
    ProgressStalledError,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.diagnosis.log import Verdict as LogVerdict
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution

FINDING = "n.plus.one"
MODEL = "claude-opus-5"
RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))


def a_budget(*, stall_after: int = 3) -> Budget:
    return Budget(ledger=Ledger(), rate=RATE, stall_after=stall_after)


def an_experiment() -> Experiment:
    log = ExperimentLog()
    return log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000, 10000])",
        measurement={"db.query": 7.0},
        verdict=LogVerdict.REJECTED,
        outcome="queries flat at 7 across a 1000x sweep",
    )


def an_exclusion_audit(*, adequate: bool) -> ExclusionAudit:
    exclusion = Exclusion(
        experiment=an_experiment(),
        conditions=Conditions.of(
            fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
            platform="x86_64-linux",
            concurrency=[1, 8],
            scales=[10.0, 100.0, 1000.0, 10_000.0],
        ),
    )
    objections = () if adequate else (Narrowness.UNIFORM_ONLY,)
    return ExclusionAudit(exclusion=exclusion, objections=objections, scales=None)


def a_fixture_audit(*, adequate: bool) -> FixtureAudit:
    return FixtureAudit(
        shapes_tested=("uniform", "long_tail") if adequate else ("uniform",),
        could_hide=() if adequate else (Hiding.UNIFORM_MASKS_PER_PARENT,),
        request=None,
    )


def a_scale_audit(*, adequate: bool) -> ScaleAudit:
    return ScaleAudit(
        scales=(10.0, 100.0, 1000.0, 10_000.0) if adequate else (10.0, 20.0),
        span=1000.0 if adequate else 2.0,
        uncertainty=0.01 if adequate else 0.17,
        required=11.0,
        objections=() if adequate else (Inadequacy.SPAN_TOO_NARROW,),
    )


def an_alternative_audit(*, adequate: bool) -> AlternativeAudit:
    if adequate:
        return AlternativeAudit(alternative=None)
    return AlternativeAudit(
        alternative=Alternative(
            mechanism="a constant per-request overhead, not a per-row one",
            cites={"db.query": 7.0},
            not_excluded_because="no experiment varied rows while holding requests fixed",
        )
    )


def a_reproducibility_audit(*, adequate: bool) -> ReproducibilityAudit:
    divergence = Divergence.UNCHANGED if adequate else Divergence.COUNT_MOVED
    return ReproducibilityAudit(
        experiment=an_experiment(),
        comparisons=(
            MetricComparison(
                metric="db.query",
                kind=MetricKind.COUNT,
                recorded=7.0,
                rerun=7.0 if adequate else 8.0,
                divergence=divergence,
            ),
        ),
        relative_noise=0.12,
    )


def a_representativeness_audit(*, adequate: bool) -> RepresentativenessAudit:
    if adequate:
        return RepresentativenessAudit(
            verdict=Representativeness.REPRESENTATIVE,
            reason="",
            synthesized_fixture=False,
        )
    return RepresentativenessAudit(
        verdict=Representativeness.UNREPRESENTATIVE,
        reason="this is the admin healthcheck page, which no user of the shop opens",
        synthesized_fixture=False,
    )


def clean_sweep() -> list[AttackResult]:
    """Every attack ran and every one passed. The audit that must reach repair."""
    return [
        from_exclusions([an_exclusion_audit(adequate=True)]),
        from_fixture(a_fixture_audit(adequate=True)),
        from_scales(a_scale_audit(adequate=True)),
        from_alternatives(an_alternative_audit(adequate=True)),
        from_reproducibility(a_reproducibility_audit(adequate=True)),
        from_representativeness(a_representativeness_audit(adequate=True)),
    ]


def a_call() -> ModelCall:
    return ModelCall(
        phase=Phase.FINDING_AUDIT,
        agent=Agent.FINDING_AUDITOR,
        step_class=StepClass.CREATIVE,
        model=MODEL,
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )


# ============================================ the module makes no model calls


def test_the_verdict_is_arithmetic_and_calls_no_model() -> None:
    """`CLAUDE.md`: *do not add a model call where a function would do.*

    Six attacks have already answered; combining them is counting. This matters
    to state because Epic 9's vocabulary — *attacks*, *the Adversary* — reads as
    adversary calls from end to end, and it is now five of seven attacks plus the
    routing decision that turned out to be code.

    Asserted over what the module **imports and takes**, not over its source
    text. S-9.3 recorded that lesson after an isolation test asserted
    `"budget" not in source.lower()` against a docstring that discusses the
    budget at length: a substring check over source cannot tell an explanation
    from an action, and the docstring above is an explanation.
    """
    imported = set(vars(verdict_module))
    assert not imported & {"ModelClient", "Session", "invoke", "StepOutcome"}

    parameters = {
        name
        for _, function in inspect.getmembers(verdict_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"client", "session", "log", "question"}


# ================================================= AC 1: the verdict schema


def test_the_vocabulary_is_exactly_the_five_the_backlog_names() -> None:
    assert {item.name for item in Verdict} == {
        "SOUND",
        "UNSOUND",
        "UNREPRESENTATIVE",
        "NEGATIVE_SOUND",
        "INCONCLUSIVE",
    }


@pytest.mark.parametrize(
    ("verdict", "subject"),
    [
        (Verdict.UNSOUND, Subject.FINDING),
        (Verdict.UNREPRESENTATIVE, Subject.FINDING),
        (Verdict.INCONCLUSIVE, Subject.FINDING),
    ],
)
def test_a_verdict_with_a_payload_is_refused_without_one(
    verdict: Verdict, subject: Subject
) -> None:
    """AC 1 writes three verdicts with a payload — *+ objection*, *+ reason*,
    *+ what is missing* — and every one exists to tell somebody what to do next.
    An objection nobody can read is an escalation with no instruction in it."""
    with pytest.raises(VerdictError, match="nothing said"):
        AuditVerdict(verdict=verdict, subject=subject)


@pytest.mark.parametrize(
    ("verdict", "subject"),
    [
        (Verdict.SOUND, Subject.FINDING),
        (Verdict.NEGATIVE_SOUND, Subject.PARTIAL_CHAIN),
    ],
)
def test_a_verdict_with_no_payload_refuses_one(verdict: Verdict, subject: Subject) -> None:
    """The other half, and it is not symmetry for its own sake. A `sound` verdict
    carrying an objection says two things at once, and a reader would be entitled
    to believe either — the shape S-8.9 refused when it made `PartialChain` and
    `EvidenceChain` unable to impersonate each other."""
    with pytest.raises(VerdictError, match="carries an objection"):
        AuditVerdict(verdict=verdict, subject=subject, detail="something")


def test_negative_sound_cannot_be_said_about_a_finding() -> None:
    """*Nothing was found* is not a thing you can say about a found thing."""
    with pytest.raises(VerdictError, match="cannot be said about a finding"):
        AuditVerdict(verdict=Verdict.NEGATIVE_SOUND, subject=Subject.FINDING)


@pytest.mark.parametrize("verdict", ABOUT_A_FINDING)
def test_a_finding_verdict_cannot_be_said_about_a_partial_chain(verdict: Verdict) -> None:
    """A `PartialChain` confirms nothing by construction (S-8.9), so there is
    nothing in one to repair, to disprove, or to call unrepresentative."""
    detail = "x" if verdict in (Verdict.UNSOUND, Verdict.UNREPRESENTATIVE) else ""
    with pytest.raises(VerdictError, match="presupposes a claimed cause"):
        AuditVerdict(verdict=verdict, subject=Subject.PARTIAL_CHAIN, detail=detail)


def test_negative_sound_is_constructible_about_a_partial_chain() -> None:
    """The control for the two refusals above. ADR 094 added this verdict because
    `00-BRIEF.md` §9 ships a null result as output and nothing in Epic 9 could
    express one — a schema that refused it everywhere would have kept the gap.

    S-9.9 decides *when* it applies; this story owns the vocabulary and where it
    routes.
    """
    answer = AuditVerdict(verdict=Verdict.NEGATIVE_SOUND, subject=Subject.PARTIAL_CHAIN)
    assert answer.verdict is Verdict.NEGATIVE_SOUND


def test_inconclusive_is_legal_about_either_subject() -> None:
    """*This run stopped too early* is S-9.9's question about a partial chain and
    *an attack did not run* is this story's about a finding. One verdict, two
    subjects, and restricting it to one would leave S-9.9 without a way to say the
    thing it was added to say."""
    for subject in Subject:
        answer = AuditVerdict(
            verdict=Verdict.INCONCLUSIVE, subject=subject, detail="the sweep was never audited"
        )
        assert answer.subject is subject


def test_verdict_for_can_never_return_negative_sound() -> None:
    """Not by convention — by schema. This function audits a finding, and the
    model refuses `negative_sound` about one, so the guarantee holds for inputs
    nobody thought to test."""
    for adequate in (True, False):
        results = [
            from_exclusions([an_exclusion_audit(adequate=adequate)]),
            from_fixture(a_fixture_audit(adequate=adequate)),
            from_scales(a_scale_audit(adequate=adequate)),
            from_alternatives(an_alternative_audit(adequate=adequate)),
            from_reproducibility(a_reproducibility_audit(adequate=adequate)),
            from_representativeness(a_representativeness_audit(adequate=adequate)),
        ]
        assert verdict_for(results).verdict is not Verdict.NEGATIVE_SOUND
    assert verdict_for([not_run(item, "not run") for item in Attack]).verdict is not (
        Verdict.NEGATIVE_SOUND
    )


# =============================================== an attack result carries its text


@pytest.mark.parametrize("outcome", [Outcome.OBJECTED, Outcome.NOT_RUN])
def test_an_objection_or_a_gap_without_text_is_refused(outcome: Outcome) -> None:
    with pytest.raises(VerdictError, match="said nothing about it"):
        AttackResult(attack=Attack.SCALE_ADEQUACY, outcome=outcome, detail="   ")


@pytest.mark.parametrize("outcome", [Outcome.PASSED, Outcome.INAPPLICABLE])
def test_a_pass_needs_no_text(outcome: Outcome) -> None:
    """The control. Requiring text everywhere would make `PASSED` carry a
    sentence nobody wrote, and the sentence would end up being *passed*."""
    assert AttackResult(attack=Attack.SCALE_ADEQUACY, outcome=outcome).detail == ""


def test_an_inapplicable_attack_counts_as_answered_and_a_missing_one_does_not() -> None:
    assert inapplicable(Attack.SCALE_ADEQUACY, "no sweep behind this finding").answered
    assert not not_run(Attack.SCALE_ADEQUACY, "the fit was never gathered").answered


# ================================== the adapters: nothing hand-builds a result


def test_an_investigation_that_excluded_nothing_is_inapplicable_not_passed() -> None:
    """**The join Epic 8's composition check kept finding.** A reader told
    *exclusion validity passed* about zero exclusions has been told something
    false; S-9.2's own report says *nothing was ruled out, so there are no
    preconditions to attack*, and this is that sentence as a value."""
    result = from_exclusions([])
    assert result.outcome is Outcome.INAPPLICABLE


@pytest.mark.parametrize(
    ("adapter", "builder"),
    [
        (lambda audit: from_exclusions([audit]), an_exclusion_audit),
        (from_fixture, a_fixture_audit),
        (from_scales, a_scale_audit),
        (from_alternatives, an_alternative_audit),
        (from_reproducibility, a_reproducibility_audit),
        (from_representativeness, a_representativeness_audit),
    ],
)
def test_every_adapter_passes_a_clean_audit_and_objects_to_a_dirty_one(
    adapter: object, builder: object
) -> None:
    """Both directions for all six, because an adapter that always passed and one
    that always objected are each a single wrong line, and each defeats the whole
    verdict.

    They exist at all for the reason the Epic 8 composition check recorded: the
    conditions and the symptom had no producer, *every caller built them by hand
    including every test, which is precisely why nothing noticed*.
    """
    assert adapter(builder(adequate=True)).outcome is Outcome.PASSED  # type: ignore[operator]
    dirty = adapter(builder(adequate=False))  # type: ignore[operator]
    assert dirty.outcome is Outcome.OBJECTED
    assert dirty.detail.strip()


def test_no_alternative_reads_as_this_attack_passing() -> None:
    """S-9.5 spends its length making the empty answer sayable. Reading it as
    anything other than a pass at the join would undo that one module later."""
    assert from_alternatives(AlternativeAudit(alternative=None)).outcome is Outcome.PASSED


# ============================================= AC 1: what the six add up to


def test_a_complete_clean_audit_is_sound() -> None:
    """**The control that carries this file.** An audit that never returns
    `sound` passes every test that only checks objections land, and it would make
    the epic a machine for rejecting findings."""
    answer = verdict_for(clean_sweep())
    assert answer.verdict is Verdict.SOUND
    assert answer.detail == ""
    assert len(answer.results) == len(Attack)


@pytest.mark.parametrize(
    "spoiled",
    [
        Attack.EXCLUSION_VALIDITY,
        Attack.FIXTURE_ADEQUACY,
        Attack.SCALE_ADEQUACY,
        Attack.ALTERNATIVE_EXPLANATION,
        Attack.REPRODUCIBILITY,
    ],
)
def test_any_of_the_five_hard_attacks_objecting_makes_the_finding_unsound(
    spoiled: Attack,
) -> None:
    results = [
        AttackResult(attack=item.attack, outcome=Outcome.OBJECTED, detail=f"{item.attack} landed")
        if item.attack is spoiled
        else item
        for item in clean_sweep()
    ]
    answer = verdict_for(results)
    assert answer.verdict is Verdict.UNSOUND
    assert str(spoiled) in answer.detail


def test_representativeness_objecting_alone_is_unrepresentative_not_unsound() -> None:
    """The one attack whose objection does not mean the finding is wrong. The N+1
    is real; nobody runs it."""
    results = [item for item in clean_sweep() if item.attack is not Attack.REPRESENTATIVENESS]
    results.append(from_representativeness(a_representativeness_audit(adequate=False)))
    assert verdict_for(results).verdict is Verdict.UNREPRESENTATIVE


def test_unrepresentative_outranks_unsound_when_both_land() -> None:
    """**Precedence, and the reason is the routing rather than a preference.**
    Routing an unrepresentative finding back to investigate spends the experiment
    budget establishing a better answer about a workload nobody runs — ADR 094's
    hazard reached through the verdict rather than through the agent. No
    experiment can make a workload one that users exercise, so the objection that
    cannot be answered wins.
    """
    results = [
        from_exclusions([an_exclusion_audit(adequate=True)]),
        from_fixture(a_fixture_audit(adequate=True)),
        from_scales(a_scale_audit(adequate=True)),
        from_alternatives(an_alternative_audit(adequate=False)),
        from_reproducibility(a_reproducibility_audit(adequate=True)),
        from_representativeness(a_representativeness_audit(adequate=False)),
    ]
    assert verdict_for(results).verdict is Verdict.UNREPRESENTATIVE


def test_the_same_hard_objection_alone_is_unsound() -> None:
    """The control for the precedence test above. Without it, a `verdict_for`
    that returned `unrepresentative` for *everything* would pass — the eighth
    instance in this project of a fixture where the right answer and the wrong
    answer coincide, avoided by varying the one thing under test."""
    results = [item for item in clean_sweep() if item.attack is not Attack.ALTERNATIVE_EXPLANATION]
    results.append(from_alternatives(an_alternative_audit(adequate=False)))
    assert verdict_for(results).verdict is Verdict.UNSOUND


def test_soft_attack_is_the_only_one_of_its_kind() -> None:
    assert SOFT_ATTACK is Attack.REPRESENTATIVENESS


# ================================ AC 1: silence is not agreement


def test_an_attack_that_did_not_run_makes_the_audit_inconclusive() -> None:
    """**The S-3.1 distinction at the top of the vocabulary.** A four-verdict
    scheme has an audit that ran two of six attacks and objected to neither
    reporting `sound` — the same *no* versus *not known* confusion S-9.4 refused
    for a missing fit and S-9.6 for a metric that vanished."""
    results = [item for item in clean_sweep() if item.attack is not Attack.REPRODUCIBILITY]
    results.append(not_run(Attack.REPRODUCIBILITY, "no re-run callable was supplied"))
    answer = verdict_for(results)
    assert answer.verdict is Verdict.INCONCLUSIVE
    assert "no re-run callable was supplied" in answer.detail
    assert answer.unanswered[0].attack is Attack.REPRODUCIBILITY


def test_an_attack_nobody_reported_at_all_makes_the_audit_inconclusive() -> None:
    """The harder half: an attack that did not run at least produces a row. One
    that was never invoked produces nothing, and *nothing* is what a caller
    forgetting a step looks like. So the six are compared against, not counted."""
    results = [item for item in clean_sweep() if item.attack is not Attack.FIXTURE_ADEQUACY]
    answer = verdict_for(results)
    assert answer.verdict is Verdict.INCONCLUSIVE
    assert "fixture adequacy" in answer.detail


def test_an_attack_that_does_not_apply_is_not_a_gap() -> None:
    """**The control, and it pulls the opposite way from the two above.** Folding
    *inapplicable* into *not run* makes every ablation-based finding
    `inconclusive` because it had no sweep to audit — an audit that escalates
    every finding is as useless as one that passes every finding, and less
    obviously so."""
    results = [item for item in clean_sweep() if item.attack is not Attack.SCALE_ADEQUACY]
    results.append(inapplicable(Attack.SCALE_ADEQUACY, "this finding rests on an ablation"))
    assert verdict_for(results).verdict is Verdict.SOUND


def test_an_objection_outranks_a_gap() -> None:
    """An audit that landed a real objection has told the reader something
    actionable; reporting *the audit was incomplete* instead would bury it."""
    results = [
        item
        for item in clean_sweep()
        if item.attack not in (Attack.REPRODUCIBILITY, Attack.ALTERNATIVE_EXPLANATION)
    ]
    results.append(not_run(Attack.REPRODUCIBILITY, "no re-run callable was supplied"))
    results.append(from_alternatives(an_alternative_audit(adequate=False)))
    assert verdict_for(results).verdict is Verdict.UNSOUND


def test_an_objection_outranks_a_gap_for_the_soft_attack_too() -> None:
    """**The survivor of the sabotage pass, and it is the same property asserted
    for only one of the two objection kinds.**

    A representativeness objection alongside an attack that did not run is still
    `unrepresentative`: representativeness *ran*, and no missing attack can make
    a workload one that users exercise. Without this, the pair returns
    `inconclusive` — which routes to escalation instead of skipping, so a finding
    nobody runs collects a human's attention and a second audit round.
    """
    results = [
        item
        for item in clean_sweep()
        if item.attack not in (Attack.REPRODUCIBILITY, Attack.REPRESENTATIVENESS)
    ]
    results.append(not_run(Attack.REPRODUCIBILITY, "no re-run callable was supplied"))
    results.append(from_representativeness(a_representativeness_audit(adequate=False)))
    assert verdict_for(results).verdict is Verdict.UNREPRESENTATIVE


def test_an_attack_reported_twice_is_refused() -> None:
    """Two rows for one attack means a pass and an objection can both be present
    with nothing deciding which counts."""
    results = [*clean_sweep(), from_alternatives(an_alternative_audit(adequate=False))]
    with pytest.raises(VerdictError, match="more than once"):
        verdict_for(results)


def test_the_verdict_keeps_every_attack_result() -> None:
    """A reader deciding whether to overturn an `unrepresentative` needs to see
    that the other five passed, and an `inconclusive` is unactionable without the
    list of what did not run."""
    answer = verdict_for(clean_sweep())
    assert {item.attack for item in answer.results} == set(Attack)
    assert "exclusion validity" in answer.describe()


# ======================================================== AC 2: routing


def test_a_sound_finding_proceeds_to_repair() -> None:
    routing = route(verdict_for(clean_sweep()), a_budget(), FINDING)
    assert routing.route is Route.REPAIR
    assert routing.spends_repair
    assert routing.disposition is None


def test_an_unsound_finding_returns_to_investigate_while_it_has_budget() -> None:
    answer = AuditVerdict(
        verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="an alternative fits"
    )
    routing = route(answer, a_budget(), FINDING)
    assert routing.route is Route.INVESTIGATE
    assert not routing.spends_repair
    assert "40 experiments left" in routing.because


def test_an_unsound_finding_with_no_experiments_left_escalates() -> None:
    """**AC 2's condition, and the whole of ADR 094's amendment.** Against an
    agent that declined to stop 60 times out of 60, an audit whose only lever is
    *run more experiments* makes the one failure S-0.8 actually measured worse.
    The budget bounds the loop and a human sees the objection instead."""
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.INVESTIGATE].limit):
        budget.record_step(Phase.INVESTIGATE, FINDING, conclusion=f"step-{index}")

    answer = AuditVerdict(
        verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="an alternative fits"
    )
    routing = route(answer, budget, FINDING)
    assert routing.route is Route.ESCALATE
    assert routing.disposition is Disposition.ESCALATE
    assert "no experiments left" in routing.because


def test_the_budget_boundary_is_the_last_experiment_not_the_last_but_one() -> None:
    """One experiment left is a lever; none is not. An off-by-one here either
    escalates a finding that could still have been settled or lets the loop run
    one round past the cap S-8.9 exists to enforce."""
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.INVESTIGATE].limit - 1):
        budget.record_step(Phase.INVESTIGATE, FINDING, conclusion=f"step-{index}")
    answer = AuditVerdict(verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="objection")

    assert budget.remaining(Phase.INVESTIGATE, FINDING) == 1
    assert route(answer, budget, FINDING).route is Route.INVESTIGATE

    budget.record_step(Phase.INVESTIGATE, FINDING, conclusion="last")
    assert route(answer, budget, FINDING).route is Route.ESCALATE


def test_one_exhausted_finding_does_not_escalate_another() -> None:
    """The investigate cap is scoped per finding (S-5.4), and routing that read a
    run-wide counter would escalate every finding after the first one to spend its
    forty."""
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.INVESTIGATE].limit):
        budget.record_step(Phase.INVESTIGATE, "other-finding", conclusion=f"step-{index}")
    answer = AuditVerdict(verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="objection")
    assert route(answer, budget, FINDING).route is Route.INVESTIGATE


def test_routing_reads_the_budget_and_spends_none_of_it() -> None:
    """`remaining` is a question. Charging an experiment for the decision to run
    one would make the forty-experiment cap a thirty-something cap, which is
    S-8.9's finding in a new place."""
    budget = a_budget()
    answer = AuditVerdict(verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="objection")
    route(answer, budget, FINDING)
    assert budget.used(Phase.INVESTIGATE, FINDING) == 0
    assert budget.used(Phase.FINDING_AUDIT, FINDING) == 0


def test_an_unrepresentative_finding_skips_without_repair_spend() -> None:
    """AC 2 of S-9.7, enforced where the routing actually happens: this is the
    only verdict besides `sound` that ends a finding without more experiments, and
    it is the one that must never reach the Surgeon."""
    answer = AuditVerdict(
        verdict=Verdict.UNREPRESENTATIVE,
        subject=Subject.FINDING,
        detail="the admin healthcheck page",
    )
    routing = route(answer, a_budget(), FINDING)
    assert routing.route is Route.NEXT_FINDING
    assert not routing.spends_repair
    assert "overturn this" in routing.because


def test_an_unrepresentative_finding_never_returns_to_investigate() -> None:
    """Even with the full forty experiments available. More experiments cannot
    make a workload one that users exercise, and this is where that argument stops
    being prose."""
    answer = AuditVerdict(
        verdict=Verdict.UNREPRESENTATIVE, subject=Subject.FINDING, detail="admin page"
    )
    assert route(answer, a_budget(), FINDING).route is not Route.INVESTIGATE


def test_a_null_result_is_reported_rather_than_re_investigated() -> None:
    """`00-BRIEF.md` §9 ships *screened nine workloads, nothing found* as an
    answer. Returning a `negative_sound` to investigate would be asking for a
    finding the evidence says is not there."""
    answer = AuditVerdict(verdict=Verdict.NEGATIVE_SOUND, subject=Subject.PARTIAL_CHAIN)
    routing = route(answer, a_budget(), FINDING)
    assert routing.route is Route.REPORT
    assert not routing.spends_repair


def test_an_incomplete_audit_escalates_rather_than_asking_for_experiments() -> None:
    """What is missing is an attack, not a measurement. More experiments cannot
    complete an audit that did not run, and asking for them would add spend to
    close a gap somewhere else entirely."""
    results = [item for item in clean_sweep() if item.attack is not Attack.REPRODUCIBILITY]
    results.append(not_run(Attack.REPRODUCIBILITY, "no re-run callable was supplied"))
    routing = route(verdict_for(results), a_budget(), FINDING)
    assert routing.route is Route.ESCALATE
    assert routing.disposition is Disposition.ESCALATE


def test_the_two_escalations_say_why_they_are_different() -> None:
    """`ESCALATE` is reached from an unsound finding out of budget and from an
    incomplete audit, and the human's next action differs: answer the objection,
    or run the attack that never ran."""
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.INVESTIGATE].limit):
        budget.record_step(Phase.INVESTIGATE, FINDING, conclusion=f"step-{index}")
    unsound = route(
        AuditVerdict(verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="objection"),
        budget,
        FINDING,
    )
    incomplete = route(
        AuditVerdict(
            verdict=Verdict.INCONCLUSIVE,
            subject=Subject.FINDING,
            detail="the sweep was not audited",
        ),
        budget,
        FINDING,
    )
    assert unsound.route is incomplete.route
    assert unsound.because != incomplete.because
    assert "Why:" in unsound.describe()


def test_every_verdict_has_a_route() -> None:
    """A `route` missing a branch would fall through to the unsound arm and send a
    verdict nobody wrote a case for back to investigate."""
    budget = a_budget()
    for verdict in Verdict:
        subject = Subject.PARTIAL_CHAIN if verdict is Verdict.NEGATIVE_SOUND else Subject.FINDING
        detail = (
            "because"
            if verdict
            in (
                Verdict.UNSOUND,
                Verdict.UNREPRESENTATIVE,
                Verdict.INCONCLUSIVE,
            )
            else ""
        )
        routing = route(AuditVerdict(verdict=verdict, subject=subject, detail=detail), budget)
        assert isinstance(routing.route, Route)
        assert routing.because.strip()


# ==================================================== AC 3: cost of the audit


def test_the_ceiling_is_read_strictly_as_under_fifteen() -> None:
    assert AUDIT_CALL_CEILING == 15
    refuse_overspend([a_call()] * (AUDIT_CALL_CEILING - 1))
    with pytest.raises(VerdictError, match="ceiling of 15"):
        refuse_overspend([a_call()] * AUDIT_CALL_CEILING)


def test_the_audit_costs_two_model_calls_against_a_ceiling_of_fifteen() -> None:
    """**The measured figure, not the estimate.** `08-audit.md` §4 costs the
    finding audit at *~10 calls* against a ~50-call repair phase, and that
    estimate assumed six adversary invocations. Four of the six turned out to be
    arithmetic — S-9.2, S-9.3, S-9.4 and S-9.6 call no model at all — so a full
    audit makes two.

    The Epic 8 composition check's closing lesson: *a defect whose only symptom is
    a cost figure needs a test that reads the cost figure.*
    """
    model_calling = (Attack.ALTERNATIVE_EXPLANATION, Attack.REPRESENTATIVENESS)
    calls = [a_call() for _ in model_calling]
    assert calls_made(calls) == 2
    assert calls_made(calls) < AUDIT_CALL_CEILING
    refuse_overspend(calls)


def test_calls_are_counted_and_not_steps() -> None:
    """S-5.6's cascade makes up to three calls inside one step, so a count of
    steps reports a third of the bill — S-8.9's arithmetic in the other
    direction."""
    cascaded = [a_call(), a_call(), a_call()]
    assert calls_made(cascaded) == 3


# ============================== the two-round cap, dead since S-5.4


def test_nothing_counted_audit_rounds_before_this_story() -> None:
    """`Phase.FINDING_AUDIT`'s cap counts *rounds*, and S-8.9 made `Session.run`
    record a step only where a phase's cap counts steps — correctly, since a round
    is six attacks and a call is not a round. Nothing else counted, so the counter
    stayed at zero and `authorize` compared zero against two on every call.

    Whoever owns the unit counts the unit.
    """
    budget = a_budget()
    assert budget.used(Phase.FINDING_AUDIT, FINDING) == 0
    record_round(budget, verdict_for(clean_sweep()), FINDING)
    assert budget.used(Phase.FINDING_AUDIT, FINDING) == 1


def test_a_third_round_is_refused_before_it_spends_anything() -> None:
    """Authorized before the round rather than after, for S-5.4's reason: a check
    after the work reports a breach instead of preventing one.

    It cannot be left to `Session.run`, which only authorizes if a round makes a
    model call — and four of the six attacks are arithmetic, so a round objecting
    on those alone would slip past a cap enforced at the API boundary.
    """
    budget = a_budget()
    answer = verdict_for(clean_sweep())
    for _ in range(PHASE_CAPS[Phase.FINDING_AUDIT].limit):
        authorize_round(budget, FINDING)
        record_round(budget, answer, FINDING)

    with pytest.raises(BudgetExhaustedError) as raised:
        authorize_round(budget, FINDING)
    assert raised.value.exhaustion.disposition is Disposition.ESCALATE


def test_audit_rounds_are_counted_per_finding() -> None:
    budget = a_budget()
    answer = verdict_for(clean_sweep())
    for _ in range(PHASE_CAPS[Phase.FINDING_AUDIT].limit):
        record_round(budget, answer, "first-finding")
    authorize_round(budget, "second-finding")
    assert budget.remaining(Phase.FINDING_AUDIT, "second-finding") == 2


def test_the_verdict_is_recorded_as_the_stall_conclusion() -> None:
    """With the default `stall_after` of 3 against a cap of 2 the cap always fires
    first — but `Budget` accepts 2, at which point two audits reaching the same
    verdict is a stall. Passing `None` would clear the run of repeats rather than
    extend it, which is the quiet way to make a stall check unreachable."""
    budget = a_budget(stall_after=2)
    answer = verdict_for(clean_sweep())
    record_round(budget, answer, FINDING)
    with pytest.raises(ProgressStalledError):
        record_round(budget, answer, FINDING)


def test_two_different_verdicts_in_a_row_do_not_stall() -> None:
    """The control. A conclusion that never differs makes the stall check
    unreachable in one direction; a conclusion that never repeats makes it
    unreachable in the other, which is S-8.8's recorded finding."""
    budget = a_budget(stall_after=2)
    record_round(budget, verdict_for(clean_sweep()), FINDING)
    record_round(
        budget,
        AuditVerdict(verdict=Verdict.UNSOUND, subject=Subject.FINDING, detail="objection"),
        FINDING,
    )
    assert budget.used(Phase.FINDING_AUDIT, FINDING) == 2

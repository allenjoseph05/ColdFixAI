"""Epic 9 composed: a diagnosis in, a routed verdict out.

Nine stories, six attacks, a verdict vocabulary and a routing rule — and after
all of them **nothing could take an investigation and audit it**. Every attack
was reachable from inputs a test built by hand; not one was reachable from what
the system actually produces. Epic 7 recorded that shape, Epic 8 repeated it, and
this is the third time: *the criterion is met* and *the criterion is reachable*
are different claims, and only a composition tests the second.

The defects, and every one is a join again:

1. **There was no path.** Six attacks with six different input shapes and nothing
   that assembles them, counts the audit's round, or checks its call ceiling.
2. **The log cannot say which metrics are counts.** `Executor` returns a bare
   `Mapping[str, float]`, so the `kinds` mapping every Epic 3 result carries is
   discarded at the loop boundary — and S-9.6 needs it. **The obvious repair is
   worse than the gap**: `metric_kind` is a pure function of spelling that
   defaults to `COUNT`, the thesis ablation reports `seconds.share_removed`, and
   a share of a duration read as a count diverges on every re-run — which is
   `unsound` every time and the infinite loop ADR 094 exists to prevent.
3. **A growth fit does not survive into the log either**, so S-9.4 and S-9.2's
   scale axis had no reachable input.

Measurements here are real; model calls are replayed. Same split as S-8.7.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from coldfix.audit import alternatives, representativeness
from coldfix.audit import invocation as invocation_module
from coldfix.audit import verdict as verdict_module
from coldfix.audit.compose import (
    NO_FIT,
    NO_KINDS,
    NO_RERUN,
    NO_SWEEP,
    _fit_for,
    audit_cost,
    audit_finding,
    audit_partial,
    audit_scales_result,
    audit_session,
    fits_from,
    key_experiment,
    reproducibility_result,
)
from coldfix.audit.invocation import AuditError
from coldfix.audit.reproducibility import Rerun
from coldfix.audit.verdict import (
    AUDIT_CALL_CEILING,
    Attack,
    Outcome,
    Route,
    Verdict,
    VerdictError,
)
from coldfix.bench.stats import CONSTANT_BELOW, SUPERLINEAR_ABOVE, Fit, Growth
from coldfix.cost.accounting import Phase
from coldfix.cost.budget import PHASE_CAPS, BudgetExhaustedError
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.diagnosis.log import Verdict as LogVerdict
from coldfix.diagnosis.loop import Investigation, LoopError, Measured, run_investigation
from coldfix.diagnosis.progress import PartialChain, Stopped, partial_chain
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.orchestrator.adapters import _log_of, _stored
from coldfix.primitives.measurement import SECONDS, MetricKind, metric_kind
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import FixtureRecipe, Observation, Workload
from coldfix.state.checkpoint import CheckpointedState
from fixtures.thesis import (  # the subject and its harness, not a second copy
    CONDITIONS,
    QUERIES,
    SCALES,
    Subject,
    _query_counter,  # noqa: F401 - registers the `query_counter` fixture
    a_session,
    ablate_renderer,
    an_investigation,
    payload,
    sweep_queries,
)
from fixtures.thesis import (
    _thesis_recordings as thesis_recordings,
)

FINDING = "n.plus.one"

WIDENED = Conditions.of(
    fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
    platform="x86_64-linux",
    concurrency=[1, 8],
    scales=[float(scale) for scale in SCALES],
)
"""The conditions a reseed leaves behind: S-8.8 moves them on the investigation,
and `conditions_for(workload)` can never report them because a `FixtureRecipe`
holds one distribution."""

WELL_SWEPT_SCALES = (10, 100, 1000, 10_000)
"""Four points spanning 1000x. S-9.4's threshold is **11x at 12% drift**, derived
rather than chosen, and the thesis fixture's 10/20/40 is 4x — which cannot
separate linear from superlinear at all. A control that could not reach `sound`
would prove nothing about the audit."""

WELL_SWEPT = Conditions.of(
    fixture_shape=[Distribution.UNIFORM.value, Distribution.LONG_TAIL.value],
    platform="x86_64-linux",
    concurrency=[1, 8],
    scales=[float(scale) for scale in WELL_SWEPT_SCALES],
)


def a_workload() -> Workload:
    """What Epic 8 hands Epic 9, built from the same planted subject."""
    return Workload(
        id="shop-books-list",
        description="the book list endpoint, rendering every row",
        entry_point="shop/views.py::ListView.list_books",
        fixture=FixtureRecipe(
            entity="book",
            per_parent=2,
            parents=SCALES[-1],
            distribution=Distribution.UNIFORM,
            source="synthesis from schema",
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        observations=tuple(
            Observation(scale=scale, metrics={"db.query": 2.0, "seconds": 0.4 * scale})
            for scale in SCALES
        ),
    )


def a_well_swept_workload() -> Workload:
    """The same workload, screened across a span S-9.4 can resolve.

    Identical in every field the audit puts to a model — id, description, entry
    point and fixture — so the recorded replies still match. Only the
    observations differ, which is the one thing the arithmetic attacks read.
    """
    workload = a_workload()
    return workload.model_copy(
        update={
            "observations": tuple(
                Observation(scale=scale, metrics={"db.query": 2.0, "seconds": 0.4 * scale})
                for scale in WELL_SWEPT_SCALES
            )
        }
    )


def run_the_diagnosis(subject: Subject) -> Investigation:
    """Epic 8's whole loop, driven the way a caller would. Ends confirmed."""

    def execute(spec: ExperimentSpec) -> Measured:
        if spec.primitive == "scaling.volume":
            return sweep_queries(subject)
        return ablate_renderer(subject)[0]

    investigation = an_investigation(ReplayingClient([]), execute)
    client = thesis_recordings(investigation, subject)
    return run_investigation(
        investigation.sessions,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=execute,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )


def an_audit_session() -> Session:
    """The auditor's own, through the composed path rather than by hand."""
    return audit_session(source="shop/views.py::ListView.list_books", rate=a_session().rate)


def audit_recorded(session: Session, *, question: str, reply: str) -> Recording:
    """One recording for an audit call, keyed the way `invoke` will ask.

    The model is taken from the router rather than written down, because S-5.5
    decides it and a hardcoded id here would pass while the routing changed
    underneath.
    """
    model = session.router.route(StepType.ATTACK_DESIGN, Phase.FINDING_AUDIT)
    return Recording.of(
        model=model,
        system=invocation_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=invocation_module.MAX_OUTPUT_TOKENS,
        temperature=invocation_module.AUDIT_TEMPERATURE,
        response=payload(reply, model=model),
    )


def audit_client_recordings(
    session: Session, log: ExperimentLog, workload: Workload
) -> list[Recording]:
    """The two replies a full audit needs: S-9.5's and S-9.7's.

    Two, because four of the six attacks are arithmetic. Built against the **real
    log**, which is what makes this a composition rather than another unit test.
    """
    evidence = invocation_module.render_evidence(log)
    representativeness_question = (
        f"WORKLOAD\n  {workload.id}: {workload.description}\n"
        f"  entry point: {workload.entry_point}\n"
        f"  fixture: {workload.fixture.entity} from {workload.fixture.source}\n\n"
        f"{representativeness.QUESTION}"
    )
    return [
        audit_recorded(
            session,
            question=f"{evidence}\n\n{alternatives.QUESTION}",
            reply=json.dumps({"mechanism": "none"}),
        ),
        audit_recorded(
            session,
            question=f"{evidence}\n\n{representativeness_question}",
            reply=json.dumps({"representative": True, "reason": ""}),
        ),
    ]


def audit_client(session: Session, log: ExperimentLog, workload: Workload) -> ReplayingClient:
    return ReplayingClient(audit_client_recordings(session, log, workload))


def a_fit() -> Fit:
    return Fit(
        slope=0.0,
        intercept=2.0,
        linear_r_squared=0.99,
        exponent=0.01,
        power_r_squared=0.98,
        growth=Growth.CONSTANT,
        constant_below=CONSTANT_BELOW,
        superlinear_above=SUPERLINEAR_ABOVE,
    )


def a_rerun(measurement: Mapping[str, float]) -> Rerun:
    def rerun(experiment: Experiment) -> Mapping[str, float]:
        return measurement

    return rerun


# ================= defect 1: the epic could not perform its own sentence


def test_a_confirmed_diagnosis_can_now_be_audited_end_to_end(query_counter: None) -> None:
    """**The missing path.** Six attacks, six input shapes, and nothing that
    assembled them, counted the audit's round or checked its call ceiling.

    Everything measured here comes from a real investigation of the planted
    defect; only the two auditor replies are replayed.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    routing, calls = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=CONDITIONS,
        log=investigation.log,
        metric=QUERIES,
        exclusions=investigation.exclusions.exclusions,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert {item.attack for item in routing.verdict.results} == set(Attack)
    assert routing.route in set(Route)
    assert calls


def test_the_audit_costs_two_model_calls_through_the_real_path(query_counter: None) -> None:
    """AC 3's figure, **measured rather than estimated**. `08-audit.md` §4 costed
    the finding audit at ~10 calls assuming six adversary invocations; four of the
    six turned out to be arithmetic.

    The Epic 8 composition's closing lesson was that *a defect whose only symptom
    is a cost figure needs a test that reads the cost figure*.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    _, calls = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=CONDITIONS,
        log=investigation.log,
        metric=QUERIES,
        exclusions=investigation.exclusions.exclusions,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert audit_cost(calls) == 2
    assert audit_cost(calls) < AUDIT_CALL_CEILING


def test_the_audit_round_is_counted_only_when_an_audit_actually_runs(
    query_counter: None,
) -> None:
    """S-9.8 built `authorize_round`/`record_round` and **nothing called them**,
    which is how the two-round cap stayed decorative from S-5.4 to here."""
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    assert session.budget.used(Phase.FINDING_AUDIT, FINDING) == 0
    audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=CONDITIONS,
        log=investigation.log,
        metric=QUERIES,
        exclusions=investigation.exclusions.exclusions,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )
    assert session.budget.used(Phase.FINDING_AUDIT, FINDING) == 1


def test_a_third_audit_round_is_refused_through_the_composed_path(
    query_counter: None,
) -> None:
    """The cap fires before any spend, so an audit that could not be authorized
    makes no model call at all."""
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    for _ in range(PHASE_CAPS[Phase.FINDING_AUDIT].limit):
        audit_finding(
            session,
            audit_client(session, investigation.log, workload),
            workload=workload,
            conditions=CONDITIONS,
            log=investigation.log,
            metric=QUERIES,
            exclusions=investigation.exclusions.exclusions,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )

    with pytest.raises(BudgetExhaustedError):
        audit_finding(
            session,
            ReplayingClient([]),
            workload=workload,
            conditions=CONDITIONS,
            log=investigation.log,
            metric=QUERIES,
            exclusions=investigation.exclusions.exclusions,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )


def test_the_diagnosticians_session_is_refused_by_the_composed_audit(
    query_counter: None,
) -> None:
    """S-9.1 wrote `refuse_shared_session` and **only fires if somebody calls it**.
    Until this path existed, nothing did — so the isolation the whole epic rests
    on was enforced by a function with no caller."""
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()

    with pytest.raises(AuditError, match="not the auditor's"):
        audit_finding(
            # A real session that is not the auditor's — the Diagnostician's own.
            investigation.hypothesis_session,
            ReplayingClient([]),
            workload=workload,
            conditions=CONDITIONS,
            log=investigation.log,
            metric=QUERIES,
            exclusions=investigation.exclusions.exclusions,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )


# ======== defect 2: the log cannot say which metrics are counts


def test_the_experiment_log_records_metric_kinds() -> None:
    """**The gap, closed. S-8.12.**

    This test used to assert the opposite, and its previous docstring was the
    specification for the story that inverted it: *`Executor` returns a bare
    mapping of numbers, so everything the primitive knew about them — which Epic
    3 records as `kinds` on every result — is dropped at the loop boundary.* The
    boundary now carries them.
    """
    assert "kinds" in Experiment.model_fields


def test_deriving_kinds_from_the_metric_name_misclassifies_a_real_measurement() -> None:
    """**Why the obvious repair is worse than the gap.**

    `metric_kind` is a pure function of spelling and defaults to `COUNT`. The
    thesis ablation reports `seconds.share_removed` — a *share of a duration* —
    and it reads as a count. S-9.6 calls any count that moved material, so a
    re-run would diverge every time, every finding would be `unsound`, and the
    amended S-9.8 would route every investigation back for more experiments for
    ever. That is the failure S-9.6's control test exists to prevent, reached
    through the join instead of through the module.
    """
    assert metric_kind("seconds.share_removed") is MetricKind.COUNT
    assert metric_kind("seconds") is MetricKind.DURATION


def test_a_missing_kinds_mapping_is_not_run_rather_than_a_guess() -> None:
    result = reproducibility_result(_an_experiment(), a_rerun({"db.query": 7.0}), None)
    assert result.outcome is Outcome.NOT_RUN
    assert result.detail == NO_KINDS


def test_a_missing_rerun_is_not_run_and_says_so() -> None:
    result = reproducibility_result(_an_experiment(), None, {"db.query": MetricKind.COUNT})
    assert result.outcome is Outcome.NOT_RUN
    assert result.detail == NO_RERUN


def test_supplying_kinds_and_a_rerun_makes_the_attack_actually_run() -> None:
    """The control. Without it every one of the tests above passes against a
    function that can only ever answer `NOT_RUN`."""
    experiment = _an_experiment()
    result = reproducibility_result(
        experiment,
        a_rerun(dict(experiment.measurement)),
        dict.fromkeys(experiment.measurement, MetricKind.COUNT),
    )
    assert result.outcome is Outcome.PASSED


# ============ defect 3: the fit does not survive into the log either


def test_a_growth_fit_survives_into_the_experiment_log(query_counter: None) -> None:
    """**The other half of the gap, closed. S-8.12.**

    Its previous docstring was the specification too: *`measurement` is
    `Mapping[str, float]` and a `Fit` is not a float, so the curve S-3.2 fitted
    is gone by the time an auditor reads the log.* It travels beside the
    measurement now rather than inside it — the numbers are still numbers, which
    is what keeps them comparable between experiments.

    **The ablation still fits nothing, and that is the point of the pair.** A
    sweep records its curves; a primitive that drew none records an empty
    mapping, and S-9.2 refuses to judge a curve nobody drew.

    **S-17.12 made it a curve per metric.** A sweep fits every metric it
    measured, and the audit picks the one the finding's claim rests on — so what
    travels is the whole mapping rather than the one fit an earlier version had
    to choose between carrying wrongly and not carrying at all.
    """
    investigation = run_the_diagnosis(Subject())
    for experiment in investigation.log.experiments:
        assert all(isinstance(value, float) for value in experiment.measurement.values())
    assert "fits" in Experiment.model_fields

    by_primitive = {item.primitive: item for item in investigation.log.experiments}
    assert by_primitive["scaling.volume"].fits, "the sweep fitted a curve"
    assert by_primitive["ablation.stub"].fits == {}, "and the ablation drew none"


def test_an_unfitted_sweep_is_not_run_and_a_finding_with_no_sweep_is_inapplicable() -> None:
    """**The pair that matters, and folding them breaks the epic in opposite
    directions.** A sweep nobody fitted is a gap; a finding resting on an ablation
    has no sweep at all, and calling that a gap makes every such finding
    `inconclusive` — an audit that escalates everything."""
    unfitted = audit_scales_result([10.0, 100.0, 1000.0, 10_000.0], None)
    assert unfitted.outcome is Outcome.NOT_RUN
    assert unfitted.detail == NO_FIT

    no_sweep = audit_scales_result([100.0], None)
    assert no_sweep.outcome is Outcome.INAPPLICABLE
    assert no_sweep.detail == NO_SWEEP

    # Four distinct points, because S-9.4 needs one more than the instrument
    # does — *an audit whose bar equals the instrument's bar is not auditing
    # anything* — and three would object for a reason this test is not about.
    assert audit_scales_result([10.0, 100.0, 1000.0, 10_000.0], a_fit()).outcome is Outcome.PASSED


def test_the_sweep_bar_here_is_not_s_9_4s_bar() -> None:
    """`A_SWEEP_NEEDS` is two, deliberately not S-9.4's four. Borrowing that
    number would turn three real scale points into *no growth claim was made*,
    which is a different sentence and a false one — the quality of a sweep that
    exists is S-9.4's to grade, not this module's to pre-empt."""
    three_points = audit_scales_result([10.0, 100.0, 1000.0], a_fit())
    assert three_points.outcome is Outcome.OBJECTED
    assert "points" in three_points.detail.lower()


# ================== what the composed audit actually concludes


def test_the_thesis_diagnosis_does_not_survive_its_own_audit(query_counter: None) -> None:
    """**The composition's headline result, and it is not a defect.**

    The thesis run's finding is real — stubbing the renderer removes essentially
    all the wall time — but its exclusion *not the database* was established
    under a uniform fixture driven serially, and S-9.2 proves uniform is the
    blindest shape there is. So the epic's own demonstration audits `unsound`,
    and routes back to investigate with all forty experiments intact.

    Worth asserting rather than working around: the audit objecting to the run
    the project uses as its showcase is the first evidence that it objects to
    anything real.

    **S-8.12 added a third objector without changing the verdict.** The scale
    attack used to answer `NOT_RUN` here, because the log could not carry the
    `Fit` the sweep produced and nothing supplied one by hand. It runs now, and
    it objects: the thesis sweep is deliberately small, and a span that narrow
    cannot separate linear growth from superlinear. An attack that had been
    silently absent from this result is the exact thing that story exists to fix.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    routing, _ = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=CONDITIONS,
        log=investigation.log,
        metric=QUERIES,
        exclusions=investigation.exclusions.exclusions,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert routing.verdict.verdict is Verdict.UNSOUND
    assert routing.route is Route.INVESTIGATE
    objected = {item.attack for item in routing.verdict.results if item.objected}
    assert objected == {
        Attack.EXCLUSION_VALIDITY,
        Attack.FIXTURE_ADEQUACY,
        Attack.SCALE_ADEQUACY,
    }


def test_the_audit_reads_the_conditions_in_force_not_the_original_recipe(
    query_counter: None,
) -> None:
    """**Defect 4.** The obvious implementation derives the conditions with
    `emit.conditions_for(workload)`, and a `FixtureRecipe` holds **one**
    distribution — the one the run started with. S-8.8 moves the conditions on
    the `Investigation` when it reseeds.

    So an audit rebuilding them from the recipe reports a single fixture shape
    after a reseed swept two: it objects to a narrowness already fixed, and the
    remedy it names is the reseed that just happened. Here the same run, the same
    log and the same workload audit clean once the conditions say what was
    actually swept.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_well_swept_workload()
    session = an_audit_session()

    routing, _ = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=WELL_SWEPT,
        log=investigation.log,
        metric=QUERIES,
        exclusions=[
            Exclusion(experiment=item.experiment, conditions=WELL_SWEPT)
            for item in investigation.exclusions.exclusions
        ],
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert not [item for item in routing.verdict.results if item.objected]
    assert workload.fixture.distribution is Distribution.UNIFORM


def test_an_audit_missing_the_one_input_the_log_cannot_carry_is_inconclusive(
    query_counter: None,
) -> None:
    """**The honest answer when an attack cannot run, and S-8.12 left exactly one.**

    This test used to be about *two* missing attacks: without a fit and without
    kinds, neither the scale attack nor the reproducibility check ran, so `sound`
    would have meant *nothing objected among the ones we tried*. The log carries
    both now.

    What it cannot carry is a **re-run** — re-running an experiment is the
    harness's and not the record's — so that is the one input still supplied, and
    withholding it is the way this state is now reached. `inconclusive` still
    says what is missing rather than passing what was never asked.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_well_swept_workload()
    session = an_audit_session()

    routing, _ = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=WELL_SWEPT,
        log=investigation.log,
        metric=QUERIES,
        exclusions=[
            Exclusion(experiment=item.experiment, conditions=WELL_SWEPT)
            for item in investigation.exclusions.exclusions
        ],
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert routing.verdict.verdict is Verdict.INCONCLUSIVE
    assert routing.route is Route.ESCALATE
    unanswered = {item.attack for item in routing.verdict.unanswered}
    assert unanswered == {Attack.REPRODUCIBILITY}, "the scale attack runs off the log now"


def test_the_thesis_sweep_is_too_narrow_to_support_a_growth_claim(
    query_counter: None,
) -> None:
    """**A second honest result, and S-9.4's derived threshold earning itself.**

    The thesis fixture sweeps 10, 20, 40 — a 4x span across three points. S-9.4
    requires 11x at the 12% drift S-0.4 measured, because at 4x the exponent is
    determined to far worse than the 0.15 gap between the growth classes. So the
    moment a fit is supplied, scale adequacy objects.

    That is the instrument working on the project's own demo, not a defect: the
    thesis run exists to show an instrument *switch*, and it never needed to
    separate linear from superlinear.

    **Two attacks object, and that is the delegation S-9.2 designed showing up
    through the composed path.** That story sends the scale axis to S-9.4 rather
    than asking the same question twice, so a narrow span makes the exclusion
    narrow as well — one judgement, reported in both places it bears on.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()

    routing, _ = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=WIDENED,
        log=investigation.log,
        metric=QUERIES,
        exclusions=[
            Exclusion(experiment=item.experiment, conditions=WIDENED)
            for item in investigation.exclusions.exclusions
        ],
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    objected = {item.attack for item in routing.verdict.results if item.objected}
    assert objected == {Attack.SCALE_ADEQUACY, Attack.EXCLUSION_VALIDITY}


def test_a_fully_supplied_audit_over_a_proper_sweep_reaches_sound(
    query_counter: None,
) -> None:
    """**The control that keeps the composed path from being a machine for
    escalating**, and the proof that `sound` is reachable at all.

    Everything an attack needs, supplied: conditions that say what was swept, a
    sweep wide enough for S-9.4's threshold, a fit, the metric kinds and a way to
    re-run. Six attacks answer, none objects, and the finding proceeds to repair.

    Without this test every assertion above is satisfied by an audit that objects
    to everything, which would make `00-BRIEF.md` §9's shippable findings
    unreachable and the whole epic a machine for rejecting sound work.
    """
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_well_swept_workload()
    session = an_audit_session()
    key = key_experiment(investigation.log)
    assert key is not None

    routing, _ = audit_finding(
        session,
        audit_client(session, investigation.log, workload),
        workload=workload,
        conditions=WELL_SWEPT,
        log=investigation.log,
        metric=QUERIES,
        exclusions=[
            Exclusion(experiment=item.experiment, conditions=WELL_SWEPT)
            for item in investigation.exclusions.exclusions
        ],
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        rerun=a_rerun(dict(key.measurement)),
        finding_id=FINDING,
    )

    assert routing.verdict.verdict is Verdict.SOUND
    assert routing.route is Route.REPAIR
    assert routing.verdict.unanswered == ()


def test_the_experiment_nominated_for_re_running_is_the_one_the_finding_rests_on() -> None:
    """A confirmation if there is one: that is the measurement the chain hangs
    from, and the one whose failure to reproduce destroys the finding.

    **The log here ends on a rejection deliberately.** The thesis run confirms on
    its last step, so *the last confirmation* and *the last settled experiment*
    are the same record and a sabotage that dropped the preference changed
    nothing — the tenth time in this project that a fixture could not tell the
    right answer from the wrong one.
    """
    log = ExperimentLog()
    log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 20, 40])",
        measurement={"db.query": 2.0},
        verdict=LogVerdict.REJECTED,
        outcome="queries flat at 2",
    )
    confirmation = log.append(
        hypothesis="the renderer dominates",
        primitive="ablation.stub",
        rationale="queries are flat, so it is not the database",
        target="ExpensiveRenderer.render",
        design="ablation.stub(attribute='render')",
        measurement={"seconds.share_removed": 0.98},
        verdict=LogVerdict.CONFIRMED,
        outcome="stubbing the renderer removes 98% of the time",
    )
    log.append(
        hypothesis="the template engine also contributes",
        primitive="ablation.stub",
        rationale="something may remain after the renderer",
        target="Template.render",
        design="ablation.stub(attribute='render')",
        measurement={"seconds.share_removed": 0.01},
        verdict=LogVerdict.REJECTED,
        outcome="stubbing the template removes nothing",
    )

    assert key_experiment(log) == confirmation


def test_the_composed_audit_refuses_to_spend_past_its_call_ceiling(
    query_counter: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 3's ceiling, made reachable. A full audit makes **two** calls against a
    ceiling of fifteen, so the guard cannot fire on the real design — and a guard
    that cannot fire is decoration rather than enforcement.

    Lowering the ceiling is the honest way to prove the path checks it.

    **The client is given only the first attack's recording**, which is what makes
    this discriminating: the check has to land *between* the two attacks, so the
    second is never billed. Hand it both recordings and a path that only checked
    the ceiling at the end would pass this test having spent the call the ceiling
    was there to prevent.
    """
    monkeypatch.setattr(verdict_module, "AUDIT_CALL_CEILING", 1)
    subject = Subject()
    investigation = run_the_diagnosis(subject)
    workload = a_workload()
    session = an_audit_session()
    only_the_first = ReplayingClient(
        [audit_client_recordings(session, investigation.log, workload)[0]]
    )

    with pytest.raises(VerdictError, match="ceiling of 1"):
        audit_finding(
            session,
            only_the_first,
            workload=workload,
            conditions=CONDITIONS,
            log=investigation.log,
            metric=QUERIES,
            exclusions=investigation.exclusions.exclusions,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )


def test_a_third_partial_chain_audit_is_refused_though_it_spends_nothing() -> None:
    """**Why `authorize_round` exists, and the only path that needs it.**

    `audit_finding` does not authorize — `Session.run` does it first against the
    same cap — but this path makes no model call at all, so nothing else would
    ever refuse it. Without the check here the two-round cap is decorative again,
    on the one path that has nothing else watching it.
    """
    session = an_audit_session()
    chain = a_partial_chain()

    for _ in range(PHASE_CAPS[Phase.FINDING_AUDIT].limit):
        audit_partial(chain, session, finding_id=FINDING)

    with pytest.raises(BudgetExhaustedError):
        audit_partial(chain, session, finding_id=FINDING)


def test_a_log_that_only_narrowed_nominates_nothing_to_re_run() -> None:
    """The control, and it is not a degenerate case: a run that only narrowed has
    no settled number whose reproduction would mean anything."""
    log = ExperimentLog()
    log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        rationale="queries are flat",
        target="BookSerializer",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds.share_removed": 0.4},
        verdict=LogVerdict.NARROWED,
        outcome="some of the cost, not all",
    )
    assert key_experiment(log) is None
    assert reproducibility_result(key_experiment(log), None, None).outcome is Outcome.NOT_RUN


# ============== S-9.9 through the same accounting


def test_a_run_that_found_nothing_routes_through_the_same_round_accounting() -> None:
    """The partial-chain path, which spends no model call at all — S-9.9's
    decision that the stopping question is the harness's."""
    session = an_audit_session()
    routing = audit_partial(a_partial_chain(), session, finding_id=FINDING)

    assert session.budget.used(Phase.FINDING_AUDIT, FINDING) == 1
    # `CONDITIONS` is the thesis fixture: uniform-only and serial. The one
    # exclusion this run bought does not hold as widely as it reads, so the
    # negative is not yet a result — and S-9.9 escalates rather than asking for
    # experiments, which is the lever ADR 094 says an audit must not reach for.
    assert routing.route is Route.ESCALATE
    assert routing.verdict.verdict is Verdict.INCONCLUSIVE


# ---------------------------------------------------------------- helpers


def a_partial_chain() -> PartialChain:
    """A run that ran out of instruments having ruled one thing out."""
    log = ExperimentLog()
    experiment = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 20, 40])",
        measurement={"db.query": 2.0},
        verdict=LogVerdict.REJECTED,
        outcome="queries flat at 2 across a 4x sweep",
    )
    return partial_chain(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=40),
        stopped=Stopped.INSTRUMENTS,
        conditions=CONDITIONS,
        experiments=log.experiments,
        exclusions=[_exclusion(experiment)],
    )


def _an_experiment() -> Experiment:
    log = ExperimentLog()
    return log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=LogVerdict.REJECTED,
        outcome="queries flat at 7",
    )


def _exclusion(experiment: Experiment) -> Exclusion:
    return Exclusion(experiment=experiment, conditions=CONDITIONS)


# ==================================================== S-8.12 — what the boundary carries


def test_the_loop_carries_what_the_primitive_knew_and_computes_none_of_it() -> None:
    """**AC 4.** What widened is what the boundary *carries*, not what the loop
    works out. A loop that could produce a fit would be the one place
    `CLAUDE.md`'s rule about measurement is unenforceable — so the module holds no
    way to make one, asserted by inspection rather than by hoping."""
    source = Path(inspect.getfile(run_investigation)).read_text(encoding="utf-8")

    assert "fit_growth" not in source
    assert "metric_kind" not in source


def test_a_kind_for_a_number_nobody_measured_is_refused() -> None:
    """A kind describes a number. One describing a number this experiment did not
    take is a claim about a measurement that does not exist."""
    with pytest.raises(LoopError, match="a claim about a measurement that does not exist"):
        Measured(
            measurement={"db.query": 2.0},
            kinds={"seconds": MetricKind.DURATION},
        )


def test_an_executor_that_knows_nothing_extra_still_works() -> None:
    """The ordinary case, and the reason both fields default. A primitive that
    fitted nothing and reports no kinds leaves the attacks that need them
    `NOT_RUN`, which is the answer `audit/compose.py` chose when they had to be
    passed by hand."""
    measured = Measured(measurement={"db.query": 2.0})

    assert measured.kinds == {}
    assert measured.fits == {}


def test_the_kinds_and_the_fit_survive_a_checkpoint(query_counter: None) -> None:
    """**AC 3, and the reason it is an AC.** Three of Epic 9's attacks read these
    off the log, so a resumed run that dropped them would audit weaker than the
    run that wrote them — and an attack that did not run is not one that passed.
    """
    investigation = run_the_diagnosis(Subject())
    stored = [_stored(item) for item in investigation.log.experiments]

    assert json.loads(json.dumps(stored)) == stored, "a checkpoint cannot hold what will not encode"

    restored = _log_of(CheckpointedState(experiments=stored))
    before = {
        item.index: (dict(item.kinds), dict(item.fits)) for item in investigation.log.experiments
    }
    after = {item.index: (dict(item.kinds), dict(item.fits)) for item in restored.experiments}

    assert after == before


def test_a_stored_experiment_still_fits_the_checkpoint_budget(query_counter: None) -> None:
    """S-6.3 budgets ~1 KiB per experiment so forty fit in one checkpoint. A `Fit`
    is eight numbers and a kinds mapping is one entry per metric; neither is the
    megabytes-per-node write F13 exists to prevent, but the budget is what says
    so rather than the intuition."""
    investigation = run_the_diagnosis(Subject())

    for item in investigation.log.experiments:
        assert len(json.dumps(_stored(item))) < 1024


# ======================== S-17.12: the fit the audit judges is the metric's own


def test_the_fit_the_audit_judges_is_the_one_for_the_cited_metric(
    query_counter: None,
) -> None:
    """**S-17.12's AC, driven rather than passed in.**

    `_fit_for` used to answer *the most recent fit recorded*, which was the only
    rule available while an experiment carried one — and recency is not the
    claim. A sweep fits every metric it measured, `audit_scales` reads `exponent`
    and `power_r_squared` off whatever curve it is handed, and the finding cites
    one metric. So the fit judged has to be that metric's.

    Driven through a real investigation, because the mapping this reads is built
    by the loop from what the primitive produced — supplying one by hand would
    test the selection against a shape nothing produces.
    """
    investigation = run_the_diagnosis(Subject())
    fits = fits_from(investigation.log)

    chosen = _fit_for(investigation.log, fits, QUERIES)

    sweep = next(
        item for item in investigation.log.experiments if item.primitive == "scaling.volume"
    )
    assert chosen is not None, "the sweep fitted this metric and the audit found it"
    assert chosen is sweep.fits[QUERIES]


def test_a_fit_for_a_metric_the_finding_does_not_cite_is_not_judged(
    query_counter: None,
) -> None:
    """**The other half, and the defect S-17.11 recorded.**

    A poor fit on a metric the finding says nothing about must raise no objection
    against it. Here the log carries a deliberately terrible curve under
    `seconds` — r² of 0.01, the shape `audit_scales` raises `FIT_TOO_POOR` from —
    beside the real one under the cited metric. Selecting by recency or by
    *whatever is there* would object to a claim nobody made.
    """
    investigation = run_the_diagnosis(Subject())
    fits = dict(fits_from(investigation.log))

    unusable = Fit(
        slope=0.0,
        intercept=0.0,
        linear_r_squared=0.01,
        exponent=0.0,
        power_r_squared=0.01,
        growth=Growth.CONSTANT,
        constant_below=0.2,
        superlinear_above=1.2,
    )
    latest = max(fits)
    fits[latest] = {**fits[latest], SECONDS: unusable}

    assert _fit_for(investigation.log, fits, QUERIES) is not unusable
    assert _fit_for(investigation.log, fits, SECONDS) is unusable, (
        "and it is found when it is the metric that was asked for"
    )


def test_a_metric_nothing_fitted_is_unjudged_rather_than_misjudged(
    query_counter: None,
) -> None:
    """`None` is the honest answer and S-9.2 already treats it as *not judged*.

    The alternative — falling back to some other metric's curve — is the failure
    this story exists to close, and it would read as an audit that ran.
    """
    investigation = run_the_diagnosis(Subject())

    assert _fit_for(investigation.log, fits_from(investigation.log), "nothing.measured") is None


def test_every_metric_the_sweep_fitted_reaches_the_log(query_counter: None) -> None:
    """S-17.11 carried a fit only for a single-metric sweep, which no real sweep
    is — so the scale audit had nothing to judge on any real run."""
    investigation = run_the_diagnosis(Subject())

    sweep = next(
        item for item in investigation.log.experiments if item.primitive == "scaling.volume"
    )

    assert len(sweep.fits) >= 1
    assert QUERIES in sweep.fits

"""S-8.7 — the thesis behaviour: concluding *not the database* and switching instrument.

`00-BRIEF.md` §5 calls this *the demo that justifies the whole architecture*. What
that demo has to show is not that a tool can run a scaling sweep — Epic 3 showed
that — but that a **rejection changes which instrument runs next**.

**What these tests prove, and what they cannot.** The measurements are real: the
sweep and the ablation execute against a planted defect and the numbers come back
from the harness. The model calls are replayed, because `CLAUDE.md` forbids a test
hitting the API. So this proves the rejection propagates, the harness refuses a
repeat, and the log records the switch and its reason — and it does **not** prove
that a model would choose to switch unprompted. That is what the video is for,
and it runs `run_investigation` with a real client rather than a second
implementation.

**The measurement reaching the model is counts and a rounded share, on purpose.**
A replayed call is found by hashing its prompt, and a prompt carrying
`8.2447281639...` differs on every run. Counts reproduce to the integer — ADR 052
already makes that the reason counts are what raise a flag — so the executor
reports those, and the raw timings travel in `detail` where they are retrievable
and not hashed.
"""

from __future__ import annotations

import inspect
import json

import pytest

import coldfix.primitives  # noqa: F401 - registers the thirteen
from coldfix.cost.accounting import Phase
from coldfix.cost.budget import Disposition
from coldfix.cost.routing import STEP_KINDS, StepType
from coldfix.diagnosis import hypothesis as hypothesis_module
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.exclusions import Conditions
from coldfix.diagnosis.log import ExperimentLog, ExperimentLogError, Verdict
from coldfix.diagnosis.loop import (
    Investigation as Loop,
)
from coldfix.diagnosis.loop import (
    Measured,
    NoNewInstrumentError,
    confirming_links,
    run_investigation,
)
from coldfix.diagnosis.progress import ProgressError, Stopped
from coldfix.llm.client import ReplayingClient
from coldfix.primitives.scaling import Distribution
from fixtures.thesis import (
    CONDITIONS,
    SCALES,
    Subject,
    _query_counter,  # noqa: F401 - registers the `query_counter` fixture
    _repeat_recordings,
    _synthetic,
    _synthetic_recordings,
    _thesis_recordings,
    ablate_renderer,
    an_investigation,
    instruments,
    recorded,
    rejected_experiment,
    sweep_queries,
)

# ================== AC 2, first half: the subject really does have both signatures


def test_the_query_count_is_flat_across_the_sweep(query_counter: None) -> None:
    """**Measured, not asserted.** The demo's premise is a repo where the
    database is genuinely not the answer, and this is the evidence for it: two
    queries at every scale, so a volume sweep concludes *not the database* and is
    right to."""
    counts = sweep_queries(Subject()).measurement

    assert set(counts.values()) == {2.0}, counts


def test_ablating_the_renderer_removes_almost_all_of_the_cost(query_counter: None) -> None:
    """The other half of the premise: the cost the query counter cannot see is
    real, large, and attributable to one component."""
    measured, _ = ablate_renderer(Subject())
    measurement = measured.measurement

    assert measurement["seconds.share_removed"] >= 0.9
    assert measurement["render.calls_baseline"] == 80.0
    assert measurement["render.calls_ablated"] == 80.0


def test_the_control_subject_has_nothing_for_an_ablation_to_find(query_counter: None) -> None:
    """**The load-bearing control.** A loop that always switched instruments and
    always confirmed would pass the demo while being useless. With the cheap
    renderer the same ablation removes nothing worth reporting."""
    measured, _ = ablate_renderer(Subject(expensive=False))
    measurement = measured.measurement

    assert measurement["seconds.share_removed"] < 0.9


# ============================== AC 1: a settled instrument may not be proposed again


def test_a_hypothesis_reproposing_a_settled_instrument_is_refused_and_reasked() -> None:
    """**AC 1, enforced rather than hoped for.** `CLAUDE.md`'s hard-enforcement
    table: a rule that must hold regardless of what an agent decides lives in
    code. The refusal is fed back in the vocabulary S-8.1's prompt already
    speaks — *an exclusion has already settled this* — and the re-ask is a fresh
    `generate` at the same temperature on the same tier."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"x": 1.0})
    investigation.exclusions.record(rejected_experiment(investigation.log).experiment, CONDITIONS)

    repeat = json.dumps(
        {
            "statement": "the database is still the bottleneck",
            "primitive": "scaling.volume",
            "rationale": "let us sweep again",
        }
    )
    switch = json.dumps(
        {
            "statement": "the renderer dominates the request",
            "primitive": "ablation.stub",
            "rationale": "queries are flat, so the cost is above the database",
        }
    )

    first = hypothesis_module.render_question(
        log=investigation.log,
        exclusions=investigation.exclusions.render(CONDITIONS),
        source=investigation.source,
        instruments=investigation.instruments,
    )
    note = (
        "scaling.volume has already answered under the conditions in force and was proposed "
        "again. Choose a different instrument, or say which condition would have to change for "
        "this one to be worth repeating."
    )
    second = hypothesis_module.render_question(
        log=investigation.log,
        exclusions=(*investigation.exclusions.render(CONDITIONS), note),
        source=investigation.source,
        instruments=investigation.instruments,
    )

    investigation.client = ReplayingClient(
        [
            recorded(
                system=hypothesis_module._SYSTEM,
                question=first,
                reply=repeat,
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            ),
            recorded(
                system=hypothesis_module._SYSTEM,
                question=second,
                reply=switch,
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            ),
        ]
    )

    hypothesis, refused = investigation.propose(
        measured_prefix_tokens=100, measured_prompt_tokens=900
    )

    assert refused == ("scaling.volume",)
    assert hypothesis.primitive == "ablation.stub"
    assert first != second


def test_an_agent_that_only_ever_repeats_is_a_result_and_not_a_crash() -> None:
    """`00-BRIEF.md` §9 ships null results. *Out of applicable experiments* is
    something a reader can act on; a traceback is not."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"x": 1.0})
    investigation.exclusions.record(rejected_experiment(investigation.log).experiment, CONDITIONS)

    repeat = json.dumps(
        {
            "statement": "the database is still the bottleneck",
            "primitive": "scaling.volume",
            "rationale": "again",
        }
    )
    notes: list[str] = []
    recordings = []
    for _ in range(3):
        question = hypothesis_module.render_question(
            log=investigation.log,
            exclusions=(*investigation.exclusions.render(CONDITIONS), *notes),
            source=investigation.source,
            instruments=investigation.instruments,
        )
        recordings.append(
            recorded(
                system=hypothesis_module._SYSTEM,
                question=question,
                reply=repeat,
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            )
        )
        notes.append(
            "scaling.volume has already answered under the conditions in force and was "
            "proposed again. Choose a different instrument, or say which condition would "
            "have to change for this one to be worth repeating."
        )
    investigation.client = ReplayingClient(recordings)

    with pytest.raises(NoNewInstrumentError) as raised:
        investigation.propose(measured_prefix_tokens=100, measured_prompt_tokens=900)

    assert raised.value.proposed == ("scaling.volume",) * 3
    assert "not a fault" in str(raised.value)


def test_a_settled_instrument_becomes_proposable_when_a_condition_moves() -> None:
    """**"Where the evidence supports it" is S-8.5's rule, not a new one.** The
    loop asks the register rather than keeping its own list, so a reseed to a
    skewed fixture reopens the instrument without this module knowing that
    reseeding exists."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"x": 1.0})
    investigation.exclusions.record(rejected_experiment(investigation.log).experiment, CONDITIONS)

    assert investigation.settled_instruments() == ("scaling.volume",)

    investigation.conditions = Conditions.of(
        fixture_shape=Distribution.LONG_TAIL.value,
        platform="x86_64-linux",
        concurrency=1,
        scales=list(SCALES),
    )

    assert investigation.settled_instruments() == ()


def test_re_asking_never_becomes_a_cascade() -> None:
    """The non-negotiable, checked from the story that added the re-ask. S-8.1
    must never cascade, and a loop that "retried" by supplying a validator would
    reach the forbidden thing through the front door."""
    assert STEP_KINDS[StepType.HYPOTHESIS_GENERATION].mechanical_check is None
    assert "validate" not in inspect.signature(Loop.propose).parameters
    assert "validate" not in inspect.getsource(Loop.propose)


# ================================= AC 3: the switch and its rationale are in the log


def test_the_log_shows_the_switch_with_the_reason_it_was_made() -> None:
    """AC 3. The primitive alone shows *that* the instrument changed; the thesis
    claim is about the choosing, so the rationale travels with it — and the
    verdict that provoked the switch is named, because switching after a
    rejection and switching after a confirmation are different behaviours."""
    log = ExperimentLog()
    log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 20, 40])",
        measurement={"db.query.n40": 2.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 2 across a 4x sweep",
    )
    log.append(
        hypothesis="the renderer dominates the request",
        primitive="ablation.stub",
        rationale="queries are flat, so the cost is above the database",
        target="ListView.renderer.render",
        design="ablation.stub(attribute='render')",
        measurement={"seconds.share_removed": 1.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the renderer removed the cost",
    )

    (switch,) = log.switches()
    described = log.describe_switches()

    assert switch[0].primitive == "scaling.volume"
    assert switch[1].primitive == "ablation.stub"
    assert "scaling.volume -> ablation.stub" in described
    assert "came back rejected" in described
    assert "queries are flat, so the cost is above the database" in described


def test_a_log_that_never_switched_says_so_rather_than_rendering_nothing() -> None:
    log = ExperimentLog()
    log.append(
        hypothesis="h",
        primitive="scaling.volume",
        rationale="r",
        target="t",
        design="d",
        measurement={"db.query": 2.0},
        verdict=Verdict.REJECTED,
        outcome="o",
    )

    assert "No instrument switch" in log.describe_switches()


def test_an_experiment_with_no_rationale_is_refused() -> None:
    """Required rather than defaulted: a default would make AC 3 hold only for
    the callers that remembered."""
    with pytest.raises(ExperimentLogError):
        ExperimentLog().append(
            hypothesis="h",
            primitive="p",
            rationale="",
            target="t",
            design="d",
            measurement={"m": 1.0},
            verdict=Verdict.REJECTED,
            outcome="o",
        )


# ================================================ AC 2: the whole thing, end to end


def test_the_thesis_run(query_counter: None) -> None:
    """**The demo.** Query count flat, so *not the database* — then a switch to
    ablation, which localizes the real cause. Real primitives, real measurements,
    replayed model calls.

    Every question is rendered from the modules under test rather than copied, so
    a change to any prompt breaks this loudly instead of silently replaying the
    wrong recording.
    """
    subject = Subject()
    detail: dict[str, str] = {}

    def execute(spec: object) -> Measured:
        primitive = spec.primitive  # type: ignore[attr-defined]
        if primitive == "scaling.volume":
            return sweep_queries(subject)
        measured, note = ablate_renderer(subject)
        detail["ablation"] = note
        return measured

    investigation = an_investigation(ReplayingClient([]), execute)
    investigation.client = _thesis_recordings(investigation, subject)

    result = run_investigation(
        investigation.session,
        investigation.client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=execute,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    first, second = result.log.experiments
    assert first.primitive == "scaling.volume"
    assert first.verdict is Verdict.REJECTED
    assert second.primitive == "ablation.stub"
    assert second.verdict is Verdict.CONFIRMED

    assert result.switched()
    assert "scaling.volume -> ablation.stub" in result.log.describe_switches()
    assert len(confirming_links(result)) == 1

    # The exclusion the first experiment bought is on the record, with the
    # conditions that make it conditional.
    (exclusion,) = result.exclusions.exclusions
    assert "fixture shape uniform" in exclusion.conditions.describe()


# ============================ the four properties a sabotage pass found untested


def test_the_loop_records_the_rationale_the_agent_actually_gave(query_counter: None) -> None:
    """**AC 3 through the loop, not around it.**

    A sabotage that replaced the rationale with a constant on its way into the
    log survived every test here: the switch test builds its log by hand, and the
    thesis run never read the field back. AC 3 is about what the *loop* records,
    so it has to be asserted where the loop puts it.
    """
    subject = Subject()

    def execute(spec: ExperimentSpec) -> Measured:
        if spec.primitive == "scaling.volume":
            return sweep_queries(subject)
        return ablate_renderer(subject)[0]

    investigation = an_investigation(ReplayingClient([]), execute)
    client = _thesis_recordings(investigation, subject)
    result = run_investigation(
        investigation.session,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=execute,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    first, second = result.log.experiments

    assert first.rationale == "queries have not been counted against volume yet"
    assert second.rationale == "queries are flat, so the cost is above the database"
    assert second.rationale in result.log.describe_switches()


def test_a_narrowed_verdict_does_not_end_the_investigation() -> None:
    """Narrowing is progress, not a conclusion — S-8.4 kept the third verdict
    separate precisely because collapsing it throws away the half of the search
    space the experiment bought.

    Untested until a sabotage stopped the loop on it, because the planted defect
    never narrows: it confirms on the second step.
    """
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})
    investigation.instruments = instruments("scaling.volume", "ablation.stub", "scaling.shape")
    client = _synthetic(investigation, [Verdict.NARROWED, Verdict.CONFIRMED])

    result = run_investigation(
        investigation.session,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=lambda spec: Measured(measurement={"db.query": 2.0}),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert [item.verdict for item in result.log.experiments] == [
        Verdict.NARROWED,
        Verdict.CONFIRMED,
    ]


def test_the_loop_is_bounded_by_the_experiment_cap_and_stops_with_a_result() -> None:
    """**S-8.9 replaced S-8.7's loop guard with the real cap.** There is now
    exactly one number that stops this, and it is the forty `04-cost.md` costed.

    Reaching it is not an exception: `00-BRIEF.md` §9 ships null results as
    answers, so the run comes back with `stopped` set and the exclusions it
    bought.
    """
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})
    investigation.instruments = instruments("scaling.volume", "ablation.stub", "scaling.shape")
    investigation.session.budget.tighten(Phase.INVESTIGATE, 2)
    client = _synthetic(investigation, [Verdict.REJECTED, Verdict.REJECTED])

    result = run_investigation(
        investigation.session,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=lambda spec: Measured(measurement={"db.query": 2.0}),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert result.stopped is Stopped.CAP
    assert len(result.log.experiments) == 2
    assert len(result.exclusions.exclusions) == 2


def test_switches_ignores_two_experiments_that_used_the_same_instrument() -> None:
    """**A fixture that could not discriminate.** The switch test had exactly two
    experiments with two different primitives, so *every consecutive pair* and
    *every pair that changed* were the same list — and a `switches()` reporting
    all pairs passed it.

    Three experiments where two share an instrument tell the two rules apart.
    """
    log = ExperimentLog()
    for index, primitive in enumerate(["scaling.volume", "scaling.volume", "ablation.stub"]):
        log.append(
            hypothesis=f"hypothesis {index}",
            primitive=primitive,
            rationale=f"reason {index}",
            target="shop.books.list",
            design=f"{primitive}() on shop.books.list",
            measurement={"db.query": 2.0},
            verdict=Verdict.REJECTED,
            outcome=f"outcome {index}",
        )

    switches = log.switches()

    assert len(switches) == 1
    assert switches[0][0].primitive == "scaling.volume"
    assert switches[0][1].primitive == "ablation.stub"


# ============ S-8.9: the loop's three ways to stop, and what each has to show


def test_running_out_of_instruments_is_reported_as_such_and_not_as_the_cap() -> None:
    """Three ways to run out, and a reader's next action differs for each — so
    the loop must not collapse them. Untested until a sabotage relabelled this
    one as the cap and nothing failed."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})
    investigation.instruments = instruments("scaling.volume")
    # One instrument, one rejection, then three repeat proposals it has to
    # refuse — which is `propose` running out rather than the cap doing it.
    first_turn = _synthetic_recordings(investigation, [Verdict.REJECTED])
    repeats = _repeat_recordings(investigation, "scaling.volume")

    result = run_investigation(
        investigation.session,
        ReplayingClient([*first_turn, *repeats]),
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=lambda spec: Measured(measurement={"db.query": 2.0}),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert result.stopped is Stopped.INSTRUMENTS
    assert result.stopped.disposition is Disposition.ESCALATE


def test_a_stopped_investigation_hands_over_the_exclusions_it_bought() -> None:
    """AC 3 through the loop. A partial chain assembled without them would report
    an investigation that learned nothing, when what it learned is exactly the
    exclusions — `00-BRIEF.md` §9's proven negative."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})
    investigation.instruments = instruments("scaling.volume", "ablation.stub", "scaling.shape")
    investigation.session.budget.tighten(Phase.INVESTIGATE, 2)
    client = _synthetic(investigation, [Verdict.REJECTED, Verdict.REJECTED])

    result = run_investigation(
        investigation.session,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=lambda spec: Measured(measurement={"db.query": 2.0}),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )
    chain = result.partial_chain(Symptom(metric="seconds", magnitude=8.24, at_scale=1000))

    assert len(chain.exclusions) == 2
    assert chain.stopped is Stopped.CAP
    assert "hypothesis 0" in chain.describe()


def test_a_running_investigation_has_no_partial_chain_to_give() -> None:
    """A partial chain is what a run that *ended* without a cause has to show.
    One taken mid-run would report an investigation as finished while it is still
    buying evidence."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})

    with pytest.raises(ProgressError, match="has not stopped"):
        investigation.partial_chain(Symptom(metric="seconds", magnitude=1.0, at_scale=10))

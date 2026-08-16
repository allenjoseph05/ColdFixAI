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
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar

import pytest

import coldfix.primitives  # noqa: F401 - registers the thirteen
from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.cost.accounting import ExchangeRate, Phase
from coldfix.cost.budget import Disposition
from coldfix.cost.routing import STEP_KINDS, StepType
from coldfix.cost.session import Session
from coldfix.diagnosis import design as design_module
from coldfix.diagnosis import hypothesis as hypothesis_module
from coldfix.diagnosis import interpretation as interpretation_module
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.design import ExperimentSpec, JSONValue
from coldfix.diagnosis.exclusions import Conditions, Exclusion, ExclusionRegister
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.log import ExperimentLog, ExperimentLogError, Verdict
from coldfix.diagnosis.loop import (
    Investigation,
    NoNewInstrumentError,
    confirming_links,
    run_investigation,
)
from coldfix.diagnosis.loop import (
    Investigation as Loop,
)
from coldfix.diagnosis.progress import INVESTIGATION_STALL_AFTER, ProgressError, Stopped
from coldfix.diagnosis.schema import schema_of
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.ablation import ablate
from coldfix.primitives.registry import REGISTRY, ProjectProfile, Selection
from coldfix.primitives.scaling import Distribution, scale_volume
from coldfix.sandbox.modes import DiagnosticSession
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.rendering import CheapRenderer, ExpensiveRenderer, ListView
from fixtures.planted.store import Store, build_store

QUERIES = "store.select"
SCALES = (10, 20, 40)

CONDITIONS = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=list(SCALES),
)


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


# ------------------------------------------------------- the subject and its harness


class Subject:
    """The planted view, rebuilt at each scale, with the renderer under test."""

    def __init__(self, *, expensive: bool = True) -> None:
        self.store = Store()
        self.renderer: ExpensiveRenderer | CheapRenderer = (
            ExpensiveRenderer() if expensive else CheapRenderer()
        )
        self.view = ListView(self.store, self.renderer)
        self.processes = 0

    def seed(self, scale: int) -> None:
        self.store = build_store(authors=scale, books_per_author=2)
        self.view = ListView(self.store, self.renderer)

    def invoke(self) -> object:
        return self.view.list_books()

    def process_identity(self) -> str:
        """A fresh process per condition, as a container would give.

        S-3.2 refuses a sweep that reuses one: whatever the previous point
        warmed is still warm, and ADR 026 records that a cache cannot be
        detected from the results.
        """
        self.processes += 1
        return f"container-{self.processes}"


class SnapshotReset(ResetMechanism):
    """Restores the subject's store, which is all the state there is."""

    strategy: ClassVar[ResetStrategy] = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        return None

    def begin(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is not None:
            self.subject.store = deepcopy(self._snapshot)
            self.subject.view = ListView(self.subject.store, self.subject.renderer)


class FakeDiagnosticSession(DiagnosticSession):
    """Diagnostic mode without a container. `ablate` refuses a candidate session,
    and what is under test here is the switch rather than docker."""

    def __init__(self) -> None:
        pass


def verified(subject: Subject) -> VerifiedReset:
    mechanism = SnapshotReset(subject)
    return VerifiedReset(
        mechanism=mechanism,
        report=VerificationReport(strategy=mechanism.strategy, cycles=10),
    )


def sweep_queries(subject: Subject) -> Mapping[str, float]:
    """A real volume sweep, reported as the counts it produced."""
    result = scale_volume(
        seed=subject.seed,
        invoke=subject.invoke,
        reset=verified(subject),
        scales=SCALES,
        distribution=Distribution.UNIFORM,
        counters=[QUERIES],
        process_identity=subject.process_identity,
    )
    return {
        f"db.query.n{point.scale}": float(point.raw[QUERIES])
        for point in result.points
        if point.scale in SCALES
    }


def ablate_renderer(subject: Subject) -> tuple[Mapping[str, float], str]:
    """A real ablation of the renderer, reported as counts and a rounded share.

    The share is rounded because the prompt it lands in is hashed to find a
    recording, and a full-precision float differs on every run. Two decimals is
    far coarser than the effect — this fixture removes essentially all of the
    work — so the rounding cannot change the verdict.
    """
    subject.seed(SCALES[-1])
    result = ablate(
        # The class, not the instance: `render` is defined on the type, and
        # `ablate` refuses an owner that does not define the attribute itself.
        owner=type(subject.renderer),
        attribute="render",
        invoke=subject.invoke,
        reset=verified(subject),
        session=FakeDiagnosticSession(),
        counters=[QUERIES],
        process_identity=subject.process_identity,
    )
    share = round(result.share("seconds"), 2)
    return (
        {
            "seconds.share_removed": share,
            "render.calls_baseline": float(result.calls_baseline),
            "render.calls_ablated": float(result.calls_ablated),
        },
        f"baseline {result.baseline} against ablated {result.ablated}",
    )


# ------------------------------------------------------------------ the model double


def payload(text: str, *, model: str) -> dict[str, object]:
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 80,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8000,
        },
    }


def recorded(
    *, system: str, question: str, reply: str, model: str, temperature: float
) -> Recording:
    return Recording.of(
        model=model,
        system=system,
        messages=[{"role": "user", "content": question}],
        max_tokens={
            hypothesis_module._SYSTEM: hypothesis_module.MAX_OUTPUT_TOKENS,
            design_module._SYSTEM: design_module.MAX_OUTPUT_TOKENS,
            interpretation_module._SYSTEM: interpretation_module.MAX_OUTPUT_TOKENS,
        }[system],
        temperature=temperature,
        response=payload(reply, model=model),
    )


def instruments(*names: str) -> Selection:
    return Selection(
        profile=ProjectProfile(),
        available=tuple(REGISTRY.get(name) for name in names),
        withheld=(),
    )


def a_session() -> Session:
    return Session(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries with force_debug_cursor.",
        source="shop/views.py::ListView.list_books",
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)),
        # S-8.9 refuses an investigation budget at any other value.
        stall_after=INVESTIGATION_STALL_AFTER,
    )


def an_investigation(client: ReplayingClient, execute: object) -> Investigation:
    return Investigation(
        session=a_session(),
        client=client,
        instruments=instruments("scaling.volume", "ablation.stub"),
        source="shop/views.py::ListView.list_books",
        conditions=CONDITIONS,
        execute=execute,  # type: ignore[arg-type]
    )


def rejected_experiment(log: ExperimentLog) -> Exclusion:
    experiment = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 20, 40]) on shop.books.list",
        measurement={"db.query.n10": 2.0, "db.query.n40": 2.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 2 across a 4x sweep",
    )
    return Exclusion(experiment=experiment, conditions=CONDITIONS)


# ================== AC 2, first half: the subject really does have both signatures


def test_the_query_count_is_flat_across_the_sweep(query_counter: None) -> None:
    """**Measured, not asserted.** The demo's premise is a repo where the
    database is genuinely not the answer, and this is the evidence for it: two
    queries at every scale, so a volume sweep concludes *not the database* and is
    right to."""
    counts = sweep_queries(Subject())

    assert set(counts.values()) == {2.0}, counts


def test_ablating_the_renderer_removes_almost_all_of_the_cost(query_counter: None) -> None:
    """The other half of the premise: the cost the query counter cannot see is
    real, large, and attributable to one component."""
    measurement, _ = ablate_renderer(Subject())

    assert measurement["seconds.share_removed"] >= 0.9
    assert measurement["render.calls_baseline"] == 80.0
    assert measurement["render.calls_ablated"] == 80.0


def test_the_control_subject_has_nothing_for_an_ablation_to_find(query_counter: None) -> None:
    """**The load-bearing control.** A loop that always switched instruments and
    always confirmed would pass the demo while being useless. With the cheap
    renderer the same ablation removes nothing worth reporting."""
    measurement, _ = ablate_renderer(Subject(expensive=False))

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

    def execute(spec: object) -> Mapping[str, float]:
        primitive = spec.primitive  # type: ignore[attr-defined]
        if primitive == "scaling.volume":
            return sweep_queries(subject)
        measurement, note = ablate_renderer(subject)
        detail["ablation"] = note
        return measurement

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


@dataclass(frozen=True)
class Turn:
    """One planned turn of the demo: what the model says, and what the harness measures."""

    hypothesis: Hypothesis
    spec: ExperimentSpec
    measurement: Mapping[str, float]
    reading: Mapping[str, object]


def _thesis_recordings(investigation: Investigation, subject: Subject) -> ReplayingClient:
    """Build the six recordings the thesis run needs, from real measurements.

    The measurements come from actually running the primitives, which is what
    makes the recorded questions the ones the real run will ask.
    """
    sweep = sweep_queries(subject)
    ablation, _ = ablate_renderer(subject)

    plan = [
        Turn(
            hypothesis=Hypothesis(
                statement="the database is the bottleneck",
                primitive="scaling.volume",
                rationale="queries have not been counted against volume yet",
            ),
            spec=ExperimentSpec(
                primitive="scaling.volume",
                target="shop.books.list",
                arguments={"scales": list(SCALES), "distribution": "uniform"},
            ),
            measurement=sweep,
            reading={
                "verdict": "rejected",
                "outcome": "queries flat at 2 across a 4x sweep",
                "cites": {
                    "db.query.n10": sweep["db.query.n10"],
                    "db.query.n40": sweep["db.query.n40"],
                },
            },
        ),
        Turn(
            hypothesis=Hypothesis(
                statement="the renderer dominates the request",
                primitive="ablation.stub",
                rationale="queries are flat, so the cost is above the database",
            ),
            spec=ExperimentSpec(
                primitive="ablation.stub",
                target="ListView.renderer.render",
                arguments={"attribute": "render"},
            ),
            measurement=ablation,
            reading={
                "verdict": "confirmed",
                "outcome": "stubbing the renderer removed the wall time",
                "cites": {"seconds.share_removed": ablation["seconds.share_removed"]},
            },
        ),
    ]

    return _recordings_for(investigation, plan)


def _recordings_for(investigation: Investigation, plan: list[Turn]) -> ReplayingClient:
    return ReplayingClient(_recording_list(investigation, plan))


def _recording_list(investigation: Investigation, plan: list[Turn]) -> list[Recording]:
    """Walk a planned run once, recording the request each call will make.

    Every question is rendered by the module that will send it, so a change to
    any prompt breaks the test loudly instead of silently replaying the wrong
    recording.
    """
    log = ExperimentLog()
    recordings = []
    register = ExclusionRegister()

    for turn in plan:
        answer = {
            "statement": turn.hypothesis.statement,
            "primitive": turn.hypothesis.primitive,
            "rationale": turn.hypothesis.rationale,
        }
        spec_reply = {"target": turn.spec.target, "arguments": dict(turn.spec.arguments)}
        hypothesis, spec, measurement, reading = (
            turn.hypothesis,
            turn.spec,
            turn.measurement,
            turn.reading,
        )
        question = hypothesis_module.render_question(
            log=log,
            exclusions=register.render(CONDITIONS),
            source=investigation.source,
            instruments=investigation.instruments,
        )
        recordings.append(
            recorded(
                system=hypothesis_module._SYSTEM,
                question=question,
                reply=json.dumps(answer),
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            )
        )

        hypothesis = Hypothesis(**answer)
        schema = schema_of(investigation.instruments.get(hypothesis.primitive))
        design_question = design_module.render_question(
            hypothesis=hypothesis, schema=schema, source=investigation.source, log=log
        )
        recordings.append(
            recorded(
                system=design_module._SYSTEM,
                question=design_question,
                reply=json.dumps(spec_reply),
                model="claude-sonnet-5",
                temperature=design_module.DESIGN_TEMPERATURE,
            )
        )

        interpret_question = interpretation_module.render_question(
            hypothesis=hypothesis, spec=spec, measurement=measurement, log=log
        )
        recordings.append(
            recorded(
                system=interpretation_module._SYSTEM,
                question=interpret_question,
                reply=json.dumps(reading),
                model="claude-sonnet-5",
                temperature=interpretation_module.INTERPRETATION_TEMPERATURE,
            )
        )

        experiment = log.append(
            hypothesis=hypothesis.statement,
            primitive=hypothesis.primitive,
            rationale=hypothesis.rationale,
            target=spec.target,
            design=spec.render(),
            measurement=measurement,
            verdict=Verdict(str(reading["verdict"])),
            outcome=str(reading["outcome"]),
        )
        if experiment.verdict is Verdict.REJECTED:
            register.record(experiment, CONDITIONS)

    return recordings


# ============================ the four properties a sabotage pass found untested


def test_the_loop_records_the_rationale_the_agent_actually_gave(query_counter: None) -> None:
    """**AC 3 through the loop, not around it.**

    A sabotage that replaced the rationale with a constant on its way into the
    log survived every test here: the switch test builds its log by hand, and the
    thesis run never read the field back. AC 3 is about what the *loop* records,
    so it has to be asserted where the loop puts it.
    """
    subject = Subject()

    def execute(spec: ExperimentSpec) -> Mapping[str, float]:
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


def _synthetic(investigation: Investigation, verdicts: list[Verdict]) -> ReplayingClient:
    return ReplayingClient(_synthetic_recordings(investigation, verdicts))


def _synthetic_recordings(investigation: Investigation, verdicts: list[Verdict]) -> list[Recording]:
    """A run with fixed measurements, for the paths a real subject cannot reach.

    The thesis run measures for real; these two need a *narrowed* verdict and an
    exhausted bound, neither of which the planted defect produces.
    """
    instruments_in_order = ["scaling.volume", "ablation.stub", "scaling.shape"]
    # Real arguments, because S-8.2's schema check is not bypassed by a test: a
    # specification the primitive would refuse gets rejected and re-asked, and
    # the re-ask has no recording.
    arguments: dict[str, Mapping[str, JSONValue]] = {
        "scaling.volume": {"scales": list(SCALES), "distribution": "uniform"},
        "ablation.stub": {"attribute": "render"},
        "scaling.shape": {"groups": 10, "total": 20},
    }
    measurements = {name: {"db.query": 2.0} for name in instruments_in_order}
    plan = [
        Turn(
            hypothesis=Hypothesis(
                statement=f"hypothesis {index}",
                primitive=instruments_in_order[index],
                rationale=f"reason {index}",
            ),
            spec=ExperimentSpec(
                primitive=instruments_in_order[index],
                target="shop.books.list",
                arguments=arguments[instruments_in_order[index]],
            ),
            measurement=measurements[instruments_in_order[index]],
            reading={
                "verdict": verdict.value,
                "outcome": f"outcome {index}",
                "cites": {"db.query": 2.0},
            },
        )
        for index, verdict in enumerate(verdicts)
    ]
    return _recording_list(investigation, plan)


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
        execute=lambda spec: {"db.query": 2.0},
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
        execute=lambda spec: {"db.query": 2.0},
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
        execute=lambda spec: {"db.query": 2.0},
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
        execute=lambda spec: {"db.query": 2.0},
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


def _repeat_recordings(investigation: Investigation, primitive: str) -> list[Recording]:
    """The three refusals `propose` needs before it gives up on one instrument."""
    register = ExclusionRegister()
    log = ExperimentLog()
    experiment = log.append(
        hypothesis="hypothesis 0",
        primitive=primitive,
        rationale="reason 0",
        target="shop.books.list",
        design=f"{primitive}(scales=[10, 20, 40], distribution='uniform') on shop.books.list",
        measurement={"db.query": 2.0},
        verdict=Verdict.REJECTED,
        outcome="outcome 0",
    )
    register.record(experiment, CONDITIONS)

    repeat = json.dumps(
        {"statement": "again", "primitive": primitive, "rationale": "one more sweep"}
    )
    note = (
        f"{primitive} has already answered under the conditions in force and was proposed "
        "again. Choose a different instrument, or say which condition would have to change "
        "for this one to be worth repeating."
    )
    notes: list[str] = []
    recordings = []
    for _ in range(3):
        question = hypothesis_module.render_question(
            log=log,
            exclusions=(*register.render(CONDITIONS), *notes),
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
        notes.append(note)
    return recordings

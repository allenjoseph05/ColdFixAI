"""The thesis subject and the harness that drives it.

Extracted at Epic 8's composition check. `test_switching.py` built this for
S-8.7 and `test_diagnosis_composed.py` needs the same subject — and a test module
importing another test module is a source file mypy sees under two names, as well
as a dependency between two suites that should each stand alone.

A second copy would have been the other option, and it is the one this project
keeps refusing: the subject's signatures are what both suites assert about, so
two of them would drift and the one that drifted would be the one nobody ran.
"""

from __future__ import annotations

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
from coldfix.cost.accounting import ExchangeRate
from coldfix.cost.session import Session
from coldfix.diagnosis import design as design_module
from coldfix.diagnosis import hypothesis as hypothesis_module
from coldfix.diagnosis import interpretation as interpretation_module
from coldfix.diagnosis.design import ExperimentSpec, JSONValue
from coldfix.diagnosis.exclusions import Conditions, Exclusion, ExclusionRegister
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.diagnosis.loop import Investigation, Measured
from coldfix.diagnosis.progress import INVESTIGATION_STALL_AFTER
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
from fixtures.requests import shaped

QUERIES = "store.select"
SCALES = (10, 20, 40)

CONDITIONS = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=list(SCALES),
)


# ------------------------------------------------------- the subject and its harness


@pytest.fixture(name="query_counter")
def _query_counter() -> Iterator[None]:
    """The store's `select`, registered as a counted hook for one test.

    Registered under a name that differs from the function's, so a module can
    import `_query_counter` without the fixture *parameter* in its test
    signatures reading to a linter as a redefinition of the import. The obvious
    route — a `tests/diagnosis/conftest.py` — collides with
    `tests/sandbox/conftest.py` under mypy, which this project had already
    recorded and this session walked into anyway.
    """
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


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


def sweep_queries(subject: Subject) -> Measured:
    """A real volume sweep, with what the primitive knew about it. **S-8.12.**

    The counts used to be all that survived. `scale_volume` returns a `kinds`
    mapping and a `Fit` per metric, and both were dropped here because
    `Executor` had nowhere to put them — which left S-9.4 with no curve to judge
    and S-9.6 with no way to know a count from a duration.

    The kind is taken from the result rather than from the key. These are counts
    because the primitive says the counter is a count, not because the string
    starts with `db.query`.
    """
    result = scale_volume(
        seed=subject.seed,
        invoke=subject.invoke,
        reset=verified(subject),
        scales=SCALES,
        distribution=Distribution.UNIFORM,
        counters=[QUERIES],
        process_identity=subject.process_identity,
    )
    measurement = {
        f"db.query.n{point.scale}": float(point.raw[QUERIES])
        for point in result.points
        if point.scale in SCALES
    }
    return Measured(
        measurement=measurement,
        kinds=dict.fromkeys(measurement, result.kinds[QUERIES]),
        fits={QUERIES: result.fits[QUERIES]},
    )


def ablate_renderer(subject: Subject) -> tuple[Measured, str]:
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
    # **The key comes from the primitive, not from this fixture.** It emitted
    # exactly this quantity under exactly this spelling before anything in `src/`
    # named it, and `shares_from` has to find it — a name each end spells for
    # itself is a finding with no localization and nothing saying why.
    name, measured = result.reported("seconds")
    share = round(measured, 2)
    measurement = {
        name: share,
        "render.calls_baseline": float(result.calls_baseline),
        "render.calls_ablated": float(result.calls_ablated),
    }
    return (
        Measured(
            measurement=measurement,
            # **`seconds.share_removed` is a share of a duration, and this is the
            # case `audit/compose.py` names.** `metric_kind` reads it off the
            # spelling and calls it a count; S-9.6 holds that a count moving at
            # all is material, so a re-run would diverge every time and every
            # finding would come back `unsound`. The primitive knows it came from
            # `seconds`, so the kind travels from there.
            kinds={
                name: result.kinds["seconds"],
                "render.calls_baseline": result.kinds[QUERIES],
                "render.calls_ablated": result.kinds[QUERIES],
            },
            # An ablation fits no curve, and saying so is what keeps S-9.4
            # `NOT_RUN` here rather than judging one nobody drew.
            fits={},
        ),
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


def recording_session(log: ExperimentLog) -> Session:
    """A session rendering `log`, for building recordings against. **S-17.16.**

    **The log has to be joined before the first `prompt_for`.** `Investigation`
    binds `log_source=self.log.render` when it builds a model's prompt and caches
    the prompt from then on, so a session given its log afterwards renders the
    empty one it was constructed with — for every call, silently, because an
    empty log block is a valid prefix of a full one and caching would still
    report hits.

    Bound to the `PrunedLog` object rather than to a copy of its text, so the
    block grows as the walk appends and each recording sees the log as it stood
    at its own call. That is what the real run does: `Investigation.__post_init__`
    performs this same assignment, for this same reason.
    """
    session = a_session()
    session.log = log.pruned
    return session


def recorded(  # noqa: PLR0913 - a recording is identified by its whole request, and
    # the digest is over every one of these. Dropping one to satisfy the rule would
    # mean defaulting it, and a defaulted temperature or model is how a recording
    # comes to answer a call it was not made for.
    *,
    session: Session,
    system: str,
    question: str,
    reply: str,
    model: str,
    temperature: float,
) -> Recording:
    """One recording, shaped from `session`'s prompt as it stands right now.

    `session` is not a default. A recording built from a fresh session records
    the request as it would be on call one, and every call after the first asks
    against a longer log — so a default here would make the caller's *second*
    recording wrong in a way nothing reads as an error.
    """
    return Recording.of(
        model=model,
        # **The module's own prompt, never the session's.** `as_request` does not
        # shape `Segment.SYSTEM`, because one session drives all three
        # Diagnostician steps and its string is not every step's prompt.
        system=system,
        messages=shaped(session, model, question),
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
    sweep = sweep_queries(subject).measurement
    ablation = ablate_renderer(subject)[0].measurement

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
    session = recording_session(log)
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
            exclusions=register.render(CONDITIONS), instruments=investigation.instruments
        )
        recordings.append(
            recorded(
                session=session,
                system=hypothesis_module._SYSTEM,
                question=question,
                reply=json.dumps(answer),
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            )
        )

        hypothesis = Hypothesis(**answer)
        schema = schema_of(investigation.instruments.get(hypothesis.primitive))
        design_question = design_module.render_question(hypothesis=hypothesis, schema=schema)
        recordings.append(
            recorded(
                session=session,
                system=design_module._SYSTEM,
                question=design_question,
                reply=json.dumps(spec_reply),
                model="claude-sonnet-5",
                temperature=design_module.DESIGN_TEMPERATURE,
            )
        )

        interpret_question = interpretation_module.render_question(
            hypothesis=hypothesis, spec=spec, measurement=measurement
        )
        recordings.append(
            recorded(
                session=session,
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


def _repeat_recordings(investigation: Investigation, primitive: str) -> list[Recording]:
    """The three refusals `propose` needs before it gives up on one instrument."""
    register = ExclusionRegister()
    log = ExperimentLog()
    session = recording_session(log)
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
            exclusions=(*register.render(CONDITIONS), *notes), instruments=investigation.instruments
        )
        recordings.append(
            recorded(
                session=session,
                system=hypothesis_module._SYSTEM,
                question=question,
                reply=repeat,
                model="claude-opus-5",
                temperature=hypothesis_module.HYPOTHESIS_TEMPERATURE,
            )
        )
        notes.append(note)
    return recordings


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

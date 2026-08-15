"""S-8.2 — turning a hypothesis into an experiment, and what may not get through.

The mirror of S-8.1's suite. That story's non-negotiable was *no cascade*; this
one's is that the cascade is **real** — it retries with something new, its
validator cannot be replaced, and adding a mechanical row to `04-cost.md` §3 did
not make the creative rows cascadable.

Every test runs against S-0.7b's replaying client, which holds no vendor client
at all.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal

import pytest

import coldfix.primitives  # noqa: F401 - registers the thirteen; REGISTRY is empty without it
from coldfix.cost.accounting import ExchangeRate, Phase, StepClass
from coldfix.cost.cascade import cascadable
from coldfix.cost.pruning import MAX_SUMMARY_CHARS
from coldfix.cost.routing import STEP_KINDS, Router, StepType, Tier, classify
from coldfix.cost.session import Session, StepOutcome
from coldfix.diagnosis import design as design_module
from coldfix.diagnosis.design import (
    DESIGN_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    DesignError,
    ExperimentSpec,
    UndesignableError,
    design,
    parse,
    render_question,
)
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.diagnosis.schema import Parameter, PrimitiveSchema, SchemaError, schema_of
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    CostClass,
    Primitive,
    PrimitiveUnavailableError,
    ProjectProfile,
    Selection,
    Withheld,
)
from coldfix.primitives.registry import (
    Verdict as ApplicabilityVerdict,
)
from coldfix.primitives.scaling import _check_scales

HYPOTHESIS = Hypothesis(
    statement="the author lookup is an N+1 across the book list",
    primitive="scaling.volume",
    rationale="queries have not been counted against volume yet",
)

SPEC = {
    "target": "shop.books.list",
    "arguments": {"scales": [10, 100, 1000], "distribution": "power_law"},
}

SOURCE = "shop/views.py::book_list"


# ------------------------------------------------------------------------ helpers


def payload(
    text: str, *, model: str = "claude-sonnet-5", stop_reason: str = "end_turn"
) -> dict[str, object]:
    """A response the vendor's own model will parse, which is S-0.7b's whole rule."""
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_design",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 90,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8_000,
        },
    }


def instruments(*names: str) -> Selection:
    """A selection built from the **real** registry entries.

    A fake primitive would let the schema check pass against a vocabulary this
    system does not have, which is S-8.1's argument one story on.
    """
    return Selection(
        profile=ProjectProfile(),
        available=tuple(REGISTRY.get(name) for name in names),
        withheld=(),
    )


def volume_schema() -> PrimitiveSchema:
    return schema_of(REGISTRY.get("scaling.volume"))


def a_log() -> ExperimentLog:
    log = ExperimentLog()
    log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        target="shop.books.list",
        design='ablation.stub(attribute="to_representation") on shop.books.list',
        measurement={"seconds": 0.4},
        verdict=Verdict.REJECTED,
        outcome="stubbing changed nothing",
    )
    return log


def a_session() -> Session:
    return Session(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries with force_debug_cursor.",
        source=SOURCE,
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 15)),
    )


def recording(
    question: str,
    reply: str,
    *,
    model: str = "claude-sonnet-5",
    temperature: float = DESIGN_TEMPERATURE,
    stop_reason: str = "end_turn",
) -> Recording:
    return Recording.of(
        model=model,
        system=design_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=temperature,
        response=payload(reply, model=model, stop_reason=stop_reason),
    )


def question_after(*rejections: str) -> str:
    """The question this module sends on the attempt following `rejections`."""
    return render_question(
        hypothesis=HYPOTHESIS,
        schema=volume_schema(),
        source=SOURCE,
        log=a_log(),
        rejections=rejections,
    )


def rejection_for(reply: str) -> str:
    """What this module will say is wrong with `reply`, read from the module."""
    return parse(reply, primitive="scaling.volume", schema=volume_schema()).rejection


def run_design(client: ReplayingClient) -> StepOutcome[ExperimentSpec]:
    return design(
        a_session(),
        client,
        hypothesis=HYPOTHESIS,
        instruments=instruments("scaling.volume", "ablation.stub"),
        source=SOURCE,
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )


# ============================== AC 2: what a primitive's schema is, and where from


def test_a_schema_is_read_from_the_function_rather_than_declared_beside_it() -> None:
    """S-3.1's argument for `Primitive.signature`, one layer on: two statements of
    one signature drift, and the one that drifts would be the one the model reads
    rather than the one that is executed."""

    def run(*, scales: list[int], label: str = "x") -> None: ...

    schema = schema_of(
        Primitive(name="fake.thing", summary="a fake", cost=CostClass.SECONDS, run=run)
    )

    assert [parameter.name for parameter in schema.specifiable] == ["scales", "label"]
    assert schema.specifiable[0].required
    assert not schema.specifiable[1].required


def test_the_harness_half_and_the_design_half_are_separated() -> None:
    """The story's substance. `scale_volume` takes nine parameters and a model can
    answer three of them; the other six are the grounded workload."""
    schema = volume_schema()

    assert [parameter.name for parameter in schema.specifiable] == [
        "scales",
        "distribution",
        "counters",
    ]
    assert {parameter.name for parameter in schema.bound} == {
        "seed",
        "invoke",
        "reset",
        "extra_counters",
        "clear_caches",
        "process_identity",
    }


def test_every_registered_primitive_has_a_readable_schema() -> None:
    """Swept across the whole registry rather than asserted on one primitive: the
    partition is only real if it falls out of annotations that were already
    written, and thirteen instruments are the evidence for that."""
    for primitive in REGISTRY.declared():
        schema = schema_of(primitive)
        assert schema.primitive == primitive.name
        assert schema.render()


def test_nothing_a_model_cannot_write_is_ever_the_designs_to_set() -> None:
    """The partition, checked against the registry rather than against one case.

    Every parameter typed as a callable, a live object, a path or a mapping is
    the harness's. If a future primitive annotates one of those as specifiable,
    this fails before a model is ever asked to invent one.
    """
    unwritable = ("Callable", "VerifiedReset", "DiagnosticSession", "Repository", "Path", "Mapping")

    for primitive in REGISTRY.declared():
        for parameter in schema_of(primitive).specifiable:
            rendered = str(parameter.annotation)
            assert not any(word in rendered for word in unwritable), (
                f"{primitive.name}.{parameter.name} is {rendered}"
            )


def test_a_measurement_shaped_mapping_is_never_the_designs_to_set() -> None:
    """`CLAUDE.md`: do not let an agent report a measurement.

    `bounds.headroom(metrics=...)` is a `Mapping[str, float]`, which is exactly
    the shape of one — and a schema that let a design carry it would defeat the
    non-negotiable through the front door, with the numbers arriving inside a
    validated artifact having been typed by a model.

    **This survived its first sabotage and the survival was the decoy's fault.**
    Adding `Mapping` to the specifiable origins changed nothing, because a
    mapping has two type arguments and the element check rejects it for that
    instead. The property holds — origin *and* arity together do fail this — but
    it is guarded twice and a single edit cannot reach it.
    """
    schema = schema_of(REGISTRY.get("bounds.headroom"))

    assert "metrics" in {parameter.name for parameter in schema.bound}
    assert schema.check({"metrics": {"db.query": 42.0}}) is not None


def test_a_primitive_with_nothing_to_choose_still_has_a_valid_design() -> None:
    """An empty argument map is a design, not a failure. `bounds.headroom` is
    called entirely on what the harness measured."""
    schema = schema_of(REGISTRY.get("bounds.headroom"))

    assert schema.specifiable == ()
    assert schema.check({}) is None
    assert "takes no parameters you choose" in schema.render()


def test_the_render_names_what_the_harness_supplies_rather_than_hiding_it() -> None:
    """A model told nothing about `invoke` will invent a value for it."""
    rendered = volume_schema().render()

    assert "scales" in rendered
    assert "invoke" in rendered
    assert "not yours to name" in rendered


def test_an_unresolvable_signature_is_refused_rather_than_emptied() -> None:
    """Degrading to *nothing is specifiable* would produce an empty design that
    validates and then cannot be called."""

    def run(*, thing: NotAThing) -> None: ...  # type: ignore[name-defined] # noqa: F821

    with pytest.raises(SchemaError, match="could not be resolved"):
        schema_of(Primitive(name="fake.bad", summary="s", cost=CostClass.SECONDS, run=run))


# ============================================ AC 2: what the schema actually refuses


def test_an_unknown_parameter_is_refused_with_the_ones_that_exist() -> None:
    fault = volume_schema().check({"scales": [10, 100], "distribution": "uniform", "warmup": 3})

    assert fault is not None
    assert "no parameter 'warmup'" in fault
    assert "scales, distribution, counters" in fault


def test_a_harness_supplied_parameter_is_refused() -> None:
    """A design that set `invoke` would be a model deciding what the harness
    drives, which is the Explorer's answer and not this call's."""
    fault = volume_schema().check(
        {"scales": [10, 100], "distribution": "uniform", "invoke": "the view"}
    )

    assert fault is not None
    assert "supplied by the harness" in fault


def test_a_missing_required_parameter_is_refused() -> None:
    fault = volume_schema().check({"distribution": "uniform"})

    assert fault is not None
    assert "requires ['scales']" in fault


def test_a_string_is_not_a_list_of_strings() -> None:
    """`isinstance(value, Sequence)` is true of a string, so a permissive check
    turns `counters="queries"` into seven single-character counter names."""
    fault = volume_schema().check(
        {"scales": [10, 100], "distribution": "uniform", "counters": "db.query"}
    )

    assert fault is not None
    assert "takes a list, not str" in fault


def test_a_boolean_is_not_a_whole_number() -> None:
    """`isinstance(True, int)` is true in Python, so a permissive check accepts
    `repetitions=true` and the primitive runs one iteration."""
    fault = schema_of(REGISTRY.get("isolation.interference")).check({"repetitions": True})

    assert fault is not None
    assert "whole number" in fault


def test_a_boolean_parameter_still_takes_a_boolean() -> None:
    """The control for the check above: refusing `bool` everywhere would refuse a
    correct design for `inputs.search(guided=true)`."""
    assert schema_of(REGISTRY.get("inputs.search")).check({"label": "x", "guided": True}) is None


def test_a_whole_number_is_accepted_where_a_number_is_wanted() -> None:
    """JSON has no way to write `3.0` as distinct from `3`, so refusing an integer
    for a `float` would fail a correct design over its notation."""
    assert schema_of(REGISTRY.get("longitudinal.soak")).check({"duration": 30}) is None


def test_a_fraction_is_not_a_whole_number() -> None:
    """The other direction, which is not notation: 3.5 scale points is not a
    number of scale points."""
    fault = volume_schema().check({"scales": [10.5], "distribution": "uniform"})

    assert fault is not None
    assert "scales[0]" in fault


def test_a_value_outside_an_enum_is_refused_with_the_members() -> None:
    fault = volume_schema().check({"scales": [10, 100], "distribution": "zipf"})

    assert fault is not None
    assert "'uniform', 'power_law', 'long_tail'" in fault


def test_every_problem_is_reported_at_once() -> None:
    """A rejection costs a model call to correct, and the cascade has three
    attempts to spend — so one problem per round trip is a budget nobody has."""
    fault = volume_schema().check({"distribution": "zipf", "warmup": 3})

    assert fault is not None
    assert "warmup" in fault
    assert "zipf" in fault
    assert "requires ['scales']" in fault


def test_a_correct_design_is_accepted() -> None:
    """The control. A checker that refused everything would pass every negative
    test above while leaving the agent unable to specify anything at all."""
    assert volume_schema().check({"scales": [10, 100, 1000], "distribution": "power_law"}) is None
    assert (
        volume_schema().check(
            {"scales": [10, 100, 1000], "distribution": "uniform", "counters": ["db.query"]}
        )
        is None
    )


def test_the_schema_checks_shape_and_not_sense() -> None:
    """The stated bound, asserted so that nobody reads *validated against the
    schema* as *the experiment will run*.

    `scales=[-4]` is a perfectly well-typed `Sequence[int]` and a meaningless
    sweep. The primitive's own guard is the authority on that, and restating it
    here would be the second statement this module exists to avoid.
    """
    assert volume_schema().check({"scales": [-4], "distribution": "uniform"}) is None

    with pytest.raises(Exception, match="scale points"):
        _check_scales([-4])


# ================================== AC 1: a hypothesis becomes a concrete experiment


def test_a_well_formed_reply_becomes_a_specification() -> None:
    draft = parse(json.dumps(SPEC), primitive="scaling.volume", schema=volume_schema())

    assert draft.valid
    assert draft.spec is not None
    assert draft.spec.target == "shop.books.list"
    assert draft.spec.arguments["scales"] == [10, 100, 1000]


def test_the_primitive_comes_from_the_hypothesis_and_not_from_the_reply() -> None:
    """S-8.1 already validated the instrument against S-3.1's selection. Asking
    again would create two answers to one question with no rule for which wins."""
    reply = {**SPEC, "primitive": "ablation.stub"}

    draft = parse(json.dumps(reply), primitive="scaling.volume", schema=volume_schema())

    assert draft.spec is not None
    assert draft.spec.primitive == "scaling.volume"


def test_a_specification_wrapped_in_prose_is_still_read() -> None:
    text = f"Here is the design:\n```json\n{json.dumps(SPEC)}\n```"

    assert parse(text, primitive="scaling.volume", schema=volume_schema()).valid


def test_a_reply_that_is_not_json_is_a_rejection_rather_than_an_error() -> None:
    """The line this story draws. A malformed reply is a *wrong answer*, which is
    the retryable kind — and it is the archetypal cheap-model failure, so making
    it fatal would defeat the cascade the story exists to use."""
    draft = parse("I'd sweep the volume.", primitive="scaling.volume", schema=volume_schema())

    assert not draft.valid
    assert "no JSON object" in draft.rejection


def test_a_reply_with_no_target_is_a_rejection() -> None:
    draft = parse(
        json.dumps({"arguments": SPEC["arguments"]}),
        primitive="scaling.volume",
        schema=volume_schema(),
    )

    assert not draft.valid
    assert "target" in draft.rejection


def test_a_multi_line_target_is_refused_here_rather_than_at_the_log() -> None:
    """S-5.8 refuses a multi-line summary field. A design that reached S-8.4 and
    was refused there would fail *after* the experiment ran, with the measurement
    taken and nowhere to record it."""
    draft = parse(
        json.dumps({**SPEC, "target": "shop.books.list\nand also the detail view"}),
        primitive="scaling.volume",
        schema=volume_schema(),
    )

    assert not draft.valid
    assert "one non-empty line" in draft.rejection


def test_an_overlong_target_is_refused_here_too() -> None:
    draft = parse(
        json.dumps({**SPEC, "target": "x" * (MAX_SUMMARY_CHARS + 1)}),
        primitive="scaling.volume",
        schema=volume_schema(),
    )

    assert not draft.valid


def test_the_rendering_is_what_the_log_records() -> None:
    """Composition with S-8.4, whose `design` field says *S-8.2 produces these*.

    Asserted by actually appending it, because a rendering that the log refuses
    is one that fails after the experiment has run.
    """
    draft = parse(json.dumps(SPEC), primitive="scaling.volume", schema=volume_schema())
    assert draft.spec is not None

    log = ExperimentLog()
    experiment = log.append(
        hypothesis=HYPOTHESIS.statement,
        primitive=draft.spec.primitive,
        target=draft.spec.target,
        design=draft.spec.render(),
        measurement={"db.query": 1004.0},
        verdict=Verdict.CONFIRMED,
        outcome="queries grew linearly with volume",
    )

    assert experiment.design == draft.spec.render()
    assert "scaling.volume(" in experiment.design


def test_the_rendering_is_canonical() -> None:
    """Two runs that designed the same experiment must produce the same string,
    or S-8.4's digest reports them as different experiments."""
    one = ExperimentSpec(
        primitive="scaling.volume",
        target="t",
        arguments={"distribution": "uniform", "scales": [1, 2, 3]},
    )
    other = ExperimentSpec(
        primitive="scaling.volume",
        target="t",
        arguments={"scales": [1, 2, 3], "distribution": "uniform"},
    )

    assert one.render() == other.render()


def test_the_specification_is_frozen() -> None:
    """It is about to be written into an append-only log."""
    spec = ExperimentSpec(primitive="p", target="t", arguments={})

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        spec.target = "something else"  # type: ignore[misc]


def test_an_argument_keeps_the_json_type_it_arrived_as() -> None:
    """A boolean that became `1` on the way into the artifact would change the
    experiment silently, since the schema already accepted it as a boolean.

    **What protects this is `bool` being in `JSONValue`, not `strict=True`.** The
    sabotage pass turned strict off and nothing failed — the union covers every
    type JSON has, so there is nothing for a coercion to reach for — and the
    sabotage that does fail is removing `bool` from the union. Named for the
    property rather than for the setting, because the first version of this test
    was named for a setting that was not providing it.
    """
    spec = ExperimentSpec(primitive="p", target="t", arguments={"guided": True, "examples": 1})

    assert spec.arguments["guided"] is True
    assert spec.arguments["examples"] == 1
    assert not isinstance(spec.arguments["examples"], bool)


# ================================= AC 3: mechanical, mid tier, and a cascade that works


def test_experiment_design_is_mechanical_and_routes_to_the_mid_tier() -> None:
    """AC 3, both halves. The class is derived from §3's table rather than
    declared, so this is a check on the row rather than on this module."""
    router = Router()

    assert classify(StepType.EXPERIMENT_DESIGN) is StepClass.MECHANICAL
    assert router.tier_for(StepClass.MECHANICAL, Phase.INVESTIGATE) is Tier.MID
    assert router.route(StepType.EXPERIMENT_DESIGN, Phase.INVESTIGATE) == "claude-sonnet-5"


def test_experiment_design_is_cascade_safe_and_names_the_check_that_makes_it_so() -> None:
    """A row is only cascade-safe because of the check written against it, and a
    step whose named check turns out not to exist is a routing decision made on a
    fiction. The check here is `PrimitiveSchema.check`, which this file spends
    twenty tests on."""
    assert StepType.EXPERIMENT_DESIGN in cascadable()
    assert "schema" in STEP_KINDS[StepType.EXPERIMENT_DESIGN].mechanical_check.lower()  # type: ignore[union-attr]


def test_adding_a_mechanical_row_did_not_make_the_creative_ones_cascadable() -> None:
    """`CLAUDE.md`'s non-negotiable, re-asserted from the story that touched the
    table. Editing §3's rows is exactly how the two *none exists* entries would
    stop being *none exists* without anybody deciding to change them."""
    assert StepType.HYPOTHESIS_GENERATION not in cascadable()
    assert StepType.ATTACK_DESIGN not in cascadable()
    assert classify(StepType.HYPOTHESIS_GENERATION) is StepClass.CREATIVE
    assert classify(StepType.ATTACK_DESIGN) is StepClass.CREATIVE


def test_there_is_no_way_to_substitute_the_validator() -> None:
    """The opposite enforcement to S-8.1's, and an absence again. This step
    cascades, so the danger is a caller-supplied check that returns `True` —
    which would make the cascade decorative and let an unrunnable specification
    through wearing a validated artifact's clothes."""
    parameters = inspect.signature(design).parameters

    assert "validate" not in parameters
    assert not any("valid" in name or "check" in name for name in parameters)


def test_a_rejected_design_is_retried_with_the_rejection_fed_back() -> None:
    """**The story's finding.** §3's *2 cheap attempts, then strong* assumes the
    second attempt is a second answer, and at temperature 0 it is the same call —
    same model, same prompt, same sampling. Two identical calls are one call and a
    wasted authorization.

    Proved by replay rather than by inspection: the second recording is filed
    under a different question, so if the retry sent the first question again it
    would replay the first recording and get the same rejection.
    """
    bad = json.dumps({"target": "shop.books.list", "arguments": {"distribution": "uniform"}})
    first = question_after()
    second = question_after(rejection_for(bad))

    assert first != second
    assert "requires ['scales']" in second

    client = ReplayingClient([recording(first, bad), recording(second, json.dumps(SPEC))])

    outcome = run_design(client)

    assert isinstance(outcome.value, ExperimentSpec)
    assert not outcome.escalated
    assert len(set(client.served)) == 2


def test_a_retry_that_still_fails_escalates_to_the_dearer_model() -> None:
    """S-5.6's shape, reached through this call site: two attempts on the mid tier,
    then one rung dearer."""
    bad = json.dumps({"target": "t", "arguments": {"distribution": "uniform"}})
    worse = json.dumps({"target": "t", "arguments": {"scales": "10,100", "distribution": "zipf"}})

    first = question_after()
    second = question_after(rejection_for(bad))
    third = question_after(rejection_for(bad), rejection_for(worse))

    client = ReplayingClient(
        [
            recording(first, bad),
            recording(second, worse),
            recording(third, json.dumps(SPEC), model="claude-opus-5"),
        ]
    )

    outcome = run_design(client)

    assert outcome.escalated
    assert outcome.model == "claude-opus-5"


def test_every_attempt_failing_reports_what_each_one_got_wrong() -> None:
    """S-5.6 raises `NoDearerTierError` with the step type and the model and
    **without the results it rejected**, which for this step is the whole
    diagnosis: *the design was invalid* is not actionable."""
    bad = json.dumps({"target": "t", "arguments": {"distribution": "zipf"}})
    first = question_after()
    second = question_after(rejection_for(bad))
    third = question_after(rejection_for(bad), rejection_for(bad))

    client = ReplayingClient(
        [
            recording(first, bad),
            recording(second, bad),
            recording(third, bad, model="claude-opus-5"),
        ]
    )

    with pytest.raises(UndesignableError) as raised:
        run_design(client)

    assert len(raised.value.rejections) == 3
    assert "zipf" in str(raised.value)
    assert "requires ['scales']" in str(raised.value)


def test_a_refusal_is_raised_rather_than_retried() -> None:
    """An absent design, not a wrong one. Feeding *your previous answer was
    rejected because the model declined* back to a model is noise."""
    client = ReplayingClient([recording(question_after(), "", stop_reason="refusal")])

    with pytest.raises(DesignError, match="declined"):
        run_design(client)


def test_a_truncated_reply_is_raised_rather_than_retried() -> None:
    """Retrying under the same cap truncates at the same place."""
    question = question_after()
    client = ReplayingClient(
        [recording(question, '{"target": "shop.books', stop_reason="max_tokens")]
    )

    with pytest.raises(DesignError, match="cut off"):
        run_design(client)


def test_the_call_is_made_at_the_temperature_a_translation_calls_for() -> None:
    """Not `03-agents.md`'s 0.8: there is nothing unusual to want in *which scales
    to sweep*, and a design that varies between identical calls is one nobody can
    reproduce. Variation between attempts comes from the rejection."""
    assert DESIGN_TEMPERATURE == 0.0


def test_a_recording_made_at_the_hypothesis_temperature_does_not_answer() -> None:
    """S-8.1 put the temperature in S-0.7b's digest for this case, and this is the
    other side of it: two Diagnostician calls, one at 0.8 and one at 0.0."""
    question = question_after()
    client = ReplayingClient([recording(question, json.dumps(SPEC), temperature=0.8)])

    with pytest.raises(Exception, match=r"no recording|different temperature"):
        run_design(client)


def test_an_instrument_this_run_withheld_is_refused_where_the_reason_is_recorded() -> None:
    """A `Hypothesis` can be rebuilt from a log written in an earlier run, and
    S-3.1's selection is a snapshot of *this* one. Refusing here keeps the
    withholding reason attached; refusing at the call site loses it."""
    withheld = Withheld(
        primitive=REGISTRY.get("scaling.volume"),
        verdict=ApplicabilityVerdict(Applicability.UNSUPPORTED, "no fixture seeding here"),
    )
    selection = Selection(profile=ProjectProfile(), available=(), withheld=(withheld,))

    with pytest.raises(PrimitiveUnavailableError, match="no fixture seeding here"):
        design(
            a_session(),
            ReplayingClient([]),
            hypothesis=HYPOTHESIS,
            instruments=selection,
            source=SOURCE,
            log=a_log(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


# ============================================================= end to end, replayed


def test_a_design_comes_back_priced_and_attributed() -> None:
    """The whole call: routed to mid, authorized, replayed, schema-checked, billed."""
    client = ReplayingClient([recording(question_after(), json.dumps(SPEC))])

    outcome = design(
        a_session(),
        client,
        hypothesis=HYPOTHESIS,
        instruments=instruments("scaling.volume", "ablation.stub"),
        source=SOURCE,
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert outcome.value.primitive == "scaling.volume"
    assert outcome.value.arguments["distribution"] == "power_law"
    assert outcome.routed_model == "claude-sonnet-5"
    assert outcome.cost_usd > 0
    assert not outcome.escalated


def test_the_question_carries_the_hypothesis_the_instrument_and_the_log() -> None:
    question = question_after()

    assert HYPOTHESIS.statement in question
    assert "scales" in question
    assert "ablation.stub of shop.books.list" in question
    assert SOURCE in question


def test_the_rejections_go_last_so_the_cached_prefix_survives_a_retry() -> None:
    """`04-cost.md` §4: the stable prefix first, the varying question last. The
    rejection is the only part that differs between attempts, so anywhere but the
    end invalidates the prefix on every retry — on the one step designed to make
    three calls."""
    first = question_after()
    second = question_after("scales is missing")

    assert second.startswith(first)


def test_the_system_prompt_says_the_instrument_is_already_chosen() -> None:
    assert "already chosen" in design_module._SYSTEM


def test_a_parameter_knows_whether_it_is_the_designs_to_set() -> None:
    chosen = Parameter(name="n", required=True, describes="a whole number", annotation=int)
    supplied = Parameter(name="f", required=True, describes=None, annotation=object)

    assert chosen.specifiable
    assert not supplied.specifiable

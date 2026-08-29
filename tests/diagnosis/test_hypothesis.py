"""S-8.1 — the first model call in the system, and what it is not allowed to do.

Every test here runs against S-0.7b's replaying client, which **holds no vendor
client at all** — so *no test hits a real API* is structural rather than a rule
this file follows.

The three things worth attacking are the ones `CLAUDE.md` makes non-negotiable:
the tier this is routed to, the cascade it must never request, and whether an
answer the model invents can reach the rest of the system.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal

import pytest

import coldfix.primitives  # noqa: F401 - registers the twelve; REGISTRY is empty without it
from coldfix.cost.accounting import ExchangeRate, Phase, StepClass
from coldfix.cost.routing import Router, StepType, Tier, UnsafeRoutingError
from coldfix.cost.session import Session
from coldfix.diagnosis import hypothesis as hypothesis_module
from coldfix.diagnosis.hypothesis import (
    HYPOTHESIS_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    Hypothesis,
    HypothesisError,
    generate,
    parse,
    render_question,
)
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.registry import REGISTRY, ProjectProfile, Selection
from fixtures.requests import shaped

SYSTEM_HINT = "You are diagnosing a performance problem"

ANSWER = {
    "statement": "the author lookup is an N+1 across the book list",
    "primitive": "scaling.volume",
    "rationale": "queries have not been counted against volume yet",
}


def payload(
    text: str, *, model: str = "claude-opus-5", stop_reason: str = "end_turn"
) -> dict[str, object]:
    """A response the vendor's own model will parse, which is S-0.7b's whole rule."""
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_hypothesis",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 120,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8_000,
        },
    }


def instruments(*names: str) -> Selection:
    """A selection offering exactly these instruments and nothing else.

    Built from the **real** registry entries rather than invented ones: the
    check under test is that a proposed primitive is one this project was
    offered, and a fake primitive with a name this system does not have would
    make the check pass against a vocabulary that does not exist.
    """
    return Selection(
        profile=ProjectProfile(),
        available=tuple(REGISTRY.get(name) for name in names),
        withheld=(),
    )


def log_with_one_experiment() -> ExperimentLog:
    log = ExperimentLog()
    log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="shop.books.list",
        design="stub the serializer, compare",
        measurement={"seconds": 0.4},
        verdict=Verdict.REJECTED,
        outcome="stubbing changed nothing",
    )
    return log


def a_session() -> Session:
    """Epic 5's entry point, built the way its own composition check builds one."""
    return Session(
        # **This step's own prompt. S-17.17.** `refuse_foreign_session` rejects a
        # session whose system text is not the one these calls send, so a generic
        # string here would fail every test in the file rather than model a
        # session the campaign builds.
        system=hypothesis_module._SYSTEM,
        playbook="Django: count queries with force_debug_cursor.",
        source="def list_books(): ...",
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 15)),
    )


# ================================================= AC 1: temperature 0.8, on purpose


def test_the_call_is_made_at_the_temperature_the_design_calls_for() -> None:
    """`03-agents.md` §2.4: hypothesis generation benefits from diversity. The
    recording is made at 0.8 and nothing else will replay it."""
    assert HYPOTHESIS_TEMPERATURE == 0.8


def test_a_recording_made_at_another_temperature_does_not_answer() -> None:
    """The reason S-0.7b's digest gained the temperature in this story: S-8.3
    sends the same question about the same log at 0.0, and without it that
    recording would answer this call."""
    offered = instruments("scaling.volume", "ablation.stub")
    question = render_question(exclusions=(), instruments=offered)
    client = ReplayingClient(
        [
            Recording.of(
                model="claude-opus-5",
                system=_system_of(),
                messages=shaped(a_session(), "claude-opus-5", question),
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.0,
                response=payload(json.dumps(ANSWER)),
            )
        ]
    )

    with pytest.raises(Exception, match=r"no recording|different temperature"):
        generate(
            a_session(),
            client,
            exclusions=(),
            instruments=offered,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


# ============================ AC 4: frontier tier, and no way to ask for a cascade


def test_hypothesis_generation_is_creative_and_cannot_be_routed_cheaply() -> None:
    """`CLAUDE.md`: never cascade to a cheap model on hypothesis generation — no
    deterministic validator exists for it. S-5.5 already refuses the routing;
    this asserts the step type this module uses is the one that gets refused."""
    router = Router()

    assert router.route(StepType.HYPOTHESIS_GENERATION, Phase.INVESTIGATE)
    with pytest.raises(UnsafeRoutingError, match="creative"):
        Router(tiers={StepClass.CREATIVE: Tier.CHEAP, StepClass.MECHANICAL: Tier.CHEAP})


def test_there_is_no_way_to_request_a_cascade_from_this_call_site() -> None:
    """The enforcement is an absence. S-5.6 cascades a step only when its caller
    supplies a validator, so a signature with nowhere to pass one cannot ask for
    the thing the non-negotiable forbids.

    Asserted by inspection so it fails the moment somebody adds one.
    """
    parameters = inspect.signature(generate).parameters

    assert "validate" not in parameters
    assert not any("valid" in name for name in parameters)


# ======================================= AC 2: it receives the four things it needs


def test_the_question_carries_what_varies_and_nothing_the_session_caches() -> None:
    """**S-17.16 halved this question, and both halves are asserted.**

    What is offered and what is already ruled out change from call to call, so
    they belong in the question. The source and the log do not: they were
    rendered here *and* into the cached blocks beside this question, so every
    call sent both copies. Asserting only their absence would pass on a question
    that had lost the exclusions too, which is why what stayed is checked first.
    """
    question = render_question(
        exclusions=("not the database, queries flat 10 to 100",),
        instruments=instruments("scaling.volume", "ablation.stub"),
    )

    assert "scaling.volume" in question
    assert "ablation.stub" in question
    assert "not the database" in question

    assert "shop/views.py::book_list" not in question, "the source is the session's block now"
    assert "ablation.stub of shop.books.list" not in question, "so is the log"


def test_an_empty_log_and_no_exclusions_still_ask_a_question() -> None:
    """The first hypothesis of an investigation is asked with nothing behind it,
    and rendering that as an empty section would read as a missing input."""
    question = render_question(exclusions=(), instruments=instruments("scaling.volume"))

    assert "(none yet)" in question
    assert "What is the next hypothesis" in question


# ============================ AC 3: a structured hypothesis, checked against reality


def test_a_well_formed_reply_becomes_a_hypothesis() -> None:
    found = parse(json.dumps(ANSWER), instruments("scaling.volume", "ablation.stub"))

    assert found.primitive == "scaling.volume"
    assert found.statement.startswith("the author lookup")
    assert found.rationale


def test_a_hypothesis_wrapped_in_prose_is_still_read() -> None:
    """Models asked for JSON return JSON, a fenced block, or JSON with a sentence
    in front of it. Refusing the third would be refusing a correct answer."""
    text = f"Here is my hypothesis:\n```json\n{json.dumps(ANSWER)}\n```"

    assert parse(text, instruments("scaling.volume")).primitive == "scaling.volume"


def test_a_reply_that_is_not_json_is_refused_with_what_was_said() -> None:
    """Nothing here repairs an answer: *the model answered something else* and
    *the model was wrong* are different problems needing different fixes."""
    with pytest.raises(HypothesisError, match="no hypothesis could be read"):
        parse("I think it is probably the database.", instruments("scaling.volume"))


def test_a_hypothesis_missing_a_field_is_refused() -> None:
    with pytest.raises(HypothesisError, match="missing"):
        parse(
            json.dumps({"statement": "something", "primitive": "scaling.volume"}),
            instruments("scaling.volume"),
        )


def test_a_hypothesis_naming_an_instrument_that_was_not_offered_is_refused() -> None:
    """The check that matters. S-3.1 withholds an instrument when a project fact
    says it cannot run here, and proposing one anyway moves the failure to S-8.2
    with the reason already lost."""
    invented = {**ANSWER, "primitive": "flame_graph"}

    with pytest.raises(HypothesisError, match="not an instrument this project was offered"):
        parse(json.dumps(invented), instruments("scaling.volume", "ablation.stub"))


def test_an_offered_instrument_is_accepted() -> None:
    """The control. A check that refused every primitive would pass the test
    above and make the agent unable to propose anything at all."""
    assert parse(json.dumps(ANSWER), instruments("scaling.volume")).primitive == "scaling.volume"


# =========================================================== end to end, replayed


def test_a_hypothesis_comes_back_priced_and_attributed() -> None:
    """The whole call: routed, authorized, replayed, parsed and billed. Epic 5's
    machinery with Epic 8's first caller in front of it."""
    offered = instruments("scaling.volume", "ablation.stub")
    question = render_question(exclusions=(), instruments=offered)
    client = ReplayingClient(
        [
            Recording.of(
                model="claude-opus-5",
                system=_system_of(),
                messages=shaped(a_session(), "claude-opus-5", question),
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=HYPOTHESIS_TEMPERATURE,
                response=payload(json.dumps(ANSWER)),
            )
        ]
    )

    outcome = generate(
        a_session(),
        client,
        exclusions=(),
        instruments=offered,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert isinstance(outcome.value, Hypothesis)
    assert outcome.value.primitive == "scaling.volume"
    assert outcome.calls
    assert not outcome.escalated


def test_a_refusal_is_reported_rather_than_parsed_as_a_short_answer() -> None:
    """S-0.7b: a decline is a successful response with an **empty content list**,
    so a caller reading `text` reads emptiness as brevity."""
    offered = instruments("scaling.volume")
    question = render_question(exclusions=(), instruments=offered)
    client = ReplayingClient(
        [
            Recording.of(
                model="claude-opus-5",
                system=_system_of(),
                messages=shaped(a_session(), "claude-opus-5", question),
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=HYPOTHESIS_TEMPERATURE,
                response=payload("", stop_reason="refusal"),
            )
        ]
    )

    with pytest.raises(HypothesisError, match="declined"):
        generate(
            a_session(),
            client,
            exclusions=(),
            instruments=offered,
            measured_prefix_tokens=10,
            measured_prompt_tokens=100,
        )


def test_a_truncated_reply_is_refused_rather_than_half_parsed() -> None:
    """A truncated JSON object parses as nothing, and a hypothesis assembled from
    half a sentence is a guess about what the model was going to say."""
    offered = instruments("scaling.volume")
    question = render_question(exclusions=(), instruments=offered)
    client = ReplayingClient(
        [
            Recording.of(
                model="claude-opus-5",
                system=_system_of(),
                messages=shaped(a_session(), "claude-opus-5", question),
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=HYPOTHESIS_TEMPERATURE,
                response=payload('{"statement": "the author loo', stop_reason="max_tokens"),
            )
        ]
    )

    with pytest.raises(HypothesisError, match="cut off"):
        generate(
            a_session(),
            client,
            exclusions=(),
            instruments=offered,
            measured_prefix_tokens=10,
            measured_prompt_tokens=100,
        )


def _system_of() -> str:
    """The system prompt this module sends, read from the module rather than
    copied — a copy would drift and every recording would stop matching."""
    return hypothesis_module._SYSTEM


def test_the_system_prompt_says_what_the_agent_is_for() -> None:
    assert SYSTEM_HINT in _system_of()


def test_the_hypothesis_is_frozen_once_returned() -> None:
    """It is about to be written into an append-only log, and a record that can
    be edited after the experiment ran is a record of what somebody wishes had
    been proposed."""
    found = parse(json.dumps(ANSWER), instruments("scaling.volume"))

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        found.statement = "something else"  # type: ignore[misc]

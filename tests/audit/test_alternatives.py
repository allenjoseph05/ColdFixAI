"""S-9.5 — a different mechanism the same measurements would also produce.

The first attack in this epic that needs a model, and therefore the first whose
tests are mostly about **what the model is not allowed to get away with**.

The one that matters most is the empty answer. AC 2 turns any alternative into
`unsound`, and the amended S-9.8 routes `unsound` back to investigate — so an
auditor that cannot say *I have nothing* would guarantee an investigation never
ends. That is S-0.8's measured failure, reached through the audit instead of
through the agent.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import invocation as invocation_module
from coldfix.audit.alternatives import (
    NONE_FOUND,
    QUESTION,
    Alternative,
    AlternativeAudit,
    AlternativeError,
    attack,
    check_against_log,
    measured_pairs,
    parse,
)
from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    audit_messages,
    audit_session,
    render_evidence,
)
from coldfix.cost.accounting import ExchangeRate
from coldfix.cost.routing import STEP_KINDS, StepType
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))
SOURCE = "shop/views.py::book_list"


def a_log() -> ExperimentLog:
    """A sweep that ruled the database out, then an ablation that found a cause.

    Two experiments measuring `db.query` at **different** values, which is the
    case `measured_pairs` exists for.
    """
    log = ExperimentLog()
    log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0, "seconds": 0.4},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7",
    )
    log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        rationale="queries are flat, so the cost is above the database",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"db.query": 1004.0, "seconds": 8.24},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing removed most of the wall time",
    )
    return log


def an_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "mechanism": "the ORM is materialising the whole queryset before slicing",
        "cites": {"seconds": 8.24},
        "not_excluded_because": "the sweep ruled out query *count*, not query result size",
    }
    payload.update(overrides)
    return json.dumps(payload)


def payload(text: str) -> dict[str, object]:
    return {
        "id": "msg_alt",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 150,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 4_000,
        },
    }


def recording(reply: str) -> Recording:
    return Recording.of(
        model="claude-opus-5",
        system=invocation_module._SYSTEM,
        messages=audit_messages(render_evidence(a_log()), QUESTION),
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=AUDIT_TEMPERATURE,
        response=payload(reply),
    )


def run(reply: str) -> AlternativeAudit:
    outcome = attack(
        audit_session(rate=RATE, source=SOURCE),
        ReplayingClient([recording(reply)]),
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )
    return outcome.value


# ================== the empty answer, which is the one S-0.8 makes load-bearing


def test_no_alternative_is_a_first_class_answer() -> None:
    """**The test this story exists around.** AC 2 turns any alternative into
    `unsound` and the amended S-9.8 routes `unsound` back to investigate — so an
    auditor that cannot say *I have nothing* guarantees an investigation never
    ends, which is S-0.8's failure reached through the audit."""
    audit = run(json.dumps({"mechanism": NONE_FOUND}))

    assert audit.alternative is None
    assert not audit.unsound


def test_the_empty_answer_reads_as_the_attack_passing() -> None:
    """Not as the attack failing to run. A reader who cannot tell those apart
    will treat a clean finding as an incomplete audit."""
    described = run(json.dumps({"mechanism": NONE_FOUND})).describe()

    assert "could find no other cause" in described
    assert "this attack passing, not this attack failing to run" in described


def test_the_prompt_offers_the_empty_answer_explicitly() -> None:
    """A model that has to invent the escape hatch will not use it."""
    assert NONE_FOUND in QUESTION
    assert "that is a result, not a failure" in QUESTION
    assert "whenever you have to strain to find one" in QUESTION


def test_the_empty_answer_is_recognised_whatever_its_case() -> None:
    assert parse(json.dumps({"mechanism": "None"}), a_log()).alternative is None
    assert parse(json.dumps({"mechanism": " NONE "}), a_log()).alternative is None


# ============================ AC 1: an alternative, resting on real measurements


def test_a_well_formed_alternative_comes_back() -> None:
    audit = run(an_answer())

    assert audit.unsound
    assert audit.alternative is not None
    assert "materialising" in audit.alternative.mechanism
    assert audit.alternative.cites == {"seconds": 8.24}


def test_an_alternative_citing_a_metric_nobody_measured_is_refused() -> None:
    """*Consistent with the same measurements* means the same measurements. An
    explanation resting on numbers nobody took is a story that happens to mention
    numbers."""
    with pytest.raises(AlternativeError, match="never measured"):
        run(an_answer(cites={"cpu_seconds": 3.0}))


def test_an_alternative_misquoting_a_measured_figure_is_refused() -> None:
    with pytest.raises(AlternativeError, match="the log records"):
        run(an_answer(cites={"seconds": 99.0}))


def test_an_alternative_citing_nothing_is_refused() -> None:
    with pytest.raises(AlternativeError, match="cites no measurement"):
        run(an_answer(cites={}))


def test_a_metric_may_take_different_values_in_different_experiments() -> None:
    """**The reason this does not reuse `check_citations`.** `db.query` is 7 in
    the sweep that ruled the database out and 1004 in the ablation that found the
    cause. A checker comparing one mapping to one measurement would call the
    second of those a fabrication."""
    pairs = measured_pairs(a_log().experiments)

    assert pairs["db.query"] == {7.0, 1004.0}
    assert check_against_log({"db.query": 7.0}, a_log()) is None
    assert check_against_log({"db.query": 1004.0}, a_log()) is None
    assert check_against_log({"db.query": 500.0}, a_log()) is not None


def test_true_is_not_a_citation_of_a_measured_one() -> None:
    log = ExperimentLog()
    log.append(
        hypothesis="h",
        primitive="p",
        rationale="r",
        target="t",
        design="d",
        measurement={"errors": 1.0},
        verdict=Verdict.REJECTED,
        outcome="o",
    )

    fault = check_against_log({"errors": True}, log)

    assert fault is not None
    assert "not a number" in fault


# ========== AC 2: "not excluded" is argued by the auditor, not assumed by the code


def test_an_alternative_that_does_not_say_why_it_was_missed_is_refused() -> None:
    """**AC 2 says *if one exists and was not excluded*.** Deciding whether
    exclusion X covers alternative Y is the semantic judgement this whole story
    exists because code cannot make — so the auditor argues it, and *there might
    be another explanation* with no account of why the experiments missed it is
    not an objection anybody can act on."""
    with pytest.raises(AlternativeError, match="which rejection fails to cover it"):
        run(an_answer(not_excluded_because=""))


def test_the_argument_reaches_the_report() -> None:
    described = run(an_answer()).describe()

    assert "not ruled out because" in described
    assert "query *count*, not query result size" in described


def test_the_auditor_sees_what_was_rejected_so_it_can_argue_about_it() -> None:
    """S-9.1's evidence carries the verdicts, which is what makes AC 2's *was not
    excluded* answerable at all — an auditor shown only the measurements could
    not know what had already been ruled out."""
    evidence = render_evidence(a_log())

    assert "the database is the bottleneck" in evidence
    assert "verdict: rejected" in evidence


# ================================================= malformed answers are absent ones


def test_a_reply_naming_no_mechanism_is_refused() -> None:
    with pytest.raises(AlternativeError, match="names no mechanism"):
        run(json.dumps({"cites": {"seconds": 8.24}}))


def test_a_reply_that_is_not_json_is_refused() -> None:
    with pytest.raises(AlternativeError, match="no JSON object"):
        run("I think it could be the ORM.")


def test_cites_that_are_not_an_object_are_refused() -> None:
    with pytest.raises(AlternativeError, match="must be an object"):
        run(an_answer(cites=["seconds"]))


def test_a_malformed_answer_raises_because_this_step_cannot_cascade() -> None:
    """`04-cost.md` §3 records that no deterministic validator exists for attack
    design, so there is no cheap retry to fall back on and a malformed answer is
    an absent one rather than a wrong one."""
    assert STEP_KINDS[StepType.ATTACK_DESIGN].mechanical_check is None


# ================================================= it goes through S-9.1's isolation


def test_the_attack_runs_through_the_isolated_invocation() -> None:
    """The isolation is S-9.1's and is not re-implemented here: a fresh message
    list, the auditor's own session, and evidence with the Diagnostician's
    reasoning removed."""
    audit_run = attack(
        audit_session(rate=RATE, source=SOURCE),
        ReplayingClient([recording(an_answer())]),
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert audit_run.value.unsound
    assert audit_run.cost_usd > 0


def test_the_reasoning_behind_the_instrument_choice_never_reaches_this_attack() -> None:
    """S-9.1 strips it, and this asserts the property survives at the point the
    alternative attack actually sends its request."""
    sent = str(audit_messages(render_evidence(a_log()), QUESTION)[0]["content"])

    assert "queries are flat, so the cost is above the database" not in sent
    assert "stubbing removed most of the wall time" not in sent
    assert "db.query=1004.0" in sent


def test_an_alternative_is_frozen_once_returned() -> None:
    alternative = Alternative(mechanism="m", cites={"seconds": 8.24}, not_excluded_because="b")

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        alternative.mechanism = "something else"  # type: ignore[misc]

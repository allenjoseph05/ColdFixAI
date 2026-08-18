"""S-8.3 — reading a result, and refusing a verdict with nothing under it.

AC 3 is the one worth attacking, because **its obvious test is worthless**. Two
`interpret()` calls against a replaying client agree because the recording made
them agree — that is a property of S-0.7b's cache, not of this module, and it
would pass against code that rendered the measurement in a different order every
time. So determinism is tested where it can actually fail: identical inputs must
produce an identical **request**, including across a process boundary, where
hash-order randomization is free to move things.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from datetime import date
from decimal import Decimal

import pytest

from coldfix.cost.accounting import ExchangeRate, Phase, StepClass
from coldfix.cost.cascade import cascadable
from coldfix.cost.pruning import MAX_SUMMARY_CHARS
from coldfix.cost.routing import STEP_KINDS, Router, StepType, Tier, classify
from coldfix.cost.session import Session, StepOutcome
from coldfix.diagnosis import interpretation as interpretation_module
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.interpretation import (
    INTERPRETATION_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    Interpretation,
    InterpretationError,
    UninterpretableError,
    check_citations,
    interpret,
    parse,
    render_measurement,
    render_question,
)
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient, request_digest

HYPOTHESIS = Hypothesis(
    statement="the author lookup is an N+1 across the book list",
    primitive="scaling.volume",
    rationale="queries have not been counted against volume yet",
)

SPEC = ExperimentSpec(
    primitive="scaling.volume",
    target="shop.books.list",
    arguments={"scales": [10, 100, 1000], "distribution": "power_law"},
)

MEASUREMENT = {"db.query": 1004.0, "seconds": 8.24, "rows": 1000.0}

ANSWER = {
    "verdict": "confirmed",
    "outcome": "queries rose 14 to 1004 across a 100x volume sweep",
    "cites": {"db.query": 1004.0},
}

SOURCE = "shop/views.py::book_list"


# ------------------------------------------------------------------------ helpers


def payload(
    text: str, *, model: str = "claude-sonnet-5", stop_reason: str = "end_turn"
) -> dict[str, object]:
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_interpret",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 70,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 8_000,
        },
    }


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


def question_after(*rejections: str) -> str:
    return render_question(
        hypothesis=HYPOTHESIS,
        spec=SPEC,
        measurement=MEASUREMENT,
        log=a_log(),
        rejections=rejections,
    )


def rejection_for(reply: str) -> str:
    return parse(reply, MEASUREMENT).rejection


def recording(
    question: str,
    reply: str,
    *,
    model: str = "claude-sonnet-5",
    temperature: float = INTERPRETATION_TEMPERATURE,
    stop_reason: str = "end_turn",
) -> Recording:
    return Recording.of(
        model=model,
        system=interpretation_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=temperature,
        response=payload(reply, model=model, stop_reason=stop_reason),
    )


def run_interpret(client: ReplayingClient) -> StepOutcome[Interpretation]:
    return interpret(
        a_session(),
        client,
        hypothesis=HYPOTHESIS,
        spec=SPEC,
        measurement=MEASUREMENT,
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )


# ============================================== AC 1: a separate call at 0.0


def test_the_call_is_made_at_the_temperature_that_must_not_vary() -> None:
    """`03-agents.md` §2.4: 8.24 seconds means the same thing every time."""
    assert INTERPRETATION_TEMPERATURE == 0.0


def test_a_recording_made_at_the_hypothesis_temperature_does_not_answer() -> None:
    """**This is the call S-8.1's digest change was made for.** The two
    Diagnostician calls go out at 0.8 and 0.0 over the same log, so without the
    temperature in the request's identity the recording for the call that may
    vary would answer the call that must not."""
    client = ReplayingClient([recording(question_after(), json.dumps(ANSWER), temperature=0.8)])

    with pytest.raises(Exception, match=r"no recording|different temperature"):
        run_interpret(client)


# ================== AC 3: identical inputs, identical request — where it can fail


def test_the_measurement_renders_in_a_fixed_order_whatever_order_it_was_built_in() -> None:
    """**The property AC 3 actually rests on.** A `Mapping` iterates in insertion
    order, so the same measurement assembled by two code paths would otherwise
    render as two different prompts — and two different prompts are two questions
    a model at temperature 0 is entitled to answer differently."""
    one = {"db.query": 1004.0, "seconds": 8.24, "rows": 1000.0}
    other = {"rows": 1000.0, "db.query": 1004.0, "seconds": 8.24}

    assert list(one) != list(other)
    assert render_measurement(one) == render_measurement(other)


def test_identical_inputs_produce_a_byte_identical_question() -> None:
    """The whole question, not just the measurement block: a difference anywhere
    in it is a different request."""
    first = render_question(
        hypothesis=HYPOTHESIS,
        spec=SPEC,
        measurement={"db.query": 1004.0, "seconds": 8.24, "rows": 1000.0},
        log=a_log(),
    )
    second = render_question(
        hypothesis=HYPOTHESIS,
        spec=ExperimentSpec(
            primitive="scaling.volume",
            target="shop.books.list",
            arguments={"distribution": "power_law", "scales": [10, 100, 1000]},
        ),
        measurement={"rows": 1000.0, "seconds": 8.24, "db.query": 1004.0},
        log=a_log(),
    )

    assert first == second


def test_identical_inputs_produce_the_same_request_in_a_second_process() -> None:
    """S-8.4's construction, for the same reason: a guarantee about identity is a
    guarantee that **another process agrees**, and hash-order randomization only
    has room to move across a process boundary.

    Compares the request digest rather than the question, because the digest is
    what actually decides whether two calls are the same call.
    """
    program = (
        "import json;"
        "from coldfix.diagnosis.interpretation import render_question, "
        "INTERPRETATION_TEMPERATURE, MAX_OUTPUT_TOKENS, _SYSTEM;"
        "from coldfix.diagnosis.design import ExperimentSpec;"
        "from coldfix.diagnosis.hypothesis import Hypothesis;"
        "from coldfix.diagnosis.log import ExperimentLog, Verdict;"
        "from coldfix.llm.client import request_digest;"
        "log = ExperimentLog();"
        "log.append(hypothesis='the serializer dominates', primitive='ablation.stub',"
        " target='shop.books.list',"
        " design='ablation.stub(attribute=\"to_representation\") on shop.books.list',"
        " measurement={'seconds': 0.4}, verdict=Verdict.REJECTED,"
        " outcome='stubbing changed nothing');"
        "spec = ExperimentSpec(primitive='scaling.volume', target='shop.books.list',"
        " arguments={'scales': [10, 100, 1000], 'distribution': 'power_law'});"
        "h = Hypothesis(statement='the author lookup is an N+1 across the book list',"
        " primitive='scaling.volume',"
        " rationale='queries have not been counted against volume yet');"
        "q = render_question(hypothesis=h, spec=spec,"
        " measurement={'rows': 1000.0, 'db.query': 1004.0, 'seconds': 8.24}, log=log);"
        "print(request_digest(model='claude-sonnet-5', system=_SYSTEM,"
        " messages=[{'role': 'user', 'content': q}], max_tokens=MAX_OUTPUT_TOKENS,"
        " temperature=INTERPRETATION_TEMPERATURE))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=300, check=True
    )

    here = request_digest(
        model="claude-sonnet-5",
        system=interpretation_module._SYSTEM,
        messages=[{"role": "user", "content": question_after()}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=INTERPRETATION_TEMPERATURE,
    )

    assert result.stdout.strip() == here


def test_repeated_calls_on_identical_inputs_reach_the_same_recording() -> None:
    """AC 3 end to end — and **the digest is the assertion, not the verdict.**

    Two replays of one recording return the same verdict whatever this module
    does with its prompt, so agreeing verdicts prove nothing on their own. What
    proves the property is that both calls resolved to the *same* recording,
    which means both built the same request.
    """
    client = ReplayingClient([recording(question_after(), json.dumps(ANSWER))])

    first = run_interpret(client)
    second = run_interpret(client)

    assert first.value.verdict is second.value.verdict
    assert len(client.served) == 2
    assert len(set(client.served)) == 1


# =================== AC 2: verdict plus the measurement, and nothing fabricated


def test_a_well_formed_reply_becomes_a_verdict_with_its_measurement() -> None:
    attempt = parse(json.dumps(ANSWER), MEASUREMENT)

    assert attempt.valid
    assert attempt.value is not None
    assert attempt.value.verdict is Verdict.CONFIRMED
    assert attempt.value.measurement == MEASUREMENT
    assert attempt.value.cites == {"db.query": 1004.0}


@pytest.mark.parametrize("named", ["confirmed", "narrowed", "rejected"])
def test_all_three_verdicts_are_readable(named: str) -> None:
    """The control on the verdict check: a parser that accepted only one of them
    would pass every negative test here while making two thirds of the loop
    unreachable — and `NARROWED` is the one S-8.4 kept separate on purpose."""
    reply = {**ANSWER, "verdict": named}

    attempt = parse(json.dumps(reply), MEASUREMENT)

    assert attempt.value is not None
    assert attempt.value.verdict.value == named


def test_a_verdict_that_is_not_one_of_the_three_is_refused() -> None:
    attempt = parse(json.dumps({**ANSWER, "verdict": "inconclusive"}), MEASUREMENT)

    assert not attempt.valid
    assert "is not a verdict" in attempt.rejection


def test_a_verdict_citing_a_metric_nobody_measured_is_refused() -> None:
    """The check that makes this step cascade-safe, and the first non-negotiable
    made mechanical: a conclusion resting on a number the harness never took is
    exactly *a conclusion drawn from reading code*."""
    reply = {**ANSWER, "cites": {"cpu_seconds": 7.1}}

    attempt = parse(json.dumps(reply), MEASUREMENT)

    assert not attempt.valid
    assert "was not measured" in attempt.rejection
    assert "db.query" in attempt.rejection


def test_a_verdict_misquoting_a_measured_figure_is_refused() -> None:
    """The sharper half. The metric exists, so a check that only tested key
    membership would pass this — and *confirmed, queries grew to 40000* against a
    measured 1004 is the failure the check is for."""
    reply = {**ANSWER, "cites": {"db.query": 40000.0}}

    attempt = parse(json.dumps(reply), MEASUREMENT)

    assert not attempt.valid
    assert "measured as 1004.0 and cited as 40000.0" in attempt.rejection


def test_a_verdict_citing_nothing_at_all_is_refused() -> None:
    attempt = parse(json.dumps({**ANSWER, "cites": {}}), MEASUREMENT)

    assert not attempt.valid
    assert "cites no measurement" in attempt.rejection


def test_a_cited_figure_written_as_a_whole_number_is_accepted() -> None:
    """JSON has no way to write `1000.0` as distinct from `1000`, and refusing a
    correct citation over its notation would spend an escalation on nothing —
    S-8.2's argument, one module across."""
    reply = {**ANSWER, "cites": {"rows": 1000}}

    assert parse(json.dumps(reply), MEASUREMENT).valid


def test_true_is_not_a_citation_of_a_measured_one() -> None:
    """`True == 1` in Python, so a permissive numeric check accepts `true` as a
    citation of a metric measured as 1.0 — and the verdict then rests on a
    boolean the harness never recorded."""
    fault = check_citations({"errors": True}, {"errors": 1.0})

    assert fault is not None
    assert "not a number" in fault


def test_a_quoted_number_is_not_a_number() -> None:
    """Checked on the raw reply before pydantic sees it, because lax coercion
    would turn `"1004"` into a float and the citation would pass as exact."""
    fault = check_citations({"db.query": "1004.0"}, MEASUREMENT)

    assert fault is not None
    assert "not a number" in fault


def test_every_bad_citation_is_reported_at_once() -> None:
    """A rejection costs a model call to correct and the cascade has three
    attempts to spend.

    **Asserted on text unique to the second problem.** The first version checked
    for `"db.query"` and passed against a first-problem-only implementation,
    because the *unmeasured metric* message lists everything that was measured —
    and `db.query` is in that list. An assertion that matches a different part of
    the same output is not an assertion about the part it names.
    """
    fault = check_citations({"cpu": 1.0, "db.query": 3.0}, MEASUREMENT)

    assert fault is not None
    assert "'cpu' was not measured" in fault
    assert "db.query was measured as 1004.0 and cited as 3.0" in fault


def test_a_correct_citation_is_accepted() -> None:
    """The control. A check that refused everything would pass every negative
    test above and leave the agent unable to conclude anything at all."""
    assert check_citations({"db.query": 1004.0, "seconds": 8.24}, MEASUREMENT) is None


def test_the_model_cannot_supply_the_measurement() -> None:
    """`CLAUDE.md`: agents reason about measurements the harness took.

    The reply carries a `measurement` key and it is never read — the attached
    mapping is the one `parse` was handed, so there is no path from an answer to
    that field.
    """
    reply = {**ANSWER, "measurement": {"db.query": 1.0, "invented": 99.0}}

    attempt = parse(json.dumps(reply), MEASUREMENT)

    assert attempt.value is not None
    assert attempt.value.measurement == MEASUREMENT
    assert "invented" not in attempt.value.measurement


def test_an_interpretation_cannot_be_built_around_the_parser() -> None:
    """*Enforced by schema, not by prompt* — so the check lives on the artifact
    too, and not only in the one function that happens to call it today."""
    with pytest.raises(Exception, match=r"was not measured|validation error"):
        Interpretation(
            verdict=Verdict.CONFIRMED,
            outcome="queries exploded",
            cites={"invented": 5.0},
            measurement=MEASUREMENT,
        )


def test_a_multi_line_outcome_is_refused_here_rather_than_at_the_log() -> None:
    """S-5.8 refuses a multi-line summary, and a verdict refused there would fail
    after the experiment had already run."""
    attempt = parse(
        json.dumps({**ANSWER, "outcome": "queries rose\nand rows did too"}), MEASUREMENT
    )

    assert not attempt.valid


def test_an_overlong_outcome_is_refused_here_too() -> None:
    reply = {**ANSWER, "outcome": "x" * (MAX_SUMMARY_CHARS + 1)}

    assert not parse(json.dumps(reply), MEASUREMENT).valid


def test_the_interpretation_is_frozen() -> None:
    attempt = parse(json.dumps(ANSWER), MEASUREMENT)
    assert attempt.value is not None

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        attempt.value.verdict = Verdict.REJECTED  # type: ignore[misc]


def test_the_reading_is_what_the_log_records() -> None:
    """Composition with S-8.4: every field this produces is one `append` wants,
    asserted by actually appending, because a reading the log refuses is one that
    fails after the experiment has run."""
    attempt = parse(json.dumps(ANSWER), MEASUREMENT)
    assert attempt.value is not None
    reading = attempt.value

    experiment = a_log().append(
        hypothesis=HYPOTHESIS.statement,
        primitive=SPEC.primitive,
        target=SPEC.target,
        design=SPEC.render(),
        measurement=reading.measurement,
        verdict=reading.verdict,
        outcome=reading.outcome,
    )

    assert experiment.verdict is Verdict.CONFIRMED
    assert experiment.measurement == MEASUREMENT


# ======================== AC 1 again: mechanical, mid tier, and a working cascade


def test_result_interpretation_is_mechanical_and_routes_to_the_mid_tier() -> None:
    """`04-cost.md` §2 lists *interpret a growth table* as mechanical at ~40
    calls/run — the most frequent step the Diagnostician takes — and §3 had no
    row for it, so until this story the largest mechanical workload in the system
    could not be routed away from the frontier."""
    router = Router()

    assert classify(StepType.RESULT_INTERPRETATION) is StepClass.MECHANICAL
    assert router.route(StepType.RESULT_INTERPRETATION, Phase.INVESTIGATE) == "claude-sonnet-5"
    assert router.tier_for(StepClass.MECHANICAL, Phase.INVESTIGATE) is Tier.MID


def test_it_is_cascade_safe_and_names_the_check_that_makes_it_so() -> None:
    check = STEP_KINDS[StepType.RESULT_INTERPRETATION].mechanical_check

    assert StepType.RESULT_INTERPRETATION in cascadable()
    assert check is not None
    assert "cites" in check


def test_a_second_added_row_still_did_not_make_the_creative_ones_cascadable() -> None:
    """`CLAUDE.md`'s non-negotiable, re-asserted from the second story to edit
    §3's table. Two additions is exactly when the two *none exists* rows would
    stop being *none exists* without anybody deciding to change them."""
    assert StepType.HYPOTHESIS_GENERATION not in cascadable()
    assert StepType.ATTACK_DESIGN not in cascadable()
    assert classify(StepType.HYPOTHESIS_GENERATION) is StepClass.CREATIVE
    assert classify(StepType.ATTACK_DESIGN) is StepClass.CREATIVE


def test_there_is_no_way_to_substitute_the_validator() -> None:
    """This step cascades, so the danger is a caller supplying a check that
    accepts anything — which would let a fabricated figure through inside a
    validated artifact."""
    parameters = inspect.signature(interpret).parameters

    assert "validate" not in parameters
    assert not any("valid" in name or "check" in name for name in parameters)


def test_a_fabricated_citation_is_retried_with_the_rejection_fed_back() -> None:
    """ADR 085's finding applied here: at temperature 0 a bare retry is the same
    call, so the correction has to come from the question rather than the
    sampler. Proved by replay — the second recording is filed under a different
    question, so a retry that resent the first would replay the first."""
    bad = json.dumps({**ANSWER, "cites": {"cpu_seconds": 7.1}})
    first = question_after()
    second = question_after(rejection_for(bad))

    assert first != second
    assert "was not measured" in second

    client = ReplayingClient([recording(first, bad), recording(second, json.dumps(ANSWER))])

    outcome = run_interpret(client)

    assert outcome.value.verdict is Verdict.CONFIRMED
    assert not outcome.escalated
    assert len(set(client.served)) == 2


def test_a_retry_that_still_fabricates_escalates_to_the_dearer_model() -> None:
    bad = json.dumps({**ANSWER, "cites": {"cpu_seconds": 7.1}})
    worse = json.dumps({**ANSWER, "cites": {"db.query": 40000.0}})
    first = question_after()
    second = question_after(rejection_for(bad))
    third = question_after(rejection_for(bad), rejection_for(worse))

    client = ReplayingClient(
        [
            recording(first, bad),
            recording(second, worse),
            recording(third, json.dumps(ANSWER), model="claude-opus-5"),
        ]
    )

    outcome = run_interpret(client)

    assert outcome.escalated
    assert outcome.model == "claude-opus-5"


def test_every_attempt_failing_reports_what_each_one_cited() -> None:
    bad = json.dumps({**ANSWER, "cites": {"cpu_seconds": 7.1}})
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

    with pytest.raises(UninterpretableError) as raised:
        run_interpret(client)

    assert len(raised.value.rejections) == 3
    assert "cpu_seconds" in str(raised.value)


def test_a_refusal_is_raised_rather_than_retried() -> None:
    client = ReplayingClient([recording(question_after(), "", stop_reason="refusal")])

    with pytest.raises(InterpretationError, match="declined"):
        run_interpret(client)


def test_a_truncated_reply_is_raised_rather_than_retried() -> None:
    client = ReplayingClient(
        [recording(question_after(), '{"verdict": "confi', stop_reason="max_tokens")]
    )

    with pytest.raises(InterpretationError, match="cut off"):
        run_interpret(client)


# ============================================================= end to end, replayed


def test_a_verdict_comes_back_priced_and_attributed() -> None:
    client = ReplayingClient([recording(question_after(), json.dumps(ANSWER))])

    outcome = run_interpret(client)

    assert outcome.value.verdict is Verdict.CONFIRMED
    assert outcome.value.measurement == MEASUREMENT
    assert outcome.routed_model == "claude-sonnet-5"
    assert outcome.cost_usd > 0


def test_the_question_carries_what_was_believed_run_and_measured() -> None:
    question = question_after()

    assert HYPOTHESIS.statement in question
    assert "scaling.volume(" in question
    assert "db.query = 1004.0" in question
    assert "ablation.stub of shop.books.list" in question


def test_the_rejections_go_last_so_the_cached_prefix_survives_a_retry() -> None:
    assert question_after("db.query was not measured").startswith(question_after())


def test_an_empty_measurement_renders_as_such_rather_than_as_nothing() -> None:
    """An experiment that measured nothing is a failed experiment, not a silent
    one — and a blank block reads as a missing input rather than an empty result."""
    assert "nothing was measured" in render_measurement({})


def test_the_system_prompt_says_the_measurement_is_not_the_models() -> None:
    assert "not yours to change" in interpretation_module._SYSTEM

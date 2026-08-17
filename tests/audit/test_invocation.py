"""S-9.1 — handing a diagnosis to something that did not produce it.

The whole story is one property: **the auditor cannot see the Diagnostician's
reasoning**, and `CLAUDE.md` requires that to hold because of how the list is
built rather than because of what the prompt asks for. So every test here
attempts to get reasoning across and asserts it does not arrive.

The sharp one is `rationale`. S-8.7 put the Diagnostician's justification for
each instrument choice **into the log**, and AC 1 says the auditor receives the
raw log — so satisfying AC 1 naively breaks AC 2. `08-audit.md` decides it:
isolation *removes the explicit rationalization, which is the documented risk —
72% of reward-hacking episodes carry explicit justifying reasoning.*
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import invocation as invocation_module
from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    RESIDUE,
    WITHHELD,
    AuditError,
    audit_messages,
    audit_session,
    invoke,
    refuse_shared_session,
    render_evidence,
)
from coldfix.cost.accounting import ExchangeRate, Phase
from coldfix.cost.budget import PHASE_CAPS
from coldfix.cost.routing import STEP_KINDS, StepType
from coldfix.cost.session import Session
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient

RATIONALE = "queries have not been counted against volume yet, so the database is untested"
OUTCOME = "queries flat at 7, 7, 7 — this is clearly not the database"

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))
SOURCE = "shop/views.py::book_list"
QUESTION = "What is wrong with this diagnosis?"


def a_log() -> ExperimentLog:
    log = ExperimentLog()
    log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale=RATIONALE,
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000], distribution='uniform')",
        measurement={"db.query": 7.0, "seconds": 8.24},
        verdict=Verdict.REJECTED,
        outcome=OUTCOME,
    )
    return log


def a_session() -> Session:
    return audit_session(rate=RATE, source=SOURCE)


def payload(text: str, *, stop_reason: str = "end_turn") -> dict[str, object]:
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_audit",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 4_000,
        },
    }


def recording(reply: str, *, stop_reason: str = "end_turn") -> Recording:
    return Recording.of(
        model="claude-opus-5",
        system=invocation_module._SYSTEM,
        messages=audit_messages(render_evidence(a_log()), QUESTION),
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=AUDIT_TEMPERATURE,
        response=payload(reply, stop_reason=stop_reason),
    )


# ================ AC 1 and 2: the raw log, minus the explicit rationalization


def test_the_auditor_never_sees_why_the_instrument_was_chosen() -> None:
    """**The contradiction between AC 1 and AC 2, resolved.** S-8.7 put the
    Diagnostician's justification into the log and AC 1 says hand over the log,
    so satisfying AC 1 verbatim breaks AC 2. `08-audit.md` decides which way:
    the explicit rationalization is the documented risk."""
    rendered = render_evidence(a_log())

    assert RATIONALE not in rendered
    assert "untested" not in rendered


def test_the_auditor_never_sees_the_diagnosticians_gloss_on_its_own_result() -> None:
    """`outcome` is the agent's one-line interpretation of its own measurement —
    *this is clearly not the database* — and it is prose, not a classification."""
    rendered = render_evidence(a_log())

    assert OUTCOME not in rendered
    assert "clearly" not in rendered


def test_what_was_tested_run_and_measured_does_arrive() -> None:
    """**The control**, and it is what stops the story being satisfied by sending
    nothing. An auditor with no measurements cannot object to anything, and
    `08-audit.md` asks for *the measurements before someone else's interpretation
    of them*."""
    rendered = render_evidence(a_log())

    assert "the database is the bottleneck" in rendered
    assert "scaling.volume" in rendered
    assert "shop.books.list" in rendered
    assert "db.query=7.0" in rendered
    assert "scales=[10, 100, 1000]" in rendered


def test_the_verdict_arrives_and_that_is_a_decision() -> None:
    """Kept deliberately: a verdict is a three-valued classification S-8.3 ties to
    cited measurements, not prose, and an auditor asked whether an exclusion was
    adequate has to know something was excluded."""
    assert "verdict: rejected" in render_evidence(a_log())


def test_the_withheld_fields_are_named_as_data_rather_than_buried() -> None:
    """So a test can assert the list, and so adding a third is a line rather than
    an edit inside a loop."""
    assert WITHHELD == ("rationale", "outcome")


def test_an_empty_log_says_there_is_nothing_to_audit() -> None:
    """Rendering nothing would read as an audit of a diagnosis with no evidence,
    which is a different and much more alarming thing than no diagnosis."""
    assert "nothing here to audit" in render_evidence(ExperimentLog())


# ========================= AC 2: the message list is fresh, structurally


def test_the_message_list_is_built_here_and_shared_with_nothing() -> None:
    """`CLAUDE.md`: enforced by constructing a fresh message list, not by
    instructing the model to ignore it. Two calls must not hand back the same
    object, or a caller mutating one reaches the next audit."""
    first = audit_messages("evidence", "question")
    second = audit_messages("evidence", "question")

    assert first == second
    assert first is not second

    first.append({"role": "assistant", "content": "the Diagnostician's turn"})

    assert len(audit_messages("evidence", "question")) == 1


def test_there_is_nowhere_to_pass_a_prior_conversation() -> None:
    """The absence *is* the enforcement — S-8.1's construction. A caller holding
    the Diagnostician's history cannot supply it, because no parameter takes
    one."""
    parameters = inspect.signature(invoke).parameters

    assert not {"messages", "history", "conversation", "transcript"} & set(parameters)


def test_there_is_nowhere_to_pass_the_assembled_chain() -> None:
    """AC 1 says the raw log *rather than* the evidence chain, and the way that
    holds is that `invoke` cannot be given one."""
    parameters = inspect.signature(invoke).parameters

    assert "chain" not in parameters
    assert not any("chain" in name for name in parameters)


# ============== the session is the auditor's, because a shared one leaks silently


def test_an_audit_session_carries_the_auditors_own_prompt() -> None:
    session = a_session()

    assert session.system == invocation_module._SYSTEM
    assert "auditing a performance diagnosis" in session.system


def test_the_audit_session_has_no_playbook_to_inherit() -> None:
    """A playbook is accumulated advice about how to investigate. An auditor
    reasoning from the investigator's habits is inheriting exactly the frame
    `08-audit.md` says this cannot remove and should not add to."""
    assert "none" in a_session().playbook.lower()


def test_running_an_audit_through_the_diagnosticians_session_is_refused() -> None:
    """**The leak the message list cannot close.** `Session` caches one assembled
    prompt per model carrying its owner's system text, so an audit billed through
    the Diagnostician's session inherits all of it — isolation undone by the
    object it was billed through, while every message list stayed clean."""
    diagnostician = Session(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries with force_debug_cursor.",
        source=SOURCE,
        rate=RATE,
    )

    with pytest.raises(AuditError, match="not the auditor's"):
        refuse_shared_session(diagnostician)


def test_the_auditors_own_session_is_accepted() -> None:
    """The control. A check that refused every session would pass the test above
    and make the audit impossible to run at all."""
    refuse_shared_session(a_session())  # must not raise


def test_invoke_refuses_a_shared_session_before_it_spends_anything() -> None:
    diagnostician = Session(system="D", playbook="p", source=SOURCE, rate=RATE)

    with pytest.raises(AuditError, match="not the auditor's"):
        invoke(
            diagnostician,
            ReplayingClient([]),
            log=a_log(),
            question=QUESTION,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )

    assert diagnostician.budget.used(Phase.FINDING_AUDIT) == 0


# ================================ AC 3: routing, and the vendor that is configured


def test_an_audit_is_attack_design_so_it_can_never_cascade() -> None:
    """`CLAUDE.md`: never cascade to a cheap model on attack design — no
    deterministic validator exists for it, and there is none for *is this
    diagnosis sound* either. Reusing §3's existing row rather than adding one:
    an audit **is** an attack, on the diagnosis instead of on a patch."""
    assert STEP_KINDS[StepType.ATTACK_DESIGN].mechanical_check is None
    assert "validate" not in inspect.signature(invoke).parameters


def test_the_audit_is_billed_to_the_finding_audit_phase() -> None:
    """Its own cap and its own disposition — S-5.4 gives finding audit two rounds
    and escalates rather than halting."""
    assert PHASE_CAPS[Phase.FINDING_AUDIT].limit == 2


# =========================================================== end to end, replayed


def test_an_objection_comes_back_priced_and_attributed() -> None:
    client = ReplayingClient([recording("The 1000-row sweep is too narrow to separate...")])

    outcome = invoke(
        a_session(),
        client,
        log=a_log(),
        question=QUESTION,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert "too narrow" in outcome.value
    assert outcome.cost_usd > 0
    assert not outcome.escalated


def test_a_refusal_is_not_read_as_an_audit_that_found_nothing() -> None:
    """The reading that would let a decline pass as a pass."""
    client = ReplayingClient([recording("", stop_reason="refusal")])

    with pytest.raises(AuditError, match="declined"):
        invoke(
            a_session(),
            client,
            log=a_log(),
            question=QUESTION,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_a_truncated_objection_is_refused_rather_than_half_read() -> None:
    client = ReplayingClient([recording("The sweep is too na", stop_reason="max_tokens")])

    with pytest.raises(AuditError, match="cut off"):
        invoke(
            a_session(),
            client,
            log=a_log(),
            question=QUESTION,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_request_carries_no_rationale_even_end_to_end(  # the property, at the wire
) -> None:
    """Asserted against the **recorded request** rather than against
    `render_evidence`, because what matters is what was sent. A recording is found
    by hashing the request, so a request carrying the rationale would not match
    this recording at all — and that is the point.
    """
    client = ReplayingClient([recording("an objection")])
    messages = audit_messages(render_evidence(a_log()), QUESTION)
    sent = str(messages[0]["content"])

    assert RATIONALE not in sent
    assert OUTCOME not in sent
    assert "db.query=7.0" in sent

    invoke(
        a_session(),
        client,
        log=a_log(),
        question=QUESTION,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )
    assert len(client.served) == 1


# =========================================== the bound, carried rather than implied


def test_the_isolation_is_described_as_partial() -> None:
    """`08-audit.md`'s honest position: it removes the explicit rationalization
    and does **not** remove framing bias. S-7.12's `Anchor.residue` construction —
    a bound nobody can read is one somebody will quote past."""
    assert "partial" in RESIDUE
    assert "framing bias" in RESIDUE
    assert "72%" in RESIDUE
    assert "not describe this as clean separation" in RESIDUE.lower()

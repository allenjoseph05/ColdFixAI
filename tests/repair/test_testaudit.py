"""S-10.3 — attacking the test before the patch it will judge exists.

`08-audit.md` §3.3: *the test is written by the agent that then writes the patch.
The Surgeon can write a weak test to make its own life easier.*

The audit's own failure modes mirror the flaw it exists to catch. An auditor that
objects to everything sends every test back to be rewritten for ever — S-0.8's
non-termination, one epic over. An auditor that "strengthens" by loosening the
threshold has produced a round of work and less coverage than before. Both are
tested, and the second is the one nobody would notice.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from coldfix.audit.invocation import AUDIT_TEMPERATURE, AuditError, audit_session
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent, ExchangeRate, ModelCall, Phase, StepClass, TokenUsage
from coldfix.cost.budget import (
    PHASE_CAPS,
    BudgetExhaustedError,
    Disposition,
    ProgressStalledError,
)
from coldfix.cost.routing import STEP_KINDS, StepType
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.scaling import Distribution
from coldfix.repair import testaudit as testaudit_module
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.testaudit import (
    QUESTION,
    SYSTEM,
    TEST_AUDIT_CALL_CEILING,
    TestAudit,
    TestAuditError,
    Weakness,
    audit_test,
    authorize_round,
    check_stronger,
    parse,
    record_round,
    refuse_overspend,
    render_test,
)

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=[10, 100, 1000],
)


def a_chain() -> EvidenceChain:
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=UNIFORM_AT_1000)],
        localization=[
            LocalizationLink(
                scope="BookSerializer.to_representation",
                experiment=confirmed,
                share_of_cost=0.89,
                basis="8.24s baseline against 0.90s ablated",
            )
        ],
        mechanism="the serializer re-renders the author for every book",
        complexity={"rows": Growth.LINEAR},
        site=Site(path="shop/serializers.py", first_line=41, last_line=52),
        context=[Implicated(path="shop/models.py", reason="declares the Author relation")],
    )


def a_test(**overrides: Any) -> FalsificationTest:
    fields: dict[str, Any] = {
        "claim": "the list endpoint stops re-rendering the author for every book",
        "script": "assert measure()['seconds'] < 2.0",
        "equivalence": "the same books in the same order",
        "cost": CostClaim(
            metric="seconds",
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
        ),
        "catches": (Cheat.CACHED_STATE,),
    }
    fields.update(overrides)
    return FalsificationTest(**fields)


def a_replacement(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "claim": "the endpoint is fast in a cold process and still returns every author",
        "script": "assert measure_in_fresh_process()['seconds'] < 2.0",
        "equivalence": "the same books in the same order with the same author fields",
        "cost": {
            "metric": "seconds",
            "baseline": 8.24,
            "at_most": 2.0,
            "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
        },
        "catches": ["cached_state", "stubbed_response"],
    }
    payload.update(overrides)
    return payload


def a_reply(*, weaknesses: Any = None, strengthened: Any = None) -> str:
    payload: dict[str, Any] = {
        "weaknesses": [
            {
                "cheat": "stubbed_response",
                "how": "the test reads only the elapsed time, so a view returning an empty "
                "list satisfies it",
            }
        ]
        if weaknesses is None
        else weaknesses
    }
    if payload["weaknesses"]:
        payload["strengthened"] = a_replacement() if strengthened is None else strengthened
    elif strengthened is not None:
        payload["strengthened"] = strengthened
    return json.dumps(payload)


def a_session() -> Session:
    return audit_session(rate=RATE, source=SOURCE, system=SYSTEM)


def recorded(session: Session, reply: str, *, stop_reason: str = "end_turn") -> Recording:
    model = session.router.route(StepType.ATTACK_DESIGN, Phase.TEST_AUDIT)
    content = [{"type": "text", "text": reply}] if stop_reason != "refusal" else []
    return Recording.of(
        model=model,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"{render_test(a_test())}\n\n{QUESTION}"}],
        max_tokens=testaudit_module.MAX_OUTPUT_TOKENS,
        temperature=AUDIT_TEMPERATURE,
        response={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 900,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 8000,
            },
        },
    )


def a_call() -> ModelCall:
    return ModelCall(
        phase=Phase.TEST_AUDIT,
        agent=Agent.ADVERSARY,
        step_class=StepClass.CREATIVE,
        model="claude-opus-5",
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        at=datetime(2026, 8, 17, tzinfo=UTC),
    )


# ================= AC 1: this happens before a patch exists


def test_the_audit_cannot_be_handed_a_patch() -> None:
    """**AC 1 enforced by absence.** *Before patch generation* is an ordering
    claim, and an ordering trusted to a caller is one a caller can get wrong. A
    function with nowhere to put a diff cannot be called after one exists in any
    way that matters."""
    parameters = set(inspect.signature(audit_test).parameters)
    assert not parameters & {"patch", "diff", "files", "candidate"}

    imported = set(vars(testaudit_module))
    assert not imported & {"apply_patch", "CandidateSession", "Falsified"}


def test_the_whole_test_is_handed_over_because_there_is_nothing_to_withhold() -> None:
    """**S-10.1 paying off.** S-9.1 had to strip `rationale` and `outcome` from
    the experiment log because the Diagnostician wrote justifying prose into it.
    `FalsificationTest` has no rationale field — S-10.1 refused one — so the
    artifact goes over whole."""
    assert "rationale" not in FalsificationTest.model_fields

    rendered = render_test(a_test())
    assert "re-rendering the author" in rendered
    assert "measure()['seconds'] < 2.0" in rendered
    assert "cached_state" in rendered
    assert "rows was 1000" in rendered


def test_the_auditors_session_is_its_own_and_the_surgeons_is_refused() -> None:
    """A session carrying the Surgeon's prompt as its cached prefix would hand
    this auditor the Surgeon's framing while every message list stayed clean."""
    surgeons = Session(system="You are writing the test", playbook="", source=SOURCE, rate=RATE)

    with pytest.raises(AuditError, match="not the auditor's"):
        audit_test(
            surgeons,
            ReplayingClient([]),
            test=a_test(),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_finding_auditors_session_is_also_refused() -> None:
    """**The control that makes the extension to S-9.1 mean something.** Two
    audits now share `audit_session`, and a test-audit run through the *finding*
    auditor's session would inherit the wrong prompt just as surely as through
    the Surgeon's."""
    finding_auditors = audit_session(rate=RATE, source=SOURCE)

    with pytest.raises(AuditError, match="not the auditor's"):
        audit_test(
            finding_auditors,
            ReplayingClient([]),
            test=a_test(),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


# ============ AC 2: could a cheat pass this test?


def test_a_named_cheat_and_how_it_would_pass_are_read_back() -> None:
    audit = parse(a_reply(), a_test(), a_chain())

    assert not audit.sound
    assert audit.weaknesses[0].cheat is Cheat.STUBBED_RESPONSE
    assert "empty list" in audit.weaknesses[0].how


def test_no_cheat_passing_is_a_first_class_answer() -> None:
    """**S-9.5's construction, and for the same reason.** An auditor that cannot
    say *I have nothing* sends every falsification test back to be rewritten for
    ever — S-0.8's non-termination reached through the test audit."""
    audit = parse(json.dumps({"weaknesses": []}), a_test(), a_chain())

    assert audit.sound
    assert audit.strengthened is None
    assert "finding nothing, not this attack failing to run" in audit.describe()


def test_an_objection_with_no_account_of_how_is_refused() -> None:
    """*There might be a way round this* cannot be acted on, and cannot be checked
    against the replacement either."""
    with pytest.raises(TestAuditError, match="does not say how"):
        parse(
            a_reply(weaknesses=[{"cheat": "cached_state", "how": "   "}]),
            a_test(),
            a_chain(),
        )


def test_a_cheat_class_nobody_defined_is_refused() -> None:
    with pytest.raises(TestAuditError, match="not one of the cheat classes"):
        parse(
            a_reply(weaknesses=[{"cheat": "vibes", "how": "somehow"}]),
            a_test(),
            a_chain(),
        )


def test_the_prompt_names_the_classes_it_expects_an_answer_in() -> None:
    """A vocabulary the auditor is not shown is one it cannot answer in, and every
    reply would be refused."""
    for name, _ in [(item.name.lower(), item.value) for item in Cheat]:
        assert name in QUESTION


# ======== AC 3: if yes, a strengthened test the Surgeon must satisfy


def test_an_objection_without_a_replacement_is_refused() -> None:
    """`03-agents.md` §6.3: *would a cheat pass the Surgeon's own test — if so,
    write the test that wouldn't.* Objecting without that is asking somebody else
    to solve the problem you found."""
    with pytest.raises(TestAuditError, match="supplies no replacement"):
        parse(json.dumps({"weaknesses": json.loads(a_reply())["weaknesses"]}), a_test(), a_chain())


def test_the_strengthened_test_is_the_one_carried_forward() -> None:
    """AC 3 as one accessor, so a caller cannot carry the weak test forward by
    reading the wrong field — the mistake this story exists to prevent, made one
    layer up."""
    audit = parse(a_reply(), a_test(), a_chain())

    assert audit.forward is audit.strengthened
    assert audit.forward is not audit.original
    assert Cheat.STUBBED_RESPONSE in audit.forward.catches


def test_a_sound_audit_carries_the_original_forward() -> None:
    audit = parse(json.dumps({"weaknesses": []}), a_test(), a_chain())

    assert audit.forward is audit.original


def test_a_replacement_that_raises_the_cost_threshold_is_refused() -> None:
    """**The first way a "strengthened" test is weaker**, and it reads as an
    improvement to anybody skimming: a higher threshold means more changes
    satisfy it."""
    with pytest.raises(TestAuditError, match="more changes satisfy it"):
        parse(
            a_reply(
                strengthened=a_replacement(
                    cost={
                        "metric": "seconds",
                        "baseline": 8.24,
                        "at_most": 6.0,
                        "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
                    }
                )
            ),
            a_test(),
            a_chain(),
        )


def test_a_replacement_that_drops_a_guard_is_refused() -> None:
    """The second. A guard the original had and the replacement does not is a
    trade that used to be caught and now is not."""
    with pytest.raises(TestAuditError, match="drops the guard"):
        parse(
            a_reply(
                strengthened=a_replacement(
                    cost={
                        "metric": "seconds",
                        "baseline": 8.24,
                        "at_most": 2.0,
                        "guards": [{"metric": "db.query", "baseline": 7.0, "at_most": 7.0}],
                    }
                )
            ),
            a_test(),
            a_chain(),
        )


def test_a_replacement_that_does_not_claim_to_catch_the_named_cheat_is_refused() -> None:
    """**The third, and the one worth the most.** An auditor that names a hole and
    hands back a test which does not claim to close it has produced a round of
    work and no coverage — and the Surgeon would satisfy it while the hole stayed
    open."""
    with pytest.raises(TestAuditError, match="the objection and the replacement disagree"):
        parse(
            a_reply(strengthened=a_replacement(catches=["cached_state"])),
            a_test(),
            a_chain(),
        )


def test_a_replacement_may_add_guards_and_tighten_the_threshold() -> None:
    """**The control for the three refusals.** A checker that refused every
    replacement would satisfy all of them while making AC 3 unreachable."""
    audit = parse(
        a_reply(
            strengthened=a_replacement(
                cost={
                    "metric": "seconds",
                    "baseline": 8.24,
                    "at_most": 1.0,
                    "guards": [
                        {"metric": "rows", "baseline": 1000.0, "at_most": 1000.0},
                        {"metric": "db.query", "baseline": 7.0, "at_most": 7.0},
                    ],
                }
            )
        ),
        a_test(),
        a_chain(),
    )

    assert audit.forward.cost.at_most == 1.0
    assert set(audit.forward.guarded_metrics) == {"rows", "db.query"}


def test_the_replacement_is_validated_by_s_10_1s_parser() -> None:
    """Every rule about what makes a falsification test usable — the guard
    requirement, the improvement threshold, the citation check — is S-10.1's, and
    a second implementation would be a second answer to the same question."""
    with pytest.raises(TestAuditError, match="never measured"):
        parse(
            a_reply(
                strengthened=a_replacement(
                    cost={
                        "metric": "latency_p99",
                        "baseline": 8.24,
                        "at_most": 1.0,
                        "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
                    }
                )
            ),
            a_test(),
            a_chain(),
        )


def test_check_stronger_returns_its_objection_rather_than_raising() -> None:
    weakness = Weakness(cheat=Cheat.STUBBED_RESPONSE, how="returns an empty list")
    same = a_test(catches=(Cheat.CACHED_STATE, Cheat.STUBBED_RESPONSE))

    assert check_stronger(a_test(), same, [weakness]) is None
    assert check_stronger(a_test(), a_test(), [weakness]) is not None


# ================ a strengthened test is re-gated, not trusted


def test_a_strengthened_test_is_not_a_falsification() -> None:
    """**The type system carries the requirement.** `strengthened` is a
    `FalsificationTest`; only S-10.2 produces a `Falsified`. So a strengthened
    test has to go back through the must-fail gate — and it must, because a
    *stronger* test the unpatched code already passes is exactly as useless as a
    weak one."""
    audit = parse(a_reply(), a_test(), a_chain())

    assert isinstance(audit.forward, FalsificationTest)
    assert not isinstance(audit.forward, Falsified)
    assert "must itself fail on unpatched code" in audit.describe()


# ======================= AC 4: cost, rounds and the wire


def test_a_test_audit_costs_one_model_call() -> None:
    session = a_session()
    client = ReplayingClient([recorded(session, a_reply())])

    outcome = audit_test(
        session,
        client,
        test=a_test(),
        chain=a_chain(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert len(outcome.calls) == 1
    assert len(outcome.calls) < TEST_AUDIT_CALL_CEILING
    assert outcome.step.phase is Phase.TEST_AUDIT
    assert outcome.step.agent is Agent.ADVERSARY
    assert not outcome.value.sound


def test_the_call_ceiling_is_read_strictly_as_under_five() -> None:
    assert TEST_AUDIT_CALL_CEILING == 5
    refuse_overspend([a_call()] * (TEST_AUDIT_CALL_CEILING - 1))
    with pytest.raises(TestAuditError, match="ceiling of 5"):
        refuse_overspend([a_call()] * TEST_AUDIT_CALL_CEILING)


def test_nothing_counted_test_audit_rounds_before_this_story() -> None:
    """`Phase.TEST_AUDIT`'s cap has had no caller since S-5.4, the same way
    `FINDING_AUDIT`'s had none until S-9.8."""
    session = a_session()
    assert session.budget.used(Phase.TEST_AUDIT, FINDING) == 0

    record_round(session.budget, parse(a_reply(), a_test(), a_chain()), FINDING)
    assert session.budget.used(Phase.TEST_AUDIT, FINDING) == 1


def test_a_third_round_is_refused_before_it_spends_anything() -> None:
    session = a_session()
    audit = parse(a_reply(), a_test(), a_chain())

    for _ in range(PHASE_CAPS[Phase.TEST_AUDIT].limit):
        authorize_round(session.budget, FINDING)
        record_round(session.budget, audit, FINDING)

    with pytest.raises(BudgetExhaustedError) as raised:
        authorize_round(session.budget, FINDING)
    assert raised.value.exhaustion.disposition is Disposition.ESCALATE


def test_a_sound_and_a_strengthened_round_conclude_differently() -> None:
    """**The survivor of the sabotage pass.** Recording a constant conclusion
    changed nothing, because the default `stall_after` is 3 against a cap of 2 —
    the cap always fires first, so a stall is unreachable and the conclusion is
    never compared.

    At `stall_after=2` the two are distinguishable: two audits reaching the same
    verdict is a phase repeating itself, and two reaching different ones is not.
    """
    sound = parse(json.dumps({"weaknesses": []}), a_test(), a_chain())
    strengthened = parse(a_reply(), a_test(), a_chain())

    mixed = Session(system=SYSTEM, playbook="", source=SOURCE, rate=RATE, stall_after=2)
    record_round(mixed.budget, sound, FINDING)
    record_round(mixed.budget, strengthened, FINDING)
    assert mixed.budget.used(Phase.TEST_AUDIT, FINDING) == 2

    repeated = Session(system=SYSTEM, playbook="", source=SOURCE, rate=RATE, stall_after=2)
    record_round(repeated.budget, sound, FINDING)
    with pytest.raises(ProgressStalledError):
        record_round(repeated.budget, sound, FINDING)


def test_an_omitted_weakness_field_is_refused_rather_than_read_as_none() -> None:
    """**A real defect a sabotage found.** `weaknesses: []` is the auditor saying
    *I looked and found nothing*; a missing key is a reply that never addressed
    the question. Reading the second as the first passes a weak test **on
    silence** — S-9.7's rule in the place where it costs most.
    """
    with pytest.raises(TestAuditError, match="never addressed the question"):
        parse(json.dumps({"strengthened": a_replacement()}), a_test(), a_chain())

    # The control: the explicit empty answer is still first-class.
    assert parse(json.dumps({"weaknesses": []}), a_test(), a_chain()).sound


def test_a_refusal_is_not_read_as_an_audit_that_found_no_cheat() -> None:
    session = a_session()
    client = ReplayingClient([recorded(session, "", stop_reason="refusal")])

    with pytest.raises(TestAuditError, match="declined"):
        audit_test(
            session,
            client,
            test=a_test(),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_a_truncated_replacement_is_refused() -> None:
    """A half-written replacement script would be carried into the must-fail gate
    looking complete."""
    session = a_session()
    client = ReplayingClient([recorded(session, a_reply()[:80], stop_reason="max_tokens")])

    with pytest.raises(TestAuditError, match="cut off"):
        audit_test(
            session,
            client,
            test=a_test(),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_message_list_is_fresh_on_every_call() -> None:
    """S-9.1's non-negotiable, reused rather than reimplemented: there is no
    accumulated conversation and nowhere to pass one."""
    parameters = set(inspect.signature(audit_test).parameters)
    assert not parameters & {"messages", "history", "prior", "attempts"}


def test_the_audit_is_an_attack_and_routed_as_one() -> None:
    """`04-cost.md` §3 records *none exists* for `ATTACK_DESIGN`'s validator, so
    this cannot cascade — the same row S-9.1 reuses, and for the same reason."""
    assert STEP_KINDS[StepType.ATTACK_DESIGN].mechanical_check is None
    assert "validate" not in inspect.signature(audit_test).parameters


def test_a_sound_audit_and_a_weak_one_are_distinguishable_in_the_report() -> None:
    sound = parse(json.dumps({"weaknesses": []}), a_test(), a_chain()).describe()
    weak = parse(a_reply(), a_test(), a_chain()).describe()

    assert "TEST AUDIT PASSED" in sound
    assert "a cheat would pass" in weak
    assert "stubbed_response" in weak


def test_the_audit_object_can_be_built_only_with_a_matching_pair() -> None:
    """A `TestAudit` carrying weaknesses and no replacement would be the state AC 3
    forbids. `parse` refuses it; the dataclass records both so a reader can see
    which test was replaced and why."""
    audit = TestAudit(original=a_test(), weaknesses=(), strengthened=None)

    assert audit.sound
    assert audit.original is audit.forward

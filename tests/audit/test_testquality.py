"""Epic 11, S-11.6 — the test-quality attack, run with the change in front of you.

*Asks whether a cheat could pass the Surgeon's falsification test. If yes, writes
the test that would catch it. Strengthened test becomes the permanent regression
test.*

**AC 1 and AC 2 are S-10.3's**, which `08-audit.md` §3.3 moved *before* the patch
so the weak test could not shape it. These tests assert that this module reuses
that machinery rather than re-deriving it, and cover the two things that are this
story's own: an audit that can see the diff, and an artifact that outlives the
repair.

The regression-test half is where the sharp property is. What is lying around at
this point is a proof about the **Surgeon's original** test, and what ships is the
**Adversary's strengthened** one — attaching the first to the second produces a
permanent regression test nobody has ever watched fail.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from coldfix.audit import testquality as module
from coldfix.audit.invocation import AUDIT_TEMPERATURE, AuditError
from coldfix.audit.patchaudit import Candidate, candidate_from
from coldfix.audit.testquality import (
    QUESTION,
    RESIDUE,
    SYSTEM,
    RegressionTest,
    TestQualityError,
    invoke,
    keep,
    named,
    quality_session,
    render,
)
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent, ExchangeRate, Phase, StepClass
from coldfix.cost.routing import StepType
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
from coldfix.repair import testaudit
from coldfix.repair.compose import Outcome
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Patch
from coldfix.repair.testaudit import TestAudit, TestAuditError, Weakness

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 19))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"

DIFF = """\
diff --git a/shop/serializers.py b/shop/serializers.py
--- a/shop/serializers.py
+++ b/shop/serializers.py
@@ -41,2 +41,3 @@
-        return [self.render(book) for book in books]
+        cached = getattr(self, "_seen", None) or {}
+        return [cached.get(book.id) or self.render(book) for book in books]
"""

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
        site=Site(path="shop/serializers.py", first_line=41, last_line=42),
        context=[Implicated(path="shop/models.py", reason="the relation walked per row")],
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


def a_stronger_test(**overrides: Any) -> FalsificationTest:
    fields: dict[str, Any] = {
        "claim": "the endpoint is fast in a cold process and still returns every author",
        "script": "assert measure_in_fresh_process()['seconds'] < 2.0",
        "equivalence": "the same books in the same order with the same author fields",
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


def a_candidate() -> Candidate:
    patch = Patch(
        diff=DIFF,
        approach="memoise the rendered book",
        rationale="the serializer walked the relation per book",
    )
    return candidate_from(patch, original={}, patched={})


def a_falsified(test: FalsificationTest | None = None) -> Falsified:
    return Falsified(
        test=test if test is not None else a_test(),
        evidence="AssertionError: the endpoint is still slow",
        wall_seconds=8.4,
    )


def an_audit(*, sound: bool) -> TestAudit:
    if sound:
        return TestAudit(original=a_test(), weaknesses=(), strengthened=None)
    return TestAudit(
        original=a_test(),
        weaknesses=(
            Weakness(
                cheat=Cheat.CACHED_STATE,
                how="the diff memoises on self, so the second call in a process is free",
            ),
        ),
        strengthened=a_stronger_test(),
    )


def a_session(system: str = SYSTEM) -> Session:
    return (
        quality_session(rate=RATE, source=SOURCE)
        if system == SYSTEM
        else Session(system=system, playbook="", source=SOURCE, rate=RATE)
    )


def recorded(session: Session, reply: str, *, stop_reason: str = "end_turn") -> Recording:
    model = session.router.route(StepType.ATTACK_DESIGN, Phase.PATCH_AUDIT)
    content = [{"type": "text", "text": reply}] if stop_reason != "refusal" else []
    return Recording.of(
        model=model,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"{render(a_candidate(), a_test())}\n\n{QUESTION}"}],
        max_tokens=module.MAX_OUTPUT_TOKENS,
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


def ask(reply: str, *, stop_reason: str = "end_turn") -> TestAudit:
    session = a_session()
    client = ReplayingClient([recorded(session, reply, stop_reason=stop_reason)])
    return invoke(
        session,
        client,
        candidate=a_candidate(),
        test=a_test(),
        chain=a_chain(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    ).value


SOUND_REPLY = json.dumps({"weaknesses": []})
WEAK_REPLY = json.dumps(
    {
        "weaknesses": [
            {
                "cheat": "cached_state",
                "how": "the diff memoises on self, so the second call in a process is free",
            }
        ],
        "strengthened": {
            "claim": "the endpoint is fast in a cold process and still returns every author",
            "script": "assert measure_in_fresh_process()['seconds'] < 2.0",
            "equivalence": "the same books in the same order with the same author fields",
            "cost": {
                "metric": "seconds",
                "baseline": 8.24,
                "at_most": 2.0,
                "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
            },
            "catches": ["cached_state"],
        },
    }
)


# ============ AC 1 and AC 2 are S-10.3's, reused rather than rewritten


def test_the_machinery_is_s_10_3s_and_not_a_second_implementation() -> None:
    """`08-audit.md` §3.3 moved this audit *before* the patch and S-10.3 built it.
    Two implementations of *is this replacement actually stronger* would be two
    answers where the whole point is that there is one."""
    assert vars(module)["parse"] is testaudit.parse
    assert vars(module)["render_test"] is testaudit.render_test
    assert vars(module)["Weakness"] is testaudit.Weakness
    assert vars(module)["TestAudit"] is testaudit.TestAudit


def test_the_replacement_is_held_to_check_strongers_three_refusals() -> None:
    """Inherited, not re-derived: the threshold may not rise, no guard may vanish,
    and the replacement must claim to catch what was just named."""
    looser = json.loads(WEAK_REPLY)
    looser["strengthened"]["cost"]["at_most"] = 4.0
    with pytest.raises(TestAuditError, match="more changes satisfy it"):
        ask(json.dumps(looser))

    unguarded = json.loads(WEAK_REPLY)
    unguarded["strengthened"]["cost"]["guards"] = [
        {"metric": "db.query", "baseline": 7.0, "at_most": 7.0}
    ]
    with pytest.raises(TestAuditError, match="drops the guard"):
        ask(json.dumps(unguarded))

    uncovered = json.loads(WEAK_REPLY)
    uncovered["strengthened"]["catches"] = ["over_fetch"]
    with pytest.raises(TestAuditError, match="the objection and the replacement disagree"):
        ask(json.dumps(uncovered))


def test_an_empty_weaknesses_list_is_a_result() -> None:
    audit = ask(SOUND_REPLY)
    assert audit.sound
    assert audit.forward == a_test()


def test_a_missing_weaknesses_field_is_not_read_as_none() -> None:
    """S-10.3's rule, inherited: a reply that never addressed the question would
    otherwise ship a weak test as a permanent one on silence."""
    with pytest.raises(TestAuditError, match="never addressed the question"):
        ask(json.dumps({"strengthened": {}}))


# ============ what this story owns, first half: the audit can see the diff


def test_the_test_is_the_subject_and_the_diff_is_the_new_information() -> None:
    """S-10.3 asks *could some change* slip through. This asks *did this one*, and
    leading with the diff would invite an audit of the change — which is S-11.2 to
    S-11.5's job, not this one's."""
    rendered = render(a_candidate(), a_test())
    assert rendered.index("THE TEST UNDER ATTACK") < rendered.index("THE CHANGE THAT PASSED IT")
    assert "getattr(self" in rendered, "the diff is there"
    assert "The question is about the test." in rendered


def test_the_subject_is_a_candidate_so_the_reasoning_cannot_arrive() -> None:
    """S-11.1's construction, unchanged: a `Candidate` has nowhere to put
    `rationale` or `approach`, so `invoke` cannot be handed either."""
    parameters = inspect.signature(invoke).parameters
    assert "patch" not in parameters
    assert "attempts" not in parameters
    assert "messages" not in parameters
    assert parameters["candidate"].annotation == "Candidate"

    patch = Patch(diff=DIFF, approach="memoise the rendered book", rationale="walked per book")
    rendered = render(candidate_from(patch, original={}, patched={}), a_test())
    assert "memoise the rendered book" not in rendered
    assert "walked per book" not in rendered


def test_this_audit_runs_in_the_patch_audit_phase() -> None:
    """S-10.3's runs under `TEST_AUDIT` before a patch exists. This one is a round
    of the patch audit, because by now there is a patch."""
    session = a_session()
    client = ReplayingClient([recorded(session, SOUND_REPLY)])
    outcome = invoke(
        session,
        client,
        candidate=a_candidate(),
        test=a_test(),
        chain=a_chain(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )
    (call,) = outcome.calls
    assert call.phase is Phase.PATCH_AUDIT
    assert call.agent is Agent.ADVERSARY
    assert call.step_class is StepClass.CREATIVE, "attack design never cascades"


def test_a_session_carrying_another_agents_prompt_is_refused() -> None:
    """A session whose cached prefix is somebody else's would hand this auditor
    their framing while every message list stayed clean."""
    with pytest.raises(AuditError):
        invoke(
            a_session(testaudit.SYSTEM),
            ReplayingClient(),
            candidate=a_candidate(),
            test=a_test(),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_a_decline_is_not_an_audit_that_found_nothing() -> None:
    with pytest.raises(TestQualityError, match="lets a decline ship a weak test"):
        ask("", stop_reason="refusal")


def test_a_truncated_reply_is_refused_because_this_one_is_kept_for_ever() -> None:
    with pytest.raises(TestQualityError, match="assertions may be missing"):
        ask(WEAK_REPLY, stop_reason="max_tokens")


def test_the_session_constructor_is_not_named_like_a_test() -> None:
    """**pytest collects a function on the `test_` prefix alone**, exactly as it
    collects a class on `Test`. The obvious name — `test_quality_session` — made
    this constructor a test case, and it errored on fixtures it does not have. The
    module already carried `__test__ = False` on two classes for that hazard."""
    assert not module.quality_session.__name__.startswith("test")
    assert not hasattr(module, "test_quality_session")


def test_the_fourth_audit_shares_the_one_session_constructor() -> None:
    session = quality_session(rate=RATE, source=SOURCE)
    assert session.system == SYSTEM
    assert session.system != testaudit.SYSTEM


# ============ what this story owns, second half: AC 3, the permanent test


def test_a_strengthened_test_becomes_the_regression_test() -> None:
    audit = an_audit(sound=False)
    kept = keep(
        audit,
        proof_of_failure=a_falsified(a_stronger_test()),
        verified=Outcome.VERIFIED,
        finding_id=FINDING,
    )
    assert kept.test == a_stronger_test()
    assert kept.strengthened
    assert kept.closes == (Cheat.CACHED_STATE,)
    assert "strengthened to close cached_state" in kept.describe()


def test_a_sound_test_is_kept_too() -> None:
    """A regression test is still worth keeping when nobody found a hole in it."""
    kept = keep(an_audit(sound=True), proof_of_failure=a_falsified(), verified=Outcome.VERIFIED)
    assert kept.test == a_test()
    assert not kept.strengthened
    assert kept.closes == ()
    assert "no audit found a hole" in kept.describe()


def test_the_proof_must_be_about_the_test_that_ships() -> None:
    """**The trap, and the sharpest property here.** What is lying around at this
    point is the gate result for the Surgeon's *original* test; what ships is the
    Adversary's *strengthened* one. Attaching the first to the second is S-10.3's
    re-gating rule failed at the last possible moment, and the result is a
    permanent regression test nobody has ever watched fail."""
    with pytest.raises(TestQualityError, match="ever watched fail"):
        keep(
            an_audit(sound=False),
            proof_of_failure=a_falsified(a_test()),
            verified=Outcome.VERIFIED,
        )


def test_a_test_that_does_not_pass_on_the_patch_cannot_ship() -> None:
    """A permanent test that does not pass on the code it ships with is a build
    broken on arrival."""
    for outcome in (Outcome.STILL_FAILING, Outcome.PATCH_BROKE_THE_TEST):
        with pytest.raises(TestQualityError, match="broken on arrival"):
            keep(
                an_audit(sound=True),
                proof_of_failure=a_falsified(),
                verified=outcome,
            )


def test_a_test_that_does_not_claim_what_it_is_said_to_close_is_refused() -> None:
    """`check_stronger`'s third refusal, arriving at the permanent artifact."""
    mismatched = TestAudit(
        original=a_test(),
        weaknesses=(Weakness(cheat=Cheat.OVER_FETCH, how="fetches every column now"),),
        strengthened=a_stronger_test(catches=(Cheat.CACHED_STATE,)),
    )
    with pytest.raises(TestQualityError, match="hole and the fix disagree"):
        keep(mismatched, proof_of_failure=a_falsified(a_stronger_test()), verified=Outcome.VERIFIED)


def test_keep_reads_forward_so_the_weak_test_cannot_be_shipped_by_mistake() -> None:
    """S-10.3's accessor exists so a caller cannot carry the weak test forward by
    reading the wrong field. This is the layer where that mistake would be
    permanent."""
    audit = an_audit(sound=False)
    assert audit.original != audit.forward
    kept = keep(audit, proof_of_failure=a_falsified(a_stronger_test()), verified=Outcome.VERIFIED)
    assert kept.test == audit.forward
    assert kept.test != audit.original


def test_a_regression_test_carries_both_proofs_in_its_report() -> None:
    kept = keep(an_audit(sound=True), proof_of_failure=a_falsified(), verified=Outcome.VERIFIED)
    described = kept.describe()
    assert "proved to fail without the patch" in described
    assert "proved to pass with it" in described
    assert "runs against every later change" in described


def test_named_reports_the_classes_in_the_enums_own_order() -> None:
    weaknesses = (
        Weakness(cheat=Cheat.SHAPE_SPECIFIC, how="only for uniform data"),
        Weakness(cheat=Cheat.CACHED_STATE, how="memoised on self"),
    )
    assert named(weaknesses) == (Cheat.CACHED_STATE, Cheat.SHAPE_SPECIFIC)
    assert named(()) == ()


def test_the_residue_says_what_a_second_round_can_and_cannot_add() -> None:
    assert "the diff, not a longer list of cheats" in RESIDUE
    assert "S-11.8" in RESIDUE


def test_the_classes_are_the_same_five_as_everywhere_else() -> None:
    """A sixth vocabulary would be a sixth answer. S-10.1's enum, via S-10.3."""
    audit = an_audit(sound=False)
    assert all(item.cheat in set(Cheat) for item in audit.weaknesses)
    assert len(Cheat) == 5


def test_a_regression_test_is_not_collected_as_a_pytest_suite() -> None:
    """pytest collects on the `Test` prefix alone, and every name here begins with
    the word because the *subject* is a test."""
    assert RegressionTest.__test__ is False
    assert TestQualityError.__test__ is False

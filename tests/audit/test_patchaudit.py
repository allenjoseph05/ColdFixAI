"""S-11.1 — handing a patch to something that did not write it.

`08-audit.md`: *the Adversary runs twice.* This is S-9.1's story against a
different subject, and the two failure modes are the same pair pulling opposite
ways.

**Sending the Surgeon's reasoning** hands the auditor the rationalization —
`08-audit.md`'s number is that 72% of reward-hacking episodes carry explicit
justifying reasoning.

**Isolation by sending nothing** satisfies every rule in the criterion while
making the audit useless, and S-9.1 recorded it as a sabotage in its own right.
So the tests below check both what is absent and what is present.
"""

from __future__ import annotations

import inspect
from dataclasses import fields as dataclass_fields
from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import patchaudit as patchaudit_module
from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    AuditError,
    audit_messages,
    audit_session,
)
from coldfix.audit.patchaudit import (
    MAX_OUTPUT_TOKENS,
    RESIDUE,
    SYSTEM,
    WITHHELD,
    Candidate,
    PatchAuditError,
    authorize_round,
    candidate_from,
    invoke,
    patch_audit_session,
    record_round,
    render_candidate,
)
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent, ExchangeRate, Phase
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
from coldfix.repair import patch as patch_module
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.patch import Attempt, Patch

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 18))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"
SITE = "shop/serializers.py"
IMPLICATED = "shop/models.py"

RATIONALE = "the serializer walked the relation per book; one query now serves all"
APPROACH = "prefetch the authors once and index them"

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
        site=Site(path=SITE, first_line=41, last_line=52),
        context=[Implicated(path=IMPLICATED, reason="declares the Author relation")],
    )


def a_test() -> FalsificationTest:
    return FalsificationTest(
        claim="the list endpoint stops re-rendering the author for every book",
        script="assert measure()['seconds'] < 2.0",
        equivalence="the same books in the same order with the same author fields",
        cost=CostClaim(
            metric="seconds",
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
        ),
        catches=(Cheat.CACHED_STATE,),
    )


def a_diff(path: str = SITE) -> str:
    return "\n".join(
        [
            f"--- a/{path}",
            f"+++ b/{path}",
            # The counts must match the body: one removed line and one added
            # line. Declaring 2 each with 1 each present made the parser consume
            # the *next* file's header as hunk content — correctly, per
            # `touched_paths`' own docstring — and the second file vanished.
            "@@ -41,1 +41,1 @@",
            "-        return AuthorSerializer(obj.author).data",
            "+        return self._authors[obj.author_id]",
            "",
        ]
    )


def a_patch() -> Patch:
    return Patch(diff=a_diff(), approach=APPROACH, rationale=RATIONALE)


def a_candidate(**overrides: object) -> Candidate:
    fields: dict[str, object] = {
        "diff": a_diff(),
        # **Deliberately not the diff's own lines.** With the source blocks
        # echoing the added and removed lines, a sabotage that stopped sending
        # the diff changed no assertion — the fourteenth fixture in this project
        # that could not tell the right answer from the wrong one.
        "original": {SITE: "class BookSerializer:\n    ORIGINAL_MARKER = 1"},
        "patched": {SITE: "class BookSerializer:\n    PATCHED_MARKER = 2"},
    }
    fields.update(overrides)
    return Candidate(**fields)  # type: ignore[arg-type]


def a_session() -> Session:
    return patch_audit_session(rate=RATE, source=SOURCE)


def recorded(session: Session, question: str, reply: str, *, stop: str = "end_turn") -> Recording:
    model = session.router.route(StepType.ATTACK_DESIGN, Phase.PATCH_AUDIT)
    return Recording.of(
        model=model,
        system=SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=AUDIT_TEMPERATURE,
        response={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": reply}] if stop != "refusal" else [],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 900,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 8000,
            },
        },
    )


# ============ AC 2: the Surgeon's reasoning has nowhere to go


def test_the_handover_type_has_no_field_for_the_surgeons_reasoning() -> None:
    """**S-9.1 had to strip; this one has nowhere to put it.** The log's type
    carries `rationale` and `outcome`, so the finding audit removes them while
    rendering. Here the artifact handed over cannot express either field, which is
    the construction S-10.1 used to keep a diff out of a falsification test."""
    names = {field.name for field in dataclass_fields(Candidate)}

    assert names == {"diff", "original", "patched"}
    assert not names & set(WITHHELD)

    with pytest.raises(TypeError):
        Candidate(  # type: ignore[call-arg]
            diff=a_diff(), original={}, patched={}, rationale=RATIONALE
        )


def test_the_withheld_fields_are_the_ones_the_patch_actually_has() -> None:
    """Named as data so this can be asserted against the source artifact rather
    than against a rendering — and so that a `Patch` gaining a third piece of
    prose is a line here, not a silent leak."""
    assert set(WITHHELD) <= set(Patch.model_fields)
    assert set(WITHHELD) == {"rationale", "approach"}


def test_neither_the_rationale_nor_the_approach_survives_the_boundary() -> None:
    """`approach` goes as well, and that is a decision. It is the Surgeon's
    one-line account of what it believes it did — what `outcome` was to S-9.1's
    log — and S-10.5 proved it is renameable without anything else changing."""
    candidate = candidate_from(a_patch(), original={SITE: "before"}, patched={SITE: "after"})
    rendered = render_candidate(candidate, a_chain(), a_test())

    assert RATIONALE not in rendered
    assert APPROACH not in rendered
    assert "prefetch" not in rendered


def test_invoke_has_nowhere_to_receive_a_patch_or_prior_attempts() -> None:
    """**The enforcement is an absence three times over**, as it was in S-9.1: no
    `patch` parameter, so the rationale cannot arrive; no `attempts` parameter, so
    prior attempts cannot; no `messages` parameter, so a conversation cannot."""
    parameters = set(inspect.signature(invoke).parameters)

    assert not parameters & {"patch", "attempts", "prior", "messages", "history"}
    assert "candidate" in parameters


def test_nothing_in_the_module_takes_an_attempt() -> None:
    """Prior attempts are refused structurally rather than by rendering. A
    function that accepted one would be a route around the criterion."""
    annotations = {
        str(parameter.annotation)
        for _, function in inspect.getmembers(patchaudit_module, inspect.isfunction)
        for parameter in inspect.signature(function).parameters.values()
    }

    assert not any("Attempt" in item for item in annotations)
    assert Attempt is not None  # the type exists; it simply cannot reach here


# ============ AC 1: everything it needs to attack


def test_the_diff_and_both_revisions_are_sent() -> None:
    """**The opposite failure.** S-9.1 recorded that *isolation by sending
    nothing* satisfies every rule while making the audit useless."""
    rendered = render_candidate(a_candidate(), a_chain(), a_test())

    # The diff itself, by a marker only the diff carries.
    assert "@@ -41,1 +41,1 @@" in rendered
    assert "-        return AuthorSerializer(obj.author).data" in rendered
    assert "+        return self._authors[obj.author_id]" in rendered

    # And both revisions, by markers only the source blocks carry.
    assert "ORIGINAL_MARKER" in rendered
    assert "PATCHED_MARKER" in rendered
    assert "before:" in rendered and "after:" in rendered


def test_the_evidence_chain_is_sent_whole() -> None:
    """AC 1 requires it, and it is what makes an objection about the *cost*
    possible rather than only about the code."""
    rendered = render_candidate(a_candidate(), a_chain(), a_test())

    assert "SYMPTOM" in rendered
    assert "RULED OUT" in rendered
    assert "declares the Author relation" in rendered


def test_the_falsification_test_is_sent_with_its_thresholds() -> None:
    """The auditor is deciding whether the patch really satisfies this, so the
    claim alone is not enough — the threshold and the guards are what it has to
    check against."""
    rendered = render_candidate(a_candidate(), a_chain(), a_test())

    assert "stops re-rendering the author" in rendered
    assert "must come in below 2" in rendered
    assert "rows was 1000" in rendered
    assert "the same books in the same order" in rendered
    assert "cached_state" in rendered


def test_a_file_whose_source_is_missing_is_named_rather_than_passed_over() -> None:
    """S-3.9 reads source best-effort because a file it cannot see weakens a
    finding and an exception loses it. An Adversary that cannot see a changed
    file should be told, not left to assume it saw everything."""
    candidate = a_candidate(diff=a_diff() + a_diff(IMPLICATED))
    rendered = render_candidate(candidate, a_chain(), a_test())

    assert candidate.unreadable == (IMPLICATED,)
    assert "source was not available to this audit" in rendered


def test_a_candidate_with_no_diff_is_refused() -> None:
    """An audit handed nothing would find no objection and report that as the
    patch surviving."""
    with pytest.raises(PatchAuditError, match="nothing to attack"):
        Candidate(diff="   ", original={}, patched={})


def test_the_files_come_from_the_diff_rather_than_being_reported() -> None:
    assert a_candidate().files == {SITE}


# ============ AC 3: a fresh message list, and its own session


def test_the_message_list_is_s_9_1s_and_is_new_every_call() -> None:
    """Reused rather than copied. `CLAUDE.md`'s non-negotiable is one function,
    and two implementations of it would be two things to keep right."""
    first = audit_messages("evidence", "question")
    second = audit_messages("evidence", "question")

    assert first == second
    assert first is not second
    first.clear()
    assert audit_messages("evidence", "question")


def test_the_finding_auditors_session_is_refused() -> None:
    """**Three audits now share one constructor and one isolation argument.** A
    patch audit billed through the finding auditor's session would inherit that
    agent's prompt as a cached prefix while every message list stayed clean."""
    with pytest.raises(AuditError, match="not the auditor's"):
        invoke(
            audit_session(rate=RATE, source=SOURCE),
            ReplayingClient([]),
            candidate=a_candidate(),
            chain=a_chain(),
            test=a_test(),
            question="could this be a cheat?",
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_surgeons_session_is_refused() -> None:
    surgeons = Session(system=patch_module._SYSTEM, playbook="p", source=SOURCE, rate=RATE)

    with pytest.raises(AuditError, match="not the auditor's"):
        invoke(
            surgeons,
            ReplayingClient([]),
            candidate=a_candidate(),
            chain=a_chain(),
            test=a_test(),
            question="could this be a cheat?",
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_patch_auditors_own_session_is_accepted() -> None:
    """The control: a check that refused every session would make the epic
    unreachable rather than isolated."""
    session = a_session()
    question = "could this be a cheat?"
    evidence = render_candidate(a_candidate(), a_chain(), a_test())
    client = ReplayingClient([recorded(session, f"{evidence}\n\n{question}", "no objection")])

    outcome = invoke(
        session,
        client,
        candidate=a_candidate(),
        chain=a_chain(),
        test=a_test(),
        question=question,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert outcome.value == "no objection"
    assert outcome.step.phase is Phase.PATCH_AUDIT
    assert outcome.step.agent is Agent.ADVERSARY


def test_the_audit_is_an_attack_and_cannot_cascade() -> None:
    """`04-cost.md` §3 records *none exists* for `ATTACK_DESIGN`'s validator — the
    same row S-9.1 and S-10.3 reuse, and for the same reason."""
    assert STEP_KINDS[StepType.ATTACK_DESIGN].mechanical_check is None
    assert "validate" not in inspect.signature(invoke).parameters


def test_a_refusal_is_not_read_as_a_patch_that_survived() -> None:
    session = a_session()
    question = "could this be a cheat?"
    evidence = render_candidate(a_candidate(), a_chain(), a_test())

    with pytest.raises(PatchAuditError, match="declined"):
        invoke(
            session,
            ReplayingClient([recorded(session, f"{evidence}\n\n{question}", "", stop="refusal")]),
            candidate=a_candidate(),
            chain=a_chain(),
            test=a_test(),
            question=question,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_a_truncated_objection_is_refused() -> None:
    session = a_session()
    question = "could this be a cheat?"
    evidence = render_candidate(a_candidate(), a_chain(), a_test())

    with pytest.raises(PatchAuditError, match="cut off"):
        invoke(
            session,
            ReplayingClient(
                [recorded(session, f"{evidence}\n\n{question}", "half an ob", stop="max_tokens")]
            ),
            candidate=a_candidate(),
            chain=a_chain(),
            test=a_test(),
            question=question,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


# ============ the rounds, and the bound


def test_nothing_counted_patch_audit_rounds_before_this_story() -> None:
    """**The fourth of these**, after `FINDING_AUDIT` (S-9.8), `TEST_AUDIT`
    (S-10.3) and `REPAIR` (S-10.5). Every phase whose cap is counted in something
    other than steps has needed the story owning the unit to count it."""
    session = a_session()
    assert session.budget.used(Phase.PATCH_AUDIT, FINDING) == 0

    record_round(session.budget, "no objection", FINDING)
    assert session.budget.used(Phase.PATCH_AUDIT, FINDING) == 1


def test_a_third_round_is_refused_before_it_spends_anything() -> None:
    session = a_session()
    for index in range(PHASE_CAPS[Phase.PATCH_AUDIT].limit):
        authorize_round(session.budget, FINDING)
        record_round(session.budget, f"round {index}", FINDING)

    with pytest.raises(BudgetExhaustedError) as raised:
        authorize_round(session.budget, FINDING)
    assert raised.value.exhaustion.disposition is Disposition.ESCALATE


def test_two_rounds_concluding_the_same_thing_stall() -> None:
    """The conclusion is the caller's because S-11.2 to S-11.5 have not defined
    their verdicts, but it still has to reach S-5.4's check."""
    session = Session(system=SYSTEM, playbook="p", source=SOURCE, rate=RATE, stall_after=2)

    record_round(session.budget, "no objection", FINDING)
    with pytest.raises(ProgressStalledError):
        record_round(session.budget, "no objection", FINDING)


def test_an_empty_conclusion_clears_the_run_rather_than_extending_it() -> None:
    """S-5.4: a step that concluded nothing is not the same conclusion twice."""
    session = Session(system=SYSTEM, playbook="p", source=SOURCE, rate=RATE, stall_after=2)

    record_round(session.budget, "no objection", FINDING)
    # **Two identical blanks in a row**, and both details matter. With the strip
    # each becomes `None` and clears the run, so nothing stalls. Without it they
    # are two equal conclusions and this raises. An earlier version used `"   "`
    # then `""` — different strings, so they could not stall under either
    # implementation, and the sabotage survived.
    record_round(session.budget, "   ", FINDING)
    record_round(session.budget, "   ", FINDING)
    assert session.budget.used(Phase.PATCH_AUDIT, FINDING) == 3


def test_the_residue_states_what_the_isolation_does_not_remove() -> None:
    """`08-audit.md`: the evidence chain and the falsification test both encode
    the Diagnostician's and Surgeon's framing, and AC 1 requires both. That
    cannot be fixed here — only stated."""
    assert "partial" in RESIDUE
    assert "framing bias" in RESIDUE
    assert "not describe this as clean separation" in RESIDUE.replace("Do not", "not")


def test_the_prompt_says_the_authors_account_is_absent() -> None:
    """The model is told, *and* the message list is what makes it true. S-9.1's
    rule: a prompt saying *disregard the reasoning above* is a wish; a list that
    never contained it is a guarantee — but saying so stops the auditor inventing
    what it thinks it was not shown."""
    assert "not given anything they wrote about their own change" in SYSTEM
    assert "Nothing the author wrote" in render_candidate(a_candidate(), a_chain(), a_test())

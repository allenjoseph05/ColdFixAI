"""S-9.7 — whether the thing we made faster is a thing anybody runs.

One asymmetry organises every test here. `unrepresentative` skips the finding and
attempts no repair, so a **wrong `unrepresentative` throws away a real finding
silently** — nobody sees what was not investigated — while a wrong
`representative` wastes repair effort somebody notices. The errors are not
symmetric, so the safe answer is the default.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import invocation as invocation_module
from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    audit_messages,
    audit_session,
    render_evidence,
)
from coldfix.audit.representativeness import (
    QUESTION,
    RESIDUE,
    Representativeness,
    RepresentativenessError,
    assess,
    parse,
    synthesized,
)
from coldfix.cost.accounting import ExchangeRate
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import FixtureRecipe, Observation, Workload

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))
SOURCE = "shop/views.py::book_list"


def a_workload(*, fixture_source: str = "factory BookFactory") -> Workload:
    return Workload(
        id="shop-books-list",
        description="the book list endpoint, rendering every row",
        entry_point="shop/views.py::ListView.list_books",
        fixture=FixtureRecipe(
            entity="book",
            per_parent=20,
            parents=100,
            distribution=Distribution.UNIFORM,
            source=fixture_source,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        observations=(Observation(scale=100, metrics={"seconds": 8.24}),),
    )


def a_log() -> ExperimentLog:
    log = ExperimentLog()
    log.append(
        hypothesis="the serializer dominates",
        primitive="ablation.stub",
        rationale="queries are flat",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing removed most of the wall time",
    )
    return log


def payload(text: str) -> dict[str, object]:
    return {
        "id": "msg_rep",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 80,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 4_000,
        },
    }


def run(reply: str, *, workload: Workload | None = None) -> object:
    subject = workload or a_workload()
    question = (
        f"WORKLOAD\n  {subject.id}: {subject.description}\n"
        f"  entry point: {subject.entry_point}\n"
        f"  fixture: {subject.fixture.entity} from {subject.fixture.source}\n\n"
        f"{QUESTION}"
    )
    recording = Recording.of(
        model="claude-opus-5",
        system=invocation_module._SYSTEM,
        messages=audit_messages(render_evidence(a_log()), question),
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=AUDIT_TEMPERATURE,
        response=payload(reply),
    )
    return assess(
        audit_session(rate=RATE, source=SOURCE),
        ReplayingClient([recording]),
        workload=subject,
        log=a_log(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    ).value


# ============ the asymmetry: unrepresentative destroys a finding, so it defaults off


def test_a_workload_is_representative_unless_there_is_a_reason_it_is_not() -> None:
    """**The whole design.** A wrong `unrepresentative` throws away a real finding
    silently; a wrong `representative` wastes repair effort somebody notices."""
    audit = run(json.dumps({"representative": True, "reason": ""}))

    assert audit.verdict is Representativeness.REPRESENTATIVE  # type: ignore[attr-defined]
    assert not audit.skips_repair  # type: ignore[attr-defined]


def test_calling_a_workload_unrepresentative_without_a_reason_is_refused() -> None:
    """A verdict that discards a finding has to carry its reason. `08-audit.md`
    calls this a partial fix, and a partial fix applied without an argument is a
    coin toss with a finding on it."""
    with pytest.raises(RepresentativenessError, match="with no reason given"):
        run(json.dumps({"representative": False}))


def test_a_reasoned_unrepresentative_verdict_is_accepted() -> None:
    """The control. A check that refused every negative verdict would make AC 2
    unreachable — nothing could ever skip repair."""
    audit = run(
        json.dumps(
            {
                "representative": False,
                "reason": "this entry point is a health check that returns a constant",
            }
        )
    )

    assert audit.verdict is Representativeness.UNREPRESENTATIVE  # type: ignore[attr-defined]
    assert audit.skips_repair  # type: ignore[attr-defined]


def test_the_prompt_tells_the_auditor_which_way_to_lean_and_why() -> None:
    """A default nobody is told about is a default nobody uses."""
    assert "Answer true unless you have a positive reason not to" in QUESTION
    assert "discards real work that nobody will see was discarded" in QUESTION
    assert "Not being sure is not a reason" in QUESTION


def test_an_unanswerable_reply_does_not_silently_become_a_default() -> None:
    """**The default has to be reached deliberately.** A parser that fell back to
    *representative* on a malformed answer would make the safe default invisible —
    and the next reader could not tell a considered *yes* from a shrug."""
    with pytest.raises(RepresentativenessError, match="does not answer true or false"):
        parse(json.dumps({"reason": "hmm"}), synthesized_fixture=False)


# ================================ one fact is computable and handed over, not judged


def test_a_synthesized_fixture_is_a_fact_rather_than_an_opinion() -> None:
    """S-7.6 records that synthesized data is uniform **by construction**, so it
    is known not to resemble a production distribution whatever the endpoint is."""
    assert synthesized(a_workload(fixture_source="synthesized from schema (4 step(s))"))
    assert not synthesized(a_workload(fixture_source="factory BookFactory"))


def test_the_synthesis_fact_reaches_the_report_separately_from_the_judgement() -> None:
    """Kept apart on purpose: one is measurable and one is a guess, and a reader
    who cannot tell them apart will trust both equally."""
    audit = run(
        json.dumps({"representative": True, "reason": ""}),
        workload=a_workload(fixture_source="synthesized from schema (4 step(s))"),
    )
    described = audit.describe()  # type: ignore[attr-defined]

    assert "uniform by construction" in described
    assert "separate from the judgement above" in described


def test_a_discovered_fixture_does_not_get_the_synthesis_note() -> None:
    described = run(json.dumps({"representative": True, "reason": ""})).describe()  # type: ignore[attr-defined]

    assert "uniform by construction" not in described


# =========================== AC 3: the limitation is carried, not merely known


def test_the_report_says_what_this_verdict_cannot_mean() -> None:
    """AC 3 is not a footnote on this story, it is most of it. `08-audit.md`:
    *the agent still cannot know real traffic patterns.*"""
    described = run(json.dumps({"representative": True, "reason": ""})).describe()  # type: ignore[attr-defined]

    assert RESIDUE in described
    assert "no traffic data" in RESIDUE
    assert "never *this is not exercised*" in RESIDUE
    assert "partial fix" in RESIDUE


def test_a_skipped_finding_is_told_it_can_be_overturned() -> None:
    """Nobody sees a finding that was not investigated, so the report has to say
    out loud that this one is being dropped and on whose say-so."""
    described = run(
        json.dumps({"representative": False, "reason": "an admin-only export nobody calls"})
    ).describe()  # type: ignore[attr-defined]

    assert "no repair attempted" in described
    assert "Nobody sees a finding that was not investigated" in described
    assert "overturn this" in described


# ============================================= it goes through S-9.1's isolation


def test_the_workload_is_put_to_the_auditor_along_with_the_evidence() -> None:
    """*Is this exercised* is a question about the subject rather than about the
    measurements — and unlike every other attack in this epic, the measurements
    cannot answer it."""
    audit = run(json.dumps({"representative": True, "reason": "a public list endpoint"}))

    assert audit.reason == "a public list endpoint"  # type: ignore[attr-defined]


def test_a_reply_that_is_not_json_is_refused() -> None:
    with pytest.raises(RepresentativenessError, match="no JSON object"):
        run("I think it is probably fine.")

"""S-27.1 — the Optimizer, the prompt behind `repair.Propose`. ADR 180.

No test here reaches a model: a scripted client answers each round. What is under
test is everything around the answer -- what the model is shown, what it is not,
which candidates reach the harness, and which tier each round runs on. The cascade
tests drive the real `search`, so the check they escalate on is the one the
harness wrote into the archive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.cost.accounting import Agent, Phase, StepClass, TokenUsage
from coldfix.cost.accounting import Ledger as Bill
from coldfix.cost.budget import Budget, BudgetExhaustedError
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Router, Tier
from coldfix.evidence.ledger import Basis, Citation, Claim, Finding, Ledger, Location, Proof
from coldfix.evidence.optimizer import MAX_ROUNDS, PROPOSE, Optimizer
from coldfix.evidence.repair import (
    Apply,
    Archive,
    Candidate,
    Falsified,
    Scored,
    must_fail,
    search,
)
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS, ModelResponse
from coldfix.llm.metered import Meter
from fixtures.metering import RATE, Counted, metered

SITE = "app/models.py"
SOURCE = "class Author:\n    def books(self):\n        return Book.objects.filter(author=self)\n"
MID = DEFAULT_TIER_MODELS[Tier.MID]
FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]
SHARE = 0.784


def bare(identifier: str, median: float) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=1,
        wall=Spread(median=median, low=median, high=median),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=1024,
        read_bytes=0,
        write_bytes=0,
    )


def proven(site: str = SITE) -> Finding:
    """A finding through the only door there is: measured, recorded, attested."""
    ledger = Ledger()
    before = bare("m-before", 2.41)
    after = bare("m-after", 2.41 * (1 - SHARE))
    ledger.record(before)
    ledger.record(after)
    ledger.record(
        AblationMeasurement(
            measurement_id="m-abl",
            symbol="Author.books",
            file=site,
            line=3,
            before=before,
            after=after,
            share_removed=SHARE,
            output_changed=True,
        )
    )
    return ledger.attest(
        Claim(
            kind="repeated_query",
            summary="the serializer reads .books inside the loop",
            location=Location(file=site, line=3, symbol="Author.books"),
            evidence=(
                Citation(measurement_id="m-before", field="cpu_s", value=2.41),
                Citation(measurement_id="m-abl", field="share_removed", value=SHARE),
            ),
            basis=Basis.ABLATION,
            payoff=SHARE,
            proof=Proof(
                measurement_id="m-abl", before=2.41, after=2.41 * (1 - SHARE), share_removed=SHARE
            ),
        )
    )


TEST = "def test_books_prefetched(): assert queries() == 1"


def gate() -> Falsified:
    return must_fail(TEST, lambda _: (1, "AssertionError: expected 1 query, got 161"))


def diff(new: str = "        return self._books", *, path: str = SITE) -> str:
    return (
        f"--- a/{path}\n+++ b/{path}\n@@ -3,1 +3,1 @@\n"
        f"-        return Book.objects.filter(author=self)\n+{new}\n"
    )


def reply(*items: tuple[str, str], **extra: str) -> str:
    return json.dumps(
        {"candidates": [{"approach": name, "diff": change, **extra} for name, change in items]}
    )


def one(round_number: int) -> str:
    """A round with one fresh candidate, different in both name and edit."""
    return reply((f"idea {round_number}", diff(f"        return self._books_{round_number}")))


BASELINE = Scored(
    candidate=Candidate(identifier="baseline", approach="baseline", diff=""),
    measurement_id="m-base",
    outputs_match=True,
    tests_pass=True,
    wall_s=8.42,
    peak_rss_bytes=1024,
)


def measured(wall: float, *, tests_pass: bool = True) -> Apply:
    """The harness's side, scripted: every candidate measures `wall` seconds."""

    def apply(_: Falsified, candidate: Candidate) -> Scored:
        return Scored(
            candidate=candidate,
            measurement_id=f"m-{candidate.identifier}",
            wall_s=wall,
            peak_rss_bytes=1024,
            outputs_match=True,
            tests_pass=tests_pass,
        )

    return apply


@dataclass
class Scripted:
    """Answers each round in order; an exhausted script offers nothing."""

    replies: list[str]
    stop_reason: str = "end_turn"
    models: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    caps: list[int] = field(default_factory=list)

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.models.append(str(kwargs["model"]))
        self.asked.append(str(kwargs["messages"][0]["content"]))
        self.caps.append(int(kwargs["max_tokens"]))
        text = self.replies.pop(0) if self.replies else reply()
        return ModelResponse(
            model=str(kwargs["model"]),
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason=self.stop_reason,
        )


def optimizer(client: Scripted, meter: Meter | None = None, *, site: str = SITE) -> Optimizer:
    return Optimizer(
        meter=meter or metered(client), finding=proven(site), source=SOURCE, falsified=gate()
    )


def first_round(client: Scripted) -> tuple[Optimizer, tuple[Candidate, ...]]:
    proposer = optimizer(client)
    return proposer, proposer(Archive(baseline=BASELINE))


def run(client: Scripted, apply: Apply, proposer: Optimizer | None = None) -> Archive:
    return search(
        falsified=gate(), baseline=BASELINE, propose=proposer or optimizer(client), apply=apply
    )


# ------------------------------------------------------------ what a round yields


def test_a_round_hands_the_search_what_the_model_wrote() -> None:
    client = Scripted([reply(("prefetch", diff()), ("cache", diff("        return self._c")))])
    _, candidates = first_round(client)
    assert [c.approach for c in candidates] == ["prefetch", "cache"]
    assert candidates[0].diff == diff()


def test_the_identifier_is_the_harness_s_not_the_model_s() -> None:
    """An identifier the model chose could collide with, or impersonate, one the
    archive already holds."""
    client = Scripted([reply(("prefetch", diff()), identifier="baseline")])
    _, candidates = first_round(client)
    assert [c.identifier for c in candidates] == ["c1"]


def test_identifiers_continue_from_what_the_archive_already_holds() -> None:
    """A search seeded with earlier candidates must not mint ids that collide with
    them: the state keys candidates by id, so a second `c1` overwrites the first.

    Asserted against a seeded archive rather than an empty one, because numbering
    from the round gives `c1` either way on a first search and proves nothing.
    """
    seed = Scored(
        candidate=Candidate(identifier="c1", approach="prefetch", diff=diff()),
        measurement_id="m-c1",
        outputs_match=True,
        tests_pass=True,
        wall_s=9.0,
        peak_rss_bytes=1024,
    )
    client = Scripted([reply(("cache", diff("        return self._c")))])
    (offered,) = optimizer(client)(Archive(baseline=BASELINE, scored=(seed,)))

    assert offered.identifier == "c2"


def test_a_rationale_in_the_reply_reaches_nothing() -> None:
    """Isolation by absence: `Candidate` has no field for a reason, so the
    Adversary downstream cannot be shown one however hard the model argues."""
    client = Scripted([reply(("prefetch", diff()), rationale="trust me, this is safe")])
    _, candidates = first_round(client)
    assert candidates
    assert "trust me" not in repr(candidates)


def test_the_question_carries_the_finding_the_source_and_the_failed_test() -> None:
    client = Scripted([reply(("prefetch", diff()))])
    first_round(client)
    asked = client.asked[0]
    for expected in (SITE, "reads .books inside the loop", SOURCE, TEST, "expected 1 query"):
        assert expected in asked


def test_a_round_is_a_patch_step_billed_to_the_optimizer_with_room_to_think() -> None:
    client = Scripted([reply(("prefetch", diff()))])
    proposer, _ = first_round(client)
    (bill,) = proposer.meter.budget.ledger.calls
    assert (bill.agent, bill.phase, bill.step_class) == (
        Agent.OPTIMIZER,
        Phase.REPAIR,
        StepClass.MECHANICAL,
    )
    assert client.models == [MID]
    assert client.caps == [NON_STREAMING_MAX_TOKENS]


# ------------------------------------------------- refused before it is measured

OUTSIDE = diff(path="app/views.py")
PROTECTED = diff(path="tests/test_models.py")
NO_FILE = "this is not a diff at all"


@pytest.mark.parametrize(
    ("approach", "change", "because"),
    [
        ("widen", OUTSIDE, "app/views.py"),
        ("edit the test", PROTECTED, "protected"),
        ("prose", NO_FILE, "touches no file"),
        ("   ", diff(), "blank"),
    ],
)
def test_a_candidate_outside_what_may_change_is_refused_with_its_reason(
    approach: str, change: str, because: str
) -> None:
    client = Scripted([reply((approach, change)), reply(("prefetch", diff()))])
    proposer, candidates = first_round(client)
    assert [c.approach for c in candidates] == ["prefetch"]
    (refused,) = proposer.refusals
    assert because in refused.reason
    assert because in client.asked[1], "the next round is told why"


def test_a_protected_file_is_refused_even_where_the_finding_points() -> None:
    """A finding may sit in a factory or a fixture -- slow test setup is real
    waste, and it can be reported. But a change to what judges changes is never
    measured, whichever file the evidence names; the scope check alone would let
    this one through."""
    site = "tests/factories.py"
    client = Scripted([reply(("faster factory", diff(path=site)))] * MAX_ROUNDS)
    proposer = optimizer(client, site=site)
    assert proposer(Archive(baseline=BASELINE)) == ()
    assert "protected" in proposer.refusals[0].reason


def test_the_same_edit_under_a_new_name_is_measured_once() -> None:
    """F12: the label is the one part a model can change while changing nothing."""
    client = Scripted(
        [reply(("prefetch", diff()), ("eager load", diff("        return self._books ")))]
    )
    proposer, candidates = first_round(client)
    assert [c.approach for c in candidates] == ["prefetch"]
    assert "same edit" in proposer.refusals[0].reason


def test_an_edit_already_measured_is_not_measured_again() -> None:
    client = Scripted([reply(("prefetch", diff())), reply(("renamed", diff())), one(3)])
    archive = run(client, measured(9.0))
    assert [e.candidate.approach for e in archive.scored] == ["prefetch", "idea 3"]


def test_a_name_already_measured_is_refused_rather_than_ending_the_search() -> None:
    """`search` stops when a round offers nothing new. Refused here, the name is a
    spent candidate and the next round can still be asked."""
    client = Scripted(
        [reply(("prefetch", diff())), reply(("prefetch", diff("        x = 1"))), one(3)]
    )
    archive = run(client, measured(9.0))
    assert [e.candidate.approach for e in archive.scored] == ["prefetch", "idea 3"]


# ---------------------------------------------- a round that yields nothing


def test_a_round_with_nothing_measurable_is_spent_and_the_next_is_asked_at_once() -> None:
    """Returning nothing would end the search while rounds remained."""
    client = Scripted([reply(("widen", OUTSIDE)), reply(("prefetch", diff()))])
    proposer, candidates = first_round(client)
    assert [c.approach for c in candidates] == ["prefetch"]
    assert len(proposer.rounds) == 2


@pytest.mark.parametrize(
    ("stop_reason", "because"),
    [("max_tokens", f"cut off at {NON_STREAMING_MAX_TOKENS} tokens"), ("refusal", "declined")],
)
def test_a_cut_off_or_declined_round_is_never_read(stop_reason: str, because: str) -> None:
    """The reply is a whole, valid round -- so reading it before checking would
    hand the harness candidates the model had not finished."""
    client = Scripted([reply(("prefetch", diff()))] * MAX_ROUNDS, stop_reason=stop_reason)
    proposer, candidates = first_round(client)
    assert candidates == ()
    assert because in proposer.refusals[0].reason


def test_no_more_than_three_rounds_are_asked() -> None:
    client = Scripted(["not json"] * 10)
    proposer, candidates = first_round(client)
    assert candidates == ()
    assert proposer(Archive(baseline=BASELINE)) == ()
    assert len(client.models) == MAX_ROUNDS


def test_the_search_measures_at_most_eight() -> None:
    """Three, three, then two: the last round is asked for what the limit leaves."""
    rounds = [
        reply(*((f"idea {r}{i}", diff(f"        return {r}{i}")) for i in range(3)))
        for r in range(3)
    ]
    client = Scripted(rounds)
    archive = run(client, measured(9.0))
    assert len(archive.scored) == 8
    assert "up to 2 candidates" in client.asked[2]


def test_the_budget_refusing_a_round_is_not_softened() -> None:
    client = Scripted([reply(("prefetch", diff()))])
    proposer = optimizer(client, metered(client, ceiling_eur=Decimal("0.000001")))
    with pytest.raises(BudgetExhaustedError):
        proposer(Archive(baseline=BASELINE))
    assert client.models == []


# ------------------------------------------------ the cascade runs on measurement


def test_two_rounds_whose_tests_failed_escalate_the_third() -> None:
    """§12.3's *mid → frontier*, and the check is `Scored.tests_pass` -- written
    by the harness, never said by the model."""
    client = Scripted([one(1), one(2), one(3)])
    proposer = optimizer(client)
    run(client, measured(9.0, tests_pass=False), proposer)
    assert client.models == [MID, MID, FRONTIER]
    assert [r.escalation for r in proposer.rounds] == [0, 0, 1]


def test_a_round_whose_tests_passed_keeps_the_tier() -> None:
    """Slower but passing is not a failed check: the cheaper model wrote working
    code, and the search -- not the tier -- is what finds a faster one."""
    client = Scripted([one(1), one(2), one(3)])
    run(client, measured(9.0))
    assert client.models == [MID, MID, MID]


def test_rounds_that_yielded_nothing_count_as_rounds_that_passed_nothing() -> None:
    client = Scripted(["not json", "not json", one(3)])
    _, candidates = first_round(client)
    assert [c.approach for c in candidates] == ["idea 3"]
    assert client.models == [MID, MID, FRONTIER]


def test_on_the_dearest_tier_already_the_rounds_stay_there() -> None:
    """Configuration may route repair to the frontier. Then there is nothing to
    escalate to, and the round limit is what bounds the spend."""
    client = Scripted(["not json"] * MAX_ROUNDS)
    router = Router(tiers={StepClass.CREATIVE: Tier.FRONTIER, StepClass.MECHANICAL: Tier.FRONTIER})
    meter = Meter(
        client=client, counter=Counted(), router=router, budget=Budget(ledger=Bill(), rate=RATE)
    )
    optimizer(client, meter)(Archive(baseline=BASELINE))
    assert client.models == [FRONTIER] * MAX_ROUNDS


# ------------------------------------------------ what the next round is told


def test_the_next_round_is_told_what_each_attempt_measured() -> None:
    """Numbers and outcomes, not labels alone: what makes the next round different
    is knowing what each attempt did."""
    client = Scripted([one(1), one(2)])
    run(client, measured(9.0))
    told = client.asked[1]
    assert "idea 1: measured no faster (9.000s)" in told
    assert "8.420s" in told


def test_the_winner_is_chosen_by_measurement_not_by_the_order_proposed() -> None:
    """The end-to-end claim of E22 with a real proposer: the model's first answer
    is not privileged; the fastest one that broke nothing is."""
    client = Scripted(
        [
            reply(
                ("elegant rewrite", diff("        return a")),
                ("ugly hack", diff("        return b")),
            )
        ]
    )

    def apply(falsified: Falsified, candidate: Candidate) -> Scored:
        wall = 7.0 if candidate.approach == "elegant rewrite" else 3.0
        return measured(wall)(falsified, candidate)

    archive = run(client, apply)
    assert archive.winner is not None
    assert archive.winner.candidate.approach == "ugly hack"
    assert PROPOSE.agent is Agent.OPTIMIZER

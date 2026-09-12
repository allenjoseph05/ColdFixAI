"""S-28.6 — the failing test, written and then run. ADR 185.

Nothing here reads a test and decides it looks right, which is the point:
`must_fail` runs what the model wrote, and only what that returns mints the token
a patch needs. The cascade is real too -- two attempts on the routed tier and
then one rung dearer -- because §3's check for this step *is* the gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from coldfix.cost.accounting import Agent, Phase, StepClass, TokenUsage
from coldfix.cost.budget import BudgetExhaustedError
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Tier
from coldfix.evidence.falsify import ATTEMPTS, SYSTEM, NoTestError, falsify, question
from coldfix.evidence.ledger import Citation, Claim, Finding, Location
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS, ModelResponse
from fixtures.metering import metered

MID = DEFAULT_TIER_MODELS[Tier.MID]
FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]

SOURCE = "class Author:\n    def books(self):\n        return Book.objects.filter(author=self)\n"
TEST = "def test_one_query():\n    assert queries() == 1\n"


def finding() -> Finding:
    return Finding(
        claim=Claim(
            kind="repeated_query",
            summary="the serializer reads .books inside the loop, so each row is a SELECT",
            location=Location(file="app/models.py", line=112, symbol="Author.books"),
            evidence=(Citation(measurement_id="m-1", field="wall.median", value=2.41),),
        ),
        attested_against=("m-1",),
    )


def reply(test: str | None = TEST, **extra: Any) -> str:
    return json.dumps({"test": test, "counts": "SELECTs", **extra})


@dataclass
class Scripted:
    """Answers each attempt in order, and records how each was asked."""

    replies: list[str]
    stop_reason: str = "end_turn"
    asked: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    caps: list[int] = field(default_factory=list)

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.asked.append(str(kwargs["messages"]))
        self.models.append(str(kwargs["model"]))
        self.caps.append(int(kwargs["max_tokens"]))
        return ModelResponse(
            model=str(kwargs["model"]),
            text=self.replies.pop(0) if self.replies else reply(),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason=self.stop_reason,
        )


def fails(_: str) -> tuple[int, str]:
    return 1, "AssertionError: expected 1 query, got 161"


def passes(_: str) -> tuple[int, str]:
    return 0, ""


def never_ran(_: str) -> tuple[int, str]:
    return 1, "ImportError: no module named app"


def write(client: Scripted, run: Any = fails, **extra: Any) -> Any:
    return falsify(metered(client, **extra), finding=finding(), source=SOURCE, run=run)


# ------------------------------------------------------- the gate, not the reader


def test_a_test_that_fails_on_unpatched_code_mints_the_token() -> None:
    proof = write(Scripted([reply()]))
    assert proof.test == TEST
    assert proof.exit_code == 1
    assert "161" in proof.detail


def test_a_test_that_passes_proves_the_problem_is_absent_and_is_refused() -> None:
    """A test that passes before anything changed is testing something else, and
    a token minted from it would authorise a patch for a defect nobody saw."""
    client = Scripted([reply()] * ATTEMPTS)
    with pytest.raises(NoTestError, match="no test that fails"):
        write(client, passes)
    assert len(client.models) == ATTEMPTS


def test_a_test_that_did_not_run_is_refused_rather_than_counted_as_failure() -> None:
    """A broken file also exits non-zero. Under *non-zero means it failed* a
    syntax error would authorise patching -- the gate inverted."""
    client = Scripted([reply()] * ATTEMPTS)
    with pytest.raises(NoTestError):
        write(client, never_ran)
    assert "did not run" in client.asked[1], "the next attempt is told which way it failed"


def test_the_reason_a_test_was_refused_reaches_the_next_attempt() -> None:
    client = Scripted([reply(), reply()])
    runs = iter([passes("x"), fails("x")])
    write(client, lambda _: next(runs))
    assert "it passed" in client.asked[1]


# ------------------------------------------------------------------- the cascade


def test_two_attempts_run_cheap_and_the_third_runs_dearer() -> None:
    """§3 gives this step a mechanical check -- *fails on unpatched code* -- so it
    routes below the frontier and escalates rather than starting there."""
    client = Scripted([reply()] * ATTEMPTS)
    with pytest.raises(NoTestError):
        write(client, passes)
    assert client.models == [MID, MID, FRONTIER]


def test_a_test_accepted_on_the_first_attempt_never_escalates() -> None:
    client = Scripted([reply()])
    write(client)
    assert client.models == [MID]


# ------------------------------------------------- what is not worth retrying


def test_declining_because_nothing_is_countable_stops_at_once() -> None:
    """A finding whose mechanism predicts no count cannot be falsified by
    counting, and asking twice more would not change that."""
    client = Scripted([reply(None, because="the cost is spread across the interpreter")])
    with pytest.raises(NoTestError, match="spread across the interpreter"):
        write(client)
    assert len(client.models) == 1


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_declined_or_cut_off_attempt_is_spent_and_never_read(stop_reason: str) -> None:
    client = Scripted([reply()] * ATTEMPTS, stop_reason=stop_reason)
    with pytest.raises(NoTestError):
        write(client)
    assert len(client.models) == ATTEMPTS


def test_an_unreadable_reply_is_spent_and_the_next_attempt_is_asked() -> None:
    client = Scripted(["not json at all", reply()])
    assert write(client).test == TEST
    assert len(client.models) == 2


def test_the_budget_refusing_an_attempt_is_not_softened() -> None:
    client = Scripted([reply()])
    with pytest.raises(BudgetExhaustedError):
        write(client, ceiling_eur=Decimal("0.000001"))
    assert client.models == []


# ------------------------------------------------------- what it is asked, and how


def test_the_attempt_is_billed_to_the_optimizer_with_room_to_think() -> None:
    client = Scripted([reply()])
    meter = metered(client)
    falsify(meter, finding=finding(), source=SOURCE, run=fails)
    (bill,) = meter.budget.ledger.calls
    assert (bill.agent, bill.phase, bill.step_class) == (
        Agent.OPTIMIZER,
        Phase.REPAIR,
        StepClass.MECHANICAL,
    )
    assert client.caps == [NON_STREAMING_MAX_TOKENS]


def test_it_is_shown_the_finding_and_the_source() -> None:
    asked = question(finding(), SOURCE, ())
    assert "app/models.py:112" in asked
    assert "each row is a SELECT" in asked
    assert SOURCE in asked
    assert "m-1.wall.median = 2.41" in asked


def test_the_prompt_asks_for_a_count_and_forbids_a_duration() -> None:
    """The rule this story turns on: a threshold in seconds passes on a quiet
    machine and fails on a busy one, so it cannot tell a fix from an idle
    afternoon."""
    assert "COUNT SOMETHING. NEVER TIME ANYTHING." in SYSTEM
    assert "under two seconds" in SYSTEM, "it says why, not just what"

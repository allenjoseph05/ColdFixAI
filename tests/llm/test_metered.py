"""S-26.1 — every v3 model call is routed, counted, authorized, then billed.

The meter is four existing parts -- the router, the budget, the ledger and the
worst-case price -- put in front of every v3 call. What these tests pin is the
order and the absences: the model is derived and never chosen, the count is taken
before the budget is asked, the budget is asked before anything is sent, and the
bill lands on the one ledger the budget reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from coldfix.cost.accounting import Agent, Ledger, Phase, StepClass, TokenUsage
from coldfix.cost.budget import Budget, BudgetExhaustedError, worst_case_usd
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Router, StepType, Tier, UnsafeRoutingError
from coldfix.llm.client import AnthropicClient, ModelResponse
from coldfix.llm.metered import Call, Meter
from fixtures.metering import RATE, Counted, metered

FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]
CHEAP = DEFAULT_TIER_MODELS[Tier.CHEAP]

HYPOTHESIS = Call(
    step=StepType.HYPOTHESIS_GENERATION, phase=Phase.INVESTIGATE, agent=Agent.SCAN, max_tokens=1000
)
EXPLORE = Call(step=StepType.EXPLORER_ACTION, phase=Phase.GROUND, agent=Agent.SCAN, max_tokens=1000)
AUDIT = Call(
    step=StepType.ATTACK_DESIGN,
    phase=Phase.FINDING_AUDIT,
    agent=Agent.FINDING_AUDITOR,
    max_tokens=1000,
)


@dataclass
class Recorder:
    """Answers every call, and records the order things happened in."""

    events: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    stop_reason: str = "end_turn"

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.events.append("called")
        self.models.append(str(kwargs["model"]))
        return ModelResponse(
            model=str(kwargs["model"]),
            text="ok",
            usage=TokenUsage(input_tokens=100, output_tokens=20),
            stop_reason=self.stop_reason,
        )


@dataclass
class OrderedCounter(Counted):
    """A counter that writes into the same event list as the client."""

    events: list[str] = field(default_factory=list)

    def count_tokens(self, *, model: str, system: str, messages: Any) -> int:
        self.events.append("counted")
        return super().count_tokens(model=model, system=system, messages=messages)


def ask(meter: Meter, call: Call = HYPOTHESIS) -> ModelResponse:
    return meter.complete(
        call, system="s", messages=[{"role": "user", "content": "q"}], temperature=0.0
    )


# ------------------------------------------------------------ the route is derived


def test_the_step_type_decides_the_model() -> None:
    """Grounding is checked by the harness, so it may run cheap; choosing an
    experiment and attacking a finding have no deterministic check, so they may
    not."""
    client = Recorder()
    meter = metered(client)
    ask(meter, EXPLORE)
    ask(meter, HYPOTHESIS)
    ask(meter, AUDIT)
    assert client.models == [CHEAP, FRONTIER, FRONTIER]


def test_a_caller_cannot_name_the_model() -> None:
    """The absence is the enforcement. A field here would let a call site put
    hypothesis generation on the cheapest model by asking for it."""
    assert "model" not in Call.__dataclass_fields__


def test_creative_work_cannot_be_configured_below_the_frontier() -> None:
    """Configuration may move a step dearer and never a creative one cheaper --
    the router refuses to be built that way, so no meter can hold one."""
    with pytest.raises(UnsafeRoutingError, match="creative work"):
        Router(tiers={StepClass.CREATIVE: Tier.CHEAP, StepClass.MECHANICAL: Tier.MID})


def test_the_count_is_taken_against_the_model_that_will_run() -> None:
    """Counts are model-specific, so counting against any other model would be
    authorizing one prompt and sending another."""
    meter = metered(Recorder())
    ask(meter, EXPLORE)
    assert cast("Counted", meter.counter).asked == [CHEAP]


# ------------------------------------------------ counted, authorized, then sent


def test_the_prompt_is_counted_before_anything_is_sent() -> None:
    client = Recorder()
    meter = Meter(
        client=client,
        counter=OrderedCounter(events=client.events),
        router=Router(),
        budget=Budget(ledger=Ledger(), rate=RATE),
    )
    ask(meter)
    assert client.events == ["counted", "called"]
    assert len(meter.budget.ledger.calls) == 1, "and billed once it came back"


def test_a_refused_call_is_never_sent() -> None:
    """After would mean the call that broke the budget was already paid for."""
    client = Recorder()
    meter = metered(client, ceiling_eur=Decimal("0.000001"))
    with pytest.raises(BudgetExhaustedError):
        ask(meter)
    assert client.events == []
    assert meter.budget.ledger.calls == []


def test_the_worst_case_authorized_is_the_measured_count() -> None:
    """The same call is allowed at a small counted size and refused at a large
    one, under the same ceiling -- so the count, not a guess, is what the ceiling
    is checked against."""
    small = worst_case_usd(FRONTIER, 100, HYPOTHESIS.max_tokens)
    large = worst_case_usd(FRONTIER, 100_000, HYPOTHESIS.max_tokens)
    ceiling = RATE.convert((small + large) / 2)

    allowed = Recorder()
    ask(metered(allowed, ceiling_eur=ceiling, tokens=100))
    assert allowed.events == ["called"]

    refused = Recorder()
    with pytest.raises(BudgetExhaustedError):
        ask(metered(refused, ceiling_eur=ceiling, tokens=100_000))
    assert refused.events == []


# ---------------------------------------------------------------- one bill


def test_every_call_is_billed_with_who_spent_it_and_on_what() -> None:
    meter = metered(Recorder())
    ask(meter, EXPLORE)
    ask(meter, AUDIT)
    calls = meter.budget.ledger.calls
    assert [call.agent for call in calls] == [Agent.SCAN, Agent.FINDING_AUDITOR]
    assert [call.phase for call in calls] == [Phase.GROUND, Phase.FINDING_AUDIT]
    assert [call.step_class for call in calls] == [StepClass.MECHANICAL, StepClass.CREATIVE]
    assert [call.model for call in calls] == [CHEAP, FRONTIER]
    assert all(call.usage.output_tokens == 20 for call in calls)
    assert meter.budget.spent_eur == RATE.convert(meter.budget.ledger.total_usd) > 0


def test_two_meters_on_one_budget_share_one_bill() -> None:
    """ADR 170: a ceiling that sees part of the spend is not a ceiling."""
    budget = Budget(ledger=Ledger(), rate=RATE)
    first = Meter(client=Recorder(), counter=Counted(), router=Router(), budget=budget)
    second = Meter(client=Recorder(), counter=Counted(), router=Router(), budget=budget)
    ask(first, HYPOTHESIS)
    ask(second, AUDIT)
    assert len(budget.ledger.calls) == 2


def test_a_refusal_is_billed_like_any_other_call() -> None:
    """The API reports usage for a decline, and a ledger that dropped declined
    calls would under-report the run."""
    meter = metered(Recorder(stop_reason="refusal"))
    assert ask(meter).refused
    assert len(meter.budget.ledger.calls) == 1


# ------------------------------------------------------ the live client counts


def test_the_live_client_counts_through_the_api() -> None:
    """The count comes from the endpoint that will bill the call. A fake SDK
    stands in for the network; what is checked is what the client asks it."""

    class Counts:
        def __init__(self) -> None:
            self.asked: dict[str, object] = {}

        def count_tokens(self, **kwargs: object) -> SimpleNamespace:
            self.asked = kwargs
            return SimpleNamespace(input_tokens=321)

    sdk = SimpleNamespace(messages=Counts())
    client = AnthropicClient(client=cast("Any", sdk))
    counted = client.count_tokens(
        model=FRONTIER, system="s", messages=[{"role": "user", "content": "q"}]
    )
    assert counted == 321
    assert sdk.messages.asked["model"] == FRONTIER
    assert sdk.messages.asked["system"] == "s"

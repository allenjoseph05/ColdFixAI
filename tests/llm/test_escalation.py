"""S-27.1 — the meter escalates only dearer, and only where a check exists. ADR 180.

`Call` still has no model field. What a caller may ask is *n rungs dearer than the
router chose*, and the tests below attempt every way that could be abused: going
cheaper, escalating a step nothing can check, and escalating past the top. Each
refusal must happen before anything is sent -- and an escalated call must be
authorized and billed at the price of the model it actually ran on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.budget import BudgetExhaustedError, worst_case_usd
from coldfix.cost.cascade import NoDearerTierError, NoValidatorError
from coldfix.cost.routing import DEFAULT_TIER_MODELS, StepType, Tier, UnsafeRoutingError
from coldfix.llm.client import ModelResponse
from coldfix.llm.metered import Call, Meter
from fixtures.metering import RATE, metered

MID = DEFAULT_TIER_MODELS[Tier.MID]
FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]
COUNTED = 100
"""What `metered`'s counter reports for every prompt."""

PATCH = Call(step=StepType.PATCH, phase=Phase.REPAIR, agent=Agent.OPTIMIZER, max_tokens=1000)
HYPOTHESIS = Call(
    step=StepType.HYPOTHESIS_GENERATION, phase=Phase.INVESTIGATE, agent=Agent.SCAN, max_tokens=1000
)


@dataclass
class Recorder:
    models: list[str] = field(default_factory=list)

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.models.append(str(kwargs["model"]))
        return ModelResponse(
            model=str(kwargs["model"]),
            text="ok",
            usage=TokenUsage(input_tokens=100, output_tokens=20),
            stop_reason="end_turn",
        )


def ask(meter: Meter, call: Call = PATCH, *, escalation: int = 0) -> ModelResponse:
    return meter.complete(
        call,
        system="s",
        messages=[{"role": "user", "content": "q"}],
        temperature=0.0,
        escalation=escalation,
    )


def test_a_patch_runs_where_the_router_sends_it() -> None:
    """The control: mechanical repair work routes to mid, unescalated."""
    client = Recorder()
    ask(metered(client))
    assert client.models == [MID]


def test_one_rung_of_escalation_reaches_the_frontier() -> None:
    """§12.3's *repair: cascade mid → frontier*."""
    client = Recorder()
    ask(metered(client), escalation=1)
    assert client.models == [FRONTIER]


def test_escalation_past_the_dearest_tier_is_refused_and_nothing_is_sent() -> None:
    client = Recorder()
    with pytest.raises(NoDearerTierError):
        ask(metered(client), escalation=2)
    assert client.models == []


def test_a_step_with_no_check_cannot_be_escalated() -> None:
    """Hypothesis generation already runs on the frontier, so this is about the
    principle rather than the price: escalation needs a failed check, and §3 says
    this step has none. The same refusal `cascade` makes."""
    client = Recorder()
    with pytest.raises(NoValidatorError):
        ask(metered(client), HYPOTHESIS, escalation=1)
    assert client.models == []


def test_an_escalation_cannot_route_cheaper() -> None:
    client = Recorder()
    with pytest.raises(UnsafeRoutingError):
        ask(metered(client), escalation=-1)
    assert client.models == []


def test_the_model_can_be_asked_without_being_called() -> None:
    """`model_for` answers the same question `complete` acts on, so a caller
    deciding whether to escalate can ask without spending."""
    client = Recorder()
    meter = metered(client)
    assert meter.model_for(PATCH, escalation=1) == FRONTIER
    assert client.models == []


def test_an_escalated_call_is_authorized_at_the_dearer_price() -> None:
    """A ceiling between the two tiers' worst cases admits the routed call and
    refuses the escalated one. Authorized at the routed price, an escalation could
    spend past a ceiling that was checked against a cheaper model."""
    routed_worst = RATE.convert(worst_case_usd(MID, COUNTED, PATCH.max_tokens))
    escalated_worst = RATE.convert(worst_case_usd(FRONTIER, COUNTED, PATCH.max_tokens))
    between = (routed_worst + escalated_worst) / 2

    routed = Recorder()
    ask(metered(routed, ceiling_eur=between))
    assert routed.models == [MID]

    escalated = Recorder()
    with pytest.raises(BudgetExhaustedError):
        ask(metered(escalated, ceiling_eur=between), escalation=1)
    assert escalated.models == []


def test_an_escalated_call_is_billed_at_the_model_it_ran_on() -> None:
    """Billed at the routed model, the dearest calls would be the cheapest lines
    on the bill."""
    meter = metered(Recorder())
    ask(meter, escalation=1)
    assert [call.model for call in meter.budget.ledger.calls] == [FRONTIER]

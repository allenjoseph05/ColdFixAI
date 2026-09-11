"""A meter for tests: the real router, budget and ledger, and a counter that answers.

Only the token count is a double, because counting needs the API. Everything that
decides -- which model, whether the call may be made, what it cost -- is the
production code, so a test driven through this meter exercises the routing and the
budget rather than a stand-in for them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from anthropic.types import MessageParam

from coldfix.cost.accounting import ExchangeRate, Ledger
from coldfix.cost.budget import Budget
from coldfix.cost.routing import Router
from coldfix.llm.client import ModelClient
from coldfix.llm.metered import Meter

RATE = ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 9, 11))


@dataclass
class Counted:
    """Answers every count with the same size, and records which model was asked."""

    tokens: int = 100
    asked: list[str] = field(default_factory=list)

    def count_tokens(self, *, model: str, system: str, messages: Sequence[MessageParam]) -> int:
        self.asked.append(model)
        return self.tokens


def metered(
    client: ModelClient,
    *,
    ceiling_eur: Decimal | None = None,
    tokens: int = 100,
    ledger: Ledger | None = None,
) -> Meter:
    """A meter over `client`, with the default routing and an optional ceiling."""
    return Meter(
        client=client,
        counter=Counted(tokens),
        router=Router(),
        budget=Budget(ledger=ledger or Ledger(), rate=RATE, ceiling_eur=ceiling_eur),
    )

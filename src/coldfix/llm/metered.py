"""Every v3 model call: routed, counted, authorized before it is made, and billed.

S-26.1, ADR 176. v3's scan agent and finding auditor called `client.complete`
with a model their caller chose and nothing checking what it cost -- `scan`
defaulted to `NoBudget`, which counts nothing and refuses nothing. The cost
controls Epic 5 built existed, and v3 went round them.

**The money parts are reused; the prompt assembly is not.** `cost.session.Session`
is Epic 5's assembly, and it assumes v1's prompt: five segments, a playbook and a
source it refuses to leave blank, a question rendered into blocks. v3 has no
playbook and gives its agents no source -- the scan agent's prompt is a
conversation that grows by appending. So this takes the four parts that are about
money rather than about prompt shape -- `Router`, `Budget`, `Ledger` and
`worst_case_usd` -- and leaves the rest where it is.

**Routed, counted, authorized, called, billed -- in that order.**

- The model comes from the step type, and `Call` has no field through which a
  caller could name one. So hypothesis generation cannot be relabelled onto a
  cheap model: the router derives the class from the step and refuses to send
  creative work below the frontier tier (ADR 058, ADR 059).
- The prompt is **counted by the API that will bill it**, never estimated.
  `count_tokens` is free, and the worst case authorized is that count at the
  dearest input rate plus the whole output cap. A ceiling checked against a
  guessed size holds only when the guess happened to be good.
- Authorization happens before the call, so a refusal means nothing was sent.
- The call is billed to the budget's own ledger. Every meter sharing a budget
  therefore shares one bill, which is ADR 170's rule: a ceiling that sees only
  part of the spend is not a ceiling.

**Escalation is the one way off the routed tier, and it only goes up.** S-27.1,
ADR 180. A cascade retries a step on a dearer model when a deterministic check
rejected the cheaper one's work. The Optimizer is the first v3 step with such a
check -- the harness measures whether each candidate's tests pass -- and its
check runs after the search has applied a round, so it cannot use
`cost.cascade.cascade`, whose validator runs inside the call. What it asks for
instead is `escalation=n`: *n rungs dearer than the router chose*. The caller
still names no model, cannot go cheaper, and cannot escalate a step that §3's
table gives no check -- the same refusal `cascade` makes, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from anthropic.types import MessageParam

from coldfix.cost.accounting import Agent, ModelCall, Phase
from coldfix.cost.budget import Budget, worst_case_usd
from coldfix.cost.cascade import NoDearerTierError, NoValidatorError, dearer_than
from coldfix.cost.routing import STEP_KINDS, Router, StepType, UnsafeRoutingError, classify
from coldfix.llm.client import ModelClient, ModelResponse


class TokenCounter(Protocol):
    """What measures a prompt before it is sent. `AnthropicClient` is one.

    Separate from `ModelClient` rather than added to it, because every double in
    the suite satisfies `ModelClient` and only the callers that authorize spend
    need a count. Widening the protocol would make each of those doubles invent a
    count it has no business producing.
    """

    def count_tokens(self, *, model: str, system: str, messages: Sequence[MessageParam]) -> int: ...


@dataclass(frozen=True)
class Call:
    """What a caller is about to spend on. **The model is not among it.**

    The step type decides the model, through the router. A field here would let a
    call site choose one, which is exactly how the ~220 mechanical calls a run
    makes end up on the frontier model, or a creative one ends up on the cheapest.
    """

    step: StepType
    phase: Phase
    agent: Agent
    max_tokens: int
    finding_id: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Meter:
    """One run's gate on spending. Share it across the agents in a run."""

    client: ModelClient
    counter: TokenCounter
    router: Router
    budget: Budget
    clock: Callable[[], datetime] = _now

    def model_for(self, call: Call, *, escalation: int = 0) -> str:
        """The model this call will run on, derived and never chosen.

        `escalation` moves it that many tiers dearer than the router's choice.

        Raises:
            UnsafeRoutingError: a negative escalation, which would route cheaper.
            NoValidatorError: an escalation of a step §3 gives no deterministic
                check -- a dearer retry there is a second guess nothing verifies.
            NoDearerTierError: the escalation runs past the dearest tier.
        """
        if escalation < 0:
            message = (
                f"an escalation of {escalation} would route {call.step.value} below the tier the "
                "router chose. Escalation exists to retry dearer after a check failed; the "
                "router's choice is the floor"
            )
            raise UnsafeRoutingError(message)

        kind = STEP_KINDS[call.step]
        if escalation and not kind.cascade_safe:
            message = (
                f"{call.step.value} has no deterministic check (`04-cost.md` §3), so there is no "
                "failed check to escalate on. A dearer retry of it is a second guess that nothing "
                "verifies, which is what `cascade` refuses too"
            )
            raise NoValidatorError(message)

        tier = self.router.tier_for(kind.step_class, call.phase)
        for _ in range(escalation):
            dearer = dearer_than(tier)
            if dearer is None:
                message = (
                    f"{call.step.value} is already on the {tier.value} tier, the dearest "
                    f"configured, so an escalation of {escalation} has nowhere to go"
                )
                raise NoDearerTierError(message)
            tier = dearer
        return self.router.tier_models[tier]

    def complete(  # noqa: PLR0913 - the call, the prompt's three parts, the cache
        # lifetime the bill depends on, and the escalation. Each is decided by the
        # caller and none has a default a caller could safely forget.
        self,
        call: Call,
        *,
        system: str,
        messages: Sequence[MessageParam],
        temperature: float,
        cache_ttl: str = "5m",
        escalation: int = 0,
    ) -> ModelResponse:
        """Make the call if the budget allows it, and bill it.

        Raises:
            BudgetExhaustedError: a phase cap or the euro ceiling refuses the
                worst case of this call. Nothing was sent.
            UnknownModelError: the routed model has no published price, so the
                worst case cannot be computed and the call is not made.
            UnsafeRoutingError, NoValidatorError, NoDearerTierError: the
                escalation was refused. Nothing was sent. See `model_for`.
        """
        model = self.model_for(call, escalation=escalation)
        prompt_tokens = self.counter.count_tokens(model=model, system=system, messages=messages)
        self.budget.authorize(
            call.phase,
            call.finding_id,
            worst_case_usd(model, prompt_tokens, call.max_tokens),
        )

        response = self.client.complete(
            model=model,
            system=system,
            messages=messages,
            max_tokens=call.max_tokens,
            temperature=temperature,
            cache_ttl=cache_ttl,
        )

        # Billed at the model the call ran on, escalated or not, which is the one
        # the price book and the router agree on. A refusal is billed too: the API
        # reports its usage, and a ledger that dropped declined calls would
        # under-report the run.
        self.budget.ledger.record(
            ModelCall(
                phase=call.phase,
                agent=call.agent,
                step_class=classify(call.step),
                model=model,
                usage=response.usage,
                at=self.clock(),
                finding_id=call.finding_id,
            )
        )
        return response

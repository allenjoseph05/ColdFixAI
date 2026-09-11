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

**There is no cascade here, deliberately.** A cascade retries a step on a dearer
model when a deterministic check rejects the answer. None of v3's current calls
has a check that can run before a side effect: the scan agent's answer is a tool
call whose check *is* running the tool, and the auditor's is a judgement. The
optimizer (S-27.1) is the first v3 step with a real validator -- the test passes
and the candidate measurably beats the baseline -- and it will be the first to
cascade.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from anthropic.types import MessageParam

from coldfix.cost.accounting import Agent, ModelCall, Phase
from coldfix.cost.budget import Budget, worst_case_usd
from coldfix.cost.routing import Router, StepType, classify
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

    def model_for(self, call: Call) -> str:
        """The model this call will run on, derived and never chosen."""
        return self.router.route(call.step, call.phase)

    def complete(
        self,
        call: Call,
        *,
        system: str,
        messages: Sequence[MessageParam],
        temperature: float,
        cache_ttl: str = "5m",
    ) -> ModelResponse:
        """Make the call if the budget allows it, and bill it.

        Raises:
            BudgetExhaustedError: a phase cap or the euro ceiling refuses the
                worst case of this call. Nothing was sent.
            UnknownModelError: the routed model has no published price, so the
                worst case cannot be computed and the call is not made.
        """
        model = self.model_for(call)
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

        # Billed at the routed model, which is the one the price book and the
        # router agree on. A refusal is billed too: the API reports its usage,
        # and a ledger that dropped declined calls would under-report the run.
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

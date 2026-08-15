"""Asking the model what to try next, and refusing an answer it cannot support.

Epic 8, S-8.1. **The first call to a model in this system.** Everything before it
— fourteen primitives, a sandbox, a screening pass, a whole Explorer — is
deterministic, and `00-BRIEF.md` §1 says why that is the right shape: *the
methods are well-established and mechanizable; choosing which one applies to a
given program is documented in the fault-localization literature as requiring
expert knowledge.* This is the choosing.

**Temperature 0.8, and the split is a real design decision.** `03-agents.md` §2.4:
*hypothesis generation benefits from diversity — you want unusual explanations
considered. Result interpretation must not vary — 8.24 seconds means the same
thing every time.* S-8.3 will send the other call at 0.0, and the two are
frequently the same question about the same log, which is why S-0.7b's request
digest gained the temperature when this story was written.

**Frontier tier, and no cascade, and neither is this module's choice to make.**
`CLAUDE.md`: *never cascade to a cheap model on hypothesis generation or attack
design — no deterministic validator exists for those.* S-5.5 already refuses to
route a creative step below the frontier, and S-5.6 only cascades a step whose
caller supplies a validator. So the enforcement here is an **absence**:
`generate` has no `validate` parameter, and there is therefore no way to ask for
a cascade from this call site.

**The instrument the model names is checked against the instruments it was
offered.** A hypothesis proposing `flame_graph` in a project where S-3.1 withheld
it is not a hypothesis, it is a step that will fail when something tries to run
it — and it would fail two stories later, in S-8.2, with the reason lost. The
registry's `Selection` is the authority and it is consulted here.

**A refusal is not an answer.** S-0.7b established that a decline is a successful
HTTP response with an empty content list, so a caller reading `text` on a refusal
reads emptiness as brevity. This checks `refused` before it parses anything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from anthropic.types import MessageParam

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.log import ExperimentLog
from coldfix.llm.client import ModelClient
from coldfix.primitives.registry import Selection

HYPOTHESIS_TEMPERATURE = 0.8
"""`03-agents.md` §2.4. Diversity is the point: an unusual explanation that turns
out to be wrong costs one experiment, and a hypothesis nobody considered costs
the investigation."""

MAX_OUTPUT_TOKENS = 1_000
"""A hypothesis is a sentence, an instrument name and a reason. Required by
S-5.4's `Step` rather than defaulted there, because the ceiling is enforced
against it — and a cap this small is also what stops a diverse call rambling."""

_SYSTEM = """\
You are diagnosing a performance problem by running experiments. You do not read \
code to reach conclusions; you propose one hypothesis at a time and name the \
instrument that would test it.

Answer with a single JSON object and nothing else:

{"statement": "...", "primitive": "...", "rationale": "..."}

`statement` is what you believe, specifically enough to be wrong.
`primitive` is one of the instruments offered, exactly as named.
`rationale` is why this experiment is worth its cost given what is already known.

Propose the experiment that would most change your mind. Do not propose one \
whose answer is already in the log, and do not propose one an exclusion has \
already settled unless a condition it depended on has changed."""

# A model asked for JSON returns JSON, a fenced block, or JSON with a sentence in
# front of it. The first balanced object is taken, and nothing is repaired: a
# response this cannot parse is reported with the text, because "the model
# answered something else" is a different problem from "the model was wrong".
_JSON = re.compile(r"\{.*\}", re.DOTALL)


class HypothesisError(Exception):
    """No usable hypothesis came back."""


@dataclass(frozen=True)
class Hypothesis:
    """What to test next, and what would test it.

    Frozen because it is about to be written into an append-only log, and a
    record that can be edited after the experiment ran is a record of what
    somebody wishes had been proposed.
    """

    statement: str
    primitive: str
    rationale: str

    def describe(self) -> str:
        return f"{self.statement}\n  test with: {self.primitive}\n  because: {self.rationale}"


def render_question(
    *,
    log: ExperimentLog,
    exclusions: Sequence[str],
    source: str,
    instruments: Selection,
) -> str:
    """AC 2's four inputs, in the order they are read.

    Exclusions arrive as rendered statements rather than as objects. S-8.5 owns
    what an exclusion *is* — its preconditions, and when a later experiment makes
    it stale — and this call needs only the sentence, so taking the structure
    here would be this story fixing a shape S-8.5 has not designed yet.

    The instruments come from S-3.1's `Selection`, which is a snapshot: ADR 002
    forbids the tool list moving mid-investigation, so what is offered here is
    what was offered when the investigation started.
    """
    offered = "\n".join(f"  - {name}" for name in instruments.names) or "  (none)"
    excluded = "\n".join(f"  - {entry}" for entry in exclusions) or "  (none yet)"
    return (
        f"SOURCE UNDER SUSPICION\n{source}\n\n"
        f"INSTRUMENTS AVAILABLE\n{offered}\n\n"
        f"ALREADY EXCLUDED\n{excluded}\n\n"
        f"EXPERIMENT LOG\n{log.render()}\n\n"
        "What is the next hypothesis worth testing?"
    )


def parse(text: str, instruments: Selection) -> Hypothesis:
    """Read one hypothesis out of a reply, or refuse it.

    Raises:
        HypothesisError: the reply is not a JSON object, is missing a field, or
            names an instrument this project was not offered. The last is the
            one worth naming separately: a hypothesis proposing an instrument
            S-3.1 withheld would fail in S-8.2 with the reason already lost.
    """
    found = _JSON.search(text)
    if found is None:
        message = (
            f"no hypothesis could be read from this reply: {text.strip()[:300]!r}. Nothing here "
            "repairs an answer — the model answering something else is a different problem from "
            "the model being wrong, and they need different fixes"
        )
        raise HypothesisError(message)

    try:
        payload = json.loads(found.group(0))
    except json.JSONDecodeError as error:
        message = f"the hypothesis was not valid JSON: {error}"
        raise HypothesisError(message) from error

    if not isinstance(payload, dict):
        message = f"a hypothesis must be an object, got {type(payload).__name__}"
        raise HypothesisError(message)

    missing = [name for name in ("statement", "primitive", "rationale") if not payload.get(name)]
    if missing:
        message = (
            f"the hypothesis is missing {missing}. A statement with no instrument cannot be "
            "tested, and an instrument with no statement tests nothing in particular"
        )
        raise HypothesisError(message)

    primitive = str(payload["primitive"]).strip()
    if primitive not in instruments.names:
        offered = ", ".join(instruments.names) or "none"
        message = (
            f"the hypothesis proposes {primitive!r}, which is not an instrument this project was "
            f"offered. Available: {offered}. S-3.1 withholds an instrument when a project fact "
            "says it cannot run here, and proposing one anyway moves the failure to S-8.2 with "
            "the reason lost"
        )
        raise HypothesisError(message)

    return Hypothesis(
        statement=str(payload["statement"]).strip(),
        primitive=primitive,
        rationale=str(payload["rationale"]).strip(),
    )


def generate(  # noqa: PLR0913 - AC 2's four inputs plus the session and the client.
    # None is derivable from the others, and there is deliberately no `validate`
    # among them — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    log: ExperimentLog,
    exclusions: Sequence[str],
    source: str,
    instruments: Selection,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[Hypothesis]:
    """Ask for the next hypothesis, at 0.8, on the frontier, without a cascade.

    **There is no `validate` parameter and that is the enforcement.** S-5.6
    cascades a step only when its caller supplies a validator, so a call site
    with nowhere to pass one cannot request the thing `CLAUDE.md` forbids. The
    routing is S-5.5's and refuses the frontier's alternatives on its own.

    Returns the whole `StepOutcome` rather than the hypothesis alone, because the
    model that answered and what it cost are part of what the caller records —
    and an outcome that dropped them would make the log unable to say which tier
    proposed a finding.

    Raises:
        HypothesisError: no usable hypothesis came back.
        UnsafeRoutingError: the configuration would route this below the frontier.
        BudgetExhaustedError: a cap or the ceiling stopped the call.
    """
    question = render_question(
        log=log, exclusions=exclusions, source=source, instruments=instruments
    )
    step = Step(
        step_type=StepType.HYPOTHESIS_GENERATION,
        phase=Phase.INVESTIGATE,
        agent=Agent.DIAGNOSTICIAN,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str) -> tuple[Hypothesis, TokenUsage]:
        messages: Sequence[MessageParam] = [{"role": "user", "content": question}]
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=HYPOTHESIS_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the model declined to answer. A refusal is a successful response with an empty "
                "content list, so this is reported rather than parsed as a short hypothesis"
            )
            raise HypothesisError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated JSON object "
                "parses as nothing, and a hypothesis assembled from half a sentence is a guess "
                "about what the model was going to say"
            )
            raise HypothesisError(message)
        return parse(reply.text, instruments), reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )

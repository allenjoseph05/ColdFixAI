"""The failing test: the Optimizer's first phase. **S-28.6, ADR 185.**

`Falsified` is minted only by `must_fail`, and until this existed nothing in v3
produced the test it runs -- so `optimize` could prove a finding and never
propose a fix. `10-v3-mechanics.md` §4.5 puts this where it belongs: the
Optimizer has two phases, and phase one is the test. Not a fifth agent.

**It counts something; it never times it.** The finding is about cost, and the
obvious test -- run it and assert it finishes inside N seconds -- is the one
thing this project refuses everywhere else: a duration is one sample, and a
threshold in seconds encodes the machine that wrote it. A gate like that mints
tokens on a busy machine and refuses patches on a quiet one. So the test asserts
the count the mechanism predicts: 161 queries now, 2 when it is fixed, on any
machine, every time. Whatever counting that needs, the agent writes itself from
the source it is shown -- the agent's knowledge of a language, not the core's
knowledge of a framework.

**The check is running it.** `FALSIFICATION_TEST` is `04-cost.md` §3's row whose
mechanical check is *fails on unpatched code*, which is `must_fail` exactly. So
the step is mechanical, routes below the frontier, and cascades: two attempts on
the routed tier, then one rung dearer. Nothing here reads a test and decides it
looks right.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from anthropic.types import MessageParam
from pydantic import BaseModel, ValidationError

from coldfix.cost.accounting import Agent, Phase
from coldfix.cost.cascade import CHEAP_ATTEMPTS
from coldfix.cost.routing import StepType
from coldfix.evidence.ledger import Finding
from coldfix.evidence.repair import Falsified, NoFailingTestError, must_fail
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS
from coldfix.llm.metered import Call, Meter

TEMPERATURE = 0.0

CACHE_TTL: Final = "5m"
"""Five minutes, not the hour `scan` buys. **S-26.5, ADR 192.**

A write costs 1.25x at five minutes and 2x at an hour, and a read 0.1x. Three
attempts therefore pay 1.45x of one prompt at five minutes and 2.2x at an hour,
against 3x uncached. The hour only wins on a conversation long enough to amortise
its premium -- forty turns in `scan`, three here. The Adversary's four turns made
the same call."""

ATTEMPTS = CHEAP_ATTEMPTS + 1
"""Two on the routed tier, then one rung dearer. §3's cascade, and the validator
is the gate itself rather than a check somebody supplies."""

WRITE = Call(
    step=StepType.FALSIFICATION_TEST,
    phase=Phase.REPAIR,
    agent=Agent.OPTIMIZER,
    max_tokens=NON_STREAMING_MAX_TOKENS,
)

SYSTEM = """\
You write the test that has to fail before anybody may change the program.

Somebody measured a cost and proved where it comes from. Your test makes that
cost visible as a number a machine can check: it fails on the code as it stands,
and it passes once the waste is gone.

COUNT SOMETHING. NEVER TIME ANYTHING.
A test that asserts the program finishes in under two seconds passes on a quiet
machine and fails on a busy one, so it cannot tell a fix from an idle afternoon.
Assert the thing the mechanism predicts instead: the number of queries, the
number of calls, the number of times a value is recomputed, the number of rows
fetched. If the finding says each row costs a SELECT, count SELECTs.

You are shown the source. Write whatever you need to do the counting -- wrap the
cursor, patch the function, install a counter, record the calls. That is ordinary
code in the language in front of you.

The test must fail *for the reason in the finding*. A test that fails because it
cannot import, or because a fixture is missing, is refused: it would let a change
be made on the strength of a broken file.

If the mechanism predicts nothing you can count, say so rather than inventing a
test that times something. That is a real answer and it stops the run honestly.

Reply with one JSON object and nothing else:

  {"test": "import app\\n\\ndef test_one_query_per_request():\\n    ...",
   "counts": "SELECT statements issued while rendering one page"}

To decline:

  {"test": null, "because": "the cost is spread across the interpreter and no
   count distinguishes it"}
"""


class NoTestError(Exception):
    """No test that fails on unpatched code came back. **Never a patch.**

    Raised rather than returning something falsy, because the caller's next step
    is applying a patch and the only thing standing between a finding and that is
    a token this could not mint.
    """


class _Reply(BaseModel, frozen=True):
    """A turn's reply. Any other key is dropped; the test itself is what matters."""

    test: str | None = None
    counts: str = ""
    because: str = ""


Falsify = Callable[[Finding, str], Falsified]
"""A finding and the source of the file it names in; the proof out."""


def falsify(
    meter: Meter, *, finding: Finding, source: str, run: Callable[[str], tuple[int, str]]
) -> Falsified:
    """Write a test, run it, and return the proof it failed. Retry on the gate's terms.

    Raises:
        NoTestError: every attempt was spent, or the model declined because the
            mechanism predicts nothing countable.
        BudgetExhaustedError: the meter refused an attempt. Nothing was sent.
    """
    rejected: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        escalation = 0 if attempt <= CHEAP_ATTEMPTS else 1
        response = meter.complete(
            WRITE,
            system=SYSTEM,
            messages=_request(finding, source, rejected),
            temperature=TEMPERATURE,
            cache_ttl=CACHE_TTL,
            escalation=escalation,
        )
        if response.refused:
            rejected.append(f"attempt {attempt}: the model declined to answer")
            continue
        if response.truncated:
            rejected.append(f"attempt {attempt}: the reply was cut off and was not read")
            continue

        try:
            reply = _Reply.model_validate_json(_unfenced(response.text))
        except ValidationError as unreadable:
            rejected.append(f"attempt {attempt}: {unreadable.errors()[0]['msg']}")
            continue

        if reply.test is None or not reply.test.strip():
            message = (
                "no test was written, and the reason given is: "
                f"{reply.because or 'none'}. A finding whose mechanism predicts nothing "
                "countable cannot be falsified by counting, and this refuses rather than "
                "asserting a duration, which would pass or fail on how busy the machine is"
            )
            raise NoTestError(message)

        try:
            return must_fail(reply.test, run)
        except NoFailingTestError as refused:
            rejected.append(f"attempt {attempt}: {refused}")

    raise NoTestError(_spent(rejected))


def brief(finding: Finding, source: str) -> str:
    """The part of every attempt's question that does not change between attempts.

    Byte-identical across the three attempts, which is what makes it worth a
    breakpoint: the finding and the source are the bulk of the prompt, and only
    the refusals after it grow.
    """
    claim = finding.claim
    where = f"{claim.location.file}:{claim.location.line} {claim.location.symbol}".strip()
    return "\n".join(
        [
            "THE FINDING",
            f"  where: {where}",
            f"  kind: {claim.kind}",
            f"  what: {claim.summary}",
            "  measured:",
            *(f"    {c.measurement_id}.{c.field} = {c.value}" for c in claim.evidence),
            "",
            f"THE SOURCE OF {claim.location.file}",
            source,
        ]
    )


def refused(rejected: Sequence[str]) -> str:
    """What the earlier attempts were refused for, and the instruction.

    The half that grows, so it carries no breakpoint: a marker on a block that
    changes every attempt is a write nothing ever reads, which costs more than
    not caching at all.
    """
    lines: list[str] = []
    if rejected:
        lines += [
            "",
            "WHAT WAS REFUSED, AND WHY -- yours must not repeat it",
            *(f"  {r}" for r in rejected),
        ]
    lines += ["", "Write the test."]
    return "\n".join(lines)


def question(finding: Finding, source: str, rejected: Sequence[str]) -> str:
    """The whole of what one attempt is asked, as one string.

    The single definition of the rendered prompt. `_request` sends the same two
    halves as separate blocks and a test asserts they concatenate to this, because
    a prompt that changed when caching arrived would be a change to what the model
    was asked, smuggled in as an optimisation.
    """
    return f"{brief(finding, source)}\n{refused(rejected)}"


def _request(finding: Finding, source: str, rejected: Sequence[str]) -> list[MessageParam]:
    """One user message: the brief, cached, then what changed, which is not."""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": brief(finding, source),
                    "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
                },
                {"type": "text", "text": f"\n{refused(rejected)}"},
            ],
        }
    ]


def _spent(rejected: Sequence[str]) -> str:
    return (
        f"no test that fails on the unpatched code came back in {ATTEMPTS} attempts, so no "
        "patch may be proposed for this finding. What was refused:\n"
        + "\n".join(f"  {item}" for item in rejected)
    )


def _unfenced(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

"""The scan agent's loop: bounds, the phase gate, and nothing that decides.

E19. What this module does is sequence turns and refuse the ones that are out of
bounds. What it deliberately does *not* do is judge a measurement, price a
finding, or repair a malformed reply -- those belong to the ledger, to `ablate`,
and to nobody respectively.

**The agent never produces a number.** Three of its tools return measurements and
each returns an id; a claim cites ids, and `Ledger.attest` checks every cited
value against what was recorded. That is enforced one layer up, and this module
could not weaken it if it tried: it hands the claim over and takes back either a
`Finding` or an exception.

**The phase gate is the tool list, not an instruction.** `profile` and `ablate`
are absent from what the agent is offered until a `measure` has returned a
repeatable measurement. A profile of a workload that will not run the same way
twice is a profile of the machine, and an ablation against an unrepeatable
baseline is a comparison with nothing.

**Every turn goes through the meter, and the phase decides the model.** S-26.1.
The loop used to take a model from its caller and a budget that defaulted to
counting nothing. Now each turn is a `Call` whose step type is fixed by the phase
-- see `STEPS` -- so the router, not the caller, picks the tier, and the budget
refuses a turn before it is sent rather than noticing it afterwards.

**The conversation is cached, one breakpoint at a time.** S-26.2, ADR 177. The
conversation only grows by appending, so each request is the previous one plus
two messages, and a single marker on the newest block lets it read everything
before it from the cache. The marker lives on the *request*, never on the stored
conversation -- see `_marked` -- so no request carries more than one and the
bytes of every earlier turn are identical from one request to the next.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Any, Protocol

from pydantic import BaseModel

from coldfix.agent.protocol import SUBMIT, Action, JsonReader, ProtocolError, Reader
from coldfix.cost.accounting import Agent
from coldfix.cost.accounting import Phase as Spending
from coldfix.cost.budget import BudgetExhaustedError
from coldfix.cost.routing import StepType
from coldfix.evidence.ledger import Claim, Finding, Ledger
from coldfix.llm.metered import Call, Meter

TEMPERATURE = 0.0
"""The loop proposes experiments and the harness judges them. Variety in what is
proposed buys nothing that a second turn does not."""

CACHE_TTL = "1h"
"""How long the conversation's cache entry lives. **ADR 177.**

Not five minutes, although most turns start well within five minutes of the last.
The two lifetimes fail in different currencies: an hour costs an extra 0.75x on
what a turn *adds*, while five minutes costs an extra 1.15x on the *whole
conversation* every time a gap runs long -- and installs, image builds and
ablations of slow workloads make long gaps ordinary. One late miss costs about as
much as a whole run of the hour's premium. The gaps are recorded (`Transcript.
gaps`), so the first live run can overturn this with data."""

BREAKPOINT: Mapping[str, str] = {"type": "ephemeral", "ttl": CACHE_TTL}
"""The marker the newest block carries. The API allows four per request; this
loop uses one, and moves it."""


class ScanError(Exception):
    """The scan could not continue."""


class OutOfBoundsError(ScanError):
    """A bound was reached. Not a failure -- the run submits what it has."""

    def __init__(self, bound: str, detail: str) -> None:
        super().__init__(f"{bound}: {detail}")
        self.bound = bound


class MalformedSubmissionError(ScanError):
    """The submission is not a list of claims."""

    def __init__(self, got: str) -> None:
        super().__init__(
            f"`findings` must be a list of claims and this is {got}. A submission is checked "
            "against the ledger claim by claim; there is no shape here that skips that."
        )


class UnavailableToolError(ScanError):
    """The agent asked for a tool it does not have at this phase."""

    def __init__(self, tool: str, offered: Sequence[str]) -> None:
        super().__init__(
            f"{tool!r} is not available yet. Offered: {', '.join(sorted(offered))}. "
            "Profiling and ablation need a workload that measures the same way twice; until "
            "`measure` says so, a profile describes the machine and an ablation compares "
            "against nothing."
        )
        self.tool = tool


class Phase(StrEnum):
    EXPLORING = "exploring"
    """Make it run. `measure` is here because verifying is how the phase ends."""

    MEASURING = "measuring"
    """A repeatable measurement exists, so the instruments mean something."""


EXPLORING_TOOLS = ("bash", "read_file", "write_file", "measure", SUBMIT)
MEASURING_TOOLS = (*EXPLORING_TOOLS, "profile", "ablate")

STEPS: Mapping[Phase, tuple[StepType, Spending]] = {
    Phase.EXPLORING: (StepType.EXPLORER_ACTION, Spending.GROUND),
    Phase.MEASURING: (StepType.HYPOTHESIS_GENERATION, Spending.INVESTIGATE),
}
"""What kind of step a turn is, by phase. **This is what routes the turn.** ADR 176.

*Exploring* is making the program run, and the harness decides when it is done:
only a `measure` that proves the workload repeatable ends the phase. That makes it
the mechanical `EXPLORER_ACTION` of `04-cost.md` §3, billed to grounding, which
routes below the frontier by default.

*Measuring* is choosing which experiment to run next, and nothing deterministic
can say a choice was wrong -- it is hypothesis generation. The router sends it to
the frontier tier and refuses any configuration that would send it lower (ADR
059). Both rows are `04-cost.md`'s existing step types; neither needed inventing.
"""


@dataclass(frozen=True)
class Bounds:
    """Where the loop stops. Every one submits what it has rather than dying."""

    turns: int = 40
    wall_seconds: float = 1200.0
    stall_turns: int = 5
    """Turns with no new measurement. A loop reading files and thinking is a loop
    spending money on a decision it already had the evidence for."""

    max_tokens: int = 2048


class ToolResult(BaseModel, frozen=True):
    """What a tool returned, and whether it produced a measurement."""

    content: str
    measurement_id: str | None = None
    verified: bool = False
    """`measure` sets this when the workload proved repeatable. It is what opens
    the second phase, and no other tool can set it."""


class Toolbox(Protocol):
    """What the agent may do. Supplied, so this module owns no capability."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult: ...


@dataclass
class Transcript:
    """The turns so far, and what the run learned from them."""

    turns: list[tuple[Action, ToolResult]] = field(default_factory=list)
    measurement_ids: list[str] = field(default_factory=list)
    phase: Phase = Phase.EXPLORING
    requested_at: list[float] = field(default_factory=list)
    """When each request that was actually sent began, by the loop's clock.

    Recorded for ADR 177: the cache lifetime was chosen on the argument that long
    gaps between turns are ordinary here, and this is what lets a live run say how
    ordinary. A turn the budget refused was never sent, so it is not here."""

    def record(self, action: Action, result: ToolResult) -> None:
        self.turns.append((action, result))
        if result.measurement_id:
            self.measurement_ids.append(result.measurement_id)
        if result.verified:
            self.phase = Phase.MEASURING

    def tools(self) -> tuple[str, ...]:
        return MEASURING_TOOLS if self.phase is Phase.MEASURING else EXPLORING_TOOLS

    def since_last_measurement(self) -> int:
        for distance, (_, result) in enumerate(reversed(self.turns)):
            if result.measurement_id:
                return distance
        return len(self.turns)

    def gaps(self) -> tuple[float, ...]:
        """Seconds between the starts of consecutive requests.

        Start to start, because that is what a cache entry's lifetime is measured
        against: the timer runs from the start of the request that last wrote or
        read the entry."""
        return tuple(later - earlier for earlier, later in pairwise(self.requested_at))


@dataclass(frozen=True)
class Outcome:
    """How the scan ended, and with what."""

    findings: tuple[Finding, ...]
    transcript: Transcript
    stopped_by: str
    """`submitted`, or the bound that ended it. Both are answers."""


def scan(  # noqa: PLR0913 - what pays for the calls, what it may do, what to check
    # claims against, how far it may go, and how to read a reply. Every one is a
    # decision the caller makes; a config object would hide them.
    meter: Meter,
    *,
    toolbox: Toolbox,
    ledger: Ledger,
    system: str,
    bounds: Bounds | None = None,
    reader: Reader | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run the loop until it submits or runs out of room.

    Every ending returns an `Outcome`. A bound reached is not an exception here
    because a scan that ran out of turns still knows things, and throwing away
    what it proved to signal *how* it stopped would be the expensive kind of tidy.
    The budget is one of those bounds: the meter refuses a turn *before* it is
    sent, and the run ends with `budget` rather than with a call it could not pay
    for.
    """
    bounds = bounds or Bounds()
    reader = reader or JsonReader()
    transcript = Transcript()
    started = clock()
    conversation: list[dict[str, Any]] = [_turn("user", _opening(transcript))]

    for turn in range(bounds.turns):
        now = clock()
        exceeded = _exceeded(turn, transcript, bounds, started, now)
        if exceeded:
            return Outcome((), transcript, exceeded)

        step, spending = STEPS[transcript.phase]
        try:
            response = meter.complete(
                Call(step=step, phase=spending, agent=Agent.SCAN, max_tokens=bounds.max_tokens),
                system=system,
                messages=_marked(conversation),  # type: ignore[arg-type]
                temperature=TEMPERATURE,
                cache_ttl=CACHE_TTL,
            )
        except BudgetExhaustedError:
            return Outcome((), transcript, "budget")
        transcript.requested_at.append(now)
        if response.refused:
            return Outcome((), transcript, "refused")

        try:
            action = reader.read(response.text)
        except ProtocolError as unreadable:
            conversation.extend(
                [
                    _turn("assistant", response.text),
                    _turn("user", f"{unreadable}\nReply with one JSON object."),
                ]
            )
            continue

        if action.finishes:
            return Outcome(_attest(action, ledger), transcript, "submitted")

        offered = transcript.tools()
        if action.tool not in offered:
            result = ToolResult(content=str(UnavailableToolError(action.tool, offered)))
        else:
            result = toolbox.call(action.tool, action.arguments)

        transcript.record(action, result)
        conversation.extend(
            [
                _turn("assistant", response.text),
                _turn("user", _observation(result, transcript)),
            ]
        )

    return Outcome((), transcript, "turns")


def _turn(role: str, text: str) -> dict[str, Any]:
    """One message, always as a list of blocks.

    Never as a bare string, even though the API accepts one. The newest turn has
    to be a block to carry the marker, and a message sent as a block on one request
    and as a string on the next is a change in the prefix nobody meant to make. One
    shape for every message keeps the only difference between two requests the
    marker itself, which the cache does not count as a change.
    """
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _marked(conversation: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The request to send: the conversation, with one breakpoint on its newest block.

    Built fresh each turn and never written back. A marker stored in the
    conversation would stay on that turn for every later request, so they would
    accumulate past the four a request may carry, and the stored bytes of a turn
    would depend on whether it had once been the newest.
    """
    *earlier, newest = conversation
    blocks = [dict(block) for block in newest["content"]]
    blocks[-1] = {**blocks[-1], "cache_control": dict(BREAKPOINT)}
    return [*(dict(message) for message in earlier), {"role": newest["role"], "content": blocks}]


def _attest(action: Action, ledger: Ledger) -> tuple[Finding, ...]:
    """Hand every claim to the ledger. Anything it refuses does not exist.

    Refusals are not caught and softened into a partial result: a submission
    citing a number nobody measured is the failure this whole system is built to
    make impossible, and swallowing it here would be the one place that could.
    """
    raw = action.arguments.get("findings", [])
    if not isinstance(raw, list):
        raise MalformedSubmissionError(type(raw).__name__)
    return tuple(ledger.attest(Claim.model_validate(claim)) for claim in raw)


def _exceeded(
    turn: int,
    transcript: Transcript,
    bounds: Bounds,
    started: float,
    now: float,
) -> str | None:
    """The bounds that can be read before a turn. The budget is the meter's.

    Checked here, before the meter is asked, because none of them costs anything
    to read -- and a turn refused for the wall clock should not first have been
    counted by the API. `now` is read once per turn by the caller, so the time a
    bound is judged at and the time the request is recorded at are the same.
    """
    if now - started > bounds.wall_seconds:
        return "wall_clock"
    if turn and transcript.since_last_measurement() >= bounds.stall_turns:
        return "stalled"
    return None


def _opening(transcript: Transcript) -> str:
    return (
        "Begin. Reply with one JSON object: "
        '{"tool": ..., "arguments": {...}, "reason": "..."}.\n'
        f"Available now: {', '.join(transcript.tools())}."
    )


def _observation(result: ToolResult, transcript: Transcript) -> str:
    """What came back, and what is available now.

    The tool list is repeated every turn because it *changes* -- being told twice
    is cheaper than a turn spent asking for something that is not there yet.
    """
    lines = [result.content]
    if result.measurement_id:
        lines.append(f"[recorded as {result.measurement_id}]")
    lines.append(f"Available now: {', '.join(transcript.tools())}.")
    return "\n".join(lines)

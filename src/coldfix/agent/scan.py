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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from coldfix.agent.protocol import SUBMIT, Action, JsonReader, ProtocolError, Reader
from coldfix.evidence.ledger import Claim, Finding, Ledger
from coldfix.llm.client import ModelClient

TEMPERATURE = 0.0
"""The loop proposes experiments and the harness judges them. Variety in what is
proposed buys nothing that a second turn does not."""


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


@dataclass(frozen=True)
class Bounds:
    """Where the loop stops. Every one submits what it has rather than dying."""

    turns: int = 40
    wall_seconds: float = 1200.0
    stall_turns: int = 5
    """Turns with no new measurement. A loop reading files and thinking is a loop
    spending money on a decision it already had the evidence for."""

    max_tokens: int = 2048


class Budget(Protocol):
    """Checked *before* each call, so a halt writes what it has."""

    def affordable(self) -> bool: ...

    def spend(self, tokens: int) -> None: ...


@dataclass
class NoBudget:
    """The default: nothing is counted and nothing is refused."""

    def affordable(self) -> bool:
        return True

    def spend(self, tokens: int) -> None:
        return None


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


@dataclass(frozen=True)
class Outcome:
    """How the scan ended, and with what."""

    findings: tuple[Finding, ...]
    transcript: Transcript
    stopped_by: str
    """`submitted`, or the bound that ended it. Both are answers."""


def scan(  # noqa: PLR0913 - who to ask, what it may do, what to check claims
    # against, how far it may go, what it may spend, and how to read a reply.
    # Every one is a decision the caller makes; a config object would hide them.
    client: ModelClient,
    *,
    toolbox: Toolbox,
    ledger: Ledger,
    system: str,
    model: str,
    bounds: Bounds | None = None,
    budget: Budget | None = None,
    reader: Reader | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run the loop until it submits or runs out of room.

    Every ending returns an `Outcome`. A bound reached is not an exception here
    because a scan that ran out of turns still knows things, and throwing away
    what it proved to signal *how* it stopped would be the expensive kind of tidy.
    """
    bounds = bounds or Bounds()
    budget = budget or NoBudget()
    reader = reader or JsonReader()
    transcript = Transcript()
    started = clock()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _opening(transcript)}]

    for turn in range(bounds.turns):
        exceeded = _exceeded(turn, transcript, bounds, started, clock, budget)
        if exceeded:
            return Outcome((), transcript, exceeded)

        response = client.complete(
            model=model,
            system=system,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=bounds.max_tokens,
            temperature=TEMPERATURE,
        )
        budget.spend(response.usage.total if hasattr(response.usage, "total") else 0)
        if response.refused:
            return Outcome((), transcript, "refused")

        try:
            action = reader.read(response.text)
        except ProtocolError as unreadable:
            messages.extend(
                [
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": f"{unreadable}\nReply with one JSON object."},
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
        messages.extend(
            [
                {"role": "assistant", "content": response.text},
                {"role": "user", "content": _observation(result, transcript)},
            ]
        )

    return Outcome((), transcript, "turns")


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


def _exceeded(  # noqa: PLR0913, PLR0917 - five separate bounds and the clock
    # that reads three of them. Bundling them would hide that each is a distinct
    # reason a run can stop, and each stop is a different thing to tell somebody.
    turn: int,
    transcript: Transcript,
    bounds: Bounds,
    started: float,
    clock: Callable[[], float],
    budget: Budget,
) -> str | None:
    if not budget.affordable():
        return "budget"
    if clock() - started > bounds.wall_seconds:
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

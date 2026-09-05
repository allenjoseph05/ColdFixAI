"""How a turn is asked for and read back.

E19. The loop, the bounds, the phase gate and the ledger are all indifferent to
whether the model asks for a tool through the API's `tool_use` blocks or through
a JSON object in its reply. That difference lives here and nowhere else, so
moving to native tool use later is a change to one module.

**Structured text today, and the reason is honest rather than architectural.**
`ModelClient` carries `text` and no tool blocks, and extending it is a change to
tested code that cannot be validated without a live call. What can be built and
proved today is the JSON protocol; the seam is here so the upgrade is small.

**A malformed reply is an error, never a guess.** Nothing here repairs a missing
field or infers an intended tool from a near-miss name. A parser that guessed
would turn a model that misunderstood its instructions into a run that did
something nobody asked for, and the guess would be invisible.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError


class ProtocolError(Exception):
    """The model's reply cannot be read as an action."""


class UnreadableReplyError(ProtocolError):
    """It is not the JSON object the protocol asks for."""

    def __init__(self, text: str, detail: str) -> None:
        super().__init__(
            f"the reply could not be read as an action: {detail}. It began {text[:120]!r}. "
            "Nothing is repaired here -- a parser that guessed at the intended tool would turn "
            "a misunderstanding into a run that did something nobody asked for."
        )


class Action(BaseModel, frozen=True):
    """One turn: a tool to call, or the run's answer."""

    tool: str
    arguments: dict[str, Any] = {}
    reason: str = ""
    """What the model says it is doing. Recorded, never acted on."""

    @property
    def finishes(self) -> bool:
        return self.tool == SUBMIT


SUBMIT = "submit"
"""The one action that ends the loop rather than calling a tool."""


class Reader(Protocol):
    """Turns a model's reply into an action."""

    def read(self, text: str) -> Action: ...


class JsonReader:
    """One JSON object per turn, and nothing else in the reply.

    Fenced code blocks are stripped because models emit them constantly and a
    fence is not a misunderstanding of the instruction. Anything else is.
    """

    def read(self, text: str) -> Action:
        stripped = _unfence(text.strip())
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as bad:
            raise UnreadableReplyError(text, str(bad)) from bad
        if not isinstance(payload, dict):
            raise UnreadableReplyError(text, f"expected an object, got {type(payload).__name__}")
        try:
            return Action.model_validate(payload)
        except ValidationError as invalid:
            raise UnreadableReplyError(text, invalid.errors()[0]["msg"]) from invalid


def _unfence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    body = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
    return "\n".join(body).strip()

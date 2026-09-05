"""Who owns each prompt in the v3 pipeline.

Every model call goes through a session whose prefix is a system prompt, so a
prompt nobody owns is a call nobody can locate on a bill or in a transcript.
`tests/agent/test_roles.py` asserts the two sides agree, the same way
`tests/agents/test_roles.py` does for v1 -- each registry is complete over its own
tree, and neither can quietly acquire a prompt.

Separate from v1's registry rather than added to it: that one is keyed by v1's
agents and phases, and a v3 role listed against a v1 phase would be a row that
reads as true and describes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from coldfix.agent import prompt


class Agent(StrEnum):
    """The v3 roles. Three more arrive with E21 to E23."""

    SCAN = "scan"


@dataclass(frozen=True)
class Role:
    agent: Agent
    purpose: str
    prompts: tuple[str, ...]
    receives: tuple[str, ...]
    cannot: tuple[str, ...]
    """What the role is structurally unable to do. The enforcement lives in the
    tool surface and the ledger; this is where it is written down."""


ROLES: Mapping[Agent, Role] = {
    Agent.SCAN: Role(
        agent=Agent.SCAN,
        purpose="make an unfamiliar program runnable, measure it, and prove what wastes time",
        prompts=(prompt.SYSTEM,),
        receives=(
            "a container and a repository it has never seen",
            "the result of every tool call it makes, and nothing it did not ask for",
            "the tools available at its current phase, restated each turn",
        ),
        cannot=(
            "produce a number -- three tools return measurements and each returns an id, and a "
            "claim citing anything else is refused by the ledger",
            "profile or ablate before a measurement has proved the workload repeatable -- those "
            "tools are absent from what it is offered, not discouraged",
            "write a fix -- no tool it has applies a patch",
        ),
    ),
}

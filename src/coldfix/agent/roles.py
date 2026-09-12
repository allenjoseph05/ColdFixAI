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
from coldfix.evidence import adversary, auditor, falsify, optimizer

V3_PACKAGES = ("agent", "collect", "evidence", "pipeline")
"""The packages this registry is complete over.

Named once and imported by both role tests, so the boundary between the two
registries cannot drift: whatever v1's test excludes is exactly what this one
covers. Two hardcoded lists would agree until somebody added a package to one.
"""


class Agent(StrEnum):
    """The v3 roles: one per agent in the pipeline, and four in all."""

    SCAN = "scan"
    FINDING_AUDITOR = "finding_auditor"
    OPTIMIZER = "optimizer"
    ADVERSARY = "adversary"


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
    Agent.FINDING_AUDITOR: Role(
        agent=Agent.FINDING_AUDITOR,
        purpose="attack one finding before any repair money is spent on it",
        prompts=(auditor.SYSTEM,),
        receives=(
            "one finding, every part of it, at once",
            "which arithmetic attacks already held, so it does not redo them",
        ),
        cannot=(
            "go and look at anything -- it is given no tools, so there is nothing to call, and "
            "pre-loading everything is what makes that the isolation rather than an instruction",
            "see the reasoning that produced the finding -- `Presented` has no field for it, the "
            "same way the Adversary has none for the Optimizer's",
            "be reached at all by a finding the four code attacks already rejected",
        ),
    ),
    Agent.OPTIMIZER: Role(
        agent=Agent.OPTIMIZER,
        purpose="write the failing test, then several candidate fixes, so measurement can choose",
        prompts=(falsify.SYSTEM, optimizer.SYSTEM),
        receives=(
            "one proven finding, and the source of the one file it names",
            "the test that already failed against that source",
            "what earlier rounds measured, with their numbers, and why any candidate was refused",
        ),
        cannot=(
            "run, apply or measure anything -- it has no tools; the harness applies every "
            "candidate and measures it",
            "choose the winner -- the archive does, by measurement, and reads nothing it wrote",
            "change a file other than the finding's, or a test, fixture or harness file -- such a "
            "candidate is refused before it is measured",
            "explain itself to the Adversary -- a candidate has no field for a reason",
            "be asked for anything before a test has failed -- it is built with a `Falsified`, "
            "which only the gate mints",
        ),
    ),
    Agent.ADVERSARY: Role(
        agent=Agent.ADVERSARY,
        purpose="find an input on which the patched program does what the original did not",
        prompts=(adversary.SYSTEM,),
        receives=(
            "the diff, the test it was written to pass, and how the program is run -- "
            "`PatchUnderAudit`, which refuses a sixth field",
            "what both revisions did with every input it has tried, each run twice",
        ),
        cannot=(
            "read the repository -- its one tool runs the program and returns what it did, never "
            "what it is",
            "see why the patch was written -- nothing it is built from has a field for a reason, "
            "and `PatchUnderAudit` refuses one passed in",
            "declare a verdict -- the harness compares outputs byte for byte, and `PatchReview` "
            "refuses a verdict its comparisons contradict",
            "run the test suite -- the suite already ran when the candidate was measured, and a "
            "failing test prints source into a reviewer that must not read it",
        ),
    ),
}

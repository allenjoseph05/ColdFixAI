"""The five roles in one place, and what each one is and is not shown.

`ADR 002` names five agents and `cost/accounting.py` enumerates them, but that
enum exists for **cost accounting** — it answers *who spent this* and nothing
else. Which agent may see what is enforced correctly and in six separate places,
each argued for on its own merits:

- `refuse_shared_session` rejects a session whose cached prefix belongs to
  another agent, so one cannot inherit another's framing;
- `audit_messages` builds a new message list per call, so a conversation cannot
  be carried across;
- `Candidate` has no field for the Surgeon's `rationale` or `approach`, so the
  Adversary cannot be handed either;
- `scope_of` confines the Surgeon to the files the evidence implicates;
- `DiagnosticSession` has neither `apply_patch` nor a reader, so an ablation run
  can produce nothing shippable and leak nothing;
- `apply_patch` refuses a diff that touches a test or the harness.

**Six enforcements and no index.** Every one is structural and none is
persuadable, which is the property that matters — but somebody verifying the
system has to find all six and know that six is all there are. This module is
that index.

**It declares and verifies; it does not enforce.** A second enforcement layer
would be a second answer to questions already answered, and the two would
disagree the first time one moved — which is the failure this project has now
found five times at epic joins. What lives here is a description, and the tests
beside it check the description against the code: a prompt that gains an owner it
does not have, a withheld field that quietly reappears on the handover type, or a
sixth prompt added with no role claiming it, all fail.

**One role is declared and unused, and saying so is the point.** `Agent.EXPLORER`
appears in no call site anywhere in `src/`: grounding authorizes and records
`Phase.GROUND` against the budget, and no model call is attributed to the agent
that is supposed to make them. Either E7 built the mechanics without the loop that
drives them, or grounding's calls are billed to nobody. An index that omitted the
role would hide that; this one has a field for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from coldfix.audit import invocation, patchaudit, testquality
from coldfix.cost.accounting import Agent, Phase
from coldfix.diagnosis import design, explain, hypothesis, interpretation
from coldfix.repair import falsification, patch, testaudit


class RoleError(Exception):
    """The role index does not describe the system."""


@dataclass(frozen=True)
class Role:
    """One agent, what it is for, and the boundary drawn around it."""

    agent: Agent
    purpose: str

    prompts: tuple[str, ...]
    """Every system prompt this role owns. More than one because an agent has a
    prompt **per step**, not per role — the Diagnostician has four and the
    Adversary has three — and `refuse_shared_session` keys on the prompt, so the
    session boundary is per step rather than per agent."""

    phases: frozenset[Phase]
    """Which budget phases its calls are billed to."""

    receives: tuple[str, ...]
    """What it is given. Prose, because the artifacts are types from five
    packages and naming them here would make this module import all of them for
    documentation."""

    withheld: tuple[str, ...] = ()
    """Field names that must **not** exist on `handover`. Not *are not passed* —
    have nowhere to be passed. Empty where the role has no such boundary."""

    handover: type | None = None
    """The type carrying `withheld`'s absence, so a test can check the claim
    against the dataclass rather than against this docstring."""

    attributed: bool = True
    """Whether any call site in `src/` actually names this agent. `False` records
    a declared role nothing bills to — a gap, kept visible rather than tidied
    away."""

    notes: str = ""

    def __post_init__(self) -> None:
        if self.withheld and self.handover is None:
            message = (
                f"{self.agent.value} declares withheld fields {list(self.withheld)} and no type "
                "to check them against. A boundary nothing can verify is a comment"
            )
            raise RoleError(message)
        if not self.prompts and self.attributed:
            message = (
                f"{self.agent.value} is attributed calls and owns no prompt. Every model call "
                "in this system goes through a session whose prefix is a system prompt, so a "
                "billed role with none is a call nobody can locate"
            )
            raise RoleError(message)


ROLES: Mapping[Agent, Role] = {
    Agent.EXPLORER: Role(
        agent=Agent.EXPLORER,
        purpose="stand an unfamiliar repository up and drive one workload at controllable scale",
        prompts=(),
        phases=frozenset({Phase.GROUND}),
        receives=("the repository", "the adapter's fingerprint"),
        attributed=False,
        notes=(
            "**Declared and unused.** No call site in `src/` names `Agent.EXPLORER`. "
            "`explorer/run.py` authorizes and records `Phase.GROUND` against the budget, so the "
            "spend is bounded, but nothing attributes a model call to the agent that is supposed "
            "to make them — and `00-BRIEF.md` §5 step 5 is emphatic that grounding is the step "
            "the project's viability turns on. Either the loop that drives it is not built, or "
            "its calls are billed to nobody."
        ),
    ),
    Agent.DIAGNOSTICIAN: Role(
        agent=Agent.DIAGNOSTICIAN,
        purpose=(
            "choose the next experiment from what the last one revealed, "
            "and state what they add up to"
        ),
        prompts=(hypothesis._SYSTEM, design._SYSTEM, interpretation._SYSTEM, explain._SYSTEM),
        phases=frozenset({Phase.INVESTIGATE}),
        receives=("the workload", "the append-only experiment log", "the primitive registry"),
        notes=(
            "Three prompts because the loop is three steps — propose, design, interpret — and "
            "each is a separate session so a hypothesis cannot be justified by the framing that "
            "produced it. **A fourth was added at S-8.11 and it is not a loop step**: the loop "
            "runs until something is confirmed, and `explain` is asked once afterwards for the "
            "mechanism, the site and the implicated files — the half of an evidence chain no "
            "measurement contains. This index is what caught it, which is what it is for: the "
            "prompt existed for the length of one test run before anything claimed it."
        ),
    ),
    Agent.SURGEON: Role(
        agent=Agent.SURGEON,
        purpose="write the falsification test, then the patch that satisfies it",
        prompts=(falsification._SYSTEM, patch._SYSTEM),
        phases=frozenset({Phase.REPAIR}),
        receives=(
            "the evidence chain",
            "the implicated files",
            "prior attempts and why each failed",
        ),
        withheld=("diff", "patch", "files", "approach"),
        handover=falsification.FalsificationTest,
        notes=(
            "The withheld set is on the *test*, not on the patch: S-10.1 gives "
            "`FalsificationTest` no field a diff could arrive through, which is how *test first* "
            "becomes a fact about the type rather than a claim about ordering."
        ),
    ),
    Agent.FINDING_AUDITOR: Role(
        agent=Agent.FINDING_AUDITOR,
        purpose="attack the diagnosis before any repair budget is spent",
        prompts=(invocation._SYSTEM,),
        phases=frozenset({Phase.FINDING_AUDIT}),
        receives=("the experiment log, rendered", "the workload", "the conditions"),
        notes=(
            "`08-audit.md` F2 — *nobody audits the diagnosis, only the patch* — and this is the "
            "role that closes it. Its isolation is by rendering: S-9.1 strips `rationale` and "
            "`outcome` from the log, because the log's type carries them and cannot be given a "
            "shape that does not."
        ),
    ),
    Agent.ADVERSARY: Role(
        agent=Agent.ADVERSARY,
        purpose="defeat the patch, and the test that judged it",
        prompts=(testaudit.SYSTEM, patchaudit.SYSTEM, testquality.SYSTEM),
        phases=frozenset({Phase.TEST_AUDIT, Phase.PATCH_AUDIT}),
        receives=("the diff", "both revisions of every touched file", "the chain", "the test"),
        withheld=("rationale", "approach"),
        handover=patchaudit.Candidate,
        notes=(
            "The strongest boundary in the system, and the only one where the withheld thing is "
            "**inexpressible** rather than removed: a caller holding a `Patch` cannot pass its "
            "reasoning because `Candidate` has nowhere for it to go. `08-audit.md`'s number is "
            "the argument — 72% of reward-hacking episodes carry explicit justifying reasoning."
        ),
    ),
}


ENFORCEMENTS: Mapping[str, str] = {
    "refuse_shared_session": (
        "a session whose cached prefix belongs to another agent is rejected, so one agent "
        "cannot inherit another's framing while every message list stays clean"
    ),
    "audit_messages": (
        "a new message list is built per call and there is no parameter to pass an old one, "
        "so a conversation cannot be carried across an isolation boundary"
    ),
    "Candidate": (
        "no field for `rationale` or `approach`, so the Surgeon's account of itself cannot "
        "reach the Adversary — enforcement by absence rather than by stripping"
    ),
    "scope_of": (
        "the Surgeon may touch only the files the evidence chain implicates; a fourth file is "
        "a claim no experiment supports"
    ),
    "DiagnosticSession": (
        "no `apply_patch`, no `diff`, no reader — an ablation run can produce nothing "
        "shippable and leak nothing, and the operations are absent rather than guarded"
    ),
    "apply_patch": (
        "a diff touching a test, a fixture or the harness is refused server-side; the model is "
        "never asked what it intends to touch"
    ),
}
"""Every place a boundary between agents is enforced. **The index this module
exists to be.**

Each is structural and none is persuadable. What was missing was not enforcement
but the list — somebody verifying the system had to find all six and know that
six is all there are."""


def role_of(agent: Agent, roles: Mapping[Agent, Role] = ROLES) -> Role:
    """The declared role, or a refusal naming what is missing.

    `roles` is a parameter so the refusal is reachable. With the index closed over,
    the branch could only fire for an agent that does not exist — which is to say
    never, and a guard no test can reach is a guard nobody has checked.

    Raises:
        RoleError: the agent has no declared role — which is a role added to the
            enum without anybody saying what it may see.
    """
    try:
        return roles[agent]
    except KeyError:
        message = (
            f"{agent.value} has no declared role. An agent in the enum and not in this index is "
            "one whose boundary nobody has written down, and the enum is about cost rather than "
            "about what may be seen"
        )
        raise RoleError(message) from None


def owner_of(prompt: str) -> Agent:
    """Which agent owns a system prompt.

    Raises:
        RoleError: no role claims it, or more than one does. A prompt with two
            owners is an isolation boundary two agents are on the same side of.
    """
    owners = [role.agent for role in ROLES.values() if prompt in role.prompts]
    if len(owners) == 1:
        return owners[0]
    if not owners:
        message = (
            "no role claims this prompt. Every model call goes through a session whose prefix "
            "is one of these, and `refuse_shared_session` compares against the prompt — so an "
            "unclaimed one is a call nobody can attribute to a role"
        )
        raise RoleError(message)
    message = f"{[item.value for item in owners]} both claim this prompt, so neither is isolated"
    raise RoleError(message)


def unattributed() -> tuple[Role, ...]:
    """Declared roles that no call site bills to. **Kept visible on purpose.**"""
    return tuple(role for role in ROLES.values() if not role.attributed)


def describe() -> str:
    """The whole index, for somebody verifying the system rather than running it."""
    lines = ["AGENT ROLES — five declared, and what each is shown."]
    for role in ROLES.values():
        billed = ", ".join(sorted(item.value for item in role.phases))
        lines.append(f"  {role.agent.value} — {role.purpose}")
        lines.append(f"    billed to: {billed}; prompts: {len(role.prompts)}")
        lines.append(f"    receives: {', '.join(role.receives)}")
        if role.withheld and role.handover is not None:
            held = ", ".join(role.withheld)
            lines.append(
                f"    **cannot be given**: {held} (no such field on {role.handover.__name__})"
            )
        if not role.attributed:
            lines.append("    **no call site names this agent**")
        if role.notes:
            lines.append(f"    {role.notes}")

    lines.append("  Where the boundaries are enforced:")
    lines.extend(f"    {name}: {why}" for name, why in sorted(ENFORCEMENTS.items()))
    return "\n".join(lines)

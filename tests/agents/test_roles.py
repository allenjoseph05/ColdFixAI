"""The role index, checked against the system it claims to describe.

`cost/accounting.py` enumerates five agents for **cost accounting** — who spent
this — and which agent may see what is enforced in six separate places. Every one
is structural; what was missing was the list.

So this file's job is not to test a new enforcement. It is to make the description
fail when the code moves under it: a prompt with no owner, a withheld field that
reappears on the type that is supposed not to have it, or an agent added to the
enum that nobody has written a boundary for.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from coldfix.agents.roles import (
    ENFORCEMENTS,
    ROLES,
    Role,
    RoleError,
    describe,
    owner_of,
    role_of,
    unattributed,
)
from coldfix.audit import invocation, patchaudit, testquality
from coldfix.cost.accounting import Agent, Phase
from coldfix.repair import falsification, testaudit

SOURCE = Path(__file__).resolve().parents[2] / "src" / "coldfix"

ATTRIBUTION_EXEMPT = frozenset({"accounting.py", "roles.py"})
"""The enum that defines the agents and the index that describes them. Naming a
role in either is not attributing a call to it, and counting them would make
every role look attributed the moment it was written down."""


def declared_prompts() -> set[str]:
    return {prompt for role in ROLES.values() for prompt in role.prompts}


def defined_prompts() -> dict[str, str]:
    """Every module-level `SYSTEM`/`_SYSTEM` string literal under `src/coldfix`.

    Parsed rather than imported, so a module that is never imported by this test
    still counts. The point is to catch a **new** prompt, and a new prompt in a
    module nobody imports is exactly the one that would slip through.
    """
    found: dict[str, str] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for statement in tree.body:
            if not isinstance(statement, ast.Assign):
                continue
            names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            if not any(name in ("SYSTEM", "_SYSTEM") for name in names):
                continue
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                found[f"{path.relative_to(SOURCE).as_posix()}"] = statement.value.value
    return found


# ============ the index describes every role


def test_every_agent_in_the_enum_has_a_declared_role() -> None:
    """An agent in the enum and not in the index is one whose boundary nobody has
    written down — and the enum is about cost, not about what may be seen."""
    assert set(ROLES) == set(Agent)
    assert all(role.agent is agent for agent, role in ROLES.items())


def test_asking_for_a_role_that_does_not_exist_says_so() -> None:
    """An agent added to the enum with no boundary written for it. The index is a
    parameter so this branch is reachable — a guard no test can reach is a guard
    nobody has checked."""
    missing: dict[Agent, Role] = {
        agent: role for agent, role in ROLES.items() if agent is not Agent.SURGEON
    }
    with pytest.raises(RoleError, match="nobody has written down"):
        role_of(Agent.SURGEON, missing)

    assert role_of(Agent.SURGEON).agent is Agent.SURGEON, "and the real index still works"


def test_every_phase_that_has_a_cap_is_billed_to_some_role() -> None:
    """A phase nobody is declared to spend is either dead or unattributed, and the
    two look identical from the budget."""
    billed = {phase for role in ROLES.values() for phase in role.phases}
    assert billed == set(Phase)


# ============ prompts have exactly one owner


def test_every_prompt_defined_in_the_source_is_claimed_by_one_role() -> None:
    """**The check that catches a sixth prompt.** `refuse_shared_session` compares
    against the prompt, so an unclaimed one is a call nobody can attribute to a
    role — and adding a step to an agent is exactly how one appears."""
    defined = defined_prompts()
    assert defined, "the parser found no prompts at all, which would make this vacuous"

    unclaimed = {where: text for where, text in defined.items() if text not in declared_prompts()}
    assert not unclaimed, f"prompts with no owner: {sorted(unclaimed)}"


def test_the_prompts_are_the_modules_own_objects_and_not_copies() -> None:
    """Identity, not equality. A copied string would drift the first time either
    side was edited and the index would describe a system that no longer exists."""
    assert ROLES[Agent.ADVERSARY].prompts == (
        testaudit.SYSTEM,
        patchaudit.SYSTEM,
        testquality.SYSTEM,
    )
    assert ROLES[Agent.FINDING_AUDITOR].prompts == (invocation._SYSTEM,)


def test_no_prompt_has_two_owners() -> None:
    """Two agents on the same side of an isolation boundary is not an isolation
    boundary."""
    every = [prompt for role in ROLES.values() for prompt in role.prompts]
    assert len(every) == len(set(every))

    for role in ROLES.values():
        for prompt in role.prompts:
            assert owner_of(prompt) is role.agent


def test_an_unclaimed_prompt_is_refused_rather_than_attributed() -> None:
    with pytest.raises(RoleError, match="no role claims this prompt"):
        owner_of("you are a helpful assistant")


# ============ withheld fields are genuinely absent


def test_the_adversary_cannot_be_given_the_surgeons_reasoning() -> None:
    """**The strongest boundary in the system**, and the only one where the
    withheld thing is inexpressible rather than removed: a caller holding a `Patch`
    cannot pass its reasoning because `Candidate` has nowhere for it to go."""
    role = ROLES[Agent.ADVERSARY]
    assert role.handover is patchaudit.Candidate

    fields = set(patchaudit.Candidate.__dataclass_fields__)
    assert not (set(role.withheld) & fields)
    assert "rationale" in role.withheld
    assert "approach" in role.withheld


def test_the_falsification_test_has_no_field_a_diff_could_arrive_through() -> None:
    """S-10.1's construction: *test first* as a fact about the type rather than a
    claim about ordering."""
    role = ROLES[Agent.SURGEON]
    assert role.handover is falsification.FalsificationTest

    fields = set(falsification.FalsificationTest.model_fields)
    assert not (set(role.withheld) & fields)


def test_every_withheld_claim_is_checked_against_a_real_type() -> None:
    """A boundary nothing can verify is a comment."""
    for role in ROLES.values():
        if not role.withheld:
            continue
        assert role.handover is not None
        carried = set(getattr(role.handover, "model_fields", None) or {}) | set(
            getattr(role.handover, "__dataclass_fields__", None) or {}
        )
        assert carried, f"{role.agent.value}'s handover type exposes no fields to check"
        assert not (set(role.withheld) & carried)


def test_a_withheld_claim_with_nothing_to_check_it_against_is_refused() -> None:
    with pytest.raises(RoleError, match="A boundary nothing can verify is a comment"):
        Role(
            agent=Agent.SURGEON,
            purpose="x",
            prompts=("p",),
            phases=frozenset({Phase.REPAIR}),
            receives=("y",),
            withheld=("rationale",),
            handover=None,
        )


def test_a_billed_role_with_no_prompt_is_refused() -> None:
    """Every model call goes through a session whose prefix is a system prompt, so
    a billed role with none is a call nobody can locate."""
    with pytest.raises(RoleError, match="a call nobody can locate"):
        Role(
            agent=Agent.SURGEON,
            purpose="x",
            prompts=(),
            phases=frozenset({Phase.REPAIR}),
            receives=("y",),
        )


# ============ the gap the index exists to keep visible


def test_the_explorer_is_declared_and_nothing_bills_to_it() -> None:
    """**Kept visible rather than tidied away.** `Agent.EXPLORER` appears in no call
    site in `src/`: grounding authorizes and records `Phase.GROUND` against the
    budget, so the spend is bounded, but no model call is attributed to the agent
    that is supposed to make them.

    This test passes today by asserting the gap. When the grounding loop is built
    it will fail, and the index will have to be corrected — which is the point."""
    assert [role.agent for role in unattributed()] == [Agent.EXPLORER]

    named = [
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if "Agent.EXPLORER" in path.read_text(encoding="utf-8")
        and path.name not in ATTRIBUTION_EXEMPT
    ]
    assert named == [], f"the Explorer is now attributed in {named}; update its role"


def test_every_other_role_is_attributed_somewhere() -> None:
    for agent in Agent:
        if agent is Agent.EXPLORER:
            continue
        named = [
            path
            for path in SOURCE.rglob("*.py")
            if f"Agent.{agent.name}" in path.read_text(encoding="utf-8")
            and path.name not in ATTRIBUTION_EXEMPT
        ]
        assert named, f"{agent.value} is declared attributed and no call site names it"


# ============ the index of enforcements


def test_the_six_enforcements_are_listed_where_somebody_can_find_them() -> None:
    """What was missing was not enforcement but the list — somebody verifying the
    system had to find all six and know that six is all there are."""
    assert set(ENFORCEMENTS) == {
        "refuse_shared_session",
        "audit_messages",
        "Candidate",
        "scope_of",
        "DiagnosticSession",
        "apply_patch",
    }
    assert all(why.strip() for why in ENFORCEMENTS.values())


def test_the_index_renders_the_gap_and_the_boundaries() -> None:
    rendered = describe()
    assert "no call site names this agent" in rendered
    assert "cannot be given" in rendered
    assert "Where the boundaries are enforced" in rendered
    for agent in Agent:
        assert agent.value in rendered

"""Every v3 prompt is owned by a named role.

The mirror of `tests/agents/test_roles.py` for the v3 tree. A prompt with no
owner is a model call nobody can locate on a bill; a role with no prompt is a
role that cannot be called.
"""

from __future__ import annotations

import ast
from pathlib import Path

from coldfix.agent.roles import ROLES, V3_PACKAGES
from coldfix.cost.accounting import Agent as CostAgent

ROOT = Path("src/coldfix")


def defined_prompts() -> dict[str, str]:
    """Every module-level `SYSTEM`/`_SYSTEM` literal under the agent package.

    Parsed rather than imported, so a module nothing imports still counts -- a new
    prompt in a module nobody imports is exactly the one that would slip through.
    """
    found: dict[str, str] = {}
    for package in V3_PACKAGES:
        found.update(_in(ROOT / package))
    return found


def _in(tree: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(tree.rglob("*.py")):
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(statement, ast.Assign):
                continue
            names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            if not any(name in ("SYSTEM", "_SYSTEM") for name in names):
                continue
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                found[path.relative_to(ROOT).as_posix()] = statement.value.value
    return found


def test_the_two_registries_cover_the_whole_tree_between_them() -> None:
    """Whatever v1's test excludes is exactly what this one covers. Two hardcoded
    lists would agree until somebody added a package to one of them."""
    for package in V3_PACKAGES:
        assert (ROOT / package).is_dir(), f"{package} is named but does not exist"


def test_every_prompt_in_the_tree_is_claimed_by_a_role() -> None:
    claimed = {text for role in ROLES.values() for text in role.prompts}
    defined = defined_prompts()
    assert defined, "the parser found no prompts, which would make this vacuous"
    unclaimed = {path for path, text in defined.items() if text not in claimed}
    assert not unclaimed, f"prompts with no owner: {sorted(unclaimed)}"


def test_every_role_owns_at_least_one_prompt() -> None:
    """A role attributed calls and owning no prompt is a call nobody can locate."""
    for agent, role in ROLES.items():
        assert role.prompts, f"{agent.value} owns no prompt"


def test_every_role_records_what_it_structurally_cannot_do() -> None:
    """Written down beside the role, because the enforcement is elsewhere -- in
    the tool surface and the ledger -- and a reader needs both halves."""
    for agent, role in ROLES.items():
        assert role.cannot, f"{agent.value} records no limits"
        assert role.receives, f"{agent.value} records nothing it is given"


def test_every_v3_role_bills_under_its_own_name() -> None:
    """A role with no billing agent is a role whose spend lands on somebody else's
    line in the ledger. S-26.1: the meter bills by `cost.accounting.Agent`, and
    this registry is matched to it by member name."""
    billed = {agent.name for agent in CostAgent}
    missing = sorted(agent.value for agent in ROLES if agent.name not in billed)
    assert not missing, f"v3 roles with no billing agent: {missing}"

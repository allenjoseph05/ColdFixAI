"""Every v3 prompt is owned by a named role.

The mirror of `tests/agents/test_roles.py` for the v3 tree. A prompt with no
owner is a model call nobody can locate on a bill; a role with no prompt is a
role that cannot be called.
"""

from __future__ import annotations

import ast
from pathlib import Path

from coldfix.agent.roles import ROLES

TREE = Path("src/coldfix/agent")


def defined_prompts() -> dict[str, str]:
    """Every module-level `SYSTEM`/`_SYSTEM` literal under the agent package.

    Parsed rather than imported, so a module nothing imports still counts -- a new
    prompt in a module nobody imports is exactly the one that would slip through.
    """
    found: dict[str, str] = {}
    for path in sorted(TREE.rglob("*.py")):
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(statement, ast.Assign):
                continue
            names = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            if not any(name in ("SYSTEM", "_SYSTEM") for name in names):
                continue
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                found[path.relative_to(TREE).as_posix()] = statement.value.value
    return found


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

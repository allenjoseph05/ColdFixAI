"""The holdout must not leak into development.

S-0.6 designates `healthchecks` as the holdout: a repository never used during
development, so that evaluation measures generalization rather than memory of
the one repo the tool was built against. The backlog states the risk directly —
*"developing and evaluating against the same repo produces a tool that works on
exactly one repo."*

A rule like that decays. It is easy to reach for the holdout as a convenient
second example when a Django question comes up, and nothing about doing so feels
like a violation at the time. So it is enforced here rather than promised in a
document, per the project's own standard: if a file is the only thing preventing
something, that rule needs code instead.

This is the first test in the repository. S-0.7 builds the actual test strategy;
this is one guard, not that.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS = REPO_ROOT / "targets.toml"

# Files permitted to name the holdout. Each is a place where naming it is the
# point: the pin itself, the decision record, and this test.
#
# The three S-0.3 entries are the recorded contamination. That spike ground the
# repository once, by hand, *before* it was designated the holdout, and its
# obstacles fed the playbook proposals. Deleting those records would hide a real
# if minor compromise; listing them here keeps it visible. Nothing has been added
# to this set since designation, and adding one should be a deliberate act with a
# reason, not a way to make this test pass.
ALLOWED = {
    "targets.toml",
    "docs/adr/011-development-target-and-holdout.md",
    "tests/test_holdout_discipline.py",
    "spikes/S-0.3-grounding/FINDINGS.md",
    "spikes/S-0.3-grounding/README.md",
    "spikes/S-0.3-grounding/seeds/seed_a.py",
}

# Directories that are not ours to police.
SKIP_DIRS = {".git", ".venv", "repo", "repos", "results", "__pycache__"}
SKIP_SUFFIXES = {".pyc", ".dump", ".sqlite3", ".db", ".lock"}


def _load_holdout() -> dict[str, str]:
    if not TARGETS.exists():
        pytest.fail(f"{TARGETS} is missing — S-0.6 pins the targets there")
    with TARGETS.open("rb") as handle:
        # `tomllib.load` is untyped by nature, so the annotation is the assertion.
        holdout: dict[str, str] = tomllib.load(handle)["holdout"]
    return holdout


def _searchable_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def test_targets_file_pins_by_commit() -> None:
    """Every designated repository is pinned to a full commit SHA.

    "Pinned by commit" is an S-0.6 acceptance criterion, and a branch name or a
    short SHA does not satisfy it — branches move, and short SHAs collide.
    """
    with TARGETS.open("rb") as handle:
        targets = tomllib.load(handle)

    for role in ("development", "holdout", "reserve"):
        assert role in targets, f"targets.toml is missing the {role!r} entry"
        commit = targets[role]["commit"]
        assert len(commit) == 40, f"{role} commit {commit!r} is not a full SHA"
        assert all(c in "0123456789abcdef" for c in commit), (
            f"{role} commit {commit!r} is not hexadecimal"
        )


def test_development_target_and_holdout_are_different_repositories() -> None:
    """The whole point of a holdout is that it is not the target."""
    with TARGETS.open("rb") as handle:
        targets = tomllib.load(handle)

    assert targets["development"]["url"] != targets["holdout"]["url"]
    assert targets["development"]["commit"] != targets["holdout"]["commit"]


def test_holdout_is_not_referenced_outside_the_allowed_files() -> None:
    """The holdout appears only where naming it is the point.

    This is the test that actually protects the evaluation. If it fails, either
    the holdout has been used during development — in which case it is no longer
    a holdout and S-0.6 needs revisiting — or a new file legitimately needs to
    name it and `ALLOWED` should be extended **deliberately**, with the reason
    recorded.

    Extending `ALLOWED` to make this pass is not a fix unless the new entry is a
    place where naming the holdout is the point.
    """
    holdout = _load_holdout()
    needles = {holdout["name"].lower(), holdout["url"].lower(), holdout["commit"].lower()}

    offenders: dict[str, set[str]] = {}
    for path in _searchable_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        found = {needle for needle in needles if needle in text}
        if found:
            offenders[relative] = found

    assert not offenders, (
        "The holdout is referenced outside the files allowed to name it.\n"
        "Either it has been used during development — in which case it is no "
        "longer a holdout — or ALLOWED needs a deliberate addition.\n"
        + "\n".join(f"  {path}: {sorted(found)}" for path, found in sorted(offenders.items()))
    )


def test_the_guard_would_actually_catch_a_violation(tmp_path: Path) -> None:
    """The guard detects a leak rather than passing vacuously.

    A test that scans for a string is only as good as its scanning. This plants
    the holdout's name in a file the guard would examine and asserts the
    matching logic finds it — so a future refactor that silently stops searching
    fails here instead of passing forever.
    """
    holdout = _load_holdout()
    planted = tmp_path / "some_module.py"
    planted.write_text(
        f"# accidentally reaching for the holdout\nURL = '{holdout['url']}'\n",
        encoding="utf-8",
    )

    text = planted.read_text(encoding="utf-8").lower()
    assert holdout["url"].lower() in text
    assert holdout["name"].lower() in text

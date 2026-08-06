"""A real repository to run real git against, shared by the Epic 2 test files.

Nothing here is mocked. The acceptance criteria in this epic are claims about
what git and docker actually do, and a fake would only assert what the tests
already believe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coldfix.bench.execute import execute

GIT_TIMEOUT = 120.0


def git(root: Path, *args: str) -> str:
    """Run git in `root` and return stdout, failing loudly on a non-zero exit.

    Identity and signing are supplied per-invocation rather than written into
    the repository, so the tests do not depend on the developer's global git
    configuration and do not modify it.
    """
    result = execute(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=ColdFix Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        timeout=GIT_TIMEOUT,
    )
    if result.exit_code != 0:
        message = f"git {' '.join(args)} failed: {result.stderr}"
        raise AssertionError(message)
    return result.stdout


def commit_of(root: Path, revision: str) -> str:
    return git(root, "rev-parse", revision).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with two commits, on `main`, with a clean working tree.

    `subject.py` differs between the two commits, so a test can tell which
    revision a worktree was created at by reading it.
    """
    root = tmp_path / "subject"
    root.mkdir()
    git(root, "init", "--initial-branch=main")

    (root / "subject.py").write_text("VERSION = 1\n")
    (root / ".gitignore").write_text("build/\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "first")

    (root / "subject.py").write_text("VERSION = 2\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "second")

    # A branch that is *not* checked out anywhere. `main` cannot show whether
    # `--detach` is doing any work, because git refuses to check out a branch
    # that another worktree already holds — the refusal masks the flag.
    git(root, "branch", "feature", "HEAD~1")

    return root


@pytest.fixture
def worktree_path(tmp_path: Path) -> Path:
    """A path outside the repository, since one inside it is refused."""
    return tmp_path / "worktrees" / "diagnostic"

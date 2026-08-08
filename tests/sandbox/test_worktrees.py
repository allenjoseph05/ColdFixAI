"""Worktrees are created at a fixed commit, and can be proven gone afterwards.

Every test builds a real repository and runs real git. There is no mock: the
acceptance criteria are claims about what git does — that `--force` really
discards uncommitted work, that a detached worktree really does not follow a
branch — and a fake would only assert what this file already believes.

The interesting tests are the two asymmetric ones. `create` refuses a dirty main
working tree; `destroy` deliberately does not, because a guard that stranded a
worktree full of ablated code would be protecting the wrong thing. Both
directions are asserted, so neither can be changed silently.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from conftest import commit_of, git

from coldfix.bench.execute import ExecutionResult, execute
from coldfix.sandbox import worktrees
from coldfix.sandbox.worktrees import (
    DirtyWorkingTreeError,
    NotARepositoryError,
    Repository,
    UnknownRevisionError,
    WorktreeError,
    WorktreeNotDestroyedError,
    WorktreePathError,
)

# ------------------------------------------------------------ create and list


def test_a_worktree_is_created_listed_and_destroyed(repo: Path, worktree_path: Path) -> None:
    repository = Repository(root=repo)

    created = repository.create_worktree(worktree_path, "HEAD")

    assert worktree_path.is_dir()
    assert [w.path for w in repository.worktrees()] == [repo.resolve(), worktree_path.resolve()]

    repository.destroy_worktree(created.path)

    assert not worktree_path.exists()
    assert [w.path for w in repository.worktrees()] == [repo.resolve()]


def test_the_main_working_tree_is_listed_first_and_marked(repo: Path) -> None:
    main = Repository(root=repo).worktrees()[0]

    assert main.is_main
    assert main.path == repo.resolve()
    assert main.branch == "main"


def test_a_worktree_can_be_created_at_an_arbitrary_revision(
    repo: Path, worktree_path: Path
) -> None:
    """AC 2, and the reason `revision` has no default.

    The first commit is not what HEAD points at, so reading the file back is
    what distinguishes a worktree at the requested revision from one that
    quietly used HEAD.
    """
    repository = Repository(root=repo)
    first = commit_of(repo, "HEAD~1")

    created = repository.create_worktree(worktree_path, first)

    assert created.revision == first
    assert (worktree_path / "subject.py").read_text() == "VERSION = 1\n"


@pytest.mark.parametrize("revision", ["HEAD", "HEAD~1", "main"])
def test_anything_rev_parse_accepts_is_accepted(
    repo: Path, worktree_path: Path, revision: str
) -> None:
    repository = Repository(root=repo)

    created = repository.create_worktree(worktree_path, revision)

    assert created.revision == commit_of(repo, revision)


def test_a_worktree_is_always_detached(repo: Path, worktree_path: Path) -> None:
    """Naming a branch pins its current commit rather than following it.

    A branch checked out in one worktree cannot be checked out in another, so
    branch-based creation would fail depending on what else is running. And a
    branch moves: an investigation that measured "the revision on main" would
    have measured whatever main pointed at during each separate experiment.
    """
    repository = Repository(root=repo)

    created = repository.create_worktree(worktree_path, "main")

    assert created.detached
    assert created.branch is None
    assert repository.worktrees()[1].branch is None


def test_a_branch_nobody_has_checked_out_is_still_detached(repo: Path, worktree_path: Path) -> None:
    """The only case where `--detach` is load-bearing, so the only one that proves it.

    Given `main`, git refuses outright — a branch checked out in one worktree
    cannot be checked out in another — and that refusal masks whether the flag
    is present. `feature` is checked out nowhere, so without `--detach` git
    would happily attach the worktree to it, and every commit made during an
    ablation would move a branch the user owns.
    """
    repository = Repository(root=repo)
    feature_before = commit_of(repo, "feature")

    created = repository.create_worktree(worktree_path, "feature")

    assert created.detached
    assert repository.worktrees()[1].branch is None
    assert git(repo, "symbolic-ref", "-q", "HEAD").strip() == "refs/heads/main"
    assert commit_of(repo, "feature") == feature_before


def test_the_worktree_does_not_follow_the_branch_it_was_created_from(
    repo: Path, worktree_path: Path
) -> None:
    """The measurement is pinned to a commit, and a later commit cannot move it."""
    repository = Repository(root=repo)
    created = repository.create_worktree(worktree_path, "main")

    (repo / "subject.py").write_text("VERSION = 3\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "third")

    assert (worktree_path / "subject.py").read_text() == "VERSION = 2\n"
    assert created.revision != commit_of(repo, "main")


def test_nothing_this_module_does_advances_a_ref(repo: Path, worktree_path: Path) -> None:
    """A detached worktree has no branch to move, so the user's refs are untouched."""
    repository = Repository(root=repo)
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")

    created = repository.create_worktree(worktree_path, "HEAD~1")
    (created.path / "subject.py").write_text("ablated\n")
    repository.destroy_worktree(created.path)

    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before


# ---------------------------------------------------------------- destruction


def test_destroying_a_worktree_discards_uncommitted_changes(
    repo: Path, worktree_path: Path
) -> None:
    """AC 3. A diagnostic worktree is *expected* to be full of broken code.

    `--force` is not an escalation offered to the caller; a removal that
    refused to discard the ablation would never be what this system wants.
    """
    repository = Repository(root=repo)
    created = repository.create_worktree(worktree_path, "HEAD")
    (created.path / "subject.py").write_text("deliberately broken\n")
    (created.path / "new_file.py").write_text("also uncommitted\n")

    repository.destroy_worktree(created.path)

    assert not worktree_path.exists()


def test_a_worktree_that_survives_removal_is_reported(
    repo: Path, worktree_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git worktree remove` can report success and leave files behind.

    A process still holding a file is routine on Windows, and becomes possible
    on every platform once S-2.3 bind-mounts a worktree into a container. The
    directory is checked rather than git's exit code trusted, so this simulates
    the surviving directory rather than the locked file that would cause it.
    """
    repository = Repository(root=repo)
    created = repository.create_worktree(worktree_path, "HEAD")

    def pretend_removal_worked(command: Sequence[str], **kwargs: object) -> ExecutionResult:
        argv = [str(part) for part in command]
        if "worktree" in argv and "remove" in argv:
            return ExecutionResult(
                command=tuple(argv), exit_code=0, stdout="", stderr="", wall_seconds=0.01
            )
        # The same function object `worktrees` imported, so this is the real
        # call rather than a second route into it.
        return execute(command, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worktrees, "execute", pretend_removal_worked)

    with pytest.raises(WorktreeNotDestroyedError, match="directory remains"):
        repository.destroy_worktree(created.path)


def test_destroying_something_that_is_not_a_worktree_is_an_error(
    repo: Path, tmp_path: Path
) -> None:
    stray = tmp_path / "not-a-worktree"
    stray.mkdir()
    (stray / "file.txt").write_text("x")

    with pytest.raises(WorktreeNotDestroyedError):
        Repository(root=repo).destroy_worktree(stray)


# ------------------------------------------------- the dirty-tree guard


def test_creating_refuses_a_modified_working_tree(repo: Path, worktree_path: Path) -> None:
    """AC 4.

    Uncommitted edits live in no commit, so a worktree at HEAD does not contain
    them. Every finding would cite code that differs from the user's copy.
    """
    (repo / "subject.py").write_text("VERSION = 99\n")

    with pytest.raises(DirtyWorkingTreeError, match="uncommitted change"):
        Repository(root=repo).create_worktree(worktree_path, "HEAD")


def test_creating_refuses_a_staged_change(repo: Path, worktree_path: Path) -> None:
    (repo / "subject.py").write_text("VERSION = 99\n")
    git(repo, "add", "-A")

    with pytest.raises(DirtyWorkingTreeError):
        Repository(root=repo).create_worktree(worktree_path, "HEAD")


def test_creating_refuses_an_untracked_file_and_says_which_problem_it_is(
    repo: Path, worktree_path: Path
) -> None:
    """`stash` fixes a modification and does nothing about an untracked file.

    A message that conflated the two would send the reader to a command that
    cannot help them.
    """
    (repo / "scratch.py").write_text("print('local experiment')\n")

    with pytest.raises(DirtyWorkingTreeError, match="untracked file") as raised:
        Repository(root=repo).create_worktree(worktree_path, "HEAD")

    assert raised.value.untracked == ("scratch.py",)
    assert raised.value.modified == ()


def test_ignored_files_are_not_changes(repo: Path, worktree_path: Path) -> None:
    """Otherwise no real repository is ever clean enough to measure.

    Build output and a local database are ignored, so `git status --porcelain`
    does not list them and this definition of clean stays usable.
    """
    build = repo / "build"
    build.mkdir()
    (build / "artifact.o").write_text("binary")

    repository = Repository(root=repo)

    assert repository.is_clean()
    assert repository.create_worktree(worktree_path, "HEAD").path.is_dir()


def test_destroying_is_deliberately_not_refused_by_a_dirty_tree(
    repo: Path, worktree_path: Path
) -> None:
    """The asymmetry, asserted so it cannot be 'fixed' into a safety regression.

    A main tree that goes dirty mid-investigation must not be able to strand a
    worktree full of ablated, deliberately broken code — that is the outcome
    ADR 004 exists to prevent. The guard protects measurements, not broken code.
    """
    repository = Repository(root=repo)
    created = repository.create_worktree(worktree_path, "HEAD")

    (repo / "subject.py").write_text("the user started editing mid-run\n")
    (repo / "untracked.py").write_text("and added a file\n")
    assert not repository.is_clean()

    repository.destroy_worktree(created.path)

    assert not worktree_path.exists()


def test_listing_is_deliberately_not_refused_by_a_dirty_tree(repo: Path) -> None:
    """A caller trying to find what needs cleaning up must not be refused
    because something needs cleaning up."""
    repository = Repository(root=repo)
    (repo / "subject.py").write_text("dirty\n")

    assert len(repository.worktrees()) == 1


# ------------------------------------------------------------- bad input


def test_an_unknown_revision_is_refused_before_a_directory_is_made(
    repo: Path, worktree_path: Path
) -> None:
    with pytest.raises(UnknownRevisionError):
        Repository(root=repo).create_worktree(worktree_path, "no-such-revision")

    assert not worktree_path.exists()


def test_a_revision_that_is_not_a_commit_is_refused(repo: Path, worktree_path: Path) -> None:
    """A tree object is a legal git revision and cannot hold a worktree."""
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()

    with pytest.raises(UnknownRevisionError):
        Repository(root=repo).create_worktree(worktree_path, tree)


def test_an_existing_path_is_refused(repo: Path, worktree_path: Path) -> None:
    worktree_path.mkdir(parents=True)

    with pytest.raises(WorktreePathError, match="already exists"):
        Repository(root=repo).create_worktree(worktree_path, "HEAD")


def test_a_worktree_inside_the_main_tree_is_refused(repo: Path) -> None:
    """Adversarial: this is the module disabling itself by running once.

    A worktree created inside the main tree appears there as untracked content,
    which makes the main tree dirty, which makes every subsequent `create`
    refuse. Git permits it; this does not.
    """
    with pytest.raises(WorktreePathError, match="inside the main working tree"):
        Repository(root=repo).create_worktree(repo / "nested", "HEAD")


def test_a_path_that_is_not_a_repository_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(NotARepositoryError, match="not a git repository"):
        Repository(root=plain)


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotARepositoryError, match="not a directory"):
        Repository(root=tmp_path / "absent")


def test_a_linked_worktree_cannot_be_used_as_a_repository_root(
    repo: Path, worktree_path: Path
) -> None:
    """Every check would otherwise be scoped to the wrong tree.

    `git status` in a linked worktree reports on that checkout, while the
    dirty-tree guard's entire purpose is to describe the main one. The
    operations would appear to work and would answer a different question.
    """
    Repository(root=repo).create_worktree(worktree_path, "HEAD")

    with pytest.raises(NotARepositoryError, match="linked worktree"):
        Repository(root=worktree_path)


def test_a_stale_registration_is_refused_rather_than_forced_over(
    repo: Path, worktree_path: Path
) -> None:
    """Someone deleted the directory by hand; git still has it registered.

    Git offers `add --force` to override this. Taking that offer automatically
    would mean writing over whatever the registration was protecting, without
    the caller ever learning the repository was in an unexpected state — so
    git's refusal is passed through as a typed error instead.
    """
    repository = Repository(root=repo)
    created = repository.create_worktree(worktree_path, "HEAD")
    shutil.rmtree(created.path)

    with pytest.raises(WorktreeError, match="already registered"):
        repository.create_worktree(worktree_path, "HEAD")

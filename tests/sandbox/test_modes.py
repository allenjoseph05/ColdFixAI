"""A diagnostic run cannot produce a patch, and each route is attacked separately.

`CLAUDE.md` requires this be structural and requires the test to attempt the
violation rather than describe it. So most of this file is written as attacks:
each one takes a route by which a change made during an ablation could reach a
patch, and asserts the route is not there.

The four routes, and what closes each:

1. Ask the session for a diff — there is no such method on `DiagnosticSession`,
   and the public surface is asserted by name so adding one fails a test.
2. Produce the diff inside the container — the bind mount carries the working
   files and not the `.git/worktrees/` metadata they point at, so no repository
   exists in there to diff against.
3. Produce it from the worktree after the fact — closing the session destroys
   the worktree, verified against the filesystem.
4. Let the change reach the repository itself — the worktree is detached and
   nothing commits, so the main tree and every ref are untouched.

Route 2 deserves a note. `python:3.12-slim` ships no git at all, so a test that
merely ran `git diff` in the container would pass for the wrong reason and would
keep passing if the metadata were later mounted. The test therefore asserts the
metadata's absence directly, which is the fact that would actually change.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from conftest import git

from coldfix.sandbox import docker_available
from coldfix.sandbox.modes import (
    CandidateSession,
    DiagnosticSession,
    ExecutionMode,
    SessionClosedError,
    Workbench,
)
from coldfix.sandbox.worktrees import Repository

IMAGE = "python:3.12-slim"
TIMEOUT = 120.0


@pytest.fixture
def workbench(repo: Path, tmp_path: Path) -> Workbench:
    return Workbench(
        repository=Repository(root=repo),
        image=IMAGE,
        worktree_root=tmp_path / "sessions",
    )


@pytest.fixture
def _requires_docker() -> None:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")


def gitdir_of(worktree: Path) -> str:
    """The path a linked worktree's `.git` file points at.

    That file is the whole of route 2: it names a directory in the main
    repository, and nothing mounts that directory into the container.
    """
    return (worktree / ".git").read_text().removeprefix("gitdir:").strip()


# ------------------------------------------------------------ the two modes


def test_both_modes_exist(workbench: Workbench) -> None:
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as diagnostic:
        assert isinstance(diagnostic, DiagnosticSession)
        assert diagnostic.mode is ExecutionMode.DIAGNOSTIC

    with workbench.open("HEAD", mode=ExecutionMode.CANDIDATE) as candidate:
        assert isinstance(candidate, CandidateSession)
        assert candidate.mode is ExecutionMode.CANDIDATE


def test_mode_is_required_and_has_no_default(workbench: Workbench) -> None:
    """AC 5. There is no value of `mode` that means "whichever"."""
    parameter = inspect.signature(Workbench.open).parameters["mode"]

    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    with pytest.raises(TypeError):
        workbench.open("HEAD")  # type: ignore[call-arg]


def test_each_mode_gets_its_own_worktree(workbench: Workbench) -> None:
    """AC 2. Sharing one would make every other guarantee here decorative."""
    with (
        workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as diagnostic,
        workbench.open("HEAD", mode=ExecutionMode.CANDIDATE) as candidate,
    ):
        assert diagnostic.worktree.path != candidate.worktree.path
        assert diagnostic.worktree.path not in candidate.worktree.path.parents
        assert candidate.worktree.path not in diagnostic.worktree.path.parents
        assert diagnostic.worktree.path.name.startswith("diagnostic-")
        assert candidate.worktree.path.name.startswith("candidate-")


def test_two_sessions_in_the_same_mode_do_not_share_either(workbench: Workbench) -> None:
    with (
        workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as first,
        workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as second,
    ):
        assert first.worktree.path != second.worktree.path


# ------------------------------------- route 1: ask the session for the diff


def test_a_diagnostic_session_has_no_operation_that_returns_a_diff() -> None:
    """The enforcement, stated as precisely as a test can state it.

    Not "a diff request is rejected" — there is nothing to call. This asserts
    the public surface by name, and is deliberately brittle: adding any
    accessor to `DiagnosticSession` fails here and has to be argued for rather
    than merged.
    """
    surface = {name for name in dir(DiagnosticSession) if not name.startswith("_")}

    assert surface == {"mode", "worktree", "closed", "run", "close"}
    assert "diff" not in surface


def test_asking_a_diagnostic_session_for_a_diff_fails(workbench: Workbench) -> None:
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        assert not hasattr(session, "diff")

        with pytest.raises(AttributeError):
            session.diff()  # type: ignore[attr-defined]


def test_a_candidate_session_is_the_only_thing_that_produces_a_diff(
    workbench: Workbench,
) -> None:
    surface = {name for name in dir(CandidateSession) if not name.startswith("_")}

    assert surface == {"mode", "worktree", "closed", "run", "close", "diff"}

    with workbench.open("HEAD", mode=ExecutionMode.CANDIDATE) as session:
        # The narrowing a type checker needs is the same fact the test asserts:
        # only this class has the method, so only this class can be asked.
        assert isinstance(session, CandidateSession)
        (session.worktree.path / "subject.py").write_text("VERSION = 2\nfixed = True\n")

        assert "fixed = True" in session.diff()


def test_a_candidate_diff_includes_a_file_the_change_added(workbench: Workbench) -> None:
    """Untracked additions are registered first, or a new module is invisible."""
    with workbench.open("HEAD", mode=ExecutionMode.CANDIDATE) as session:
        assert isinstance(session, CandidateSession)
        (session.worktree.path / "added.py").write_text("CACHE = {}\n")

        diff = session.diff()

    assert "added.py" in diff
    assert "CACHE = {}" in diff


# ------------------------------- route 2: produce the diff inside the container


def test_the_container_has_no_repository_to_diff_against(workbench: Workbench) -> None:
    """The bind mount carries working files, never the metadata they refer to.

    A linked worktree's `.git` is a file naming a directory inside the main
    repository. S-2.1 mounts exactly one directory — the worktree — so that
    named path does not exist in the container and git there has nothing to
    read, whether or not a git binary is present.
    """
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        gitdir = Path(gitdir_of(session.worktree.path))

        assert gitdir.exists()
        assert session.worktree.path not in gitdir.parents
        assert gitdir.is_relative_to(workbench.repository.root)


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.usefixtures("_requires_docker")
def test_the_metadata_path_is_absent_inside_the_container(workbench: Workbench) -> None:
    """The same claim, asserted from inside rather than reasoned about outside."""
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        gitdir = gitdir_of(session.worktree.path)

        result = session.run(
            [
                "python",
                "-c",
                f"import os;print('PRESENT' if os.path.exists({gitdir!r}) else 'ABSENT')",
            ],
            timeout=TIMEOUT,
        )

    assert "ABSENT" in result.stdout


# --------------------------- route 3: recover the diff after the session ends


def test_closing_a_diagnostic_session_destroys_its_worktree(workbench: Workbench) -> None:
    """AC 4. Text describing files that no longer exist cannot be applied."""
    session = workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC)
    path = session.worktree.path
    assert path.is_dir()

    session.close()

    assert not path.exists()


def test_an_ablation_leaves_nothing_behind_to_cut_a_patch_from(
    workbench: Workbench,
) -> None:
    """The attack, end to end.

    Break the code exactly as an ablation does, keep the path, let the session
    end, then try to reach any of it. Nothing survives: not the modified file,
    not the directory, and not the worktree registration.
    """
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        ablated = session.worktree.path
        (ablated / "subject.py").write_text("def serialize(self): return None  # stubbed\n")
        (ablated / "extra_evidence.py").write_text("MEASUREMENT = 1\n")
        assert (ablated / "subject.py").read_text().startswith("def serialize")

    assert not ablated.exists()
    assert not (ablated / "subject.py").exists()
    assert [w.path for w in workbench.repository.worktrees()] == [workbench.repository.root]


def test_a_closed_session_cannot_be_used_again(workbench: Workbench) -> None:
    """Reopening on demand would give an ablation a second life after collection."""
    diagnostic = workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC)
    candidate = workbench.open("HEAD", mode=ExecutionMode.CANDIDATE)
    assert isinstance(candidate, CandidateSession)
    diagnostic.close()
    candidate.close()

    with pytest.raises(SessionClosedError):
        diagnostic.run(["true"], timeout=TIMEOUT)

    with pytest.raises(SessionClosedError):
        candidate.diff()


def test_closing_twice_is_not_an_error(workbench: Workbench) -> None:
    session = workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC)

    session.close()
    session.close()

    assert session.closed


def test_the_worktree_is_destroyed_even_when_the_body_raises(workbench: Workbench) -> None:
    """A failed experiment is exactly when a stranded ablation is most likely."""
    opened: list[Path] = []

    def experiment_that_fails() -> None:
        with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
            opened.append(session.worktree.path)
            message = "the experiment failed"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError):
        experiment_that_fails()

    assert not opened[0].exists()


# ------------------------ route 4: let the change reach the repository itself


def test_nothing_done_in_a_diagnostic_session_reaches_the_repository(
    workbench: Workbench, repo: Path
) -> None:
    """The worktree is detached, so there is no branch for an ablation to move."""
    refs_before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")

    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        (session.worktree.path / "subject.py").write_text("catastrophically broken\n")

    assert (repo / "subject.py").read_text() == "VERSION = 2\n"
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert workbench.repository.is_clean()


def test_a_session_is_pinned_to_the_revision_it_was_opened_at(workbench: Workbench) -> None:
    with workbench.open("HEAD~1", mode=ExecutionMode.DIAGNOSTIC) as session:
        assert (session.worktree.path / "subject.py").read_text() == "VERSION = 1\n"
        assert session.worktree.detached


# ---------------------------------------------------------------- lifecycle


def test_a_failed_open_does_not_strand_a_worktree(repo: Path, tmp_path: Path) -> None:
    """Between creating the worktree and building the sandbox there is no owner.

    A failure there would leave a checkout that no `close()` can reach, which is
    the stranded worktree S-2.2 exists to prevent, arriving through the door
    S-2.3 opened.
    """
    workbench = Workbench(
        repository=Repository(root=repo),
        image="   ",
        worktree_root=tmp_path / "sessions",
    )

    with pytest.raises(ValueError, match="image"):
        workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC)

    assert [w.path for w in workbench.repository.worktrees()] == [workbench.repository.root]


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.usefixtures("_requires_docker")
def test_a_session_runs_its_workload_in_a_container_over_its_own_worktree(
    workbench: Workbench,
) -> None:
    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        result = session.run(["python", "-c", "print(open('subject.py').read())"], timeout=TIMEOUT)

    assert "VERSION = 2" in result.stdout


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.usefixtures("_requires_docker")
def test_the_two_modes_run_in_distinct_containers_over_distinct_mounts(
    workbench: Workbench,
) -> None:
    """AC 2, the container half, asserted from what docker was actually told."""
    with (
        workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as diagnostic,
        workbench.open("HEAD", mode=ExecutionMode.CANDIDATE) as candidate,
    ):
        first = diagnostic.run(["python", "-c", "print(1)"], timeout=TIMEOUT).command
        second = candidate.run(["python", "-c", "print(1)"], timeout=TIMEOUT).command

    names = [argv[argv.index("--name") + 1] for argv in (first, second)]
    mounts = [argv[argv.index("--mount") + 1] for argv in (first, second)]

    assert names[0] != names[1]
    assert mounts[0] != mounts[1]

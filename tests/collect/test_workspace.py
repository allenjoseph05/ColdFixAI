"""S-18.6.

The escape attempts here are the point. Each one is a shape a real repository
produces by accident -- a relative path that climbs, a symlink to somewhere
shared, a build directory outside the tree -- rather than an attack somebody
composed, and every one is refused by resolving the path rather than by
recognising it.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

from coldfix.collect.workspace import (
    MAX_LINES,
    MAX_OUTPUT_CHARS,
    AbsolutePathError,
    FileExistsHereError,
    MissingFileError,
    NoPosixShellError,
    NotTextError,
    OutsideScratchError,
    PathEscapesWorkspaceError,
    Workspace,
    _truncate,
    read_file,
    run_bash,
    write_file,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "subject"
    (root / "app").mkdir(parents=True)
    (root / "app" / "models.py").write_text(
        "\n".join(f"line {n}" for n in range(1, 301)), encoding="utf-8"
    )
    return Workspace(root=root)


# ------------------------------------------------------------- confinement


def test_a_path_that_climbs_out_is_refused(workspace: Workspace) -> None:
    with pytest.raises(PathEscapesWorkspaceError, match="outside"):
        read_file(workspace, "../../etc/passwd")


def test_an_absolute_path_is_refused(workspace: Workspace) -> None:
    absolute = "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd"
    with pytest.raises(AbsolutePathError, match="absolute"):
        read_file(workspace, absolute)


def test_climbing_and_returning_is_allowed_because_it_ends_up_inside(
    workspace: Workspace,
) -> None:
    """`a/../b` is inside. A check that rejected the characters `..` would refuse
    a path that never leaves, which is how a guard earns a reputation for being
    something to work around."""
    window = read_file(workspace, "app/../app/models.py", limit=1)
    assert window.lines == ("line 1",)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_pointing_out_of_the_tree_is_refused(workspace: Workspace) -> None:
    """The case a string check cannot catch. The path contains no `..` and is not
    absolute; only resolving it reveals where it goes."""
    outside = workspace.root.parent / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("not ours", encoding="utf-8")
    (workspace.root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathEscapesWorkspaceError):
        read_file(workspace, "link/secret.txt")


def test_a_sibling_directory_with_a_shared_prefix_is_refused(tmp_path: Path) -> None:
    """`/work` and `/work-other` share a string prefix and share nothing else. A
    guard comparing prefixes as text lets the second through."""
    (tmp_path / "work").mkdir()
    (tmp_path / "work-other").mkdir()
    (tmp_path / "work-other" / "f.txt").write_text("x", encoding="utf-8")
    workspace = Workspace(root=tmp_path / "work")

    with pytest.raises(PathEscapesWorkspaceError):
        read_file(workspace, "../work-other/f.txt")


# ---------------------------------------------------------------- read_file


def test_a_window_is_one_indexed_so_it_can_be_cited(workspace: Workspace) -> None:
    """A finding says `models.py:112`, and that is the number counted here."""
    window = read_file(workspace, "app/models.py", offset=112, limit=3)
    assert window.first_line == 112
    assert window.lines == ("line 112", "line 113", "line 114")
    assert window.last_line == 114


def test_the_window_is_capped_however_much_is_asked_for(workspace: Workspace) -> None:
    """Otherwise a caller reads a repository one large window at a time, which is
    the thing the design exists to avoid."""
    window = read_file(workspace, "app/models.py", limit=10_000)
    assert len(window.lines) == MAX_LINES
    assert window.total_lines == 300
    assert window.more_below


def test_a_window_past_the_end_is_empty_rather_than_an_error(workspace: Workspace) -> None:
    """The file has 300 lines and the caller asked for 900. That is worth knowing,
    not worth failing over."""
    window = read_file(workspace, "app/models.py", offset=900)
    assert window.lines == ()
    assert window.total_lines == 300
    assert not window.more_below


def test_a_file_that_is_not_text_is_named_rather_than_mangled(workspace: Workspace) -> None:
    (workspace.root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    with pytest.raises(NotTextError, match="not valid UTF-8"):
        read_file(workspace, "blob.bin")


def test_a_missing_file_is_named(workspace: Workspace) -> None:
    with pytest.raises(MissingFileError):
        read_file(workspace, "app/nope.py")


def test_a_directory_is_not_a_file(workspace: Workspace) -> None:
    with pytest.raises(MissingFileError):
        read_file(workspace, "app")


# --------------------------------------------------------------- write_file


def test_a_new_file_lands_in_the_scratch_directory(workspace: Workspace) -> None:
    written = write_file(workspace, "coldfix/drive.py", "print('hello')\n")
    assert written.read_text(encoding="utf-8") == "print('hello')\n"
    assert written.parent == workspace.scratch


def test_overwriting_a_source_file_is_refused(workspace: Workspace) -> None:
    """The AC's test. The one thing this system adds to a subject is a driver;
    everything else it does to source happens in a copy that is destroyed. A tool
    that could overwrite would make that promise unenforceable."""
    original = (workspace.root / "app" / "models.py").read_bytes()
    with pytest.raises(OutsideScratchError):
        write_file(workspace, "app/models.py", "raise SystemExit()\n")
    assert (workspace.root / "app" / "models.py").read_bytes() == original


def test_overwriting_even_inside_scratch_is_refused(workspace: Workspace) -> None:
    """Creation and modification are different operations, and only one exists."""
    write_file(workspace, "coldfix/drive.py", "first\n")
    with pytest.raises(FileExistsHereError, match="never modifies"):
        write_file(workspace, "coldfix/drive.py", "second\n")
    assert (workspace.scratch / "drive.py").read_text(encoding="utf-8") == "first\n"


def test_writing_outside_the_workspace_is_refused(workspace: Workspace) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        write_file(workspace, "../escaped.py", "x\n")


def test_nested_scratch_directories_are_created(workspace: Workspace) -> None:
    written = write_file(workspace, "coldfix/bench/drive.py", "x\n")
    assert written.exists()


# ------------------------------------------------------------------- bash

posix_shell = pytest.mark.skipif(
    os.name == "nt", reason="run_bash refuses on Windows; these run in the container"
)


def test_truncation_says_what_to_do_rather_than_only_that_it_happened() -> None:
    """The pure half, tested everywhere. Truncation that only reports itself
    leaves the caller to guess; naming the three commands that narrow it is the
    difference between a dead end and a next step."""
    clipped, cut = _truncate("x" * 50_000)
    assert cut
    assert len(clipped) < 50_000
    assert f"truncated at {MAX_OUTPUT_CHARS} characters" in clipped
    for narrowing in ("head", "tail", "grep"):
        assert narrowing in clipped


def test_short_output_is_returned_untouched() -> None:
    clipped, cut = _truncate("small")
    assert (clipped, cut) == ("small", False)


def test_bash_is_refused_on_a_platform_without_one() -> None:
    """cmd.exe would run the same string and do something else."""
    if os.name != "nt":
        pytest.skip("this platform has a POSIX shell")
    with pytest.raises(NoPosixShellError, match="not bash"):
        run_bash(Workspace(root=Path.cwd()), "echo hello")


def test_there_is_no_way_to_ask_for_a_different_directory() -> None:
    """Not a security control -- the command may still `cd`, and the container is
    what makes that not matter. It removes the ordinary accident: a command that
    quietly ran somewhere else and measured the wrong tree."""
    assert "cwd" not in inspect.signature(run_bash).parameters


@posix_shell
def test_a_command_runs_at_the_workspace_root(workspace: Workspace) -> None:
    result = run_bash(workspace, f'{sys.executable} -c "import os; print(os.getcwd())"')
    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == workspace.resolved_root


@posix_shell
def test_long_output_is_cut_and_says_what_to_do_about_it(workspace: Workspace) -> None:
    """Truncation that only says it happened leaves the caller to guess. Naming
    the three commands that narrow it is the difference between a dead end and a
    next step."""
    result = run_bash(workspace, f"{sys.executable} -c \"print('x' * 50000)\"")
    assert result.truncated
    assert len(result.stdout) < 50_000
    assert f"truncated at {MAX_OUTPUT_CHARS} characters" in result.stdout
    for narrowing in ("head", "tail", "grep"):
        assert narrowing in result.stdout


@posix_shell
def test_short_output_is_untouched(workspace: Workspace) -> None:
    result = run_bash(workspace, f"{sys.executable} -c \"print('small')\"")
    assert result.stdout.strip() == "small"
    assert not result.truncated


@posix_shell
def test_a_failing_command_returns_its_code_rather_than_raising(workspace: Workspace) -> None:
    """Exploration is mostly failing commands. Raising on each would make the
    tool unusable for the thing it exists to do."""
    result = run_bash(workspace, f'{sys.executable} -c "raise SystemExit(7)"')
    assert result.returncode == 7
    assert not result.timed_out


@posix_shell
def test_a_command_that_will_not_finish_is_stopped_and_says_so(workspace: Workspace) -> None:
    result = run_bash(workspace, f'{sys.executable} -c "import time; time.sleep(30)"', timeout=1.0)
    assert result.timed_out
    assert result.returncode == 124


@posix_shell
def test_output_from_a_timed_out_command_is_still_returned(workspace: Workspace) -> None:
    """What it printed before it hung is often the whole diagnosis."""
    result = run_bash(
        workspace,
        f'{sys.executable} -c "'
        "import sys,time; print('started'); sys.stdout.flush(); time.sleep(30)\"",
        timeout=1.0,
    )
    assert result.timed_out
    assert "started" in result.stdout

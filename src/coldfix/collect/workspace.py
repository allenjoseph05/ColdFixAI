"""The three tools that do not measure anything.

S-18.6. `bash` to explore and install, `read_file` to describe a finding once
measurement has pointed at one, and `write_file` for the single thing this
system adds to a subject: a driver script.

**These are not the security boundary and must not be read as one.** The
container is: a read-only root, an internal network with no route off the host,
memory and pid limits, and a wall clock. `bash -c "cd / && cat etc/passwd"` is
stopped by the container, not by anything here. What these tools provide is a
*shape* -- a surface with no way to express certain requests -- and the shape is
chosen so that the common accident is impossible rather than discouraged.

**No denylist.** There is no list of forbidden commands, no allowlist of safe
ones, and no catalogue of dangerous file extensions. Every such list is
incomplete on the day it is written, and the next repository is the one it does
not cover. Where something must not happen, the operation is absent instead:

| | |
|---|---|
| `bash` cannot choose a directory | there is no `cwd` parameter to pass one |
| `write_file` cannot modify a file | it refuses any path that already exists |
| nothing addresses a path outside the workspace | paths are **resolved**, symlinks and all |

**Containment is proved by resolution, not by inspecting the string.** A check
that rejects `..` and accepts everything else is defeated by a symlink, and a
check that compares string prefixes is defeated by `/work-other`. Both paths are
resolved to what the filesystem would actually open, and then one must be inside
the other.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

MAX_OUTPUT_CHARS = 4_000
"""Per stream. Enough to see what happened, small enough that forty turns of it
cannot fill a context window."""

MAX_LINES = 100
"""Source enters this system in windows, and only after a measurement has named
somewhere to look. A larger window would let a caller read a repository one
hundred lines at a time, which is the thing the design exists to avoid."""

DEFAULT_TIMEOUT = 300.0


class WorkspaceError(Exception):
    """Something about the request cannot be honoured."""


class NoPosixShellError(WorkspaceError):
    """This is `run_bash`, and Windows does not have one.

    `shell=True` on Windows runs `cmd.exe`, which is a different language with
    different quoting, different builtins and different exit conventions. A
    command written for one and run by the other does not fail loudly; it does
    something else. That is the same species of error as reporting a wrong CPU
    time, and it gets the same answer -- refuse, and say where to run it.
    """

    def __init__(self) -> None:
        super().__init__(
            "there is no POSIX shell on this platform: `shell=True` here runs cmd.exe, "
            "which is not bash and would quietly do something else with the same command. "
            "Run it inside the Linux container."
        )


class PathEscapesWorkspaceError(WorkspaceError):
    """The path resolves to somewhere outside the workspace."""

    def __init__(self, requested: str, resolved: Path, root: Path) -> None:
        super().__init__(
            f"{requested!r} resolves to {resolved}, which is outside {root}. "
            "Paths are given relative to the workspace and are resolved before they are "
            "used, so a symlink or a '..' that leaves the tree is refused wherever it appears."
        )
        self.requested = requested


class AbsolutePathError(WorkspaceError):
    """Paths are relative to the workspace, always."""

    def __init__(self, requested: str) -> None:
        super().__init__(
            f"{requested!r} is absolute. Every path here is relative to the workspace root; "
            "an absolute path names a location the workspace does not define."
        )
        self.requested = requested


class FileExistsHereError(WorkspaceError):
    """`write_file` creates; it never modifies.

    The one thing this system adds to a subject is a driver script. Everything
    else it does to source happens in a copy that is destroyed. A tool that could
    overwrite an existing file would make that promise unenforceable.
    """

    def __init__(self, requested: str) -> None:
        super().__init__(
            f"{requested!r} already exists. This tool creates new files and never modifies "
            "one -- the only thing added to a subject is a driver, and changing source is "
            "done in a copy that is thrown away."
        )
        self.requested = requested


class OutsideScratchError(WorkspaceError):
    """New files go in the scratch directory, not among the subject's own."""

    def __init__(self, requested: str, scratch: str) -> None:
        super().__init__(
            f"{requested!r} is not under {scratch!r}. New files are written there so that "
            "what this system added to a repository is one directory somebody can look at."
        )
        self.requested = requested


class NotTextError(WorkspaceError):
    """The file is not text, and a window into it would be meaningless."""

    def __init__(self, requested: str) -> None:
        super().__init__(
            f"{requested!r} is not valid UTF-8 text. Reading a window of it would produce "
            "characters that are not in the file."
        )
        self.requested = requested


class MissingFileError(WorkspaceError):
    def __init__(self, requested: str) -> None:
        super().__init__(f"{requested!r} does not exist in the workspace")
        self.requested = requested


@dataclass(frozen=True)
class Workspace:
    """A root, and one directory inside it that new files may be written to."""

    root: Path
    scratch_name: str = "coldfix"

    @property
    def resolved_root(self) -> Path:
        # The root is resolved too. On macOS `/tmp` is a symlink to `/private/tmp`,
        # so comparing an unresolved root against a resolved child says every path
        # escapes.
        return self.root.resolve()

    @property
    def scratch(self) -> Path:
        return self.resolved_root / self.scratch_name

    def locate(self, requested: str) -> Path:
        """Resolve a relative path and prove it stays inside."""
        if Path(requested).is_absolute():
            raise AbsolutePathError(requested)
        resolved = (self.resolved_root / requested).resolve()
        if resolved != self.resolved_root and self.resolved_root not in resolved.parents:
            raise PathEscapesWorkspaceError(requested, resolved, self.resolved_root)
        return resolved


class FileWindow(BaseModel, frozen=True):
    """Lines from one file, with enough context to cite them."""

    path: str
    first_line: int
    lines: tuple[str, ...]
    total_lines: int

    @property
    def last_line(self) -> int:
        return self.first_line + len(self.lines) - 1

    @property
    def more_below(self) -> bool:
        return self.last_line < self.total_lines


class CommandResult(BaseModel, frozen=True):
    """What a command printed and how it ended."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    truncated: bool
    timed_out: bool = False


def read_file(
    workspace: Workspace, requested: str, *, offset: int = 0, limit: int = MAX_LINES
) -> FileWindow:
    """A window of at most `MAX_LINES` lines, one-indexed by `offset`.

    `offset` is a line number rather than a byte position so that what comes back
    can be cited: a finding says `models.py:112`, and that is the number the
    caller counted here.
    """
    target = workspace.locate(requested)
    if not target.is_file():
        raise MissingFileError(requested)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as undecodable:
        raise NotTextError(requested) from undecodable

    all_lines = text.splitlines()
    start = max(offset - 1, 0) if offset else 0
    window = all_lines[start : start + min(limit, MAX_LINES)]
    return FileWindow(
        path=requested,
        first_line=start + 1,
        lines=tuple(window),
        total_lines=len(all_lines),
    )


def write_file(workspace: Workspace, requested: str, content: str) -> Path:
    """Create a new file under the scratch directory. Never modify one."""
    target = workspace.locate(requested)
    scratch = workspace.scratch
    if target != scratch and scratch not in target.parents:
        raise OutsideScratchError(requested, workspace.scratch_name)
    if target.exists():
        raise FileExistsHereError(requested)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def run_bash(
    workspace: Workspace, command: str, *, timeout: float = DEFAULT_TIMEOUT
) -> CommandResult:
    """Run a shell command at the workspace root.

    **There is no `cwd` parameter, on purpose.** A caller cannot ask for a
    different directory, so the ordinary accident -- a command that quietly ran
    somewhere else and measured the wrong tree -- cannot be expressed. It is not
    a security control: the command may still `cd`, and what stops that mattering
    is the container.
    """
    # `os.name`, not `sys.platform`: the question is whether this system has a
    # POSIX shell, not whether it is one particular operating system. It also
    # keeps the rest of the function reachable for a type checker running on
    # a developer machine, which is where this code is read.
    if os.name != "posix":
        raise NoPosixShellError

    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace.resolved_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout, stderr, code, expired = (
            completed.stdout,
            completed.stderr,
            completed.returncode,
            False,
        )
    except subprocess.TimeoutExpired as late:
        stdout = _decode(late.stdout)
        stderr = _decode(late.stderr)
        code, expired = 124, True

    clipped_out, cut_out = _truncate(stdout)
    clipped_err, cut_err = _truncate(stderr)
    return CommandResult(
        command=command,
        returncode=code,
        stdout=clipped_out,
        stderr=clipped_err,
        truncated=cut_out or cut_err,
        timed_out=expired,
    )


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")


def _truncate(text: str) -> tuple[str, bool]:
    """Cut long output, and say what to do about it rather than only that it happened."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    kept = text[:MAX_OUTPUT_CHARS]
    return (
        f"{kept}\n\n[truncated at {MAX_OUTPUT_CHARS} characters, {len(text)} produced. "
        "Re-run the command piped through head, tail or grep to narrow it.]",
        True,
    )

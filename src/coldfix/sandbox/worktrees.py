"""Check out a revision somewhere else, and be able to prove it is gone again.

Epic 2, S-2.2. A worktree is the second half of the separation ADR 004 requires:
an ablation run gets its own container *and* its own directory, so that the
deliberately broken code it produces is not in the tree any patch could be cut
from. This module owns the directory half. S-2.3 is what binds the two together
and makes the separation structural; nothing here knows about execution modes.

Three decisions carry most of the weight, and each exists because the obvious
alternative fails quietly.

**Every worktree is detached.** `git worktree add` given a branch checks that
branch out, and a branch checked out in one worktree cannot be checked out in
another — so branch-based creation fails depending on what else happens to be
running. Worse, a branch moves. An investigation that measured "the revision on
`main`" measured whatever `main` pointed at when each experiment ran, which is
not a revision at all. Detaching pins the measurement to a commit and makes it
impossible for anything this system does to advance a ref the user cares about.

Two independent things guarantee it, and either alone is sufficient: the
revision is resolved to a commit SHA before git is invoked, so a branch name
never reaches `worktree add`; and `--detach` is passed anyway. Sabotaging
either one changes nothing observable — that redundancy is deliberate, and it
is recorded because it also means no single test can prove either flag is
carrying the property. Removing both attaches the worktree to the branch, which
is the failure both tests actually assert against.

**A dirty main working tree refuses `create`, and does not refuse `destroy`.**
The danger is measuring a repository whose committed state is not the state the
user is looking at: uncommitted edits live in no commit, so a worktree at HEAD
does not contain them, and every finding would cite code that differs from the
user's copy. That argument applies to making a worktree. Applied to removing
one it inverts — a main tree that goes dirty mid-investigation would strand a
worktree full of ablated, deliberately broken code, which is the one outcome
ADR 004 says must never happen. The guard protects measurements; it must not
be allowed to protect broken code.

**Removal is verified, not assumed.** `git worktree remove --force` reports
success and leaves the directory behind when something still holds a file in
it, which on Windows is routine and — once S-2.3 bind-mounts a worktree into a
container — is a live possibility on every platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coldfix.bench.execute import ExecutionResult, ExecutionStartError, execute

# `git worktree add` writes out a full checkout, which on a repository the size
# of the S-0.3 subjects is seconds rather than milliseconds. Generous enough not
# to fail on a cold filesystem cache, bounded because every other call here
# returns immediately and a git that has stopped answering should say so.
_GIT_TIMEOUT_SECONDS = 300.0


class WorktreeError(Exception):
    """A worktree operation could not be performed."""


class NotARepositoryError(WorktreeError):
    """The path is not a git repository, or is not its main working tree.

    Linked worktrees are refused as a root because every check in this module
    would then be scoped to the wrong tree: `git status` would report on the
    linked checkout while the guard's whole purpose is to describe the main one.
    The operations would appear to work and would be answering a different
    question.
    """


class DirtyWorkingTreeError(WorktreeError):
    """The main working tree has changes that exist in no commit.

    A worktree is created at a commit. Anything uncommitted is therefore absent
    from it, so the investigation would measure code that differs from what the
    user is looking at and report findings citing lines they cannot reproduce.

    Ignored files are not changes — `git status --porcelain` does not list them,
    so a repository with the usual build output and local database is clean by
    this definition. A file that is untracked *and* not ignored is code no
    commit contains, and is reported here as its own category so that the
    message says which of the two problems this is.
    """

    def __init__(self, root: Path, modified: tuple[str, ...], untracked: tuple[str, ...]) -> None:
        self.root = root
        self.modified = modified
        self.untracked = untracked
        parts = []
        if modified:
            parts.append(f"{len(modified)} uncommitted change(s): {', '.join(modified[:5])}")
        if untracked:
            parts.append(f"{len(untracked)} untracked file(s): {', '.join(untracked[:5])}")
        super().__init__(
            f"the main working tree at {root} is not clean — {'; and '.join(parts)}. "
            "Commit or stash before creating a worktree, so that what is measured is "
            "what a commit contains."
        )


class UnknownRevisionError(WorktreeError):
    """The revision does not name a commit in this repository.

    Resolved before a directory is created, so a typo fails without leaving a
    half-made worktree and an administrative record behind to be pruned.
    """

    def __init__(self, revision: str, root: Path) -> None:
        self.revision = revision
        self.root = root
        super().__init__(f"revision {revision!r} does not name a commit in {root}")


class WorktreePathError(WorktreeError):
    """The path cannot hold a worktree."""


class WorktreeNotDestroyedError(WorktreeError):
    """A worktree survived the attempt to remove it.

    Loud for the same reason `ContainerNotDestroyedError` is: a worktree that
    outlives its run holds the ablated, deliberately broken source that ADR 004
    requires be structurally incapable of reaching a patch. A leftover directory
    is that guarantee failing.
    """

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"worktree at {path} was not removed: {detail}")


@dataclass(frozen=True)
class Worktree:
    """One checkout belonging to a repository.

    `revision` is the resolved commit SHA rather than whatever was asked for,
    because that is the fact an experiment log has to record: `HEAD` and
    `main` describe different commits on different days.
    """

    path: Path
    revision: str
    is_main: bool
    branch: str | None = None
    prunable: bool = False

    @property
    def detached(self) -> bool:
        return self.branch is None


@dataclass(frozen=True)
class Repository:
    """The main working tree of a git repository, and the worktrees it owns.

    Constructing one validates that `root` really is a main working tree, so
    every later call can state its preconditions in terms of this repository
    rather than re-deriving what it is looking at.
    """

    root: Path

    def __post_init__(self) -> None:
        resolved = self.root.resolve()
        if not resolved.is_dir():
            message = f"not a directory: {resolved}"
            raise NotARepositoryError(message)

        toplevel = self._git("rev-parse", "--show-toplevel", check=False)
        if toplevel.exit_code != 0:
            message = f"not a git repository: {resolved} ({toplevel.stderr.strip()})"
            raise NotARepositoryError(message)

        object.__setattr__(self, "root", resolved)

        # The first record `git worktree list` emits is always the main working
        # tree. Comparing against it is what refuses a linked worktree as a
        # root, and it costs one command that is needed anyway.
        main = self.worktrees()[0]
        if main.path != resolved:
            message = (
                f"{resolved} is a linked worktree of {main.path}, not a main working tree; "
                "every check here would be scoped to the wrong tree"
            )
            raise NotARepositoryError(message)

    # ----------------------------------------------------------------- reading

    def worktrees(self) -> tuple[Worktree, ...]:
        """Every worktree git knows about, main first.

        Deliberately not guarded by the clean-tree check. Listing changes
        nothing, and a caller trying to find out what needs cleaning up must not
        be refused because something needs cleaning up.
        """
        return _parse_worktree_list(self._git("worktree", "list", "--porcelain").stdout)

    def is_clean(self) -> bool:
        """Whether the main working tree holds anything that exists in no commit."""
        modified, untracked = self._status()
        return not modified and not untracked

    # ----------------------------------------------------------------- writing

    def create_worktree(self, path: Path, revision: str) -> Worktree:
        """Check `revision` out at `path` as a detached worktree.

        `revision` is anything `git rev-parse` accepts — a SHA, a tag, a branch
        name, `HEAD~3`. It is resolved to a commit first, and the worktree is
        detached at that commit, so naming a branch here pins the branch's
        *current* commit rather than following it.

        Raises:
            DirtyWorkingTreeError: the main working tree has uncommitted or
                untracked content, so a commit does not describe it.
            UnknownRevisionError: `revision` does not name a commit.
            WorktreePathError: the path already exists, or lies inside the main
                working tree.
            WorktreeError: git refused for any other reason.
        """
        self._require_clean()

        target = path.resolve()
        if target.exists():
            message = f"path already exists: {target}"
            raise WorktreePathError(message)

        # A worktree inside the main tree appears there as untracked content,
        # which makes the main tree dirty and refuses every subsequent create —
        # the module disabling itself by running once.
        if self.root == target or self.root in target.parents:
            message = (
                f"{target} is inside the main working tree at {self.root}; it would appear "
                "there as untracked content and make every later operation refuse"
            )
            raise WorktreePathError(message)

        commit = self._resolve(revision)

        result = self._git("worktree", "add", "--detach", str(target), commit, check=False)
        if result.exit_code != 0:
            message = f"git refused to create a worktree at {target}: {result.stderr.strip()}"
            raise WorktreeError(message)

        return Worktree(path=target, revision=commit, is_main=False)

    def destroy_worktree(self, path: Path) -> None:
        """Remove the worktree at `path`, discarding everything uncommitted in it.

        `--force` is not an escalation offered to the caller, it is the whole
        operation: a diagnostic worktree is *expected* to be full of
        deliberately broken code, and a removal that asked to keep it would
        never be the thing this system wants.

        Not guarded by the clean-tree check. A main tree that goes dirty
        mid-investigation must not be able to strand a worktree full of ablated
        source — that is the outcome ADR 004 exists to prevent, and a guard that
        caused it would be protecting the wrong thing.

        Raises:
            WorktreeNotDestroyedError: git refused, or the directory survived.
        """
        target = path.resolve()

        result = self._git("worktree", "remove", "--force", str(target), check=False)
        if result.exit_code != 0 and target.exists():
            raise WorktreeNotDestroyedError(target, result.stderr.strip())

        # Verified rather than assumed. `git worktree remove` can report success
        # having left files it could not unlink — a process still holding one is
        # routine on Windows, and becomes possible everywhere once a worktree is
        # bind-mounted into a container.
        if target.exists():
            surviving = sum(1 for _ in target.rglob("*"))
            detail = f"git reported success but the directory remains, holding {surviving} entries"
            raise WorktreeNotDestroyedError(target, detail)

    # ----------------------------------------------------------------- internals

    def _require_clean(self) -> None:
        modified, untracked = self._status()
        if modified or untracked:
            raise DirtyWorkingTreeError(self.root, modified, untracked)

    def _status(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Tracked changes and untracked files, as two separate lists.

        Split so the error can say which problem it found. `stash` fixes the
        first and does nothing about the second, so a message that conflated
        them would send the reader to a command that cannot help.
        """
        output = self._git("status", "--porcelain=v1").stdout
        modified: list[str] = []
        untracked: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            name = line[3:].strip()
            (untracked if line.startswith("??") else modified).append(name)
        return tuple(modified), tuple(untracked)

    def _resolve(self, revision: str) -> str:
        """The commit SHA `revision` names.

        `^{commit}` makes this refuse a tag or tree that is not a commit rather
        than returning an object a worktree cannot be created at.
        """
        wanted = f"{revision}^{{commit}}"
        result = self._git("rev-parse", "--verify", "--quiet", wanted, check=False)
        if result.exit_code != 0 or not result.stdout.strip():
            raise UnknownRevisionError(revision, self.root)
        return result.stdout.strip()

    def _git(self, *args: str, check: bool = True) -> ExecutionResult:
        """Run git against this repository.

        `-C` rather than a working directory, so the command is self-describing
        in the experiment log and cannot be affected by where the harness
        happens to be running.
        """
        try:
            result = execute(["git", "-C", str(self.root), *args], timeout=_GIT_TIMEOUT_SECONDS)
        except ExecutionStartError as error:
            message = f"git could not be started: {error.cause}"
            raise WorktreeError(message) from error
        if check and result.exit_code != 0:
            message = f"git {' '.join(args)} failed: {result.stderr.strip()}"
            raise WorktreeError(message)
        return result


def _parse_worktree_list(output: str) -> tuple[Worktree, ...]:
    """Read `git worktree list --porcelain` into records.

    The format is blank-line-separated stanzas of `key value` lines, with
    `detached`, `bare` and `locked` appearing as bare keys. Parsed rather than
    read from the human format because that one aligns columns and cannot be
    split reliably on a path containing a space.
    """
    worktrees: list[Worktree] = []
    fields: dict[str, str] = {}

    def flush() -> None:
        if not fields:
            return
        branch = fields.get("branch")
        worktrees.append(
            Worktree(
                path=Path(fields["worktree"]).resolve(),
                revision=fields.get("HEAD", ""),
                is_main=not worktrees,
                branch=branch.removeprefix("refs/heads/") if branch else None,
                prunable="prunable" in fields,
            )
        )
        fields.clear()

    for line in output.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        fields[key] = value
    flush()

    return tuple(worktrees)

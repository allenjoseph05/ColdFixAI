"""Two ways to run code, and only one of them can produce something shippable.

Epic 2, S-2.3. This is where ADR 004's requirement stops being a description and
becomes a fact about the program: *an ablation run cannot produce a patch*.
`CLAUDE.md` is specific that it must be structural, and `03-agents.md` §7 lists
mode separation among the layers that work because **none of them asks the model
to behave**.

The enforcement is not a check that rejects a diagnostic diff. It is that a
diagnostic session **has no operation that returns a diff at all**. There is no
argument to pass, no flag to set, and no method to call — `DiagnosticSession`
exposes `run`, `close` and two properties, and `run` returns an
`ExecutionResult`, which is measurements. `02-architecture.md` §6 states the
same thing as a table row: *Output — measurements only*.

Three separate things have to fail for a diagnostic change to ship, and they
fail independently:

**There is no method.** Not a rejected call — an absent one. A test asserts the
public surface of `DiagnosticSession` by name, so adding a diff accessor to it
fails a test rather than passing review.

**There is no git inside the container.** A linked worktree's `.git` is a file
containing a path into the main repository's `.git/worktrees/`, and that path is
outside the only directory bind-mounted into the sandbox. Git run inside the
container therefore cannot see a repository at all — not because it was removed,
but because the metadata it needs was never mounted. This was not designed in;
it falls out of S-2.1 mounting exactly one directory, and it is asserted here so
that mounting a second one later fails a test.

**There is no worktree afterwards.** Closing a diagnostic session destroys the
worktree, verified against the filesystem by S-2.2. Text describing changes to
files that no longer exist cannot be applied, and a candidate session cannot be
opened on the path because there is nothing there.

Mode is required and has no default, and it is supplied once, when the session
is opened, rather than on each call. That is deliberate and slightly stronger
than the acceptance criterion's wording: a mode passed per call is a mode two
calls on the same session can disagree about, and it makes the separation a
runtime comparison instead of a difference between two types.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self

from coldfix.bench.execute import DEFAULT_MAX_OUTPUT_CHARS, ExecutionResult, execute
from coldfix.sandbox.patching import DEFAULT_PATCH_POLICY, PatchPolicy
from coldfix.sandbox.patching import apply_patch as _apply_patch
from coldfix.sandbox.runner import DEFAULT_LIMITS, InternalNetwork, ResourceLimits, Sandbox
from coldfix.sandbox.worktrees import Repository, Worktree

_GIT_TIMEOUT_SECONDS = 300.0

MAXIMUM_SOURCE_BYTES = 1024 * 1024
"""How large a file may be and still be handed to an audit.

A megabyte of Python is generated, vendored or a data file wearing a `.py`
suffix, and none of the three is a place a patch's callers live. The bound is
on the reader rather than on the caller because the caller is an agent's
context window, and one file can fill it."""


class ExecutionMode(StrEnum):
    """Why a thing is being run, decided before it runs.

    Not a preference and not a hint. The value selects which of two types is
    constructed, and the two types differ in what they can do rather than in
    how they behave.
    """

    DIAGNOSTIC = "diagnostic"
    """Measurement. Correctness may be broken deliberately; output is numbers."""

    CANDIDATE = "candidate"
    """A proposed change. Correctness must be preserved; output includes a diff."""


class SessionError(Exception):
    """A session could not do what was asked of it."""


class SessionClosedError(SessionError):
    """The session's worktree has been destroyed, so there is nothing to run in.

    Raised rather than silently reopening. A session that recreated its worktree
    on demand would give a diagnostic run a second life after the point at which
    its results were supposed to have been collected and its evidence discarded.
    """

    def __init__(self, mode: ExecutionMode) -> None:
        self.mode = mode
        super().__init__(f"this {mode.value} session is closed; its worktree no longer exists")


class Session:
    """A checkout, and a container bound to it. Never constructed directly.

    `Workbench.open` is the only caller, which is what ties a session to a
    worktree it created rather than to one it was handed. A session pointed at
    an arbitrary directory would be a session whose isolation nobody checked.
    """

    mode: ClassVar[ExecutionMode]

    def __init__(
        self,
        repository: Repository,
        worktree: Worktree,
        sandbox: Sandbox,
        policy: PatchPolicy = DEFAULT_PATCH_POLICY,
    ) -> None:
        self._repository = repository
        self._worktree = worktree
        self._sandbox = sandbox
        self._policy = policy
        self._closed = False

    @property
    def worktree(self) -> Worktree:
        """The checkout this session runs against."""
        return self._worktree

    @property
    def closed(self) -> bool:
        return self._closed

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        """Run `command` in a fresh container against this session's worktree.

        Returns an `ExecutionResult` and nothing else. For a diagnostic session
        that is the entire output surface — there is no second method that
        returns anything about the state of the files.
        """
        if self._closed:
            raise SessionClosedError(self.mode)
        return self._sandbox.run(
            command, timeout=timeout, env=env, max_output_chars=max_output_chars
        )

    def close(self) -> None:
        """Destroy the worktree. Idempotent, and verified by S-2.2."""
        if self._closed:
            return
        self._closed = True
        self._repository.destroy_worktree(self._worktree.path)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class DiagnosticSession(Session):
    """Measurement only. Deliberately broken code is expected here.

    The class body is the point: there is nothing in it. Every operation that
    could carry a change out of this session is absent rather than guarded, so
    the failure mode "someone passed the wrong flag" does not exist.
    """

    mode: ClassVar[ExecutionMode] = ExecutionMode.DIAGNOSTIC


class CandidateSession(Session):
    """A proposed change, which may be read back as a diff.

    Correctness is supposed to be preserved here. Nothing in this class checks
    that — the falsification test, the protected-path filter and the Adversary
    are what check it, and they are later stories. What this class provides is
    the one sanctioned route by which a change becomes text, so that the route
    exists in exactly one place and diagnostic sessions demonstrably do not
    have it.
    """

    mode: ClassVar[ExecutionMode] = ExecutionMode.CANDIDATE

    def diff(self) -> str:
        """The unified diff of everything changed since the checked-out commit.

        Untracked files are registered with `--intent-to-add` first, so a change
        that adds a file appears as an addition rather than being invisible. That
        writes to this worktree's index, which is discarded with the worktree.

        This runs git on the *host*, against the worktree directory. It cannot be
        done from inside the container: the bind mount carries the working files
        and not the `.git/worktrees/` metadata they refer to, so there is no
        repository in there to diff against.
        """
        if self._closed:
            raise SessionClosedError(self.mode)
        _git(self._worktree.path, "add", "--intent-to-add", "--all")
        return _git(self._worktree.path, "diff", "HEAD").stdout

    def apply_patch(self, diff: str) -> frozenset[str]:
        """Apply `diff` to this worktree, or reject it and change nothing.

        The protected-path filter runs here, in the applier, because this is the
        only route by which a diff becomes a file. A model is never asked what
        it intends to touch and is never told what it may not — S-2.4's rule is
        that the rejection is server-side, and a check anywhere else would be a
        check something could be routed around.

        Returns the paths written, so a caller recording an attempt does not
        re-derive them.

        Raises:
            ProtectedPathError: the patch touches a file that decides whether
                the patch worked.
            UnsafePathError: a path is absolute or climbs out of the worktree.
            UnparsablePatchError: git sees a path the filter did not.
            PatchDidNotApplyError: the patch was allowed and does not fit.
        """
        if self._closed:
            raise SessionClosedError(self.mode)
        return _apply_patch(diff, worktree=self._worktree.path, policy=self._policy)

    def sources(
        self, *, suffix: str = ".py", max_bytes: int = MAXIMUM_SOURCE_BYTES
    ) -> dict[str, str]:
        """Every readable source file in this worktree, as it now stands.

        `03-agents.md` §6.2 lists `read_file(path)` among the Adversary's tools
        and nothing implemented it, so S-11.1's `Candidate` and S-11.5's
        `ScopeAudit` — both of which need source — had to be handed theirs by a
        caller with no way to obtain it. Epic 11's composition check named that
        gap; this closes it.

        **It is on this class and not on `Session`, and that is the whole design.**
        A `DiagnosticSession` may run any command, which means it may *write* any
        file. Give it a way to read one back and a diagnostic run can emit a diff
        to disk and hand it out — ADR 004's *an ablation run cannot produce a
        patch* defeated through a reader rather than through a writer. S-2.3's
        rule is that the operation is **absent**, not guarded, so the absence is
        kept and only the session that is already allowed to return a diff gains
        the ability to return source.

        Read on the *host*, like `diff`, because the bind mount carries the
        working files and not the `.git` metadata — and because a file read
        through the container would be a file the container could have arranged.

        Paths are worktree-relative and `/`-separated, matching what
        `touched_paths` produces from a diff, so a caller can look one up by the
        name the patch used. Files above `max_bytes`, files that are not valid
        UTF-8, and anything under `.git` are skipped rather than raising: S-3.9's
        best-effort reading, and a caller that needs to know what it did not get
        compares against the diff's own paths.
        """
        if self._closed:
            raise SessionClosedError(self.mode)

        root = self._worktree.path
        found: dict[str, str] = {}
        for path in sorted(root.rglob(f"*{suffix}")):
            if not path.is_file() or ".git" in path.relative_to(root).parts:
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return found

    def original_of(self, paths: Iterable[str]) -> dict[str, str]:
        """What those paths held at the commit this worktree was created from.

        The *other* half of what a `Candidate` needs: `sources` returns the
        patched revision because the patch is in the working tree, and this
        returns the original because `git show HEAD:path` reads the commit, which
        no applied patch has touched.

        Reading the original from **this** worktree rather than from a second
        session is deliberate. The alternative is a diagnostic session at the same
        revision, which would need `sources` — and giving a diagnostic session a
        reader is exactly what the note on `sources` refuses.

        A path that does not exist at `HEAD` is absent from the result rather than
        raising. A patch that adds a file has no original for it, and that is a
        fact about the patch rather than a failure to read.
        """
        if self._closed:
            raise SessionClosedError(self.mode)

        found: dict[str, str] = {}
        for path in sorted(set(paths)):
            result = execute(
                ["git", "-C", str(self._worktree.path), "show", f"HEAD:{path}"],
                timeout=_GIT_TIMEOUT_SECONDS,
            )
            if result.exit_code == 0:
                found[path] = result.stdout
        return found


@dataclass(frozen=True)
class Workbench:
    """Opens sessions against one repository, with one image and one set of limits.

    Holding the repository, image and limits here is what leaves `open` with a
    revision and a mode — the two things that actually vary per session, and the
    two the caller must therefore state explicitly.
    """

    repository: Repository
    image: str
    worktree_root: Path
    limits: ResourceLimits = DEFAULT_LIMITS
    policy: PatchPolicy = DEFAULT_PATCH_POLICY
    network: InternalNetwork | None = None
    """Passed to every session's sandbox. `None` means loopback and nothing else.

    A subject that needs its database needs a network, and an `InternalNetwork`
    is the only kind that exists — see ADR 029. Held here rather than per
    session because every session in one investigation talks to the same
    environment, and a session on a different network would be measuring a
    different system.
    """

    def open(self, revision: str, *, mode: ExecutionMode) -> DiagnosticSession | CandidateSession:
        """Create a worktree at `revision` and bind a sandbox to it.

        `mode` is keyword-only and has no default. There is no value of it that
        means "whichever" and no way to reach a session without stating it: the
        argument selects which class is returned, so a caller who has not decided
        cannot proceed, and one who has decided gets a type that can only do the
        things that mode permits.

        Diagnostic and candidate sessions never share a worktree. Each gets its
        own directory named for its mode and a fresh identifier, and S-2.1 gives
        every individual run its own container on top of that.

        Raises:
            DirtyWorkingTreeError: the repository has uncommitted or untracked
                content, so a commit does not describe what would be measured.
            UnknownRevisionError: `revision` does not name a commit.
        """
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        path = self.worktree_root / f"{mode.value}-{uuid.uuid4()}"

        worktree = self.repository.create_worktree(path, revision)
        try:
            sandbox = Sandbox(
                image=self.image,
                workspace=worktree.path,
                limits=self.limits,
                network=self.network,
            )
        except Exception:
            # A worktree whose session was never constructed has no owner and
            # no close() to reach it. Cleaning up here is what stops a failed
            # open from leaving exactly the stranded checkout S-2.2 exists to
            # prevent.
            self.repository.destroy_worktree(worktree.path)
            raise

        session_type = DiagnosticSession if mode is ExecutionMode.DIAGNOSTIC else CandidateSession
        return session_type(self.repository, worktree, sandbox, self.policy)


def _git(cwd: Path, *args: str) -> ExecutionResult:
    result = execute(["git", "-C", str(cwd), *args], timeout=_GIT_TIMEOUT_SECONDS)
    if result.exit_code != 0:
        message = f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}"
        raise SessionError(message)
    return result

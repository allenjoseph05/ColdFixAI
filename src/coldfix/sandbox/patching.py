"""Decide what a patch is allowed to touch, before git touches anything.

Epic 2, S-2.4. The story note calls this the highest-leverage control in the
project: environmental hardening of this shape cut exploit rates by 87.7%
relative on the Reward Hacking Benchmark, more than any detector. `03-agents.md`
§7 lists it as *the oldest cheat there is* — a model that cannot make the code
faster can always make the test agree with the code.

The whole gate is one rule: a patch may not touch a file that decides whether
the patch worked. Tests, fixtures, `conftest`, the harness and any injected
instrumentation are all that same category.

**Rejection is server-side.** It happens in the function that applies patches,
which is the only route by which a diff becomes a file. Nothing here is
communicated to a model, asked of a model, or checked by a model.

Getting the *path list* right is the entire difficulty, and two things about
git make it harder than it looks.

**`git apply --numstat` reports a rename by its destination only.** A diff that
renames `tests/test_slow.py` to `src/harmless.py` is reported as touching
`src/harmless.py` and nothing else. The test file is deleted and the protected
path never appears. Asking git what a patch touches — the obvious
implementation — walks straight into that.

**`git apply --summary` knows about the rename but compacts the paths**, as
`rename tests/deep/{test_a.py => test_renamed.py} (100%)`. Recovering the two
paths from that means guessing about filenames containing braces or ` => `.

So the diff is parsed here, with hunk-aware state so that a line of *content*
beginning `--- a/x` is never mistaken for a header, and git's own view is used
as a **cross-check rather than as the source**: if git reports a destination
this module did not find, the patch is rejected as unparsable. The filter must
be a superset of git's view or it is not a filter, and the failure direction is
always rejection.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from coldfix.bench.execute import ExecutionResult, ExecutionStartError, execute

_GIT_TIMEOUT_SECONDS = 300.0

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_DEV_NULL = "/dev/null"

# `a/` or `b/` plus at least one character of path.
_AB_PREFIX_LENGTH = 2

# `git apply --numstat` emits added, deleted and path as one tab-separated record.
_NUMSTAT_FIELDS = 3

# Every one of these decides whether a change worked. A patch that edits one is
# not proposing a fix, it is editing the thing that would have caught it.
#
# `**` matches any number of path segments, so a pattern applies at any depth.
# The defaults are deliberately broad: a false rejection costs one rejected
# patch and says exactly why, and a false acceptance costs the only evidence
# that the system's output means anything.
DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    # Test suites, by directory and by the two dominant naming conventions.
    "**/tests/**",
    "**/test/**",
    "**/testing/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*_tests.py",
    # The file that defines what a test can see. Editing it changes every test
    # at once without appearing in any of them.
    "**/conftest.py",
    # Fixtures are the inputs a measurement is taken over. Changing them
    # changes the answer without changing the code under test.
    "**/fixtures/**",
    "**/fixture/**",
    "**/factories.py",
    # Runner configuration. Deselecting a test is as effective as deleting it,
    # and far quieter.
    "**/pytest.ini",
    "**/tox.ini",
    "**/noxfile.py",
    "**/.coveragerc",
    # Continuous integration decides what runs before a human sees it.
    ".github/**",
    "**/.gitlab-ci.yml",
    # This tool's own code, for the case that matters most in development: the
    # system pointed at the repository it lives in.
    "**/coldfix/**",
)


class PatchError(Exception):
    """A patch was not applied."""


class ProtectedPathError(PatchError):
    """The patch touches a file that decides whether the patch worked.

    Carries the pattern as well as the path, because "rejected" without "by
    which rule" is not actionable — the caller cannot tell an over-broad default
    from a genuine attempt to edit the test suite.
    """

    def __init__(self, path: str, pattern: str) -> None:
        self.path = path
        self.pattern = pattern
        super().__init__(
            f"patch rejected: {path!r} is protected by the rule {pattern!r}. "
            "A change may not edit the tests, fixtures or harness that decide whether it worked."
        )


class UnsafePathError(PatchError):
    """The patch names a path outside the worktree.

    An absolute path, or one that climbs out with `..`. Git refuses most of
    these itself; this refuses them first, so the rejection names the path the
    caller supplied rather than a location git resolved it to.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"patch rejected: {path!r} {reason}")


class UnparsablePatchError(PatchError):
    """The patch could not be read confidently, so it is refused.

    The fail-closed case, and it exists because this filter is only a filter if
    it sees everything git sees. Raised when git reports touching a path the
    parser did not find, and when a path is quoted in a form this module does
    not decode.
    """


class PatchDidNotApplyError(PatchError):
    """The patch passed the filter and git could not apply it.

    Distinct from a rejection: nothing was wrong with what the patch wanted to
    do, only with whether it fits the current tree.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"patch did not apply: {detail}")


@dataclass(frozen=True)
class PatchPolicy:
    """Which paths a patch may not touch.

    Configurable per project, with defaults that are safe rather than minimal.
    An adapter is the eventual source of the project-specific entries — it
    already owns hook points and framework-internal frames — but the defaults
    stand alone so that a project without an adapter is still protected.
    """

    protected: tuple[str, ...] = DEFAULT_PROTECTED_PATTERNS

    def matching_rule(self, path: str) -> str | None:
        """The first rule that protects `path`, or `None` if it is writable.

        Matching is case-insensitive on purpose. Windows and macOS resolve
        `Tests/test_a.py` and `tests/test_a.py` to the same file, so a
        case-sensitive rule would be bypassable there by changing one letter.
        On Linux the two are different files and this over-rejects, which is
        the direction a safety filter should err in.
        """
        candidate = path.lower()
        return next(
            (rule for rule in self.protected if _matches(candidate, rule.lower())),
            None,
        )


DEFAULT_PATCH_POLICY = PatchPolicy()


def touched_paths(diff: str) -> frozenset[str]:
    """Every path the diff refers to, on either side of a rename.

    Parsed with hunk state rather than by scanning for prefixes. Inside a hunk
    every line begins with a space, `+`, `-` or `\\`, so a removed line whose
    content starts with `-- a/x` renders as `--- a/x` and is indistinguishable
    from a file header to anything that only looks at line starts. Tracking the
    line counts declared by each `@@` header is what tells the two apart.

    Both sides of a rename or copy are reported. That is the case git's own
    `--numstat` does not cover, and it is the one an attack would use.
    """
    paths: set[str] = set()
    remaining_old = 0
    remaining_new = 0

    for line in diff.splitlines():
        if remaining_old > 0 or remaining_new > 0:
            if line.startswith("\\"):
                continue
            marker = line[:1]
            if marker in {"", " "}:
                remaining_old -= 1
                remaining_new -= 1
            elif marker == "-":
                remaining_old -= 1
            elif marker == "+":
                remaining_new -= 1
            else:
                # A malformed hunk. Abandoning the counts here rather than
                # guessing means the next header line is read as a header,
                # which over-collects. That is the safe direction.
                remaining_old = remaining_new = 0
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk:
            remaining_old = int(hunk.group(2) or 1)
            remaining_new = int(hunk.group(4) or 1)
            continue

        for prefix, strips_ab in (
            ("--- ", True),
            ("+++ ", True),
            ("rename from ", False),
            ("rename to ", False),
            ("copy from ", False),
            ("copy to ", False),
        ):
            if line.startswith(prefix):
                path = _clean(line[len(prefix) :], strips_ab=strips_ab)
                if path is not None:
                    paths.add(path)
                break

    return frozenset(paths)


def hunk_lines(diff: str) -> tuple[tuple[str, str], ...]:
    """Every line *inside* a hunk, as `(marker, content)` with the marker stripped.

    The sibling of `touched_paths`, and it exists for that function's reason
    read the other way round. There, a removed line whose content begins `-- a/x`
    renders as `--- a/x` and would be mistaken for a file header. Here the
    mistake runs the same way: `+++ b/shop/views.py` is a header and
    `+ cache.set(key, value)` is content, and anything that classifies a diff by
    scanning for a leading `+` reads the first as the second.

    So the hunk counts declared by each `@@` header are tracked exactly as they
    are there. Both functions must agree about where a hunk begins and ends, and
    a test asserts they do on the adversarial case.

    Returned rather than yielded because callers compare the added side against
    the removed side, which needs both in hand.
    """
    lines: list[tuple[str, str]] = []
    remaining_old = 0
    remaining_new = 0

    for line in diff.splitlines():
        if remaining_old > 0 or remaining_new > 0:
            if line.startswith("\\"):
                continue
            marker = line[:1]
            if marker in {"", " "}:
                remaining_old -= 1
                remaining_new -= 1
                lines.append((" ", line[1:]))
            elif marker == "-":
                remaining_old -= 1
                lines.append(("-", line[1:]))
            elif marker == "+":
                remaining_new -= 1
                lines.append(("+", line[1:]))
            else:
                remaining_old = remaining_new = 0
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk:
            remaining_old = int(hunk.group(2) or 1)
            remaining_new = int(hunk.group(4) or 1)

    return tuple(lines)


def hunk_ranges(diff: str) -> Mapping[str, frozenset[int]]:
    """Which **original-side** line numbers each file's hunks cover.

    The third sibling of `touched_paths` and `hunk_lines`, and it exists because
    S-10.5 has to answer *did this attempt change the same lines as the last
    one*. Original-side rather than new-side: two attempts that both rewrite
    lines 41-52 are working on the same code even though one added three lines
    and the other removed two, so the numbering they have in common is the one
    they started from.

    Files with a hunk header but no path — a malformed diff — are dropped rather
    than filed under a guess. Over-collecting here would make two unrelated
    attempts look like they touched the same place, which is the direction that
    rejects honest work.
    """
    ranges: dict[str, set[int]] = {}
    current: str | None = None

    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = _clean(line[4:], strips_ab=True)
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk is None or current is None:
            continue

        start = int(hunk.group(1))
        count = int(hunk.group(2) or 1)
        ranges.setdefault(current, set()).update(range(start, start + max(count, 1)))

    return {path: frozenset(lines) for path, lines in ranges.items()}


def audit(diff: str, *, policy: PatchPolicy, worktree: Path) -> frozenset[str]:
    """Every path the patch may write, having proved none of them is protected.

    Raises before anything is written. The order matters: a patch that is both
    unsafe and protected is reported as unsafe, because "this path escapes the
    worktree" is the more urgent of the two.

    Raises:
        UnparsablePatchError: git sees a path the parser did not, or a path is
            quoted in a form this module does not decode.
        UnsafePathError: a path is absolute or climbs out of the worktree.
        ProtectedPathError: a path is one the policy forbids.
    """
    declared = touched_paths(diff)

    for path in sorted(declared):
        _require_safe(path)

    # The cross-check. If git will touch something the parser did not see, the
    # parser is wrong and every conclusion drawn from it is worthless — so the
    # patch is refused rather than filtered against an incomplete list.
    for path in sorted(_git_destinations(diff, worktree)):
        if path not in declared:
            message = (
                f"git reports the patch touches {path!r}, which this filter did not find. "
                "Refusing rather than applying a patch it cannot fully account for."
            )
            raise UnparsablePatchError(message)

    for path in sorted(declared):
        rule = policy.matching_rule(path)
        if rule is not None:
            raise ProtectedPathError(path, rule)

    return declared


def apply_patch(
    diff: str, *, worktree: Path, policy: PatchPolicy = DEFAULT_PATCH_POLICY
) -> frozenset[str]:
    """Audit `diff` and, only if it passes, apply it to `worktree`.

    Returns the set of paths written, so a caller recording what an attempt did
    does not have to re-derive it.

    The diff is written to a temporary file outside the worktree, as bytes, and
    handed to git by path — `execute()` gives every command an empty stdin, and
    writing it as text would rewrite its line endings on Windows and corrupt
    every hunk.
    """
    written = audit(diff, policy=policy, worktree=worktree)

    with tempfile.TemporaryDirectory(prefix="coldfix-patch-") as scratch:
        patch_file = Path(scratch) / "candidate.diff"
        patch_file.write_bytes(diff.encode("utf-8"))

        check = _git(worktree, "apply", "--check", str(patch_file))
        if check.exit_code != 0:
            raise PatchDidNotApplyError(check.stderr.strip())

        applied = _git(worktree, "apply", str(patch_file))
        if applied.exit_code != 0:
            raise PatchDidNotApplyError(applied.stderr.strip())

    return written


def _matches(path: str, pattern: str) -> bool:
    """Glob match where `**` spans any number of path segments.

    Written out because `PurePath.full_match` arrived in 3.13 and this project
    targets 3.12, and because `fnmatch` on the whole string lets `*` cross a
    `/` — which would make `**/test_*.py` and `*test_*.py` mean the same thing
    and quietly widen every rule here.
    """
    return _match_segments(path.split("/"), pattern.split("/"))


def _match_segments(path: list[str], pattern: list[str]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        # `**` matches zero or more segments, so every split is tried.
        return any(_match_segments(path[i:], rest) for i in range(len(path) + 1))
    if not path:
        return False
    return fnmatchcase(path[0], head) and _match_segments(path[1:], rest)


def _clean(raw: str, *, strips_ab: bool) -> str | None:
    """One path from a header line, or `None` if the line names no file.

    `---` and `+++` carry an optional timestamp after a tab, and the `a/` and
    `b/` prefixes git adds. `rename from` and its relatives carry neither.
    """
    value = raw.split("\t", 1)[0].strip()
    if not value or value == _DEV_NULL:
        return None
    if value.startswith('"'):
        # Git C-quotes paths containing control or non-ASCII bytes. Decoding
        # that exactly means reproducing git's octal escaping rules, and a
        # filter that decodes them almost correctly is worse than one that
        # refuses: the failure would be a protected path that reads as a
        # different string here than it does on disk.
        message = (
            f"the patch names a quoted path ({value}), which this filter does not decode. "
            "Refusing rather than matching rules against a path it may have misread."
        )
        raise UnparsablePatchError(message)
    if strips_ab and len(value) > _AB_PREFIX_LENGTH and value[0] in "ab" and value[1] == "/":
        value = value[2:]
    # A patch produced on Windows can use backslashes, and a rule written with
    # forward slashes would not match. Normalising here means one separator
    # reaches the matcher; a literal backslash in a POSIX filename is
    # over-rejected, which is the safe direction.
    return value.replace("\\", "/")


def _require_safe(path: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", path):
        raise UnsafePathError(path, "is an absolute path")
    if ".." in pure.parts:
        raise UnsafePathError(path, "climbs out of the worktree with '..'")


def _git_destinations(diff: str, worktree: Path) -> frozenset[str]:
    """The paths git says the patch writes, read back for the cross-check.

    `--numstat -z` because the human format quotes and truncates names, and the
    NUL-separated form gives the bytes as they are. A diff git itself cannot
    parse yields nothing here, and is left for `--check` to report with a better
    message than this function could invent.
    """
    with tempfile.TemporaryDirectory(prefix="coldfix-audit-") as scratch:
        patch_file = Path(scratch) / "candidate.diff"
        patch_file.write_bytes(diff.encode("utf-8"))
        result = _git(worktree, "apply", "--numstat", "-z", str(patch_file))

    if result.exit_code != 0:
        return frozenset()

    fields = result.stdout.split("\0")
    # Records are `added \t deleted \t path`, with the path as its own field
    # only in `-z` mode for binary files; the common case keeps all three in one
    # tab-separated field.
    destinations = {
        parts[2].replace("\\", "/")
        for field in fields
        if (parts := field.split("\t")) and len(parts) == _NUMSTAT_FIELDS and parts[2]
    }
    return frozenset(destinations)


def _git(cwd: Path, *args: str) -> ExecutionResult:
    try:
        return execute(["git", "-C", str(cwd), *args], timeout=_GIT_TIMEOUT_SECONDS)
    except ExecutionStartError as error:
        message = f"git could not be started: {error.cause}"
        raise PatchError(message) from error

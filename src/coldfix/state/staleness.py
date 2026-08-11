"""What a shipped patch invalidates, and what it leaves standing.

Epic 6, S-6.4. Two flaws in `08-audit.md` share one question. **F14:** after
`ship` the graph returns to `screen`, but the code has changed and every prior
screening measurement is now stale — *re-screen only the workloads whose files
the patch touched; others keep their measurements.* **§6's interacting
findings:** two findings in one file, fixed in sequence, and the second patch is
written against pre-first-patch source — *invalidate any pending finding whose
`context` files the patch touched, and re-investigate rather than repair from a
stale chain.*

They are the same rule asked about two artifacts, so there is one mechanism here
and two consequences.

**Nothing records which files a workload touches.** S-4.1's `Workload` has an
entry point and a fixture and no notion of the source it executes; the honest
source for that is a measurement — S-3.9 captures stacks, and a stack frame names
a file. This module therefore takes coverage from its caller and does not invent
it. What it will not do is treat the absence of that record as evidence of
anything.

**Unrecorded is not untouched, and that is the load-bearing decision.** A
workload whose files nobody recorded cannot be *shown* unaffected, and flattening
that to "fresh" keeps a measurement the patch may have invalidated — which is
precisely how the Surgeon ends up repairing from a stale chain. So coverage is
`FRESH`, `STALE`, or `UNCOVERED`, and the last two both mean measure it again.
`UNCOVERED` stays distinct from `STALE` because they call for different fixes: a
stale workload needs re-screening, an uncovered one means nobody is recording
what the workloads touch, and reporting them as one number would hide a missing
instrument behind a routine invalidation. S-3.1 made the same split four ways for
the same reason.

**Paths are normalized, and an unnormalizable one is refused.** A patch's
modified files come from git — repo-relative, forward slashes. A workload's
touched files come from stack frames — absolute, and on Windows with backslashes.
Intersecting those two forms directly yields the empty set, every workload reads
as unaffected, and **nothing raises**: the failure is silent and lands on the
flattering side. `repo_path` refuses an absolute path it has no root to
relativize, rather than guessing.

**There is no disposition meaning *repair from a stale chain*.** `FindingAction`
has two members and only `FRESH` produces `REPAIR`, so §6's rule is a property of
the type rather than a branch somebody has to remember.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath, PurePosixPath


class StalenessError(Exception):
    """A patch's effect on prior work could not be decided."""


class UnusablePathError(StalenessError):
    """A path could not be put in the one form both sides compare in.

    Refused rather than normalized on a guess. Git reports repo-relative paths
    and a stack frame reports an absolute one, and comparing the two forms
    directly returns no overlap at all — so every workload reads as unaffected
    and every stale measurement is kept, with nothing to say so.
    """


def _absolute_anywhere(pure: PurePath) -> bool:
    """Whether this path is absolute under *either* flavour.

    `PurePath.is_absolute()` is platform-dependent in a way that matters here:
    on Windows `/home/allen/subject/src/api.py` has no drive, so it reports
    `False` and would be normalized to `home/allen/subject/src/api.py` — a
    repo-relative-looking path that matches nothing git ever reports. The same
    input is correctly refused on Linux. A guard whose answer depends on which
    machine ran it is the silent, flattering failure this module is about.
    """
    return pure.is_absolute() or bool(pure.drive) or pure.as_posix().startswith("/")


def repo_path(path: str | PurePath, *, repo_root: Path | None = None) -> str:
    """One path, in the repo-relative POSIX form both sides compare in.

    Raises:
        UnusablePathError: an absolute path with no root to relativize against,
            or one that escapes the root it was given.
    """
    pure = PurePath(path)
    if _absolute_anywhere(pure):
        if repo_root is None:
            message = (
                f"{path!r} is absolute and no repo root was given, so it cannot be compared with "
                "the repo-relative paths git reports. Guessing would make the comparison return "
                "no overlap and every workload read as unaffected by every patch"
            )
            raise UnusablePathError(message)
        try:
            pure = PurePath(Path(path).resolve().relative_to(Path(repo_root).resolve()))
        except ValueError as error:
            message = (
                f"{path!r} is outside the repository at {repo_root}. A file the patch cannot have "
                "modified is not evidence about a workload in it"
            )
            raise UnusablePathError(message) from error

    normalized = PurePosixPath(*pure.parts).as_posix()
    if not normalized or normalized == ".":
        message = f"{path!r} does not name a file"
        raise UnusablePathError(message)
    return normalized


def _normalized(paths: Iterable[str | PurePath], repo_root: Path | None) -> frozenset[str]:
    return frozenset(repo_path(path, repo_root=repo_root) for path in paths)


@dataclass(frozen=True)
class Patch:
    """A patch that shipped, and the files it modified."""

    finding_id: str
    modified: frozenset[str]

    @classmethod
    def of(
        cls,
        finding_id: str,
        modified: Iterable[str | PurePath],
        *,
        repo_root: Path | None = None,
    ) -> Patch:
        """Build one from whatever form the diff arrived in.

        Raises:
            StalenessError: a patch that modified nothing, which is not a patch.
            UnusablePathError: a path that cannot be made repo-relative.
        """
        files = _normalized(modified, repo_root)
        if not files:
            message = (
                f"the patch for {finding_id} modified no files. A patch that changed nothing "
                "invalidates nothing, and treating it as a ship would silently retire a finding"
            )
            raise StalenessError(message)
        return cls(finding_id=finding_id, modified=files)


@dataclass(frozen=True)
class Coverage:
    """Which source files a workload ran, or a finding's evidence chain rests on.

    `files is None` means nobody recorded it. That is a third state, not an empty
    set: an empty set is a claim that the subject touches nothing, and `None` is
    the absence of a claim.
    """

    subject: str
    files: frozenset[str] | None

    @classmethod
    def of(
        cls,
        subject: str,
        files: Iterable[str | PurePath],
        *,
        repo_root: Path | None = None,
    ) -> Coverage:
        return cls(subject=subject, files=_normalized(files, repo_root))

    @classmethod
    def unrecorded(cls, subject: str) -> Coverage:
        """For a subject whose files were never captured.

        Named rather than reached by passing `None`, so that a caller who has no
        coverage has to say so — and a reader of the call site can see that
        nothing was measured rather than that nothing was touched.
        """
        return cls(subject=subject, files=None)


class Freshness(StrEnum):
    """What a patch did to a subject's prior measurements."""

    FRESH = "untouched by the patch; its measurements stand"
    STALE = "runs a file the patch modified; measured against code that no longer exists"
    UNCOVERED = "what it touches was never recorded, so it cannot be shown untouched"

    @property
    def must_measure_again(self) -> bool:
        """Both non-fresh states, because neither can be trusted.

        `UNCOVERED` is kept distinct from `STALE` in the report and identical to
        it in the action: what separates them is which thing to go and fix.
        """
        return self is not Freshness.FRESH


class ScreeningAction(StrEnum):
    """F14's decision for one workload."""

    KEEP = "keep its measurements"
    SCREEN_AGAIN = "screen it again"


class FindingAction(StrEnum):
    """§6's decision for one pending finding.

    **There is deliberately no third member meaning repair anyway.** The whole of
    §6's fix is that a finding whose context moved is re-investigated rather than
    repaired from a chain that no longer describes the code, and a disposition
    that could express the other thing is one somebody can select.
    """

    REPAIR = "its evidence chain still describes the code; it may be repaired"
    REINVESTIGATE = "its evidence chain describes code that changed; derive it again"


@dataclass(frozen=True)
class Assessment:
    """What one patch did to one subject, and why."""

    subject: str
    freshness: Freshness
    overlap: frozenset[str]

    @property
    def screening_action(self) -> ScreeningAction:
        return (
            ScreeningAction.SCREEN_AGAIN
            if self.freshness.must_measure_again
            else ScreeningAction.KEEP
        )

    @property
    def finding_action(self) -> FindingAction:
        return (
            FindingAction.REINVESTIGATE
            if self.freshness.must_measure_again
            else FindingAction.REPAIR
        )

    def describe(self) -> str:
        if self.freshness is Freshness.STALE:
            touched = ", ".join(sorted(self.overlap))
            return f"{self.subject}: {self.freshness.value} ({touched})"
        return f"{self.subject}: {self.freshness.value}"


def assess(coverage: Coverage, patch: Patch) -> Assessment:
    """Decide what the patch did to one subject's prior work."""
    if coverage.files is None:
        return Assessment(coverage.subject, Freshness.UNCOVERED, frozenset())

    overlap = coverage.files & patch.modified
    freshness = Freshness.STALE if overlap else Freshness.FRESH
    return Assessment(coverage.subject, freshness, overlap)


@dataclass(frozen=True)
class StalenessReport:
    """What one shipped patch invalidated across everything the run holds."""

    patch: Patch
    assessments: tuple[Assessment, ...]

    def _named(self, freshness: Freshness) -> tuple[str, ...]:
        return tuple(a.subject for a in self.assessments if a.freshness is freshness)

    @property
    def invalidated(self) -> tuple[str, ...]:
        """AC 1 and AC 3: everything that must be measured or derived again."""
        return tuple(a.subject for a in self.assessments if a.freshness.must_measure_again)

    @property
    def retained(self) -> tuple[str, ...]:
        """AC 2: shown untouched, so their measurements stand."""
        return self._named(Freshness.FRESH)

    @property
    def uncovered(self) -> tuple[str, ...]:
        """Invalidated for want of a record rather than for a reason.

        Reported apart because the fix is different: these do not need
        re-screening so much as somebody recording what the workloads run.
        """
        return self._named(Freshness.UNCOVERED)

    def describe(self) -> str:
        modified = ", ".join(sorted(self.patch.modified))
        lines = [
            f"The patch for {self.patch.finding_id} modified {modified}.",
            f"  {len(self.retained)} unaffected, {len(self.invalidated)} invalidated "
            f"({len(self.uncovered)} of them for want of a coverage record).",
        ]
        lines.extend(f"  {assessment.describe()}" for assessment in self.assessments)
        if self.uncovered:
            lines.append(
                "  Uncovered subjects are invalidated because nothing recorded what they run — "
                "which is not the same as being affected, and is fixed by recording it rather "
                "than by measuring again."
            )
        return "\n".join(lines)


def after_ship(coverages: Sequence[Coverage], patch: Patch) -> StalenessReport:
    """The whole policy: what this patch leaves standing, and what it does not."""
    return StalenessReport(patch=patch, assessments=tuple(assess(c, patch) for c in coverages))


def screening_plan(coverages: Sequence[Coverage], patch: Patch) -> Mapping[str, ScreeningAction]:
    """F14, per workload."""
    return {a.subject: a.screening_action for a in after_ship(coverages, patch).assessments}


def finding_plan(coverages: Sequence[Coverage], patch: Patch) -> Mapping[str, FindingAction]:
    """§6, per pending finding.

    The patched finding is not among these: it shipped. What this decides is what
    happens to the findings that were still waiting when it did.
    """
    return {
        a.subject: a.finding_action
        for a in after_ship(coverages, patch).assessments
        if a.subject != patch.finding_id
    }

"""What may be diagnosed but not repaired, and what is not covered at all.

Epic 2, S-2.9. Three refusals, and they are not the same *kind* of refusal —
conflating them is the mistake this module is arranged to avoid.

**Concurrency and locking findings are diagnosed and never patched.** ADR 007:
output equivalence is the verification mechanism — run the tests, compare the
results, ship if they match — and it cannot detect an introduced race. A patch
that moves a lock can pass every test on every run and still be wrong under a
scheduling order the suite never produced. Reporting *"this endpoint serializes
on a lock"* is a useful finding; producing a patch for it is not.

**A cause inside a dependency is reported, never patched.** The package manager
will overwrite the change, the user does not own the code, and its test suite is
not in scope. The finding is the deliverable.

Both of those are **per finding**, and both are enforced the same way: the
repair path takes a `RepairableFinding`, and constructing one runs the
classification. There is no diagnose-only finding that reaches repair, because
there is no object for repair to accept.

**Unsupported project types are a different thing and get different treatment.**
`00-BRIEF.md` separates *refused on principle* — four categories where no
verifier makes a change safe — from *not covered*, which is a capability
boundary. Frontend, mobile, desktop GUI, game engines, embedded, mainframe and
kernel code are the second kind. They are **reported honestly, not refused**,
and the reason is concrete: a Django application with a React frontend is a
perfectly good subject for its backend. Refusing the repository would decline
work this system can do, so the report is per area and says which parts are out
of scope while the rest proceeds.

**The false positives to avoid are specific and were designed against.** Half
the Django projects in existence have a `package.json` for asset building, so
`package.json` alone is not a frontend — a frontend framework in its
dependencies is. `\\block\\b` does not match *blocking*, *deadlock* or *block*,
which is why the pattern is written that way rather than as a substring search.
S-2.8 learned this the expensive way and the tests here carry the same controls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

# Directories whose contents belong to somebody else. A cause localized here is
# reported and never patched, which is a different rule from S-2.8's scanning:
# that one deliberately *does* look inside vendored trees, because a vendored
# RTOS is still a real-time system. Unpatchable is not the same as invisible.
THIRD_PARTY_DIRECTORIES = frozenset(
    {
        "site-packages",
        "dist-packages",
        "node_modules",
        "vendor",
        "third_party",
        "thirdparty",
        ".venv",
        "venv",
        ".tox",
        "eggs",
        ".eggs",
    }
)

# Frontend frameworks, as opposed to a build toolchain. Tailwind, esbuild and
# webpack in a Django project's package.json mean somebody compiles CSS, not
# that the repository is a frontend.
_FRONTEND_PACKAGES = frozenset(
    {"react", "react-dom", "vue", "@angular/core", "svelte", "preact", "solid-js", "ember-source"}
)


def _pattern(source: str) -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE)


# Written against the words that must *not* match. `\block\b` leaves *blocking*,
# *deadlock* and *block* alone; `deadlock` is listed separately because it is
# unambiguous on its own.
_CONCURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("lock", _pattern(r"\block(s|ing|ed)?\b")),
    ("deadlock", _pattern(r"\bdead-?lock")),
    ("mutex", _pattern(r"\bmutex\b")),
    ("semaphore", _pattern(r"\bsemaphore\b")),
    ("race condition", _pattern(r"\b(race condition|data race)\b")),
    ("critical section", _pattern(r"\bcritical section\b")),
    ("contention", _pattern(r"\bcontention\b|\bcontended\b")),
    ("synchronization", _pattern(r"\bsynchroniz(e|ed|ation)\b|\bsynchronised\b")),
    ("the GIL", _pattern(r"\bGIL\b|\bglobal interpreter lock\b")),
    ("thread safety", _pattern(r"\bthread[- ]safe(ty)?\b")),
    ("a lock primitive", _pattern(r"\b(threading|asyncio|multiprocessing)\.(R?Lock|Semaphore)\b")),
    ("SELECT FOR UPDATE", _pattern(r"\bselect_for_update\b|\bSELECT\b.{0,40}\bFOR UPDATE\b")),
    ("an explicit table lock", _pattern(r"\bLOCK TABLE\b|\bpg_advisory")),
    ("transaction isolation", _pattern(r"\bserializable isolation\b|\bisolation level\b")),
)


class Disposition(StrEnum):
    """Whether a finding may be repaired, or only reported."""

    REPAIRABLE = "repairable"
    DIAGNOSE_ONLY = "diagnose_only"


class DiagnoseOnlyReason(StrEnum):
    """Why a finding cannot be repaired. Both are ADR 007 refusals."""

    CONCURRENCY = "concurrency or locking"
    THIRD_PARTY = "third-party dependency"


class UnsupportedArea(StrEnum):
    """Project kinds outside what this system covers.

    Not refusals. `00-BRIEF.md` §3 lists these as *not covered* — a capability
    boundary — rather than *refused on principle*, which is the separate list of
    four. A repository containing one of these is still analysable everywhere
    else, and saying so is the honest report the story asks for.
    """

    FRONTEND = "frontend or browser code"
    MOBILE = "mobile application code"
    DESKTOP_GUI = "desktop GUI code"
    GAME_ENGINE = "game engine project"
    EMBEDDED = "embedded firmware"
    MAINFRAME = "mainframe or COBOL batch"
    KERNEL = "kernel or operating-system code"


class ScopeError(Exception):
    """A finding was asked to do something its scope does not permit."""


class DiagnoseOnlyError(ScopeError):
    """A diagnose-only finding was offered to the repair path.

    The message carries the reason and the evidence, because this refusal will
    be read by somebody who wants the fix and needs to understand why they are
    getting a report instead.
    """

    def __init__(self, verdict: ScopeVerdict) -> None:
        self.verdict = verdict
        super().__init__(verdict.explanation())


@dataclass(frozen=True)
class ScopeVerdict:
    """Whether a finding may be repaired, and the evidence either way."""

    disposition: Disposition
    reasons: tuple[DiagnoseOnlyReason, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def repairable(self) -> bool:
        return self.disposition is Disposition.REPAIRABLE

    def explanation(self) -> str:
        """Why this is a report rather than a patch, said once per reason."""
        if self.repairable:
            return "This finding is in scope for repair."

        paragraphs = []
        if DiagnoseOnlyReason.CONCURRENCY in self.reasons:
            paragraphs.append(
                "This finding is about concurrency or locking, so it is diagnosed and reported "
                "but never patched. The verification mechanism here is output equivalence — run "
                "the tests, compare the results, ship if they match — and that cannot detect an "
                "introduced race: a patch that moves a lock can pass every test on every run and "
                "still be wrong under a scheduling order the suite never produced. The diagnosis "
                "is still worth having; the patch is not something this system can make safe."
            )
        if DiagnoseOnlyReason.THIRD_PARTY in self.reasons:
            paragraphs.append(
                "The cause is inside a third-party dependency, so it is reported and never "
                "patched. It is code you do not own, your package manager will overwrite any "
                "change to it on the next install, and its test suite is not in scope for this "
                "system to run. The finding is the deliverable."
            )

        evidence = "\n".join(f"  - {item}" for item in self.evidence)
        return "\n\n".join(paragraphs) + f"\n\nWhat was matched:\n{evidence}"


def classify(mechanism: str, site: str | Path, *, repository: Path) -> ScopeVerdict:
    """Decide whether a finding may be repaired, from its mechanism and its site.

    Takes the two facts the decision needs rather than a finding object, because
    the evidence-chain schema belongs to the Diagnostician (E8) and this check
    has to exist before it does. When that schema arrives it supplies these two
    fields; nothing here needs to change.

    Erring toward `DIAGNOSE_ONLY` is deliberate. Marking a repairable finding
    diagnose-only costs a fix that could have been offered; marking a
    concurrency finding repairable risks shipping a race that no check in this
    system can detect.
    """
    reasons: list[DiagnoseOnlyReason] = []
    evidence: list[str] = []

    for name, pattern in _CONCURRENCY_PATTERNS:
        match = pattern.search(mechanism)
        if match:
            reasons.append(DiagnoseOnlyReason.CONCURRENCY)
            evidence.append(f"the mechanism mentions {name} ({match.group(0)!r})")
            break

    third_party = third_party_reason(site, repository)
    if third_party is not None:
        reasons.append(DiagnoseOnlyReason.THIRD_PARTY)
        evidence.append(third_party)

    if not reasons:
        return ScopeVerdict(Disposition.REPAIRABLE)
    return ScopeVerdict(Disposition.DIAGNOSE_ONLY, tuple(reasons), tuple(evidence))


def third_party_reason(site: str | Path, repository: Path) -> str | None:
    """Why this location counts as somebody else's code, or `None` if it does not.

    Two ways to qualify: sitting in a directory that holds installed or vendored
    packages, or sitting outside the repository altogether. The second matters
    because a stack frame can localize into an interpreter's standard library or
    an absolute path on the machine, and neither is the user's to change.

    **Paths are treated as POSIX regardless of the host.** A site comes from a
    stack frame taken inside a Linux container, so `/usr/lib/python3.12/...` is
    absolute whether or not this process is running on Windows —
    `Path("/usr/lib").is_absolute()` is `False` there, and relying on it would
    silently report every standard-library site as the user's own code during
    development on Windows and as third-party in CI.
    """
    text = str(site).replace("\\", "/")
    path = PurePosixPath(text)

    for part in path.parts:
        if part.lower() in THIRD_PARTY_DIRECTORIES:
            return f"the site {text!r} is inside {part!r}, which holds installed code"

    if _is_absolute(text) and not path.is_relative_to(PurePosixPath(repository.as_posix())):
        return f"the site {text!r} is outside the repository at {repository.as_posix()}"

    return None


def _is_absolute(site: str) -> bool:
    """Whether the path is rooted, in either POSIX or Windows form."""
    return site.startswith("/") or re.match(r"^[A-Za-z]:/", site) is not None


@dataclass(frozen=True)
class RepairableFinding:
    """A finding the repair path is allowed to act on. There is no other kind.

    Constructing one *is* the scope check, so a diagnose-only finding has no
    route to repair — not a rejected one, an absent one. Fifth use of this
    construction in the project, after `VerifiedDatabase`, the session types,
    `VerifiedReset` and `ScreenedRepository`.

    The repair path does not exist yet — E10 owns the Surgeon — so what this
    provides today is the guarantee that when it does, it has nothing else to
    accept.

    Raises:
        DiagnoseOnlyError: the finding is concurrency-related, or localized
            inside code the user does not own.
    """

    mechanism: str
    site: str
    repository: Path
    verdict: ScopeVerdict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        verdict = classify(self.mechanism, self.site, repository=self.repository)
        object.__setattr__(self, "verdict", verdict)
        if not verdict.repairable:
            raise DiagnoseOnlyError(verdict)


@dataclass(frozen=True)
class AreaFinding:
    """One unsupported area, and what gave it away."""

    area: UnsupportedArea
    evidence: str

    def __str__(self) -> str:
        return f"{self.area.value} ({self.evidence})"


@dataclass(frozen=True)
class ScopeReport:
    """Which parts of a repository are outside what this system covers.

    A report, not a refusal. The rest of the repository proceeds.
    """

    root: Path
    areas: tuple[AreaFinding, ...] = ()

    @property
    def fully_supported(self) -> bool:
        return not self.areas

    def explanation(self) -> str:
        if self.fully_supported:
            return f"Nothing in {self.root} is outside this system's scope."

        found = "\n".join(f"  - {item}" for item in self.areas)
        return (
            f"Parts of {self.root} are outside what this system covers, and it will not analyse "
            "them or propose changes to them. This is a capability boundary rather than a "
            "refusal: the rest of the repository is analysed normally, and a project whose "
            "backend is in scope is still a useful subject even when its frontend is not.\n\n"
            f"Out of scope here:\n{found}"
        )


def report_scope(root: Path) -> ScopeReport:
    """Detect unsupported project areas, honestly and without refusing anything.

    Detection is by manifest and by file extension, both of which are evidence
    rather than inference. A repository whose unsupported part is not declared
    anywhere is not detected, and the story says *where possible* for exactly
    that reason.
    """
    resolved = root.resolve()
    areas: list[AreaFinding] = []

    if (frontend := _frontend_evidence(resolved)) is not None:
        areas.append(AreaFinding(UnsupportedArea.FRONTEND, frontend))

    for area, evidence in _signature_evidence(resolved):
        areas.append(AreaFinding(area, evidence))

    return ScopeReport(root=resolved, areas=tuple(areas))


def _frontend_evidence(root: Path) -> str | None:
    """A frontend framework in `package.json`, not merely a `package.json`.

    Half the Django projects in existence build their CSS with npm. Treating the
    manifest itself as evidence would report a frontend in most of this
    system's own target population, which is the failure S-2.8's control fixture
    exists to prevent and the same one applies here.
    """
    for manifest in _find(root, "package.json"):
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict):
            continue

        declared: set[str] = set()
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            block = parsed.get(section)
            if isinstance(block, dict):
                declared |= {str(name).lower() for name in block}

        matched = sorted(declared & _FRONTEND_PACKAGES)
        if matched:
            relative = manifest.relative_to(root).as_posix()
            return f"{relative} declares {', '.join(matched)}"
    return None


_SIGNATURES: tuple[tuple[UnsupportedArea, str, str], ...] = (
    (UnsupportedArea.MOBILE, "AndroidManifest.xml", "an Android manifest"),
    (UnsupportedArea.MOBILE, "Podfile", "a CocoaPods Podfile"),
    (UnsupportedArea.MOBILE, "pubspec.yaml", "a Flutter pubspec"),
    (UnsupportedArea.MOBILE, "*.xcodeproj", "an Xcode project"),
    (UnsupportedArea.EMBEDDED, "platformio.ini", "a PlatformIO project"),
    (UnsupportedArea.EMBEDDED, "*.ino", "an Arduino sketch"),
    (UnsupportedArea.MAINFRAME, "*.cbl", "COBOL source"),
    (UnsupportedArea.MAINFRAME, "*.cob", "COBOL source"),
    (UnsupportedArea.MAINFRAME, "*.jcl", "JCL job control"),
    (UnsupportedArea.GAME_ENGINE, "*.uproject", "an Unreal project"),
    (UnsupportedArea.GAME_ENGINE, "project.godot", "a Godot project"),
    (UnsupportedArea.GAME_ENGINE, "ProjectSettings/ProjectVersion.txt", "a Unity project"),
    (UnsupportedArea.DESKTOP_GUI, "*.xaml", "WPF or MAUI markup"),
    (UnsupportedArea.KERNEL, "Kbuild", "a kernel build file"),
)


def _signature_evidence(root: Path) -> list[tuple[UnsupportedArea, str]]:
    found: list[tuple[UnsupportedArea, str]] = []
    seen: set[UnsupportedArea] = set()
    for area, glob, description in _SIGNATURES:
        if area in seen:
            continue
        for match in _find(root, glob):
            found.append((area, f"{description} at {match.relative_to(root).as_posix()}"))
            seen.add(area)
            break
    return found


def _find(root: Path, pattern: str) -> list[Path]:
    """Matches for `pattern`, skipping trees that belong to somebody else.

    Third-party directories are excluded here — unlike S-2.8's scan, which looks
    inside them deliberately. A React app in `node_modules` is a dependency of
    the project, not evidence that the project is a frontend.
    """
    return [
        path
        for path in sorted(root.rglob(pattern))
        if not any(part.lower() in THIRD_PARTY_DIRECTORIES or part == ".git" for part in path.parts)
    ]

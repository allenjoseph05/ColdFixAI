"""Handing a repository the toolchain of its own era, and recording which one.

Epic 7, S-7.12. The story's *Why* is the whole argument: **a repository last
touched in 2019 does not break because it is complex; it breaks because we hand
it a 2026 toolchain.** It declares `django>=2.0`, which resolved today yields
Django 6, which its code does not run on. The repository is not broken — it
worked on the day it was written.

**Nothing here calls a model.** ADR 010: *the anchor is derived mechanically. No
agent decides it and there is no new obstacle category — `git log -1` and a
manifest read are both deterministic.*

**Framed as time, it stops being an unbounded variety problem.** That is ADR
010's move and it is why this story is small: an abandoned repository becomes a
different value of an existing parameter rather than a new class of obstacle.

**S-0.3 could not have found this, and the backlog says so.** All three
repositories in the spike were committed to within three days of selection, which
is why the *Python version mismatch* and *dependency resolution failure* rows of
its recurrence matrix came back empty. Those two rows are addressed here before
they are ever populated, which is cheaper than discovering them on somebody's
repository.

**The anchor is a default, not a constraint.** ADR 010 is explicit that anchoring
can *introduce* failures — a dependency version contemporary with the repository
may carry a since-fixed incompatibility or a known vulnerability — so it must be
overridable per run, and **an override is recorded exactly as the anchor is**. An
artifact that recorded only the anchor would describe a resolution that did not
happen.

**Two declarations of an interpreter are not the same kind of claim.** A trove
classifier, a `tox` envlist and a CI matrix each *enumerate* versions the project
says it supports; `requires-python = ">=3.8"` is a **floor** and says nothing
about which newer versions work. Taking the floor as *the* version would pick
3.8 for a project whose CI tests 3.11, and taking a floor as a ceiling would be
worse — so the two are kept apart and the report says which it had.

**This covers the Python layer only, and that bound is real.** An old `psycopg2`
needing a `libpq` current Debian no longer ships is an operating-system problem
and `apt` has no `--exclude-newer`. ADR 010 puts the residue in S-17.2's honest
limitations rather than describing it as solved, and `Anchor.residue` says so in
words so the limit travels with the result.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from coldfix.bench.execute import ExecutionError, execute
from coldfix.screening.workload import EnvironmentAnchor

ANCHOR_TIMEOUT_SECONDS = 300.0
"""A resolution against a package index, not a local computation."""

# Where a project enumerates the interpreters it supports, and where it states a
# floor. The two are read differently — see `Basis`.
_CLASSIFIER = re.compile(r"Programming Language :: Python :: (\d+\.\d+)")
_TOX_ENV = re.compile(r"py(\d)(\d+)")
_CI_VERSION = re.compile(r"['\"]?(\d+\.\d+)['\"]?")
_REQUIRES_FLOOR = re.compile(r">=\s*(\d+\.\d+)")
_REQUIRES_CEILING = re.compile(r"<\s*(\d+\.\d+)")
_SETUP_REQUIRES = re.compile(r"""python_requires\s*=\s*["']([^"']+)["']""")


class AnchorError(Exception):
    """The anchor could not be derived, or a resolution against it failed."""


class Basis(StrEnum):
    """How an interpreter version was established, which is how much it is worth.

    Kept apart because they answer different questions. An enumeration is a
    positive claim — *we test on 3.11* — and a floor is a minimum, which says
    nothing about whether anything newer works. Reporting a floor as though it
    were an enumeration picks 3.8 for a project whose CI runs 3.11.
    """

    ENUMERATED = "the project enumerates it among the versions it supports"
    FLOOR = "the project's declared minimum; it names no newer version it supports"


@dataclass(frozen=True)
class Anchor:
    """The date an environment is resolved as of, and where it came from.

    An override carries no commit, which is what makes the two distinguishable
    without a flag that could disagree with the rest of the record.
    """

    on: date
    commit: str | None
    reason: str

    @property
    def overridden(self) -> bool:
        return self.commit is None

    @property
    def residue(self) -> str:
        """What anchoring does not fix, carried with the thing that does.

        ADR 010's stated bound. An exclusion that travels without its
        preconditions is the failure `CLAUDE.md` names, and *the environment is
        era-matched* is exactly the sentence somebody would quote.
        """
        return (
            "the Python layer only. An old psycopg2 needing a libpq current Debian no longer "
            "ships is an operating-system problem and apt has no --exclude-newer equivalent; "
            "that residue is a limitation to publish (S-17.2), not one this solves"
        )

    def describe(self) -> str:
        source = f"commit {self.commit[:12]}" if self.commit else "an override"
        return f"{self.on.isoformat()} (from {source}: {self.reason})"


@dataclass(frozen=True)
class Interpreter:
    """Which Python the repository says it runs on, and how it said so."""

    version: str
    basis: Basis
    evidence: str
    considered: tuple[str, ...] = ()
    """Every declaration found, so a reader can see what was weighed. A project
    whose classifiers stop at 3.6 and whose CI tests 3.11 has said two things,
    and the newer one wins — but the older one is why."""

    def describe(self) -> str:
        return f"Python {self.version} ({self.basis.value}; {self.evidence})"


@dataclass(frozen=True)
class Resolved:
    """A dependency set, and the inputs that produced it.

    AC 4's *resolved dependency set*. Carried with the anchor and the interpreter
    because a resolution is a function of all three, and a pinned list with no
    record of what constrained it cannot be reproduced or argued with.
    """

    anchor: Anchor
    python_version: str | None
    requirements: tuple[str, ...]
    pins: tuple[str, ...]

    def recorded(self) -> EnvironmentAnchor:
        """The artifact form. AC 4, and the join the composition check found missing.

        S-7.12 computed an anchor and S-4.1 gained a field to hold one, and until
        the epic was run end to end there was **no way to get from the first to
        the second** — the only code that builds a `Workload` never took one.
        """
        return EnvironmentAnchor(
            anchor=self.anchor.on,
            commit=self.anchor.commit,
            reason=self.anchor.reason,
            python_version=self.python_version,
            dependencies=self.pins,
        )

    def describe(self) -> str:
        lines = [f"Resolved {len(self.pins)} package(s) as of {self.anchor.describe()}"]
        if self.python_version:
            lines.append(f"  for Python {self.python_version}")
        lines.extend(f"  {pin}" for pin in self.pins[:12])
        if len(self.pins) > 12:  # noqa: PLR2004 - a display limit, not a threshold
            lines.append(f"  … and {len(self.pins) - 12} more")
        return "\n".join(lines)


def anchor_for(root: Path, *, timeout: float = ANCHOR_TIMEOUT_SECONDS) -> Anchor:
    """AC 1: the date of the repository's most recent commit.

    The committer date rather than the author date. A patch written in 2019 and
    applied in 2024 was *resolved against* 2024's index by whoever applied it,
    and the author date would anchor the environment to a day the code in this
    checkout never existed on.

    Raises:
        AnchorError: not a git repository, or one with no commits. Both are real
            states — a downloaded tarball is not a checkout — and neither has a
            date to derive, so neither is defaulted to today.
    """
    root = Path(root)
    try:
        result = execute(
            ["git", "-C", str(root), "log", "-1", "--format=%cI%n%H"],
            timeout=timeout,
        )
    except ExecutionError as error:
        raise AnchorError(str(error)) from error

    if result.exit_code != 0:
        said = (result.stderr or result.stdout).strip()[-300:]
        message = (
            f"no commit date could be read from {root}: {said}. A checkout without history has "
            "no era of its own, and defaulting to today would hand a 2019 repository a 2026 "
            "toolchain — which is the failure this exists to prevent"
        )
        raise AnchorError(message)

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < _COMMIT_LINES:
        message = f"git reported no commit for {root}"
        raise AnchorError(message)

    try:
        committed = datetime.fromisoformat(lines[0])
    except ValueError as error:
        message = f"git reported a commit date this cannot read: {lines[0]!r}"
        raise AnchorError(message) from error

    return Anchor(
        on=committed.date(),
        commit=lines[1],
        reason="the repository's most recent commit",
    )


_COMMIT_LINES = 2


def override(on: date, reason: str) -> Anchor:
    """ADR 010's escape hatch, and it is recorded rather than silent.

    Anchoring can introduce failures: a dependency version contemporary with the
    repository may carry a since-fixed incompatibility with a newer transitive
    package, or a known security defect. So the anchor is a default — and an
    override with no reason is refused, because *why this run resolved against a
    different date* is the whole value of recording it.

    Raises:
        AnchorError: an empty reason.
    """
    if not reason.strip():
        message = (
            "an override needs a reason. The anchor is recorded so a measurement can be "
            "reproduced and argued with, and an override with no reason records a different "
            "resolution and none of why"
        )
        raise AnchorError(message)
    return Anchor(on=on, commit=None, reason=reason.strip())


# ================================================================== the interpreter


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _newest(versions: Sequence[str]) -> str | None:
    """The highest version, compared numerically rather than as text.

    `"3.9" > "3.10"` is true for strings and false for Python, and a project whose
    CI matrix runs 3.9 through 3.12 would be handed 3.9 by a lexical maximum.
    """
    parsed = []
    for version in versions:
        parts = version.split(".")
        if len(parts) >= _VERSION_PARTS and all(part.isdigit() for part in parts[:2]):
            parsed.append(((int(parts[0]), int(parts[1])), version))
    if not parsed:
        return None
    return max(parsed)[1]


_VERSION_PARTS = 2


@dataclass(frozen=True)
class _Declaration:
    """One place a repository said something about its interpreter."""

    basis: Basis
    versions: tuple[str, ...]
    evidence: str
    ceiling: str | None = None


def _from_pyproject(root: Path) -> list[_Declaration]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        parsed = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError:
        parsed = {}
    project = parsed.get("project", {}) or {}

    found: list[_Declaration] = []
    classifiers = [str(entry) for entry in project.get("classifiers", []) or []]
    versions = [match.group(1) for entry in classifiers if (match := _CLASSIFIER.search(entry))]
    if versions:
        found.append(
            _Declaration(
                Basis.ENUMERATED,
                tuple(versions),
                f"pyproject.toml classifiers: {', '.join(versions)}",
            )
        )

    requires = str(project.get("requires-python", "") or "")
    if requires and (match := _REQUIRES_FLOOR.search(requires)) is not None:
        upper = _REQUIRES_CEILING.search(requires)
        found.append(
            _Declaration(
                Basis.FLOOR,
                (match.group(1),),
                f"pyproject.toml requires-python: {requires}",
                ceiling=upper.group(1) if upper else None,
            )
        )
    return found


def _from_setup(root: Path) -> list[_Declaration]:
    found: list[_Declaration] = []
    for name in ("setup.cfg", "setup.py"):
        path = root / name
        if not path.is_file():
            continue
        text = _read(path)
        versions = _CLASSIFIER.findall(text)
        if versions:
            found.append(
                _Declaration(
                    Basis.ENUMERATED,
                    tuple(versions),
                    f"{name} classifiers: {', '.join(versions)}",
                )
            )
        match = _SETUP_REQUIRES.search(text)
        if match is not None and (inner := _REQUIRES_FLOOR.search(match.group(1))) is not None:
            found.append(
                _Declaration(
                    Basis.FLOOR,
                    (inner.group(1),),
                    f"{name} python_requires: {match.group(1)}",
                )
            )
    return found


def _from_tox(root: Path) -> list[_Declaration]:
    path = root / "tox.ini"
    if not path.is_file():
        return []
    versions = [f"{major}.{minor}" for major, minor in _TOX_ENV.findall(_read(path))]
    if not versions:
        return []
    return [
        _Declaration(Basis.ENUMERATED, tuple(versions), f"tox.ini envlist: {', '.join(versions)}")
    ]


def _from_ci(root: Path) -> list[_Declaration]:
    found: list[_Declaration] = []
    for workflow in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        versions = _ci_versions(_read(workflow))
        if versions:
            found.append(
                _Declaration(
                    Basis.ENUMERATED,
                    tuple(versions),
                    f"{workflow.name}: {', '.join(versions)}",
                )
            )
    return found


def interpreter_for(root: Path) -> Interpreter | None:
    """AC 3: the newest interpreter the repository claims to support.

    Every place ADR 010 names is read — `python_requires`, trove classifiers,
    `tox.ini`, CI workflow matrices — and **an enumeration beats a floor**. A
    project whose classifiers stop at 3.6 while its CI tests 3.11 has said two
    things and the newer one is the claim; a project that says only `>=3.8` has
    named a minimum and nothing else, and the report says so rather than
    presenting 3.8 as a version it was tested on.

    Returns `None` where the repository declares nothing, which is a real state:
    a project with no interpreter declaration has not implicitly claimed the
    newest one.
    """
    root = Path(root)
    declarations = [
        *_from_pyproject(root),
        *_from_setup(root),
        *_from_tox(root),
        *_from_ci(root),
    ]
    considered = [entry.evidence for entry in declarations]
    enumerated = [
        version
        for entry in declarations
        if entry.basis is Basis.ENUMERATED
        for version in entry.versions
    ]
    floors = [entry for entry in declarations if entry.basis is Basis.FLOOR]
    floor = (floors[0].versions[0], floors[0].evidence) if floors else None
    ceiling = next((entry.ceiling for entry in declarations if entry.ceiling), None)

    newest = _newest(enumerated)
    if newest is not None:
        return Interpreter(
            version=newest,
            basis=Basis.ENUMERATED,
            evidence=next(
                (entry for entry in considered if newest in entry), "an enumerated declaration"
            ),
            considered=tuple(considered),
        )

    # A ceiling names a version the project says it does *not* support, so the
    # newest it claims is below one — but "below 3.10" does not name 3.9 as
    # tested, and this is still a floor-grade reading rather than an enumeration.
    if floor is not None:
        return Interpreter(
            version=floor[0],
            basis=Basis.FLOOR,
            evidence=floor[1] + (f" (declared below {ceiling})" if ceiling else ""),
            considered=tuple(considered),
        )
    return None


def _ci_versions(text: str) -> list[str]:
    """Interpreter versions from a CI workflow's matrix.

    Read line by line and only from lines naming a python version key, because a
    workflow is full of numbers — action versions, timeouts, ports — and a
    pattern loose enough to catch `python-version: [3.8, 3.9]` is loose enough to
    catch `actions/checkout@v4` if it is not anchored to the key.
    """
    found: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "python-version" not in lowered and "python_version" not in lowered:
            continue
        _, _, values = line.partition(":")
        found.extend(
            version for version in _CI_VERSION.findall(values) if version.startswith(("2.", "3."))
        )
    return found


# ================================================================== resolution


def resolve(
    requirements: Sequence[str],
    *,
    anchor: Anchor,
    python_version: str | None = None,
    timeout: float = ANCHOR_TIMEOUT_SECONDS,
) -> Resolved:
    """AC 2: resolve against the index as it stood on the anchor date.

    `uv pip compile --exclude-newer` limits candidates to packages published on
    or before the anchor, which is what turns `django>=2.0` in a 2019 repository
    into the Django it was written against instead of the one released last
    month. Nothing is installed — this reads the index and returns pins.

    Raises:
        AnchorError: no requirements, or a resolution the index cannot satisfy.
            The second is a real answer about the repository rather than a bug:
            a dependency set that did not resolve on its own anchor date did not
            resolve for its authors either.
    """
    wanted = [entry.strip() for entry in requirements if entry.strip()]
    if not wanted:
        message = "there are no requirements to resolve; an empty set is not an environment"
        raise AnchorError(message)

    # Written to a file rather than piped: S-1.1's `execute` deliberately takes no
    # stdin, and widening the lab bench's surface for one caller's convenience is
    # the kind of change this project asks to be justified rather than assumed.
    with tempfile.TemporaryDirectory(prefix="coldfix-anchor-") as workspace:
        listing = Path(workspace) / "requirements.in"
        listing.write_text("\n".join(wanted) + "\n", encoding="utf-8")

        command = [
            "uv",
            "pip",
            "compile",
            "--exclude-newer",
            anchor.on.isoformat(),
            "--no-header",
            "--quiet",
        ]
        if python_version:
            command += ["--python-version", python_version]
        command.append(str(listing))

        try:
            result = execute(command, timeout=timeout)
        except ExecutionError as error:
            raise AnchorError(str(error)) from error

    if result.exit_code != 0:
        said = (result.stderr or result.stdout).strip()[-500:]
        message = (
            f"nothing resolves for {wanted} as of {anchor.describe()}: {said}. This is a fact "
            "about the repository rather than a fault here — a dependency set that will not "
            "resolve on its own anchor date did not resolve for its authors either"
        )
        raise AnchorError(message)

    pins = tuple(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return Resolved(
        anchor=anchor,
        python_version=python_version,
        requirements=tuple(wanted),
        pins=pins,
    )

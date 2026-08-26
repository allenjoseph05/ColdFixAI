"""What a repository is built on, read from its manifests rather than guessed.

Epic 7, S-7.1. The first thing the Explorer needs, and the thing playbooks are
keyed on (S-13.1) — so it has to be stable, and it has to be honest about what it
could not determine.

**Nothing here calls a model.** Reading `pyproject.toml` and looking for
`manage.py` is a function, and `CLAUDE.md` is explicit that a model call must not
replace one.

**A declared version is not an installed version, and this reports the declared
one.** A manifest says `django>=5.0`, which is a *constraint*: what is actually
importable could be 5.0 or 5.2, and on a project with a lockfile it is neither of
those but whatever the lock pinned. Recording a constraint as "the version" would
put a number in the fingerprint that nothing measured — so the field is named for
what it is, and the installed version is left to S-7.2, which is the story that
stands the environment up and can ask it.

**Identified-and-unsupported is not the same as unknown**, and the difference is
what a user can act on. *This is Flask, which is not a supported framework yet*
sends somebody to the roadmap; *nothing here looks like a web application* sends
them to check they pointed at the right directory. Both are refusals; only one of
them is a mystery. `Unsupported` therefore carries what it *did* identify.

**Every facet carries the file that said so.** `CLAUDE.md`'s first
non-negotiable is that a conclusion drawn from reading code is not a finding
unless it says where it came from — a fingerprint that claims Postgres without
naming the settings file it read is not checkable, and this one keys playbooks.

**A facet nothing establishes is `None`, never a default.** A project with no
declared test runner is not a project that uses `unittest`; it is a project whose
test runner this cannot see, and S-7.2 has to go and find out. Defaulting would
put a guess where S-13.1 expects a key.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Where a Python project declares what it depends on. Ordered by how much the
# ecosystem trusts them, because a project with both gets read from both and the
# evidence should name the authoritative one first.
MANIFESTS: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "requirements/base.txt",
    "requirements/production.txt",
    "setup.cfg",
    "Pipfile",
)

# `>=5.0`, `==5.0.1`, `~=4.2`. A range with no determinable floor — `<6` alone,
# or a bare name — yields no version rather than a guessed one.
_FLOOR = re.compile(r"(?:>=|==|~=)\s*(\d+)(?:\.(\d+))?")

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)")


class Framework(StrEnum):
    """Web frameworks this system can name. Naming is not supporting."""

    DJANGO = "Django"
    FLASK = "Flask"
    FASTAPI = "FastAPI"

    @property
    def supported(self) -> bool:
        """Django only, for now. `CLAUDE.md`: *Django + Postgres is the first
        target framework* — and the adapter, the reset strategies and the query
        counter are all Django-specific."""
        return self is Framework.DJANGO


class Orm(StrEnum):
    DJANGO_ORM = "Django ORM"
    SQLALCHEMY = "SQLAlchemy"


class Database(StrEnum):
    POSTGRESQL = "PostgreSQL"
    SQLITE = "SQLite"
    MYSQL = "MySQL"


class TestRunner(StrEnum):
    # pytest collects any class whose name starts with `Test`, and warns that it
    # cannot because this one has a constructor. A dunder is excluded from an
    # enum's members, so this stays a class attribute rather than becoming a
    # third runner — and the name stays the domain's word for the thing.
    __test__ = False

    PYTEST = "pytest"
    DJANGO = "Django test runner"
    UNITTEST = "unittest"


@dataclass(frozen=True)
class Detected[T]:
    """One fact, and the file that establishes it.

    The evidence is not decoration: a fingerprint keys playbooks, and a key
    nobody can trace back to a file is one nobody can check when the playbook it
    selected turns out to be wrong.
    """

    value: T
    evidence: str

    def describe(self) -> str:
        return f"{self.value} ({self.evidence})"


@dataclass(frozen=True)
class Fingerprint:
    """What a supported repository is built on.

    Only ever constructed for a framework this system supports — an
    unsupported or unidentifiable project produces `Unsupported` instead, so
    there is no fingerprint holding a framework nothing can ground.
    """

    root: Path
    framework: Detected[Framework]
    declared_version: Detected[str] | None
    orm: Detected[Orm] | None
    database: Detected[Database] | None
    test_runner: Detected[TestRunner] | None

    @property
    def undetermined(self) -> tuple[str, ...]:
        """Which facets nothing established. Reported, never defaulted."""
        return tuple(
            name
            for name, facet in (
                ("declared_version", self.declared_version),
                ("orm", self.orm),
                ("database", self.database),
                ("test_runner", self.test_runner),
            )
            if facet is None
        )

    def playbook_key(self) -> str:
        """What S-13.1 files a playbook under.

        **Major version only.** A playbook learned against Django 5.0 applies to
        5.0.3 — keying on the full version would make every patch release a cold
        start, and S-13.5 measures the learning curve by how much a playbook
        saves on the tenth project of a kind.

        A project whose declared constraint has no determinable floor keys on the
        framework alone, and says so, rather than being filed under a version
        nobody established.
        """
        major = _major_of(self.declared_version.value) if self.declared_version else None
        return f"{self.framework.value}/{major}" if major else f"{self.framework.value}/unversioned"

    def describe(self) -> str:
        lines = [
            f"{self.framework.describe()} at {self.root}",
            f"  playbook key: {self.playbook_key()}",
        ]
        for name, facet in (
            ("version declared", self.declared_version),
            ("ORM", self.orm),
            ("database", self.database),
            ("test runner", self.test_runner),
        ):
            lines.append(f"  {name}: {facet.describe() if facet else 'not determined here'}")
        if self.declared_version is not None:
            lines.append(
                "  The version is what the manifest *declares*, which is a constraint rather "
                "than what is installed; S-7.2 stands the environment up and can ask it."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class Unsupported:
    """Why this repository cannot be ground, and what was seen instead.

    Carries the framework where one was identified, because *this is Flask,
    which is not supported yet* and *nothing here looks like a web application*
    send a reader to two different places.
    """

    root: Path
    identified: Detected[Framework] | None
    looked_in: tuple[str, ...]

    @property
    def reason(self) -> str:
        if self.identified is not None:
            return (
                f"{self.identified.value} is not a framework this system supports yet. The "
                "adapter, the reset strategies and the query counter are Django-specific "
                "(S-14.3 is the story that adds a second)."
            )
        return (
            "nothing here identifies a web framework this system knows. Either this is not the "
            "repository root, or its framework is one nothing here can name."
        )

    def describe(self) -> str:
        found = f"  identified: {self.identified.describe()}" if self.identified else ""
        looked = ", ".join(self.looked_in) or "no manifest was found"
        return "\n".join(filter(None, [f"Unsupported: {self.reason}", found, f"  read: {looked}"]))


Identification = Fingerprint | Unsupported
"""What fingerprinting concludes.

A union rather than one type with an empty framework, for S-4.5's reason: the two
carry different things and call for different next actions, and a `Fingerprint`
whose framework is `None` reads as a healthy result at every call site.
"""


def _major_of(constraint: str) -> str | None:
    match = _FLOOR.search(constraint)
    return match.group(1) if match else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _declared_requirements(root: Path) -> list[tuple[str, str]]:
    """Every declared dependency, as `(requirement, the file that declared it)`.

    Reads what is there and skips what is not; a project with only a
    `requirements.txt` is ordinary rather than an error.
    """
    found: list[tuple[str, str]] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            parsed = tomllib.loads(_read(pyproject))
        except tomllib.TOMLDecodeError:
            parsed = {}
        project = parsed.get("project", {})
        for requirement in project.get("dependencies", []) or []:
            found.append((str(requirement), "pyproject.toml"))
        for group, requirements in (project.get("optional-dependencies", {}) or {}).items():
            found.extend((str(r), f"pyproject.toml [{group}]") for r in requirements)
        # Poetry keeps them somewhere else entirely, and a Django project using
        # Poetry is not an exotic case.
        poetry = parsed.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
        found.extend(
            (f"{name}{spec if isinstance(spec, str) else ''}", "pyproject.toml [poetry]")
            for name, spec in poetry.items()
        )

    for name in ("requirements.txt", "requirements/base.txt", "requirements/production.txt"):
        path = root / name
        if path.is_file():
            found.extend(
                (line.strip(), name)
                for line in _read(path).splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "-"))
            )

    return found


def _requirement_named(
    requirements: Sequence[tuple[str, str]], names: Iterable[str]
) -> tuple[str, str] | None:
    wanted = {name.lower() for name in names}
    for requirement, source in requirements:
        match = _REQUIREMENT_NAME.match(requirement)
        if match and match.group(1).lower().replace("_", "-") in wanted:
            return requirement, source
    return None


def _identify_framework(
    root: Path, requirements: Sequence[tuple[str, str]]
) -> Detected[Framework] | None:
    """`manage.py` first, because it is the one signal a Django project cannot fake.

    A dependency list can name Django in a project that only imports it for a
    management command; `manage.py` at the root is what a Django *application*
    has.
    """
    if (root / "manage.py").is_file() and "django" in _read(root / "manage.py").lower():
        return Detected(Framework.DJANGO, "manage.py")

    for framework, names in (
        (Framework.DJANGO, ("django",)),
        (Framework.FASTAPI, ("fastapi",)),
        (Framework.FLASK, ("flask",)),
    ):
        found = _requirement_named(requirements, names)
        if found is not None:
            return Detected(framework, f"{found[1]}: {found[0]}")
    return None


def _declared_version(
    framework: Framework, requirements: Sequence[tuple[str, str]]
) -> Detected[str] | None:
    found = _requirement_named(requirements, (framework.value.lower(),))
    if found is None or _FLOOR.search(found[0]) is None:
        return None
    return Detected(found[0], found[1])


def _identify_orm(
    framework: Detected[Framework], requirements: Sequence[tuple[str, str]]
) -> Detected[Orm] | None:
    found = _requirement_named(requirements, ("sqlalchemy",))
    if found is not None:
        return Detected(Orm.SQLALCHEMY, f"{found[1]}: {found[0]}")
    if framework.value is Framework.DJANGO:
        return Detected(Orm.DJANGO_ORM, f"{framework.evidence} (Django ships its own ORM)")
    return None


def _settings_files(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("settings*.py")) if ".venv" not in path.parts][:8]


def _identify_database(
    root: Path, requirements: Sequence[tuple[str, str]]
) -> Detected[Database] | None:
    """The settings file first, because a driver in the manifest is not a choice.

    `psycopg` in the dependencies says Postgres is *possible*; `ENGINE:
    django.db.backends.postgresql` says it is what runs.
    """
    for path in _settings_files(root):
        text = _read(path)
        for database, needles in (
            (
                Database.POSTGRESQL,
                ("django.db.backends.postgresql", "postgresql://", "postgres://"),
            ),
            (Database.MYSQL, ("django.db.backends.mysql", "mysql://")),
            (Database.SQLITE, ("django.db.backends.sqlite3", "sqlite:///")),
        ):
            if any(needle in text for needle in needles):
                return Detected(database, str(path.relative_to(root)).replace("\\", "/"))

    for database, drivers in (
        (Database.POSTGRESQL, ("psycopg", "psycopg2", "psycopg2-binary")),
        (Database.MYSQL, ("mysqlclient", "pymysql")),
    ):
        found = _requirement_named(requirements, drivers)
        if found is not None:
            return Detected(database, f"{found[1]}: {found[0]} (a driver, not a configured engine)")
    return None


def declared_test_runner(root: Path, *, framework: Framework) -> Detected[TestRunner] | None:
    """Which runner this repository declares, without needing a full fingerprint.

    Split out at S-14.2, because an adapter already knows its framework — that is
    what an adapter *is* — and needs the runner for repositories whose manifests
    do not name the framework at all. A project holding a `pyproject.toml` with
    `[tool.pytest.ini_options]` and no dependency list is `Unsupported` to
    `fingerprint`, and an adapter asking through it would fall back to Django's
    runner on a project that had said pytest in writing.

    Not named `test_runner`: pytest collects on the `test_` prefix, and a name
    imported into a test module would be collected as a test. `TestRunner`
    carries `__test__ = False` for the same reason, one line above.
    """
    root = Path(root)
    return _identify_test_runner(root, framework, _declared_requirements(root))


def _identify_test_runner(
    root: Path, framework: Framework, requirements: Sequence[tuple[str, str]]
) -> Detected[TestRunner] | None:
    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        path = root / name
        if (path.is_file() and "[pytest]" in _read(path)) or (
            path.is_file() and "[tool:pytest]" in _read(path)
        ):
            return Detected(TestRunner.PYTEST, name)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and "[tool.pytest" in _read(pyproject):
        return Detected(TestRunner.PYTEST, "pyproject.toml [tool.pytest]")

    found = _requirement_named(requirements, ("pytest", "pytest-django"))
    if found is not None:
        return Detected(TestRunner.PYTEST, f"{found[1]}: {found[0]}")

    if framework is Framework.DJANGO and (root / "manage.py").is_file():
        return Detected(TestRunner.DJANGO, "manage.py (Django's own runner is always available)")
    return None


def fingerprint(root: Path) -> Identification:
    """Read what this repository is built on, or say honestly that it cannot.

    Reads manifests and configuration. It does not import the project, install
    it, or run it — those are S-7.2's, and this has to work before an
    environment exists.
    """
    root = Path(root)
    requirements = _declared_requirements(root)
    read = tuple(name for name in MANIFESTS if (root / name).is_file())

    identified = _identify_framework(root, requirements)
    if identified is None or not identified.value.supported:
        return Unsupported(root=root, identified=identified, looked_in=read)

    return Fingerprint(
        root=root,
        framework=identified,
        declared_version=_declared_version(identified.value, requirements),
        orm=_identify_orm(identified, requirements),
        database=_identify_database(root, requirements),
        test_runner=_identify_test_runner(root, identified.value, requirements),
    )


def playbook_keys(identifications: Mapping[str, Identification]) -> Mapping[str, str]:
    """The keys a set of projects file their playbooks under.

    Exists so S-13.5's learning curve — *the tenth Django project takes
    materially fewer Explorer steps than the first* — is measurable: ten projects
    that key differently share nothing, and the curve would never bend.
    """
    return {
        name: found.playbook_key()
        for name, found in identifications.items()
        if isinstance(found, Fingerprint)
    }

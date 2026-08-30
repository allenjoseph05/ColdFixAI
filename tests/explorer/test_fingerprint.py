"""S-7.1 — what a repository is built on, read rather than guessed.

Built against real files on disk, because the acceptance criteria are claims
about what manifests say and a fake filesystem would assert only what this file
already believes.
"""

from __future__ import annotations

from pathlib import Path

import coldfix.adapters  # noqa: F401 - registers grounding support; the registry is empty without it
from coldfix.explorer.fingerprint import (
    Database,
    Detected,
    Fingerprint,
    Framework,
    Orm,
    TestRunner,
    Unsupported,
    fingerprint,
    playbook_keys,
)

MANAGE_PY = (
    "#!/usr/bin/env python\n"
    "import django\n"
    "from django.core.management import execute_from_command_line\n"
)


def django_project(root: Path, *, version: str = "django>=5.0,<6", settings: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "requirements.txt").write_text(f"{version}\npsycopg[binary]>=3.2\n", encoding="utf-8")
    if settings:
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "settings.py").write_text(settings, encoding="utf-8")
    return root


# ================================================= AC 1: what it detects, and from where


def test_a_django_project_is_identified_from_manage_py(tmp_path: Path) -> None:
    """The one signal a Django application cannot fake.

    A dependency list can name Django in a project that only imports it for a
    management command; `manage.py` at the root is what an application has.
    """
    found = fingerprint(django_project(tmp_path / "subject"))

    assert isinstance(found, Fingerprint)
    assert found.framework.value is Framework.DJANGO
    assert found.framework.evidence == "manage.py"


def test_the_declared_version_is_named_as_declared(tmp_path: Path) -> None:
    """A manifest states a *constraint*, not a version. Recording `>=5.0` as
    "the version" would put a number in the fingerprint nothing measured."""
    found = fingerprint(django_project(tmp_path / "subject"))

    assert isinstance(found, Fingerprint)
    assert found.declared_version == Detected("django>=5.0,<6", "requirements.txt")
    assert "constraint rather than what is installed" in found.describe()


def test_the_orm_follows_from_the_framework(tmp_path: Path) -> None:
    found = fingerprint(django_project(tmp_path / "subject"))

    assert isinstance(found, Fingerprint)
    assert found.orm is not None
    assert found.orm.value is Orm.DJANGO_ORM


def test_a_configured_engine_beats_a_driver_in_the_manifest(tmp_path: Path) -> None:
    """`psycopg` in the dependencies says Postgres is *possible*; the settings
    file says it is what runs. The evidence has to name the stronger source."""
    root = django_project(
        tmp_path / "subject",
        settings="DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n",
    )

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.database is not None
    assert found.database.value is Database.POSTGRESQL
    assert found.database.evidence == "config/settings.py"


def test_a_driver_alone_is_recorded_as_a_driver(tmp_path: Path) -> None:
    """The control. Without a settings file the driver is the only evidence, and
    the fingerprint says so rather than claiming a configured engine."""
    found = fingerprint(django_project(tmp_path / "subject"))

    assert isinstance(found, Fingerprint)
    assert found.database is not None
    assert found.database.value is Database.POSTGRESQL
    assert "a driver, not a configured engine" in found.database.evidence


def test_sqlite_is_read_from_the_settings(tmp_path: Path) -> None:
    root = django_project(
        tmp_path / "subject",
        settings="DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3'}}\n",
    )

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.database is not None
    assert found.database.value is Database.SQLITE


def test_pytest_is_detected_from_its_own_configuration(tmp_path: Path) -> None:
    root = django_project(tmp_path / "subject")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.test_runner is not None
    assert found.test_runner.value is TestRunner.PYTEST


def test_django_s_own_runner_is_the_fallback_and_says_why(tmp_path: Path) -> None:
    found = fingerprint(django_project(tmp_path / "subject"))

    assert isinstance(found, Fingerprint)
    assert found.test_runner is not None
    assert found.test_runner.value is TestRunner.DJANGO
    assert "always available" in found.test_runner.evidence


def test_a_poetry_project_is_read_too(tmp_path: Path) -> None:
    """A Django project using Poetry declares dependencies somewhere else
    entirely, and is not an exotic case."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.12"\ndjango = ">=5.0"\n', encoding="utf-8"
    )

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.declared_version is not None
    assert "poetry" in found.declared_version.evidence


# ================================== every facet carries the file that said so


def test_every_detected_facet_names_its_source(tmp_path: Path) -> None:
    """A fingerprint keys playbooks, and a key nobody can trace to a file is one
    nobody can check when the playbook it selected turns out to be wrong."""
    found = fingerprint(
        django_project(
            tmp_path / "subject",
            settings="DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n",
        )
    )

    assert isinstance(found, Fingerprint)
    for facet in (found.framework, found.declared_version, found.orm, found.database):
        assert facet is not None
        assert facet.evidence


def test_a_facet_nothing_establishes_is_none_rather_than_a_default(tmp_path: Path) -> None:
    """A project with no declared version is not a project on version 0 — it is
    one whose version this cannot see, and S-7.2 has to go and find out."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "requirements.txt").write_text("django\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.declared_version is None
    assert "declared_version" in found.undetermined


def test_the_undetermined_facets_are_enumerable(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert "database" in found.undetermined
    assert "declared_version" in found.undetermined
    assert "not determined here" in found.describe()


# ============================ AC 3: unknown and unsupported are different answers


def test_an_identified_but_ungroundable_framework_is_named(tmp_path: Path) -> None:
    """*This is Flask, and nothing can ground it yet* sends somebody to the roadmap.

    Both are refusals; only the other one is a mystery.

    **S-14.6 changed what the sentence says and not what it is for.** It used to
    call Flask *not supported yet* and blame the adapter, the reset strategies
    and the query counter, pointing at S-14.3 as the story that would add a
    second adapter — which landed in August. It now names the thing that is
    actually absent, which is grounding support in the registry, and lists what
    is registered so the reader can compare.
    """
    root = tmp_path / "subject"
    root.mkdir()
    (root / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Unsupported)
    assert found.identified is not None
    assert found.identified.value is Framework.FLASK
    assert "nothing has taught this system to ground it" in found.reason
    assert "registered so far: Django" in found.reason


def test_a_repository_naming_no_framework_says_it_is_a_mystery(tmp_path: Path) -> None:
    """*Nothing here looks like a web application* sends them to check they
    pointed at the right directory, which is a different action."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "requirements.txt").write_text("numpy>=2.0\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Unsupported)
    assert found.identified is None
    assert "nothing here identifies a web framework" in found.reason


def test_an_empty_directory_is_unsupported_and_says_where_it_looked(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()

    found = fingerprint(root)

    assert isinstance(found, Unsupported)
    assert "no manifest was found" in found.describe()


def test_there_is_no_fingerprint_for_an_unsupported_framework(tmp_path: Path) -> None:
    """The two outcomes are exclusive by construction (S-4.5's rule): a
    `Fingerprint` whose framework is unsupported would read as a healthy result
    at every call site."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi>=0.115\n", encoding="utf-8")

    found = fingerprint(root)

    assert not isinstance(found, Fingerprint)
    assert isinstance(found, Unsupported)


def test_fingerprinting_this_repository_refuses_it_honestly() -> None:
    """ColdFix is not a Django application, and pointing this at its own root
    must say so rather than producing something.

    `realtime.py` had the mirror of this trap — a detector that refused this
    repository for holding RTOS patterns as literals — so the self-check is
    worth making explicit.
    """
    found = fingerprint(Path(__file__).resolve().parents[2])

    assert isinstance(found, Unsupported)
    assert found.identified is None


# ============================================ AC 2: the key playbooks are filed under


def test_the_playbook_key_uses_the_major_version_only(tmp_path: Path) -> None:
    """A playbook learned against Django 5.0 applies to 5.0.3. Keying on the full
    version would make every patch release a cold start, and S-13.5 measures the
    learning curve by how much a playbook saves on the tenth project."""
    found = fingerprint(django_project(tmp_path / "a", version="django==5.0.3"))

    assert isinstance(found, Fingerprint)
    assert found.playbook_key() == "Django/5"


def test_two_projects_on_the_same_major_share_a_key(tmp_path: Path) -> None:
    """The point of the key: ten projects that keyed differently would share
    nothing and the learning curve would never bend."""
    first = fingerprint(django_project(tmp_path / "a", version="django>=5.0,<6"))
    second = fingerprint(django_project(tmp_path / "b", version="django==5.2.1"))

    assert isinstance(first, Fingerprint)
    assert isinstance(second, Fingerprint)
    assert first.playbook_key() == second.playbook_key()


def test_different_majors_do_not_share_a_key(tmp_path: Path) -> None:
    """The control: without it the key would pass for one that ignored the
    version entirely, and a Django 4 playbook would be applied to Django 5."""
    four = fingerprint(django_project(tmp_path / "a", version="django>=4.2"))
    five = fingerprint(django_project(tmp_path / "b", version="django>=5.0"))

    assert isinstance(four, Fingerprint)
    assert isinstance(five, Fingerprint)
    assert four.playbook_key() != five.playbook_key()


def test_an_undeterminable_version_keys_as_unversioned(tmp_path: Path) -> None:
    """Filed under a version nobody established would be worse than saying so."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "requirements.txt").write_text("django<6\n", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.playbook_key() == "Django/unversioned"


def test_only_supported_projects_get_a_key(tmp_path: Path) -> None:
    root = tmp_path / "flask"
    root.mkdir()
    (root / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")

    keys = playbook_keys(
        {
            "django": fingerprint(django_project(tmp_path / "django")),
            "flask": fingerprint(root),
        }
    )

    assert keys == {"django": "Django/5"}


def test_a_malformed_pyproject_does_not_stop_the_read(tmp_path: Path) -> None:
    """An unparseable manifest is common in the wild, and refusing the whole
    repository over one would lose the evidence the other files carry."""
    root = django_project(tmp_path / "subject")
    (root / "pyproject.toml").write_text("this is not [ valid toml", encoding="utf-8")

    found = fingerprint(root)

    assert isinstance(found, Fingerprint)
    assert found.framework.value is Framework.DJANGO

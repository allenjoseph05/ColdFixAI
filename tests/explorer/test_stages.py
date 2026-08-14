"""S-7.11 — nine definitions of done, and who is allowed to write them.

A safety story. The predicates are measured against a real Django project at four
states — files only, installed but unconfigured, migrated but empty, and seeded —
because every one of the nine is a claim about a repository and a fake subject
would report whatever this file imagined.

The state that matters most is **migrated but empty**. `migrate` populates
`django_content_type` and `auth_permission` on its own, so *rows in at least two
tables* is satisfied by a repository holding no data at all — and a predicate that
called that seeded would send the Explorer to measure nothing.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

from coldfix.explorer.auth import (
    AuthProfile,
    Established,
    Requirement,
    Scheme,
)
from coldfix.explorer.auth import (
    Resolution as AuthResolution,
)
from coldfix.explorer.fingerprint import (
    Detected,
    Fingerprint,
    Framework,
    Unsupported,
    fingerprint,
)
from coldfix.explorer.stages import (
    FRAMEWORK_APPS,
    Grounding,
    Outcome,
    Progress,
    Stage,
    StageError,
    Verdict,
    claim,
    evaluate,
    predicates_for,
)

pytestmark = pytest.mark.slow
"""Every test here runs at least one `django.setup()` in a subprocess."""

MANAGE_PY = """\
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """\
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "shop",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

MODELS = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""

URLS = """\
from django.http import JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse({"books": [b.title for b in Book.objects.all()]})


urlpatterns = [path("books/", books)]
"""

PYPROJECT = """\
[project]
name = "shop"
version = "0"
dependencies = ["django>=5.0"]
"""

SEED_COMMAND = """\
from django.core.management.base import BaseCommand

from shop.models import Author, Book


class Command(BaseCommand):
    def handle(self, *args, **options):
        for index in range(12):
            author = Author.objects.create(name="author-%s" % index)
            Book.objects.create(title="book-%s" % index, author=author)
"""


def write_project(root: Path) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "management" / "commands").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
    (root / "shop" / "management" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "seed_demo.py").write_text(
        SEED_COMMAND, encoding="utf-8"
    )
    return root


def run_manage(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"manage.py {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}")


def grounding_for(root: Path, **overrides: object) -> Grounding:
    fields: dict[str, object] = {"root": root, "python": [sys.executable]}
    fields.update(overrides)
    return Grounding(**fields)  # type: ignore[arg-type]


def identified(root: Path) -> Fingerprint:
    found = fingerprint(root)
    assert isinstance(found, Fingerprint)
    return found


@pytest.fixture
def unmigrated(tmp_path: Path) -> Path:
    """Installed and configured, with no tables at all."""
    return write_project(tmp_path)


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    """Migrated and **empty** — the state the seeding predicate has to get right."""
    root = write_project(tmp_path)
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")
    return root


@pytest.fixture
def seeded(migrated: Path) -> Path:
    run_manage(migrated, "seed_demo")
    return migrated


def outcome_of(root: Path, stage: Stage, **overrides: object) -> Outcome:
    return evaluate(identified(root), grounding_for(root, **overrides)).outcome(stage)


# ================================================ AC 1: nine stages, ADR 009's nine


def test_the_nine_stages_are_adr_009s_nine_in_its_order() -> None:
    assert [stage.value for stage in Stage] == [
        "clone",
        "dependencies",
        "configure",
        "connect",
        "migrate",
        "auth",
        "seed",
        "endpoint",
        "work",
    ]


def test_every_stage_states_what_done_means() -> None:
    """A failure report has to carry the success condition it measured against, or
    it is a transcript rather than something to act on."""
    for stage in Stage:
        assert stage.definition
        assert stage.definition != stage.value


def test_every_stage_is_evaluated_in_one_report(migrated: Path) -> None:
    progress = evaluate(identified(migrated), grounding_for(migrated))

    assert [entry.stage for entry in progress.outcomes] == list(Stage)


# ============================================== the predicates, against a real subject


def test_clone_holds_on_a_checkout_with_an_entry_point(unmigrated: Path) -> None:
    assert outcome_of(unmigrated, Stage.CLONE).verdict is Verdict.HOLDS


def test_clone_fails_where_nothing_is_a_way_in(tmp_path: Path) -> None:
    write_project(tmp_path)
    (tmp_path / "config" / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
    (tmp_path / "shop" / "management" / "commands" / "seed_demo.py").unlink()

    assert outcome_of(tmp_path, Stage.CLONE).verdict is Verdict.FAILS


def test_dependencies_holds_when_the_framework_imports(unmigrated: Path) -> None:
    outcome = outcome_of(unmigrated, Stage.DEPENDENCIES)

    assert outcome.verdict is Verdict.HOLDS
    assert "version" in outcome.detail


def test_dependencies_fails_when_the_interpreter_cannot_import_it(unmigrated: Path) -> None:
    outcome = evaluate(
        identified(unmigrated),
        grounding_for(unmigrated, python=[str(unmigrated / "no-such-python")]),
    ).outcome(Stage.DEPENDENCIES)

    assert outcome.verdict is Verdict.FAILS


def test_configure_holds_when_the_frameworks_own_check_passes(unmigrated: Path) -> None:
    assert outcome_of(unmigrated, Stage.CONFIGURE).verdict is Verdict.HOLDS


def test_configure_fails_on_a_project_the_framework_rejects(tmp_path: Path) -> None:
    """The framework's own opinion decides, because it knows what a misconfigured
    Django looks like and this module does not."""
    root = write_project(tmp_path)
    (root / "config" / "settings.py").write_text(
        SETTINGS.replace('ROOT_URLCONF = "config.urls"', "ROOT_URLCONF = 12345"), encoding="utf-8"
    )

    assert outcome_of(root, Stage.CONFIGURE).verdict is Verdict.FAILS


def test_connect_holds_against_a_database_that_answers(unmigrated: Path) -> None:
    assert outcome_of(unmigrated, Stage.CONNECT).verdict is Verdict.HOLDS


def test_connect_fails_when_the_database_will_not_open(tmp_path: Path) -> None:
    """The negative half, which nothing else in this file reaches: SQLite opens
    anything it can create, so refusing a connection takes a path it cannot."""
    root = write_project(tmp_path)
    (root / "config" / "settings.py").write_text(
        SETTINGS.replace(
            '"NAME": os.path.join(BASE_DIR, "db.sqlite3"),',
            '"NAME": os.path.join(BASE_DIR, "no-such-directory", "db.sqlite3"),',
        ),
        encoding="utf-8",
    )

    outcome = outcome_of(root, Stage.CONNECT)

    assert outcome.verdict is Verdict.FAILS
    assert "connect" in outcome.detail


def test_migrate_is_unknown_when_the_database_never_answered(tmp_path: Path) -> None:
    """*Migrations are unapplied* and *nothing could be asked* are two answers,
    and this is the second one."""
    root = write_project(tmp_path)
    (root / "config" / "settings.py").write_text(
        SETTINGS.replace(
            '"NAME": os.path.join(BASE_DIR, "db.sqlite3"),',
            '"NAME": os.path.join(BASE_DIR, "no-such-directory", "db.sqlite3"),',
        ),
        encoding="utf-8",
    )

    assert outcome_of(root, Stage.MIGRATE).verdict is Verdict.UNKNOWN


def test_endpoint_fails_when_no_route_was_enumerated(tmp_path: Path) -> None:
    """The control for the endpoint predicate. A repository the framework answers
    for, with an empty route table, is exactly the case ADR 009's predicate is
    about — and the only one that separates *asked and got none* from *asked*."""
    root = write_project(tmp_path)
    (root / "config" / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")

    outcome = outcome_of(root, Stage.ENDPOINT)

    assert outcome.verdict is Verdict.FAILS
    assert "no candidate route" in outcome.detail


def test_auth_fails_when_a_route_needs_a_credential_and_none_was_made(
    migrated: Path,
) -> None:
    """The negative half of the auth predicate. `None` reports UNKNOWN — nobody
    asked — and this is *asked, and the answer was no*, which is a different
    stage state and a different thing to go and fix."""
    unresolved = AuthResolution(
        profile=AuthProfile(
            settings_module=Detected("config.settings", "manage.py"),
            declared=(),
            user_model=None,
            login_url=None,
            session_cookie_name="sessionid",
        ),
        requirement=Requirement(
            path="/private/",
            scheme=Scheme.JWT,
            established=Established.OBSERVED,
        ),
        credential=None,
    )

    outcome = outcome_of(migrated, Stage.AUTH, auth=unresolved)

    assert outcome.verdict is Verdict.FAILS
    assert "no credential was made" in outcome.detail


def test_auth_holds_when_the_route_needed_nothing(migrated: Path) -> None:
    """A route requiring no credential satisfies this stage. ADR 009's pipeline
    is an ordering of predicates to satisfy, and *nothing to authenticate
    against* is satisfied."""
    open_route = AuthResolution(
        profile=AuthProfile(
            settings_module=Detected("config.settings", "manage.py"),
            declared=(),
            user_model=None,
            login_url=None,
            session_cookie_name="sessionid",
        ),
        requirement=Requirement(
            path="/books/",
            scheme=Scheme.NONE,
            established=Established.OBSERVED,
        ),
        credential=None,
    )

    assert outcome_of(migrated, Stage.AUTH, auth=open_route).verdict is Verdict.HOLDS


def test_migrate_fails_before_migrations_are_applied(unmigrated: Path) -> None:
    outcome = outcome_of(unmigrated, Stage.MIGRATE)

    assert outcome.verdict is Verdict.FAILS
    assert "have not been applied" in outcome.detail


def test_migrate_holds_once_they_are(migrated: Path) -> None:
    assert outcome_of(migrated, Stage.MIGRATE).verdict is Verdict.HOLDS


def test_endpoint_holds_when_a_route_was_enumerated(migrated: Path) -> None:
    assert outcome_of(migrated, Stage.ENDPOINT).verdict is Verdict.HOLDS


# ======================= the seeding trap: migrate alone populates two tables


def test_a_migrated_but_empty_database_is_not_seeded(migrated: Path) -> None:
    """The finding this predicate exists around. `migrate` writes rows to
    `django_content_type` and `auth_permission` by itself, so *more than n rows in
    at least two tables* is satisfied by a repository holding no data at all."""
    outcome = outcome_of(migrated, Stage.SEED)

    assert outcome.verdict is Verdict.FAILS
    assert "framework's own tables" in outcome.detail


def test_the_framework_rows_are_counted_and_reported_rather_than_hidden(
    migrated: Path,
) -> None:
    """They are real rows and a reader comparing two runs should see them; what
    they are not is data.

    The count is matched as a number rather than as text: `"0 row(s)"` is a
    substring of `"30 row(s)"`, so the obvious negative assertion passes on
    exactly the output it was written to reject.
    """
    outcome = outcome_of(migrated, Stage.SEED)

    found = re.search(r"(\d+) row\(s\) exist in the framework's own tables", outcome.detail)
    assert found is not None, outcome.detail
    assert int(found.group(1)) > 0


def test_a_seeded_database_is_seeded(seeded: Path) -> None:
    outcome = outcome_of(seeded, Stage.SEED)

    assert outcome.verdict is Verdict.HOLDS
    assert "shop.Author" in outcome.detail


def test_one_populated_table_is_not_enough(seeded: Path) -> None:
    """ADR 009 says at least two, and the costs this system looks for live in
    relationships: one table full of rows with nothing pointing at it cannot
    exhibit a per-parent cost at all."""
    outcome = outcome_of(seeded, Stage.SEED, seed_tables=3)

    assert outcome.verdict is Verdict.FAILS


def test_the_threshold_is_stated_and_applied(seeded: Path) -> None:
    assert outcome_of(seeded, Stage.SEED, seed_threshold=11).verdict is Verdict.HOLDS
    assert outcome_of(seeded, Stage.SEED, seed_threshold=500).verdict is Verdict.FAILS


def test_the_framework_apps_are_named_rather_than_guessed_at() -> None:
    assert {"auth", "contenttypes"} <= FRAMEWORK_APPS


# ==================== AC 4: a stage already true is complete without action


def test_a_repository_that_ships_a_seeded_database_has_nothing_to_do_at_seed(
    seeded: Path,
) -> None:
    """ADR 009: the pipeline is an ordering of predicates to satisfy, not a script
    of steps to execute. A harness that seeded anyway would be adding rows to
    somebody's fixture and then measuring the result."""
    progress = evaluate(identified(seeded), grounding_for(seeded))

    assert progress.outcome(Stage.SEED).complete
    assert Stage.SEED in progress.completed
    assert progress.first_incomplete is not None
    assert progress.first_incomplete.stage is not Stage.SEED


# ======================================== three verdicts, because three next moves


def test_a_stage_whose_prerequisite_is_absent_reports_unknown_not_failure(
    unmigrated: Path,
) -> None:
    """*Nothing has been driven at two scales* is not *this endpoint does no
    work*, and flattening the two would report a run that never started as a
    repository that failed."""
    outcome = outcome_of(unmigrated, Stage.WORK)

    assert outcome.verdict is Verdict.UNKNOWN
    assert not outcome.complete


def test_auth_is_unknown_until_a_route_has_been_probed(migrated: Path) -> None:
    outcome = outcome_of(migrated, Stage.AUTH)

    assert outcome.verdict is Verdict.UNKNOWN


def test_unknown_never_counts_as_progress(unmigrated: Path) -> None:
    """A stage nobody could measure is not a stage that passed, and treating
    ignorance as progress is how a run reaches the final gate having skipped
    everything."""
    assert not Verdict.UNKNOWN.complete
    progress = evaluate(identified(unmigrated), grounding_for(unmigrated))

    assert not progress.complete


def test_configure_is_unknown_rather_than_failed_when_nothing_imports(
    unmigrated: Path,
) -> None:
    outcome = evaluate(
        identified(unmigrated),
        grounding_for(unmigrated, python=[str(unmigrated / "no-such-python")]),
    ).outcome(Stage.CONFIGURE)

    assert outcome.verdict is Verdict.UNKNOWN


# ===================================== AC 3: framework-scoped, via the fingerprint


def test_the_predicates_are_resolved_through_the_fingerprint(unmigrated: Path) -> None:
    table = predicates_for(identified(unmigrated))

    assert set(table) == set(Stage)


def test_a_repository_with_no_framework_has_no_stages(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(StageError, match="no stages"):
        predicates_for(fingerprint(tmp_path))


def test_a_framework_with_no_adapter_is_refused_rather_than_defaulted() -> None:
    """Every stage would report UNKNOWN forever, which reads as a run that never
    started rather than as a repository this system cannot ground."""
    flask = Fingerprint(
        root=Path(),
        framework=Detected(Framework.FLASK, "requirements.txt: flask"),
        declared_version=None,
        orm=None,
        database=None,
        test_runner=None,
    )

    with pytest.raises(StageError, match="no stage predicates are registered"):
        predicates_for(flask)


def test_an_unsupported_identification_carries_its_own_reason(tmp_path: Path) -> None:
    unsupported = Unsupported(root=tmp_path, identified=None, looked_in=())

    with pytest.raises(StageError, match="no stages"):
        predicates_for(unsupported)


# ============ AC 2 and 5: the agent cannot supply a predicate or claim past one


def test_nothing_takes_a_predicate_as_an_argument() -> None:
    """AC 2's structural half. The table is resolved from the fingerprint and the
    evidence comes from `Grounding`; there is no parameter anywhere in the public
    surface through which a predicate could be supplied.

    This test fails the moment somebody adds one for a demo, which is the whole
    point of asserting it by inspection rather than describing it in a docstring.
    """
    for function in (evaluate, claim, predicates_for):
        for name, parameter in inspect.signature(function).parameters.items():
            assert "predicate" not in name.lower()
            assert "Predicate" not in str(parameter.annotation)


def test_a_claim_about_a_stage_whose_predicate_is_false_is_refused(
    unmigrated: Path,
) -> None:
    """AC 5, exactly as written. The agent may say it migrated; the database is
    what decides."""
    with pytest.raises(StageError, match="was claimed complete"):
        claim(Stage.MIGRATE, identified(unmigrated), grounding_for(unmigrated))


def test_the_refusal_carries_what_done_means_and_what_was_measured(
    unmigrated: Path,
) -> None:
    """So the next attempt is a bounded search rather than a retry — which is
    ADR 009's whole argument for the decomposition."""
    with pytest.raises(StageError) as raised:
        claim(Stage.MIGRATE, identified(unmigrated), grounding_for(unmigrated))

    assert "done means: the migration tool reports zero unapplied migrations" in str(raised.value)
    assert "have not been applied" in str(raised.value)


def test_a_claim_about_a_stage_that_does_hold_is_answered_with_the_measurement(
    migrated: Path,
) -> None:
    """Claiming is not forbidden — an agent reporting what it thinks it did is the
    agent doing its job. What is forbidden is the claim settling anything."""
    outcome = claim(Stage.MIGRATE, identified(migrated), grounding_for(migrated))

    assert outcome.complete
    assert outcome.stage is Stage.MIGRATE


def test_a_claim_about_an_unevaluable_stage_is_also_refused(unmigrated: Path) -> None:
    """UNKNOWN is not a pass. An agent claiming the final stage before anything
    has been driven must not advance on the strength of nobody having looked."""
    with pytest.raises(StageError, match="could not be evaluated"):
        claim(Stage.WORK, identified(unmigrated), grounding_for(unmigrated))


# ================================================================ the report


def test_the_report_names_the_stage_that_stopped_the_run(unmigrated: Path) -> None:
    """What S-7.10 publishes, and the difference between a limitation someone can
    act on and a transcript someone has to read."""
    described = evaluate(identified(unmigrated), grounding_for(unmigrated)).describe()

    assert "stopped at: migrate" in described
    assert "done means:" in described
    assert "measured:" in described


def test_first_incomplete_is_the_first_that_does_not_hold_not_the_first_that_fails() -> None:
    """A stage reporting UNKNOWN is where the run has stopped just as much as one
    that failed — its prerequisites are missing, and that is the thing to fix."""
    progress = Progress(
        outcomes=(
            Outcome(Stage.CLONE, Verdict.HOLDS, "ok"),
            Outcome(Stage.DEPENDENCIES, Verdict.UNKNOWN, "not asked"),
            Outcome(Stage.CONFIGURE, Verdict.FAILS, "no"),
        )
    )

    assert progress.first_incomplete is not None
    assert progress.first_incomplete.stage is Stage.DEPENDENCIES


def test_a_complete_run_reports_no_stopping_point() -> None:
    progress = Progress(outcomes=tuple(Outcome(stage, Verdict.HOLDS, "ok") for stage in Stage))

    assert progress.complete
    assert progress.first_incomplete is None
    assert len(progress.completed) == len(Stage)

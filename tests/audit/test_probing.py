"""The script that turns an adversarial input into a comparable output.

S-17.13. S-10.2's attack existed and could not reach a subject: `Probe` is a
dataclass and nothing in `src/` built one. What is under test here is that the two
ends meet — an input goes in as fixture data, the workload runs, and a payload
comes back through `harness()`'s marker — because a probe that produces no output
makes both revisions agree about nothing, which the attack reads as the patch
surviving.

The end-to-end tests are `slow`: they stand up a real Django project, migrate it,
and run the script in a subprocess. The alternative is asserting the script's text,
which tests that this file and that file were written by the same person.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from coldfix.audit.equivalence import (
    MARKER,
    OBSERVED_EXIT,
    PROBE_ERROR_EXIT,
    EquivalenceError,
    Probe,
    harness,
)
from coldfix.audit.probing import probe_for

SETTINGS = """\
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
USE_TZ = True

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "shop"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

MODELS = """\
from django.db import models


class Book(models.Model):
    title = models.CharField(max_length=200)
"""

URLS = """\
from django.http import JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse({"books": [b.title for b in Book.objects.all()]})


urlpatterns = [path("books/", books)]
"""


@pytest.fixture(scope="module")
def subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real Django project with one model and one route, migrated."""
    root = tmp_path_factory.mktemp("probed")
    (root / "config").mkdir()
    (root / "shop" / "migrations").mkdir(parents=True)

    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")

    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "PYTHONPATH": ""}
    for arguments in (["makemigrations", "shop"], ["migrate"]):
        subprocess.run(
            [sys.executable, "-m", "django", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
    return root


def drive(subject: Path, probe: Probe, payload: object) -> subprocess.CompletedProcess[str]:
    """Run the probe exactly as the attack does: through `harness`, on the command
    line, never written into the tree."""
    return subprocess.run(
        [sys.executable, "-c", harness(probe.script, payload)],  # type: ignore[arg-type]
        cwd=subject,
        env={**os.environ, "PYTHONPATH": str(subject)},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def payload_of(result: subprocess.CompletedProcess[str]) -> object:
    line = next(row for row in result.stdout.splitlines() if row.startswith(MARKER))
    return json.loads(line[len(MARKER) :])


# ============================================ the two ends meet


@pytest.mark.slow
def test_an_input_is_seeded_and_the_workload_answers_it(subject: Path) -> None:
    """**The whole story.** Rows in, response out, through the real wrapper.

    Asserted on the body rather than only on the exit code, because a probe that
    seeded nothing and returned an empty list would exit cleanly and look
    identical to a workload that read what it was given.
    """
    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.settings"
    )

    result = drive(subject, probe, [{"title": "Dune"}, {"title": "Emma"}])

    assert result.returncode == OBSERVED_EXIT, result.stderr[-600:]
    assert payload_of(result) == {"status": 200, "body": {"books": ["Dune", "Emma"]}}


@pytest.mark.slow
def test_the_empty_class_is_reachable_because_the_script_deletes_first(subject: Path) -> None:
    """`EMPTY` is one of the seven the attack sweeps, and a probe that only ever
    added rows could never produce it — the previous test's rows would still be
    there, and *empty collection* would be measured against two books."""
    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.settings"
    )
    drive(subject, probe, [{"title": "left over"}])

    result = drive(subject, probe, [])

    assert payload_of(result) == {"status": 200, "body": {"books": []}}


@pytest.mark.slow
def test_unicode_survives_the_round_trip(subject: Path) -> None:
    """The one input class whose result cannot be trusted without ASCII on the
    wire — `harness` embeds and encodes `ensure_ascii=True`, and this is the test
    that the probe does not undo it."""
    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.settings"
    )

    result = drive(subject, probe, [{"title": "Ünicöde — ✂"}])

    assert payload_of(result) == {"status": 200, "body": {"books": ["Ünicöde — ✂"]}}


@pytest.mark.slow
def test_a_route_that_does_not_exist_reports_its_status_rather_than_nothing(
    subject: Path,
) -> None:
    """The status travels beside the body because they are different observations.

    A patch that turns a 200 into a 404 returns no useful body, and two absent
    bodies are two runs agreeing about nothing — which the attack would read as
    the patch surviving.
    """
    probe = probe_for(
        "shop.books.list", path="/nope/", model="shop.Book", settings="config.settings"
    )

    result = drive(subject, probe, [])

    assert result.returncode == OBSERVED_EXIT
    observed = payload_of(result)
    assert isinstance(observed, dict)
    assert observed["status"] == 404


@pytest.mark.slow
def test_a_probe_against_the_wrong_settings_fails_loudly(subject: Path) -> None:
    """Not silently, and not identically on both revisions.

    `settings` is supplied rather than detected for `grounder_for`'s reason: a
    probe run against a configuration that happens to import would compare two
    revisions of the wrong application, and both would agree.
    """
    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.not_settings"
    )

    result = drive(subject, probe, [])

    assert result.returncode == PROBE_ERROR_EXIT
    assert MARKER not in result.stdout


# ========================================================= what is refused, and why


@pytest.mark.parametrize(
    ("path", "model", "settings"),
    [
        pytest.param("", "shop.Book", "config.settings", id="no path"),
        pytest.param("/books/", "", "config.settings", id="no model"),
        pytest.param("/books/", "shop.Book", "", id="no settings"),
        pytest.param("   ", "shop.Book", "config.settings", id="blank path"),
    ],
)
def test_a_probe_missing_any_of_the_three_is_refused(path: str, model: str, settings: str) -> None:
    """Refused at construction, because the failure it prevents is invisible.

    A probe missing any of them fails the same way on both revisions and produces
    the same absence of output, which S-10.2 reads as the patch surviving its
    attack — an answer, arrived at by measuring nothing.
    """
    with pytest.raises(EquivalenceError, match="Without them both revisions fail the same way"):
        probe_for("shop.books.list", path=path, model=model, settings=settings)


def test_a_probe_that_binds_nothing_is_refused_by_the_type() -> None:
    """Not this module's rule — `Probe.__post_init__` already refuses an empty
    script, and the producer inherits it rather than restating it."""
    with pytest.raises(EquivalenceError, match="runs nothing"):
        Probe(workload="shop.books.list", script="   ")


def test_the_script_is_never_written_into_the_subject_tree(tmp_path: Path) -> None:
    """S-10.2's rule for S-2.4's reason: a patch touching a protected path is
    rejected, so a probe materialised as a file would be a protected path every
    later diff shows. Asserted as *nothing appeared*, since the producer taking no
    directory is what makes it true."""
    before = set(tmp_path.rglob("*"))

    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.settings"
    )

    assert isinstance(probe.script, str)
    assert set(tmp_path.rglob("*")) == before


def test_the_script_seeds_from_the_input_rather_than_requesting_with_it() -> None:
    """The design decision, asserted so it cannot drift back.

    All seven classes the attack sweeps — empty, null, duplicates, ties, unicode,
    boundary, unordered — are properties of the rows a workload reads. A probe
    that passed the payload as query parameters would be testing the router.
    """
    probe = probe_for(
        "shop.books.list", path="/books/", model="shop.Book", settings="config.settings"
    )

    assert "coldfix_input" in probe.script
    assert "objects.create(**row)" in probe.script
    assert "data=coldfix_input" not in probe.script

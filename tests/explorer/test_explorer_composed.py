"""Epic 7, composed: one unknown repository, all the way to an emitted workload.

The epic's goal is one sentence — *turn an unknown repository into a runnable,
scalable, resettable workload* — and twelve stories each proved a piece of it
against its own fixture. This file is the first thing that performs the whole
sentence, which is where the last three epics found their defects: a suite where
every file tests one import will not tell you the parts fit together.

The subject is a **git repository** with a real N+1, built once and driven
through fingerprint → anchor → standup → routes → auth → fixtures → verification
→ emission in the order a caller would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from coldfix.explorer.anchor import anchor_for, interpreter_for, resolve
from coldfix.explorer.auth import Reply, resolve_auth
from coldfix.explorer.compose import Plan, ground_workload
from coldfix.explorer.emission import read_document
from coldfix.explorer.entrypoints import Discovery, Kind, enumerate_entry_points
from coldfix.explorer.fingerprint import Fingerprint, fingerprint
from coldfix.explorer.fixtures import Mechanism, discover, factory_seeder, prefer
from coldfix.explorer.stages import Grounding, Stage, Verdict, evaluate
from coldfix.explorer.work import WorkVerificationError, verify_work
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset

pytestmark = pytest.mark.slow

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


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""

# `orders/` is mounted under an `include()` prefix on purpose. Its parsed fragment
# and its resolved path differ — `orders/` against `api/orders/` — which is the
# only shape that can tell a fragment from an address.
API_URLS = """\
from django.http import JsonResponse
from django.urls import path


def orders(request):
    return JsonResponse({"orders": []})


urlpatterns = [path("orders/", orders)]
"""

# A planted N+1: one query for the books, one per book for its author.
URLS = """\
from django.http import JsonResponse
from django.urls import include, path

from shop.models import Book


def books(request):
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


def health(request):
    return JsonResponse({"ok": True})


urlpatterns = [
    path("books/", books),
    path("health/", health),
    path("api/", include("shop.api")),
]
"""

FACTORIES = """\
import factory
from factory.django import DjangoModelFactory

from shop.models import Author, Book


class AuthorFactory(DjangoModelFactory):
    class Meta:
        model = Author

    name = factory.Sequence(lambda n: "author-%s" % n)


class BookFactory(DjangoModelFactory):
    class Meta:
        model = Book

    title = factory.Sequence(lambda n: "book-%s" % n)
    author = factory.SubFactory(AuthorFactory)
"""

PYPROJECT = """\
[project]
name = "shop"
version = "0"
requires-python = ">=3.9"
classifiers = ["Programming Language :: Python :: 3.11"]
dependencies = ["django>=5.0"]
"""


def run(root: Path, *command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
    )
    if check and result.returncode != 0:
        pytest.fail(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
    return result


@pytest.fixture(scope="module")
def subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An unknown repository: a real checkout, migrated, with nothing seeded."""
    root = tmp_path_factory.mktemp("subject")
    (root / "config").mkdir(parents=True)
    (root / "shop").mkdir(parents=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
    (root / "shop" / "factories.py").write_text(FACTORIES, encoding="utf-8")
    (root / "shop" / "api.py").write_text(API_URLS, encoding="utf-8")

    run(root, "git", "init", "--quiet")
    run(root, "git", "config", "user.email", "test@example.invalid")
    run(root, "git", "config", "user.name", "Test")
    run(root, "git", "add", "-A")
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "the repository as it stood"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2024-05-06T10:00:00+00:00",
            "GIT_COMMITTER_DATE": "2024-05-06T10:00:00+00:00",
        },
    )

    run(root, sys.executable, "manage.py", "makemigrations", "shop")
    run(root, sys.executable, "manage.py", "migrate")
    return root


def requester(root: Path) -> Callable[[str], Reply]:
    """Drive the subject the way the sandbox would, through its own test client."""

    def request(path: str) -> Reply:
        program = (
            "import json,os,sys;sys.path.insert(0,os.getcwd());"
            "import django;django.setup();"
            "from django.test import Client;"
            "r=Client().get(sys.argv[1]);"
            'print("<<<R>>>"+json.dumps({"status":r.status_code,"headers":dict(r.items())}))'
        )
        result = run(root, sys.executable, "-c", program, path, check=False)
        line = next((row for row in result.stdout.splitlines() if row.startswith("<<<R>>>")), None)
        if line is None:
            pytest.fail(f"the subject did not answer for {path}:\n{result.stderr}")
        answer = json.loads(line.removeprefix("<<<R>>>"))
        return Reply(status=answer["status"], headers=answer["headers"])

    return request


class DoNothingReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None:
        """Nothing to capture."""

    def begin(self) -> None:
        """Nothing to open."""

    def reset(self) -> None:
        """Nothing to restore."""


def proof() -> VerifiedReset:
    return VerifiedReset(
        mechanism=DoNothingReset(),
        report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
    )


FLUSH = [sys.executable, "manage.py", "flush", "--no-input"]


# ============================================================ the epic's own sentence


def plan() -> Plan:
    """What the Explorer decides, which `ground_workload` refuses to guess."""
    return Plan(
        workload_id="shop.books",
        description="the book list endpoint",
        entity="shop.Book",
        factory_module="shop.factories",
        target="shop.Book",
        requirements=["django>=5.0"],
        reset=ResetStrategy.SNAPSHOT_RESTORE,
        reset_between=FLUSH,
        repeats=3,
    )


def test_an_unknown_repository_becomes_an_emitted_workload(subject: Path) -> None:
    """Fingerprint to emission, in the order a caller would, with nothing skipped.

    **The sequence is `ground_workload`'s now, not this test's.** It was written
    here first, which is what let the composition check find six join defects; it
    moved to `src/` at S-7.13 because a sequence that lives in a test cannot be
    the orchestrator's `ground` node, and a second copy of it here would be the
    thing that disagrees with the first.
    """
    grounded = ground_workload(
        subject,
        python=[sys.executable],
        request=requester(subject),
        plan=plan(),
        reset=proof(),
    )

    assert isinstance(grounded.identification, Fingerprint)
    assert grounded.anchor.on == date(2024, 5, 6)
    assert grounded.interpreter is not None
    assert grounded.enumeration.of_kind(Kind.HTTP_ROUTE)
    assert grounded.auth.resolved, grounded.auth.describe()

    reloaded = read_document(json.dumps(grounded.emitted.document()))
    assert reloaded.work_verified


def test_what_the_ground_node_will_actually_read(subject: Path) -> None:
    """AC 1's other half: *the project facts and the workloads*.

    `CheckpointedState.project` is JSON because ADR 003 puts a checkpoint in
    SQLite, so what the orchestrator needs is not the `Fingerprint` — it is the
    flattened form, and `facts()` owning that is what stops a node reaching into
    the fingerprint and becoming a second place that changes when it grows a
    facet. Asserted through `json.dumps`, because *representable as JSON* is the
    actual requirement and a `Path` or an enum satisfies neither.
    """
    grounded = ground_workload(
        subject,
        python=[sys.executable],
        request=requester(subject),
        plan=plan(),
        reset=proof(),
    )

    facts = grounded.facts()
    assert json.loads(json.dumps(facts)) == facts, "a checkpoint cannot hold what will not encode"
    assert facts["framework"] == "Django"
    assert facts["anchor"] == "2024-05-06"
    assert facts["root"] == str(subject)

    assert grounded.workload.id == "shop.books"
    assert grounded.workload.observations, "the workload carries what the sweep measured"


def test_every_stage_completes_for_a_grounded_repository(subject: Path) -> None:
    """S-7.11's nine predicates, against a repository the epic has just ground."""
    run(subject, *FLUSH)
    verification = verify_work(
        subject,
        python=[sys.executable],
        path="/books/",
        target="shop.Book",
        workload_id="shop.books",
        description="the book list endpoint",
        reset=ResetStrategy.SNAPSHOT_RESTORE,
        reset_between=FLUSH,
        repeats=3,
    )
    resolution = resolve_auth(
        subject, python=[sys.executable], path="/books/", request=requester(subject)
    )

    progress = evaluate(
        fingerprint(subject),
        Grounding(
            root=subject,
            python=[sys.executable],
            auth=resolution,
            work=verification,
        ),
    )

    assert progress.complete, progress.describe()


def test_the_emitted_artifact_carries_the_environment_it_was_resolved_against(
    subject: Path,
) -> None:
    """S-7.12 AC 4, from the other end: *the anchor, the resolved dependency set,
    and any override are all recorded in the workload artifact.*"""
    anchor = anchor_for(subject)
    interpreter = interpreter_for(subject)
    assert interpreter is not None
    resolved = resolve(["django>=5.0"], anchor=anchor, python_version=interpreter.version)

    verification = verify_work(
        subject,
        python=[sys.executable],
        path="/books/",
        target="shop.Book",
        workload_id="shop.books",
        description="the book list endpoint",
        reset=ResetStrategy.SNAPSHOT_RESTORE,
        reset_between=FLUSH,
        repeats=3,
        environment=resolved.recorded(),
    )

    assert verification.workload.environment is not None
    assert verification.workload.environment.anchor == date(2024, 5, 6)
    assert verification.workload.environment.dependencies == resolved.pins


def test_a_ranked_route_can_be_requested_as_it_comes_out(subject: Path) -> None:
    """S-7.3 ranks candidates and S-7.4 and S-7.8 request one. The name that came
    out of the enumerator has to be the thing that goes into the requester, or
    every caller writes the same conversion and one of them gets it wrong.

    The ranked list mixes parsed and resolved routes, and only a resolved one has
    an address — so `drivable` is what a driver works down. Taking `scored[0]`
    blind is how a caller ends up requesting `books/` and reading the 404 as a
    missing credential, which is what the first run of this file did.
    """
    enumeration = enumerate_entry_points(subject, python=[sys.executable])

    best = enumeration.drivable[0]
    path = best.request_path
    assert path is not None

    answered = requester(subject)(path)

    assert answered.status == 200
    assert not any(candidate.request_path is None for candidate in enumeration.drivable)


def test_a_parsed_fragment_is_never_handed_out_as_an_address(subject: Path) -> None:
    """The distinction S-7.3 draws, at the join where it costs something.

    `orders/` is mounted under `api/`, so its parsed fragment and its resolved
    path differ. A fragment offered as an address sends the driver to `/orders/`,
    which does not exist — and in the first composed run that 404 was read one
    story later as *this route needs a credential*.
    """
    enumeration = enumerate_entry_points(subject, python=[sys.executable])

    parsed = next(
        candidate
        for candidate in enumeration.candidates
        if candidate.discovery is Discovery.PARSED and candidate.name == "orders/"
    )
    resolved = next(
        candidate
        for candidate in enumeration.candidates
        if candidate.discovery is Discovery.RESOLVED and candidate.name.endswith("orders/")
    )

    assert parsed.request_path is None
    assert resolved.request_path == "/api/orders/"
    assert requester(subject)("/orders/").status == 404
    assert requester(subject)(resolved.request_path).status == 200


def test_verification_needs_either_a_target_or_a_seeder(subject: Path) -> None:
    """One of the two has to say what the rows are. Neither is a caller error the
    harness should answer by seeding nothing and measuring it."""
    with pytest.raises(WorkVerificationError, match="needs a target model"):
        verify_work(
            subject,
            python=[sys.executable],
            path="/books/",
            workload_id="shop.books",
            description="the book list endpoint",
            reset=ResetStrategy.SNAPSHOT_RESTORE,
            reset_between=FLUSH,
            repeats=1,
        )


def test_the_fixture_mechanism_the_repository_has_is_the_one_used(subject: Path) -> None:
    """S-7.5 AC 2: *uses them in preference to synthesis.* The repository ships a
    factory, so seeding it must not silently synthesize from the schema."""
    discovery = discover(subject)
    chosen = prefer(discovery, entity="shop.Book")
    assert isinstance(chosen, Mechanism), chosen

    run(subject, *FLUSH)
    verification = verify_work(
        subject,
        python=[sys.executable],
        path="/books/",
        seed=factory_seeder(chosen, module="shop.factories"),
        workload_id="shop.books",
        description="the book list endpoint",
        reset=ResetStrategy.SNAPSHOT_RESTORE,
        reset_between=FLUSH,
        repeats=3,
    )

    assert "BookFactory" in verification.workload.fixture.source


def test_the_ranked_first_candidate_is_worth_trying_first(subject: Path) -> None:
    """S-7.3's ranking is a prior about S-7.8's gate. The composed check is
    whether the candidate it puts first actually passes that gate."""
    enumeration = enumerate_entry_points(subject, python=[sys.executable])
    best = next(
        entry.candidate for entry in enumeration.scored if entry.candidate.kind is Kind.HTTP_ROUTE
    )

    assert best.name.rstrip("/").endswith("books")


def test_stage_progress_is_reportable_before_anything_is_ground(subject: Path) -> None:
    """The Explorer asks where it is before it has finished. Every predicate must
    answer without the later stages' evidence, rather than raising."""
    progress = evaluate(fingerprint(subject), Grounding(root=subject, python=[sys.executable]))

    assert progress.outcome(Stage.CLONE).verdict is Verdict.HOLDS
    assert progress.outcome(Stage.WORK).verdict is Verdict.UNKNOWN
    assert progress.first_incomplete is not None

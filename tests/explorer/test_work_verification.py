"""S-7.8 — whether a workload does real work, and who is allowed to decide.

A safety story, so the tests come in two kinds. The ordinary ones check that a
growing endpoint verifies and a constant one does not, against a real Django
project measured through Django's own `CaptureQueriesContext`. The adversarial
ones **attempt the violation and assert it fails**: they try to write the verdict
in, to pass it through the agent's own serialization path, and to force a
rejected workload through the gate.

The fixture is the control that makes the rest mean anything. Three endpoints
over the same data — one that grows, one that returns a constant, and one that
returns a fixed-size aggregate — because a checker that verified all three would
pass every positive test in this file and be worthless.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from coldfix.explorer.work import (
    DEFAULT_SCALES,
    Verification,
    WorkVerificationError,
    accept,
    drive,
    verify_work,
)
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    FixtureRecipe,
    Observation,
    Workload,
)

pytestmark = pytest.mark.slow
"""Every test here migrates a project, seeds it twice and drives it six times."""

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

# The control set. `/books/` grows with the data; `/health/` is the stub F6 exists
# to reject; `/count/` is the honest false negative the artifact's own evidence
# admits to — an aggregate that does real work and returns a fixed-size answer.
URLS = """\
from django.http import HttpResponse, JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


def health(request):
    return HttpResponse("ok")


def count(request):
    return JsonResponse({"total": Book.objects.count()})


urlpatterns = [
    path("books/", books),
    path("health/", health),
    path("count/", count),
]
"""


def write_project(root: Path) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
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


@pytest.fixture
def subject(tmp_path: Path) -> Path:
    root = write_project(tmp_path)
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")
    return root


FLUSH = [sys.executable, "manage.py", "flush", "--no-input"]


def verify(root: Path, path: str, **overrides: object) -> Verification:
    arguments: dict[str, object] = {
        "python": [sys.executable],
        "path": path,
        "target": "shop.Book",
        "workload_id": "books-list",
        "description": "the book list endpoint",
        "reset": ResetStrategy.SNAPSHOT_RESTORE,
        "reset_between": FLUSH,
        "repeats": 3,
    }
    arguments.update(overrides)
    return verify_work(root, **arguments)  # type: ignore[arg-type]


def recipe() -> FixtureRecipe:
    return FixtureRecipe(
        entity="shop.Book", per_parent=1, distribution=Distribution.UNIFORM, source="synthesized"
    )


def workload_with(observations: tuple[Observation, ...]) -> Workload:
    return Workload(
        id="books-list",
        description="the book list endpoint",
        entry_point="/books/",
        fixture=recipe(),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        observations=observations,
    )


# ============================ AC 1 and 2: the harness measures and the harness decides


def test_an_endpoint_that_grows_with_the_data_verifies(subject: Path) -> None:
    verification = verify(subject, "/books/")

    assert verification.verified
    assert verification.evidence.startswith("Verified")
    assert [d.scale for d in verification.drives] == list(DEFAULT_SCALES)


def test_the_three_metrics_come_back_from_the_subject(subject: Path) -> None:
    """Wall time and bytes could be read from outside; the query count cannot —
    nothing outside the process knows how many statements a request issued."""
    verification = verify(subject, "/books/")

    small, large = verification.drives
    assert small.queries > 0
    assert large.response_bytes > small.response_bytes
    assert large.seconds > 0
    assert set(small.observation().metrics) == {DB_QUERY, RESPONSE_BYTES, SECONDS}


def test_a_stub_route_is_not_verified(subject: Path) -> None:
    """The case F6 exists for: constant bytes, constant time, no queries at all."""
    verification = verify(subject, "/health/", workload_id="health")

    assert not verification.verified
    assert "Not verified" in verification.evidence


def test_an_aggregate_endpoint_is_refused_rather_than_called_broken(subject: Path) -> None:
    """The honest false negative. `/count/` does real work and returns a
    fixed-size answer, which from outside is indistinguishable from a stub — so
    the answer is a refusal to verify, not a claim the workload is broken."""
    verification = verify(subject, "/count/", workload_id="book-count")

    assert not verification.verified
    assert "cannot tell those apart" in verification.evidence


def test_the_query_count_is_measured_not_assumed(subject: Path) -> None:
    """`/books/` has an N+1 across the author foreign key, so its query count
    climbs with the rows. Nothing here told the harness that."""
    verification = verify(subject, "/books/")

    small, large = verification.drives
    assert large.queries > small.queries


def test_a_drive_reports_the_samples_behind_its_median(subject: Path) -> None:
    """A ratio between two medians is only as honest as the spread behind them."""
    measured = drive(
        subject, python=[sys.executable], path="/health/", scale=1, created={}, repeats=3
    )

    assert len(measured.samples) == 3
    assert min(measured.samples) <= measured.seconds <= max(measured.samples)


def test_the_reported_time_is_the_median_and_not_the_first_reading(
    subject: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three candidate readings have to disagree, or the assertion cannot
    tell them apart — the *min <= seconds <= max* check above is satisfied by
    every one of them, and a sabotage taking `samples[0]` walked straight
    through it.

    Real timings cannot be made to disagree on demand, so the samples are fed in
    at the subprocess boundary: what is under test is the arithmetic this module
    does to an answer, not the answer itself.
    """
    monkeypatch.setattr(
        "coldfix.explorer.work._run_in_subject",
        lambda *args, **kwargs: {
            "samples": [0.9, 0.1, 0.2],
            "warmup_seconds": 1.5,
            "queries": 3,
            "response_bytes": 9,
            "status": 200,
        },
    )

    measured = drive(
        subject, python=[sys.executable], path="/books/", scale=10, created={}, repeats=3
    )

    assert measured.seconds == 0.2
    assert measured.seconds != measured.samples[0]
    assert measured.seconds != sum(measured.samples) / len(measured.samples)


def test_the_warm_up_is_measured_and_kept_out_of_the_samples(subject: Path) -> None:
    """The first request through a Django stack pays module imports, template
    compilation and connection setup. Charging those to the small scale point is
    how a flat workload comes to look like a growing one — so the warm-up happens,
    and it is recorded rather than merely believed in."""
    measured = drive(
        subject, python=[sys.executable], path="/health/", scale=1, created={}, repeats=3
    )

    assert measured.warmup_seconds > 0
    assert len(measured.samples) == 3
    assert measured.warmup_seconds not in measured.samples
    assert "warm-up" in measured.describe()


def test_asking_for_no_repeats_is_refused_rather_than_clamped(subject: Path) -> None:
    """Clamping to one silently would answer a question nobody asked."""
    with pytest.raises(WorkVerificationError, match="measures nothing"):
        drive(subject, python=[sys.executable], path="/health/", scale=1, created={}, repeats=0)


def test_a_subject_that_answers_without_samples_is_refused(
    subject: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch the machine cannot reach naturally, forced rather than left
    unchecked — S-7.2's rule. A subject whose program answered with a well-formed
    payload and no timings would otherwise reach `statistics.median([])` and fail
    as an arithmetic error rather than as a diagnosis.
    """
    monkeypatch.setattr(
        "coldfix.explorer.work._run_in_subject",
        lambda *args, **kwargs: {"samples": [], "queries": 3, "response_bytes": 9, "status": 200},
    )

    with pytest.raises(WorkVerificationError, match="no timing samples"):
        drive(subject, python=[sys.executable], path="/books/", scale=10, created={}, repeats=3)


def test_an_endpoint_that_errors_is_refused_rather_than_measured(subject: Path) -> None:
    """An error page is cheap, constant and identical at every scale — exactly
    the profile this check exists to reject, and it must not be allowed to
    present as one by failing consistently."""
    with pytest.raises(WorkVerificationError, match="HTTP 404"):
        drive(subject, python=[sys.executable], path="/nope/", scale=10, created={}, repeats=1)


# ================================================ the spread has to mean something


def test_one_scale_point_is_refused(subject: Path) -> None:
    with pytest.raises(WorkVerificationError, match="at least two distinct scales"):
        verify(subject, "/books/", scales=[10])


def test_a_repeated_scale_is_one_scale(subject: Path) -> None:
    with pytest.raises(WorkVerificationError, match="at least two distinct scales"):
        verify(subject, "/books/", scales=[10, 10, 10])


def test_a_spread_too_narrow_for_the_thresholds_is_refused(subject: Path) -> None:
    """Below 4x, F6's numbers ask a workload to double its payload for a small
    increase in data — so a pass would mean less than the refusal does."""
    with pytest.raises(WorkVerificationError, match="spread"):
        verify(subject, "/books/", scales=[10, 20])


# ==================================== AC 3: the agent cannot supply or override it


def test_the_verdict_cannot_be_written_into_the_artifact() -> None:
    """S-4.1's guarantee, re-attempted from this story's side because it is the
    one this story depends on.

    **No `type: ignore` here, and that is the finding.** mypy does not object to
    this call: pydantic's plugin does not model `extra="forbid"` as a signature,
    so the keyword type-checks and fails only at runtime. The guarantee is the
    schema, not the type checker — which is exactly why `CLAUDE.md` says a rule
    that must hold needs enforcement in code rather than in a convention.
    """
    with pytest.raises(ValidationError):
        Workload(
            id="books-list",
            description="d",
            entry_point="/books/",
            fixture=recipe(),
            reset_method=ResetStrategy.SNAPSHOT_RESTORE,
            work_verified=True,
        )


def test_the_verdict_cannot_arrive_through_the_agents_serialization_path() -> None:
    """The path that matters here. An agent does not call a constructor — it
    produces JSON against a schema, and `model_validate` is where that lands."""
    payload = {
        "id": "books-list",
        "description": "d",
        "entry_point": "/books/",
        "fixture": recipe().model_dump(mode="json"),
        "reset_method": ResetStrategy.SNAPSHOT_RESTORE.value,
        "observations": [],
        "work_verified": True,
        "work_evidence": "Verified: trust me",
    }

    with pytest.raises(ValidationError):
        Workload.model_validate(payload)


def test_the_verdict_cannot_be_assigned_after_construction() -> None:
    built = workload_with(())

    with pytest.raises(ValidationError):
        built.work_verified = True  # type: ignore[misc]


def test_a_verification_holds_no_verdict_of_its_own() -> None:
    """It delegates to the artifact, whose `work_verified` has no field behind
    it — so there is no attribute in the chain a claim could be written to."""
    verification = Verification(workload=workload_with(()), drives=())

    with pytest.raises((AttributeError, TypeError)):
        verification.verified = True  # type: ignore[misc]


def test_a_workload_with_no_observations_cannot_claim_verification() -> None:
    """The Explorer produces a workload before anything has swept it, and that is
    a real state. What it cannot be is verified."""
    assert not workload_with(()).work_verified


# ============================== AC 4: rejection regardless of what the agent claims


def test_accept_returns_the_workload_when_the_harness_verified_it(subject: Path) -> None:
    verification = verify(subject, "/books/")

    assert accept(verification) is verification.workload


def test_accept_refuses_a_workload_that_failed(subject: Path) -> None:
    verification = verify(subject, "/health/", workload_id="health")

    with pytest.raises(WorkVerificationError, match="is rejected"):
        accept(verification)


def test_the_refusal_carries_the_evidence_rather_than_a_bare_no(subject: Path) -> None:
    """Every way of failing calls for a different action — sweep another point,
    widen the spread, measure the missing metric, or reject the candidate."""
    verification = verify(subject, "/health/", workload_id="health")

    with pytest.raises(WorkVerificationError) as raised:
        accept(verification)

    assert "Not verified" in str(raised.value)
    assert "n=10" in str(raised.value)


def test_there_is_no_parameter_through_which_a_claim_could_enter() -> None:
    """The structural half of AC 4, asserted rather than described.

    A gate that accepted a claim and ignored it would be one refactor away from
    honouring it. `accept` takes the harness's own measurements and nothing else,
    and this test fails the moment somebody adds `force=` to make a demo pass.
    """
    parameters = inspect.signature(accept).parameters

    assert list(parameters) == ["verification"]
    assert parameters["verification"].default is inspect.Parameter.empty


def test_a_forged_verification_still_cannot_pass_the_gate() -> None:
    """The adversarial case: an agent that constructs the wrapper itself, with
    whatever observations it likes, still meets a verdict computed from those
    observations rather than from its intent."""
    forged = Verification(
        workload=workload_with(
            (
                Observation(scale=10, metrics={DB_QUERY: 2, RESPONSE_BYTES: 100, SECONDS: 0.01}),
                Observation(scale=100, metrics={DB_QUERY: 2, RESPONSE_BYTES: 100, SECONDS: 0.01}),
            )
        ),
        drives=(),
    )

    assert not forged.verified
    with pytest.raises(WorkVerificationError, match="is rejected"):
        accept(forged)


def test_measurements_that_would_pass_do_pass_however_they_were_obtained() -> None:
    """The control for the test above. The gate is not refusing everything it did
    not measure itself — it is applying F6 to whatever observations the artifact
    holds, and observations are the one thing an agent cannot put there without
    the harness (S-4.1's `Observation` is built by `Drive.observation`)."""
    honest = Verification(
        workload=workload_with(
            (
                Observation(scale=10, metrics={DB_QUERY: 2, RESPONSE_BYTES: 100, SECONDS: 0.01}),
                Observation(scale=100, metrics={DB_QUERY: 11, RESPONSE_BYTES: 1000, SECONDS: 0.05}),
            )
        ),
        drives=(),
    )

    assert honest.verified
    assert accept(honest) is honest.workload


# ============================================================ honest failure


def test_a_project_that_cannot_be_configured_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("nothing useful", encoding="utf-8")

    with pytest.raises(WorkVerificationError, match="DJANGO_SETTINGS_MODULE"):
        drive(tmp_path, python=[sys.executable], path="/books/", scale=10, created={}, repeats=1)


def test_a_failing_reset_stops_the_sweep(subject: Path) -> None:
    """Measuring the second point on top of the first makes it a measurement of
    both, and the growth it shows is arithmetic rather than a property of the
    workload."""
    with pytest.raises(WorkVerificationError, match="reset between scale points failed"):
        verify(subject, "/books/", reset_between=[sys.executable, "manage.py", "no_such_command"])


def test_each_scale_point_measures_only_its_own_rows(subject: Path) -> None:
    """The reset is what makes n=100 a hundred rows rather than a hundred and ten."""
    verification = verify(subject, "/books/")

    small, large = verification.drives
    assert small.created["shop.Book"] == 10
    assert large.created["shop.Book"] == 100


def test_the_report_names_the_scales_the_metrics_and_the_verdict(subject: Path) -> None:
    """S-7.9 emits this beside the artifact and S-17.2 can publish it."""
    described = verify(subject, "/books/").describe()

    assert "n=10:" in described
    assert "n=100:" in described
    assert "Verified" in described


def test_the_recipe_of_the_verified_workload_is_the_one_that_was_seeded(
    subject: Path,
) -> None:
    """AC 3 of S-7.7 reaching here: the artifact carries the shape it was
    measured under, not a default."""
    verification = verify(subject, "/books/", per_parent=5, distribution=Distribution.LONG_TAIL)

    assert verification.workload.fixture.distribution is Distribution.LONG_TAIL
    assert verification.workload.fixture.parents == 20


def test_the_artifact_serializes_with_its_verdict_absent(subject: Path) -> None:
    """`work_verified` is a property, so it is not in the dump — which is what
    stops a round trip from turning a computed verdict into a stored one."""
    verification = verify(subject, "/books/")

    dumped = json.loads(verification.workload.model_dump_json())

    assert "work_verified" not in dumped
    assert Workload.model_validate(dumped).work_verified

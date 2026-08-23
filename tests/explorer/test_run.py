"""S-7.10 — the three bounds on a grounding run, and what it says when it stops.

The three are different instruments and the tests keep them apart: the global cap
stops a run that is spending without arriving, the stall check stops one that is
spending without *learning*, and the per-stage budget stops one that is spending
all of it on a single stage. A run can hit any of the three first, and each says
something different.

The subject is a real Django project, held deliberately at a state where one
stage will never complete — because a failure report is the thing this story is
mostly about, and a report about a repository that grounds fine would exercise
none of it.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import Budget, CapRaisedError
from coldfix.explorer.auth import (
    AuthProfile,
    Established,
    Requirement,
    Resolution,
    Scheme,
)
from coldfix.explorer.emission import EmittedWorkload
from coldfix.explorer.fingerprint import Detected, Fingerprint, fingerprint
from coldfix.explorer.run import (
    DEFAULT_STAGE_ATTEMPTS,
    GROUNDING_STALL_AFTER,
    GroundingError,
    GroundingFailedError,
    GroundingRun,
)
from coldfix.explorer.stages import Grounding, Stage, Verdict
from coldfix.explorer.work import Verification
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    FixtureRecipe,
    Observation,
    Workload,
)

pytestmark = pytest.mark.slow
"""Every attempt evaluates all nine predicates against a real subject."""

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


def write_project(root: Path) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
    return root


@pytest.fixture
def subject(tmp_path: Path) -> Path:
    """Unmigrated on purpose: `migrate` is the stage that will never complete."""
    return write_project(tmp_path)


def identified(root: Path) -> Fingerprint:
    found = fingerprint(root)
    assert isinstance(found, Fingerprint)
    return found


def budget(**overrides: object) -> Budget:
    fields: dict[str, object] = {
        "ledger": Ledger(),
        "rate": ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 14)),
        "stall_after": GROUNDING_STALL_AFTER,
    }
    fields.update(overrides)
    return Budget(**fields)  # type: ignore[arg-type]


def run_for(root: Path, **overrides: object) -> GroundingRun:
    fields: dict[str, object] = {
        "identification": identified(root),
        "grounding": Grounding(root=root, python=[sys.executable]),
        "budget": budget(),
    }
    fields.update(overrides)
    return GroundingRun(**fields)  # type: ignore[arg-type]


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


def verified_workload() -> Verification:
    """A workload whose observations verify. Built by hand, judged by S-7.8."""
    return Verification(
        workload=Workload(
            id="books-list",
            description="the book list endpoint",
            entry_point="/books/",
            fixture=FixtureRecipe(
                entity="shop.Book",
                per_parent=1,
                parents=10,
                distribution=Distribution.UNIFORM,
                source="synthesized",
            ),
            reset_method=ResetStrategy.SNAPSHOT_RESTORE,
            observations=(
                Observation(scale=10, metrics={DB_QUERY: 11, RESPONSE_BYTES: 400, SECONDS: 0.01}),
                Observation(
                    scale=100, metrics={DB_QUERY: 101, RESPONSE_BYTES: 4000, SECONDS: 0.08}
                ),
            ),
        ),
        drives=(),
    )


# ================================================ AC 1: the 60-step cap is S-5.4's


def test_the_global_cap_is_the_one_compiled_into_s_5_4(subject: Path) -> None:
    """Reimplementing it here would be a second set of caps to keep in step with
    the first."""
    run = run_for(subject)

    assert run.budget.caps[Phase.GROUND].limit == 60


def test_the_cap_cannot_be_raised_at_runtime(subject: Path) -> None:
    run = run_for(subject)

    with pytest.raises(CapRaisedError):
        run.budget.tighten(Phase.GROUND, 200)


def test_a_run_out_of_global_budget_fails_with_the_stage_it_was_on(subject: Path) -> None:
    run = run_for(subject, budget=budget(stall_after=GROUNDING_STALL_AFTER))
    run.budget.tighten(Phase.GROUND, 2)

    run.attempt(Stage.MIGRATE, "ran migrate")
    run.attempt(Stage.MIGRATE, "ran migrate again")

    with pytest.raises(GroundingFailedError) as raised:
        run.attempt(Stage.MIGRATE, "and again")

    assert "out of budget" in str(raised.value)
    assert raised.value.failure.stopped_at is not None
    assert raised.value.failure.stopped_at.stage is Stage.MIGRATE


# ==================================== AC 2: fifteen steps with no new information


def test_grounding_stalls_after_fifteen_unchanged_reports(subject: Path) -> None:
    """S-7.11 is what makes this a measurement: the digest is the nine verdicts,
    so fifteen steps that move nothing read identically whoever believes
    otherwise.

    The attempts cycle between stages on purpose, and that is not a contrivance —
    it is the case the stall check is actually for. With the default per-stage
    budget of eight, a run hammering *one* stage is stopped by AC 3 long before
    fifteen steps, which is the backlog note's point that the per-stage budget is
    the tighter instrument. What the global stall catches is the other shape: an
    agent moving between stages, spending steps, and changing nothing anywhere.
    """
    stages = [Stage.MIGRATE, Stage.SEED, Stage.CONNECT, Stage.CONFIGURE]
    run = run_for(subject)

    for step in range(GROUNDING_STALL_AFTER - 1):
        run.attempt(stages[step % len(stages)], f"attempt {step}")

    with pytest.raises(GroundingFailedError) as raised:
        run.attempt(stages[0], "one more")

    assert "same conclusion" in str(raised.value)


def test_one_stage_hammered_hits_the_tighter_bound_first(subject: Path) -> None:
    """The relationship between AC 2 and AC 3, asserted rather than assumed: with
    the defaults, a run spending everything on one stage never reaches the stall
    check, because the per-stage budget stops it at eight."""
    run = run_for(subject)

    for step in range(DEFAULT_STAGE_ATTEMPTS):
        run.attempt(Stage.MIGRATE, f"attempt {step}")

    with pytest.raises(GroundingFailedError) as raised:
        run.attempt(Stage.MIGRATE, "a ninth")

    assert "whole budget" in str(raised.value)
    assert "same conclusion" not in str(raised.value)


def test_a_budget_with_the_wrong_progress_check_is_refused(subject: Path) -> None:
    """S-5.4's default of three is right for an investigation and wrong here.
    Substituting the right one silently would hide that the caller asked for
    something else — a run escalating after three unchanged reports would abandon
    a repository mid-install."""
    with pytest.raises(GroundingError, match="stall_after=15"):
        run_for(subject, budget=budget(stall_after=3))


def test_a_step_that_moves_a_stage_is_not_a_repeat(tmp_path: Path) -> None:
    """The control. A stall check that fired on every run of identical *attempts*
    rather than identical *reports* would stop a run that is making progress."""
    root = write_project(tmp_path)
    run = run_for(root, stage_attempts=GROUNDING_STALL_AFTER + 5)

    for step in range(GROUNDING_STALL_AFTER - 1):
        run.attempt(Stage.MIGRATE, f"attempt {step}")

    subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "shop"],
        cwd=root,
        capture_output=True,
        timeout=300,
        check=True,
    )
    subprocess.run(
        [sys.executable, "manage.py", "migrate"],
        cwd=root,
        capture_output=True,
        timeout=300,
        check=True,
    )

    outcome = run.attempt(Stage.MIGRATE, "actually migrated this time")

    assert outcome.verdict is Verdict.HOLDS


# ============================================ AC 3: a per-stage attempt budget


def test_one_stage_cannot_spend_the_whole_run(subject: Path) -> None:
    """S-0.3's runs took five to nineteen minutes, and detecting at stage two that
    a repository will not ground saves the other seven stages."""
    run = run_for(subject, stage_attempts=3)

    for step in range(3):
        run.attempt(Stage.MIGRATE, f"attempt {step}")

    with pytest.raises(GroundingFailedError) as raised:
        run.attempt(Stage.MIGRATE, "a fourth")

    assert "whole budget" in str(raised.value)
    assert run.budget.used(Phase.GROUND) == 3


def test_the_per_stage_budget_is_per_stage(subject: Path) -> None:
    """A run can exhaust the global budget across seven stages without ever
    exhausting one, which is why both bounds exist."""
    run = run_for(subject, stage_attempts=2)

    run.attempt(Stage.MIGRATE, "one")
    run.attempt(Stage.MIGRATE, "two")
    run.attempt(Stage.SEED, "a different stage entirely")

    assert run.attempts_at(Stage.MIGRATE) == 2
    assert run.attempts_at(Stage.SEED) == 1


def test_a_per_stage_budget_of_zero_is_refused(subject: Path) -> None:
    with pytest.raises(GroundingError, match="lets no stage be attempted"):
        run_for(subject, stage_attempts=0)


def test_the_default_leaves_room_above_the_worst_case_s_0_3_observed() -> None:
    assert DEFAULT_STAGE_ATTEMPTS > 6


# ================== AC 4: which stage never completed, and what was tried there


def test_the_failure_names_the_stage_and_its_predicate(subject: Path) -> None:
    run = run_for(subject, stage_attempts=2)
    run.attempt(Stage.MIGRATE, "ran migrate")
    run.attempt(Stage.MIGRATE, "ran migrate with --run-syncdb")

    with pytest.raises(GroundingFailedError) as raised:
        run.attempt(Stage.MIGRATE, "gave up")

    report = raised.value.failure.report()
    assert "never completed: migrate" in report
    assert "done would have meant: the migration tool reports zero unapplied migrations" in report
    assert "last measured:" in report


def test_the_failure_lists_only_what_was_tried_at_that_stage(subject: Path) -> None:
    """A transcript is what somebody has to read; the attempts at the stage that
    stopped the run are what somebody can act on."""
    run = run_for(subject, stage_attempts=2)
    run.attempt(Stage.SEED, "irrelevant work elsewhere")
    run.attempt(Stage.MIGRATE, "ran migrate")
    run.attempt(Stage.MIGRATE, "ran migrate again")

    failure = run.give_up("stopping here")

    assert [entry.what for entry in failure.attempts_at_the_stage] == [
        "ran migrate",
        "ran migrate again",
    ]


def test_the_failure_names_the_incomplete_stage_not_the_last_one_attempted(
    subject: Path,
) -> None:
    """The two have to differ, or the assertion cannot tell them apart.

    Every other test here attempts the stage that is also the incomplete one, so
    *last attempted* and *first incomplete* coincide and a report built on either
    reads the same. Here the run touches `migrate` (which fails) and then `seed`
    (which is later in the order and also incomplete): the report must name
    `migrate`, because that is where the run actually stopped.
    """
    run = run_for(subject)
    run.attempt(Stage.MIGRATE, "ran migrate")
    run.attempt(Stage.SEED, "tried to seed anyway")

    failure = run.give_up("stopping here")

    assert failure.stopped_at is not None
    assert failure.stopped_at.stage is Stage.MIGRATE
    assert "never completed: migrate" in failure.report()


def test_the_failure_records_what_did_complete(subject: Path) -> None:
    """A repository that got seven stages in and failed at the eighth is a
    different report from one that never started."""
    run = run_for(subject)
    run.attempt(Stage.MIGRATE, "ran migrate")

    failure = run.give_up("stopping here")

    assert Stage.CLONE in failure.progress.completed
    assert Stage.DEPENDENCIES in failure.progress.completed


def test_giving_up_returns_rather_than_raises(subject: Path) -> None:
    """An agent choosing to stop is not an error. `08-audit.md`'s null-result
    rule makes an honest *this will not ground* a legitimate output rather than a
    failure to produce one."""
    run = run_for(subject)

    failure = run.give_up("the database driver is not installable here")

    assert "not installable" in failure.report()


# ================ AC 5: never reports success when no workload does real work


def test_a_run_cannot_finish_while_a_stage_is_incomplete(subject: Path) -> None:
    run = run_for(subject)

    with pytest.raises(GroundingFailedError, match="stage still incomplete"):
        run.finish(verified_workload(), reset=proof())


def test_a_run_cannot_finish_on_a_workload_that_does_no_work(tmp_path: Path) -> None:
    """The gate is S-7.8's, reached through S-7.9's `emit`. There is no second
    opinion here about whether the run succeeded."""
    root = write_project(tmp_path)
    run = run_for(root)

    flat = Verification(
        workload=verified_workload().workload.model_copy(
            update={
                "observations": (
                    Observation(scale=10, metrics={DB_QUERY: 1, RESPONSE_BYTES: 2, SECONDS: 0.1}),
                    Observation(scale=100, metrics={DB_QUERY: 1, RESPONSE_BYTES: 2, SECONDS: 0.1}),
                )
            }
        ),
        drives=(),
    )

    with pytest.raises(GroundingFailedError):
        run.finish(flat, reset=proof())


def test_finish_is_the_only_thing_that_returns_a_workload() -> None:
    """AC 5's structural half: every other exit from a run produces a `Failure`,
    so there is no path to a reported success that skips the gate."""
    returning = sorted(
        name
        for name, member in inspect.getmembers(GroundingRun, inspect.isfunction)
        if not name.startswith("_")
        and "EmittedWorkload" in str(inspect.signature(member).return_annotation)
    )

    assert returning == ["finish"]


def test_a_ground_repository_finishes_and_emits(tmp_path: Path) -> None:
    """The positive control. Without it, every test above would pass against a
    run that can never succeed at all."""
    root = write_project(tmp_path)
    for arguments in (("makemigrations", "shop"), ("migrate",)):
        subprocess.run(
            [sys.executable, "manage.py", *arguments],
            cwd=root,
            capture_output=True,
            timeout=300,
            check=True,
        )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys;sys.path.insert(0,os.getcwd());import django;django.setup();"
            "from shop.models import Author, Book;"
            "[Book.objects.create(title='t%s' % i,"
            " author=Author.objects.create(name='a%s' % i)) for i in range(12)]",
        ],
        cwd=root,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        timeout=300,
        check=True,
    )

    run = run_for(root)
    resolution = _open_route_resolution()
    run.observed(auth=resolution)

    emitted = run.finish(verified_workload(), reset=proof())

    assert isinstance(emitted, EmittedWorkload)
    assert emitted.work_verified


def _open_route_resolution() -> Resolution:
    return Resolution(
        profile=AuthProfile(
            settings_module=Detected("config.settings", "manage.py"),
            declared=(),
            user_model=None,
            login_url=None,
            session_cookie_name="sessionid",
        ),
        requirement=Requirement(
            path="/books/", scheme=Scheme.NONE, established=Established.OBSERVED
        ),
        credential=None,
    )


# ================================================================ the running report


def test_the_report_says_where_the_run_spent_itself(subject: Path) -> None:
    run = run_for(subject)
    run.attempt(Stage.MIGRATE, "one")
    run.attempt(Stage.SEED, "two")

    described = run.report()

    assert "2 step(s)" in described
    assert "migrate: 1/" in described
    assert "seed: 1/" in described


def test_an_attempt_is_recorded_with_the_agents_own_words(subject: Path) -> None:
    """The one part of the record the agent writes, and it is a label rather than
    a verdict — the harness's reading sits beside it."""
    run = run_for(subject)

    run.attempt(Stage.MIGRATE, "installed the postgres driver")

    assert run.attempts[0].what == "installed the postgres driver"
    assert run.attempts[0].outcome.verdict is Verdict.FAILS


# ============================ S-7.14: the report an attempt was judged against


def test_the_report_an_attempt_was_judged_against_is_kept(subject: Path) -> None:
    """A driver has to know which stage to work on next, and `attempt` hands back
    one stage's outcome while the command it ran may have moved another. Without
    this the driver measures all nine again with nothing having happened in
    between, and routes on a reading that is not the one the bounds were enforced
    against."""
    assert run_for(subject).measured is None, "nothing has been judged yet"
    run = run_for(subject)

    outcome = run.attempt(Stage.MIGRATE, "ran migrate")

    kept = run.measured
    assert kept is not None
    assert kept.outcome(Stage.MIGRATE) == outcome
    assert len(kept.outcomes) == len(Stage), "all nine, not the one asked about"


def test_evidence_arriving_makes_the_kept_report_stale(subject: Path) -> None:
    """`observed` is the one thing that changes what the predicates can see, so it
    is the one thing that has to invalidate the last reading of them."""
    run = run_for(subject)
    run.attempt(Stage.MIGRATE, "ran migrate")
    kept = run.measured
    assert kept is not None, "and the fixture has something to invalidate"

    run.observed(work=verified_workload())

    assert run.measured is None

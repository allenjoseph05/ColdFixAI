"""S-7.9 — handing a workload on, and what it takes to be believed.

Most of this file needs no subprocess: an emission is an artifact, a copy of a
computed verdict and a proof, and every rule about it is a rule about those three
agreeing. The adversarial half **edits the emitted document** the way an agent
with a text editor would and asserts each edit is refused.

The one end-to-end test drives a real subject through S-7.8 and emits the result,
because a document assembled entirely from hand-built observations would prove
only that this file can build observations.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from coldfix.explorer.emission import EmissionError, EmittedWorkload, emit, read_document
from coldfix.explorer.work import Verification, verify_work
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import (
    Drift,
    VerificationError,
    VerificationReport,
    VerifiedReset,
)
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    FixtureRecipe,
    Observation,
    Workload,
)

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
from django.http import HttpResponse, JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


def health(request):
    return HttpResponse("ok")


urlpatterns = [path("books/", books), path("health/", health)]
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


# ================================================================ hand-built parts


def recipe() -> FixtureRecipe:
    return FixtureRecipe(
        entity="shop.Book",
        per_parent=1,
        parents=10,
        distribution=Distribution.UNIFORM,
        source="synthesized from schema",
    )


GROWING = (
    Observation(scale=10, metrics={DB_QUERY: 11, RESPONSE_BYTES: 400, SECONDS: 0.01}),
    Observation(scale=100, metrics={DB_QUERY: 101, RESPONSE_BYTES: 4000, SECONDS: 0.08}),
)

FLAT = (
    Observation(scale=10, metrics={DB_QUERY: 1, RESPONSE_BYTES: 2, SECONDS: 0.001}),
    Observation(scale=100, metrics={DB_QUERY: 1, RESPONSE_BYTES: 2, SECONDS: 0.001}),
)


def workload(
    observations: tuple[Observation, ...] = GROWING,
    reset_method: ResetStrategy = ResetStrategy.SNAPSHOT_RESTORE,
) -> Workload:
    return Workload(
        id="books-list",
        description="the book list endpoint",
        entry_point="/books/",
        fixture=recipe(),
        reset_method=reset_method,
        observations=observations,
    )


class DoNothingReset(ResetMechanism):
    """A mechanism that resets nothing, because nothing here is measured through it.

    What these tests need from a `VerifiedReset` is its *strategy* and its report;
    the mechanism is the thing S-2.7 already drives ten times against a real
    database in its own tests. Standing one up here would be testing S-2.7 again
    from the wrong file.
    """

    strategy: ClassVar[ResetStrategy] = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None:
        """Nothing to capture."""

    def begin(self) -> None:
        """Nothing to open."""

    def reset(self) -> None:
        """Nothing to restore."""


class RollbackDoNothingReset(DoNothingReset):
    """The same, for the other strategy, so a mismatch can be constructed."""

    strategy: ClassVar[ResetStrategy] = ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES


def proof(
    strategy: ResetStrategy = ResetStrategy.SNAPSHOT_RESTORE, cycles: int = 10
) -> VerifiedReset:
    """A real `VerifiedReset`: its constructor refuses a report that did not pass."""
    mechanism: ResetMechanism = (
        DoNothingReset() if strategy is ResetStrategy.SNAPSHOT_RESTORE else RollbackDoNothingReset()
    )
    return VerifiedReset(
        mechanism=mechanism,
        report=VerificationReport(strategy=strategy, cycles=cycles),
    )


def emitted(**overrides: object) -> EmittedWorkload:
    built = workload()
    fields: dict[str, object] = {
        "workload": built,
        "work_verified": built.work_verified,
        "evidence_of_work": built.work_evidence,
        "reset_strategy": built.reset_method,
        "reset_cycles": 10,
    }
    fields.update(overrides)
    return EmittedWorkload(**fields)  # type: ignore[arg-type]


# ============================================== AC 1: it emits a validated object


def test_a_verified_workload_is_emitted() -> None:
    emission = emit(Verification(workload=workload(), drives=()), reset=proof())

    assert emission.workload.id == "books-list"
    assert emission.work_verified
    assert emission.evidence_of_work.startswith("Verified")


def test_the_document_is_json_ready_and_reloads() -> None:
    emission = emit(Verification(workload=workload(), drives=()), reset=proof())

    reloaded = read_document(json.dumps(emission.document()))

    assert reloaded == emission


def test_an_unverified_workload_is_not_emitted() -> None:
    """`03-agents.md` §2.4: the evidence exists to make *it ran but did nothing*
    structurally unreportable as success, and emitting this would be that report."""
    with pytest.raises(EmissionError, match="did not verify"):
        emit(Verification(workload=workload(FLAT), drives=()), reset=proof())


def test_a_workload_with_no_observations_is_not_emitted() -> None:
    with pytest.raises(EmissionError, match="did not verify"):
        emit(Verification(workload=workload(()), drives=()), reset=proof())


# ================================== AC 2: the evidence is mandatory and powerless


def test_the_evidence_cannot_be_left_out() -> None:
    with pytest.raises(ValueError, match="evidence_of_work"):
        EmittedWorkload(  # type: ignore[call-arg]
            workload=workload(),
            work_verified=True,
            reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
            reset_cycles=10,
        )


def test_the_evidence_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="evidence_of_work"):
        emitted(evidence_of_work="")


def test_a_document_claiming_a_verdict_its_observations_do_not_support_is_refused() -> None:
    """The attack this module exists for: an agent with a text editor.

    The workload's observations are flat, so the harness's verdict is no. Writing
    yes into the document does not make a more convincing workload; it makes a
    document that will not load."""
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = dict(honest.document())
    tampered["workload"] = json.loads(workload(FLAT).model_dump_json())

    with pytest.raises(EmissionError, match="can only disagree if one of them was edited"):
        read_document(tampered)


def test_a_document_whose_evidence_was_rewritten_is_refused() -> None:
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = dict(honest.document())
    tampered["evidence_of_work"] = "Verified: it definitely works, trust me"

    with pytest.raises(EmissionError, match="not the evidence its observations produce"):
        read_document(tampered)


def test_a_document_whose_verdict_was_flipped_to_false_is_also_refused() -> None:
    """The control for the direction. A check that only caught *yes* would let a
    verified workload be quietly demoted, and S-7.10 reports on what verified."""
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = dict(honest.document())
    tampered["work_verified"] = False

    with pytest.raises(EmissionError, match="can only disagree"):
        read_document(tampered)


def test_an_observation_cannot_be_edited_without_the_evidence_noticing() -> None:
    """The subtler edit: leave the verdict alone and improve the measurements.
    The evidence string names the scales and the ratio, so it stops matching."""
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = json.loads(json.dumps(honest.document()))
    tampered["workload"]["observations"][1]["scale"] = 1000

    with pytest.raises(EmissionError, match="not the evidence its observations produce"):
        read_document(tampered)


def test_an_unknown_field_in_the_document_is_refused() -> None:
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = dict(honest.document())
    tampered["approved_by_agent"] = True

    with pytest.raises(EmissionError):
        read_document(tampered)


def test_a_document_that_is_not_json_is_refused() -> None:
    with pytest.raises(EmissionError, match="not JSON"):
        read_document("{not json at all")


# ================================ AC 3: the reset method is proved, not asserted


def test_emission_requires_a_verified_reset_rather_than_a_strategy_name() -> None:
    """The structural half. `Workload.reset_method` is a name any caller can
    write; `VerifiedReset` is a type whose constructor refuses a failed report,
    and there is no way to emit while holding only the name."""
    parameters = inspect.signature(emit).parameters

    assert parameters["reset"].annotation == "VerifiedReset"
    assert parameters["reset"].default is inspect.Parameter.empty


def test_a_reset_that_did_not_verify_cannot_even_be_constructed() -> None:
    """S-2.7's guarantee, re-attempted from this story's side: there is no
    unverified proof to offer `emit` in the first place."""
    failed = VerificationReport(
        strategy=ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        cycles=3,
        drift=(
            Drift(cycle=2, kind="sequence", subject="shop_book_id_seq", expected="1", found="4"),
        ),
    )

    with pytest.raises(VerificationError):
        VerifiedReset(mechanism=RollbackDoNothingReset(), report=failed)


def test_a_proof_of_a_different_strategy_is_refused() -> None:
    """Verification is a property of a strategy *on a project*, not of a
    strategy — S-0.5 had rollback alone pass its own check and fail 10/10 on
    sequences. A proof of the wrong one proves nothing about this workload."""
    verification = Verification(
        workload=workload(reset_method=ResetStrategy.SNAPSHOT_RESTORE), drives=()
    )

    with pytest.raises(EmissionError, match="proves nothing about this workload"):
        emit(verification, reset=proof(ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES))


def test_a_document_whose_reset_strategy_contradicts_its_workload_is_refused() -> None:
    honest = emit(Verification(workload=workload(), drives=()), reset=proof())
    tampered = dict(honest.document())
    tampered["reset_strategy"] = ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES.value

    with pytest.raises(EmissionError, match="records a guarantee nobody obtained"):
        read_document(tampered)


def test_the_cycle_count_is_carried_because_verified_is_not_a_property_of_a_strategy() -> None:
    emission = emit(Verification(workload=workload(), drives=()), reset=proof(cycles=7))

    assert emission.reset_cycles == 7
    assert "7 cycle(s)" in emission.describe()


def test_a_document_claiming_no_cycles_is_refused() -> None:
    """Verified over zero cycles is not verified."""
    with pytest.raises(ValueError, match="reset_cycles"):
        emitted(reset_cycles=0)


# ============================================================ end to end


@pytest.mark.slow
def test_a_real_subject_is_driven_verified_and_emitted(tmp_path: Path) -> None:
    """The whole epic in one call: seed, drive, verify, prove the reset, emit.

    Built from hand-made observations everywhere else in this file, so this is
    the test that establishes the parts fit — the failure `CLAUDE.md` records as
    the one that mattered most.
    """
    root = write_project(tmp_path)
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")

    verification = verify_work(
        root,
        python=[sys.executable],
        path="/books/",
        target="shop.Book",
        workload_id="books-list",
        description="the book list endpoint",
        reset=ResetStrategy.SNAPSHOT_RESTORE,
        reset_between=[sys.executable, "manage.py", "flush", "--no-input"],
        repeats=3,
    )

    emission = emit(verification, reset=proof())
    reloaded = read_document(json.dumps(emission.document()))

    assert reloaded.work_verified
    assert reloaded.workload.fixture.entity == "shop.Book"
    assert [o.scale for o in reloaded.workload.observations] == [10, 100]
    assert "Verified" in reloaded.evidence_of_work

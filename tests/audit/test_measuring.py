"""Before and after, and the two ways it can be silently useless.

S-17.14, the last of the six. The audit's two numeric attacks are handed results
rather than taking them, so this is where the results come from — and both failure
modes here produce an audit that runs cleanly and establishes nothing.

The numbers can come from **one session read twice**, in which case both revisions
report the same figures, `_read`'s tag check passes because the tags are whatever
the harness put on them, and every class comes back absent. Or the envelope can be
sampled in **this** process, in which case `audit_trades` compares two readings of
an idle interpreter and finds no trade anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from coldfix.audit import measuring
from coldfix.audit.cheating import Metrics, Revision
from coldfix.audit.measuring import MeasuringError, measurer_for, reading_of, sample_of
from coldfix.explorer.surface import HostSurface
from coldfix.explorer.work import Drive, drive
from coldfix.orchestrator.adapters import Measurer
from coldfix.primitives.envelope import ENVELOPE, EnvelopeSample
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import CostClaim, Guard

QUERIES = "db.query"


def claim_of() -> CostClaim:
    return CostClaim(
        metric=QUERIES,
        baseline=41.0,
        at_most=2.0,
        guards=(Guard(metric="response_bytes", baseline=2000.0, at_most=3000.0),),
    )


def metrics_of() -> Metrics:
    return Metrics(cost=QUERIES, kinds={QUERIES: MetricKind.COUNT}, calls=QUERIES)


def a_drive(*, queries: float, warm: float | None = None, levels: int = 1000) -> Drive:
    """A drive with distinct numbers at every position, so a reader taking the
    wrong one fails on the value rather than on the shape."""
    passes = tuple(
        {"seconds": 0.01 * n, QUERIES: queries, "response_bytes": 100.0 * n} for n in (1, 2, 3)
    )
    return Drive(
        scale=40,
        queries=int(queries),
        response_bytes=300,
        seconds=0.02,
        samples=(0.01, 0.02, 0.03),
        warmup_seconds=0.5,
        status=200,
        created={"author": 40},
        passes=passes,
        warm_pass=(
            {} if warm is None else {"seconds": 0.5, QUERIES: warm, "response_bytes": 100.0}
        ),
        envelope_before={"allocated_blocks": float(levels), "peak_rss_bytes": 1e7},
        envelope_after={"allocated_blocks": float(levels * 2), "peak_rss_bytes": 2e7},
    )


class Session:
    """Stands in for a sandbox session. Only its worktree path is read."""

    def __init__(self, name: str) -> None:
        self.worktree = type("Worktree", (), {"path": Path(f"/{name}")})()


@pytest.fixture
def driven(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, Distribution]]:
    """Records every drive, and answers each with numbers keyed to its worktree.

    The original reports 41 queries and the patched 2 — an N+1 and its fix — so a
    measurer reading one session twice produces two identical readings and the
    assertion below fails on the numbers rather than on a call count.
    """
    seen: list[tuple[Path, Distribution]] = []

    def fake_synthesize(root: Path, **kwargs: Any) -> Any:
        seen.append((Path(root), kwargs["distribution"]))
        return type("Synthesized", (), {"created": {"author": kwargs["count"]}})()

    def fake_drive(root: Path, **kwargs: Any) -> Drive:
        original = str(root).endswith("original")
        return a_drive(queries=41.0 if original else 2.0, warm=45.0 if original else 3.0)

    monkeypatch.setattr("coldfix.audit.measuring.synthesize", fake_synthesize)
    monkeypatch.setattr("coldfix.audit.measuring.drive", fake_drive)
    return seen


def built(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "diagnostic": Session("original"),
        "python": ["python"],
        "path": "/books/",
        "entity": "author",
        "metrics": metrics_of(),
        "claim": claim_of(),
    }
    arguments.update(overrides)
    return measurer_for(**arguments)


# ================================ the property: two revisions, two sessions


def test_the_two_revisions_are_measured_on_different_sessions(
    driven: list[tuple[Path, Distribution]],
) -> None:
    """**AC 5, and the failure that produces a cleanly-passing useless audit.**

    A `Measure` reading one session twice returns identical numbers for both
    revisions. `cheating._read` cannot catch it — it checks that the reading is
    *tagged* with what was asked for, and the tags are whatever this module puts
    on them — so every class comes back `NOT_DETECTED` and the patch ships on a
    measurement that never distinguished it from the original.
    """
    measure = built()
    candidate = Session("patched")

    measurements = measure(object(), candidate=candidate)

    original = measurements.measure(Revision.ORIGINAL, Distribution.UNIFORM)
    patched = measurements.measure(Revision.PATCHED, Distribution.UNIFORM)

    assert original.first[QUERIES] == 45.0
    assert patched.first[QUERIES] == 3.0
    assert original.first[QUERIES] != patched.first[QUERIES], (
        "one session read twice would make these equal"
    )
    assert {str(root) for root, _ in driven} == {
        str(Path("/original")),
        str(Path("/patched")),
    }


def test_a_reading_is_tagged_with_what_was_asked_for(
    driven: list[tuple[Path, Distribution]],
) -> None:
    """`cheating._read` refuses a mismatch, so what is tested here is that this
    measurer honours the request rather than that the refusal exists."""
    measurements = built()(object(), candidate=Session("patched"))

    reading = measurements.measure(Revision.PATCHED, Distribution.LONG_TAIL)

    assert reading.revision is Revision.PATCHED
    assert reading.shape is Distribution.LONG_TAIL
    assert (Path("/patched"), Distribution.LONG_TAIL) in driven


def test_an_alternative_shape_reaches_the_seeder(
    driven: list[tuple[Path, Distribution]],
) -> None:
    """The cheat detector re-measures at other shapes to catch a patch tuned to
    one — so the shape has to reach the thing that builds the fixture."""
    measurements = built()(object(), candidate=Session("patched"))

    measurements.measure(Revision.ORIGINAL, Distribution.POWER_LAW)

    assert (Path("/original"), Distribution.POWER_LAW) in driven


# ============================== the cold pass is a measurement, not a duration


def test_the_cold_pass_carries_its_counters() -> None:
    """S-17.14's first finding. The warm-up ran outside `CaptureQueriesContext`,
    so `Reading.first` could only ever have been a duration — and `_cached_state`
    compares warm-up excess on whichever metric the patch claims to reduce, which
    for an N+1 is a count."""
    reading = reading_of(
        a_drive(queries=2.0, warm=45.0), revision=Revision.ORIGINAL, shape=Distribution.UNIFORM
    )

    assert reading.first[QUERIES] == 45.0
    assert reading.repeated[0][QUERIES] == 2.0
    assert len(reading.repeated) == 3


def test_a_drive_with_no_cold_pass_is_refused() -> None:
    """Under the old driver that was every drive. A reading built from the repeats
    alone answers the cached-state question with the warm-up folded into the thing
    it is supposed to be measured against."""
    with pytest.raises(MeasuringError, match="no cold pass"):
        reading_of(
            a_drive(queries=2.0, warm=None),
            revision=Revision.ORIGINAL,
            shape=Distribution.UNIFORM,
        )


# ========================================= the envelope is the subject's


def test_the_envelope_is_the_one_the_subject_reported(
    driven: list[tuple[Path, Distribution]],
) -> None:
    """**S-17.14's second finding.**

    `primitives.envelope` reads `RUSAGE_SELF`, this interpreter's allocated blocks
    and this process's thread count. Wrapped around a containerised drive it
    reports what the harness did while waiting, and `audit_trades` would compare
    two samples of the same idle interpreter and find every trade absent.
    """
    measurements = built()(object(), candidate=Session("patched"))

    assert measurements.envelope_before["allocated_blocks"] == 1000.0
    assert measurements.envelope_after["allocated_blocks"] == 2000.0
    assert measurements.envelope_before["peak_rss_bytes"] == 1e7


def test_the_harness_envelope_is_not_what_fills_the_measurements() -> None:
    """Asserted against the module rather than the numbers.

    The numbers above would agree with a harness-side sample on any machine where
    the two happened to be close, and *close* is exactly what an idle interpreter
    either side of a subprocess produces.
    """
    source = Path(measuring.__file__).read_text(encoding="utf-8")

    assert "envelope()" not in source
    assert "envelope_before" in source


def test_every_watched_metric_is_present_even_where_the_subject_could_not_say() -> None:
    """An absent key reads as *not watched*. A `None` with an `Availability`
    beside it reads as *looked for, and this platform cannot say*, which is what
    lets `trades` report a guard it could not evaluate rather than one that
    passed."""
    sample = sample_of({"allocated_blocks": 5.0})

    assert set(sample.metrics) == set(ENVELOPE)
    assert sample.metrics["allocated_blocks"] == 5.0
    assert sample.metrics["peak_rss_bytes"] is None
    assert "peak_rss_bytes" in sample.unavailable
    assert "allocated_blocks" not in sample.unavailable


def test_the_sample_is_the_type_the_trade_audit_takes() -> None:
    assert isinstance(sample_of({}), EnvelopeSample)


# =========================================== AC 4: the field has a producer


def test_the_produced_callable_satisfies_the_measurer_protocol(
    driven: list[tuple[Path, Distribution]],
) -> None:
    """`Resources.measure` is typed `Measurer`, and this is what fills it."""
    # `built()` is typed `Any`, so this annotation is the assertion: mypy checks
    # the produced callable against the protocol, and a signature that drifted
    # would fail here rather than at the first real campaign.
    measure: Measurer = built()

    measurements = measure(object(), candidate=Session("patched"))  # type: ignore[arg-type]

    assert measurements.metrics.cost == QUERIES
    assert measurements.claim.metric == QUERIES
    assert measurements.shape is Distribution.UNIFORM


def test_what_is_supplied_is_not_measured(driven: list[tuple[Path, Distribution]]) -> None:
    """Only the adapter knows what its counters are called, and only the repair
    knows what the patch promised. Measuring either here would be this module
    deciding what the patch was for."""
    alternatives = (Distribution.LONG_TAIL, Distribution.POWER_LAW)

    measurements = built(alternatives=alternatives)(object(), candidate=Session("patched"))

    assert measurements.alternatives == alternatives
    assert measurements.metrics is not None
    assert measurements.domain_before[QUERIES] == 41.0
    assert measurements.domain_after[QUERIES] == 2.0


# ================================== the driver change, against a real subject


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

from shop.models import Author, Book


def books(request):
    if not Book.objects.exists():
        author = Author.objects.create(name="a")
        Book.objects.bulk_create([Book(title=str(n), author=author) for n in range(5)])
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


urlpatterns = [path("books/", books)]
"""


@pytest.fixture(scope="module")
def subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("measured")
    # `drive` reads DJANGO_SETTINGS_MODULE out of manage.py — without it the run
    # is refused before any command reaches the subject, which is the same trap
    # S-17.9's composition test fell into.
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config").mkdir()
    (root / "shop" / "migrations").mkdir(parents=True)
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")

    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"}
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


@pytest.mark.slow
def test_the_subject_reports_every_pass_and_its_own_levels(subject: Path) -> None:
    """**Driven for real**, because the finding is about the injected program.

    The planted route issues one query for the books and one per book for its
    author — an N+1 — so a run that reported the cold pass without its counters
    would show `db.query` absent from `warm_pass` rather than merely low.
    """
    taken = drive(
        subject,
        python=[sys.executable],
        path="/books/",
        scale=5,
        created={"shop.Book": 5},
        repeats=2,
        surface=HostSurface(subject),
    )

    assert taken.warm_pass, "the cold pass was measured, not merely timed"
    assert taken.warm_pass[QUERIES] > 0, "and its queries were captured"
    assert len(taken.passes) == 2
    assert all(measured[QUERIES] > 0 for measured in taken.passes)
    assert taken.envelope_before and taken.envelope_after
    assert "allocated_blocks" in taken.envelope_after


@pytest.mark.slow
def test_the_reported_levels_are_the_subjects_own(subject: Path) -> None:
    """The subject is a different interpreter, so its allocated-block count is a
    different number from this one's. Equal counts would mean the harness sampled
    itself."""
    mine = sys.getallocatedblocks()
    taken = drive(
        subject,
        python=[sys.executable],
        path="/books/",
        scale=5,
        created={"shop.Book": 5},
        repeats=1,
        surface=HostSurface(subject),
    )

    assert taken.envelope_after["allocated_blocks"] != float(mine)

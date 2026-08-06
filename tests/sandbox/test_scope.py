"""Concurrency and dependency findings cannot reach repair; unsupported areas are reported.

Three refusals of two different kinds, and keeping them different is what these
tests mostly check.

Concurrency and third-party are **safety** refusals and are enforced
structurally: `RepairableFinding` cannot be constructed from either, so the
repair path has nothing to accept. Unsupported project types are a **capability**
boundary — `00-BRIEF.md` lists them as *not covered* rather than *refused on
principle* — so they are reported and the rest of the repository proceeds. A
test asserts that difference directly, because collapsing the two would either
make the system refuse repositories it can help with, or make it patch races it
cannot verify.

The controls matter as much as they did in S-2.8. A Django project with a
`package.json` for building CSS is not a frontend, and a mechanism describing
*blocking I/O*, a *for block* or a drifting *clock* must not be caught by a
substring search for "lock".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coldfix.sandbox.scope import (
    AreaFinding,
    DiagnoseOnlyError,
    DiagnoseOnlyReason,
    Disposition,
    RepairableFinding,
    UnsupportedArea,
    classify,
    report_scope,
    third_party_reason,
)

REPO = Path("/srv/subject")


# Sites come from stack frames taken inside a Linux container, so they are POSIX
# even when these tests run on Windows.


def verdict_for(mechanism: str, site: str = "app/views.py") -> object:
    return classify(mechanism, site, repository=REPO)


# ------------------------------------------- concurrency: diagnose, never patch


@pytest.mark.parametrize(
    "mechanism",
    [
        "the endpoint serializes on a lock held across the whole request",
        "row-level locks are taken in inconsistent order",
        "a deadlock between the two workers",
        "contention on the shared mutex",
        "the semaphore limits throughput to one caller",
        "a race condition between the reader and the writer",
        "time spent in the critical section dominates",
        "synchronization overhead on every call",
        "the GIL serializes the parsing loop",
        "threading.Lock is acquired per row",
        "select_for_update blocks the second transaction",
        "SELECT * FROM ticket FOR UPDATE holds the row",
        "LOCK TABLE is issued during the import",
        "the isolation level forces a retry",
    ],
)
def test_a_concurrency_finding_is_diagnose_only(mechanism: str) -> None:
    """ADR 007: output equivalence cannot detect an introduced race.

    A patch that moves a lock can pass every test on every run and still be
    wrong under a scheduling order the suite never produced.
    """
    result = classify(mechanism, "app/views.py", repository=REPO)

    assert result.disposition is Disposition.DIAGNOSE_ONLY
    assert DiagnoseOnlyReason.CONCURRENCY in result.reasons


def test_a_concurrency_finding_cannot_be_offered_to_the_repair_path() -> None:
    """AC 1, structurally. Not a rejected route — an absent one."""
    with pytest.raises(DiagnoseOnlyError) as raised:
        RepairableFinding(
            mechanism="the endpoint serializes on a lock",
            site="app/views.py",
            repository=REPO,
        )

    assert DiagnoseOnlyReason.CONCURRENCY in raised.value.verdict.reasons


def test_the_concurrency_refusal_explains_what_output_equivalence_cannot_do() -> None:
    """A refusal read by somebody who wants the fix has to say why they cannot have it."""
    with pytest.raises(DiagnoseOnlyError) as raised:
        RepairableFinding(mechanism="lock contention", site="app/views.py", repository=REPO)

    message = str(raised.value)
    assert "output equivalence" in message
    assert "introduced race" in message
    assert "diagnosis is still worth having" in message


@pytest.mark.parametrize(
    "mechanism",
    [
        "blocking I/O on the request thread",
        "the template renders inside a for block",
        "the clock drifts between samples",
        "unblocking the queue consumer",
        "the serializer builds a block of JSON per row",
        "N+1 queries when rendering the ticket list",
        "the ORM re-fetches the related rows for every item",
    ],
)
def test_ordinary_performance_vocabulary_is_still_repairable(mechanism: str) -> None:
    """The control, and the reason `\\block\\b` is written that way.

    A substring search for "lock" catches *blocking*, *block* and *clock*.
    Marking those diagnose-only would decline most of the fixes this system
    exists to make, which is the S-2.8 mistake in a new place.

    "deadlock-free" is deliberately *not* in this list. It reads like a
    control and is not one: an algorithm chosen for deadlock-freedom is a
    concurrency change, and refusing to patch it is correct.
    """
    assert classify(mechanism, "app/views.py", repository=REPO).repairable, mechanism


# ------------------------------------ third-party: report, never patch


@pytest.mark.parametrize(
    "site",
    [
        ".venv/lib/python3.12/site-packages/rest_framework/serializers.py",
        "node_modules/lodash/index.js",
        "vendor/github.com/lib/pq/conn.go",
        "third_party/protobuf/message.py",
        "venv/lib/site-packages/django/db/models/query.py",
    ],
)
def test_a_cause_inside_a_dependency_is_diagnose_only(site: str) -> None:
    result = classify("the serializer re-fetches per row", site, repository=REPO)

    assert result.disposition is Disposition.DIAGNOSE_ONLY
    assert DiagnoseOnlyReason.THIRD_PARTY in result.reasons


def test_a_cause_outside_the_repository_is_diagnose_only() -> None:
    """A stack frame can localize into the standard library or an absolute path.

    Neither is the user's to change, and neither sits in a directory whose name
    gives it away.
    """
    reason = third_party_reason("/usr/lib/python3.12/json/decoder.py", REPO)

    assert reason is not None
    assert "outside the repository" in reason


def test_a_cause_inside_the_repository_is_repairable() -> None:
    assert third_party_reason("app/views.py", REPO) is None
    assert classify("the serializer re-fetches per row", "app/views.py", repository=REPO).repairable


def test_the_third_party_refusal_says_the_package_manager_will_overwrite_it() -> None:
    with pytest.raises(DiagnoseOnlyError) as raised:
        RepairableFinding(
            mechanism="the serializer re-fetches per row",
            site="node_modules/orm/query.js",
            repository=REPO,
        )

    message = str(raised.value)
    assert "package manager will overwrite" in message
    assert "The finding is the deliverable" in message


def test_both_reasons_are_reported_when_both_apply() -> None:
    """A locking bug inside a dependency is refused twice over, and says so."""
    result = classify(
        "lock contention inside the pool",
        "node_modules/pool/index.js",
        repository=REPO,
    )

    assert set(result.reasons) == {
        DiagnoseOnlyReason.CONCURRENCY,
        DiagnoseOnlyReason.THIRD_PARTY,
    }
    assert len(result.evidence) == 2


def test_an_in_scope_finding_reaches_the_repair_path() -> None:
    """The gate has to let real findings through, or it is only a refusal."""
    finding = RepairableFinding(
        mechanism="the serializer issues one query per row",
        site="app/serializers.py",
        repository=REPO,
    )

    assert finding.verdict.repairable
    assert finding.verdict.explanation() == "This finding is in scope for repair."


# --------------------------- unsupported areas: report, and do not refuse


def write_package_json(root: Path, dependencies: dict[str, str]) -> None:
    (root / "package.json").write_text(
        json.dumps({"name": "subject", "devDependencies": dependencies}), encoding="utf-8"
    )


def test_a_frontend_is_reported(tmp_path: Path) -> None:
    write_package_json(tmp_path, {"react": "^18.0.0", "vite": "^5"})

    report = report_scope(tmp_path)

    assert not report.fully_supported
    assert UnsupportedArea.FRONTEND in {item.area for item in report.areas}


def test_a_django_project_that_builds_css_with_npm_is_not_a_frontend(tmp_path: Path) -> None:
    """The control, and the one that decides whether this check is usable.

    Half the Django projects in existence have a package.json for Tailwind or
    esbuild. Treating the manifest itself as evidence would report a frontend in
    most of this system's own target population.
    """
    write_package_json(tmp_path, {"tailwindcss": "^3", "esbuild": "^0.20", "webpack": "^5"})
    (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8")

    assert report_scope(tmp_path).fully_supported


def test_a_frontend_dependency_of_the_project_is_not_the_project(tmp_path: Path) -> None:
    """React inside `node_modules` is something the project uses, not something it is."""
    nested = tmp_path / "node_modules" / "some-package"
    nested.mkdir(parents=True)
    write_package_json(nested, {"react": "^18"})

    assert report_scope(tmp_path).fully_supported


@pytest.mark.parametrize(
    ("filename", "area"),
    [
        ("AndroidManifest.xml", UnsupportedArea.MOBILE),
        ("Podfile", UnsupportedArea.MOBILE),
        ("pubspec.yaml", UnsupportedArea.MOBILE),
        ("platformio.ini", UnsupportedArea.EMBEDDED),
        ("blink.ino", UnsupportedArea.EMBEDDED),
        ("PAYROLL.cbl", UnsupportedArea.MAINFRAME),
        ("RUNJOB.jcl", UnsupportedArea.MAINFRAME),
        ("Game.uproject", UnsupportedArea.GAME_ENGINE),
        ("project.godot", UnsupportedArea.GAME_ENGINE),
        ("MainWindow.xaml", UnsupportedArea.DESKTOP_GUI),
        ("Kbuild", UnsupportedArea.KERNEL),
    ],
)
def test_each_unsupported_area_is_detected(
    tmp_path: Path, filename: str, area: UnsupportedArea
) -> None:
    (tmp_path / filename).write_text("", encoding="utf-8")

    assert area in {item.area for item in report_scope(tmp_path).areas}


def test_an_unsupported_area_is_reported_and_not_refused(tmp_path: Path) -> None:
    """AC 3, and the distinction the whole module is arranged around.

    `00-BRIEF.md` separates *refused on principle* — four categories where no
    verifier makes a change safe — from *not covered*, a capability boundary.
    These are the second kind, so `report_scope` returns rather than raising: a
    Django backend with a React frontend is a perfectly good subject for its
    backend, and refusing the repository would decline work this system can do.
    """
    write_package_json(tmp_path, {"react": "^18"})
    (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8")

    report = report_scope(tmp_path)

    assert not report.fully_supported
    message = report.explanation()
    assert "capability boundary rather than a" in message
    assert "the rest of the repository is analysed normally" in message


def test_a_supported_project_reports_nothing(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "views.py").write_text("def index(): ...\n", encoding="utf-8")

    report = report_scope(tmp_path)

    assert report.fully_supported
    assert "Nothing in" in report.explanation()


def test_each_area_is_reported_once(tmp_path: Path) -> None:
    """Ten COBOL files are one out-of-scope area, not ten."""
    for index in range(5):
        (tmp_path / f"PROG{index}.cbl").write_text("", encoding="utf-8")

    areas = [item.area for item in report_scope(tmp_path).areas]

    assert areas.count(UnsupportedArea.MAINFRAME) == 1


def test_the_report_names_the_evidence(tmp_path: Path) -> None:
    """ "Out of scope" without "because of this file" is not actionable."""
    (tmp_path / "AndroidManifest.xml").write_text("", encoding="utf-8")

    report = report_scope(tmp_path)

    assert "AndroidManifest.xml" in report.explanation()


def test_an_area_finding_renders_its_evidence() -> None:
    finding = AreaFinding(UnsupportedArea.MOBILE, "an Android manifest at app/AndroidManifest.xml")

    assert "mobile application code" in str(finding)
    assert "AndroidManifest.xml" in str(finding)

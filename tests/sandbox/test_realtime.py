"""The screening refuses a flight controller and clears a task tracker.

Two tests carry this file. `test_the_planted_real_time_repository_is_refused`
proves the detector works; `test_the_control_repository_is_cleared` proves it
discriminates, and the second is the harder and more valuable claim.

ADR 006: every defect carries a control, or the detector learns to say yes. The
control here is a task tracker with deadlines, priorities, a class named
`Scheduler`, real-time updates and mission-critical work — every word a naive
real-time detector fires on, in exactly the kind of application this tool exists
to make faster. The pinned development target is a helpdesk full of the same
vocabulary, so a screening that refuses the control refuses its own target on
day one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coldfix.sandbox import realtime
from coldfix.sandbox.realtime import (
    CONTENT_MARKERS,
    MAX_FILES_SCANNED,
    IncompleteScreeningError,
    MarkerCategory,
    RealTimeSystemError,
    ScreenedRepository,
    screen,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "realtime"
REAL_TIME = FIXTURES / "flight_controller"
CONTROL = FIXTURES / "task_tracker"


# --------------------------------------------------------- the two subjects


def test_the_planted_real_time_repository_is_refused() -> None:
    """AC 4. A fixture with real-time markers is refused."""
    with pytest.raises(RealTimeSystemError) as raised:
        ScreenedRepository(root=REAL_TIME)

    assert raised.value.screening.detections


def test_the_control_repository_is_cleared() -> None:
    """The harder claim, and the one that keeps this tool usable.

    Deadlines, priorities, a `Scheduler`, real-time updates, mission-critical
    work — and none of it a timing guarantee. If this fails, the detector has
    learned to say yes and would refuse the helpdesk application pinned as the
    development target.
    """
    screened = ScreenedRepository(root=CONTROL)

    assert screened.screening.clear
    assert screened.screening.detections == ()


def test_the_control_really_does_contain_the_tempting_words() -> None:
    """A control that lacks the trap words proves nothing.

    This asserts the fixture is still doing its job, because the way that test
    above stops being meaningful is somebody tidying the vocabulary out of the
    control rather than the detector changing.
    """
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in CONTROL.rglob("*")
        if path.is_file() and path.suffix in {".py", ".md"}
    ).lower()

    for tempting in ("deadline", "hard deadline", "priority", "critical", "scheduler", "real-time"):
        assert tempting in text, f"the control no longer contains {tempting!r}"


# ------------------------------------------------- all four marker categories


def test_all_four_categories_are_detected() -> None:
    """AC 1 names four kinds of evidence, and the fixture plants all four."""
    result = screen(REAL_TIME)

    assert set(result.categories) == {
        MarkerCategory.RTOS,
        MarkerCategory.DEADLINE,
        MarkerCategory.CERTIFICATION,
        MarkerCategory.FRAMEWORK,
    }


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ('#include "FreeRTOS.h"', MarkerCategory.RTOS),
        ("#include <zephyr/kernel.h>", MarkerCategory.RTOS),
        ("from vxWorks import taskSpawn", MarkerCategory.RTOS),
        ("this runs on a bare-metal RTOS", MarkerCategory.RTOS),
        ("sched_setscheduler(0, SCHED_FIFO, &p);", MarkerCategory.DEADLINE),
        ("policy = SCHED_DEADLINE", MarkerCategory.DEADLINE),
        ("# WCET budget is 400us", MarkerCategory.DEADLINE),
        ("worst-case execution time analysis", MarkerCategory.DEADLINE),
        ("@deadline(microseconds=250)", MarkerCategory.DEADLINE),
        ("certified to DO-178C", MarkerCategory.CERTIFICATION),
        ("IEC 61508 SIL-3", MarkerCategory.CERTIFICATION),
        ("assessed ASIL-D under ISO 26262", MarkerCategory.CERTIFICATION),
        ("MISRA C:2012 rule 8.7", MarkerCategory.CERTIFICATION),
        ("kernel built with PREEMPT_RT", MarkerCategory.FRAMEWORK),
        ("run under Xenomai", MarkerCategory.FRAMEWORK),
        ("measured with cyclictest", MarkerCategory.FRAMEWORK),
    ],
)
def test_each_marker_is_detected(tmp_path: Path, content: str, category: MarkerCategory) -> None:
    (tmp_path / "subject.txt").write_text(content, encoding="utf-8")

    result = screen(tmp_path)

    assert category in result.categories, content


@pytest.mark.parametrize(
    "filename",
    ["FreeRTOSConfig.h", "platformio.ini", "prj.conf", "blink.ino"],
)
def test_a_filename_alone_can_be_the_evidence(tmp_path: Path, filename: str) -> None:
    """Some signatures are the presence of a file, not anything written in one."""
    (tmp_path / filename).write_text("", encoding="utf-8")

    assert screen(tmp_path).detections


# ------------------------------------------- the words that must not fire


@pytest.mark.parametrize(
    "content",
    [
        "deadline = models.DateTimeField(null=True)",
        "the deadline for this ticket is Friday",
        "we agreed a hard deadline with the customer",
        "class Scheduler:  # orders the dashboard",
        "priority = Priority.CRITICAL",
        "this is mission critical work",
        "real-time updates over websockets",
        "realtime = True",
        "safety of the migration was reviewed",
        "the silicon vendor shipped it",
        "a silent failure in the worker",
        "SILENCE_DEPRECATION = True",
        "latest = Task.objects.latest('created')",
        "critical_path = compute_critical_path(tasks)",
    ],
)
def test_ordinary_application_vocabulary_is_not_refused(tmp_path: Path, content: str) -> None:
    """The failure mode that would make this tool useless.

    `silicon`, `silent` and `SILENCE` are here because a `SIL` pattern without
    its integrity number matches all three. `latest` is here for the same reason
    it is in the protected-path tests: substring matching is how a checker
    quietly starts refusing everything.
    """
    (tmp_path / "app.py").write_text(content, encoding="utf-8")

    assert screen(tmp_path).clear, content


def test_no_marker_fires_on_this_projects_own_source() -> None:
    """`coldfix/bench` is ordinary Python and must screen clean.

    Deliberately not the whole repository: `realtime.py` holds every pattern as
    a literal and the fixtures plant markers on purpose, so this project as a
    whole would — correctly — refuse itself.
    """
    bench = Path(__file__).resolve().parents[2] / "src" / "coldfix" / "bench"

    assert screen(bench).clear


# --------------------------------------------------------- the refusal itself


def test_the_refusal_explains_why_in_one_paragraph() -> None:
    """AC 2, and the explanation has to carry the reason that matters.

    Not "unsupported" — the specific claim that a caching optimisation improves
    every metric measured here while degrading worst-case timing, which is why
    this is a refusal rather than a caveat.
    """
    with pytest.raises(RealTimeSystemError) as raised:
        ScreenedRepository(root=REAL_TIME)

    message = str(raised.value)
    assert "worst-case execution time" in message
    assert "caching optimisation improves every metric" in message
    assert "less safe" in message
    assert "before anything is grounded" in message


def test_the_refusal_shows_what_it_matched(tmp_path: Path) -> None:
    """A refusal nobody can audit is one that gets worked around."""
    (tmp_path / "rtos.c").write_text('#include "FreeRTOS.h"\n', encoding="utf-8")

    with pytest.raises(RealTimeSystemError) as raised:
        ScreenedRepository(root=tmp_path)

    message = str(raised.value)
    assert "rtos.c:1" in message
    assert "FreeRTOS" in message


# ------------------------------------------------------- ordering and bounds


def test_there_is_no_unscreened_repository_to_ground(tmp_path: Path) -> None:
    """AC 3. The ordering is a type, not a rule about call sequence.

    Constructing the object grounding will require *is* the screening, so there
    is no path by which grounding happens first — the same construction as
    `VerifiedDatabase`.
    """
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")

    screened = ScreenedRepository(root=tmp_path)

    assert screened.screening.clear
    assert screened.root == tmp_path.resolve()


def test_an_unfinished_scan_is_not_a_clear_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Nothing found" and "we stopped looking" must not be the same answer.

    For a check whose failure mode is degrading a safety-critical system while
    reporting success, an incomplete search reporting clean is the worst
    available outcome.
    """
    monkeypatch.setattr(realtime, "MAX_FILES_SCANNED", 2)
    for index in range(5):
        (tmp_path / f"file{index}.py").write_text("x = 1\n", encoding="utf-8")

    result = realtime.screen(tmp_path)

    assert result.truncated
    assert result.detections == ()
    assert not result.clear

    with pytest.raises(IncompleteScreeningError, match="did not finish"):
        realtime.ScreenedRepository(root=tmp_path)


def test_binary_files_are_not_scanned(tmp_path: Path) -> None:
    """A compiled artifact can contain any byte sequence, including these ones."""
    (tmp_path / "firmware.bin").write_bytes(b"\x00\x01FreeRTOS\x00\xff")

    assert screen(tmp_path).clear


def test_generated_trees_are_skipped_and_vendored_ones_are_not(tmp_path: Path) -> None:
    """A vendored RTOS is the thing being looked for; `node_modules` is not.

    Third-party code being unpatchable (S-2.9) does not make it undetectable,
    which is why `vendor` and `third_party` are deliberately absent from the
    skip list.
    """
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("// FreeRTOS\n", encoding="utf-8")
    assert screen(tmp_path).clear

    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party" / "os.c").write_text('#include "FreeRTOS.h"\n', encoding="utf-8")
    assert not screen(tmp_path).clear


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IncompleteScreeningError, match="not a directory"):
        ScreenedRepository(root=tmp_path / "absent")


def test_every_marker_has_a_category_and_a_name() -> None:
    """The diagnostic is only auditable if each pattern says what it is."""
    for marker in CONTENT_MARKERS:
        assert marker.name
        assert isinstance(marker.category, MarkerCategory)


def test_the_file_cap_is_generous_enough_for_a_real_repository() -> None:
    """A bound that fires on ordinary repositories is a bound that gets removed."""
    assert MAX_FILES_SCANNED >= 50_000

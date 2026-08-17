"""S-6.4 — what a shipped patch invalidates, and what it leaves standing.

F14 and §6's interacting-findings flaw are the same staleness question asked
about two artifacts, so most of these exercise one mechanism and then check the
two consequences differ in the right way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coldfix.state.staleness import (
    Assessment,
    Coverage,
    FindingAction,
    Freshness,
    Patch,
    ScreeningAction,
    StalenessError,
    UnusablePathError,
    after_ship,
    assess,
    finding_plan,
    repo_path,
    screening_plan,
)

PATCH = Patch.of("F1", ["src/api/serializers.py"])


# ============================== AC 1 and 2: touched invalidated, untouched kept


def test_a_workload_running_a_modified_file_is_invalidated() -> None:
    touched = Coverage.of("api.books.list", ["src/api/serializers.py", "src/api/views.py"])

    verdict = assess(touched, PATCH)

    assert verdict.freshness is Freshness.STALE
    assert verdict.screening_action is ScreeningAction.SCREEN_AGAIN
    assert verdict.overlap == {"src/api/serializers.py"}


def test_a_workload_touching_none_of_the_modified_files_keeps_its_measurements() -> None:
    """AC 2, and F14's whole point: re-screening everything is the wasteful
    answer and re-screening nothing is the wrong one."""
    untouched = Coverage.of("api.authors.list", ["src/api/authors.py"])

    verdict = assess(untouched, PATCH)

    assert verdict.freshness is Freshness.FRESH
    assert verdict.screening_action is ScreeningAction.KEEP
    assert verdict.overlap == frozenset()


def test_a_screen_splits_into_kept_and_re_screened() -> None:
    plan = screening_plan(
        [
            Coverage.of("api.books.list", ["src/api/serializers.py"]),
            Coverage.of("api.authors.list", ["src/api/authors.py"]),
            Coverage.of("api.tags.list", ["src/api/tags.py", "src/api/serializers.py"]),
        ],
        PATCH,
    )

    assert plan == {
        "api.books.list": ScreeningAction.SCREEN_AGAIN,
        "api.authors.list": ScreeningAction.KEEP,
        "api.tags.list": ScreeningAction.SCREEN_AGAIN,
    }


def test_the_report_names_which_file_made_a_workload_stale() -> None:
    """A workload invalidated without saying why is one nobody can check.

    Asserted on the workload's own line rather than on the whole report: the
    report's header also lists the patch's modified files, so a search of the
    full text passes whether or not the per-workload line says anything. Found
    by a sabotage that removed the overlap and left every test passing.
    """
    stale = Coverage.of("api.books.list", ["src/api/serializers.py", "src/api/views.py"])

    line = assess(stale, PATCH).describe()

    assert line.startswith("api.books.list:")
    assert "src/api/serializers.py" in line
    # Only the overlap, not everything the workload runs — a line naming every
    # file it touched would not say which one the patch moved.
    assert "src/api/views.py" not in line
    assert line in after_ship([stale], PATCH).describe()


# ==================================== AC 3: a stale finding is re-investigated


def test_a_finding_whose_context_moved_is_re_investigated() -> None:
    """§6: the second patch is written against pre-first-patch source, and we
    re-probe between fixes but never re-derive the evidence chain."""
    pending = Coverage.of("F2", ["src/api/serializers.py"])

    assert assess(pending, PATCH).finding_action is FindingAction.REINVESTIGATE


def test_a_finding_whose_context_is_untouched_may_still_be_repaired() -> None:
    """The control. Without it the policy would pass for one that re-investigated
    every pending finding after every ship, which is the expensive wrong answer."""
    pending = Coverage.of("F2", ["src/api/authors.py"])

    assert assess(pending, PATCH).finding_action is FindingAction.REPAIR


def test_there_is_no_way_to_repair_from_a_stale_chain() -> None:
    """The adversarial form: §6's rule as a property of the type.

    Enumerated over `Freshness` rather than spot-checked, so a state added later
    has to decide what it means here instead of defaulting to repairable.
    """
    for freshness in Freshness:
        verdict = Assessment("F2", freshness, frozenset())
        if freshness is Freshness.FRESH:
            assert verdict.finding_action is FindingAction.REPAIR
        else:
            assert verdict.finding_action is FindingAction.REINVESTIGATE

    assert set(FindingAction) == {FindingAction.REPAIR, FindingAction.REINVESTIGATE}


def test_the_shipped_finding_is_not_in_the_pending_plan() -> None:
    """It shipped. What the policy decides is what happens to the ones waiting."""
    plan = finding_plan(
        [Coverage.of("F1", ["src/api/serializers.py"]), Coverage.of("F2", ["src/api/tags.py"])],
        PATCH,
    )

    assert "F1" not in plan
    assert plan == {"F2": FindingAction.REPAIR}


# ================================= unrecorded is not untouched


def test_a_subject_with_no_coverage_record_is_invalidated() -> None:
    """The load-bearing decision.

    Nothing today records which files a workload runs — S-4.1's `Workload` has an
    entry point and a fixture and no notion of source. Flattening that absence to
    "untouched" keeps a measurement the patch may have invalidated, which is
    exactly how the Surgeon ends up repairing from a stale chain.
    """
    verdict = assess(Coverage.unrecorded("api.books.list"), PATCH)

    assert verdict.freshness is Freshness.UNCOVERED
    assert verdict.screening_action is ScreeningAction.SCREEN_AGAIN
    assert verdict.finding_action is FindingAction.REINVESTIGATE


def test_uncovered_is_reported_apart_from_stale() -> None:
    """Same action, different fix — one needs re-screening, the other needs
    somebody to record what the workloads run. One number would hide the second.
    """
    report = after_ship(
        [
            Coverage.of("api.books.list", ["src/api/serializers.py"]),
            Coverage.unrecorded("api.tags.list"),
            Coverage.of("api.authors.list", ["src/api/authors.py"]),
        ],
        PATCH,
    )

    assert report.invalidated == ("api.books.list", "api.tags.list")
    assert report.uncovered == ("api.tags.list",)
    assert report.retained == ("api.authors.list",)
    assert "for want of a coverage record" in report.describe()


def test_touching_nothing_is_a_claim_and_recording_nothing_is_not() -> None:
    """An empty set says the subject runs no modified file; `None` says nobody
    looked. Collapsing them is the defect this distinction exists to prevent."""
    assert assess(Coverage.of("w", []), PATCH).freshness is Freshness.FRESH
    assert assess(Coverage.unrecorded("w"), PATCH).freshness is Freshness.UNCOVERED


# ============================================== paths compare in one form only


def test_paths_from_git_and_from_a_stack_frame_compare_equal(tmp_path: Path) -> None:
    """The silent failure this normalization exists to prevent.

    Git reports repo-relative forward-slash paths; a stack frame reports an
    absolute one, on Windows with backslashes. Intersecting the two forms
    directly is the empty set — every workload reads unaffected, every stale
    measurement is kept, and nothing raises.
    """
    repo = tmp_path / "subject"
    (repo / "src" / "api").mkdir(parents=True)
    frame = repo / "src" / "api" / "serializers.py"
    frame.write_text("")

    from_frame = Coverage.of("api.books.list", [frame], repo_root=repo)

    assert from_frame.files is not None
    assert from_frame.files == frozenset({"src/api/serializers.py"})
    assert assess(from_frame, PATCH).freshness is Freshness.STALE


def test_an_absolute_path_with_no_root_is_refused() -> None:
    """Refused rather than guessed, because the guess lands on the flattering
    side: no overlap, everything fresh, nothing said."""
    with pytest.raises(UnusablePathError, match="no repo root was given"):
        repo_path("/home/allen/subject/src/api/serializers.py")


def test_a_path_outside_the_repository_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "subject"
    repo.mkdir()
    outside = tmp_path / "elsewhere" / "models.py"
    outside.parent.mkdir()
    outside.write_text("")

    with pytest.raises(UnusablePathError, match="outside the repository"):
        repo_path(outside, repo_root=repo)


def test_separators_are_normalized_rather_than_compared_raw() -> None:
    assert repo_path(Path("src") / "api" / "serializers.py") == "src/api/serializers.py"
    assert repo_path("src/api/serializers.py") == "src/api/serializers.py"


def test_a_path_naming_no_file_is_refused() -> None:
    with pytest.raises(UnusablePathError, match="does not name a file"):
        repo_path(".")


# =========================================================== the patch itself


def test_a_patch_that_modified_nothing_is_refused() -> None:
    """A ship that changed no file invalidates nothing, and treating it as a
    ship would silently retire the finding it claims to have fixed."""
    with pytest.raises(StalenessError, match="modified no files"):
        Patch.of("F1", [])


def test_a_patch_normalizes_its_own_paths(tmp_path: Path) -> None:
    repo = tmp_path / "subject"
    (repo / "src").mkdir(parents=True)
    modified = repo / "src" / "views.py"
    modified.write_text("")

    patch = Patch.of("F1", [modified], repo_root=repo)

    assert patch.modified == {"src/views.py"}


def test_two_findings_in_one_file_is_the_case_section_six_is_about() -> None:
    """The worked example from §6, end to end.

    F1 and F2 are both in `serializers.py`. F1 ships; F2's chain was derived
    against the file as it was before, so repairing from it would write a patch
    against source that no longer exists.
    """
    plan = finding_plan(
        [
            Coverage.of("F2", ["src/api/serializers.py"]),
            Coverage.of("F3", ["src/api/authors.py"]),
        ],
        Patch.of("F1", ["src/api/serializers.py"]),
    )

    assert plan["F2"] is FindingAction.REINVESTIGATE
    assert plan["F3"] is FindingAction.REPAIR

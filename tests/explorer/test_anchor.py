"""S-7.12 — handing a repository the toolchain of its own era.

Most of this reads files and runs `git` against real checkouts built in the test.
AC 5 is different and is marked `index`: it asks for a repository whose unpinned
dependencies **resolve incorrectly at HEAD and correctly at its anchor**, and
there is no way to demonstrate that without a real package index. A fake resolver
would return whichever answer this file wrote into it, which is the one thing the
criterion is asking to be shown.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from coldfix.explorer.anchor import (
    AnchorError,
    Basis,
    anchor_for,
    interpreter_for,
    override,
    resolve,
)
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import EnvironmentAnchor, FixtureRecipe, Workload


def git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"git {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}")


def repository(
    root: Path,
    *,
    committed: str = "2019-03-04T11:22:33+00:00",
    authored: str | None = None,
) -> Path:
    """A checkout with one commit, whose two dates can be set independently.

    They default to differing, because a fixture that set both to the same value
    could not tell a committer date from an author date — and which one is read
    is a decision with a reason behind it.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("a repository\n", encoding="utf-8")
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    subprocess.run(
        ["git", "-C", str(root), "commit", "--quiet", "-m", "first"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": authored or "2015-01-02T03:04:05+00:00",
            "GIT_COMMITTER_DATE": committed,
        },
    )
    return root


# ============================================ AC 1: the anchor is the repository's


def test_the_anchor_is_the_date_of_the_most_recent_commit(tmp_path: Path) -> None:
    found = anchor_for(repository(tmp_path / "old"))

    assert found.on == date(2019, 3, 4)
    assert found.commit is not None
    assert not found.overridden


def test_the_anchor_moves_with_the_repository(tmp_path: Path) -> None:
    """The control. An anchor that returned the same date for every repository
    would pass the test above and defeat the entire story."""
    old = anchor_for(repository(tmp_path / "old", committed="2019-03-04T11:22:33+00:00"))
    newer = anchor_for(repository(tmp_path / "newer", committed="2024-11-30T09:00:00+00:00"))

    assert old.on == date(2019, 3, 4)
    assert newer.on == date(2024, 11, 30)


def test_the_committer_date_is_read_and_not_the_author_date(tmp_path: Path) -> None:
    """A patch written in 2015 and applied in 2019 was *resolved against* 2019's
    index by whoever applied it. The author date would anchor the environment to
    a day the code in this checkout never existed on.

    The fixture sets the two dates four years apart, because a fixture that set
    them to the same value would pass whichever field the code read.
    """
    found = anchor_for(
        repository(
            tmp_path / "old",
            authored="2015-01-02T03:04:05+00:00",
            committed="2019-03-04T11:22:33+00:00",
        )
    )

    assert found.on == date(2019, 3, 4)


def test_a_checkout_without_history_is_refused_rather_than_dated_today(
    tmp_path: Path,
) -> None:
    """A downloaded tarball is not a checkout. Defaulting to today would hand a
    2019 repository a 2026 toolchain, which is the failure this exists to
    prevent."""
    (tmp_path / "README.md").write_text("no git here\n", encoding="utf-8")

    with pytest.raises(AnchorError, match="no commit date"):
        anchor_for(tmp_path)


def test_the_anchor_names_where_it_came_from(tmp_path: Path) -> None:
    described = anchor_for(repository(tmp_path / "old")).describe()

    assert "2019-03-04" in described
    assert "commit" in described


def test_the_residue_travels_with_the_anchor(tmp_path: Path) -> None:
    """ADR 010's stated bound. *The environment is era-matched* is exactly the
    sentence somebody would quote, and an exclusion without its preconditions is
    the failure CLAUDE.md names."""
    residue = anchor_for(repository(tmp_path / "old")).residue

    assert "operating-system" in residue
    assert "S-17.2" in residue


# ======================================== the override, which ADR 010 requires


def test_an_override_is_recorded_as_an_override(tmp_path: Path) -> None:
    """A contemporary dependency may carry a since-fixed incompatibility or a
    known vulnerability, so the anchor is a default rather than a constraint."""
    forced = override(date(2021, 6, 1), "CVE-2019-19844 in the contemporary Django")

    assert forced.overridden
    assert forced.commit is None
    assert "CVE" in forced.describe()


def test_an_override_without_a_reason_is_refused() -> None:
    """*Why this run resolved against a different date* is the whole value of
    recording it."""
    with pytest.raises(AnchorError, match="needs a reason"):
        override(date(2021, 6, 1), "   ")


# ================================ AC 3: the interpreter the repository claims


def test_classifiers_enumerate_the_versions_the_project_supports(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nclassifiers = [\n'
        '  "Programming Language :: Python :: 3.6",\n'
        '  "Programming Language :: Python :: 3.8",\n'
        "]\n",
        encoding="utf-8",
    )

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.8"
    assert found.basis is Basis.ENUMERATED


def test_requires_python_is_a_floor_and_says_so(tmp_path: Path) -> None:
    """`>=3.8` claims 3.8 works and says nothing about which newer versions do.
    Presenting it as a version the project was tested on would be a different
    claim from the one it made."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.8"\n', encoding="utf-8"
    )

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.8"
    assert found.basis is Basis.FLOOR


def test_an_enumeration_beats_a_floor(tmp_path: Path) -> None:
    """A project whose CI tests 3.11 while declaring `>=3.6` has said two things,
    and the enumeration is the one that names a version it runs on."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.6"\n', encoding="utf-8"
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    strategy:\n      matrix:\n"
        '        python-version: ["3.9", "3.11"]\n'
        "    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.11"
    assert found.basis is Basis.ENUMERATED


def test_a_ci_matrix_does_not_pick_up_action_versions(tmp_path: Path) -> None:
    """A workflow is full of numbers — action versions, timeouts, ports — and a
    pattern loose enough to catch a matrix is loose enough to catch
    `actions/cache@v3.12` if it is not anchored to the key.

    **Every decoy here is a 3.x on purpose.** The first version of this test used
    `v4.2` and a `timeout-minutes: 4.5`, which the version filter discards for a
    different reason entirely — so removing the key check changed nothing and the
    test passed against exactly the code it was written to reject.
    """
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  test:\n    timeout-minutes: 3.5\n    steps:\n"
        "      - uses: actions/cache@v3.12\n"
        "      - uses: actions/checkout@v3.11\n    strategy:\n      matrix:\n"
        '        python-version: ["3.9"]\n',
        encoding="utf-8",
    )

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.9"


def test_a_tox_envlist_enumerates_versions(tmp_path: Path) -> None:
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py36,py37,py310\n", encoding="utf-8")

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.10"


def test_versions_are_compared_numerically_not_as_text(tmp_path: Path) -> None:
    """`"3.9" > "3.10"` is true for strings and false for Python, so a lexical
    maximum hands a project testing 3.9 through 3.12 the oldest of them."""
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py39,py310,py312\n", encoding="utf-8")

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.12"


def test_setup_cfg_python_requires_is_read(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text('[options]\npython_requires = ">=3.5"\n', encoding="utf-8")

    found = interpreter_for(tmp_path)

    assert found is not None
    assert found.version == "3.5"
    assert found.basis is Basis.FLOOR


def test_a_repository_declaring_nothing_claims_nothing(tmp_path: Path) -> None:
    """A project with no interpreter declaration has not implicitly claimed the
    newest one."""
    (tmp_path / "README.md").write_text("nothing here\n", encoding="utf-8")

    assert interpreter_for(tmp_path) is None


def test_every_declaration_found_is_reported(tmp_path: Path) -> None:
    """A project whose classifiers stop at 3.6 and whose tox tests 3.10 has said
    two things, and the older one is why the newer was chosen."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nclassifiers = ["Programming Language :: Python :: 3.6"]\n',
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py310\n", encoding="utf-8")

    found = interpreter_for(tmp_path)

    assert found is not None
    assert len(found.considered) == 2


# ==================== AC 4: the anchor, the set and the override are recorded


def test_the_artifact_records_a_derived_anchor() -> None:
    recorded = EnvironmentAnchor(
        anchor=date(2019, 3, 4),
        commit="abc123",
        reason="the repository's most recent commit",
        python_version="3.7",
        dependencies=("django==2.1.4", "pytz==2018.7"),
    )

    assert not recorded.overridden
    assert recorded.dependencies == ("django==2.1.4", "pytz==2018.7")


def test_the_artifact_records_an_override_as_one() -> None:
    """No separate flag: two fields that could disagree about whether an override
    happened would eventually disagree."""
    recorded = EnvironmentAnchor(
        anchor=date(2021, 6, 1), reason="CVE-2019-19844", dependencies=("django==3.2.4",)
    )

    assert recorded.overridden


def test_an_anchor_without_a_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="reason"):
        EnvironmentAnchor(anchor=date(2019, 3, 4), reason="")


def test_a_workload_carries_its_environment_and_defaults_to_none() -> None:
    """`None` means *not recorded*, never *resolved against today*. ADR 010's
    whole argument is that resolving against today is what breaks a 2019
    repository, so an absent anchor is a gap in the record rather than a
    default."""
    recipe = FixtureRecipe(
        entity="shop.Book", per_parent=1, distribution=Distribution.UNIFORM, source="synthesized"
    )
    plain = Workload(
        id="books-list",
        description="d",
        entry_point="/books/",
        fixture=recipe,
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )
    anchored = plain.model_copy(
        update={
            "environment": EnvironmentAnchor(
                anchor=date(2019, 3, 4),
                commit="abc123",
                reason="the repository's most recent commit",
                dependencies=("django==2.1.4",),
            )
        }
    )

    assert plain.environment is None
    assert anchored.environment is not None
    assert anchored.environment.anchor == date(2019, 3, 4)


def test_the_environment_survives_a_round_trip() -> None:
    """It has to: S-7.9 emits the workload as a document, and a resolution input
    that did not survive serialization is not a recorded input."""
    recipe = FixtureRecipe(
        entity="shop.Book", per_parent=1, distribution=Distribution.UNIFORM, source="synthesized"
    )
    built = Workload(
        id="books-list",
        description="d",
        entry_point="/books/",
        fixture=recipe,
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        environment=EnvironmentAnchor(
            anchor=date(2019, 3, 4),
            commit="abc123",
            reason="the repository's most recent commit",
            python_version="3.7",
            dependencies=("django==2.1.4", "pytz==2018.7"),
        ),
    )

    reloaded = Workload.model_validate(built.model_dump(mode="json"))

    assert reloaded.environment is not None
    assert reloaded.environment.dependencies == ("django==2.1.4", "pytz==2018.7")
    assert reloaded.environment.python_version == "3.7"


# ===================================== AC 5: it resolves differently at the anchor


@pytest.mark.index
def test_an_unpinned_dependency_resolves_to_its_era_at_the_anchor(tmp_path: Path) -> None:
    """AC 5, and the whole story in one assertion.

    `django>=2.0` in a 2019 repository is not a request for Django 6. Resolved
    today it yields one; resolved as of the repository's own commit it yields the
    Django the code was written against.
    """
    anchored = resolve(
        ["django>=2.0"], anchor=anchor_for(repository(tmp_path / "old")), python_version="3.7"
    )

    pins = {pin.split("==")[0].lower(): pin for pin in anchored.pins}
    assert pins["django"].startswith("django==2.")


@pytest.mark.index
def test_the_same_requirement_resolves_wrongly_without_the_anchor(tmp_path: Path) -> None:
    """The other half of AC 5, and the control that makes the first half mean
    something: without the anchor the same requirement resolves to a major
    version the repository's code does not run on."""
    today = override(date.today(), "resolving as of now, to show what the anchor prevents")

    unanchored = resolve(["django>=2.0"], anchor=today)

    pins = {pin.split("==")[0].lower(): pin for pin in unanchored.pins}
    major = int(pins["django"].split("==")[1].split(".")[0])
    assert major > 2


@pytest.mark.index
def test_the_resolution_records_what_constrained_it(tmp_path: Path) -> None:
    anchored = resolve(
        ["django>=2.0"], anchor=anchor_for(repository(tmp_path / "old")), python_version="3.7"
    )

    assert anchored.anchor.on == date(2019, 3, 4)
    assert anchored.python_version == "3.7"
    assert anchored.requirements == ("django>=2.0",)
    assert "2019-03-04" in anchored.describe()


@pytest.mark.index
def test_a_requirement_that_cannot_resolve_at_its_anchor_says_so(tmp_path: Path) -> None:
    """A fact about the repository rather than a fault here: a dependency set
    that will not resolve on its own anchor date did not resolve for its authors
    either."""
    with pytest.raises(AnchorError, match="nothing resolves"):
        resolve(
            ["django>=5.0"],
            anchor=anchor_for(repository(tmp_path / "old")),
        )


def test_resolving_nothing_is_refused() -> None:
    with pytest.raises(AnchorError, match="not an environment"):
        resolve([], anchor=override(date(2020, 1, 1), "a reason"))

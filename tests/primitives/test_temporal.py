"""A commit and a number, from a real repository and real worktrees.

S-3.11. `01-primitives.md` §6: temporal punches above its weight because it needs
no understanding of the code at all and produces the most actionable output any
primitive here can. So these tests use a real git repository with real commits
and real worktrees — nothing about checking out a revision is mocked, because
what the story claims is a fact about git and a fake would only assert what these
tests already believe.

The subject is a file whose contents *are* the cost: each commit writes a number,
and the measurement reads it out of the worktree. That makes every expected
answer exact and keeps the tests about the bisect rather than about a workload.

The two failure modes §6 names get the attention here, because both of them
produce a confident wrong commit rather than an error:

**A revision that will not build.** Skipped, and named — which is what `git
bisect skip` is for, and what stops one unbuildable commit ending the search.

**A workload that does not exist at the older end.** Checked before anything is
bisected, because a bisect over a range where the older half cannot be measured
returns the commit that added the workload. That commit is real, and it is not
the regression.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from coldfix.bench.execute import execute
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.primitives.search import Outcome
from coldfix.primitives.temporal import (
    TemporalError,
    at_revision,
    bisect_regression,
    measure_revisions,
)
from coldfix.sandbox.worktrees import Repository

# Every test here creates a real repository and several real worktrees, which
# costs seconds rather than milliseconds. `slow` is exactly the marker for that:
# a test you choose not to wait for on every edit, run in full before a story is
# called done.
pytestmark = pytest.mark.slow

GIT_TIMEOUT = 120.0

# Ten commits: the first five cost 10, the rest cost 500. The regression is at
# index 5, and every test that finds something else is finding it wrongly.
COSTS = (10.0, 10.0, 10.0, 10.0, 10.0, 500.0, 500.0, 500.0, 500.0, 500.0)
REGRESSION_AT = 5
THRESHOLD = 100.0


def git(root: Path, *args: str) -> str:
    """Run git in `root`, failing loudly. Identity is supplied per invocation so
    the tests neither depend on nor modify the developer's configuration."""
    result = execute(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=ColdFix Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        timeout=GIT_TIMEOUT,
    )
    if result.exit_code != 0:
        message = f"git {' '.join(args)} failed: {result.stderr}"
        raise AssertionError(message)
    return result.stdout


@pytest.fixture
def history(tmp_path: Path) -> tuple[Repository, tuple[str, ...]]:
    """A repository whose `cost.txt` changes over ten commits.

    The file's contents are the cost, so the measurement is exact and the tests
    are about the search rather than about a workload's noise.
    """
    root = tmp_path / "subject"
    root.mkdir()
    git(root, "init", "--initial-branch=main")

    revisions: list[str] = []
    for index, cost in enumerate(COSTS):
        # The index is written alongside the cost so consecutive commits with the
        # same cost still differ: git refuses a commit that changes nothing, and
        # half of this history is deliberately flat.
        (root / "cost.txt").write_text(f"{cost}\n{index}\n", encoding="utf-8")
        git(root, "add", "cost.txt")
        git(root, "commit", "--message", f"commit {index}, cost {cost}")
        revisions.append(git(root, "rev-parse", "HEAD").strip())

    return Repository(root=root), tuple(revisions)


def read_cost(path: Path) -> float:
    """The measurement: whatever this revision says it costs."""
    return float((path / "cost.txt").read_text(encoding="utf-8").splitlines()[0])


# ------------------------------------------- AC 1: a worktree at an old revision


def test_a_revision_is_checked_out_into_its_own_worktree(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """AC 1. Real git, real checkout — the primitive's whole interaction with the
    subject."""
    repository, revisions = history

    with at_revision(repository, revisions[0], tmp_path / "worktrees") as path:
        assert path.exists()
        assert read_cost(path) == COSTS[0]
        checked_out = path

    assert not checked_out.exists()


def test_the_worktree_is_destroyed_even_when_the_measurement_raises(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """The diff from an old revision to the current one is a revert of
    everything since, and a worktree that outlives its measurement is that text
    sitting on disk."""
    repository, revisions = history
    escaped: Path | None = None

    with (
        pytest.raises(RuntimeError, match="deliberate"),
        at_revision(repository, revisions[3], tmp_path / "worktrees") as path,
    ):
        escaped = path
        message = "deliberate"
        raise RuntimeError(message)

    assert escaped is not None
    assert not escaped.exists()


def test_the_same_measurement_runs_at_every_revision(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """The straight-line version, which is what makes the bisect's answer
    checkable on a range where both are affordable."""
    repository, revisions = history

    measured = measure_revisions(repository, revisions, read_cost, root=tmp_path / "worktrees")

    assert [item.cost for item in measured] == list(COSTS)
    assert not any(item.skipped for item in measured)


# ------------------------------------------------------- AC 2: the bisect


def test_the_bisect_finds_the_commit_where_the_cost_crossed(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """AC 2, and §6's claim about the output: a specific commit and a specific
    number, with nothing having read the code."""
    repository, revisions = history

    result = bisect_regression(
        repository,
        revisions,
        read_cost,
        root=tmp_path / "worktrees",
        threshold=THRESHOLD,
    )

    assert result.bad == revisions[REGRESSION_AT]
    assert result.good == revisions[REGRESSION_AT - 1]
    assert "crossed 100" in result.explanation()


def test_the_bisect_measures_far_fewer_revisions_than_the_range_holds(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """The entire reason to bisect rather than sweep: each measurement is a
    checkout and a workload run."""
    repository, revisions = history

    result = bisect_regression(
        repository, revisions, read_cost, root=tmp_path / "worktrees", threshold=THRESHOLD
    )

    assert result.measurements < len(revisions)
    assert len(result.probes) >= result.measurements


def test_a_range_of_one_revision_has_no_boundary_in_it(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    repository, revisions = history

    with pytest.raises(TemporalError, match="at least 2 revisions"):
        bisect_regression(
            repository, revisions[:1], read_cost, root=tmp_path / "worktrees", threshold=THRESHOLD
        )


# ------------------------------ AC 3: revisions that cannot be measured


def test_a_revision_that_fails_to_build_is_skipped_and_named(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """AC 3. An old revision needing a dependency nobody can install any more is
    ordinary in a real repository, not a reason to abandon the search."""
    repository, revisions = history
    unbuildable = {revisions[4], revisions[6]}

    def measure(path: Path) -> float:
        revision = git(path, "rev-parse", "HEAD").strip()
        if revision in unbuildable:
            message = "ModuleNotFoundError: no module named 'legacy'"
            raise RuntimeError(message)
        return read_cost(path)

    result = bisect_regression(
        repository, revisions, measure, root=tmp_path / "worktrees", threshold=THRESHOLD
    )

    assert set(result.skipped) <= unbuildable
    assert result.bad == revisions[REGRESSION_AT]
    assert "could not be measured and were skipped" in result.explanation()


def test_a_skipped_revision_is_recorded_as_unresolved_rather_than_cheap(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """Treating an unmeasurable revision as cheap would move the boundary past
    it, which is a confident wrong commit."""
    repository, revisions = history

    def measure(path: Path) -> float:
        if git(path, "rev-parse", "HEAD").strip() == revisions[5]:
            message = "does not build"
            raise RuntimeError(message)
        return read_cost(path)

    result = bisect_regression(
        repository, revisions, measure, root=tmp_path / "worktrees", threshold=THRESHOLD
    )

    unresolved = [probe for probe in result.probes if probe.outcome is Outcome.UNRESOLVED]
    assert unresolved
    assert all(probe.failure for probe in unresolved)
    # The boundary is still bracketed correctly: cheap on one side, expensive on
    # the other, with the skipped commit inside the pair.
    assert result.good in revisions[:REGRESSION_AT]
    assert result.bad in revisions[REGRESSION_AT:]


def test_when_everything_between_the_ends_is_unmeasurable_the_pair_is_the_answer(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """A smaller answer than a commit, and a far better one than the wrong
    commit: the regression is in here and these revisions cannot say where."""
    repository, revisions = history
    ends = {revisions[0], revisions[-1]}

    def measure(path: Path) -> float:
        revision = git(path, "rev-parse", "HEAD").strip()
        if revision not in ends:
            message = "does not build"
            raise RuntimeError(message)
        return read_cost(path)

    result = bisect_regression(
        repository, revisions, measure, root=tmp_path / "worktrees", threshold=THRESHOLD
    )

    assert result.good == revisions[0]
    assert result.bad == revisions[-1]
    assert len(result.skipped) == len(revisions) - 2
    assert "may have happened at any of the skipped commits" in result.explanation()


# --------------------------- AC 4: the endpoints have to say what is assumed


def test_a_workload_absent_at_the_older_end_is_refused(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """AC 4, and the failure it prevents: a bisect over a range whose older half
    cannot be measured returns the commit that *added the workload*. That commit
    is real and it is not the regression."""
    repository, revisions = history

    def measure(path: Path) -> float:
        if git(path, "rev-parse", "HEAD").strip() == revisions[0]:
            message = "FileNotFoundError: the endpoint did not exist yet"
            raise RuntimeError(message)
        return read_cost(path)

    with pytest.raises(TemporalError, match="could not be measured") as raised:
        bisect_regression(
            repository, revisions, measure, root=tmp_path / "worktrees", threshold=THRESHOLD
        )

    assert "the commit that added the workload" in str(raised.value)


def test_a_range_whose_oldest_revision_is_already_slow_is_refused(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """The regression is older than this range. Extend it backwards, or record
    that the cost was always here — an exclusion rather than a failed search."""
    repository, revisions = history

    with pytest.raises(TemporalError, match="older than this range"):
        bisect_regression(
            repository,
            revisions[REGRESSION_AT:],
            read_cost,
            root=tmp_path / "worktrees",
            threshold=THRESHOLD,
        )


def test_a_range_with_no_regression_in_it_is_refused(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """Also a result: whatever was slow is not slow at the head of this range."""
    repository, revisions = history

    with pytest.raises(TemporalError, match="no regression in this range"):
        bisect_regression(
            repository,
            revisions[:REGRESSION_AT],
            read_cost,
            root=tmp_path / "worktrees",
            threshold=THRESHOLD,
        )


# ------------------------------------------- the noise band, inherited from S-3.5


def test_a_revision_inside_the_noise_band_is_not_used_to_decide(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """S-3.5's third answer, reused rather than reinvented. A revision whose cost
    lands inside the noise band decides a step of the search on noise, and every
    step after it inherits that — so it is skipped like an unbuildable one."""
    repository, revisions = history

    inside_the_band = revisions[REGRESSION_AT - 1]

    def measure(path: Path) -> float:
        # One middle revision measures a hair above the threshold, which at this
        # resolution is indistinguishable from a hair below it. The endpoints are
        # left alone: a band that swallowed one of those is a different failure,
        # and it has its own test.
        if git(path, "rev-parse", "HEAD").strip() == inside_the_band:
            return THRESHOLD + 1.0
        return read_cost(path)

    result = bisect_regression(
        repository,
        revisions,
        measure,
        root=tmp_path / "worktrees",
        threshold=THRESHOLD,
        resolution=20.0,
    )

    assert result.skipped
    assert result.bad in revisions


def test_the_primitive_is_registered() -> None:
    primitive = REGISTRY.get("temporal.bisect")

    assert primitive.required_capabilities == {
        Capability.REVISION_HISTORY,
        Capability.STATE_RESET,
    }
    assert primitive.run is bisect_regression


def test_worktrees_do_not_accumulate(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """A bisect over a long range creates and destroys many checkouts, and one
    that leaked would fill a disk with old copies of the subject."""
    repository, revisions = history
    root = tmp_path / "worktrees"

    bisect_regression(repository, revisions, read_cost, root=root, threshold=THRESHOLD)

    assert list(root.iterdir()) == []
    # `worktrees()` always reports the main tree; what must not survive is a
    # linked one, which is what a checkout of an old revision creates.
    assert [tree for tree in repository.worktrees() if not tree.is_main] == []


def test_the_measurement_sees_the_revision_it_asked_for(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """The property everything else rests on: what is measured is the code at
    that commit, not the code at HEAD."""
    repository, revisions = history
    seen: list[tuple[str, float]] = []

    for index in (0, 4, 9):
        with at_revision(repository, revisions[index], tmp_path / f"w{index}") as path:
            seen.append((revisions[index], read_cost(path)))

    assert [cost for _, cost in seen] == [COSTS[0], COSTS[4], COSTS[9]]


def test_an_unknown_revision_is_refused_by_the_worktree_layer(
    history: tuple[Repository, tuple[str, ...]], tmp_path: Path
) -> None:
    """S-2.2 owns this and it is worth knowing it reaches here: a typo in a
    revision must not produce a worktree at something else."""
    repository, _ = history

    with (
        pytest.raises(Exception, match="does not name a commit"),
        at_revision(repository, f"deadbeef{uuid.uuid4().hex[:8]}", tmp_path / "w"),
    ):
        pass

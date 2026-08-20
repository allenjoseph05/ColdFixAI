"""Epic 12, S-12.3 — crash resume.

*A run killed mid-investigation resumes from the last checkpoint with full state.
Tested by killing the process at three different nodes. The resumed run produces
the same final result as an uninterrupted one.*

**The processes are really killed.** `crashing_run.py` calls `os._exit` inside the
node, which skips every `finally`, every buffer flush and the SQLite close — an
exception would unwind cleanly and be a graceful shutdown wearing the word crash.
What the checkpoint file holds afterwards is what a real kill would have left, and
that is the only version of this test worth running.

Three nodes because AC 2 asks for three, and they are chosen to be different
*kinds* of point: after the first write, in the middle of the investigation, and
inside the repair cycle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.graph import Node, Wiring, assemble
from coldfix.orchestrator.resume import (
    Progress,
    ResumeError,
    agrees,
    duplicated,
    progress_of,
    resume,
    same_outcome,
    start,
)
from coldfix.state.checkpoint import CheckpointedState

RUNNER = Path(__file__).resolve().parent / "crashing_run.py"
KILLED_AT = ("ground", "investigate", "repair")
"""AC 2's three, chosen as three different kinds of point: after the first write,
in the middle of the investigation, and inside the repair cycle."""


def run_until(store: Path, run_id: str, die_at: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), str(store), run_id, die_at],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def finished(store: Path, run_id: str) -> Mapping[str, Any]:
    completed = run_until(store, run_id, "-")
    assert completed.returncode == 0, completed.stderr[-800:]
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


def resume_in_this_process(store: Path, run_id: str) -> Mapping[str, Any]:
    """Resume with the same wiring the crashed process used, minus the crash."""
    from crashing_run import build  # noqa: PLC0415 - the subprocess script is the fixture

    with for_development(store) as saver:
        return resume(assemble(build(None), saver), saver, run_id)


@pytest.fixture(autouse=True)
def _runner_on_path() -> None:
    if str(RUNNER.parent) not in sys.path:
        sys.path.insert(0, str(RUNNER.parent))


# ============ AC 2 — the process is really killed, at three different nodes


@pytest.mark.parametrize("node", KILLED_AT)
def test_a_killed_process_leaves_a_resumable_checkpoint(tmp_path: Path, node: str) -> None:
    """**A real kill**: `os._exit` skips the `finally` that closes SQLite, so this
    also answers whether the checkpointer commits per write or buffers."""
    store = tmp_path / "run.sqlite"
    crashed = run_until(store, f"killed-at-{node}", node)

    assert crashed.returncode != 0, "the child did not die"
    assert store.exists(), "and it left a store behind"

    with for_development(store) as saver:
        progress = progress_of(saver, f"killed-at-{node}")

    assert progress.started, f"nothing survived the kill at {node}"
    assert progress.checkpoints >= 1


def test_the_kill_lands_further_along_the_later_the_node(tmp_path: Path) -> None:
    """Three different points, and the checkpoint count says so. If all three
    produced the same progress the parametrised test above would be one test run
    three times."""
    counts = []
    for node in KILLED_AT:
        store = tmp_path / f"{node}.sqlite"
        run_until(store, "run", node)
        with for_development(store) as saver:
            counts.append(progress_of(saver, "run").checkpoints)

    assert counts == sorted(counts), f"later nodes should get further: {counts}"
    assert counts[0] < counts[-1], "and the first is not the last"


# ============ AC 1 — resumes with full state


@pytest.mark.parametrize("node", KILLED_AT)
def test_a_resumed_run_carries_what_the_crashed_one_wrote(tmp_path: Path, node: str) -> None:
    store = tmp_path / "run.sqlite"
    run_until(store, "crashed", node)

    final = resume_in_this_process(store, "crashed")

    assert final["project"] == {"adapter": "django"} or node == "ground"
    assert final["screening"] == {}, "the run reached ship and cleared what it invalidated"


def test_resuming_a_run_nobody_started_is_refused(tmp_path: Path) -> None:
    """`invoke(None, ...)` against an unknown thread starts a new run rather than
    failing, which would spend a whole campaign under a name chosen in the belief
    that it already existed."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(_plain_wiring(), saver)
        with pytest.raises(ResumeError, match="nothing to resume"):
            resume(graph, saver, "never-ran")


def test_a_channel_absent_from_a_checkpoint_is_not_filled_in(tmp_path: Path) -> None:
    """S-12.2's finding, carried into resume: a channel missing from the last
    checkpoint has not been written yet, which is not the same as written empty.
    Supplying a default would hand a resumed run a value it never had."""
    store = tmp_path / "run.sqlite"
    run_until(store, "early", "ground")

    with for_development(store) as saver:
        progress = progress_of(saver, "early")

    assert "project" not in progress.state, "the ground node died before writing it"
    assert progress.state.get("project") is None
    assert "not yet written" not in progress.describe(), "describe reports, it does not explain"


def test_progress_reports_what_is_on_disk() -> None:
    nothing = Progress(run_id="fresh", checkpoints=0, state={})
    assert not nothing.started
    assert "this is a new run" in nothing.describe()

    some = Progress(run_id="part", checkpoints=3, state={"project": {}, "screening": {}})
    assert some.started
    assert "project, screening" in some.describe()


# ============ AC 3 — the same final result as an uninterrupted run


@pytest.mark.parametrize("node", KILLED_AT)
def test_a_resumed_run_ends_where_an_uninterrupted_one_does(tmp_path: Path, node: str) -> None:
    """**AC 3.** Two stores, two runs: one killed and resumed, one that never was.
    They have to agree channel by channel."""
    clean = finished(tmp_path / "clean.sqlite", "clean")

    crashed_store = tmp_path / "crashed.sqlite"
    run_until(crashed_store, "crashed", node)
    resumed = resume_in_this_process(crashed_store, "crashed")

    assert same_outcome(clean, resumed) == (), (
        f"killed at {node}, the resumed run disagrees with the clean one"
    )
    assert agrees(clean, resumed)


def test_the_comparison_names_the_channel_rather_than_returning_a_boolean() -> None:
    """*The resumed run produced a different answer* is only actionable with the
    channel named."""
    assert same_outcome({"a": 1, "b": 2}, {"a": 1, "b": 3}) == ("b",)
    assert same_outcome({"a": 1}, {"a": 1, "b": 2}) == ("b",), "a channel only one has"
    assert same_outcome({"a": 1}, {"a": 1}) == ()


def test_a_channel_may_be_excused_only_by_naming_it() -> None:
    """A default that excused anything would make this function agree with itself."""
    assert same_outcome({"a": 1}, {"a": 2}) == ("a",)
    assert same_outcome({"a": 1}, {"a": 2}, ignoring=("a",)) == ()
    assert agrees({"a": 1}, {"a": 2}, ignoring=("a",))


# ============ at-least-once execution, and what it would break


@pytest.mark.parametrize("node", KILLED_AT)
def test_no_append_only_entry_is_duplicated_by_the_resume(tmp_path: Path, node: str) -> None:
    """**The failure at-least-once execution produces.** A checkpoint is written
    after a node, so a crash inside one re-runs it — and a node that appended
    before dying would append again. `experiments` holding the same entry twice is
    S-8.4's append-only guarantee broken by a crash rather than by a rewrite.

    It holds today because the appends happen in the node's *return value*, after
    the work, so an interrupted node contributes nothing rather than half."""
    store = tmp_path / "run.sqlite"
    run_until(store, "crashed", node)
    final = resume_in_this_process(store, "crashed")

    assert duplicated(final, "experiments", key="key") == ()
    assert duplicated(final, "attempts", key="approach") == ()


def test_duplication_is_reported_rather_than_prevented() -> None:
    """This module cannot make a node idempotent, and deduplicating here would hide
    the fact that one was not."""
    doubled = {"experiments": [{"key": "exp-1"}, {"key": "exp-2"}, {"key": "exp-1"}]}
    assert duplicated(doubled, "experiments", key="key") == ("exp-1",)
    assert duplicated({"experiments": []}, "experiments", key="key") == ()
    assert duplicated({}, "experiments", key="key") == ()


def test_starting_and_resuming_are_two_names_for_one_argument(tmp_path: Path) -> None:
    """LangGraph reads `None` as *continue* and anything else as *begin with this*,
    so handing back the initial state after a crash is a second run that repeats
    every node and bills every phase again — and both return a final state, so the
    wrong one is only detectable by the bill."""
    store = tmp_path / "run.sqlite"
    visits: list[str] = []

    def counting(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            visits.append(name)
            return {"screening": {}} if name == "screen" else {}

        return step

    wiring = Wiring(**{item.value: counting(item.value) for item in Node})
    with for_development(store) as saver:
        graph = assemble(wiring, saver)
        start(graph, "twice")
        after_first = list(visits)

        resume(graph, saver, "twice")
        assert visits == after_first, "a resume of a finished run runs nothing again"

        start(graph, "twice")
        assert len(visits) > len(after_first), "restarting it does run the nodes again"


def _plain_wiring() -> Wiring:
    def step(state: CheckpointedState) -> Mapping[str, object]:
        return {}

    return Wiring(**{item.value: step for item in Node})

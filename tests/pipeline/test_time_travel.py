"""S-24.3 — crash-resume and rewind.

Three kill points, chosen as three different *kinds* of point rather than three
positions: after the first write, in the middle of the run, and at the last node
before the end. A run killed at all three has to come back the same as one that
was never interrupted.

The kills are real. `crashing_run.py` calls `os._exit`, so nothing flushes and
nothing closes — what the checkpoint holds is what a power cut would have left.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.orchestrator.resume import DURABILITY, progress_of
from coldfix.pipeline.graph import Node, Wiring, build
from coldfix.pipeline.state import PipelineState
from pipeline import crashing_run

HARNESS = Path(__file__).resolve().parent / "crashing_run.py"

KILLED_AT = ("scan", "optimize", "audit_patch")
"""Three kinds of point, not three positions: the first node that writes anything
substantial, one in the middle with an append-only channel behind it, and the
last node before the run would have finished."""


def run(store: Path, run_id: str, die_at: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), str(store), run_id, die_at],
        capture_output=True,
        text=True,
        check=False,
    )


def complete_wiring() -> Wiring:
    """The same steps the subprocess uses, with nothing dying.

    `tests` is on `pythonpath`, and `tests/pipeline` is a package because two
    test files in this tree share a basename with ones elsewhere — so the
    subprocess script imports as a module of it.
    """
    return crashing_run.wiring(None)


def resume(store: Path, run_id: str) -> dict[str, object]:
    """Pick the run back up from wherever it stopped."""
    with for_development(store) as checkpointer:
        graph = build(complete_wiring(), checkpointer=checkpointer, gated=False)
        return dict(graph.invoke(None, thread(run_id), durability=DURABILITY))


# ------------------------------------------------------------ crash and resume


@pytest.mark.slow
@pytest.mark.parametrize("node", KILLED_AT)
def test_a_killed_run_leaves_a_resumable_checkpoint(tmp_path: Path, node: str) -> None:
    store = tmp_path / f"{node}.sqlite"
    killed = run(store, "run", node)
    assert killed.returncode == 9, "the harness should have died, not finished"

    with for_development(store) as checkpointer:
        progress = progress_of(checkpointer, "run")
    assert progress.checkpoints > 0, "a kill left nothing to resume from"


@pytest.mark.slow
@pytest.mark.parametrize("node", KILLED_AT)
def test_a_resumed_run_ends_the_same_as_one_never_interrupted(tmp_path: Path, node: str) -> None:
    """The claim: interrupted and uninterrupted runs agree. Not *roughly* — the
    same route and the same resolution, because a resume that produced something
    slightly different would be a resume nobody could trust."""
    control_store = tmp_path / "control.sqlite"
    control = run(control_store, "control", "-")
    assert control.returncode == 0, control.stderr
    expected = json.loads(control.stdout)

    store = tmp_path / f"{node}.sqlite"
    run(store, "run", node)
    assert resume(store, "run")["route"] == expected["route"]
    assert resume(store, "run")["resolved"] == expected["resolved"]


@pytest.mark.slow
def test_the_later_the_kill_the_further_the_run_got(tmp_path: Path) -> None:
    """If all three kills left the same amount behind, the checkpoint is being
    written once at the end rather than after every node, and the test above
    would pass while proving nothing."""
    counts = []
    for node in KILLED_AT:
        store = tmp_path / f"{node}.sqlite"
        run(store, "run", node)
        with for_development(store) as checkpointer:
            counts.append(progress_of(checkpointer, "run").checkpoints)
    assert counts == sorted(counts), f"later nodes should get further: {counts}"
    assert len(set(counts)) == len(counts), (
        f"three distinct depths, not {counts}. Equal counts are what an asynchronous "
        "checkpoint write produces -- and with nothing durable a resume restarts from the "
        "beginning and reaches the same answer, so every other test here passes while the "
        "crash saved nothing. This is the one that notices."
    )


@pytest.mark.slow
def test_what_the_crashed_run_wrote_survives_into_the_resumed_one(tmp_path: Path) -> None:
    """A resume that started from nothing would also 'end the same'. This asks
    whether the work done before the crash was kept."""
    store = tmp_path / "kept.sqlite"
    run(store, "run", "optimize")
    resumed = resume(store, "run")
    assert resumed["findings"], "the scan's findings did not survive the kill"
    assert resumed["measurements"], "the measurements did not survive the kill"


# ----------------------------------------------------------------- rewind


@pytest.mark.slow
def test_a_rewound_run_can_be_sent_down_a_different_path(tmp_path: Path) -> None:
    """Time travel proper: go back to a checkpoint, change the decision, and the
    run continues from there rather than from the beginning."""
    store = tmp_path / "rewind.sqlite"
    with for_development(store) as checkpointer:
        graph = build(complete_wiring(), checkpointer=checkpointer, gated=False)
        config = thread("run")
        graph.invoke(PipelineState(), config, durability=DURABILITY)

        history = list(graph.get_state_history(config))
        before_optimize = next(
            snapshot for snapshot in history if snapshot.next == (Node.OPTIMIZE.value,)
        )
        # `as_node` is the whole trick: it says *which* node the injected update
        # came from, so the run continues at that node's router. Without it the
        # route is read by the router before the fork, which has never heard of
        # it -- and is refused, correctly.
        forked = graph.update_state(
            before_optimize.config,
            {"route": "nothing_beat_baseline"},
            as_node=Node.OPTIMIZE.value,
        )
        assert graph.invoke(None, forked)["route"] == "nothing_beat_baseline"


@pytest.mark.slow
def test_losing_candidates_survive_the_rewind(tmp_path: Path) -> None:
    """The reason the persistent half exists. A rewind that restored the state
    *and* the ignorance that caused it would send the next round back to propose
    the patch that already lost."""
    store = tmp_path / "candidates.sqlite"
    with for_development(store) as checkpointer:
        graph = build(complete_wiring(), checkpointer=checkpointer, gated=False)
        config = thread("run")
        graph.invoke(PipelineState(), config, durability=DURABILITY)

        history = list(graph.get_state_history(config))
        after_optimize = next(
            snapshot
            for snapshot in history
            if snapshot.values.get("candidates") and snapshot.next == (Node.AUDIT_PATCH.value,)
        )
        assert after_optimize.values["candidates"][0]["id"] == "c-1"
        assert after_optimize.values["candidates"][0]["won"] is False


@pytest.mark.slow
def test_an_append_only_channel_is_never_written_twice_by_a_resume(tmp_path: Path) -> None:
    """A resume that replayed a completed node would double the log — and the log
    is the prompt prefix, so doubling it breaks the cache and multiplies what the
    run costs."""
    store = tmp_path / "append.sqlite"
    run(store, "run", "audit_patch")
    resumed = resume(store, "run")
    candidates = cast("list[dict[str, object]]", resumed["candidates"])
    ids = [entry["id"] for entry in candidates]
    assert ids == ["c-1"], f"the append-only channel gained duplicates: {ids}"

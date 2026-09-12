"""S-29.2 — crash-resume at three kill points, with the real node functions.

S-24.3 proved the *graph* survives a kill: its harness returned canned mappings,
so nothing that a node actually does was ever interrupted. What a real adapter
does on resume is the part with the failure modes -- it rehydrates a ledger from
the checkpoint, re-reads an append-only channel, and picks a finding back up
mid-investigation -- and none of that was under test until here.

**The kill points are re-chosen against seven nodes** (ADR 175 asked for this
when `ground` was split out of `scan`). Three *kinds* of point rather than three
positions:

- `ground` — the first node that writes anything substantial. It mints the
  baseline measurement every later comparison is made against.
- `optimize` — the middle, with an append-only channel behind it, and the node
  S-28.5 showed can be re-entered after the Adversary sends a patch back.
- `audit_patch` — the last node before the run would have finished.

`scan` is deliberately not among them, and the reason is a property of the
double rather than of the product: `FakeTools` numbers measurements per process,
so a resumed run re-entering `scan` mints `m-1` a second time and collides with
what `ground` already recorded. In production the ids come from the measurement
itself and cannot collide. Killing at `ground` covers the same shape -- a node
that writes to the ledger and to an append-only channel -- without the artefact.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.orchestrator.resume import DURABILITY, duplicated, progress_of
from coldfix.pipeline.graph import build
from pipeline import crashing_real_run

HARNESS = Path(__file__).resolve().parent / "crashing_real_run.py"

KILLED_AT = ("ground", "optimize", "audit_patch")
"""Three kinds of point against seven nodes. The table in the module docstring
says why each one, and why `scan` is not one of them."""


def run(store: Path, run_id: str, workspace: Path, die_at: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), str(store), run_id, str(workspace), die_at],
        capture_output=True,
        text=True,
        check=False,
    )


def resume(store: Path, workspace: Path, run_id: str) -> dict[str, Any]:
    """Pick the run back up from wherever it stopped.

    A fresh assembly, because a resumed run is a new process: the ledger it
    rehydrates comes from the checkpoint, not from the object the crashed run
    held.
    """
    with for_development(store) as checkpointer:
        graph = build(
            crashing_real_run.wiring(workspace, None), checkpointer=checkpointer, gated=False
        )
        return dict(graph.invoke(None, thread(run_id), durability=DURABILITY))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "work"


# ------------------------------------------------------------ crash and resume


@pytest.mark.slow
@pytest.mark.parametrize("node", KILLED_AT)
def test_a_killed_real_node_leaves_a_resumable_checkpoint(
    tmp_path: Path, workspace: Path, node: str
) -> None:
    store = tmp_path / f"{node}.sqlite"
    killed = run(store, "run", workspace, node)
    assert killed.returncode == 9, f"the harness should have died, not finished: {killed.stderr}"

    with for_development(store) as checkpointer:
        progress = progress_of(checkpointer, "run")
    assert progress.checkpoints > 0, "a kill left nothing to resume from"


@pytest.mark.slow
@pytest.mark.parametrize("node", KILLED_AT)
def test_a_resumed_real_run_ends_the_same_as_one_never_interrupted(
    tmp_path: Path, workspace: Path, node: str
) -> None:
    """The claim, now with the adapters in it: interrupted and uninterrupted runs
    agree. Not roughly -- the same route and the same resolution, because a
    resume that produced something slightly different would be one nobody could
    trust."""
    control = run(tmp_path / "control.sqlite", "control", tmp_path / "control-work", "-")
    assert control.returncode == 0, control.stderr
    expected = json.loads(control.stdout)
    assert expected["route"] == "done", "the control run did not get all the way through"

    store = tmp_path / f"{node}.sqlite"
    run(store, "run", workspace, node)
    resumed = resume(store, workspace, "run")

    assert resumed["route"] == expected["route"]
    assert resumed["resolved"] == expected["resolved"]


@pytest.mark.slow
def test_the_later_the_kill_the_further_the_real_run_got(tmp_path: Path) -> None:
    """If all three kills left the same amount behind, the checkpoint is being
    written once at the end rather than after every node, and the test above
    would pass while proving nothing. This is the assertion that caught exactly
    that in S-24.3."""
    counts = []
    for node in KILLED_AT:
        store = tmp_path / f"{node}.sqlite"
        run(store, "run", tmp_path / f"{node}-work", node)
        with for_development(store) as checkpointer:
            counts.append(progress_of(checkpointer, "run").checkpoints)

    assert counts == sorted(counts), f"later nodes should get further: {counts}"
    assert len(set(counts)) == len(counts), (
        f"three distinct depths, not {counts}. Equal counts are what an asynchronous "
        "checkpoint write produces -- and with nothing durable a resume restarts from the "
        "beginning and reaches the same answer, so every other test here passes while the "
        "crash saved nothing. This is the one that notices."
    )


# ------------------------------------------------- what only a real node can lose


@pytest.mark.slow
def test_the_ledger_is_rebuilt_from_the_checkpoint_and_the_finding_still_attests(
    tmp_path: Path, workspace: Path
) -> None:
    """The real-node case a canned mapping cannot express.

    `Ledger` is an in-process object. The crashed run's copy died with it, so the
    resumed run's `audit_finding` and `optimize` are working against a ledger
    rebuilt from the `measurements` channel. If that rehydration were missing the
    finding would fail its attestation on resume -- and the run would end
    `unsound` having proved nothing, which reads exactly like a real rejection.
    """
    store = tmp_path / "ledger.sqlite"
    run(store, "run", workspace, "optimize")
    resumed = resume(store, workspace, "run")

    assert resumed["route"] == "done", "the resumed run could not re-attest its finding"
    (recorded,) = resumed["measurements"]
    assert resumed["findings"]["f-1"]["attested_against"] == [recorded["measurement_id"]]


@pytest.mark.slow
def test_what_the_crashed_real_run_wrote_survives_into_the_resumed_one(
    tmp_path: Path, workspace: Path
) -> None:
    """A resume that started from nothing would also end the same. This asks
    whether the work done before the crash was kept."""
    store = tmp_path / "kept.sqlite"
    run(store, "run", workspace, "optimize")
    resumed = resume(store, workspace, "run")

    assert resumed["findings"], "the scan's findings did not survive the kill"
    assert resumed["measurements"], "the measurements did not survive the kill"


@pytest.mark.slow
def test_an_append_only_channel_is_never_written_twice_by_a_real_resume(
    tmp_path: Path, workspace: Path
) -> None:
    """A resume that replayed a completed node would double the log -- and the log
    is the prompt prefix, so doubling it breaks the cache and multiplies what the
    run costs. `optimize` is the node that can be re-entered legitimately, which
    is what makes its channel the one worth asserting."""
    store = tmp_path / "append.sqlite"
    run(store, "run", workspace, "audit_patch")
    resumed = resume(store, workspace, "run")

    assert duplicated(resumed, "measurements", key="measurement_id") == ()
    candidates = cast("list[dict[str, Any]]", resumed["candidates"])
    approaches = [entry["candidate"]["approach"] for entry in candidates]
    assert len(approaches) == len(set(approaches)), f"the search re-measured a repeat: {approaches}"

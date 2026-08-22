"""Epic 12 composed: one run, checkpointed, gated, crashed, resumed, rewound.

Seven stories — a graph, a checkpointer, crash resume, two human gates, time
travel, and the adapters that bind the whole thing to the epics — and the epic's
own sentence is **durable execution across hours, crashes, and multi-day human
gates**. Every story proved its piece against its own fixture. This is the first
thing that performs the sentence.

**It found one defect, and it is the shape every previous composition check
found**: two modules that are each right, producing a wrong answer between them.

`interrupt_before` parks a run *at* a node, and `invoke(None, config)` from that
checkpoint is exactly what a human approving the gate does. S-12.6's `before`
returned that same checkpoint. So **a rewind to a gate silently counted as
approving it** — measured, a fresh run parked at `repair` and a rewind to the
identical point ran straight through to `ship`. Somebody rewinding to reconsider
the direction got the repair re-run instead, unasked.

The fix is in `before`: for a gated node, target the checkpoint one earlier so the
run *re-enters* it and the interrupt fires as it does on any other entry.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.gate import waiting_at
from coldfix.orchestrator.graph import Node, Wiring, assemble
from coldfix.orchestrator.resume import progress_of, resume, start
from coldfix.orchestrator.rewind import before, history, rewind
from coldfix.state.checkpoint import CheckpointedState

RUNNER = Path(__file__).resolve().parent / "crashing_run.py"

UPDATES: Mapping[str, Mapping[str, object]] = {
    "ground": {"project": {"adapter": "django"}},
    "screen": {"screening": {"shop.books.list": {"flagged": True, "growth": {}}}},
    "investigate": {"target": "shop.books.list"},
    "audit_finding": {"route": "REPAIR"},
    "repair": {"attempts": [{"approach": "prefetch the author"}]},
    "audit_patch": {"route": "SHIP"},
    "ship": {"screening": {}, "route": None},
}


def build() -> Wiring:
    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            return dict(UPDATES.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


# ============================================ the epic's own sentence


def test_one_run_survives_two_gates_and_a_process_boundary(tmp_path: Path) -> None:
    """**Durable execution across hours and multi-day human gates**, performed.

    Each `for_development` block is a different process in every way that
    matters: nothing carries over but the file. So the two approvals happen on
    two different days, which is the claim `03-agents.md` §1.5 makes.
    """
    store = tmp_path / "run.sqlite"

    with for_development(store) as monday:
        start(assemble(build(), monday), "campaign")
        assert waiting_at(assemble(build(), monday), "campaign") == ("repair",)

    with for_development(store) as tuesday:
        graph = assemble(build(), tuesday)
        resume(graph, tuesday, "campaign")
        assert waiting_at(graph, "campaign") == ("ship",), "the early gate let it through"

    with for_development(store) as thursday:
        graph = assemble(build(), thursday)
        final = resume(graph, thursday, "campaign")

        assert waiting_at(graph, "campaign") == ()
        assert final["screening"] == {}, "ship ran"
        assert final["attempts"] == [{"approach": "prefetch the author"}]


@pytest.mark.slow
def test_a_gated_run_that_is_killed_resumes_and_is_still_gated(tmp_path: Path) -> None:
    """**Crashes and human gates, in the same run.**

    S-12.3 kills ungated runs and S-12.4 parks gated ones, and nothing put the
    two together — but *durable execution across hours, crashes, and multi-day
    human gates* is one sentence, and a campaign that crashes between an approval
    and the next gate is the ordinary case rather than an exotic one.
    """
    store = tmp_path / "run.sqlite"
    killed = subprocess.run(
        [sys.executable, str(RUNNER), str(store), "campaign", "investigate", "gated"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert killed.returncode != 0, "the process was really killed"

    with for_development(store) as saver:
        graph = assemble(build(), saver)
        assert progress_of(saver, "campaign").started, "the kill left something durable"

        resume(graph, saver, "campaign")

        assert waiting_at(graph, "campaign") == ("repair",), "and the gate still fires"


# ============================================ the defect this check found


def test_a_rewind_to_a_gate_re_arms_it(tmp_path: Path) -> None:
    """**The defect, and the regression test for it.**

    `interrupt_before` parks a run *at* a node, and `invoke(None, config)` from
    that checkpoint is what approving the gate does. Returning that same
    checkpoint from `before` made a rewind indistinguishable from an approval.

    The control below is what makes this test able to fail: without it, a graph
    that never gated anything would pass.
    """
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(build(), saver)
        start(graph, "run")
        assert waiting_at(graph, "run") == ("repair",), "control: the gate fires on a fresh run"

        resume(graph, saver, "run")
        resume(graph, saver, "run")
        assert waiting_at(graph, "run") == (), "and the run finished"

        rewind(graph, before(graph, "run", Node.REPAIR))

        assert waiting_at(graph, "run") == ("repair",), "the rewind asks again rather than acting"


def test_the_rewind_target_is_one_earlier_only_where_a_gate_would_be_skipped(
    tmp_path: Path,
) -> None:
    """Re-entering a node means re-running the phase before it, which costs a
    model call. So it is done where a gate would otherwise be skipped and nowhere
    else — `interrupt_before_nodes` is asked rather than assumed."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        open_graph = assemble(build(), saver, gated=False)
        start(open_graph, "open")

        point = before(open_graph, "open", Node.REPAIR)

        assert point.next_nodes == ("repair",), "ungated: the parked point itself"

    with for_development(tmp_path / "gated.sqlite") as saver:
        shut = assemble(build(), saver)
        start(shut, "shut")
        resume(shut, saver, "shut")
        resume(shut, saver, "shut")

        assert before(shut, "shut", Node.REPAIR).next_nodes == ("audit_finding",), "one earlier"


def test_a_rewound_gated_run_still_carries_what_the_earlier_phases_wrote(
    tmp_path: Path,
) -> None:
    """Re-entering `repair` re-runs `audit_finding`, which is a phase and not a
    reset. Everything grounding, screening and the investigation established is
    still there — otherwise the rewind would be a restart wearing the word."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(build(), saver)
        start(graph, "run")
        resume(graph, saver, "run")
        resume(graph, saver, "run")

        rewind(graph, before(graph, "run", Node.REPAIR))

        parked = history(graph, "run")[-1].values
        assert parked["project"] == {"adapter": "django"}
        assert parked["target"] == "shop.books.list"
        assert parked["attempts"] == [], "and the repair has not happened on this branch"


# ============================================ the three ways to ask agree


def test_the_three_readers_of_a_run_agree_about_where_it_is(tmp_path: Path) -> None:
    """`progress_of` counts writes, `waiting_at` asks the graph, and `history`
    lists the checkpoints. Three stories built three of them and nothing compared
    them — which is how a run comes to be described two ways at once."""
    store = tmp_path / "run.sqlite"
    with for_development(store) as saver:
        graph = assemble(build(), saver)
        start(graph, "agree")

        assert progress_of(saver, "agree").checkpoints == len(history(graph, "agree"))
        assert waiting_at(graph, "agree") == history(graph, "agree")[-1].next_nodes

"""A v3 run that dies inside a named node, driving the **real** node functions.

`crashing_run.py` proves the graph checkpoints: its steps return canned mappings,
so what it kills is the wiring. This one kills the adapters. Every node here is
`pipeline/nodes.py` bound over `real_nodes.assembled`, so a resume has to
rehydrate a ledger, re-read an append-only channel and pick a finding back up --
none of which a canned dict ever asks for.

**`os._exit` rather than an exception**, which is the point of both harnesses. An
exception unwinds, runs `finally` blocks, flushes buffers and closes the SQLite
connection -- a graceful shutdown wearing the word crash. `os._exit` skips all of
it, so what the checkpoint holds afterwards is what a real kill would have left.

Run as `python crashing_real_run.py <store> <run-id> <workspace> <node|->`. With
`-` the run completes, which is the uninterrupted control the kills are compared
against.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.orchestrator.resume import DURABILITY
from coldfix.pipeline.graph import Node, Step, Wiring, build
from coldfix.pipeline.nodes import bind
from coldfix.pipeline.state import PipelineState
from pipeline.real_nodes import ByCaller, assembled


def dying(name: str, step: Step, die_at: str | None) -> Step:
    """The real step, with a kill in front of it.

    In front rather than behind: a node killed *after* its work but before its
    update is written is the case at-least-once execution already covers, and
    what a crash test needs is the node contributing nothing at all.
    """

    def crash_or_run(state: PipelineState) -> Mapping[str, object]:
        if name == die_at:
            # No flush, no `finally`, no connection close. A real kill.
            os._exit(9)
        return step(state)

    return crash_or_run


def wiring(workspace: Path, die_at: str | None) -> Wiring:
    real = bind(assembled(workspace, ByCaller())).steps()
    return Wiring(**{item.value: dying(item.value, real[item], die_at) for item in Node})


def main() -> int:
    store, run_id, workspace, die_at = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    with for_development(store) as checkpointer:
        graph = build(
            wiring(Path(workspace), None if die_at == "-" else die_at),
            checkpointer=checkpointer,
            gated=False,
        )
        # `durability="sync"` or the checkpoint writes go to a background
        # executor that `os._exit` never lets run -- one checkpoint survives
        # holding nothing, the resume restarts from the beginning, and it
        # reaches the same answer. Every test of resume then passes while the
        # crash saved nothing.
        final = graph.invoke(PipelineState(), thread(run_id), durability=DURABILITY)
        print(json.dumps({"route": final["route"], "resolved": final["resolved"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A v3 pipeline run that dies inside a named node. Launched as a subprocess.

**`os._exit` rather than an exception**, which is the whole point of this file.
An exception unwinds, runs `finally` blocks, flushes buffers and closes the
SQLite connection cleanly -- a graceful shutdown wearing the word crash.
`os._exit` skips every one of those, so what the checkpoint holds afterwards is
what a real kill would have left.

Run as `python crashing_run.py <store> <run-id> <node|->`. With `-` the run
completes, which is the uninterrupted control the kills are compared against.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.orchestrator.resume import DURABILITY
from coldfix.pipeline.graph import Node, Wiring, build
from coldfix.pipeline.state import PipelineState

UPDATES: Mapping[str, Mapping[str, object]] = {
    "refuse": {"route": "proceed", "project": {"image": "python:3.12-slim", "tier": 1}},
    "scan": {
        "route": "findings",
        "measurements": [{"id": "m-1", "wall": 2.41}],
        "findings": {"f-1": {"kind": "repeated_query", "payoff": 0.784}},
        "coverage": {"driven": ["GET /api/books"]},
    },
    "audit_finding": {"route": "sound"},
    "optimize": {
        "route": "candidate",
        "candidates": [{"id": "c-1", "approach": "prefetch", "seconds": 0.61, "won": False}],
        "repaired": {"id": "c-3", "approach": "hoist"},
    },
    "audit_patch": {"route": "clean", "audited": {"attacks": 12, "broke": 0}},
    "ship": {"route": "done", "resolved": {"f-1": "shipped"}},
}


def wiring(die_at: str | None) -> Wiring:
    def make(name: str) -> Any:
        def step(state: PipelineState) -> Mapping[str, object]:
            if name == die_at:
                # No flush, no `finally`, no connection close. A real kill.
                os._exit(9)
            return dict(UPDATES.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


def main() -> int:
    store, run_id, die_at = sys.argv[1], sys.argv[2], sys.argv[3]
    with for_development(store) as checkpointer:
        graph = build(
            wiring(None if die_at == "-" else die_at), checkpointer=checkpointer, gated=False
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

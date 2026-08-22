"""A graph run that dies inside a named node. Launched as a subprocess by S-12.3.

**`os._exit` rather than an exception**, and that is the whole point of this file.
AC 2 says *killing the process*: an exception unwinds, runs `finally` blocks,
flushes buffers and closes the SQLite connection cleanly — which is a graceful
shutdown wearing the word crash. `os._exit` skips every one of those, so what the
checkpoint file holds afterwards is what a real kill would have left.

Run as `python crashing_run.py <store> <run-id> <node|->`. With `-` the run
completes, which is the uninterrupted control AC 3 compares against.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.graph import Node, Wiring, assemble
from coldfix.orchestrator.resume import start
from coldfix.state.checkpoint import CheckpointedState

FLAGGED: dict[str, Any] = {"shop.books.list": {"growth": "superlinear"}}

UPDATES: Mapping[str, Mapping[str, object]] = {
    "ground": {"project": {"adapter": "django"}},
    "screen": {"screening": FLAGGED},
    "investigate": {"experiments": [{"index": 1, "key": "exp-1", "summary": "queries flat"}]},
    "audit_finding": {"route": "REPAIR"},
    "repair": {"attempts": [{"approach": "prefetch", "failed": "still slow"}]},
    "audit_patch": {"route": "SHIP"},
    "ship": {"screening": {}, "route": None},
}


def build(die_at: str | None) -> Wiring:
    def make(name: str) -> Any:
        def step(state: CheckpointedState) -> Mapping[str, object]:
            if name == die_at:
                # No flush, no `finally`, no connection close. A real kill.
                os._exit(9)
            return dict(UPDATES.get(name, {}))

        return step

    return Wiring(**{item.value: make(item.value) for item in Node})


def main() -> int:
    store, run_id, die_at = sys.argv[1], sys.argv[2], sys.argv[3]

    # **A fourth argument, added by Epic 12's composition check.** S-12.3's tests
    # want an ungated run, because a run that parks at a gate never reaches the
    # nodes whose writes they check. The composition wants the opposite: the
    # epic's sentence is *durable execution across hours, crashes, and multi-day
    # human gates*, and a crash during a **gated** run is the join where those
    # two halves meet. Defaulted so every existing caller is unchanged.
    gated = len(sys.argv) > 4 and sys.argv[4] == "gated"

    with for_development(store) as saver:
        graph = assemble(
            build(None if die_at == "-" else die_at),
            saver,
            gated=gated,
            early_review=gated,
        )
        # Through `start` rather than `invoke`, so this run is as durable as a
        # real one. Calling `invoke` here would test a configuration nothing
        # ships with — and the default loses every checkpoint to the kill.
        final = start(graph, run_id)
    print(json.dumps(dict(final), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

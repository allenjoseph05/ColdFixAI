"""Rewinding the code and keeping the learning.

Epic 12, S-12.6. `08-audit.md` F5:

> Time travel restores state at checkpoint T. But the reason for rewinding is a
> failure discovered at T+n — and that failure record lives in the state being
> discarded. We rewind and the agent repeats the same attempt. **This inverts the
> intent.** We want to rewind the *code* and keep the *learning*.

**F5 was measured before it was fixed, and it reproduced exactly.** Driving a run
to completion and inspecting its history: at the checkpoint whose next step is
`repair`, `attempts` is `[]`; resuming from there produces the same approach a
second time. That is the defect in one paragraph, and the test for it asserts the
repetition rather than describing it.

**The fix F5 names is the split, and S-6.1 and S-6.2 already built it.** The
persistent store is a separate database — `refuse_shared_store` will not let it
be the checkpoint file — written append-only and never rolled back by a restore.
Nothing this module does can reach it, which is the property rather than a
promise: a rewind operates on the checkpointer, and the checkpointer is not where
the learning lives.

**What was missing is the other half of the sentence.** Splitting the state stops
the knowledge being *destroyed*; it does not put it back in front of the Surgeon.
`repair` began each call with no prior attempts, so a rewound run was handed the
earlier code state and none of the later knowledge. `remembered` is that seam,
and S-13.3 is the story that fills it from the store on every run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from coldfix.orchestrator.checkpointing import thread
from coldfix.orchestrator.graph import Node


class RewindError(Exception):
    """The run could not be rewound."""


class NoSuchCheckpointError(RewindError):
    """No checkpoint sits where the caller asked to rewind to.

    Named rather than folded into a generic error, because the two ways to get
    here want different responses: a node the run never reached is a caller
    asking for a point in a story that did not happen, and a thread with no
    history at all is a run that was never started.
    """


@dataclass(frozen=True)
class Point:
    """One checkpoint, and what the run would have done next from it."""

    config: RunnableConfig
    """What `invoke(None, ...)` must be given to resume from *here* rather than
    from the end. The checkpoint id is inside it, and that is the whole
    difference between a rewind and a resume."""

    next_nodes: tuple[str, ...]
    values: Mapping[str, Any]

    @property
    def checkpoint_id(self) -> str:
        found = self.config.get("configurable", {}).get("checkpoint_id")
        return str(found) if found is not None else ""

    def describe(self) -> str:
        written = ", ".join(sorted(name for name, value in self.values.items() if value))
        return (
            f"{self.checkpoint_id[:8]}: next {self.next_nodes or '(end)'}; "
            f"holds {written or 'nothing'}"
        )


def history(graph: Any, run_id: str) -> tuple[Point, ...]:  # noqa: ANN401 - see `assemble`
    """Every checkpoint for a run, **oldest first**.

    LangGraph yields newest first, which is the right default for *what happened
    last* and the wrong one here: a rewind is chosen by looking forward through
    what a run did, and a reader scanning for *the point before repair* is
    reading a story. Reversed once, here, rather than at each call site.
    """
    return tuple(
        reversed(
            [
                Point(config=item.config, next_nodes=tuple(item.next), values=dict(item.values))
                for item in graph.get_state_history(thread(run_id))
            ]
        )
    )


def before(graph: Any, run_id: str, node: Node) -> Point:  # noqa: ANN401 - see `assemble`
    """The checkpoint to rewind to so the run re-enters `node`.

    **Addressed by the node rather than by a checkpoint id**, because that is how
    the decision is actually made: somebody rewinds *to before the repair*, not to
    `1f19e12b`. An id is a fact about one run and the node is the thing two runs
    have in common.

    **The first such point, not the last.** A graph with a cycle visits `repair`
    more than once — S-11.7 sends a broken patch back — and *before the repair*
    means before the first of them. A run that has already been rewound has
    several, and taking the first keeps *rewind to before the repair* meaning the
    same point however many times it is asked.

    **For a gated node this is one step earlier, and Epic 12's composition check
    is what found why.** The checkpoint whose next step is `repair` is the same
    checkpoint a gated run *parks* at, and `invoke(None, ...)` from there is what
    a human approving the gate does. So rewinding to it ran repair immediately —
    measured: a fresh run parked at `repair`, and a rewind to the identical point
    ran straight through to `ship`. **A rewind to a gate silently counted as
    approving it**, which is the opposite of what somebody reconsidering the
    direction is asking for.

    Targeting the checkpoint one earlier makes the run *re-enter* the node, and
    the interrupt fires as it does on any other entry. That re-runs the preceding
    phase, which costs a model call — so it is done only where a gate would
    otherwise be skipped, and `interrupt_before_nodes` is asked rather than
    assumed.

    Raises:
        NoSuchCheckpointError: the run never stood before that node.
    """
    points = history(graph, run_id)
    for index, point in enumerate(points):
        if node.value in point.next_nodes:
            return points[index - 1] if _gated(graph, node) and index else point

    reached = sorted({name for item in points for name in item.next_nodes})
    message = (
        f"this run never stood before {node.value!r}, so there is no checkpoint to rewind to. "
        f"It was about to take: {reached or 'nothing — nothing was ever checkpointed'}"
    )
    raise NoSuchCheckpointError(message)


def _gated(graph: Any, node: Node) -> bool:  # noqa: ANN401 - see `assemble`
    """Whether the compiled graph parks before `node`.

    Asked of the graph rather than passed in, because the answer is a property of
    how it was compiled and a caller repeating it would be a second statement of
    the same fact — wrong the first time somebody assembles with different gates.
    """
    return node.value in set(getattr(graph, "interrupt_before_nodes", ()) or ())


def rewind(graph: Any, point: Point) -> Mapping[str, Any]:  # noqa: ANN401 - see `assemble`
    """Re-run from `point`, with the checkpointed state as it was there.

    **`invoke(None, config)` where the config carries a checkpoint id**, which is
    the same call S-12.3's `resume` makes with a config that does not. That story
    recorded how invisible the neighbouring mistake is — `invoke(None, ...)`
    resumes and `invoke(state, ...)` restarts, and both return a final state — and
    this is the third member of that family: the same function again, differing
    only in which checkpoint the config names.

    **Nothing here touches the persistent store**, and that is F5's guarantee
    holding rather than being asserted. The store is a different database that
    `refuse_shared_store` will not let be the checkpoint file, so a rewind cannot
    reach it — the learning survives because it was never in the thing being
    restored.
    """
    return dict(graph.invoke(None, point.config))


def already_failed(
    discarded: Sequence[Mapping[str, Any]],
    retried: Sequence[Mapping[str, Any]],
    *,
    key: str = "approach",
) -> tuple[str, ...]:
    """Approaches the rewound run tried that the discarded state knew had failed.

    **A rewind forks the history; it does not append to it.** That was measured
    rather than assumed, and the first version of this function assumed wrong: it
    counted duplicates *within* one attempts list, and after a rewind that list
    holds one entry, because the new branch starts from the checkpoint's `[]` and
    adds its own. Nothing is repeated inside either branch — the repetition is
    *between* them, which is exactly why F5 says nobody notices.

    So the comparison takes both sides: what the rewind threw away, and what the
    run did instead. An approach in both is knowledge that was paid for and then
    bought again.

    Reported rather than prevented, which is S-12.3's construction for
    at-least-once delivery and the same argument: silently filtering would hide
    that a rewound run was handed no memory.
    """
    known = {str(item.get(key, "")) for item in discarded}
    return tuple(
        sorted({str(item.get(key, "")) for item in retried} & known - {""}),
    )

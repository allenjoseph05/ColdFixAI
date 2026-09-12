"""Seven nodes, seven routers, and nothing that costs a token.

S-24.1, and S-28.2 for the seventh. LangGraph sequences and persists; it never
touches a prompt. That is the rule that makes it worth having here rather than
something to work around: the orchestration is free, and the whole cost of a run
sits inside the nodes that call a model.

**`StateGraph` only, never a prebuilt agent.** A framework that decides what goes
into a message list decides the thing this project exists to control. Every node
is a function supplied from outside, and this module knows what order they run
in and nothing about what they do.

**`ground` is its own node** (ADR 175). Making a program measurable and finding
waste in it are two jobs with different bounds, different tiers and different
failure modes, and the boundary between them was already a hard fact in
`agent/scan.py` -- it just was not a node, so nothing was checkpointed there and
nothing could resume or rewind to it. Now a crash during measurement resumes at
`scan` instead of installing the project again, and the loops back from
`audit_finding` and `ship` return to `scan` for the same reason.

**Every ending is a real answer.** Refused, unmeasurable, nothing found,
found-but-not-fixable, escalated, shipped. Every one of the seven nodes can end a
run, and none of those endings is a failure -- which is why an unrecognised route
raises instead of quietly falling through to `END`. A run must not stop silently
at the point somebody was supposed to be told something.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from coldfix.pipeline.state import PipelineState
from coldfix.state.checkpoint import node


class PipelineError(Exception):
    """The graph could not be assembled."""


class UnknownRouteError(PipelineError):
    """A node wrote a route no edge leaves by.

    Defaulting to `END` here would end the run at exactly the point a person was
    supposed to be told something, and it would look like a clean finish.
    """

    def __init__(self, source: str, route: object, allowed: Mapping[str, str]) -> None:
        super().__init__(
            f"{source} wrote route {route!r}, which is not one this graph leaves by. "
            f"Allowed: {', '.join(sorted(allowed))}. An unrecognised route is refused rather "
            "than defaulted, because a typo that fell through to END would end the run "
            "silently and look like success."
        )


class MissingStepError(PipelineError):
    """A node has nothing behind it."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            f"no step supplied for {missing}. A node with nothing behind it compiles and "
            "returns an empty update, so the run passes straight through the phase and the "
            "state never gains what that phase produces."
        )


class NowhereToParkError(PipelineError):
    """The graph is told to wait for a person and has nowhere to wait."""

    def __init__(self, parks: list[str]) -> None:
        super().__init__(
            f"a graph that parks at {sorted(parks)} needs a checkpointer. `interrupt_before` "
            "parks the run in the checkpoint and waits, so with nowhere to park the run stops "
            "there and cannot be resumed -- the approval a person gives on Thursday has nothing "
            "to return to. Pass a checkpointer, or say `gated=False` if this is a test of shape."
        )


class Step(Protocol):
    """One node's work: read the state, do the thing, return the update.

    A `Protocol` with a *named* parameter rather than a `Callable`, because
    LangGraph's node protocol declares `__call__(self, state: ...)` and a plain
    `Callable`'s parameters are positional-only -- which makes every correctly
    annotated node fail to type-check at `add_node` while an unannotated lambda
    passes.
    """

    def __call__(self, state: PipelineState) -> Mapping[str, object]: ...


class Node(StrEnum):
    """The seven, in the order they first run."""

    REFUSE = "refuse"
    GROUND = "ground"
    """Make the program measurable: write a driver and measure until the harness
    says the workload repeats. Only a verified measurement ends this node, and a
    program that cannot be made to measure ends the run here rather than in
    `scan` (ADR 175)."""

    SCAN = "scan"
    AUDIT_FINDING = "audit_finding"
    OPTIMIZE = "optimize"
    AUDIT_PATCH = "audit_patch"
    SHIP = "ship"


@dataclass(frozen=True)
class Wiring:
    """What each node does. Supplied, never built here."""

    refuse: Step
    ground: Step
    scan: Step
    audit_finding: Step
    optimize: Step
    audit_patch: Step
    ship: Step

    def steps(self) -> Mapping[Node, Step]:
        return {item: getattr(self, item.value) for item in Node}


# Each router's routes, and where each leads. Enumerated rather than inferred:
# a conditional edge whose destinations LangGraph has to work out is one whose
# reachable set nobody can see, and an unreachable node looks identical to a
# correctly wired one.
ROUTES: Mapping[Node, Mapping[str, str]] = {
    Node.REFUSE: {"proceed": Node.GROUND.value, "refused": END},
    Node.GROUND: {"runnable": Node.SCAN.value, "unmeasurable": END},
    Node.SCAN: {"findings": Node.AUDIT_FINDING.value, "nothing_found": END},
    Node.AUDIT_FINDING: {
        "sound": Node.OPTIMIZE.value,
        "needs_evidence": Node.SCAN.value,
        "unsound": END,
    },
    Node.OPTIMIZE: {"candidate": Node.AUDIT_PATCH.value, "nothing_beat_baseline": END},
    Node.AUDIT_PATCH: {
        "clean": Node.SHIP.value,
        "another_round": Node.OPTIMIZE.value,
        "escalate": END,
    },
    Node.SHIP: {"more_findings": Node.SCAN.value, "done": END},
}
# Both loops back -- `needs_evidence` and `more_findings` -- return to `scan`
# and never to `ground`. The subject is already installed and its driver already
# verified; going back means investigating again, not setting the project up
# again, and the `runnable` channel is what carries that across (ADR 175).

GATEABLE = (Node.OPTIMIZE, Node.SHIP)
"""Where a run may be made to wait for a person. `ship` always, because nothing
reaches a repository unseen; `optimize` optionally, because that is where
spending starts."""


def router_for(source: Node) -> Callable[[PipelineState], str]:
    """Read the route the node wrote. Routers never decide anything themselves.

    A router that computed a verdict would be a second place the decision lives,
    and the two would drift. The node decided; this reads what it decided.
    """
    allowed = ROUTES[source]

    def route(state: PipelineState) -> str:
        written = state.route
        # `None` is the case that matters: a node that returned no route at all
        # is a node whose decision was never made, and it must not resolve to
        # whichever branch happens to be first.
        if written is None or written not in allowed:
            raise UnknownRouteError(source.value, written, allowed)
        return allowed[written]

    route.__name__ = f"after_{source.value}"
    return route


def build(
    wiring: Wiring,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    gated: bool = True,
    early_review: bool = False,
) -> CompiledStateGraph[PipelineState, Any, Any, Any]:
    """Assemble the seven nodes and compile.

    Raises:
        MissingStepError: a node has nothing behind it.
        NowhereToParkError: the graph parks for a person with no checkpointer.
    """
    steps = wiring.steps()
    missing = sorted(item.value for item in Node if steps.get(item) is None)
    if missing:
        raise MissingStepError(missing)

    graph: StateGraph[PipelineState, Any, Any, Any] = StateGraph(PipelineState)
    for name, step in steps.items():
        # Validated against *this* pipeline's channels. LangGraph drops an
        # unrecognised key silently, so a node writing to a channel that does
        # not exist would simply not write, and nothing would say so.
        graph.add_node(name.value, node(step, schema=PipelineState))

    parks = interrupts(gated=gated, early_review=early_review)
    if parks and checkpointer is None:
        raise NowhereToParkError(list(parks))

    graph.add_edge(START, Node.REFUSE.value)
    for source in Node:
        graph.add_conditional_edges(
            source.value, router_for(source), sorted(set(ROUTES[source].values()))
        )

    return graph.compile(checkpointer=checkpointer, interrupt_before=list(parks) or None)


def interrupts(*, gated: bool, early_review: bool) -> tuple[str, ...]:
    """Where the run waits for a person.

    Ordered rather than a set: the message somebody sees when they have no
    checkpointer names them, and *optimize, then ship* is the order they are
    thinking in.
    """
    if not gated:
        return ()
    parks = [Node.OPTIMIZE.value] if early_review else []
    parks.append(Node.SHIP.value)
    return tuple(parks)


def reachable() -> Mapping[str, tuple[str, ...]]:
    """Every node and where it can go. For a test, and for a person reading it."""
    return {
        source.value: tuple(sorted(set(destinations.values())))
        for source, destinations in ROUTES.items()
    }

"""Seven nodes, four edges that decide, and one compiled graph.

Epic 12, S-12.1. *Seven nodes wired: ground, screen, investigate, audit_finding,
repair, audit_patch, ship. Four routing functions implemented per `08-audit.md`.
Graph compiles and runs end to end.*

**Two of the four routing functions already existed**, which is what checking
before building is for. S-9.8's `verdict.route` decides where a finding audit
sends an investigation and S-11.7's `patchverdict.route` decides where a patch
audit sends a patch. Writing either again here would be a second answer to a
question those stories answer — and the two would disagree the first time a cap
moved.

**The decision is made in the node and read at the edge.** Both existing routers
take a `Budget`, whose caps live in the object and cannot be reconstructed from
the state's `budget` projection; a LangGraph conditional edge sees only state. So
a node makes the call where the budget is and writes it to the `route` channel,
and the edge reads it. The two routing functions this story *does* own — after
screening and after shipping — are the two that need nothing but state.

**The graph owns the shape and the epics own the work.** Every node is supplied by
the caller as a `Step`, because the seven compose entry points want eleven
different kinds of argument between them — two sessions, a client, a workbench, a
budget, a chain — and threading all of them through this module would make it the
place every epic's signature is repeated. `Wiring` is the seam, and it is what
lets the graph be run end to end without a container.

**Every node is registered through S-6.3's `node`**, which that story asked for in
as many words: *`assemble` in S-12.1 should register nodes through this and nothing
else.* It is what turns a write to a channel that does not exist from a silent
no-op into a failure.

**Screening returning nothing is an answer, not a dead end.** `00-BRIEF.md` §9:
*screened nine workloads, nothing found ships as an answer.* The edge after
screening goes to `END`, and the run is a null result rather than an error.

**After a ship, only the touched workloads are re-screened** — `08-audit.md` F14.
The rest keep their measurements, which is why S-6.3 keyed `screening` by workload
id: a flat sequence could not be filtered that way, and the composition check for
Epic 6 is what found it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from coldfix.audit.patchverdict import Route as PatchRoute
from coldfix.audit.verdict import Route as FindingRoute
from coldfix.state.checkpoint import CheckpointedState, node


class Step(Protocol):
    """One node's work: read the state, do the epic's thing, return the update.

    **A `Protocol` with a named parameter, not `Callable[[Mapping], Mapping]`**,
    and S-6.3 wrote down why before this story existed: LangGraph's node protocol
    declares `__call__(self, state: ...)` with a *named* parameter, so a plain
    `Callable` — whose parameters are positional-only — fails to type-check at
    `add_node`. The alias was written the wrong way first and mypy caught it at
    exactly the line that note predicted.

    The parameter is a `CheckpointedState` because that is what LangGraph hands a
    node when the graph is built over a Pydantic schema — a mapping was the first
    guess and `add_node` rejected it, since the node input has to be the schema
    type. The *return* is still a mapping, and it is checked: that is `node`'s job
    and the reason nothing registers a raw function.
    """

    def __call__(self, state: CheckpointedState) -> Mapping[str, object]: ...


class GraphError(Exception):
    """The graph could not be assembled."""


class Node(StrEnum):
    """The seven nodes of `02-architecture.md`'s flow, in the order they run."""

    GROUND = "ground"
    SCREEN = "screen"
    INVESTIGATE = "investigate"
    AUDIT_FINDING = "audit_finding"
    REPAIR = "repair"
    AUDIT_PATCH = "audit_patch"
    SHIP = "ship"


@dataclass(frozen=True)
class Wiring:
    """What each node actually does. Supplied, never built here.

    The seven compose entry points want eleven different kinds of argument between
    them. Threading those through this module would make the graph the one place
    every epic's signature is repeated, and every change to any of them would land
    here. This is the seam instead.
    """

    ground: Step
    screen: Step
    investigate: Step
    audit_finding: Step
    repair: Step
    audit_patch: Step
    ship: Step

    def steps(self) -> Mapping[Node, Step]:
        return {item: getattr(self, item.value) for item in Node}


def flagged(state: CheckpointedState) -> tuple[str, ...]:
    """Workload ids screening flagged as worth investigating.

    Reads the mapping S-6.3 keys by workload id. A screening entry that is falsy —
    no flag, no growth — is not a candidate, and an empty result overall is a null
    result rather than a failure.
    """
    screening = state.screening or {}
    return tuple(sorted(name for name, entry in screening.items() if entry))


def after_screen(state: CheckpointedState) -> str:
    """**Routing function 1.** Investigate something, or finish with a null result.

    `00-BRIEF.md` §9 makes *screened nine workloads, nothing found* shippable
    output, so nothing flagged ends the run rather than raising. An orchestrator
    that treated an empty screen as an error would turn the project's own
    non-negotiable into a crash.
    """
    return Node.INVESTIGATE.value if flagged(state) else END


def after_audit_finding(state: CheckpointedState) -> str:
    """**Routing function 2.** S-9.8's decision, read rather than re-derived.

    That story's `route` takes a `Budget` and returns a `Routing`; the node calls
    it and writes the result here. This maps its three destinations onto the graph:
    repair a sound finding, go back for more experiments on an unsound one that
    still has budget, and stop otherwise.

    An unrecognised route is refused rather than defaulted. A typo that fell
    through to `END` would end a run silently at the point a human was supposed to
    be told something.
    """
    decided = _decision(state, FindingRoute)
    if decided is FindingRoute.REPAIR:
        return Node.REPAIR.value
    if decided is FindingRoute.INVESTIGATE:
        return Node.INVESTIGATE.value
    return END


def after_audit_patch(state: CheckpointedState) -> str:
    """**Routing function 3.** S-11.7's decision, likewise read rather than redone.

    Ship a clean patch, return a broken one to the Surgeon while a round remains,
    and escalate anything else — which ends this run, because an escalation is a
    person's turn rather than another node's.
    """
    decided = _decision(state, PatchRoute)
    if decided is PatchRoute.SHIP:
        return Node.SHIP.value
    if decided is PatchRoute.RETURN_TO_SURGEON:
        return Node.REPAIR.value
    return END


def after_ship(state: CheckpointedState) -> str:
    """**Routing function 4.** `08-audit.md` F14 — re-screen, but only what changed.

    *After `ship`, the graph returns to `screen`. But the code has changed — every
    prior screening measurement is now stale. The spec never decided whether to
    re-screen. Fix: re-screen only the workloads whose files the patch touched.*

    The invalidation is the ship node's job, because it is the thing that knows
    which files the patch touched. This function reads what is left: something
    still flagged means another pass, nothing left means the run is done.

    **The state is what makes F14 expressible at all.** Epic 6's composition check
    changed `screening` from a sequence to a mapping keyed by workload id for
    exactly this — a flat sequence of opaque entries cannot be filtered per
    workload, so the policy had a correct answer with nowhere to go.
    """
    return Node.SCREEN.value if flagged(state) else END


ROUTERS: Mapping[Node, Callable[[CheckpointedState], str]] = {
    Node.SCREEN: after_screen,
    Node.AUDIT_FINDING: after_audit_finding,
    Node.AUDIT_PATCH: after_audit_patch,
    Node.SHIP: after_ship,
}
"""AC 2's four, as data. The other three nodes have one successor each and need no
function — a conditional edge with a single destination is a decision nobody is
making, written as though somebody were."""

LINEAR: Mapping[Node, Node] = {
    Node.GROUND: Node.SCREEN,
    Node.INVESTIGATE: Node.AUDIT_FINDING,
    Node.REPAIR: Node.AUDIT_PATCH,
}
"""The three edges that do not decide. **`investigate` goes to the finding audit
and never straight to repair** — `08-audit.md` F2 is that nobody audits the
diagnosis, only the patch, and the fix is this node between those two."""


def assemble(
    wiring: Wiring,
    # LangGraph's saver and its compiled graph are generic over the state schema,
    # and their parameters have moved between releases; naming either here would
    # pin this module to one version's spelling of a type nothing introspects.
    checkpointer: Any = None,  # noqa: ANN401
    *,
    gated: bool = True,
    early_review: bool = True,
) -> Any:  # noqa: ANN401
    """Wire the seven nodes and compile. AC 1 and AC 3.

    Every node goes through S-6.3's `node`, which that story asked for by name:
    a write to a channel the state does not have is dropped silently by LangGraph,
    so the check has to be on the way out of every node rather than at any one
    caller.

    **`checkpointer` is a compile-time argument and there is no runtime
    equivalent.** S-12.2 reached for `with_config(checkpointer=...)` first, which
    is accepted, changes nothing, and writes no checkpoints — a run that looks
    persisted and is not. Passing `None` compiles a graph that keeps no history,
    which is right for a unit test of the shape and wrong for anything that has to
    resume.

    **`gated` defaults to on and is not a trust level.** S-12.4 puts
    `interrupt_before=["ship"]` at trust level 0, and S-13.4's third criterion is
    that *new projects start at level 0 regardless of cross-project history* — so
    until that ledger exists, level 0 is the only value any project can be at and
    the gate is unconditional. A `trust: int` parameter here would have exactly
    one reachable value, and the danger is not that nobody could flip it: it is
    that a caller **could**, turning the gate off with no ledger to justify it.
    What this parameter is for is the unit test that needs a graph which runs to
    completion, and its name says so.

    **`early_review` is S-12.5's, and the asymmetry between the two is the
    decision.** F16: *`interrupt_before=["ship"]` means the human reviews after
    grounding, screening, investigation, repair and audit are all paid for — if
    they would have rejected the direction, the whole budget is gone.* So this one
    parks before `repair`, where a person can still decline to spend it.

    Its AC says **optional** where S-12.4's does not, and that word is doing work:
    the ship gate guards an irreversible outward act and the early one guards a
    budget. An operator running unattended may reasonably decline the second, and
    the worst case is euros. Declining the first would ship a patch nobody read.
    A parameter that could turn *that* off is the thing S-12.4 refused, and this
    one existing does not make that one exist.

    An ungated graph with no checkpointer is the shape S-12.1 tested. An
    interrupted one without a checkpointer cannot resume at all, so the arguments
    are related: `interrupt_before` parks the run in the checkpoint, and there is
    nowhere to park without one.

    Raises:
        GraphError: a node has no step, a router names a node that is not in the
            graph, or the run is interrupted with nothing to checkpoint into.
    """
    steps = wiring.steps()
    missing = sorted(item.value for item in Node if steps.get(item) is None)
    if missing:
        message = (
            f"no step supplied for {missing}. A node with nothing behind it compiles and "
            "returns an empty update, so the run would pass straight through the phase and "
            "the state would simply never gain what that phase produces"
        )
        raise GraphError(message)

    graph = StateGraph(CheckpointedState)
    for name, step in steps.items():
        graph.add_node(name.value, node(step))

    parks = _interrupts(gated=gated, early_review=early_review)
    if parks and checkpointer is None:
        message = (
            f"a graph that parks at {sorted(parks)} needs a checkpointer. `interrupt_before` "
            "parks the run in the checkpoint and waits for a person, so with nowhere to park the "
            "run stops there and cannot be resumed — the approval a human gives on Thursday has "
            "nothing to return to. Pass a checkpointer, or say `gated=False, early_review=False` "
            "if this is a test of the shape"
        )
        raise GraphError(message)

    graph.add_edge(START, Node.GROUND.value)
    for source, destination in LINEAR.items():
        graph.add_edge(source.value, destination.value)
    for source, router in ROUTERS.items():
        graph.add_conditional_edges(source.value, router, _destinations(router))

    return graph.compile(checkpointer=checkpointer, interrupt_before=list(parks) or None)


def _interrupts(*, gated: bool, early_review: bool) -> tuple[str, ...]:
    """Where this graph parks, in the order the run reaches them.

    **`gated` is the master switch and `early_review` narrows it**, which is not
    how the first draft read it. Two independent flags meant *no interrupts at
    all* took two arguments, and fifteen existing tests said `gated=False` and
    parked at `repair` anyway — the shape of a switch that does not do what its
    one obvious use implies. So `gated=False` removes every gate, which is what a
    test of the graph's shape wants, and `early_review=False` keeps the ship gate
    and drops the early one, which is what an operator running unattended wants.

    Ordered rather than a set, because the message a caller sees when it has no
    checkpointer names them and *repair, then ship* is the order somebody
    debugging a parked run is thinking in.
    """
    if not gated:
        return ()
    parks = [Node.REPAIR.value] if early_review else []
    parks.append(Node.SHIP.value)
    return tuple(parks)


def _destinations(router: Callable[[CheckpointedState], str]) -> list[str]:
    """Where a router may send the run, declared so LangGraph can draw the graph.

    Enumerated rather than left implicit: a conditional edge whose destinations
    LangGraph has to infer is one whose reachable set nobody can see, and an
    unreachable node would look identical to a correctly wired one.
    """
    return sorted(_REACHABLE[router])


_REACHABLE: Mapping[Callable[[CheckpointedState], str], set[str]] = {
    after_screen: {Node.INVESTIGATE.value, END},
    after_audit_finding: {Node.REPAIR.value, Node.INVESTIGATE.value, END},
    after_audit_patch: {Node.SHIP.value, Node.REPAIR.value, END},
    after_ship: {Node.SCREEN.value, END},
}


def _decision[RouteEnum: StrEnum](
    state: CheckpointedState, routes: type[RouteEnum]
) -> RouteEnum | None:
    """Read the route a node wrote, or `None` where it wrote nothing usable.

    **Refused rather than defaulted**, and the difference matters at exactly one
    place: a value this does not recognise returns `None`, and every caller sends
    `None` to `END`. That is deliberate — a run that stops is a run somebody looks
    at, where a run that guessed a destination would carry on with a decision
    nobody made.
    """
    written = state.route
    if not isinstance(written, str):
        return None
    for candidate in routes:
        if written in (candidate.name, candidate.value):
            return candidate
    return None


def decided(route: StrEnum) -> Mapping[str, object]:
    """What a node writes so its edge can read the decision it already made.

    A helper rather than a convention, because *which spelling of the route goes
    in the channel* is the sort of thing two nodes come to disagree about — and
    the disagreement shows up as a run that ends early rather than as an error.
    """
    return {"route": route.name}


def null_result(reason: str) -> Mapping[str, object]:
    """The update a node writes when it has nothing to hand on.

    Named because `00-BRIEF.md` §9 makes this an *answer*, and an update that
    cleared `target` without saying why would be indistinguishable from a node
    that failed to write.
    """
    return {"target": None, "route": None, "flags": [{"null_result": reason}]}


def order() -> Sequence[str]:
    """The seven node names, for a caller checking what was registered."""
    return tuple(item.value for item in Node)

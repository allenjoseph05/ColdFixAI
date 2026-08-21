"""Epic 12, S-12.1 — graph assembly.

*Seven nodes wired: ground, screen, investigate, audit_finding, repair,
audit_patch, ship. Four routing functions implemented per `08-audit.md`. Graph
compiles and runs end to end.*

The graph is compiled and **executed** here with steps that record what they were
given. That is the whole point of `Wiring`: the shape can be run without a
container, a model or a repository, so a routing mistake shows up as a wrong path
rather than as an expensive one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from langgraph.graph import END

from coldfix.audit.patchverdict import Route as PatchRoute
from coldfix.audit.verdict import Route as FindingRoute
from coldfix.orchestrator.graph import (
    LINEAR,
    ROUTERS,
    GraphError,
    Node,
    Wiring,
    after_audit_finding,
    after_audit_patch,
    after_screen,
    after_ship,
    assemble,
    decided,
    flagged,
    null_result,
    order,
)
from coldfix.state.checkpoint import CheckpointedState, UnknownChannelError

FLAGGED = {"shop.books.list": {"growth": "superlinear"}}
CLEARED = {"shop.books.list": None}


def state(**fields: Any) -> CheckpointedState:
    return CheckpointedState(**fields)


class Recorder:
    """A step that records the visit and applies a canned update."""

    def __init__(self, name: Node, update: Mapping[str, object] | None = None) -> None:
        self.name = name
        self._update = dict(update or {})
        self.visits: list[CheckpointedState] = []

    def __call__(self, state: CheckpointedState) -> Mapping[str, object]:
        self.visits.append(state)
        return dict(self._update)

    @property
    def seen(self) -> int:
        return len(self.visits)


def wiring(**updates: Mapping[str, object]) -> tuple[Wiring, dict[Node, Recorder]]:
    recorders = {item: Recorder(item, updates.get(item.value)) for item in Node}
    return Wiring(**{item.value: recorders[item] for item in Node}), recorders


# ============ AC 1 — the seven nodes


def test_the_seven_nodes_of_the_flow_are_registered() -> None:
    graph, _ = wiring()
    compiled = assemble(graph, gated=False)

    registered = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    assert registered == {item.value for item in Node}
    assert order() == (
        "ground",
        "screen",
        "investigate",
        "audit_finding",
        "repair",
        "audit_patch",
        "ship",
    )


def test_investigate_goes_to_the_finding_audit_and_never_straight_to_repair() -> None:
    """`08-audit.md` F2: nobody audits the diagnosis, only the patch. The fix is a
    node between those two, and this is the edge that makes it unavoidable."""
    assert LINEAR[Node.INVESTIGATE] is Node.AUDIT_FINDING
    assert LINEAR[Node.GROUND] is Node.SCREEN
    assert LINEAR[Node.REPAIR] is Node.AUDIT_PATCH


def test_a_node_with_nothing_behind_it_is_refused() -> None:
    """It compiles and returns an empty update, so the run passes straight through
    the phase and the state simply never gains what it produces."""
    graph, _ = wiring()
    partial = Wiring(**{**{item.value: getattr(graph, item.value) for item in Node}, "ship": None})
    with pytest.raises(GraphError, match="no step supplied for"):
        assemble(partial, gated=False)


def test_every_node_is_registered_through_the_state_check() -> None:
    """S-6.3 asked for this by name: LangGraph drops a write to a channel that does
    not exist without a word, so the check has to be on the way out of every node."""
    graph, _ = wiring(ground={"nonesuch": 1})
    compiled = assemble(graph, gated=False)

    with pytest.raises(UnknownChannelError, match="keys the state does not have"):
        compiled.invoke(state())


# ============ AC 2 — the four routing functions


def test_there_are_exactly_four_routing_functions() -> None:
    """The other three nodes have one successor each. A conditional edge with a
    single destination is a decision nobody is making, written as though somebody
    were."""
    assert set(ROUTERS) == {Node.SCREEN, Node.AUDIT_FINDING, Node.AUDIT_PATCH, Node.SHIP}
    assert set(ROUTERS) | set(LINEAR) == set(Node)
    assert not set(ROUTERS) & set(LINEAR)


def test_nothing_flagged_ends_the_run_as_a_null_result() -> None:
    """`00-BRIEF.md` §9: *screened nine workloads, nothing found* ships as an answer.
    An orchestrator that treated an empty screen as an error would turn the
    project's own non-negotiable into a crash."""
    assert after_screen(state(screening={})) == END
    assert after_screen(state(screening=CLEARED)) == END
    assert after_screen(state(screening=FLAGGED)) == Node.INVESTIGATE.value


def test_the_finding_audit_route_is_s_9_8s_decision_read_back() -> None:
    """Its `route` takes a `Budget` whose caps cannot be reconstructed from state,
    so the node decides and the edge reads."""
    assert after_audit_finding(state(**decided(FindingRoute.REPAIR))) == Node.REPAIR.value
    assert after_audit_finding(state(**decided(FindingRoute.INVESTIGATE))) == Node.INVESTIGATE.value
    for route in FindingRoute:
        if route not in (FindingRoute.REPAIR, FindingRoute.INVESTIGATE):
            assert after_audit_finding(state(**decided(route))) == END


def test_the_patch_audit_route_is_s_11_7s_decision_read_back() -> None:
    assert after_audit_patch(state(**decided(PatchRoute.SHIP))) == Node.SHIP.value
    assert after_audit_patch(state(**decided(PatchRoute.RETURN_TO_SURGEON))) == Node.REPAIR.value
    assert after_audit_patch(state(**decided(PatchRoute.ESCALATE))) == END


def test_a_route_nobody_wrote_ends_the_run_rather_than_guessing() -> None:
    """A run that stops is a run somebody looks at; one that guessed a destination
    would carry on with a decision nobody made."""
    assert after_audit_finding(state()) == END
    assert after_audit_patch(state(route="not a route")) == END
    assert after_audit_patch(state(route="SHIP")) == Node.SHIP.value, "the real one still works"


def test_after_a_ship_only_what_is_still_flagged_is_re_screened() -> None:
    """`08-audit.md` F14. The invalidation is the ship node's job — it is the thing
    that knows which files the patch touched — and this reads what is left."""
    assert after_ship(state(screening=FLAGGED)) == Node.SCREEN.value
    assert after_ship(state(screening=CLEARED)) == END
    assert after_ship(state(screening={})) == END


def test_screening_is_read_per_workload_which_is_what_makes_f14_expressible() -> None:
    """Epic 6's composition check changed `screening` from a sequence to a mapping
    keyed by workload id for exactly this: a flat sequence of opaque entries cannot
    be filtered per workload, so the policy had a correct answer with nowhere to
    go."""
    mixed = {"kept": {"growth": "superlinear"}, "invalidated": None}
    assert flagged(state(screening=mixed)) == ("kept",)


# ============ AC 3 — the graph runs end to end


def test_a_null_screen_runs_ground_then_screen_and_stops() -> None:
    graph, seen = wiring()
    assemble(graph, gated=False).invoke(state())

    assert seen[Node.GROUND].seen == 1
    assert seen[Node.SCREEN].seen == 1
    assert seen[Node.INVESTIGATE].seen == 0, "nothing was flagged"


def test_a_finding_runs_the_whole_path_to_ship() -> None:
    """Ground, screen, investigate, **audit the finding**, repair, audit the patch,
    ship — and then stop, because the ship node cleared what it invalidated."""
    graph, seen = wiring(
        screen={"screening": FLAGGED},
        audit_finding=decided(FindingRoute.REPAIR),
        audit_patch=decided(PatchRoute.SHIP),
        ship={"screening": CLEARED, "route": None},
    )
    assemble(graph, gated=False).invoke(state())

    assert [item for item in Node if seen[item].seen] == list(Node)
    assert all(seen[item].seen == 1 for item in Node)


def test_a_broken_patch_goes_back_to_the_surgeon_and_is_audited_again() -> None:
    """The cycle S-11.7 authorises. It ends because the second audit escalates —
    the graph does not count rounds, the budget does."""
    audits = iter([decided(PatchRoute.RETURN_TO_SURGEON), decided(PatchRoute.ESCALATE)])

    class Cycling(Recorder):
        def __call__(self, state: CheckpointedState) -> Mapping[str, object]:
            self.visits.append(state)
            return dict(next(audits))

    graph, seen = wiring(
        screen={"screening": FLAGGED},
        audit_finding=decided(FindingRoute.REPAIR),
    )
    cycling = Cycling(Node.AUDIT_PATCH)
    graph = Wiring(**{**{item.value: seen[item] for item in Node}, "audit_patch": cycling})

    assemble(graph, gated=False).invoke(state())

    assert seen[Node.REPAIR].seen == 2, "back to the Surgeon once"
    assert cycling.seen == 2
    assert seen[Node.SHIP].seen == 0, "and never shipped"


def test_an_unsound_finding_goes_back_for_more_experiments() -> None:
    audits = iter([decided(FindingRoute.INVESTIGATE), decided(FindingRoute.ESCALATE)])

    class Cycling(Recorder):
        def __call__(self, state: CheckpointedState) -> Mapping[str, object]:
            self.visits.append(state)
            return dict(next(audits))

    graph, seen = wiring(screen={"screening": FLAGGED})
    cycling = Cycling(Node.AUDIT_FINDING)
    graph = Wiring(**{**{item.value: seen[item] for item in Node}, "audit_finding": cycling})

    assemble(graph, gated=False).invoke(state())

    assert seen[Node.INVESTIGATE].seen == 2
    assert seen[Node.REPAIR].seen == 0, "no patch is written on an unsound finding"


def test_the_state_a_node_receives_carries_what_earlier_nodes_wrote() -> None:
    """End to end means the channels join up, not only that the nodes ran."""
    graph, seen = wiring(
        ground={"project": {"adapter": "django"}},
        screen={"screening": FLAGGED},
        audit_finding=decided(FindingRoute.REPAIR),
        audit_patch=decided(PatchRoute.SHIP),
        ship={"screening": CLEARED, "route": None},
    )
    final = assemble(graph, gated=False).invoke(state())

    assert seen[Node.SCREEN].visits[0].project == {"adapter": "django"}
    assert seen[Node.REPAIR].visits[0].screening == FLAGGED
    assert final["project"] == {"adapter": "django"}


def test_an_append_only_channel_accumulates_across_nodes() -> None:
    """S-6.3's channels, exercised through the graph rather than directly — the
    experiment log is append-only and a node that replaced it would be rewriting
    history."""
    graph, _ = wiring(
        ground={"experiments": [{"index": 1}]},
        screen={"experiments": [{"index": 2}], "screening": {}},
    )
    final = assemble(graph, gated=False).invoke(state())

    assert [item["index"] for item in final["experiments"]] == [1, 2]


def test_a_null_result_is_an_answer_and_says_which() -> None:
    written = null_result("screened 9 workloads, nothing found")
    assert written["target"] is None
    assert written["route"] is None
    assert written["flags"] == [{"null_result": "screened 9 workloads, nothing found"}]


def test_decided_writes_the_name_so_two_nodes_cannot_disagree_on_spelling() -> None:
    """Which spelling of a route goes in the channel is the sort of thing two nodes
    come to disagree about, and the disagreement shows up as a run that ends early
    rather than as an error."""
    assert decided(PatchRoute.SHIP) == {"route": "SHIP"}
    assert after_audit_patch(state(**decided(PatchRoute.SHIP))) == Node.SHIP.value


def test_every_destination_a_router_can_choose_is_declared() -> None:
    """**The drawn graph is the only place an unreachable node is visible.** A
    conditional edge whose destinations LangGraph has to infer draws no edges at
    all, and a node nothing can reach then looks identical to a correctly wired
    one — which is exactly the mistake a graph diagram is read to catch."""
    drawn = assemble(wiring()[0], gated=False).get_graph()

    reachable: dict[str, set[str]] = {}
    for edge in drawn.edges:
        reachable.setdefault(edge.source, set()).add(edge.target)

    assert reachable["screen"] == {"investigate", "__end__"}
    assert reachable["audit_finding"] == {"repair", "investigate", "__end__"}
    assert reachable["audit_patch"] == {"ship", "repair", "__end__"}
    assert reachable["ship"] == {"screen", "__end__"}

    assert reachable["__start__"] == {"ground"}
    assert set(reachable) == {item.value for item in Node} | {"__start__"}, (
        "every node has somewhere to go"
    )

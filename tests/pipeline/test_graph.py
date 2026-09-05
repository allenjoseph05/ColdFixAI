"""S-24.1 and S-24.2.

The graph is exercised with steps that do nothing but write a route, so what is
under test is the wiring and not any node's work. Nothing here calls a model,
which is the point: orchestration is free, and a test that proved otherwise
would be testing something that should not exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from langgraph.graph import END

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.pipeline.graph import (
    GATEABLE,
    ROUTES,
    MissingStepError,
    Node,
    NowhereToParkError,
    Step,
    UnknownRouteError,
    Wiring,
    build,
    interrupts,
    reachable,
    router_for,
)
from coldfix.pipeline.state import PipelineState


def writes(route: str, **extra: object) -> Step:
    """A step that records it ran and says where to go next."""

    def step(state: PipelineState) -> Mapping[str, object]:
        return {"route": route, **extra}

    return step


def wiring(**routes: str) -> Wiring:
    defaults = {
        "refuse": "proceed",
        "scan": "nothing_found",
        "audit_finding": "unsound",
        "optimize": "nothing_beat_baseline",
        "audit_patch": "escalate",
        "ship": "done",
    }
    return Wiring(**{name: writes(routes.get(name, value)) for name, value in defaults.items()})


# ------------------------------------------------------------------ the shape


def test_there_are_six_nodes_and_every_one_has_a_router() -> None:
    """Six routers for six nodes: every node in this graph can end the run or
    send it somewhere other than the next one along, so none of them has a single
    unconditional successor."""
    assert len(Node) == 6
    assert set(ROUTES) == set(Node)


def test_every_destination_is_a_node_or_the_end() -> None:
    """Enumerated, never inferred. A conditional edge whose reachable set nobody
    can see makes an unreachable node look identical to a wired one."""
    names = {item.value for item in Node} | {END}
    for source, destinations in ROUTES.items():
        for route, destination in destinations.items():
            assert destination in names, f"{source}/{route} goes nowhere"


def test_four_of_the_six_nodes_can_end_the_run() -> None:
    """Refused, unmeasurable or nothing-found, unsound, nothing-beat-baseline,
    escalated, done. Every one of those is an answer."""
    enders = {source.value for source, routes in ROUTES.items() if END in routes.values()}
    assert enders == {"refuse", "scan", "audit_finding", "optimize", "audit_patch", "ship"}


def test_the_graph_loops_back_so_one_run_can_chase_several_findings() -> None:
    assert reachable()["ship"] == (END, "scan")
    assert "scan" in reachable()["audit_finding"]
    assert "optimize" in reachable()["audit_patch"]


# --------------------------------------------------------------- the routers


def test_a_router_reads_the_route_the_node_wrote_and_decides_nothing() -> None:
    """A router that computed a verdict would be a second place the decision
    lives, and the two would drift."""
    route = router_for(Node.AUDIT_FINDING)
    assert route(PipelineState(route="sound")) == "optimize"
    assert route(PipelineState(route="needs_evidence")) == "scan"
    assert route(PipelineState(route="unsound")) == END


def test_an_unrecognised_route_raises_rather_than_ending_the_run() -> None:
    """A typo that fell through to END would end the run at exactly the point
    somebody was supposed to be told something, and it would look like success."""
    with pytest.raises(UnknownRouteError, match="not one this graph leaves by"):
        router_for(Node.SCAN)(PipelineState(route="sound"))


def test_a_node_that_wrote_no_route_at_all_raises() -> None:
    """The decision was never made. It must not resolve to whichever branch
    happens to be listed first."""
    with pytest.raises(UnknownRouteError):
        router_for(Node.SHIP)(PipelineState())


def test_the_refusal_names_what_would_have_been_accepted() -> None:
    with pytest.raises(UnknownRouteError) as caught:
        router_for(Node.OPTIMIZE)(PipelineState(route="nonsense"))
    assert "candidate" in str(caught.value)
    assert "nothing_beat_baseline" in str(caught.value)


# ------------------------------------------------------------- assembling it


def test_a_node_with_nothing_behind_it_is_refused() -> None:
    """It would compile, return an empty update, and the run would pass straight
    through the phase without ever gaining what that phase produces."""
    incomplete = wiring()
    object.__setattr__(incomplete, "optimize", None)
    with pytest.raises(MissingStepError, match="optimize"):
        build(incomplete, gated=False)


def test_a_graph_that_parks_without_a_checkpointer_is_refused() -> None:
    """`interrupt_before` parks the run in the checkpoint and waits. With nowhere
    to park, the approval a person gives on Thursday has nothing to return to."""
    with pytest.raises(NowhereToParkError, match=r"nothing .*to return to"):
        build(wiring(), gated=True)


def test_a_graph_that_parks_nowhere_needs_no_checkpointer() -> None:
    """So the shape can be tested without a database."""
    assert build(wiring(), gated=False) is not None


def test_ship_always_parks_and_optimize_parks_only_when_asked() -> None:
    """Nothing reaches a repository unseen. Spending is a separate decision."""
    assert interrupts(gated=True, early_review=False) == ("ship",)
    assert interrupts(gated=True, early_review=True) == ("optimize", "ship")
    assert interrupts(gated=False, early_review=True) == ()
    assert {item.value for item in GATEABLE} == {"optimize", "ship"}


# ------------------------------------------------------------- running it


def test_a_refused_run_ends_without_touching_anything_else(tmp_path: Path) -> None:
    graph = build(wiring(refuse="refused"), gated=False)
    final = graph.invoke(PipelineState(route="refused"))
    assert final["route"] == "refused"


def test_a_run_that_finds_nothing_ends_at_scan() -> None:
    """A null result ships. It is not an error and it is not an empty success."""
    graph = build(wiring(scan="nothing_found"), gated=False)
    final = graph.invoke(PipelineState())
    assert final["route"] == "nothing_found"


def test_a_run_reaches_ship_through_every_node(tmp_path: Path) -> None:
    with for_development(tmp_path / "checkpoints.sqlite") as checkpointer:
        graph = build(
            wiring(
                scan="findings",
                audit_finding="sound",
                optimize="candidate",
                audit_patch="clean",
                ship="done",
            ),
            checkpointer=checkpointer,
            gated=False,
        )
        final = graph.invoke(PipelineState(), thread("run-1"))
        assert final["route"] == "done"


def test_the_run_parks_before_ship_and_waits(tmp_path: Path) -> None:
    """The human gate. It stops *before* the node that would write to a
    repository, not after."""
    with for_development(tmp_path / "checkpoints.sqlite") as checkpointer:
        graph = build(
            wiring(
                scan="findings",
                audit_finding="sound",
                optimize="candidate",
                audit_patch="clean",
            ),
            checkpointer=checkpointer,
            gated=True,
        )
        config = thread("run-2")
        graph.invoke(PipelineState(), config)
        parked = graph.get_state(config)
        assert parked.next == ("ship",), "the run should be waiting to enter ship"


def test_a_parked_run_continues_when_a_person_lets_it(tmp_path: Path) -> None:
    with for_development(tmp_path / "checkpoints.sqlite") as checkpointer:
        graph = build(
            wiring(
                scan="findings",
                audit_finding="sound",
                optimize="candidate",
                audit_patch="clean",
            ),
            checkpointer=checkpointer,
            gated=True,
        )
        config = thread("run-3")
        graph.invoke(PipelineState(), config)
        resumed = graph.invoke(None, config)
        assert resumed["route"] == "done"


def test_a_checkpoint_is_written_after_every_node(tmp_path: Path) -> None:
    """Not at the end. A crash between two nodes has to leave the run resumable
    from the last one that finished."""
    with for_development(tmp_path / "checkpoints.sqlite") as checkpointer:
        graph = build(
            wiring(
                scan="findings",
                audit_finding="sound",
                optimize="candidate",
                audit_patch="clean",
            ),
            checkpointer=checkpointer,
            gated=False,
        )
        config = thread("run-4")
        graph.invoke(PipelineState(), config)
        history = list(graph.get_state_history(config))
        assert len(history) > len(Node), "one checkpoint per node at least"


def test_no_node_in_this_module_calls_a_model() -> None:
    """Orchestration is free, and it stays free by containing no prompt. The
    whole cost of a run sits inside the three nodes that call a model, and this
    module knows nothing about them."""
    source = (Path("src/coldfix/pipeline/graph.py")).read_text(encoding="utf-8")
    for forbidden in ("anthropic", "ModelClient", "system_prompt", "messages="):
        assert forbidden not in source, f"{forbidden} has no business in the graph"

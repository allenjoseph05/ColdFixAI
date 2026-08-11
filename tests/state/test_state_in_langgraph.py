"""AC 2, proved against the runtime that will actually apply node returns.

The story's note says the missing-`Annotated` bug is *the most common bug when
building these systems*, and it is a bug **in the framework's merge**, not in
our code. A test against a reducer we call ourselves would pass whether or not
the schema is annotated, which is the one thing worth knowing. So these run a
compiled `StateGraph`.

`langgraph` is a **dev dependency** for now. Nothing under `src/` imports it —
`Annotated[list[X], AppendOnly(...)]` is `typing` and a callable — so the schema
stays framework-independent while being read correctly by the framework. It
becomes a project dependency at S-12.1, where the graph is assembled.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from coldfix.state.checkpoint import (
    CheckpointedState,
    HistoryRewrittenError,
    StateError,
    UnknownChannelError,
    node,
)

# `StateGraph[StateT, ContextT, InputT, OutputT]`. The helpers below build graphs
# over three different schemas — ours, and two deliberately-wrong ones kept as
# controls — so the state parameter is genuinely open there.
AnyGraph = StateGraph[Any, Any, Any, Any]

# Where a node is a named function annotated against our own state, the graph is
# parameterized too. `add_node` also takes `input_schema`, which is what binds
# its `NodeInputT`: left off, that variable is solved as `Never` and every
# correctly-annotated node fails to type-check while an unannotated lambda
# passes. S-12.1 should register nodes the same way.
OurGraph = StateGraph[CheckpointedState, Any, Any, Any]


def run(graph: AnyGraph, start: object) -> dict[str, object]:
    compiled = graph.compile()
    return dict(compiled.invoke(start))


def one_node(returns: object, schema: type[BaseModel] = CheckpointedState) -> AnyGraph:
    graph: AnyGraph = StateGraph(schema)
    graph.add_node("probe", lambda state: returns)
    graph.add_edge(START, "probe")
    graph.add_edge("probe", END)
    return graph


# ============================================ AC 2: a single experiment appends


def test_a_node_returning_one_experiment_appends_rather_than_replaces() -> None:
    """AC 2, verbatim, against a real graph.

    This is the acceptance criterion the whole story is built around: without
    the reducer the second experiment would be the only one, and an agent that
    lost its history would re-test hypotheses it had already rejected while
    every counter still said it was making progress.
    """
    started = CheckpointedState(experiments=[{"index": 1, "primitive": "scaling"}])

    result = run(one_node({"experiments": [{"index": 2, "primitive": "ablation"}]}), started)

    assert result["experiments"] == [
        {"index": 1, "primitive": "scaling"},
        {"index": 2, "primitive": "ablation"},
    ]


def test_the_same_holds_for_attempts_and_flags() -> None:
    """All three annotated channels, since one working proves only that one."""
    started = CheckpointedState(attempts=[{"n": 1}], flags=["awaiting a human"])

    result = run(one_node({"attempts": [{"n": 2}], "flags": ["and another"]}), started)

    assert result["attempts"] == [{"n": 1}, {"n": 2}]
    assert result["flags"] == ["awaiting a human", "and another"]


def test_an_unannotated_channel_replaces_in_the_same_graph() -> None:
    """The control, and the reason the test above means anything.

    Without this, `test_a_node_returning_one_experiment_appends` would pass for
    a LangGraph that appended every list field regardless of annotation — and
    the annotation the story calls load-bearing would be carrying nothing.
    `screening` carries no reducer and is overwritten wholesale — the previous
    workload's entry is gone rather than merged.
    """
    started = CheckpointedState(screening={"list_books": {"growth": "quadratic"}})

    result = run(one_node({"screening": {"list_authors": {"growth": "linear"}}}), started)

    assert result["screening"] == {"list_authors": {"growth": "linear"}}


def test_dropping_the_annotation_loses_the_history() -> None:
    """The bug itself, reproduced, so the guard above is known to detect it.

    A schema identical to ours but for the missing `Annotated` — the single
    edit the story's note warns about — and the first experiment is gone.
    """

    class Unannotated(BaseModel):
        experiments: list[dict[str, int]] = Field(default_factory=list)

    result = run(
        one_node({"experiments": [{"index": 2}]}, schema=Unannotated),
        Unannotated(experiments=[{"index": 1}]),
    )

    assert result["experiments"] == [{"index": 2}]


def test_experiments_accumulate_across_many_nodes() -> None:
    """One append is a reducer working; forty is the log S-5.4 caps and S-5.8
    prunes, and the indices have to stay meaningful the whole way."""
    graph: AnyGraph = StateGraph(CheckpointedState)
    for index in range(1, 6):
        graph.add_node(f"probe_{index}", lambda state, n=index: {"experiments": [{"index": n}]})
    graph.add_edge(START, "probe_1")
    for index in range(1, 5):
        graph.add_edge(f"probe_{index}", f"probe_{index + 1}")
    graph.add_edge("probe_5", END)

    result = run(graph, CheckpointedState())

    assert result["experiments"] == [{"index": n} for n in range(1, 6)]


# ================================ the reducer's refusals reach the real runtime


def test_a_whole_channel_returned_by_a_node_is_refused_by_the_graph() -> None:
    """`operator.add` would double the history here without a word."""
    started = CheckpointedState(experiments=[{"index": 1}])

    with pytest.raises(HistoryRewrittenError):
        run(one_node({"experiments": [{"index": 1}, {"index": 2}]}), started)


def test_operator_add_accepts_exactly_what_our_reducer_refuses() -> None:
    """Why the reducer is checked rather than `add`, shown rather than argued."""

    class WithPlainAdd(BaseModel):
        experiments: Annotated[list[dict[str, int]], add] = Field(default_factory=list)

    result = run(
        one_node({"experiments": [{"index": 1}, {"index": 2}]}, schema=WithPlainAdd),
        WithPlainAdd(experiments=[{"index": 1}]),
    )

    assert result["experiments"] == [{"index": 1}, {"index": 1}, {"index": 2}]


def test_a_bare_entry_is_refused_by_the_graph() -> None:
    with pytest.raises(StateError, match="must be a sequence of entries"):
        run(one_node({"experiments": {"index": 2}}), CheckpointedState())


# ==================== AC 3: the hole in the framework this module has to cover


def test_langgraph_drops_an_unknown_key_without_saying_so() -> None:
    """Measured, and the reason `node` exists.

    This is the behaviour AC 3 is written against. The node writes nothing, the
    graph reports success, and the state comes back exactly as it went in.
    """
    started = CheckpointedState(experiments=[{"index": 1}])

    result = run(one_node({"experiements": [{"index": 2}]}), started)

    assert result["experiments"] == [{"index": 1}]


def test_a_wrapped_node_refuses_the_key_the_graph_would_have_dropped() -> None:
    """The same graph, the same typo, with the node registered through `node`."""
    graph: OurGraph = StateGraph(CheckpointedState)

    @node
    def probe(state: CheckpointedState) -> dict[str, object]:
        return {"experiements": [{"index": 2}]}

    graph.add_node("probe", probe, input_schema=CheckpointedState)
    graph.add_edge(START, "probe")
    graph.add_edge("probe", END)

    with pytest.raises(UnknownChannelError, match="experiements"):
        run(graph, CheckpointedState(experiments=[{"index": 1}]))


def test_a_wrapped_node_still_appends_normally() -> None:
    """The wrapper must not cost the property the rest of the story is about."""
    graph: OurGraph = StateGraph(CheckpointedState)

    @node
    def probe(state: CheckpointedState) -> dict[str, object]:
        return {"experiments": [{"index": 2}]}

    graph.add_node("probe", probe, input_schema=CheckpointedState)
    graph.add_edge(START, "probe")
    graph.add_edge("probe", END)

    result = run(graph, CheckpointedState(experiments=[{"index": 1}]))

    assert result["experiments"] == [{"index": 1}, {"index": 2}]


def test_a_node_sees_the_state_as_the_model() -> None:
    """Nodes read typed fields rather than dictionary keys, which is most of why
    the schema is a model and not §1.1's `TypedDict`."""
    seen: list[object] = []

    graph: OurGraph = StateGraph(CheckpointedState)

    @node
    def probe(state: CheckpointedState) -> dict[str, object]:
        seen.append(state)
        return {"verdict": "confirmed" if not state.experiments else "unconfirmed"}

    graph.add_node("probe", probe, input_schema=CheckpointedState)
    graph.add_edge(START, "probe")
    graph.add_edge("probe", END)

    result = run(graph, CheckpointedState())

    assert isinstance(seen[0], CheckpointedState)
    assert result["verdict"] == "confirmed"

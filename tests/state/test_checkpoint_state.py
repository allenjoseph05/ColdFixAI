"""S-6.1 — the checkpointed state, its channels, and what a node may return.

The append semantics are proved against a real graph in
`test_state_in_langgraph.py`; this file is the schema and the validation, which
are ours rather than the framework's.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coldfix.state.checkpoint import (
    AppendOnly,
    CheckpointedState,
    HistoryRewrittenError,
    StateError,
    UnknownChannelError,
    check_update,
    node,
)

# ------------------------------------------------- AC 1: the channels that append


def test_the_three_append_only_channels_are_the_ones_the_spec_names() -> None:
    """`03-agents.md` §1.1 annotates exactly `experiments`, `attempts`, `flags`."""
    assert CheckpointedState.append_only_channels() == ("experiments", "attempts", "flags")


def test_every_other_channel_replaces() -> None:
    """Stated rather than inferred: which channels accumulate is the story."""
    channels = CheckpointedState.channels()

    assert channels["project"] is False
    assert channels["screening"] is False
    assert channels["target"] is False
    assert channels["chain"] is False
    assert channels["budget"] is False


def test_the_trust_ledger_is_not_in_the_checkpointed_state() -> None:
    """`08-audit.md` F5 supersedes `03-agents.md` §1.1, which lists `ledger` here.

    This is the defect F5 is about, not an omission. A rewind restores the state
    that preceded the failure that caused the rewind — so a ledger kept here
    would have its trust level restored too, and the agent would re-earn the
    lesson it rewound in order to keep. It belongs to S-6.2's persistent store.
    """
    assert "ledger" not in CheckpointedState.model_fields
    assert "failure_memory" not in CheckpointedState.model_fields
    assert "playbooks" not in CheckpointedState.model_fields


def test_a_state_refuses_a_field_it_does_not_have() -> None:
    with pytest.raises(ValidationError):
        CheckpointedState(ledger={"select_related": 3})  # type: ignore[call-arg]


def test_a_fresh_state_starts_empty_rather_than_absent() -> None:
    """Every channel has a default, so a graph can start without constructing one."""
    state = CheckpointedState()

    assert state.experiments == []
    assert state.attempts == []
    assert state.flags == []
    assert state.target is None
    assert state.verdict is None


# --------------------------------------------------------- the checked reducer


def test_the_reducer_appends() -> None:
    assert AppendOnly("experiments")(["e1"], ["e2"]) == ["e1", "e2"]


def test_the_reducer_appends_onto_nothing() -> None:
    assert AppendOnly("experiments")([], ["e1"]) == ["e1"]


def test_an_empty_write_changes_nothing() -> None:
    """A node that ran and added nothing is not an error."""
    assert AppendOnly("experiments")(["e1"], []) == ["e1"]


def test_the_reducer_refuses_a_single_entry_returned_bare() -> None:
    """`operator.add` rejects this too, with a TypeError naming neither the
    channel nor what the caller should have done instead."""
    with pytest.raises(StateError, match="must be a sequence of entries"):
        AppendOnly("experiments")(["e1"], {"primitive": "ablation"})  # type: ignore[arg-type]


def test_the_reducer_refuses_a_string_that_looks_like_a_sequence() -> None:
    """A string is a `Sequence`, so the obvious check admits it and appends its
    characters one at a time — a channel of letters, and no error anywhere."""
    with pytest.raises(StateError, match="must be a sequence of entries"):
        AppendOnly("experiments")(["e1"], "e2")


def test_the_reducer_refuses_a_write_that_repeats_the_channel() -> None:
    """The mirror of the bug `Annotated[list, add]` prevents.

    A node that returns the whole channel instead of its delta does not lose
    history, it doubles it — and `operator.add` does that silently. Nothing
    downstream survives it: S-5.7's cached prefix requires the log's earlier
    bytes never move, and S-5.8's `read_experiment(7)` has to mean the seventh
    experiment.
    """
    with pytest.raises(HistoryRewrittenError, match="already holds"):
        AppendOnly("experiments")(["e1", "e2"], ["e1", "e2", "e3"])


def test_a_write_that_merely_starts_similarly_is_not_refused() -> None:
    """The control. Without it this guard would pass for one that refuses any
    write to a non-empty channel, which would make the channel write-once."""
    assert AppendOnly("experiments")(["e1"], ["e2", "e3"]) == ["e1", "e2", "e3"]


def test_a_reducer_names_its_own_channel() -> None:
    """Three channels share one implementation, so the message has to say which."""
    with pytest.raises(StateError, match="'attempts'"):
        AppendOnly("attempts")(["a1"], "a2")


# ---------------------------------------- AC 3: validated on every transition


def test_an_unknown_key_is_refused_by_name() -> None:
    """LangGraph drops it silently — measured, not assumed.

    A node returning a typo'd key writes nothing, reports nothing, and leaves an
    investigation that ran to its cap having recorded no experiments. That is
    the story's *loops while appearing to work*, reached through a typo.
    """
    with pytest.raises(UnknownChannelError, match="experiements"):
        check_update({"experiements": ["e1"]})


def test_the_refusal_lists_the_channels_that_do_exist() -> None:
    with pytest.raises(UnknownChannelError, match="experiments"):
        check_update({"experiements": ["e1"]})


def test_a_value_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(StateError, match="does not fit the channel"):
        check_update({"verdict": ["not", "a", "string"]})


def test_a_value_that_cannot_be_checkpointed_is_refused() -> None:
    """ADR 003 stores checkpoints in SQLite or Postgres, so a value that cannot
    be represented as JSON is a run that cannot resume."""
    with pytest.raises(StateError, match="does not fit the channel"):
        check_update({"experiments": [object()]})


def test_an_append_only_write_is_checked_as_the_delta() -> None:
    """The value a node returns for an append-only channel is what it added, so
    it is checked against the channel's element type — not against the channel."""
    assert check_update({"experiments": [{"primitive": "ablation"}]})


def test_a_valid_update_passes_through_unchanged() -> None:
    update = {"verdict": "confirmed", "flags": ["needs a human"]}

    assert check_update(update) == update


def test_the_node_wrapper_validates_what_the_node_returned() -> None:
    @node
    def typo(state: CheckpointedState) -> dict[str, object]:
        return {"experiements": ["e1"]}

    with pytest.raises(UnknownChannelError):
        typo(CheckpointedState())


def test_the_node_wrapper_lets_a_correct_node_through() -> None:
    @node
    def probe(state: CheckpointedState) -> dict[str, object]:
        return {"experiments": [{"primitive": "scaling"}]}

    assert probe(CheckpointedState()) == {"experiments": [{"primitive": "scaling"}]}


def test_the_wrapper_keeps_the_nodes_name() -> None:
    """LangGraph and every trace read it; an investigation whose nodes are all
    called `validated` is one nobody can debug."""

    @node
    def ground(state: CheckpointedState) -> dict[str, object]:
        return {}

    assert ground.__name__ == "ground"

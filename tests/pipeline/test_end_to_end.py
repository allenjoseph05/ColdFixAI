"""S-28.5 — the seven nodes, composed, twice.

Every story in E28 was tested alone. This is the check that they join: one run
from a repository nobody has seen to a patch at the ship gate, and one run over a
subject with nothing wrong that says so and spends nothing on repair.

**The client answers by who is asking, never by call order.** A first attempt
scripted positionally ended at `escalate`, and the nodes were right: `search`
keeps asking the Optimizer for rounds until the archive fills or a round offers
nothing new, so replies meant for the Adversary were eaten by the Optimizer,
parsed as unreadable, and an attack that never ran was correctly judged
`unattacked`. Keying on the system prompt is both robust and readable -- each
agent's answers sit together.

**The failing test is written for real.** `falsify` runs through the model and
through `must_fail`, because a composition check that faked the one gate between
a finding and a patch would be checking the wrong thing. What stays faked is the
half that needs a worktree and a container: applying a candidate, and comparing
two revisions.

The assembly itself lives in `pipeline/real_nodes.py`, because S-29.2 drives the
same seven nodes from a subprocess that crashes inside one of them. Two copies of
it would drift until the crash tests proved something about a pipeline this file
never runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.pipeline.graph import build
from coldfix.pipeline.nodes import bind
from coldfix.pipeline.state import PipelineState
from pipeline.real_nodes import APPROACH, ByCaller, assembled


def final_state(tmp_path: Path, client: ByCaller, **extra: Any) -> dict[str, Any]:
    resources = assembled(tmp_path, client, **extra)
    return dict(build(bind(resources), gated=False).invoke(PipelineState()))


# --------------------------------------------- a planted defect, all the way through


def test_a_proven_finding_reaches_a_patch_at_the_ship_gate(tmp_path: Path) -> None:
    """The epic's sentence: repository in, patch out, every step measured.

    `done` is only reachable through `ship`, which is only reachable through an
    `audit_patch` that found nothing wrong -- so the route is itself the assertion
    that the attack ran and held.
    """
    client = ByCaller()
    final = final_state(tmp_path, client)

    assert final["route"] == "done"
    assert final["resolved"]["f-1"]["verdict"] == "shipped"
    assert final["resolved"]["f-1"]["approach"] == APPROACH
    assert final["resolved"]["f-1"]["share_removed"] > 0


def test_every_agent_was_asked_and_none_was_asked_out_of_turn(tmp_path: Path) -> None:
    """Four agents and two phases of one of them, in the order the graph sets.

    A composition check earns its keep here: each of these was tested alone, and
    what this says is that the handover between them exists.
    """
    client = ByCaller()
    final_state(tmp_path, client)

    assert client.log[:4] == ["scan", "scan", "auditor", "falsify"]
    assert set(client.log) == {"scan", "auditor", "falsify", "optimizer", "adversary"}
    assert client.log.index("falsify") < client.log.index("optimizer"), "the test comes first"


def test_the_same_approach_offered_every_round_is_measured_once(tmp_path: Path) -> None:
    """F12: the label is the one part a model can change while changing nothing.
    Here it does not even change the label, and the archive refuses the repeat."""
    client = ByCaller()
    final = final_state(tmp_path, client)

    assert client.rounds > 1, "the search did ask again"
    assert [entry["candidate"]["approach"] for entry in final["candidates"]] == [APPROACH]


def test_the_finding_is_attested_against_a_measurement_the_ledger_holds(
    tmp_path: Path,
) -> None:
    """No finding without a measurement, carried across four nodes and a
    checkpoint's worth of serialization."""
    client = ByCaller()
    final = final_state(tmp_path, client)

    (recorded,) = final["measurements"]
    assert final["findings"]["f-1"]["attested_against"] == [recorded["measurement_id"]]
    assert recorded["fields"]["wall.median"] > 0


# ------------------------------------------------------- the subject with nothing wrong


def test_a_clean_subject_says_so_and_spends_nothing_on_repair(tmp_path: Path) -> None:
    """The negative control, and the reason it exists: a pipeline that always
    finds something would pass every test above while being useless."""
    client = ByCaller(finds=False)
    final = final_state(tmp_path, client)

    assert final["route"] == "nothing_found"
    assert final["findings"] == {}
    assert final["resolved"] == {}
    assert client.log == ["scan", "scan"], "no auditor, no test, no candidates, no attack"


# ------------------------------------------------------------- the gate, and a rejection


def test_the_run_parks_before_ship_with_the_audit_still_readable(tmp_path: Path) -> None:
    """`ship` clears `repaired` and `audited`, so the patch audit cannot be read
    from a finished run. At the gate -- which is where a person reads it -- it is
    still there."""
    client = ByCaller()
    resources = assembled(tmp_path, client)
    with for_development(tmp_path / "checkpoints.sqlite") as checkpointer:
        graph = build(bind(resources), checkpointer=checkpointer, gated=True)
        config = thread("run-1")
        graph.invoke(PipelineState(), config)
        parked = graph.get_state(config)

    assert parked.next == ("ship",)
    assert parked.values["audited"]["verdict"] == "clean"
    assert parked.values["repaired"]["approach"] == APPROACH


def test_a_patch_the_adversary_breaks_goes_back_instead_of_shipping(tmp_path: Path) -> None:
    """The same run, with a patch that changes what the program outputs. It must
    not reach the gate, and the Optimizer is asked again."""
    client = ByCaller()
    final = final_state(tmp_path, client, breaks=True)

    assert final["route"] != "done"
    assert final["resolved"].get("f-1", {}).get("verdict") != "shipped"
    assert client.rounds > 1, "it was sent back for another round"


@pytest.mark.parametrize("finds", [True, False])
def test_no_run_ends_without_a_route_somebody_can_read(tmp_path: Path, finds: bool) -> None:
    """Every ending is an answer. A run that stopped with nothing written would
    look like a clean finish and be a silent failure."""
    final = final_state(tmp_path, ByCaller(finds=finds))
    assert final["route"] in {"done", "nothing_found", "another_round", "escalate"}

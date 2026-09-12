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

Helpers come from `pipeline.test_nodes` -- a cross-module import between test
files, which this project treats as a hazard. It is safe here for the reason
`test_time_travel.py` already relies on: `tests/pipeline` is a package, so the
module resolves under exactly one name.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from coldfix.agent.prompt import SYSTEM as SCAN_AGENT
from coldfix.agent.scan import Bounds, Phase
from coldfix.cost.accounting import TokenUsage
from coldfix.evidence.adversary import SYSTEM as ADVERSARY
from coldfix.evidence.auditor import SYSTEM as AUDITOR
from coldfix.evidence.falsify import SYSTEM as FALSIFY
from coldfix.evidence.falsify import falsify
from coldfix.evidence.ledger import Finding, Ledger
from coldfix.evidence.optimizer import SYSTEM as OPTIMIZER
from coldfix.evidence.repair import Candidate, Falsified, Scored
from coldfix.evidence.revisions import Comparison, PatchUnderAudit, Side
from coldfix.llm.client import ModelResponse
from coldfix.orchestrator.checkpointing import for_development, thread
from coldfix.pipeline.graph import build
from coldfix.pipeline.nodes import Repairs, Resources, bind
from coldfix.pipeline.state import PipelineState
from fixtures.metering import metered
from pipeline.test_nodes import DRIVER, SOURCE, FakeDocker, FakeTools, act, finding_payload

DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n"
    "-        return [book for author in authors for book in author.books]\n"
    "+        return Book.objects.filter(author__in=authors)\n"
)
APPROACH = "prefetch the authors"


@dataclass
class ByCaller:
    """Answers each call by which agent is asking, and records the order."""

    finds: bool = True
    log: list[str] = field(default_factory=list)
    turns: int = 0
    rounds: int = 0
    attacks: int = 0

    def complete(self, **kwargs: Any) -> ModelResponse:
        who, text = self._answer(str(kwargs["system"]))
        self.log.append(who)
        return ModelResponse(
            model=str(kwargs["model"]),
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    def _answer(self, system: str) -> tuple[str, str]:
        if system == SCAN_AGENT:
            self.turns += 1
            if self.turns == 1:
                return "scan", act("measure", command=DRIVER)
            return "scan", act("submit", findings=[finding_payload()] if self.finds else [])
        if system == AUDITOR:
            return "auditor", json.dumps({"verdict": "sound", "because": "the count is cited"})
        if system == FALSIFY:
            return "falsify", json.dumps(
                {"test": "def test_one_query(): assert queries() == 1", "counts": "SELECTs"}
            )
        if system == OPTIMIZER:
            self.rounds += 1
            # The same approach every round, as a tiring model would. The archive
            # is what refuses to measure it a second time.
            return "optimizer", json.dumps({"candidates": [{"approach": APPROACH, "diff": DIFF}]})
        if system == ADVERSARY:
            self.attacks += 1
            inputs = [["one"], ["one", "two"]] if self.attacks == 1 else []
            return "adversary", json.dumps({"inputs": inputs})
        message = f"nobody owns this prompt: {system[:60]!r}"
        raise AssertionError(message)


def seams(resources: Resources, *, breaks: bool = False) -> Repairs:
    """The half that needs a worktree and a container, faked; the gate, real."""

    def apply(_: Falsified, candidate: Candidate) -> Scored:
        return Scored(
            candidate=candidate,
            measurement_id=f"m-{candidate.identifier}",
            wall_s=0.5,
            peak_rss_bytes=81_234,
        )

    def under_audit(diff: str) -> PatchUnderAudit:
        return PatchUnderAudit(
            diff=diff,
            test="def test_one_query(): assert queries() == 1",
            baseline=Path("baseline"),
            patched=Path("patched"),
            command=("python", "app.py"),
        )

    def compare(_: PatchUnderAudit) -> Any:
        def run(given: Sequence[str]) -> Comparison:
            side = Side(
                ran=True,
                output_digest="d",
                output_bytes=1,
                wall_s=1.0,
                peak_rss_bytes=1,
                stable=True,
            )
            patched = side.model_copy(update={"output_digest": "other"}) if breaks else side
            return Comparison(given=tuple(given), baseline=side, patched=patched)

        return run

    def write_and_run(finding: Finding, source: str) -> Falsified:
        return falsify(
            resources.meter,
            finding=finding,
            source=source,
            run=lambda _: (1, "AssertionError: expected 1 query, got 161"),
        )

    return Repairs(falsify=write_and_run, apply=apply, under_audit=under_audit, compare=compare)


def assembled(tmp_path: Path, client: ByCaller, *, breaks: bool = False) -> Resources:
    """One run's resources, with the repair seams bound to the same meter."""
    root = tmp_path / "subject"
    root.mkdir(exist_ok=True)
    (root / "app.py").write_text(SOURCE, encoding="utf-8")
    ledger = Ledger()
    bare = Resources(
        meter=metered(client),
        ledger=ledger,
        toolbox=FakeTools(ledger),
        repository=root,
        image="subject:latest",
        docker=FakeDocker(),
        read_source=lambda _: SOURCE,
        ground_bounds=Bounds(turns=4, until_phase=Phase.MEASURING),
        scan_bounds=Bounds(turns=4),
    )
    return Resources(**{**bare.__dict__, "repairs": seams(bare, breaks=breaks)})


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

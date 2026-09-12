"""S-28.3 — what each of the seven nodes does. ADR 183.

Every seam is injected, so nothing here needs Docker, a database or a model: a
scripted client answers the agent, a fake toolbox answers its tools, and the
repair half is supplied as callables. What is under test is the translation --
what each node reads out of the state, what it writes back, and which route it
writes -- because that is where a run loses things.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from coldfix.agent.scan import Bounds, Phase, ToolResult
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.collect.tiers import BuildResult, Tier
from coldfix.cost.accounting import TokenUsage
from coldfix.evidence.ledger import Finding, Ledger
from coldfix.evidence.repair import Candidate, Falsified, Scored, must_fail
from coldfix.evidence.revisions import Comparison, PatchUnderAudit, Side
from coldfix.llm.client import ModelResponse
from coldfix.pipeline.graph import Node, build
from coldfix.pipeline.nodes import (
    NodeError,
    Repairs,
    Resources,
    audit_finding,
    audit_patch,
    bind,
    ground,
    optimize,
    refuse,
    scan_for_waste,
    ship,
)
from coldfix.pipeline.state import PipelineState
from fixtures.metering import metered

MEDIAN = 2.41034567891
DRIVER: list[JsonValue] = ["python", "coldfix/drive.py"]
SOURCE = "class Author:\n    def books(self):\n        return Book.objects.all()\n"
DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -3,1 +3,1 @@\n"
    "-        return Book.objects.all()\n+        return self._books\n"
)

Update = Mapping[str, object]


# ------------------------------------------------------------------- doubles


@dataclass
class Scripted:
    """Answers each model call in order."""

    replies: list[str]
    asked: list[str] = field(default_factory=list)

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.asked.append(str(kwargs["messages"]))
        return ModelResponse(
            model="claude-opus-5",
            text=self.replies.pop(0) if self.replies else json.dumps({"tool": "submit"}),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )


@dataclass
class FakeTools:
    """Answers the agent's tools, and mints a measurement when asked to measure."""

    ledger: Ledger
    measured: list[str] = field(default_factory=list)
    called: list[str] = field(default_factory=list)

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult:
        del arguments
        self.called.append(tool)
        if tool != "measure":
            return ToolResult(content=f"{tool} ran")
        identifier = f"m-{len(self.measured) + 1}"
        self.measured.append(identifier)
        self.ledger.record(bare(identifier))
        return ToolResult(
            content=f"measured\n  wall.median = {MEDIAN}",
            measurement_id=identifier,
            verified=True,
        )


class FakeDocker:
    """A `Docker` that reaches whichever tier the test asks for."""

    def __init__(self, *, runs: bool = True, builds: bool = True) -> None:
        self.runs, self.builds = runs, builds

    def can_run(self, image: str) -> BuildResult:
        del image
        return BuildResult(self.runs, "a process started" if self.runs else "nothing ran")

    def build(self, dockerfile: str, tag: str) -> BuildResult:
        del dockerfile, tag
        return BuildResult(self.builds, "instrumented" if self.builds else "no package manager")

    def reads_compose(self, root: Path) -> BuildResult:
        del root
        return BuildResult(False, "no composed environment")


def bare(identifier: str, median: float = MEDIAN) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "coldfix/drive.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=12,
        wall=Spread(median=median, low=median, high=median),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=81_234,
        read_bytes=0,
        write_bytes=0,
    )


def act(tool: str, **arguments: Any) -> str:
    return json.dumps({"tool": tool, "arguments": arguments})


def finding_payload(identifier: str = "m-1", value: float = MEDIAN) -> dict[str, JsonValue]:
    return {
        "kind": "slow_path",
        "summary": "the serializer reads .books inside the loop",
        "location": {"file": "app.py", "line": 3, "symbol": "Author.books"},
        "evidence": [{"measurement_id": identifier, "field": "wall.median", "value": value}],
    }


def stored(identifier: str = "m-1") -> dict[str, JsonValue]:
    """One measurement in the shape a checkpoint carries it."""
    ledger = Ledger()
    ledger.record(bare(identifier))
    (entry,) = ledger.entries()
    return dict(entry)


def repairs(*, wins: bool = True, broke: bool = False) -> Repairs:
    def apply(falsified: Falsified, candidate: Candidate) -> Scored:
        del falsified
        return Scored(
            candidate=candidate,
            measurement_id=f"m-{candidate.identifier}",
            wall_s=0.5 if wins else 9.0,
            peak_rss_bytes=81_234,
        )

    def under_audit(diff: str) -> PatchUnderAudit:
        return PatchUnderAudit(
            diff=diff,
            test="def test_it(): ...",
            baseline=Path("baseline"),
            patched=Path("patched"),
            command=("python", "app.py"),
        )

    def compare(under: PatchUnderAudit) -> Any:
        del under

        def run(given: Sequence[str]) -> Comparison:
            side = Side(
                ran=True,
                output_digest="d",
                output_bytes=1,
                wall_s=1.0,
                peak_rss_bytes=1,
                stable=True,
            )
            changed = side.model_copy(update={"output_digest": "other"})
            return Comparison(given=tuple(given), baseline=side, patched=changed if broke else side)

        return run

    def falsify(finding: Finding, source: str) -> Falsified:
        return must_fail(
            f"def test_{finding.claim.kind}(): assert {len(source)}",
            lambda _: (1, "AssertionError: expected 1 query, got 161"),
        )

    return Repairs(
        falsify=falsify,
        apply=apply,
        under_audit=under_audit,
        compare=compare,
    )


def resources(
    client: Scripted, tmp_path: Path, *, tools: FakeTools | None = None, **extra: Any
) -> Resources:
    ledger = extra.pop("ledger", None) or Ledger()
    return Resources(
        meter=metered(client),
        ledger=ledger,
        toolbox=tools or FakeTools(ledger),
        repository=tmp_path,
        image="subject:latest",
        docker=FakeDocker(),
        read_source=lambda _: SOURCE,
        ground_bounds=Bounds(turns=4, until_phase=Phase.MEASURING),
        scan_bounds=Bounds(turns=4),
        **extra,
    )


def mapping_at(update: Update, key: str) -> Mapping[str, Any]:
    value = update[key]
    assert isinstance(value, Mapping), f"{key} is {type(value).__name__}"
    return value


def list_at(update: Update, key: str) -> list[Any]:
    value = update[key]
    assert isinstance(value, list), f"{key} is {type(value).__name__}"
    return value


def resolution(update: Update, identifier: str) -> Mapping[str, Any]:
    entry = mapping_at(update, "resolved")[identifier]
    assert isinstance(entry, Mapping)
    return entry


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------- refuse


def test_a_repository_that_is_nothing_special_proceeds(repository: Path) -> None:
    update = refuse(resources(Scripted([]), repository), PipelineState())
    assert update["route"] == "proceed"
    project = mapping_at(update, "project")
    assert project["tier"] == int(Tier.INSTRUMENTED)
    assert "stacks_with_line_numbers" in project["available"]


def test_a_real_time_system_is_declined_before_anything_is_built(repository: Path) -> None:
    """The one category where running this system could make things worse while
    reporting success."""
    (repository / "sched.c").write_text(
        "sched_setattr(pid, SCHED_DEADLINE, 0);\n", encoding="utf-8"
    )
    update = refuse(resources(Scripted([]), repository), PipelineState())
    assert update["route"] == "refused"
    assert "real-time" in str(update["project"])


def test_a_database_that_is_not_provably_a_test_database_is_declined(repository: Path) -> None:
    update = refuse(
        resources(Scripted([]), repository, database_url="postgresql://app@prod-db/customers"),
        PipelineState(),
    )
    assert update["route"] == "refused"
    assert "not permitted" in str(update["project"])


def test_an_image_nothing_runs_in_is_declined(repository: Path) -> None:
    at_hand = resources(Scripted([]), repository)
    refused = refuse(
        Resources(**{**at_hand.__dict__, "docker": FakeDocker(runs=False)}), PipelineState()
    )
    assert refused["route"] == "refused"
    assert "nothing was measured" in str(refused["project"])


# ------------------------------------------------------------------- ground


def test_grounding_stops_at_the_measurement_and_hands_over_how_to_drive(repository: Path) -> None:
    client = Scripted([act("bash", command="ls"), act("measure", command=DRIVER)])
    update = ground(resources(client, repository), PipelineState())

    assert update["route"] == "runnable"
    runnable = mapping_at(update, "runnable")
    assert runnable["command"] == DRIVER
    assert runnable["measurement_id"] == "m-1"
    assert len(client.asked) == 2, "it stopped the turn the workload measured"


def test_what_grounding_measured_reaches_the_state(repository: Path) -> None:
    client = Scripted([act("measure", command=DRIVER)])
    update = ground(resources(client, repository), PipelineState())
    (entry,) = list_at(update, "measurements")
    assert entry["measurement_id"] == "m-1"
    assert entry["fields"]["wall.median"] == MEDIAN


def test_a_measurement_written_to_the_state_is_one_a_checkpoint_can_hold(
    repository: Path,
) -> None:
    """A record dumped as Python holds tuples, and the channel refuses those -- so
    a ledger that could not be checkpointed would fail at the transition."""
    client = Scripted([act("measure", command=DRIVER)])
    update = ground(resources(client, repository), PipelineState())
    assert PipelineState(measurements=list_at(update, "measurements")).measurements


def test_a_program_that_never_measures_ends_the_run_there(repository: Path) -> None:
    client = Scripted([act("bash", command="ls")] * 8)
    update = ground(resources(client, repository), PipelineState())
    assert update["route"] == "unmeasurable"
    assert mapping_at(update, "coverage")["grounding"] == "turns"


# --------------------------------------------------------------------- scan


def test_the_scan_starts_from_what_grounding_proved(repository: Path) -> None:
    """A fresh conversation, told how to drive the subject -- and already past the
    phase gate, because a verified measurement is what opens it.

    The tool is the assertion: `profile` is offered only once something has
    measured, so a scan that began at `EXPLORING` would have this turn refused
    and the brief would still read exactly the same."""
    ledger = Ledger()
    tools = FakeTools(ledger)
    client = Scripted([act("profile", command=DRIVER), act("submit", findings=[])])
    state = PipelineState(runnable={"command": DRIVER, "measurement_id": "m-1"})
    scan_for_waste(resources(client, repository, tools=tools, ledger=ledger), state)
    assert "python coldfix/drive.py" in client.asked[0]
    assert "m-1" in client.asked[0]
    assert tools.called == ["profile"], "the instruments were available on turn one"


def test_a_node_writes_only_the_measurements_it_added(repository: Path) -> None:
    """`measurements` is append-only, so returning the whole ledger would record
    every earlier entry twice -- which the reducer refuses by name."""
    ledger = Ledger()
    ledger.record(bare("m-1"))
    client = Scripted([act("submit", findings=[])])
    state = PipelineState(
        runnable={"command": DRIVER, "measurement_id": "m-1"}, measurements=[stored()]
    )
    update = scan_for_waste(
        resources(client, repository, tools=FakeTools(ledger), ledger=ledger), state
    )
    assert list_at(update, "measurements") == []


def test_findings_are_keyed_by_the_harness_and_go_into_the_state(repository: Path) -> None:
    ledger = Ledger()
    ledger.record(bare("m-1"))
    client = Scripted([act("submit", findings=[finding_payload()])])
    update = scan_for_waste(
        resources(client, repository, tools=FakeTools(ledger), ledger=ledger),
        PipelineState(runnable={"command": DRIVER, "measurement_id": "m-1"}),
    )
    assert update["route"] == "findings"
    assert list(mapping_at(update, "findings")) == ["f-1"]


def test_a_second_pass_keeps_the_findings_the_first_one_made(repository: Path) -> None:
    ledger = Ledger()
    ledger.record(bare("m-1"))
    client = Scripted([act("submit", findings=[finding_payload()])])
    state = PipelineState(
        runnable={"command": DRIVER, "measurement_id": "m-1"},
        findings={"f-1": {"claim": finding_payload(), "attested_against": ["m-1"]}},
    )
    update = scan_for_waste(
        resources(client, repository, tools=FakeTools(ledger), ledger=ledger), state
    )
    assert list(mapping_at(update, "findings")) == ["f-1", "f-2"]


def test_finding_nothing_is_an_answer(repository: Path) -> None:
    client = Scripted([act("submit", findings=[])])
    update = scan_for_waste(
        resources(client, repository),
        PipelineState(runnable={"command": DRIVER, "measurement_id": "m-1"}),
    )
    assert update["route"] == "nothing_found"


def test_the_scan_node_needs_grounding_to_have_run(repository: Path) -> None:
    with pytest.raises(NodeError, match="grounding is what produces one"):
        scan_for_waste(resources(Scripted([]), repository), PipelineState())


# ------------------------------------------------------------- audit_finding


def audited_state() -> PipelineState:
    return PipelineState(
        runnable={"command": DRIVER, "measurement_id": "m-1"},
        measurements=[stored()],
        findings={"f-1": {"claim": finding_payload(), "attested_against": ["m-1"]}},
    )


def test_the_audit_rebuilds_the_ledger_from_the_checkpoint(repository: Path) -> None:
    """The resumed-run case: an empty ledger would fail the first attack fatally,
    and `unsound` is the verdict that does not send the run back."""
    client = Scripted([json.dumps({"verdict": "sound", "because": "the count is cited"})])
    update = audit_finding(resources(client, repository), audited_state())
    assert update["route"] == "sound"
    assert resolution(update, "f-1")["verdict"] == "sound"


def test_a_finding_the_code_attacks_reject_never_reaches_the_model(repository: Path) -> None:
    """Four free attacks first: a finding that fails one costs nothing to reject."""
    client = Scripted([])
    state = audited_state()
    state.findings["f-1"] = {"claim": finding_payload(value=9.99), "attested_against": ["m-1"]}
    update = audit_finding(resources(client, repository), state)
    assert update["route"] == "unsound"
    assert client.asked == [], "no model call was made"


# ----------------------------------------------------------------- optimize


def sound_state() -> PipelineState:
    state = audited_state()
    state.resolved = {"f-1": {"verdict": "sound", "why": "survived every attack"}}
    return state


def test_a_winning_candidate_becomes_the_patch_under_audit(repository: Path) -> None:
    client = Scripted([json.dumps({"candidates": [{"approach": "prefetch", "diff": DIFF}]})])
    update = optimize(resources(client, repository, repairs=repairs()), sound_state())

    assert update["route"] == "candidate"
    repaired = mapping_at(update, "repaired")
    assert repaired["finding"] == "f-1"
    assert repaired["approach"] == "prefetch"
    assert repaired["share_removed"] > 0
    assert len(list_at(update, "candidates")) == 1


def test_a_second_round_writes_only_what_it_added(repository: Path) -> None:
    """The defect the composition check found. A second `optimize` seeded from the
    state must not re-measure the candidate that lost, and must not write it into
    the append-only channel a second time -- which is refused, by name."""
    client = Scripted([json.dumps({"candidates": [{"approach": "prefetch", "diff": DIFF}]})])
    state = sound_state()
    first = optimize(resources(client, repository, repairs=repairs()), state)
    state.candidates = list(list_at(first, "candidates"))

    # Counted rather than inferred from what was written: measuring the repeat and
    # then slicing it off the update would leave the channel correct and the run
    # paying twice, which is half the defect and the half a write-only assertion
    # cannot see.
    applied: list[str] = []
    base = repairs()

    def watching(falsified: Falsified, candidate: Candidate) -> Scored:
        applied.append(candidate.approach)
        return base.apply(falsified, candidate)

    again = Scripted([json.dumps({"candidates": [{"approach": "prefetch", "diff": DIFF}]})])
    watched = Repairs(
        falsify=base.falsify,
        apply=watching,
        under_audit=base.under_audit,
        compare=base.compare,
    )
    second = optimize(resources(again, repository, repairs=watched), state)

    assert applied == [], "the candidate that already lost was not measured again"
    assert list_at(second, "candidates") == [], "and nothing new was written"
    assert PipelineState(candidates=state.candidates + list_at(second, "candidates"))


def test_nothing_beating_the_baseline_is_an_answer(repository: Path) -> None:
    client = Scripted([json.dumps({"candidates": [{"approach": "slower", "diff": DIFF}]})])
    update = optimize(resources(client, repository, repairs=repairs(wins=False)), sound_state())
    assert update["route"] == "nothing_beat_baseline"
    assert resolution(update, "f-1")["outcome"]


def test_a_pipeline_with_no_repair_seams_says_so_rather_than_reporting_a_search(
    repository: Path,
) -> None:
    """A search that never ran and a search that found nothing are different facts,
    and only one of them is an answer.

    The failing test has a writer now (S-28.6); what a pipeline can still be
    assembled without is the half that applies a candidate and measures it, which
    needs a worktree and a container."""
    with pytest.raises(NodeError, match="misassembled harness"):
        optimize(resources(Scripted([]), repository), sound_state())


def test_the_baseline_is_the_number_grounding_recorded(repository: Path) -> None:
    """Measuring again would spend the run's money to learn what it wrote down --
    and would get a different answer, which every later comparison would inherit."""
    client = Scripted([json.dumps({"candidates": [{"approach": "prefetch", "diff": DIFF}]})])
    update = optimize(resources(client, repository, repairs=repairs()), sound_state())
    (candidate,) = list_at(update, "candidates")
    assert candidate["wall_s"] == 0.5
    assert mapping_at(update, "repaired")["share_removed"] == pytest.approx(1 - 0.5 / MEDIAN)


# --------------------------------------------------------------- audit_patch


def repaired_state() -> PipelineState:
    state = sound_state()
    state.repaired = {
        "finding": "f-1",
        "approach": "prefetch",
        "diff": DIFF,
        "test": "def test_it(): ...",
        "measurement_id": "m-c1",
        "share_removed": 0.79,
        "trades": [],
    }
    return state


def test_a_patch_nothing_broke_goes_to_the_ship_gate(repository: Path) -> None:
    client = Scripted([json.dumps({"inputs": [["a"], ["a", "b"]]}), json.dumps({"inputs": []})])
    update = audit_patch(resources(client, repository, repairs=repairs()), repaired_state())
    assert update["route"] == "clean"
    assert mapping_at(update, "audited")["verdict"] == "clean"


def test_a_broken_patch_goes_back_for_another_round(repository: Path) -> None:
    client = Scripted([json.dumps({"inputs": [["a", "b"]]})])
    update = audit_patch(
        resources(client, repository, repairs=repairs(broke=True)), repaired_state()
    )
    assert update["route"] == "another_round"
    assert mapping_at(update, "audited")["verdict"] == "broken"


# --------------------------------------------------------------------- ship


def test_shipping_closes_the_finding_and_clears_the_handover(repository: Path) -> None:
    update = ship(resources(Scripted([]), repository), repaired_state())
    assert resolution(update, "f-1")["verdict"] == "shipped"
    assert update["repaired"] is None
    assert update["audited"] is None
    assert update["route"] == "done"


def test_a_finding_still_waiting_sends_the_run_back(repository: Path) -> None:
    state = repaired_state()
    state.findings["f-2"] = {"claim": finding_payload(), "attested_against": ["m-1"]}
    update = ship(resources(Scripted([]), repository), state)
    assert update["route"] == "more_findings"


# --------------------------------------------------------------- the wiring


def test_every_node_has_a_step_and_the_graph_compiles(repository: Path) -> None:
    wiring = bind(resources(Scripted([]), repository))
    assert set(wiring.steps()) == set(Node)
    assert build(wiring, gated=False) is not None


def test_each_step_is_the_adapter_of_that_name(repository: Path) -> None:
    """A wiring that bound the same adapter twice would compile and run the wrong
    work at a node, which nothing downstream could tell from the state."""
    wiring = bind(resources(Scripted([]), repository))
    named = [getattr(step, "__name__", "?") for step in wiring.steps().values()]
    assert named == [
        "refuse",
        "ground",
        "scan_for_waste",
        "audit_finding",
        "optimize",
        "audit_patch",
        "ship",
    ]

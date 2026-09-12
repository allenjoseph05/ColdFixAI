"""S-28.1 — the host half of the toolbox. ADR 182.

A fake sandbox stands in for the container and answers with envelopes, because
what is under test is the host's side of the line: what it sends, what it
refuses to believe, what reaches the ledger, and whether the numbers the agent is
shown are the numbers a claim can cite. The last test drives the real scan loop
through this toolbox, from a measurement to an attested finding.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from coldfix.agent.prompt import SYSTEM
from coldfix.agent.scan import Phase, scan
from coldfix.agent.toolbox import ENTRY, TOOL_TIMEOUT_S, SandboxedToolbox, ToolboxError
from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError
from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread, Unmeasured
from coldfix.collect.profiling import ProfileMeasurement, Site
from coldfix.cost.accounting import TokenUsage
from coldfix.evidence.ledger import Citation, Claim, Ledger, Location
from coldfix.llm.client import ModelResponse
from coldfix.sandbox.runner import MemoryLimitExceededError, Sandbox
from fixtures.metering import metered

UGLY = 2.41034567891
"""A median no summary could round without changing it."""


def bare(identifier: str = "m-bare", median: float = UGLY) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "coldfix/drive.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=12,
        wall=Spread(median=median, low=median - 0.0123, high=median + 0.0456),
        cpu_s=median - 0.00789,
        mode=Mode.COMPUTING,
        peak_rss_bytes=81_234_567,
        read_bytes=0,
        write_bytes=4096,
    )


def ablation() -> AblationMeasurement:
    return AblationMeasurement(
        measurement_id="m-abl",
        symbol="Author.books",
        file="app/models.py",
        line=112,
        before=bare("m-before"),
        after=bare("m-after", 0.52076),
        share_removed=0.78394,
        output_changed=True,
    )


def envelope(tool: str, payload: dict[str, Any], *, ok: bool = True) -> str:
    return json.dumps({"tool": tool, "ok": ok, "payload": payload})


def executed(stdout: str, *, exit_code: int = 0, stderr: str = "") -> ExecutionResult:
    return ExecutionResult(
        command=("docker", "run"),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=1.0,
    )


@dataclass
class FakeSandbox:
    """Answers each run in order with a result, or raises what it was given."""

    answers: list[ExecutionResult | Exception]
    commands: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def run(self, command: Any, *, timeout: float, env: Any = None) -> ExecutionResult:
        self.commands.append(list(command))
        self.timeouts.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def toolbox(*answers: ExecutionResult | Exception) -> tuple[SandboxedToolbox, FakeSandbox, Ledger]:
    sandbox = FakeSandbox(list(answers))
    ledger = Ledger()
    return SandboxedToolbox(sandbox=sandbox, ledger=ledger), sandbox, ledger


def measured(measurement: BareMeasurement | None = None) -> ExecutionResult:
    return executed(envelope("measure", (measurement or bare()).model_dump(mode="json")))


# ----------------------------------------------------------- what is sent


def test_a_call_runs_the_in_container_entry_point_with_its_arguments() -> None:
    tools, sandbox, _ = toolbox(
        executed(
            envelope(
                "bash",
                {"command": "ls", "returncode": 0, "stdout": "", "stderr": "", "truncated": False},
            )
        )
    )
    tools.call("bash", {"command": "ls"})
    assert sandbox.commands == [[*ENTRY, "bash", '{"command": "ls"}']]
    assert sandbox.timeouts == [TOOL_TIMEOUT_S] == [600.0]


def test_the_real_sandbox_is_a_thing_the_toolbox_can_run(tmp_path: Path) -> None:
    """Structural: `Sandbox` satisfies `Runs` as it stands, with its policy
    unchanged. Constructing one needs no daemon."""
    SandboxedToolbox(sandbox=Sandbox(image="subject:latest", workspace=tmp_path), ledger=Ledger())


# ------------------------------------------------------ what reaches the ledger


def test_a_measurement_is_recorded_and_its_id_handed_back() -> None:
    tools, _, ledger = toolbox(measured())
    result = tools.call("measure", {"command": ["python", "coldfix/drive.py"]})
    assert result.measurement_id == "m-bare"
    assert result.verified, "a measure that succeeded is what opens the second phase"
    assert "m-bare" in ledger.known


def test_every_number_shown_is_citable_exactly_as_shown() -> None:
    """The ledger checks citations for equality. Each `name = value` line the
    agent reads must attest when cited verbatim -- a rounded summary would make
    every honest citation a fabricated one."""
    tools, _, ledger = toolbox(
        measured(), executed(envelope("ablate", ablation().model_dump(mode="json")))
    )
    shown = [
        (result.measurement_id, line)
        for result in (
            tools.call("measure", {"command": ["python", "coldfix/drive.py"]}),
            tools.call(
                "ablate", {"command": ["python"], "path": "app/models.py", "symbol": "Author.books"}
            ),
        )
        for line in result.content.splitlines()
    ]
    cited = [
        (identifier, match.group(1), float(match.group(2)))
        for identifier, line in shown
        if (match := re.fullmatch(r"\s+([a-z_.]+) = ([-0-9.e]+)", line))
    ]
    assert len(cited) >= 12, "the summaries show the numbers"
    for identifier, name, value in cited:
        assert identifier is not None
        ledger.attest(
            Claim(
                kind="k",
                summary="s",
                location=Location(file="app/models.py", line=1),
                evidence=(Citation(measurement_id=identifier, field=name, value=value),),
            )
        )


def test_an_ablation_and_a_profile_are_recorded_too() -> None:
    profile = ProfileMeasurement(
        measurement_id="m-prof",
        command=("py-spy",),
        repeats=1,
        output_digest="0" * 64,
        output_bytes=0,
        instrument="py-spy",
        counts={"samples": 812},
        sites=(
            Site(
                file="app/models.py",
                line=112,
                symbol="books",
                self_samples=640,
                self_share=0.788,
                call_path=("main", "books"),
            ),
        ),
    )
    tools, _, ledger = toolbox(
        executed(envelope("ablate", ablation().model_dump(mode="json"))),
        executed(envelope("profile", profile.model_dump(mode="json"))),
    )
    ablated = tools.call("ablate", {"command": ["python"], "path": "p", "symbol": "s"})
    profiled = tools.call("profile", {"command": ["python"]})
    assert {ablated.measurement_id, profiled.measurement_id} <= set(ledger.known)
    assert "candidates" in profiled.content
    assert "app/models.py:112" in profiled.content
    assert not ablated.verified and not profiled.verified


def test_what_was_not_measured_is_said() -> None:
    gappy = bare().model_copy(
        update={"not_measured": (Unmeasured(what="output_digest", why="it varied"),)}
    )
    tools, _, _ = toolbox(measured(gappy))
    assert (
        "not measured: output_digest -- it varied"
        in tools.call("measure", {"command": ["x"]}).content
    )


@pytest.mark.parametrize(
    ("tool", "payload", "expected"),
    [
        (
            "bash",
            {"command": "ls", "returncode": 0, "stdout": "app\n", "stderr": "", "truncated": False},
            "exit 0",
        ),
        (
            "read_file",
            {"path": "app/models.py", "first_line": 3, "lines": ["a", "b"], "total_lines": 9},
            "     3  a",
        ),
        ("write_file", {"path": "coldfix/drive.py"}, "created coldfix/drive.py"),
    ],
)
def test_the_tools_that_measure_nothing_record_nothing(
    tool: str, payload: dict[str, Any], expected: str
) -> None:
    tools, _, ledger = toolbox(executed(envelope(tool, payload)))
    result = tools.call(tool, {})
    assert expected in result.content
    assert result.measurement_id is None
    assert not ledger.known


# ------------------------------------- the subject fails: the agent is told


def test_a_refusal_reaches_the_agent_with_its_reason() -> None:
    refusal = {"error": "NotRepeatableError", "message": "the runs varied by 40%"}
    tools, _, ledger = toolbox(executed(envelope("measure", refusal, ok=False)))
    result = tools.call("measure", {"command": ["x"]})
    assert "NotRepeatableError" in result.content
    assert "varied by 40%" in result.content
    assert result.measurement_id is None
    assert not result.verified
    assert not ledger.known


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        (MemoryLimitExceededError(["x"], 1, "", ""), "memory limit"),
        (ExecutionTimeoutError(["x"], 600.0, "", ""), "stopped after 600s"),
    ],
)
def test_the_memory_cap_and_the_clock_are_observations(raised: Exception, said: str) -> None:
    tools, _, _ = toolbox(raised)
    assert said in tools.call("measure", {"command": ["x"]}).content


# ------------------------------------- the harness fails: nothing is believed


@pytest.mark.parametrize(
    ("answer", "because"),
    [
        (executed("", exit_code=1, stderr="Traceback: boom"), "printed no envelope"),
        (executed("not json at all"), "not an envelope"),
        (executed(envelope("bash", {})), "answers for 'bash'"),
        (executed(envelope("measure", {"measurement_id": "m-half"})), "its own model rejects"),
    ],
)
def test_a_harness_fault_raises_and_records_nothing(answer: ExecutionResult, because: str) -> None:
    tools, _, ledger = toolbox(answer)
    with pytest.raises(ToolboxError, match=because):
        tools.call("measure", {"command": ["x"]})
    assert not ledger.known


def test_only_the_last_line_is_the_envelope() -> None:
    """A library may print a warning first. That is noise, not a fault."""
    noisy = executed(
        "DeprecationWarning: something\n" + envelope("measure", bare().model_dump(mode="json"))
    )
    tools, _, _ = toolbox(noisy)
    assert tools.call("measure", {"command": ["x"]}).measurement_id == "m-bare"


# ------------------------------------------------------- the whole scan loop


@dataclass
class Scripted:
    replies: list[str]

    def complete(self, **_: Any) -> ModelResponse:
        return ModelResponse(
            model="claude-opus-5",
            text=self.replies.pop(0),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )


def test_the_scan_loop_goes_from_a_measurement_to_an_attested_finding() -> None:
    """The composition: the agent measures through this toolbox, the measurement
    opens the second phase, and a claim citing a number it was shown attests
    against the same ledger."""
    tools, _, ledger = toolbox(measured())
    finding = {
        "kind": "slow_path",
        "summary": "the driver's one call is computing, not waiting",
        "location": {"file": "app/models.py", "line": 112, "symbol": "Author.books"},
        "evidence": [{"measurement_id": "m-bare", "field": "wall.median", "value": UGLY}],
    }
    client = Scripted(
        [
            json.dumps(
                {"tool": "measure", "arguments": {"command": ["python", "coldfix/drive.py"]}}
            ),
            json.dumps({"tool": "submit", "arguments": {"findings": [finding]}}),
        ]
    )

    outcome = scan(metered(client), toolbox=tools, ledger=ledger, system=SYSTEM)

    assert outcome.stopped_by == "submitted"
    assert outcome.transcript.phase is Phase.MEASURING
    (attested,) = outcome.findings
    assert attested.attested_against == ("m-bare",)

"""S-28.1 — one tool call inside the container, run here in-process. ADR 182.

`collect/run.py` is what the container runs. These tests call it directly with an
injected child runner and clock, so they exercise the dispatch, the argument
validation and the envelope on any machine, and every refusal the agent may see.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.collect import run as tool_run
from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement
from coldfix.collect.run import WORKSPACE, Envelope, Seams, main, run
from coldfix.collect.usage import ChildResult, Usage
from coldfix.collect.workspace import FileWindow, Workspace
from coldfix.sandbox.runner import WORKSPACE_MOUNTPOINT


class Steady:
    """Runs nothing; every child exits 0 with the same output and usage."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        self.commands.append(tuple(command))
        return ChildResult(
            returncode=self.returncode,
            stdout="ok\n",
            stderr="" if self.returncode == 0 else "Traceback: boom",
            usage=Usage(cpu_s=0.5, peak_rss_bytes=2048, read_blocks=0, write_blocks=0),
        )


class FixedClock:
    """Every run takes exactly one second, so repeatability never turns on the machine."""

    def __init__(self) -> None:
        self.now = 0.0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls % 2 == 0:
            self.now += 1.0
        return self.now


APP = "class Author:\n    def books(self):\n        return [1, 2, 3]\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    return Workspace(tmp_path)


def call(
    tool: str, arguments: object, workspace: Workspace, runner: Steady | None = None
) -> Envelope:
    return run(
        tool,
        arguments if isinstance(arguments, str) else json.dumps(arguments),
        workspace=workspace,
        seams=Seams(runner=runner or Steady(), clock=FixedClock()),
    )


def test_the_container_workspace_is_where_the_sandbox_mounts_it() -> None:
    assert str(WORKSPACE) == WORKSPACE_MOUNTPOINT


# ------------------------------------------------------------- measuring tools


def test_measure_returns_a_measurement_its_model_accepts(workspace: Workspace) -> None:
    runner = Steady()
    envelope = call("measure", {"command": ["python", "coldfix/drive.py"]}, workspace, runner)
    assert envelope.ok
    measurement = BareMeasurement.model_validate(envelope.payload)
    assert measurement.command == ("python", "coldfix/drive.py")
    assert set(runner.commands) == {("python", "coldfix/drive.py")}


def test_a_command_must_be_an_argument_list_not_a_shell_line(workspace: Workspace) -> None:
    """What is measured is exactly this process, not a shell that starts it."""
    envelope = call("measure", {"command": "python coldfix/drive.py"}, workspace)
    assert not envelope.ok
    assert envelope.payload["error"] == "ValidationError"


def test_a_workload_that_fails_is_a_refusal_the_agent_can_act_on(workspace: Workspace) -> None:
    envelope = call("measure", {"command": ["python", "x.py"]}, workspace, Steady(returncode=1))
    assert not envelope.ok
    assert envelope.payload["error"] == "WorkloadFailedError"


def test_ablate_measures_a_stubbed_copy(workspace: Workspace) -> None:
    envelope = call(
        "ablate",
        {"command": ["python", "app.py"], "path": "app.py", "symbol": "Author.books"},
        workspace,
    )
    assert envelope.ok
    measurement = AblationMeasurement.model_validate(envelope.payload)
    assert measurement.symbol == "Author.books"
    assert (workspace.root / "app.py").read_text(encoding="utf-8") == APP, (
        "the original is untouched"
    )


def test_ablating_a_file_that_is_not_python_is_refused(workspace: Workspace) -> None:
    (workspace.root / "notes.txt").write_text("def (\n", encoding="utf-8")
    envelope = call(
        "ablate", {"command": ["python", "app.py"], "path": "notes.txt", "symbol": "x"}, workspace
    )
    assert not envelope.ok
    assert envelope.payload["error"] == "SyntaxError"


# --------------------------------------------------------- the three other tools


def test_read_file_returns_a_window(workspace: Workspace) -> None:
    envelope = call("read_file", {"path": "app.py", "offset": 2}, workspace)
    assert envelope.ok
    window = FileWindow.model_validate(envelope.payload)
    assert window.first_line == 2
    assert window.lines[0] == "    def books(self):"


def test_a_path_out_of_the_workspace_is_refused(workspace: Workspace) -> None:
    envelope = call("read_file", {"path": "../outside.txt"}, workspace)
    assert not envelope.ok
    assert envelope.payload["error"] == "PathEscapesWorkspaceError"


def test_write_file_creates_under_scratch_and_nowhere_else(workspace: Workspace) -> None:
    created = call("write_file", {"path": "coldfix/drive.py", "content": "print(1)\n"}, workspace)
    assert created.ok
    assert created.payload == {"path": "coldfix/drive.py"}
    again = call("write_file", {"path": "coldfix/drive.py", "content": "x"}, workspace)
    assert again.payload["error"] == "FileExistsHereError"
    outside = call("write_file", {"path": "app2.py", "content": "x"}, workspace)
    assert outside.payload["error"] == "OutsideScratchError"


def test_an_argument_a_tool_does_not_take_is_refused_not_ignored(workspace: Workspace) -> None:
    """`bash` has no `cwd`. Asking for one is an error, so the accident of a
    command that quietly ran somewhere else cannot be expressed."""
    envelope = call("bash", {"command": "ls", "cwd": "/"}, workspace)
    assert not envelope.ok
    assert "cwd" in envelope.payload["message"]


# ------------------------------------------------------------ bad requests


@pytest.mark.parametrize(
    ("tool", "arguments", "error"),
    [
        ("teleport", "{}", "UnknownTool"),
        ("measure", "[1, 2]", "BadArguments"),
        ("measure", "{not json", "JSONDecodeError"),
    ],
)
def test_a_malformed_request_is_refused_with_its_reason(
    tool: str, arguments: str, error: str, workspace: Workspace
) -> None:
    envelope = call(tool, arguments, workspace)
    assert not envelope.ok
    assert envelope.payload["error"] == error


def test_a_fault_in_the_harness_is_not_turned_into_an_answer(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An envelope that papered over a bug would hand the agent the bug as a fact
    about the subject."""

    def broken(*_: object) -> None:
        message = "a bug in the harness"
        raise RuntimeError(message)

    monkeypatch.setitem(tool_run.TOOLS, "measure", broken)
    with pytest.raises(RuntimeError, match="a bug in the harness"):
        call("measure", {"command": ["x"]}, workspace)


# ----------------------------------------------------------------- the entry point


def test_the_entry_point_prints_exactly_one_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["teleport", "{}"]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert Envelope.model_validate_json(line).payload["error"] == "UnknownTool"


def test_the_entry_point_says_how_to_call_it(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["measure"]) == 2
    (line,) = capsys.readouterr().out.splitlines()
    assert "usage" in Envelope.model_validate_json(line).payload["message"]

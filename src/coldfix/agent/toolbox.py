"""The scan agent's six tools, each run in its own sandboxed container. **S-28.1.**

ADR 182. The host half. `collect/run.py` runs inside a fresh container per call
and prints one envelope; this sends the call, reads the envelope, validates it
with the collector's own model, records a measurement in the ledger, and hands the
agent a summary and the measurement's id. The subject and every measurement stay
in the box; the model calls, the budget and the ledger stay here.

**Every number the agent is shown is printed exactly as recorded, under the name a
claim cites it by.** The ledger checks a cited value for equality, so a summary
that rounded `2.4103` to `2.410` would turn every honest citation into a
fabricated one.

**A subject's failure is an observation; the harness's failure is an error.** A
refused tool, a run killed by the memory cap or the clock -- the agent is told,
because it can act on each. An envelope that is missing, malformed, answers a
different tool, or claims success with a payload its model rejects is a fault
here, and it raises rather than reaching the agent as a fact about the subject.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from coldfix.agent.scan import Bounds, ToolResult
from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError
from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Unmeasured
from coldfix.collect.profiling import ProfileMeasurement
from coldfix.collect.run import Envelope, Written
from coldfix.collect.workspace import CommandResult, FileWindow
from coldfix.evidence.ledger import Ledger
from coldfix.sandbox.runner import MemoryLimitExceededError

ENTRY = ("python", "-m", "coldfix.collect.run")
"""What every container is asked to run. The tool and its arguments follow it."""

TOOL_TIMEOUT_S = Bounds().wall_seconds / 2
"""Half the scan's wall-clock bound, so no single call can spend a whole scan."""

TOP_SITES = 10

BARE_FIELDS = (
    "repeats",
    "wall.median",
    "wall.low",
    "wall.high",
    "cpu_s",
    "peak_rss_bytes",
    "read_bytes",
    "write_bytes",
    "output_bytes",
)
ABLATION_FIELDS = (
    "share_removed",
    "before.wall.median",
    "after.wall.median",
    "before.cpu_s",
    "after.cpu_s",
    "before.peak_rss_bytes",
    "after.peak_rss_bytes",
)
"""The numbers a claim may cite, by the dotted names `Ledger` records them under."""


class ToolboxError(Exception):
    """The harness failed, not the subject. Raised; never shown to the agent as a fact."""


class Runs(Protocol):
    """What runs one command in isolation. `sandbox.runner.Sandbox` is the real one."""

    def run(
        self, command: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> ExecutionResult: ...


@dataclass(frozen=True)
class SandboxedToolbox:
    """A `Toolbox` for `agent.scan`: one container per call, one ledger per run."""

    sandbox: Runs
    ledger: Ledger
    timeout: float = TOOL_TIMEOUT_S

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Run one tool in a fresh container and describe what it found.

        Raises:
            ToolboxError: the harness itself failed -- no envelope, a malformed
                one, one for another tool, or a success its own model rejects.
                Nothing is recorded.
        """
        reader = READERS.get(tool)
        if reader is None:
            return ToolResult(content=f"there is no tool called {tool!r}")

        command = [*ENTRY, tool, json.dumps(dict(arguments))]
        try:
            executed = self.sandbox.run(command, timeout=self.timeout)
        except MemoryLimitExceededError:
            return ToolResult(
                content=f"{tool} was stopped: the run exceeded the container's memory limit. "
                "A smaller input may measure; this one cannot."
            )
        except ExecutionTimeoutError:
            return ToolResult(
                content=f"{tool} was stopped after {self.timeout:.0f}s, half the scan's time. "
                "A smaller input may finish."
            )

        envelope = _envelope(tool, executed)
        if not envelope.ok:
            return ToolResult(
                content=f"{tool} refused ({envelope.payload.get('error')}): "
                f"{envelope.payload.get('message')}"
            )
        try:
            return reader(envelope.payload, self.ledger)
        except ValidationError as malformed:
            message = (
                f"{tool} reported success with a payload its own model rejects: "
                f"{malformed.errors()[0]['msg']}. Nothing was recorded"
            )
            raise ToolboxError(message) from malformed


def _envelope(tool: str, executed: ExecutionResult) -> Envelope:
    """The last non-empty line of stdout, which is the only line that is ours.

    Last rather than only: a library the collector imports may print a warning
    first, and that is noise rather than a fault.
    """
    lines = [line for line in executed.stdout.splitlines() if line.strip()]
    if not lines:
        message = (
            f"{tool} printed no envelope (exit {executed.exit_code}): {executed.stderr[-500:]}"
        )
        raise ToolboxError(message)
    try:
        envelope = Envelope.model_validate_json(lines[-1])
    except ValidationError as malformed:
        message = f"{tool} printed something that is not an envelope: {lines[-1][:200]!r}"
        raise ToolboxError(message) from malformed
    if envelope.tool != tool:
        message = f"asked for {tool}, and the envelope answers for {envelope.tool!r}"
        raise ToolboxError(message)
    return envelope


def _measured(payload: Mapping[str, Any], ledger: Ledger) -> ToolResult:
    measurement = BareMeasurement.model_validate(payload)
    ledger.record(measurement)
    lines = [
        f"measured {' '.join(measurement.command)} -- {measurement.mode.value}",
        *_citable(measurement, BARE_FIELDS),
        *_gaps(measurement.not_measured),
    ]
    return ToolResult(
        content="\n".join(lines), measurement_id=measurement.measurement_id, verified=True
    )


def _profiled(payload: Mapping[str, Any], ledger: Ledger) -> ToolResult:
    measurement = ProfileMeasurement.model_validate(payload)
    ledger.record(measurement)
    lines = [
        f"profiled {' '.join(measurement.command)}: where the samples landed. These are "
        "candidates -- only an ablation says whether removing one saves anything.",
        *(
            f"  {site.self_share:.1%}  {site.file}:{site.line}  {site.symbol}"
            for site in measurement.sites[:TOP_SITES]
        ),
        *(f"  counts.{name} = {value}" for name, value in sorted(measurement.counts.items())),
        *_gaps(measurement.not_measured),
    ]
    return ToolResult(content="\n".join(lines), measurement_id=measurement.measurement_id)


def _ablated(payload: Mapping[str, Any], ledger: Ledger) -> ToolResult:
    measurement = AblationMeasurement.model_validate(payload)
    ledger.record(measurement)
    lines = [
        f"stubbed {measurement.symbol} ({measurement.file}:{measurement.line}) to return a "
        "constant, in a copy that no longer exists",
        *_citable(measurement, ABLATION_FIELDS),
        f"  the output {'changed' if measurement.output_changed else 'did not change'}",
    ]
    return ToolResult(content="\n".join(lines), measurement_id=measurement.measurement_id)


def _ran(payload: Mapping[str, Any], _: Ledger) -> ToolResult:
    result = CommandResult.model_validate(payload)
    ending = f"exit {result.returncode}" + (" (timed out)" if result.timed_out else "")
    parts = [ending]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return ToolResult(content="\n".join(parts))


def _read(payload: Mapping[str, Any], _: Ledger) -> ToolResult:
    window = FileWindow.model_validate(payload)
    lines = [f"{window.path}, lines {window.first_line}-{window.last_line} of {window.total_lines}"]
    lines.extend(
        f"{number:>6}  {text}" for number, text in enumerate(window.lines, start=window.first_line)
    )
    if window.more_below:
        lines.append(f"[more below; read from offset {window.last_line + 1}]")
    return ToolResult(content="\n".join(lines))


def _wrote(payload: Mapping[str, Any], _: Ledger) -> ToolResult:
    return ToolResult(content=f"created {Written.model_validate(payload).path}")


READERS: Mapping[str, Callable[[Mapping[str, Any], Ledger], ToolResult]] = {
    "bash": _ran,
    "read_file": _read,
    "write_file": _wrote,
    "measure": _measured,
    "profile": _profiled,
    "ablate": _ablated,
}
"""One reader per tool. A measurement reaches the ledger only through the three that
validate one; the other three record nothing, because they measure nothing."""


def _citable(measurement: BaseModel, names: Sequence[str]) -> list[str]:
    """`name = value` for each number, exactly as the ledger holds it.

    `str` of a float is its shortest round-tripping form, which is what JSON
    carried it as and what `Ledger.record` stored -- so the line the agent reads is
    the value a citation must match.
    """
    dumped = measurement.model_dump()
    lines: list[str] = []
    for name in names:
        value: Any = dumped
        for part in name.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            lines.append(f"  {name} = {value}")
    return lines


def _gaps(missing: Sequence[Unmeasured]) -> list[str]:
    return [f"  not measured: {gap.what} -- {gap.why}" for gap in missing]

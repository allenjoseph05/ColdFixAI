"""One tool call, inside the container. **S-28.1, ADR 182.**

`python -m coldfix.collect.run <tool> <arguments-json>` runs one of the scan
agent's six tools against the workspace and prints exactly one JSON envelope. The
host runs this in a fresh sandboxed container per call, validates the envelope
with the collector's own model, and only then records it in the ledger.

**A refusal is an answer, not a crash.** A workload that will not measure the same
way twice, a path outside the workspace, an argument the tool does not take, a
file `ablate` cannot parse -- each comes back with `ok: false` and the reason,
because the agent needs the reason to fix its driver. Anything else is a fault in
the harness and is allowed to crash: an envelope that papered over one would hand
the agent a harness bug dressed as a fact about the subject.

**Arguments are validated, and an unknown one is refused.** `bash` has no `cwd`
field, so asking for one is an error rather than a value quietly ignored -- the
same *absent, not guarded* shape `collect/workspace.py` gives the tools.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from coldfix.collect.ablation import ablate
from coldfix.collect.measurement import Clock, measure
from coldfix.collect.profiling import profile
from coldfix.collect.usage import ChildRunner, MeasurementError
from coldfix.collect.workspace import (
    MAX_LINES,
    Workspace,
    WorkspaceError,
    read_file,
    run_bash,
    write_file,
)

WORKSPACE = "/workspace"
"""Where `sandbox.runner` mounts the workspace -- a path in the container, so POSIX
whatever the host is. This module runs inside the container and imports nothing
from the host's sandbox, so a test holds the two equal instead."""

MAX_MESSAGE_CHARS = 2_000
"""A refusal's message, cut. Enough to act on; a validation error on a large
argument can otherwise repeat the argument back in full."""

REFUSALS = (
    ValidationError,
    json.JSONDecodeError,
    MeasurementError,
    WorkspaceError,
    OSError,
    SyntaxError,
    UnicodeDecodeError,
)
"""What a tool may legitimately refuse with. Each is about the request or the
subject: bad arguments, a workload that failed or would not repeat, a path that
escapes, a command that does not exist, a file `ablate` cannot parse."""


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Run(_Arguments):
    command: tuple[str, ...] = Field(min_length=1)
    """An argument list, never a shell string: what is measured is exactly this
    process, not a shell that starts it."""


class _Ablate(_Run):
    path: str
    symbol: str
    returns: str = "None"


class _Bash(_Arguments):
    command: str


class _Read(_Arguments):
    path: str
    offset: int = 0
    limit: int = MAX_LINES


class _Write(_Arguments):
    path: str
    content: str


class Written(BaseModel, frozen=True):
    """What `write_file` created, relative to the workspace."""

    path: str


class Envelope(BaseModel, frozen=True):
    """What one call printed. The host parses nothing else."""

    tool: str
    ok: bool
    payload: dict[str, Any]
    """The tool's result, dumped by its own model, when `ok`; the refusal's type
    and message when not."""


@dataclass(frozen=True)
class Seams:
    """What a test injects: how a child is run and how time is read. `None` means
    the real one."""

    runner: ChildRunner | None = None
    clock: Clock | None = None


def _measure(workspace: Workspace, raw: Mapping[str, Any], seams: Seams) -> BaseModel:
    arguments = _Run.model_validate(raw)
    return measure(
        arguments.command,
        cwd=workspace.resolved_root,
        runner=seams.runner,
        clock=seams.clock or time.perf_counter,
    )


def _profile(workspace: Workspace, raw: Mapping[str, Any], seams: Seams) -> BaseModel:
    arguments = _Run.model_validate(raw)
    return profile(arguments.command, cwd=workspace.resolved_root, runner=seams.runner)


def _ablate(workspace: Workspace, raw: Mapping[str, Any], seams: Seams) -> BaseModel:
    arguments = _Ablate.model_validate(raw)
    return ablate(
        arguments.command,
        cwd=workspace.resolved_root,
        path=arguments.path,
        symbol=arguments.symbol,
        returns=arguments.returns,
        runner=seams.runner,
        clock=seams.clock or time.perf_counter,
    )


def _bash(workspace: Workspace, raw: Mapping[str, Any], _: Seams) -> BaseModel:
    return run_bash(workspace, _Bash.model_validate(raw).command)


def _read(workspace: Workspace, raw: Mapping[str, Any], _: Seams) -> BaseModel:
    arguments = _Read.model_validate(raw)
    return read_file(workspace, arguments.path, offset=arguments.offset, limit=arguments.limit)


def _write(workspace: Workspace, raw: Mapping[str, Any], _: Seams) -> BaseModel:
    arguments = _Write.model_validate(raw)
    created = write_file(workspace, arguments.path, arguments.content)
    return Written(path=created.relative_to(workspace.resolved_root).as_posix())


TOOLS: Mapping[str, Callable[[Workspace, Mapping[str, Any], Seams], BaseModel]] = {
    "bash": _bash,
    "read_file": _read,
    "write_file": _write,
    "measure": _measure,
    "profile": _profile,
    "ablate": _ablate,
}
"""The six tools. `submit` is not here: it ends the scan loop on the host and
never reaches a container."""


def run(tool: str, arguments: str, *, workspace: Workspace, seams: Seams | None = None) -> Envelope:
    """Run one tool and describe what happened. Refusals are returned; faults raise."""
    handler = TOOLS.get(tool)
    if handler is None:
        return _refused(
            tool, "UnknownTool", f"there is no tool called {tool!r}; there are {', '.join(TOOLS)}"
        )
    try:
        raw = json.loads(arguments)
        if not isinstance(raw, dict):
            return _refused(tool, "BadArguments", "a tool's arguments are one JSON object")
        result = handler(workspace, raw, seams or Seams())
    except REFUSALS as refused:
        return _refused(tool, type(refused).__name__, str(refused))
    return Envelope(tool=tool, ok=True, payload=result.model_dump(mode="json"))


def main(argv: Sequence[str] | None = None) -> int:
    """The container's entry point. Prints one envelope and nothing else."""
    given = list(sys.argv[1:] if argv is None else argv)
    expected = 2
    if len(given) != expected:
        envelope = _refused(
            "", "Usage", "usage: python -m coldfix.collect.run <tool> <arguments-json>"
        )
        code = 2
    else:
        envelope = run(given[0], given[1], workspace=Workspace(Path(WORKSPACE)))
        code = 0
    sys.stdout.write(envelope.model_dump_json() + "\n")
    return code


def _refused(tool: str, error: str, message: str) -> Envelope:
    return Envelope(
        tool=tool, ok=False, payload={"error": error, "message": message[:MAX_MESSAGE_CHARS]}
    )


if __name__ == "__main__":
    raise SystemExit(main())

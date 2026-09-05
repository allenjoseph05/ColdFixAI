"""Delete the work and see whether the cost goes with it.

S-18.4. Sampling says where a program *was*; ablation says what removing that
work is *worth*. They disagree whenever the site was waiting on something else,
or the time simply moves. This is therefore the only tool whose payoff a finding
may call **proven**, and the tool the whole product rests on.

**It deliberately breaks the program.** That is not a side effect to be managed,
it is the method: replace the suspected work with a constant, run again, and
compare. A stubbed program is expected to produce different output, and
`output_changed` being `False` is the suspicious result -- it means the work made
no difference to what came out, which is a finding of its own.

**Three properties make it safe to break things.**

*The copy is separate.* Everything happens in a temporary directory built from
the workspace, and it is removed before this function returns. There is no code
path from here back to the original.

*The subject's source is never touched.* A test asserts the original file is
byte-identical afterwards.

*The stub can only be a constant.* `returns` is parsed with `ast.literal_eval`
before it is used, so an ablation cannot smuggle in code to execute. There is no
field on the result that could carry a diff either -- this function measures, and
producing a patch is not among the operations it has.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

from coldfix.collect.measurement import (
    DEFAULT_REPEATS,
    BareMeasurement,
    Clock,
    WorkloadFailedError,
    measure,
)
from coldfix.collect.usage import ChildRunner, MeasurementError


class SymbolNotFoundError(MeasurementError):
    """The symbol named for ablation is not in the file."""

    def __init__(self, symbol: str, path: Path) -> None:
        super().__init__(
            f"{symbol!r} is not a function or method defined in {path.name}; "
            "ablation names a definition, not a call site"
        )
        self.symbol = symbol


class NotAConstantError(MeasurementError):
    """A stub returns a constant, never an expression to evaluate."""

    def __init__(self, returns: str) -> None:
        super().__init__(
            f"{returns!r} is not a literal. A stub returns a constant -- [], None, 0, '' -- "
            "so that an ablation cannot introduce code of its own to run"
        )


class AblationBrokeTheSubjectError(MeasurementError):
    """Stubbing it stopped the program working, so its cost cannot be isolated.

    A real answer, not a failure of the tool: it says the work is structurally
    required, and whatever it costs cannot be measured by removing it.
    """

    def __init__(self, symbol: str, detail: str) -> None:
        super().__init__(
            f"stubbing {symbol!r} stopped the program running, so the cost of that work "
            f"cannot be separated by removing it: {detail[-300:]}"
        )
        self.symbol = symbol


class AblationMeasurement(BaseModel, frozen=True):
    """What removing one piece of work was worth.

    There is no diff field, and no path to the workspace it ran in. The copy is
    gone by the time this exists.
    """

    measurement_id: str
    symbol: str
    file: str
    line: int
    before: BareMeasurement
    after: BareMeasurement
    share_removed: float
    output_changed: bool

    def removed(self, metric: str) -> float | None:
        """The share of a named count the ablation removed, or `None` if absent."""
        was = getattr(self.before, metric, None)
        now = getattr(self.after, metric, None)
        if not isinstance(was, (int, float)) or not isinstance(now, (int, float)) or not was:
            return None
        return (was - now) / was


def stub_source(source: str, symbol: str, returns: str) -> tuple[str, int]:
    """Replace one function body with `return <constant>`, leaving the rest alone.

    Line-based rather than a full `ast.unparse`, because unparsing rewrites the
    whole file: every other line moves, comments vanish, and a profile taken
    afterwards would name lines that no longer correspond to anything.
    """
    try:
        literal = ast.parse(returns, mode="eval")
        ast.literal_eval(literal)
    except (SyntaxError, ValueError) as bad:
        raise NotAConstantError(returns) from bad

    tree = ast.parse(source)
    target = _find(tree, symbol.split("."))
    if target is None:
        raise SymbolNotFoundError(symbol, Path("<source>"))

    first = target.body[0]
    last = target.body[-1]
    indent = " " * first.col_offset
    lines = source.splitlines(keepends=True)
    replaced = [*lines[: first.lineno - 1], f"{indent}return {returns}\n"]
    if last.end_lineno is not None:
        replaced.extend(lines[last.end_lineno :])
    return "".join(replaced), target.lineno


def _find(node: ast.AST, path: Sequence[str]) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Walk `Class.method` or a bare `function` down the tree."""
    head, rest = path[0], path[1:]
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == head:
            return child if not rest else None
        if isinstance(child, ast.ClassDef) and child.name == head and rest:
            return _find(child, rest)
    return None


def ablate(  # noqa: PLR0913 - which file, which symbol, what to return in its place,
    # what to run, where, and how carefully. Every one is the caller's decision.
    command: Sequence[str],
    *,
    cwd: Path,
    path: str,
    symbol: str,
    returns: str = "None",
    repeats: int = DEFAULT_REPEATS,
    reset: Callable[[], None] | None = None,
    env: Mapping[str, str] | None = None,
    runner: ChildRunner | None = None,
    clock: Clock = time.perf_counter,
) -> AblationMeasurement:
    """Measure the workload, stub `symbol` in a copy, measure again, compare."""
    before = measure(
        command, cwd=cwd, repeats=repeats, reset=reset, env=env, runner=runner, clock=clock
    )

    workspace = Path(tempfile.mkdtemp(prefix="coldfix-ablation-"))
    try:
        copy = workspace / "subject"
        shutil.copytree(cwd, copy)
        target = copy / path
        if not target.exists():
            raise SymbolNotFoundError(symbol, target)
        stubbed, line = stub_source(target.read_text(encoding="utf-8"), symbol, returns)
        target.write_text(stubbed, encoding="utf-8")

        try:
            after = measure(
                command,
                cwd=copy,
                repeats=repeats,
                reset=reset,
                env=env,
                runner=runner,
                require_repeatable=False,
                clock=clock,
            )
        except WorkloadFailedError as broken:
            raise AblationBrokeTheSubjectError(symbol, str(broken)) from broken

        return AblationMeasurement(
            measurement_id=f"m-{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            file=path,
            line=line,
            before=before,
            after=after,
            share_removed=_share(before.wall.median, after.wall.median),
            output_changed=before.output_digest != after.output_digest,
        )
    finally:
        # Before returning, not on interpreter exit. Nothing survives this call
        # that could become a patch.
        shutil.rmtree(workspace, ignore_errors=True)


def _share(before: float, after: float) -> float:
    return (before - after) / before if before else 0.0

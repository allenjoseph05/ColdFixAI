"""Where the time is spent -- which is not where it can be saved.

S-18.3. `profile()` launches the subject under a sampling profiler and reports
the sites the samples landed in, each with a file, a line and the call path that
reached it. **The runtime names the files.** No model reads source to find them.

Two things this module is careful about.

**A hot site is a candidate, never a finding.** Sampling answers *where was the
program when we looked*. It does not answer *would speeding this up help*, and
the two differ whenever the site was waiting on something else or the time
merely moves elsewhere. Only `ablate` answers the second question, so nothing
here may be called proven.

**"Framework frames" are declared, not guessed.** The harness has no idea what a
framework is, and acquiring that knowledge is the thing v3 exists to remove. The
default strips only frames the interpreter itself synthesises -- `<frozen ...>`,
`<built-in>` -- and anything beyond that is a prefix the caller supplies.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from coldfix.collect.instrumented import InstrumentFailedError, measure_under
from coldfix.collect.measurement import (
    InstrumentedMeasurement,
    Unmeasured,
    WorkloadFailedError,
)
from coldfix.collect.usage import ChildResult, ChildRunner

DEFAULT_TOP = 20

ATTEMPTS = 2
"""py-spy intermittently loses its child under load. Retrying costs no model
tokens, so it is retried once before the instrument is declared broken."""

SYNTHETIC_FILE_PREFIX = "<"
"""`<frozen importlib._bootstrap>`, `<built-in>`, `<string>`. The interpreter's
own scaffolding, present in every profile of every program, and never the
answer."""


class Site(BaseModel, frozen=True):
    """One place the samples landed, and how it was reached."""

    file: str
    line: int
    symbol: str
    self_samples: int
    self_share: float
    call_path: tuple[str, ...]


class ProfileMeasurement(InstrumentedMeasurement, frozen=True):
    """A sampling run. Counts and sites; no timing, as with every instrumented pass."""

    sites: tuple[Site, ...] = ()


class _Frame:
    __slots__ = ("file", "line", "symbol")

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.symbol = str(raw.get("name") or "?")
        self.file = str(raw.get("file") or "?")
        self.line = int(raw.get("line") or 0)

    @property
    def synthetic(self) -> bool:
        return self.file.startswith(SYNTHETIC_FILE_PREFIX)

    def where(self) -> str:
        return f"{self.file}:{self.line}"


def sites_from_speedscope(
    document: Mapping[str, Any],
    *,
    top: int = DEFAULT_TOP,
    strip_prefixes: Sequence[str] = (),
) -> tuple[tuple[Site, ...], dict[str, int]]:
    """Group stacks into sites and walk each to its divergence point.

    Returns the sites and the counts worth recording beside them -- how many
    samples were taken, and how many landed nowhere we can name, which is the
    honest way to report a C extension the profiler cannot see into.
    """
    frames = [_Frame(raw) for raw in document.get("shared", {}).get("frames", [])]

    def keep(frame: _Frame) -> bool:
        return not frame.synthetic and not any(frame.file.startswith(p) for p in strip_prefixes)

    self_samples: Counter[tuple[str, str]] = Counter()
    lines: defaultdict[tuple[str, str], Counter[int]] = defaultdict(Counter)
    stacks: defaultdict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    total = 0
    unattributable = 0

    for profile_block in document.get("profiles", []):
        for sample in profile_block.get("samples", []):
            total += 1
            stack = [frames[i] for i in sample if 0 <= i < len(frames)]
            named = [frame for frame in stack if keep(frame)]
            if not named:
                # Every frame was interpreter scaffolding or a declared strip. A
                # sample with nowhere to attribute it is recorded, not dropped.
                unattributable += 1
                continue
            leaf = named[-1]
            key = (leaf.file, leaf.symbol)
            self_samples[key] += 1
            lines[key][leaf.line] += 1
            stacks[key].append(tuple(frame.where() for frame in named[:-1]))

    attributed = total - unattributable
    sites = tuple(
        Site(
            file=key[0],
            line=lines[key].most_common(1)[0][0],
            symbol=key[1],
            self_samples=count,
            self_share=count / attributed if attributed else 0.0,
            call_path=_divergence(stacks[key]),
        )
        for key, count in self_samples.most_common(top)
    )
    counts = {
        "samples": total,
        "attributed_samples": attributed,
        "unattributable_samples": unattributable,
    }
    return sites, counts


def _divergence(paths: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    """The deepest frame every occurrence shares.

    Two hundred identical stacks are one finding, not two hundred. Where the
    stacks differ, the point at which they stop agreeing is where one call path
    became many, and that is what somebody debugging wants to look at.
    """
    if not paths:
        return ()
    shortest = min(len(path) for path in paths)
    common: list[str] = []
    for depth in range(shortest):
        frame = paths[0][depth]
        if any(path[depth] != frame for path in paths):
            break
        common.append(frame)
    return tuple(common)


def profile(  # noqa: PLR0913 - what to run, where, how many sites, what counts as
    # somebody else's code, what environment, and which runner. All caller decisions.
    command: Sequence[str],
    *,
    cwd: Path,
    top: int = DEFAULT_TOP,
    strip_prefixes: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    runner: ChildRunner | None = None,
) -> ProfileMeasurement:
    """Run `command` under py-spy and report where the samples landed.

    **Launched, never attached.** Attaching to a running process needs
    `SYS_PTRACE`, which Docker drops by default; the `--` form needs no elevated
    capability at all. That removes a container requirement and one more thing
    that can fail on somebody else's machine.
    """
    output = cwd / "coldfix-profile.json"
    wrapped = ["py-spy", "record", "-o", str(output), "-f", "speedscope", "--", *command]
    captured: dict[str, tuple[Site, ...]] = {}

    def harvest(_: ChildResult) -> Mapping[str, int]:
        if not output.exists():
            return {"samples": 0, "attributed_samples": 0, "unattributable_samples": 0}
        document = json.loads(output.read_text(encoding="utf-8"))
        sites, counts = sites_from_speedscope(document, top=top, strip_prefixes=strip_prefixes)
        captured["sites"] = sites
        return counts

    measurement = None
    last = ""
    for _ in range(ATTEMPTS):
        output.unlink(missing_ok=True)
        try:
            measurement = measure_under(
                "py-spy", wrapped, cwd=cwd, harvest=harvest, env=env, runner=runner
            )
        except WorkloadFailedError as failure:
            # The subject is only implicated if py-spy managed to profile it.
            # No profile file means the instrument never got started.
            if output.exists():
                raise
            last = str(failure)
            continue
        break
    if measurement is None:
        raise InstrumentFailedError("py-spy", ATTEMPTS, last)
    return ProfileMeasurement(
        **measurement.model_dump(exclude={"not_measured"}),
        not_measured=_gaps(measurement.counts),
        sites=captured.get("sites", ()),
    )


def _gaps(counts: Mapping[str, int]) -> tuple[Unmeasured, ...]:
    """What the profiler could not see, said rather than implied."""
    gaps: list[Unmeasured] = []
    if not counts.get("samples"):
        gaps.append(
            Unmeasured(
                what="sites",
                why="py-spy produced no samples; the run may be too short to sample at all",
            )
        )
    blind = counts.get("unattributable_samples", 0)
    if blind:
        gaps.append(
            Unmeasured(
                what="native_frames",
                why=(
                    f"{blind} of {counts.get('samples', 0)} samples landed in code the profiler "
                    "cannot name -- a C extension, or the interpreter itself"
                ),
            )
        )
    return tuple(gaps)

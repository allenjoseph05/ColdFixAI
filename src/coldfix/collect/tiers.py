"""What could actually be measured here, rather than what was hoped for.

S-18.7. An image either lets us add a profiler to it or it does not, and which
one is true decides what a run can honestly claim. A finding that says *"not the
allocator"* is only true if the allocator was measured, so every run records the
tier it reached and what that tier could not see.

**Tiers are probed, never recognised.** There is no list of known-good images, no
check for `distroless` in a name, no catalogue of package managers. Each tier is
a capability, and a capability is established by *exercising* it:

| Tier | Question | How it is answered |
|---|---|---|
| 0 | can a process run at all? | run one and see |
| 1 | can instrumentation be added? | **build the derived image and see if the build succeeds** |
| 2 | is there a composed environment? | ask Docker to read one |

Building an image to find out whether an image can be built is slower than
matching a string, and it is the only method that is right about an image nobody
anticipated. The alternative -- probing for `pip`, then `apt-get`, then `apk`, then
whatever the next base image uses -- is a list that is incomplete on the day it
is written.

**The target's image is never modified.** The derived image is `FROM` it plus
layers of our own, built into a tag we own and thrown away with the run. What
the operator built stays exactly as they built it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

TOOL_PACKAGES = ("py-spy", "memray", "opentelemetry-distro", "opentelemetry-exporter-otlp")
"""What tier 1 buys. Named here rather than discovered, because these are *our*
requirements -- what the subject needs is the subject's business."""


class Tier(IntEnum):
    """How much of the toolkit this environment supports."""

    UNMEASURABLE = -1
    """Nothing runs. The honest ending, and a real answer."""

    OS_ONLY = 0
    """A process runs and the operating system counts it. Elapsed and processor
    time, peak memory, I/O, output size, growth across scales, and ablation --
    which adds nothing to the image and so needs nothing from it."""

    INSTRUMENTED = 1
    """Tools can be added: stacks with file and line, allocation counts, database
    and HTTP spans, import timing."""

    ORCHESTRATED = 2
    """A composed environment: the service topology is declared, and tearing the
    volumes down and back up is a reset that is correct by construction."""


AVAILABLE: dict[Tier, tuple[str, ...]] = {
    Tier.UNMEASURABLE: (),
    Tier.OS_ONLY: ("elapsed_and_processor_time", "peak_memory", "io_bytes", "output", "ablation"),
    Tier.INSTRUMENTED: ("stacks_with_line_numbers", "allocations", "spans", "import_timing"),
    Tier.ORCHESTRATED: ("service_topology", "verified_reset"),
}
"""What each tier adds. A fact about this toolkit, not a guess about the world."""


class Probe(BaseModel, frozen=True):
    """One capability, and whether exercising it worked."""

    name: str
    achieved: bool
    detail: str


class Capabilities(BaseModel, frozen=True):
    """The tier reached, how it was established, and what it cannot see."""

    image: str
    tier: Tier
    probes: tuple[Probe, ...]

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(
            name for tier, names in sorted(AVAILABLE.items()) if tier <= self.tier for name in names
        )

    @property
    def unavailable(self) -> tuple[str, ...]:
        """What a run here may not conclude anything about."""
        return tuple(
            name for tier, names in sorted(AVAILABLE.items()) if tier > self.tier for name in names
        )

    def statement(self) -> str:
        """The line that goes on the front of a report."""
        if self.tier is Tier.UNMEASURABLE:
            return f"{self.image} could not be made to run; nothing was measured"
        missing = ", ".join(self.unavailable)
        reached = f"tier {int(self.tier)} on {self.image}"
        if not missing:
            return f"{reached}; everything this toolkit measures was available"
        return f"{reached}; not measured here: {missing}"


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    detail: str


@runtime_checkable
class Docker(Protocol):
    """The seam. Real against the CLI, injected in tests.

    Narrow on purpose: three questions, none of which is *"what kind of image is
    this"*, because that question has no reliable answer.
    """

    def can_run(self, image: str) -> BuildResult: ...

    def build(self, dockerfile: str, tag: str) -> BuildResult: ...

    def reads_compose(self, root: Path) -> BuildResult: ...


def derived_dockerfile(image: str, packages: Sequence[str] = TOOL_PACKAGES) -> str:
    """A layer on top of the operator's image. Never a change to it.

    `USER root` is set for the install and left there: the derived image exists
    only to be measured in and is discarded with the run, and an image that
    cannot write to its own site-packages cannot receive a profiler.
    """
    return f"FROM {image}\nUSER root\nRUN pip install --no-cache-dir {' '.join(packages)}\n"


def detect(image: str, *, root: Path, docker: Docker, tag: str = "coldfix-derived") -> Capabilities:
    """Establish the tier by exercising each capability in turn.

    Stops climbing at the first thing that does not work, because the tiers are
    cumulative: an image that cannot run a process will not build either, and
    trying would only turn one honest answer into two confusing ones.
    """
    probes: list[Probe] = []

    runs = docker.can_run(image)
    probes.append(Probe(name="runs_a_process", achieved=runs.ok, detail=runs.detail))
    if not runs.ok:
        return Capabilities(image=image, tier=Tier.UNMEASURABLE, probes=tuple(probes))

    built = docker.build(derived_dockerfile(image), tag)
    probes.append(
        Probe(
            name="accepts_instrumentation",
            achieved=built.ok,
            detail=built.detail,
        )
    )
    if not built.ok:
        return Capabilities(image=image, tier=Tier.OS_ONLY, probes=tuple(probes))

    composed = docker.reads_compose(root)
    probes.append(Probe(name="composed_environment", achieved=composed.ok, detail=composed.detail))
    tier = Tier.ORCHESTRATED if composed.ok else Tier.INSTRUMENTED
    return Capabilities(image=image, tier=tier, probes=tuple(probes))

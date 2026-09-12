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
| — | can a process run at all? | run one and see |
| — | **can it carry the collector?** | build `FROM` it with this package's wheel |
| 0 | both of the above | nothing further is needed to time a process |
| 1 | can instrumentation be added? | build again, `FROM` the collector, and see |
| 2 | is there a composed environment? | ask Docker to read one |

The second row is a precondition rather than a tier, and it is why an image that
refuses this package is `UNMEASURABLE` rather than tier 0 (S-28.1b, ADR 191):
every tool call is `python -m coldfix.collect.run` *inside* the container, so an
image that will not have it cannot deliver even the elapsed time tier 0 claims.

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

from collections.abc import Mapping, Sequence
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
    """The operator's image, as configured. Never modified."""

    tier: Tier
    probes: tuple[Probe, ...]

    runs_in: str = ""
    """The image a tool call should actually run in. **S-28.1b.**

    The derived tag once one was built, and the operator's image otherwise. It
    exists because every tool call is `python -m coldfix.collect.run` inside the
    container, so a run that used `image` here would be running an image with no
    collector in it -- which is what happened before this field: `detect` built a
    derived tag as a probe and then discarded it.
    """

    @property
    def entry_image(self) -> str:
        """Where to run, falling back to the operator's image when nothing was built."""
        return self.runs_in or self.image

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

    `context` is the build context: filenames mapped to the files that supply
    them, copied in beside the Dockerfile. It exists because the collector
    reaches the image as a wheel, and a `COPY` needs something to copy.
    """

    def can_run(self, image: str) -> BuildResult: ...

    def build(
        self, dockerfile: str, tag: str, context: Mapping[str, Path] | None = None
    ) -> BuildResult: ...

    def reads_compose(self, root: Path) -> BuildResult: ...


def collector_dockerfile(image: str, wheel: str) -> str:
    """The image every tool call runs in: theirs, plus this package.

    **Required, not an upgrade.** `agent/toolbox.py` runs
    `python -m coldfix.collect.run` inside the container, so without this there
    is no `measure`, no `bash`, and no `read_file` -- an image that cannot take
    it cannot be measured at all, which is why `detect` treats a failure here as
    `UNMEASURABLE` rather than as tier 0.

    **`--no-deps`, plus pydantic and nothing else.** This package declares
    `anthropic`, `langgraph`, two checkpointers and `psycopg`, and every one of
    them belongs to the half that runs on the *host*. Nothing under `collect/`
    imports anything but the standard library and pydantic, so installing the
    declared set would push the whole agent stack into somebody else's image --
    slow, pointless, and a real chance of moving a pin the subject depends on.

    `USER root` is set for the install and left there: the derived image exists
    only to be measured in and is discarded with the run, and an image that
    cannot write to its own site-packages cannot receive anything.
    """
    return (
        f"FROM {image}\n"
        "USER root\n"
        f"COPY {wheel} /tmp/{wheel}\n"
        f'RUN pip install --no-cache-dir --no-deps "/tmp/{wheel}" && '
        'pip install --no-cache-dir "pydantic>=2.9"\n'
    )


def derived_dockerfile(image: str, packages: Sequence[str] = TOOL_PACKAGES) -> str:
    """The collector image, plus the instruments. A layer on a layer.

    `image` here is the *collector* tag, not the operator's: built `FROM` that
    rather than repeating its install, so a failure is unambiguously about the
    instruments. The collector already installed, or this build would never have
    been attempted.
    """
    return f"FROM {image}\nUSER root\nRUN pip install --no-cache-dir {' '.join(packages)}\n"


def detect(  # noqa: PLR0913 - the image, the repository, the docker seam, the wheel
    # and the two tags are six independent decisions of the caller's, and none is
    # derivable from another. A config object holding them would be an abstraction
    # with one implementation whose only purpose is to be unpacked here.
    image: str,
    *,
    root: Path,
    docker: Docker,
    wheel: Path,
    collector_tag: str = "coldfix-collector",
    tag: str = "coldfix-derived",
) -> Capabilities:
    """Establish the tier by exercising each capability in turn.

    Stops climbing at the first thing that does not work, because the tiers are
    cumulative: an image that cannot run a process will not build either, and
    trying would only turn one honest answer into two confusing ones.

    **The collector is the second probe and it is not optional (S-28.1b, ADR
    191).** Every tool call runs `python -m coldfix.collect.run` in the
    container, so an image that cannot take this package cannot be measured at
    all -- not even for elapsed time. Reporting that as tier 0 would advertise
    exactly the measurements it cannot deliver.
    """
    probes: list[Probe] = []

    runs = docker.can_run(image)
    probes.append(Probe(name="runs_a_process", achieved=runs.ok, detail=runs.detail))
    if not runs.ok:
        return Capabilities(image=image, tier=Tier.UNMEASURABLE, probes=tuple(probes))

    carries = docker.build(
        collector_dockerfile(image, wheel.name), collector_tag, {wheel.name: wheel}
    )
    probes.append(Probe(name="carries_the_collector", achieved=carries.ok, detail=carries.detail))
    if not carries.ok:
        return Capabilities(image=image, tier=Tier.UNMEASURABLE, probes=tuple(probes))

    built = docker.build(derived_dockerfile(collector_tag), tag)
    probes.append(Probe(name="accepts_instrumentation", achieved=built.ok, detail=built.detail))
    if not built.ok:
        # Tier 0 runs in the collector image: the operator's image plus this
        # package and nothing else. That is what makes tier 0's own list --
        # elapsed time, memory, I/O, ablation -- true rather than advertised.
        return Capabilities(
            image=image, tier=Tier.OS_ONLY, probes=tuple(probes), runs_in=collector_tag
        )

    composed = docker.reads_compose(root)
    probes.append(Probe(name="composed_environment", achieved=composed.ok, detail=composed.detail))
    tier = Tier.ORCHESTRATED if composed.ok else Tier.INSTRUMENTED
    return Capabilities(image=image, tier=tier, probes=tuple(probes), runs_in=tag)

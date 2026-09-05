"""S-18.7.

Every tier path is covered with an injected `Docker`, so the logic is tested
without Docker running. Two real images then confirm the probes answer correctly
about environments nobody wrote them for -- one that accepts a profiler and one
that does not -- and those are marked `slow`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from coldfix.collect._docker_cli import DockerCli
from coldfix.collect.tiers import (
    TOOL_PACKAGES,
    BuildResult,
    Capabilities,
    Probe,
    Tier,
    derived_dockerfile,
    detect,
)

HERE = Path()

OK = BuildResult(True, "worked")
NO = BuildResult(False, "did not work")


class FakeDocker:
    """Answers each probe however the test needs, and counts what was asked."""

    def __init__(self, runs: BuildResult, builds: BuildResult, composes: BuildResult) -> None:
        self._runs, self._builds, self._composes = runs, builds, composes
        self.asked: list[str] = []
        self.dockerfile = ""

    def can_run(self, image: str) -> BuildResult:
        self.asked.append("can_run")
        return self._runs

    def build(self, dockerfile: str, tag: str) -> BuildResult:
        self.asked.append("build")
        self.dockerfile = dockerfile
        return self._builds

    def reads_compose(self, root: Path) -> BuildResult:
        self.asked.append("reads_compose")
        return self._composes


def detected(runs: BuildResult, builds: BuildResult, composes: BuildResult) -> Capabilities:
    return detect("some/image", root=HERE, docker=FakeDocker(runs, builds, composes))


# ------------------------------------------------------------- the four tiers


def test_an_image_nothing_runs_in_is_unmeasurable_and_says_so() -> None:
    """An honest ending, not a crash. There is no tier at which guessing is
    better than saying nothing was measured."""
    capabilities = detected(NO, OK, OK)
    assert capabilities.tier is Tier.UNMEASURABLE
    assert "could not be made to run" in capabilities.statement()
    assert capabilities.available == ()


def test_an_image_that_runs_but_takes_no_tools_is_tier_zero() -> None:
    """The distroless case. Clocks, memory, I/O and ablation still work, because
    none of them adds anything to the image."""
    capabilities = detected(OK, NO, OK)
    assert capabilities.tier is Tier.OS_ONLY
    assert "ablation" in capabilities.available
    assert "elapsed_and_processor_time" in capabilities.available
    assert "stacks_with_line_numbers" in capabilities.unavailable


def test_an_image_that_accepts_tools_without_compose_is_tier_one() -> None:
    capabilities = detected(OK, OK, NO)
    assert capabilities.tier is Tier.INSTRUMENTED
    assert "stacks_with_line_numbers" in capabilities.available
    assert "verified_reset" in capabilities.unavailable


def test_a_composed_environment_is_tier_two() -> None:
    capabilities = detected(OK, OK, OK)
    assert capabilities.tier is Tier.ORCHESTRATED
    assert capabilities.unavailable == ()
    assert "everything this toolkit measures" in capabilities.statement()


# ------------------------------------------------------------- how it decides


def test_climbing_stops_at_the_first_thing_that_does_not_work() -> None:
    """An image that cannot run a process will not build either. Asking anyway
    turns one honest answer into two confusing ones."""
    docker = FakeDocker(NO, OK, OK)
    detect("some/image", root=HERE, docker=docker)
    assert docker.asked == ["can_run"]


def test_a_tier_zero_image_is_never_asked_about_compose() -> None:
    docker = FakeDocker(OK, NO, OK)
    detect("some/image", root=HERE, docker=docker)
    assert docker.asked == ["can_run", "build"]


def test_nothing_is_decided_from_the_name_of_the_image() -> None:
    """The whole point. `distroless` in a name proves nothing, and an image
    called `python:3.12` may have had its package manager stripped."""
    misleading = FakeDocker(OK, NO, NO)
    honest = detect("python:3.12-slim", root=HERE, docker=misleading)
    assert honest.tier is Tier.OS_ONLY

    reassuring = FakeDocker(OK, OK, NO)
    assert (
        detect("gcr.io/distroless/static", root=HERE, docker=reassuring).tier is Tier.INSTRUMENTED
    )


def test_every_probe_is_recorded_with_what_it_found() -> None:
    """So a reader can see how the tier was reached, not only what it was."""
    capabilities = detected(OK, NO, OK)
    names = [probe.name for probe in capabilities.probes]
    assert names == ["runs_a_process", "accepts_instrumentation"]
    assert capabilities.probes[0].achieved
    assert not capabilities.probes[1].achieved
    assert capabilities.probes[1].detail


# ------------------------------------------------------------ the derived image


def test_the_derived_image_is_a_layer_on_the_operators_image() -> None:
    """`FROM` theirs. What they built stays exactly as they built it."""
    dockerfile = derived_dockerfile("their/app:1.4")
    assert dockerfile.startswith("FROM their/app:1.4\n")
    for package in TOOL_PACKAGES:
        assert package in dockerfile


def test_the_dockerfile_the_probe_builds_is_the_one_it_reports() -> None:
    docker = FakeDocker(OK, OK, NO)
    detect("their/app:1.4", root=HERE, docker=docker)
    assert docker.dockerfile == derived_dockerfile("their/app:1.4")


def test_a_report_says_what_was_not_measured_rather_than_implying_it_was() -> None:
    """`not the allocator` is only sayable where the allocator was measured."""
    statement = detected(OK, NO, OK).statement()
    assert "not measured here" in statement
    assert "allocations" in statement


def test_the_unavailable_list_is_the_complement_of_the_available_one() -> None:
    """Both halves stated, so a dimension cannot go missing from both and be
    quietly forgotten."""
    for capabilities in (detected(OK, NO, OK), detected(OK, OK, NO), detected(OK, OK, OK)):
        everything = set(capabilities.available) | set(capabilities.unavailable)
        assert not set(capabilities.available) & set(capabilities.unavailable)
        assert everything == {
            "elapsed_and_processor_time",
            "peak_memory",
            "io_bytes",
            "output",
            "ablation",
            "stacks_with_line_numbers",
            "allocations",
            "spans",
            "import_timing",
            "service_topology",
            "verified_reset",
        }


# ------------------------------------------------------------- against Docker


needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not on PATH")


@pytest.mark.slow
@needs_docker
def test_a_real_image_with_a_package_manager_reaches_tier_one(tmp_path: Path) -> None:
    capabilities = detect("python:3.12-slim", root=tmp_path, docker=DockerCli())
    assert capabilities.tier >= Tier.INSTRUMENTED, capabilities.statement()


@pytest.mark.slow
@needs_docker
def test_a_real_image_without_python_stays_at_tier_zero(tmp_path: Path) -> None:
    """`busybox` runs processes and has a shell, and no amount of shell makes a
    profiler installable. Nothing about its *name* says so -- the build is what
    says so."""
    capabilities = detect("busybox:stable", root=tmp_path, docker=DockerCli())
    assert capabilities.tier is Tier.OS_ONLY, capabilities.statement()
    assert "ablation" in capabilities.available
    assert "stacks_with_line_numbers" in capabilities.unavailable


def test_a_probe_result_is_a_model_that_survives_a_checkpoint() -> None:
    """It ends up in run state, so it has to serialize like everything else."""
    capabilities = detected(OK, NO, OK)
    revived = Capabilities.model_validate_json(capabilities.model_dump_json())
    assert revived == capabilities
    assert isinstance(revived.probes[0], Probe)

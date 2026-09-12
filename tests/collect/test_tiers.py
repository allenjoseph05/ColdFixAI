"""S-18.7.

Every tier path is covered with an injected `Docker`, so the logic is tested
without Docker running. Two real images then confirm the probes answer correctly
about environments nobody wrote them for -- one that accepts a profiler and one
that does not -- and those are marked `slow`.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

from coldfix.collect._docker_cli import DockerCli
from coldfix.collect.tiers import (
    TOOL_PACKAGES,
    BuildResult,
    Capabilities,
    Probe,
    Tier,
    collector_dockerfile,
    derived_dockerfile,
    detect,
)
from coldfix.collect.wheel import build_wheel

HERE = Path()

OK = BuildResult(True, "worked")
NO = BuildResult(False, "did not work")


WHEEL = Path("coldfix-0.1.0-py3-none-any.whl")


class FakeDocker:
    """Answers each probe however the test needs, and records what was asked.

    Two build answers, because there are two builds: the collector layer that
    every tool call needs, and the instruments on top of it (S-28.1b).
    """

    def __init__(
        self,
        runs: BuildResult,
        carries: BuildResult,
        builds: BuildResult,
        composes: BuildResult,
    ) -> None:
        self._runs, self._carries, self._builds = runs, carries, builds
        self._composes = composes
        self.asked: list[str] = []
        self.dockerfiles: list[str] = []
        self.tags: list[str] = []
        self.contexts: list[Mapping[str, Path]] = []

    def can_run(self, image: str) -> BuildResult:
        self.asked.append("can_run")
        return self._runs

    def build(
        self, dockerfile: str, tag: str, context: Mapping[str, Path] | None = None
    ) -> BuildResult:
        self.asked.append("build")
        self.dockerfiles.append(dockerfile)
        self.tags.append(tag)
        self.contexts.append(dict(context or {}))
        # First build is the collector, second the instruments. Counted off the
        # tags rather than a flag, so the order is read from what was asked.
        return self._carries if len(self.tags) == 1 else self._builds

    def reads_compose(self, root: Path) -> BuildResult:
        self.asked.append("reads_compose")
        return self._composes


def detected(
    runs: BuildResult, builds: BuildResult, composes: BuildResult, carries: BuildResult = OK
) -> Capabilities:
    """The three original answers, with the collector build defaulting to working.

    Defaulted so the tier tests below still read as *runs / instruments /
    compose*: the collector layer is a precondition rather than a tier, and a
    test about tier 1 should not have to restate it.
    """
    return detect(
        "some/image", root=HERE, docker=FakeDocker(runs, carries, builds, composes), wheel=WHEEL
    )


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
    docker = FakeDocker(NO, OK, OK, OK)
    detect("some/image", root=HERE, docker=docker, wheel=WHEEL)
    assert docker.asked == ["can_run"]


def test_a_tier_zero_image_is_never_asked_about_compose() -> None:
    """Two builds by the time it stops: the collector, which every tool call
    needs, and the instruments, which this image would not take."""
    docker = FakeDocker(OK, OK, NO, OK)
    detect("some/image", root=HERE, docker=docker, wheel=WHEEL)
    assert docker.asked == ["can_run", "build", "build"]


def test_nothing_is_decided_from_the_name_of_the_image() -> None:
    """The whole point. `distroless` in a name proves nothing, and an image
    called `python:3.12` may have had its package manager stripped."""
    misleading = FakeDocker(OK, OK, NO, NO)
    honest = detect("python:3.12-slim", root=HERE, docker=misleading, wheel=WHEEL)
    assert honest.tier is Tier.OS_ONLY

    reassuring = FakeDocker(OK, OK, OK, NO)
    assert (
        detect("gcr.io/distroless/static", root=HERE, docker=reassuring, wheel=WHEEL).tier
        is Tier.INSTRUMENTED
    )


def test_every_probe_is_recorded_with_what_it_found() -> None:
    """So a reader can see how the tier was reached, not only what it was."""
    capabilities = detected(OK, NO, OK)
    names = [probe.name for probe in capabilities.probes]
    assert names == ["runs_a_process", "carries_the_collector", "accepts_instrumentation"]
    assert capabilities.probes[0].achieved
    assert capabilities.probes[1].achieved, "the collector went in; the instruments did not"
    assert not capabilities.probes[2].achieved
    assert capabilities.probes[2].detail


# ------------------------------------------------------------ the derived image


def test_the_collector_image_is_a_layer_on_the_operators_image() -> None:
    """`FROM` theirs. What they built stays exactly as they built it."""
    dockerfile = collector_dockerfile("their/app:1.4", WHEEL.name)
    assert dockerfile.startswith("FROM their/app:1.4\n")
    assert f"COPY {WHEEL.name}" in dockerfile


def test_the_collector_is_installed_without_this_package_s_own_dependencies() -> None:
    """`collect/` imports the standard library and pydantic. The rest of what
    this package declares -- anthropic, langgraph, psycopg -- belongs to the half
    that runs on the host, and installing it would push the whole agent stack
    into somebody else's image and move pins the subject depends on."""
    dockerfile = collector_dockerfile("their/app:1.4", WHEEL.name)

    assert "--no-deps" in dockerfile
    assert "pydantic" in dockerfile
    for host_only in ("anthropic", "langgraph", "psycopg"):
        assert host_only not in dockerfile


def test_the_instruments_are_a_layer_on_the_collector_not_on_the_subject() -> None:
    """So a failure there is unambiguously about the instruments: the collector
    already installed, or this build would never have been attempted."""
    dockerfile = derived_dockerfile("coldfix-collector")
    assert dockerfile.startswith("FROM coldfix-collector\n")
    for package in TOOL_PACKAGES:
        assert package in dockerfile


def test_the_dockerfiles_the_probe_builds_are_the_ones_it_reports() -> None:
    docker = FakeDocker(OK, OK, NO, NO)
    detect("their/app:1.4", root=HERE, docker=docker, wheel=WHEEL)

    assert docker.dockerfiles == [
        collector_dockerfile("their/app:1.4", WHEEL.name),
        derived_dockerfile("coldfix-collector"),
    ]


def test_the_wheel_reaches_the_build_context() -> None:
    """A `COPY` needs something to copy. Before this the build context was an
    empty temporary directory, so there was nowhere for the collector to be."""
    docker = FakeDocker(OK, OK, OK, NO)
    detect("their/app:1.4", root=HERE, docker=docker, wheel=WHEEL)

    assert docker.contexts[0] == {WHEEL.name: WHEEL}
    assert docker.contexts[1] == {}, "the instrument layer copies nothing"


def test_an_image_that_will_not_take_the_collector_is_unmeasurable() -> None:
    """Not tier 0. Tier 0 advertises elapsed time, memory, I/O and ablation, and
    every one of those is this package running inside the container -- so an
    image that refuses it cannot deliver them, and reporting tier 0 would promise
    measurements that cannot be taken."""
    capabilities = detected(OK, OK, OK, carries=NO)

    assert capabilities.tier is Tier.UNMEASURABLE
    assert capabilities.available == ()
    assert [probe.name for probe in capabilities.probes] == [
        "runs_a_process",
        "carries_the_collector",
    ]


def test_the_image_a_tool_call_runs_in_is_the_one_that_was_built() -> None:
    """The tag `detect` builds used to be discarded, so a run would have executed
    `python -m coldfix.collect.run` in an image with no collector in it."""
    assert detected(OK, OK, OK).runs_in == "coldfix-derived"
    assert detected(OK, NO, OK).runs_in == "coldfix-collector", "tier 0 still needs the collector"
    assert detected(NO, OK, OK).entry_image == "some/image", "nothing built, so nothing to run in"


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


def test_the_build_context_carries_the_files_the_dockerfile_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `Docker`'s half of the context, which only the fake covered.

    Assembled in a temporary directory rather than pointing docker at the
    repository: a context pointed at the subject's tree uploads it to the daemon,
    and would let a `COPY` in a generated Dockerfile reach a file this system
    never meant to send.
    """
    wheel = tmp_path / "coldfix-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"not really a wheel")
    contexts: list[tuple[Path, list[str]]] = []

    class Ran:
        ok = True
        detail = ""

    def fake_run(argv: list[str], timeout: float, *, cwd: Path | None = None) -> Ran:
        del timeout, cwd
        directory = Path(argv[-1])
        contexts.append((directory, sorted(item.name for item in directory.iterdir())))
        return Ran()

    monkeypatch.setattr("coldfix.collect._docker_cli._run", fake_run)

    result = DockerCli().build("FROM x\nCOPY the.whl .\n", "some-tag", {wheel.name: wheel})

    assert result.ok
    directory, names = contexts[0]
    assert names == ["Dockerfile", wheel.name], "the wheel is beside the Dockerfile"
    assert not directory.exists(), "and the context is destroyed with the build"


needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not on PATH")


@pytest.mark.slow
@needs_docker
def test_a_real_image_with_a_package_manager_reaches_tier_one(tmp_path: Path) -> None:
    capabilities = detect(
        "python:3.12-slim", root=tmp_path, docker=DockerCli(), wheel=build_wheel()
    )
    assert capabilities.tier >= Tier.INSTRUMENTED, capabilities.statement()
    assert capabilities.runs_in != capabilities.image


@pytest.mark.slow
@needs_docker
def test_a_real_image_without_python_is_unmeasurable(tmp_path: Path) -> None:
    """`busybox` runs processes and has a shell, and no amount of shell installs
    a Python package. It used to report tier 0 -- which advertises ablation, and
    ablation is this package running in the container. Nothing about its *name*
    says so; the build is what says so."""
    capabilities = detect("busybox:stable", root=tmp_path, docker=DockerCli(), wheel=build_wheel())

    assert capabilities.tier is Tier.UNMEASURABLE, capabilities.statement()
    assert capabilities.available == ()


def test_a_probe_result_is_a_model_that_survives_a_checkpoint() -> None:
    """It ends up in run state, so it has to serialize like everything else."""
    capabilities = detected(OK, NO, OK)
    revived = Capabilities.model_validate_json(capabilities.model_dump_json())
    assert revived == capabilities
    assert isinstance(revived.probes[0], Probe)

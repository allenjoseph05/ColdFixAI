"""S-28.1b — one real tool call, in a real container. ADR 191.

Everything else about the toolbox is exercised against a fake sandbox that
answers with envelopes. That is the right shape for testing the host's side of
the line, and it cannot catch the thing this file exists for: until now the
derived image never carried this package, so `python -m coldfix.collect.run`
would have failed with `ModuleNotFoundError` on the first call of a real run,
and every test in the suite would still have passed.

Skipped where no daemon is listening. The `docker` marker alone does not skip --
it selects -- so the check is explicit, and a missing base image says which
command to run rather than failing as though something were broken.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from coldfix.agent.toolbox import SandboxedToolbox
from coldfix.bench.execute import execute
from coldfix.collect._docker_cli import DockerCli
from coldfix.collect.tiers import collector_dockerfile
from coldfix.collect.wheel import build_wheel
from coldfix.evidence.ledger import Ledger
from coldfix.sandbox.runner import Sandbox, docker_available

pytestmark = [pytest.mark.docker, pytest.mark.slow]

BASE = "python:3.12-slim"
TAG = "coldfix-collector-test"

SUBJECT = """\
total = 0
for index in range(400_000):
    total += index % 7
print(f"books: {total}")
"""
"""Deterministic, and doing enough work to be worth timing.

**Whether it is *measurable* is a property of the machine, not of this file**, and
that is why the assertions below accept a refusal. Measured on the host, the
wall-time spread across five repeats does not fall as the workload grows -- it
rises, because a longer run collects more scheduler interference:

    n=  400,000   median=  164ms   spread= 11%
    n=2,000,000   median=  363ms   spread= 15%
    n=8,000,000   median= 1178ms   spread= 29%

`measure` refuses anything above a 20% spread, and inside Docker Desktop's VM the
variance is worse still -- a real container reported 34% for the first size. So
there is no workload size that is reliably measurable on a contended laptop, and
a test that insisted on one would be green only on a quiet machine. That is the
failure the `timing` marker exists to prevent, one layer out.
"""

MEASUREMENT_REFUSALS = ("WorkloadTooShortError", "NotRepeatableError")
"""What `measure` may legitimately decline with here, by name.

Named rather than accepting any failure, because the thing under test is that the
collector is *in the image*: a refusal like these is proof it imported, validated
the arguments, ran the workload five times and applied its own rule. A container
without the collector produces no envelope at all, which the toolbox raises as a
`ToolboxError` rather than returning -- so these two cannot mask it.
"""


@pytest.fixture(scope="module")
def collector_image() -> Iterator[str]:
    """The operator's image plus this package, built once for the module."""
    if not docker_available():
        pytest.skip("no Docker daemon is listening")
    present = execute(["docker", "image", "inspect", BASE], timeout=60.0)
    if present.exit_code != 0:
        pytest.skip(f"{BASE} is not present locally; run `docker pull {BASE}`")

    wheel = build_wheel()
    built = DockerCli().build(collector_dockerfile(BASE, wheel.name), TAG, {wheel.name: wheel})
    if not built.ok:
        pytest.fail(f"the collector image did not build: {built.detail}")
    try:
        yield TAG
    finally:
        execute(["docker", "image", "rm", "--force", TAG], timeout=120.0)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(SUBJECT, encoding="utf-8")
    return tmp_path


def tools(image: str, workspace: Path) -> tuple[SandboxedToolbox, Ledger]:
    ledger = Ledger()
    return SandboxedToolbox(
        sandbox=Sandbox(image=image, workspace=workspace), ledger=ledger
    ), ledger


def test_a_command_runs_in_the_collector_image(collector_image: str, workspace: Path) -> None:
    """The simplest proof that the image carries the collector: `bash` is
    dispatched by `coldfix.collect.run`, so a reply at all means the module
    imported inside the container."""
    box, _ = tools(collector_image, workspace)

    result = box.call("bash", {"command": "python app.py"})

    assert "books: " in result.content
    assert "exit 0" in result.content


def test_a_real_measurement_runs_and_answers_on_its_own_terms(
    collector_image: str, workspace: Path
) -> None:
    """The story's acceptance criterion: one real tool call, inside a container
    built from the operator's image plus this package.

    A verified measurement and a named refusal are **both** passes, and that is
    the point rather than a concession. Either one proves `coldfix.collect.run`
    imported in the container, parsed its arguments, ran the workload five times
    and applied its own rule -- which is what this story exists to establish.
    Whether the numbers clear a 20% spread is a fact about the machine.
    """
    box, ledger = tools(collector_image, workspace)

    result = box.call("measure", {"command": ["python", "app.py"]})

    if result.verified:
        assert result.measurement_id
        record = ledger.recorded(result.measurement_id)
        assert record is not None
        assert record["wall.median"] > 0
        return

    assert any(named in result.content for named in MEASUREMENT_REFUSALS), result.content
    assert ledger.known == (), "a refused measurement records nothing"


def test_the_number_the_agent_is_shown_is_the_number_a_claim_must_cite(
    collector_image: str, workspace: Path
) -> None:
    """`Ledger.attest` compares a cited value for equality, so a summary that
    rounded `2.4103` to `2.410` would turn every honest citation into a
    fabricated one. Asserted here against a real measurement, where the digits
    are whatever the machine produced rather than whatever a fixture chose."""
    box, ledger = tools(collector_image, workspace)

    result = box.call("measure", {"command": ["python", "app.py"]})
    if not result.verified:
        pytest.skip(f"this machine could not measure the workload: {result.content}")

    record = ledger.recorded(result.measurement_id or "")
    assert record is not None

    shown = re.search(r"wall\.median = (\S+)", result.content)
    assert shown is not None, result.content
    assert float(shown.group(1)) == record["wall.median"]
    assert shown.group(1) == str(record["wall.median"]), "shown exactly, not reformatted"

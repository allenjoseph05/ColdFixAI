"""S-18.4.

The source transform is a pure function and is tested directly. The rest runs
real subprocesses, which works on any platform because the usage numbers come
from an injected runner.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.collect.ablation import (
    AblationBrokeTheSubjectError,
    AblationMeasurement,
    NotAConstantError,
    SymbolNotFoundError,
    ablate,
    stub_source,
)
from coldfix.collect.usage import ChildResult, Usage

SOURCE = '''\
import sys

CONSTANT = 3


def cheap(n):
    return n + 1


class Store:
    def fetch(self, key):
        """Docstring that is part of the body."""
        rows = [key] * 5
        return rows

    def other(self, key):
        return key


def main():
    print(Store().fetch(1), cheap(2))
'''


class FixedClock:
    """Every run takes exactly the same time, so the spread is zero.

    The child really runs -- output, exit codes and the digest are genuine, which
    is what these tests are about -- but elapsed time comes from here. Otherwise
    a busy host makes the baseline unrepeatable and the test fails for a reason
    that has nothing to do with what it is checking.
    """

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls % 2 == 0:
            self.now += self.step
        return self.now


class RealRunner:
    """Really runs the child; reports usage we control."""

    def __init__(self, cpu_s: float = 1.0) -> None:
        self.cpu_s = cpu_s

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        return ChildResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            usage=Usage(cpu_s=self.cpu_s, peak_rss_bytes=4096, read_blocks=0, write_blocks=0),
        )


# ------------------------------------------------------------- the transform


def test_a_method_body_is_replaced_and_nothing_else_moves() -> None:
    """Line-based rather than unparsing the whole file: unparsing moves every
    other line, so a profile taken afterwards would name lines that no longer
    correspond to anything."""
    stubbed, line = stub_source(SOURCE, "Store.fetch", "[]")
    assert "return []" in stubbed
    assert "Docstring that is part of the body" not in stubbed
    # Everything outside the stubbed body is untouched.
    assert "CONSTANT = 3" in stubbed
    assert "def cheap(n):\n    return n + 1" in stubbed
    assert "def other(self, key):\n        return key" in stubbed
    assert line == SOURCE.splitlines().index("    def fetch(self, key):") + 1


def test_a_bare_function_is_found_too() -> None:
    stubbed, _ = stub_source(SOURCE, "cheap", "0")
    assert "def cheap(n):\n    return 0" in stubbed


def test_the_stubbed_file_is_still_valid_python() -> None:
    ast.parse(stub_source(SOURCE, "Store.fetch", "[]")[0])


def test_a_symbol_that_is_not_there_is_named_rather_than_silently_skipped() -> None:
    """Silently skipping would produce an ablation that removed nothing and a
    payoff of zero, which reads exactly like proof that the work is free."""
    with pytest.raises(SymbolNotFoundError, match="missing"):
        stub_source(SOURCE, "missing", "None")
    with pytest.raises(SymbolNotFoundError):
        stub_source(SOURCE, "Store.absent", "None")


def test_a_stub_can_only_return_a_constant() -> None:
    """So an ablation cannot introduce code of its own to run inside the subject."""
    for attempted in ("os.system('rm -rf /')", "open('x').read()", "1 + cheap(2)"):
        with pytest.raises(NotAConstantError):
            stub_source(SOURCE, "cheap", attempted)

    for allowed in ("[]", "None", "0", "''", "{}", "(1, 2)"):
        stub_source(SOURCE, "cheap", allowed)


# ------------------------------------------------------------------ the tool


def workload(tmp_path: Path, cost: int = 900_000) -> list[str]:
    (tmp_path / "app.py").write_text(
        "import sys\n"
        "\n"
        "def expensive(n):\n"
        "    return sum(i * i for i in range(n))\n"
        "\n"
        "def main():\n"
        f"    total = sum(expensive({cost}) for _ in range(4))\n"
        "    print('total=%s' % total)\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    return [sys.executable, "app.py"]


@pytest.mark.timing
def test_removing_the_expensive_call_removes_most_of_the_cost(tmp_path: Path) -> None:
    """Real clock, real work, real saving. Marked `timing` because the
    baseline has to be repeatable for the comparison to mean anything, and a
    loaded host can make any short workload unrepeatable -- which would fail
    this test for a reason that has nothing to do with ablation."""
    result = ablate(
        workload(tmp_path),
        cwd=tmp_path,
        path="app.py",
        symbol="expensive",
        returns="0",
        repeats=2,
        runner=RealRunner(),
    )
    assert isinstance(result, AblationMeasurement)
    assert result.share_removed > 0.5
    assert result.output_changed
    assert result.symbol == "expensive"
    assert result.line == 3


def test_the_subject_on_disk_is_byte_identical_afterwards(tmp_path: Path) -> None:
    """The strongest form of "it cannot emit a patch": nothing it did survives."""
    command = workload(tmp_path)
    original = (tmp_path / "app.py").read_bytes()
    ablate(
        command,
        cwd=tmp_path,
        path="app.py",
        symbol="expensive",
        returns="0",
        repeats=2,
        runner=RealRunner(),
        clock=FixedClock(),
    )
    assert (tmp_path / "app.py").read_bytes() == original


def test_the_copy_is_gone_before_the_call_returns(tmp_path: Path) -> None:
    """Not at interpreter exit -- before. There is no window in which a caller
    could read the stubbed tree back out."""
    before = (
        set(Path(os.environ.get("TMPDIR", "/tmp")).glob("coldfix-ablation-*"))
        if os.name != "nt"
        else set()
    )
    ablate(
        workload(tmp_path),
        cwd=tmp_path,
        path="app.py",
        symbol="expensive",
        returns="0",
        repeats=2,
        runner=RealRunner(),
        clock=FixedClock(),
    )
    if os.name != "nt":
        after = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("coldfix-ablation-*"))
        assert after == before


def test_the_result_carries_no_field_that_could_hold_a_patch() -> None:
    """Enforcement by absence, as everywhere else. A diff has nowhere to go."""
    fields = set(AblationMeasurement.model_fields)
    assert not fields & {"diff", "patch", "stubbed_source", "workspace", "copy"}


def test_a_stub_that_breaks_the_program_is_a_named_answer_not_a_crash(tmp_path: Path) -> None:
    """It says the work is structurally required and its cost cannot be isolated
    by removing it -- which is information, not a failure of the tool."""
    (tmp_path / "app.py").write_text(
        "def rows():\n"
        "    return [i * i for i in range(1_200_000)]\n"
        "\n"
        "def main():\n"
        "    print(sum(rows()))\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    with pytest.raises(AblationBrokeTheSubjectError, match=r"cannot be separated"):
        ablate(
            [sys.executable, "app.py"],
            cwd=tmp_path,
            path="app.py",
            symbol="rows",
            returns="None",
            repeats=2,
            runner=RealRunner(),
            clock=FixedClock(),
        )


def test_output_unchanged_after_stubbing_is_itself_a_result(tmp_path: Path) -> None:
    """The work made no difference to what came out. That is a finding about the
    work, not a broken ablation, so it is reported rather than raised."""
    (tmp_path / "app.py").write_text(
        "def ignored(n):\n"
        "    return sum(i * i for i in range(n))\n"
        "\n"
        "def main():\n"
        "    ignored(2_000_000)\n"
        "    print('done')\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    result = ablate(
        [sys.executable, "app.py"],
        cwd=tmp_path,
        path="app.py",
        symbol="ignored",
        returns="0",
        repeats=2,
        runner=RealRunner(),
        clock=FixedClock(),
    )
    assert result.output_changed is False


def test_a_count_can_be_compared_across_the_two_sides(tmp_path: Path) -> None:
    result = ablate(
        workload(tmp_path),
        cwd=tmp_path,
        path="app.py",
        symbol="expensive",
        returns="0",
        repeats=2,
        runner=RealRunner(),
    )
    assert result.removed("output_bytes") is not None
    assert result.removed("measurement_id") is None

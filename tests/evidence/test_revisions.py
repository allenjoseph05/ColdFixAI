"""S-23.1 and S-23.2 — probing without browsing.

Real subprocesses on two real workspaces, because what is under test is whether
a patch that behaves differently on some input is actually caught. The usage
numbers come from an injected runner so a busy machine cannot decide the answer.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from coldfix.collect.usage import ChildResult, Usage
from coldfix.evidence.revisions import (
    Comparison,
    PatchUnderAudit,
    Side,
    broken_by,
    compare,
    probe,
)

# The patch under audit throughout: a cache keyed on the first argument. It is
# faster, it is correct for a single value, and it is wrong the moment the same
# process sees two -- which is the shape of a real optimisation gone wrong, not a
# bug somebody planted to be found.
ORIGINAL = """\
import sys

def shout(word):
    return word.upper()

print(" ".join(shout(w) for w in sys.argv[1:]))
"""

CACHED = """\
import sys

_seen = {}

def shout(word):
    if not _seen:
        _seen["only"] = word.upper()
    return _seen["only"]

print(" ".join(shout(w) for w in sys.argv[1:]))
"""


class SteadyRunner:
    """Really runs the child; reports usage the test controls."""

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
            usage=Usage(cpu_s=0.5, peak_rss_bytes=2048, read_blocks=0, write_blocks=0),
        )


class FixedClock:
    """Every run takes the same time, so nothing here turns on the machine."""

    def __init__(self) -> None:
        self.now = 0.0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls % 2 == 0:
            self.now += 1.0
        return self.now


@pytest.fixture
def under_audit(tmp_path: Path) -> PatchUnderAudit:
    baseline, patched = tmp_path / "baseline", tmp_path / "patched"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "app.py").write_text(ORIGINAL, encoding="utf-8")
    (patched / "app.py").write_text(CACHED, encoding="utf-8")
    return PatchUnderAudit(
        diff="--- a/app.py\n+++ b/app.py\n",
        test="def test_shout(): assert shout('a') == 'A'",
        baseline=baseline,
        patched=patched,
        command=(sys.executable, "app.py"),
    )


def run(given: Sequence[str], under_audit: PatchUnderAudit) -> Comparison:
    return compare(given, under_audit=under_audit, runner=SteadyRunner(), clock=FixedClock())


# ------------------------------------------------------ what the tool can find


def test_an_input_the_patch_handles_looks_identical(under_audit: PatchUnderAudit) -> None:
    """One word, and the cache is right. This is the input the author tested."""
    comparison = run(["hello"], under_audit)
    assert comparison.both_ran
    assert comparison.outputs_agree
    assert not comparison.broke


def test_the_input_the_author_did_not_think_of_is_caught(
    under_audit: PatchUnderAudit,
) -> None:
    """Two words in one process, and the cache returns the first for both. The
    patch is faster, passes its own test, and is wrong."""
    comparison = run(["hello", "world"], under_audit)
    assert comparison.both_ran
    assert not comparison.outputs_agree
    assert comparison.broke
    assert "the output changed" in comparison.why()


def test_outputs_are_compared_byte_for_byte(under_audit: PatchUnderAudit) -> None:
    """The only definition of "the same" that needs no judgement."""
    same = run(["x"], under_audit)
    assert same.baseline.output_digest == same.patched.output_digest
    different = run(["x", "y"], under_audit)
    assert different.baseline.output_digest != different.patched.output_digest


def test_both_sides_are_given_the_same_input(under_audit: PatchUnderAudit) -> None:
    """One argument, used twice. An API with two input parameters would invite a
    comparison that finds a difference every time and means nothing."""
    parameters = inspect.signature(compare).parameters
    assert [name for name, p in parameters.items() if p.kind is p.POSITIONAL_OR_KEYWORD] == [
        "given"
    ]


# ------------------------------------------------- crashes are observations


def test_a_patch_that_stops_working_is_caught_rather_than_raising(
    tmp_path: Path, under_audit: PatchUnderAudit
) -> None:
    (under_audit.patched / "app.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
    comparison = run(["hello"], under_audit)
    assert comparison.broke
    assert not comparison.patched.ran
    assert "stopped working" in comparison.why()


def test_an_input_the_original_also_rejects_has_found_nothing(under_audit: PatchUnderAudit) -> None:
    """Not the patch's fault, and the tool says so rather than counting it."""
    (under_audit.baseline / "app.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    (under_audit.patched / "app.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    comparison = run(["hello"], under_audit)
    assert not comparison.broke
    assert "the original rejects this input too" in comparison.why()


# --------------------------------------------------------------- probing


def test_every_input_is_reported_not_only_the_failures(
    under_audit: PatchUnderAudit,
) -> None:
    """Otherwise nobody can tell a patch that survived twenty inputs from one
    that survived two."""
    inputs = [["a"], ["a", "b"], ["c"], ["c", "d", "e"]]
    results = probe(inputs, under_audit=under_audit, runner=SteadyRunner(), clock=FixedClock())
    assert len(results) == 4
    assert len(broken_by(results)) == 2


def test_nothing_filters_the_inputs_a_reviewer_constructs(
    under_audit: PatchUnderAudit,
) -> None:
    """Constructing the input the patch cannot handle is the reviewer's entire
    job; a tool that sanitised them would filter out the finding.

    The last of these is a null byte, which no operating system will carry in an
    argv -- it is refused before any program sees it. That is a fact about the
    input rather than about the patch, and it arrived here by accident when a
    null was written into this file. An audit that crashed on it would end a
    review over something the reviewer is supposed to be free to try.
    """
    # Built with chr() rather than escapes: the null in particular cannot be
    # written as a literal in a source file, which is how it got into this one
    # by accident in the first place.
    for given in ([chr(0)], [chr(10)], [], [chr(45) * 200], [chr(0x1F4A9)]):
        assert isinstance(run(given, under_audit), Comparison)


# ------------------------------------------- what the Adversary is not given


def test_the_result_carries_no_source_only_what_the_program_did() -> None:
    """A tool that returned file contents would let a reviewer form its own view
    of a repository, which is what having no filesystem access is for."""
    fields = set(Side.model_fields) | set(Comparison.model_fields)
    assert not fields & {"source", "contents", "workspace", "path", "diff", "files"}


def test_passing_the_authors_reasoning_is_an_error_not_a_silent_drop(
    tmp_path: Path,
) -> None:
    """The AC's test. Pydantic drops unknown keys by default -- safe, and
    invisible, so nobody learns they tried. Forbidding them makes it an error."""
    with pytest.raises(ValidationError, match="reasoning"):
        PatchUnderAudit(
            diff="d",
            test="t",
            baseline=tmp_path,
            patched=tmp_path,
            command=("python",),
            # The type error here is the point: mypy rejects the extra field and
            # so does Pydantic. Silencing the first is what lets the test prove
            # the second.
            reasoning="I cached it because the profiler said this was hot",  # type: ignore[call-arg]
        )


def test_no_smuggling_route_is_left_open(tmp_path: Path) -> None:
    for smuggled in ("rationale", "why", "transcript", "notes", "author", "explanation"):
        with pytest.raises(ValidationError):
            PatchUnderAudit(
                diff="d",
                test="t",
                baseline=tmp_path,
                patched=tmp_path,
                command=("python",),
                **{smuggled: "the surgeon's argument"},
            )


def test_what_the_adversary_is_given_is_exactly_five_things(tmp_path: Path) -> None:
    """Named so that adding a sixth is a deliberate act somebody has to justify."""
    assert set(PatchUnderAudit.model_fields) == {
        "diff",
        "test",
        "baseline",
        "patched",
        "command",
    }


# ------------------------------------ a difference must reproduce (S-27.2)

RANDOM = "import os\nprint(os.urandom(8).hex())\n"
"""Different output on every run -- a timestamp, a uuid, an unordered set."""


def test_an_original_that_varies_on_its_own_has_found_nothing(
    under_audit: PatchUnderAudit,
) -> None:
    """ADR 181. `measure` already knew the two repeats disagreed, and the tool
    used to drop that -- so this program read as broken on every input, sending
    the Optimizer to rewrite code that was right."""
    (under_audit.baseline / "app.py").write_text(RANDOM, encoding="utf-8")
    (under_audit.patched / "app.py").write_text(RANDOM, encoding="utf-8")
    comparison = run(["hello"], under_audit)
    assert not comparison.baseline.stable
    assert comparison.unstable
    assert not comparison.broke
    assert "varied across repeats" in comparison.why()


def test_a_patch_that_makes_the_output_vary_has_broken_it(under_audit: PatchUnderAudit) -> None:
    """The other direction: the original prints the same thing twice, and the
    patched revision does not. That is a change in what the program does."""
    (under_audit.patched / "app.py").write_text(RANDOM, encoding="utf-8")
    comparison = run(["hello"], under_audit)
    assert comparison.baseline.stable
    assert not comparison.patched.stable
    assert comparison.broke
    assert "where the original's did not" in comparison.why()


def test_a_deterministic_program_is_stable_on_both_sides(under_audit: PatchUnderAudit) -> None:
    """The control: stability is measured, not assumed false."""
    comparison = run(["hello"], under_audit)
    assert comparison.baseline.stable
    assert comparison.patched.stable


def test_patched_output_that_varies_is_a_break_even_when_its_last_run_matches() -> None:
    """The digest is the last repeat's, and it can match the original's by chance.
    The variation itself is the change -- so it is judged, not the digest alone."""
    steady = Side(
        ran=True, output_digest="d", output_bytes=1, wall_s=1.0, peak_rss_bytes=1, stable=True
    )
    varying = Side(
        ran=True, output_digest="d", output_bytes=1, wall_s=1.0, peak_rss_bytes=1, stable=False
    )
    assert Comparison(given=("x",), baseline=steady, patched=varying).broke

"""S-10.2 — running the test against unpatched code, and stopping if it passes.

The gate's whole value is that it kills wasted branches cheaply, and the way to
break it is not to make it too strict but to make it too permissive in one
specific way: reading *non-zero exit* as *the test failed*. Under that rule a
script with a typo authorizes a patch.

So the exit-code protocol is verified against a **real interpreter** rather than
assumed — a vacuous script really does exit 0, a failing assertion really exits 1,
and a broken script really exits 3 — and the gate's branching is then tested
against those three codes.
"""

from __future__ import annotations

import inspect
import sys

import pytest

from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError, execute
from coldfix.repair import mustfail as mustfail_module
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import (
    BROKEN_EXIT,
    FAILED_EXIT,
    PASSED_EXIT,
    Falsified,
    MustFailError,
    NotFalsified,
    Refusal,
    read,
    run_gate,
    wrap,
)
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession

TIMEOUT = 30.0

VACUOUS = "assert True"
FAILING = "assert 1 == 2, 'the endpoint is still slow'"
BROKEN = "import a_module_that_does_not_exist"
SYNTAX_ERROR = "def (:"


def a_test(*, script: str = FAILING) -> FalsificationTest:
    return FalsificationTest(
        claim="the list endpoint stops re-rendering the author for every book",
        script=script,
        equivalence="the same books in the same order",
        cost=CostClaim(
            metric="seconds",
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
        ),
        catches=(Cheat.CACHED_STATE, Cheat.STUBBED_RESPONSE),
    )


def a_result(*, exit_code: int, stdout: str = "", stderr: str = "trace") -> ExecutionResult:
    return ExecutionResult(
        command=("python", "-c", "..."),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=1.5,
    )


class FakeSession(DiagnosticSession):
    """A diagnostic session without a container, the way `FakeDiagnosticSession`
    does it for the thesis harness. What is under test is the gate, not docker."""

    def __init__(self, result: ExecutionResult | Exception) -> None:
        self._result = result
        self.commands: list[list[str]] = []

    def run(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.commands.append(list(command))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ================= the exit-code protocol, against a real interpreter


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (VACUOUS, PASSED_EXIT),
        (FAILING, FAILED_EXIT),
        (BROKEN, BROKEN_EXIT),
        (SYNTAX_ERROR, BROKEN_EXIT),
    ],
)
def test_the_wrapper_separates_an_assertion_from_everything_else(
    script: str, expected: int
) -> None:
    """**Run for real, because the protocol is the story.** A wrapper that
    reported the wrong code would make every test below assert the wrong thing
    confidently, which is the failure this project keeps recording about fixtures
    that cannot discriminate.

    A syntax error is included deliberately: it fails at `compile`, not at `exec`,
    and a wrapper that only guarded the `exec` would let it escape as a crash.
    """
    result = execute([sys.executable, "-c", wrap(script)], timeout=TIMEOUT)
    assert result.exit_code == expected


def test_a_failing_assertion_carries_its_message_out() -> None:
    """The evidence a `Falsified` is built from. A gate that reported *it failed*
    with nothing under it would be an assertion by whoever wanted it to fail."""
    result = execute([sys.executable, "-c", wrap(FAILING)], timeout=TIMEOUT)

    assert "the endpoint is still slow" in result.stderr
    assert "falsification_test" in result.stderr


def test_the_script_is_compiled_under_its_own_name_not_as_a_string() -> None:
    """A traceback naming `<string>` tells a reader nothing about where to look."""
    assert "'falsification_test'" in wrap(VACUOUS)


def test_a_script_containing_quotes_survives_being_embedded() -> None:
    """`repr` rather than string interpolation, so quoting is the interpreter's
    problem. A script asserting on a message with an apostrophe is ordinary."""
    tricky = "assert 0, \"it's still slow\" + '''x'''"
    result = execute([sys.executable, "-c", wrap(tricky)], timeout=TIMEOUT)

    assert result.exit_code == FAILED_EXIT
    assert "it's still slow" in result.stderr


# ======================= AC 1: it runs against unpatched code


def test_the_gate_takes_a_diagnostic_session_and_nothing_else() -> None:
    """**"Unpatched" is a property of the type.** S-2.3 built `DiagnosticSession`
    with `apply_patch` and `diff` deliberately absent, so a patch has no route
    into the worktree this runs against. A gate accepting any session would be
    checking a claim about what the caller happened to check out."""
    annotation = inspect.signature(run_gate).parameters["session"].annotation
    assert annotation == "DiagnosticSession"

    assert not hasattr(DiagnosticSession, "apply_patch")
    assert not hasattr(DiagnosticSession, "diff")
    # The control: the other mode has both, which is what makes the absence mean
    # something rather than being a class nobody finished.
    assert hasattr(CandidateSession, "apply_patch")
    assert hasattr(CandidateSession, "diff")


def test_the_script_is_never_written_into_the_subjects_tree() -> None:
    """S-2.4 refuses a patch touching a test, so a falsification script
    materialised as a file would be a test the patch must not touch and which
    every later diff would show. It travels on the command line instead."""
    session = FakeSession(a_result(exit_code=FAILED_EXIT))
    test = a_test()
    run_gate(test, session, interpreter="python")

    command = session.commands[0]
    assert command[:2] == ["python", "-c"]
    assert FAILING in command[2]


def test_the_gate_runs_the_wrapped_program_and_not_the_bare_script() -> None:
    """**The survivor of the sabotage pass.** Dropping `wrap` left the raw script
    running, and nothing failed: the fake session returns a canned exit code
    whatever it is handed, and the test above only checked the script was *in*
    the command — which it is, either way.

    Without the wrapper there is no protocol at all: the bare script exits 1 for
    an assertion **and** 1 for a syntax error, so every broken test would read as
    a falsification.
    """
    session = FakeSession(a_result(exit_code=FAILED_EXIT))
    test = a_test()
    run_gate(test, session)

    assert session.commands[0][2] == wrap(test.script)
    assert session.commands[0][2] != test.script


def test_nothing_here_can_write_a_patch() -> None:
    parameters = {
        name
        for _, function in inspect.getmembers(mustfail_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"diff", "patch", "worktree"}

    imported = set(vars(mustfail_module))
    assert not imported & {"apply_patch", "CandidateSession", "Workbench"}


# ============= AC 2: if it passes, the story stops with a report


def test_a_test_that_passes_on_unpatched_code_stops_the_repair() -> None:
    """§5.3: *a test that passes before you change anything is testing nothing.*"""
    outcome = read(a_test(), a_result(exit_code=PASSED_EXIT, stderr=""))

    assert isinstance(outcome, NotFalsified)
    assert outcome.reason is Refusal.PASSED_UNPATCHED
    assert "no patch will be written" in outcome.report()
    assert "do not write a patch" in outcome.report()


def test_a_failing_test_authorizes_the_patch_and_carries_its_evidence() -> None:
    """**The control.** A gate that refused everything satisfies every assertion
    above while making the repair phase unreachable."""
    outcome = read(a_test(), a_result(exit_code=FAILED_EXIT, stderr="AssertionError: slow"))

    assert isinstance(outcome, Falsified)
    assert "AssertionError: slow" in outcome.evidence
    assert "failed on unpatched code" in outcome.describe()


def test_the_two_outcomes_are_exclusive_by_construction() -> None:
    """S-7.1's `Fingerprint | Unsupported`: a caller cannot proceed without having
    branched, because there is no third thing the return value could be."""
    falsified = read(a_test(), a_result(exit_code=FAILED_EXIT))
    stopped = read(a_test(), a_result(exit_code=PASSED_EXIT))

    assert isinstance(falsified, Falsified)
    assert isinstance(stopped, NotFalsified)
    assert not isinstance(falsified, NotFalsified)


def test_a_falsification_with_no_evidence_is_refused() -> None:
    """S-2.7's construction. The run's own output is what makes the claim
    checkable; without it, *the test failed* is an assertion by whoever wanted it
    to have failed."""
    with pytest.raises(MustFailError, match="no evidence"):
        Falsified(test=a_test(), evidence="   ", wall_seconds=1.0)


# ============ the third outcome: a broken script is not a falsification


def test_a_broken_script_does_not_authorize_a_patch() -> None:
    """**The gate inverted, and the reason there are three outcomes.** A script
    with a bad import exits non-zero, and an implementation reading *non-zero
    means the test failed* would let a typo authorize patch generation."""
    outcome = read(a_test(script=BROKEN), a_result(exit_code=BROKEN_EXIT))

    assert isinstance(outcome, NotFalsified)
    assert outcome.reason is Refusal.SCRIPT_ERRORED
    assert not outcome.vacuous


def test_an_errored_script_is_not_reported_as_a_test_that_passed() -> None:
    """Each refusal needs a different fix: a passing test is a branch to abandon,
    an errored one is a script to repair. Sending the second back as the first
    would have the Surgeon rewriting a correct test because of a typo."""
    errored = read(a_test(), a_result(exit_code=BROKEN_EXIT)).reason  # type: ignore[union-attr]
    passed = read(a_test(), a_result(exit_code=PASSED_EXIT)).reason  # type: ignore[union-attr]

    assert errored is not passed
    assert errored.remedy != passed.remedy
    assert "about the test" in errored.remedy


@pytest.mark.parametrize("exit_code", [2, 3, 127, 137])
def test_any_exit_code_that_is_not_the_assertion_code_refuses(exit_code: int) -> None:
    """Only `FAILED_EXIT` authorizes. 137 is a container killed by the memory cap,
    127 is a missing interpreter — neither is evidence about the subject."""
    outcome = read(a_test(), a_result(exit_code=exit_code))

    assert isinstance(outcome, NotFalsified)
    assert outcome.exit_code == exit_code


def test_a_timeout_is_not_a_failure() -> None:
    """A killed run proves nothing in either direction, and reading it as *the
    test failed* would authorize a patch on the strength of a script that hung."""
    session = FakeSession(ExecutionTimeoutError(("python", "-c", "..."), 30.0, "", ""))
    outcome = run_gate(a_test(), session)

    assert isinstance(outcome, NotFalsified)
    assert outcome.reason is Refusal.TIMED_OUT
    assert "proves nothing" in outcome.report()


# ================================ AC 3: the gate fires on a vacuous test


def test_the_gate_fires_on_a_vacuous_falsification_test_end_to_end() -> None:
    """**AC 3, run for real.** `assert True` is the cheapest weak test there is,
    and it is what a Surgeon writes when it wants an easy life. The whole path —
    wrapper, interpreter, exit code, verdict — is exercised."""
    result = execute([sys.executable, "-c", wrap(VACUOUS)], timeout=TIMEOUT)
    outcome = read(a_test(script=VACUOUS), result)

    assert isinstance(outcome, NotFalsified)
    assert outcome.vacuous
    assert outcome.reason is Refusal.PASSED_UNPATCHED


def test_a_test_that_asserts_nothing_at_all_is_also_vacuous() -> None:
    """A script with no assertion in it exits 0 just as `assert True` does, and
    the gate cannot tell them apart — nor does it need to. Both assert nothing
    the unpatched code does not already satisfy."""
    result = execute([sys.executable, "-c", wrap("x = 1 + 1")], timeout=TIMEOUT)
    outcome = read(a_test(script="x = 1 + 1"), result)

    assert isinstance(outcome, NotFalsified)
    assert outcome.vacuous


def test_a_real_failing_test_passes_the_gate_end_to_end() -> None:
    """The control for AC 3. Without it, a gate hardcoded to refuse would satisfy
    every vacuity test in this file."""
    result = execute([sys.executable, "-c", wrap(FAILING)], timeout=TIMEOUT)
    outcome = read(a_test(), result)

    assert isinstance(outcome, Falsified)
    assert "the endpoint is still slow" in outcome.evidence


def test_the_report_names_what_the_test_claimed_and_what_it_watched_for() -> None:
    """A human reading a stopped repair needs to know which test was abandoned,
    or the next Surgeon writes the same one."""
    report = read(a_test(script=VACUOUS), a_result(exit_code=PASSED_EXIT)).report()  # type: ignore[union-attr]

    assert "re-rendering the author" in report
    assert "cached_state" in report

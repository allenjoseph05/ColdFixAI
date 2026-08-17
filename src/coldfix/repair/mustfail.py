"""Running the test against unpatched code, and refusing to go on if it passes.

Epic 10, S-10.2. *The test runs against unpatched code before any patch is
written. If it passes, the story stops with a report — no patch is written. A
test proves this gate fires on a vacuous falsification test.*

`03-agents.md` §5.3: *a test that passes before you change anything is testing
nothing. This gate costs one script and kills entire wasted branches.*

**"Unpatched" is a property of the type, not a promise from the caller.** The
gate takes a `DiagnosticSession` and nothing else. S-2.3 built that class with
`apply_patch` and `diff` deliberately absent — *every operation that could carry a
change out of this session is absent rather than guarded* — so a patch has **no
route into** a diagnostic worktree. A gate that accepted any session would be
checking a claim about which revision the caller happened to check out, which is
the sort of criterion that reads as met and is satisfied by convention.

**Three outcomes, and the third is the one that matters.** The obvious
implementation reads *non-zero exit* as *the test failed* and lets the story
proceed. But a script with a syntax error, a bad import or a missing fixture also
exits non-zero — and under that rule **a broken test authorizes patch
generation**, which is the gate inverted. So the script runs under a wrapper that
separates an `AssertionError` from everything else, and only the first is a
falsification.

That is S-3.1's *no* against *not known* at the top of the repair phase, and the
same distinction S-9.6 drew for a metric that vanished: silence, an error and a
negative result are three things, and two of them are not evidence.

**The script is never written into the subject's tree.** It travels on the
command line. S-2.4 refuses a patch that touches a test, so a falsification
script materialised as a file in the repository would be a test the patch must
not touch and which every subsequent diff would show. `03-agents.md` §5.2 lists a
`write_test(script)` tool; this is that tool's effect without the file.

**Nothing here writes a patch, and there is nowhere to put one.** The gate's
positive outcome is a `Falsified` — the artifact S-10.4 will require, and one
that refuses to represent a passing run, which is S-2.7's `VerifiedReset`
construction: a type whose constructor will not describe a failure as a success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError
from coldfix.repair.falsification import FalsificationTest
from coldfix.sandbox.modes import DiagnosticSession

PASSED_EXIT = 0
"""Nothing in the script raised. On unpatched code that is the gate's whole
subject: the test asserts nothing that is not already true."""

FAILED_EXIT = 1
"""An `AssertionError` reached the wrapper. **This is the only exit code that
authorizes a patch.**"""

BROKEN_EXIT = 3
"""Anything else the script raised. Three rather than two, because a script that
cannot run has not established that the code is slow — it has established that
the script is wrong."""

DEFAULT_TIMEOUT_SECONDS = 300.0
"""Long enough for a workload the falsification test has to drive at scale, and
finite for S-1.1's reason: a subprocess with no deadline can hang an entire
investigation with no diagnostic."""


class MustFailError(Exception):
    """The gate could not be applied."""


class Refusal(StrEnum):
    """Why the gate did not authorize a patch. Three, and each needs a different fix.

    Collapsing them into *the gate did not pass* would lose the distinction the
    gate exists for: a test that passed is a wasted branch to abandon, and a test
    that errored is a script to repair. Sending the second back as the first
    would have the Surgeon rewriting a correct test because a typo in it looked
    like the code already being fast.
    """

    PASSED_UNPATCHED = (
        "the test passed against unpatched code, so it asserts nothing that is not already true"
    )
    SCRIPT_ERRORED = (
        "the script raised something other than an assertion, so it never ran as a test"
    )
    TIMED_OUT = "the script did not finish inside its timeout, so nothing was established"

    @property
    def remedy(self) -> str:
        return _REMEDY[self]


_REMEDY: dict[Refusal, str] = {
    Refusal.PASSED_UNPATCHED: (
        "stop. `03-agents.md` §5.3: do not write a patch. Either the finding does not "
        "reproduce under this test's conditions, or the test measures something the "
        "cost is not in — and either way a patch written now would be verified by a "
        "test that cannot tell whether it worked"
    ),
    Refusal.SCRIPT_ERRORED: (
        "repair the script and re-run the gate. This is not evidence about the subject: "
        "the traceback below is about the test"
    ),
    Refusal.TIMED_OUT: (
        "raise the timeout if the workload is genuinely this slow, or reduce the scale "
        "the script drives. A run that was killed proves nothing in either direction"
    ),
}


@dataclass(frozen=True)
class Falsified:
    """Proof that the test failed on unpatched code. **S-10.4 requires one.**

    S-2.7's construction: a type whose constructor refuses to describe a failure
    as a success. Somebody who builds one by hand still has to supply the exit
    code of a run that actually failed, and the evidence travels with it.
    """

    test: FalsificationTest
    evidence: str
    wall_seconds: float

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            message = (
                "a falsification with no evidence is an assertion that the test failed, made "
                "by whoever wanted it to have failed. The run's own output is what makes it "
                "checkable"
            )
            raise MustFailError(message)

    def describe(self) -> str:
        return (
            f"MUST-FAIL GATE PASSED — the test failed on unpatched code in "
            f"{self.wall_seconds:.2f}s, which is what makes it worth running again after a "
            f"patch.\n  Claim: {self.test.claim}\n  {self.test.cost.describe()}"
        )


@dataclass(frozen=True)
class NotFalsified:
    """The gate stopped the story. AC 2, and the report it stops with."""

    test: FalsificationTest
    reason: Refusal
    evidence: str
    exit_code: int

    @property
    def vacuous(self) -> bool:
        """AC 3's subject: a test the unpatched code already satisfies."""
        return self.reason is Refusal.PASSED_UNPATCHED

    def report(self) -> str:
        designed = ", ".join(item.name.lower() for item in self.test.catches)
        lines = [
            "MUST-FAIL GATE STOPPED THIS REPAIR — no patch will be written.",
            f"  Because: {self.reason.value}",
            f"  What to do: {self.reason.remedy}",
            f"  Claim the test made: {self.test.claim}",
            f"  It was designed to catch: {designed}",
        ]
        if self.evidence.strip():
            lines.append(f"  The run said:\n    {self.evidence.strip()}")
        return "\n".join(lines)


def wrap(script: str) -> str:
    """The program that runs `script` and reports which of three things happened.

    **The exit codes are the whole protocol.** A caller reading *non-zero means
    the test failed* would let a script with an import error authorize a patch,
    so an `AssertionError` is separated from every other exception here rather
    than guessed at from a traceback afterwards.

    The script is embedded with `repr` and compiled under its own filename, so a
    traceback names `falsification_test` rather than `<string>` and quoting is
    the interpreter's problem rather than this function's.

    **`compile` is inside the guarded block, and a test proved it has to be.**
    With it outside, a script with a syntax error raised `SyntaxError` before the
    `try` and the interpreter exited **1** — which is `FAILED_EXIT`, so a
    malformed script read as a falsification and would have authorized a patch.
    That is the gate inverted by two lines of indentation, and the failure this
    module's whole three-outcome design exists to prevent.
    """
    return (
        "import sys, traceback\n"
        f"_script = {script!r}\n"
        "try:\n"
        "    _code = compile(_script, 'falsification_test', 'exec')\n"
        "    exec(_code, {'__name__': '__main__'})\n"
        "except AssertionError:\n"
        "    traceback.print_exc()\n"
        f"    sys.exit({FAILED_EXIT})\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        f"    sys.exit({BROKEN_EXIT})\n"
        f"sys.exit({PASSED_EXIT})\n"
    )


def read(test: FalsificationTest, result: ExecutionResult) -> Falsified | NotFalsified:
    """Turn one run into the gate's answer. Pure, so the protocol is testable.

    Separated from `run_gate` because the branching is the part worth attacking
    and a function that also needs a container is one a sabotage pass cannot
    reach cheaply.
    """
    evidence = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)

    if result.exit_code == FAILED_EXIT:
        return Falsified(test=test, evidence=evidence, wall_seconds=result.wall_seconds)

    reason = Refusal.PASSED_UNPATCHED if result.exit_code == PASSED_EXIT else Refusal.SCRIPT_ERRORED
    return NotFalsified(test=test, reason=reason, evidence=evidence, exit_code=result.exit_code)


def run_gate(
    test: FalsificationTest,
    session: DiagnosticSession,
    *,
    interpreter: str = "python",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Falsified | NotFalsified:
    """Run the falsification test against unpatched code. AC 1 and AC 2.

    **The session type is the enforcement.** A `DiagnosticSession` has no
    `apply_patch` and no `diff` (S-2.3), so there is no route by which a change
    could be in the worktree this runs against. *Unpatched* is therefore a fact
    about the type rather than a claim about the caller's discipline.

    Returns `Falsified` — which S-10.4 requires — or `NotFalsified`, which
    carries the report AC 2 says the story stops with. The two are exclusive by
    construction, so a caller cannot proceed without having branched, which is
    S-7.1's `Fingerprint | Unsupported` construction.

    **A timeout is not a failure.** A killed run proves nothing in either
    direction, and reading it as *the test failed* would authorize a patch on the
    strength of a script that hung.

    Raises:
        SessionClosedError: the worktree is gone, so there is nothing to run in.
    """
    program = wrap(test.script)
    try:
        result = session.run([interpreter, "-c", program], timeout=timeout)
    except ExecutionTimeoutError as timed_out:
        return NotFalsified(
            test=test,
            reason=Refusal.TIMED_OUT,
            evidence=str(timed_out),
            exit_code=BROKEN_EXIT,
        )
    return read(test, result)

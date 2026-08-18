"""A confirmed finding in, a verified patch out. **The path Epic 10 did not have.**

Epic 10 composed. Six stories — a test, a gate, an audit of the test, a
slack-reducing classifier, a patch, a retry discipline — and after all of them
the epic could not perform its own sentence: *fix the confirmed finding, test
first.*

**The test was never run against the patched code.** S-10.2 runs it against
*unpatched* code and requires it to **fail**; that is the whole of its job, and
its signature takes a `DiagnosticSession` precisely so a patch cannot be there.
Nothing ran it afterwards. So the epic wrote a test, proved it failed, audited
it, strengthened it, generated a patch that had to stay inside the evidence, gated
that patch for slack — and never asked whether the patch made the test pass.
Epic 11 does not cover it either: that epic *attacks* the patch, and §5.2 gives
the Surgeon its own `run_test(script, on_ref)`.

**The same three exit codes mean different things on the two sides, and reading
them alike blames the test for the patch's regression.** On unpatched code a
script that raises something other than an assertion is a **broken script** —
S-10.2's third outcome, and its remedy is *repair the script*. On patched code
the same script has already run cleanly once, so an error now means **the patch
broke something the test depends on**: a method it called, a field it read. That
is a correctness failure of the patch, and sending it back as *fix your test*
would have the Surgeon rewriting a test that was right.

**A strengthened test has to be re-gated, and only the composed path can do it.**
S-10.3 returns a `FalsificationTest`, never a `Falsified`, exactly so that the
replacement must go back through S-10.2 — and until this module existed, nothing
made that second trip. A stronger test the unpatched code already passes is as
useless as a weak one.

**Two Surgeon prompts, and nothing noticed the wrong session.** S-10.1 and S-10.4
have different `_SYSTEM` text, and `Session` caches one assembled prompt per
model built from *its* system string while each `generate` sends *its own* to the
client. A caller reusing one Surgeon session for both bills and caches against a
prefix that is not what was sent. S-9.1 closed this for the audit with
`refuse_shared_session`; nothing had closed it for the Surgeon, and the fix
belongs in each `generate` rather than here, because a check only at the join is
a check something can be routed around.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.execute import ExecutionTimeoutError
from coldfix.cost.accounting import ModelCall
from coldfix.cost.budget import BudgetExhaustedError, ProgressStalledError
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.llm.client import ModelClient
from coldfix.primitives.faults import Amplification
from coldfix.repair import falsification, retry, slack, testaudit
from coldfix.repair import patch as patch_module
from coldfix.repair.falsification import FalsificationTest
from coldfix.repair.mustfail import (
    DEFAULT_TIMEOUT_SECONDS,
    FAILED_EXIT,
    PASSED_EXIT,
    Falsified,
    NotFalsified,
    run_gate,
    wrap,
)
from coldfix.repair.patch import Attempt, Patch
from coldfix.repair.retry import Escalation
from coldfix.repair.slack import Classification
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession


class RepairError(Exception):
    """The repair could not be carried out."""


class Outcome(StrEnum):
    """What running the falsification test against the **patched** code showed.

    Three, and the third is why this is not a boolean. S-10.2 needed three on the
    unpatched side for the same reason and the middle one swaps meaning between
    them: there, an error means the script is wrong; here, the script has already
    run cleanly once, so an error means the patch broke what it depended on.
    """

    VERIFIED = "the test passed against the patched code"
    STILL_FAILING = "the test still fails, so the cost is still there"
    PATCH_BROKE_THE_TEST = (
        "the script errored against the patched code after running cleanly against the "
        "original, so the patch removed something the test depends on"
    )

    @property
    def worked(self) -> bool:
        return self is Outcome.VERIFIED

    @property
    def failure(self) -> str:
        """What to hand the next attempt. AC 3 of S-10.5, from this side."""
        return self.value


def verify(
    test: FalsificationTest,
    session: CandidateSession,
    *,
    interpreter: str = "python",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Outcome:
    """Run the falsification test against the patched code. **The missing step.**

    The mirror of S-10.2's gate: same wrapper, same three exit codes, opposite
    requirement. Reusing `wrap` rather than re-deriving the protocol matters —
    two encodings of *which exit code means an assertion failed* would be two
    answers to a question with one right one, in the two places that have to
    agree.

    Takes a `CandidateSession` because that is where the patch is, which is the
    exact inverse of `run_gate`'s `DiagnosticSession`. Between them the pair says
    the whole rule: the test is proved to fail where a patch cannot exist, and
    proved to pass where it does.
    """
    try:
        result = session.run([interpreter, "-c", wrap(test.script)], timeout=timeout)
    except ExecutionTimeoutError:
        return Outcome.PATCH_BROKE_THE_TEST

    if result.exit_code == PASSED_EXIT:
        return Outcome.VERIFIED
    if result.exit_code == FAILED_EXIT:
        return Outcome.STILL_FAILING
    return Outcome.PATCH_BROKE_THE_TEST


def gate_and_audit(  # noqa: PLR0913 - two sessions, the client, the chain, the
    # diagnostic worktree and the two measured token counts are seven different
    # facts, and none is derivable from the others.
    surgeon: Session,
    auditor: Session,
    client: ModelClient,
    *,
    chain: EvidenceChain,
    diagnostic: DiagnosticSession,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> tuple[Falsified, testaudit.TestAudit] | NotFalsified:
    """Write the test, prove it fails, attack it, and prove the replacement fails too.

    **The second trip through the gate is the join.** S-10.3 hands back a
    `FalsificationTest` rather than a `Falsified` so the strengthened test cannot
    reach a patch without being re-run — and nothing performed that second run
    until this function existed.

    Returns the proof and the audit, or the `NotFalsified` that stops the story.
    """
    written = falsification.generate(
        surgeon,
        client,
        chain=chain,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )

    first = run_gate(written.value, diagnostic)
    if isinstance(first, NotFalsified):
        return first

    audited = testaudit.audit_test(
        auditor,
        client,
        test=first.test,
        chain=chain,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    if audited.value.sound:
        return first, audited.value

    regated = run_gate(audited.value.forward, diagnostic)
    if isinstance(regated, NotFalsified):
        return regated
    return regated, audited.value


@dataclass(frozen=True)
class Repaired:
    """A patch that made its own test pass, and what it costs to ship."""

    patch: Patch
    classification: Classification
    attempts: tuple[Attempt, ...]
    """Everything tried, including this one. A reader asking *why this approach*
    needs the ones that did not work."""

    @property
    def needs_human_review(self) -> bool:
        """S-10.6's label, read as the question a caller actually asks."""
        return self.classification.slack_reducing

    def describe(self) -> str:
        lines = [
            f"REPAIRED on attempt {len(self.attempts)} — the falsification test passes.",
            f"  {self.patch.describe()}",
        ]
        if self.needs_human_review:
            lines.append(f"  {self.classification.describe()}")
        return "\n".join(lines)


def repair(  # noqa: PLR0913 - the two sessions, the client, the chain, the two
    # worktrees and the two measured token counts are eight different facts and
    # none is derivable from the others. Bundling them would invent a type whose
    # only purpose is to be unpacked here.
    surgeon: Session,
    auditor: Session,
    client: ModelClient,
    *,
    chain: EvidenceChain,
    falsified: Falsified,
    candidate: CandidateSession,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    amplification: Amplification | None = None,
    finding_id: str | None = None,
) -> Repaired | Escalation:
    """Write patches until one makes the test pass, or until the attempts run out.

    **The whole of Epic 10's repair half, performed once.** Each attempt is
    authorized against S-5.4's three-attempt cap *before* it spends, checked
    against S-10.5's structural repeat rule *before* any gate runs, classified by
    S-10.6 for slack, applied through S-2.4's protected-path filter, and then —
    the step that did not exist — run against the patched code.

    `falsified` comes from `gate_and_audit`, and carries the test that survived
    the audit. Passing the pre-audit one would verify against a test the
    Adversary said a cheat could pass.

    **Two ways this ends without a patch, and the composition found that the
    second was unreachable as written.** `Phase.REPAIR`'s cap is three attempts
    and `Budget`'s `stall_after` defaults to **three**, so three attempts failing
    the same way raise `ProgressStalledError` on the third — *before* the cap's
    `BudgetExhaustedError` can fire on the fourth. A loop catching only exhaustion
    lets the stall escape as an unhandled exception, which is not an escalation
    and carries no history.

    Both end this repair and both escalate, and §7.2 gives this phase `ESCALATE`
    either way. They are caught separately so the report can say which, because
    *tried three things, none worked* and *tried three things and got the same
    answer each time* send a reader somewhere different.
    """
    attempts: list[Attempt] = []

    while True:
        try:
            retry.authorize_attempt(surgeon.budget, finding_id)
        except BudgetExhaustedError:
            return retry.escalate(attempts, finding_id)

        written = patch_module.generate(
            surgeon,
            client,
            chain=chain,
            falsified=falsified,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
            prior=attempts,
            temperature=retry.temperature_for(len(attempts) + 1),
            finding_id=finding_id,
        )
        candidate_patch = written.value

        repetition = retry.repeats(candidate_patch, attempts)
        if repetition is not None:
            attempt = Attempt(patch=candidate_patch, failure=repetition.describe())
            attempts.append(attempt)
            try:
                retry.record_attempt(surgeon.budget, attempt, finding_id)
            except ProgressStalledError:
                return retry.escalate(attempts, finding_id)
            continue

        classification = slack.classify(candidate_patch.diff, amplification=amplification)
        patch_module.apply(candidate_patch, chain, candidate)
        outcome = verify(falsified.test, candidate)

        attempt = Attempt(patch=candidate_patch, failure=outcome.failure)
        attempts.append(attempt)
        try:
            retry.record_attempt(surgeon.budget, attempt, finding_id)
        except ProgressStalledError:
            return retry.escalate(attempts, finding_id)

        if outcome.worked:
            return Repaired(
                patch=candidate_patch,
                classification=classification,
                attempts=tuple(attempts),
            )


def calls_of(*outcomes: Sequence[ModelCall]) -> int:
    """Every model call a repair made, for a caller checking what it spent."""
    return sum(len(item) for item in outcomes)

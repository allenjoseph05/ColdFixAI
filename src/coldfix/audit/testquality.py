"""Attacking the test again, this time with the change in front of you.

Epic 11, S-11.6. *Asks whether a cheat could pass the Surgeon's falsification
test. If yes, writes the test that would catch it. Strengthened test becomes the
permanent regression test.*

**AC 1 and AC 2 are S-10.3, and this module does not write them a second time.**
`08-audit.md` §3.3 found the flaw this story was written against — *the test is
written by the agent that then writes the patch* — and its fix was to move the
audit **earlier**: *the falsification test is submitted and audited before the
patch is written.* S-10.3 built that, with no `patch` parameter so the ordering
could not be got wrong. The backlog's wording here predates that correction, and
`CLAUDE.md` says the audit wins.

So `Weakness`, `check_stronger` and the reply parser are imported from S-10.3.
Two implementations of *is this replacement actually stronger* would be two
answers where the whole point is that there is one.

**What this story owns is the evidence and the artifact.**

*The evidence*: S-10.3 asks **could some change** slip through this test. This
asks **did this one**, because by now the diff exists and the Adversary is holding
it. A hole nobody could name in the abstract is often obvious once you can see
what the patch actually did — a threshold that looks defensible until you notice
the change only moves the metric on the first call. The two questions are close
enough to share a parser and far enough apart that only the second can be
answered against a `Candidate`.

*The artifact*: **AC 3, and it exists nowhere else.** S-10.3's `forward` is the
test the Surgeon must satisfy *in this repair*; it is consumed and forgotten. A
permanent regression test outlives the repair, ships with the patch, and runs
against every later change — so the bar for creating one is higher, and
`RegressionTest` cannot be constructed without both proofs.

**Both proofs, and the second one is the trap.** A regression test has to have
failed on the unpatched code — otherwise it is not about this bug — and passed on
the patched code, or it cannot be shipped green. The trap is that the `Falsified`
lying around at this point is usually a proof about the **Surgeon's original
test**, and the artifact being shipped is the **Adversary's strengthened** one.
Attaching the first to the second is S-10.3's *a strengthened test is not trusted,
it is re-gated*, failed at the last possible moment, and the result is a
regression test nobody has ever seen fail. The constructor compares them.

**Isolation is S-11.1's, unchanged.** The subject is a `Candidate`, which has
nowhere to put `rationale` or `approach`, and the session is `audit_session` with
this story's system text — the fourth audit to share that constructor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    AuditError,
    audit_messages,
    audit_session,
    refuse_shared_session,
)
from coldfix.audit.patchaudit import Candidate
from coldfix.cost.accounting import Agent, ExchangeRate, Phase, TokenUsage
from coldfix.cost.context import Block
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.llm.client import ModelClient
from coldfix.repair.compose import Outcome
from coldfix.repair.falsification import Cheat, FalsificationTest, catalogue
from coldfix.repair.mustfail import Falsified
from coldfix.repair.testaudit import TestAudit, Weakness, parse, render_test

MAX_OUTPUT_TOKENS = 4_000
"""A reply may carry a whole replacement script, as S-10.3's may."""

RESIDUE = (
    "This is the second time the test has been attacked and the classes are the same "
    "five both times, so a hole outside that vocabulary is outside both audits. The "
    "new information here is the diff, not a longer list of cheats — if seeing the "
    "change does not suggest a hole that seeing the evidence alone did not, this round "
    "found nothing and cost a call, which is exactly what S-11.8's ablation is for."
)


class TestQualityError(AuditError):
    """No usable test-quality audit came back."""

    __test__ = False
    """Not a pytest suite. pytest collects on the `Test` prefix alone, and the
    subject here is a test — S-10.3's note, and it applies to every name in this
    module that starts with the word."""


SYSTEM = """\
You are attacking a test, and this time you can see the change it passed.

Somebody was told a specific cost had been measured somewhere specific. They \
wrote a test that would decide whether their change was real, and then they wrote \
the change. Both are in front of you. You are not given anything they said about \
either.

Your question is about the test, not the change: **could a change that does not \
genuinely fix anything still pass this test?**

The difference from asking that in the abstract is the diff. Read what was \
actually done, then ask what the test would have failed to notice. A threshold \
that looked reasonable can turn out to be satisfied by the first call alone. A \
guard on the wrong metric can turn out to guard nothing this change could \
affect.

If a cheat would pass, write the test that would catch it. Objecting without \
that is asking somebody else to solve the problem you found.

If nothing you can think of would pass, say so plainly. That is a result, and it \
is the answer this round should usually reach."""


QUESTION = f"""\
Could a change that does not genuinely fix anything still pass this test?

Answer with a single JSON object and nothing else:

{{"weaknesses": [{{"cheat": "...", "how": "..."}}],
 "strengthened": {{"claim": "...", "script": "...", "equivalence": "...",
   "cost": {{"metric": "...", "baseline": number, "at_most": number,
             "guards": [{{"metric": "...", "baseline": number, "at_most": number}}]}},
   "catches": ["...", "..."]}}}}

`cheat` is one of: {", ".join(name for name, _ in catalogue())}
`how` says concretely what such a change would do and why this test would not \
notice. Point at the diff where you can: an objection that would read the same \
without having seen the change is one the earlier audit already had the chance to \
make.

`strengthened` is required whenever `weaknesses` is non-empty, and must be a \
complete replacement test that would catch every cheat you named. Its `catches` \
must include them. Its baselines must be the same measured figures, and its cost \
threshold may not be higher than the original's.

If no cheat you can think of would pass, answer exactly \
{{"weaknesses": []}} — that is a result, not a failure."""


@dataclass(frozen=True)
class RegressionTest:
    """**AC 3.** A test that outlives the repair, and needs both proofs to exist.

    S-10.3's `forward` is the test the Surgeon must satisfy *in this repair* — it
    is consumed and forgotten. This one ships with the patch and runs against
    every later change, so the bar is higher: it must be shown to fail where the
    bug is and pass where the fix is, and neither is assumed.
    """

    __test__ = False

    test: FalsificationTest
    closes: tuple[Cheat, ...]
    """The cheat classes this test was strengthened to catch. Empty where the
    original was already sound — a regression test is still worth keeping when
    nobody found a hole in it."""

    proof_of_failure: Falsified
    """S-10.2's artifact: this test, run against code with no patch in it, failing.
    Without it the test is not about this bug."""

    verified: Outcome
    """S-10.6's answer from the patched worktree. Anything but `VERIFIED` and the
    test cannot be shipped green."""

    finding_id: str | None = None

    def __post_init__(self) -> None:
        if self.verified is not Outcome.VERIFIED:
            message = (
                f"this test {self.verified.value}, so it cannot ship as a regression test. A "
                "permanent test that does not pass on the code it ships with is a build "
                "broken on arrival"
            )
            raise TestQualityError(message)

        # **The proof is usually about the wrong test, and that is the whole
        # check.** What is lying around at this point is S-10.2's gate result for
        # the *Surgeon's original* test; the artifact being shipped is the
        # *Adversary's strengthened* one. S-10.3's rule is that a strengthened
        # test is re-gated rather than trusted, and attaching the old proof to the
        # new test is that rule failed at the last possible moment — the result
        # is a permanent regression test nobody has ever seen fail.
        if self.proof_of_failure.test != self.test:
            message = (
                "the proof of failure is about a different test from the one being shipped. A "
                "strengthened test is re-gated, not trusted (S-10.3): carrying the original's "
                "proof forward would make this a regression test nobody has ever watched fail"
            )
            raise TestQualityError(message)

        uncovered = sorted(item.name.lower() for item in set(self.closes) - set(self.test.catches))
        if uncovered:
            message = (
                f"this test is said to close {uncovered} and does not claim to catch them. The "
                "hole and the fix disagree, which is `check_stronger`'s third refusal arriving "
                "at the permanent artifact"
            )
            raise TestQualityError(message)

    @property
    def strengthened(self) -> bool:
        """Whether this test exists because an audit found a hole."""
        return bool(self.closes)

    def describe(self) -> str:
        origin = (
            f"strengthened to close {', '.join(item.name.lower() for item in self.closes)}"
            if self.strengthened
            else "kept as written; no audit found a hole in it"
        )
        return "\n".join(
            [
                f"PERMANENT REGRESSION TEST — {origin}.",
                f"  {self.test.claim}",
                f"  {self.test.cost.describe()}",
                f"  proved to fail without the patch: {self.proof_of_failure.wall_seconds:.2f}s",
                f"  proved to pass with it: {self.verified.value}",
                "  It ships with the patch and runs against every later change.",
            ]
        )


def quality_session(
    *, rate: ExchangeRate, source: str, ceiling_eur: Decimal | None = None
) -> Session:
    """A session belonging to this auditor, with its prompt as the cached prefix.

    The **fourth** audit to share S-9.1's `audit_session` — after the finding
    audit, S-10.3's test audit and S-11.1's patch audit. Sharing the constructor
    is what keeps `refuse_shared_session` meaningful: each caller supplies its own
    system text and the session remembers which.

    **Not `test_quality_session`, and that was not a style preference.** pytest
    collects a *function* on the `test_` prefix alone, exactly as it collects a
    class on `Test` — so the obvious name made this constructor a test case, which
    errored on the fixtures it does not have. This module already carried
    `__test__ = False` on two classes for the same hazard and still walked into it
    on a function name.
    """
    return audit_session(rate=rate, source=source, ceiling_eur=ceiling_eur, system=SYSTEM)


def render(candidate: Candidate, test: FalsificationTest) -> str:
    """The test as the subject, and the diff as the new information.

    Order matters and is the opposite of S-11.1's. There the diff is what is under
    attack and the test is context; here the **test** is under attack and the diff
    is what this round knows that the earlier one did not. Leading with the diff
    would invite an audit of the change, which is S-11.2 to S-11.5's job and not
    this one's.
    """
    return "\n\n".join(
        [
            render_test(test),
            "THE CHANGE THAT PASSED IT",
            candidate.diff.rstrip(),
            (
                "Nothing the author wrote about either the test or the change is included. "
                "The question is about the test."
            ),
        ]
    )


def invoke(  # noqa: PLR0913 - the candidate, the test, the chain and the two
    # measured token counts are five different facts, plus the session and the
    # client. There is deliberately no parameter for a `Patch` or for prior
    # attempts — the subject is a `Candidate`, which cannot carry either.
    session: Session,
    client: ModelClient,
    *,
    candidate: Candidate,
    test: FalsificationTest,
    chain: EvidenceChain,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[TestAudit]:
    """Ask whether a cheat could pass this test, now that the change exists. AC 1, AC 2.

    Returns S-10.3's `TestAudit` rather than a type of this module's own. The
    answer has the same shape — weaknesses, and a replacement if there are any —
    and inventing a parallel type would give the composed path two things to
    branch on where the branch is identical.

    The reply is parsed by `testaudit.parse`, so `check_stronger`'s three refusals
    apply here unchanged: the threshold may not rise, no guard may vanish, and the
    replacement must claim to catch what this audit just said would slip through.

    Raises:
        TestAuditError: no usable audit came back, or the replacement is weaker.
        AuditError: the session belongs to another agent, or the model declined.
        BudgetExhaustedError: this finding's patch-audit rounds are spent.
    """
    refuse_shared_session(session, expected=SYSTEM)

    evidence = render(candidate, test)
    step = Step(
        step_type=StepType.ATTACK_DESIGN,
        phase=Phase.PATCH_AUDIT,
        agent=Agent.ADVERSARY,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str, blocks: Sequence[Block]) -> tuple[TestAudit, TokenUsage]:
        del blocks  # the Adversary builds its own list — see `audit_messages`
        reply = client.complete(
            model=model,
            system=SYSTEM,
            messages=audit_messages(evidence, QUESTION),
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=AUDIT_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the test-quality auditor declined to answer. A refusal is a successful "
                "response with an empty content list, so it is reported rather than read as an "
                "audit that found no hole — which is the reading that lets a decline ship a "
                "weak test as a permanent one"
            )
            raise TestQualityError(message)
        if reply.truncated:
            message = (
                f"the audit was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated replacement "
                "test is one whose assertions may be missing, and this one would be kept for "
                "ever"
            )
            raise TestQualityError(message)
        return parse(reply.text, test, chain), reply.usage

    return session.run(
        step,
        question=QUESTION,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )


def keep(
    audit: TestAudit,
    *,
    proof_of_failure: Falsified,
    verified: Outcome,
    finding_id: str | None = None,
) -> RegressionTest:
    """**AC 3.** Turn the audit's outcome into the test that ships and stays.

    Takes `audit.forward` — S-10.3's accessor for *the test that must be
    satisfied* — so the strengthened one is kept where there is one and the
    original where there is not, and a caller cannot ship the weak test by reading
    the wrong field.

    The two proofs are parameters rather than anything this function derives,
    because both come from runs it did not perform: `CLAUDE.md` puts the measuring
    in the harness, and a `keep` that ran the gate itself would be an auditor
    producing its own evidence.

    Raises:
        TestQualityError: the test did not pass on the patched code, the proof of
            failure is about a different test, or it does not claim to catch what
            it is said to close.
    """
    return RegressionTest(
        test=audit.forward,
        closes=tuple(item.cheat for item in audit.weaknesses),
        proof_of_failure=proof_of_failure,
        verified=verified,
        finding_id=finding_id,
    )


def named(weaknesses: tuple[Weakness, ...]) -> tuple[Cheat, ...]:
    """Which classes an audit said would slip through, in the enum's own order.

    Exposed so a caller can ask without unpacking `Weakness`, and so a test can
    assert the set rather than a rendering — S-9.1's construction.
    """
    found = {item.cheat for item in weaknesses}
    return tuple(item for item in Cheat if item in found)

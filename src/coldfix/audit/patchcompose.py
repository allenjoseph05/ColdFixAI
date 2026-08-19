"""A patch in, a verdict out. **The path Epic 11 did not have.**

Epic 11 composed. Eight stories — an isolated handover, five attacks, a verdict,
an ablation — and after all of them the epic could not perform its own sentence:
*defeat the patch, not review it.* Five separate audits existed and nothing ran a
single patch through all of them.

This is the fifth epic to end that way, and the defect is the same shape every
time: **a value one story produces and another consumes, where nothing in either
story's tests holds both ends.** What that shape produced here:

**The round is authorized twice and the second check answers a different
question.** S-11.1 wired `authorize_round` for *may this round start*, and S-11.7's
`route` calls it again to decide whether a broken patch may go back. Between them
sits `record_round`, which is what actually moves the counter — so on the last
permitted round the pre-check passes, the attacks run, and `route` is asked *may
another round start* **before the round that just happened was recorded**. It says
yes. The patch goes back to the Surgeon with no audit left to judge the reply.
The order here is authorize, attack, **record**, then route.

**The suite command was passed twice and nothing held both ends.** S-11.5 runs the
suite with one command and S-11.7 builds the reproduction from another argument of
the same name. Two call sites, no shared value, and the failure is silent: the
reproduction names a command that was never run, and it is the one thing a human
is told to paste.

**A strengthened test could never become a regression test.** S-11.6's
`RegressionTest` refuses a proof of failure about a different test, which is
S-10.3's re-gating rule at the permanent artifact. The only `Falsified` in
existence at this point is the one for the Surgeon's **original** test, so `keep`
could not be called at all on the branch where the audit strengthened something —
the artifact AC 3 exists for was unreachable. The strengthened test is re-gated
against the diagnostic worktree and re-verified against the candidate before it is
kept.

**Nothing in this project could read a file out of a worktree**, and this
composition is what found that. `03-agents.md` §6.2 lists `read_file(path)` among
the Adversary's tools; no session had it, and `Candidate` and `ScopeAudit` both
need source. `CandidateSession.sources` and `original_of` now close it, and
`Subject.of` assembles a subject without a caller having to supply either.

**The reader is on the candidate session and must never be on the diagnostic one.**
A diagnostic session may run any command, so it may write any file; give it a way
to read one back and a diagnostic run can emit a diff to disk and hand it out —
ADR 004's *an ablation run cannot produce a patch* defeated through a reader
rather than through a writer. S-2.3's rule is that the operation is absent, not
guarded, and it stays absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from coldfix.audit import cheating, equivalence, patchverdict, scoping, testquality, trades
from coldfix.audit.cheating import Measure, Metrics
from coldfix.audit.equivalence import Probe
from coldfix.audit.patchaudit import Candidate, authorize_round, candidate_from
from coldfix.audit.patchverdict import (
    Attack,
    AttackResult,
    PatchVerdict,
    Route,
    Routing,
    Verdict,
    not_run,
)
from coldfix.audit.testquality import RegressionTest
from coldfix.cost.budget import Budget, BudgetExhaustedError
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.llm.client import ModelClient
from coldfix.primitives.envelope import EnvelopeSample
from coldfix.primitives.scaling import Distribution
from coldfix.repair.compose import Outcome, verify
from coldfix.repair.falsification import CostClaim, FalsificationTest
from coldfix.repair.mustfail import Falsified, NotFalsified, run_gate
from coldfix.repair.patch import Patch
from coldfix.repair.testaudit import TestAudit
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.patching import touched_paths

SOURCE_IS_THE_CANDIDATES = (
    "Source comes from the candidate worktree and from nowhere else. A `DiagnosticSession` "
    "has no reader and must not get one: it may run any command, so it may write any file, "
    "and a reader would let a diagnostic run emit a diff to disk and hand it out — ADR 004 "
    "defeated through a reader rather than a writer. The original revision is read from the "
    "same worktree's `HEAD`, which no applied patch has touched."
)


class CompositionError(Exception):
    """The patch audit could not be composed."""


@dataclass(frozen=True)
class Subject:
    """The two revisions and everything read off them.

    Bundled because five attacks want overlapping slices of the same handful of
    facts, and threading eleven arguments through `audit_patch` would put the
    order of them in every caller.

    `sources` is the **patched** revision of the whole repository, not only the
    touched files: S-11.5 looks for callers, and callers are by definition
    somewhere else. `original_sources` is what the touched files held before.
    `Subject.of` reads both off the candidate worktree; the fields stay
    constructible by hand so a test can supply source without a git checkout.
    """

    @classmethod
    def of(
        cls,
        diff: str,
        *,
        diagnostic: DiagnosticSession,
        candidate: CandidateSession,
        suite_command: Sequence[str],
        probe: Probe,
    ) -> Subject:
        """Read both revisions off the candidate worktree. **The gap, closed.**

        Until `CandidateSession.sources` existed nothing could produce these two
        mappings, so `audit_patch` could only be called by something that already
        had them — and nothing did.

        The original is read from the same worktree's `HEAD` rather than from a
        second session at the base revision, because that session would be a
        diagnostic one and giving it a reader is the thing the module docstring
        refuses.
        """
        return cls(
            diagnostic=diagnostic,
            candidate=candidate,
            sources=candidate.sources(),
            original_sources=candidate.original_of(touched_paths(diff)),
            suite_command=suite_command,
            probe=probe,
        )

    diagnostic: DiagnosticSession
    candidate: CandidateSession
    sources: Mapping[str, str]
    original_sources: Mapping[str, str]
    suite_command: Sequence[str]
    """**One value, used twice.** S-11.5 runs the suite with it and S-11.7 puts it
    in the reproduction a human is told to paste. Two arguments of the same name
    at two call sites is how those come to disagree."""

    probe: Probe
    """How to drive the workload for the equivalence attack."""

    def __post_init__(self) -> None:
        if not self.suite_command:
            message = (
                "no suite command. S-11.5's second criterion is *runs the full test suite*, and "
                "an empty command would make that attack report `NOT_RUN` on every patch while "
                "the audit still called itself complete"
            )
            raise CompositionError(message)


@dataclass(frozen=True)
class Measurements:
    """What the harness measured, for the two attacks that reason over numbers.

    Neither S-11.3 nor S-11.4 measures anything itself — `CLAUDE.md` puts the
    measuring in the harness — so both are handed results, and this is the shape
    the orchestrator has to fill.
    """

    measure: Measure
    metrics: Metrics
    shape: Distribution
    alternatives: Sequence[Distribution]
    envelope_before: EnvelopeSample
    envelope_after: EnvelopeSample
    domain_before: Mapping[str, float]
    domain_after: Mapping[str, float]
    claim: CostClaim


@dataclass(frozen=True)
class Audited:
    """What one round of the patch audit concluded, and what it produced."""

    candidate: Candidate
    verdict: PatchVerdict
    routing: Routing
    regression: RegressionTest | None
    """AC 3 of S-11.6, and `None` where the test could not be kept — a strengthened
    test that failed its re-gate has no proof to ship with."""

    @property
    def ships(self) -> bool:
        return self.routing.route is Route.SHIP

    def describe(self) -> str:
        lines = [self.routing.describe()]
        if self.regression is not None:
            lines.extend(self.regression.describe().splitlines())
        else:
            lines.append("  No permanent regression test was kept from this round.")
        lines.append(f"  {SOURCE_IS_THE_CANDIDATES}")
        return "\n".join(lines)


def attack_all(  # noqa: PLR0913 - the two sessions, the client, the candidate, the
    # test, the chain, the subject, the measurements and the two token counts are
    # ten independent facts. Five attacks want different ones and none is
    # derivable from another.
    auditor: Session,
    quality: Session,
    client: ModelClient,
    *,
    candidate: Candidate,
    test: FalsificationTest,
    chain: EvidenceChain,
    subject: Subject,
    measurements: Measurements,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> tuple[tuple[AttackResult, ...], TestAudit]:
    """Mount all five attacks of §6.3 on one patch.

    **Two sessions, and that is not an accident.** S-11.1's patch audit and
    S-11.6's test-quality audit have different system prompts, so one session
    reused for both would bill and cache against a prefix that was never sent —
    the defect Epic 10's composition found for the Surgeon. `refuse_shared_session`
    catches it inside each `invoke`; passing two is what makes it not arise.

    Returns the five results and the test audit, because S-11.6's answer is needed
    twice: once as a verdict input and once to decide what regression test to keep.
    """
    outcome = equivalence.attack(
        subject.probe,
        original=subject.diagnostic,
        patched=subject.candidate,
        inputs=equivalence.catalogue(),
    )
    cheat = cheating.detect(
        measurements.measure,
        metrics=measurements.metrics,
        shape=measurements.shape,
        alternatives=measurements.alternatives,
        equivalence=outcome,
    )
    trade = trades.audit_trades(
        before=measurements.envelope_before,
        after=measurements.envelope_after,
        domain_before=measurements.domain_before,
        domain_after=measurements.domain_after,
        claim=measurements.claim,
    )
    suite = scoping.run_suite(subject.diagnostic, subject.candidate, command=subject.suite_command)
    scope = scoping.audit_scope(candidate.diff, sources=subject.sources, chain=chain, suite=suite)
    written = testquality.invoke(
        quality,
        client,
        candidate=candidate,
        test=test,
        chain=chain,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )

    results = (
        patchverdict.from_equivalence(outcome),
        patchverdict.from_cheat(cheat),
        patchverdict.from_trades(trade),
        # **The same command that ran the suite**, rather than a second argument
        # spelled the same way. The reproduction is what a human pastes.
        patchverdict.from_scope(scope, suite_command=subject.suite_command),
        patchverdict.from_test_quality(written.value),
    )
    _ = auditor
    return results, written.value


def keep_regression_test(
    audit: TestAudit,
    *,
    original: Falsified,
    subject: Subject,
    finding_id: str | None = None,
) -> RegressionTest | None:
    """Turn the test audit's answer into the permanent test, re-gating if it changed.

    **This is the join AC 3 was unreachable across.** `RegressionTest` refuses a
    proof of failure about a different test — S-10.3's *a strengthened test is
    re-gated, not trusted*, applied at the artifact that ships. The only
    `Falsified` in existence here is the one for the Surgeon's **original** test,
    so on the branch where the audit strengthened something there was no proof to
    attach and `keep` could not be called at all.

    So the strengthened test goes back through S-10.2's gate against the
    diagnostic worktree — where a patch cannot exist — and then through S-10.6's
    `verify` against the candidate. Both, because a permanent test needs to be
    seen failing where the bug is and passing where the fix is, and neither is
    inherited from the test it replaced.

    Returns `None` where the replacement did not fail on unpatched code. That is
    S-10.2's `PASSED_UNPATCHED` refusal reaching the audit: a *stronger* test the
    original code already satisfies is exactly as useless as a weak one, and
    shipping it would install a permanent test that can never fail.
    """
    if audit.sound:
        return testquality.keep(
            audit,
            proof_of_failure=original,
            verified=verify(audit.forward, subject.candidate),
            finding_id=finding_id,
        )

    regated = run_gate(audit.forward, subject.diagnostic)
    if isinstance(regated, NotFalsified):
        return None
    return testquality.keep(
        audit,
        proof_of_failure=regated,
        verified=verify(audit.forward, subject.candidate),
        finding_id=finding_id,
    )


def audit_patch(  # noqa: PLR0913 - see `attack_all`; this adds the patch, the
    # proof of failure and the budget, and subtracts nothing.
    auditor: Session,
    quality: Session,
    client: ModelClient,
    *,
    patch: Patch,
    test: FalsificationTest,
    chain: EvidenceChain,
    falsified: Falsified,
    subject: Subject,
    measurements: Measurements,
    budget: Budget,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> Audited:
    """One round of the patch audit. **The epic's sentence, performed once.**

    Takes a `Patch` and hands `candidate_from` the only thing that ever sees it —
    S-11.1's boundary, with this module on the wrong side of it deliberately, so
    that no later caller has to remember to strip anything.

    **Authorize, attack, record, then route.** The order is the defect this
    composition found. `route` asks *may another round start*, and asking it
    before the round that just happened is recorded means the last permitted round
    sends a broken patch back to a Surgeon whose reply nothing is left to audit.

    Raises:
        BudgetExhaustedError: this finding's audit rounds are already spent. The
            caller escalates; this module does not decide what happens to a patch
            it was not allowed to look at.
    """
    authorize_round(budget, finding_id)

    candidate = candidate_from(
        patch, original=dict(subject.original_sources), patched=dict(subject.sources)
    )
    results, written = attack_all(
        auditor,
        quality,
        client,
        candidate=candidate,
        test=test,
        chain=chain,
        subject=subject,
        measurements=measurements,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    verdict = patchverdict.verdict_for(results)

    # **Recorded before routed.** See the docstring: the other order spends the
    # last round on an answer nobody can audit.
    patchverdict.record(budget, verdict, finding_id)
    routing = patchverdict.route(budget, verdict, finding_id)

    regression = keep_regression_test(
        written, original=falsified, subject=subject, finding_id=finding_id
    )
    return Audited(candidate=candidate, verdict=verdict, routing=routing, regression=regression)


def escalated(routing: Routing) -> bool:
    """Whether this round ended with a person rather than with the Surgeon."""
    return routing.route is Route.ESCALATE


def unattempted(reason: str) -> tuple[AttackResult, ...]:
    """Five `NOT_RUN` results, for a round that could not mount its attacks.

    A caller that cannot stand the subject up still has to produce a verdict, and
    the verdict for *nothing ran* is `suspicious` — never `clean`. Written here so
    that path cannot be spelled as an empty list, which `verdict_for` refuses and
    which a caller might otherwise be tempted to pass.
    """
    return tuple(not_run(attack, reason) for attack in Attack)


__all__ = [
    "SOURCE_IS_THE_CANDIDATES",
    "Audited",
    "BudgetExhaustedError",
    "CompositionError",
    "Measurements",
    "Outcome",
    "Subject",
    "Verdict",
    "attack_all",
    "audit_patch",
    "escalated",
    "keep_regression_test",
    "unattempted",
]

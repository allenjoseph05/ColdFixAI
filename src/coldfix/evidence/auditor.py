"""The one model call in the finding audit, and what it is not allowed to have.

S-21.2. It runs **only if all four code attacks held**, which is the economics of
the node: a finding that fails an arithmetic check costs nothing to reject, and
the call is spent on the ones arithmetic cannot settle -- whether the mechanism
described is the mechanism the numbers show.

**Everything is pre-loaded, and that is the isolation.** The auditor has no
tools. Not "tools it is told not to use" -- none are passed, so there is nothing
to call. A reviewer that could go and look would be a reviewer forming its own
view of a repository, and what it was asked to review is a chain of evidence.

**It cannot be given the reasoning that produced the finding.** There is no field
for it on the input, the same way the Adversary has none for the Surgeon's. A
reviewer handed a justification reviews the justification.

**The call goes through the meter.** S-26.1. It is billed as the finding auditor
in the finding-audit phase, and its step type is `ATTACK_DESIGN`: whether a
mechanism follows from the numbers is a judgement with no deterministic check, so
the router keeps it on the frontier tier and nothing may cascade it lower.
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel

from coldfix.cost.accounting import Agent, Phase
from coldfix.cost.routing import StepType
from coldfix.evidence.audit import Attack, Audit, Verdict
from coldfix.evidence.ledger import Finding
from coldfix.llm.metered import Call, Meter

TEMPERATURE = 0.0
MAX_TOKENS = 1024

REVIEW = Call(
    step=StepType.ATTACK_DESIGN,
    phase=Phase.FINDING_AUDIT,
    agent=Agent.FINDING_AUDITOR,
    max_tokens=MAX_TOKENS,
)
"""What the review spends on. A constant, because nothing about a finding changes
which step it is -- and a step type chosen per call is one a caller could lower."""

SYSTEM = """\
You review one finding about a program's performance and decide whether the
evidence establishes it. You are not looking for other problems and you cannot
go and look at anything -- what you are given is all there is.

Four arithmetic checks have already passed: the numbers still match what was
recorded, enough measurements stand behind the payoff, it clears the noise floor
of the run it was measured on, and nothing else got worse. Do not redo those.

What is left for you is the one thing arithmetic cannot settle: whether the
mechanism the finding describes is the mechanism these numbers show.

A finding can be arithmetically perfect and still wrong. Removing a function
call removes its cost -- that says the call is expensive, not that the reason
given for why it is expensive is right. If the summary says "the loop issues one
query per row" and the evidence is a wall-clock difference with no count in it,
the numbers do not say that.

Reply with one JSON object and nothing else:

  {"verdict": "sound" | "needs_evidence" | "unsound", "because": "..."}

sound            the numbers show the mechanism described
needs_evidence   plausible, and what is here does not establish it
unsound          the numbers contradict the description

Be specific in `because`. "Insufficient evidence" tells nobody what to measure
next; "nothing cited is a count, so a per-row claim is not supported" does.
"""


class Review(BaseModel, frozen=True):
    """What the auditor decided. No field carries anything it was not asked."""

    verdict: Verdict
    because: str


class Presented(BaseModel, frozen=True):
    """What the auditor is shown. Everything, at once, and nothing else.

    There is no `reasoning` field and no `transcript` field, and their absence is
    the point: a reviewer given the argument that produced a finding reviews the
    argument.
    """

    kind: str
    summary: str
    location: str
    cited: tuple[str, ...]
    payoff: str
    attacks_that_held: tuple[str, ...]

    @classmethod
    def of(cls, finding: Finding, audit: Audit) -> Presented:
        claim = finding.claim
        return cls(
            kind=claim.kind,
            summary=claim.summary,
            location=f"{claim.location.file}:{claim.location.line} {claim.location.symbol}".strip(),
            cited=tuple(f"{c.measurement_id}.{c.field} = {c.value}" for c in claim.evidence),
            payoff=(
                f"{claim.payoff:.1%} proven by ablation {claim.proof.measurement_id}"
                if claim.payoff is not None and claim.proof is not None
                else "none claimed; reported as suspected"
            ),
            attacks_that_held=tuple(a.name for a in audit.attacks if a.held),
        )

    def render(self) -> str:
        lines = [
            f"kind: {self.kind}",
            f"where: {self.location}",
            f"summary: {self.summary}",
            f"payoff: {self.payoff}",
            "cited measurements:",
            *(f"  {line}" for line in self.cited),
            f"already checked: {', '.join(self.attacks_that_held)}",
        ]
        return "\n".join(lines)


def review(finding: Finding, audit: Audit, *, meter: Meter) -> Audit:
    """Ask once, and only where the attacks left something to ask.

    Returns the audit with the model's verdict folded in. A finding the
    arithmetic already rejected is returned untouched and unbilled -- there is
    nothing a reviewer could add to a claim whose payoff is inside the noise.

    Raises:
        BudgetExhaustedError: the meter refused the call. Not folded into a
            verdict: a review that was never asked is not one that found the
            finding unproven, and the graph decides what a halt means.
    """
    if audit.verdict is not Verdict.SOUND:
        return audit

    response = meter.complete(
        REVIEW,
        system=SYSTEM,
        messages=[{"role": "user", "content": Presented.of(finding, audit).render()}],
        temperature=TEMPERATURE,
    )
    if response.refused:
        return _folded(audit, Verdict.NEEDS_EVIDENCE, "the reviewer declined to answer")

    decided = _read(response.text)
    return _folded(audit, decided.verdict, decided.because)


class _Unreadable(StrEnum):
    WHY = "the reviewer's reply could not be read as a verdict"


def _read(text: str) -> Review:
    """Parse, or treat it as unproven. Never repaired into a verdict.

    A reply that cannot be read is not a `sound` -- defaulting to the permissive
    answer is how a broken reviewer becomes an approving one.
    """
    try:
        payload = json.loads(
            text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        )
        return Review.model_validate(payload)
    except (ValueError, TypeError):
        return Review(verdict=Verdict.NEEDS_EVIDENCE, because=_Unreadable.WHY.value)


def _folded(audit: Audit, verdict: Verdict, because: str) -> Audit:
    return Audit(
        attacks=(
            *audit.attacks,
            Attack(name="reviewed", held=verdict is Verdict.SOUND, detail=because),
        ),
        verdict=verdict,
    )

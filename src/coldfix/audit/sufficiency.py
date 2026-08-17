"""Whether *nothing found* is an answer or an interruption.

Epic 9, S-9.9. Added by ADR 094 after S-0.8 measured the agent choosing *no
finding, stop* **0 times in 60** — including on the one scenario where stopping
was the only correct answer — and concluded the stopping decision *probably
cannot be the agent's own*. S-8.9's cap bounds the damage without deciding
sufficiency, and the `PartialChain` it emits is the one artifact **nothing else
in Epic 9 can audit**, because every other story assumes a finding exists.

**The decision is the harness's, and no model is asked.** This is F6's rule
carried to its end: *what counts as new information is decided by the harness,
not the agent*, because a self-judged criterion is one the judge is incentivised
to claim. S-0.8 measured a model asked *should we stop?* answering no, sixty
times out of sixty, on curated evidence with the right answer already worked out.
Routing that same question through a second model and hoping a different frame
saves it is not a fix; it is the same question asked of the same kind of thing.
Every input here is a fact the run already recorded — **why it stopped, what it
ruled out, and whether those exclusions were adequately conditioned** — so
`CLAUDE.md`'s *do not add a model call where a function would do* settles it.
That makes **six of Epic 9's eight audit stories** need no model at all.

**Why it stopped is the first-order signal, and the three ways differ.**

- `INSTRUMENTS` — every applicable instrument had already answered. This run ran
  out of **questions**, not money. Nothing remains to try.
- `STALL` — eight experiments with no narrowing. S-5.4 already reads this as
  *more steps of the same kind will spend budget without changing the answer*,
  which is a sufficiency judgement the budget module makes before this one does.
- `CAP` — the experiment cap was reached. This run ran out of **money** with
  something still being proposed, and a negative from an interrupted search is
  not a negative. **`CAP` is never sufficient.**

**`Stopped.disposition` answers a different question and the two deliberately
diverge.** §7.2 gives the cap `PARTIAL` (emit the chain) and the other two
`ESCALATE` — that is what the *run* does next. Whether the negative is
**believable** is not the same question, and a `CAP` stop is simultaneously the
one that ships a partial chain and the one whose negative is worth least.
Folding sufficiency into the disposition would answer one with the other.

**The exclusions are the result, so a result with no content is not one.**
`PartialChain` says it in words — *this is the result* — and allows the tuple to
be empty, correctly, because forty narrowings that never rejected have still
learned something. But *learned something* and *established a trustworthy
negative* are different claims: a run that closed no doors has not ruled anything
out, and `00-BRIEF.md` §9 ships *screened nine workloads, nothing found* on the
strength of what was excluded.

**There is no minimum experiment count, and its absence is deliberate.** Any
number would be a guess, and S-9.4's precedent is that a threshold is derived or
it does not belong. A subject supporting one applicable instrument that came back
rejected has genuinely answered the question in one experiment. The exclusion
rule does the work without inventing an arbitrary floor.

**What this cannot see is named rather than glossed over.** Adequate conditions
on the exclusions a run *made* says nothing about the hypotheses it never
attempted. `negative_sound` means *this run's exclusions are trustworthy and it
ran out of questions* — **never** *there is no performance problem here*. The
question *is there an explanation these measurements would also support?* is
S-9.5's, and this module does not re-ask it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.audit.exclusions import ExclusionAudit, audit_all
from coldfix.audit.exclusions import report as exclusion_report
from coldfix.audit.verdict import AuditVerdict, Subject, Verdict
from coldfix.bench.stats import Fit
from coldfix.diagnosis.progress import PartialChain, Stopped

RAN_OUT_OF_QUESTIONS = (Stopped.INSTRUMENTS, Stopped.STALL)
"""The two ways of stopping that are not an interruption.

Named as data so the rule is a membership test against a list a reader can check,
rather than a negation of `CAP` buried in a condition. A fourth member of
`Stopped` would have to be classified here on purpose."""

RESIDUE = (
    "A sufficient run is one whose exclusions hold and which ran out of questions. "
    "That is not the same as *there is no performance problem here*: this audit sees "
    "the hypotheses the investigation attempted and cannot see the ones it never "
    "thought of. Whether another mechanism would fit the same measurements is S-9.5's "
    "question, and a `negative_sound` verdict is only as strong as the attacks that "
    "ran beside it."
)


class Insufficiency(StrEnum):
    """Why *nothing found* is not yet an answer. AC 2's second half.

    Each is a different next action, which is the reason they are separate: a
    run cut off by the cap needs more budget, a run that closed no doors needs a
    different approach, and a run whose exclusions were narrow needs a reseed.
    """

    STOPPED_ON_BUDGET = (
        "the experiment cap was reached, so this run was cut off with something still "
        "being proposed rather than finishing"
    )
    NOTHING_RULED_OUT = (
        "no hypothesis was rejected, so this run closed no doors and has nothing to "
        "report as ruled out"
    )
    EXCLUSIONS_TOO_NARROW = (
        "what was ruled out was ruled out under conditions too narrow to establish it, "
        "so the negative rests on exclusions that do not hold as widely as they read"
    )

    @property
    def remedy(self) -> str:
        """What would settle it. `None` is not among them — every one is actionable."""
        return _REMEDY[self]


_REMEDY: dict[Insufficiency, str] = {
    Insufficiency.STOPPED_ON_BUDGET: (
        "raise the budget for this finding and resume, or accept the exclusions as a "
        "bounded result and say in the report that the search was truncated"
    ),
    Insufficiency.NOTHING_RULED_OUT: (
        "none from more of the same — S-5.4 stopped this run because more steps of the "
        "same kind will not change the answer. A different instrument set, a reseed, or "
        "a human deciding the workload is not worth further spend"
    ),
    Insufficiency.EXCLUSIONS_TOO_NARROW: (
        "reseed under the shape or scale the exclusion never saw (S-8.8), which reopens "
        "exactly the exclusions that depended on it"
    ),
}


@dataclass(frozen=True)
class SufficiencyAudit:
    """Whether this run's silence is an answer, and what is missing if it is not."""

    stopped: Stopped
    exclusions: tuple[ExclusionAudit, ...]
    shortfalls: tuple[Insufficiency, ...]

    @property
    def sufficient(self) -> bool:
        """AC 2. *Nothing was found and that is a result* — the whole of it."""
        return not self.shortfalls

    @property
    def inadequate(self) -> tuple[ExclusionAudit, ...]:
        """AC 1's answer, per exclusion. S-9.2 decides what narrow means."""
        return tuple(item for item in self.exclusions if not item.adequate)

    def describe(self) -> str:
        head = f"Sufficiency: this investigation stopped because {self.stopped.value}."
        if self.sufficient:
            lines = [
                head,
                "  This is a result. The run ran out of applicable questions rather than "
                "out of budget, and what it ruled out was ruled out under conditions wide "
                "enough to establish it.",
                f"  {len(self.exclusions)} exclusion(s) hold and are the answer.",
            ]
        else:
            lines = [head, "  This run stopped too early to call its silence a result:"]
            lines.extend(
                f"    - {item.value}\n      remedy: {item.remedy}" for item in self.shortfalls
            )
        if self.exclusions:
            lines.append(exclusion_report(self.exclusions))
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def assess_sufficiency(
    chain: PartialChain,
    *,
    fits: dict[int, Fit] | None = None,
    relative_noise: float | None = None,
) -> SufficiencyAudit:
    """Audit a partial chain. AC 1 and AC 2.

    **AC 1 needed no new machinery.** *Were the exclusions established under
    adequate conditions* is S-9.2's question word for word, and every exclusion on
    a `PartialChain` is the same `Exclusion` type S-9.2 already attacks. Writing a
    second conditions-checker here would be two modules holding two copies of one
    argument — the thing S-9.3 recorded is *not* duplication only because both
    consult a single proof.

    `fits` and `relative_noise` are S-9.2's parameters passed through, and a
    missing fit is not an objection there either: not every rejection came from a
    sweep.
    """
    audits = audit_all(chain.exclusions, fits=fits, relative_noise=relative_noise)

    shortfalls: list[Insufficiency] = []
    if chain.stopped not in RAN_OUT_OF_QUESTIONS:
        shortfalls.append(Insufficiency.STOPPED_ON_BUDGET)
    if not chain.exclusions:
        shortfalls.append(Insufficiency.NOTHING_RULED_OUT)
    elif any(not item.adequate for item in audits):
        shortfalls.append(Insufficiency.EXCLUSIONS_TOO_NARROW)

    return SufficiencyAudit(
        stopped=chain.stopped,
        exclusions=audits,
        shortfalls=tuple(shortfalls),
    )


def verdict_for_partial(audit: SufficiencyAudit) -> AuditVerdict:
    """The audit's answer about a run that found nothing. AC 3.

    **A sufficient run cannot be returned to investigate, and the enforcement is
    the schema rather than this function.** The verdict is about a
    `Subject.PARTIAL_CHAIN`, and S-9.8 refuses `sound`, `unsound` and
    `unrepresentative` about one — those three presuppose a claimed cause. So the
    only two verdicts constructible here are `negative_sound`, which S-9.8 routes
    to `REPORT` without reading the budget at all, and `inconclusive`, which
    escalates. **Neither route can reach investigate however much budget remains**,
    which is AC 3 stated as a property of the type instead of a branch somebody
    could add an exception to.

    Escalation is right for the insufficient case for a reason rather than by
    default: a run stopped by the cap has no experiments left to answer the
    objection with, and a run stopped by the stall has just been told by S-5.4
    that more steps of the same kind will not change the answer. In both, *run
    more experiments* is unavailable — which is the lever ADR 094 says an audit
    must not reach for.
    """
    if audit.sufficient:
        return AuditVerdict(verdict=Verdict.NEGATIVE_SOUND, subject=Subject.PARTIAL_CHAIN)
    return AuditVerdict(
        verdict=Verdict.INCONCLUSIVE,
        subject=Subject.PARTIAL_CHAIN,
        detail=_missing(audit.shortfalls),
    )


def _missing(shortfalls: Sequence[Insufficiency]) -> str:
    """AC 1's *what is missing*, in the form S-9.8's schema requires."""
    named = "\n  ".join(f"{item.value}\n    remedy: {item.remedy}" for item in shortfalls)
    return f"Nothing was found, and this run stopped too early for that to be a result:\n  {named}"

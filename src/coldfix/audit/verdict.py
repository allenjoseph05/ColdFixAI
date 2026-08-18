"""What the six attacks add up to, and where the run goes next.

Epic 9, S-9.8. AC: *verdict schema `sound` / `unsound` + objection /
`unrepresentative` + reason / `negative_sound` / `inconclusive` + what is
missing; `unsound` returns to investigate with the objection in context **only
while the investigation has budget left**, with none it escalates; cost of the
audit is under 15 calls.*

**Nothing here calls a model.** Six attacks have already answered; combining
their answers is `CLAUDE.md`'s *do not add a model call where a function would
do*. That makes five of Epic 9's seven attacks plus its routing decision
arithmetic, which is worth stating because the epic's own vocabulary — *attacks*,
*the Adversary* — reads as adversary calls from end to end. A test asserts this
module imports no client and no session-running function.

**`unsound` and `unrepresentative` differ in whether more experiments could
change the answer, and the routing falls out of that rather than being asserted.**
An unsound finding is a claim the evidence does not support: another experiment
can settle it, so it goes back to investigate. An unrepresentative finding is
usually *correct* — the N+1 is real — about something nobody runs, and no
experiment fixes that. Routing it back to investigate would spend the
investigate budget to establish a better answer about a thing that does not
matter, which is exactly the failure ADR 094 says this epic must not make worse.
So when both apply, `unrepresentative` wins: it is the verdict that spends
nothing, and the one no amount of further work can overturn from inside.

**`inconclusive` exists so that an attack which did not run cannot read as an
attack that passed.** That is S-3.1's distinction between *no* and *not known*,
which S-9.4 already drew for a missing fit and S-9.6 for a metric that vanished.
Silence is the failure this epic keeps finding in other places, and a four-verdict
vocabulary would reintroduce it at the top: an audit that ran two of six attacks
and objected to neither would report `sound`.

**An attack that does not apply is not an attack that is missing**, and
collapsing the two would make `inconclusive` the answer to almost everything —
an audit that escalates every finding is as useless as one that passes every
finding, and less obviously so. A diagnosis resting on an ablation has no sweep
to audit; `Outcome.INAPPLICABLE` says so and does not count against it.

**`negative_sound` is in the vocabulary here and is produced by S-9.9.** ADR 094
added it because `00-BRIEF.md` §9 makes *screened nine workloads, nothing found*
shippable output and nothing in Epic 9 could express it. It is unreachable from
`verdict_for` **by schema rather than by convention**: a verdict carries the
`Subject` it is about, `negative_sound` requires a `PartialChain`, and the three
verdicts that presuppose a cause require a finding. `sound` means *proceed to
repair* and there is nothing to repair in a chain that confirmed nothing.

**The two-round audit cap has been dead since S-5.4 and this story is its
owner.** S-8.9 made `Session.run` record a step only where the phase's cap counts
steps — `Phase.FINDING_AUDIT` counts *rounds* — so nothing has ever incremented
it and `authorize` has been passing on a counter frozen at zero. Whoever owns the
unit counts the unit, and a round of the finding audit ends here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from coldfix.audit.alternatives import AlternativeAudit
from coldfix.audit.exclusions import ExclusionAudit
from coldfix.audit.exclusions import report as exclusion_report
from coldfix.audit.fixtures import FixtureAudit
from coldfix.audit.representativeness import RepresentativenessAudit
from coldfix.audit.reproducibility import ReproducibilityAudit
from coldfix.audit.scales import ScaleAudit
from coldfix.cost.accounting import ModelCall, Phase
from coldfix.cost.budget import Budget, Disposition

AUDIT_CALL_CEILING = 15
"""AC 3, read strictly: *under* 15 calls, so fifteen is already too many.

`08-audit.md` §4 costs the finding audit at *~10 calls*, against a repair phase
costing ~50 — the whole economic argument for running it. That estimate assumed
six adversary invocations. Four of the six attacks turned out to be arithmetic,
so a full audit makes **two** model calls plus whatever S-5.6's cascade retries,
and the ceiling is an order of magnitude above what the design actually spends.

It is checked anyway, because the number that matters is the measured one and
the Epic 8 composition check's closing lesson was that *a defect whose only
symptom is a cost figure needs a test that reads the cost figure*.
"""


class VerdictError(Exception):
    """A verdict could not be formed, or was asked to say two things at once."""


class Attack(StrEnum):
    """Epic 9's six attacks, as `08-audit.md` §4 lists them.

    Named as data so that *which attacks ran* is a set this module can compare
    against, rather than something a caller remembers to pass completely. An
    audit missing one is the case `inconclusive` exists for, and it cannot be
    detected against a list nobody wrote down.
    """

    EXCLUSION_VALIDITY = "exclusion validity"
    FIXTURE_ADEQUACY = "fixture adequacy"
    SCALE_ADEQUACY = "scale adequacy"
    ALTERNATIVE_EXPLANATION = "alternative explanation"
    REPRODUCIBILITY = "reproducibility"
    REPRESENTATIVENESS = "representativeness"


SOFT_ATTACK = Attack.REPRESENTATIVENESS
"""The one attack whose objection does not mean the finding is wrong.

Every other attack says *the evidence does not support this claim*.
Representativeness says *this claim is probably true and nobody cares*, which is
a different sentence with a different remedy, and it is the whole reason the
verdict vocabulary has more than two members."""


class Outcome(StrEnum):
    """What happened to one attack. Four values, and the last two are not one value.

    `INAPPLICABLE` and `NOT_RUN` both mean *no answer*, and treating them alike
    breaks the audit in one direction or the other: fold `NOT_RUN` into
    `INAPPLICABLE` and an attack nobody ran reads as one that did not apply, which
    is the silence this vocabulary exists to prevent; fold `INAPPLICABLE` into
    `NOT_RUN` and every ablation-based finding is `inconclusive` because it had no
    sweep to audit.
    """

    PASSED = "ran, and found nothing to object to"
    OBJECTED = "ran, and objected"
    INAPPLICABLE = "does not apply to this finding"
    NOT_RUN = "applies to this finding and was not run"


NEEDS_DETAIL = (Outcome.OBJECTED, Outcome.NOT_RUN)
"""The outcomes that are useless without their text. AC 1 asks for *+ objection*
and *+ what is missing*; these are where those two come from."""


@dataclass(frozen=True)
class AttackResult:
    """One attack's answer, in the form the verdict is computed from.

    Frozen and validated in `__post_init__` rather than trusted, because the
    detail is what a human acts on: an objection nobody can read is an
    escalation with no instruction in it.
    """

    attack: Attack
    outcome: Outcome
    detail: str = ""

    def __post_init__(self) -> None:
        if self.outcome in NEEDS_DETAIL and not self.detail.strip():
            message = (
                f"{self.attack.value} {self.outcome.value} and said nothing about it. That text "
                "is the objection a reader acts on, or the gap a reader has to close; without it "
                "the verdict names a problem nobody can do anything with"
            )
            raise VerdictError(message)

    @property
    def objected(self) -> bool:
        return self.outcome is Outcome.OBJECTED

    @property
    def answered(self) -> bool:
        """Whether this attack produced an answer at all.

        `INAPPLICABLE` counts: *this does not apply here* is an answer, and a
        finding is not less audited for resting on an instrument one attack has
        no purchase on.
        """
        return self.outcome is not Outcome.NOT_RUN

    def describe(self) -> str:
        line = f"{self.attack.value}: {self.outcome.value}"
        return f"{line}\n    {self.detail}" if self.detail.strip() else line


def not_run(attack: Attack, missing: str) -> AttackResult:
    """An attack that applies here and did not happen. AC 1's *what is missing*.

    A constructor rather than a `None` the adapters return, because `None` would
    then mean both *did not apply* and *did not run* — one value for the two
    things this module exists to keep apart.
    """
    return AttackResult(attack=attack, outcome=Outcome.NOT_RUN, detail=missing)


def inapplicable(attack: Attack, because: str) -> AttackResult:
    """An attack with no purchase on this finding. Not an objection, not a gap."""
    return AttackResult(attack=attack, outcome=Outcome.INAPPLICABLE, detail=because)


def from_exclusions(audits: Sequence[ExclusionAudit]) -> AttackResult:
    """S-9.2's answer, as a result. Empty means nothing was ruled out.

    An investigation that excluded nothing has no preconditions to attack, which
    is `INAPPLICABLE` and not a pass — S-9.2's own `report` says so in words, and
    a reader told *exclusion validity passed* about zero exclusions has been told
    something false.
    """
    if not audits:
        return inapplicable(
            Attack.EXCLUSION_VALIDITY,
            "nothing was ruled out, so there are no preconditions to attack",
        )
    inadequate = [item for item in audits if not item.adequate]
    if not inadequate:
        return AttackResult(attack=Attack.EXCLUSION_VALIDITY, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.EXCLUSION_VALIDITY,
        outcome=Outcome.OBJECTED,
        detail=exclusion_report(inadequate),
    )


def from_fixture(audit: FixtureAudit) -> AttackResult:
    """S-9.3's answer, as a result."""
    if audit.adequate:
        return AttackResult(attack=Attack.FIXTURE_ADEQUACY, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.FIXTURE_ADEQUACY,
        outcome=Outcome.OBJECTED,
        detail=audit.describe(),
    )


def from_scales(audit: ScaleAudit) -> AttackResult:
    """S-9.4's answer, as a result.

    A finding with no growth claim behind it has no sweep to audit, and the
    caller says so with `inapplicable` rather than passing a fit nobody drew —
    S-9.4 refuses to invent one and this refuses to paper over its absence.
    """
    if audit.adequate:
        return AttackResult(attack=Attack.SCALE_ADEQUACY, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.SCALE_ADEQUACY,
        outcome=Outcome.OBJECTED,
        detail=audit.describe(),
    )


def from_alternatives(audit: AlternativeAudit) -> AttackResult:
    """S-9.5's answer, as a result.

    *No alternative* is a pass, and S-9.5 spends its length making sure a model
    can say it. Reading the empty answer as anything other than this attack
    passing would undo that at the join.
    """
    if not audit.unsound:
        return AttackResult(attack=Attack.ALTERNATIVE_EXPLANATION, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.ALTERNATIVE_EXPLANATION,
        outcome=Outcome.OBJECTED,
        detail=audit.describe(),
    )


def from_reproducibility(audit: ReproducibilityAudit) -> AttackResult:
    """S-9.6's answer, as a result. Only material divergence objects."""
    if not audit.unsound:
        return AttackResult(attack=Attack.REPRODUCIBILITY, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.REPRODUCIBILITY,
        outcome=Outcome.OBJECTED,
        detail=audit.describe(),
    )


def from_representativeness(audit: RepresentativenessAudit) -> AttackResult:
    """S-9.7's answer, as a result. Its objection is the one that skips repair."""
    if not audit.skips_repair:
        return AttackResult(attack=Attack.REPRESENTATIVENESS, outcome=Outcome.PASSED)
    return AttackResult(
        attack=Attack.REPRESENTATIVENESS,
        outcome=Outcome.OBJECTED,
        detail=audit.describe(),
    )


class Subject(StrEnum):
    """What a verdict is about. Two artifacts, and they take different verdicts."""

    FINDING = "an evidence chain claiming a cause"
    PARTIAL_CHAIN = "an investigation that confirmed nothing"


class Verdict(StrEnum):
    """AC 1's vocabulary. Five members, and ADR 094 added the last two."""

    SOUND = "sound: the evidence supports the finding, proceed to repair"
    UNSOUND = "unsound: an attack landed, and the finding does not survive it"
    UNREPRESENTATIVE = "unrepresentative: probably true, about something nobody runs"
    NEGATIVE_SOUND = "negative_sound: nothing was found, and that is a trustworthy answer"
    INCONCLUSIVE = "inconclusive: the audit itself is incomplete"


NEEDS_REASON = (Verdict.UNSOUND, Verdict.UNREPRESENTATIVE, Verdict.INCONCLUSIVE)
"""AC 1's three verdicts with a payload: *+ objection*, *+ reason*, *+ what is
missing*. The other two carry nothing, and carrying something would be a
contradiction rather than an extra — see `AuditVerdict`."""

ABOUT_A_FINDING = (Verdict.SOUND, Verdict.UNSOUND, Verdict.UNREPRESENTATIVE)
"""The three verdicts that presuppose a cause was claimed. Each is meaningless
about a `PartialChain`: there is nothing to repair, nothing to disprove, and no
workload whose representativeness would change what happens next."""


class AuditVerdict(BaseModel):
    """The finding audit's answer, and the artifact the graph routes on.

    A Pydantic model rather than a dataclass because `CLAUDE.md` requires one for
    every artifact crossing a node boundary, and this crosses the one Epic 9
    exists to insert — between investigate and repair.

    **The validators are the schema half of AC 1.** A verdict that carries a
    payload it should not have, or lacks one it must, is refused at construction:
    a `sound` verdict with an objection attached says two things at once, and the
    reader would be right to believe either.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    subject: Subject
    detail: str = ""
    """The objection, the reason, or what is missing — whichever AC 1 attaches to
    this verdict. Empty exactly when the verdict attaches none."""

    results: tuple[AttackResult, ...] = ()
    """What each attack answered. Kept rather than reduced to a verdict, because
    a reader deciding whether to overturn an `unrepresentative` needs to see that
    the other five attacks passed — and because an `inconclusive` is unactionable
    without the list of what did not run."""

    @model_validator(mode="after")
    def _payload_matches_verdict(self) -> Self:
        needs = self.verdict in NEEDS_REASON
        has = bool(self.detail.strip())
        if needs and not has:
            message = (
                f"{self.verdict.value} with nothing said. This is one of the three verdicts "
                "`10-BACKLOG.md` writes with a payload — an objection, a reason, or what is "
                "missing — and every one of them exists to tell somebody what to do next"
            )
            raise VerdictError(message)
        if has and not needs:
            message = (
                f"{self.verdict.value} carries an objection. That verdict attaches none, so the "
                "artifact would be saying two things at once and a reader would be entitled to "
                "believe either"
            )
            raise VerdictError(message)
        return self

    @model_validator(mode="after")
    def _verdict_matches_subject(self) -> Self:
        if self.verdict is Verdict.NEGATIVE_SOUND and self.subject is not Subject.PARTIAL_CHAIN:
            message = (
                "negative_sound says nothing was found, which cannot be said about a finding. "
                "ADR 094 added this verdict for the artifact an exhausted investigation emits"
            )
            raise VerdictError(message)
        if self.verdict in ABOUT_A_FINDING and self.subject is not Subject.FINDING:
            message = (
                f"{self.verdict.value} presupposes a claimed cause, and a partial chain confirms "
                "nothing by construction (S-8.9). There is nothing here to repair, to disprove, "
                "or to call unrepresentative"
            )
            raise VerdictError(message)
        return self

    @property
    def unanswered(self) -> tuple[AttackResult, ...]:
        return tuple(item for item in self.results if not item.answered)

    def describe(self) -> str:
        lines = [f"Finding audit — {self.verdict.value}", f"  subject: {self.subject.value}"]
        if self.detail.strip():
            lines.append(f"  {self.detail}")
        if self.results:
            lines.append("  Attacks:")
            lines.extend(f"    {item.describe()}" for item in self.results)
        return "\n".join(lines)


def verdict_for(results: Sequence[AttackResult]) -> AuditVerdict:
    """Combine six attacks into one verdict. No model, and no judgement.

    Precedence, and the argument for it:

    1. **`unrepresentative`**, because it is the only objection more experiments
       cannot answer. A finding that is both unsound and unrepresentative routed
       back to investigate would spend the experiment budget establishing a
       better answer about a workload nobody runs — ADR 094's hazard reached
       through the verdict rather than through the agent.
    2. **`unsound`**, because an objection somebody can act on outranks a gap.
    3. **`inconclusive`**, when nothing objected but not every attack ran. This
       is below the two objections deliberately: an audit that landed a real
       objection has told the reader something useful, and reporting *the audit
       was incomplete* instead would bury it.
    4. **`sound`**, which is what is left when every attack answered and none
       objected.

    `negative_sound` is not reachable from here and cannot be: this function
    audits a finding, and `AuditVerdict` refuses that verdict about one.

    Raises:
        VerdictError: an attack was reported twice, which would let one answer
            silently replace another.
    """
    seen = [item.attack for item in results]
    duplicated = sorted({attack.value for attack in seen if seen.count(attack) > 1})
    if duplicated:
        message = (
            f"these attacks were reported more than once: {duplicated}. One attack has one "
            "answer, and two rows for the same attack means a pass and an objection can both "
            "be present with nothing deciding which one counts"
        )
        raise VerdictError(message)

    objections = [item for item in results if item.objected]
    soft = [item for item in objections if item.attack is SOFT_ATTACK]
    hard = [item for item in objections if item.attack is not SOFT_ATTACK]
    ordered = tuple(results)

    if soft:
        return AuditVerdict(
            verdict=Verdict.UNREPRESENTATIVE,
            subject=Subject.FINDING,
            detail="\n".join(item.detail for item in soft),
            results=ordered,
        )
    if hard:
        return AuditVerdict(
            verdict=Verdict.UNSOUND,
            subject=Subject.FINDING,
            detail="\n".join(item.detail for item in hard),
            results=ordered,
        )

    missing = [item for item in results if not item.answered]
    absent = [attack for attack in Attack if attack not in seen]
    if missing or absent:
        named = [f"{item.attack.value}: {item.detail}" for item in missing]
        named.extend(f"{attack.value}: no result was reported at all" for attack in absent)
        return AuditVerdict(
            verdict=Verdict.INCONCLUSIVE,
            subject=Subject.FINDING,
            detail=(
                "No attack objected, and the audit is not complete. Until these run, "
                "`sound` would mean *nothing objected among the ones we tried*:\n  "
                + "\n  ".join(named)
            ),
            results=ordered,
        )

    return AuditVerdict(verdict=Verdict.SOUND, subject=Subject.FINDING, results=ordered)


class Route(StrEnum):
    """Where the run goes next. One per verdict, except where budget decides."""

    REPAIR = "proceed to repair"
    INVESTIGATE = "return to investigate, with the objection in context"
    NEXT_FINDING = "skip to the next finding, with no repair spend"
    REPORT = "report the null result — it is the answer, not the absence of one"
    ESCALATE = "escalate to a human, with the history"


@dataclass(frozen=True)
class Routing:
    """The verdict, where it sends the run, and why that rather than the obvious.

    `because` is not decoration. Two of the five routes are reached from more
    than one verdict — `ESCALATE` from an unsound finding with no budget and from
    an incomplete audit — and a reader who cannot tell those apart cannot act on
    either.
    """

    route: Route
    verdict: AuditVerdict
    because: str

    @property
    def spends_repair(self) -> bool:
        """AC 2's premise, and S-9.7's: which routes reach the Surgeon."""
        return self.route is Route.REPAIR

    @property
    def disposition(self) -> Disposition | None:
        """S-5.4's vocabulary for the routes that end this finding's run.

        `ESCALATE` maps onto the disposition `PHASE_CAPS` already gives the
        finding audit, so a caller that wrote a handler against §7.2's four
        dispositions does not need a second one here. The routes that continue
        the run map to none — a disposition is what running out means, and these
        have not run out.
        """
        return Disposition.ESCALATE if self.route is Route.ESCALATE else None

    def describe(self) -> str:
        return f"{self.verdict.describe()}\n  Next: {self.route.value}\n  Why: {self.because}"


def route(
    verdict: AuditVerdict,
    budget: Budget,
    finding_id: str | None = None,
) -> Routing:
    """Where an audited finding goes. AC 2.

    **The budget condition is the whole of ADR 094's amendment.** `unsound`
    originally returned to investigate unconditionally; against an agent that
    declined to stop 60 times out of 60, an audit whose only lever is *run more
    experiments* makes the one failure that spike actually measured worse. So the
    return is conditional on the investigation having budget left, and with none
    it escalates — which is what `Disposition.ESCALATE` already means and what
    `PHASE_CAPS` already says the finding audit does when it runs out.

    The budget is **read, not spent**: `remaining` is a question, and this
    function authorizes nothing. Routing back to investigate does not itself
    consume an experiment, and charging one here would make the forty-experiment
    cap a thirty-something cap for exactly the reason S-8.9 found the last time a
    caller counted somebody else's unit.
    """
    if verdict.verdict is Verdict.SOUND:
        return Routing(
            route=Route.REPAIR,
            verdict=verdict,
            because="every attack answered and none of them landed",
        )

    if verdict.verdict is Verdict.UNREPRESENTATIVE:
        return Routing(
            route=Route.NEXT_FINDING,
            verdict=verdict,
            because=(
                "no experiment can make a workload one that users exercise, so returning to "
                "investigate would spend the budget on a better answer about the wrong subject. "
                "Nobody sees a finding that was skipped: overturn this if the reason does not "
                "convince you"
            ),
        )

    if verdict.verdict is Verdict.NEGATIVE_SOUND:
        return Routing(
            route=Route.REPORT,
            verdict=verdict,
            because=(
                "`00-BRIEF.md` §9 ships a null result as output. This investigation is finished "
                "and its exclusions are the answer; returning it to investigate would be asking "
                "for a finding that the evidence says is not there"
            ),
        )

    if verdict.verdict is Verdict.INCONCLUSIVE:
        return Routing(
            route=Route.ESCALATE,
            verdict=verdict,
            because=(
                "what is missing is an attack, not a measurement. More experiments cannot "
                "complete an audit that did not run, and asking for them would add spend to "
                "close a gap somewhere else"
            ),
        )

    left = budget.remaining(Phase.INVESTIGATE, finding_id)
    if left > 0:
        return Routing(
            route=Route.INVESTIGATE,
            verdict=verdict,
            because=(
                f"the objection names an experiment worth running and this finding has {left} "
                "of its 40 experiments left"
            ),
        )
    return Routing(
        route=Route.ESCALATE,
        verdict=verdict,
        because=(
            "the objection stands and the investigation has no experiments left to answer it. "
            "ADR 094: an audit whose only lever is *run more experiments* makes non-termination "
            "worse, so the budget bounds the loop and a human sees the objection instead"
        ),
    )


def calls_made(calls: Sequence[ModelCall]) -> int:
    """How many model calls an audit actually made. AC 3.

    Counts `ModelCall` records rather than `StepOutcome`s, because S-5.6's
    cascade makes up to three calls inside one step and a count of steps would
    report a third of the bill — S-8.9's finding, which was the same arithmetic
    in the other direction.
    """
    return len(calls)


def refuse_overspend(calls: Sequence[ModelCall]) -> None:
    """Refuse an audit that has spent more calls than AC 3 allows.

    Raises:
        VerdictError: the ceiling is reached. Checked rather than assumed
            because the design's two calls are a property of *which* attacks
            need a model, and a later attack that needs one would move the
            figure without anything noticing.
    """
    made = calls_made(calls)
    if made >= AUDIT_CALL_CEILING:
        message = (
            f"this audit has made {made} model calls against a ceiling of {AUDIT_CALL_CEILING}. "
            "`08-audit.md` §4 justifies the finding audit by its cost against a ~50-call repair "
            "phase, and an audit that costs what it saves is not worth running"
        )
        raise VerdictError(message)


def authorize_round(budget: Budget, finding_id: str | None = None) -> None:
    """Refuse a third round of the finding audit, before it spends anything.

    **The cap this enforces has been decorative since S-5.4.**
    `Phase.FINDING_AUDIT`'s cap is two *rounds*, and S-8.9 made `Session.run`
    record a step only where the phase's cap counts steps — correctly, because a
    round is six attacks and a call is not a round. Nothing else counted rounds,
    so the counter stayed at zero, `authorize` compared zero against two on every
    call, and an audit could have run forever. Whoever owns the unit counts the
    unit, and a round of this phase begins and ends here.

    Called before the round rather than after, for S-5.4's reason: a check after
    the work reports a breach instead of preventing one. It also has to be
    separate from `Session.run`'s authorization, which only happens if a round
    makes a model call — four of the six attacks are arithmetic, so a round that
    objects on those alone would slip past a cap enforced at the API boundary.

    No worst case is passed, because the euro ceiling is enforced per call by
    `Session.run` against that call's own tokens; a figure invented here would be
    a guess standing in front of a measurement.

    Raises:
        BudgetExhaustedError: both rounds are spent. Its disposition is
            `ESCALATE`, which is what `PHASE_CAPS` already says this phase does.
    """
    budget.authorize(Phase.FINDING_AUDIT, finding_id)


def record_round(
    budget: Budget,
    verdict: AuditVerdict,
    finding_id: str | None = None,
) -> None:
    """Count one completed round against the cap `authorize_round` reads.

    The verdict is passed as the stall conclusion. With the default `stall_after`
    of 3 against a cap of 2 the cap always fires first, so this cannot stall a
    default run — but `Budget` accepts a `stall_after` of 2, at which point two
    audits reaching the same verdict is a stall, and passing `None` would clear
    the run of repeats rather than extend it.

    Raises:
        ProgressStalledError: the configured run of identical verdicts was hit.
    """
    budget.record_step(Phase.FINDING_AUDIT, finding_id, conclusion=verdict.verdict.value)

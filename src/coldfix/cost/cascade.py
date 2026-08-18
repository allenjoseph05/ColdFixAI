"""Try the cheaper model, check mechanically, escalate — and log how often.

Epic 5, S-5.6. `04-cost.md` §3 calls this the technique that makes *no quality
loss* honest rather than aspirational: where a machine can catch a wrong cheap
answer, the cheap model costs only an occasional retry.

**AC 4 is not a separate rule.** *No cascading on hypothesis generation or attack
design* falls out of AC 1 — cascade is available exactly where a deterministic
validator exists, and §3's table records that those two have none. So there is no
special case for them in this module; `cascade` refuses any step type whose
`mechanical_check` is `None`, and those two are the step types that qualify.
Writing it as a separate rule would have made it a rule somebody could forget to
apply, which for this one is `CLAUDE.md`'s standing non-negotiable.

**A caller may not supply the missing validator.** Handing `cascade` a check for
hypothesis generation is refused rather than accepted, because §3's table is the
statement that no *deterministic* check exists — a caller-supplied one is a
judgement wearing a validator's clothes, and accepting it routes the one step
nothing can verify onto a cheap model. If a real validator is ever built, §3
changes, which is a code change and a recorded decision.

**The cascade starts where S-5.5 routes and escalates one rung dearer.** That is
what reproduces §12.3's engineered case exactly: repair is listed there as
*cascade mid→frontier*, and repair's mechanical work routes to mid, so escalation
lands on frontier. It also keeps S-5.5's AC 4 true — mechanical work is never
*routed* to the frontier tier; it only ever *reaches* it after failing its own
validator twice.

**§3 specifies a fifth thing the acceptance criteria omit.** *If a step escalates
more than ~30% of the time, promote it permanently.* A step type escalating a
third of the time is paying two cheap attempts plus a dear one on most calls,
which costs more than starting dear. `promotion_candidates` is that rule, and the
log is deliberately two-sided: a step type that has never escalated across many
attempts is as worth knowing about, because a validator that cannot fail is a
cascade that is not checking anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from coldfix.cost.accounting import AccountingError, Phase
from coldfix.cost.routing import STEP_KINDS, Router, StepType, Tier

# `04-cost.md` §3: *2 cheap attempts, then strong.* Two rather than one because a
# cheap model failing once is ordinary and retrying is nearly free; two rather
# than three because a third attempt costs more than the escalation it defers.
CHEAP_ATTEMPTS = 2

# §3's promotion rule, which the acceptance criteria leave out. Above this, the
# two cheap attempts plus the dear one cost more than starting dear.
PROMOTION_THRESHOLD = Decimal("0.30")

# Below this many attempts a rate is noise: one escalation out of one attempt is
# 100% and means nothing. S-4.2's rule for a ratio with no denominator worth
# dividing by — report `None`, never a number somebody will act on.
MINIMUM_SAMPLES = 10


class CascadeError(AccountingError):
    """A cascade could not be run, or was refused."""


class NoValidatorError(CascadeError):
    """This step type has no deterministic check, so nothing may be retried cheaply.

    `CLAUDE.md`'s non-negotiable and §3's two *none exists* rows. A wrong cheap
    hypothesis is caught by nothing and wastes an entire investigation branch,
    which costs far more than the model upgrade that would have prevented it.
    """


class NoDearerTierError(CascadeError):
    """The step failed its own validator on the dearest model available.

    Raised rather than returning the last failing result. A cascade that handed
    back an answer its validator rejected would make the validator decorative,
    and the caller cannot tell a validated result from an unvalidated one by
    looking at it.
    """


class Resolution(StrEnum):
    """How a cascade finished."""

    CHEAP = "validated on the routed tier"
    ESCALATED = "escalated, then validated"


@dataclass(frozen=True)
class Attempt:
    """One try at one tier, and whether its validator accepted the result."""

    tier: Tier
    model: str
    accepted: bool


@dataclass(frozen=True)
class Cascaded[T]:
    """A validated result, and what it took to get one.

    The attempts travel with the value because the escalation rate is computed
    from them and because a result that took three tries is a different fact
    about the step type from one that took one.
    """

    value: T
    step_type: StepType
    attempts: tuple[Attempt, ...]
    resolution: Resolution

    @property
    def escalated(self) -> bool:
        return self.resolution is Resolution.ESCALATED

    @property
    def model(self) -> str:
        """The model that produced the accepted result."""
        return self.attempts[-1].model


@dataclass(frozen=True)
class StepStatistics:
    """What one step type's cascade has cost so far. AC 3's unit.

    Per **step type** rather than per phase or per class, because that is the
    unit §3's promotion rule is written in and the unit a validator belongs to.
    """

    step_type: StepType
    cascades: int
    escalations: int

    @property
    def escalation_rate(self) -> Decimal | None:
        """`None` below `MINIMUM_SAMPLES`, rather than a number.

        One escalation out of one attempt is 100%, and promoting a step type on
        that would move it to the dearest model on a coin flip. The same rule
        S-4.2 applies to a ratio whose denominator is too small to divide by.
        """
        if self.cascades < MINIMUM_SAMPLES:
            return None
        return Decimal(self.escalations) / Decimal(self.cascades)

    @property
    def should_promote(self) -> bool:
        """§3's rule: above ~30%, starting dear is cheaper than cascading."""
        rate = self.escalation_rate
        return rate is not None and rate > PROMOTION_THRESHOLD

    def describe(self) -> str:
        rate = self.escalation_rate
        if rate is None:
            return (
                f"{self.step_type.value}: {self.escalations}/{self.cascades} escalated — too few "
                f"to rate (needs {MINIMUM_SAMPLES})"
            )
        verdict = " — promote it permanently" if self.should_promote else ""
        return f"{self.step_type.value}: {rate:.0%} of {self.cascades} escalated{verdict}"


@dataclass
class EscalationLog:
    """Escalation counts per step type. AC 3.

    Append-only in the same weak sense as S-5.3's ledger: the counts are what a
    promotion decision rests on, and a decision made from numbers somebody edited
    is one nobody can check.
    """

    cascades: dict[StepType, int] = field(default_factory=dict)
    escalations: dict[StepType, int] = field(default_factory=dict)

    def record(self, outcome: Cascaded[object]) -> None:
        step_type = outcome.step_type
        self.cascades[step_type] = self.cascades.get(step_type, 0) + 1
        if outcome.escalated:
            self.escalations[step_type] = self.escalations.get(step_type, 0) + 1

    def statistics(self, step_type: StepType) -> StepStatistics:
        return StepStatistics(
            step_type=step_type,
            cascades=self.cascades.get(step_type, 0),
            escalations=self.escalations.get(step_type, 0),
        )

    def all_statistics(self) -> Sequence[StepStatistics]:
        return [self.statistics(step_type) for step_type in sorted(self.cascades, key=str)]

    def promotion_candidates(self) -> Sequence[StepStatistics]:
        """Step types §3 says to move to the dearer model permanently."""
        return [statistics for statistics in self.all_statistics() if statistics.should_promote]

    def never_escalated(self) -> Sequence[StepStatistics]:
        """Step types that have never escalated across enough attempts to notice.

        The other side of the same log, and worth reading: a validator that has
        never rejected anything is either a step the cheap model genuinely
        handles — the result this technique is for — or a check that cannot fail,
        which is a cascade that is not checking anything. The log cannot tell
        those apart, and says so by reporting the number rather than a verdict.
        """
        return [
            statistics
            for statistics in self.all_statistics()
            if statistics.escalations == 0 and statistics.cascades >= MINIMUM_SAMPLES
        ]

    def report(self) -> str:
        if not self.cascades:
            return "Cascades: none run."
        lines = ["Cascades, by step type:"]
        lines.extend(f"  {statistics.describe()}" for statistics in self.all_statistics())
        return "\n".join(lines)


def dearer_than(tier: Tier) -> Tier | None:
    """The next tier up, or `None` at the top."""
    ordered = sorted(Tier, key=lambda item: item.rank)
    index = ordered.index(tier)
    return ordered[index + 1] if index + 1 < len(ordered) else None


def cascade[T](  # noqa: PLR0913 - the attempt and the check are two halves of one
    # argument and splitting them into an object would be an abstraction with a
    # single implementation; the router, phase and log are the context S-5.5 and
    # AC 3 require, and defaulting any of them would let a call site skip it.
    step_type: StepType,
    *,
    attempt: Callable[[str], T],
    validate: Callable[[T], bool],
    router: Router,
    phase: Phase | None = None,
    log: EscalationLog | None = None,
) -> Cascaded[T]:
    """Try the routed tier twice, check, and escalate one rung on failure.

    `attempt` is given a model id and returns a result. `validate` is the
    deterministic check §3 names for this step type — the caller supplies the
    implementation, but only for a step type the table says has one.

    Raises:
        NoValidatorError: this step type has no deterministic check, so it may
            not be cascaded at any price.
        NoDearerTierError: the result failed its validator on the dearest tier
            available, so there is nothing left to escalate to.
    """
    kind = STEP_KINDS[step_type]
    if not kind.cascade_safe:
        message = (
            f"{step_type.value} has no deterministic validator (`04-cost.md` §3), so it cannot "
            "cascade and no supplied check makes it able to. A wrong cheap answer here is caught "
            "by nothing and wastes an entire investigation branch, which costs far more than the "
            "model upgrade it saved"
        )
        raise NoValidatorError(message)

    routed = router.tier_for(kind.step_class, phase)
    attempts: list[Attempt] = []

    for _ in range(CHEAP_ATTEMPTS):
        model = router.tier_models[routed]
        result = attempt(model)
        accepted = validate(result)
        attempts.append(Attempt(tier=routed, model=model, accepted=accepted))
        if accepted:
            return _finish(step_type, result, attempts, Resolution.CHEAP, log)

    dearer = dearer_than(routed)
    if dearer is None:
        message = (
            f"{step_type.value} failed {kind.mechanical_check!r} on {router.tier_models[routed]} "
            f"after {CHEAP_ATTEMPTS} attempts, and the {routed.value} tier is the dearest "
            "configured — there is nothing to escalate to. Returning the failing result would "
            "make the validator decorative"
        )
        raise NoDearerTierError(message)

    model = router.tier_models[dearer]
    result = attempt(model)
    accepted = validate(result)
    attempts.append(Attempt(tier=dearer, model=model, accepted=accepted))
    if not accepted:
        message = (
            f"{step_type.value} failed {kind.mechanical_check!r} on {model} as well, after "
            f"{CHEAP_ATTEMPTS} attempts on {router.tier_models[routed]}. The step is not a "
            "routing problem"
        )
        raise NoDearerTierError(message)

    return _finish(step_type, result, attempts, Resolution.ESCALATED, log)


def _finish[T](
    step_type: StepType,
    value: T,
    attempts: list[Attempt],
    resolution: Resolution,
    log: EscalationLog | None,
) -> Cascaded[T]:
    outcome = Cascaded(
        value=value, step_type=step_type, attempts=tuple(attempts), resolution=resolution
    )
    if log is not None:
        log.record(outcome)
    return outcome


def cascadable() -> Mapping[StepType, str]:
    """Every step type that may cascade, with the check that makes it safe.

    Enumerable rather than something a caller has to derive, because the two
    absentees are the ones `CLAUDE.md` names and a list is easier to check
    against §3 than a predicate.
    """
    return {
        step_type: kind.mechanical_check
        for step_type, kind in STEP_KINDS.items()
        if kind.mechanical_check is not None
    }

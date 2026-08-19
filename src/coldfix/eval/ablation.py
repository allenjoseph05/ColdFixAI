"""Whether the Adversary earns its cost, and the arithmetic that could say it does not.

Epic 11, S-11.8. *Runs a set of findings with and without the Adversary. Counts
bad patches reaching a human in each condition. Reports the delta. Repeats at two
model tiers to test whether the mid tier misses attack classes.*

`00-BRIEF.md` §5 states the stake in one sentence: **if the delta is small, cut it
— it would be theatre.** Step 11 is called the contribution of the whole project,
and this is the study that is allowed to conclude the contribution is not one.

**A module written to measure its own epic has an obvious failure mode**, and
every decision here is against it. `CUT` is reachable, has its own tests, and is
returned by the same function that returns `KEEP` — there is no separate
"negative" path anybody could forget to call.

**Blocking everything is a perfect catch rate and is worthless.** This is the
measurement the study exists to get right. An Adversary that objects to every
patch catches every bad one, and the naive count — *bad patches reaching a human*,
which is what AC 2 asks for — would show it in the best possible light. So the
sound patches are counted too, and an arm whose over-blocking rate is at least its
catch rate is `CUT` however many bad patches it stopped. The AC's number alone
cannot distinguish an audit from a wall.

**The counterfactual is structural, not measured.** Without the Adversary, every
patch that satisfied the Surgeon's own gate reaches a human unflagged; there is
nothing else standing between them. So the *without* arm needs no run at all —
it is `len(cases)`, by construction, and running the pipeline a second time with
the Adversary disabled would produce that number at the cost of the corpus.
Recording it as an assumption rather than a measurement is the honest form, and it
is why the paired test collapses: the Adversary can only ever reduce the count, so
the question is not *did it help on this corpus* but *does this corpus say
anything about the next one*.

That is a sampling question, so the interval is a Wilson score interval on the
catch rate, computed here in stdlib arithmetic (ADR 015). A point estimate with no
interval would let a corpus of three decide an epic.

**Two tiers, and the interesting output is not the delta.** AC 4 asks whether the
mid tier *misses attack classes*, which is a question about coverage rather than
about counts: two arms can catch the same number of bad patches while one of them
is blind to an entire class. `missed_classes` answers that directly and is the
part of this study that would change what gets routed where.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from coldfix.audit.patchverdict import Attack, Verdict
from coldfix.cost.routing import Tier

MINIMUM_PER_LABEL = 10
"""Cases of each label below which this study concludes nothing.

Chosen against what the interval can do rather than picked: at ten, one case moves
a rate by ten points and the 95% Wilson interval still spans more than half the
unit interval. **Ten is the floor, not a target** — a study that reaches a verdict
at exactly ten has reached a weak one, and `describe` says so. Thirty per label is
where the interval narrows enough to separate a working Adversary from a
mediocre one."""

CONFIDENCE_Z = 1.96
"""Two-sided 95%. Stated as the constant it is, because every interval in this
module widens or narrows with it and a reader comparing two studies needs to know
they used the same one."""


class AblationError(Exception):
    """The ablation could not be computed."""


class Label(StrEnum):
    """What is actually true of a patch, established outside this system.

    **Ground truth, and this module cannot produce it.** A corpus whose labels
    came from the Adversary would be the Adversary marking its own work, and every
    number here would be a tautology. The labels come from a human or from a
    planted defect whose fix is known.
    """

    SOUND = "the patch is correct"
    BAD = "the patch is wrong"


@dataclass(frozen=True)
class Case:
    """One patch in the corpus, with what is true of it."""

    id: str
    label: Label
    exemplifies: Attack | None = None
    """Which attack class *should* catch this, for a bad patch. Required when
    `BAD`, because AC 4's question — does the mid tier miss a class — cannot be
    asked of cases that do not say which class they belong to."""

    def __post_init__(self) -> None:
        if self.label is Label.BAD and self.exemplifies is None:
            message = (
                f"bad case {self.id!r} does not say which attack class should catch it. AC 4 "
                "asks whether a tier misses a class, and a corpus that does not label its "
                "cases by class can only answer how many, never which"
            )
            raise AblationError(message)
        if self.label is Label.SOUND and self.exemplifies is not None:
            message = (
                f"sound case {self.id!r} names an attack class. A correct patch exemplifies no "
                "cheat, and labelling one would put it in the denominator of a coverage figure "
                "it cannot belong to"
            )
            raise AblationError(message)


@dataclass(frozen=True)
class Observation:
    """What the patch audit said about one case, at one tier."""

    case: Case
    tier: Tier
    verdict: Verdict
    eur: Decimal = Decimal("0")
    """What this audit cost. `00-BRIEF.md` §6 lists euros per confirmed finding as
    an evaluation metric; the ablation's version is euros per bad patch stopped."""

    @property
    def reached_a_human_unflagged(self) -> bool:
        """Whether a person would see this patch presented as ready.

        `suspicious` reaches a human too — that is what §4.4 says it does — but it
        arrives *with the concern stated*, which is the difference this study is
        measuring. Counting it as unflagged would score the Adversary's escalation
        as a failure to catch anything.
        """
        return self.verdict is Verdict.CLEAN

    @property
    def stopped(self) -> bool:
        return not self.reached_a_human_unflagged


@dataclass(frozen=True)
class Arm:
    """One tier's results over the whole corpus."""

    tier: Tier
    observations: tuple[Observation, ...]

    @property
    def bad(self) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.case.label is Label.BAD)

    @property
    def sound(self) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.case.label is Label.SOUND)

    @property
    def bad_stopped(self) -> int:
        """The value. AC 2's delta is this number."""
        return sum(1 for item in self.bad if item.stopped)

    @property
    def sound_stopped(self) -> int:
        """The cost, and the number a naive study omits."""
        return sum(1 for item in self.sound if item.stopped)

    @property
    def bad_reaching_a_human(self) -> int:
        """AC 2, with the Adversary in place."""
        return len(self.bad) - self.bad_stopped

    @property
    def catch_rate(self) -> float | None:
        return self.bad_stopped / len(self.bad) if self.bad else None

    @property
    def overblock_rate(self) -> float | None:
        """How often a correct patch was stopped. **An Adversary that objects to
        everything has a catch rate of 1.0 and is a wall, not an audit.**"""
        return self.sound_stopped / len(self.sound) if self.sound else None

    @property
    def catch_interval(self) -> tuple[float, float] | None:
        """A 95% Wilson interval on the catch rate. `None` where nothing was bad."""
        return wilson(self.bad_stopped, len(self.bad)) if self.bad else None

    @property
    def spent(self) -> Decimal:
        return sum((item.eur for item in self.observations), Decimal("0"))

    @property
    def eur_per_bad_patch_stopped(self) -> Decimal | None:
        """`None` where it stopped none — a cost per unit of nothing is undefined,
        and reporting the whole spend as the price of zero catches reads as a
        number."""
        if self.bad_stopped == 0:
            return None
        return self.spent / self.bad_stopped

    @property
    def classes_caught(self) -> frozenset[Attack]:
        """Which attack classes this tier stopped at least one bad patch from."""
        return frozenset(
            item.case.exemplifies
            for item in self.bad
            if item.stopped and item.case.exemplifies is not None
        )

    @property
    def classes_present(self) -> frozenset[Attack]:
        return frozenset(
            item.case.exemplifies for item in self.bad if item.case.exemplifies is not None
        )

    def describe(self) -> str:
        interval = self.catch_interval
        band = f" (95% CI {interval[0]:.0%} to {interval[1]:.0%})" if interval else ""
        catch = "n/a" if self.catch_rate is None else f"{self.catch_rate:.0%}"
        over = "n/a" if self.overblock_rate is None else f"{self.overblock_rate:.0%}"
        cost = self.eur_per_bad_patch_stopped
        price = "no bad patch stopped" if cost is None else f"€{cost:.2f} each"
        return (
            f"  {self.tier.value}: stopped {self.bad_stopped}/{len(self.bad)} bad ({catch}"
            f"{band}) and {self.sound_stopped}/{len(self.sound)} sound ({over}); "
            f"{self.bad_reaching_a_human} bad patches still reached a human; {price}"
        )


class Recommendation(StrEnum):
    """What this study concludes. Three, and the third is not a hedge.

    A corpus too small or too one-sided to separate a working Adversary from a
    wall has not established either answer, and reporting `KEEP` from it would be
    the epic marking its own homework on a sample that could not have said
    otherwise.
    """

    KEEP = "the Adversary stops bad patches at a rate its over-blocking does not explain"
    CUT = "the delta does not justify the cost; `00-BRIEF.md` §5 says cut it"
    NOT_ESTABLISHED = "this corpus cannot separate the two, whatever the counts say"


@dataclass(frozen=True)
class Ablation:
    """The whole study. **AC 1 to AC 4.**"""

    cases: tuple[Case, ...]
    arms: tuple[Arm, ...]

    def __post_init__(self) -> None:
        if not self.arms:
            message = "an ablation with no arms measured nothing and would report a delta of zero"
            raise AblationError(message)
        seen = [arm.tier for arm in self.arms]
        if len(set(seen)) != len(seen):
            message = f"two arms for the same tier: {sorted(item.value for item in seen)}"
            raise AblationError(message)
        for arm in self.arms:
            covered = {item.case.id for item in arm.observations}
            expected = {case.id for case in self.cases}
            if covered != expected:
                missing = sorted(expected - covered)
                message = (
                    f"the {arm.tier.value} arm covers {len(covered)} of {len(expected)} cases. "
                    f"Arms over different corpora cannot be compared. Missing: {missing}"
                )
                raise AblationError(message)

    @property
    def without_adversary(self) -> int:
        """**AC 1's other condition, and it needs no run.**

        Without the Adversary every patch that satisfied the Surgeon's own gate
        reaches a human unflagged — there is nothing else in the way. So this is
        the count of bad cases, by construction, and spending the corpus a second
        time to observe it would buy a number already known.
        """
        return sum(1 for case in self.cases if case.label is Label.BAD)

    def arm(self, tier: Tier) -> Arm:
        for item in self.arms:
            if item.tier is tier:
                return item
        message = f"no arm at {tier.value}; this study ran {[a.tier.value for a in self.arms]}"
        raise AblationError(message)

    @property
    def best(self) -> Arm:
        """The strongest tier that was run — the arm the recommendation is about."""
        return max(self.arms, key=lambda arm: arm.tier.rank)

    def delta(self, tier: Tier) -> int:
        """**AC 3.** Bad patches that reach a human without the Adversary, minus
        those that reach one with it."""
        return self.without_adversary - self.arm(tier).bad_reaching_a_human

    @property
    def missed_classes(self) -> dict[Tier, frozenset[Attack]]:
        """**AC 4.** Which classes each weaker tier failed to catch that the best
        tier did.

        The question AC 4 actually asks, and it is about *coverage* rather than
        counts: two arms can stop the same number of bad patches while one of them
        is blind to a whole class, and only this reads that off.
        """
        strongest = self.best
        return {
            arm.tier: strongest.classes_caught - arm.classes_caught
            for arm in self.arms
            if arm.tier is not strongest.tier
        }

    @property
    def underpowered(self) -> bool:
        """Whether either label is too thin for the interval to mean anything."""
        bad = self.without_adversary
        sound = len(self.cases) - bad
        return bad < MINIMUM_PER_LABEL or sound < MINIMUM_PER_LABEL

    @property
    def recommendation(self) -> Recommendation:
        """**Where this study is allowed to say the epic was not worth building.**

        Three tests, and the last two are the ones a study written by the people
        who built the thing would omit:

        1. an underpowered corpus establishes nothing, whichever way the counts
           fall;
        2. an arm whose over-blocking rate is at least its catch rate is not
           discriminating — it stops bad patches because it stops patches — and
           the count AC 2 asks for cannot tell that apart from an audit that
           works;
        3. **an edge too small for this corpus to establish is not an edge.** The
           lower bound of the catch rate has to clear the over-blocking rate, not
           merely the point estimate: 12 of 20 caught against 10 of 20 blocked is
           a ten-point lead whose interval runs from 39% to 78%, and a corpus that
           could as easily have produced the opposite ordering has not shown one.

        **The third test replaced a dead one.** It was originally *the interval
        reaches zero*, which cannot happen: a Wilson lower bound is zero only when
        nothing was caught, and nothing caught means a catch rate of zero, which
        test 2 has already returned `CUT` for. A sabotage deleting the branch
        changed no outcome because no input could reach it.

        The counts are divided here rather than read off `Arm`'s optional
        properties, because `underpowered` has already ruled out both empty
        denominators — and routing through `None` afterwards would add a second
        unreachable branch in place of the one just removed.
        """
        if self.underpowered:
            return Recommendation.NOT_ESTABLISHED

        arm = self.best
        catch = arm.bad_stopped / len(arm.bad)
        over = arm.sound_stopped / len(arm.sound)
        if over >= catch:
            return Recommendation.CUT
        if wilson(arm.bad_stopped, len(arm.bad))[0] <= over:
            return Recommendation.CUT
        return Recommendation.KEEP

    def describe(self) -> str:
        lines = [
            f"ADVERSARY ABLATION — {len(self.cases)} cases, "
            f"{self.without_adversary} of them bad, {len(self.arms)} tiers.",
            f"  Without the Adversary: {self.without_adversary} bad patches reach a human "
            "unflagged, because nothing else is in the way.",
        ]
        lines.extend(arm.describe() for arm in sorted(self.arms, key=lambda a: -a.tier.rank))
        lines.append(f"  Delta at {self.best.tier.value}: {self.delta(self.best.tier)}")

        for tier, missed in sorted(self.missed_classes.items(), key=lambda item: item[0].value):
            if missed:
                names = ", ".join(sorted(item.name.lower() for item in missed))
                lines.append(
                    f"  **{tier.value} is blind to {names}** — classes the "
                    f"{self.best.tier.value} tier caught and it did not. That is a routing "
                    "decision, not a score."
                )
            else:
                lines.append(f"  {tier.value} caught every class the {self.best.tier.value} did.")

        lines.append(f"  {self.recommendation.value}")
        if self.recommendation is Recommendation.NOT_ESTABLISHED:
            lines.append(
                f"    Fewer than {MINIMUM_PER_LABEL} cases of one label. The interval spans too "
                "much of the range to separate a working Adversary from one that objects to "
                "everything."
            )
        elif self.underpowered or len(self.cases) < 2 * MINIMUM_PER_LABEL + 10:
            lines.append(
                "    This is a weak study. Thirty cases per label is where the interval narrows "
                "enough to separate a working Adversary from a mediocre one."
            )
        return "\n".join(lines)


def wilson(successes: int, trials: int, *, z: float = CONFIDENCE_Z) -> tuple[float, float]:
    """A Wilson score interval, in stdlib arithmetic.

    Wilson rather than the normal approximation because the normal one is wrong
    exactly where this study lives: at rates near 0 or 1 and at small n it produces
    bounds outside the unit interval, and a lower bound below zero would make the
    *interval reaches zero* test — the one that can return `CUT` — impossible to
    fail.

    Raises:
        AblationError: more successes than trials, or no trials.
    """
    if trials <= 0:
        message = "an interval over no trials"
        raise AblationError(message)
    if not 0 <= successes <= trials:
        message = f"{successes} successes in {trials} trials"
        raise AblationError(message)

    rate = successes / trials
    denominator = 1 + z**2 / trials
    centre = (rate + z**2 / (2 * trials)) / denominator
    spread = z / denominator * math.sqrt(rate * (1 - rate) / trials + z**2 / (4 * trials**2))
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def study(cases: Sequence[Case], observations: Sequence[Observation]) -> Ablation:
    """Assemble one ablation from a corpus and what each tier said about it.

    Runs nothing. The observations come from the harness, which is `CLAUDE.md`'s
    rule about measurement and also the only way this study can be re-run against
    recorded results — an ablation that drove the pipeline itself could not be
    checked without spending the corpus again.

    Raises:
        AblationError: the corpus is empty, two cases share an id, or an arm does
            not cover it.
    """
    if not cases:
        message = (
            "an ablation over an empty corpus. Every count is zero and the delta is zero, "
            "which is the shape of an Adversary that does nothing"
        )
        raise AblationError(message)

    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        repeated = sorted({item for item in ids if ids.count(item) > 1})
        message = f"two cases share an id: {repeated}. Which label applies is undefined"
        raise AblationError(message)

    by_tier: dict[Tier, list[Observation]] = {}
    for item in observations:
        by_tier.setdefault(item.tier, []).append(item)

    return Ablation(
        cases=tuple(cases),
        arms=tuple(
            Arm(tier=tier, observations=tuple(found))
            for tier, found in sorted(by_tier.items(), key=lambda entry: entry[0].rank)
        ),
    )

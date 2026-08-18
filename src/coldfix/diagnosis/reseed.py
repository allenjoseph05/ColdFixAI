"""Asking for different data, and paying for the question.

Epic 8, S-8.8. `08-audit.md` names this as a **capability gap** rather than a
defect: *the Diagnostician cannot request new fixtures. If it suspects a
skew-dependent defect, it has no way to ask for skewed data.* Everything needed
to answer that already exists — S-7.7 builds a fixture at a chosen shape and
S-8.5 decides what a changed condition reopens. What was missing is the doorway
between them, and the two guards that stop it being a way around the budget and
around the exclusion rules.

**A reseed is the other half of S-8.5.** That story made an exclusion conditional
so it *could* be reopened; this one is the only thing in the system that changes a
condition on purpose. Without it, `Conditions` moves only when somebody rebuilds
the world by hand, and *may be reopened* is a property nothing exercises.

**A reseed that changes no condition is refused.** It would reopen nothing,
establish nothing, and cost an experiment out of forty — and the agent asking for
it has misunderstood what it is for. This is the same guard S-8.5 put on
`reopen`, pointing the other way: there, an exclusion may not be set aside
without a condition having moved; here, a condition may not be *claimed* to have
moved without moving.

**The conditions change only after the seeding succeeded, and the order is the
whole correctness argument.** If they were updated first and the seeder then
failed, every exclusion whose shape they mention would be reopened against a
fixture that was never built. The agent would re-run an experiment believing the
world had changed, get the same answer, and record it as new evidence — which is
worse than the gap this story closes, because it manufactures a reason to
disbelieve a correct exclusion. Found by asking what S-8.4's build-then-append
finding looks like one module over.

**The cost is authorized before and recorded after.** AC 3 counts a reseed
against the experiment budget, and S-5.4 already has both halves: `authorize`
refuses when the cap is reached — before the work, because *cost is known once a
call returns, so a check afterwards reports a breach rather than preventing one*
— and `record_step` counts it. No money changes hands; what a reseed spends is
one of the forty experiments a finding gets.

**Who decides to reseed is not settled here.** This story builds the tool and its
guards; wiring it to a model's tool call is E12's, and S-8.7 drew the same line.
So `Investigation.reseed` is callable and enforced, and nothing in Epic 8 makes a
model choose to call it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from coldfix.cost.accounting import Phase
from coldfix.cost.budget import Budget
from coldfix.diagnosis.exclusions import Conditions, Dimension, Exclusion, ExclusionRegister
from coldfix.screening.workload import FixtureRecipe

# What the harness does with a recipe: build the fixture and leave the subject
# holding it. **This module seeds nothing itself** — S-7.6 and S-7.7 own that,
# and a second seeding path here would be a second statement of how data is made.
type Seeder = Callable[[FixtureRecipe], None]


class ReseedError(Exception):
    """The fixtures could not be rebuilt, or the request was not worth making."""


class PointlessReseedError(ReseedError):
    """The requested fixture is the one already in place.

    Refused rather than performed, because it reopens nothing, establishes
    nothing, and costs one of the forty experiments a finding gets. Carries both
    sets of conditions so the caller can see they are the same rather than being
    told so.
    """

    def __init__(self, conditions: Conditions) -> None:
        self.conditions = conditions
        super().__init__(
            f"this reseed would leave every condition where it is ({conditions.describe()}). "
            "Nothing would be reopened by it and it would cost an experiment — a reseed is how a "
            "condition moves, so one that moves none is a question with no answer in it"
        )


@dataclass(frozen=True)
class Reseeding:
    """What a reseed changed, and what that reopened. AC 2's answer."""

    recipe: FixtureRecipe
    before: Conditions
    after: Conditions
    reopened: tuple[Exclusion, ...]

    def describe(self) -> str:
        if not self.reopened:
            return (
                f"Reseeded to {self.recipe.distribution.value}: {self.after.describe()}. "
                "No exclusion was established under conditions this changes, so nothing reopened."
            )
        lines = [
            f"Reseeded to {self.recipe.distribution.value}: {self.after.describe()}.",
            f"{len(self.reopened)} exclusion(s) reopened by it:",
        ]
        lines.extend(f"  - {item.hypothesis}" for item in self.reopened)
        return "\n".join(lines)


def conditions_after(
    current: Conditions, recipe: FixtureRecipe, scales: Sequence[float]
) -> Conditions:
    """The conditions a reseed to `recipe` would put in force.

    Shape and scale come from the request; platform and concurrency are carried
    forward, because reseeding changes the data and not the machine or the load.
    Reading them off the current conditions rather than asking for them again is
    what stops a reseed silently claiming a platform nobody moved to.
    """
    return Conditions.of(
        fixture_shape=recipe.distribution.value,
        platform=str(current.observed[Dimension.PLATFORM].values[0]),
        concurrency=[float(value) for value in current.observed[Dimension.CONCURRENCY].values],
        scales=list(scales),
    )


def reseed(  # noqa: PLR0913 - the register, the conditions and the budget are
    # three different things a reseed touches, and the recipe and seeder are the
    # request and the means. None is derivable from the others.
    *,
    recipe: FixtureRecipe,
    scales: Sequence[float],
    current: Conditions,
    register: ExclusionRegister,
    seeder: Seeder,
    budget: Budget,
    finding_id: str | None = None,
) -> Reseeding:
    """Rebuild the fixtures at a chosen shape, and report what that reopened.

    Raises:
        PointlessReseedError: the request would leave every condition unchanged.
        BudgetExhaustedError: the experiment cap is already reached.
        ReseedError: the seeder could not build the fixture. **The conditions are
            unchanged in this case**, which is the point of doing the work before
            adopting them.
    """
    after = conditions_after(current, recipe, scales)
    if not current.drift_from(after) and not after.drift_from(current):
        raise PointlessReseedError(current)

    # Before the work. S-5.4: a check afterwards reports a breach rather than
    # preventing one. The worst case is zero euros — a reseed spends an
    # experiment, not tokens.
    budget.authorize(Phase.INVESTIGATE, finding_id)

    try:
        seeder(recipe)
    except Exception as error:
        message = (
            f"the fixtures could not be rebuilt as {recipe.distribution.value}: {error}. The "
            "conditions are unchanged, because adopting them here would reopen exclusions "
            "against a fixture that was never built — and the agent would then re-run an "
            "experiment believing the world had moved"
        )
        raise ReseedError(message) from error

    reopened = register.stale(after)
    budget.record_step(
        Phase.INVESTIGATE,
        finding_id,
        conclusion=f"reseed:{recipe.distribution.value}:{recipe.digest()}",
    )
    return Reseeding(recipe=recipe, before=current, after=after, reopened=reopened)

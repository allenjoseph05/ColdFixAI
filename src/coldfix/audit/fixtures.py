"""Whether the data's shape could have hidden the answer, and what to seed instead.

Epic 9, S-9.3. *Assesses whether fixture shape could have hidden the real cause.
Can request a re-run under different fixture shape.*

**The second criterion is the one with teeth.** Every audit story before this can
only object. This one can **ask for an experiment**, which is the first time the
finding audit causes work rather than judging it — and that makes it the story
where ADR 094's warning applies most directly: an audit whose lever is *run more
experiments* worsens the one failure S-0.8 actually measured.

So the capability is deliberately split. **This module produces a request; it
cannot perform one.** There is no seeder parameter and no call into S-8.8 —
executing a request goes through `reseed`, which authorizes against the
experiment cap before it seeds anything. An auditor that could seed directly
would be doing the harness's job and spending budget nobody authorized.

**A request is only made when it would change something.** S-8.8 refuses a reseed
that moves no condition; this refuses to *ask* for one. Two guards on the same
waste at different layers, and this is the cheaper one — it costs nothing,
whereas S-8.8's refusal arrives after a caller has already decided to spend.

**Which shape to ask for is derived, not chosen.** `LONG_TAIL` first, because
S-3.3 records it as *the deliberate worst case for any per-parent cost — the one
that turns milliseconds into minutes for a single request while every other
request stays fast*, and its signature is bimodal rather than smooth. If a long
tail has already been swept, `POWER_LAW` is the remaining shape. If all three
have been, there is nothing to ask for and the answer is that the shape did not
hide anything.

**How this differs from S-9.2.** That story audits an *exclusion* — was this
particular thing ruled out under adequate conditions — and reports narrowness.
This one audits the *investigation's fixture* against the cause it claims or
failed to find, and produces something executable. They agree about uniform being
blind, and they should: both read it off the same `Σ k²` argument.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.diagnosis.exclusions import Conditions, Dimension
from coldfix.primitives.scaling import Distribution
from coldfix.screening.workload import FixtureRecipe

PREFERRED_ORDER = (Distribution.LONG_TAIL, Distribution.POWER_LAW, Distribution.UNIFORM)
"""Which shape to ask for first, and why that order.

`LONG_TAIL` leads because S-3.3 calls it *the deliberate worst case for any
per-parent cost* — bimodal, a handful of parents holding almost everything. If
a per-parent cost exists, that is the shape that shows it. `UNIFORM` is last
because it is the one an investigation almost always already ran, and because
`Σ k²` makes it the blindest."""


class Hiding(StrEnum):
    """How the fixture's shape could have concealed the answer."""

    UNIFORM_MASKS_PER_PARENT = (
        "the data was uniform, which minimizes Σk² and is therefore the blindest "
        "shape for any per-parent cost"
    )
    SHAPE_NEVER_VARIED = (
        "only one shape was ever seeded, so nothing here distinguishes a cost that "
        "depends on size from one that depends on skew"
    )


@dataclass(frozen=True)
class ReseedRequest:
    """An experiment the audit is asking for, and what it expects it to settle.

    A **request**, not an action. S-8.8 executes one and charges it against the
    experiment cap; nothing here can seed anything.
    """

    recipe: FixtureRecipe
    because: str

    @property
    def shape(self) -> Distribution:
        return self.recipe.distribution

    def describe(self) -> str:
        return (
            f"Re-run under {self.shape.value} fixtures ({self.because}). "
            f"This costs one experiment and reopens every exclusion recorded under the "
            f"shape it replaces."
        )


@dataclass(frozen=True)
class FixtureAudit:
    """What the fixture's shape could have hidden, and what to do about it."""

    shapes_tested: tuple[str, ...]
    could_hide: tuple[Hiding, ...]
    request: ReseedRequest | None
    """`None` when no shape remains to ask for — either every shape was swept, or
    asking would change nothing. Never `None` merely because the audit found no
    objection; the two are reported separately."""

    @property
    def adequate(self) -> bool:
        return not self.could_hide

    def describe(self) -> str:
        tested = ", ".join(self.shapes_tested)
        if self.adequate:
            return (
                f"Fixture shapes tested: {tested}. Nothing about the shape of the data "
                "could have hidden a per-parent cost."
            )
        lines = [f"Fixture shapes tested: {tested}. The shape could have hidden the cause:"]
        lines.extend(f"  - {item.value}" for item in self.could_hide)
        lines.append(
            f"  {self.request.describe()}"
            if self.request is not None
            else "  No shape remains to ask for."
        )
        return "\n".join(lines)


def assess_fixture(conditions: Conditions, recipe: FixtureRecipe) -> FixtureAudit:
    """Whether the shape could have hidden the cause, and what to seed instead.

    `recipe` is the fixture that was actually used, and the request is that same
    recipe with a different distribution — same entity, same size, same source.
    Changing only the shape is what makes the re-run comparable: S-3.3's
    `allocate` spends the same total over the same parents, so the only
    difference between the two measurements is skew.
    """
    tested = tuple(str(value) for value in conditions.observed[Dimension.FIXTURE_SHAPE].values)

    could_hide: list[Hiding] = []
    if len(tested) == 1:
        if tested[0] == Distribution.UNIFORM.value:
            could_hide.append(Hiding.UNIFORM_MASKS_PER_PARENT)
        could_hide.append(Hiding.SHAPE_NEVER_VARIED)

    return FixtureAudit(
        shapes_tested=tested,
        could_hide=tuple(could_hide),
        request=_request_for(tested, recipe),
    )


def _request_for(tested: Sequence[str], recipe: FixtureRecipe) -> ReseedRequest | None:
    """The next shape worth seeding, or `None` when asking would change nothing.

    Refusing to ask is as important as asking. S-8.8 already refuses a reseed
    that moves no condition; requesting one anyway would spend a round of the
    audit's own budget producing an instruction that is going to be rejected.
    """
    for shape in PREFERRED_ORDER:
        if shape.value not in tested:
            return ReseedRequest(
                recipe=recipe.model_copy(update={"distribution": shape}),
                because=_WHY[shape],
            )
    return None


_WHY: dict[Distribution, str] = {
    Distribution.LONG_TAIL: (
        "a long tail is the deliberate worst case for a per-parent cost — a handful of "
        "parents holding almost everything, which is what turns milliseconds into minutes "
        "for one request while every other request stays fast"
    ),
    Distribution.POWER_LAW: (
        "a power law is what naturally occurring popularity looks like, and it is the "
        "smooth spectrum a long tail's bimodal shape does not cover"
    ),
    Distribution.UNIFORM: (
        "uniform is the remaining shape, though it is the blindest one and is unlikely "
        "to reveal anything the others did not"
    ),
}

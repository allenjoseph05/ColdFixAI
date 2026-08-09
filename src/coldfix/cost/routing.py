"""Which model a step runs on, and the one direction configuration may not move it.

Epic 5, S-5.5. `04-cost.md` puts ~30 calls per run on the frontier model and ~220
on something cheaper, and §12.3's engineered case turns that split into most of
the 60x gap between the worst case and the affordable one.

**`creative` and `mechanical` are not opinions.** §3 lists eight step types and,
against each, the mechanical check that would catch a wrong cheap answer:

| Step | Mechanical check | Cascade safe? |
|---|---|---|
| Explorer action | command exit code | yes |
| Ablation stub | does it execute | yes |
| Patch | test suite passes | yes |
| Falsification test | fails on unpatched code | yes |
| Evidence chain | schema requires a measurement | yes |
| Attack execution | outputs differ or they do not | yes |
| **Hypothesis generation** | **none exists** | **no** |
| **Attack design** | **none exists** | **no** |

So the class is a *property of the step type* — creative exactly where no
validator exists — and a call site that declares the wrong one is refused rather
than believed. AC 1 asks the call site to declare; `08-audit.md` F6 is the reason
the declaration is also checked.

**AC 3 and S-5.4's note point in opposite directions, and both are right.** A cap
must be in code because configuration that can raise it defeats it — the harm is
unbounded spend. A tier assignment must be configurable because a cheaper model
arriving is the normal case and should not need a release. What separates them is
which direction the harm runs, so this module is asymmetric in the mirror image
of S-5.4's: **configuration may move any step to a dearer tier, and may never
move a creative step below the frontier.** `CLAUDE.md` makes that a
non-negotiable — *never cascade to a cheap model on hypothesis generation or
attack design; no deterministic validator exists for those* — and a bad
hypothesis wastes an entire investigation branch, which costs far more than the
model upgrade it saved.

**Naming a tier "frontier" does not make it one.** The models behind the tiers
are configurable, so a configuration could put the cheapest model in the frontier
tier and satisfy every rule above while defeating all of them. The tiers are
therefore checked against S-5.3's price book and must be **ordered by price** —
frontier at least as dear as mid, mid at least as dear as cheap. A tier is what
it costs, not what it is called.

**Step class alone under-specifies §12.3's engineered case.** Grounding's
mechanical calls run on the cheap model with a mature playbook while the
investigate loop's mechanical calls run mid-tier — two mechanical steps, two
tiers, distinguished by phase. AC 2 says *routing maps step class to model tier*,
and routing that literally cannot express the cost model the project's own
arithmetic rests on. So a route is keyed on the phase as well, falling back to the
class alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Any

from coldfix.cost.accounting import AccountingError, Phase, StepClass, price_of


class RoutingError(AccountingError):
    """A step could not be routed, or a routing was refused."""


class UnsafeRoutingError(RoutingError):
    """A configuration tried to send creative work to a cheaper model.

    `CLAUDE.md`'s non-negotiable, enforced here rather than described: no
    deterministic validator exists for hypothesis generation or attack design, so
    a wrong cheap answer is not caught by anything and costs an entire
    investigation branch. This is the one direction configuration may not move.
    """


class MisdeclaredStepError(RoutingError):
    """A call site declared a class its step type does not have.

    §3's table decides which steps have a mechanical check. A step type with no
    validator is creative whatever the call site says, and believing the
    declaration would let a caller route hypothesis generation to a cheap model
    by mislabelling it — the exact thing `UnsafeRoutingError` exists to prevent,
    reached through the front door.
    """


class Tier(StrEnum):
    """A price band, not a model. Ordered, and the order is checked against cost."""

    CHEAP = "cheap"
    MID = "mid"
    FRONTIER = "frontier"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK: Mapping[Tier, int] = {Tier.CHEAP: 0, Tier.MID: 1, Tier.FRONTIER: 2}


class StepType(StrEnum):
    """§3's eight rows. The unit a mechanical check is defined against."""

    EXPLORER_ACTION = "explorer action"
    ABLATION_STUB = "ablation stub"
    PATCH = "patch"
    FALSIFICATION_TEST = "falsification test"
    EVIDENCE_CHAIN = "evidence chain"
    ATTACK_EXECUTION = "attack execution"
    HYPOTHESIS_GENERATION = "hypothesis generation"
    ATTACK_DESIGN = "attack design"


# §3's table, verbatim, including the check that would catch a wrong cheap
# answer. The check is recorded rather than reduced to a boolean because
# `cascade_safe` without it is a claim nobody can audit — S-5.6 needs to know
# *what* validates a step before it may retry one, and a step whose named check
# turns out not to exist is a routing decision made on a fiction.
@dataclass(frozen=True)
class StepKind:
    step_type: StepType
    mechanical_check: str | None
    """`None` where §3 records *none exists*. That is what makes a step creative."""

    @property
    def cascade_safe(self) -> bool:
        return self.mechanical_check is not None

    @property
    def step_class(self) -> StepClass:
        """The class, derived. Creative exactly where no validator exists."""
        return StepClass.MECHANICAL if self.cascade_safe else StepClass.CREATIVE


STEP_KINDS: Mapping[StepType, StepKind] = {
    StepType.EXPLORER_ACTION: StepKind(StepType.EXPLORER_ACTION, "command exit code"),
    StepType.ABLATION_STUB: StepKind(StepType.ABLATION_STUB, "does it execute"),
    StepType.PATCH: StepKind(StepType.PATCH, "test suite passes"),
    StepType.FALSIFICATION_TEST: StepKind(StepType.FALSIFICATION_TEST, "fails on unpatched code"),
    StepType.EVIDENCE_CHAIN: StepKind(StepType.EVIDENCE_CHAIN, "schema requires a measurement"),
    StepType.ATTACK_EXECUTION: StepKind(StepType.ATTACK_EXECUTION, "outputs differ or they do not"),
    StepType.HYPOTHESIS_GENERATION: StepKind(StepType.HYPOTHESIS_GENERATION, None),
    StepType.ATTACK_DESIGN: StepKind(StepType.ATTACK_DESIGN, None),
}


def classify(step_type: StepType) -> StepClass:
    """The class §3's table gives this step type."""
    return STEP_KINDS[step_type].step_class


def check_declaration(step_type: StepType, declared: StepClass) -> None:
    """Refuse a call site whose declared class disagrees with §3's table.

    Raises:
        MisdeclaredStepError: the declaration and the table disagree.
    """
    actual = classify(step_type)
    if declared is actual:
        return

    kind = STEP_KINDS[step_type]
    because = (
        f"its mechanical check is {kind.mechanical_check!r}"
        if kind.cascade_safe
        else "no mechanical check exists for it"
    )
    message = (
        f"{step_type.value} was declared {declared.value} and is {actual.value}, because "
        f"{because} (`04-cost.md` §3). A declaration that overrode the table would let a caller "
        "route work with no validator to a cheap model by relabelling it"
    )
    raise MisdeclaredStepError(message)


# Which model backs each tier. Configurable — a cheaper model arriving is the
# normal case and must not need a release — and checked against S-5.3's price
# book, both for existence and for order.
DEFAULT_TIER_MODELS: Mapping[Tier, str] = {
    Tier.FRONTIER: "claude-opus-5",
    Tier.MID: "claude-sonnet-5",
    Tier.CHEAP: "claude-haiku-4-5",
}

# The class-level default. Creative goes frontier because nothing can catch it
# being wrong; mechanical goes mid because §12.3's investigate loop does.
DEFAULT_TIERS: Mapping[StepClass, Tier] = {
    StepClass.CREATIVE: Tier.FRONTIER,
    StepClass.MECHANICAL: Tier.MID,
}

# Where §12.3 routes a phase away from its class default. Grounding's mechanical
# calls run on the cheap model with a mature playbook — ten calls at $0.01 for
# the whole phase — which the class alone cannot express.
DEFAULT_PHASE_TIERS: Mapping[tuple[Phase, StepClass], Tier] = {
    (Phase.GROUND, StepClass.MECHANICAL): Tier.CHEAP,
}


@dataclass(frozen=True)
class Router:
    """Step class and phase in, a model out — with the unsafe direction refused.

    Every field is configurable, which is AC 3. What is not configurable is the
    *direction*: a creative step cannot be routed below the frontier tier, and a
    tier cannot be backed by a model cheaper than the tier beneath it.
    """

    tier_models: Mapping[Tier, str] = field(default_factory=lambda: dict(DEFAULT_TIER_MODELS))
    tiers: Mapping[StepClass, Tier] = field(default_factory=lambda: dict(DEFAULT_TIERS))
    phase_tiers: Mapping[tuple[Phase, StepClass], Tier] = field(
        default_factory=lambda: dict(DEFAULT_PHASE_TIERS)
    )

    def __post_init__(self) -> None:
        missing = sorted(tier.value for tier in Tier if tier not in self.tier_models)
        if missing:
            message = f"no model is configured for these tiers: {missing}"
            raise RoutingError(message)

        # Every tier must name a model somebody can price. A routing that named
        # an unpriceable model would produce a run whose cost is unknown, which
        # is the one thing S-5.3 exists to prevent.
        priced = {tier: price_of(model) for tier, model in self.tier_models.items()}

        ordered = sorted(Tier, key=lambda tier: tier.rank)
        for cheaper, dearer in pairwise(ordered):
            if priced[dearer].input_usd < priced[cheaper].input_usd:
                message = (
                    f"the {dearer.value} tier is configured with {self.tier_models[dearer]!r} at "
                    f"${priced[dearer].input_usd}/MTok, which is cheaper than the "
                    f"{cheaper.value} tier's {self.tier_models[cheaper]!r} at "
                    f"${priced[cheaper].input_usd}. A tier is what it costs, not what it is "
                    "called — and naming the cheapest model 'frontier' would satisfy every other "
                    "rule here while defeating all of them"
                )
                raise UnsafeRoutingError(message)

        routes: list[tuple[str, StepClass, Tier]] = [
            (step_class.value, step_class, tier) for step_class, tier in self.tiers.items()
        ]
        routes += [
            (f"{phase.value}/{step_class.value}", step_class, tier)
            for (phase, step_class), tier in self.phase_tiers.items()
        ]
        for where, step_class, tier in routes:
            if step_class is StepClass.CREATIVE and tier.rank < Tier.FRONTIER.rank:
                message = (
                    f"{where} routes creative work to the {tier.value} tier. Hypothesis "
                    "generation and attack design have no deterministic validator (`04-cost.md` "
                    "§3), so a wrong cheap answer is caught by nothing and costs an entire "
                    "investigation branch. Configuration may route a step dearer, never cheaper "
                    "than the frontier for creative work"
                )
                raise UnsafeRoutingError(message)

    def tier_for(self, step_class: StepClass, phase: Phase | None = None) -> Tier:
        """Which price band this step runs in.

        Phase first, class second. Two mechanical steps in different phases go to
        different tiers in §12.3's engineered case, and a router that could not
        express that could not implement the project's own cost model.
        """
        if phase is not None and (phase, step_class) in self.phase_tiers:
            return self.phase_tiers[(phase, step_class)]
        return self.tiers[step_class]

    def model_for(self, step_class: StepClass, phase: Phase | None = None) -> str:
        """The model this step runs on.

        **`step_class` has no default.** AC 1 asks every call site to declare, and
        a default would let a call site decline to — which is how the ~220
        mechanical calls a run makes end up on the frontier model without anybody
        choosing that.
        """
        return self.tier_models[self.tier_for(step_class, phase)]

    def route(self, step_type: StepType, phase: Phase | None = None) -> str:
        """The model for a step type, with its class taken from §3 rather than asked for.

        The form to prefer at a call site that knows what it is doing: the class
        is derived, so it cannot be misdeclared.
        """
        return self.model_for(classify(step_type), phase)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Router:
        """Build a router from plain data — AC 3's *without code changes*.

        Takes what a YAML or JSON file parses to, so a deployment can move a tier
        without a release. Every guard in `__post_init__` applies to the result,
        which is what keeps *configurable* from meaning *unconstrained*.

        Raises:
            RoutingError: the configuration is malformed.
            UnsafeRoutingError: it would route creative work below the frontier,
                or leave the tiers out of price order.
            UnknownModelError: it names a model with no published price.
        """
        try:
            models = {Tier(name): model for name, model in config.get("tier_models", {}).items()}
            tiers = {StepClass(name): Tier(tier) for name, tier in config.get("tiers", {}).items()}
            phase_tiers = {
                (Phase(entry["phase"]), StepClass(entry["step_class"])): Tier(entry["tier"])
                for entry in config.get("phase_tiers", [])
            }
        except (KeyError, ValueError, AttributeError, TypeError) as error:
            message = f"this routing configuration could not be read: {error}"
            raise RoutingError(message) from error

        return cls(
            tier_models={**DEFAULT_TIER_MODELS, **models},
            tiers={**DEFAULT_TIERS, **tiers},
            phase_tiers={**DEFAULT_PHASE_TIERS, **phase_tiers},
        )

    def describe(self) -> str:
        """Every route this router will take, for a run report.

        Enumerated rather than left implicit: the whole point of the story is a
        30/220 split, and a split nobody can read is one nobody checks.
        """
        lines = []
        for tier in sorted(Tier, key=lambda tier: -tier.rank):
            model = self.tier_models[tier]
            lines.append(f"  {tier.value}: {model} (${price_of(model).input_usd}/MTok in)")
        for step_class in sorted(StepClass, key=lambda item: item.value):
            lines.append(f"  {step_class.value} -> {self.tiers[step_class].value}")
        for (phase, step_class), tier in sorted(
            self.phase_tiers.items(), key=lambda item: (item[0][0].value, item[0][1].value)
        ):
            lines.append(f"  {phase.value}/{step_class.value} -> {tier.value}")
        return "Routing:\n" + "\n".join(lines)


def frontier_share(router: Router, calls: Mapping[tuple[Phase, StepClass], int]) -> Decimal:
    """What fraction of a run's calls land on the frontier tier.

    The story's *why* stated as a number a run report can carry: ~30 of ~250
    calls should need the frontier model. A share that drifts upward is the
    routing quietly stopping.
    """
    total = sum(calls.values())
    if total == 0:
        return Decimal(0)
    frontier = sum(
        count
        for (phase, step_class), count in calls.items()
        if router.tier_for(step_class, phase) is Tier.FRONTIER
    )
    return Decimal(frontier) / Decimal(total)

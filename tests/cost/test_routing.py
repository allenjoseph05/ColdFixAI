"""S-5.5: the class is not an opinion, and configuration has one forbidden direction.

`04-cost.md` puts ~30 calls per run on the frontier model and ~220 elsewhere, so
the tests that matter are the ones about the boundary rather than the lookup.
Three things carry the story:

- **`creative` is a property, not a declaration.** §3's table says which steps
  have a mechanical check, and a call site that mislabels one would route work
  with no validator to a cheap model through the front door.
- **Configuration may route dearer, never cheaper.** S-5.4's caps are asymmetric
  the other way round, and the reason is which direction the harm runs.
- **A tier is what it costs.** The models behind the tiers are configurable, so
  calling the cheapest model "frontier" would satisfy every other rule here while
  defeating all of them.

AC 4 is the last section, plus the override it deliberately still permits.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from coldfix.cost.accounting import Phase, StepClass, UnknownModelError
from coldfix.cost.routing import (
    DEFAULT_TIER_MODELS,
    STEP_KINDS,
    MisdeclaredStepError,
    Router,
    RoutingError,
    StepType,
    Tier,
    UnsafeRoutingError,
    check_declaration,
    classify,
    frontier_share,
)

CREATIVE_STEPS = (StepType.HYPOTHESIS_GENERATION, StepType.ATTACK_DESIGN)


# ------------------------------------ the class follows from §3's table (AC 1)


@pytest.mark.parametrize("step_type", CREATIVE_STEPS)
def test_a_step_with_no_validator_is_creative(step_type: StepType) -> None:
    """§3's two rows that read *none exists*, and the reason the rule exists."""
    assert STEP_KINDS[step_type].mechanical_check is None
    assert classify(step_type) is StepClass.CREATIVE


@pytest.mark.parametrize(
    "step_type",
    [step for step in StepType if step not in CREATIVE_STEPS],
)
def test_a_step_with_a_named_check_is_mechanical(step_type: StepType) -> None:
    """The other six rows. The check is recorded, not just a boolean — S-5.6
    needs to know *what* validates a step before it may retry one."""
    assert STEP_KINDS[step_type].mechanical_check
    assert classify(step_type) is StepClass.MECHANICAL


def test_declaring_hypothesis_generation_mechanical_is_refused() -> None:
    """AC 1 asks the call site to declare; F6 is why the declaration is checked.

    Believed, this declaration is the whole non-negotiable defeated through the
    front door: relabel the one step nothing can validate, and the router will
    happily send it to the cheap model.
    """
    with pytest.raises(MisdeclaredStepError, match="no mechanical check exists"):
        check_declaration(StepType.HYPOTHESIS_GENERATION, StepClass.MECHANICAL)


def test_declaring_a_validated_step_creative_is_also_refused() -> None:
    """The other direction, which costs money rather than correctness — and is
    still a disagreement with the table, so it is still wrong."""
    with pytest.raises(MisdeclaredStepError, match="test suite passes"):
        check_declaration(StepType.PATCH, StepClass.CREATIVE)


def test_a_correct_declaration_passes() -> None:
    """The control. A checker that refused everything would pass both tests
    above while making every call site unusable."""
    check_declaration(StepType.PATCH, StepClass.MECHANICAL)
    check_declaration(StepType.ATTACK_DESIGN, StepClass.CREATIVE)


def test_every_step_type_has_a_recorded_kind() -> None:
    """§3's table is the source of truth, so a step type missing from it would
    be one whose cascade safety nobody decided."""
    assert set(STEP_KINDS) == set(StepType)


# ------------------------------------------- routing maps class to tier (AC 2)


def test_creative_work_routes_to_the_frontier_tier() -> None:
    router = Router()

    assert router.tier_for(StepClass.CREATIVE) is Tier.FRONTIER
    assert router.model_for(StepClass.CREATIVE) == "claude-opus-5"


def test_mechanical_work_routes_below_the_frontier() -> None:
    router = Router()

    assert router.tier_for(StepClass.MECHANICAL) is Tier.MID
    assert router.model_for(StepClass.MECHANICAL) == "claude-sonnet-5"


def test_grounding_routes_cheaper_than_the_investigate_loop() -> None:
    """§12.3's engineered case, which step class alone cannot express.

    Grounding's mechanical calls run on the cheap model with a mature playbook —
    ten calls at $0.01 for the whole phase — while the investigate loop's
    mechanical calls run mid-tier. Two mechanical steps, two tiers.
    """
    router = Router()

    assert router.tier_for(StepClass.MECHANICAL, Phase.GROUND) is Tier.CHEAP
    assert router.tier_for(StepClass.MECHANICAL, Phase.INVESTIGATE) is Tier.MID


def test_routing_a_step_type_derives_its_class_rather_than_asking() -> None:
    """The form to prefer where the call site knows what it is doing: a derived
    class cannot be misdeclared."""
    router = Router()

    assert router.route(StepType.HYPOTHESIS_GENERATION) == "claude-opus-5"
    assert router.route(StepType.EXPLORER_ACTION, Phase.GROUND) == "claude-haiku-4-5"


def test_a_call_site_cannot_decline_to_declare() -> None:
    """AC 1, as the thing that must be impossible.

    A default would let a call site say nothing, and the ~220 mechanical calls a
    run makes would end up on the frontier model without anybody choosing it.
    """
    with pytest.raises(TypeError):
        Router().model_for()  # type: ignore[call-arg]


# --------------------------- configuration may route dearer, never cheaper (AC 3)


def test_a_tier_can_be_repointed_at_another_model_without_code_changes() -> None:
    """AC 3. A cheaper model arriving is the normal case, not a release."""
    router = Router.from_config({"tier_models": {"mid": "claude-opus-4-8"}})

    assert router.model_for(StepClass.MECHANICAL) == "claude-opus-4-8"


def test_configuration_can_route_a_mechanical_step_dearer() -> None:
    """The safe direction. It costs money and cannot cost correctness, which is
    why AC 4 says mechanical avoids the frontier *by default* rather than
    always."""
    router = Router.from_config({"tiers": {"mechanical": "frontier"}})

    assert router.tier_for(StepClass.MECHANICAL) is Tier.FRONTIER


def test_configuration_cannot_route_creative_work_below_the_frontier() -> None:
    """`CLAUDE.md`'s non-negotiable, enforced rather than described.

    No validator exists for hypothesis generation, so a wrong cheap answer is
    caught by nothing and costs an entire investigation branch — far more than
    the model upgrade it saved.
    """
    with pytest.raises(UnsafeRoutingError, match="no deterministic validator"):
        Router.from_config({"tiers": {"creative": "cheap"}})


def test_configuration_cannot_route_creative_work_cheaper_for_one_phase_either() -> None:
    """The same rule against the more specific key, which is where a rule
    enforced only on the general one would be walked around."""
    with pytest.raises(UnsafeRoutingError, match="no deterministic validator"):
        Router.from_config(
            {"phase_tiers": [{"phase": "repair", "step_class": "creative", "tier": "mid"}]}
        )


def test_the_asymmetry_mirrors_the_budget_caps() -> None:
    """S-5.4 permits lowering a cap and refuses raising it; this permits routing
    dearer and refuses routing creative work cheaper. Both allow the direction
    that costs money and refuse the one that costs correctness."""
    dearer = Router.from_config({"tiers": {"mechanical": "frontier"}})

    assert dearer.tier_for(StepClass.MECHANICAL) is Tier.FRONTIER
    with pytest.raises(UnsafeRoutingError):
        Router.from_config({"tiers": {"creative": "mid"}})


def test_a_malformed_configuration_is_refused_rather_than_partly_applied() -> None:
    """Half a routing configuration is a routing nobody wrote."""
    with pytest.raises(RoutingError, match="could not be read"):
        Router.from_config({"tiers": {"imaginative": "frontier"}})


@pytest.mark.parametrize(
    "config",
    [
        {"tier_models": {"frontier": "claude-opus-9"}},
        {"tier_models": {"cheap": "gpt-4"}},
    ],
)
def test_a_tier_pointed_at_an_unpriceable_model_is_refused(config: dict[str, Any]) -> None:
    """A routing that named a model nobody can price would produce a run whose
    cost is unknown, which is the one thing S-5.3 exists to prevent."""
    with pytest.raises(UnknownModelError):
        Router.from_config(config)


# ------------------------------------------- a tier is what it costs, not its name


def test_the_frontier_tier_cannot_be_the_cheapest_model() -> None:
    """The hole every other rule here leaves open.

    Creative work always routes to the tier called *frontier* — so a
    configuration that puts the cheapest model in that tier satisfies every
    other check while defeating all of them. The tiers are checked against
    S-5.3's price book instead.
    """
    with pytest.raises(UnsafeRoutingError, match="what it costs, not what it is called"):
        Router.from_config(
            {
                "tier_models": {
                    "frontier": "claude-haiku-4-5",
                    "mid": "claude-sonnet-5",
                    "cheap": "claude-haiku-4-5",
                }
            }
        )


def test_the_tiers_must_be_ordered_by_price() -> None:
    """Mid dearer than frontier is the same defect one rung down."""
    with pytest.raises(UnsafeRoutingError, match="cheaper than"):
        Router.from_config(
            {"tier_models": {"frontier": "claude-sonnet-5", "mid": "claude-fable-5"}}
        )


def test_tiers_of_equal_price_are_allowed() -> None:
    """Two tiers on the same model is a legitimate deployment — a shop with no
    mid-tier model available — and refusing it would be strictness with nothing
    behind it. Only *inversion* is unsafe."""
    router = Router.from_config({"tier_models": {"mid": "claude-opus-5"}})

    assert router.model_for(StepClass.MECHANICAL) == "claude-opus-5"


def test_a_tier_with_no_model_is_refused() -> None:
    with pytest.raises(RoutingError, match="no model is configured"):
        Router(tier_models={Tier.FRONTIER: "claude-opus-5"})


def test_the_default_tiers_are_in_price_order() -> None:
    """The shipped defaults have to satisfy the rule they enforce."""
    router = Router()

    assert router.tier_models == DEFAULT_TIER_MODELS
    assert "opus" in router.tier_models[Tier.FRONTIER]
    assert "haiku" in router.tier_models[Tier.CHEAP]


# ---------------------------------------------------------------- AC 4


def test_mechanical_steps_never_hit_the_frontier_tier_by_default() -> None:
    """AC 4, stated over every mechanical step type and every phase rather than
    over one example."""
    router = Router()

    for step_type in StepType:
        if classify(step_type) is not StepClass.MECHANICAL:
            continue
        for phase in Phase:
            assert router.tier_for(StepClass.MECHANICAL, phase) is not Tier.FRONTIER
        assert router.route(step_type) != router.tier_models[Tier.FRONTIER]


def test_creative_steps_always_hit_the_frontier_tier_by_default() -> None:
    """The other half, without which AC 4 would be satisfied by a router that
    sent everything to the cheap model."""
    router = Router()

    for step_type in CREATIVE_STEPS:
        for phase in Phase:
            assert router.tier_for(StepClass.CREATIVE, phase) is Tier.FRONTIER
        assert router.route(step_type) == router.tier_models[Tier.FRONTIER]


def test_the_frontier_share_of_a_run_matches_the_story_s_reason() -> None:
    """The *why* line as a number: ~30 of ~250 calls need the frontier model.

    A share that drifts upward is the routing quietly stopping, and a run report
    that carried only a total would never show it.
    """
    calls = {
        (Phase.GROUND, StepClass.MECHANICAL): 10,
        (Phase.INVESTIGATE, StepClass.CREATIVE): 15,
        (Phase.INVESTIGATE, StepClass.MECHANICAL): 105,
        (Phase.REPAIR, StepClass.MECHANICAL): 25,
        (Phase.PATCH_AUDIT, StepClass.CREATIVE): 10,
        (Phase.PATCH_AUDIT, StepClass.MECHANICAL): 30,
    }

    share = frontier_share(Router(), calls)

    assert Decimal("0.10") < share < Decimal("0.20")


def test_an_empty_run_has_no_frontier_share_to_report() -> None:
    assert frontier_share(Router(), {}) == Decimal(0)


def test_the_routing_is_readable_in_a_report() -> None:
    """A 30/220 split nobody can read is one nobody checks."""
    rendered = Router().describe()

    assert "frontier: claude-opus-5" in rendered
    assert "creative -> frontier" in rendered
    assert "ground/mechanical -> cheap" in rendered

"""S-9.3 — whether the data's shape could have hidden the answer.

The second acceptance criterion is the one with teeth: *can request a re-run
under different fixture shape.* Every audit story before this can only object;
this one can ask for an experiment — which makes it the story where ADR 094's
warning applies most directly, since an audit whose lever is *run more
experiments* worsens the failure S-0.8 actually measured.

So the tests spend most of their attention on two things: that a request is only
made when it would change something, and that this module **cannot perform one**.
"""

from __future__ import annotations

import inspect

import pytest

from coldfix.audit import fixtures
from coldfix.audit.fixtures import (
    PREFERRED_ORDER,
    Hiding,
    ReseedRequest,
    assess_fixture,
)
from coldfix.diagnosis.exclusions import Conditions
from coldfix.diagnosis.reseed import conditions_after
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy  # noqa: F401 - kept for parity with workloads
from coldfix.screening.workload import FixtureRecipe

PLATFORM = "x86_64-linux"
SCALES = [10, 100, 1000]


def conditions(*shapes: Distribution) -> Conditions:
    return Conditions.of(
        fixture_shape=[shape.value for shape in shapes],
        platform=PLATFORM,
        concurrency=1,
        scales=SCALES,
    )


def a_recipe(distribution: Distribution = Distribution.UNIFORM) -> FixtureRecipe:
    return FixtureRecipe(
        entity="book",
        per_parent=20,
        parents=1000,
        distribution=distribution,
        source="synthesis from schema",
    )


# ============================== AC 1: could the shape have hidden the cause


def test_a_uniform_only_investigation_could_have_hidden_a_per_parent_cost() -> None:
    """`Σ k²` is minimized exactly when every parent is equal, so uniform is the
    blindest shape for any per-parent cost — the same argument S-9.2 reads, and
    they should agree because it is the same proof."""
    audit = assess_fixture(conditions(Distribution.UNIFORM), a_recipe())

    assert Hiding.UNIFORM_MASKS_PER_PARENT in audit.could_hide
    assert Hiding.SHAPE_NEVER_VARIED in audit.could_hide
    assert not audit.adequate


def test_a_long_tail_only_investigation_still_never_varied_the_shape() -> None:
    """One shape is one shape. What it does not get is the *uniform* objection —
    a long tail is the deliberate worst case, not the blindest."""
    audit = assess_fixture(conditions(Distribution.LONG_TAIL), a_recipe(Distribution.LONG_TAIL))

    assert Hiding.SHAPE_NEVER_VARIED in audit.could_hide
    assert Hiding.UNIFORM_MASKS_PER_PARENT not in audit.could_hide


def test_an_investigation_that_swept_every_shape_is_adequate() -> None:
    """**The control.** An auditor that objected here would be objecting to
    S-3.3's `compare_shapes` doing exactly what it was built to do."""
    audit = assess_fixture(conditions(*Distribution), a_recipe())

    assert audit.adequate
    assert audit.could_hide == ()
    # Asserted on the *positive* sentence, not on the absence of a phrase: the
    # adequate rendering says "Nothing ... could have hidden a per-parent cost",
    # so `"could have hidden" not in ...` fails on the negation. A negative
    # assertion over formatted text is a substring check — this project's own
    # recorded hazard, walked into again.
    assert "Nothing about the shape of the data could have hidden" in audit.describe()


# ============ AC 2: it can ask for a re-run, and asks for the right one


def test_the_request_is_for_a_long_tail_first() -> None:
    """Derived, not chosen. S-3.3: a long tail is *the deliberate worst case for
    any per-parent cost* — a handful of parents holding almost everything — so if
    a per-parent cost exists, that is the shape that shows it."""
    audit = assess_fixture(conditions(Distribution.UNIFORM), a_recipe())

    assert audit.request is not None
    assert audit.request.shape is Distribution.LONG_TAIL
    assert PREFERRED_ORDER[0] is Distribution.LONG_TAIL


def test_the_request_asks_for_the_remaining_shape_when_the_tail_was_swept() -> None:
    audit = assess_fixture(conditions(Distribution.UNIFORM, Distribution.LONG_TAIL), a_recipe())

    assert audit.request is not None
    assert audit.request.shape is Distribution.POWER_LAW


def test_the_request_changes_only_the_shape() -> None:
    """**Same entity, same size, same source.** S-3.3's `allocate` spends the same
    total over the same parents, so changing only the distribution is what makes
    the re-run comparable — a request that also changed the size would produce a
    measurement that differs for two reasons."""
    recipe = a_recipe()

    audit = assess_fixture(conditions(Distribution.UNIFORM), recipe)

    assert audit.request is not None
    asked = audit.request.recipe
    assert asked.distribution is Distribution.LONG_TAIL
    assert asked.entity == recipe.entity
    assert asked.per_parent == recipe.per_parent
    assert asked.parents == recipe.parents
    assert asked.source == recipe.source


def test_no_request_is_made_when_every_shape_has_been_swept() -> None:
    """**Refusing to ask is as important as asking.** S-8.8 refuses a reseed that
    moves no condition; asking for one anyway would spend a round of the audit's
    budget producing an instruction that is going to be rejected."""
    audit = assess_fixture(conditions(*Distribution), a_recipe())

    assert audit.request is None
    assert audit.adequate


def test_the_request_says_what_it_expects_to_settle_and_what_it_costs() -> None:
    audit = assess_fixture(conditions(Distribution.UNIFORM), a_recipe())

    assert audit.request is not None
    described = audit.request.describe()

    assert "long_tail" in described
    assert "costs one experiment" in described
    assert "reopens every exclusion" in described
    assert "milliseconds into minutes" in described


def test_the_report_carries_the_request() -> None:
    """Testing the rendering, not just the data — S-9.2's survivor, applied
    forward."""
    described = assess_fixture(conditions(Distribution.UNIFORM), a_recipe()).describe()

    assert "Re-run under long_tail" in described
    assert "costs one experiment" in described


# ================ the capability is split: it can ask, and it cannot do


def test_this_module_can_request_a_reseed_and_cannot_perform_one() -> None:
    """**The structural half of ADR 094's warning.** Executing a request goes
    through S-8.8's `reseed`, which authorizes against the experiment cap before
    it seeds anything. An auditor that could seed directly would be doing the
    harness's job and spending budget nobody authorized."""
    # Asserted on what the module *imports and takes*, not on words in its prose:
    # the docstring discusses the budget at length, and a substring check over
    # source text cannot tell an explanation from an action.
    imported = {name for name in dir(fixtures) if not name.startswith("_")}

    assert "reseed" not in imported
    assert "Seeder" not in imported
    assert "Budget" not in imported
    assert not any("seed" in name.lower() and name != "ReseedRequest" for name in imported)

    parameters = inspect.signature(assess_fixture).parameters
    assert "seeder" not in parameters
    assert "budget" not in parameters


def test_the_request_is_a_recipe_the_reseed_tool_accepts() -> None:
    """The join: what this produces is exactly what S-8.8 takes, so the auditor's
    ask is executable rather than a sentence somebody has to translate."""
    audit = assess_fixture(conditions(Distribution.UNIFORM), a_recipe())
    assert audit.request is not None

    after = conditions_after(conditions(Distribution.UNIFORM), audit.request.recipe, SCALES)

    assert "fixture shape long_tail" in after.describe()


def test_executing_the_request_would_reopen_what_was_ruled_out_under_uniform() -> None:
    """End to end with S-8.5: the audit asks for a shape, and that shape is
    precisely one that reopens the uniform exclusions — which is why asking is
    worth an experiment."""
    before = conditions(Distribution.UNIFORM)
    audit = assess_fixture(before, a_recipe())
    assert audit.request is not None

    after = conditions_after(before, audit.request.recipe, SCALES)

    assert before.drift_from(after)


def test_a_request_is_a_frozen_record_of_what_was_asked() -> None:
    request = ReseedRequest(recipe=a_recipe(Distribution.LONG_TAIL), because="because")

    with pytest.raises(Exception, match=r"frozen|immutable|cannot assign"):
        request.because = "something else"  # type: ignore[misc]

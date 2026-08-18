"""S-8.8 — asking for different data, and paying for the question.

`08-audit.md` calls this a **capability gap**: *the Diagnostician cannot request
new fixtures. If it suspects a skew-dependent defect, it has no way to ask for
skewed data.* S-7.7 already builds a fixture at a chosen shape and S-8.5 already
decides what a changed condition reopens; what was missing is the doorway, and
the guards that stop it being a way around the budget and around the exclusion
rules.

The two tests that matter most are the ones nobody asked for. A reseed that moves
nothing must be refused, and a reseed that **fails** must leave the conditions
exactly where they were — because reopening an exclusion against a fixture that
was never built manufactures a reason to disbelieve a correct result.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import (
    DEFAULT_STALL_AFTER,
    Budget,
    BudgetExhaustedError,
    ProgressStalledError,
)
from coldfix.cost.session import Session
from coldfix.diagnosis.exclusions import Conditions, ExclusionRegister
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.diagnosis.loop import Investigation
from coldfix.diagnosis.reseed import (
    PointlessReseedError,
    ReseedError,
    conditions_after,
    reseed,
)
from coldfix.llm.client import ReplayingClient
from coldfix.primitives.registry import ProjectProfile, Selection
from coldfix.primitives.scaling import Distribution
from coldfix.screening.workload import FixtureRecipe

SCALES = [10, 100, 1000]
PLATFORM = "x86_64-linux"

UNIFORM = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform=PLATFORM,
    concurrency=1,
    scales=SCALES,
)


def recipe(distribution: Distribution = Distribution.LONG_TAIL) -> FixtureRecipe:
    return FixtureRecipe(
        entity="book",
        per_parent=50,
        parents=100,
        distribution=distribution,
        source="synthesis from schema",
    )


def a_budget() -> Budget:
    return Budget(ledger=Ledger(), rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)))


def register_with_a_uniform_exclusion() -> ExclusionRegister:
    log = ExperimentLog()
    experiment = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000], distribution='uniform')",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7, 7, 7 across a 100x sweep",
    )
    register = ExclusionRegister()
    register.record(experiment, UNIFORM)
    return register


def recorded_seeds() -> tuple[list[FixtureRecipe], object]:
    """A seeder that records what it was asked for."""
    seen: list[FixtureRecipe] = []
    return seen, seen.append


# ================================ AC 1: a fixture can be requested with a shape


def test_a_reseed_asks_the_harness_for_the_shape_it_was_given() -> None:
    """This module seeds nothing itself — S-7.6 and S-7.7 own that, and a second
    seeding path here would be a second statement of how data is made."""
    seen, seeder = recorded_seeds()

    outcome = reseed(
        recipe=recipe(Distribution.LONG_TAIL),
        scales=SCALES,
        current=UNIFORM,
        register=ExclusionRegister(),
        seeder=seeder,  # type: ignore[arg-type]
        budget=a_budget(),
    )

    assert [item.distribution for item in seen] == [Distribution.LONG_TAIL]
    assert outcome.recipe.distribution is Distribution.LONG_TAIL


def test_the_new_conditions_carry_the_shape_and_keep_the_machine() -> None:
    """Reseeding changes the data, not the platform or the load. Reading those
    off the current conditions rather than asking again is what stops a reseed
    silently claiming a platform nobody moved to."""
    after = conditions_after(UNIFORM, recipe(Distribution.POWER_LAW), [10, 100])

    described = after.describe()

    assert "fixture shape power_law" in described
    assert f"platform {PLATFORM}" in described
    assert "concurrency 1" in described
    assert "scale 10 to 100" in described


# =========================== AC 2: reseeding invalidates exclusions per S-8.5


def test_a_reseed_to_a_skewed_fixture_reopens_the_uniform_exclusion() -> None:
    """**The capability gap, closed.** F3's worked example needs somebody to
    actually seed the skew — until this story, `Conditions` moved only when a
    human rebuilt the world, and *may be reopened* was a property nothing in the
    system exercised."""
    register = register_with_a_uniform_exclusion()
    _, seeder = recorded_seeds()

    outcome = reseed(
        recipe=recipe(Distribution.LONG_TAIL),
        scales=SCALES,
        current=UNIFORM,
        register=register,
        seeder=seeder,  # type: ignore[arg-type]
        budget=a_budget(),
    )

    assert len(outcome.reopened) == 1
    assert outcome.reopened[0].hypothesis == "the database is the bottleneck"
    assert "1 exclusion(s) reopened" in outcome.describe()


def test_a_reseed_that_reopens_nothing_says_so_rather_than_implying_it() -> None:
    """The control. A module that reported everything reopened would pass the
    test above while making the register meaningless — S-8.5's pairing, one story
    on. Here the shape moves and there is simply nothing recorded under it."""
    _, seeder = recorded_seeds()

    outcome = reseed(
        recipe=recipe(Distribution.LONG_TAIL),
        scales=SCALES,
        current=UNIFORM,
        register=ExclusionRegister(),
        seeder=seeder,  # type: ignore[arg-type]
        budget=a_budget(),
    )

    assert outcome.reopened == ()
    assert "nothing reopened" in outcome.describe()


def test_an_exclusion_recorded_under_the_new_shape_is_not_reopened_by_it() -> None:
    """The sharper control: reseeding to `long_tail` must not reopen an exclusion
    that was itself established under `long_tail`."""
    log = ExperimentLog()
    experiment = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="r",
        target="t",
        design="d",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat under a long tail too",
    )
    skewed = Conditions.of(
        fixture_shape=Distribution.LONG_TAIL.value,
        platform=PLATFORM,
        concurrency=1,
        scales=SCALES,
    )
    register = ExclusionRegister()
    register.record(experiment, skewed)
    _, seeder = recorded_seeds()

    outcome = reseed(
        recipe=recipe(Distribution.LONG_TAIL),
        scales=SCALES,
        current=UNIFORM,
        register=register,
        seeder=seeder,  # type: ignore[arg-type]
        budget=a_budget(),
    )

    assert outcome.reopened == ()


# ==================================== the two guards the acceptance criteria omit


def test_a_reseed_that_would_move_nothing_is_refused() -> None:
    """**Not in the AC, and the mirror of S-8.5's `reopen`.** There, an exclusion
    may not be set aside without a condition having moved; here, a condition may
    not be claimed to have moved without moving. It would reopen nothing,
    establish nothing, and cost one of the forty experiments a finding gets."""
    seen, seeder = recorded_seeds()
    budget = a_budget()

    with pytest.raises(PointlessReseedError, match="leave every condition where it is"):
        reseed(
            recipe=recipe(Distribution.UNIFORM),
            scales=SCALES,
            current=UNIFORM,
            register=ExclusionRegister(),
            seeder=seeder,  # type: ignore[arg-type]
            budget=budget,
        )

    assert seen == []
    assert budget.used(Phase.INVESTIGATE) == 0


def test_a_reseed_to_the_same_shape_at_a_wider_scale_is_not_pointless() -> None:
    """The control for the guard above. The scale is a condition too, so widening
    the sweep genuinely moves one — refusing this would make the guard a
    prohibition on reseeding at all."""
    _, seeder = recorded_seeds()

    outcome = reseed(
        recipe=recipe(Distribution.UNIFORM),
        scales=[10, 100, 100_000],
        current=UNIFORM,
        register=register_with_a_uniform_exclusion(),
        seeder=seeder,  # type: ignore[arg-type]
        budget=a_budget(),
    )

    assert len(outcome.reopened) == 1


def test_a_failed_reseed_leaves_the_conditions_exactly_where_they_were() -> None:
    """**The correctness argument, and S-8.4's ordering finding one module over.**

    Adopting the conditions before the seeding succeeded would reopen every
    exclusion mentioning the old shape against a fixture that was never built.
    The agent would re-run an experiment believing the world had changed, get the
    same answer, and record it as new evidence — worse than the gap this story
    closes, because it manufactures a reason to disbelieve a correct exclusion.
    """

    def broken(_: FixtureRecipe) -> None:
        message = "no factory can build a long tail for this schema"
        raise RuntimeError(message)

    register = register_with_a_uniform_exclusion()

    with pytest.raises(ReseedError, match="conditions are unchanged"):
        reseed(
            recipe=recipe(Distribution.LONG_TAIL),
            scales=SCALES,
            current=UNIFORM,
            register=register,
            seeder=broken,
            budget=a_budget(),
        )

    # The exclusion is still live, because the world it was established in is
    # still the world.
    assert register.live(UNIFORM)
    assert register.stale(UNIFORM) == ()


def test_a_failed_reseed_reports_what_the_harness_said() -> None:
    """A refusal naming what would fix it beats one that only says *it failed* —
    S-7.4's rule, and the seeder's own message is the diagnosis."""

    def broken(_: FixtureRecipe) -> None:
        message = "no factory can build a long tail for this schema"
        raise RuntimeError(message)

    with pytest.raises(ReseedError, match="no factory can build a long tail"):
        reseed(
            recipe=recipe(),
            scales=SCALES,
            current=UNIFORM,
            register=ExclusionRegister(),
            seeder=broken,
            budget=a_budget(),
        )


# ================================ AC 3: counted against the experiment budget


def test_a_reseed_costs_one_experiment() -> None:
    _, seeder = recorded_seeds()
    budget = a_budget()

    reseed(
        recipe=recipe(),
        scales=SCALES,
        current=UNIFORM,
        register=ExclusionRegister(),
        seeder=seeder,  # type: ignore[arg-type]
        budget=budget,
    )

    assert budget.used(Phase.INVESTIGATE) == 1


def test_a_reseed_is_refused_once_the_experiment_cap_is_reached() -> None:
    """**Authorized before the work, which is the only place a cap can be
    enforced** — S-5.4: cost is known once a call returns, so a check afterwards
    reports a breach rather than preventing one."""
    seen, seeder = recorded_seeds()
    budget = a_budget()
    budget.tighten(Phase.INVESTIGATE, 1)
    budget.record_step(Phase.INVESTIGATE)

    with pytest.raises(BudgetExhaustedError):
        reseed(
            recipe=recipe(),
            scales=SCALES,
            current=UNIFORM,
            register=ExclusionRegister(),
            seeder=seeder,  # type: ignore[arg-type]
            budget=budget,
        )

    assert seen == [], "the fixture was rebuilt after the budget refused it"


def test_a_failed_reseed_is_not_charged_as_an_experiment() -> None:
    """S-5.4's precedent: `Session.run` records a step after the call, so a call
    that raised does not consume one. Safe here because the refusal propagates —
    nothing retries a broken seeder in a loop."""

    def broken(_: FixtureRecipe) -> None:
        message = "cannot build"
        raise RuntimeError(message)

    budget = a_budget()

    with pytest.raises(ReseedError):
        reseed(
            recipe=recipe(),
            scales=SCALES,
            current=UNIFORM,
            register=ExclusionRegister(),
            seeder=broken,
            budget=budget,
        )

    assert budget.used(Phase.INVESTIGATE) == 0


def test_reseeds_to_different_fixtures_do_not_read_as_a_stalled_phase() -> None:
    """**The conclusion carries the recipe digest, and a sabotage proved it has
    to.** S-5.4 escalates a phase whose last three steps concluded the same
    thing, so a reseed that always reported `"reseed"` would stall the
    investigation on its third genuinely different fixture.

    Three because `DEFAULT_STALL_AFTER` is three: two reseeds cannot tell the two
    implementations apart, which is why the first version of this test passed
    against the constant.
    """
    _, seeder = recorded_seeds()
    budget = a_budget()
    conditions = UNIFORM

    for index, distribution in enumerate(
        [Distribution.LONG_TAIL, Distribution.POWER_LAW, Distribution.UNIFORM]
    ):
        outcome = reseed(
            recipe=recipe(distribution),
            scales=[10, 100, 1000 * (index + 2)],
            current=conditions,
            register=ExclusionRegister(),
            seeder=seeder,  # type: ignore[arg-type]
            budget=budget,
        )
        conditions = outcome.after

    assert budget.used(Phase.INVESTIGATE) == 3


def test_reseeding_the_same_fixture_over_and_over_does_stall() -> None:
    """The control that gives the test above its meaning. A conclusion that never
    repeats would make the stall check unreachable, which is the opposite defect
    and just as silent — so the same recipe under a moving scale, three times,
    must trip it.
    """
    _, seeder = recorded_seeds()
    budget = a_budget()
    conditions = UNIFORM

    with pytest.raises(ProgressStalledError):
        for index in range(DEFAULT_STALL_AFTER):
            # The scale moves so the reseed is never pointless; the *recipe* is
            # identical, so what it concluded is identical.
            outcome = reseed(
                recipe=recipe(Distribution.LONG_TAIL),
                scales=[10, 100, 1000 * (index + 2)],
                current=conditions,
                register=ExclusionRegister(),
                seeder=seeder,  # type: ignore[arg-type]
                budget=budget,
            )
            conditions = outcome.after


def test_the_concurrency_in_force_is_carried_forward_rather_than_assumed() -> None:
    """**A fixture that could not discriminate.** Every other test here runs at
    concurrency 1, so replacing the carried-forward value with the literal `1`
    changed nothing and the sabotage survived.

    A load experiment establishes exclusions at concurrency 8, and a reseed
    during one must not quietly reset the recorded load to serial — every
    exclusion held under it would reopen for a reason nobody caused.
    """
    under_load = Conditions.of(
        fixture_shape=Distribution.UNIFORM.value,
        platform=PLATFORM,
        concurrency=[8],
        scales=SCALES,
    )

    after = conditions_after(under_load, recipe(Distribution.LONG_TAIL), SCALES)

    assert "concurrency 8" in after.describe()
    assert "concurrency 1" not in after.describe()


# =============================== AC 1 through the loop: the Diagnostician's reach


def test_reseeding_makes_a_settled_instrument_proposable_again() -> None:
    """**The whole point, composed.** S-8.7 refuses a hypothesis re-proposing an
    instrument already settled; S-8.5 says a settled instrument comes back when a
    condition moves; this is the only thing that moves one.

    Asserted through `Investigation` rather than through `reseed` alone, because
    the criterion is *the Diagnostician can request new fixtures* — a capability
    reachable from the loop is the thing that was missing, and one reachable only
    from a helper function would satisfy the AC without closing the gap.
    """
    investigation = Investigation(
        session=Session(
            system="s",
            playbook="p",
            source="shop/views.py",
            rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)),
        ),
        client=ReplayingClient([]),
        instruments=Selection(profile=ProjectProfile(), available=(), withheld=()),
        source="shop/views.py",
        conditions=UNIFORM,
        execute=lambda spec: {"db.query": 2.0},
    )
    investigation.exclusions = register_with_a_uniform_exclusion()

    assert investigation.settled_instruments() == ("scaling.volume",)

    _, seeder = recorded_seeds()
    outcome = investigation.reseed(recipe(Distribution.LONG_TAIL), SCALES, seeder)  # type: ignore[arg-type]

    assert investigation.conditions is outcome.after
    assert investigation.settled_instruments() == ()
    assert len(outcome.reopened) == 1


def test_a_failed_reseed_leaves_the_investigation_where_it_was() -> None:
    """The ordering finding, asserted where it actually bites: the loop must not
    be holding conditions describing a fixture that was never built."""

    def broken(_: FixtureRecipe) -> None:
        message = "cannot build"
        raise RuntimeError(message)

    investigation = Investigation(
        session=Session(
            system="s",
            playbook="p",
            source="shop/views.py",
            rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 16)),
        ),
        client=ReplayingClient([]),
        instruments=Selection(profile=ProjectProfile(), available=(), withheld=()),
        source="shop/views.py",
        conditions=UNIFORM,
        execute=lambda spec: {"db.query": 2.0},
    )
    investigation.exclusions = register_with_a_uniform_exclusion()

    with pytest.raises(ReseedError):
        investigation.reseed(recipe(Distribution.LONG_TAIL), SCALES, broken)

    assert investigation.conditions is UNIFORM
    assert investigation.settled_instruments() == ("scaling.volume",)

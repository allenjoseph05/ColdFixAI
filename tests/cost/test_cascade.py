"""S-5.6: two cheap tries, a mechanical check, and the rule the AC leaves out.

`04-cost.md` §3 is what makes *no quality loss* honest rather than aspirational,
and the tests that matter are about the boundary it draws:

- cascade is available exactly where a validator exists, so AC 4 is not a
  separate rule but a consequence of AC 1 — and a caller cannot supply the
  missing validator to get round it;
- the cascade starts where S-5.5 routes and escalates one rung, which is what
  reproduces §12.3's *repair: cascade mid→frontier* without breaking S-5.5's AC 4;
- a result that failed its own validator is never returned, at any tier;
- and §3's promotion rule — escalate more than ~30% of the time and the step
  should start dear — is a fifth criterion the story's AC omit.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from coldfix.cost.accounting import Phase, StepClass
from coldfix.cost.cascade import (
    CHEAP_ATTEMPTS,
    MINIMUM_SAMPLES,
    PROMOTION_THRESHOLD,
    Cascaded,
    EscalationLog,
    NoDearerTierError,
    NoValidatorError,
    Resolution,
    cascadable,
    cascade,
    dearer_than,
)
from coldfix.cost.routing import STEP_KINDS, Router, StepType, Tier

CREATIVE_STEPS = (StepType.HYPOTHESIS_GENERATION, StepType.ATTACK_DESIGN)


class Attempts:
    """An `attempt` that records which models it was asked for.

    The models matter more than the results here: the whole story is *which tier
    ran*, and a double that returned a constant would make every routing
    assertion vacuous.
    """

    def __init__(self, accept_from: str | None = None) -> None:
        self.models: list[str] = []
        self._accept_from = accept_from

    def __call__(self, model: str) -> str:
        self.models.append(model)
        return f"result from {model}"

    def validate(self, result: str) -> bool:
        if self._accept_from is None:
            return True
        return result.endswith(self._accept_from)


def run(
    step_type: StepType = StepType.PATCH,
    *,
    accept_from: str | None = None,
    phase: Phase | None = None,
    log: EscalationLog | None = None,
    router: Router | None = None,
) -> tuple[Cascaded[str], Attempts]:
    attempts = Attempts(accept_from)
    outcome = cascade(
        step_type,
        attempt=attempts,
        validate=attempts.validate,
        router=router or Router(),
        phase=phase,
        log=log,
    )
    return outcome, attempts


# --------------------------- cascade exists exactly where a validator does (AC 1, 4)


@pytest.mark.parametrize("step_type", CREATIVE_STEPS)
def test_a_step_with_no_validator_cannot_cascade(step_type: StepType) -> None:
    """AC 4, which is not a separate rule but a consequence of AC 1.

    §3 records *none exists* against these two, so they fall out of the same
    check that lets the other six in. Written as a separate rule it would have
    been one somebody could forget to apply — and for this one that is
    `CLAUDE.md`'s standing non-negotiable.
    """
    with pytest.raises(NoValidatorError, match="no deterministic validator"):
        run(step_type)


@pytest.mark.parametrize("step_type", CREATIVE_STEPS)
def test_a_caller_cannot_supply_the_missing_validator(step_type: StepType) -> None:
    """The hole a rule written as *do not cascade these* would leave open.

    §3's table is the statement that no *deterministic* check exists. A
    caller-supplied one is a judgement wearing a validator's clothes, and
    accepting it would route the one step nothing can verify onto a cheap model
    with every other guard satisfied.
    """
    with pytest.raises(NoValidatorError):
        cascade(
            step_type,
            attempt=lambda model: model,
            validate=lambda _: True,
            router=Router(),
        )


def test_every_validated_step_type_may_cascade() -> None:
    """The control. A refusal that fired on everything would pass the tests
    above while switching the technique off entirely."""
    assert set(cascadable()) == {
        step for step in StepType if STEP_KINDS[step].mechanical_check is not None
    }
    assert len(cascadable()) == len(StepType) - len(CREATIVE_STEPS)


def test_the_cascadable_list_carries_the_check_that_makes_each_safe() -> None:
    """A list of names is easier to check against §3 than a predicate is."""
    assert cascadable()[StepType.PATCH] == "test suite passes"
    assert cascadable()[StepType.FALSIFICATION_TEST] == "fails on unpatched code"


# ------------------------------------ the cheap tier is tried first (AC 1, AC 2)


def test_the_cheap_tier_is_attempted_first() -> None:
    """AC 1. The routed tier runs before anything dearer is considered."""
    outcome, attempts = run(accept_from="claude-sonnet-5")

    assert attempts.models == ["claude-sonnet-5"]
    assert outcome.resolution is Resolution.CHEAP
    assert outcome.model == "claude-sonnet-5"


def test_a_validated_first_attempt_costs_nothing_more() -> None:
    """The case the whole technique is for: a cheap answer a machine accepted."""
    outcome, attempts = run()

    assert len(attempts.models) == 1
    assert outcome.escalated is False


def test_escalation_happens_after_two_failures_not_one() -> None:
    """AC 2, and §3's *2 cheap attempts, then strong*.

    Two rather than one because a cheap model failing once is ordinary and the
    retry is nearly free; the count is the difference between a cascade and a
    coin flip.
    """
    outcome, attempts = run(accept_from="claude-opus-5")

    assert attempts.models == ["claude-sonnet-5", "claude-sonnet-5", "claude-opus-5"]
    assert len(attempts.models) == CHEAP_ATTEMPTS + 1
    assert outcome.resolution is Resolution.ESCALATED


def test_repair_cascades_mid_to_frontier() -> None:
    """§12.3's engineered case, reproduced exactly.

    Its repair row reads *cascade mid→frontier*. Repair's mechanical work routes
    to mid under S-5.5, so escalating one rung lands on frontier — which is also
    why S-5.5's AC 4 survives: mechanical work is never *routed* to the frontier
    tier, it only ever *reaches* it after failing its own validator twice.
    """
    outcome, attempts = run(StepType.PATCH, accept_from="claude-opus-5", phase=Phase.REPAIR)

    assert attempts.models == ["claude-sonnet-5", "claude-sonnet-5", "claude-opus-5"]
    assert outcome.attempts[0].tier is Tier.MID
    assert outcome.attempts[-1].tier is Tier.FRONTIER


def test_grounding_cascades_cheap_to_mid() -> None:
    """The other end of §12.3's table. Grounding routes cheap, so it escalates
    to mid rather than straight to the frontier."""
    outcome, attempts = run(
        StepType.EXPLORER_ACTION, accept_from="claude-sonnet-5", phase=Phase.GROUND
    )

    assert attempts.models == ["claude-haiku-4-5", "claude-haiku-4-5", "claude-sonnet-5"]
    assert outcome.attempts[0].tier is Tier.CHEAP
    assert outcome.attempts[-1].tier is Tier.MID


def test_the_routed_tier_is_never_the_frontier_for_a_cascaded_step() -> None:
    """S-5.5's AC 4 restated from this side: a cascade always has somewhere to
    escalate to, because mechanical work never starts at the top."""
    router = Router()
    for step_type in cascadable():
        for phase in Phase:
            routed = router.tier_for(STEP_KINDS[step_type].step_class, phase)
            assert dearer_than(routed) is not None


def test_dearer_than_the_top_tier_is_nothing() -> None:
    assert dearer_than(Tier.FRONTIER) is None
    assert dearer_than(Tier.CHEAP) is Tier.MID


# -------------------------- a rejected result is never returned, at any tier


def test_a_result_that_failed_on_the_dearest_tier_raises() -> None:
    """Returning it would make the validator decorative.

    The caller cannot tell a validated result from an unvalidated one by looking
    at it, so a cascade that handed back a rejected answer would quietly undo the
    guarantee the whole technique rests on.
    """
    with pytest.raises(NoDearerTierError, match="is not a routing problem"):
        run(accept_from="nothing accepts this")


def test_a_step_configured_at_the_top_tier_has_nowhere_to_escalate() -> None:
    """Refused rather than papered over: the step failed its own check on the
    dearest model available, which is a real failure the caller must handle."""
    router = Router.from_config({"tiers": {"mechanical": "frontier"}})

    with pytest.raises(NoDearerTierError, match="nothing to escalate to"):
        run(accept_from="never", router=router)


def test_the_attempts_travel_with_the_result() -> None:
    """A result that took three tries is a different fact about the step type
    from one that took one, and the escalation rate is computed from them."""
    outcome, _ = run(accept_from="claude-opus-5")

    assert [attempt.accepted for attempt in outcome.attempts] == [False, False, True]
    assert outcome.attempts[-1].model == "claude-opus-5"


# ------------------------------------ escalation rate, per step type (AC 3)


def test_the_escalation_rate_is_logged_per_step_type() -> None:
    """AC 3, in §3's unit — a validator belongs to a step type."""
    log = EscalationLog()
    for _ in range(8):
        run(StepType.PATCH, log=log)
    for _ in range(2):
        run(StepType.PATCH, accept_from="claude-opus-5", log=log)

    statistics = log.statistics(StepType.PATCH)

    assert statistics.cascades == 10
    assert statistics.escalations == 2
    assert statistics.escalation_rate == Decimal("0.2")


def test_step_types_are_counted_apart() -> None:
    """A rate averaged across step types would hide the one that needs
    promoting behind the ones that do not."""
    log = EscalationLog()
    run(StepType.PATCH, log=log)
    run(StepType.EVIDENCE_CHAIN, accept_from="claude-opus-5", log=log)

    assert log.statistics(StepType.PATCH).escalations == 0
    assert log.statistics(StepType.EVIDENCE_CHAIN).escalations == 1


def test_a_rate_with_too_few_samples_is_none_rather_than_a_number() -> None:
    """One escalation out of one attempt is 100%.

    Promoting a step type on that would move it to the dearest model on a coin
    flip — S-4.2's rule for a ratio whose denominator is too small to divide by.
    """
    log = EscalationLog()
    run(StepType.PATCH, accept_from="claude-opus-5", log=log)

    assert log.statistics(StepType.PATCH).escalation_rate is None
    assert log.statistics(StepType.PATCH).should_promote is False


# ----------------------- §3's promotion rule, which the AC leave out


def test_a_step_escalating_more_than_thirty_percent_is_a_promotion_candidate() -> None:
    """§3: *if a step escalates more than ~30% of the time, promote it
    permanently.*

    Above that, two cheap attempts plus a dear one cost more than starting dear
    — so the cascade is losing money on the step it was meant to save it on.
    """
    log = EscalationLog()
    for _ in range(5):
        run(StepType.PATCH, log=log)
    for _ in range(5):
        run(StepType.PATCH, accept_from="claude-opus-5", log=log)

    candidates = [statistics.step_type for statistics in log.promotion_candidates()]

    assert log.statistics(StepType.PATCH).escalation_rate == Decimal("0.5")
    assert candidates == [StepType.PATCH]


def test_a_step_below_the_threshold_is_left_alone() -> None:
    """The control. A rule that promoted everything would save nothing."""
    log = EscalationLog()
    for _ in range(9):
        run(StepType.PATCH, log=log)
    run(StepType.PATCH, accept_from="claude-opus-5", log=log)

    assert log.statistics(StepType.PATCH).escalation_rate == Decimal("0.1")
    assert log.promotion_candidates() == []


def test_the_threshold_is_the_one_the_cost_document_names() -> None:
    assert Decimal("0.30") == PROMOTION_THRESHOLD
    assert MINIMUM_SAMPLES >= 10


def test_a_step_that_never_escalates_is_reported_too() -> None:
    """The log is two-sided on purpose.

    A validator that has never rejected anything is either a step the cheap model
    genuinely handles — the result this technique exists for — or a check that
    cannot fail, which is a cascade that is not checking anything. The log
    reports the number rather than choosing between them.
    """
    log = EscalationLog()
    for _ in range(MINIMUM_SAMPLES):
        run(StepType.PATCH, log=log)

    quiet = [statistics.step_type for statistics in log.never_escalated()]

    assert quiet == [StepType.PATCH]
    assert log.promotion_candidates() == []


def test_the_report_names_the_step_types_to_promote() -> None:
    log = EscalationLog()
    for _ in range(5):
        run(StepType.PATCH, log=log)
    for _ in range(5):
        run(StepType.PATCH, accept_from="claude-opus-5", log=log)

    rendered = log.report()

    assert "patch: 50% of 10 escalated — promote it permanently" in rendered


def test_an_unrated_step_type_says_so_rather_than_showing_a_percentage() -> None:
    log = EscalationLog()
    run(StepType.PATCH, log=log)

    assert "too few to rate" in log.report()


def test_a_log_with_nothing_in_it_says_so() -> None:
    assert EscalationLog().report() == "Cascades: none run."


def test_a_cascade_without_a_log_still_runs() -> None:
    """The log is for the run report, not for correctness — a caller that has
    not started one must not be unable to cascade."""
    outcome, _ = run(log=None)

    assert outcome.resolution is Resolution.CHEAP


def test_the_class_of_a_cascaded_step_is_always_mechanical() -> None:
    """A structural restatement of AC 4: every step type that can reach this
    module is one §3 gave a validator, and those are exactly the mechanical
    ones."""
    for step_type in cascadable():
        assert STEP_KINDS[step_type].step_class is StepClass.MECHANICAL

"""S-5.4: caps nothing can raise, and the three exhaustions that are not halts.

`CLAUDE.md` lists this story in the hard-enforcement table, so the tests that
matter are the ones that attempt the violation and assert it fails rather than
the ones that show the happy path working.

Four properties carry the story, and three of them are about reading the
acceptance criteria against `02-architecture.md` §7.2 rather than alone:

- exhaustion means four different things, and only the global ceiling halts;
- the four caps count four different units, and calls are none of them;
- ground is scoped per run and everything else per finding;
- a ceiling checked after the spend is a report, not a ceiling.

The last section is the progress check, whose whole difficulty is that *did this
step teach me anything* is the self-judged question F6 removed once already.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from coldfix.cost.accounting import (
    Agent,
    ExchangeRate,
    Ledger,
    ModelCall,
    Phase,
    StepClass,
    TokenUsage,
)
from coldfix.cost.budget import (
    PHASE_CAPS,
    Budget,
    BudgetError,
    BudgetExhaustedError,
    Cap,
    CapRaisedError,
    Disposition,
    ProgressStalledError,
    Scope,
    StepUnit,
    dispositions,
    worst_case_usd,
)

AT = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
RATE = ExchangeRate(euros_per_dollar=Decimal("1.00"), as_of=date(2026, 8, 9))


def budget(**overrides: object) -> Budget:
    fields: dict[str, object] = {"ledger": Ledger(), "rate": RATE}
    return Budget(**{**fields, **overrides})  # type: ignore[arg-type]


def spend(ledger: Ledger, usd: str) -> None:
    """Put a known number of dollars on the ledger, priced through S-5.3."""
    dollars = Decimal(usd)
    ledger.record(
        ModelCall(
            phase=Phase.INVESTIGATE,
            agent=Agent.DIAGNOSTICIAN,
            step_class=StepClass.MECHANICAL,
            model="claude-opus-5",
            usage=TokenUsage(input_tokens=int(dollars * 200_000), output_tokens=0),
            at=AT,
        )
    )


# ------------------------------------------ exhaustion is four things, not one


def test_each_phase_carries_its_own_disposition() -> None:
    """AC 3 read as universal would make three of the four caps wrong.

    `02-architecture.md` §7.2 gives each phase its own column, and only the
    global ceiling halts.
    """
    assert PHASE_CAPS[Phase.GROUND].on_exhaustion is Disposition.ABORT
    assert PHASE_CAPS[Phase.INVESTIGATE].on_exhaustion is Disposition.PARTIAL
    assert PHASE_CAPS[Phase.REPAIR].on_exhaustion is Disposition.ESCALATE
    assert PHASE_CAPS[Phase.PATCH_AUDIT].on_exhaustion is Disposition.ESCALATE


def test_running_out_of_investigation_is_not_a_halt() -> None:
    """The one that matters most, and the one a universal reading destroys.

    Forty experiments that found something and could not finish is an answer —
    *here is what we measured, here is what this run therefore does not cover* —
    and it is the same answer S-4.5 ships when a screen finds nothing. Halting
    would discard everything the forty experiments established.
    """
    running = budget()
    for _ in range(PHASE_CAPS[Phase.INVESTIGATE].limit):
        running.record_step(Phase.INVESTIGATE, "n.plus.one")

    with pytest.raises(BudgetExhaustedError) as exhausted:
        running.authorize(Phase.INVESTIGATE, "n.plus.one")

    assert exhausted.value.exhaustion.disposition is Disposition.PARTIAL
    assert "partial chain" in exhausted.value.exhaustion.report()


def test_only_the_global_ceiling_halts() -> None:
    """AC 3's *halt, checkpoint and report* belongs to the ceiling alone."""
    halting = {phase for phase, disposition in dispositions() if disposition is Disposition.HALT}

    assert halting == set()


def test_the_dispositions_are_enumerable_rather_than_something_to_notice() -> None:
    """A handler written against a single outcome would be silently wrong for
    three of the six phases, so the four are listable."""
    assert {disposition for _, disposition in dispositions()} == {
        Disposition.ABORT,
        Disposition.PARTIAL,
        Disposition.ESCALATE,
    }


# --------------------------------------------- the caps cannot be raised (AC 1)


def test_a_cap_cannot_be_raised_at_construction() -> None:
    """The backlog note: caps in code, not configuration.

    This is the test the hard-enforcement table asks for — it attempts the
    violation rather than demonstrating the rule.
    """
    with pytest.raises(CapRaisedError, match="above the figures compiled"):
        budget(
            caps={
                **PHASE_CAPS,
                Phase.INVESTIGATE: Cap(
                    400, StepUnit.EXPERIMENT, Scope.FINDING, Disposition.PARTIAL
                ),
            }
        )


def test_a_cap_cannot_be_raised_at_runtime() -> None:
    """The same rule against a process that is already running."""
    running = budget()

    with pytest.raises(CapRaisedError, match="nothing at runtime may exceed it"):
        running.tighten(Phase.GROUND, 600)


def test_a_cap_may_be_lowered() -> None:
    """Asymmetric on purpose: spending less is not the failure mode this exists
    for, and forbidding it would make the caps unusable on a smoke test."""
    running = budget()
    running.tighten(Phase.GROUND, 5)

    assert running.remaining(Phase.GROUND) == 5


def test_the_compiled_caps_are_the_ones_the_story_names() -> None:
    """AC 1, read straight off §7.2."""
    assert PHASE_CAPS[Phase.GROUND].limit == 60
    assert PHASE_CAPS[Phase.INVESTIGATE].limit == 40
    assert PHASE_CAPS[Phase.REPAIR].limit == 3
    assert PHASE_CAPS[Phase.FINDING_AUDIT].limit == 2


def test_a_cap_of_zero_is_refused() -> None:
    """A phase that should not run is not configured to zero, it is not called."""
    with pytest.raises(BudgetError, match="do nothing at all"):
        Cap(0, StepUnit.STEP, Scope.RUN, Disposition.ABORT)


# ------------------------------------------ the units and the scopes differ


def test_the_four_caps_count_four_different_units() -> None:
    """S-4.4's finding again: conflating them is a 3x error.

    §12.1 budgets 120 model calls per finding in investigate against a cap of 40
    experiments, so a cap counted in calls would stop investigation at a third of
    its intended budget.
    """
    assert PHASE_CAPS[Phase.GROUND].unit is StepUnit.STEP
    assert PHASE_CAPS[Phase.INVESTIGATE].unit is StepUnit.EXPERIMENT
    assert PHASE_CAPS[Phase.REPAIR].unit is StepUnit.ATTEMPT
    assert PHASE_CAPS[Phase.TEST_AUDIT].unit is StepUnit.ROUND


def test_grounding_is_counted_once_for_the_whole_run() -> None:
    """`04-cost.md` §11: grounding happens once per repository.

    Counted per finding it would be re-granted 60 steps for every finding the run
    goes on to open — the cap would rise with the number of findings, which is
    the direction it must never move.
    """
    running = budget()
    running.record_step(Phase.GROUND, "n.plus.one")
    running.record_step(Phase.GROUND, "over.fetch")

    assert running.used(Phase.GROUND) == 2
    assert running.used(Phase.GROUND, "n.plus.one") == 2


def test_investigation_is_counted_per_finding() -> None:
    """The other direction of the same trap.

    §12.1's table is written per finding, so one run-wide counter would give five
    findings eight experiments each instead of forty.
    """
    running = budget()
    for _ in range(PHASE_CAPS[Phase.INVESTIGATE].limit):
        running.record_step(Phase.INVESTIGATE, "n.plus.one")

    assert running.remaining(Phase.INVESTIGATE, "n.plus.one") == 0
    assert running.remaining(Phase.INVESTIGATE, "over.fetch") == 40
    running.authorize(Phase.INVESTIGATE, "over.fetch")


def test_each_audit_gets_its_own_rounds() -> None:
    """Three audits asking different questions of different artifacts.

    A shared pool would let a patch audit spend rounds a finding audit had not
    used yet, which is the audit most worth not running out of.
    """
    running = budget()
    for _ in range(2):
        running.record_step(Phase.FINDING_AUDIT, "n.plus.one")

    assert running.remaining(Phase.FINDING_AUDIT, "n.plus.one") == 0
    assert running.remaining(Phase.PATCH_AUDIT, "n.plus.one") == 2


def test_an_audit_on_one_finding_does_not_spend_another_finding_s_rounds() -> None:
    """Every audit is scoped per finding, and asserting it needs a *second*
    finding.

    Found by sabotage: re-scoping the patch audit to the run left the test above
    passing, because nothing had been recorded against that phase at all. The
    property is only visible once two findings compete for the same counter —
    and a run-scoped audit is the shape where finding five is audited on rounds
    finding one used up.
    """
    running = budget()
    for _ in range(2):
        running.record_step(Phase.PATCH_AUDIT, "n.plus.one")

    assert running.remaining(Phase.PATCH_AUDIT, "n.plus.one") == 0
    assert running.remaining(Phase.PATCH_AUDIT, "over.fetch") == 2
    running.authorize(Phase.PATCH_AUDIT, "over.fetch")


# --------------------------------------------------- the euro ceiling (AC 2)


def test_the_ceiling_refuses_a_step_before_it_is_spent() -> None:
    """A ceiling checked afterwards is a report, not a ceiling.

    Cost is known once a call returns, so authorization takes the worst case the
    step could cost and refuses on the projection.
    """
    ledger = Ledger()
    spend(ledger, "9")
    running = budget(ledger=ledger, ceiling_eur=Decimal("10.00"))

    with pytest.raises(BudgetExhaustedError) as exhausted:
        running.authorize(Phase.INVESTIGATE, "n.plus.one", worst_case=Decimal("2.00"))

    assert exhausted.value.exhaustion.disposition is Disposition.HALT
    assert exhausted.value.exhaustion.phase is None


def test_a_step_that_fits_under_the_ceiling_is_authorized() -> None:
    """The control. A ceiling that refused everything would pass the test above
    while making the system useless."""
    ledger = Ledger()
    spend(ledger, "5")
    running = budget(ledger=ledger, ceiling_eur=Decimal("10.00"))

    running.authorize(Phase.INVESTIGATE, "n.plus.one", worst_case=Decimal("1.00"))


def test_the_worst_case_prices_every_prompt_token_at_the_dearest_input_rate() -> None:
    """A one-hour cache write, 2x input — the dearest an input token can be.

    Pessimistic on purpose: a ceiling enforced against an optimistic estimate
    holds only when the caching went well, which is the run where a ceiling
    matters least.
    """
    worst = worst_case_usd("claude-opus-5", prompt_tokens=1_000_000, max_output_tokens=0)

    assert worst == Decimal("10.00")


def test_the_worst_case_assumes_the_whole_output_budget_comes_back() -> None:
    worst = worst_case_usd("claude-opus-5", prompt_tokens=0, max_output_tokens=1_000_000)

    assert worst == Decimal("25.00")


def test_the_ceiling_reads_its_spend_from_the_ledger() -> None:
    """A budget that counted its own spend could disagree with the bill, and the
    bill is the one somebody checks against an invoice."""
    ledger = Ledger()
    running = budget(ledger=ledger, ceiling_eur=Decimal("10.00"))
    spend(ledger, "7")

    assert running.spent_eur == Decimal("7.00")


def test_a_run_with_no_ceiling_still_has_its_phase_caps() -> None:
    """`None` is a development setting, never a production one — and it switches
    off the ceiling, not the caps. There is no way to switch those off."""
    running = budget(ceiling_eur=None)
    for _ in range(PHASE_CAPS[Phase.REPAIR].limit):
        running.record_step(Phase.REPAIR, "n.plus.one")

    with pytest.raises(BudgetExhaustedError):
        running.authorize(Phase.REPAIR, "n.plus.one")


def test_a_non_positive_ceiling_is_refused() -> None:
    with pytest.raises(BudgetError, match="must be positive"):
        budget(ceiling_eur=Decimal("0"))


# ------------------------------- exhaustion carries its evidence, not a message


def test_exhaustion_carries_the_state_it_stopped_in() -> None:
    """S-1.7's recorded argument for `NoiseFloorTooHighError`: refusing by return
    value lets a caller ignore it, refusing without the evidence makes the
    refusal unloggable.

    This is also AC 3's checkpoint — a complete statement of where the run
    stopped. It is deliberately not a checkpoint *schema*, which is S-6.1's
    artifact and is not guessed at here.
    """
    ledger = Ledger()
    spend(ledger, "4")
    running = budget(ledger=ledger, ceiling_eur=Decimal("100"))
    for _ in range(3):
        running.record_step(Phase.REPAIR, "n.plus.one")

    with pytest.raises(BudgetExhaustedError) as raised:
        running.authorize(Phase.REPAIR, "n.plus.one")

    exhaustion = raised.value.exhaustion
    assert exhaustion.phase is Phase.REPAIR
    assert exhaustion.finding_id == "n.plus.one"
    assert exhaustion.used == 3
    assert exhaustion.limit == 3
    assert exhaustion.unit is StepUnit.ATTEMPT
    assert exhaustion.spent_eur == Decimal("4.00")
    assert exhaustion.ceiling_eur == Decimal("100")


def test_exhaustion_does_not_warn_and_continue() -> None:
    """AC 3, stated as the thing that must be impossible.

    A budget that returned a boolean would be one a caller could ignore, and the
    caller that ignores it is the unbounded run `04-cost.md` §12.1 prices at
    €125,000.
    """
    running = budget()
    for _ in range(PHASE_CAPS[Phase.REPAIR].limit):
        running.record_step(Phase.REPAIR, "n.plus.one")

    with pytest.raises(BudgetExhaustedError):
        running.authorize(Phase.REPAIR, "n.plus.one")


def test_the_report_says_what_happens_next() -> None:
    """A refusal a human reads has to name the action, not just the number."""
    running = budget()
    for _ in range(PHASE_CAPS[Phase.GROUND].limit):
        running.record_step(Phase.GROUND)

    with pytest.raises(BudgetExhaustedError) as raised:
        running.authorize(Phase.GROUND)

    assert "abort with a diagnostic" in str(raised.value)
    assert "60 of 60 steps" in str(raised.value)


# ------------------------------------------------ the progress check (AC 4)


def test_repeating_a_conclusion_escalates() -> None:
    """§7.2: if the last N steps produced no new information, escalate."""
    running = budget()
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")

    with pytest.raises(ProgressStalledError) as stalled:
        running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")

    assert stalled.value.stall.repeated == 3
    assert "spend budget without changing the answer" in str(stalled.value)


def test_a_changing_conclusion_is_progress() -> None:
    """The control. A stall detector that fired on any three steps would stop
    every investigation at its third experiment."""
    running = budget()
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries linear")
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="queries flat")

    assert running.used(Phase.INVESTIGATE, "n.plus.one") == 4


def test_two_identical_conclusions_are_a_confirmation_not_a_stall() -> None:
    """Confirming a result twice is a thing an investigation legitimately does,
    which is why the default run length is three rather than two."""
    running = budget()
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="not the database")
    running.record_step(Phase.INVESTIGATE, "n.plus.one", conclusion="not the database")

    assert running.used(Phase.INVESTIGATE, "n.plus.one") == 2


def test_a_stall_is_tracked_per_finding_like_the_cap_it_shares_a_scope_with() -> None:
    """Two findings that each concluded the same thing twice have not stalled;
    one finding that concluded it three times has."""
    running = budget()
    for finding in ("a", "b", "a", "b"):
        running.record_step(Phase.INVESTIGATE, finding, conclusion="flat")

    assert running.used(Phase.INVESTIGATE, "a") == 2


def test_a_step_with_no_conclusion_resets_the_run_rather_than_extending_it() -> None:
    """A step that concluded nothing is not the same conclusion twice.

    Extending the run would let a phase escalate on steps that never claimed to
    have established anything — a failed experiment is not a repeated one.
    """
    running = budget()
    running.record_step(Phase.INVESTIGATE, "a", conclusion="flat")
    running.record_step(Phase.INVESTIGATE, "a", conclusion="flat")
    running.record_step(Phase.INVESTIGATE, "a", conclusion=None)
    running.record_step(Phase.INVESTIGATE, "a", conclusion="flat")

    assert running.used(Phase.INVESTIGATE, "a") == 4


def test_a_stall_and_an_exhaustion_are_different_errors() -> None:
    """They call for opposite actions — exhausted means stop, stalled means
    change approach while you still have budget — so a caller that caught one
    type would handle the other wrongly."""
    assert not issubclass(ProgressStalledError, BudgetExhaustedError)
    assert not issubclass(BudgetExhaustedError, ProgressStalledError)


def test_a_stall_run_shorter_than_two_is_refused() -> None:
    """At one, the first step of every phase would escalate."""
    with pytest.raises(BudgetError, match="at least two steps"):
        budget(stall_after=1)


def test_the_report_shows_every_counter() -> None:
    """For a run report, and for the checkpoint AC 3 asks exhaustion to leave."""
    running = budget(ceiling_eur=Decimal("50"))
    running.record_step(Phase.GROUND)
    running.record_step(Phase.INVESTIGATE, "n.plus.one")

    rendered = running.report()

    assert "ceiling of €50.00" in rendered
    assert "1/60 steps per run" in rendered
    assert "investigate (n.plus.one): 1/40 experiments per finding" in rendered

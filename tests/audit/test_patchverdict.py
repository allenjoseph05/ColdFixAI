"""Epic 11, S-11.7 — the patch audit's verdict.

*Schema `clean` / `broken` + reproducing input / `suspicious` + concern. `broken`
requires a reproducing input — schema-enforced. Two rounds maximum, then
escalate.*

The composition story for the epic. Five attacks have already answered and this
combines them, so nothing here calls a model — the same first sentence S-9.8's
tests open with, and it holds harder here because three of the five attacks never
asked a model anything in the first place.

The two properties worth the most: **`broken` cannot be constructed without
something to run**, and **`clean` requires every attack to have passed** — which
makes an attack that did not run a concern rather than a silent pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from coldfix.audit import patchverdict as module
from coldfix.audit.cheating import CheatAudit, Check, Finding, Metrics, Reading, Revision
from coldfix.audit.equivalence import (
    AdversarialInput,
    Equivalence,
    Observed,
    Probed,
    ReproducingInput,
    Shape,
    compare_outputs,
)
from coldfix.audit.equivalence import Outcome as EquivalenceOutcome
from coldfix.audit.patchverdict import (
    Attack,
    AttackResult,
    Outcome,
    PatchVerdict,
    PatchVerdictError,
    Reproduction,
    Route,
    Verdict,
    from_cheat,
    from_equivalence,
    from_scope,
    from_test_quality,
    from_trades,
    not_run,
    record,
    route,
    verdict_for,
)
from coldfix.audit.scoping import (
    Caller,
    CallSite,
    Reference,
    ScopeAudit,
    SuiteOutcome,
    SuiteRun,
    Symbol,
    Unreadable,
)
from coldfix.audit.trades import GuardOutcome, Movement, Trade, TradeAudit
from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import PHASE_CAPS, Budget
from coldfix.primitives.envelope import (
    ALLOCATED_BLOCKS,
    BYTES_WRITTEN,
    CPU_SECONDS,
    OPEN_FILE_DESCRIPTORS,
    PEAK_RSS_BYTES,
    PROCESS_COUNT,
    THREAD_COUNT,
    WALL_SECONDS,
    Availability,
    Breach,
    GuardReport,
)
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.testaudit import TestAudit, Weakness

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 19))
FINDING = "n.plus.one"
SUITE = ["pytest", "-q"]

SECONDS = "seconds"
ROWS = "rows"


def a_reproduction(attack: Attack = Attack.EQUIVALENCE) -> Reproduction:
    return Reproduction(attack=attack, shows="the empty list differs", how="print('hello')")


def a_result(
    attack: Attack, outcome: Outcome, *, detail: str = "something", repro: bool = False
) -> AttackResult:
    return AttackResult(
        attack=attack,
        outcome=outcome,
        detail="" if outcome is Outcome.PASSED else detail,
        reproduction=a_reproduction(attack) if repro else None,
    )


def all_passing() -> list[AttackResult]:
    return [a_result(attack, Outcome.PASSED) for attack in Attack]


def a_budget() -> Budget:
    return Budget(ledger=Ledger(), rate=RATE)


# ---- adapters' inputs


def an_equivalence(*, broken: bool, complete: bool = True) -> Equivalence:
    payload = AdversarialInput(shape=Shape.EMPTY, label="an empty list", payload=[])
    if broken:
        divergence = compare_outputs([{"id": 1, "name": "a"}], [{"id": 1}])
        assert divergence is not None
        found = ReproducingInput(
            input=payload,
            before=[{"id": 1, "name": "a"}],
            after=Observed(payload=[{"id": 1}], wall_seconds=0.1),
            divergence=divergence,
            program="import json\nprint(json.dumps([1]))",
        )
        return Equivalence(
            workload="w",
            probed=(Probed(payload, EquivalenceOutcome.DIFFERED, "the name is gone"),),
            reproducing=(found,),
            runs=4,
        )
    outcome = EquivalenceOutcome.MATCHED if complete else EquivalenceOutcome.NOT_COMPARED
    return Equivalence(
        workload="w",
        probed=(Probed(payload, outcome, "identical" if complete else "the probe raised"),),
        reproducing=(),
        runs=2,
    )


KINDS = {SECONDS: MetricKind.DURATION, ROWS: MetricKind.COUNT}


def a_cheat_audit(
    *, checks: tuple[Check, ...] | None = None, warm_only: bool = False
) -> CheatAudit:
    """`warm_only` is the shape S-11.3 needed a separate case for: a gain on the
    repeated passes that is too small a warm-up excess to trip the caching check,
    so every class comes back `NOT_DETECTED` and the improvement still is not one
    anybody gets. It is the only way to reach `survives_a_fresh_process is False`
    without a detected cheat beside it."""
    every = checks or tuple(
        Check(cheat=item, finding=Finding.NOT_DETECTED, reason="looked") for item in Cheat
    )
    cold = (100.0, 100.0) if warm_only else (10.0, 2.0)
    warm = (75.0, 64.0) if warm_only else (10.0, 2.0)
    return CheatAudit(
        metrics=Metrics(cost=SECONDS, kinds=KINDS),
        checks=every,
        original=Reading(
            revision=Revision.ORIGINAL,
            shape=Distribution.UNIFORM,
            first={SECONDS: cold[0]},
            repeated=({SECONDS: warm[0]},),
        ),
        patched=Reading(
            revision=Revision.PATCHED,
            shape=Distribution.UNIFORM,
            first={SECONDS: cold[1]},
            repeated=({SECONDS: warm[1]},),
        ),
        relative_noise=0.12,
    )


QUIET = {
    WALL_SECONDS: 1.0,
    CPU_SECONDS: 1.0,
    PEAK_RSS_BYTES: 1e8,
    ALLOCATED_BLOCKS: 5_000.0,
    BYTES_WRITTEN: 0.0,
    OPEN_FILE_DESCRIPTORS: 20.0,
    THREAD_COUNT: 4.0,
    PROCESS_COUNT: 0.0,
}


def a_trade_audit(
    *,
    breaches: tuple[str, ...] = (),
    broken_guard: bool = False,
    unread: tuple[str, ...] = (),
) -> TradeAudit:
    guard = Guard(metric=ROWS, baseline=1000.0, at_most=1000.0)
    claim = CostClaim(metric=SECONDS, baseline=8.24, at_most=2.0, guards=(guard,))
    return TradeAudit(
        claim=claim,
        trade=Trade(fell=(Movement(metric=SECONDS, before=8.24, after=1.5),), rose=()),
        guards=(GuardOutcome(guard=guard, measured=50_000.0 if broken_guard else 1000.0),),
        envelope=GuardReport(
            breaches=tuple(
                Breach(metric=name, before=1e8, after=9e8, tolerance=0.25) for name in breaches
            ),
            checked=tuple(name for name in QUIET if name not in unread),
            unmeasured=dict.fromkeys(unread, Availability.NEEDS_RUSAGE),
        ),
        cost=Movement(metric=SECONDS, before=8.24, after=1.5),
    )


def a_scope_audit(
    *,
    suite: SuiteOutcome = SuiteOutcome.PASSED_ON_BOTH,
    outside: bool = False,
    unreadable: bool = False,
) -> ScopeAudit:
    symbol = Symbol(
        path="shop/serializers.py",
        qualname="BookSerializer.to_representation",
        first_line=7,
        last_line=8,
    )
    site = CallSite(
        path="reports/exports.py",
        line=11,
        inside="nightly_csv",
        kind=Reference.CALL,
        text="    return serializer.to_representation(book)",
    )
    codes = {
        SuiteOutcome.PASSED_ON_BOTH: (0, 0),
        SuiteOutcome.BROKEN_BY_THE_PATCH: (0, 1),
        SuiteOutcome.ALREADY_BROKEN: (1, 1),
        SuiteOutcome.NOT_RUN: (None, None),
    }[suite]
    return ScopeAudit(
        symbols=(symbol,),
        callers=(Caller(symbol=symbol, site=site),) if outside else (),
        suite=SuiteRun(
            outcome=suite, original_exit=codes[0], patched_exit=codes[1], evidence="..."
        ),
        scope=frozenset({"shop/serializers.py"}),
        unreadable={"shop/x.py": Unreadable.NOT_SUPPLIED} if unreadable else {},
    )


def a_test() -> FalsificationTest:
    return FalsificationTest(
        claim="the endpoint stops re-rendering the author",
        script="assert measure()['seconds'] < 2.0",
        equivalence="the same books in the same order",
        cost=CostClaim(
            metric=SECONDS,
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric=ROWS, baseline=1000.0, at_most=1000.0),),
        ),
        catches=(Cheat.CACHED_STATE,),
    )


def a_test_audit(*, sound: bool) -> TestAudit:
    if sound:
        return TestAudit(original=a_test(), weaknesses=(), strengthened=None)
    return TestAudit(
        original=a_test(),
        weaknesses=(Weakness(cheat=Cheat.CACHED_STATE, how="memoised on self"),),
        strengthened=a_test(claim="stronger") if False else a_test(),
    )


# ============ nothing here calls a model


def test_the_verdict_is_arithmetic_and_not_an_adversary_call() -> None:
    """Five attacks have already answered. `CLAUDE.md`: do not add a model call
    where a function would do."""
    names = vars(module)
    assert "ModelClient" not in names
    assert "audit_messages" not in names
    assert not any(name.endswith("SYSTEM") or name == "QUESTION" for name in names)


# ============ AC 1 and AC 2 — the schema


def test_broken_cannot_be_constructed_without_something_to_run() -> None:
    """**AC 2, and the one rule this schema exists to carry.** §222 returns this
    verdict to the Surgeon *with a reproducing input*; one arriving without it asks
    the recipient to find the failure themselves."""
    with pytest.raises(PatchVerdictError, match="`broken` with nothing to run"):
        PatchVerdict(verdict=Verdict.BROKEN)

    ok = PatchVerdict(verdict=Verdict.BROKEN, reproduction=a_reproduction())
    assert ok.reproduction is not None


def test_only_broken_may_carry_a_reproduction() -> None:
    """A `clean` with something runnable attached says two things at once."""
    for verdict in (Verdict.CLEAN, Verdict.SUSPICIOUS):
        with pytest.raises(PatchVerdictError, match="carries a reproduction"):
            PatchVerdict(verdict=verdict, reproduction=a_reproduction(), concern="x")


def test_suspicious_must_state_its_concern() -> None:
    """§4.4 escalates this verdict to a human, and an escalation with no
    instruction in it is a person asked to review something nobody told them
    about."""
    with pytest.raises(PatchVerdictError, match="no concern stated"):
        PatchVerdict(verdict=Verdict.SUSPICIOUS)
    assert PatchVerdict(verdict=Verdict.SUSPICIOUS, concern="memory tripled").concern


def test_clean_carries_neither_payload() -> None:
    with pytest.raises(PatchVerdictError, match="so it is not clean"):
        PatchVerdict(verdict=Verdict.CLEAN, concern="but actually")
    assert PatchVerdict(verdict=Verdict.CLEAN).ships


def test_a_reproduction_needs_both_halves() -> None:
    with pytest.raises(PatchVerdictError, match="what it shows and how to run it"):
        Reproduction(attack=Attack.EQUIVALENCE, shows="", how="pytest")
    with pytest.raises(PatchVerdictError, match="what it shows and how to run it"):
        Reproduction(attack=Attack.EQUIVALENCE, shows="it differs", how="   ")


def test_only_broke_it_may_carry_a_reproduction_at_the_attack_level() -> None:
    """The schema rule pushed down one layer, so `broken` cannot be reached with a
    reproduction that was attached to a passing attack."""
    with pytest.raises(PatchVerdictError, match="carries no reproduction"):
        AttackResult(attack=Attack.SCOPE, outcome=Outcome.BROKE_IT, detail="the suite fails")
    with pytest.raises(PatchVerdictError, match="carries reproduction"):
        AttackResult(
            attack=Attack.SCOPE,
            outcome=Outcome.SUSPECT,
            detail="callers outside",
            reproduction=a_reproduction(Attack.SCOPE),
        )


def test_an_attack_that_landed_must_say_something() -> None:
    for outcome in (Outcome.SUSPECT, Outcome.NOT_RUN):
        with pytest.raises(PatchVerdictError, match="said nothing about it"):
            AttackResult(attack=Attack.CHEAT, outcome=outcome, detail="  ")


# ============ verdict_for — precedence and coverage


def test_every_attack_passing_is_clean() -> None:
    result = verdict_for(all_passing())
    assert result.verdict is Verdict.CLEAN
    assert result.ships
    assert not result.unanswered


def test_broken_wins_over_suspicious() -> None:
    """**§4.4 sends the first back to the Surgeon and the second to a human.** A
    patch with a failing case is one the Surgeon can act on, and spending a cheap
    repair attempt beats spending a person."""
    results = all_passing()
    results[1] = a_result(Attack.CHEAT, Outcome.SUSPECT, detail="only warm")
    results[3] = a_result(Attack.SCOPE, Outcome.BROKE_IT, detail="suite fails", repro=True)

    result = verdict_for(results)
    assert result.verdict is Verdict.BROKEN
    assert result.reproduction is not None
    assert result.reproduction.attack is Attack.SCOPE
    assert any(item.outcome is Outcome.SUSPECT for item in result.results), (
        "the concerns still travel, and reach the human if it comes back"
    )


def test_a_suspect_attack_makes_the_whole_verdict_suspicious() -> None:
    results = all_passing()
    results[2] = a_result(Attack.TRADE, Outcome.SUSPECT, detail="peak rss tripled")
    result = verdict_for(results)
    assert result.verdict is Verdict.SUSPICIOUS
    assert "peak rss tripled" in result.concern


def test_an_attack_that_did_not_run_is_a_concern_and_never_a_pass() -> None:
    """**The fifth construction in this epic built to say this.** Five attacks of
    which four ran and none objected is not a patch that survived an audit."""
    results = all_passing()
    results[0] = not_run(Attack.EQUIVALENCE, "the probe could not drive the workload")
    result = verdict_for(results)
    assert result.verdict is Verdict.SUSPICIOUS
    assert "could not drive" in result.concern
    assert result.unanswered


def test_an_attack_missing_from_the_list_entirely_is_a_concern() -> None:
    """Not supplying a result is the quietest way to skip an attack, and it must
    not read as one that passed."""
    result = verdict_for([a_result(Attack.EQUIVALENCE, Outcome.PASSED)])
    assert result.verdict is Verdict.SUSPICIOUS
    assert "never attempted" in result.concern
    assert Attack.CHEAT.value in result.concern


def test_a_verdict_over_no_attacks_is_refused() -> None:
    """Nothing landed because nothing was attempted, and that is indistinguishable
    from a patch that survived."""
    with pytest.raises(PatchVerdictError, match="nothing was attempted"):
        verdict_for([])


def test_two_results_for_one_attack_are_refused() -> None:
    doubled = [*all_passing(), a_result(Attack.CHEAT, Outcome.SUSPECT, detail="actually no")]
    with pytest.raises(PatchVerdictError, match="Which one counts is undefined"):
        verdict_for(doubled)


# ============ the adapters


def test_an_equivalence_objection_becomes_broken_and_carries_its_program() -> None:
    result = from_equivalence(an_equivalence(broken=True))
    assert result.outcome is Outcome.BROKE_IT
    assert result.reproduction is not None
    assert "json.dumps" in result.reproduction.how, "S-11.2's program, not a summary of it"
    assert (
        verdict_for(
            [result, *[a_result(a, Outcome.PASSED) for a in Attack if a is not result.attack]]
        ).verdict
        is Verdict.BROKEN
    )


def test_an_equivalence_attack_that_compared_nothing_is_not_a_pass() -> None:
    result = from_equivalence(an_equivalence(broken=False, complete=False))
    assert result.outcome is Outcome.NOT_RUN
    assert "never driven" in result.detail


def test_a_surviving_equivalence_attack_passes() -> None:
    assert from_equivalence(an_equivalence(broken=False)).outcome is Outcome.PASSED


def test_a_detected_cheat_is_a_concern_and_not_a_broken_patch() -> None:
    """Nothing here produces a case the Surgeon can run: *the improvement only
    exists warm* is a judgement about measurements, and handing it back as a
    failing input would hand back an input that does not exist."""
    detected = tuple(
        Check(
            cheat=item,
            finding=Finding.DETECTED if item is Cheat.CACHED_STATE else Finding.NOT_DETECTED,
            reason="the later passes are free",
        )
        for item in Cheat
    )
    result = from_cheat(a_cheat_audit(checks=detected))
    assert result.outcome is Outcome.SUSPECT
    assert result.reproduction is None
    assert "cached_state" in result.detail


def test_an_untested_cheat_class_is_not_run_rather_than_passed() -> None:
    untested = tuple(
        Check(
            cheat=item,
            finding=Finding.UNTESTED if item is Cheat.OVER_FETCH else Finding.NOT_DETECTED,
            reason="no work counter",
        )
        for item in Cheat
    )
    result = from_cheat(a_cheat_audit(checks=untested))
    assert result.outcome is Outcome.NOT_RUN
    assert "over_fetch" in result.detail


def test_an_improvement_that_only_exists_warm_is_a_concern_on_its_own() -> None:
    """**The one path that needs its own case.** A warm-only gain usually trips the
    caching check too, and every other fixture here reaches this branch with a
    detected cheat already beside it — so the branch was unreachable and a sabotage
    deleting it changed nothing. S-11.3 needed exactly this shape for the same
    reason."""
    audit = a_cheat_audit(warm_only=True)
    assert not audit.detected, "no class fired"
    assert audit.complete
    assert audit.survives_a_fresh_process is False

    result = from_cheat(audit)
    assert result.outcome is Outcome.SUSPECT
    assert "gone on a cold one" in result.detail


def test_a_clean_cheat_audit_passes() -> None:
    assert from_cheat(a_cheat_audit()).outcome is Outcome.PASSED


def test_an_undeclared_envelope_breach_is_a_concern() -> None:
    """F10's finding, as a verdict input. Whether memory tripling is acceptable is
    a question about the deployment, and no test this system writes answers it."""
    result = from_trades(a_trade_audit(breaches=(PEAK_RSS_BYTES,)))
    assert result.outcome is Outcome.SUSPECT
    assert PEAK_RSS_BYTES in result.detail
    assert result.reproduction is None


def test_a_broken_declared_guard_is_a_concern_too() -> None:
    result = from_trades(a_trade_audit(broken_guard=True))
    assert result.outcome is Outcome.SUSPECT
    assert "declared guard was broken" in result.detail


def test_an_unread_envelope_resource_is_not_run() -> None:
    result = from_trades(a_trade_audit(unread=(PEAK_RSS_BYTES,)))
    assert result.outcome is Outcome.NOT_RUN


def test_a_clean_trade_audit_passes() -> None:
    assert from_trades(a_trade_audit()).outcome is Outcome.PASSED


def test_a_suite_broken_by_the_patch_is_the_second_source_of_a_reproduction() -> None:
    """The command is the reproduction — the thing somebody runs to see it again."""
    result = from_scope(a_scope_audit(suite=SuiteOutcome.BROKEN_BY_THE_PATCH), suite_command=SUITE)
    assert result.outcome is Outcome.BROKE_IT
    assert result.reproduction is not None
    assert result.reproduction.how == "pytest -q"


def test_callers_outside_the_evidence_are_a_concern_not_a_break() -> None:
    result = from_scope(a_scope_audit(outside=True), suite_command=SUITE)
    assert result.outcome is Outcome.SUSPECT
    assert "reports/exports.py" in result.detail


def test_a_suite_that_was_already_broken_is_not_run() -> None:
    """It establishes nothing in either direction, so it cannot be a pass."""
    result = from_scope(a_scope_audit(suite=SuiteOutcome.ALREADY_BROKEN), suite_command=SUITE)
    assert result.outcome is Outcome.NOT_RUN


def test_an_unreadable_touched_file_is_not_run() -> None:
    result = from_scope(a_scope_audit(unreadable=True), suite_command=SUITE)
    assert result.outcome is Outcome.NOT_RUN


def test_a_clean_scope_audit_passes() -> None:
    assert from_scope(a_scope_audit(), suite_command=SUITE).outcome is Outcome.PASSED


def test_a_weak_test_makes_the_verification_suspect_not_the_patch() -> None:
    """Nothing here says the change is wrong. It says the thing that judged the
    change would not have noticed if it were — a different sentence, and it goes to
    a human rather than back to a Surgeon with nothing to fix."""
    result = from_test_quality(a_test_audit(sound=False))
    assert result.outcome is Outcome.SUSPECT
    assert result.reproduction is None
    assert "not judged by it" in result.detail


def test_a_sound_test_audit_passes() -> None:
    assert from_test_quality(a_test_audit(sound=True)).outcome is Outcome.PASSED


# ============ AC 3 — two rounds, then escalate


def test_clean_ships_and_spends_no_round() -> None:
    budget = a_budget()
    routing = route(budget, verdict_for(all_passing()), FINDING)
    assert routing.route is Route.SHIP
    assert budget.used(Phase.PATCH_AUDIT, FINDING) == 0


def test_suspicious_escalates_and_spends_no_round() -> None:
    """There is nothing here to re-run, so returning it to the Surgeon would only
    produce another guess."""
    results = all_passing()
    results[2] = a_result(Attack.TRADE, Outcome.SUSPECT, detail="peak rss tripled")
    budget = a_budget()
    routing = route(budget, verdict_for(results), FINDING)
    assert routing.route is Route.ESCALATE
    assert budget.used(Phase.PATCH_AUDIT, FINDING) == 0


def test_broken_returns_to_the_surgeon_while_a_round_remains() -> None:
    results = all_passing()
    results[0] = a_result(Attack.EQUIVALENCE, Outcome.BROKE_IT, detail="differs", repro=True)
    verdict = verdict_for(results)
    budget = a_budget()

    first = route(budget, verdict, FINDING)
    assert first.route is Route.RETURN_TO_SURGEON
    record(budget, verdict, FINDING)

    second = route(budget, verdict, FINDING)
    assert second.route is Route.RETURN_TO_SURGEON
    record(budget, verdict, FINDING)


def test_a_third_round_escalates_instead_of_cycling() -> None:
    """**AC 3.** A patch that keeps coming back broken stops coming back. S-5.4's
    cap, and S-11.1 wired it without this caller."""
    results = all_passing()
    results[0] = a_result(Attack.EQUIVALENCE, Outcome.BROKE_IT, detail="differs", repro=True)
    verdict = verdict_for(results)
    budget = a_budget()

    for _ in range(PHASE_CAPS[Phase.PATCH_AUDIT].limit):
        assert route(budget, verdict, FINDING).route is Route.RETURN_TO_SURGEON
        record(budget, verdict, FINDING)

    final = route(budget, verdict, FINDING)
    assert final.route is Route.ESCALATE
    assert "both audit rounds are spent" in final.because


def test_a_clean_verdict_ships_even_with_both_rounds_spent() -> None:
    """**`authorize_round` checks the cap; it does not record**, so a round nobody
    spends is invisible to `used`. What is visible is that shipping still works
    when the cap is gone — a `clean` verdict that consulted the cap would raise
    here instead of shipping."""
    budget = a_budget()
    broken = all_passing()
    broken[0] = a_result(Attack.EQUIVALENCE, Outcome.BROKE_IT, detail="differs", repro=True)
    spent = verdict_for(broken)
    for _ in range(PHASE_CAPS[Phase.PATCH_AUDIT].limit):
        route(budget, spent, FINDING)
        record(budget, spent, FINDING)

    assert route(budget, verdict_for(all_passing()), FINDING).route is Route.SHIP


def test_a_suspicious_verdict_escalates_even_with_both_rounds_spent() -> None:
    budget = a_budget()
    broken = all_passing()
    broken[0] = a_result(Attack.EQUIVALENCE, Outcome.BROKE_IT, detail="differs", repro=True)
    spent = verdict_for(broken)
    for _ in range(PHASE_CAPS[Phase.PATCH_AUDIT].limit):
        route(budget, spent, FINDING)
        record(budget, spent, FINDING)

    worried = all_passing()
    worried[2] = a_result(Attack.TRADE, Outcome.SUSPECT, detail="peak rss tripled")
    assert route(budget, verdict_for(worried), FINDING).route is Route.ESCALATE


def test_the_cap_is_two_rounds() -> None:
    assert PHASE_CAPS[Phase.PATCH_AUDIT].limit == 2


def test_a_round_is_recorded_under_its_verdict() -> None:
    """S-11.1 left the conclusion to the caller in as many words, because S-11.2 to
    S-11.5 had not defined their verdicts. They have, so this module is it."""
    budget = a_budget()
    record(budget, PatchVerdict(verdict=Verdict.CLEAN), FINDING)
    assert budget.used(Phase.PATCH_AUDIT, FINDING) == 1


def test_the_routing_report_carries_the_verdict_and_the_reason() -> None:
    results = all_passing()
    results[0] = a_result(Attack.EQUIVALENCE, Outcome.BROKE_IT, detail="differs", repro=True)
    described = route(a_budget(), verdict_for(results), FINDING).describe()
    assert "back to the Surgeon" in described
    assert "Run this to see it again" in described
    assert "PATCH AUDIT" in described

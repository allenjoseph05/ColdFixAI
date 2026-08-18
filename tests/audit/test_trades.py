"""Epic 11, S-11.4 — trade attacks.

*Checks the global resource envelope, not just declared guard pairs. Reports what
increased alongside what decreased.*

`08-audit.md` F10 is what these tests are about: a guard pair catches the trade
somebody predicted, and the ones worth catching are the ones nobody listed. The
sharp case is a patch that **passes every guard it declared** and still bought its
speed with memory — the guards held, and something got worse anyway.

The envelope samples are built by hand rather than measured, because S-3.8 owns
the measuring and already tests it against real processes. What is under test here
is what gets concluded from two of them.
"""

from __future__ import annotations

import pytest

from coldfix.audit import trades
from coldfix.audit.trades import (
    RESIDUE,
    Direction,
    Movement,
    Trade,
    TradeAudit,
    TradeError,
    audit_trades,
    uncovered_by,
)
from coldfix.primitives import envelope
from coldfix.primitives.envelope import (
    ALLOCATED_BLOCKS,
    BYTES_WRITTEN,
    CPU_SECONDS,
    ENVELOPE,
    OPEN_FILE_DESCRIPTORS,
    PEAK_RSS_BYTES,
    PROCESS_COUNT,
    THREAD_COUNT,
    WALL_SECONDS,
    Availability,
    EnvelopeSample,
)
from coldfix.repair.falsification import CostClaim, Guard

SECONDS = "seconds"
QUERIES = "db.query"
ROWS = "rows"

QUIET = {
    WALL_SECONDS: 1.0,
    CPU_SECONDS: 1.0,
    PEAK_RSS_BYTES: 100_000_000.0,
    ALLOCATED_BLOCKS: 5_000.0,
    BYTES_WRITTEN: 0.0,
    OPEN_FILE_DESCRIPTORS: 20.0,
    THREAD_COUNT: 4.0,
    PROCESS_COUNT: 0.0,
}


def sample(overrides: dict[str, float | None] | None = None) -> EnvelopeSample:
    """One instant of the envelope, with everything readable unless said otherwise.

    **Takes a mapping, not keyword arguments, and that is not a style choice.** The
    metric names are module constants whose *values* are the keys — `THREAD_COUNT`
    is `"thread_count"` — so `sample({THREAD_COUNT: 64.0})` binds a key spelled
    `THREAD_COUNT`, leaves `thread_count` at its quiet value, and produces a sample
    that looks overridden and is not. Seven tests passed nothing to the code they
    were testing before this was caught.
    """
    metrics: dict[str, float | None] = {**QUIET, **(overrides or {})}
    return EnvelopeSample(
        metrics=metrics,
        unavailable={
            name: Availability.NEEDS_RUSAGE for name, value in metrics.items() if value is None
        },
    )


def a_claim(**overrides: object) -> CostClaim:
    fields: dict[str, object] = {
        "metric": SECONDS,
        "baseline": 8.24,
        "at_most": 2.0,
        "guards": (Guard(metric=ROWS, baseline=1000.0, at_most=1000.0),),
    }
    fields.update(overrides)
    return CostClaim(**fields)  # type: ignore[arg-type]


def audit(
    *,
    before: EnvelopeSample | None = None,
    after: EnvelopeSample | None = None,
    domain_before: dict[str, float] | None = None,
    domain_after: dict[str, float] | None = None,
    claim: CostClaim | None = None,
) -> TradeAudit:
    return audit_trades(
        before=before if before is not None else sample(),
        after=after if after is not None else sample(),
        domain_before=domain_before if domain_before is not None else {SECONDS: 8.24, ROWS: 1000.0},
        domain_after=domain_after if domain_after is not None else {SECONDS: 1.5, ROWS: 1000.0},
        claim=claim if claim is not None else a_claim(),
    )


# ============ AC 1 — the envelope, not just the declared guards


def test_a_patch_can_pass_every_guard_it_declared_and_still_be_caught() -> None:
    """**F10 as an observation rather than a warning, and the reason this story
    exists.** The Surgeon predicted the queries-against-rows trade and was right
    about it; it did not predict that the way to fewer queries was to hold the whole
    result set in memory, and nobody writes a guard against the thing they did not
    think of."""
    result = audit(
        after=sample({PEAK_RSS_BYTES: 900_000_000.0}),
        domain_before={SECONDS: 8.24, QUERIES: 101.0, ROWS: 1000.0},
        domain_after={SECONDS: 1.5, QUERIES: 2.0, ROWS: 1000.0},
    )

    assert not result.broken_guards, "every declared guard held"
    assert [outcome.held for outcome in result.guards] == [True]
    assert result.envelope.flagged, "and the envelope caught it anyway"
    assert [breach.metric for breach in result.uncovered] == [PEAK_RSS_BYTES]
    assert not result.clean
    assert "on no declared guard" in result.describe()


def test_the_envelope_check_is_s_3_8s_and_not_a_second_copy() -> None:
    """S-3.8 owns the tolerances, the absolute floors and the availability
    reporting. A second implementation of *a rise must clear a ratio and a floor*
    would be two answers to a question with one right one."""
    assert vars(trades)["compare_envelope"] is envelope.compare
    assert vars(trades)["ENVELOPE"] is envelope.ENVELOPE
    # Asserted on the bindings rather than on the source text. `"def compare" not
    # in source` is the substring check this project has now walked into four
    # times, and it would match `compare_envelope` here on its own name.
    assert not any(
        name.startswith("compare") and name != "compare_envelope" for name in vars(trades)
    )


def test_every_envelope_resource_is_examined_not_a_list_of_expected_trades() -> None:
    result = audit()
    assert set(result.envelope.checked) == set(ENVELOPE)
    assert len(ENVELOPE) == 8


def test_a_rise_inside_tolerance_is_not_a_trade() -> None:
    """S-3.8's floors, inherited. A tolerance tight enough to catch a few percent
    would flag ordinary run-to-run variation on every candidate."""
    result = audit(after=sample({PEAK_RSS_BYTES: 105_000_000.0}))
    assert not result.envelope.flagged
    assert not result.uncovered


def test_a_declared_guard_that_was_broken_is_reported_as_its_own_thing() -> None:
    """Different from an envelope breach: this is the trade somebody *did* predict,
    and it failing means the falsification test worked."""
    result = audit(
        domain_after={SECONDS: 1.5, ROWS: 50_000.0},
    )
    (broken,) = result.broken_guards
    assert broken.guard.metric == ROWS
    assert broken.measured == 50_000.0
    assert "BROKEN" in broken.describe()
    assert not result.uncovered, "nothing in the envelope moved"
    assert not result.clean


def test_a_declared_guard_nobody_measured_is_not_a_guard_that_held() -> None:
    """The quietest way a denylist fails: the metric somebody *did* think of, and
    still no answer about it."""
    result = audit(domain_after={SECONDS: 1.5})
    (outcome,) = result.guards
    assert outcome.held is None
    assert not result.broken_guards, "unevaluated is not broken either"
    assert result.unevaluated_guards
    assert not result.complete
    assert not result.clean
    assert "could not be evaluated" in result.describe()


def test_a_declared_guard_cannot_be_dropped_from_the_report() -> None:
    claim = a_claim(
        guards=(
            Guard(metric=ROWS, baseline=1000.0, at_most=1000.0),
            Guard(metric=QUERIES, baseline=101.0, at_most=101.0),
        )
    )
    result = audit(claim=claim, domain_after={SECONDS: 1.5, ROWS: 1000.0, QUERIES: 2.0})
    assert {outcome.guard.metric for outcome in result.guards} == {ROWS, QUERIES}

    with pytest.raises(TradeError, match="going unanswered"):
        TradeAudit(
            claim=claim,
            trade=result.trade,
            guards=result.guards[:1],
            envelope=result.envelope,
            cost=result.cost,
        )


def test_a_guard_declared_on_an_envelope_resource_covers_its_breach() -> None:
    """**The other side of `uncovered`, and without it the property is untested.**
    Every earlier case declares guards on domain metrics only, where *not covered*
    and *every breach* are the same list — so an implementation that skipped the
    check entirely passed. A guard aimed at the envelope is what separates them.
    """
    watching = a_claim(guards=(Guard(metric=PEAK_RSS_BYTES, baseline=1e8, at_most=2e8),))
    result = audit(
        after=sample({PEAK_RSS_BYTES: 900_000_000.0, THREAD_COUNT: 64.0}),
        domain_after={SECONDS: 1.5, PEAK_RSS_BYTES: 9e8},
        claim=watching,
    )

    assert {breach.metric for breach in result.envelope.breaches} == {
        PEAK_RSS_BYTES,
        THREAD_COUNT,
    }
    assert [breach.metric for breach in result.uncovered] == [THREAD_COUNT]
    assert result.broken_guards, "the declared one is reported as a broken guard instead"


def test_a_guard_is_measured_against_its_allowance_and_not_its_baseline() -> None:
    """**A tolerated regression is the only case that separates the two.** Every other
    guard here has `at_most` equal to `baseline`, where comparing against either
    gives the same answer — a fixture that cannot discriminate, and a sabotage
    swapping one for the other survived it.
    """
    tolerated = a_claim(guards=(Guard(metric=ROWS, baseline=1000.0, at_most=1200.0),))
    within = audit(claim=tolerated, domain_after={SECONDS: 1.5, ROWS: 1100.0})
    (outcome,) = within.guards
    assert outcome.measured == 1100.0
    assert outcome.measured > outcome.guard.baseline, "worse than before"
    assert outcome.held is True, "and still inside what was allowed"
    assert not within.broken_guards

    beyond = audit(claim=tolerated, domain_after={SECONDS: 1.5, ROWS: 1300.0})
    assert beyond.broken_guards


def test_uncovered_by_answers_f10s_question_without_building_an_audit() -> None:
    result = audit(after=sample({PEAK_RSS_BYTES: 900_000_000.0, THREAD_COUNT: 64.0}))
    assert set(uncovered_by(a_claim(), result.envelope.breaches)) == {
        PEAK_RSS_BYTES,
        THREAD_COUNT,
    }

    watched = a_claim(guards=(Guard(metric=PEAK_RSS_BYTES, baseline=1.0, at_most=2.0),))
    assert set(uncovered_by(watched, result.envelope.breaches)) == {THREAD_COUNT}


# ============ AC 2 — what increased alongside what decreased


def test_the_report_carries_both_directions() -> None:
    """**AC 2.** S-3.8 records only rises because a flag is a verdict; this is a
    report, and a rise with nothing beside it means something different."""
    result = audit(
        after=sample({PEAK_RSS_BYTES: 900_000_000.0}),
        domain_before={SECONDS: 8.24, QUERIES: 101.0, ROWS: 1000.0},
        domain_after={SECONDS: 1.5, QUERIES: 2.0, ROWS: 1000.0},
    )

    rose = {item.metric for item in result.trade.rose}
    fell = {item.metric for item in result.trade.fell}
    assert PEAK_RSS_BYTES in rose
    assert {SECONDS, QUERIES} <= fell
    assert result.trade.is_a_trade
    assert not result.trade.is_a_regression

    described = result.describe()
    assert "What rose:" in described
    assert "What it bought:" in described


def test_a_rise_with_nothing_falling_is_a_regression_and_says_so() -> None:
    """Two sentences that send a reader somewhere different, and an audit printing
    only the rises makes them indistinguishable."""
    result = audit(
        after=sample({PEAK_RSS_BYTES: 900_000_000.0}),
        domain_before={SECONDS: 8.24, ROWS: 1000.0},
        domain_after={SECONDS: 8.24, ROWS: 1000.0},
    )
    assert result.trade.is_a_regression
    assert not result.trade.is_a_trade
    assert "not a trade, it is a regression" in result.describe()


def test_a_cost_metric_that_did_not_fall_is_called_out() -> None:
    result = audit(
        domain_before={SECONDS: 8.24, ROWS: 1000.0},
        domain_after={SECONDS: 9.0, ROWS: 1000.0},
    )
    assert result.cost.direction is Direction.ROSE
    assert "cost metric did not fall" in result.describe()


def test_an_unmeasured_cost_metric_is_refused() -> None:
    """Every trade here is a cost paid for an improvement. Without the improvement
    there is nothing to weigh anything against."""
    with pytest.raises(TradeError, match="nothing to weigh anything against"):
        audit(domain_after={ROWS: 1000.0})


def test_a_movement_from_nothing_has_no_ratio() -> None:
    """Reporting `inf` would put a number meaning *undefined* into a report."""
    assert Movement(metric=BYTES_WRITTEN, before=0.0, after=4096.0).ratio is None
    assert Movement(metric=BYTES_WRITTEN, before=0.0, after=4096.0).direction is Direction.ROSE
    assert Movement(metric=ROWS, before=None, after=1.0).direction is Direction.UNMEASURED


def test_an_unchanged_metric_is_neither_bought_nor_paid_for() -> None:
    result = audit()
    moved = {item.metric for item in result.trade.rose} | {
        item.metric for item in result.trade.fell
    }
    assert ROWS not in moved, "1000 rows before and after is not a movement in either list"


# ============ the two sources of metrics do not shadow each other


def test_a_domain_counter_sharing_an_envelope_name_is_reported_twice() -> None:
    """**Silently, and in the direction of the patch looking better.** A domain timer
    measures the window and the envelope measures the process, so one mapping would
    let the smaller number overwrite the larger."""
    result = audit(
        before=sample({WALL_SECONDS: 10.0}),
        after=sample({WALL_SECONDS: 40.0}),
        domain_before={SECONDS: 8.24, ROWS: 1000.0, WALL_SECONDS: 8.0},
        domain_after={SECONDS: 1.5, ROWS: 1000.0, WALL_SECONDS: 1.4},
        claim=a_claim(),
    )
    names = {item.metric for item in result.trade.rose} | {
        item.metric for item in result.trade.fell
    }
    assert WALL_SECONDS in names, "the envelope's, which rose"
    assert f"workload.{WALL_SECONDS}" in names, "and the workload's, which fell"

    (envelope_timer,) = [item for item in result.trade.rose if item.metric == WALL_SECONDS]
    assert envelope_timer.after == 40.0


# ============ unmeasured is not within tolerance


def test_an_envelope_resource_that_could_not_be_read_blocks_a_clean_verdict() -> None:
    """S-3.8 refuses to let a metric it could not read pass quietly, and that has to
    survive into the verdict — an audit that never saw peak RSS has not cleared the
    memory trade."""
    blind = sample({PEAK_RSS_BYTES: None, BYTES_WRITTEN: None})
    result = audit(before=blind, after=blind)

    assert not result.envelope.flagged, "nothing that was read had risen"
    assert not result.broken_guards
    assert set(result.unmeasured) == {PEAK_RSS_BYTES, BYTES_WRITTEN}
    assert not result.complete
    assert not result.clean, "five of eight checked is not a clean envelope"
    assert "never read" in result.describe()
    assert "Not within tolerance — unseen" in result.describe()


def test_a_patch_that_traded_nothing_and_was_fully_measured_is_a_null_result() -> None:
    result = audit(
        domain_before={SECONDS: 8.24, QUERIES: 101.0, ROWS: 1000.0},
        domain_after={SECONDS: 1.5, QUERIES: 2.0, ROWS: 1000.0},
    )
    assert result.complete
    assert result.clean
    assert not result.uncovered
    assert "null result" in result.describe()


def test_the_three_conditions_on_clean_are_independently_observable() -> None:
    """S-11.3 recorded this the hard way: a `clean` whose failures are always
    overdetermined is a `clean` whose clauses nobody has checked. One case each."""
    only_guard = audit(domain_after={SECONDS: 1.5, ROWS: 50_000.0})
    assert only_guard.complete and not only_guard.envelope.flagged
    assert only_guard.broken_guards and not only_guard.clean

    only_envelope = audit(after=sample({THREAD_COUNT: 64.0}))
    assert only_envelope.complete and not only_envelope.broken_guards
    assert only_envelope.envelope.flagged and not only_envelope.clean

    blind = sample({PEAK_RSS_BYTES: None})
    only_coverage = audit(before=blind, after=blind)
    assert not only_coverage.broken_guards and not only_coverage.envelope.flagged
    assert not only_coverage.complete and not only_coverage.clean


def test_the_report_states_what_neither_list_reaches() -> None:
    assert RESIDUE in audit().describe()
    assert "still a list" in RESIDUE


def test_a_trade_with_neither_side_is_neither_a_trade_nor_a_regression() -> None:
    nothing = Trade(fell=(), rose=())
    assert not nothing.is_a_trade
    assert not nothing.is_a_regression
    assert "Nothing rose" in nothing.describe()

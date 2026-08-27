"""S-5.3: the eight fields, the three cuts, and the number that cannot be a number.

The arithmetic here is easy and almost none of the risk lives in it. What a cost
ledger gets wrong is *what it counts*, and every way of getting that wrong
produces a plausible bill rather than an error:

- collapsing the two cache figures into one "cached tokens", which loses the
  sign — a write costs more than not caching, a read costs a tenth — and makes
  the bill unrecoverable in the flattering direction;
- reading `input_tokens` as the prompt when it is only the uncached remainder;
- pricing an unrecognised model at a default;
- letting an agent supply a cost instead of computing it from tokens;
- dividing by a finding count that can legitimately be zero;
- quoting euros with no rate and no date.

So the tests are organised by which of those a number has to survive. The last
section is AC 3 end to end: a run priced from `04-cost.md` §12.3's engineered
case, and the same run with nothing confirmed.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coldfix.cost.accounting import (
    BATCH_MULTIPLIER,
    PRICE_BOOK,
    PRICE_BOOK_AS_OF,
    AccountingError,
    Agent,
    ExchangeRate,
    Ledger,
    ModelCall,
    Phase,
    ProjectReport,
    RunReport,
    StepClass,
    TokenUsage,
    UnknownModelError,
    price_of,
    total_of,
)

AT = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
RATE = ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 9))

# One million of each kind, so every assertion below reads as the published rate
# rather than as arithmetic somebody has to redo by hand.
MILLION = 1_000_000


def call(**overrides: object) -> ModelCall:
    fields: dict[str, object] = {
        "phase": Phase.INVESTIGATE,
        "agent": Agent.DIAGNOSTICIAN,
        "step_class": StepClass.MECHANICAL,
        "model": "claude-opus-5",
        "usage": TokenUsage(input_tokens=0, output_tokens=0),
        "at": AT,
    }
    return ModelCall(**{**fields, **overrides})  # type: ignore[arg-type]


# ------------------------------------------- the two cache figures are not one


def test_a_cache_write_costs_more_than_not_caching_at_all() -> None:
    """The half of prompt caching that gets forgotten.

    A write bills at 1.25x input on the five-minute TTL — *more* than sending the
    tokens uncached. Anything that treats "cached" as a synonym for "discounted"
    under-bills every cold prefix in the run, which is every first call of every
    investigation.
    """
    uncached = call(usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
    written = call(
        usage=TokenUsage(input_tokens=0, output_tokens=0, cache_creation_input_tokens=MILLION)
    )

    assert uncached.cost_usd == Decimal("5.00")
    assert written.cost_usd == Decimal("6.25")
    assert written.cost_usd > uncached.cost_usd


def test_a_cache_read_costs_a_tenth() -> None:
    """The half everybody remembers, and the reason the append-only log exists."""
    read = call(usage=TokenUsage(input_tokens=0, output_tokens=0, cache_read_input_tokens=MILLION))

    assert read.cost_usd == Decimal("0.50")


def test_the_hour_ttl_write_costs_more_than_the_five_minute_one() -> None:
    """2x against 1.25x — a 60% difference on every cached prefix.

    Recorded per call rather than assumed, because a run that took the hour TTL
    and was priced at the five-minute rate is under-billed by that margin and
    nothing in the totals looks wrong.
    """
    short = call(
        usage=TokenUsage(
            input_tokens=0, output_tokens=0, cache_creation_input_tokens=MILLION, cache_ttl="5m"
        )
    )
    long = call(
        usage=TokenUsage(
            input_tokens=0, output_tokens=0, cache_creation_input_tokens=MILLION, cache_ttl="1h"
        )
    )

    assert short.cost_usd == Decimal("6.25")
    assert long.cost_usd == Decimal("10.00")


def test_the_two_cache_kinds_cannot_be_collapsed_into_one_number() -> None:
    """The story's central point, as an arithmetic fact.

    A million cache-write tokens and a million cache-read tokens are both "a
    million cached tokens" and cost 12.5x different amounts. There is no function
    from the collapsed figure to the bill, which is why no field in this module
    holds one.
    """
    written = call(
        usage=TokenUsage(input_tokens=0, output_tokens=0, cache_creation_input_tokens=MILLION)
    )
    read = call(usage=TokenUsage(input_tokens=0, output_tokens=0, cache_read_input_tokens=MILLION))

    assert written.usage.prompt_tokens == read.usage.prompt_tokens
    assert written.cost_usd != read.cost_usd


def test_input_tokens_is_the_uncached_remainder_not_the_prompt() -> None:
    """The field-name trap, stated as the property that protects against it.

    An agent that ran for an hour and reports 4k `input_tokens` did not process
    4k tokens. `prompt_tokens` is the sum, and anything reporting prompt volume
    has to use it.
    """
    usage = TokenUsage(
        input_tokens=1_000,
        output_tokens=500,
        cache_creation_input_tokens=2_000,
        cache_read_input_tokens=40_000,
    )

    assert usage.prompt_tokens == 43_000
    assert usage.input_tokens != usage.prompt_tokens


def test_the_cache_hit_rate_is_over_the_whole_prompt() -> None:
    """S-5.7 reports this; it is derived rather than stored, so it cannot
    disagree with the tokens it came from."""
    usage = TokenUsage(input_tokens=1_000, output_tokens=0, cache_read_input_tokens=9_000)

    assert usage.cache_hit_rate == pytest.approx(0.9)


def test_an_empty_prompt_has_no_hit_rate_rather_than_zero() -> None:
    """Nothing measured and nothing hit are different states — S-4.2's rule for a
    metric that starts at zero, applied to a rate."""
    assert TokenUsage(input_tokens=0, output_tokens=100).cache_hit_rate is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 0, "output_tokens": -5},
        {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": -2},
    ],
)
def test_negative_token_counts_are_refused(usage: dict[str, Any]) -> None:
    """A negative count is a subtraction somebody did upstream, and it would
    credit the run for tokens it spent."""
    with pytest.raises(AccountingError, match="negative"):
        TokenUsage(**usage)


def test_an_unrecognised_cache_ttl_is_refused() -> None:
    """The write multiplier is chosen by TTL, so an unknown one has no price —
    and guessing would misprice every cached prefix in the run."""
    with pytest.raises(AccountingError, match="not a cache TTL"):
        TokenUsage(input_tokens=0, output_tokens=0, cache_ttl="7d")


# --------------------------------------------------- the price is not guessed


def test_every_model_in_the_book_prices_at_its_published_rate() -> None:
    """AC 1's `model` field is only worth recording if it selects a rate."""
    assert price_of("claude-opus-5").input_usd == Decimal("5.00")
    assert price_of("claude-sonnet-5").output_usd == Decimal("15.00")
    assert price_of("claude-haiku-4-5").input_usd == Decimal("1.00")


def test_an_unknown_model_is_refused_rather_than_priced() -> None:
    """A default rate produces a bill indistinguishable from a real one.

    And the error is signed: the first model this system meets that is not in the
    book will be a newer, dearer one, so a default under-bills.
    """
    with pytest.raises(UnknownModelError, match="no price is recorded"):
        _ = call(model="claude-opus-6").cost_usd


def test_fast_mode_is_a_separate_entry_at_twice_the_rate() -> None:
    """The same model id at two prices, which a book keyed on the id alone
    cannot express — it would bill a fast-mode run at half what it cost."""
    standard = call(model="claude-opus-5", usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
    fast = call(model="claude-opus-5/fast", usage=TokenUsage(input_tokens=MILLION, output_tokens=0))

    assert fast.cost_usd == standard.cost_usd * 2


def test_the_book_records_the_day_it_was_read() -> None:
    """ADR 002 says to re-check rates before publishing any cost figure. A date
    is what makes a stale table visible rather than merely wrong."""
    assert PRICE_BOOK_AS_OF.year >= 2026
    assert set(PRICE_BOOK) >= {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}


def test_the_batch_api_halves_the_bill() -> None:
    """`04-cost.md` §8: 50% off for anything not latency-sensitive, which is
    every evaluation run and every agreement study."""
    usage = TokenUsage(input_tokens=MILLION, output_tokens=MILLION)

    assert call(usage=usage, batched=True).cost_usd == call(usage=usage).cost_usd * BATCH_MULTIPLIER


def test_output_tokens_bill_at_five_times_input() -> None:
    """`04-cost.md` §7's whole argument for structured output — the reason the
    two rates are separate fields rather than one blended number."""
    inputs = call(usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
    outputs = call(usage=TokenUsage(input_tokens=0, output_tokens=MILLION))

    assert outputs.cost_usd == inputs.cost_usd * 5


def test_a_call_has_no_cost_field_to_set() -> None:
    """`CLAUDE.md`: an agent never reports a measurement, and a cost is one.

    S-4.1 closed this once already for `work_verified`, where the flaw was that
    an agent decided. Here the incentive is the same shape — a cheaper number is
    a better-looking run — so cost is a property over tokens and a price, with
    no field behind it.
    """
    with pytest.raises(TypeError):
        call(cost_usd=Decimal("0.01"))


def test_money_is_exact_rather_than_binary_floating_point() -> None:
    """A cent is not representable in binary floating point, and a run is ~250
    calls. Summed as floats these three land at 0.30000000000000004."""
    calls = [
        call(usage=TokenUsage(input_tokens=20_000, output_tokens=0)),
        call(usage=TokenUsage(input_tokens=20_000, output_tokens=0)),
        call(usage=TokenUsage(input_tokens=20_000, output_tokens=0)),
    ]

    assert total_of(calls) == Decimal("0.30")


# ---------------------------------------------------- AC 1 and AC 2: the cuts


def test_a_call_records_every_field_the_story_asks_for() -> None:
    """AC 1, read off one object — with `cached tokens` deliberately arriving as
    the two the API reports rather than the one the story names."""
    recorded = call(
        phase=Phase.REPAIR,
        agent=Agent.SURGEON,
        step_class=StepClass.CREATIVE,
        model="claude-opus-5",
        usage=TokenUsage(
            input_tokens=1_000,
            output_tokens=2_000,
            cache_creation_input_tokens=3_000,
            cache_read_input_tokens=4_000,
        ),
        finding_id="n.plus.one",
    )

    assert recorded.phase is Phase.REPAIR
    assert recorded.agent is Agent.SURGEON
    assert recorded.step_class is StepClass.CREATIVE
    assert recorded.model == "claude-opus-5"
    assert recorded.usage.input_tokens == 1_000
    assert recorded.usage.output_tokens == 2_000
    assert recorded.usage.cache_creation_input_tokens == 3_000
    assert recorded.usage.cache_read_input_tokens == 4_000
    assert recorded.cost_usd > 0


def test_cost_is_queryable_per_phase() -> None:
    """AC 2, first cut."""
    ledger = Ledger()
    ledger.record(call(phase=Phase.GROUND, usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))
    ledger.record(
        call(phase=Phase.INVESTIGATE, usage=TokenUsage(input_tokens=2 * MILLION, output_tokens=0))
    )

    per_phase = ledger.by_phase()

    assert per_phase[Phase.GROUND] == Decimal("5.00")
    assert per_phase[Phase.INVESTIGATE] == Decimal("10.00")


def test_cost_is_queryable_per_finding() -> None:
    """AC 2, second cut."""
    ledger = Ledger()
    ledger.record(
        call(finding_id="n.plus.one", usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
    )
    ledger.record(
        call(finding_id="over.fetch", usage=TokenUsage(input_tokens=2 * MILLION, output_tokens=0))
    )

    per_finding = ledger.by_finding()

    assert per_finding["n.plus.one"] == Decimal("5.00")
    assert per_finding["over.fetch"] == Decimal("10.00")


def test_grounding_is_not_split_across_findings() -> None:
    """`04-cost.md` §11: grounding happens once per repository, not once per
    finding.

    Split across findings it would make each finding look dearer than it was; a
    ledger that demanded a finding id would have to invent one, and the invented
    one would collect the whole grounding bill.
    """
    ledger = Ledger()
    ledger.record(call(phase=Phase.GROUND, usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))
    ledger.record(
        call(finding_id="n.plus.one", usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
    )

    assert ledger.by_finding() == {"n.plus.one": Decimal("5.00")}
    assert ledger.unattributed_usd == Decimal("5.00")


def test_the_per_finding_table_reconciles_against_the_run_total() -> None:
    """The assertion that stops a per-finding table quietly costing less than
    the run it came from.

    Trivially true today, and the first time a call is attributed two ways or a
    phase is left out of a cut, this is the only thing that notices.
    """
    ledger = Ledger()
    ledger.record(call(phase=Phase.GROUND, usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))
    ledger.record(
        call(finding_id="a", usage=TokenUsage(input_tokens=MILLION, output_tokens=MILLION))
    )
    ledger.record(call(finding_id="b", usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))

    assert ledger.reconciles
    assert sum(ledger.by_finding().values()) + ledger.unattributed_usd == ledger.total_usd


def test_the_ledger_sums_tokens_as_well_as_money() -> None:
    """A run report quotes volume beside cost, and the volume has to come from
    the same records the cost did."""
    ledger = Ledger()
    ledger.record(
        call(usage=TokenUsage(input_tokens=100, output_tokens=10, cache_read_input_tokens=900))
    )
    ledger.record(
        call(usage=TokenUsage(input_tokens=200, output_tokens=20, cache_read_input_tokens=800))
    )

    usage = ledger.usage()

    assert usage.prompt_tokens == 2_000
    assert usage.cache_read_input_tokens == 1_700
    assert usage.output_tokens == 30


def test_the_ledger_keeps_calls_in_the_order_they_were_made() -> None:
    """Append-only, for a weaker version of the experiment log's reason: a total
    assembled from a list somebody reordered is one nobody can check against an
    invoice."""
    ledger = Ledger()
    first = call(phase=Phase.GROUND)
    second = call(phase=Phase.REPAIR)
    ledger.record(first)
    ledger.record(second)

    assert ledger.calls == [first, second]


# ------------------------------------------- AC 3: euros per confirmed finding


def test_a_run_report_gives_euros_per_confirmed_finding() -> None:
    """AC 3."""
    ledger = Ledger()
    ledger.record(call(usage=TokenUsage(input_tokens=2 * MILLION, output_tokens=0)))

    report = RunReport(ledger=ledger, confirmed_findings=2, rate=RATE)

    assert report.ledger.total_usd == Decimal("10.00")
    assert report.total_eur == Decimal("9.20")
    assert report.eur_per_confirmed_finding == Decimal("4.60")


def test_a_run_that_confirmed_nothing_has_no_cost_per_finding() -> None:
    """The division this story exists to get right.

    S-4.5 ships *screened nine workloads, nothing found* as an answer, so a run
    with zero confirmed findings is a **successful** run that cost real money.
    The ratio is undefined — which is not zero (it cost something), not the total
    (that is the run, not a per-finding figure), and not infinity.
    """
    ledger = Ledger()
    ledger.record(call(usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))

    report = RunReport(ledger=ledger, confirmed_findings=0, rate=RATE)

    assert report.eur_per_confirmed_finding is None
    assert report.total_eur > 0


def test_the_null_result_report_says_the_run_still_cost_something() -> None:
    """A reader who sees no per-finding figure must not read it as a free run."""
    ledger = Ledger()
    ledger.record(call(usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))

    rendered = RunReport(ledger=ledger, confirmed_findings=0, rate=RATE).render()

    assert "not applicable" in rendered
    assert "still cost what it cost" in rendered
    assert "a null result is an answer" in rendered


def test_a_negative_finding_count_is_refused() -> None:
    """The denominator is a count, and a negative one would flip the sign of
    every euro figure in the report."""
    with pytest.raises(AccountingError, match="cannot confirm"):
        RunReport(ledger=Ledger(), confirmed_findings=-1, rate=RATE)


def test_an_exchange_rate_must_be_positive() -> None:
    with pytest.raises(AccountingError, match="must be positive"):
        ExchangeRate(euros_per_dollar=Decimal("0"), as_of=date(2026, 8, 9))


def test_the_report_carries_the_rate_and_the_date_it_was_true() -> None:
    """A euro figure whose rate is not stated is a number that expires silently.

    The same rule every other precondition in this project follows: `€2,150`
    means one thing with a rate beside it and something much weaker without.
    """
    ledger = Ledger()
    ledger.record(call(usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))

    rendered = RunReport(ledger=ledger, confirmed_findings=1, rate=RATE).render()

    assert "0.92 per $1" in rendered
    assert "2026-08-09" in rendered
    assert "prices read" in rendered


def test_the_report_breaks_the_run_down_by_phase() -> None:
    """AC 2's cuts have to reach the thing a human actually reads."""
    ledger = Ledger()
    ledger.record(call(phase=Phase.GROUND, usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))
    ledger.record(
        call(
            phase=Phase.INVESTIGATE,
            finding_id="a",
            usage=TokenUsage(input_tokens=MILLION, output_tokens=0),
        )
    )

    rendered = RunReport(ledger=ledger, confirmed_findings=1, rate=RATE).render()

    assert "ground:" in rendered
    assert "investigate:" in rendered
    assert "not attributed to any finding" in rendered


def test_an_engineered_run_lands_near_the_cost_document_s_figure() -> None:
    """`04-cost.md` §12.3 puts the engineered case at ~$15 per five-finding run.

    Not a precise reproduction of that table — it assumes model routing and
    playbooks that do not exist yet — but the ledger has to be able to *express*
    it, and a shape this far from the document would mean the arithmetic is
    measuring something else. The check is order-of-magnitude on purpose.
    """
    ledger = Ledger()
    ledger.record(
        call(
            phase=Phase.GROUND,
            agent=Agent.EXPLORER,
            model="claude-haiku-4-5",
            usage=TokenUsage(input_tokens=10 * 8_000, output_tokens=10 * 500),
        )
    )
    for finding in ("a", "b", "c", "d", "e"):
        ledger.record(
            call(
                phase=Phase.INVESTIGATE,
                step_class=StepClass.CREATIVE,
                finding_id=finding,
                usage=TokenUsage(
                    input_tokens=15 * 1_800,
                    output_tokens=15 * 1_000,
                    cache_read_input_tokens=15 * 10_200,
                ),
            )
        )
        ledger.record(
            call(
                phase=Phase.INVESTIGATE,
                model="claude-sonnet-5",
                finding_id=finding,
                usage=TokenUsage(
                    input_tokens=105 * 1_800,
                    output_tokens=105 * 1_000,
                    cache_read_input_tokens=105 * 10_200,
                ),
            )
        )

    report = RunReport(ledger=ledger, confirmed_findings=5, rate=RATE)

    assert Decimal(5) < report.ledger.total_usd < Decimal(40)
    assert report.eur_per_confirmed_finding is not None
    assert report.ledger.reconciles


def test_the_same_run_batched_costs_half() -> None:
    """Evaluation runs are not latency-sensitive, and §12.3's per-run figure
    halves to ~$7.50 when batched — the one lever that applies to the whole
    thesis workload at once."""
    usage = TokenUsage(input_tokens=MILLION, output_tokens=MILLION)
    live = Ledger()
    live.record(call(usage=usage))
    batched = Ledger()
    batched.record(call(usage=usage, batched=True))

    assert batched.total_usd == live.total_usd / 2


def test_the_accounting_layer_cannot_reach_a_model_sdk() -> None:
    """S-4.2's structural form, applied here for a different reason than usual.

    This module prices model calls and must never make one — a cost ledger that
    could call a model could bill itself, and the run report is the one artifact
    whose numbers nobody would think to question.
    """
    loaded = subprocess.run(
        [sys.executable, "-c", "import sys, coldfix.cost.accounting; print(' '.join(sys.modules))"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.split()

    roots = {name.split(".")[0] for name in loaded}
    assert not (roots & {"anthropic", "openai", "httpx", "requests"})


# ==================================== S-15.3: the model cut and the project cut


def test_cost_is_queryable_by_model() -> None:
    """The layer under the tier cut. A ledger knows models, not price bands.

    `cost.routing` imports this module, so a `by_tier` here would be a cycle —
    `Session.by_tier` maps these through the router that chose them.
    """
    ledger = Ledger()
    for model in ("claude-opus-5", "claude-opus-5", "claude-haiku-4-5"):
        ledger.record(call(model=model, usage=TokenUsage(input_tokens=MILLION, output_tokens=0)))

    by_model = ledger.by_model()

    assert by_model["claude-opus-5"] == Decimal("10.00")
    assert by_model["claude-haiku-4-5"] == Decimal("1.00")
    assert sum(by_model.values(), Decimal(0)) == ledger.total_usd


def _run(*, spent_mtok: int, confirmed: int, rate: ExchangeRate = RATE) -> RunReport:
    ledger = Ledger()
    for _ in range(spent_mtok):
        ledger.record(
            call(model="claude-opus-5", usage=TokenUsage(input_tokens=MILLION, output_tokens=0))
        )
    return RunReport(ledger=ledger, confirmed_findings=confirmed, rate=rate)


def test_a_project_costs_what_all_of_its_runs_cost() -> None:
    """AC 1's third cut. `04-cost.md` §11's unit: grounding is per repository."""
    report = ProjectReport(
        project="shop",
        runs=[_run(spent_mtok=1, confirmed=1), _run(spent_mtok=2, confirmed=1)],
        rate=RATE,
    )

    assert report.total_usd == Decimal("15.00")
    assert report.confirmed_findings == 2
    assert report.total_eur == Decimal("15.00") * Decimal("0.92")


def test_the_project_figure_is_not_the_mean_of_the_runs_ratios() -> None:
    """**The arithmetic that hides the null runs**, and it is the obvious one.

    Averaging each run's euros-per-finding weights a cheap run that found three
    equally with an expensive one that found one — and a run that found nothing
    has no ratio to average in, so it disappears from the answer entirely. The
    project figure is total euros over total findings.
    """
    cheap_and_productive = _run(spent_mtok=1, confirmed=3)
    dear_and_empty = _run(spent_mtok=9, confirmed=0)

    report = ProjectReport(project="shop", runs=[cheap_and_productive, dear_and_empty], rate=RATE)

    mean_of_ratios = cheap_and_productive.eur_per_confirmed_finding
    assert mean_of_ratios is not None
    per_finding = report.eur_per_confirmed_finding
    assert per_finding is not None
    assert per_finding > mean_of_ratios, "the empty run's spend is in the numerator"
    assert per_finding == report.total_eur / 3


def test_a_run_that_confirmed_nothing_is_counted_and_named() -> None:
    """A null result is an answer and it is not a free one."""
    report = ProjectReport(
        project="shop",
        runs=[_run(spent_mtok=1, confirmed=1), _run(spent_mtok=1, confirmed=0)],
        rate=RATE,
    )

    assert report.runs_confirming_nothing == 1
    assert "1 of 2 run(s) confirmed nothing" in report.render()
    assert report.total_usd == Decimal("10.00")


def test_a_project_that_has_confirmed_nothing_has_no_ratio() -> None:
    """`None`, for `RunReport`'s reason: the spend is real, the ratio is not."""
    report = ProjectReport(project="shop", runs=[_run(spent_mtok=1, confirmed=0)], rate=RATE)

    assert report.eur_per_confirmed_finding is None
    assert "not applicable" in report.render()
    assert report.total_usd > 0


def test_a_project_with_no_runs_is_refused() -> None:
    """Never run and found nothing are different answers, as everywhere else."""
    with pytest.raises(AccountingError, match="no runs"):
        ProjectReport(project="shop", runs=[], rate=RATE)


def test_the_total_converts_once_and_says_when_the_runs_did_not() -> None:
    """Euros are a presentation; the vendor bills dollars.

    Summing each run's euro figure adds numbers taken at several rates into a
    total nobody can reproduce, so the project converts the dollar sum once at
    its own rate — and says so when a run was reported at a different one, rather
    than quietly restating it.
    """
    older = ExchangeRate(euros_per_dollar=Decimal("0.80"), as_of=date(2026, 1, 1))
    report = ProjectReport(
        project="shop",
        runs=[_run(spent_mtok=1, confirmed=1, rate=older), _run(spent_mtok=1, confirmed=1)],
        rate=RATE,
    )

    assert report.total_eur == Decimal("10.00") * Decimal("0.92")
    rendered = report.render()
    assert "each reported at their own rate" in rendered
    assert "0.80" in rendered


def test_runs_at_one_rate_say_nothing_about_rates() -> None:
    """The note is for the case that needs it; a control so it is not always on."""
    report = ProjectReport(project="shop", runs=[_run(spent_mtok=1, confirmed=1)], rate=RATE)

    assert "each reported at their own rate" not in report.render()

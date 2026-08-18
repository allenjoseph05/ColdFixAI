"""S-5.9 (partial): list price is the wrong number, and the second column is empty.

Only AC 3 and AC 5 are in scope. AC 1, 2, 4 and 6 need a second vendor's account
and E9's finding audit, and the tests here assert that the module **says so**
rather than filling the gap with a default — a comparison that reported zero
where it had measured nothing would read as *this vendor is free*.

The hypothetical vendor below is named hypothetical and lives only in this file.
Its figures are invented to exercise the arithmetic, and inventing them in the
module would have been the exact failure — deciding a vendor from recollection —
that this story exists to end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from coldfix.cost.accounting import PRICE_BOOK, Price
from coldfix.cost.context import MINIMUM_CACHEABLE_PREFIX
from coldfix.cost.vendors import (
    ANTHROPIC,
    VENDORS,
    CachePolicy,
    Comparison,
    MeasuredRun,
    VendorError,
    VendorProfile,
    WorkloadShape,
    caches_at_all,
    cheaper_sticker_can_lose,
    effective_input_usd_per_mtok,
    effective_run_usd,
    list_run_usd,
    price_book_agrees,
    recorded_profile,
)

# §12.2's engineered investigate loop: 120 calls, 12k prompt, 85% cached. The
# shape the whole cost argument is about.
ENGINEERED = WorkloadShape(
    calls=120, prompt_tokens=12_000, output_tokens=1_000, cached_share=Decimal("0.85")
)

# **Hypothetical, and only in this test file.** 30% cheaper per token, with a
# minimum cacheable prefix four times larger — the shape the story's Notes name.
# No claim is made that any real vendor looks like this.
HYPOTHETICAL_CHEAPER = VendorProfile(
    vendor="Hypothetical",
    prices={"claude-opus-5": Price(Decimal("3.50"), Decimal("17.50"))},
    cache=CachePolicy(
        read_multiplier=Decimal("0.1"),
        write_multipliers={"5m": Decimal("1.25")},
        minimum_cacheable_prefix={"claude-opus-5": 32_000},
        prefix_match=True,
        scope="model",
    ),
    recorded_on=date(2026, 8, 9),
    source="invented for this test; not a claim about any vendor",
)


# ------------------------------- AC 3: effective cost against list price


def test_caching_makes_an_input_token_cost_a_fraction_of_its_list_price() -> None:
    """The claim the story rests on, as arithmetic.

    At 85% cached, most input tokens bill at 0.1x and the write is amortised
    across 120 calls — so the effective rate is roughly a quarter of sticker.
    """
    effective = effective_input_usd_per_mtok(ANTHROPIC, "claude-opus-5", ENGINEERED)
    listed = ANTHROPIC.price_for("claude-opus-5").input_usd

    assert effective < listed / 3
    assert effective > 0


def test_the_effective_run_costs_far_less_than_the_same_run_at_list() -> None:
    """AC 3 asks for both numbers side by side, because only the pair shows the
    size of what caching is doing."""
    effective = effective_run_usd(ANTHROPIC, "claude-opus-5", ENGINEERED)
    sticker = list_run_usd(ANTHROPIC, "claude-opus-5", ENGINEERED)

    assert effective < sticker
    assert sticker / effective > 2


def test_a_run_with_no_caching_costs_its_list_price() -> None:
    """The control. If effective cost did not converge on list at a zero hit
    rate, the model would be measuring something other than caching."""
    cold = WorkloadShape(
        calls=120, prompt_tokens=12_000, output_tokens=1_000, cached_share=Decimal(0)
    )

    assert effective_run_usd(ANTHROPIC, "claude-opus-5", cold) == list_run_usd(
        ANTHROPIC, "claude-opus-5", cold
    )


def test_a_single_call_barely_benefits_because_the_write_is_not_amortised() -> None:
    """A cache written once and never read is a loss (ADR 056).

    The write premium is spread across the calls that read it, so a one-call run
    pays it in full — which is why S-5.7 separates the first call from the rest.
    """
    once = WorkloadShape(
        calls=1, prompt_tokens=12_000, output_tokens=1_000, cached_share=Decimal("0.85")
    )
    many = WorkloadShape(
        calls=120, prompt_tokens=12_000, output_tokens=1_000, cached_share=Decimal("0.85")
    )

    assert effective_input_usd_per_mtok(ANTHROPIC, "claude-opus-5", once) > (
        effective_input_usd_per_mtok(ANTHROPIC, "claude-opus-5", many)
    )


def test_the_hour_ttl_write_is_dearer_than_the_five_minute_one() -> None:
    """AC 5 asks for the TTL recorded because it moves cost independently of
    price: 2x against 1.25x on the write."""
    short = WorkloadShape(
        calls=2, prompt_tokens=12_000, output_tokens=0, cached_share=Decimal("0.85"), cache_ttl="5m"
    )
    long = WorkloadShape(
        calls=2, prompt_tokens=12_000, output_tokens=0, cached_share=Decimal("0.85"), cache_ttl="1h"
    )

    assert effective_run_usd(ANTHROPIC, "claude-opus-5", long) > effective_run_usd(
        ANTHROPIC, "claude-opus-5", short
    )


def test_a_ttl_the_vendor_does_not_offer_is_refused() -> None:
    unknown = WorkloadShape(
        calls=2, prompt_tokens=12_000, output_tokens=0, cached_share=Decimal("0.5"), cache_ttl="7d"
    )

    with pytest.raises(VendorError, match="does not offer"):
        effective_run_usd(ANTHROPIC, "claude-opus-5", unknown)


@pytest.mark.parametrize("share", [Decimal("-0.1"), Decimal("1.5")])
def test_an_impossible_cached_share_is_refused(share: Decimal) -> None:
    with pytest.raises(VendorError, match="between 0 and 1"):
        WorkloadShape(calls=1, prompt_tokens=1, output_tokens=1, cached_share=share)


# ------------------- AC 5: the facts that move cost independently of price


def test_the_minimum_cacheable_prefix_is_recorded_per_model() -> None:
    """AC 5, and it is per model because on Anthropic it is per model — S-5.7's
    finding, not restated here but shared."""
    assert ANTHROPIC.minimum_prefix("claude-opus-5") == 512
    assert ANTHROPIC.minimum_prefix("claude-haiku-4-5") == 4096
    assert ANTHROPIC.cache.minimum_cacheable_prefix == MINIMUM_CACHEABLE_PREFIX


def test_the_cache_ttls_and_their_write_premiums_are_recorded() -> None:
    """AC 5's other half. A write above 1.0 is the fact that gets forgotten."""
    assert ANTHROPIC.write_multiplier("5m") == Decimal("1.25")
    assert ANTHROPIC.write_multiplier("1h") == Decimal("2.0")
    assert ANTHROPIC.cache.read_multiplier == Decimal("0.1")
    assert all(multiplier > 1 for multiplier in ANTHROPIC.cache.write_multipliers.values())


def test_prefix_semantics_and_cache_scope_are_recorded() -> None:
    """A vendor whose caching is not a prefix match cannot be exploited by an
    append-only log at all — so it is not merely dearer, it is a different
    architecture, and `CLAUDE.md`'s rule buys nothing there."""
    assert ANTHROPIC.cache.prefix_match is True
    assert ANTHROPIC.cache.scope == "model"


def test_the_profile_carries_its_date_and_source() -> None:
    """A profile with no provenance is memory with a dataclass around it, which
    is what ADR-002 was criticised for."""
    assert ANTHROPIC.recorded_on == date(2026, 8, 9)
    assert "platform.claude.com" in ANTHROPIC.source


def test_the_profile_is_derived_from_the_price_book_rather_than_copied() -> None:
    """One source of truth. A second copy is a second thing to go stale, and
    going stale is the failure this story exists to prevent."""
    assert ANTHROPIC.prices == dict(PRICE_BOOK)
    assert price_book_agrees(ANTHROPIC)


def test_the_agreement_guard_can_actually_fail() -> None:
    """Found by sabotage: the guard took no argument, so it could only ever
    return `True` and a version that always did was indistinguishable.

    S-3.12's finding for the fourth time in this project — a guard nothing can
    make fail is a guard nobody has checked.
    """
    assert not price_book_agrees(HYPOTHETICAL_CHEAPER)


def test_a_model_with_no_recorded_price_is_refused() -> None:
    with pytest.raises(VendorError, match="no recorded price"):
        ANTHROPIC.price_for("claude-opus-9")


def test_a_model_with_no_recorded_minimum_is_refused() -> None:
    """Below the minimum nothing caches with no error to say so, so a default
    here would hide a silent failure."""
    with pytest.raises(VendorError, match="no recorded minimum cacheable prefix"):
        ANTHROPIC.minimum_prefix("claude-opus-9")


# ---------------- the Notes' claim: a cheaper sticker price can lose


def test_a_prompt_below_the_minimum_does_not_cache_however_good_it_is() -> None:
    """The mechanism behind the claim.

    A 12k prompt clears Anthropic's 512-token minimum and not the hypothetical
    vendor's 32k one — so on that vendor the 85% hit rate is unreachable and
    every token bills at list.
    """
    assert caches_at_all(ANTHROPIC, "claude-opus-5", ENGINEERED)
    assert not caches_at_all(HYPOTHETICAL_CHEAPER, "claude-opus-5", ENGINEERED)


def test_the_cheaper_sticker_price_costs_more_on_this_workload() -> None:
    """The Notes, verbatim: *a vendor that is 30% cheaper per token but has a
    larger minimum cacheable prefix ... can cost more here.*

    Demonstrated against a hypothetical vendor, because no real second vendor's
    figures were verified and inventing them in the module would be the failure
    this story exists to end.
    """
    assert cheaper_sticker_can_lose(
        dearer=ANTHROPIC, cheaper=HYPOTHETICAL_CHEAPER, model="claude-opus-5", shape=ENGINEERED
    )


def test_the_cheaper_vendor_wins_once_the_prompt_clears_its_minimum() -> None:
    """The control, and it matters: without it the test above would pass for a
    module that simply always preferred Anthropic."""
    long_prompt = WorkloadShape(
        calls=120, prompt_tokens=40_000, output_tokens=1_000, cached_share=Decimal("0.85")
    )

    assert not cheaper_sticker_can_lose(
        dearer=ANTHROPIC,
        cheaper=HYPOTHETICAL_CHEAPER,
        model="claude-opus-5",
        shape=long_prompt,
    )


def test_asking_the_question_backwards_is_refused() -> None:
    """The function asks whether a *lower* list price loses, so the argument
    order carries meaning and a swapped call would answer a different question
    while looking like this one."""
    with pytest.raises(VendorError, match="not the cheaper sticker"):
        cheaper_sticker_can_lose(
            dearer=HYPOTHETICAL_CHEAPER,
            cheaper=ANTHROPIC,
            model="claude-opus-5",
            shape=ENGINEERED,
        )


# ------------- what is NOT measured says so, rather than defaulting


def test_only_one_vendor_is_recorded() -> None:
    """AC 1 is blocked, and the module must not paper over it.

    No second vendor has been run and none of its cache figures were verified,
    so there is no second profile. This test exists so that adding one is a
    deliberate act with a failing test behind it, not a quiet import.
    """
    assert list(VENDORS) == ["Anthropic"]


def test_a_comparison_of_one_vendor_names_no_winner() -> None:
    """A field of one is how ADR-002 came to be defended rather than tested."""
    comparison = Comparison(shape=ENGINEERED, model="claude-opus-5", profiles=[ANTHROPIC])

    assert comparison.cheapest() is None
    assert "cost model rather than a comparison" in comparison.render()
    assert "nothing here supersedes ADR-002" in comparison.render()


def test_a_comparison_of_two_names_the_cheaper_one() -> None:
    """The control, so the refusal above is about the count and not about the
    function being broken."""
    comparison = Comparison(
        shape=ENGINEERED, model="claude-opus-5", profiles=[ANTHROPIC, HYPOTHETICAL_CHEAPER]
    )

    assert comparison.cheapest() == "Anthropic"


def test_an_unmeasured_vendor_reports_that_nothing_was_measured() -> None:
    """AC 2, AC 3's hit-rate clause and AC 4, all of which are unmeasured.

    Defaulting any of them to zero would read as *this vendor is free and
    reaches its conclusions instantly*.
    """
    rendered = Comparison(shape=ENGINEERED, model="claude-opus-5", profiles=[ANTHROPIC]).render()

    assert "not measured" in rendered


def test_a_measured_run_reports_what_it_has_and_names_what_it_lacks() -> None:
    """The frame a real run fills. Cost per confirmed finding needs E9; the
    experiments-to-conclusion figure has a harder blocker (below)."""
    comparison = Comparison(
        shape=ENGINEERED,
        model="claude-opus-5",
        profiles=[ANTHROPIC],
        measured={"Anthropic": MeasuredRun("Anthropic", measured_cache_hit_rate=Decimal("0.85"))},
    )

    rendered = comparison.render()

    assert "85% measured hit rate" in rendered
    assert "needs E9's finding audit" in rendered


def test_experiments_to_conclusion_names_the_spike_that_blocked_it() -> None:
    """Not merely unmeasured — S-0.8 found it unmeasurable with the current
    scenario set.

    In sixty runs the model chose *no finding, stop* zero times: it reasons
    correctly, withholds the verdict, and proposes one more experiment. Until
    something bounds that, the figure has no value to report.
    """
    comparison = Comparison(
        shape=ENGINEERED,
        model="claude-opus-5",
        profiles=[ANTHROPIC],
        measured={"Anthropic": MeasuredRun("Anthropic")},
    )

    assert "the model never concludes" in comparison.render()


def test_a_vendor_nobody_recorded_is_refused_rather_than_invented() -> None:
    """The refusal that keeps the module honest: a profile from recollection is
    precisely what ADR-002 was criticised for."""
    with pytest.raises(VendorError, match="a profile from memory"):
        recorded_profile("SomeVendor")


def test_the_report_states_when_a_vendor_cannot_cache_this_shape() -> None:
    """Otherwise a reader sees an effective cost equal to list and assumes the
    hit rate simply was not applied yet."""
    rendered = Comparison(
        shape=ENGINEERED, model="claude-opus-5", profiles=[HYPOTHETICAL_CHEAPER]
    ).render()

    assert "does not cache" in rendered
    assert "32000-token minimum" in rendered

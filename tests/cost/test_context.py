"""S-5.7: the prefix that stays identical, and two ways caching silently stops.

`04-cost.md` §12.2 makes this the largest cost variable in the system — 23x from
one variable — and every way of losing it is silent. The request succeeds,
`cache_read_input_tokens` comes back zero, and the bill goes up. So the tests are
about the failures that read as success:

- a stable segment that moved between calls (AC 3);
- a log that was reordered or re-summarized rather than appended to (AC 2);
- a prefix below the model's minimum, which is **largest on the cheap tier**;
- a log rendered per experiment, which exceeds the 20-block lookback at
  experiment 21 — halfway to S-5.4's cap of 40.

The last section is AC 4, including the first call, which can never hit and would
otherwise drag the reported figure down for a cache that is working perfectly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from coldfix.cost.accounting import TokenUsage
from coldfix.cost.context import (
    LOOKBACK_BLOCKS,
    MAX_BREAKPOINTS,
    MINIMUM_CACHEABLE_PREFIX,
    Block,
    Cacheability,
    ContextError,
    Investigation,
    Segment,
    check_blocks,
    is_append_only,
    minimum_prefix,
)
from coldfix.cost.routing import DEFAULT_TIER_MODELS


def investigation(**overrides: object) -> Investigation:
    fields: dict[str, object] = {
        "system": "You find performance problems by running experiments.",
        "playbook": "Django: count queries with force_debug_cursor.",
        "source": "def list_books(): ...",
        "model": "claude-opus-5",
    }
    return Investigation(**{**fields, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------ AC 1: the structure and its order


def test_the_prompt_has_the_five_segments_in_render_order() -> None:
    """AC 1. Caching is a prefix match, so the order is the whole technique —
    stable first, varying last."""
    blocks = investigation().render("Why is this slow?")

    assert [block.segment for block in blocks] == [
        Segment.SYSTEM,
        Segment.PLAYBOOK,
        Segment.SOURCE,
        Segment.LOG,
        Segment.QUESTION,
    ]


def test_the_question_is_never_cached() -> None:
    """Caching it would write an entry no later call can read, and spend one of
    only four breakpoints doing it."""
    blocks = investigation().render("Why is this slow?")

    assert blocks[-1].segment is Segment.QUESTION
    assert blocks[-1].breakpoint is False
    assert Segment.QUESTION.cacheable is False


def test_the_four_cacheable_segments_fit_the_four_breakpoints_exactly() -> None:
    """A request allows four `cache_control` breakpoints, and AC 1's structure
    has exactly four cacheable boundaries. Not a coincidence."""
    blocks = investigation().render("Why?")
    breakpoints = [block for block in blocks if block.breakpoint]

    assert len(breakpoints) == MAX_BREAKPOINTS
    assert all(block.segment.cacheable for block in breakpoints)


def test_an_empty_stable_segment_is_refused() -> None:
    """A breakpoint spent on nothing, and there are only four."""
    with pytest.raises(ContextError, match="empty"):
        investigation(playbook="   ")


def test_a_prompt_with_no_question_is_refused() -> None:
    with pytest.raises(ContextError, match="asks the model nothing"):
        investigation().render("")


# --------------------------- AC 3: the prefix is byte-identical between calls


def test_the_stable_prefix_is_byte_identical_between_consecutive_calls() -> None:
    """AC 3, stated exactly. Any byte change invalidates everything after it."""
    run = investigation()

    first_prefix = run.stable_prefix()
    first = run.render("What grows?")
    run.append("experiment 1: queries flat across 16x")
    second = run.render("What else?")

    assert run.stable_prefix() == first_prefix
    for segment in (Segment.SYSTEM, Segment.PLAYBOOK, Segment.SOURCE):
        before = next(block.text for block in first if block.segment is segment)
        after = next(block.text for block in second if block.segment is segment)
        assert before == after


def test_the_stable_prefix_holds_every_stable_segment() -> None:
    """It is what AC 3 is about, so it has to contain what AC 1 lists.

    Found by sabotage: dropping the source from it changed no test, because the
    only assertion on it compared it to itself. A prefix missing a segment would
    report *byte-identical* while the segment it omitted moved freely.
    """
    run = investigation()

    prefix = run.stable_prefix()

    assert run.system in prefix
    assert run.playbook in prefix
    assert run.source in prefix


def test_a_stable_segment_cannot_be_changed_after_construction() -> None:
    """AC 3 made structural rather than intended.

    The classic silent invalidator is a `datetime.now()` in the system prompt.
    Captured at construction it is evaluated once; re-rendered on every call it
    would move the prefix every time and nothing would ever cache.
    """
    run = investigation()

    assert not hasattr(run, "set_system")
    assert not hasattr(run, "update_playbook")
    assert not hasattr(run, "replace_source")


def test_the_log_grows_without_moving_what_came_before_it() -> None:
    """What makes an append-only log cache-safe: it need not be identical, only
    a continuation, so everything before the last write still matches."""
    run = investigation()
    run.append("experiment 1")
    first = run.log_text()
    run.append("experiment 2")

    assert run.log_text().startswith(first)


# --------------------------------- AC 2: never reordered, never re-summarized


def test_there_is_no_way_to_reorder_or_re_summarize_the_log() -> None:
    """`CLAUDE.md`'s non-negotiable, expressed as an absence rather than a
    warning. All three of reordering, editing and summarizing look identical
    from the bill: full input price on every later call instead of 0.1x."""
    run = investigation()

    assert not hasattr(run, "reorder")
    assert not hasattr(run, "summarize")
    assert not hasattr(run, "replace")
    assert not hasattr(run, "compact")


def test_append_is_the_only_way_the_prompt_grows() -> None:
    run = investigation()
    run.append("experiment 1")
    run.append("experiment 2")

    assert list(run.entries) == ["experiment 1", "experiment 2"]


def test_an_empty_log_entry_is_refused() -> None:
    """It would move the log's bytes without recording anything — a cache
    invalidation that buys nothing at all."""
    with pytest.raises(ContextError, match="records nothing"):
        investigation().append("  ")


@pytest.mark.parametrize(
    ("later", "expected"),
    [
        (["a", "b", "c"], True),
        (["a", "b"], True),
        (["b", "a"], False),
        (["a", "B"], False),
        (["a"], False),
        (["summary of a and b"], False),
    ],
)
def test_append_only_is_checkable(later: list[str], expected: bool) -> None:
    """Reordering, editing, truncating and summarizing all fail the same check,
    because from the cache's point of view they are the same event."""
    assert is_append_only(["a", "b"], later) is expected


# ---------------------- the minimum cacheable prefix, and why it bites the cheap tier


def test_the_minimum_is_not_monotonic_across_models() -> None:
    """The fact that makes this dangerous rather than merely fiddly.

    The newest, dearest model has the smallest minimum and the cheap tier has
    the largest — eight times larger. Nothing about "cheaper model" suggests
    "harder to cache".
    """
    assert minimum_prefix("claude-opus-5") == 512
    assert minimum_prefix("claude-sonnet-5") == 1024
    assert minimum_prefix("claude-haiku-4-5") == 4096
    assert minimum_prefix("claude-haiku-4-5") > minimum_prefix("claude-opus-5")


def test_a_short_prompt_caches_on_the_frontier_model_and_not_the_cheap_one() -> None:
    """The cross-story defect this story exists to surface.

    S-5.5 routes grounding's mechanical work to `claude-haiku-4-5` *because it is
    cheap*, and §12.3's engineered grounding is ten calls with a mature playbook
    — a short prompt. At 1,000 tokens that prompt caches on the frontier model
    and not on the cheap one, so routing the step down a tier can raise its
    effective cost. Nothing in the response says so.
    """
    short = 1_000

    assert investigation(model="claude-opus-5").viability(short).verdict is Cacheability.CACHEABLE
    below = investigation(model="claude-haiku-4-5").viability(short)

    assert below.verdict is Cacheability.BELOW_MINIMUM
    assert "raise its effective cost" in below.describe()


def test_a_prefix_over_the_minimum_is_cacheable() -> None:
    """The control."""
    viable = investigation(model="claude-haiku-4-5").viability(8_000)

    assert viable.verdict is Cacheability.CACHEABLE


def test_without_a_measured_count_the_answer_is_unknown_rather_than_a_guess() -> None:
    """`CLAUDE.md` forbids reporting a measurement nobody took, and here the
    measurement is *whether a cost control is working*.

    S-4.5's rule: *could not tell* stays distinct from *nothing wrong*.
    """
    unknown = investigation().viability()

    assert unknown.verdict is Cacheability.UNKNOWN
    assert "count_tokens" in unknown.describe()
    assert "tiktoken" in unknown.describe()


def test_a_model_with_no_recorded_minimum_is_refused() -> None:
    """The failure being guarded is silent, so a default would hide it."""
    with pytest.raises(ContextError, match="no minimum cacheable prefix"):
        investigation(model="claude-opus-9")


def test_every_routed_model_has_a_recorded_minimum() -> None:
    """S-5.5's tiers must all be answerable here, or routing a step could put it
    on a model whose caching behaviour nobody knows."""
    for model in DEFAULT_TIER_MODELS.values():
        assert model in MINIMUM_CACHEABLE_PREFIX


# --------------------------------- the 20-block lookback, and the log's shape


def test_the_log_is_one_block_however_many_experiments_it_holds() -> None:
    """The difference between caching and not.

    A breakpoint looks back at most 20 content blocks. Rendered one block per
    experiment, the log exceeds that at experiment 21 — and S-5.4 caps
    investigation at 40, so the obvious implementation stops caching exactly
    halfway to its own budget, with no error.
    """
    run = investigation()
    for index in range(40):
        run.append(f"experiment {index}")

    blocks = run.render("What now?")

    assert len(blocks) == 5
    assert sum(1 for block in blocks if block.segment is Segment.LOG) == 1


def test_the_block_count_stays_inside_the_lookback_window() -> None:
    """Asserted against the constant rather than the literal five, so a future
    segment cannot quietly push the prompt past the window."""
    run = investigation()
    for index in range(40):
        run.append(f"experiment {index}")

    assert len(run.render("What now?")) <= LOOKBACK_BLOCKS


def test_every_experiment_survives_being_rendered_into_one_block() -> None:
    """One block must not mean one summary — the whole log is still there."""
    run = investigation()
    for index in range(40):
        run.append(f"experiment {index}")

    rendered = next(block.text for block in run.render("What now?") if block.segment is Segment.LOG)

    assert rendered.count("experiment ") == 40
    assert "experiment 39" in rendered


def test_more_breakpoints_than_a_request_allows_are_refused() -> None:
    """Reachable now that the check is its own function.

    Inside `render` it could never fire — `render` builds a fixed five-block
    tuple — and a guard no test can reach is a guard nobody has checked. The
    extra breakpoints are not an error the API reports; they are simply not
    applied, so the segments a caller believed were cached are not.
    """
    blocks = [Block(Segment.SYSTEM, "x", breakpoint=True) for _ in range(MAX_BREAKPOINTS + 1)]

    with pytest.raises(ContextError, match="a request allows"):
        check_blocks(blocks)


def test_more_blocks_than_the_lookback_window_are_refused() -> None:
    """The guard that catches a log rendered one block per experiment.

    Twenty-one blocks is where a breakpoint stops finding the previous entry —
    and a per-experiment log reaches that at experiment 21, halfway to S-5.4's
    cap of 40, with no error and a bill that quietly triples.
    """
    blocks = [Block(Segment.LOG, f"experiment {index}", breakpoint=False) for index in range(21)]

    with pytest.raises(ContextError, match="looks back over at most"):
        check_blocks(blocks)


def test_a_block_sequence_inside_both_limits_is_accepted() -> None:
    """The control. A checker that refused everything would pass both tests
    above while making every prompt unsendable."""
    check_blocks(investigation().render("Why?"))


# ------------------------------------------- AC 4: the hit rate, measured and reported


def test_the_hit_rate_is_measured_over_prompt_tokens() -> None:
    """AC 4. Over the whole prompt, not over `input_tokens` — which is only the
    uncached remainder (ADR 056)."""
    run = investigation()
    run.record(TokenUsage(input_tokens=1_000, output_tokens=50, cache_creation_input_tokens=9_000))
    run.record(TokenUsage(input_tokens=500, output_tokens=50, cache_read_input_tokens=9_500))

    assert run.hit_rate() == Decimal("0.475")


def test_the_first_call_is_separated_because_it_can_never_hit() -> None:
    """A cache that is working perfectly still reports a poor blended figure on a
    short investigation, because the first call has nothing to hit and pays the
    1.25x write premium on top. Reporting only the blend makes a working cache
    look worse the shorter the run, which is backwards.
    """
    run = investigation()
    run.record(TokenUsage(input_tokens=10_000, output_tokens=50))
    run.record(TokenUsage(input_tokens=0, output_tokens=50, cache_read_input_tokens=10_000))

    assert run.hit_rate() == Decimal("0.5")
    assert run.warm_hit_rate() == Decimal(1)


def test_a_single_call_has_no_warm_rate_to_report() -> None:
    """`None` rather than zero: one call is not a cache that failed, it is a
    cache that has not been asked yet."""
    run = investigation()
    run.record(TokenUsage(input_tokens=10_000, output_tokens=50))

    assert run.warm_hit_rate() is None
    assert "can never hit" in run.report()


def test_the_report_says_how_much_was_written_as_well_as_read() -> None:
    """A write bills above the uncached rate (ADR 056), so a report showing only
    reads makes caching look free when it is not."""
    run = investigation()
    run.record(TokenUsage(input_tokens=0, output_tokens=50, cache_creation_input_tokens=9_000))
    run.record(TokenUsage(input_tokens=0, output_tokens=50, cache_read_input_tokens=9_000))

    rendered = run.report()

    assert "9000 tokens written to cache" in rendered
    assert "billed above the uncached rate" in rendered


def test_nothing_recorded_reports_nothing_rather_than_zero_percent() -> None:
    assert investigation().report() == "Cache: no calls recorded."


def test_the_hit_rate_is_none_before_anything_is_recorded() -> None:
    assert investigation().hit_rate() is None

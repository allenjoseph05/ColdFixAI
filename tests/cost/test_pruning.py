"""S-5.8: what the prompt shows, what it keeps, and whether the trade paid.

`04-cost.md` §5 calls zero information loss *the difference between this and
naive truncation*, so AC 4 is the property everything else has to preserve:

- nothing can drop a record, and that is an absence rather than a promise;
- the retrieved detail never re-enters the cached prefix S-5.7 just built;
- the summary is composed by the harness, because an agent asked to summarize
  its own experiment can bury it (F6, again);
- and the 60-80% §5 claims is measured rather than repeated, including what
  retrieval adds back.
"""

from __future__ import annotations

import pytest

from coldfix.cost.context import Investigation, is_append_only
from coldfix.cost.pruning import (
    CLAIMED_REDUCTION,
    MAX_SUMMARY_CHARS,
    RETRIEVAL_NOTICE,
    PrunedLog,
    PruningError,
)

DETAIL = "stdout:\n" + ("x" * 4_000) + "\nstack:\n  frame\ncounters: db.query=41"


def logged(count: int = 3, detail: str = DETAIL) -> PrunedLog:
    log = PrunedLog()
    for index in range(count):
        log.append(
            primitive="ablation",
            target=f"get_discount_price_{index}",
            outcome="8.24s becomes 1.11s. 87% of cost localized.",
            detail=detail,
        )
    return log


# ------------------------------------------------ AC 1: summaries in context


def test_an_entry_renders_in_the_shape_the_cost_document_specifies() -> None:
    """§5's example, exactly."""
    log = PrunedLog()
    record = log.append(
        primitive="ablation",
        target="get_discount_price",
        outcome="8.24s becomes 1.11s. 87% of cost localized.",
        detail=DETAIL,
    )

    assert record.summary() == (
        "experiment 1 — ablation of get_discount_price\n"
        "  → 8.24s becomes 1.11s. 87% of cost localized."
    )


def test_the_detail_never_appears_in_the_rendered_log() -> None:
    """The whole technique in one assertion: forty full stdout dumps are held
    and none of them is preloaded."""
    rendered = logged().render()

    assert "x" * 4_000 not in rendered
    assert "stack:" not in rendered
    assert "experiment 3 — ablation of get_discount_price_2" in rendered


def test_the_summary_is_composed_rather_than_authored() -> None:
    """F6 again. An agent asked to summarize its own experiment can write
    *experiment 7 — nothing of interest*, and the detail is then never retrieved
    by anyone. The header comes from the primitive and the target, which are
    facts about what ran."""
    record = logged(1).records[0]

    assert record.summary().startswith("experiment 1 — ablation of get_discount_price_0")


@pytest.mark.parametrize("field", ["primitive", "target", "outcome"])
def test_a_multi_line_summary_part_is_refused(field: str) -> None:
    """A summary that grows with its subject is the thing pruning exists to
    prevent."""
    parts = {
        "primitive": "ablation",
        "target": "get_discount_price",
        "outcome": "faster",
        "detail": DETAIL,
    }
    parts[field] = "first line\nsecond line"

    with pytest.raises(PruningError, match="multiple lines"):
        PrunedLog().append(**parts)


def test_an_over_long_summary_part_is_refused() -> None:
    """Forty experiments is the log's whole share of §12.3's 12k prompt, and an
    unbounded part makes that unpredictable rather than merely large."""
    with pytest.raises(PruningError, match=str(MAX_SUMMARY_CHARS)):
        PrunedLog().append(
            primitive="ablation",
            target="x" * (MAX_SUMMARY_CHARS + 1),
            outcome="faster",
            detail=DETAIL,
        )


@pytest.mark.parametrize("field", ["primitive", "target", "outcome"])
def test_an_empty_summary_part_is_refused(field: str) -> None:
    parts = {
        "primitive": "ablation",
        "target": "get_discount_price",
        "outcome": "faster",
        "detail": DETAIL,
    }
    parts[field] = "  "

    with pytest.raises(PruningError, match="cannot be summarized"):
        PrunedLog().append(**parts)


# ------------------------------------------------- AC 2: read_experiment(n)


def test_read_experiment_returns_the_full_detail() -> None:
    """AC 2: full output, stacks, and raw counters."""
    log = logged()

    detail = log.read_experiment(2)

    assert detail == DETAIL
    assert "stack:" in detail
    assert "counters: db.query=41" in detail


def test_indexes_are_assigned_by_the_log_and_start_at_one() -> None:
    """`read_experiment(7)` has to mean the seventh experiment.

    A caller-supplied index can collide, skip or restart, and each makes a
    retrieval return somebody else's measurement with no error at all.
    """
    log = logged(3)

    assert [record.index for record in log.records] == [1, 2, 3]
    assert log.read_experiment(1) == log.records[0].detail


def test_an_index_that_does_not_exist_is_refused_by_name() -> None:
    """The caller asking is a model, and a model that guesses an index must be
    told rather than handed the nearest record."""
    log = logged(3)

    with pytest.raises(PruningError, match="no experiment 9"):
        log.read_experiment(9)


def test_retrieving_from_an_empty_log_says_so() -> None:
    with pytest.raises(PruningError, match="none yet"):
        PrunedLog().read_experiment(1)


def test_retrieval_changes_nothing_in_the_rendered_log() -> None:
    """The constraint S-5.7 imposes on this story.

    Writing the detail back into the log would insert content into the middle of
    a cached prefix and invalidate every breakpoint after it — a larger loss than
    the tokens it saved, on the same call that was trying to save them.
    """
    log = logged()
    before = log.render()

    log.read_experiment(2)

    assert log.render() == before


def test_the_rendered_log_stays_append_only_as_experiments_accumulate() -> None:
    """It feeds S-5.7's log segment, so it has to obey S-5.7's rule."""
    log = logged(2)
    early = log.render()
    early_records = [record.summary() for record in log.records]

    log.append(primitive="scaling", target="list_books", outcome="linear", detail=DETAIL)

    assert log.render().startswith(early)
    assert is_append_only(early_records, [record.summary() for record in log.records])


def test_the_rendered_log_fits_the_context_assembly() -> None:
    """The two halves of the epic's context work, composed."""
    log = logged(40)
    run = Investigation(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries with force_debug_cursor.",
        source="def list_books(): ...",
        model="claude-opus-5",
    )
    run.append(log.render())

    blocks = run.render("What next?")

    assert len(blocks) == 5
    assert "read_experiment" in blocks[3].text
    assert "x" * 4_000 not in blocks[3].text


# --------------------------------- AC 3: the prompt says detail is retrievable


def test_the_rendered_log_states_that_detail_is_retrievable() -> None:
    """AC 3, and it is not decoration: an agent that does not know it can ask
    will not ask, and the deferred detail is then lost in practice even though
    it was never discarded."""
    rendered = logged().render()

    assert "read_experiment(n)" in rendered
    assert "retrievable" in rendered
    assert "only deferred" in rendered


def test_the_notice_is_present_before_any_experiment_has_run() -> None:
    """It is part of the stable head of the log block, so it must not depend on
    there being entries — and being constant is what keeps the block a
    byte-identical prefix as entries append."""
    assert PrunedLog().render() == RETRIEVAL_NOTICE


def test_the_notice_does_not_move_when_experiments_are_added() -> None:
    log = PrunedLog()
    log.append(primitive="ablation", target="x", outcome="y", detail=DETAIL)

    assert log.render().startswith(RETRIEVAL_NOTICE)


# ------------------------------------ AC 4: nothing discarded, only deferred


def test_there_is_no_way_to_discard_a_record() -> None:
    """AC 4 as an absence rather than a promise — the same construction S-5.7
    used for the append-only rule.

    §5 calls zero information loss *the difference between this and naive
    truncation*, so a method that could truncate would be the difference
    removed.
    """
    log = logged()

    assert not hasattr(log, "truncate")
    assert not hasattr(log, "summarize")
    assert not hasattr(log, "forget")
    assert not hasattr(log, "evict")
    assert not hasattr(log, "compact")


def test_every_experiment_is_still_retrievable_after_forty_more() -> None:
    """Deferred means deferred. The first experiment is as readable at the cap
    as it was when it ran."""
    log = logged(40)

    assert log.read_experiment(1) == DETAIL
    assert log.read_experiment(40) == DETAIL
    assert len(log.records) == 40


def test_the_detail_is_held_not_referenced() -> None:
    """Held here rather than only pointed at, so *nothing discarded* does not
    depend on another store still having it."""
    log = logged(1, detail="unique-detail-marker")

    assert log.records[0].detail == "unique-detail-marker"


# ------------------------- the 60-80% claim, measured rather than repeated


def test_the_reduction_is_measured_against_an_unpruned_log() -> None:
    """§5 claims context drops 60-80%. This is that number, computed."""
    log = logged(10)

    reduction = log.reduction()

    assert reduction is not None
    assert reduction > CLAIMED_REDUCTION
    assert log.meets_claim()


def test_an_empty_log_has_no_reduction_to_report() -> None:
    """Nothing measured and nothing saved are different states.

    Found by this test failing: the emptiness check was on total characters, but
    the retrieval notice is always rendered — so an empty log reported a
    confident 0% saving rather than *no experiments yet*. The question is whether
    anything ran, not whether anything was printed.
    """
    assert PrunedLog().reduction() is None
    assert PrunedLog().net_reduction() is None
    assert PrunedLog().rendered_chars > 0


def test_retrieval_is_counted_against_the_saving() -> None:
    """Pruning removes tokens from every later call; retrieval adds them to the
    calls that ask. The net figure is the honest one."""
    log = logged(10)
    gross = log.reduction()

    log.read_experiment(1)
    log.read_experiment(2)

    net = log.net_reduction()

    assert gross is not None
    assert net is not None
    assert net < gross


def test_retrieving_the_same_experiment_twice_counts_twice() -> None:
    """The agent paid for it twice. Counting distinct experiments would flatter
    the number in exactly the case worth catching — a loop re-reading one
    experiment."""
    log = logged(10)
    log.read_experiment(1)
    once = log.retrieved_chars
    log.read_experiment(1)

    assert log.retrieved_chars == once * 2
    assert list(log.retrievals) == [1, 1]


def test_retrieving_everything_once_cancels_the_saving_exactly() -> None:
    """The failure mode the net figure exists to expose, and it is sharper than
    *worse*: pulling every experiment back exactly once nets **zero** tokens
    saved, having also paid for ten round trips the metric does not count."""
    log = logged(10)
    for index in range(1, 11):
        log.read_experiment(index)

    net = log.net_reduction()

    assert net == 0
    assert not log.meets_claim()


def test_re_reading_takes_the_net_saving_negative() -> None:
    """Not clamped, because a clamp would hide precisely this: an agent looping
    over experiments it has already pulled back is paying twice for tokens
    pruning had already removed."""
    log = logged(10)
    for _ in range(2):
        for index in range(1, 11):
            log.read_experiment(index)

    net = log.net_reduction()

    assert net is not None
    assert net < 0


def test_a_log_whose_detail_is_no_bigger_than_its_summaries_says_so() -> None:
    """The technique is not always worth it, and a report that assumed it was
    would be repeating §5 rather than measuring it."""
    log = logged(3, detail="tiny")

    assert not log.meets_claim()
    assert "below the 60%" in log.report()


def test_the_report_names_what_pruning_bought() -> None:
    log = logged(10)

    rendered = log.report()

    assert "10 experiments" in rendered
    assert "deferred" in rendered
    assert "nothing retrieved" in rendered


def test_the_report_names_what_retrieval_cost() -> None:
    log = logged(10)
    log.read_experiment(3)

    rendered = log.report()

    assert "1 retrievals pulled back" in rendered
    assert "net" in rendered


def test_a_log_with_nothing_in_it_reports_nothing_rather_than_zero() -> None:
    assert PrunedLog().report() == "Pruning: no experiments logged."


def test_the_deferred_and_rendered_sizes_are_both_visible() -> None:
    """Both halves, so a reader can check the ratio rather than trust it."""
    log = logged(5)

    assert log.deferred_chars == 5 * len(DETAIL)
    assert log.rendered_chars == len(log.render())
    assert log.rendered_chars < log.deferred_chars

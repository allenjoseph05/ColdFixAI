"""The trade nobody predicted, which is the only kind worth a second check.

S-3.8. `08-audit.md` F10 in one sentence: a guard *pair* catches the trade
somebody thought of, and the trades that matter are the ones nobody listed. So
the pair check stays and the envelope is added beside it, shaped the other way
round — a fixed set of global resources, every one of them checked on every
candidate, flagged on any rise past tolerance whether or not that trade was
predicted.

The acceptance criterion names the case directly: *a patch trading queries for a
memory explosion is flagged*. That patch is written here, it really does halve
the query count, it really does explode memory, and nothing in the checking path
knows in advance that memory is where to look.

Its control matters as much. A patch that halves the queries *without* buying
them with anything must not be flagged, or the envelope is a check that fails
every candidate and gets switched off within a week.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
from typing import Any

import pytest

from coldfix.bench.counting import count, register_hook, unregister_hook
from coldfix.primitives.counters import (
    ALLOCATION,
    ALLOCATION_BYTES,
    BLOCKED_NETWORK_CALLS,
    CATALOGUE,
    DB_QUERY,
    DB_ROWS,
    FILE_OPEN,
    HTTP_BYTES,
    HTTP_REQUEST,
    Counter,
    CounterError,
    Reading,
    _check_guards_resolve,
    guard_of,
    measuring,
)
from coldfix.primitives.envelope import (
    ALLOCATED_BLOCKS,
    BYTES_WRITTEN,
    CPU_SECONDS,
    DEFAULT_TOLERANCES,
    ENVELOPE,
    OPEN_FILE_DESCRIPTORS,
    PEAK_RSS_BYTES,
    PROCESS_COUNT,
    THREAD_COUNT,
    WALL_SECONDS,
    Availability,
    EnvelopeSample,
    compare,
    envelope,
)
from coldfix.primitives.off_cpu import BLOCKED_NETWORK

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="needs getrusage or /proc")


class Store:
    """One counted entry point, so both versions are measured the same way."""

    def __init__(self, pages: int, per_page: int) -> None:
        self.pages = pages
        self.per_page = per_page

    def fetch(self, first: int, last: int) -> list[dict[str, str]]:
        """One query for a range of pages. The hook attaches here."""
        return [
            {"id": f"{index}-{n}", "body": "x" * 200}
            for index in range(first, last)
            for n in range(self.per_page)
        ]


def paged(store: Store) -> int:
    """The baseline: one query per page, and each page released after use."""
    seen = 0
    for index in range(store.pages):
        seen += len(store.fetch(index, index + 1))
    return seen


def cached_in_memory(store: Store, held: list[Any]) -> int:
    """**The candidate.** One query instead of forty, bought with the whole set.

    `08-audit.md` F10's case exactly: it is not a query-and-rows trade, so no
    guard pair has it, and the improvement it reports is real and correctly
    measured. It is also the metastability shape `00-BRIEF.md` §4 warns about — a
    cache that removes slack while improving every metric anybody declared.
    """
    everything = store.fetch(0, store.pages)
    held.append(everything)
    return len(everything)


def sample_with(**metrics: float | None) -> EnvelopeSample:
    """An envelope sample stated directly, for the comparison's own tests."""
    filled: dict[str, float | None] = dict.fromkeys(ENVELOPE, 1.0)
    filled.update(metrics)
    return EnvelopeSample(metrics=filled)


# ------------------------------------------------- AC 1: every guard resolves


def test_every_counter_declares_a_guard_that_resolves() -> None:
    """AC 1, and it is checked at import as well as here.

    A guard naming a counter that does not exist is a guard that silently guards
    nothing — which is the failure mode of the whole story, one level down.
    """
    for name, counter in CATALOGUE.items():
        target, is_counter = guard_of(name)

        assert target
        assert (target in CATALOGUE) if is_counter else (target in ENVELOPE)
        assert counter.guard == target


def test_a_guard_may_point_at_an_envelope_metric() -> None:
    """Because some counters have no counter to be traded against. Opening fewer
    files is bought by writing more through the ones left open, and nothing here
    counts that — the envelope does."""
    target, is_counter = guard_of(FILE_OPEN)

    assert target == BYTES_WRITTEN
    assert not is_counter


def test_the_paired_counters_guard_each_other() -> None:
    """The canonical pair, and the two S-3.6 and S-3.7 added beside it."""
    assert guard_of(DB_QUERY) == (DB_ROWS, True)
    assert guard_of(DB_ROWS) == (DB_QUERY, True)
    assert guard_of(HTTP_REQUEST) == (HTTP_BYTES, True)
    assert guard_of(ALLOCATION) == (ALLOCATION_BYTES, True)
    assert guard_of(BLOCKED_NETWORK) == (BLOCKED_NETWORK_CALLS, True)


def test_a_guard_pointing_at_nothing_is_refused() -> None:
    """The import-time check, exercised directly. It is what makes AC 1 a
    property of the module rather than a promise in a docstring."""
    broken = Counter(
        name="db.invented",
        hook="db.invented",
        reads=Reading.EVENTS,
        event="something",
        amount="one per event",
        guard="db.nothing_by_this_name",
    )

    with pytest.raises(CounterError, match="guards nothing"):
        _check_guards_resolve({**CATALOGUE, broken.name: broken})


# ------------------------------- AC 2 and 4: the trade nobody predicted


def test_a_patch_trading_queries_for_memory_is_flagged() -> None:
    """AC 4, and the reason the envelope exists.

    The candidate halves the query count — a real improvement, correctly
    measured, and exactly what it was written to do — by holding every row in
    memory. No guard pair has *queries against peak memory* in it, because
    nobody wrote that pair down. The envelope has memory in it because memory is
    always in it.
    """
    store = Store(pages=40, per_page=200)
    held: list[Any] = []

    register_hook(DB_QUERY, measuring(Store, "fetch", len))
    try:
        with count(DB_QUERY) as before_queries, envelope() as baseline:
            paged(store)
        with count(DB_QUERY) as after_queries, envelope() as candidate:
            cached_in_memory(store, held)
    finally:
        unregister_hook(DB_QUERY)

    report = compare(baseline, candidate)

    assert after_queries.events < before_queries.events  # the patch works
    assert report.flagged
    assert {breach.metric for breach in report.breaches} & {ALLOCATED_BLOCKS, PEAK_RSS_BYTES}
    assert "made something else worse" in report.explanation()


def test_a_patch_that_buys_nothing_is_not_flagged() -> None:
    """The control. An envelope that flags every candidate is an envelope
    somebody switches off, and then the unpredicted trades go through."""
    store = Store(pages=40, per_page=200)

    with envelope() as baseline:
        paged(store)
    with envelope() as candidate:
        paged(store)

    report = compare(baseline, candidate)

    assert not report.flagged
    assert report.checked


def test_nothing_in_the_check_consults_a_prediction() -> None:
    """The structural difference from a guard pair: `compare` takes two samples
    and a tolerance table, and has no argument through which a caller could say
    which trade to expect."""
    parameters = set(inspect.signature(compare).parameters)

    assert parameters == {"baseline", "candidate", "tolerances"}


# ------------------------------------------------------ AC 3: the flagging


@pytest.mark.parametrize(
    "metric", [WALL_SECONDS, CPU_SECONDS, PEAK_RSS_BYTES, ALLOCATED_BLOCKS, BYTES_WRITTEN]
)
def test_any_envelope_metric_outside_tolerance_flags(metric: str) -> None:
    """AC 3. Every metric, not a chosen few — the one left out would be the one
    the next unpredicted trade uses."""
    # Chosen above every metric's absolute floor, so what is under test is that
    # the metric is checked at all rather than where its floor happens to sit.
    baseline = sample_with(**{metric: 1_000.0})
    candidate = sample_with(**{metric: 100_000.0})

    report = compare(baseline, candidate)

    assert [breach.metric for breach in report.breaches] == [metric]


def test_a_metric_that_falls_is_never_flagged() -> None:
    """A candidate exists to make something smaller. A two-sided check would
    flag every successful patch for the improvement it was written to make."""
    report = compare(sample_with(**{CPU_SECONDS: 100.0}), sample_with(**{CPU_SECONDS: 1.0}))

    assert not report.flagged


def test_a_rise_inside_tolerance_is_not_flagged() -> None:
    """Run-to-run variation is not a finding, and a check that treats it as one
    is a check that gets ignored."""
    inside = 1.0 + DEFAULT_TOLERANCES[ALLOCATED_BLOCKS] / 2

    report = compare(
        sample_with(**{ALLOCATED_BLOCKS: 100.0}), sample_with(**{ALLOCATED_BLOCKS: 100.0 * inside})
    )

    assert not report.flagged


def test_a_small_count_is_compared_absolutely_rather_than_by_ratio() -> None:
    """Two descriptors becoming three is a 50% rise and is nothing at all. A
    ratio on a small count flags noise, which costs the check its credibility
    before it ever meets a real trade."""
    noise = compare(
        sample_with(**{OPEN_FILE_DESCRIPTORS: 2.0}), sample_with(**{OPEN_FILE_DESCRIPTORS: 3.0})
    )
    leak = compare(
        sample_with(**{OPEN_FILE_DESCRIPTORS: 2.0}), sample_with(**{OPEN_FILE_DESCRIPTORS: 400.0})
    )

    assert not noise.flagged
    assert leak.flagged


def test_a_resource_that_appears_from_nothing_is_flagged() -> None:
    """Zero threads becoming eight has no ratio and is exactly the kind of thing
    worth knowing about."""
    report = compare(sample_with(**{THREAD_COUNT: 0.0}), sample_with(**{THREAD_COUNT: 8.0}))

    assert report.flagged
    assert report.breaches[0].ratio == float("inf")
    assert "from nothing" in str(report.breaches[0])


def test_the_breach_says_why_the_metric_is_watched() -> None:
    """Whoever reads this is deciding whether to ship a patch, and *peak memory
    rose 12x* is a number until it is also a sentence."""
    report = compare(sample_with(**{PEAK_RSS_BYTES: 10.0}), sample_with(**{PEAK_RSS_BYTES: 120.0}))

    assert "held in memory" in str(report.breaches[0])


# --------------------------------- unavailable is not "within tolerance"


def test_a_metric_that_could_not_be_read_is_named_rather_than_passed() -> None:
    """A guard check that quietly passed on the metrics it could not see would
    carry the same reassurance with none of the coverage."""
    baseline = sample_with(**{PEAK_RSS_BYTES: None})
    candidate = sample_with(**{PEAK_RSS_BYTES: None})

    report = compare(baseline, candidate)

    assert PEAK_RSS_BYTES not in report.checked
    assert report.unmeasured[PEAK_RSS_BYTES] is Availability.NEEDS_RUSAGE
    assert "covers less than a sandbox run would" in report.explanation()


def test_an_unmeasured_metric_is_not_counted_as_checked() -> None:
    report = compare(sample_with(**{BYTES_WRITTEN: None}), sample_with(**{BYTES_WRITTEN: None}))

    assert BYTES_WRITTEN not in report.checked
    assert len(report.checked) == len(ENVELOPE) - 1


def test_the_portable_metrics_are_measurable_everywhere() -> None:
    """Which is what stops a Windows run reporting an envelope of nothing. They
    are the portable half of the memory and process questions, not a substitute
    for the real ones."""
    with envelope() as sample:
        time.sleep(0.01)

    assert sample[WALL_SECONDS] is not None
    assert sample[CPU_SECONDS] is not None
    assert sample[ALLOCATED_BLOCKS] is not None
    assert sample[THREAD_COUNT] is not None


@POSIX_ONLY
def test_the_sandbox_metrics_are_measurable_on_posix() -> None:
    """The three that need `getrusage` or `/proc`, in the place they exist."""
    with envelope() as sample:
        time.sleep(0.01)

    assert sample[PEAK_RSS_BYTES] is not None
    assert sample[BYTES_WRITTEN] is not None
    assert sample[OPEN_FILE_DESCRIPTORS] is not None
    assert sample[PROCESS_COUNT] is not None


# ------------------------------------------------- the envelope, measured


def test_a_thread_that_outlives_the_block_is_visible() -> None:
    """Concurrency introduced to hide a cost rather than remove it. The thread
    count is portable, so this is checkable on every platform."""
    stop = threading.Event()
    threads = [threading.Thread(target=stop.wait) for _ in range(6)]

    with envelope() as baseline:
        time.sleep(0.01)
    with envelope() as candidate:
        for thread in threads:
            thread.start()
        time.sleep(0.01)

    try:
        report = compare(baseline, candidate)
        assert THREAD_COUNT in {breach.metric for breach in report.breaches}
    finally:
        stop.set()
        for thread in threads:
            thread.join()


def test_levels_are_levels_and_retention_is_a_difference() -> None:
    """Peak RSS is a level — the peak *is* the number, and differencing it would
    report the peak of a difference, which is not a thing. Retained blocks are a
    difference, because the interpreter-wide level is diluted by everything else
    the process is holding and a real explosion disappears into it."""
    held: list[Any] = []

    with envelope() as idle:
        time.sleep(0.01)
    with envelope() as retaining:
        held.append([{"n": index} for index in range(20_000)])

    idle_blocks, retained_blocks = idle[ALLOCATED_BLOCKS], retaining[ALLOCATED_BLOCKS]

    assert idle[WALL_SECONDS] is not None
    assert idle_blocks is not None
    assert retained_blocks is not None
    assert abs(idle_blocks) < 1_000
    assert retained_blocks > 20_000

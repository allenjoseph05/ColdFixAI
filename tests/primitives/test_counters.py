"""Six counters, one spelling each, and one of them expensive enough to declare.

S-3.6. ADR 013 shipped the counting mechanism deliberately without counters, on
the grounds that *what* to count is a question about a framework and *how* is
not. This is the other half, and most of it is a vocabulary — which sounds like
bookkeeping until you notice what the alternative costs.

An adapter that registers `db.queries` while a primitive asks for `db.query`
produces a system where the primitive raises, a long way from the typo, in a
codebase where the same raise means *this project has no database counter*. So
the catalogue is the spelling, and a name outside it is refused where it is
registered rather than where it is missed.

The counters that need no framework are shipped and tested against real
behaviour. The four that do are declarations plus the constructor an adapter
needs, and the tests here drive that constructor against a stand-in cursor —
which is the same shape S-14.2 will wire to a real one.

The overhead criterion is measured rather than asserted, and the measurement is
the point: five of the six are far inside five percent and `tracemalloc` is not,
which is why the catalogue has a field saying so.
"""

from __future__ import annotations

import builtins
import io
import time
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from coldfix.bench.counting import (
    calls_to,
    count,
    register_hook,
    registered_hooks,
    unregister_hook,
)
from coldfix.primitives.counters import (
    ALLOCATION,
    ALLOCATION_BYTES,
    CATALOGUE,
    DB_BYTES,
    DB_QUERY,
    DB_ROWS,
    FILE_OPEN,
    HTTP_REQUEST,
    OVERHEAD_BUDGET,
    REFERENCE_OPERATION_SECONDS,
    CounterError,
    CounterOverhead,
    CounterShape,
    Reading,
    UnknownCounterError,
    allocations,
    describe,
    framework_free_counters,
    measuring,
    register_counter,
)
from coldfix.primitives.measurement import TOTAL_SUFFIX, measure_once


class Cursor:
    """A stand-in for the thing an adapter would wrap. Rows are known by count."""

    def __init__(self, rows: dict[str, int]) -> None:
        self.rows = rows

    def execute(self, statement: str) -> list[dict[str, int]]:
        return [{"n": index} for index in range(self.rows.get(statement, 0))]


# ------------------------------------------------------------- the catalogue


def test_the_six_counters_the_story_names_are_all_declared() -> None:
    """AC 1. S-3.7's blocked-time counters share the catalogue, so this asserts
    a subset — the six have to be there, and the catalogue is allowed to grow."""
    assert {
        DB_QUERY,
        DB_ROWS,
        DB_BYTES,
        HTTP_REQUEST,
        FILE_OPEN,
        ALLOCATION,
    } <= set(CATALOGUE)


def test_every_counter_says_what_an_event_and_an_amount_are() -> None:
    """A counter whose meaning lives only in the adapter's head is one two
    adapters can implement differently while both looking correct."""
    for counter in CATALOGUE.values():
        assert counter.event.strip()
        assert counter.amount.strip()


def test_a_name_outside_the_catalogue_is_refused_at_registration() -> None:
    """One step earlier than ADR 013's rule, and for the same reason: the
    consequence of `db.queries` is a raise a long way from the typo."""
    with pytest.raises(UnknownCounterError) as raised:
        register_counter("db.queries", calls_to(Cursor, "execute"))

    assert DB_QUERY in raised.value.known


def test_a_counter_this_module_supplies_cannot_be_registered_by_an_adapter() -> None:
    """Two answers to one question is worse than none: `count(FILE_OPEN)` would
    return whichever was registered first, and nothing would say so."""
    with pytest.raises(CounterError):
        register_counter(FILE_OPEN, calls_to(Cursor, "execute"))


def test_a_reading_of_another_hook_cannot_be_registered_on_its_own() -> None:
    """`db.rows` is the total of the `db.query` hook. Registering it separately
    would wrap the cursor twice for two numbers that must come from one run."""
    with pytest.raises(CounterError) as raised:
        register_counter(DB_ROWS, calls_to(Cursor, "execute"))

    assert DB_QUERY in str(raised.value)


def test_queries_and_rows_are_one_attachment_read_two_ways() -> None:
    """The guard pair `01-primitives.md` §2 requires, and the reason `Count` has
    two numbers: queries falling while rows explode is only visible if both came
    from the same run."""
    queries = describe(DB_QUERY)
    rows = describe(DB_ROWS)

    assert queries.hook == rows.hook
    assert queries.reads is Reading.EVENTS
    assert rows.reads is Reading.TOTAL
    assert queries.guard == DB_ROWS
    assert rows.guard == DB_QUERY


def test_an_unknown_counter_cannot_be_described() -> None:
    with pytest.raises(UnknownCounterError):
        describe("db.statements")


# ------------------------------------------------- the adapter's constructor


def test_a_measuring_hook_records_both_the_calls_and_the_quantity() -> None:
    """AC 2's other half: the adapter declares where the cursor is, this
    supplies how a quantity gets from its return value into the tally."""
    cursor = Cursor({"tickets": 40, "users": 2})
    register_hook(DB_QUERY, measuring(Cursor, "execute", len))
    try:
        with count(DB_QUERY) as tally:
            cursor.execute("tickets")
            cursor.execute("users")
            cursor.execute("tickets")
    finally:
        unregister_hook(DB_QUERY)

    assert tally.events == 3
    assert tally.total == 82


def test_a_counting_hook_leaves_the_total_equal_to_the_events() -> None:
    """The amount defaults to one, so a hook that only counts is not a special
    case — it is the general case with the default taken."""
    with framework_free_counters(), count(FILE_OPEN) as tally:
        _open_temporary_files(3)

    assert tally.events == 3
    assert tally.total == 3.0


def test_a_measuring_hook_resolves_its_target_when_it_is_installed() -> None:
    """Not when it is constructed. An ablation stub may have replaced the
    attribute in between, and wrapping the value captured at construction would
    measure a callable nobody is calling."""
    hook = measuring(Cursor, "execute", len)
    original = Cursor.execute

    def replacement(self: Cursor, statement: str) -> list[dict[str, int]]:
        return [{"n": 0}]

    Cursor.execute = replacement  # type: ignore[method-assign]
    try:
        register_hook(DB_QUERY, hook)
        with count(DB_QUERY) as tally:
            Cursor({"tickets": 40}).execute("tickets")
    finally:
        unregister_hook(DB_QUERY)
        Cursor.execute = original  # type: ignore[method-assign]

    # The stub returns one row, so the wrap saw the stub rather than the
    # original's forty.
    assert tally.total == 1.0


def test_the_measuring_constructor_refuses_a_descriptor() -> None:
    """`calls_to`'s rule, inherited: replacing a classmethod with a plain
    function changes how it binds, giving a correct count of a different
    program."""

    class Owner:
        @classmethod
        def build(cls) -> int:
            return 1

    with pytest.raises(Exception, match="classmethod"):
        measuring(Owner, "build", float)(lambda amount=1.0: None).__enter__()


# --------------------------------------------------- the framework-free ones


def _open_temporary_files(how_many: int, tmp: Path | None = None) -> None:
    target = tmp / "f.txt" if tmp is not None else Path(__file__)
    for _ in range(how_many):
        with open(target, encoding="utf-8"):  # noqa: PTH123 - the counter is on this call
            pass


def test_file_opens_are_counted_without_any_adapter(tmp_path: Path) -> None:
    """AC 1 and AC 2: two of the six need no framework, so they are shipped
    rather than declared."""
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")

    with framework_free_counters(), count(FILE_OPEN) as tally:
        _open_temporary_files(4, tmp_path)

    assert tally.events == 4


def test_the_file_counter_cannot_see_the_other_ways_to_open_a_file(
    tmp_path: Path,
) -> None:
    """Stated rather than discovered from a number that seemed low. Replacing an
    attribute sees calls that go through that attribute and no others, and an
    undercount is a measurement that looks like a finding."""
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")

    with framework_free_counters(), count(FILE_OPEN) as tally:
        path.open(encoding="utf-8").close()
        io.open(path, encoding="utf-8").close()  # noqa: UP020

    assert tally.events == 0


def test_the_file_counter_is_removed_afterwards(tmp_path: Path) -> None:
    original = builtins_open()

    with framework_free_counters(), count(FILE_OPEN):
        assert builtins_open() is not original

    assert builtins_open() is original


def builtins_open() -> Any:
    return builtins.open


# ------------------------------------------------------------- allocations


def test_allocations_are_counted_over_a_block_with_their_sites() -> None:
    """AC 1's last counter, and AC 3 for it: the attribution comes from
    tracemalloc's own tracebacks rather than from S-1.3's stack capture."""
    with allocations() as measured:
        held = [object() for _ in range(5000)]

    assert len(held) == 5000
    assert measured.events > 0
    assert measured.total > 0
    assert measured.sites
    assert any("test_counters" in site for site, _, _ in measured.sites)


def test_allocations_are_declared_a_different_shape_from_the_hooks() -> None:
    """Nothing in Python fires per allocation that a probe can attach to, so the
    alternative to a block meter is inventing events — and an invented event is
    a fabricated measurement."""
    meters = {
        name for name, counter in CATALOGUE.items() if counter.shape is CounterShape.BLOCK_METER
    }

    # Both readings of the allocation meter and nothing else. S-3.8 added the
    # bytes reading beside the count, so this is a set rather than one name.
    assert meters == {ALLOCATION, ALLOCATION_BYTES}


def test_two_allocation_meters_cannot_own_the_same_measurement() -> None:
    """Counting allocations inside somebody else's trace reports their
    allocations alongside the subject's."""
    with allocations(), pytest.raises(CounterError), allocations():
        pass


def test_tracing_is_stopped_even_when_the_block_raises() -> None:
    """Instrumentation that outlives its block taxes everything measured after
    it — S-1.3's rule, and tracemalloc taxes a great deal."""
    with pytest.raises(ValueError, match="deliberate"), allocations():
        message = "deliberate"
        raise ValueError(message)

    assert not tracemalloc.is_tracing()


# -------------------------------------------- AC 3: optional stack capture


def test_stack_capture_is_available_per_counter_and_off_by_default(
    tmp_path: Path,
) -> None:
    """AC 3. Off by default because it is the expensive half — S-1.3 measured
    the difference at more than an order of magnitude — and on when localizing,
    which is S-3.9's job."""
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")

    with framework_free_counters():
        with count(FILE_OPEN) as quiet:
            _open_temporary_files(2, tmp_path)
        with count(FILE_OPEN, capture_stacks=True) as localized:
            _open_temporary_files(2, tmp_path)

    assert quiet.stacks == []
    assert len(localized.stacks) == 2
    assert "test_counters" in localized.stacks[0][0].filename


# ------------------------------------------------- AC 4: measured overhead


REPETITIONS = 50_000

# The budget and its denominator moved into `primitives/counters.py` at S-14.4,
# where the catalogue that claims `NEGLIGIBLE` can be checked against them and
# where the adapter conformance suite reads the same two numbers. The reasoning
# that put them here is now that module's docstring.


def _busy(cursor: Cursor) -> None:
    for _ in range(REPETITIONS):
        cursor.execute("tickets")


def _median_seconds(work: Any, rounds: int = 5) -> float:
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        work()
        samples.append(time.perf_counter() - started)
    return sorted(samples)[len(samples) // 2]


def _at_depth(frames: int, work: Any) -> Any:
    """Run `work` with `frames` extra frames beneath it on the call stack."""
    if frames <= 0:
        return work()
    return _at_depth(frames - 1, work)


@contextmanager
def _counting(*, stacks: bool = False) -> Iterator[None]:
    register_hook(DB_QUERY, measuring(Cursor, "execute", len))
    try:
        with count(DB_QUERY, capture_stacks=stacks):
            yield
    finally:
        unregister_hook(DB_QUERY)


def _per_event_overhead(*, stacks: bool = False, depth: int = 0) -> float:
    cursor = Cursor({"tickets": 3})
    run = lambda: _at_depth(depth, lambda: _busy(cursor))  # noqa: E731

    bare = _median_seconds(run)
    with _counting(stacks=stacks):
        instrumented = _median_seconds(run)
    return (instrumented - bare) / REPETITIONS


@pytest.mark.slow
def test_a_counting_hook_costs_under_five_percent_of_what_it_observes() -> None:
    """AC 4.

    S-1.3 met this at 0.07% — but only after a `Path.resolve()` on the
    stack-capture path was found costing 590µs per event, which is exactly the
    class of defect this tool exists to find in other people's code. The check
    is here because the last time it was run it failed by two orders of
    magnitude and looked fine.
    """

    per_event = _per_event_overhead()
    share = per_event / REFERENCE_OPERATION_SECONDS

    assert share < OVERHEAD_BUDGET, (
        f"counting cost {per_event * 1e6:.3f}us per event, "
        f"{share:.2%} of a {REFERENCE_OPERATION_SECONDS * 1e6:.0f}us operation"
    )
    # A tight absolute bound as well as the ratio: the defect ADR 013 records
    # cost 590µs per event and would pass any ratio stated against a slow
    # enough operation.
    assert per_event < 5e-6


@pytest.mark.slow
def test_counting_costs_the_same_however_deep_the_stack_is() -> None:
    """The property that makes the budget above a property of the counter.

    An increment and an attribute wrap do not care what is beneath them on the
    call stack, so one measurement of the counter's cost is a measurement of the
    counter's cost. The next test is about the thing that does not have this
    property.
    """
    shallow = _per_event_overhead(depth=0)
    deep = _per_event_overhead(depth=100)

    assert deep < 3 * max(shallow, 1e-7)


@pytest.mark.slow
def test_stack_capture_costs_more_the_deeper_the_stack_gets() -> None:
    """AC 3's option, and the finding this story turned up.

    Capturing a stack walks the whole stack, so its cost is linear in depth:
    measured at roughly 12µs per event with nothing beneath it and 296µs at 200
    frames — about 1.4µs a frame. A Django request is tens of frames deep before
    the view is entered, so at a realistic depth this costs *as much as the
    database call it is observing*, and no fixed percentage describes it.

    That is why it stays opt-in and why the budget above is stated for counting
    alone. S-3.9 localizes findings by walking these stacks and inherits the
    problem with them: capture on a sample of events, or bound the walk, but do
    not attach it to a screening sweep.
    """
    shallow = _per_event_overhead(stacks=True, depth=0)
    deep = _per_event_overhead(stacks=True, depth=100)

    assert deep > 3 * shallow
    assert deep > OVERHEAD_BUDGET * REFERENCE_OPERATION_SECONDS


@pytest.mark.slow
def test_the_same_counter_blows_the_budget_on_an_operation_that_does_nothing() -> None:
    """The reason the denominator is stated rather than assumed.

    The counter's cost is fixed per event, so the ratio is a property of the
    *pair*. Against the 0.4µs call this test suite uses as a stand-in cursor, the
    instrument that costs 0.1% of a real query costs most of the runtime — and a
    budget written without saying what it is a budget of would have failed a
    perfectly good counter here, or passed a terrible one elsewhere.
    """
    cursor = Cursor({"tickets": 3})
    bare = _median_seconds(lambda: _busy(cursor))

    register_hook(DB_QUERY, measuring(Cursor, "execute", len))
    try:
        with count(DB_QUERY):
            counted = _median_seconds(lambda: _busy(cursor))
    finally:
        unregister_hook(DB_QUERY)

    assert (counted - bare) / bare > OVERHEAD_BUDGET
    assert bare / REPETITIONS < 0.01 * REFERENCE_OPERATION_SECONDS


@pytest.mark.slow
def test_the_allocation_meter_is_declared_heavy_because_it_measures_heavy() -> None:
    """The one that does not meet the budget by any denominator, declared rather
    than hidden.

    tracemalloc stores a traceback for every live allocation, which is exactly
    what makes the attribution possible and exactly what makes it cost — measured
    here at more than three times the runtime of the thing it observes. A
    screening pass that attached every counter it could find would be measuring
    the instrument, so the catalogue says so and the caller decides.
    """
    cursor = Cursor({"tickets": 3})
    bare = _median_seconds(lambda: _busy(cursor), rounds=3)

    def traced() -> None:
        with allocations():
            _busy(cursor)

    overhead = (_median_seconds(traced, rounds=3) - bare) / bare

    assert describe(ALLOCATION).overhead is CounterOverhead.HEAVY
    assert overhead > OVERHEAD_BUDGET, (
        f"tracemalloc cost {overhead:.1%}, which is inside the budget — if that is real, "
        "the catalogue's HEAVY declaration is now wrong and should be changed"
    )


def test_every_other_counter_is_declared_negligible() -> None:
    """The declaration is what a screening pass reads to decide what it may
    attach without thinking."""
    heavy = {
        name for name, counter in CATALOGUE.items() if counter.overhead is CounterOverhead.HEAVY
    }

    assert heavy == {ALLOCATION, ALLOCATION_BYTES}


# ------------------------------------------- the counters through a primitive


def test_a_counter_reaches_a_primitive_as_both_of_its_numbers() -> None:
    """`measure_once` records events and total for every hook, so a counter
    whose meaning is its magnitude cannot be silently read as its own operation
    count — `db.rows` read as events is the query count, which is a plausible
    number and the wrong one."""
    cursor = Cursor({"tickets": 40, "users": 2})
    register_hook(DB_QUERY, measuring(Cursor, "execute", len))
    try:
        metrics = measure_once(
            lambda: [cursor.execute("tickets"), cursor.execute("users")],
            counters=[DB_QUERY],
        )
    finally:
        unregister_hook(DB_QUERY)

    assert metrics[DB_QUERY] == 2.0
    assert metrics[f"{DB_QUERY}{TOTAL_SUFFIX}"] == 42.0


@pytest.fixture(autouse=True)
def _no_hooks_left_behind() -> Iterator[None]:
    """A hook that outlives its test taxes every test after it, and the failure
    would appear somewhere else entirely."""
    before = registered_hooks()
    yield
    assert registered_hooks() == before

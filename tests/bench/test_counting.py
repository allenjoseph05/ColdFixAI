"""`count()` reports events without changing the program that raised them.

Two of these carry most of the weight.

`test_an_unknown_hook_name_raises_rather_than_counting_zero` is the safety
test. Zero is a real measurement here — "queries flat at 7,7,7 across 100x
scale" ships as a published exclusion — so an instrument that answers zero when
it is not actually attached would let a typo enter the evidence chain as
evidence of absence.

`test_counts_match_an_uninstrumented_run` is the acceptance criterion that
ADR 008 exists because of. Counting queries by flipping `settings.DEBUG` gives
correct integers about a program that no longer behaves the way the unobserved
one does, and it did that on one repository in three.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Callable, Iterator

import pytest

from coldfix.bench.counting import (
    HookError,
    UnknownHookError,
    calls_to,
    count,
    register_hook,
    registered_hooks,
    unregister_hook,
)
from coldfix.bench.timing import time

THIS_MODULE = sys.modules[__name__]

HOOK = "test.work"
OTHER_HOOK = "test.other"

# Two workload shapes, because the AC is a ratio between two quantities that
# are best measured apart.
#
# The overhead of counting is a fixed cost per event, and it is far below the
# run-to-run variance of any operation worth counting — measuring it directly
# against one gives a difference of a few tenths of a percent, which is noise
# with a sign. So it is measured against a nearly free operation called tens of
# thousands of times, where it dominates and is stable, and then compared
# against the separately measured cost of a realistic operation.
TRIVIAL_SIZE = 0
TRIVIAL_CALLS = 20_000

# Stack capture costs some tens of times the tally, so it resolves against far
# fewer events and does not need to spend the time to say so.
STACK_CALLS = 2_000

# A few hundred microseconds — the order of magnitude of the operations this
# instrument exists to count. A database query costs more.
WORK_SIZE = 20_000
CALLS_PER_BATCH = 200
ROUNDS = 11


def work(n: int = WORK_SIZE) -> int:
    """The counted operation. Its return value is used, so a broken wrapper shows."""
    return sum(range(n))


def other_work() -> str:
    return "other"


class Widget:
    def render(self) -> str:
        return "rendered"

    @classmethod
    def build(cls) -> Widget:
        return cls()


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """No test may leave a hook behind for the next one to trip over."""
    before = set(registered_hooks())
    yield
    for name in registered_hooks():
        if name not in before:
            unregister_hook(name)


@pytest.fixture
def work_hook() -> Iterator[str]:
    register_hook(HOOK, calls_to(THIS_MODULE, "work"))
    yield HOOK


# ------------------------------------------------------------------- counting


def test_counts_every_call_for_the_duration_of_the_block(work_hook: str) -> None:
    with count(work_hook) as tally:
        for _ in range(5):
            work(10)

    assert tally.events == 5
    assert tally.hook_name == HOOK


def test_calls_outside_the_block_are_not_counted(work_hook: str) -> None:
    work(10)
    with count(work_hook) as tally:
        work(10)
    work(10)

    assert tally.events == 1


def test_two_hooks_can_be_counted_at_once(work_hook: str) -> None:
    """Guard counters require it — a metric is read against what it trades against.

    Queries down while rows returned explodes is not an improvement, and seeing
    that at all means counting both across the same run.
    """
    register_hook(OTHER_HOOK, calls_to(THIS_MODULE, "other_work"))

    with count(work_hook) as work_tally, count(OTHER_HOOK) as other_tally:
        work(10)
        other_work()
        other_work()

    assert (work_tally.events, other_tally.events) == (1, 2)


# ------------------------------------------------------- the safety property


def test_an_unknown_hook_name_raises_rather_than_counting_zero(work_hook: str) -> None:
    """A misnamed instrument must not be able to report absence of events.

    Returning zero here would be indistinguishable from a genuine null result,
    and null results are shippable output in this system. The failure would
    survive review precisely because "we screened it and found nothing" is an
    answer we expect to see.
    """
    with pytest.raises(UnknownHookError) as caught, count("test.works"):
        # the registered hook is "test.work"
        work(10)

    assert caught.value.name == "test.works"
    assert HOOK in caught.value.available
    assert "test.work" in str(caught.value)


def test_registering_a_duplicate_name_is_refused(work_hook: str) -> None:
    """Silently replacing would make two adapters disagree about what a name means."""
    with pytest.raises(HookError, match="already registered"):
        register_hook(HOOK, calls_to(THIS_MODULE, "other_work"))


def test_instrumentation_is_removed_when_the_block_exits(work_hook: str) -> None:
    original = vars(THIS_MODULE)["work"]

    with count(work_hook):
        assert vars(THIS_MODULE)["work"] is not original, "the hook never installed"

    assert vars(THIS_MODULE)["work"] is original


def test_instrumentation_is_removed_even_when_the_body_raises(work_hook: str) -> None:
    """The leak ADR 008 documents: it raises nothing and taxes everything after it."""
    original = vars(THIS_MODULE)["work"]

    with pytest.raises(ZeroDivisionError), count(work_hook):
        work(10)
        _ = 1 / 0

    assert vars(THIS_MODULE)["work"] is original


def test_a_descriptor_that_cannot_be_wrapped_faithfully_is_refused() -> None:
    """Wrapping a classmethod with a plain function changes how it binds.

    The count would be correct and the program would be different, which is the
    one outcome this module exists to prevent. Refusing is the honest option.
    """
    with pytest.raises(HookError, match="classmethod"):
        register_hook(OTHER_HOOK, calls_to(Widget, "build"))
        with count(OTHER_HOOK):
            Widget.build()


def test_an_attribute_the_owner_does_not_define_is_refused() -> None:
    with pytest.raises(HookError, match="does not define"):
        register_hook(OTHER_HOOK, calls_to(Widget, "missing"))
        with count(OTHER_HOOK):
            pass


# ------------------------------------------- the instrument is not observable


def test_counts_match_an_uninstrumented_run(work_hook: str) -> None:
    """The AC that ADR 008 is the cautionary tale for.

    The workload is run twice — once unobserved, once counted — and both the
    return value and the workload's own tally of what it did must be identical.
    A wrapper that dropped a return value, swallowed an exception, or changed a
    branch would show here rather than in a number nobody can check.
    """

    def workload() -> tuple[int, int]:
        total = 0
        calls = 0
        for i in range(1, 8):
            total += work(i * 100)
            calls += 1
            if total % 2 == 0:
                total += work(i)
                calls += 1
        return total, calls

    unobserved_total, unobserved_calls = workload()

    with count(work_hook) as tally:
        observed_total, observed_calls = workload()

    assert observed_total == unobserved_total, "observing the workload changed its output"
    assert observed_calls == unobserved_calls, "observing the workload changed its control flow"
    assert tally.events == unobserved_calls


def test_an_exception_from_the_counted_callable_still_propagates(work_hook: str) -> None:
    """A counter that swallowed an error would report a clean, fictional run."""
    with count(work_hook) as tally, pytest.raises(TypeError):
        work("not a number")  # type: ignore[arg-type]

    assert tally.events == 1, "the call happened, so it counts, even though it failed"


def test_the_wrapper_keeps_the_callables_identity(work_hook: str) -> None:
    """`functools.wraps`, so anything reading `__name__` sees the real one.

    Stack localization (S-3.9) names files and functions back to a human. A
    repository full of frames called `counted` would be useless.
    """
    with count(work_hook):
        assert vars(THIS_MODULE)["work"].__name__ == "work"


# ------------------------------------------------------------------- stacks


def test_stacks_are_not_captured_unless_asked_for(work_hook: str) -> None:
    with count(work_hook) as tally:
        work(10)

    assert tally.events == 1
    assert tally.stacks == []


def test_a_captured_stack_starts_at_the_call_site_not_at_the_observer(
    work_hook: str,
) -> None:
    """Innermost frame first, and no coldfix frame in it.

    An observer frame is present only because the observation is happening. It
    would sit at the top of every event's stack, and S-3.9 localizes a finding
    by walking these stacks to the deepest frame they share — so a frame common
    to all of them by construction is exactly the wrong thing to leave in.
    """

    def call_site() -> None:
        work(10)

    with count(work_hook, capture_stacks=True) as tally:
        call_site()

    assert len(tally.stacks) == 1
    frames = tally.stacks[0]
    assert [frame.name for frame in frames][:2] == [
        "call_site",
        "test_a_captured_stack_starts_at_the_call_site_not_at_the_observer",
    ]
    assert not any("coldfix" in frame.filename for frame in frames)


def test_one_stack_is_captured_per_event(work_hook: str) -> None:
    with count(work_hook, capture_stacks=True) as tally:
        for _ in range(4):
            work(10)

    assert tally.events == 4
    assert len(tally.stacks) == 4


# ------------------------------------------------------------------ overhead


def _batch(calls: int, size: int) -> Callable[[], None]:
    def run() -> None:
        for _ in range(calls):
            work(size)

    return run


def _cost_per_event(hook_name: str, calls: int, size: int, **count_kwargs: bool) -> float:
    """Seconds of counting overhead per event, measured against a batch.

    Conditions alternate within one session rather than being measured in two
    blocks, for the reason S-1.6 exists: anything that drifts during the run —
    thermal state, another process waking up — then lands on both conditions
    rather than on whichever was measured second.
    """
    batch = _batch(calls, size)
    plain: list[float] = []
    counted: list[float] = []

    for _ in range(ROUNDS):
        plain.append(time(batch, 1).durations[0])
        with count(hook_name, **count_kwargs) as tally:
            counted.append(time(batch, 1).durations[0])
        # Without this the overhead tests pass most convincingly when the hook
        # is broken: an instrument that never attaches has no overhead at all.
        assert tally.events == calls, "the instrumented batch was not instrumented"

    return (statistics.median(counted) - statistics.median(plain)) / calls


def _cost_per_call(calls: int, size: int) -> float:
    """Seconds per call of the uninstrumented operation."""
    batch = _batch(calls, size)
    return statistics.median(time(batch, ROUNDS).durations) / calls


def test_counting_overhead_is_under_five_percent(work_hook: str) -> None:
    """The AC — overhead against the cost of the operation being counted.

    Both sides are measured rather than assumed, and they are measured on
    different workloads on purpose: overhead is a fixed per-event cost, so it
    is resolved against an operation cheap enough for it to dominate, while the
    5% is claimed against an operation of the size this instrument is for.

    Five percent is therefore not a property of the counter alone. The failure
    message states the break-even operation cost, which is the honest form of
    the claim and the number a future caller counting something small needs.
    """
    overhead = _cost_per_event(work_hook, TRIVIAL_CALLS, TRIVIAL_SIZE)
    operation = _cost_per_call(CALLS_PER_BATCH, WORK_SIZE)

    assert overhead > 0, "overhead measured at or below zero — the measurement is noise"
    assert overhead / operation < 0.05, (
        f"{overhead * 1e6:.2f}us per event against a {operation * 1e6:.0f}us operation; "
        f"stays under 5% only for operations above {overhead / 0.05 * 1e6:.0f}us"
    )


def test_capturing_stacks_costs_far_more_than_tallying(work_hook: str) -> None:
    """Which is why it is off by default, and why that default is an AC.

    An order of magnitude, not a few percent. Asserted so that a future change
    making capture unconditional cannot be waved through as free.
    """
    tally_only = _cost_per_event(work_hook, STACK_CALLS, TRIVIAL_SIZE)
    with_stacks = _cost_per_event(work_hook, STACK_CALLS, TRIVIAL_SIZE, capture_stacks=True)

    assert with_stacks > tally_only * 10, (
        f"stack capture {with_stacks * 1e6:.2f}us/event vs tally alone "
        f"{tally_only * 1e6:.2f}us/event"
    )

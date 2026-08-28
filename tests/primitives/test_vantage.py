"""Where a measurement was taken, and what the harness must not claim about it.

S-17.6, and the whole story is S-17.5's measurement made structural. That spike
drove an out-of-process subject and timed the call from the harness: 1266 ms
recorded for a 9.6 ms endpoint, and the same workload at three scales fitting
`LINEAR` inside the subject and `CONSTANT` outside it. Screening fits growth on a
duration, so that is not a large number — it is the wrong shape, published as an
exclusion.

The tests here are about the two halves of the fix: under the subject vantage the
harness records none of its own clock, and it refuses the numbers a subject
cannot honestly supply.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Iterator, Mapping

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.primitives.measurement import (
    BLOCKED_SECONDS,
    CPU_SECONDS,
    HARNESS_ONLY_METRICS,
    MATERIALIZED,
    RESERVED_METRICS,
    SECONDS,
    MeasurementError,
    MetricSetError,
    Reported,
    Vantage,
    measure_once,
    vantage_of,
)

HOOK = "db.query"


class Cursor:
    """Something a hook can count calls to."""

    def execute(self, statement: str) -> str:
        return statement


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(HOOK, calls_to(Cursor, "execute"))
    try:
        yield
    finally:
        unregister_hook(HOOK)


def taken_here(**metrics: float) -> Callable[[], Mapping[str, float]]:
    return lambda: dict(metrics)


def reported(**metrics: float) -> Reported:
    """Numbers a subject measured about itself. Constructing one *is* the vantage."""
    return Reported(taken_here(**metrics))


# ============================================ the harness keeps its own clock


def test_the_harness_vantage_is_unchanged_and_is_the_default() -> None:
    """Ten call sites measure something running here and are correct as written.

    A default that changed their meaning would be the defect this story exists
    to prevent, introduced by its fix.
    """
    taken = measure_once(lambda: [1, 2, 3])

    assert set(taken) >= {SECONDS, MATERIALIZED, CPU_SECONDS, BLOCKED_SECONDS}
    assert taken[MATERIALIZED] == 3.0
    assert taken[SECONDS] >= 0.0


def test_the_harness_vantage_still_refuses_a_reported_duration() -> None:
    """The reservation S-17.5 measured, kept.

    Two records of one number are two things that can disagree, and under this
    vantage the harness took the number itself.
    """
    with pytest.raises(MetricSetError, match="already measured"):
        measure_once(lambda: None, extra_counters=taken_here(seconds=0.001))


# ======================================= the subject vantage takes no clock


def test_the_subject_vantage_records_none_of_the_harness_own_clock() -> None:
    """**The fix.** The numbers that would have been wrong are simply absent.

    Not corrected, not down-weighted — a harness that recorded its own idleness
    as the subject's would be publishing a measurement of the wrong process, and
    S-17.5 showed the shape survives that better than the value does.
    """
    slept = measure_once(
        lambda: time.sleep(0.05),
        extra_counters=reported(seconds=0.0001, response_bytes=512.0),
    )

    assert slept[SECONDS] == 0.0001, "the subject's number, not the harness's 0.05"
    assert MATERIALIZED not in slept
    assert CPU_SECONDS not in slept
    assert BLOCKED_SECONDS not in slept


def test_the_reported_duration_is_the_one_kept_even_when_it_disagrees_wildly() -> None:
    """S-17.5's ratio, as a property rather than a measurement.

    The harness's own reading would have been three orders of magnitude larger.
    Under this vantage it is never taken, so there is nothing for it to win.
    """
    started = time.perf_counter()
    taken = measure_once(
        lambda: time.sleep(0.05),
        extra_counters=reported(seconds=0.00001),
    )
    actually_elapsed = time.perf_counter() - started

    assert actually_elapsed > 0.04
    assert taken[SECONDS] == 0.00001


def test_a_subject_measurement_still_drives_the_subject() -> None:
    """Something has to run it. The vantage is about who holds the stopwatch."""
    ran: list[str] = []

    measure_once(
        lambda: ran.append("driven"),
        extra_counters=reported(seconds=0.002),
    )

    assert ran == ["driven"]


def test_a_hook_is_refused_under_the_subject_vantage(query_counter: None) -> None:
    """**S-17.10 reversed S-17.6 here, and measurement is what decided it.**

    S-17.6 kept hook counters under this vantage, reasoning that the Django
    adapter's `execute_wrapper` is in-process so a binding able to reach the
    subject's connections would keep its query count. **That case does not
    exist**: a subject the harness can reach in-process is a subject the harness
    can time, which is `HARNESS`. Under `SUBJECT` the run happened somewhere else,
    so a hook counts zero — always — and files that zero under the name the
    subject's real count belongs to.

    Measured before it was changed: a subject-vantage binding reporting `db.query`
    together with the `db.query` hook a real screen installs raised
    `MetricSetError` on the first measurement, and the message advised renaming
    the metric — which would mean screening's expectation for `db.query` never
    matches and the N+1 is never flagged.

    So this is `HARNESS_ONLY_METRICS`'s own rule reaching a category it had not
    enumerated: a number this process produced about itself.
    """
    cursor = Cursor()

    with pytest.raises(MetricSetError, match="a hook counts what happens in"):
        measure_once(
            lambda: cursor.execute("SELECT 1"),
            counters=[HOOK],
            extra_counters=reported(**{HOOK: 41.0, SECONDS: 0.003}),
        )


def test_the_refusal_names_the_process_rather_than_the_collision() -> None:
    """The old message said *name them differently*, and that advice was wrong.

    Renaming is available and is the worse answer: `subject.db.query` is a metric
    screening has no expectation for, so the count would be measured, fitted, and
    never compared against anything. The refusal has to point at where the number
    came from, because that is the part the caller must change.
    """
    with pytest.raises(MetricSetError, match="Ask the subject to report them instead"):
        measure_once(
            lambda: None,
            counters=[HOOK],
            extra_counters=reported(seconds=0.001),
        )


def test_the_subject_reports_its_own_count_and_it_is_kept(query_counter: None) -> None:
    """What replaces the hook: the count the subject took of itself.

    The hook is registered here and never installed, so the number in the result
    is the subject's 41 rather than the zero this process would have counted.
    """
    taken = measure_once(
        lambda: None,
        extra_counters=reported(**{HOOK: 41.0, SECONDS: 0.003}),
    )

    assert taken[HOOK] == 41.0
    assert taken[SECONDS] == 0.003


# ================================== what a subject may not report about us


@pytest.mark.parametrize("metric", sorted(HARNESS_ONLY_METRICS))
def test_a_subject_cannot_report_a_metric_about_the_harness_process(metric: str) -> None:
    """**Worse than supplying nothing**: numbers about the wrong process.

    `materialized` counts what was drained here; the two rusage figures come
    from reading this interpreter. A subject supplying them is describing the
    harness while the reader believes it is reading the subject.
    """
    with pytest.raises(MetricSetError, match="the wrong process"):
        measure_once(
            lambda: None,
            extra_counters=reported(**{metric: 1.0, SECONDS: 0.001}),
        )


def test_the_transferable_reserved_metrics_are_not_in_the_refused_set() -> None:
    """`seconds` and `instructions` are reserved and a subject *can* measure them.

    That distinction is the story: not every reserved metric means the same
    thing off-process, and refusing all of them would leave the subject vantage
    unable to report the one number it exists to report.
    """
    assert HARNESS_ONLY_METRICS < RESERVED_METRICS
    assert SECONDS not in HARNESS_ONLY_METRICS


def test_a_subject_measurement_with_nothing_reported_cannot_be_constructed() -> None:
    """Stronger than the refusal it replaces: there is no way to ask for it.

    `Reported` holds the supplier, so *the subject measured this* and *here is
    what it measured* are one object. A caller cannot declare the vantage and
    then hand over nothing, because the declaration is the handing over.
    """
    with pytest.raises(TypeError):
        Reported()  # type: ignore[call-arg]  # the point: the numbers are required


def test_the_vantage_is_read_off_the_counters_and_is_never_a_parameter() -> None:
    """AC 1, and the reason the parameter version was wrong twice.

    Sabotage found a `vantage=` argument silently ignorable by a call site that
    forgot to forward it, and `diagnosis.schema` found something worse — an
    enum-annotated parameter reads as a *design* choice, which would offer a
    model the decision of whether the harness should trust its own clock.
    """
    assert "vantage" not in inspect.signature(measure_once).parameters
    assert vantage_of(None) is Vantage.HARNESS
    assert vantage_of(taken_here(response_bytes=1.0)) is Vantage.HARNESS
    assert vantage_of(reported(seconds=0.001)) is Vantage.SUBJECT


def test_a_subject_measurement_without_a_duration_is_refused() -> None:
    """Nobody else can supply it. The harness deliberately did not look."""
    with pytest.raises(MetricSetError, match="no duration was reported"):
        measure_once(
            lambda: None,
            extra_counters=reported(response_bytes=512.0),
        )


def test_every_refusal_is_a_measurement_error() -> None:
    """A caller catching the module's own error catches all of them."""
    assert issubclass(MetricSetError, MeasurementError)

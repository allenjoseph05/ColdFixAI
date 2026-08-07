"""A component alone, among neighbours, and against each neighbour in turn.

S-3.13. `01-primitives.md` §11: the gap between running alone and running in
context *is* the interference — it exists only because something else is
running, which is why no single-component measurement can see it.

The test that matters most here is the one for **no** interference. A primitive
that reports a gap will always find one, because two runs of the same thing
differ; and the gap it reports names a real neighbour, which makes a false
positive worse than a vague answer rather than better. So the isolated condition
is measured repeatedly, its own spread is the floor, and the control — a
component whose neighbour shares nothing with it — has to come back clean.

The subject is a lock two workloads either do or do not share. Sharing it is
contention by construction, so the expected answer is known rather than
observed, and the tests are about whether the instrument finds what is there.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from coldfix.primitives.isolation import (
    Attribution,
    Interference,
    IsolationError,
    attribute_interference,
    measure_interference,
    running,
)
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.sandbox.scope import (
    DiagnoseOnlyError,
    DiagnoseOnlyReason,
    Disposition,
    RepairableFinding,
    classify,
)

REPO = Path("/srv/subject")

# Comfortably above the platform's timer granularity, which on Windows is about
# 15.6ms — at 20ms the contended and uncontended cases landed on the same
# quantum often enough to make this file flaky, measuring 0.0203 alone against
# 0.0205 contended for a workload that holds the lock the whole time. The
# component takes the lock and holds it, so a neighbour holding the same lock
# has to make it wait.
HOLD = 0.05


class SharedResource:
    """The One-Lane Bridge, in the smallest form that is really one."""

    def __init__(self) -> None:
        self.lock = threading.Lock()

    def use(self, seconds: float) -> None:
        with self.lock:
            time.sleep(seconds)


def interference_from(alone: tuple[float, ...], together: tuple[float, ...]) -> Interference:
    """An `Interference` stated directly, for the tests about its arithmetic."""
    return Interference(
        component="checkout",
        context="the report job",
        alone=alone,
        in_context=together,
    )


# ------------------------------------------- AC 1: alone, in context, the gap


def test_a_component_sharing_a_lock_is_slower_among_its_neighbours() -> None:
    """AC 1. The gap exists only because something else is running, which is the
    whole definition of interference."""
    resource = SharedResource()

    result = measure_interference(
        lambda: resource.use(HOLD),
        lambda: resource.use(HOLD),
        name="checkout",
        context_name="the report job",
        repetitions=5,
    )

    assert result.detectable
    assert result.gap > 0
    assert result.ratio > 1.0
    assert result.alone_cost >= HOLD


def test_a_component_sharing_nothing_shows_no_interference() -> None:
    """The control, and the more important test of the two.

    Every pair of measurements differs. A primitive that called that difference
    interference would report contention for every component it ever looked at —
    and it would name a real neighbour while doing it.
    """
    mine = SharedResource()
    theirs = SharedResource()

    result = measure_interference(
        lambda: mine.use(HOLD),
        lambda: theirs.use(HOLD),
        name="checkout",
        context_name="the unrelated job",
        repetitions=5,
    )

    assert not result.detectable
    assert "no interference to report" in result.explanation()


def test_a_gap_inside_the_spread_of_the_isolated_runs_is_not_a_finding() -> None:
    """Stated on the arithmetic rather than the scheduler: the isolated runs
    varied by 0.04 all by themselves, so a gap of 0.01 is the same measurement
    twice."""
    result = interference_from(alone=(0.10, 0.14, 0.11), together=(0.12, 0.12, 0.12))

    assert result.noise == pytest.approx(0.04)
    assert result.gap < result.noise
    assert not result.detectable


def test_a_gap_outside_that_spread_is_a_finding() -> None:
    """The other side of the same rule, so the floor is not simply a way of
    never reporting anything."""
    result = interference_from(alone=(0.10, 0.11, 0.10), together=(0.40, 0.42, 0.41))

    assert result.detectable
    assert result.ratio == pytest.approx(4.1, rel=0.05)


def test_the_gap_is_reported_as_a_ratio_and_a_difference() -> None:
    """A difference says how much time; a ratio says how bad. Whoever reads this
    needs both — 200ms of contention means one thing on a 4-second job and
    another on a 20ms one."""
    result = interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0))

    assert result.gap == pytest.approx(2.0)
    assert result.ratio == pytest.approx(3.0)


def test_the_isolated_condition_needs_enough_runs_to_have_a_spread() -> None:
    """With fewer, the floor is one difference and every gap clears it."""
    with pytest.raises(IsolationError, match="every difference looks like a finding"):
        measure_interference(lambda: None, lambda: None, repetitions=2)


# --------------------------------- which neighbour: the middle step of §17


def test_the_neighbour_actually_contended_with_is_named() -> None:
    """`01-primitives.md` §17's composition — *Load → Isolation → Substitution* —
    needs the middle step to say **which**. A gap against the whole context says
    a component is interfered with; only this says by what."""
    resource = SharedResource()
    unrelated = SharedResource()

    attribution = attribute_interference(
        lambda: resource.use(HOLD),
        {
            "the report job": lambda: unrelated.use(HOLD),
            "the export job": lambda: resource.use(HOLD),
            "the mailer": lambda: unrelated.use(HOLD),
        },
        name="checkout",
        repetitions=5,
    )

    assert attribution.worst is not None
    assert attribution.worst.context == "the export job"
    assert [item.context for item in attribution.culprits] == ["the export job"]


def test_when_no_single_neighbour_contends_the_report_says_so() -> None:
    """A real outcome, and a different finding: if the whole context interferes
    but no neighbour does alone, it is the combination — which is harder and
    should not be reported as one of them."""
    attribution = Attribution(
        component="checkout",
        against=(
            interference_from(alone=(1.0, 1.0, 1.0), together=(1.0, 1.0, 1.0)),
            interference_from(alone=(1.0, 1.0, 1.0), together=(1.0, 1.0, 1.0)),
        ),
    )

    assert attribution.worst is None
    assert "it is the combination rather than any single neighbour" in attribution.explanation()


def test_neighbours_are_ranked_worst_first() -> None:
    """Whoever reads this acts on one of them, and it should be the one that
    costs the most."""
    mild = interference_from(alone=(1.0, 1.0, 1.0), together=(1.5, 1.5, 1.5))
    severe = interference_from(alone=(1.0, 1.0, 1.0), together=(6.0, 6.0, 6.0))
    attribution = Attribution(component="checkout", against=(mild, severe))

    assert [item.ratio for item in attribution.culprits] == [6.0, 1.5]


def test_attributing_with_no_neighbours_is_refused() -> None:
    with pytest.raises(IsolationError, match="nothing for the component to contend with"):
        attribute_interference(lambda: None, {})


# ------------------------------------------------- the context really runs


def test_the_context_is_running_during_the_measurement() -> None:
    """The property the whole primitive rests on. A context that had not started
    yet would make every component look uncontended."""
    ran = 0
    lock = threading.Lock()

    def neighbour() -> None:
        nonlocal ran
        with lock:
            ran += 1
        time.sleep(0.005)

    with running(neighbour, workers=2):
        time.sleep(0.05)
        during = ran

    assert during > 0


def test_the_context_stops_when_the_block_ends() -> None:
    """A neighbour still running after the measurement taxes everything measured
    afterwards — S-1.3's rule about instrumentation, applied to scenery."""
    ran = 0
    lock = threading.Lock()

    def neighbour() -> None:
        nonlocal ran
        with lock:
            ran += 1
        time.sleep(0.005)

    with running(neighbour, workers=2):
        time.sleep(0.03)
    settled = ran
    time.sleep(0.05)

    assert ran == settled


def test_the_context_stops_even_when_the_measurement_raises() -> None:
    """The case that leaves neighbours running forever if it is missed: a
    measurement that failed is exactly when the load is most likely to be left
    on, and every measurement afterwards would be taken against it."""
    ran = 0
    lock = threading.Lock()

    def neighbour() -> None:
        nonlocal ran
        with lock:
            ran += 1
        time.sleep(0.005)

    with pytest.raises(RuntimeError, match="deliberate"), running(neighbour, workers=2):
        time.sleep(0.02)
        message = "deliberate"
        raise RuntimeError(message)

    settled = ran
    time.sleep(0.05)

    assert ran == settled


def test_a_neighbour_that_fails_does_not_stop_the_context() -> None:
    """The context is scenery. A neighbour failing under load is S-3.16's
    finding; what matters here is that it keeps occupying what it shares."""
    attempts = 0
    lock = threading.Lock()

    def flaky() -> None:
        nonlocal attempts
        with lock:
            attempts += 1
        message = "the neighbour is unhappy"
        raise RuntimeError(message)

    with running(flaky, workers=1):
        time.sleep(0.05)

    assert attempts > 1


def test_a_context_with_no_workers_is_refused() -> None:
    with (
        pytest.raises(IsolationError, match="at least one worker"),
        running(lambda: None, workers=0),
    ):
        pass


# ------------------------------------------- AC 2: diagnose-only, structurally


def test_the_finding_is_diagnose_only() -> None:
    """AC 2. §11's standing restriction, which is what lets the rest of this
    system's patches claim to be safe."""
    result = interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0))

    assert result.disposition is Disposition.DIAGNOSE_ONLY
    assert "never patched" in result.explanation()


def test_the_mechanism_is_refused_by_the_scope_check() -> None:
    """The structural half. S-2.9 classifies the sentence independently, so this
    module does not have to be trusted to remember what it produced."""
    result = interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0))

    verdict = classify(result.mechanism, "app/views.py", repository=REPO)

    assert verdict.disposition is Disposition.DIAGNOSE_ONLY
    assert DiagnoseOnlyReason.CONCURRENCY in verdict.reasons


def test_an_interference_finding_cannot_reach_the_repair_path() -> None:
    result = interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0))

    with pytest.raises(DiagnoseOnlyError):
        RepairableFinding(mechanism=result.mechanism, site="app/views.py", repository=REPO)


def test_an_attribution_is_diagnose_only_too() -> None:
    """Naming the neighbour makes the finding more actionable and no more
    patchable."""
    attribution = Attribution(
        component="checkout",
        against=(interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0)),),
    )

    assert attribution.disposition is Disposition.DIAGNOSE_ONLY


def test_the_finding_says_the_size_still_needs_confirming() -> None:
    """The same discipline S-3.10 applies to a sweep: the gap is real, and its
    magnitude is one sample per condition against a noise floor."""
    result = interference_from(alone=(1.0, 1.0, 1.0), together=(3.0, 3.0, 3.0))

    assert "confirm the magnitude with an interleaved comparison" in result.explanation()


def test_the_primitive_is_registered() -> None:
    primitive = REGISTRY.get("isolation.interference")

    assert primitive.required_capabilities == {
        Capability.LOAD_GENERATION,
        Capability.STATE_RESET,
    }
    assert primitive.run is measure_interference

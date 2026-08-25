"""A sleep and a busy loop take the same wall clock and want opposite fixes.

S-3.7. The story's note is the whole subject: an ablation tells you a component
is expensive and never whether it computed or waited, and those have nothing in
common as fixes. One subtraction separates them, and until now nothing in the
system was doing it.

The pair is what makes the tests mean anything. A test that only shows a sleep
reporting blocked time passes against an implementation that reports blocked time
for everything, so every case here has its opposite beside it: sleep against busy
loop, unavailable against zero, and a container against the host.

`resource` is absent on Windows, so the context-switch signals are `None` here
and real in the sandbox — which is why AC 3 is tested by running the measurement
inside a container rather than by asserting it would work there.
"""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from coldfix.bench.counting import count, register_hook, unregister_hook
from coldfix.primitives.counters import CATALOGUE, describe
from coldfix.primitives.measurement import (
    BLOCKED_SECONDS,
    CPU_SECONDS,
    SECONDS,
    TOTAL_SUFFIX,
    MetricKind,
    measure_once,
    metric_kind,
)
from coldfix.primitives.off_cpu import (
    BLOCKED_DISK,
    BLOCKED_LOCK,
    BLOCKED_NETWORK,
    Boundedness,
    OffCpuCategory,
    OffCpuProfile,
    blocking,
    counter_for,
    off_cpu,
)
from coldfix.sandbox import docker_available
from coldfix.sandbox.runner import Sandbox
from fixtures.containers import require_image

SLEEP = 0.15

# `process_time` ticks at about 15.6ms on Windows, so `wall - cpu` can understate
# blocked time by a whole tick — measured here as a sleep of 0.15s reporting
# 0.1347s blocked, which is arithmetic rather than a defect. Assertions about
# blocked time allow for one tick; a fraction of the sleep would not, and a
# looser fraction would stop being an assertion.
CLOCK_TICK = 0.020
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="needs getrusage")


def busy_for(seconds: float) -> int:
    """Burn CPU for roughly `seconds`, without sleeping."""
    deadline = time.perf_counter() + seconds
    spins = 0
    while time.perf_counter() < deadline:
        spins += 1
    return spins


class Client:
    """A stand-in for whatever an adapter would declare as a waiting point."""

    def fetch(self, seconds: float) -> str:
        time.sleep(seconds)
        return "rows"

    def explode(self, seconds: float) -> str:
        time.sleep(seconds)
        message = "the connection timed out"
        raise TimeoutError(message)


# ------------------------------------------ AC 4: a sleep, and its control


def test_a_sleep_reports_blocked_time_rather_than_cpu_time() -> None:
    """AC 4. `perf_counter` is elapsed, `process_time` is CPU charged to this
    process, so the difference is time the process existed and was not running."""
    with off_cpu() as profile:
        time.sleep(SLEEP)

    assert profile.wall_seconds >= SLEEP
    assert profile.cpu_seconds < SLEEP / 2
    assert profile.blocked_seconds >= SLEEP - CLOCK_TICK
    assert profile.boundedness is Boundedness.BLOCKED


@pytest.mark.timing
def test_a_busy_loop_reports_cpu_time_rather_than_blocked_time() -> None:
    """The control, and the reason the test above says anything.

    Same wall clock, opposite answer, opposite fix. An implementation reporting
    blocked time for everything passes the sleep test and fails here.
    """
    with off_cpu() as profile:
        busy_for(SLEEP)

    assert profile.wall_seconds >= SLEEP * 0.9
    assert profile.cpu_seconds >= SLEEP * 0.7
    assert profile.boundedness is Boundedness.COMPUTE_BOUND


@pytest.mark.timing
def test_the_two_are_the_same_wall_clock_and_different_findings() -> None:
    """Stated as one assertion because it is the story in one line."""
    with off_cpu() as waited:
        time.sleep(SLEEP)
    with off_cpu() as computed:
        busy_for(SLEEP)

    assert waited.wall_seconds == pytest.approx(computed.wall_seconds, rel=0.5)
    assert waited.boundedness is not computed.boundedness


def test_a_run_that_does_both_is_reported_as_mixed() -> None:
    """Rather than rounded to whichever side is larger. A fix aimed at one half
    of a mixed run addresses one half of the cost, and saying so is more useful
    than picking a winner.

    **Constructed rather than provoked, which is the argument two tests down
    applied to this one.** That test declines to make four threads race because
    *a test that sometimes lands between two classifications tests the machine's
    scheduler rather than the classification*. This one provoked its condition
    with a real half-and-half block, and S-0.9 measured what that costs: under
    twice the core count in spinners it reported the wrong class **five times out
    of five**, because the scheduler stops giving the busy half a core and
    wall-clock time inflates while CPU time does not.

    What is under test here is the boundary arithmetic. That the instrument can
    measure a real busy loop and a real sleep is a different claim, tested above
    against the clock and marked `timing` for the same reason.
    """
    profile = OffCpuProfile(wall_seconds=SLEEP, cpu_seconds=SLEEP / 2)

    assert profile.boundedness is Boundedness.MIXED


@pytest.mark.timing
def test_a_real_half_and_half_run_is_measured_as_mixed() -> None:
    """The empirical half, kept and quarantined rather than deleted.

    The classification above is arithmetic over two numbers; this is the claim
    that the two numbers come back right off a real block that computes for half
    its life and waits for the other half. Both are worth having and only one of
    them belongs in a gate that has to be trusted.
    """
    with off_cpu() as profile:
        busy_for(SLEEP / 2)
        time.sleep(SLEEP / 2)

    assert profile.boundedness is Boundedness.MIXED


def test_more_cpu_than_wall_clock_is_reported_as_parallel() -> None:
    """Not an error and not clamped to zero. It means the work ran on more than
    one core, so elapsed-minus-CPU stops being time spent waiting — and a run
    that hits it needs the load primitive, not a subtraction.

    Constructed rather than provoked. Four Python threads in a busy loop cannot
    reliably produce this condition, because the GIL keeps them on one core, and
    a test that sometimes lands between "parallel enough to classify" and
    "parallel enough to skip" tests the machine's scheduler rather than the
    classification. The condition is what matters and it is stated directly.
    """
    profile = OffCpuProfile(wall_seconds=1.0, cpu_seconds=3.5)

    assert profile.boundedness is Boundedness.PARALLEL
    assert profile.blocked_seconds < 0


def test_threads_that_do_wait_are_still_measured_as_waiting() -> None:
    """The threaded case that *is* reliable: sleeping threads release the GIL,
    so the process is genuinely off the CPU while they wait."""
    threads = [threading.Thread(target=time.sleep, args=(SLEEP,)) for _ in range(4)]

    with off_cpu() as profile:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert profile.boundedness is Boundedness.BLOCKED


def test_the_explanation_says_what_to_do_about_it() -> None:
    """The distinction is only worth making if it reaches whoever picks the fix."""
    with off_cpu() as profile:
        time.sleep(SLEEP)

    assert "waiting rather than computing" in profile.explanation()


def test_elapsed_time_is_still_divided_when_the_block_raises() -> None:
    """Which side a failed run spent its time on is often the most useful thing
    known about it."""
    profile: OffCpuProfile | None = None

    with pytest.raises(TimeoutError), off_cpu() as measured:
        profile = measured
        time.sleep(SLEEP)
        message = "deliberate"
        raise TimeoutError(message)

    assert profile is not None
    assert profile.blocked_seconds >= SLEEP - CLOCK_TICK


# ---------------------------------- AC 1: what the operating system counted


@POSIX_ONLY
def test_a_sleep_shows_up_as_a_voluntary_context_switch() -> None:
    """The process gave up the CPU to wait. The coarse signal, which covers every
    kind of waiting at once — which is why `blocking()` exists for the kinds an
    adapter can name."""
    with off_cpu() as profile:
        time.sleep(SLEEP)

    assert profile.voluntary_switches is not None
    assert profile.voluntary_switches >= 1


@POSIX_ONLY
def test_scheduler_queueing_is_measured_from_involuntary_switches() -> None:
    """AC 1's fourth category, and the only one with no hook: being preempted is
    not a call anything can wrap.

    A busy loop that outlasts its scheduling quantum gets taken off the CPU, and
    that count is saturation in the USE Method's sense — time spent ready and not
    running, invisible to every other instrument here.
    """
    with off_cpu() as profile:
        busy_for(SLEEP * 4)

    assert profile.scheduler_signal_available
    assert profile.involuntary_switches is not None


def test_an_unavailable_signal_is_absent_rather_than_zero() -> None:
    """ADR 013's rule in its original form.

    Zero involuntary context switches is a publishable finding — *nothing was
    preempted, so the cost is not queueing* — so a platform that cannot measure
    must not be able to produce it.
    """
    with off_cpu() as profile:
        time.sleep(0.01)

    if sys.platform == "win32":
        assert profile.involuntary_switches is None
        assert not profile.scheduler_signal_available
        assert "could not be measured on this platform" in profile.explanation()
    else:
        assert profile.scheduler_signal_available


def test_the_explanation_never_claims_a_signal_it_does_not_have() -> None:
    profile = OffCpuProfile(wall_seconds=1.0, cpu_seconds=0.1)

    assert "not the same as none having occurred" in profile.explanation()


# --------------------------------- AC 1: what an adapter declares as waiting


def test_a_declared_waiting_point_records_the_seconds_it_waited() -> None:
    """The adapter's half. The events are the calls and the total is the seconds,
    which is S-3.6's magnitude record used for time instead of rows."""
    client = Client()
    register_hook(BLOCKED_NETWORK, blocking(Client, "fetch", OffCpuCategory.NETWORK))
    try:
        with count(BLOCKED_NETWORK) as tally:
            client.fetch(SLEEP)
            client.fetch(SLEEP)
    finally:
        unregister_hook(BLOCKED_NETWORK)

    assert tally.events == 2
    assert tally.total >= 2 * SLEEP - CLOCK_TICK


def test_a_waiting_point_that_fails_still_reports_what_it_waited() -> None:
    """A workload whose database call times out has waited for exactly as long
    as the timeout, and that is the finding."""
    client = Client()
    register_hook(BLOCKED_NETWORK, blocking(Client, "explode", OffCpuCategory.NETWORK))
    try:
        with count(BLOCKED_NETWORK) as tally, pytest.raises(TimeoutError):
            client.explode(SLEEP)
    finally:
        unregister_hook(BLOCKED_NETWORK)

    assert tally.events == 1
    assert tally.total >= SLEEP - CLOCK_TICK


def test_the_waiting_point_is_restored_afterwards() -> None:
    original = Client.fetch
    register_hook(BLOCKED_DISK, blocking(Client, "fetch", OffCpuCategory.DISK))
    try:
        with count(BLOCKED_DISK):
            assert Client.fetch is not original
    finally:
        unregister_hook(BLOCKED_DISK)

    assert Client.fetch is original


def test_the_scheduler_has_no_hook_and_saying_so_is_the_point() -> None:
    """Being preempted is not a call. A category that quietly accepted a wrapper
    would let an adapter believe it had instrumented queueing."""
    with pytest.raises(ValueError, match="not a call anything can wrap"):
        blocking(Client, "fetch", OffCpuCategory.SCHEDULER)

    with pytest.raises(ValueError, match="context switches"):
        counter_for(OffCpuCategory.SCHEDULER)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (OffCpuCategory.DISK, BLOCKED_DISK),
        (OffCpuCategory.NETWORK, BLOCKED_NETWORK),
        (OffCpuCategory.LOCK, BLOCKED_LOCK),
    ],
)
def test_each_hookable_category_has_a_catalogued_counter(
    category: OffCpuCategory, expected: str
) -> None:
    """S-3.6's catalogue is the spelling, and blocked time is not exempt."""
    assert counter_for(category) == expected
    assert expected in CATALOGUE
    assert "seconds" in describe(expected).amount


def test_blocked_time_totals_are_durations_not_counts() -> None:
    """A metric whose amount is seconds must not be read as an exact count, or
    a difference the noise floor covers becomes a conclusion."""
    assert metric_kind(f"{BLOCKED_NETWORK}{TOTAL_SUFFIX}") is MetricKind.DURATION
    assert metric_kind(BLOCKED_NETWORK) is MetricKind.COUNT


# ------------------------------------------- AC 2: in the experiment result


@pytest.mark.timing
def test_every_measurement_carries_the_distinction() -> None:
    """AC 2. Recorded on every run rather than only when off-CPU time is already
    the hypothesis, because it costs two clock reads and because a delta that
    does not say whether the component computed or waited leads to the wrong fix
    as easily as the right one."""
    waited = measure_once(lambda: time.sleep(SLEEP))
    computed = measure_once(lambda: busy_for(SLEEP))

    assert waited[BLOCKED_SECONDS] > waited[CPU_SECONDS]
    assert computed[CPU_SECONDS] > computed[BLOCKED_SECONDS]
    assert waited[SECONDS] == pytest.approx(computed[SECONDS], rel=0.5)


def test_the_two_new_metrics_are_marked_as_timings() -> None:
    assert metric_kind(CPU_SECONDS) is MetricKind.DURATION
    assert metric_kind(BLOCKED_SECONDS) is MetricKind.DURATION


# ------------------------------------------------ AC 3: inside the sandbox


CONTAINER_PROBE = textwrap.dedent(
    """
    import json, resource, time
    started, cpu = time.perf_counter(), time.process_time()
    before = resource.getrusage(resource.RUSAGE_SELF)
    time.sleep(0.2)
    deadline = time.perf_counter() + 0.2
    while time.perf_counter() < deadline:
        pass
    after = resource.getrusage(resource.RUSAGE_SELF)
    wall = time.perf_counter() - started
    used = time.process_time() - cpu
    print(json.dumps({
        "wall": wall,
        "cpu": used,
        "blocked": wall - used,
        "voluntary": after.ru_nvcsw - before.ru_nvcsw,
        "involuntary": after.ru_nivcsw - before.ru_nivcsw,
    }))
    """
)


IMAGE = "python:3.12-slim"


@pytest.mark.docker
def test_the_measurement_works_inside_the_container_sandbox(tmp_path: Path) -> None:
    """AC 3, run rather than assumed.

    The host here is Windows, where `resource` does not exist at all, so the
    signals this story leans on are exactly the ones that cannot be checked
    locally. The sandbox is Linux and is where every real measurement is taken,
    so the check belongs inside it.

    The probe is written out rather than imported because the sandbox mounts one
    directory and carries no installed package (S-2.1); what is being proved is
    that the *measurement* survives the container, not that the import path does.
    """
    # **Both guards, and the second was missing.** The image check answers *can
    # this container start*; it says nothing about whether the daemon can clean up
    # afterwards. On a degraded Docker Desktop this test built its container, took
    # its measurement, and then failed in teardown when `docker rm --force` blew
    # past sixty seconds — reported as `ContainerNotDestroyedError`, which is a
    # real safety failure (S-2.2) about somebody else's daemon.
    if not docker_available():
        pytest.skip("no usable Docker daemon")
    require_image(IMAGE)

    (tmp_path / "probe.py").write_text(CONTAINER_PROBE, encoding="utf-8")
    result = Sandbox(image=IMAGE, workspace=tmp_path).run(
        ["python", "/workspace/probe.py"], timeout=120.0
    )

    assert result.exit_code == 0, result.stderr
    measured = json.loads(result.stdout)

    assert measured["blocked"] >= 0.15
    assert measured["cpu"] >= 0.15
    assert measured["voluntary"] >= 1
    assert measured["involuntary"] is not None

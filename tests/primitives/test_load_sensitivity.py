"""S-0.9 AC 3 — the flake, provoked rather than waited for.

`CLAUDE.md` requires the fast subset green before a story is called done, and it
was not reliably green: five sightings across three months, always in
`tests/primitives/`, always attributed by re-running standalone and checking that
the change in hand could not have reached the failing module. That check costs
three full subset runs and is the only thing standing between a flake and a
laundered regression.

This file is the instrument that replaces the waiting. It runs a measurement that
is known to be load-sensitive, under load, and asserts the machine moved it — so
*this suite contains tests whose answers depend on the scheduler* becomes a thing
the suite demonstrates rather than a thing somebody remembers.

**Marked `timing` like the tests it is about.** It needs the machine quiet before
it makes it busy, which is the same requirement, and a demonstration of load
sensitivity running inside an already-loaded gate would be measuring the gate.
"""

from __future__ import annotations

import time

import pytest

from coldfix.primitives.off_cpu import Boundedness, off_cpu
from fixtures.contention import DEFAULT_OVERSUBSCRIPTION, under_load

pytestmark = [pytest.mark.timing, pytest.mark.slow]

SLEEP = 0.15


def busy_for(seconds: float) -> int:
    deadline = time.perf_counter() + seconds
    spins = 0
    while time.perf_counter() < deadline:
        spins += 1
    return spins


def half_and_half() -> Boundedness:
    """Half a busy loop and half a sleep — the shape `off_cpu` calls `MIXED`."""
    with off_cpu() as profile:
        busy_for(SLEEP / 2)
        time.sleep(SLEEP / 2)
    return profile.boundedness


def test_the_measurement_is_right_on_a_quiet_machine() -> None:
    """**The control, and it has to come first.** A demonstration that a
    measurement breaks under load says nothing unless the same measurement holds
    without it — otherwise the test is simply broken and the load is decoration.
    """
    assert half_and_half() is Boundedness.MIXED


def test_the_same_measurement_is_wrong_when_the_cores_are_oversubscribed() -> None:
    """**AC 3.** The scheduler stops giving the busy half a core, so wall-clock
    time inflates while CPU time does not, and a run that really was half compute
    is classified as though it spent its time waiting.

    Nothing about the code under test changed between this and the control. That
    is the point: the assertion two tests down in `test_off_cpu.py` is measuring
    the machine as much as the classification, and it took three months of chance
    sightings to notice.
    """
    with under_load() as processes:
        assert processes >= DEFAULT_OVERSUBSCRIPTION
        assert half_and_half() is not Boundedness.MIXED


def test_a_load_generator_leaves_nothing_running() -> None:
    """A spinner is an infinite loop. One escaping into the rest of a suite run
    would turn this from an instrument for finding load-sensitive tests into a
    machine for creating them."""
    with under_load(processes=2):
        pass

    quiet = time.perf_counter()
    busy_for(0.05)
    assert time.perf_counter() - quiet < 0.5, "the machine is its own again"

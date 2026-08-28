"""Rebinding a workload artifact, and where its numbers come from.

S-17.10. The whole measurement-boundary thread lands here: S-17.5 measured that a
harness timing an out-of-process subject fits a linear workload as `CONSTANT`,
S-17.6 made *the subject measured this* a type, and this is the first thing that
produces one.

The tests that matter are about the two ways this can be silently wrong. The
numbers can come from the wrong process — which the vantage now prevents — or they
can come from the wrong *invocation*, which nothing prevents but the cell, and
which produces a growth curve fitted over one measurement repeated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Growth
from coldfix.explorer import work as work_module
from coldfix.explorer.work import Drive
from coldfix.orchestrator.adapters import Binder
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS, Reported, Vantage
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.binding import BindingError, bind_workload, binder_for
from coldfix.screening.growth import ScreeningError, screen_growth
from coldfix.screening.workload import RESPONSE_BYTES, FixtureRecipe, Workload


class NoReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None: ...
    def begin(self) -> None: ...
    def reset(self) -> None: ...


def verified() -> VerifiedReset:
    return VerifiedReset(
        mechanism=NoReset(),
        report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
    )


def descriptor(entity: str = "author") -> Workload:
    return Workload(
        id="books",
        description="the books list",
        entry_point="/books/",
        fixture=FixtureRecipe(
            entity=entity,
            per_parent=2,
            distribution=Distribution.UNIFORM,
            source="synthesis",
            seed=0,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )


class Subject:
    """A subject with an N+1, measured from inside itself.

    Queries are `n + 1` and the payload grows with `n`, so a screen of this has a
    known answer: `db.query` is `LINEAR` where a round trip count is expected to be
    `CONSTANT`. The duration is derived from `n` rather than timed, which is the
    point — under this vantage no clock in the harness is consulted at all.
    """

    def __init__(self) -> None:
        self.drives: list[int] = []

    def drive(self, *, scale: int, **_: Any) -> Drive:
        self.drives.append(scale)
        return Drive(
            scale=scale,
            queries=scale + 1,
            response_bytes=scale * 64,
            seconds=0.002 * scale,
            samples=(0.002 * scale,),
            warmup_seconds=0.05,
            status=200,
            created={"author": scale},
        )


@pytest.fixture
def subject(monkeypatch: pytest.MonkeyPatch) -> Subject:
    """Replaces `drive` and `synthesize`, which are the two subprocess boundaries.

    Nothing is mocked *inside* the binding — `invoke`, the cell and the `Reported`
    are the real ones. What is replaced is the pair that would need a Django
    checkout and a container, which is S-17.9's territory and tested there.
    """
    made = Subject()
    monkeypatch.setattr("coldfix.screening.binding.drive", lambda *a, **k: made.drive(**k))
    monkeypatch.setattr(
        "coldfix.screening.binding.synthesize",
        lambda *a, **k: _Synthesized({"author": k["count"]}),
    )
    return made


class _Synthesized:
    def __init__(self, created: Mapping[str, int]) -> None:
        self.created = dict(created)


def bound(subject_scale: str = "author") -> Any:
    return bind_workload(
        descriptor(subject_scale),
        root=Path("/subject"),
        python=["python"],
        surface=_Surface(),
        reset=verified(),
    )


class _NeverRunError(AssertionError):
    """The binding drives through `drive`, never the surface directly."""


class _Surface:
    root = Path("/subject")

    def run(self, command: Sequence[str], **kwargs: Any) -> Any:  # pragma: no cover
        # The binding reaches the subject through `drive`, which owns the injected
        # program and the sampling. A binding running its own command here would be
        # a second driver with its own idea of what a repeat is.
        raise _NeverRunError


# ================================================ AC 1: the numbers are the subject's


def test_a_binding_takes_the_subject_vantage_because_of_what_it_supplies(
    subject: Subject,
) -> None:
    """**AC 1.** Nobody declares it. `extra_counters` is a `Reported` and the
    vantage is read off that, which is S-17.6's whole design."""
    binding = bound()

    assert isinstance(binding.extra_counters, Reported)
    assert binding.vantage is Vantage.SUBJECT


def test_the_reported_numbers_are_the_ones_the_subject_measured(subject: Subject) -> None:
    """The three metrics `drive` returns, carried without the harness touching them."""
    binding = bound()
    binding.scale(40)
    binding.invoke()

    assert isinstance(binding.extra_counters, Reported)
    reported = binding.extra_counters.counters()

    assert reported[SECONDS] == pytest.approx(0.08)
    assert reported[DB_QUERY] == 41.0
    assert reported[RESPONSE_BYTES] == 40 * 64


# ==================================== AC 4's real risk: the numbers of the wrong run


def test_reading_the_counters_twice_is_refused_rather_than_stale(subject: Subject) -> None:
    """**The property this module exists to hold.**

    `scale_volume` runs reset, seed, invoke, then reads — once per scale point. A
    cell that survived into the next point would report the previous scale's
    numbers, and three points reporting one measurement fit `CONSTANT`. That is
    S-17.5's failure reached by a different route, and it would look like a
    perfectly healthy exclusion.
    """
    binding = bound()
    binding.scale(10)
    binding.invoke()

    assert isinstance(binding.extra_counters, Reported)
    binding.extra_counters.counters()

    with pytest.raises(BindingError, match="without a drive to read them from"):
        binding.extra_counters.counters()


def test_reading_the_counters_before_any_drive_is_refused(subject: Subject) -> None:
    """An experiment reporting numbers nobody took."""
    binding = bound()

    assert isinstance(binding.extra_counters, Reported)
    with pytest.raises(BindingError, match="numbers nobody took"):
        binding.extra_counters.counters()


def test_each_scale_point_reports_its_own_drive(subject: Subject) -> None:
    """The positive form of the same property, asserted across three points."""
    binding = bound()
    assert isinstance(binding.extra_counters, Reported)

    seen = []
    for n in (10, 40, 160):
        binding.scale(n)
        binding.invoke()
        seen.append(binding.extra_counters.counters()[DB_QUERY])

    assert seen == [11.0, 41.0, 161.0]
    assert subject.drives == [10, 40, 160]


# ============================== AC 4: a screen fits growth on the subject's numbers


def test_a_screen_of_a_bound_workload_fits_the_subject_own_numbers(subject: Subject) -> None:
    """**AC 4**, against a workload whose answer is known.

    An N+1 is `n + 1` queries where a round trip count is expected to be constant,
    so this is the defect the system exists to find, measured end to end through
    the binding. The duration is derived from `n` inside the subject, so a fit of
    `LINEAR` on `seconds` is the subject's growth and not the harness's clock.
    """
    screened = screen_growth(bound(), scales=[10, 40, 160], counters=[DB_QUERY])

    assert screened.vantage is Vantage.SUBJECT
    assert screened.metric(DB_QUERY).growth in {Growth.LINEAR, Growth.SUPERLINEAR}
    assert screened.metric(SECONDS).growth in {Growth.LINEAR, Growth.SUPERLINEAR}


def test_the_screen_installs_no_hook_against_a_subject_vantage_binding(
    subject: Subject,
) -> None:
    """S-17.10's rule, from the caller's side.

    Measured before it was fixed: a subject-vantage binding reporting `db.query`
    plus the `db.query` hook a real screen installs raised `MetricSetError` on the
    first measurement, and the advice was to rename the metric — which would mean
    screening's expectation for `db.query` never matches and the N+1 is never
    flagged. The screen now declines to install what cannot fire.
    """

    class Cursor:
        def execute(self, statement: str) -> str:  # pragma: no cover - never called
            return statement

    register_hook(DB_QUERY, calls_to(Cursor, "execute"))
    try:
        screened = screen_growth(bound(), scales=[10, 40, 160], counters=[DB_QUERY])
    finally:
        unregister_hook(DB_QUERY)

    assert screened.metric(DB_QUERY).growth is not Growth.CONSTANT, (
        "the count came from the subject, not from a hook that counted zero"
    )


def test_a_counter_the_subject_did_not_report_is_refused(subject: Subject) -> None:
    """**AC 3.** Declining to install hooks must not silently lose a metric.

    This is the Epic 16 composition check's failure with a new cause: a screen
    that measured no queries could not verify the work and emitted a null result
    covering nothing, and said so only in a field nobody read.
    """
    with pytest.raises(ScreeningError, match="was screened for"):
        screen_growth(bound(), scales=[10, 40, 160], counters=[DB_QUERY, "db.rows"])


# ====================================================== AC 5: the field has a producer


def test_binder_for_binds_every_artifact_it_is_given(subject: Subject) -> None:
    """**AC 5.** `Resources.bind` takes a sequence and returns a sequence."""
    bind = binder_for(
        Path("/subject"),
        python=["python"],
        surface=_Surface(),
        reset=verified(),
    )
    assert callable(bind)

    bindings = bind([descriptor(), descriptor()])

    assert len(bindings) == 2
    assert all(binding.vantage is Vantage.SUBJECT for binding in bindings)


def test_the_produced_callable_satisfies_the_binder_protocol(subject: Subject) -> None:
    """Asserted here rather than in `screening/`, because the protocol lives in the
    orchestrator and the dependency runs that way."""
    bind: Binder = binder_for(  # type: ignore[assignment]  # the point: it fits
        Path("/subject"),
        python=["python"],
        surface=_Surface(),
        reset=verified(),
    )

    assert len(bind([descriptor()])) == 1


def test_the_binding_reads_its_route_and_its_entity_off_the_artifact(
    subject: Subject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binding that chose either would measure a different workload from the one
    the screen names."""
    asked: dict[str, Any] = {}
    monkeypatch.setattr(
        "coldfix.screening.binding.synthesize",
        lambda *a, **k: asked.update(k) or _Synthesized({"widget": k["count"]}),
    )
    monkeypatch.setattr(
        "coldfix.screening.binding.drive",
        lambda *a, **k: asked.update(path=k["path"]) or subject.drive(**k),
    )

    binding = bound("widget")
    binding.scale(25)
    binding.invoke()

    assert asked["target"] == "widget"
    assert asked["count"] == 25
    assert asked["path"] == "/books/"


def test_the_module_is_referenced_so_the_boundary_stays_visible() -> None:
    """`drive` is the subject-facing call the whole thread is about."""
    assert hasattr(work_module, "drive")

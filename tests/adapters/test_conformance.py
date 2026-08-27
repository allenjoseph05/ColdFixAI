"""The conformance suite, checked against both real adapters and against fakes.

S-14.4. A suite that only ever runs against adapters that pass proves nothing
about the suite. So every check here is exercised twice: once against a real
adapter, where it must pass, and once against a deliberately broken one, where it
must fail and say why.

The broken adapters are the point. Each one is a plausible mistake an implementer
would actually make — narrowing the protected paths because the defaults looked
like somebody else's problem, claiming a capability because the enum member
existed, reporting a median without computing one — and each is invisible to
every other check in the suite.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from coldfix.adapters import Declarations, FrameworkAdapter, Subject
from coldfix.adapters.conformance import (
    CHECKS,
    PROTECTED_PROBE,
    Inputs,
    Outcome,
    Report,
    Result,
    check_capabilities,
    check_hook_overhead,
    check_internal_frames,
    check_measurement,
    check_patch_refusal,
    check_protected_paths,
    run_conformance,
)
from coldfix.adapters.django import DjangoAdapter
from coldfix.adapters.flask import FlaskAdapter
from coldfix.bench.counting import Hook, Record, calls_to
from coldfix.bench.execute import DEFAULT_MAX_OUTPUT_CHARS, ExecutionResult
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.work import Drive
from coldfix.primitives.counters import DB_QUERY, REFERENCE_OPERATION_SECONDS
from coldfix.primitives.registry import Capability
from coldfix.sandbox.modes import CandidateSession, Session
from coldfix.sandbox.patching import DEFAULT_PATCH_POLICY, PatchPolicy
from coldfix.sandbox.reset import ResetMechanism
from coldfix.sandbox.worktrees import Worktree
from coldfix.screening.workload import FixtureRecipe

FLASK_APPLICATION = """
from flask import Flask

app = Flask(__name__)


@app.route("/tickets")
def list_tickets():
    return {"tickets": []}
"""


@pytest.fixture
def subject(tmp_path: Path) -> Subject:
    """A repository with one route in it, and a git worktree behind it."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "app.py").write_text(FLASK_APPLICATION, encoding="utf-8")
    (root / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")
    return Subject(root=root, python=[sys.executable])


@pytest.fixture
def session(subject: Subject) -> CandidateSession:
    """A candidate session over a real git checkout, with the real patch filter.

    `apply_patch` is **not** overridden: the protected-path check is only
    meaningful against the session that owns the filter, and the filter runs git
    against a real worktree. Only `run` is stubbed, because that is the one
    operation that would need a container.
    """
    root = subject.root
    _git(root, "init", "--initial-branch=main")
    (root / PROTECTED_PROBE).parent.mkdir(parents=True, exist_ok=True)
    (root / PROTECTED_PROBE).write_text("def test_it(): assert True\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-m", "first")
    return _Session(root)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True, timeout=120)


class _Session(CandidateSession):
    """A real candidate session, minus the container."""

    def __init__(self, path: Path) -> None:
        self._worktree = Worktree(path=path, revision="0" * 40, is_main=False)
        self._policy = DEFAULT_PATCH_POLICY
        self._closed = False

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        del timeout, env, max_output_chars
        return ExecutionResult(
            command=tuple(command), exit_code=0, stdout="", stderr="", wall_seconds=0.0
        )


def _outcome(report: Report, check: str) -> Result:
    return next(result for result in report.results if result.check == check)


# ======================================================== the real adapters pass


@pytest.mark.parametrize("adapter", [FlaskAdapter(app="app:app"), DjangoAdapter()])
def test_a_real_adapter_conforms(adapter: FrameworkAdapter, subject: Subject) -> None:
    """Neither shipped adapter fails a check it is given the inputs for."""
    report = run_conformance(Inputs(adapter=adapter, subject=subject))

    assert report.conforms, report.describe()


def test_a_run_without_inputs_conforms_and_attests_almost_nothing(subject: Subject) -> None:
    """The distinction the whole report is arranged around.

    `conforms` is true here and means very little: no session, no database and no
    workload were supplied, so the measurement, patch, reset and overhead checks
    never ran. Reading `conforms` alone is how a suite comes to certify an
    adapter nobody tested.
    """
    report = run_conformance(Inputs(adapter=FlaskAdapter(app="app:app"), subject=subject))

    assert report.conforms
    assert not report.attested
    assert {result.check for result in report.skips} >= {
        "the measurement is self-consistent",
        "a protected path is refused",
        "a reset restores state",
        "the query counter is cheap enough",
    }


def test_the_report_says_a_skip_is_not_a_pass(subject: Subject) -> None:
    """In words, in the rendered report, because that is what a human reads."""
    rendered = run_conformance(
        Inputs(adapter=FlaskAdapter(app="app:app"), subject=subject)
    ).describe()

    assert "A skipped check is not a passed one" in rendered
    assert "skipped" in rendered.splitlines()[0]


def test_a_session_moves_three_checks_from_skipped_to_passed(
    subject: Subject, session: CandidateSession
) -> None:
    """Supplying an input has to change the report, or the input is decoration."""
    without = run_conformance(Inputs(adapter=FlaskAdapter(app="app:app"), subject=subject))
    with_session = run_conformance(
        Inputs(adapter=FlaskAdapter(app="app:app"), subject=subject, session=session)
    )

    for check in (
        "the test suite can be run",
        "source is readable and worktree-relative",
        "a protected path is refused",
    ):
        assert _outcome(without, check).outcome is Outcome.SKIPPED
        assert _outcome(with_session, check).outcome is Outcome.PASSED


def test_the_measurement_check_passes_against_a_real_drive(subject: Subject) -> None:
    """The self-consistency checks, run against an adapter that actually drives.

    Marked slow nowhere else in this file: this is the one check here that starts
    an interpreter.
    """
    report = run_conformance(
        Inputs(
            adapter=FlaskAdapter(app="app:app"),
            subject=subject,
            entry_point="/tickets",
            repeats=3,
            scale=7,
            created={"ticket": 7},
            timeout=180.0,
        )
    )

    measured = _outcome(report, "the measurement is self-consistent")
    assert measured.outcome is Outcome.PASSED, measured.detail


# ============================================== the broken adapters are caught

# Each fake below implements the whole Protocol so that mypy checks it, and
# breaks exactly one requirement. The bodies that are not under test raise,
# because a check that reached them would be a check testing the wrong thing.


@dataclass(frozen=True)
class _Base:
    """A conforming adapter with nothing behind it, to subclass and break."""

    @property
    def framework(self) -> Framework:
        return Framework.FLASK

    @property
    def declarations(self) -> Declarations:
        return Declarations(
            orm=Orm.SQLALCHEMY,
            hooks={DB_QUERY: _nothing_hook()},
            internal_frames=("sqlalchemy/",),
            protected_paths=(),
        )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.EVENT_COUNTERS})

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        raise NotImplementedError

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        raise NotImplementedError

    def run_workload(  # noqa: PLR0913 - the Protocol's shape
        self,
        subject: Subject,
        *,
        entry_point: str,
        scale: int,
        created: Mapping[str, int],
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        repeats: int,
        timeout: float,
    ) -> Drive:
        raise NotImplementedError

    def run_tests(
        self, session: Session, *, selection: Sequence[str] = (), timeout: float
    ) -> ExecutionResult:
        raise NotImplementedError

    def read_source(self, session: CandidateSession) -> Mapping[str, str]:
        raise NotImplementedError

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        raise NotImplementedError

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        return ()


def _nothing_hook() -> Hook:
    """A hook that instruments nothing. Enough for the declaration checks, which
    never enter it — registration does not call a hook."""

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        yield

    return install


class _NarrowDeclarations(Declarations):
    """A `Declarations` subclass whose policy replaces the defaults.

    **The only way an adapter can narrow them**, and it is worth knowing that it
    exists: `Declarations.patch_policy` concatenates onto
    `DEFAULT_PROTECTED_PATTERNS`, so an adapter using it *cannot* drop a rule —
    but a frozen dataclass is still subclassable and the Protocol accepts any
    `Declarations`. An implementer who wanted different defaults would land here.
    """

    def patch_policy(self) -> PatchPolicy:
        return PatchPolicy(protected=("**/alembic/**",))


@dataclass(frozen=True)
class _NarrowsProtectedPaths(_Base):
    @property
    def declarations(self) -> Declarations:
        base = super().declarations
        return _NarrowDeclarations(
            orm=base.orm,
            hooks=base.hooks,
            internal_frames=base.internal_frames,
            protected_paths=base.protected_paths,
        )


@dataclass(frozen=True)
class _OverclaimsCapabilities(_Base):
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.EVENT_COUNTERS, Capability.DIAGNOSTIC_WORKTREE})


@dataclass(frozen=True)
class _DeclaresNoFrames(_Base):
    @property
    def declarations(self) -> Declarations:
        return replace(super().declarations, internal_frames=())


@dataclass(frozen=True)
class _FabricatesSamples(_Base):
    """Drives once and reports the reading several times."""

    def run_workload(  # noqa: PLR0913 - the Protocol's shape
        self,
        subject: Subject,
        *,
        entry_point: str,
        scale: int,
        created: Mapping[str, int],
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        repeats: int,
        timeout: float,
    ) -> Drive:
        return Drive(
            scale=scale,
            queries=3,
            response_bytes=100,
            seconds=0.01,
            samples=(0.01,),
            warmup_seconds=0.02,
            status=200,
            created=dict(created),
        )


@dataclass(frozen=True)
class _FabricatesTheMedian(_Base):
    """Reports a median it did not compute from the samples it returned."""

    def run_workload(  # noqa: PLR0913 - the Protocol's shape
        self,
        subject: Subject,
        *,
        entry_point: str,
        scale: int,
        created: Mapping[str, int],
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        repeats: int,
        timeout: float,
    ) -> Drive:
        samples = tuple(0.01 * (index + 1) for index in range(repeats))
        return Drive(
            scale=scale,
            queries=3,
            response_bytes=100,
            seconds=0.001,
            samples=samples,
            warmup_seconds=0.02,
            status=200,
            created=dict(created),
        )


@dataclass(frozen=True)
class _WritesWithoutTheFilter(_Base):
    """Reports a write it made without asking the session."""

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        return frozenset({PROTECTED_PROBE})


class TestBrokenAdaptersAreCaught:
    def test_narrowing_the_protected_paths_fails(self, subject: Subject) -> None:
        result = check_protected_paths(Inputs(adapter=_NarrowsProtectedPaths(), subject=subject))

        assert result.outcome is Outcome.FAILED
        assert "**/tests/**" in result.detail

    def test_claiming_a_harness_capability_fails(self, subject: Subject) -> None:
        result = check_capabilities(Inputs(adapter=_OverclaimsCapabilities(), subject=subject))

        assert result.outcome is Outcome.FAILED
        assert "diagnostic worktree" in result.detail

    def test_declaring_no_internal_frames_fails(self, subject: Subject) -> None:
        result = check_internal_frames(Inputs(adapter=_DeclaresNoFrames(), subject=subject))
        assert result.outcome is Outcome.FAILED

    def test_reporting_one_sample_as_several_fails(self, subject: Subject) -> None:
        result = check_measurement(
            Inputs(adapter=_FabricatesSamples(), subject=subject, entry_point="/x", repeats=5)
        )

        assert result.outcome is Outcome.FAILED
        assert "5 repeat(s) were requested and 1 sample(s)" in result.detail

    def test_a_median_that_is_not_the_median_fails(self, subject: Subject) -> None:
        """The check nothing else in the system would catch.

        `Drive.seconds` is what screening fits a growth curve to. An adapter
        reporting a number it did not compute from its own samples produces a
        finding whose measurement never happened, and every schema in the system
        accepts it.
        """
        result = check_measurement(
            Inputs(adapter=_FabricatesTheMedian(), subject=subject, entry_point="/x", repeats=3)
        )

        assert result.outcome is Outcome.FAILED
        assert "is not the median of the samples" in result.detail

    def test_applying_a_protected_patch_fails(
        self, subject: Subject, session: CandidateSession
    ) -> None:
        """The safety check, against the mistake it exists for.

        This adapter never consults the filter, and every other check in the
        suite passes for it.
        """
        result = check_patch_refusal(
            Inputs(adapter=_WritesWithoutTheFilter(), subject=subject, session=session)
        )

        assert result.outcome is Outcome.FAILED
        assert "protected-path filter was not consulted" in result.detail


# ================================================================ hook overhead

# The workload has to *raise events*, or no hook can cost anything per event and
# the check measures two identical timings. So it calls an instrumented method,
# which is the shape every real adapter's hook has: the Django one wraps a
# cursor's `execute`, the SQLAlchemy one listens for it.


class _Probe:
    """Something to instrument. Stands in for a cursor."""

    def tick(self) -> int:
        return 1


def _cheap_hook() -> Hook:
    """`calls_to` is the real constructor an adapter reaches for. Half a
    microsecond per event, measured by S-1.3."""
    return calls_to(_Probe, "tick")


def _slow_hook(cost_seconds: float) -> Hook:
    """A hook that burns time per event.

    This is what a `Path.resolve()` on the counting path looked like when S-1.3
    found one costing 590 microseconds per event — a real defect, in this
    repository, of exactly the class the tool exists to find in other people's
    code.
    """

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        original = _Probe.tick

        def slow(self: _Probe) -> int:
            _burn(cost_seconds)
            record()
            return original(self)

        _Probe.tick = slow  # type: ignore[method-assign]
        try:
            yield
        finally:
            _Probe.tick = original  # type: ignore[method-assign]

    return install


def _burn(seconds: float) -> None:
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        pass


EVENTS = 500


def _raises_events() -> None:
    probe = _Probe()
    for _ in range(EVENTS):
        probe.tick()


@dataclass(frozen=True)
class _CheapHook(_Base):
    @property
    def declarations(self) -> Declarations:
        return replace(super().declarations, hooks={DB_QUERY: _cheap_hook()})


@dataclass(frozen=True)
class _ExpensiveHook(_Base):
    @property
    def declarations(self) -> Declarations:
        # A tenth of the reference operation per event, which is twice the budget
        # and small enough that the test is not slow.
        return replace(
            super().declarations,
            hooks={DB_QUERY: _slow_hook(REFERENCE_OPERATION_SECONDS / 10)},
        )


@pytest.mark.timing
def test_a_cheap_hook_is_inside_the_budget(subject: Subject) -> None:
    """Real clock: the check is a comparison of two timings, so it is marked."""
    result = check_hook_overhead(
        Inputs(adapter=_CheapHook(), subject=subject, events=_raises_events, event_count=EVENTS)
    )

    assert result.outcome is Outcome.PASSED, result.detail


@pytest.mark.timing
def test_an_expensive_hook_is_caught(subject: Subject) -> None:
    """Without this the check could return `PASSED` unconditionally.

    An instrument that changes what it observes produces integers that are just
    as confident and no longer about the program, which is `bench/counting.py`'s
    opening argument and the reason this requirement exists at all.
    """
    result = check_hook_overhead(
        Inputs(adapter=_ExpensiveHook(), subject=subject, events=_raises_events, event_count=EVENTS)
    )

    assert result.outcome is Outcome.FAILED, result.detail
    assert "per event" in result.detail


def test_the_overhead_check_skips_without_a_workload(subject: Subject) -> None:
    """And a skip is not a pass, which is the whole reason the third outcome exists."""
    result = check_hook_overhead(Inputs(adapter=_CheapHook(), subject=subject))

    assert result.outcome is Outcome.SKIPPED
    assert "no event-raising workload" in result.detail


# ================================================================== the catalogue


def test_every_check_is_in_the_suite() -> None:
    """A check written and not registered is a requirement nobody runs.

    The list is what `run_conformance` walks, and adding a function without
    adding it there is the quiet failure — the suite gets greener rather than
    stricter.
    """
    module_checks = {
        check_capabilities,
        check_hook_overhead,
        check_internal_frames,
        check_measurement,
        check_patch_refusal,
        check_protected_paths,
    }

    assert module_checks <= set(CHECKS)


def test_the_report_is_ordered_the_same_way_every_run(subject: Subject) -> None:
    """Two reports of one adapter have to be comparable."""
    adapter = FlaskAdapter(app="app:app")
    first = run_conformance(Inputs(adapter=adapter, subject=subject))
    second = run_conformance(Inputs(adapter=adapter, subject=subject))

    assert [r.check for r in first.results] == [r.check for r in second.results]

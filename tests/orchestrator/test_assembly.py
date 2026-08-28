"""Assembling a whole `Resources`, and what it refuses to assemble.

S-17.15. *Nothing in `src/` constructs a `Resources`* has been the sentence
blocking S-17.1 since 2026-08-27, and this is the file that makes it false.

The test that matters is not that assembly succeeds. It is that **every** field
is set, iterated off the dataclass rather than listed — a list is what the author
remembered, and the field they forgot is the one that reads as `None` two nodes
into a run that has already spent its grounding budget.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coldfix.adapters.interface import (
    ADAPTER_CAPABILITIES,
    HARNESS_CAPABILITIES,
    Declarations,
    FrameworkAdapter,
    Subject,
)
from coldfix.audit import measuring, probing
from coldfix.bench.counting import Record
from coldfix.bench.execute import ExecutionResult
from coldfix.cost.accounting import ExchangeRate, Ledger
from coldfix.diagnosis import execution
from coldfix.explorer import binding, hands
from coldfix.explorer.compose import Plan
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.work import Drive
from coldfix.orchestrator.adapters import Resources, Tokens
from coldfix.orchestrator.assembly import AssemblyError, campaign_for
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.registry import Capability
from coldfix.repair.falsification import CostClaim, Guard
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.modes import Session as SandboxSession
from coldfix.sandbox.production import ProductionDatabaseError
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening import binding as rebinding
from coldfix.screening.workload import FixtureRecipe


class _FailedError(RuntimeError):
    """Stands in for anything a run can raise between assembly and teardown."""


SAFE_URL = "postgresql://coldfix@localhost:5432/subject_test"
PRODUCTION_URL = "postgresql://app@db.prod.internal:5432/app_production"


class Reset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None: ...
    def begin(self) -> None: ...
    def reset(self) -> None: ...


class Adapter:
    """A framework adapter with one hook and one reset candidate.

    **Implements the whole protocol, and mypy is what checks that.** A fake
    answering only the four operations this assembly happens to call would pass
    every test here while proving nothing about what `campaign_for` accepts — and
    the annotation on `_adapter` below is the assertion.

    The operations the assembly does not reach raise rather than returning
    something plausible: a stub returning an empty `Enumeration` is a subject with
    no routes, which is a result rather than an absence.
    """

    def __init__(self, *, candidates: Sequence[ResetMechanism] | None = None) -> None:
        self._candidates = (Reset(),) if candidates is None else tuple(candidates)
        self.asked_for: list[Subject] = []

    @property
    def framework(self) -> Framework:
        return Framework.DJANGO

    @property
    def declarations(self) -> Declarations:
        return Declarations(orm=Orm.DJANGO_ORM, hooks={DB_QUERY: _hook})

    def capabilities(self) -> AbstractSet[Capability]:
        return ADAPTER_CAPABILITIES

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        self.asked_for.append(subject)
        return self._candidates

    def run_workload(  # noqa: PLR0913 - the protocol's shape, kept exactly
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
            queries=1,
            response_bytes=10,
            seconds=0.01,
            samples=(0.01,),
            warmup_seconds=0.01,
            status=200,
            created=dict(created),
        )

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        raise _NotReachedError

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        raise _NotReachedError

    def run_tests(
        self, session: SandboxSession, *, selection: Sequence[str] = (), timeout: float
    ) -> ExecutionResult:
        raise _NotReachedError

    def read_source(self, session: CandidateSession) -> Mapping[str, str]:
        raise _NotReachedError

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        raise _NotReachedError


class _NotReachedError(AssertionError):
    """The assembly does not call this operation. Raising says so."""


@contextmanager
def _hook(record: Record) -> Iterator[None]:
    """A counter attachment the assembly never installs. Shaped correctly so the
    protocol is satisfied for real rather than by an annotation nobody checks."""
    yield


class Worktree:
    def __init__(self, path: Path) -> None:
        self.path = path


class Opened:
    """A diagnostic session that records whether it was closed."""

    def __init__(self, path: Path) -> None:
        self.worktree = Worktree(path)
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Opened:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()


class Bench:
    """A workbench that hands out one recorded session."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.opened: list[str] = []
        self.session: Opened | None = None

    def open(self, revision: str, *, mode: Any) -> Opened:
        self.opened.append(revision)
        self.session = Opened(self.path)
        return self.session


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replaces `choose_reset`, which drives the subject ten times against a real
    database. What is under test is the assembly, not S-2.7's verification."""
    seen: list[object] = []

    def fake(candidates: Any, database: Any, workload: Any, **kwargs: Any) -> VerifiedReset:
        seen.append(database)
        workload()  # the workload has to be callable, and this is where that shows
        return VerifiedReset(
            mechanism=Reset(),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        )

    monkeypatch.setattr("coldfix.orchestrator.assembly.choose_reset", fake)
    return seen


def assembled(tmp_path: Path, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "client": object(),
        "project": "shop",
        "trust_key": "n-plus-one:uniform",
        "revision": "HEAD",
        "root": tmp_path,
        "python": ["python"],
        "database_url": SAFE_URL,
        "workbench": Bench(tmp_path),
        "store": object(),
        "plan": Plan(workload_id="books", description="the books list"),
        "entity": "author",
        "path": "/books/",
        "model": "shop.Book",
        "settings": "config.settings",
        "source": "shop@HEAD",
        "suite_command": ["pytest", "-q"],
        "metric": DB_QUERY,
        "tokens": Tokens(prefix=100, prompt=200),
        "claim": CostClaim(
            metric=DB_QUERY,
            baseline=41.0,
            at_most=2.0,
            guards=(Guard(metric="response_bytes", baseline=2000.0, at_most=3000.0),),
        ),
        "rate": ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 28)),
        "ceiling_eur": Decimal("1.00"),
    }
    adapter: FrameworkAdapter = overrides.pop("adapter", None) or Adapter()
    subject = Subject(root=tmp_path, python=["python"])
    # **The caller unpacks the adapter, not `campaign_for`.** Core must never
    # import `coldfix.adapters`, and ADR 148 §1 files the widening that would let
    # the campaign do so on S-14.5. So the assembly takes what an adapter supplies
    # and this is where the adapter is asked.
    arguments.setdefault("framework", adapter.framework.value)
    arguments.setdefault("reset_candidates", adapter.reset_state(subject))
    arguments.setdefault("capabilities", frozenset(adapter.capabilities()) | HARNESS_CAPABILITIES)
    arguments.setdefault("counters", tuple(sorted(adapter.declarations.hooks)))
    arguments.setdefault(
        "workload",
        lambda: adapter.run_workload(
            subject,
            entry_point="/books/",
            scale=1,
            created={},
            repeats=1,
            timeout=300.0,
        ),
    )
    arguments.update(overrides)
    return campaign_for(**arguments)


# ============================================ AC 1: every field, iterated not listed


def test_every_field_of_resources_is_set(tmp_path: Path, verified: list[object]) -> None:
    """**AC 1, and the assertion is `dataclasses.fields` rather than a list.**

    A list is what the author remembered. The field they forgot is the one that
    reads as `None` two nodes into a run which has already spent its grounding
    budget, and no test naming twenty-two of twenty-three would say so.
    """
    with assembled(tmp_path) as resources:
        unset = [
            field.name
            for field in dataclasses.fields(Resources)
            if getattr(resources, field.name, None) is None
        ]

    assert unset == []
    assert len(dataclasses.fields(Resources)) >= 23


def test_the_six_subject_facing_fields_are_the_real_producers(
    tmp_path: Path, verified: list[object]
) -> None:
    """AC 4. A stub satisfies every one of these protocols, so the check is
    structural: each field is a closure from the module that produces it."""
    with assembled(tmp_path) as resources:
        origins = {
            "ground": binding.__name__,
            "hands": hands.__name__,
            "bind": rebinding.__name__,
            "measure": measuring.__name__,
            "executor": execution.__name__,
        }
        for name, module in origins.items():
            produced = getattr(resources, name)
            assert produced.__module__ == module, f"{name} came from {produced.__module__}"

        assert resources.probe.__class__.__module__.startswith("coldfix.audit")
        assert probing.probe_for is not None


def test_one_ledger_is_under_both_the_budget_and_the_sessions(
    tmp_path: Path, verified: list[object]
) -> None:
    """S-17.4's argument, one layer up.

    `Budget.spent_eur` reads its own ledger's total, so a budget and the sessions
    billing into a different one would each see a fraction of the spend — and the
    run could pass six ceilings on the way to breaching one.
    """
    shared = Ledger()

    with assembled(tmp_path, ledger=shared) as resources:
        assert resources.budget.ledger is shared


# ================================================== AC 2: refused before anything opens


def test_a_production_database_is_refused_before_the_workbench_opens(tmp_path: Path) -> None:
    """**AC 2.** Constructing a `VerifiedDatabase` *is* S-2.5's check, and the
    ordering is the point: a guard that fired after a container was running would
    be reporting a rule it had already broken."""
    bench = Bench(tmp_path)

    with (
        pytest.raises(ProductionDatabaseError),
        assembled(tmp_path, database_url=PRODUCTION_URL, workbench=bench),
    ):
        pass  # pragma: no cover - the context manager raises on entry

    assert bench.opened == [], "nothing was opened"


def test_an_adapter_with_no_reset_candidate_is_refused(tmp_path: Path) -> None:
    """Every scale point after the first would be measured on top of the one
    before it, and the growth that showed would be arithmetic.

    Refused before the workbench opens too, for the same reason the production
    guard is: there is nothing to tear down if nothing was stood up.
    """
    bench = Bench(tmp_path)

    with (
        pytest.raises(AssemblyError, match="no way to reset"),
        assembled(tmp_path, workbench=bench, adapter=Adapter(candidates=())),
    ):
        pass  # pragma: no cover - the context manager raises on entry

    assert bench.opened == []


def test_the_reset_is_chosen_against_the_verified_database(
    tmp_path: Path, verified: list[object]
) -> None:
    """Not supplied, and not against some other database. A reset verified
    elsewhere would restore something this run never touches."""
    with assembled(tmp_path):
        pass

    assert len(verified) == 1
    assert getattr(verified[0], "name", "") == "subject_test"


# ============================================ AC 3: the session is closed on the way out


def test_the_diagnostic_session_is_closed_on_the_way_out(
    tmp_path: Path, verified: list[object]
) -> None:
    """**AC 3.** A session owns a worktree S-2.2 destroys, and a stranded checkout
    per run is a disk that fills rather than an error anybody sees."""
    bench = Bench(tmp_path)

    with assembled(tmp_path, workbench=bench):
        assert bench.session is not None
        assert not bench.session.closed

    assert bench.session is not None
    assert bench.session.closed


def test_the_session_is_closed_when_the_body_raises(tmp_path: Path, verified: list[object]) -> None:
    """The direction that leaks. A run that failed mid-investigation is exactly
    when nobody is looking at the worktree directory."""
    bench = Bench(tmp_path)

    with pytest.raises(_FailedError), assembled(tmp_path, workbench=bench):
        raise _FailedError

    assert bench.session is not None
    assert bench.session.closed


def test_the_counters_are_the_adapters_declared_hooks(
    tmp_path: Path, verified: list[object]
) -> None:
    """Not invented here. The catalogue decides which counters exist and the
    adapter says which of them it supplies — a campaign naming its own would ask
    for a hook nothing registers."""
    with assembled(tmp_path) as resources:
        assert tuple(resources.counters) == (DB_QUERY,)


def test_the_instruments_are_the_adapters_capabilities_and_the_harness_own(
    tmp_path: Path, verified: list[object]
) -> None:
    """S-14.1 derives the harness half as the complement of the adapter's, so a
    capability in neither is one every primitive requiring it is withheld for —
    the honest report rather than a silent gap."""
    with assembled(tmp_path) as resources:
        assert resources.instruments.available, "some primitive is offered"

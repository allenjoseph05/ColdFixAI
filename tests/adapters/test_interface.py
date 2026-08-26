"""The framework boundary, and the two halves of proving an interface.

S-14.1. Half of what this story asserts is static and cannot be asserted at
runtime: *a Protocol with full typing* is a claim about what mypy rejects, and a
test that calls a method proves only that the method exists. So the conformance
half is written as annotated assignments and deliberate `type: ignore` comments,
which `mypy --strict` checks on every gate run — `warn_unused_ignores` is part of
strict, so an ignore that stops being necessary **fails the run**. That is what
makes `# type: ignore[arg-type]` on a diagnostic session an assertion rather
than a suppression: widen the parameter to `Session` and this file stops
type-checking.

The other half is behaviour, and it is the declarations rather than the
operations. An adapter's four declarations are only worth having if they reach
the core types that consume them, so those tests go through `matching_rule` and
through `localize` rather than reading the fields back.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from coldfix.adapters import (
    ADAPTER_CAPABILITIES,
    HARNESS_CAPABILITIES,
    ROW_COUNTING_VENDORS,
    Declarations,
    FrameworkAdapter,
    Subject,
    installed,
)
from coldfix.bench.counting import Hook, calls_to, count, registered_hooks
from coldfix.bench.execute import ExecutionResult
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.work import Drive
from coldfix.primitives.counters import (
    ALLOCATION,
    DB_BYTES,
    DB_QUERY,
    DB_ROWS,
    FILE_OPEN,
    CounterError,
    UnknownCounterError,
)
from coldfix.primitives.localization import Frame
from coldfix.primitives.registry import Capability
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession, Session
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS
from coldfix.sandbox.reset import ResetMechanism
from coldfix.screening.workload import FixtureRecipe

# The eight operations S-14.1 names, and the two declarations. Written out
# rather than derived from the class, because a test that reads the interface to
# decide what the interface should contain asserts nothing at all.
OPERATIONS = frozenset(
    {
        "discover_workloads",
        "seed",
        "run_workload",
        "run_tests",
        "read_source",
        "apply_patch",
        "reset_state",
        "capabilities",
    }
)

DECLARATIONS = frozenset({"framework", "declarations"})


class _Cursor:
    """Something to count calls to. Stands in for a database cursor."""

    def execute(self, statement: str) -> str:
        return statement


def _query_hook() -> Hook:
    return calls_to(_Cursor, "execute")


class _Adapter:
    """A conforming adapter, whose bodies are never the point.

    It exists so that `adapter: FrameworkAdapter = _Adapter()` below is checked
    by mypy: if the Protocol and this class disagree about a single parameter,
    the gate's type run fails. The bodies raise because what an adapter *does*
    is S-14.2, and a fake that returned plausible measurements here would be a
    fake asserting what it was written to believe.
    """

    def __init__(self, declarations: Declarations | None = None) -> None:
        self._declarations = declarations or Declarations(orm=Orm.DJANGO_ORM)

    @property
    def framework(self) -> Framework:
        return Framework.DJANGO

    @property
    def declarations(self) -> Declarations:
        return self._declarations

    def capabilities(self) -> AbstractSet[Capability]:
        return ADAPTER_CAPABILITIES

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        raise NotImplementedError

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        raise NotImplementedError

    def run_workload(  # noqa: PLR0913 - the Protocol's shape, for the Protocol's reason
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
        raise NotImplementedError


def _member(name: str) -> Any:
    """The function behind a Protocol member, unwrapping a property."""
    member = vars(FrameworkAdapter)[name]
    return member.fget if isinstance(member, property) else member


class TestTheInterface:
    """AC 1 and AC 3 — the eight operations, and what they are typed as."""

    def test_it_names_the_eight_operations_and_nothing_else(self) -> None:
        """AC 1. Equality rather than containment, in both directions.

        A missing operation is the obvious failure. A *ninth* is the one worth
        catching: the boundary is the whole of what the core may ask a framework
        for, so an operation added here without a story is a framework-specific
        call the core has quietly started to depend on.
        """
        public = {name for name in vars(FrameworkAdapter) if not name.startswith("_")}
        assert public == OPERATIONS | DECLARATIONS

    def test_every_parameter_and_return_is_annotated(self) -> None:
        """AC 3, first half. `get_type_hints` resolves, so a broken name fails."""
        for name in sorted(OPERATIONS | DECLARATIONS):
            function = _member(name)
            hints = get_type_hints(function)
            parameters = set(inspect.signature(function).parameters) - {"self"}
            assert not parameters - set(hints), f"{name} has unannotated parameters"
            assert "return" in hints, f"{name} has no return annotation"

    def test_no_operation_is_typed_as_any(self) -> None:
        """AC 3, second half, and CLAUDE.md's rule about `Any` without a reason.

        The repr is scanned rather than the top-level value compared, so that a
        `Mapping[str, Any]` — which is the way `Any` actually arrives in an
        interface — is caught as well as a bare one.
        """
        for name in sorted(OPERATIONS | DECLARATIONS):
            for where, hint in get_type_hints(_member(name)).items():
                assert "Any" not in repr(hint), f"{name}.{where} is typed as Any"

    def test_a_conforming_implementation_satisfies_the_protocol(self) -> None:
        """The static assertion. The annotation is the test; mypy runs it.

        Nothing here can fail at runtime, and that is the point being recorded:
        structural conformance is checked by the gate's type run, not by this
        assert. `isinstance` is deliberately not available — the Protocol is not
        `@runtime_checkable`, because that check passes for any object with the
        right eight names and would read as a stronger statement than it is.
        """
        adapter: FrameworkAdapter = _Adapter()
        assert adapter.framework is Framework.DJANGO

    def test_the_write_path_refuses_a_diagnostic_session(self) -> None:
        """S-2.3 survives the new seam, and the `type: ignore` is the assertion.

        `DiagnosticSession` is not a subtype of `CandidateSession`, so this call
        is a type error today. Widen `apply_patch`'s parameter to `Session` and
        the error disappears — at which point `warn_unused_ignores` fails the
        gate on the now-unnecessary ignore. **Do not delete the comment to make
        mypy quiet: it is the only thing asserting that an ablation run cannot
        be handed the writer.**
        """
        adapter: FrameworkAdapter = _Adapter()
        diagnostic = object.__new__(DiagnosticSession)
        with pytest.raises(NotImplementedError):
            adapter.apply_patch(diagnostic, "--- a/x.py\n")  # type: ignore[arg-type]

    def test_the_reader_refuses_a_diagnostic_session(self) -> None:
        """The same rule for `read_source`, and it is not redundant.

        `CandidateSession.sources` exists on that class and not on `Session`
        precisely because a diagnostic session that could read a file back could
        emit a diff to disk and hand it out. A reader on the adapter that took
        either session would restore the route the class layout removed.
        """
        adapter: FrameworkAdapter = _Adapter()
        diagnostic = object.__new__(DiagnosticSession)
        with pytest.raises(NotImplementedError):
            adapter.read_source(diagnostic)  # type: ignore[arg-type]


class TestProtectedPaths:
    """An adapter may add to the defaults. It may not take one away."""

    def test_the_defaults_survive_an_adapter_that_declares_nothing(self) -> None:
        policy = Declarations(orm=Orm.DJANGO_ORM).patch_policy()
        assert policy.matching_rule("app/tests/test_views.py") is not None
        assert policy.matching_rule("conftest.py") is not None

    def test_an_adapter_cannot_narrow_the_defaults(self) -> None:
        """The safety property, stated as the attack.

        An adapter declaring one pattern is the natural way to write one, and
        the natural implementation — take the declaration as the policy — would
        drop every default silently. What would show up is a patch that edits
        the test suite and applies cleanly.
        """
        narrow = Declarations(orm=Orm.SQLALCHEMY, protected_paths=("**/migrations/**",))
        policy = narrow.patch_policy()

        assert policy.matching_rule("app/tests/test_views.py") is not None
        assert policy.matching_rule("app/factories.py") is not None
        for default in DEFAULT_PROTECTED_PATTERNS:
            assert default in policy.protected

    def test_a_declared_pattern_protects_a_file_the_defaults_do_not(self) -> None:
        """And the addition is not decoration: it changes a decision."""
        bare = Declarations(orm=Orm.DJANGO_ORM).patch_policy()
        assert bare.matching_rule("shop/migrations/0002_add_index.py") is None

        declared = Declarations(
            orm=Orm.DJANGO_ORM, protected_paths=("**/migrations/**",)
        ).patch_policy()
        assert declared.matching_rule("shop/migrations/0002_add_index.py") == "**/migrations/**"

    def test_a_pattern_the_defaults_already_hold_is_not_repeated(self) -> None:
        """A rejection names one rule, and a duplicated rule is two answers."""
        policy = Declarations(
            orm=Orm.DJANGO_ORM,
            protected_paths=("**/conftest.py", "**/migrations/**", "**/migrations/**"),
        ).patch_policy()

        assert policy.protected.count("**/conftest.py") == 1
        assert policy.protected.count("**/migrations/**") == 1
        assert policy.protected[: len(DEFAULT_PROTECTED_PATTERNS)] == DEFAULT_PROTECTED_PATTERNS


class TestInternalFrames:
    """The deny list has to reach `localize`, not merely be stored."""

    def _stack(self) -> tuple[Frame, ...]:
        return (
            Frame(filename="/env/site-packages/django/db/models/query.py", lineno=10, function="_"),
            Frame(filename="/app/shop/views.py", lineno=42, function="list_orders"),
            Frame(filename="/env/site-packages/django/core/handlers.py", lineno=7, function="run"),
        )

    def test_the_declaration_moves_the_causal_site_into_the_subject(self) -> None:
        """The discriminating assertion: without the declaration, the site is the ORM.

        A test that read `localizer().deny` back would pass for a localizer that
        never used it. This one asserts which line a reader would be sent to.
        """
        declared = Declarations(orm=Orm.DJANGO_ORM, internal_frames=("django/",))
        site = declared.localizer().localize([self._stack()]).causal_site
        assert site is not None
        assert site.filename.endswith("shop/views.py")

        undeclared = Declarations(orm=Orm.DJANGO_ORM).localizer().localize([self._stack()])
        assert undeclared.causal_site is not None
        assert undeclared.causal_site.filename.endswith("django/db/models/query.py")

    def test_the_root_and_resolver_stay_the_harness_choice(self) -> None:
        """An adapter declares which frames are the framework's, and nothing else.

        Where the source is read from is a fact about this run's checkout, so it
        is passed at the call rather than declared once by the adapter.
        """
        declared = Declarations(orm=Orm.DJANGO_ORM, internal_frames=("django/",))
        localizer = declared.localizer(root=Path("/somewhere"))
        assert localizer.root == Path("/somewhere")
        assert localizer.deny == ("django/",)


class TestHookInstallation:
    """Hook points, registered under the catalogue's names and removed after."""

    def test_a_declared_hook_becomes_a_countable_counter(self) -> None:
        declarations = Declarations(orm=Orm.DJANGO_ORM, hooks={DB_QUERY: _query_hook()})
        cursor = _Cursor()

        with installed(declarations), count(DB_QUERY) as tally:
            cursor.execute("SELECT 1")
            cursor.execute("SELECT 2")

        assert tally.events == 2

    def test_the_registry_is_clean_afterwards(self) -> None:
        declarations = Declarations(orm=Orm.DJANGO_ORM, hooks={DB_QUERY: _query_hook()})
        with installed(declarations):
            assert DB_QUERY in registered_hooks()
        assert DB_QUERY not in registered_hooks()

    def test_a_refused_hook_leaves_nothing_behind(self) -> None:
        """A partial registration is unwound before the error leaves.

        Without this, a declaration that is rejected poisons the registry it
        failed to enter: the first run reports the real problem and every run
        after it reports a duplicate registration instead.
        """
        declarations = Declarations(
            orm=Orm.DJANGO_ORM,
            hooks={DB_QUERY: _query_hook(), DB_ROWS: _query_hook()},
        )
        with pytest.raises(CounterError), installed(declarations):
            pytest.fail("a reading is not a hook and should have been refused")

        assert DB_QUERY not in registered_hooks()

    def test_a_name_outside_the_catalogue_is_refused(self) -> None:
        """`db.queries` for `db.query` is the typo the catalogue exists to catch."""
        declarations = Declarations(orm=Orm.DJANGO_ORM, hooks={"db.queries": _query_hook()})
        with pytest.raises(UnknownCounterError), installed(declarations):
            pass

    def test_a_framework_free_counter_is_not_an_adapters_to_supply(self) -> None:
        """Two of the catalogue's counters need no framework, and this installs them.

        An adapter registering one would be a second answer to a question that
        already has one — ADR 013's rule, and the reason `adapter_supplied` is a
        field rather than a convention.
        """
        for name in (FILE_OPEN, ALLOCATION):
            declarations = Declarations(orm=Orm.DJANGO_ORM, hooks={name: _query_hook()})
            with pytest.raises(CounterError), installed(declarations):
                pass

    def test_several_hooks_install_together(self) -> None:
        declarations = Declarations(
            orm=Orm.DJANGO_ORM,
            hooks={DB_QUERY: _query_hook(), DB_BYTES: _query_hook()},
        )
        with installed(declarations):
            assert {DB_QUERY, DB_BYTES} <= set(registered_hooks())
        assert not {DB_QUERY, DB_BYTES} & set(registered_hooks())


class TestCapabilities:
    """Which half of the environment an adapter answers for."""

    def test_the_two_sets_partition_the_enum(self) -> None:
        """A thirteenth capability has to be classified, not merely added.

        Deriving the harness half as the complement is what makes this hold by
        construction; the test is here so that replacing the derivation with a
        second hand-written list fails immediately.
        """
        assert frozenset(Capability) == ADAPTER_CAPABILITIES | HARNESS_CAPABILITIES
        assert not ADAPTER_CAPABILITIES & HARNESS_CAPABILITIES

    def test_the_harness_keeps_the_capabilities_no_framework_supplies(self) -> None:
        """The four that matter, named rather than counted.

        A diagnostic worktree and an input mutation engine are this system's
        regardless of what the subject is written in. An adapter claiming one
        would be claiming a capability whose implementation it has never seen,
        and a primitive would be offered on the strength of it.
        """
        assert Capability.DIAGNOSTIC_WORKTREE in HARNESS_CAPABILITIES
        assert Capability.INPUT_MUTATION in HARNESS_CAPABILITIES
        assert Capability.LOAD_GENERATION in HARNESS_CAPABILITIES
        assert Capability.OFF_CPU_TIMING in HARNESS_CAPABILITIES

    def test_the_measured_row_counting_vendors_are_short_and_are_evidence(self) -> None:
        """Shared by both adapters, because it is a fact about the database.

        SQLAlchemy over Postgres and the Django ORM over Postgres ask the same
        driver the same question. The list is what has been measured (ADR 147),
        and a vendor absent from it is refused rather than assumed either way.
        """
        assert frozenset({"postgresql"}) == ROW_COUNTING_VENDORS

    def test_the_adapter_keeps_the_ones_only_a_framework_can_answer(self) -> None:
        assert Capability.EVENT_COUNTERS in ADAPTER_CAPABILITIES
        assert Capability.STATE_RESET in ADAPTER_CAPABILITIES
        assert Capability.FIXTURE_SEEDING in ADAPTER_CAPABILITIES


class TestSubject:
    """The two facts that travel together, held so they cannot disagree."""

    def test_the_interpreter_is_frozen_into_a_tuple(self) -> None:
        """A caller that keeps mutating its list cannot change a subject already made.

        `ProjectProfile` copies for the same reason: a value that decides what
        runs must not be editable by whoever handed it over.
        """
        command = ["python", "-X", "utf8"]
        subject = Subject(root=Path("/repo"), python=command)
        command.append("-v")
        assert subject.python == ("python", "-X", "utf8")

    def test_a_string_root_becomes_a_path(self) -> None:
        subject = Subject(root=Path("/repo"), python=["python"])
        assert isinstance(subject.root, Path)

    def test_it_is_hashable(self) -> None:
        """Frozen and hashable, so a subject can key a cache without being copied."""
        subject = Subject(root=Path("/repo"), python=["python"])
        assert {subject: "seen"}[Subject(root=Path("/repo"), python=["python"])] == "seen"


class TestDeclarations:
    """The declaration object itself: copied on the way in, ORM required."""

    def test_the_hooks_mapping_is_copied(self) -> None:
        hooks: dict[str, Hook] = {DB_QUERY: _query_hook()}
        declarations = Declarations(orm=Orm.DJANGO_ORM, hooks=hooks)
        hooks[DB_BYTES] = _query_hook()
        assert set(declarations.hooks) == {DB_QUERY}

    def test_the_orm_has_no_default(self) -> None:
        """The dialect a reset, a query hook and a row counter are all written
        against. A default would put a guess where a fact belongs."""
        with pytest.raises(TypeError):
            Declarations()  # type: ignore[call-arg]

    def test_sequences_are_frozen(self) -> None:
        frames = ["django/"]
        declarations = Declarations(orm=Orm.DJANGO_ORM, internal_frames=frames)  # type: ignore[arg-type]
        frames.append("rest_framework/")
        assert declarations.internal_frames == ("django/",)

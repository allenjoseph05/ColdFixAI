"""Django, DRF and Postgres. The first adapter, and mostly a transcription.

Epic 14, S-14.2. Seven of the eight operations already existed as Django-specific
code spread across `explorer/`, `sandbox/` and `screening/`; this is where they
stop being spread out. The table in `interface.py` says which function each one
wraps, and the wrapping is deliberately thin — an adapter that reimplemented
`drive` would be a second place the measurement is taken.

**The eighth operation is new, and it is the query hook.** Nothing registered a
Django counter anywhere in the system before this module.

**Nothing here imports Django at module level.** `pyproject.toml` keeps Django in
the dev group on the grounds that *nothing under `src/` imports it*, and a wheel
installed without Django must still import `coldfix`. Every Django import is
inside the function that needs it, which is checked by a test that imports this
module in a fresh interpreter and asserts `django` did not arrive with it.

## Why `execute_wrapper` and not the mechanism `drive` already uses

There are two counting paths in this system and they are not competitors.

`explorer.work.drive` counts with `CaptureQueriesContext` **inside the subject's
own interpreter**, which is ADR 008's `force_debug_cursor` and is correct there:
it needs a total, once, from a process this one cannot reach into.

This hook counts **in this process**, which is what `bench.counting.count` and
`primitives.measurement.measure_once` do, and it is the only one of the two that
can produce **per-event stacks**. `connection.queries` is a list read after the
fact; a stack has to be walked at the moment the query is raised, from inside the
call. S-3.9 localizes a finding by walking those stacks to their divergence
point, so without a callback-shaped hook there is no localization on Django at
all. `execute_wrapper` is a callback. That is the whole argument.

It also avoids what ADR 008 records as the cost of the other path: the debug
cursor accumulates full query text for the life of the connection.

## The amount is rows, and rows are not always knowable

`counters.CATALOGUE` defines `db.query`'s amount as *rows returned by that
statement*, because `db.rows` is the same attachment read as a total — the
project's guard-counter rule needs both numbers from one run, since queries
falling while rows explode is only visible if both were counted at once.

`execute_wrapper` can read `cursor.rowcount` after the statement, and **whether
that means anything depends on the backend**. Measured in this story, against
both:

| Statement | PostgreSQL | SQLite |
|---|---|---|
| `SELECT` returning three rows | `3` | `-1` |
| `SELECT` matching nothing | `0` | `-1` |
| `INSERT` of three rows | `3` | `3` |
| `CREATE TABLE` | `-1` | `-1` |

So on Postgres the two cases are distinguishable — `0` is a real empty result and
`-1` is *this statement has no row count* — and on SQLite every `SELECT` is
indistinguishable from a statement that returned nothing.

**A backend that cannot report rows is refused rather than recorded as zero.**
Recording zero would make `db.rows` read flat while rows grew, which is the guard
counter failing in the one direction the project calls out by name. Refusing
costs a SQLite project this hook and leaves `drive`'s count untouched, because
that path does not depend on `rowcount` at all. Missing is the recoverable one.

`ROW_COUNTING_VENDORS` is therefore a *measured* list rather than a guess, and a
vendor absent from it is refused rather than assumed either way.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coldfix.adapters.interface import ROW_COUNTING_VENDORS, Declarations, Subject
from coldfix.bench.counting import Hook, HookError, Record
from coldfix.bench.execute import ExecutionResult
from coldfix.explorer.entrypoints import Enumeration, Kind, enumerate_entry_points
from coldfix.explorer.fingerprint import Framework, Orm, TestRunner, declared_test_runner
from coldfix.explorer.registry import Grounds, register
from coldfix.explorer.stages import (
    FRAMEWORK_NEUTRAL_PREDICATES,
    Grounding,
    Outcome,
    Predicate,
    Stage,
    Verdict,
)
from coldfix.explorer.synthesis import SYNTHESIS_TIMEOUT_SECONDS, synthesize
from coldfix.explorer.work import Drive, Seeder, drive
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.registry import Capability
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.modes import CandidateSession, Session
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetMechanism, RollbackReset, SnapshotRestoreReset
from coldfix.screening.workload import FixtureRecipe

DJANGO_INTERNAL_FRAMES: tuple[str, ...] = (
    # The framework itself, and the two paths a stack through the ORM spends most
    # of its frames in.
    "django/db/",
    "django/core/",
    "django/template/",
    "django/utils/",
    "django/views/generic/",
    # DRF. A serializer's own frames are the framework's, not the subject's — a
    # localization that stops inside `rest_framework/serializers.py` names a line
    # nobody investigating their own project can change.
    "rest_framework/",
    # Everything installed rather than written. Keeping these would make the
    # deepest frame of nearly every stack belong to a dependency.
    "site-packages",
    "dist-packages",
)
"""Path fragments belonging to the framework rather than to the subject.

Fragments, not globs, and matched against the normalized path, so nothing here
has to know which separator the host uses. `django/` alone would be enough for
most stacks and is deliberately not used: a subject with an app *called* django
is unlikely, and a subject with a directory called `django` in its own tree is
not, so the entries name the framework's own subpackages.
"""

SUITE_COMMANDS: Mapping[TestRunner, tuple[str, ...]] = {
    TestRunner.PYTEST: ("-m", "pytest"),
    TestRunner.DJANGO: ("manage.py", "test"),
    TestRunner.UNITTEST: ("-m", "unittest"),
}
"""How each runner is invoked, after the interpreter and before any selection.

The one part of choosing a test command that is knowledge about the framework
rather than about the repository. Which runner a repository *uses* is S-7.1's
question and is answered by `declared_test_runner`.
"""

DJANGO_PROTECTED_PATHS: tuple[str, ...] = (
    # A migration is generated from a model change and applied once. Editing one
    # does not change what the application does at runtime, and editing an
    # *applied* one silently desynchronizes every database that already ran it.
    "**/migrations/**",
    # Collected and compiled assets. Generated output, not source, and a
    # performance patch that edits them is editing a build product.
    "**/static/**",
    "**/staticfiles/**",
)
"""Added to `DEFAULT_PROTECTED_PATTERNS`, never substituted for them.

Deliberately not here: settings modules. Substitution — swapping a configuration
value and re-measuring — is `01-primitives.md`'s safest primitive, and protecting
settings would refuse the class of fix most likely to be correct.
"""


def query_hook(*, aliases: Sequence[str] | None = None) -> Hook:
    """Count statements through Django's own `execute_wrapper`.

    One event per statement, and the amount is the rows that statement returned,
    so `db.query` and `db.rows` come from a single attachment.

    **Every connection Django knows about is wrapped by default**, because
    ADR 008 records that a target using more than one database alias needs the
    instrument on *each connection under measurement* — and an alias left
    uninstrumented is a silent undercount, which is a measurement that looks like
    a finding. Removal is Django's own context manager under an `ExitStack`, so a
    workload that raises still leaves the connections as it found them; ADR 008
    asks for that property to be tested adversarially, and it is.

    **`aliases` narrows that to the connections actually under measurement**,
    which is ADR 008's own phrase and the case its consequences section describes.
    A project with a read replica or an analytics database its workload never
    touches would otherwise be un-instrumentable the moment one of them is on a
    backend that cannot report rows. Narrowing is the caller's decision and is
    stated, never inferred: an alias nobody names is one nobody decided about.

    **A connection opened after the block begins is not wrapped.** Django's
    connections are thread-local, so a request served on another thread gets a
    different connection object with no wrapper on it, and the undercount is
    silent. This is the same limitation `calls_to` documents for the same reason:
    it is a property of instrumenting an object that already exists, not a defect
    that can be fixed from here. The in-process measurement path drives the
    workload on the calling thread.

    Raises:
        HookError: a connection's backend does not report rows per statement, so
            the amount would be a fiction; or a named alias that Django has no
            connection for. Both are raised before anything is installed.
    """

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        # Imported here rather than at module scope: `coldfix` must import on a
        # machine with no Django, and this is the only function that needs one.
        from django.db import connections  # noqa: PLC0415 - see the module docstring:
        # `coldfix` must import on a machine with no Django, so this is the one place
        # a Django import may live and it is inside the function that needs it.

        def wrapper(
            execute: Any,  # noqa: ANN401 - Django's execute_wrapper API is untyped,
            # and inventing signatures for someone else's callback is how a wrapper
            # comes to be wrong about what it is passed.
            sql: str,
            params: Any,  # noqa: ANN401 - a sequence, a mapping, or None, per backend
            many: bool,  # Django's parameter, in Django's order
            context: Any,  # noqa: ANN401 - a dict Django documents by key, not by type
        ) -> Any:  # noqa: ANN401 - whatever the backend's execute returns
            result = execute(sql, params, many, context)
            rows = getattr(context.get("cursor"), "rowcount", -1)
            # A negative rowcount is *no row count for this statement* — DDL, on
            # every backend measured. Zero is the honest reading there, and the
            # case where it would not be honest is refused below rather than
            # recorded here, because a per-statement decision cannot tell a real
            # empty result from a backend that never answers.
            record(float(rows) if rows >= 0 else 0.0)
            return result

        if aliases is None:
            wrapped = list(connections.all())
        else:
            known = set(connections)
            missing = sorted(set(aliases) - known)
            if missing:
                message = (
                    f"no database connection is configured under {missing}; Django knows "
                    f"{sorted(known)}. Refusing rather than counting the connections that "
                    "do exist, because a named alias that silently contributes nothing is "
                    "an undercount wearing the shape of a measurement"
                )
                raise HookError(message)
            wrapped = [connections[alias] for alias in aliases]

        for connection in wrapped:
            if connection.vendor not in ROW_COUNTING_VENDORS:
                message = (
                    f"the {connection.vendor!r} backend on connection "
                    f"{connection.alias!r} does not report rows per statement, so "
                    f"{DB_QUERY}'s amount would be zero on every read — a guard counter "
                    "reading flat while rows grow. Refusing to install rather than "
                    "counting something that is not what the catalogue says it is. "
                    f"Measured to report rows: {', '.join(sorted(ROW_COUNTING_VENDORS))}"
                )
                raise HookError(message)

        with ExitStack() as stack:
            for connection in wrapped:
                stack.enter_context(connection.execute_wrapper(wrapper))
            yield

    return install


@dataclass(frozen=True)
class DjangoAdapter:
    """The framework boundary for Django + DRF + Postgres.

    **Every field is a fact grounding established and none is derivable here.**
    That is S-7.2's convention rather than a shortcut: what seeds *this* project,
    which model its workload is about and which database it was stood up against
    are properties of the repository, and a module under `src/` that chose one on
    its own account would be guessing where the Explorer measured.

    An adapter given none of them still answers for the framework — its
    declarations, its query hook and its route enumeration need no grounding —
    and `capabilities()` reports the difference rather than claiming it away.
    """

    seeder: Seeder | None = None
    """The repository's own factory, already bound. `explorer.fixtures.factory_seeder`
    builds one; S-7.5 prefers it over synthesis wherever a repository has one."""

    target: str | None = None
    """The model synthesis seeds from, where there is no factory to prefer.

    Named exactly as `verify_work` names it, and the pair means the same thing
    here: one of the two has to say what the rows are.
    """

    per_parent: int = 1
    distribution: Distribution = Distribution.UNIFORM
    """The fixture's shape, which only synthesis can choose — S-3.3 proved the
    uniform fixture is the blindest one for any per-parent cost."""

    database: VerifiedDatabase | None = None
    """The subject's database, already past the production guard.

    A `VerifiedDatabase` rather than a URL, because constructing one *is* S-2.5's
    check: an adapter that took a string could be pointed at production and would
    have to remember to look.
    """

    aliases: tuple[str, ...] | None = None
    """Which database connections the workload actually touches.

    `None` instruments every alias Django has, which is the safe default: an
    uninstrumented alias is a silent undercount. Naming them is for the project
    whose settings hold a replica or an analytics database the workload never
    reaches — ADR 008's *each connection under measurement*, made a decision
    somebody states rather than one this module infers.
    """

    interpreter: str = "python"
    """What runs a command inside a session's container.

    Distinct from `Subject.python`, which reaches the subject's environment from
    the host — the two are different machines and conflating them is why this is
    named for the container. The default matches `audit.equivalence` and
    `repair.compose`, which run in the same containers.
    """

    @property
    def framework(self) -> Framework:
        return Framework.DJANGO

    @property
    def declarations(self) -> Declarations:
        """The four, and DRF is present in two of them.

        DRF needs nothing of its own beyond `rest_framework/` in the frames: its
        routers are already resolved by asking the framework for its route table
        (S-7.3), and its token credential is already minted by S-7.4.
        """
        return Declarations(
            orm=Orm.DJANGO_ORM,
            hooks={DB_QUERY: query_hook(aliases=self.aliases)},
            internal_frames=DJANGO_INTERNAL_FRAMES,
            protected_paths=DJANGO_PROTECTED_PATHS,
        )

    def capabilities(self) -> AbstractSet[Capability]:
        """What this adapter can supply *as constructed*.

        Not a constant. An adapter with no way to seed cannot honestly claim
        `FIXTURE_SEEDING`, and the consequence of claiming it is concrete:
        `Registry.select` would offer scaling and ablation, and they would fail
        at the point of seeding rather than being withheld with a reason a
        reader can act on.
        """
        supplied = {Capability.EVENT_COUNTERS}
        if self.seeder is not None or self.target is not None:
            supplied.add(Capability.FIXTURE_SEEDING)
        if self.target is not None:
            # Only synthesis takes a distribution. A repository's own factory
            # builds whatever shape it was written to build, and recording that
            # as a *chosen* distribution would be a claim nobody made.
            supplied.add(Capability.FIXTURE_SHAPING)
        if self.database is not None:
            supplied.add(Capability.STATE_RESET)
        return frozenset(supplied)

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        """Ask the framework for its route table, falling back to reading files.

        The interpreter is passed, so the URLconf is *resolved* rather than
        parsed: a route table built in a loop or by a DRF router has no routes to
        read out of a file, and `Enumeration` says which of the two happened.
        """
        return enumerate_entry_points(subject.root, python=subject.python, timeout=timeout)

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        """Fill the subject with `scale` rows, preferring its own factory.

        Raises:
            ValueError: neither a seeder nor a target was supplied, so nothing
                here can say what the rows are.
        """
        if self.seeder is not None:
            return self.seeder(
                root=subject.root, python=subject.python, scale=scale, timeout=timeout
            )
        if self.target is None:
            message = (
                "this adapter was given neither a seeder nor a target, so it cannot say what "
                "the rows are. `target` names what to build from the schema, and `seeder` is "
                "the repository's own mechanism (S-7.5, preferred where there is one)"
            )
            raise ValueError(message)

        synthesized = synthesize(
            subject.root,
            python=subject.python,
            target=self.target,
            count=scale,
            per_parent=self.per_parent,
            distribution=self.distribution,
            timeout=min(timeout, SYNTHESIS_TIMEOUT_SECONDS),
        )
        return synthesized.recipe(), synthesized.created

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
        """Drive the route through Django's test client and measure it."""
        return drive(
            subject.root,
            python=subject.python,
            path=entry_point,
            scale=scale,
            created=created,
            headers=headers,
            cookies=cookies,
            repeats=repeats,
            timeout=timeout,
        )

    def run_tests(
        self, session: Session, *, selection: Sequence[str] = (), timeout: float
    ) -> ExecutionResult:
        """Run the subject's own suite on this session's revision.

        The command is chosen from what the repository declares — see
        `suite_command`. The result is handed back whole: *the suite was already
        failing* and *the patch broke the suite* are different findings and only
        a caller holding both revisions can tell them apart.
        """
        command = self.suite_command(session.worktree.path, selection=selection)
        return session.run(command, timeout=timeout)

    def suite_command(self, root: Path, *, selection: Sequence[str] = ()) -> tuple[str, ...]:
        """Which command runs this repository's tests, read from the repository.

        **Which runner is S-7.1's question and this does not answer it again.**
        `declared_test_runner` already reads the pytest configuration, the
        requirements and — because Django ships a runner and every Django project
        has one — `manage.py`. The first draft of this method reimplemented the
        last two cases and got the first one wrong: it asked through
        `fingerprint`, which returns `Unsupported` for a repository whose
        manifests do not name Django, so a project declaring
        `[tool.pytest.ini_options]` and nothing else fell through to
        `manage.py test`. A second copy of a question is a second answer to it.

        What is genuinely this module's is the *command* each runner is invoked
        by, which is the mapping below.

        A repository that declares nothing at all gets `unittest`. That case is
        narrow by the time it is reached — a Django project with no `manage.py`
        and no pytest configuration — and it is the only remaining runner.
        """
        declared = declared_test_runner(root, framework=Framework.DJANGO)
        runner = declared.value if declared is not None else TestRunner.UNITTEST
        return (self.interpreter, *SUITE_COMMANDS[runner], *selection)

    def read_source(self, session: CandidateSession) -> Mapping[str, str]:
        """Python and templates, because both decide what a view returns.

        A patch to a view that a template renders has callers a `.py`-only read
        would not find, and S-11.5's scope audit reports *no callers outside the
        evidence* as the shape of a safe patch. Reading only half the sources
        would produce that answer for the wrong reason.
        """
        sources = dict(session.sources(suffix=".py"))
        sources.update(session.sources(suffix=".html"))
        return sources

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        """Hand the diff to the session, which is where the filter runs.

        Django needs nothing after a source change: the subject is re-imported by
        the next process that drives it, and there is nothing compiled to rebuild.
        The method exists because a non-Python adapter's would not be empty, and
        because there must be exactly one route from a diff to a file.
        """
        return session.apply_patch(diff)

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        """Rollback first, then snapshot restore. Both need only the database.

        Cheapest first, which is `choose_reset`'s order and S-0.5's measurement:
        19 ms against 163 ms.

        **Container restart is deliberately absent.** It needs the container's
        name, its image and the seed SQL, none of which is a fact about Django —
        they belong to standup, which created the container. An adapter inventing
        them would be an adapter restarting somebody else's container.
        `choose_reset` takes an iterable, so a campaign holding those facts
        appends its own candidate after these two.

        An adapter with no database offers nothing, and `choose_reset` refuses an
        empty candidate list rather than proceeding unreset.
        """
        del subject
        if self.database is None:
            return ()
        return (
            RollbackReset(database=self.database),
            SnapshotRestoreReset(database=self.database),
        )


# ------------------------------------------------- grounding: the three that are ours
#
# **S-14.6.** `stages.py` held all nine and called them `_DJANGO_PREDICATES`, in
# core, which is one of the three places ADR 148 §1 said Epic 14 had not
# finished. Six of the nine turned out to be framework-neutral and stayed there;
# these are the ones that are actually Django's — two reach for Django's
# entry-point enumerator and one runs `manage.py check`.


def _clone(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    del payload
    root = Path(grounding.root)
    if not root.is_dir():
        return Outcome(Stage.CLONE, Verdict.FAILS, f"{root} is not a directory")

    parsed = enumerate_entry_points(root)
    routes = parsed.of_kind(Kind.HTTP_ROUTE)
    commands = parsed.of_kind(Kind.MANAGEMENT_COMMAND)
    if not routes and not commands:
        return Outcome(
            Stage.CLONE,
            Verdict.FAILS,
            f"{parsed.files_read} file(s) read under {root} and no route or management "
            "command was found in any of them",
        )
    return Outcome(
        Stage.CLONE,
        Verdict.HOLDS,
        f"{len(routes)} route(s) and {len(commands)} command(s) read from the checkout",
    )


def _configure(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    """The framework's own check command, which is the point.

    Django's `check` knows what a misconfigured Django looks like and this module
    does not. Reimplementing its judgement here would be a second opinion to keep
    in step with the first, and it is the framework's opinion that decides whether
    the framework will run.
    """
    if not payload.get("imported"):
        return Outcome(
            Stage.CONFIGURE,
            Verdict.UNKNOWN,
            "the framework does not import, so its check command cannot be run",
        )

    result = grounding.where().run(
        [*grounding.python, "manage.py", "check"],
        timeout=grounding.timeout,
    )
    if result.exit_code == 0:
        return Outcome(Stage.CONFIGURE, Verdict.HOLDS, "manage.py check exited 0")
    said = (result.stderr or result.stdout).strip()[-400:]
    return Outcome(
        Stage.CONFIGURE, Verdict.FAILS, f"manage.py check exited {result.exit_code}: {said}"
    )


def _endpoint(grounding: Grounding, payload: Mapping[str, Any]) -> Outcome:
    """The route table, and it is claimed complete only when the framework answered.

    S-7.3's distinction reaching here: a parse establishes that a `path()` call
    appears in a file, and a repository whose routes are all registered by a DRF
    router has none to read. So the interpreter is used where there is one.
    """
    del payload
    root = Path(grounding.root)
    found = enumerate_entry_points(root, python=list(grounding.python))
    routes = found.of_kind(Kind.HTTP_ROUTE)
    if not routes:
        return Outcome(
            Stage.ENDPOINT,
            Verdict.FAILS,
            f"no candidate route was enumerated. {found.resolution.describe().splitlines()[0]}",
        )
    return Outcome(
        Stage.ENDPOINT,
        Verdict.HOLDS,
        f"{len(routes)} candidate route(s) enumerated; table complete: {found.routes_are_complete}",
    )


DJANGO_PREDICATES: Mapping[Stage, Predicate] = {
    **FRAMEWORK_NEUTRAL_PREDICATES,
    Stage.CLONE: _clone,
    Stage.CONFIGURE: _configure,
    Stage.ENDPOINT: _endpoint,
}
"""ADR 009's nine, six of them core's and three of them this adapter's."""


register(
    Grounds(
        framework=Framework.DJANGO,
        enumerate_entry_points=enumerate_entry_points,
        predicates=DJANGO_PREDICATES,
    )
)
"""**Registered at import, which is what makes Django groundable at all.**

Nothing in `explorer/` names Django any more, so this line is the whole of the
answer to *can this system ground a Django project*. `adapters/__init__.py`
imports this module for that reason, and a test reads this directory for
`register(` to check that every adapter is reachable — ADR 050's construction,
because a framework whose adapter nobody imported is not withheld, it does not
exist.
"""

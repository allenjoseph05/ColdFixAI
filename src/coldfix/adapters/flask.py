"""Flask and SQLAlchemy. The second adapter, and the one that checks the first.

Epic 14, S-14.3. The Django adapter could satisfy an interface shaped around
Django without anybody noticing. This one is the control: a different web
framework, a different ORM, a different way of finding routes and of counting
queries, going through the same eight operations and the same core.

**What it establishes and what it does not.** No file outside `adapters/`
changed to add it, which is AC 2, and `tests/adapters/test_both.py` drives both
adapters through one pipeline that names neither framework. What it does **not**
establish is that the *grounding sequence* runs on Flask: `explorer/compose.py`
calls `enumerate_entry_points` directly, `stages.PREDICATES` has one entry, and
`Framework.supported` is `Django only`. Those are correct today — grounding
really does only support Django — and routing them through an adapter is work
this story deliberately did not start. ADR 148 says what it would take.

**The differences from the Django adapter are the interesting part.** Three of
them are not incidental:

*It cannot synthesize fixtures.* `explorer/synthesis.py` introspects Django
models; there is no equivalent for a SQLAlchemy schema and this story did not
write one. So `FlaskAdapter` has no `target` field, `seed` works only from a
supplied seeder, and `capabilities()` never reports `FIXTURE_SHAPING`. That is
the tri-state applicability design paying for itself: the primitives needing a
chosen distribution are **withheld with a reason** rather than offered and then
failed at the point of seeding.

*Its routes are read, never resolved.* Django's route table is obtained by asking
the framework in the subject's interpreter (S-7.3), because a URLconf built in a
loop has no routes to read. Flask's are decorators, so they can be parsed — and
parsing is honest about what it cannot see, which `Enumeration.resolution`
records rather than hides.

*Its query hook is an event listener, not a wrapper.* SQLAlchemy's
`after_cursor_execute` fires on the `Engine` class, so it catches every engine
including ones created after the block opens — which is the limitation the
Django hook has to document and this one does not.

**Nothing here imports Flask or SQLAlchemy at module level**, for the reason
`django.py` gives: a wheel installed without them must still import `coldfix`.
"""

from __future__ import annotations

import ast
import json
import os
import statistics
from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coldfix.adapters.interface import ROW_COUNTING_VENDORS, Declarations, Subject
from coldfix.bench.counting import Hook, HookError, Record
from coldfix.bench.execute import ExecutionError, ExecutionResult, execute
from coldfix.explorer.entrypoints import (
    Candidate,
    Discovery,
    Enumeration,
    Kind,
    Resolution,
    rank,
)
from coldfix.explorer.fingerprint import Framework, Orm, TestRunner, declared_test_runner
from coldfix.explorer.work import Drive, Seeder, WorkVerificationError
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.registry import Capability
from coldfix.sandbox.modes import CandidateSession, Session
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetMechanism, RollbackReset, SnapshotRestoreReset
from coldfix.screening.workload import FixtureRecipe

FLASK_INTERNAL_FRAMES: tuple[str, ...] = (
    # The framework and the WSGI layer under it. A stack through a Flask view is
    # werkzeug at the top for four or five frames before any of the subject's
    # code appears.
    "flask/",
    "werkzeug/",
    # The ORM, which is where a stack raised by a query begins. Deeper than
    # Django's equivalent — a lazy-load stack passes through `orm/`, `engine/`
    # and `sql/` before reaching the caller.
    "sqlalchemy/",
    # Templates, for the same reason `django/template/` is in the other list.
    "jinja2/",
    "site-packages",
    "dist-packages",
)
"""Path fragments belonging to the framework rather than to the subject."""

FLASK_PROTECTED_PATHS: tuple[str, ...] = (
    # Alembic. `versions/` holds generated revisions and `env.py` decides how
    # they run; editing either changes what a database becomes rather than what
    # the application does.
    "**/alembic/**",
    "**/migrations/versions/**",
    "**/static/**",
)
"""Added to `DEFAULT_PROTECTED_PATTERNS`, never substituted for them.

`**/migrations/**` is deliberately narrower here than in the Django list. Flask
projects put application code beside Alembic's output often enough that
protecting the whole directory would refuse patches to code somebody wrote.
"""

SUITE_COMMANDS: Mapping[TestRunner, tuple[str, ...]] = {
    TestRunner.PYTEST: ("-m", "pytest"),
    TestRunner.UNITTEST: ("-m", "unittest"),
    # Flask has no runner of its own. `declared_test_runner` only returns this
    # for a framework whose `manage.py` it found, and it is not asked about
    # Django here — but a mapping missing a member would be a `KeyError` at the
    # worst moment, so it maps to the only thing that could run a suite.
    TestRunner.DJANGO: ("-m", "pytest"),
}

DRIVE_TIMEOUT_SECONDS = 600.0

_MARKER = "<<<COLDFIX-FLASK-WORK>>>"

_OK_LOW = 200
_OK_HIGH = 299

# `@app.route("/x")`, `@bp.route(...)`, and the method shorthands Flask 2 added.
_ROUTE_DECORATORS: frozenset[str] = frozenset({"route", "get", "post", "put", "patch", "delete"})

_SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "migrations",
        "alembic",
    }
)


# Runs in the *subject's* interpreter. Counting is an `Engine`-class event
# listener, so every engine the application creates is counted — including ones
# created lazily on the first request, which is the common Flask shape.
#
# Cookies travel as a `Cookie` header rather than through `client.set_cookie`,
# whose signature changed between Flask 2.2 and 2.3. A header is what the client
# sends either way, and it is the version-independent spelling.
_DRIVE_SOURCE = """
import json, os, sys, time

sys.path.insert(0, os.getcwd())

from sqlalchemy import event
from sqlalchemy.engine import Engine

REQUEST = json.loads(sys.argv[1])

counted = {"queries": 0}


@event.listens_for(Engine, "after_cursor_execute")
def _count(conn, cursor, statement, parameters, context, executemany):
    counted["queries"] += 1


module_name, _, attribute = REQUEST["app"].partition(":")
module = __import__(module_name, fromlist=["*"])
app = getattr(module, attribute or "app")
if not hasattr(app, "test_client"):
    app = app()

headers = dict(REQUEST["headers"])
if REQUEST["cookies"]:
    headers["Cookie"] = "; ".join(
        name + "=" + value for name, value in REQUEST["cookies"].items()
    )
path = REQUEST["path"]

client = app.test_client()

warm_started = time.perf_counter()
client.get(path, headers=headers)
warmup = time.perf_counter() - warm_started

samples = []
queries = None
size = None
status = None

for _ in range(REQUEST["repeats"]):
    counted["queries"] = 0
    started = time.perf_counter()
    response = client.get(path, headers=headers)
    elapsed = time.perf_counter() - started
    samples.append(elapsed)
    queries = counted["queries"]
    size = len(response.data)
    status = response.status_code

print("__MARKER__" + json.dumps({
    "warmup_seconds": warmup,
    "samples": samples,
    "queries": queries,
    "response_bytes": size,
    "status": status,
}))
"""

_DRIVE = _DRIVE_SOURCE.replace("__MARKER__", _MARKER)


def query_hook() -> Hook:
    """Count statements through SQLAlchemy's `after_cursor_execute` event.

    One event per statement, and the amount is the rows it returned, so
    `db.query` and `db.rows` come from one attachment — the same contract the
    Django hook has and the same refusal when a backend cannot honour it.

    **Listening on the `Engine` class rather than on an instance** is what makes
    this catch engines created after the block opens, which a Flask application
    factory routinely does. The Django hook cannot do the equivalent: its
    connections are thread-local objects that have to exist before they can be
    wrapped.

    Raises:
        HookError: a backend that does not report rows per statement. See
            `ROW_COUNTING_VENDORS`; the reasoning is ADR 147's and is not
            specific to either ORM.
    """

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        # Imported here, not at module scope: `coldfix` must import on a machine
        # with no SQLAlchemy.
        from sqlalchemy import event  # noqa: PLC0415 - see the module docstring
        from sqlalchemy.engine import Engine  # noqa: PLC0415

        def after_cursor_execute(  # noqa: PLR0913, PLR0917 - SQLAlchemy's event
            # signature, six positional parameters in its order since 0.7. A listener
            # that took fewer would not be called.
            conn: Any,  # noqa: ANN401 - SQLAlchemy's event signature, unchanged since 0.7
            cursor: Any,  # noqa: ANN401
            statement: str,
            parameters: Any,  # noqa: ANN401
            context: Any,  # noqa: ANN401
            executemany: bool,
        ) -> None:
            vendor = getattr(getattr(conn, "dialect", None), "name", "unknown")
            if vendor not in ROW_COUNTING_VENDORS:
                message = (
                    f"the {vendor!r} dialect does not report rows per statement, so "
                    f"{DB_QUERY}'s amount would be zero on every read — a guard counter "
                    "reading flat while rows grow. Measured to report rows: "
                    f"{', '.join(sorted(ROW_COUNTING_VENDORS))}"
                )
                raise HookError(message)
            rows = getattr(cursor, "rowcount", -1)
            record(float(rows) if rows >= 0 else 0.0)

        event.listen(Engine, "after_cursor_execute", after_cursor_execute)
        try:
            yield
        finally:
            event.remove(Engine, "after_cursor_execute", after_cursor_execute)

    return install


@dataclass(frozen=True)
class FlaskAdapter:
    """The framework boundary for Flask + SQLAlchemy.

    **No `target` field, and its absence is the point.** Synthesis from a schema
    is `explorer/synthesis.py`, which introspects Django models; there is no
    SQLAlchemy equivalent and this story did not write one. So this adapter can
    seed only from a mechanism the repository already has, and it says so through
    `capabilities()` instead of failing later.
    """

    app: str = ""
    """Where the application object lives, as Flask's own `module:attribute`.

    The Flask analogue of `DJANGO_SETTINGS_MODULE`, and supplied for the same
    reason: it is a fact about this repository's layout, and S-7.2's convention
    is that nothing under `src/` chooses one on its own account. An attribute
    that is not already an application is called, which is how a factory
    (`myapp:create_app`) is reached.
    """

    seeder: Seeder | None = None
    """The repository's own way of making rows. There is no fallback."""

    database: VerifiedDatabase | None = None
    interpreter: str = "python"

    @property
    def framework(self) -> Framework:
        return Framework.FLASK

    @property
    def declarations(self) -> Declarations:
        return Declarations(
            orm=Orm.SQLALCHEMY,
            hooks={DB_QUERY: query_hook()},
            internal_frames=FLASK_INTERNAL_FRAMES,
            protected_paths=FLASK_PROTECTED_PATHS,
        )

    def capabilities(self) -> AbstractSet[Capability]:
        """Never `FIXTURE_SHAPING`, and that is a real difference from Django.

        A primitive that needs a chosen distribution is withheld here with the
        reason *this environment does not provide seeding fixtures with a chosen
        distribution*, which is what a reader can act on. Claiming it and failing
        at the point of seeding would spend an experiment to learn the same
        thing.
        """
        supplied = {Capability.EVENT_COUNTERS}
        if self.seeder is not None:
            supplied.add(Capability.FIXTURE_SEEDING)
        if self.database is not None:
            supplied.add(Capability.STATE_RESET)
        return frozenset(supplied)

    def discover_workloads(self, subject: Subject, *, timeout: float) -> Enumeration:
        """Read the route decorators. Flask's routes are declarations, so they can be.

        `timeout` is accepted and unused: nothing is executed. It stays in the
        signature because the Protocol has it and because an adapter for a
        framework that *must* be asked — Django is one — spends it on a
        subprocess. Dropping it here would make the interface's shape depend on
        which adapter happened to be written first.

        The resolution says plainly that the framework was not asked. A parsed
        route may never register — a blueprint added conditionally, a route
        behind an `if` — and an enumeration that presented a parse as a
        resolution would report a guess in the column that holds measurements.
        """
        del timeout
        root = Path(subject.root)
        candidates: list[Candidate] = []
        files = 0

        for path in _python_files(root):
            files += 1
            tree = _parse(path)
            if tree is None:
                continue
            candidates.extend(_routes_in(tree, _relative(root, path)))
            candidates.extend(_commands_in(tree, _relative(root, path)))

        return Enumeration(
            root=root,
            scored=rank(candidates),
            unexpanded=(),
            resolution=Resolution(
                available=False,
                error=(
                    "not attempted: Flask routes are decorators and were read from the "
                    "files. A route registered at runtime — a blueprint added behind a "
                    "condition, a rule added by `add_url_rule` — is not in this list."
                ),
            ),
            files_read=files,
        )

    def seed(
        self, subject: Subject, *, scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        """Fill the subject using the mechanism it already has.

        Raises:
            ValueError: no seeder was supplied. There is deliberately no
                synthesis fallback — see the class docstring.
        """
        if self.seeder is None:
            message = (
                "this adapter was given no seeder, and there is no synthesis fallback for "
                "SQLAlchemy: `explorer/synthesis.py` introspects Django models. Supply the "
                "repository's own mechanism, or ask for a primitive that does not need rows"
            )
            raise ValueError(message)
        return self.seeder(root=subject.root, python=subject.python, scale=scale, timeout=timeout)

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
        """Drive the route through Flask's test client and measure it.

        Returns the same `Drive` the Django adapter does, which is what lets the
        screening layer above read either without knowing which produced it.

        Raises:
            WorkVerificationError: the subject could not be driven, produced no
                samples, or answered with a status that makes the measurement
                meaningless. The same three refusals `explorer.work.drive` makes,
                and for the same reasons.
        """
        if not self.app:
            message = (
                "this adapter was given no application to drive. Flask's own "
                "`module:attribute` spelling is what `app` takes — `myapp:create_app` for a "
                "factory, `myapp:app` for a module-level application"
            )
            raise WorkVerificationError(message)
        if repeats < 1:
            message = f"{repeats} repeat(s) measures nothing"
            raise WorkVerificationError(message)

        payload = _run_in_subject(
            json.dumps(
                {
                    "app": self.app,
                    "path": entry_point,
                    "headers": dict(headers or {}),
                    "cookies": dict(cookies or {}),
                    "repeats": repeats,
                }
            ),
            root=subject.root,
            python=subject.python,
            timeout=timeout,
        )

        samples = tuple(float(sample) for sample in payload.get("samples", []))
        if not samples:
            message = f"{entry_point} was driven and reported no timing samples at n={scale}"
            raise WorkVerificationError(message)

        status = int(payload.get("status") or 0)
        if not _OK_LOW <= status <= _OK_HIGH:
            message = (
                f"{entry_point} answered HTTP {status} at n={scale}. An error page is cheap, "
                "constant and identical at every scale, which is exactly the profile a work "
                "check exists to reject"
            )
            raise WorkVerificationError(message)

        return Drive(
            scale=scale,
            queries=int(payload.get("queries") or 0),
            response_bytes=int(payload.get("response_bytes") or 0),
            seconds=statistics.median(samples),
            samples=samples,
            warmup_seconds=float(payload.get("warmup_seconds") or 0.0),
            status=status,
            created=dict(created),
        )

    def run_tests(
        self, session: Session, *, selection: Sequence[str] = (), timeout: float
    ) -> ExecutionResult:
        """Run the subject's own suite on this session's revision."""
        command = self.suite_command(session.worktree.path, selection=selection)
        return session.run(command, timeout=timeout)

    def suite_command(self, root: Path, *, selection: Sequence[str] = ()) -> tuple[str, ...]:
        """pytest where the repository declares it, and pytest otherwise.

        Flask ships no test runner, so the fallback differs from Django's — which
        is the whole reason this is per adapter and not a core function with a
        framework argument. `declared_test_runner` answers *what the repository
        says*; what to do when it says nothing is a fact about the framework.
        """
        declared = declared_test_runner(root, framework=Framework.FLASK)
        runner = declared.value if declared is not None else TestRunner.PYTEST
        return (self.interpreter, *SUITE_COMMANDS[runner], *selection)

    def read_source(self, session: CandidateSession) -> Mapping[str, str]:
        """Python and Jinja templates, for `django.py`'s reason with Django's."""
        sources = dict(session.sources(suffix=".py"))
        sources.update(session.sources(suffix=".html"))
        return sources

    def apply_patch(self, session: CandidateSession, diff: str) -> frozenset[str]:
        """Hand the diff to the session, which is where the filter runs."""
        return session.apply_patch(diff)

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        """The same two mechanisms the Django adapter offers, cheapest first.

        **Identical, and that is a finding rather than duplication.** Resetting a
        Postgres database is a fact about Postgres: a rollback and a template
        restore know nothing about which ORM wrote the rows. The framework
        contributes nothing here, which is why `Declarations.orm` records the
        dialect and the mechanisms take a `VerifiedDatabase`.
        """
        del subject
        if self.database is None:
            return ()
        return (
            RollbackReset(database=self.database),
            SnapshotRestoreReset(database=self.database),
        )


# ============================================================== reading the routes


def _run_in_subject(
    argument: str, *, root: Path, python: Sequence[str], timeout: float
) -> Mapping[str, Any]:
    """Run the drive program in the subject's interpreter and read its answer.

    No environment variable is set, which is the difference from the Django
    equivalent: Flask is told where its application is by an argument rather than
    by `DJANGO_SETTINGS_MODULE`.

    `Any` at a subprocess boundary: another interpreter's JSON, converted at the
    call site rather than trusted.
    """
    try:
        result = execute(
            [*python, "-c", _DRIVE, argument],
            timeout=timeout,
            cwd=root,
            env=dict(os.environ),
        )
    except ExecutionError as error:
        raise WorkVerificationError(str(error)) from error

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        message = f"the subject's interpreter did not answer (exit {result.exit_code}): {said}"
        raise WorkVerificationError(message)

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        message = f"the subject's answer was not JSON: {error}"
        raise WorkVerificationError(message) from error
    return payload


def _python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if not any(part in _SKIP_DIRECTORIES for part in path.relative_to(root).parts):
            yield path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _parse(path: Path) -> ast.Module | None:
    """A file this cannot parse is skipped, never guessed at."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return None


def _decorator_call(node: ast.expr) -> tuple[str, ast.Call] | None:
    """`@thing.route(...)` as `("route", call)`, or `None` for anything else.

    Matching on the *attribute* rather than on the object is deliberate: an
    application is called `app` by convention and a blueprint is called anything
    at all, so requiring a name would find the convention and miss the blueprints
    — which are where a real project keeps most of its routes.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    return (node.func.attr, node) if node.func.attr in _ROUTE_DECORATORS else None


def _routes_in(tree: ast.Module, evidence: str) -> list[Candidate]:
    found: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            matched = _decorator_call(decorator)
            if matched is None:
                continue
            _, call = matched
            rule = _first_string(call)
            if rule is None:
                continue
            found.append(
                Candidate(
                    kind=Kind.HTTP_ROUTE,
                    name=rule,
                    evidence=evidence,
                    discovery=Discovery.PARSED,
                    target=node.name,
                    route_name=node.name,
                )
            )
    return found


def _commands_in(tree: ast.Module, evidence: str) -> list[Candidate]:
    """`@app.cli.command()` and `@click.command()`, Flask's management commands."""
    found: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr != "command":
                continue
            found.append(
                Candidate(
                    kind=Kind.MANAGEMENT_COMMAND,
                    name=_first_string(decorator) or node.name,
                    evidence=evidence,
                    discovery=Discovery.PARSED,
                    target=node.name,
                )
            )
    return found


def _first_string(call: ast.Call) -> str | None:
    for argument in call.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            return argument.value
    return None

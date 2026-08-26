"""The Django adapter, and the one operation in it that is not a transcription.

S-14.2. Seven operations delegate to code Epic 7 and Epic 2 already tested, so
the tests here assert *that the delegation happens and with what* rather than
re-testing `drive` and `enumerate_entry_points`. The eighth — the query hook —
is new, and it is tested against real Django connections rather than a fake,
which is the standard the rest of this repository already holds: a fake
connection would assert only what this file was written to believe.

**The counting half needs Postgres and is marked for it.** The hook's refusal
needs only SQLite and stays in the fast subset, which matters because the
refusal is the safety property: a backend that cannot report rows would make
`db.rows` read flat while rows grew.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.db import connections

from coldfix.adapters import (
    ADAPTER_CAPABILITIES,
    Declarations,
    FrameworkAdapter,
    Subject,
    installed,
)
from coldfix.adapters.django import (
    DJANGO_INTERNAL_FRAMES,
    DJANGO_PROTECTED_PATHS,
    ROW_COUNTING_VENDORS,
    DjangoAdapter,
    query_hook,
)
from coldfix.bench.counting import HookError, count, registered_hooks
from coldfix.bench.execute import DEFAULT_MAX_OUTPUT_CHARS, ExecutionResult
from coldfix.explorer.entrypoints import Kind
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.localization import Frame
from coldfix.primitives.registry import Capability
from coldfix.sandbox import docker_available
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetStrategy, wait_until_ready
from coldfix.sandbox.worktrees import Worktree
from fixtures.containers import require_image

SPIKE_REPOS = Path(__file__).resolve().parents[2] / "spikes" / "S-0.3-grounding" / "repos"


class _Session(CandidateSession):
    """A candidate session over a plain directory, recording what it was asked to do.

    Constructed rather than opened, because `Workbench.open` needs a repository
    and a container and the properties under test here are about *which* command
    and *which* diff the adapter hands over. `sources` is deliberately not
    overridden — `read_source` is tested against a real directory read.
    """

    def __init__(self, path: Path) -> None:
        self._worktree = Worktree(path=path, revision="0" * 40, is_main=False)
        self._closed = False
        self.commands: list[tuple[str, ...]] = []
        self.applied: list[str] = []

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        del timeout, env, max_output_chars
        self.commands.append(tuple(command))
        return ExecutionResult(
            command=tuple(command), exit_code=0, stdout="", stderr="", wall_seconds=0.0
        )

    def apply_patch(self, diff: str) -> frozenset[str]:
        self.applied.append(diff)
        return frozenset({"shop/views.py"})


# ============================================================ the module's own imports


def test_importing_the_adapter_does_not_import_django() -> None:
    """A wheel installed without Django must still import `coldfix`.

    `pyproject.toml` keeps Django in the dev group on exactly that ground, and
    the guarantee is one `import` statement away from being lost. A fresh
    interpreter rather than `sys.modules` here, because this test session has
    imported Django for the tests below.
    """
    program = (
        "import sys; import coldfix.adapters.django as m; "
        "print('django' in sys.modules or 'django.db' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stderr


# =================================================================== the query hook


@pytest.fixture(scope="module")
def _sqlite_django() -> Iterator[None]:
    """Django configured against SQLite, for the refusal path.

    Settings are process-global and configuring them twice raises, so this is
    module-scoped and guarded. Nothing else in the suite configures Django
    in-process — every other Django test runs it in a subject's own interpreter.
    """
    if not settings.configured:
        settings.configure(
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            INSTALLED_APPS=[],
            USE_TZ=True,
        )
    yield


def test_the_hook_refuses_a_backend_that_cannot_report_rows(_sqlite_django: None) -> None:
    """The safety property, measured rather than assumed.

    SQLite reports `rowcount = -1` for every `SELECT`, so the amount recorded
    would be zero on every read and `db.rows` — a guard counter — would stay flat
    while rows grew. That is the guard-counter failure this project names in its
    own non-negotiables, so the hook refuses to install rather than counting
    something that is not what the catalogue says it is.
    """
    with pytest.raises(HookError) as raised, query_hook()(lambda amount=1.0: None):
        pytest.fail("a backend with no row count should not have been instrumented")

    assert "sqlite" in str(raised.value)
    assert "rows per statement" in str(raised.value)
    assert connections["default"].execute_wrappers == []


def test_the_refusal_names_what_was_measured(_sqlite_django: None) -> None:
    """A refusal that does not say which backends do work is not actionable."""
    with pytest.raises(HookError) as raised, query_hook()(lambda amount=1.0: None):
        pass
    assert "postgresql" in str(raised.value)


def test_postgresql_is_the_measured_vendor() -> None:
    """`ROW_COUNTING_VENDORS` is short on purpose and its contents are evidence."""
    assert frozenset({"postgresql"}) == ROW_COUNTING_VENDORS


# ------------------------------------------------------- the counting half, on Postgres

IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"

# Not 5432, and not a port another test module pinned. A hook pointed at the
# wrong database would count somebody else's queries and look like it worked.
PORT = 55461


@pytest.fixture(scope="module")
def _postgres_django() -> Iterator[str]:
    """A Postgres container, and Django configured to talk to it.

    Module-scoped for the same reason `test_reset.py`'s is: starting Postgres is
    seconds and the tests here do not cross a database boundary.
    """
    if not docker_available():
        pytest.skip("no Docker daemon is listening")
    require_image(IMAGE)

    name = f"coldfix_subject_{uuid.uuid4().hex[:8]}"
    container = f"coldfix-django-hook-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "--detach", "--name", container,
            "--publish", f"{PORT}:5432",
            "--env", f"POSTGRES_USER={USER}",
            "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", f"POSTGRES_DB={name}",
            "--", IMAGE,
        ],
        capture_output=True,
        check=True,
        timeout=180,
    )  # fmt: skip
    url = f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/{name}"
    try:
        wait_until_ready(VerifiedDatabase(url))
        if not settings.configured:
            settings.configure(
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.postgresql",
                        "NAME": name,
                        "USER": USER,
                        "PASSWORD": PASSWORD,
                        "HOST": "localhost",
                        "PORT": str(PORT),
                    }
                },
                INSTALLED_APPS=[],
                USE_TZ=True,
            )
        # Without this Django warns that the database is being reached before
        # the app registry is ready, which is true and is an artifact of a
        # settings-only configuration rather than anything about the hook.
        django.setup()
        yield url
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", container],
            capture_output=True,
            check=False,
            timeout=180,
        )


@pytest.mark.postgres
@pytest.mark.slow
def test_it_counts_one_event_per_statement_and_records_rows(_postgres_django: str) -> None:
    """AC 2, end to end, and through the adapter's own declaration.

    **Driven through `installed(...)` and `count(...)` rather than by entering
    the hook directly**, because the property worth having is not that
    `query_hook` works in isolation — it is that the adapter declares it, files
    it under the catalogue's name, and that `count(DB_QUERY)` therefore finds it.
    An isolated test would still pass if the adapter declared a different hook or
    filed it under a name nothing asks for.

    Both numbers come from one attachment, which is what the guard-counter rule
    needs: the events are the statements and the total is the rows they returned.
    """
    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS ticket")
        cursor.execute("CREATE TABLE ticket (id serial PRIMARY KEY, title text)")
        cursor.execute("INSERT INTO ticket (title) VALUES ('a'), ('b'), ('c')")

    with installed(DjangoAdapter().declarations):
        assert DB_QUERY in registered_hooks()
        with count(DB_QUERY) as tally, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM ticket")
            cursor.fetchall()
            cursor.execute("SELECT * FROM ticket WHERE title = 'zzz'")
            cursor.fetchall()

    assert tally.events == 2
    # Three rows and then none. A real empty result is zero, and on this backend
    # it is distinguishable from *this statement has no row count* — which is the
    # whole reason the hook accepts this vendor and refuses SQLite.
    assert tally.total == 3.0
    assert DB_QUERY not in registered_hooks()


@pytest.mark.postgres
@pytest.mark.slow
def test_the_wrapper_is_removed_when_the_workload_raises(_postgres_django: str) -> None:
    """ADR 008 asks for this adversarially, and the mechanism changed under it.

    A connection left instrumented outlives the measurement, and every query the
    process makes afterwards is counted into a tally nobody is reading. Entered
    directly here rather than through the registry, because what is under test is
    the hook's own `finally` and not the registration around it.
    """
    connection = connections["default"]
    before = list(connection.execute_wrappers)

    with pytest.raises(ZeroDivisionError), query_hook()(lambda amount=1.0: None):
        raise ZeroDivisionError

    assert connection.execute_wrappers == before


@pytest.mark.postgres
@pytest.mark.slow
def test_it_counts_a_statement_that_returns_no_rows_as_an_event(
    _postgres_django: str,
) -> None:
    """A statement with no row count is one event and zero rows, not a lost event.

    DDL reports `rowcount = -1` on every backend measured. Recording the event
    but not inventing rows for it is the honest reading, and dropping the event
    would undercount the queries a workload made.
    """
    connection = connections["default"]

    with (
        installed(DjangoAdapter().declarations),
        count(DB_QUERY) as tally,
        connection.cursor() as cursor,
    ):
        cursor.execute("DROP TABLE IF EXISTS scratch")
        cursor.execute("CREATE TABLE scratch (id serial PRIMARY KEY)")

    assert tally.events == 2
    assert tally.total == 0.0


# ================================================================== the declarations


class TestDeclarations:
    def test_the_orm_is_djangos(self) -> None:
        assert DjangoAdapter().declarations.orm is Orm.DJANGO_ORM

    def test_the_framework_is_django(self) -> None:
        assert DjangoAdapter().framework is Framework.DJANGO

    def test_the_frames_move_the_causal_site_off_the_orm_and_off_drf(self) -> None:
        """Both halves of *Django + DRF*, asserted where a reader would notice.

        A stack through a DRF serializer into the ORM has its two deepest frames
        in code the subject's author cannot change; the site has to be the view.
        """
        orm = "/env/site-packages/django/db/models/query.py"
        serializer = "/env/site-packages/rest_framework/serializers.py"
        stack = (
            Frame(filename=orm, lineno=1, function="_fetch_all"),
            Frame(filename=serializer, lineno=2, function="to_representation"),
            Frame(filename="/app/shop/views.py", lineno=42, function="list_tickets"),
        )
        site = DjangoAdapter().declarations.localizer().localize([stack]).causal_site
        assert site is not None
        assert site.filename.endswith("shop/views.py")

    def test_the_policy_adds_migrations_and_keeps_the_defaults(self) -> None:
        policy = DjangoAdapter().declarations.patch_policy()

        assert policy.matching_rule("shop/migrations/0002_add_index.py") == "**/migrations/**"
        assert policy.matching_rule("shop/tests/test_views.py") is not None
        for default in DEFAULT_PROTECTED_PATTERNS:
            assert default in policy.protected

    def test_settings_are_deliberately_writable(self) -> None:
        """Substitution swaps a configuration value and re-measures.

        Protecting settings would refuse `01-primitives.md`'s safest primitive,
        so the absence is deliberate and is asserted rather than left to be
        rediscovered as a gap.
        """
        policy = DjangoAdapter().declarations.patch_policy()
        assert policy.matching_rule("demodesk/config/settings.py") is None

    def test_the_declared_frames_and_paths_are_the_module_constants(self) -> None:
        declarations = DjangoAdapter().declarations
        assert declarations.internal_frames == DJANGO_INTERNAL_FRAMES
        assert declarations.protected_paths == DJANGO_PROTECTED_PATHS


# =================================================================== capabilities


class TestCapabilities:
    def test_an_adapter_given_nothing_claims_only_the_counter(self) -> None:
        """The declarations need no grounding; everything else does."""
        assert DjangoAdapter().capabilities() == {Capability.EVENT_COUNTERS}

    def test_a_target_buys_seeding_and_shaping(self) -> None:
        supplied = DjangoAdapter(target="shop.Ticket").capabilities()
        assert Capability.FIXTURE_SEEDING in supplied
        assert Capability.FIXTURE_SHAPING in supplied

    def test_a_factory_buys_seeding_but_not_shaping(self) -> None:
        """A repository's factory builds the shape it was written to build.

        Only synthesis takes a distribution, so claiming `FIXTURE_SHAPING` for a
        factory would be recording a choice nobody made — and S-3.3 proved the
        shape decides the answer for any per-parent cost.
        """

        def seeder(**_: object) -> tuple[object, object]:  # pragma: no cover - never called
            raise NotImplementedError

        supplied = DjangoAdapter(seeder=seeder).capabilities()  # type: ignore[arg-type]
        assert Capability.FIXTURE_SEEDING in supplied
        assert Capability.FIXTURE_SHAPING not in supplied

    def test_a_database_buys_state_reset(self) -> None:
        adapter = DjangoAdapter(database=VerifiedDatabase("postgresql://u@localhost/app_test"))
        assert Capability.STATE_RESET in adapter.capabilities()
        assert Capability.STATE_RESET not in DjangoAdapter().capabilities()

    def test_it_never_claims_a_capability_the_harness_owns(self) -> None:
        """S-14.1's split, enforced for the first adapter that exists.

        A diagnostic worktree and an input mutation engine are the harness's, and
        an adapter claiming one would have a primitive offered on the strength of
        an implementation it has never seen.
        """
        full = DjangoAdapter(
            target="shop.Ticket",
            database=VerifiedDatabase("postgresql://u@localhost/app_test"),
        )
        assert full.capabilities() <= ADAPTER_CAPABILITIES


# ==================================================================== reset_state


class TestResetState:
    def test_it_offers_rollback_before_snapshot(self) -> None:
        """Cheapest first, which is `choose_reset`'s order and S-0.5's measurement."""
        adapter = DjangoAdapter(database=VerifiedDatabase("postgresql://u@localhost/app_test"))
        offered = [mechanism.strategy for mechanism in adapter.reset_state(_subject())]
        assert offered == [
            ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
            ResetStrategy.SNAPSHOT_RESTORE,
        ]

    def test_no_database_offers_nothing_rather_than_an_unusable_mechanism(self) -> None:
        """`choose_reset` refuses an empty list, so the absence is loud."""
        assert DjangoAdapter().reset_state(_subject()) == ()


# ===================================================================== run_tests


class TestSuiteCommand:
    def test_pytest_wins_where_the_repository_declares_it(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("# manage\n")
        (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["t"]\n')

        assert DjangoAdapter().suite_command(tmp_path) == ("python", "-m", "pytest")

    def test_manage_py_is_the_fallback_for_a_project_that_declares_nothing(
        self, tmp_path: Path
    ) -> None:
        """Django ships a test runner, so this is framework knowledge, not a guess.

        Treating an undeclared runner as `unittest` would run a discovery pass
        with no settings configured against a project that certainly has some.
        """
        (tmp_path / "manage.py").write_text("# manage\n")
        (tmp_path / "requirements.txt").write_text("django>=5.0\n")

        assert DjangoAdapter().suite_command(tmp_path) == ("python", "manage.py", "test")

    def test_unittest_is_what_is_left(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("django>=5.0\n")
        assert DjangoAdapter().suite_command(tmp_path) == ("python", "-m", "unittest")

    def test_a_selection_is_appended(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("# manage\n")
        command = DjangoAdapter().suite_command(tmp_path, selection=["shop.tests.TicketTests"])
        assert command[-1] == "shop.tests.TicketTests"

    def test_the_interpreter_is_the_containers(self, tmp_path: Path) -> None:
        (tmp_path / "manage.py").write_text("# manage\n")
        adapter = DjangoAdapter(interpreter="python3.12")
        assert adapter.suite_command(tmp_path)[0] == "python3.12"

    def test_run_tests_runs_that_command_in_the_session(self, tmp_path: Path) -> None:
        """The join. Deriving the right command and running a different one
        would pass every test above."""
        (tmp_path / "manage.py").write_text("# manage\n")
        session = _Session(tmp_path)

        result = DjangoAdapter().run_tests(session, timeout=30.0)

        assert session.commands == [("python", "manage.py", "test")]
        assert result.exit_code == 0


# ============================================================= source and patching


class TestSourceAndPatching:
    def test_read_source_returns_templates_as_well_as_python(self, tmp_path: Path) -> None:
        """A patch to a view has callers a `.py`-only read would not find.

        S-11.5 reports *no callers outside the evidence* as the shape of a safe
        patch, and half the sources would produce that answer for the wrong
        reason.
        """
        (tmp_path / "views.py").write_text("def index(): ...\n")
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "index.html").write_text("{% for t in tickets %}{{ t }}{% endfor %}\n")

        sources = DjangoAdapter().read_source(_Session(tmp_path))

        assert "views.py" in sources
        assert "templates/index.html" in sources
        assert "{% for t in tickets %}" in sources["templates/index.html"]

    def test_apply_patch_goes_through_the_session(self, tmp_path: Path) -> None:
        """The one route from a diff to a file, and the adapter does not have another.

        The protected-path filter lives in `session.apply_patch`; an adapter that
        wrote the file itself would apply a patch the filter never saw.
        """
        session = _Session(tmp_path)
        written = DjangoAdapter().apply_patch(session, "--- a/shop/views.py\n")

        assert session.applied == ["--- a/shop/views.py\n"]
        assert written == frozenset({"shop/views.py"})


# ======================================================== it satisfies the interface


def test_the_adapter_satisfies_the_protocol() -> None:
    """The static assertion, checked by the gate's mypy run rather than here.

    S-14.1's `FrameworkAdapter` is not `@runtime_checkable` on purpose, so this
    annotation is the conformance test: a signature that drifts from the Protocol
    fails `uv run mypy .`.
    """
    adapter: FrameworkAdapter = DjangoAdapter()
    assert isinstance(adapter.declarations, Declarations)


# ================================================== it survives a real repository


@pytest.mark.parametrize("name", ["django-helpdesk", "netbox"])
def test_it_discovers_workloads_in_a_repository_nobody_here_wrote(name: str) -> None:
    """AC 3's first half, on the development target and on the reserve.

    **The holdout is deliberately absent and this is not an oversight.** S-0.6
    designates it as never used during development, `tests/test_holdout_discipline.py`
    enforces that, and AC 3's second half is earned at S-17.1 — the story that
    runs the whole pipeline against it once, for evaluation. The reserve is the
    honest stand-in: a second real repository nobody here wrote, and one this
    project is allowed to look at.

    The repositories are gitignored, so this skips where they are absent rather
    than cloning — a test suite that reaches the network fails for reasons that
    are not the code.
    """
    root = SPIKE_REPOS / name
    if not root.is_dir():
        pytest.skip(f"{name} is not checked out (see spikes/S-0.3-grounding)")

    found = DjangoAdapter().discover_workloads(
        Subject(root=root, python=[sys.executable]), timeout=60.0
    )

    assert found.of_kind(Kind.HTTP_ROUTE)
    assert found.scored[0].score > 0
    # This interpreter is not the subject's, so the framework could not be asked
    # and the enumeration says so rather than presenting a partial route table as
    # a complete one.
    assert not found.routes_are_complete
    # And it was *asked*. Without this the adapter could drop the interpreter
    # entirely and every assertion above would still hold — a parse-only
    # enumeration is incomplete for a different reason, and `Resolution` is the
    # only place the two are distinguishable.
    assert "not attempted" not in (found.resolution.error or "")


def _subject() -> Subject:
    return Subject(root=Path("/repo"), python=["python"])

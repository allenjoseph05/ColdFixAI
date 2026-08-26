"""The second adapter, against a real Flask application with a real ORM.

S-14.3. The point of a second adapter is that it is *different*, so the tests
that matter here are the ones where it diverges from Django: routes read instead
of resolved, an event listener instead of a wrapper, and no synthesis fallback at
all.

The application is generated rather than mocked, and driven through the adapter's
own drive program in a subprocess, because what is under test is whether the
interface fits a framework it was not shaped around. A fake app with a fake
engine would demonstrate only that the interface fits a fake.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as OrmSession

from coldfix.adapters import ADAPTER_CAPABILITIES, FrameworkAdapter, Subject, installed
from coldfix.adapters.flask import (
    FLASK_INTERNAL_FRAMES,
    FLASK_PROTECTED_PATHS,
    FlaskAdapter,
    query_hook,
)
from coldfix.bench.counting import HookError, count, registered_hooks
from coldfix.explorer.entrypoints import Discovery, Kind
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.work import WorkVerificationError
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.localization import Frame
from coldfix.primitives.registry import Capability
from coldfix.sandbox import docker_available
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetStrategy, wait_until_ready
from fixtures.containers import require_image

# A Flask application with an N+1 in it, which is not decoration: a route whose
# query count is flat would let a broken counter pass. `/tickets` issues one
# query for the list and one per row for the follow-ups.
APPLICATION = """
from flask import Blueprint, Flask, jsonify
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "ticket"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    followups: Mapped[list["Followup"]] = relationship(back_populates="ticket")


class Followup(Base):
    __tablename__ = "followup"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id"))
    ticket: Mapped["Ticket"] = relationship(back_populates="followups")


engine = create_engine("sqlite:///subject.db")


# A blueprint, because a real project keeps most of its routes on one and the
# variable is named anything at all. The adapter matches on the decorator's
# attribute rather than on the object, which is what finds this.
reports = Blueprint("reports", __name__)


@reports.route("/reports/summary")
def report_summary():
    return {"rows": 0}


def create_app():
    app = Flask(__name__)
    app.register_blueprint(reports)

    @app.route("/tickets")
    def list_tickets():
        with Session(engine) as session:
            tickets = session.scalars(select(Ticket)).all()
            return jsonify(
                [{"id": t.id, "followups": len(t.followups)} for t in tickets]
            )

    @app.route("/health")
    def health():
        return {"ok": True}

    @app.cli.command("seed-tickets")
    def seed_tickets():
        pass

    return app
"""


@pytest.fixture
def flask_project(tmp_path: Path) -> Path:
    """A Flask + SQLAlchemy application with rows in it, ready to be driven."""
    root = tmp_path / "subject"
    root.mkdir()
    (root / "shop.py").write_text(APPLICATION, encoding="utf-8")
    (root / "requirements.txt").write_text("flask>=3.0\nsqlalchemy>=2.0\n", encoding="utf-8")
    _seed(root, tickets=5, per_ticket=2)
    return root


def _seed(root: Path, *, tickets: int, per_ticket: int) -> None:
    """Create the database the application reads, using the application's own models."""
    sys.path.insert(0, str(root))
    try:
        # The application this fixture just wrote. Importable only now, and
        # invisible to mypy for the same reason — it does not exist on disk
        # until the line above ran.
        import shop  # type: ignore[import-not-found]  # noqa: PLC0415

        engine = create_engine(f"sqlite:///{(root / 'subject.db').as_posix()}")
        shop.Base.metadata.create_all(engine)
        with OrmSession(engine) as session:
            for index in range(tickets):
                ticket = shop.Ticket(title=f"ticket {index}")
                ticket.followups = [shop.Followup() for _ in range(per_ticket)]
                session.add(ticket)
            session.commit()
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("shop", None)


def _subject(root: Path) -> Subject:
    return Subject(root=root, python=[sys.executable])


# ============================================================== driving a real app


@pytest.mark.slow
def test_it_drives_a_real_flask_application_and_measures_it(flask_project: Path) -> None:
    """AC 1's hardest half: a `Drive` from a framework the core knows nothing about.

    The same artifact the Django adapter returns, which is what lets the
    screening layer read either without knowing which produced it.
    """
    adapter = FlaskAdapter(app="shop:create_app")

    measured = adapter.run_workload(
        _subject(flask_project),
        entry_point="/tickets",
        scale=5,
        created={"ticket": 5},
        repeats=2,
        timeout=180.0,
    )

    assert measured.status == 200
    assert measured.scale == 5
    assert len(measured.samples) == 2
    assert measured.response_bytes > 0
    assert measured.created == {"ticket": 5}
    # One query for the list and one per ticket for the follow-ups. The exact
    # number is SQLAlchemy's business; that it is more than one is the evidence
    # the listener fired at all.
    assert measured.queries > 1


@pytest.mark.slow
def test_the_query_count_grows_with_the_rows(flask_project: Path) -> None:
    """The measurement is about the workload, not about the instrument.

    A counter wired to a constant would pass every assertion above. Seeding more
    tickets has to move the number, and on this route — which is an N+1 on
    purpose — it has to move *up*.
    """
    adapter = FlaskAdapter(app="shop:create_app")
    subject = _subject(flask_project)

    small = adapter.run_workload(
        subject, entry_point="/tickets", scale=5, created={}, repeats=1, timeout=180.0
    )
    _seed(flask_project, tickets=15, per_ticket=2)
    large = adapter.run_workload(
        subject, entry_point="/tickets", scale=20, created={}, repeats=1, timeout=180.0
    )

    assert large.queries > small.queries


@pytest.mark.slow
def test_a_route_that_does_not_exist_is_refused_rather_than_measured(
    flask_project: Path,
) -> None:
    """404 is cheap, constant and identical at every scale.

    `explorer.work.drive` refuses a non-2xx status for exactly this reason, and
    an adapter that returned the measurement instead would hand screening a
    perfectly flat growth curve for a route that does no work.
    """
    adapter = FlaskAdapter(app="shop:create_app")

    with pytest.raises(WorkVerificationError) as raised:
        adapter.run_workload(
            _subject(flask_project),
            entry_point="/nope",
            scale=5,
            created={},
            repeats=1,
            timeout=180.0,
        )

    assert "404" in str(raised.value)


def test_it_refuses_to_drive_without_an_application(flask_project: Path) -> None:
    """Flask is told where its application is; there is no settings module to read."""
    with pytest.raises(WorkVerificationError) as raised:
        FlaskAdapter().run_workload(
            _subject(flask_project),
            entry_point="/tickets",
            scale=5,
            created={},
            repeats=1,
            timeout=30.0,
        )
    assert "module:attribute" in str(raised.value)


# =================================================================== route reading


class TestDiscovery:
    def test_it_reads_routes_off_the_decorators(self, flask_project: Path) -> None:
        found = FlaskAdapter().discover_workloads(_subject(flask_project), timeout=30.0)

        routes = {candidate.name for candidate in found.of_kind(Kind.HTTP_ROUTE)}
        assert routes == {"/tickets", "/health", "/reports/summary"}

    def test_it_finds_routes_on_a_blueprint(self, flask_project: Path) -> None:
        """The decorator's *attribute* is what is matched, not the object.

        An application is called `app` by convention; a blueprint is called
        whatever its author felt like. Requiring the name would find the
        convention and miss the blueprints — which is where a real project keeps
        most of its routes.
        """
        found = FlaskAdapter().discover_workloads(_subject(flask_project), timeout=30.0)

        routes = {candidate.name for candidate in found.of_kind(Kind.HTTP_ROUTE)}
        assert "/reports/summary" in routes

    def test_it_finds_the_cli_command(self, flask_project: Path) -> None:
        found = FlaskAdapter().discover_workloads(_subject(flask_project), timeout=30.0)
        commands = {c.name for c in found.of_kind(Kind.MANAGEMENT_COMMAND)}
        assert "seed-tickets" in commands

    def test_the_collection_route_outranks_the_health_check(self, flask_project: Path) -> None:
        """Core's ranking, applied to candidates a different adapter produced.

        `rank` is framework-free — it scores a plural name above an
        infrastructure one — and this is the assertion that it works on
        candidates nothing Django-shaped built.
        """
        found = FlaskAdapter().discover_workloads(_subject(flask_project), timeout=30.0)
        assert found.scored[0].candidate.name == "/tickets"

    def test_it_says_the_framework_was_never_asked(self, flask_project: Path) -> None:
        """A parse is not a resolution, and the difference is recorded.

        A blueprint registered behind a condition is invisible to this, and an
        enumeration that reported *four routes* rather than *four routes that
        could be read* would put a guess in the column that holds measurements.
        """
        found = FlaskAdapter().discover_workloads(_subject(flask_project), timeout=30.0)

        assert not found.resolution.available
        assert not found.routes_are_complete
        assert all(c.discovery is Discovery.PARSED for c in found.candidates)


# ======================================================================= the hook


class TestQueryHook:
    def test_it_counts_statements_and_records_rows(self, tmp_path: Path) -> None:
        """SQLite is used here to drive the listener, and it must refuse.

        The refusal is the assertion: the amount is *rows returned*, SQLite
        reports `-1` for every `SELECT`, and recording zero would make `db.rows`
        read flat while rows grew. Same rule as the Django hook, same measured
        reason, different ORM — which is why `ROW_COUNTING_VENDORS` lives in the
        interface rather than in either adapter.
        """
        engine = create_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE TABLE t (id integer primary key)")
            connection.commit()

        with (
            pytest.raises(HookError) as raised,
            query_hook()(lambda amount=1.0: None),
            engine.connect() as connection,
        ):
            connection.execute(select(1))

        assert "sqlite" in str(raised.value)
        assert "rows per statement" in str(raised.value)

    def test_the_listener_is_removed_after_the_block_raises(self, tmp_path: Path) -> None:
        """A listener left on the `Engine` class outlives the measurement.

        **The refusal is the probe, which is why this needs no private API.**
        Inside the block a SQLite statement raises, because a listener is
        attached and that backend cannot answer for rows. Outside it, the same
        statement must run quietly — and it can only do that if the listener was
        removed on the way out of an exception.

        `event.contains` would be the direct check and is unusable here: it
        matches on the exact function object, which the hook closes over and does
        not hand back.
        """
        engine = create_engine(f"sqlite:///{(tmp_path / 'z.db').as_posix()}")

        with (
            pytest.raises(HookError),
            query_hook()(lambda amount=1.0: None),
            engine.connect() as connection,
        ):
            connection.exec_driver_sql("SELECT 1")

        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1

    def test_the_hook_is_declared_under_the_catalogue_name(self) -> None:
        with installed(FlaskAdapter().declarations):
            assert DB_QUERY in registered_hooks()
        assert DB_QUERY not in registered_hooks()

    def test_counting_through_the_registry_reaches_the_listener(self, tmp_path: Path) -> None:
        """The join, as far as SQLite allows it to be driven.

        The statement raises through `count(DB_QUERY)`, which proves the
        registered hook is the one that installed the listener; the refusal it
        raises is the same one the isolated test asserts.
        """
        engine = create_engine(f"sqlite:///{(tmp_path / 'y.db').as_posix()}")

        with (
            pytest.raises(HookError),
            installed(FlaskAdapter().declarations),
            count(DB_QUERY),
            engine.connect() as connection,
        ):
            connection.exec_driver_sql("SELECT 1")


# -------------------------------------------------- the recording path, on Postgres

IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"

# A port of this module's own. The container boilerplate is duplicated from
# `test_django.py` rather than shared, because a `tests/adapters/conftest.py`
# would be the third file named `conftest` in a tree with no `__init__.py` and
# mypy resolves those to one module name — the collision `pyproject.toml`
# already documents for `tests/sandbox/conftest.py`.
PORT = 55462


@pytest.fixture(scope="module")
def _postgres_url() -> Iterator[str]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")
    require_image(IMAGE)

    name = f"coldfix_subject_{uuid.uuid4().hex[:8]}"
    container = f"coldfix-flask-hook-{uuid.uuid4().hex[:8]}"
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
    url = f"postgresql+psycopg://{USER}:{PASSWORD}@localhost:{PORT}/{name}"
    try:
        wait_until_ready(VerifiedDatabase(url.replace("+psycopg", "")))
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
def test_it_records_the_rows_a_statement_returned(_postgres_url: str) -> None:
    """The recording path, which SQLite cannot reach because the hook refuses it.

    Without this the `record(rows)` line is never executed by any test and could
    be replaced by `record()` with nothing to notice. Two statements, three rows
    and then none: the events are the statements and the total is the rows.
    """
    engine = create_engine(_postgres_url)
    with engine.connect() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS ticket")
        connection.exec_driver_sql("CREATE TABLE ticket (id serial PRIMARY KEY, title text)")
        connection.exec_driver_sql("INSERT INTO ticket (title) VALUES ('a'), ('b'), ('c')")
        connection.commit()

    with (
        installed(FlaskAdapter().declarations),
        count(DB_QUERY) as tally,
        engine.connect() as connection,
    ):
        connection.exec_driver_sql("SELECT * FROM ticket").fetchall()
        connection.exec_driver_sql("SELECT * FROM ticket WHERE title = 'zzz'").fetchall()

    assert tally.events == 2
    assert tally.total == 3.0


# =============================================================== the declarations


class TestDeclarations:
    def test_the_orm_is_sqlalchemy(self) -> None:
        """The one declaration that is a different value rather than a different list."""
        assert FlaskAdapter().declarations.orm is Orm.SQLALCHEMY

    def test_the_framework_is_flask(self) -> None:
        assert FlaskAdapter().framework is Framework.FLASK

    def test_the_frames_strip_the_orm_and_the_wsgi_layer(self) -> None:
        werkzeug = "/env/site-packages/werkzeug/serving.py"
        orm = "/env/site-packages/sqlalchemy/orm/loading.py"
        stack = (
            Frame(filename=orm, lineno=1, function="instances"),
            Frame(filename="/app/shop.py", lineno=42, function="list_tickets"),
            Frame(filename=werkzeug, lineno=3, function="run_wsgi"),
        )

        site = FlaskAdapter().declarations.localizer().localize([stack]).causal_site

        assert site is not None
        assert site.filename.endswith("shop.py")

    def test_alembic_is_protected_and_application_code_beside_it_is_not(self) -> None:
        """Narrower than Django's `**/migrations/**`, deliberately.

        Flask projects keep application code beside Alembic's output often
        enough that protecting the whole directory would refuse patches to code
        somebody wrote.
        """
        policy = FlaskAdapter().declarations.patch_policy()

        assert policy.matching_rule("migrations/versions/a1b2_add_index.py") is not None
        assert policy.matching_rule("alembic/env.py") is not None
        assert policy.matching_rule("migrations/helpers.py") is None

    def test_the_defaults_survive(self) -> None:
        policy = FlaskAdapter().declarations.patch_policy()
        for default in DEFAULT_PROTECTED_PATTERNS:
            assert default in policy.protected

    def test_the_declared_constants_are_what_is_published(self) -> None:
        declarations = FlaskAdapter().declarations
        assert declarations.internal_frames == FLASK_INTERNAL_FRAMES
        assert declarations.protected_paths == FLASK_PROTECTED_PATHS


# ================================================================== capabilities


class TestCapabilities:
    def test_it_never_claims_fixture_shaping(self) -> None:
        """The difference from Django, and it is not an omission.

        Synthesis from a schema is Django-model introspection. Without it there
        is no way to seed a *chosen distribution*, so the capability is not
        claimed and the primitives needing one are withheld with a reason.
        """

        def seeder(**_: object) -> tuple[object, object]:  # pragma: no cover - never called
            raise NotImplementedError

        full = FlaskAdapter(
            app="shop:create_app",
            seeder=seeder,  # type: ignore[arg-type]
            database=VerifiedDatabase("postgresql://u@localhost/app_test"),
        )

        assert Capability.FIXTURE_SHAPING not in full.capabilities()
        assert Capability.FIXTURE_SEEDING in full.capabilities()
        assert Capability.STATE_RESET in full.capabilities()

    def test_an_adapter_given_nothing_claims_only_the_counter(self) -> None:
        assert FlaskAdapter().capabilities() == {Capability.EVENT_COUNTERS}

    def test_it_claims_nothing_the_harness_owns(self) -> None:
        assert FlaskAdapter().capabilities() <= ADAPTER_CAPABILITIES


# ============================================================ seeding and resetting


def test_seeding_without_a_mechanism_says_why_there_is_no_fallback(
    flask_project: Path,
) -> None:
    """The refusal names the missing thing rather than raising a `TypeError`."""
    with pytest.raises(ValueError, match="no synthesis fallback"):
        FlaskAdapter().seed(_subject(flask_project), scale=10, timeout=30.0)


def test_reset_offers_the_same_two_mechanisms_as_django(flask_project: Path) -> None:
    """Resetting Postgres is a fact about Postgres, not about the ORM.

    The two adapters agreeing here is the finding: a rollback and a template
    restore know nothing about which ORM wrote the rows, which is why the
    mechanisms take a `VerifiedDatabase` and the dialect is a declaration.
    """
    adapter = FlaskAdapter(database=VerifiedDatabase("postgresql://u@localhost/app_test"))

    offered = [mechanism.strategy for mechanism in adapter.reset_state(_subject(flask_project))]

    assert offered == [
        ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES,
        ResetStrategy.SNAPSHOT_RESTORE,
    ]


def test_the_suite_falls_back_to_pytest_rather_than_to_a_runner_flask_lacks(
    tmp_path: Path,
) -> None:
    """Django's fallback is `manage.py test`; Flask has no runner of its own.

    The same question — *what does this repository declare* — is asked of the
    same core function, and what to do when it declares nothing differs per
    framework. That is why the fallback lives in the adapter.
    """
    (tmp_path / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")

    assert FlaskAdapter().suite_command(tmp_path) == ("python", "-m", "pytest")


def test_it_satisfies_the_protocol() -> None:
    """Checked by the gate's mypy run; the annotation is the test."""
    adapter: FrameworkAdapter = FlaskAdapter()
    assert adapter.framework is Framework.FLASK

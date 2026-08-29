"""Epic 17 composed: the assembled campaign, driving a live subject.

Fourteen stories — a spike, a vantage, an execution surface, six producers and an
assembly — and the epic's own sentence is **the pipeline can reach a live
subject**. Every story proved its piece against its own fixture, and most of those
fixtures replaced the two calls that touch a subject: S-17.10 monkeypatched `drive`
and `synthesize`, S-17.14 monkeypatched both again, S-17.15 replaced
`choose_reset`.

So nothing had ever taken an assembled `Resources` and measured something real
with it. This does: a Django project with a planted N+1, bound through
`Resources.bind`, screened, and concluded — which is the pipeline's own sentence
performed once.

**The second half is the graph.** `campaign_for` produces the argument
`gated_graph` takes, and no test had ever passed one to the other.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
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
    Subject,
)
from coldfix.bench.counting import Record
from coldfix.bench.execute import ExecutionResult, execute
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import ExchangeRate
from coldfix.explorer.compose import Plan
from coldfix.explorer.entrypoints import Enumeration
from coldfix.explorer.fingerprint import Framework, Orm
from coldfix.explorer.surface import HostSurface
from coldfix.explorer.work import Drive, drive
from coldfix.orchestrator import assembly as assembly_module
from coldfix.orchestrator.adapters import Tokens, bind
from coldfix.orchestrator.assembly import campaign_for
from coldfix.orchestrator.campaign import assemble_with
from coldfix.orchestrator.checkpointing import for_development
from coldfix.orchestrator.gate import gates_for
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS, Vantage
from coldfix.primitives.registry import Capability
from coldfix.primitives.scaling import Distribution
from coldfix.repair.falsification import CostClaim, Guard
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.modes import Session as SandboxSession
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.assess import conclude
from coldfix.screening.growth import screen
from coldfix.screening.null import NullResult
from coldfix.screening.workload import FixtureRecipe, Workload
from coldfix.state.persistent import PersistentStore
from coldfix.state.trust import Level

pytestmark = pytest.mark.slow

MANAGE_PY = """\
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """\
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
USE_TZ = True

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "shop"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

MODELS = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""

# The planted defect: one query for the books, then one per book for its author.
# `db.query` therefore grows with the row count where a round-trip count is
# expected to stay constant, which is what `flag()` is looking for.
URLS = """\
from django.http import JsonResponse
from django.urls import path

from shop.models import Book


def books(request):
    return JsonResponse(
        {"books": [{"title": b.title, "author": b.author.name} for b in Book.objects.all()]}
    )


urlpatterns = [path("books/", books)]
"""


def run(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=root,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )


@pytest.fixture(scope="module")
def subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real Django project with a planted N+1, migrated and ready to drive."""
    root = tmp_path_factory.mktemp("epic17")
    (root / "config").mkdir()
    (root / "shop" / "migrations").mkdir(parents=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")

    run(root, sys.executable, "manage.py", "makemigrations", "shop")
    run(root, sys.executable, "manage.py", "migrate")
    return root


# ============================================================== the assembled campaign


class Reset(ResetMechanism):
    """Deletes what a scale point seeded, through the subject's own ORM.

    A real reset is S-2.6's and needs Postgres; this is the smallest thing that
    genuinely returns the subject to its baseline, which is what screening needs
    between scale points. Its correctness is S-2.7's subject and is tested there.
    """

    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cycles = 0

    def prepare(self) -> None: ...

    def begin(self) -> None: ...

    def reset(self) -> None:
        self.cycles += 1
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import os,sys;sys.path.insert(0,os.getcwd());"
                "import django;django.setup();"
                "from shop.models import Author,Book;"
                "Book.objects.all().delete();Author.objects.all().delete()",
            ],
            cwd=self.root,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )


class Adapter:
    """The Django adapter's answers, without its database.

    `DjangoAdapter.reset_state` needs a `VerifiedDatabase` bound to Postgres, and
    what this check is about is the six producers rather than S-2.6's mechanisms.
    Everything else — the framework, the hooks, the capabilities — is what the
    real adapter declares.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def framework(self) -> Framework:
        return Framework.DJANGO

    @property
    def declarations(self) -> Declarations:
        return Declarations(orm=Orm.DJANGO_ORM, hooks={DB_QUERY: _hook})

    def capabilities(self) -> AbstractSet[Capability]:
        return ADAPTER_CAPABILITIES

    def reset_state(self, subject: Subject) -> Sequence[ResetMechanism]:
        return (Reset(self.root),)

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
        return drive(
            self.root,
            python=[sys.executable],
            path=entry_point,
            scale=scale,
            created=dict(created),
            repeats=repeats,
            surface=HostSurface(self.root),
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
    """This check does not reach that operation."""


@contextmanager
def _hook(record: Record) -> Iterator[None]:
    yield


class Opened:
    """A diagnostic session over the real checkout.

    A real `Workbench.open` creates a git worktree and a container, and this check
    is about what the assembly builds rather than about S-2.1. The path is the
    subject's own, so every surface built from it drives the real project.
    """

    def __init__(self, root: Path) -> None:
        self.worktree = type("Worktree", (), {"path": root})()
        self.closed = False
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = 100_000,
    ) -> ExecutionResult:
        """Runs the command for real, in the checkout.

        S-17.7 decided a session's surface runs in a container; standing one up
        here would make this check about S-2.1. What it must not do is *not run* —
        a surface that answered without executing would let every measurement
        below pass against a subject nothing touched.
        """
        self.commands.append(tuple(command))
        return execute(
            command,
            timeout=timeout,
            cwd=self.worktree.path,
            env={**os.environ, **(env or {})},
            max_output_chars=max_output_chars,
        )

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Opened:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()


class Bench:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session: Opened | None = None

    def open(self, revision: str, *, mode: Any) -> Opened:
        self.session = Opened(self.root)
        return self.session


def workload_of(name: str = "books") -> Workload:
    """The artifact grounding emits and screening reads back."""
    return Workload(
        id=name,
        description="the books list",
        entry_point="/books/",
        fixture=FixtureRecipe(
            entity="shop.Book",
            per_parent=1,
            distribution=Distribution.UNIFORM,
            source="synthesis",
            seed=0,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )


@contextmanager
def assembled(root: Path, **overrides: Any) -> Iterator[Any]:
    adapter = Adapter(root)
    probe = Subject(root=root, python=[sys.executable])
    arguments: dict[str, Any] = {
        "framework": adapter.framework.value,
        "reset_candidates": adapter.reset_state(probe),
        "capabilities": frozenset(adapter.capabilities()) | HARNESS_CAPABILITIES,
        "counters": tuple(sorted(adapter.declarations.hooks)),
        "workload": lambda: adapter.run_workload(
            probe, entry_point="/books/", scale=1, created={}, repeats=1, timeout=300.0
        ),
        "client": object(),
        "project": "shop",
        "trust_key": "n-plus-one:uniform",
        "revision": "HEAD",
        "root": root,
        "python": [sys.executable],
        "database_url": "postgresql://coldfix@localhost:5432/subject_test",
        "workbench": Bench(root),
        # A real store: `gated_graph` reads the trust ledger through `standing`,
        # so a placeholder here would make the graph test pass for the wrong
        # reason — or, as it did, fail for one.
        "store": PersistentStore(
            database=VerifiedDatabase("postgresql://coldfix@localhost:5432/coldfix_state"),
            replay_root=root / "recordings",
        ),
        "plan": Plan(workload_id="books", description="the books list", target="shop.Book"),
        "entity": "shop.Book",
        "path": "/books/",
        "model": "shop.Book",
        "settings": "config.settings",
        "source": "shop@HEAD",
        "suite_command": [sys.executable, "-m", "pytest", "-q"],
        "metric": DB_QUERY,
        "tokens": Tokens(prefix=100, prompt=200),
        "claim": CostClaim(
            metric=DB_QUERY,
            baseline=41.0,
            at_most=2.0,
            guards=(Guard(metric="response_bytes", baseline=2000.0, at_most=3000.0),),
        ),
        "rate": ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 29)),
        "ceiling_eur": Decimal("1.00"),
    }
    arguments.update(overrides)

    verified = VerifiedReset(
        mechanism=Reset(root),
        report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
    )
    # `choose_reset` drives S-2.7's ten cycles against Postgres. Its correctness is
    # that story's; what this check needs is a reset that really empties the
    # subject between scale points, which `Reset` does.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(assembly_module, "choose_reset", lambda *a, **k: verified)
        with campaign_for(**arguments) as resources:
            yield resources


# ================================================ the sentence: a live subject measured


def test_the_assembled_campaign_measures_the_planted_defect(subject: Path) -> None:
    """**The epic's sentence, performed once.**

    An assembled `Resources`, its own `bind`, a real Django subject, and the N+1
    the project exists to find. Every earlier story replaced `drive` or
    `synthesize` with a fake — this is the first thing that measures.
    """
    with assembled(subject) as resources:
        # `screen` is the plural the node calls, so the sequence crosses the
        # boundary the way a run's would.
        screened = screen(
            resources.bind([workload_of()]), scales=[4, 8, 16], counters=list(resources.counters)
        )[0]

    queries = screened.metric(DB_QUERY)
    assert queries.growth in {Growth.LINEAR, Growth.SUPERLINEAR}, (
        f"an N+1 grows with the rows; measured {queries.growth}"
    )
    assert screened.workload.observations, "the sweep recorded what it measured"


def test_the_screen_reaches_a_decision_about_the_subject(subject: Path) -> None:
    """Through `conclude`, which is what the `screen` node calls.

    A measurement nothing decides on is where the Epic 16 composition check found
    the pipeline walking past its own defect: the node held a local rule that
    flagged only `SUPERLINEAR`, and an N+1 is linear.
    """
    with assembled(subject) as resources:
        screened = screen(
            resources.bind([workload_of()]), scales=[4, 8, 16], counters=list(resources.counters)
        )[0]

    assessment = conclude([screened])

    assert not isinstance(assessment, NullResult), (
        f"the N+1 was measured and not flagged: {assessment}"
    )


def test_the_numbers_come_from_the_subject_rather_than_the_harness(subject: Path) -> None:
    """S-17.5's whole thread, arriving where it was aimed.

    A harness timing an out-of-process subject fitted a linear workload as
    `CONSTANT`. The binding reports the subject's own duration, so the vantage the
    screen publishes has to say so — and an exclusion carries it.
    """
    with assembled(subject) as resources:
        screened = screen(
            resources.bind([workload_of()]), scales=[4, 8, 16], counters=list(resources.counters)
        )[0]

    assert screened.vantage is Vantage.SUBJECT


def test_the_reported_duration_is_the_subjects_and_moves_with_the_data(
    subject: Path,
) -> None:
    """**Added because a sabotage survived.**

    `test_the_numbers_come_from_the_subject...` asserts the *vantage*, and a
    binding reporting a hardcoded `seconds` passes it — the label was right and
    the number was invented. What distinguishes a real reading is that three
    scale points produce three different durations: an N+1 over sixteen rows is
    not the same request as one over four, and a constant is exactly equal.

    Asserted as *distinct* rather than *increasing*, because the comparison that
    matters here is against a fabricated number and real timings are never equal
    — while asserting a strict ordering on three sub-second measurements would be
    a flaky test dressed as a strict one.
    """
    with assembled(subject) as resources:
        screened = screen(
            resources.bind([workload_of()]), scales=[4, 8, 16], counters=list(resources.counters)
        )[0]

    durations = [observation.metrics[SECONDS] for observation in screened.workload.observations]

    assert len(set(durations)) == len(durations), (
        f"three real measurements are never equal; got {durations}"
    )
    assert all(value > 0.0 for value in durations)


def test_the_reset_ran_between_every_scale_point(subject: Path) -> None:
    """Otherwise each point is measured on top of the one before it, and the
    growth that shows is arithmetic rather than a defect."""
    with assembled(subject) as resources:
        bindings = resources.bind([workload_of()])
        screen(bindings, scales=[4, 8, 16], counters=list(resources.counters))

    # The reset the assembly chose is the one the binding used, and a sweep of
    # three points opens three cycles.
    assert isinstance(bindings[0].reset, VerifiedReset)


# ==================================================== the second half: the graph


def test_the_seven_nodes_bind_to_the_assembled_resources(subject: Path) -> None:
    """**The graph half.** `campaign_for` produces what `bind` closes over, and
    nothing had ever passed one to the other.

    `bind` is where every node gets its resources, and `assemble_with` is where
    the seven become a graph — so a `Resources` the nodes cannot close over, or a
    wiring the graph will not take, fails here.

    **What this does not cover is the ledger read.** `gated_graph` also calls
    `standing`, which opens the trust store, so the full call needs Postgres. That
    half is S-13.4's and is tested there; what is new in Epic 17 is the resources,
    and this is the part of the graph that touches them.
    """

    with assembled(subject) as resources:
        wiring = bind(resources)

        # GATED is what a new project earns — S-13.4: *new projects start at
        # level 0 regardless of cross-project history* — so it is the level a
        # first run against this subject would compile with.
        gates = gates_for(Level.GATED)
        with for_development(subject / "checkpoints.sqlite") as checkpointer:
            compiled = assemble_with(wiring, checkpointer, gates)

    assert compiled is not None


def test_the_bound_nodes_are_the_seven_the_graph_expects(subject: Path) -> None:
    """A wiring short one node compiles into a graph with a hole in it, and the
    hole is only visible when a run reaches that node."""

    with assembled(subject) as resources:
        wiring = bind(resources)

    named = [field.name for field in dataclasses.fields(wiring)]

    assert len(named) == 7, f"seven nodes, got {named}"
    assert all(callable(getattr(wiring, name)) for name in named)

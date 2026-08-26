"""AC 2: core code unchanged, asserted by running both adapters through one pipeline.

S-14.3. Two claims are made here and they are checked differently.

**The pipeline treats them identically.** `run_pipeline` calls core APIs and the
Protocol and nothing else, and it is parametrized over both adapters. A test
reads its own source back and asserts it names neither framework — so the day
somebody makes it work by branching, the branch is the failure.

**Nothing in the core imports an adapter.** Walked over `src/coldfix/` with `ast`
rather than grepped, because a docstring that mentions the package is not an
import and the difference is the whole assertion. This is the invariant that
breaks first when a framework problem is solved in the wrong place, and it is
the mechanical form of *core code unchanged*.

What is **not** claimed: that the grounding sequence runs on Flask. It does not —
`explorer/compose.py` calls `enumerate_entry_points` directly, `stages.PREDICATES`
has one entry and `Framework.supported` is Django only. Those are honest today,
and ADR 148 records what routing them through an adapter would take.
"""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from coldfix.adapters import Declarations, FrameworkAdapter, Subject, installed
from coldfix.adapters.django import DjangoAdapter
from coldfix.adapters.flask import FlaskAdapter
from coldfix.bench.counting import registered_hooks
from coldfix.bench.execute import DEFAULT_MAX_OUTPUT_CHARS, ExecutionResult
from coldfix.explorer.entrypoints import Enumeration, Kind
from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.localization import Frame
from coldfix.primitives.registry import (
    Capability,
    CostClass,
    Primitive,
    ProjectProfile,
    Registry,
    Selection,
)
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.patching import DEFAULT_PROTECTED_PATTERNS, PatchPolicy
from coldfix.sandbox.reset import ResetMechanism
from coldfix.sandbox.worktrees import Worktree

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "coldfix"


class _Session(CandidateSession):
    """A candidate session over a directory, recording what it was asked to do."""

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
        return frozenset({"subject/views.py"})


@dataclass(frozen=True)
class Ran:
    """What one pass of the pipeline produced."""

    declarations: Declarations
    policy: PatchPolicy
    hooks: tuple[str, ...]
    selection: Selection
    enumeration: Enumeration
    resets: Sequence[ResetMechanism]
    causal_site: str | None
    suite: tuple[str, ...]
    sources: Mapping[str, str]
    written: frozenset[str]


def run_pipeline(adapter: FrameworkAdapter, subject: Subject, instruments: Registry) -> Ran:
    """Every operation the interface offers, driven with core code only.

    This function is the assertion. It knows the Protocol, the counter
    catalogue, the primitive registry, the patch policy and the localizer, and it
    knows nothing whatever about which framework it was handed. A test reads its
    source back and fails if either framework's name appears in it.

    The stack it localizes is a dependency frame followed by one of the subject's
    own. Which paths belong to a dependency is the adapter's declaration; that
    the subject's frame is the answer is the core's rule.
    """
    declarations = adapter.declarations
    session = _Session(subject.root)

    with installed(declarations):
        hooks = registered_hooks()

    stack = (
        Frame(filename="/env/site-packages/whatever/loading.py", lineno=1, function="load"),
        Frame(filename=str(subject.root / "subject" / "views.py"), lineno=9, function="index"),
    )
    localized = declarations.localizer().localize([stack])

    return Ran(
        declarations=declarations,
        policy=declarations.patch_policy(),
        hooks=hooks,
        selection=instruments.select(ProjectProfile(capabilities=adapter.capabilities())),
        enumeration=adapter.discover_workloads(subject, timeout=120.0),
        resets=adapter.reset_state(subject),
        causal_site=localized.causal_site.filename if localized.causal_site else None,
        suite=tuple(adapter.run_tests(session, timeout=30.0).command),
        sources=adapter.read_source(session),
        written=adapter.apply_patch(session, "--- a/subject/views.py\n"),
    )


# ============================================================== the two subjects

DJANGO_URLS = """
from django.urls import path

from . import views

urlpatterns = [
    path("tickets/", views.list_tickets, name="tickets"),
    path("health/", views.health, name="health"),
]
"""

FLASK_APPLICATION = """
from flask import Flask

app = Flask(__name__)


@app.route("/tickets")
def list_tickets():
    return {"tickets": []}


@app.route("/health")
def health():
    return {"ok": True}
"""

VIEWS = """
def list_tickets(request=None):
    return None


def health(request=None):
    return None
"""

TEMPLATE = "<ul>{% for t in tickets %}<li>{{ t }}</li>{% endfor %}</ul>\n"


def _subject_tree(root: Path, *, entry: str, body: str, manifest: str) -> Subject:
    """A repository with one package, one entry-point file and one template."""
    package = root / "subject"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / entry).write_text(body, encoding="utf-8")
    (package / "views.py").write_text(VIEWS, encoding="utf-8")
    (package / "index.html").write_text(TEMPLATE, encoding="utf-8")
    (root / "requirements.txt").write_text(manifest, encoding="utf-8")
    return Subject(root=root, python=[sys.executable])


@pytest.fixture
def django_subject(tmp_path: Path) -> Subject:
    root = tmp_path / "dj"
    subject = _subject_tree(root, entry="urls.py", body=DJANGO_URLS, manifest="django>=5.0\n")
    (root / "manage.py").write_text(
        'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "subject.settings")\n',
        encoding="utf-8",
    )
    return subject


@pytest.fixture
def flask_subject(tmp_path: Path) -> Subject:
    root = tmp_path / "fl"
    return _subject_tree(root, entry="app.py", body=FLASK_APPLICATION, manifest="flask>=3.0\n")


def _instruments() -> Registry:
    """Two primitives whose capabilities the two adapters answer differently."""
    registry = Registry()
    registry.register(
        Primitive(
            name="observation.queries",
            summary="count statements while the workload runs",
            cost=CostClass.SECONDS,
            run=lambda workload: None,
            required_capabilities={Capability.EVENT_COUNTERS},
        )
    )
    registry.register(
        Primitive(
            name="scaling.skewed",
            summary="seed a long-tailed fixture and fit growth against it",
            cost=CostClass.MINUTES,
            run=lambda workload: None,
            required_capabilities={Capability.FIXTURE_SHAPING},
        )
    )
    return registry


@pytest.fixture
def adapters(
    django_subject: Subject, flask_subject: Subject
) -> dict[str, tuple[FrameworkAdapter, Subject]]:
    """Both adapters, each with a repository of its own framework.

    Keyed by a label rather than by `Framework`, so that the parametrized tests
    below read as *two adapters* rather than as two named frameworks.
    """
    return {
        "first": (DjangoAdapter(target="subject.Ticket"), django_subject),
        "second": (FlaskAdapter(app="subject.app:app"), flask_subject),
    }


# =============================================================== the same pipeline


@pytest.mark.parametrize("which", ["first", "second"])
class TestOnePipelineTwoAdapters:
    """Every assertion holds for both, with no branch on which is which."""

    def test_the_pipeline_completes(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        adapter, subject = adapters[which]
        assert run_pipeline(adapter, subject, _instruments()) is not None

    def test_the_defaults_are_still_protected(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        ran = run_pipeline(*adapters[which], _instruments())
        for default in DEFAULT_PROTECTED_PATTERNS:
            assert default in ran.policy.protected

    def test_the_counter_is_registered_under_the_catalogue_name(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        """One name, two mechanisms. A wrapper and an event listener land here
        indistinguishably, which is what lets a primitive ask for `db.query`."""
        ran = run_pipeline(*adapters[which], _instruments())
        assert DB_QUERY in ran.hooks
        assert DB_QUERY not in registered_hooks()

    def test_the_causal_site_is_the_subjects_own_frame(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        ran = run_pipeline(*adapters[which], _instruments())
        assert ran.causal_site is not None
        assert ran.causal_site.endswith("views.py")

    def test_both_route_tables_are_found_and_ranked(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        """Two entirely different discovery mechanisms, one ranked `Enumeration`.

        One asks the framework and falls back to reading `path()` calls; the
        other reads decorators. `rank` scores what either produced, and the
        collection route wins in both.
        """
        ran = run_pipeline(*adapters[which], _instruments())
        routes = {candidate.name for candidate in ran.enumeration.of_kind(Kind.HTTP_ROUTE)}

        assert any("tickets" in route for route in routes)
        assert "tickets" in ran.enumeration.scored[0].candidate.name

    def test_the_counting_primitive_is_offered(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        ran = run_pipeline(*adapters[which], _instruments())
        assert "observation.queries" in ran.selection.names

    def test_the_suite_command_runs_in_the_session(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        ran = run_pipeline(*adapters[which], _instruments())
        assert ran.suite[0] == "python"
        assert len(ran.suite) > 1

    def test_the_sources_include_the_template(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        """Both frameworks render templates, and both adapters read them."""
        ran = run_pipeline(*adapters[which], _instruments())
        assert "subject/index.html" in ran.sources
        assert "subject/views.py" in ran.sources

    def test_the_write_went_through_the_session(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        ran = run_pipeline(*adapters[which], _instruments())
        assert ran.written == frozenset({"subject/views.py"})

    def test_no_database_means_no_reset_candidates(
        self, which: str, adapters: dict[str, tuple[FrameworkAdapter, Subject]]
    ) -> None:
        """Neither invents one, and `choose_reset` refuses an empty list."""
        ran = run_pipeline(*adapters[which], _instruments())
        assert ran.resets == ()


# ====================================================== where they legitimately differ


def test_the_capability_difference_withholds_a_primitive_with_a_reason(
    adapters: dict[str, tuple[FrameworkAdapter, Subject]],
) -> None:
    """The adapters disagree, and the core turns that into a withholding.

    One can seed a chosen distribution and the other cannot, because synthesis
    from a schema exists for one ORM and not the other. The consequence is not an
    error at the point of seeding: the primitive is **not offered**, and the
    notice says which capability was missing. That is the tri-state applicability
    design doing the job it was built for, across two adapters for the first
    time.
    """
    first = run_pipeline(*adapters["first"], _instruments())
    second = run_pipeline(*adapters["second"], _instruments())

    assert "scaling.skewed" in first.selection.names
    assert "scaling.skewed" not in second.selection.names

    withheld = {entry.primitive.name: entry.verdict.reason for entry in second.selection.withheld}
    assert "chosen distribution" in withheld["scaling.skewed"]


def test_the_two_adapters_declare_different_orms(
    adapters: dict[str, tuple[FrameworkAdapter, Subject]],
) -> None:
    """If they agreed, the second adapter would not be a second case."""
    first, second = adapters["first"][0], adapters["second"][0]
    assert first.declarations.orm is not second.declarations.orm


# =========================================================== core code unchanged


def test_the_pipeline_names_neither_framework() -> None:
    """The mechanical form of *the same pipeline*.

    Making a shared pipeline pass by branching on the framework is the obvious
    way to satisfy AC 2 without satisfying it, and it is invisible in a diff that
    also adds a legitimate adapter. This reads the function's own source.
    """
    source = inspect.getsource(run_pipeline).lower()

    assert "django" not in source
    assert "flask" not in source


def test_no_core_module_imports_an_adapter() -> None:
    """The import direction, checked with `ast` so a docstring is not an import.

    Adapters import the core; the core must never import an adapter. This is the
    invariant that breaks first when a framework-specific problem gets solved in
    a framework-agnostic file, and once broken it is very hard to see: everything
    still works, and the layering is gone.

    `orchestrator/adapters.py` is deliberately in scope despite its name — it is
    the LangGraph node adapters and has nothing to do with this package, which is
    exactly why it is worth checking rather than exempting.
    """
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT)
        if relative.parts[0] == "adapters":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "coldfix.adapters"
            ):
                offenders.append(f"{relative}: from {node.module}")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"{relative}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("coldfix.adapters")
                )

    assert offenders == []


def test_every_adapter_module_satisfies_the_protocol() -> None:
    """Both, by annotation, so mypy checks the pair rather than one at a time."""
    both: list[FrameworkAdapter] = [DjangoAdapter(), FlaskAdapter()]
    assert len({adapter.framework for adapter in both}) == 2

"""S-7.3 — every way into a repository, and what order to try them in.

Built against real files on disk and, for the resolution half, against a real
Django. AC 3 is a claim about what a parser *cannot* see, and a fake resolver
would only report what this file already believes it would — the failure S-0.7b
recorded as *a test double more forgiving than the real thing turns a structural
assertion into a decoration*.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from coldfix.explorer.entrypoints import (
    Candidate,
    Discovery,
    Enumeration,
    Kind,
    enumerate_entry_points,
    parse_entry_points,
    rank,
    resolve_entry_points,
    score,
    settings_module,
)

MANAGE_PY = """\
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """\
SECRET_KEY = "not-a-secret"
DEBUG = True
ROOT_URLCONF = "config.urls"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "shop"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
USE_TZ = True
"""

# Three literal routes a parse can read, and a set of routes built in a loop
# that it cannot. The loop is what a DRF router is underneath: a list of names
# turned into URL patterns when the module imports, present in the route table
# and absent from the file as text.
URLS = """\
from django.http import HttpResponse
from django.urls import include, path

from shop import views

DYNAMIC = ["widgets", "gadgets", "sprockets"]


def stub(request, **kwargs):
    return HttpResponse("ok")


urlpatterns = [
    path("books/", views.book_list, name="book-list"),
    path("books/<int:pk>/", views.book_detail, name="book-detail"),
    path("health/", views.health, name="health"),
    path("archives/", views.order_list),
    path("api/", include("shop.api")),
]

urlpatterns += [path(f"{name}/", stub, name=name) for name in DYNAMIC]
"""

API_URLS = """\
from django.urls import path

from shop import views

urlpatterns = [
    path("orders/", views.order_list, name="order-list"),
]
"""

VIEWS = """\
from django.http import HttpResponse


def book_list(request):
    return HttpResponse("books")


def book_detail(request, pk):
    return HttpResponse("book")


def health(request):
    return HttpResponse("ok")


def order_list(request):
    return HttpResponse("orders")
"""

TASKS = """\
from celery import shared_task


@shared_task
def rebuild_index():
    return 1


@shared_task(bind=True)
def send_receipts(self):
    return 2


def not_a_task():
    return 3
"""


def django_project(root: Path) -> Path:
    """A minimal but real Django project: it imports, configures and resolves."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "management" / "commands").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")

    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "views.py").write_text(VIEWS, encoding="utf-8")
    (root / "shop" / "api.py").write_text(API_URLS, encoding="utf-8")
    (root / "shop" / "tasks.py").write_text(TASKS, encoding="utf-8")
    (root / "shop" / "management" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "rebuild_reports.py").write_text(
        "from django.core.management.base import BaseCommand\n\n\n"
        "class Command(BaseCommand):\n    def handle(self, *args, **options):\n        pass\n",
        encoding="utf-8",
    )
    (root / "shop" / "management" / "commands" / "_private.py").write_text("", encoding="utf-8")

    (root / "tests" / "test_integration_orders.py").write_text("def test_x():\n    pass\n", "utf-8")
    (root / "tests" / "test_units.py").write_text("def test_y():\n    pass\n", encoding="utf-8")

    (root / "pyproject.toml").write_text(
        '[project]\nname = "shop"\nversion = "0"\n\n[project.scripts]\nshopctl = "shop.cli:main"\n',
        encoding="utf-8",
    )
    return root


def route_named(enumeration: Enumeration, name: str) -> Candidate | None:
    return next((c for c in enumeration.candidates if c.name == name), None)


def names(candidates: Sequence[Candidate]) -> set[str]:
    return {candidate.name for candidate in candidates}


# =============================================== AC 1: it enumerates all five kinds


def test_all_five_kinds_are_enumerated(tmp_path: Path) -> None:
    """AC 1, verbatim: routes, CLI entry points, management commands, job
    handlers and integration tests."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    for kind in Kind:
        assert found.of_kind(kind), f"nothing enumerated for {kind.value}"


def test_literal_routes_are_read_with_the_file_that_declared_them(tmp_path: Path) -> None:
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    books = route_named(found, "books/")
    assert books is not None
    assert books.kind is Kind.HTTP_ROUTE
    assert books.evidence == "config/urls.py"
    assert books.target == "book_list"
    assert books.discovery is Discovery.PARSED


def test_a_management_command_carries_the_application_that_owns_it(tmp_path: Path) -> None:
    """Which application owns it is what separates the subject's batch job from
    `collectstatic`, and the ranking spends it."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    commands = found.of_kind(Kind.MANAGEMENT_COMMAND)
    assert names(commands) == {"rebuild_reports"}
    assert commands[0].owner == "shop"


def test_a_private_module_is_not_a_management_command(tmp_path: Path) -> None:
    """Django does not expose `_private.py` as a command and neither does this."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    assert "_private" not in names(found.of_kind(Kind.MANAGEMENT_COMMAND))


def test_job_handlers_are_found_by_decorator_in_both_spellings(tmp_path: Path) -> None:
    """`@shared_task` and `@shared_task(bind=True)` are the same declaration."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    jobs = found.of_kind(Kind.JOB_HANDLER)
    assert names(jobs) == {"rebuild_index", "send_receipts"}
    assert all(job.evidence.startswith("shop/tasks.py:") for job in jobs)


def test_console_scripts_are_read_from_the_manifest(tmp_path: Path) -> None:
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    scripts = found.of_kind(Kind.CLI_ENTRY_POINT)
    assert names(scripts) == {"shopctl"}
    assert scripts[0].target == "shop.cli:main"


def test_migrations_and_virtualenvs_are_not_walked(tmp_path: Path) -> None:
    """An installed virtualenv holds a `urls.py` for every package in it, and a
    mature project holds thousands of migrations. Neither is a way in."""
    root = django_project(tmp_path / "subject")
    (root / ".venv" / "lib" / "site-packages" / "rest_framework").mkdir(parents=True)
    (root / ".venv" / "lib" / "site-packages" / "rest_framework" / "urls.py").write_text(
        'from django.urls import path\n\nurlpatterns = [path("borrowed/", None)]\n',
        encoding="utf-8",
    )
    (root / "shop" / "migrations").mkdir()
    (root / "shop" / "migrations" / "0001_initial.py").write_text(
        'from django.urls import path\n\nurlpatterns = [path("migrated/", None)]\n',
        encoding="utf-8",
    )

    found = enumerate_entry_points(root)

    assert "borrowed/" not in names(found.candidates)
    assert "migrated/" not in names(found.candidates)


def test_one_unparseable_file_does_not_cost_the_others(tmp_path: Path) -> None:
    """A repository written for a Python this interpreter cannot parse is
    ordinary (`00-BRIEF.md` §3: age is irrelevant)."""
    root = django_project(tmp_path / "subject")
    (root / "shop" / "broken.py").write_text("def (:\n", encoding="utf-8")

    found = enumerate_entry_points(root)

    assert "books/" in names(found.candidates)


# ============================== AC 3: routes that are registered rather than written


def test_a_parse_cannot_expand_a_computed_route_and_says_so(tmp_path: Path) -> None:
    """The honest half of AC 3, for the case where no environment exists yet.

    The loop over `DYNAMIC` produces three routes. A parser sees a `path()` call
    whose pattern is an f-string, which is not a route it can name — and the
    result records the place rather than dropping it silently.
    """
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    assert not found.routes_are_complete
    assert any(
        entry.evidence == "config/urls.py" and "computed pattern" in entry.construct
        for entry in found.unexpanded
    )
    assert "INCOMPLETE" in found.describe()


def test_a_router_registration_is_recorded_as_unexpandable(tmp_path: Path) -> None:
    """A DRF router generates six routes per viewset when the module imports.
    Reading the file finds one call and no routes."""
    root = django_project(tmp_path / "subject")
    (root / "shop" / "routers.py").write_text(
        "from rest_framework.routers import DefaultRouter\n\n"
        "from shop import views\n\n"
        "router = DefaultRouter()\n"
        'router.register("invoices", views.InvoiceViewSet)\n'
        "urlpatterns = router.urls\n",
        encoding="utf-8",
    )

    found = enumerate_entry_points(root)

    assert any(
        entry.evidence == "shop/routers.py" and "router" in entry.construct
        for entry in found.unexpanded
    )


def test_an_include_is_a_mount_point_and_not_a_candidate(tmp_path: Path) -> None:
    """`path("api/", include(...))` is a prefix, not an endpoint — requesting it
    returns 404 — and as a parameterless route it would rank at the very top of
    the list the Explorer works down."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    assert "api/" not in names(found.of_kind(Kind.HTTP_ROUTE))
    assert any("include(...) mounted at 'api/'" in entry.construct for entry in found.unexpanded)


def test_a_parsed_table_is_never_claimed_complete(tmp_path: Path) -> None:
    """Even with nothing dynamic in it. A URLconf is code, and a parse cannot
    establish completeness of something built by running code."""
    root = tmp_path / "plain"
    (root / "app").mkdir(parents=True)
    (root / "app" / "urls.py").write_text(
        'from django.urls import path\n\nurlpatterns = [path("books/", None)]\n', encoding="utf-8"
    )

    found = enumerate_entry_points(root)

    assert found.of_kind(Kind.HTTP_ROUTE)
    assert not found.unexpanded
    assert not found.routes_are_complete


# ===================================== AC 3 measured: the framework's own route table


def test_the_resolver_reports_routes_the_parse_could_not_see(tmp_path: Path) -> None:
    """AC 3, against a real Django rather than an assertion about one.

    The three routes built in the loop are in the resolver's table and in no
    file. This is the whole reason resolution exists, and it is measured by
    comparing the two enumerations of one project.
    """
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    assert found.resolution.available, found.resolution.describe()
    assert found.routes_are_complete
    assert {"widgets/", "gadgets/", "sprockets/"} <= names(found.dynamically_registered)


def test_the_parse_alone_misses_exactly_those_routes(tmp_path: Path) -> None:
    """The control. Without it, the test above shows only that resolution finds
    routes — not that the parse could not."""
    root = django_project(tmp_path / "subject")

    parsed = parse_entry_points(root)

    patterns = {candidate.name for candidate in parsed.candidates}
    assert "books/" in patterns
    assert not {"widgets/", "gadgets/", "sprockets/"} & patterns


def test_an_included_route_reaches_the_resolver_under_its_prefix(tmp_path: Path) -> None:
    """`include()` is the other thing a parse cannot follow: the file says
    `orders/` and the route is `api/orders/`."""
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    resolved = {
        candidate.name
        for candidate in found.of_kind(Kind.HTTP_ROUTE)
        if candidate.discovery is Discovery.RESOLVED
    }
    assert "api/orders/" in resolved
    assert "orders/" in names(parse_entry_points(root).candidates)


def test_an_included_route_is_not_reported_as_dynamically_registered(tmp_path: Path) -> None:
    """The comparison has to survive the prefix, or every included route reads as
    a route the parse missed and the AC 3 result becomes noise."""
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    assert "api/orders/" not in names(found.dynamically_registered)


def test_a_resolved_route_carries_the_view_that_runs(tmp_path: Path) -> None:
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    books = next(
        candidate
        for candidate in found.of_kind(Kind.HTTP_ROUTE)
        if candidate.discovery is Discovery.RESOLVED and candidate.name == "books/"
    )
    assert books.target == "shop.views.book_list"
    assert books.route_name == "book-list"


def test_a_route_the_subject_did_not_name_carries_no_name(tmp_path: Path) -> None:
    """`name=` is optional, and the resolver reports `None` for a route without
    one. Stringified, that becomes the literal `"None"` — a route this system
    would then try to address by a name nothing answers to."""
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    unnamed = next(
        candidate
        for candidate in found.of_kind(Kind.HTTP_ROUTE)
        if candidate.discovery is Discovery.RESOLVED and candidate.name == "archives/"
    )
    assert unnamed.route_name is None


def test_output_printed_during_setup_does_not_break_the_answer(tmp_path: Path) -> None:
    """A settings module that prints is ordinary — a deprecation warning, an
    application banner — and `json.loads(stdout)` on such a project fails on
    output that is not an error."""
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")
    settings = root / "config" / "settings.py"
    settings.write_text('print("configuring the shop")\n' + settings.read_text(), encoding="utf-8")

    found = enumerate_entry_points(root, python=[sys.executable])

    assert found.resolution.available, found.resolution.describe()
    assert found.routes_are_complete


def test_a_subject_that_cannot_be_configured_reports_why_and_keeps_the_parse(
    tmp_path: Path,
) -> None:
    """Resolution needs a stood-up environment and the Explorer routinely
    enumerates before it has one. A failure must not cost the caller the parse."""
    root = django_project(tmp_path / "subject")
    (root / "config" / "settings.py").write_text("import nonexistent_package\n", encoding="utf-8")

    found = enumerate_entry_points(root, python=[sys.executable])

    assert not found.resolution.available
    assert not found.routes_are_complete
    assert "nonexistent_package" in (found.resolution.error or "")
    assert "books/" in names(found.candidates)


def test_an_interpreter_that_does_not_exist_is_reported_rather_than_raised(
    tmp_path: Path,
) -> None:
    """The other way resolution fails, and no test reached it until the sabotage
    pass said so.

    A settings module that will not import is a command that *runs* and exits
    non-zero; an interpreter that is not there cannot be started at all, and
    `execute` raises for that one. Handing the Explorer a wrong path to Python is
    an ordinary mistake, and it must cost the caller a report rather than an
    exception it did not expect from an enumeration.
    """
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=["coldfix-no-such-interpreter"])

    assert not found.resolution.available
    assert not found.routes_are_complete
    assert found.resolution.error
    assert "books/" in names(found.candidates)


def test_a_project_that_names_no_settings_module_is_not_asked(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    (root / "app").mkdir(parents=True)
    (root / "app" / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")

    resolved, resolution = resolve_entry_points(root, python=[sys.executable])

    assert resolved == []
    assert not resolution.available
    assert "DJANGO_SETTINGS_MODULE" in (resolution.error or "")


def test_the_settings_module_is_read_from_the_file_that_sets_it(tmp_path: Path) -> None:
    found = settings_module(django_project(tmp_path / "subject"))

    assert found is not None
    assert found.value == "config.settings"
    assert found.evidence == "manage.py"


def test_a_urlconf_that_will_not_import_answers_without_claiming_completeness(
    tmp_path: Path,
) -> None:
    """The sharpest case for `routes_are_complete`, and it corrected the design.

    `include("shop.api")` imports eagerly, so one broken application's URLconf
    takes the whole table with it — the resolver *answers*, with zero routes and
    one problem. Counted as available-therefore-complete, that reports a
    repository as having no endpoints when what happened is that nobody could
    read them, and ADR 009's *endpoint* predicate would be evaluated against it.
    """
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")
    (root / "shop" / "api.py").write_text("import nonexistent_module\n", encoding="utf-8")

    found = enumerate_entry_points(root, python=[sys.executable])

    assert found.resolution.available, found.resolution.describe()
    assert any("nonexistent_module" in problem for problem in found.resolution.problems)
    assert not found.routes_are_complete
    assert "books/" in names(found.candidates)  # the parse still stands


def test_resolved_management_commands_include_the_frameworks_own(tmp_path: Path) -> None:
    """Which is why they carry an owner: `django.core` is not the subject's work."""
    pytest.importorskip("django")
    root = django_project(tmp_path / "subject")

    found = enumerate_entry_points(root, python=[sys.executable])

    resolved = {
        candidate.name: candidate.owner
        for candidate in found.of_kind(Kind.MANAGEMENT_COMMAND)
        if candidate.discovery is Discovery.RESOLVED
    }
    assert resolved["migrate"] == "django.core"
    assert resolved["rebuild_reports"] == "shop"


# ================================================= AC 2: ranked by likely usefulness


def test_a_collection_route_outranks_the_detail_route_beside_it(tmp_path: Path) -> None:
    """The sharpest rule, and the one anchored on S-7.8: a route addressing one
    object returns one object at N=10 and at N=100, so it cannot show growth."""
    listing = score(Candidate(Kind.HTTP_ROUTE, "books/", "urls.py", Discovery.PARSED))
    detail = score(Candidate(Kind.HTTP_ROUTE, "books/<int:pk>/", "urls.py", Discovery.PARSED))

    assert listing.score > detail.score
    assert any("addresses a set" in reason for reason in listing.reasons)
    assert any("one object at every scale" in reason for reason in detail.reasons)


def test_every_required_parameter_costs_the_same_again(tmp_path: Path) -> None:
    """**The two routes are the same depth on purpose.** Written as `a/<int:pk>/`
    against `a/<int:pk>/b/<slug:tag>/` this test passed with the parameter
    penalty set to zero, because the second is one segment deeper and the depth
    term alone separated them — it asserted the right order for the wrong
    reason. Caught by the sabotage pass, not by review.
    """
    one = score(Candidate(Kind.HTTP_ROUTE, "a/<int:pk>/", "urls.py", Discovery.PARSED))
    two = score(Candidate(Kind.HTTP_ROUTE, "a/<int:pk>/<slug:tag>/", "urls.py", Discovery.PARSED))

    assert one.candidate.parameters == ("pk",)
    assert two.candidate.parameters == ("pk", "tag")
    assert two.score < one.score


def test_a_regular_expression_group_is_a_parameter_too(tmp_path: Path) -> None:
    """`re_path` is how every Django project written before 2.0 spells a route,
    and its parameters are named groups rather than converters."""
    candidate = Candidate(Kind.HTTP_ROUTE, r"^books/(?P<pk>\d+)/$", "urls.py", Discovery.PARSED)

    assert candidate.parameters == ("pk",)


def test_infrastructure_routes_are_enumerated_and_ranked_last(tmp_path: Path) -> None:
    """AC 1 is enumeration and AC 2 is order. Dropping the admin would hide it;
    ranking it last is what says it is not the application's work."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    health = next(entry for entry in found.scored if entry.candidate.name == "health/")
    books = next(entry for entry in found.scored if entry.candidate.name == "books/")

    assert health.candidate in found.candidates
    assert health.score < books.score
    assert any("designed to do no work" in reason for reason in health.reasons)


def test_the_frameworks_own_commands_rank_below_the_subjects(tmp_path: Path) -> None:
    """`collectstatic` is Django's code, which `CLAUDE.md` refuses to patch.

    **The two commands share a name**, so the owner is the only thing that
    differs. Written as `rebuild_reports` against `migrate` this passed with the
    owner rule removed entirely — `reports` is a collection word and the name
    bonus alone separated them. Caught by the sabotage pass.
    """
    ours = score(
        Candidate(Kind.MANAGEMENT_COMMAND, "rebuild_reports", "x", Discovery.RESOLVED, owner="shop")
    )
    theirs = score(
        Candidate(
            Kind.MANAGEMENT_COMMAND, "rebuild_reports", "x", Discovery.RESOLVED, owner="django.core"
        )
    )

    assert theirs.score < ours.score


def test_an_auth_flow_is_infrastructure_wherever_it_sits_in_the_path(tmp_path: Path) -> None:
    """A login page costs the same against ten rows and ten million, so it can
    never pass S-7.8 — and that is true wherever the application mounted it.
    Matching only the first segment would make the rule about URL layout rather
    than about what the route does.
    """
    login = score(Candidate(Kind.HTTP_ROUTE, "accounts/login/", "urls.py", Discovery.PARSED))
    listing = score(Candidate(Kind.HTTP_ROUTE, "accounts/keys/", "urls.py", Discovery.PARSED))

    assert login.score < listing.score
    assert any("designed to do no work" in reason for reason in login.reasons)


def test_a_deeply_nested_route_ranks_below_a_top_level_one(tmp_path: Path) -> None:
    """Found by measurement, not by review: without a depth term netbox ranked
    thirty-nine routes level at the top and the order among them was
    alphabetical, which is a ranking that does not rank. With it, eighteen."""
    shallow = score(Candidate(Kind.HTTP_ROUTE, "checks/", "urls.py", Discovery.PARSED))
    deep = score(Candidate(Kind.HTTP_ROUTE, "projects/checks/tokens/", "urls.py", Discovery.PARSED))

    assert deep.score < shallow.score
    assert any("narrower thing" in reason for reason in deep.reasons)


def test_a_parameter_costs_more_than_a_segment(tmp_path: Path) -> None:
    """Depth is a hint about what a route probably addresses; a parameter is a
    fact about what it cannot be requested without."""
    deeper = score(Candidate(Kind.HTTP_ROUTE, "a/b/", "urls.py", Discovery.PARSED))
    parameterised = score(Candidate(Kind.HTTP_ROUTE, "a/<int:pk>/", "urls.py", Discovery.PARSED))

    assert parameterised.score < deeper.score


def test_an_http_route_outranks_a_console_script(tmp_path: Path) -> None:
    """Its response bytes are S-7.8's measure without instrumenting the subject;
    a console script may not touch the database at all."""
    route = score(Candidate(Kind.HTTP_ROUTE, "books/", "urls.py", Discovery.PARSED))
    script = score(Candidate(Kind.CLI_ENTRY_POINT, "shopctl", "pyproject.toml", Discovery.PARSED))

    assert route.score > script.score


def test_an_integration_test_outranks_a_unit_test(tmp_path: Path) -> None:
    integration = score(
        Candidate(Kind.INTEGRATION_TEST, "tests/test_integration_orders.py", "x", Discovery.PARSED)
    )
    unit = score(Candidate(Kind.INTEGRATION_TEST, "tests/test_units.py", "x", Discovery.PARSED))

    assert integration.score > unit.score


def test_a_word_that_ends_in_s_without_being_plural_earns_nothing(tmp_path: Path) -> None:
    """`status` and `address` are ordinary route segments, and a bonus that fires
    on them is a bonus that fires on everything."""
    plural = score(Candidate(Kind.HTTP_ROUTE, "orders/", "urls.py", Discovery.PARSED))
    singular = score(Candidate(Kind.HTTP_ROUTE, "address/", "urls.py", Discovery.PARSED))

    assert plural.score > singular.score


def test_every_score_carries_the_reasons_that_produced_it(tmp_path: Path) -> None:
    """A prior nobody can read is one nobody can correct."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    assert found.scored
    assert all(entry.reasons for entry in found.scored)


def test_the_ranking_says_it_is_a_prior_and_not_a_measurement(tmp_path: Path) -> None:
    """`CLAUDE.md`'s first non-negotiable. Nothing here measured anything, and a
    ranked list is exactly the shape a reader mistakes for one."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    assert "not a measurement" in found.describe()


def test_the_order_is_deterministic_and_does_not_depend_on_discovery_order(
    tmp_path: Path,
) -> None:
    """Two enumerations that disagree on order would make S-13.5's learning curve
    measure the walk instead of the repository."""
    candidates = [
        Candidate(Kind.HTTP_ROUTE, "books/", "urls.py", Discovery.PARSED),
        Candidate(Kind.HTTP_ROUTE, "authors/", "urls.py", Discovery.PARSED),
        Candidate(Kind.MANAGEMENT_COMMAND, "rebuild", "x", Discovery.PARSED, owner="shop"),
    ]

    forwards = [entry.candidate.name for entry in rank(candidates)]
    backwards = [entry.candidate.name for entry in rank(list(reversed(candidates)))]

    assert forwards == backwards


def test_the_highest_ranked_candidate_is_a_collection_route(tmp_path: Path) -> None:
    """The end-to-end form of AC 2 on a whole project: what the Explorer would
    try first is the list endpoint, not the admin and not a console script."""
    found = enumerate_entry_points(django_project(tmp_path / "subject"))

    best = found.scored[0].candidate
    assert best.kind is Kind.HTTP_ROUTE
    assert not best.parameters


# ======================================================= it survives a real repository


SPIKE_REPOS = Path(__file__).resolve().parents[2] / "spikes" / "S-0.3-grounding" / "repos"


@pytest.mark.parametrize("name", ["django-helpdesk", "netbox"])
def test_it_enumerates_a_repository_nobody_here_wrote(name: str) -> None:
    """A control that is not a fixture this file wrote.

    Everything above runs against a project built to be measured, which proves
    the parser handles what it was designed for and nothing about repositories
    S-0.3 ground by hand. Those are checked out on the development machine and
    gitignored, so this **skips** where they are absent rather than cloning — a
    test suite that reaches the network is a test suite that fails for reasons
    that are not the code.

    **S-0.6's holdout is deliberately not in this list.** It is one of the three
    S-0.3 checkouts and the obvious third case, which is exactly the reach
    `tests/test_holdout_discipline.py` exists to stop.
    """
    root = SPIKE_REPOS / name
    if not root.is_dir():
        pytest.skip(f"{name} is not checked out (see spikes/S-0.3-grounding)")

    found = enumerate_entry_points(root)

    assert found.of_kind(Kind.HTTP_ROUTE)
    assert found.of_kind(Kind.MANAGEMENT_COMMAND)
    assert not found.routes_are_complete
    assert found.scored[0].score > 0

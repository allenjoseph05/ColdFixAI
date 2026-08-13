"""S-7.5 — what a repository already has for making data, and what running one wrote.

The discovery half runs against real files. The exercising half runs a **real
factory_boy factory** against a **real Django project** in the subject's own
interpreter, and every row count comes back from the ORM.

That matters most for the two claims this module makes that a fake cannot check:
that `create_batch(n)` also creates the parents a `SubFactory` declares — which is
where `per_parent` comes from and is invisible in the factory's source — and that
a spread is uniform. A stub factory would seed exactly what this file imagined a
factory seeds, which is S-0.7b's *a test double more forgiving than the real thing
turns a structural assertion into a decoration*.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from coldfix.explorer.fixtures import (
    Discovery,
    Exercise,
    FixtureError,
    Kind,
    Mechanism,
    NeedsSynthesis,
    Scalability,
    Spread,
    count_models,
    discover,
    exercise_factory,
    prefer,
    recipe_from,
    score,
)
from coldfix.primitives.scaling import Distribution

pytestmark = pytest.mark.slow
"""The exercising tests migrate a project and start several `django.setup()`s."""

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
ROOT_URLCONF = "config.urls"
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "shop",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

URLS = "urlpatterns = []\n"

MODELS = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""

# A real factory_boy module. `BookFactory` declares a `SubFactory`, which is the
# whole point: `create_batch(10)` writes ten books *and ten authors*, and nothing
# in this file says so — `per_parent` is that invisible ratio.
FACTORIES = """\
import factory
from factory.django import DjangoModelFactory

from shop.models import Author, Book


class BaseFactory(DjangoModelFactory):
    class Meta:
        abstract = True


class AuthorFactory(BaseFactory):
    class Meta:
        model = Author

    name = factory.Sequence(lambda n: "author-%s" % n)


class BookFactory(BaseFactory):
    class Meta:
        model = Book

    title = factory.Sequence(lambda n: "book-%s" % n)
    author = factory.SubFactory(AuthorFactory)


class AuthorWithBooksFactory(AuthorFactory):
    books = factory.RelatedFactoryList(
        BookFactory, factory_related_name="author", size=3
    )
"""

# A factory addressed by string, which factory_boy accepts and projects use to
# avoid a circular import.
STRING_FACTORY = """\
from factory.django import DjangoModelFactory


class AuthorByLabelFactory(DjangoModelFactory):
    class Meta:
        model = "shop.Author"
"""

SEED_COMMAND = """\
from django.core.management.base import BaseCommand

from shop.models import Author


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=5)

    def handle(self, *args, **options):
        for index in range(options["count"]):
            Author.objects.create(name="seeded-%s" % index)
"""

PLAIN_COMMAND = """\
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        pass
"""

CONFTEST = """\
import pytest


@pytest.fixture
def author():
    return None


@pytest.fixture(scope="session")
def library():
    return None


def not_a_fixture():
    return None
"""

FIXTURE_JSON = json.dumps(
    [
        {"model": "shop.author", "pk": 1, "fields": {"name": "a"}},
        {"model": "shop.author", "pk": 2, "fields": {"name": "b"}},
        {"model": "shop.book", "pk": 1, "fields": {"title": "t", "author": 1}},
    ]
)


def write_project(root: Path) -> Path:
    """A real Django project holding one of everything AC 1 names."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "management" / "commands").mkdir(parents=True, exist_ok=True)
    (root / "shop" / "fixtures").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")

    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
    (root / "shop" / "factories.py").write_text(FACTORIES, encoding="utf-8")
    (root / "shop" / "other_factories.py").write_text(STRING_FACTORY, encoding="utf-8")
    (root / "shop" / "management" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "management" / "commands" / "seed_demo_data.py").write_text(
        SEED_COMMAND, encoding="utf-8"
    )
    (root / "shop" / "management" / "commands" / "rebuild_reports.py").write_text(
        PLAIN_COMMAND, encoding="utf-8"
    )
    (root / "shop" / "fixtures" / "initial.json").write_text(FIXTURE_JSON, encoding="utf-8")
    # A JSON file that is not a fixture, so the `fixtures/` rule has a control.
    (root / "package.json").write_text('{"name": "shop"}', encoding="utf-8")
    (root / "tests" / "conftest.py").write_text(CONFTEST, encoding="utf-8")

    return root


def migrate(root: Path) -> None:
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")


def run_manage(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"manage.py {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Files only. Discovery works before an environment exists."""
    return write_project(tmp_path)


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    """A project with tables, for the half that writes rows.

    Function-scoped on purpose: these tests count rows before and after, and a
    shared database would make each one's baseline depend on which ran first.
    """
    root = write_project(tmp_path)
    migrate(root)
    return root


def named(discovery: Discovery, name: str) -> Mechanism | None:
    return next((m for m in discovery.mechanisms if m.name == name), None)


# ============================== AC 1: it locates factories, fixtures and commands


def test_a_factory_is_located_with_the_model_it_declares(project: Path) -> None:
    found = named(discover(project), "BookFactory")

    assert found is not None
    assert found.kind is Kind.FACTORY
    assert found.model == "Book"
    assert found.evidence == "shop/factories.py"


def test_a_factory_declaring_its_model_by_label_is_located(project: Path) -> None:
    """factory_boy accepts a string, and projects use it to dodge a circular
    import — so reading only `model = Book` would miss the factories a large
    project is most likely to have."""
    found = named(discover(project), "AuthorByLabelFactory")

    assert found is not None
    assert found.model == "shop.Author"


def test_an_abstract_base_factory_is_not_a_mechanism(project: Path) -> None:
    """`BaseFactory` declares `Meta.abstract`, and `create_batch` on it raises. It
    is what mechanisms inherit from, not one of them."""
    assert named(discover(project), "BaseFactory") is None


def test_a_factory_inheriting_its_model_is_still_a_mechanism(project: Path) -> None:
    """Abstractness is what `Meta.abstract` says, never the absence of a model. A
    factory that subclasses another to add a related object declares no `Meta` at
    all — and it is the one that builds the most interesting data."""
    found = named(discover(project), "AuthorWithBooksFactory")

    assert found is not None
    assert found.model == "Author"


def test_a_seeding_command_is_located_with_its_count_flag(project: Path) -> None:
    found = named(discover(project), "seed_demo_data")

    assert found is not None
    assert found.kind is Kind.SEED_COMMAND
    assert found.scalability is Scalability.PARAMETERISED
    assert found.count_argument == "--count"


def test_a_command_that_does_not_seed_is_not_located(project: Path) -> None:
    """The control. A rule that called every management command a fixture would
    pass every test above and rank `migrate` above a factory."""
    assert named(discover(project), "rebuild_reports") is None


def test_a_fixture_file_is_located_and_its_contents_counted(project: Path) -> None:
    """The one kind that can be counted without being run: it is a list of
    objects and each names its model."""
    found = named(discover(project), "initial")

    assert found is not None
    assert found.kind is Kind.FIXTURE_FILE
    assert found.scalability is Scalability.FIXED
    assert dict(found.declared_rows) == {"shop.author": 2, "shop.book": 1}


def test_json_outside_a_fixtures_directory_is_not_a_fixture(project: Path) -> None:
    """The control for the directory rule. Every repository has JSON that is not
    a fixture, and `loaddata` only reads what a `fixtures/` directory holds."""
    assert named(discover(project), "package") is None


def test_pytest_fixtures_are_located(project: Path) -> None:
    fixtures = {m.name for m in discover(project).of_kind(Kind.PYTEST_FIXTURE)}

    assert fixtures == {"author", "library"}


def test_a_plain_function_beside_a_fixture_is_not_one(project: Path) -> None:
    assert named(discover(project), "not_a_fixture") is None


# ==================================== AC 2: what exists is preferred, and to what


def test_the_factory_outranks_everything_else_found(project: Path) -> None:
    chosen = prefer(discover(project))

    assert isinstance(chosen, Mechanism)
    assert chosen.kind is Kind.FACTORY


def test_being_unable_to_scale_is_what_costs_the_ranking(project: Path) -> None:
    """Two mechanisms differing in **one** term, because that is the term under test.

    The obvious comparison — a factory against a fixture file — differs in three:
    kind, scalability, and whether it names a model. It holds whatever the
    scalability penalty is, so it asserts the right order for the wrong reason.
    S-7.3 recorded that shape and the sabotage pass found it here.
    """
    fixed = Mechanism(
        kind=Kind.FIXTURE_FILE,
        name="initial",
        evidence="shop/fixtures/initial.json",
        scalability=Scalability.FIXED,
    )
    varying = Mechanism(
        kind=Kind.FIXTURE_FILE,
        name="initial",
        evidence="shop/fixtures/initial.json",
        scalability=Scalability.UNKNOWN,
    )

    assert score(fixed).score < score(varying).score
    assert any("second scale" in reason for reason in score(fixed).reasons)


def test_a_fixed_fixture_is_ranked_below_a_scalable_one(project: Path) -> None:
    """The end-to-end ordering, on the real project. The term itself is isolated
    above; this asserts the list an agent reads comes out in the right order."""
    discovery = discover(project)
    factory = score(next(m for m in discovery.mechanisms if m.name == "BookFactory"))
    fixture = score(next(m for m in discovery.mechanisms if m.name == "initial"))

    assert factory.score > fixture.score
    assert discovery.scored[-1].mechanism.name == "initial"


def test_a_repository_with_only_a_fixed_fixture_needs_synthesis(tmp_path: Path) -> None:
    """Having a fixture is not the same as being able to scale one, and the two
    send a reader to different places — so the answer names what was found."""
    (tmp_path / "app" / "fixtures").mkdir(parents=True)
    (tmp_path / "app" / "fixtures" / "seed.json").write_text(FIXTURE_JSON, encoding="utf-8")

    chosen = prefer(discover(tmp_path))

    assert isinstance(chosen, NeedsSynthesis)
    assert chosen.located
    assert "two scales" in chosen.reason


def test_a_repository_with_nothing_says_so_differently(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    chosen = prefer(discover(tmp_path))

    assert isinstance(chosen, NeedsSynthesis)
    assert not chosen.located
    assert "no factory" in chosen.reason


def test_a_pytest_fixture_is_never_chosen(tmp_path: Path) -> None:
    """Driving one means running the subject's test suite, and S-2.4 refuses to
    edit a test to change what it seeds."""
    (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")

    chosen = prefer(discover(tmp_path))

    assert isinstance(chosen, NeedsSynthesis)
    assert chosen.located


# ================================= AC 3: the recipe is built from what a run wrote


def test_running_a_factory_writes_rows_the_parse_could_not_predict(migrated: Path) -> None:
    """The measurement the whole module is organised around. `BookFactory` says
    it makes books; `create_batch(10)` makes ten books **and ten authors**
    through a `SubFactory`, and nothing in the file says so."""
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=factory, module="shop.factories", count=10
    )

    assert exercise.grew["shop.Book"] == 10
    assert exercise.grew["shop.Author"] == 10


def test_the_entity_is_the_model_that_grew_by_what_was_asked_for(migrated: Path) -> None:
    """The two candidate rules disagree here, which is the only way to tell them
    apart. `create_batch(5)` on a factory with three related books writes five
    authors and fifteen books — *closest to what was asked for* names the author,
    *the largest grower* names the book, and `scale(n)` means n of the entity.
    """
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "AuthorWithBooksFactory")

    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=factory, module="shop.factories", count=5
    )

    assert exercise.grew["shop.Author"] == 5
    assert exercise.grew["shop.Book"] == 15
    assert exercise.entity == "shop.Author"


def test_per_parent_is_the_ratio_the_foreign_keys_say_it_is(migrated: Path) -> None:
    """Three books per author, counted — not fifteen divided by five."""
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "AuthorWithBooksFactory")

    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=factory, module="shop.factories", count=5
    )
    recipe = recipe_from(exercise)

    assert exercise.spread is not None
    assert exercise.spread.per_parent == (3,) * 5
    assert recipe.entity == "shop.Author"
    assert recipe.per_parent == 3


def test_growth_is_measured_against_what_was_already_there(migrated: Path) -> None:
    """Every other exercising test starts from an empty database, where the total
    and the difference are the same number — so none of them can tell whether
    `grew` subtracts. A repository that ships seeded data is ordinary, and there
    the total would name the wrong entity and inflate every count in the recipe.
    """
    discovery = discover(migrated)
    author_factory = next(m for m in discovery.mechanisms if m.name == "AuthorFactory")
    book_factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    exercise_factory(
        migrated,
        python=[sys.executable],
        mechanism=author_factory,
        module="shop.factories",
        count=3,
    )
    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=book_factory, module="shop.factories", count=2
    )

    assert exercise.before["shop.Author"] == 3
    assert exercise.after["shop.Author"] == 5
    assert exercise.grew["shop.Author"] == 2


def test_the_spread_is_counted_rather_than_divided(migrated: Path) -> None:
    """Forty children over ten parents divides evenly and says nothing about
    whether one parent holds thirty-one. This is a GROUP BY."""
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=factory, module="shop.factories", count=10
    )

    assert exercise.spread is not None
    assert exercise.spread.child == "shop.Book"
    assert exercise.spread.parent == "shop.Author"
    assert exercise.spread.per_parent == (1,) * 10
    assert exercise.spread.uniform


def test_the_recipe_records_what_was_measured(migrated: Path) -> None:
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")
    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=factory, module="shop.factories", count=10
    )

    recipe = recipe_from(exercise)

    assert recipe.per_parent == 1
    assert recipe.distribution is Distribution.UNIFORM
    assert "BookFactory" in recipe.source
    assert recipe.digest()


def test_a_childless_parent_makes_the_spread_not_uniform(migrated: Path) -> None:
    """A parent holding nothing is absent from a GROUP BY over the child table.
    Left out, *nine parents with one book and one with none* is indistinguishable
    from *nine parents with one book* — and only the second is uniform."""
    discovery = discover(migrated)
    author_factory = next(m for m in discovery.mechanisms if m.name == "AuthorFactory")
    book_factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    exercise_factory(
        migrated,
        python=[sys.executable],
        mechanism=author_factory,
        module="shop.factories",
        count=3,
    )
    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=book_factory, module="shop.factories", count=2
    )

    assert exercise.spread is not None
    assert 0 in exercise.spread.per_parent
    assert not exercise.spread.uniform


def test_a_non_uniform_spread_refuses_to_become_a_recipe(migrated: Path) -> None:
    """`Distribution` has three values and *not uniform* is not one of them. A
    skewed pile of rows does not become POWER_LAW by elimination — that is a fit,
    and S-7.7 owns distribution as a parameter."""
    discovery = discover(migrated)
    author_factory = next(m for m in discovery.mechanisms if m.name == "AuthorFactory")
    book_factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    exercise_factory(
        migrated,
        python=[sys.executable],
        mechanism=author_factory,
        module="shop.factories",
        count=3,
    )
    exercise = exercise_factory(
        migrated, python=[sys.executable], mechanism=book_factory, module="shop.factories", count=2
    )

    with pytest.raises(FixtureError, match="no value for"):
        recipe_from(exercise)


def test_counting_asks_the_framework_for_model_labels(migrated: Path) -> None:
    """Labels rather than table names: `FixtureRecipe.entity` names an entity,
    and `db_table` is renameable and often renamed."""
    models = count_models(migrated, python=[sys.executable])

    assert "shop.Book" in models
    assert models["shop.Book"]["count"] == 0
    assert any(pointer["target"] == "shop.Author" for pointer in models["shop.Book"]["points_to"])


# ============================================================ honest failure


def test_a_mechanism_that_wrote_nothing_produces_no_recipe() -> None:
    """A recipe built from this would describe a fixture that does not exist, and
    every measurement against it would be a measurement of an empty database."""
    exercise = Exercise(
        mechanism=Mechanism(
            kind=Kind.FACTORY,
            name="EmptyFactory",
            evidence="shop/factories.py",
            scalability=Scalability.PARAMETERISED,
        ),
        requested=10,
        before={"shop.Book": 0},
        after={"shop.Book": 0},
    )

    assert exercise.wrote_nothing
    with pytest.raises(FixtureError, match="wrote nothing"):
        recipe_from(exercise)


def test_a_measured_zero_per_parent_is_not_clamped_to_one() -> None:
    """Clamping would record a child per parent that was counted and found not to
    be there."""
    exercise = Exercise(
        mechanism=Mechanism(
            kind=Kind.FACTORY,
            name="BookFactory",
            evidence="shop/factories.py",
            scalability=Scalability.PARAMETERISED,
        ),
        requested=2,
        before={"shop.Book": 0, "shop.Author": 0},
        after={"shop.Book": 2, "shop.Author": 2},
        spread=Spread(parent="shop.Author", child="shop.Book", per_parent=(0, 0)),
    )

    with pytest.raises(FixtureError, match=r"no shop\.Book"):
        recipe_from(exercise)


def test_running_a_non_factory_through_the_factory_runner_is_refused(project: Path) -> None:
    command = next(m for m in discover(project).mechanisms if m.kind is Kind.SEED_COMMAND)

    with pytest.raises(FixtureError, match="this runs factories"):
        exercise_factory(
            project, python=[sys.executable], mechanism=command, module="shop", count=1
        )


def test_a_project_that_cannot_be_configured_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("nothing useful", encoding="utf-8")

    with pytest.raises(FixtureError, match="DJANGO_SETTINGS_MODULE"):
        count_models(tmp_path, python=[sys.executable])


def test_a_subject_that_fails_reports_what_it_said(migrated: Path) -> None:
    """The realistic failure for the `module` parameter: a repository whose import
    root is not its checkout root. Named for what it triggers rather than for the
    more interesting failure it does not — the subject's own words are what a
    person acts on either way."""
    discovery = discover(migrated)
    factory = next(m for m in discovery.mechanisms if m.name == "BookFactory")

    with pytest.raises(FixtureError, match="did not answer"):
        exercise_factory(
            migrated,
            python=[sys.executable],
            mechanism=factory,
            module="shop.no_such_module",
            count=1,
        )


def test_one_unparseable_file_does_not_cost_the_others(project: Path) -> None:
    (project / "shop" / "broken.py").write_text("def (\n", encoding="utf-8")

    assert named(discover(project), "BookFactory") is not None


def test_a_virtualenv_is_not_walked(project: Path) -> None:
    """Every installed package has factories and fixtures, and none of them is
    the subject's."""
    borrowed = project / ".venv" / "lib" / "site-packages" / "other"
    borrowed.mkdir(parents=True)
    (borrowed / "factories.py").write_text(FACTORIES, encoding="utf-8")

    assert len(discover(project).of_kind(Kind.FACTORY)) == 4

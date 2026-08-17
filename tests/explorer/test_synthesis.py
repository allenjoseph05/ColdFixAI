"""S-7.6 — rows built from a schema, and what the database taught along the way.

Two layers, deliberately. The planner is a pure function over a schema mapping —
foreign key order, counts, choices, unfillable columns — and those tests need no
subprocess and run in the fast subset. The loop is not: AC 3 is about what an
`IntegrityError` reveals, and an error raised by a fake is an error somebody
chose. Those tests run against a real Django project with a **real
`UniqueConstraint`** and a **real disagreement between models and migrations**,
which are the two ways a schema lies about itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from coldfix.explorer.fixtures import count_models
from coldfix.explorer.synthesis import (
    Learned,
    Refusal,
    SchemaField,
    SchemaModel,
    SynthesisError,
    Violation,
    _refusal_of,
    plan,
    read_schema,
    synthesize,
)
from coldfix.primitives.scaling import Distribution

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

INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "shop"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}
"""

# A three-deep chain and one of every trap AC 2 names.
#
# `code` is the important one: its uniqueness lives in a `UniqueConstraint`, which
# is invisible on the field itself. A plan built from the field declarations fills
# it with a constant and the *second* row is refused — which is AC 3's mechanism
# with nothing staged about it.
MODELS = """\
from django.db import models


class WeirdField(models.Field):
    def get_internal_type(self):
        return "WeirdField"

    def db_type(self, connection):
        return "text"


class Publisher(models.Model):
    name = models.CharField(max_length=100)


class Author(models.Model):
    name = models.CharField(max_length=100)
    publisher = models.ForeignKey(Publisher, on_delete=models.CASCADE)
    email = models.EmailField(unique=True)
    bio = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=[("active", "Active"), ("retired", "Retired")]
    )


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
    code = models.CharField(max_length=20)
    published = models.DateField()
    price = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["code"], name="unique_book_code")]


class Gadget(models.Model):
    weird = WeirdField()


class Widget(models.Model):
    owner = models.ForeignKey(Publisher, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gt=100), name="widget_price")
        ]
"""

# The same models with `Book.published` relaxed to nullable. Written *after*
# migrating, so the column keeps its NOT NULL and the ORM says otherwise — which
# is what an unapplied migration looks like from here, and the second way AC 3's
# loop earns its place.
MODELS_RELAXED = MODELS.replace(
    "    published = models.DateField()",
    "    published = models.DateField(null=True)",
)


def write_project(root: Path, *, models: str = MODELS) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(models, encoding="utf-8")
    return root


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
def migrated(tmp_path: Path) -> Path:
    root = write_project(tmp_path)
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")
    return root


# ============================================================ a schema to plan against


def field(name: str, **overrides: object) -> SchemaField:
    """A column with sensible defaults, so each test varies only what it is about."""
    settings: dict[str, object] = {
        "name": name,
        "column": f"{name}_id" if overrides.get("relates_to") else name,
        "kind": "CharField",
        "max_length": 100,
    }
    settings.update(overrides)
    return SchemaField(**settings)  # type: ignore[arg-type]


def model(label: str, *fields: SchemaField) -> SchemaModel:
    return SchemaModel(
        label=label,
        table=label.lower().replace(".", "_"),
        fields=(field("id", kind="AutoField", auto=True), *fields),
    )


CHAIN = {
    "shop.Publisher": model("shop.Publisher", field("name")),
    "shop.Author": model(
        "shop.Author", field("name"), field("publisher", relates_to="shop.Publisher")
    ),
    "shop.Book": model("shop.Book", field("title"), field("author", relates_to="shop.Author")),
}


# ==================================== AC 1: the schema is read and the FK chain walked


def test_parents_are_planned_before_the_children_that_need_them() -> None:
    built = plan(CHAIN, target="shop.Book", count=5)

    assert [step.model for step in built.steps] == [
        "shop.Publisher",
        "shop.Author",
        "shop.Book",
    ]


def test_a_foreign_key_becomes_a_reference_to_the_parent_step() -> None:
    built = plan(CHAIN, target="shop.Book", count=2)

    book = next(step for step in built.steps if step.model == "shop.Book")
    assert book.values["author"].kind == "reference"
    assert book.values["author"].model == "shop.Author"


def test_only_the_target_is_seeded_at_the_asked_for_count() -> None:
    built = plan(CHAIN, target="shop.Book", count=10, per_parent=5)

    counts = {step.model: step.count for step in built.steps}
    assert counts["shop.Book"] == 10
    assert counts["shop.Author"] == 2
    assert counts["shop.Publisher"] == 2


def test_a_parent_count_rounds_up_rather_than_leaving_the_last_row_homeless() -> None:
    """A plan short by one parent fails on the last row and spends a revision
    learning something the arithmetic already knew."""
    built = plan(CHAIN, target="shop.Book", count=10, per_parent=3)

    counts = {step.model: step.count for step in built.steps}
    assert counts["shop.Author"] == 4


def test_a_nullable_foreign_key_is_not_walked() -> None:
    """An optional parent is optional. Walking it would seed a model the workload
    never touches, and every row of it is a row S-7.8 has to explain."""
    schema = {
        "shop.Author": model("shop.Author", field("name")),
        "shop.Book": model(
            "shop.Book", field("title"), field("author", relates_to="shop.Author", null=True)
        ),
    }

    built = plan(schema, target="shop.Book", count=3)

    assert [step.model for step in built.steps] == ["shop.Book"]


def test_a_required_cycle_is_reported_rather_than_recursed_into() -> None:
    schema = {
        "shop.Left": model("shop.Left", field("right", relates_to="shop.Right")),
        "shop.Right": model("shop.Right", field("left", relates_to="shop.Left")),
    }

    with pytest.raises(SynthesisError, match="cycle"):
        plan(schema, target="shop.Left", count=1)


# ============================ AC 2: required fields, enums and unique constraints


def test_a_field_with_choices_takes_one_of_them() -> None:
    """Django does not enforce choices at the database, so a row holding
    `coldfix-0` in a status column inserts cleanly and breaks the application
    that reads it — a workload built on those measures error handling."""
    schema = {"shop.Author": model("shop.Author", field("status", choices=("active", "retired")))}

    built = plan(schema, target="shop.Author", count=3)

    assert built.steps[0].values["status"].literal == "active"


def test_blank_is_not_consulted_because_it_is_a_form_concept() -> None:
    """`blank=True, null=False` is required by every database and optional in
    every admin form. It is the commonest way a plan built from the models alone
    fails on its very first row."""
    schema = {"shop.Author": model("shop.Author", field("bio", kind="TextField"))}

    built = plan(schema, target="shop.Author", count=1)

    assert "bio" in built.steps[0].values


def test_a_unique_column_varies_per_row_without_being_taught_to() -> None:
    """The commonest synthesis failure, and one the declarations already predict
    — so spending a revision on it would be spending one on arithmetic."""
    schema = {"shop.Author": model("shop.Author", field("email", kind="EmailField", unique=True))}

    built = plan(schema, target="shop.Author", count=3)

    value = built.steps[0].values["email"]
    assert value.kind == "sequence"
    assert "{i}" in value.template


def test_a_non_unique_column_does_not_vary() -> None:
    """The control. A planner that varied everything would pass the test above
    and never exercise AC 3's loop at all."""
    schema = {"shop.Author": model("shop.Author", field("name"))}

    built = plan(schema, target="shop.Author", count=3)

    assert built.steps[0].values["name"].kind == "sequence"
    assert built.steps[0].values["name"].template == "coldfix"


def test_a_field_with_a_default_is_left_to_the_database() -> None:
    schema = {"shop.Author": model("shop.Author", field("nickname", has_default=True))}

    built = plan(schema, target="shop.Author", count=1)

    assert "nickname" not in built.steps[0].values


def test_a_value_is_truncated_to_what_the_column_holds() -> None:
    schema = {"shop.Author": model("shop.Author", field("code", max_length=4, unique=True))}

    built = plan(schema, target="shop.Author", count=2)

    assert built.steps[0].values["code"].template == "{i}"


# ================================================ AC 4: it reports rather than guesses


def test_a_column_this_cannot_fill_is_reported_with_its_type() -> None:
    """Writing null would fail at the database with a worse message, and writing
    a zero would produce a row that is valid and means nothing."""
    schema = {"shop.Gadget": model("shop.Gadget", field("weird", kind="WeirdField"))}

    with pytest.raises(SynthesisError, match="WeirdField"):
        plan(schema, target="shop.Gadget", count=1)


def test_a_model_the_subject_does_not_have_is_refused() -> None:
    with pytest.raises(SynthesisError, match="not a model this subject has"):
        plan(CHAIN, target="shop.Nothing", count=1)


def test_a_plan_for_no_rows_is_refused() -> None:
    """It would seed nothing and report success for it."""
    with pytest.raises(SynthesisError, match="seeds nothing"):
        plan(CHAIN, target="shop.Book", count=0)


# =================================== reading a refusal: diagnostics before message


def test_structured_diagnostics_are_preferred_to_the_message() -> None:
    """S-7.2's rule. The server's own field is the same string whatever the
    locale; the message is not."""
    refusal = _refusal_of(
        {
            "message": 'null value in column "publisher_id" of relation "shop_author"',
            "column": "publisher_id",
            "table": "shop_author",
            "constraint": None,
        }
    )

    assert refusal.learned is Learned.DIAGNOSTICS
    assert refusal.column == "publisher_id"
    assert refusal.violation is Violation.NOT_NULL


def test_a_sqlite_not_null_message_is_read_when_there_are_no_diagnostics() -> None:
    refusal = _refusal_of({"message": "NOT NULL constraint failed: shop_book.published"})

    assert refusal.learned is Learned.MESSAGE
    assert refusal.violation is Violation.NOT_NULL
    assert refusal.column == "published"


def test_a_sqlite_unique_message_is_read_when_there_are_no_diagnostics() -> None:
    refusal = _refusal_of({"message": "UNIQUE constraint failed: shop_book.code"})

    assert refusal.learned is Learned.MESSAGE
    assert refusal.violation is Violation.UNIQUE
    assert refusal.column == "code"


def test_a_refusal_naming_nothing_is_not_actionable() -> None:
    """Revising on it would be the same plan submitted twice. A check constraint
    this cannot read is not made satisfiable by guessing again."""
    refusal = _refusal_of({"message": "CHECK constraint failed: price_positive"})

    assert refusal.violation is Violation.OTHER
    assert refusal.learned is Learned.NEITHER
    assert not refusal.actionable


def test_a_postgres_unique_violation_names_the_index_and_is_still_actionable() -> None:
    refusal = _refusal_of(
        {"message": 'duplicate key value violates unique constraint "shop_book_code_key"'}
    )

    assert refusal.violation is Violation.UNIQUE
    assert refusal.constraint == "shop_book_code_key"
    assert refusal.actionable


# ============================================== the loop, against a real database


@pytest.mark.slow
def test_the_schema_is_read_from_the_framework(migrated: Path) -> None:
    schema = read_schema(migrated, python=[sys.executable])

    book = schema["shop.Book"]
    assert book.table == "shop_book"
    assert {f.name for f in book.fields} >= {"title", "author", "code", "published", "price"}
    assert book.required_parents == ("shop.Author",)


@pytest.mark.slow
def test_a_chain_three_deep_is_built_end_to_end(migrated: Path) -> None:
    built = synthesize(migrated, python=[sys.executable], target="shop.Book", count=5)

    assert built.created["shop.Book"] == 5
    assert built.created["shop.Author"] == 5
    assert built.created["shop.Publisher"] == 5


@pytest.mark.slow
def test_a_unique_constraint_invisible_on_the_field_is_learned_from_the_database(
    migrated: Path,
) -> None:
    """AC 3, with nothing staged about it. `Book.code` carries no `unique=True`;
    its uniqueness lives in a `UniqueConstraint` that no field declares, so the
    first plan fills it with a constant and the second row is refused."""
    built = synthesize(migrated, python=[sys.executable], target="shop.Book", count=4)

    assert built.created["shop.Book"] == 4
    assert [refusal.violation for refusal in built.revisions] == [Violation.UNIQUE]
    assert built.revisions[0].column == "code"


@pytest.mark.slow
def test_a_column_the_models_call_nullable_and_the_database_does_not(
    tmp_path: Path,
) -> None:
    """The other half of AC 3, and the reason the loop is not decoration: models
    and migrations disagree constantly, and the ORM is the one that is wrong.

    The project is migrated with `published` required, then its model file is
    relaxed to `null=True` — which is exactly what an unapplied migration looks
    like from here. The plan omits the column and the database refuses the row.
    """
    root = write_project(tmp_path)
    run_manage(root, "makemigrations", "shop")
    run_manage(root, "migrate")
    (root / "shop" / "models.py").write_text(MODELS_RELAXED, encoding="utf-8")

    built = synthesize(root, python=[sys.executable], target="shop.Book", count=3)

    assert built.created["shop.Book"] == 3
    assert Violation.NOT_NULL in [refusal.violation for refusal in built.revisions]
    assert any(refusal.column == "published" for refusal in built.revisions)


@pytest.mark.slow
def test_the_enum_column_holds_a_value_the_application_would_accept(
    migrated: Path,
) -> None:
    synthesize(migrated, python=[sys.executable], target="shop.Book", count=2)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys;sys.path.insert(0,os.getcwd());import django;django.setup();"
            "from shop.models import Author;"
            "print(sorted({a.status for a in Author.objects.all()}))",
        ],
        cwd=migrated,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert "['active']" in result.stdout, result.stdout + result.stderr


@pytest.mark.slow
def test_per_parent_is_what_the_caller_asked_for(migrated: Path) -> None:
    built = synthesize(migrated, python=[sys.executable], target="shop.Book", count=6, per_parent=3)

    assert built.created["shop.Book"] == 6
    assert built.created["shop.Author"] == 2


@pytest.mark.slow
def test_the_recipe_says_uniform_and_the_result_says_what_that_hides(
    migrated: Path,
) -> None:
    """`07-use-cases.md` §5: if every generated customer has three orders, an N+1
    that only hurts customers with three thousand stays invisible. The recipe
    carries the shape and the result carries the consequence."""
    built = synthesize(migrated, python=[sys.executable], target="shop.Book", count=4, per_parent=2)
    recipe = built.recipe()

    assert recipe.entity == "shop.Book"
    assert recipe.per_parent == 2
    assert recipe.distribution is Distribution.UNIFORM
    assert "synthesized from schema" in recipe.source
    assert "blindest" in built.blindness


@pytest.mark.slow
def test_a_failed_attempt_leaves_the_database_exactly_as_it_found_it(
    migrated: Path,
) -> None:
    """The whole plan is one transaction, and this is why.

    Found by a real run rather than by review: the first attempt created its
    publishers and authors, failed on a unique book code, and the *revision* then
    collided with the author e-mail it had itself inserted a second earlier. A
    retry loop that writes either rolls back what it wrote or poisons its own next
    attempt — and the rows it leaves behind would inflate S-7.5's counts and
    S-7.8's scaling either way.

    `Widget` carries a check constraint its filler cannot satisfy, so the failure
    lands *after* a parent row has been created.
    """
    with pytest.raises(SynthesisError):
        synthesize(migrated, python=[sys.executable], target="shop.Widget", count=2)

    counts = count_models(migrated, python=[sys.executable])
    assert counts["shop.Publisher"]["count"] == 0
    assert counts["shop.Widget"]["count"] == 0


@pytest.mark.slow
def test_a_constraint_this_cannot_act_on_is_reported_rather_than_retried(
    migrated: Path,
) -> None:
    """A check constraint names no column to fill or vary. Revising on it would
    submit the same plan again until the cap, and report a cap where the real
    answer is *this needs a value only the domain knows*."""
    with pytest.raises(SynthesisError, match=r"cannot act on|stopped at"):
        synthesize(migrated, python=[sys.executable], target="shop.Widget", count=2)


@pytest.mark.slow
def test_a_model_with_an_unfillable_column_fails_before_writing_anything(
    migrated: Path,
) -> None:
    with pytest.raises(SynthesisError, match="WeirdField"):
        synthesize(migrated, python=[sys.executable], target="shop.Gadget", count=1)


@pytest.mark.slow
def test_a_project_that_cannot_be_configured_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("nothing useful", encoding="utf-8")

    with pytest.raises(SynthesisError, match="DJANGO_SETTINGS_MODULE"):
        read_schema(tmp_path, python=[sys.executable])


@pytest.mark.slow
def test_a_subject_that_did_not_answer_is_an_error_not_an_empty_schema(
    tmp_path: Path,
) -> None:
    """*This project has no models* and *this project would not load* are two
    answers. Flattened into one, the second becomes a plan that refuses every
    target as unknown — which reads as a bad argument rather than a broken
    subject, and sends a reader to the wrong place entirely.

    Reached by breaking the settings module, because that is the one thing that
    makes `django.setup()` fail before it can print anything.
    """
    root = write_project(tmp_path)
    (root / "config" / "settings.py").write_text("import nonexistent_module", encoding="utf-8")

    with pytest.raises(SynthesisError, match="did not answer"):
        read_schema(root, python=[sys.executable])


def test_a_failure_report_carries_what_was_already_learned() -> None:
    """AC 4. A report that named only the last refusal would hide that four
    constraints were discovered, which is the signal that a subject's models and
    migrations have drifted apart."""
    refusal = Refusal(
        violation=Violation.OTHER,
        learned=Learned.NEITHER,
        message="CHECK constraint failed: price_positive",
    )

    assert not refusal.actionable
    assert "cannot act on" in refusal.describe()

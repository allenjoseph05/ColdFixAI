"""S-7.7 — the shape of a fixture as a parameter, and as a thing on the record.

S-3.3 already generates the three shapes and is tested where it lives; nothing
here re-tests `allocate`. What is under test is the wiring: that a distribution
asked for is the distribution built, that it reaches the rows through the
subject's own foreign keys, and that a measurement taken under one cannot be
mistaken for a measurement taken under another.

The end-to-end half runs against a real Django project, because the claim *the
heaviest author holds nineteen of the books* is a claim about rows in a database
and a fake would only report what this file already believed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from coldfix.explorer.synthesis import (
    SchemaField,
    SchemaModel,
    SynthesisError,
    _assignment_from,
    plan,
    synthesize,
)
from coldfix.primitives.scaling import Distribution, allocate
from coldfix.screening.workload import FixtureRecipe

MANAGE_PY = """import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """import os

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


# Publisher <- Author <- Book, every link required, so the shape has somewhere to
# go and a grandparent exists to prove it is left alone.
CHAIN = {
    "shop.Publisher": model("shop.Publisher", field("name")),
    "shop.Author": model(
        "shop.Author", field("name"), field("publisher", relates_to="shop.Publisher")
    ),
    "shop.Book": model("shop.Book", field("title"), field("author", relates_to="shop.Author")),
}

MODELS = """\
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="books")
"""


def write_project(root: Path) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(SETTINGS, encoding="utf-8")
    (root / "config" / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "models.py").write_text(MODELS, encoding="utf-8")
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


def spread_in(root: Path) -> list[int]:
    """How many books each author actually holds, counted in the subject."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,os,sys;sys.path.insert(0,os.getcwd());"
            "import django;django.setup();"
            "from django.db.models import Count;from shop.models import Author;"
            "print(json.dumps([a.n for a in Author.objects.annotate(n=Count('books'))]))",
        ],
        cwd=root,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if not result.stdout.strip():
        pytest.fail(result.stdout + result.stderr)
    return sorted(json.loads(result.stdout.strip()), reverse=True)


# ================================= AC 2: the distribution is a parameter of the plan


def test_a_uniform_plan_spreads_every_parent_alike() -> None:
    built = plan(CHAIN, target="shop.Book", count=12, per_parent=4)

    assert built.allocation is not None
    assert set(built.allocation.counts) == {4}


def test_a_power_law_plan_puts_the_mass_on_the_first_parents() -> None:
    built = plan(
        CHAIN,
        target="shop.Book",
        count=40,
        per_parent=10,
        distribution=Distribution.POWER_LAW,
    )

    assert built.allocation is not None
    assert built.allocation.largest > 4
    assert built.allocation.total == 40


def test_a_long_tail_plan_is_bimodal_rather_than_a_second_power_law() -> None:
    """S-3.3's own separation: if the two shapes agree on head mass they are one
    shape with two names, and the second axis is decoration."""
    tail = plan(
        CHAIN, target="shop.Book", count=200, per_parent=10, distribution=Distribution.LONG_TAIL
    )
    zipf = plan(
        CHAIN, target="shop.Book", count=200, per_parent=10, distribution=Distribution.POWER_LAW
    )

    assert tail.allocation is not None
    assert zipf.allocation is not None
    assert tail.allocation.head_mass > zipf.allocation.head_mass


def test_the_shape_reaches_the_rows_as_a_per_row_parent_assignment() -> None:
    """The subject indexes a list and decides nothing. The arithmetic is S-3.3's,
    already tested where it lives, and a second generator in the subject would be
    a second thing to keep in step."""
    built = plan(
        CHAIN, target="shop.Book", count=20, per_parent=5, distribution=Distribution.POWER_LAW
    )

    book = next(step for step in built.steps if step.model == "shop.Book")
    assignment = book.values["author"].assignment
    assert len(assignment) == 20
    assert assignment.count(0) == built.allocation.counts[0]  # type: ignore[union-attr]


def test_an_assignment_lists_each_parent_as_often_as_it_holds_a_child() -> None:
    allocation = allocate(Distribution.POWER_LAW, groups=4, total=10)

    assignment = _assignment_from(allocation)

    assert len(assignment) == 10
    for position, held in enumerate(allocation.counts):
        assert assignment.count(position) == held


def test_the_shape_applies_to_the_target_and_not_to_its_grandparents() -> None:
    """A shape applied at every level compounds into one nobody asked for, and
    the cost S-3.3 is about is paid where the request walks — not three joins up."""
    built = plan(
        CHAIN, target="shop.Book", count=20, per_parent=5, distribution=Distribution.LONG_TAIL
    )

    author = next(step for step in built.steps if step.model == "shop.Author")
    assert author.values["publisher"].assignment == ()


# ======================= a shape that will not fit is refused, never flattened


def test_a_skew_with_one_parent_per_child_is_refused() -> None:
    """`allocate` guarantees every parent at least one child, so twenty parents
    and twenty children leave nothing to skew with. Building it anyway would put
    LONG_TAIL in the one field that exists to stop a fixture being described as a
    shape it does not have."""
    with pytest.raises(SynthesisError, match="flat"):
        plan(
            CHAIN,
            target="shop.Book",
            count=20,
            per_parent=1,
            distribution=Distribution.LONG_TAIL,
        )


def test_the_refusal_says_which_knob_to_turn() -> None:
    with pytest.raises(SynthesisError, match="Raise per_parent"):
        plan(
            CHAIN,
            target="shop.Book",
            count=20,
            per_parent=1,
            distribution=Distribution.POWER_LAW,
        )


def test_a_uniform_fixture_with_one_parent_per_child_is_not_refused() -> None:
    """The control. Uniform *is* flat, so the refusal must not fire on the shape
    that is allowed to be."""
    built = plan(CHAIN, target="shop.Book", count=20, per_parent=1)

    assert built.allocation is not None
    assert set(built.allocation.counts) == {1}


def test_a_model_nothing_points_at_has_no_shape_to_speak_of() -> None:
    schema = {"shop.Author": model("shop.Author", field("name"))}

    built = plan(schema, target="shop.Author", count=5, distribution=Distribution.POWER_LAW)

    assert built.allocation is None


# ================== AC 3: recorded in every measurement taken with those fixtures


def test_the_recipe_records_the_heaviest_parent_not_the_mean() -> None:
    """The whole reason to build a long tail is the request that takes minutes
    while every other request stays fast, and that request is made by the
    heaviest parent. Recording the mean would name the shape and then describe it
    with the number the shape exists to avoid."""
    built = plan(
        CHAIN, target="shop.Book", count=100, per_parent=10, distribution=Distribution.LONG_TAIL
    )
    assert built.allocation is not None

    mean = built.allocation.total // built.allocation.groups
    assert built.allocation.largest > mean


def test_two_shapes_of_the_same_size_do_not_share_a_replay_key() -> None:
    """Load-bearing for S-5.1. Same entity, same rows, same parents, different
    shape — if the digests agreed, a cached uniform measurement would be replayed
    for a long-tail run and the cache would lie faster than the experiment could
    correct it."""
    fields = {"entity": "shop.Book", "per_parent": 19, "parents": 10, "source": "synthesized"}
    uniform = FixtureRecipe(**fields, distribution=Distribution.UNIFORM)  # type: ignore[arg-type]
    tail = FixtureRecipe(**fields, distribution=Distribution.LONG_TAIL)  # type: ignore[arg-type]

    assert uniform.digest() != tail.digest()


def test_the_parent_count_is_part_of_the_replay_key() -> None:
    """Ten heavy parents and a hundred light ones are different fixtures under
    the same name, and `allocate` needs the count to rebuild either."""
    fields = {
        "entity": "shop.Book",
        "per_parent": 19,
        "source": "synthesized",
        "distribution": Distribution.LONG_TAIL,
    }
    few = FixtureRecipe(**fields, parents=10)  # type: ignore[arg-type]
    many = FixtureRecipe(**fields, parents=100)  # type: ignore[arg-type]

    assert few.digest() != many.digest()


def test_a_recipe_without_a_parent_count_is_still_valid() -> None:
    """The field is optional because recipes predating S-7.7 exist and because a
    mechanism with no parent population is an ordinary case — `None` means *not
    recorded*, never *one parent*."""
    recipe = FixtureRecipe(
        entity="shop.Book", per_parent=1, distribution=Distribution.UNIFORM, source="a factory"
    )

    assert recipe.parents is None


# ============================================ against a real database


@pytest.mark.slow
def test_a_long_tail_reaches_the_rows_through_the_subjects_own_foreign_keys(
    migrated: Path,
) -> None:
    """The claim is about rows. Ten authors, a hundred books, and one author
    holding far more than a tenth of them — which no uniform generator produces
    and which is the case the whole second axis exists for."""
    built = synthesize(
        migrated,
        python=[sys.executable],
        target="shop.Book",
        count=100,
        per_parent=10,
        distribution=Distribution.LONG_TAIL,
    )

    counted = spread_in(migrated)
    assert built.created["shop.Book"] == 100
    assert built.created["shop.Author"] == 10
    assert sum(counted) == 100
    assert counted[0] > 10
    assert counted[-1] == 1


@pytest.mark.slow
def test_the_measured_spread_is_the_one_the_plan_asked_for(migrated: Path) -> None:
    """Not merely skewed — skewed exactly as `allocate` said, because the plan is
    deterministic and the subject only indexes it."""
    built = synthesize(
        migrated,
        python=[sys.executable],
        target="shop.Book",
        count=60,
        per_parent=6,
        distribution=Distribution.POWER_LAW,
    )
    assert spread_in(migrated) == sorted(built.plan.allocation.counts, reverse=True)  # type: ignore[union-attr]


@pytest.mark.slow
def test_a_uniform_synthesis_still_spreads_evenly(migrated: Path) -> None:
    """The control, in the database rather than in the planner: the wiring must
    not have made every fixture skewed."""
    synthesize(migrated, python=[sys.executable], target="shop.Book", count=60, per_parent=6)

    assert set(spread_in(migrated)) == {6}


@pytest.mark.slow
def test_the_recipe_of_a_skewed_run_carries_the_shape_and_the_parent_count(
    migrated: Path,
) -> None:
    built = synthesize(
        migrated,
        python=[sys.executable],
        target="shop.Book",
        count=100,
        per_parent=10,
        distribution=Distribution.LONG_TAIL,
    )
    recipe = built.recipe()

    assert recipe.distribution is Distribution.LONG_TAIL
    assert recipe.parents == 10
    assert recipe.per_parent == max(spread_in(migrated))


@pytest.mark.slow
def test_a_skewed_fixture_says_what_it_shows_rather_than_what_it_hides(
    migrated: Path,
) -> None:
    """The blindness note is not boilerplate: under a long tail it says the
    opposite of what it says under uniform data, because a flat result here is a
    much stronger exclusion than a flat result under the blindest shape."""
    built = synthesize(
        migrated,
        python=[sys.executable],
        target="shop.Book",
        count=100,
        per_parent=10,
        distribution=Distribution.LONG_TAIL,
    )

    assert "stronger exclusion" in built.blindness
    assert "blindest" not in built.blindness

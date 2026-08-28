"""What a repository already has for making data, and what running one produced.

Epic 7, S-7.5. The Explorer can reach an endpoint (S-7.3) and get past its front
door (S-7.4), and an endpoint with no rows behind it measures nothing. Most
repositories can already make their own data — that is what a `factories.py`, a
`seed_demo_data` command and a `fixtures/*.json` are for — and AC 2 says to use
those in preference to synthesizing rows from the schema.

**Nothing here calls a model.** Finding a class whose `Meta.model` is `Book` is
parsing, and counting rows before and after is arithmetic.

**A located factory is a declared capability; a recipe is what a run wrote.**
This is the epic's running distinction a fourth time, and here it is forced by the
artifact rather than chosen. S-4.1's `FixtureRecipe` requires an `entity`, a
`per_parent` and a `distribution` — *how many rows of what, spread how* — and a
file establishes none of the three. `BookFactory` says it makes books; it does not
say that `create_batch(10)` also made ten authors through a `SubFactory`, and
`per_parent` is exactly that ratio. So discovery **locates and ranks**, and a
recipe is minted only from counts taken either side of a real invocation.

Minting one from the parse instead would put three numbers nobody measured into
the artifact that keys S-5.1's replay cache and carries S-3.3's blindness
qualification — the shape of claim the first non-negotiable exists to prevent.

**Preference is anchored on S-7.8, and the anchor is whether the size can vary.**
AC 2 says to prefer what exists over what would be synthesized, and among the
things that exist the ranking needs an anchor or it is taste. There is one
downstream gate: S-7.8 rejects a workload unless it can be driven at N=10 *and*
N=100. So a mechanism that takes a count is worth more than one that does not,
and **a `loaddata` fixture file is worth least of all** — it holds a fixed set of
rows with fixed primary keys, so it cannot produce a second scale and loading it
twice is a collision, not twice the data. A repository whose only fixture is a
`.json` file is a repository that needs S-7.6 despite having a fixture.

**The distribution is measured, and refused when it cannot be named.** S-3.3
proved the uniform fixture is provably the blindest for any per-parent cost, so
`UNIFORM` is the one value that must never be assumed. It is established exactly —
every parent holding the same number of children, counted — and a spread that is
*not* uniform does not become `POWER_LAW` by elimination. Which non-uniform shape
a pile of rows has is a fit, and S-7.7 is the story that owns distribution as a
parameter; here it is reported with the measured spread and no recipe is minted.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from coldfix.bench.execute import ExecutionError
from coldfix.explorer.entrypoints import SKIP_DIRECTORIES, settings_module
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.primitives.scaling import Distribution
from coldfix.screening.workload import FixtureRecipe

EXERCISE_TIMEOUT_SECONDS = 300.0
"""Longer than S-7.3's and S-7.4's, because this one *writes*. Seeding a hundred
rows through a factory with a `SubFactory` chain is a hundred inserts and their
parents, against whatever the subject's database is."""

_MARKER = "<<<COLDFIX-FIXTURES>>>"

# What a factory_boy base class is called. `DjangoModelFactory` is the Django one,
# `Factory` the base, and a project subclassing either into its own `BaseFactory`
# is ordinary — so the suffix is what is matched, not the exact name.
_FACTORY_SUFFIX = "Factory"

# Words that make a management command a seeding command. A command is not a
# fixture because it writes; `clearsessions` writes. These are the verbs projects
# use for *making test data*, and the list is deliberately short for the reason
# `08-audit.md` keeps finding: a detector needs a control, and the control here is
# that `migrate`, `collectstatic` and `runserver` must not match.
_SEED_WORDS: frozenset[str] = frozenset(
    {
        "bootstrap",
        "demo",
        "fake",
        "fill",
        "generate",
        "populate",
        "sample",
        "seed",
    }
)

# Argument names that carry "how many". Matched against what `add_arguments`
# declares, because that is where a command states its own interface.
_COUNT_ARGUMENTS: frozenset[str] = frozenset(
    {"--count", "--num", "--number", "--rows", "--size", "--total", "-n"}
)

_FIXTURE_SUFFIXES: tuple[str, ...] = (".json", ".yaml", ".yml")

# `<app>/management/commands/<name>.py` — the three trailing parts S-7.3 matches on.
_COMMAND_DEPTH = 3


class FixtureError(Exception):
    """Fixtures could not be discovered, exercised, or turned into a recipe."""


class Kind(StrEnum):
    """The four things AC 1 asks for. Ordered here by nothing; `rank` decides."""

    FACTORY = "factory_boy factory"
    SEED_COMMAND = "management command that seeds data"
    FIXTURE_FILE = "loaddata fixture file"
    PYTEST_FIXTURE = "pytest fixture"


class Scalability(StrEnum):
    """Whether the size of what this makes can be varied, and how it is known.

    Three states because there are three different next actions, which is the
    test S-7.2 set for a state enum: pass a number, run it and find out, or stop
    and synthesize.
    """

    PARAMETERISED = "takes a count, so the size is an argument"
    FIXED = "holds a fixed set of rows with fixed primary keys"
    UNKNOWN = "no count argument was found, so only running it twice establishes this"

    @property
    def action(self) -> str:
        return {
            Scalability.PARAMETERISED: "seed with the count it declares",
            Scalability.FIXED: (
                "use it for a baseline only — loading it twice is a primary key collision, "
                "not twice the data, so it cannot produce S-7.8's second scale"
            ),
            Scalability.UNKNOWN: (
                "run it, count, run it again and count again; it either doubles the rows or "
                "fails on a unique constraint, and both are answers"
            ),
        }[self]


@dataclass(frozen=True)
class Mechanism:
    """One way this repository already makes data.

    Everything here is *declared*: read out of a file that says a factory exists,
    that a command takes `--count`, that a JSON fixture holds forty rows. What any
    of it actually writes is `exercise`'s to establish.
    """

    kind: Kind
    name: str
    evidence: str
    scalability: Scalability
    model: str | None = None
    """The model the file says this makes, where it says so. A factory's
    `Meta.model` states it; a seed command almost never does."""

    count_argument: str | None = None
    """The flag that carries how many, where one was declared."""

    declared_rows: Mapping[str, int] = field(default_factory=dict)
    """Rows per model the file itself states, which only a fixture file does —
    it is a list of objects and they can be counted without running anything.
    Still a declaration: nothing here has checked that it loads."""

    def describe(self) -> str:
        model = f" → {self.model}" if self.model else ""
        return f"{self.kind.value} {self.name}{model} ({self.evidence}; {self.scalability.value})"


@dataclass(frozen=True)
class Scored:
    """A mechanism, its score, and the reasons that produced it."""

    mechanism: Mechanism
    score: int
    reasons: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.score:+3d}  {self.mechanism.describe()}\n      {'; '.join(self.reasons)}"


@dataclass(frozen=True)
class NeedsSynthesis:
    """Nothing found here can seed the subject at two scales.

    A result, not a failure — and the one AC 2 is about, seen from the other
    side: synthesis is what happens when preference has nothing to prefer. Carries
    what *was* found, because *this repository has a fixture file and it cannot be
    scaled* and *this repository has nothing* send a reader to two different
    places, the distinction S-7.1 drew for unsupported frameworks.
    """

    reason: str
    located: tuple[Mechanism, ...] = ()

    def describe(self) -> str:
        lines = [f"synthesis required (S-7.6): {self.reason}"]
        lines.extend(f"  located but unusable: {m.describe()}" for m in self.located)
        return "\n".join(lines)


Chosen = Mechanism | NeedsSynthesis
"""What preference concludes.

A union rather than an optional mechanism, for S-4.5's reason: the two carry
different things and call for different next actions, and a `None` reads as a
healthy result at every call site that forgets to check it.
"""


@dataclass(frozen=True)
class Discovery:
    """Every way this repository can already make data, in preference order."""

    root: Path
    scored: tuple[Scored, ...]
    files_read: int = 0

    @property
    def mechanisms(self) -> tuple[Mechanism, ...]:
        return tuple(entry.mechanism for entry in self.scored)

    def of_kind(self, kind: Kind) -> tuple[Mechanism, ...]:
        return tuple(entry.mechanism for entry in self.scored if entry.mechanism.kind is kind)

    def describe(self) -> str:
        lines = [f"Fixtures under {self.root} ({self.files_read} files read)"]
        lines.append(
            "  Ranked by whether the mechanism can seed two scales, which is what S-7.8 "
            "requires. Everything here is declared; what it writes is measured by exercising it."
        )
        lines.extend("  " + entry.describe() for entry in self.scored)
        return "\n".join(lines)


# ================================================================== parsing


def _python_files(root: Path) -> Iterator[Path]:
    """The same bounded walk S-7.3 uses, so the two agree on what the subject is."""
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in SKIP_DIRECTORIES and not name.endswith(".egg-info")
        )
        for name in sorted(names):
            if name.endswith(".py"):
                yield Path(directory) / name


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def _base_names(node: ast.ClassDef) -> list[str]:
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _meta_of(node: ast.ClassDef) -> tuple[str | None, bool]:
    """What a factory's inner `Meta` declares: the model, and whether it is abstract.

    The model has both spellings factory_boy accepts — `model = Book`, and
    `model = "shop.Book"` for the case where importing at class-definition time
    would be a circular import, which is common enough in real projects that
    reading only the first would miss the factories a large one is likeliest to
    have.

    **Abstractness is what `Meta.abstract` says, never the absence of a model.**
    A factory that subclasses another to change one field inherits its parent's
    `Meta.model` and declares no `Meta` at all — `AuthorWithBooksFactory` adding a
    `RelatedFactoryList` is the ordinary shape of it — and reading *no model* as
    *abstract* drops exactly the factories that build the most interesting data.
    Found by a real one.
    """
    model: str | None = None
    abstract = False

    for child in node.body:
        if not (isinstance(child, ast.ClassDef) and child.name == "Meta"):
            continue
        for statement in child.body:
            if not isinstance(statement, ast.Assign):
                continue
            targets = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            value = statement.value
            if "abstract" in targets and isinstance(value, ast.Constant):
                abstract = bool(value.value)
            if "model" not in targets:
                continue
            if isinstance(value, ast.Name):
                model = value.id
            elif isinstance(value, ast.Attribute):
                model = value.attr
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                model = value.value

    return model, abstract


def _factories_in(tree: ast.Module, evidence: str) -> list[Mechanism]:
    """Classes that inherit from something named `…Factory`.

    The suffix rather than the exact base name, because a project with more than
    two factories almost always has its own `BaseFactory`, and matching only
    `DjangoModelFactory` would find the base and none of its children.

    **Always `PARAMETERISED`**, and that is a fact about the library rather than
    about the file: `create_batch(n)` is factory_boy's interface and every factory
    has it. This is the one kind whose scale does not have to be discovered.
    """
    declared: dict[str, tuple[str | None, bool, list[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = _base_names(node)
        if not any(base.endswith(_FACTORY_SUFFIX) for base in bases):
            continue
        model, abstract = _meta_of(node)
        declared[node.name] = (model, abstract, bases)

    return [
        Mechanism(
            kind=Kind.FACTORY,
            name=name,
            evidence=evidence,
            scalability=Scalability.PARAMETERISED,
            model=_inherited_model(name, declared),
            count_argument="create_batch",
        )
        for name, (_, abstract, _) in declared.items()
        if not abstract
    ]


def _inherited_model(
    name: str, declared: Mapping[str, tuple[str | None, bool, list[str]]]
) -> str | None:
    """The model a factory builds, following its bases when it declares none.

    Only within this file: a factory whose base is imported from another module is
    resolvable, and resolving it would mean building an import graph to answer a
    question `exercise` settles by running the thing. `None` is the honest answer
    there, and it costs the mechanism the *names its model* point rather than
    its place in the list.
    """
    seen: set[str] = set()
    current = name
    while current in declared and current not in seen:
        seen.add(current)
        model, _, bases = declared[current]
        if model is not None:
            return model
        current = next((base for base in bases if base in declared), "")
    return None


def _count_argument_of(tree: ast.Module) -> str | None:
    """The flag a management command declares for how many rows to make.

    Read from `add_arguments`, which is where a command states its own interface.
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) == "add_argument"):
            continue
        for argument in node.args:
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and argument.value.lower() in _COUNT_ARGUMENTS
            ):
                return argument.value
    return None


def _called(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _seeds_by_name(name: str) -> bool:
    """Whether a command's name says it makes data.

    Split on the separators command names use, so `seed_demo_data` matches on two
    words and `migrate` matches on none. Matching a substring instead would make
    `generate` match `regenerate_thumbnails`, which writes files and no rows.
    """
    words = {word for word in re.split(r"[^a-z0-9]+", name.lower()) if word}
    return bool(words & _SEED_WORDS)


def _management_command(root: Path, path: Path) -> str | None:
    """The command name, if this file is a management command. S-7.3's rule."""
    parts = path.parts
    if len(parts) < _COMMAND_DEPTH or path.name.startswith("_"):
        return None
    if parts[-2] != "commands" or parts[-3] != "management":
        return None
    del root
    return path.stem


def _fixture_files(root: Path) -> Iterator[Mechanism]:
    """Django's own fixture format: a list of objects under a `fixtures/` directory.

    **The only kind that can be counted without being run**, because the file is
    a list of objects and each one names its model. That is still a declaration —
    nothing here has checked that it loads, and a fixture referencing a model that
    no longer exists fails at `loaddata` with the file unchanged on disk.
    """
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIP_DIRECTORIES)
        if Path(directory).name != "fixtures":
            continue
        for name in sorted(names):
            if not name.endswith(_FIXTURE_SUFFIXES):
                continue
            path = Path(directory) / name
            yield Mechanism(
                kind=Kind.FIXTURE_FILE,
                name=path.stem,
                evidence=_relative(root, path),
                scalability=Scalability.FIXED,
                declared_rows=_rows_declared_by(path),
            )


def _rows_declared_by(path: Path) -> Mapping[str, int]:
    """How many objects of each model a fixture file lists.

    JSON only. YAML needs a parser this project does not depend on, and a fixture
    whose contents cannot be counted is still located and still reported — with an
    empty count rather than a guessed one.
    """
    if path.suffix != ".json":
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, list):
        return {}

    counts: dict[str, int] = {}
    for entry in parsed:
        if isinstance(entry, dict) and isinstance(entry.get("model"), str):
            counts[entry["model"]] = counts.get(entry["model"], 0) + 1
    return counts


def _pytest_fixtures_in(tree: ast.Module, evidence: str) -> list[Mechanism]:
    """Functions decorated `@pytest.fixture`.

    Located because AC 1 names them, and ranked last because of what using one
    costs: a pytest fixture is only callable from inside a pytest session, so
    driving it means running the subject's own test suite — and S-2.4 forbids
    editing a test to change what it seeds.

    Nothing here claims a fixture seeds data. Many return a client, a temporary
    directory or a patched clock, and the name does not say which.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = call.attr if isinstance(call, ast.Attribute) else getattr(call, "id", "")
            if name == "fixture":
                found.append(
                    Mechanism(
                        kind=Kind.PYTEST_FIXTURE,
                        name=node.name,
                        evidence=evidence,
                        scalability=Scalability.UNKNOWN,
                    )
                )
                break
    return found


def discover(root: Path) -> Discovery:
    """Everything this repository states about how it makes data, without running it.

    Works before the environment does, which is why it is separate from
    `exercise`: the Explorer decides *what to try* long before it has a database
    to try it against.
    """
    root = Path(root)
    mechanisms: list[Mechanism] = list(_fixture_files(root))
    files_read = 0

    for path in _python_files(root):
        relative = _relative(root, path)
        files_read += 1

        tree = _parse(path)
        if tree is None:
            continue

        command = _management_command(root, path)
        if command is not None:
            if _seeds_by_name(command):
                argument = _count_argument_of(tree)
                mechanisms.append(
                    Mechanism(
                        kind=Kind.SEED_COMMAND,
                        name=command,
                        evidence=relative,
                        scalability=(
                            Scalability.PARAMETERISED if argument else Scalability.UNKNOWN
                        ),
                        count_argument=argument,
                    )
                )
            continue

        mechanisms.extend(_factories_in(tree, relative))
        mechanisms.extend(_pytest_fixtures_in(tree, relative))

    return Discovery(root=root, scored=rank(mechanisms), files_read=files_read)


# ================================================================== ranking

# What each kind costs to turn into a `scale(n)`, before anything about the
# individual mechanism. Every number is a claim about S-7.8: can this be driven
# at two sizes, and does it say what it makes?
_KIND_SCORE: dict[Kind, tuple[int, str]] = {
    Kind.FACTORY: (
        4,
        "a factory names its own model and factory_boy gives every one of them "
        "create_batch(n), so the size is an argument without anything being discovered",
    ),
    Kind.SEED_COMMAND: (
        3,
        "a management command is the subject's own way of making its own data, run "
        "through the interface it already supports",
    ),
    Kind.FIXTURE_FILE: (
        2,
        "a fixture file loads without any code being written, and holds whatever it holds",
    ),
    Kind.PYTEST_FIXTURE: (
        1,
        "a pytest fixture is only callable from inside a pytest session, and S-2.4 "
        "refuses to edit a test to change what it seeds",
    ),
}

_PARAMETERISED_BONUS = 4
_FIXED_PENALTY = -6
_NAMES_MODEL_BONUS = 1
_COUNTABLE_BONUS = 1


def score(mechanism: Mechanism) -> Scored:
    """How much this mechanism looks like a `scale(n)`, and why.

    Every term is a reason to expect S-7.8 to be satisfiable or not. Nothing here
    measures anything — a factory scoring nine may raise on its first row.
    """
    base, reason = _KIND_SCORE[mechanism.kind]
    total = base
    reasons = [reason]

    if mechanism.scalability is Scalability.PARAMETERISED:
        total += _PARAMETERISED_BONUS
        reasons.append(
            "the count is an argument, and two scales is what S-7.8 requires before a "
            "workload counts as doing work"
        )
    elif mechanism.scalability is Scalability.FIXED:
        total += _FIXED_PENALTY
        reasons.append(
            "a fixed set of rows with fixed primary keys: loading it twice is a collision "
            "rather than twice the data, so it cannot produce a second scale"
        )

    if mechanism.model is not None:
        total += _NAMES_MODEL_BONUS
        reasons.append("names the model it builds, so the entity is known before it is run")

    if mechanism.declared_rows:
        total += _COUNTABLE_BONUS
        reasons.append("its contents can be counted without running it")

    return Scored(mechanism=mechanism, score=total, reasons=tuple(reasons))


def rank(mechanisms: Sequence[Mechanism]) -> tuple[Scored, ...]:
    """Highest first, and deterministic — S-7.3's tie-break, for its reason."""
    return tuple(
        sorted(
            (score(mechanism) for mechanism in mechanisms),
            key=lambda entry: (-entry.score, entry.mechanism.kind.name, entry.mechanism.name),
        )
    )


def _builds(mechanism: Mechanism, entity: str | None) -> bool:
    """Whether this mechanism makes the entity the workload needs.

    Matched on the last dotted segment, case-insensitively, because a factory's
    `Meta.model` may be `Book`, `shop.Book` or `"shop.Book"` and a workload's
    entity is a model label — three spellings of one model, and a comparison that
    demanded one of them would silently prefer the wrong factory.
    """
    if entity is None or mechanism.model is None:
        return False
    return mechanism.model.rsplit(".", 1)[-1].lower() == entity.rsplit(".", 1)[-1].lower()


def prefer(discovery: Discovery, *, entity: str | None = None) -> Chosen:
    """AC 2: what to use, or why synthesis is needed after all.

    **`entity` was added by the Epic 7 composition check, and it was a real
    defect.** Ranking scores a mechanism by how well it can seed *two scales*,
    which is a property of the mechanism and not of the workload — so two
    factories that are equally good at that tie, and the tie-break is
    alphabetical. Composed, that chose `AuthorFactory` over `BookFactory`, seeded
    a hundred authors, drove `/books/` and measured an empty list: one query and
    thirteen bytes. Every S-7.5 test passed, because none of them had a second
    factory to choose between.

    Nothing here infers which entity a route serves — that is the Explorer's to
    know, and guessing it from a URL segment would be the kind of inference this
    module has avoided everywhere else. Given one, a mechanism that builds it
    wins; not given one, the ranking is unchanged.

    Preference is over what *exists*; synthesis is the floor, not a competitor.
    But existing is not sufficient — a mechanism that cannot vary its size cannot
    give S-7.8 two scales, so a repository holding nothing but a `loaddata`
    fixture needs S-7.6 despite having a fixture, and says so with the fixture
    named.
    """
    usable = [
        entry
        for entry in discovery.scored
        if entry.mechanism.scalability is not Scalability.FIXED
        and entry.mechanism.kind is not Kind.PYTEST_FIXTURE
    ]
    if usable:
        wanted = [entry for entry in usable if _builds(entry.mechanism, entity)]
        return (wanted or usable)[0].mechanism

    if discovery.mechanisms:
        return NeedsSynthesis(
            reason=(
                "everything located here is either a fixed set of rows or callable only from "
                "inside a pytest session, and neither can be driven at two scales"
            ),
            located=discovery.mechanisms,
        )
    return NeedsSynthesis(
        reason="no factory, seeding command or fixture file was found in this repository"
    )


# ================================================================== exercising

# Runs in the *subject's* interpreter. Counts every model's rows and records what
# each points at, because `per_parent` is a ratio between two of them and which
# is the parent is a fact about the foreign keys rather than about the numbers.
_COUNT_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.apps import apps

answer = {"models": {}, "problems": []}

for model in apps.get_models():
    label = model._meta.label
    points_to = []
    for field in model._meta.get_fields():
        if getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False):
            related = getattr(field, "related_model", None)
            if related is not None and getattr(field, "concrete", False):
                points_to.append({"name": field.name, "target": related._meta.label})
    try:
        count = model._default_manager.count()
    except Exception as error:
        answer["problems"].append(label + ": " + type(error).__name__ + ": " + str(error))
        continue
    answer["models"][label] = {"count": count, "points_to": points_to}

print("__MARKER__" + json.dumps(answer))
"""

_COUNT = _COUNT_SOURCE.replace("__MARKER__", _MARKER)

# Runs one factory at a size, in the subject's interpreter. The factory is
# addressed by module and class name, both of which the parse established.
_FACTORY_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

REQUEST = json.loads(sys.argv[1])

from importlib import import_module

module = import_module(REQUEST["module"])
factory = getattr(module, REQUEST["factory"])
factory.create_batch(REQUEST["count"])

print("__MARKER__" + json.dumps({"ok": True}))
"""

_FACTORY = _FACTORY_SOURCE.replace("__MARKER__", _MARKER)

# Groups the child table by its foreign key and counts. The parents holding
# nothing are counted separately, because they are absent from the grouping and
# their absence is the difference between a uniform spread and a skewed one.
_SPREAD_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

REQUEST = json.loads(sys.argv[1])

from django.apps import apps
from django.db.models import Count

child = apps.get_model(REQUEST["child"])
parent = apps.get_model(REQUEST["parent"])
field = REQUEST["field"]

grouped = child._default_manager.values(field).annotate(n=Count("pk"))
per_parent = [row["n"] for row in grouped if row[field] is not None]

held = child._default_manager.values_list(field, flat=True)
childless = parent._default_manager.exclude(pk__in=held).count()

print("__MARKER__" + json.dumps({"per_parent": per_parent, "childless": childless}))
"""

_SPREAD = _SPREAD_SOURCE.replace("__MARKER__", _MARKER)


@dataclass(frozen=True)
class Spread:
    """How many children each parent got, and whether that is uniform.

    Uniformity is the only shape established here, and it is established exactly
    rather than fitted. S-3.3's argument is that `Σk²` is minimised when every
    parent is equal, which makes `UNIFORM` the claim most worth being sure of and
    the one most damaging to assume.
    """

    parent: str
    child: str
    per_parent: tuple[int, ...]

    @property
    def uniform(self) -> bool:
        return len(set(self.per_parent)) <= 1

    @property
    def distribution(self) -> Distribution | None:
        """`UNIFORM` where every parent is equal, and nothing otherwise.

        A spread that is not uniform does **not** become `POWER_LAW` by
        elimination. Which of the two skewed shapes a pile of rows has is a fit,
        and S-7.7 is the story that owns distribution as a parameter — so this
        reports that it cannot say, and `recipe_from` refuses rather than
        choosing one of three values at random.
        """
        return Distribution.UNIFORM if self.uniform else None


@dataclass(frozen=True)
class Exercise:
    """What running a mechanism actually wrote.

    The measured half, and the only thing a recipe may be built from. `grew` is a
    difference between two counts taken either side of one invocation — not a
    reading of the file, and not the number that was asked for.
    """

    mechanism: Mechanism
    requested: int
    before: Mapping[str, int]
    after: Mapping[str, int]
    spread: Spread | None = None
    problems: tuple[str, ...] = ()

    @property
    def grew(self) -> Mapping[str, int]:
        return {
            label: self.after[label] - self.before.get(label, 0)
            for label in self.after
            if self.after[label] - self.before.get(label, 0) > 0
        }

    @property
    def wrote_nothing(self) -> bool:
        return not self.grew

    @property
    def entity(self) -> str | None:
        """The model this seeds `n` of.

        The model whose growth is closest to what was asked for, which is what
        `scale(n)` means: n of the entity. Taking the largest grower instead would
        name the child of a one-to-many, since seeding ten parents with four
        children each grows the child model by forty.
        """
        if not self.grew:
            return None
        return min(self.grew, key=lambda label: (abs(self.grew[label] - self.requested), label))

    def describe(self) -> str:
        lines = [
            f"{self.mechanism.name} at n={self.requested} wrote "
            f"{sum(self.grew.values())} row(s) across {len(self.grew)} model(s)"
        ]
        lines.extend(f"  {label}: +{grown}" for label, grown in sorted(self.grew.items()))
        if self.spread is not None:
            shape = "uniform" if self.spread.uniform else "not uniform"
            lines.append(
                f"  {self.spread.child} per {self.spread.parent}: "
                f"{sorted(self.spread.per_parent)} ({shape})"
            )
        lines.extend(f"  problem: {problem}" for problem in self.problems)
        return "\n".join(lines)


def _run_in_subject(  # noqa: PLR0913 - S-7.4's shape, for S-7.4's reason: what
    # to run, what to pass it, where, with which interpreter and under which
    # settings are five facts and three belong to the sandbox.
    program: str,
    arguments: Sequence[str],
    *,
    surface: Surface,
    python: Sequence[str],
    settings: str,
    timeout: float,
) -> Mapping[str, Any]:
    """Run one program in the subject's interpreter and read its answer.

    Returns `Any` values: another interpreter's JSON, whose shape nothing here can
    know statically. Every field is converted at the call site rather than trusted.
    """
    try:
        result = surface.run(
            [*python, "-c", program, *arguments],
            timeout=timeout,
            env={"DJANGO_SETTINGS_MODULE": settings},
        )
    except ExecutionError as error:
        raise FixtureError(str(error)) from error

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        message = f"the subject's interpreter did not answer (exit {result.exit_code}): {said}"
        raise FixtureError(message)

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        message = f"the subject's answer was not JSON: {error}"
        raise FixtureError(message) from error
    return payload


def _settings_for(root: Path) -> str:
    settings = settings_module(root)
    if settings is None:
        message = (
            "no DJANGO_SETTINGS_MODULE was found in manage.py, wsgi.py or asgi.py, so the "
            "subject cannot be asked what it holds"
        )
        raise FixtureError(message)
    return settings.value


def count_models(
    root: Path,
    *,
    python: Sequence[str],
    surface: Surface | None = None,
    timeout: float = EXERCISE_TIMEOUT_SECONDS,
) -> Mapping[str, Mapping[str, Any]]:
    """Every model's row count and foreign keys, asked of the framework.

    Model labels rather than table names, deliberately: `FixtureRecipe.entity`
    names an entity, `db_table` is renameable and often is, and a count keyed by
    table would have to be joined back through the ORM to mean anything.
    """
    payload = _run_in_subject(
        _COUNT,
        (),
        surface=surface or HostSurface(Path(root)),
        python=python,
        settings=_settings_for(root),
        timeout=timeout,
    )
    models: Mapping[str, Mapping[str, Any]] = payload.get("models", {})
    return models


def _counts_of(models: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    return {label: int(entry.get("count", 0)) for label, entry in models.items()}


def exercise_factory(  # noqa: PLR0913 - the subject, its interpreter, which
    # mechanism, where it imports from and how many are five independent facts;
    # the module is not derivable from the file path, which is the point of the
    # parameter rather than an omission.
    root: Path,
    *,
    python: Sequence[str],
    mechanism: Mechanism,
    module: str,
    count: int,
    surface: Surface | None = None,
    timeout: float = EXERCISE_TIMEOUT_SECONDS,
) -> Exercise:
    """Run one located factory at a size and measure what it wrote.

    `module` is supplied rather than derived from the file path: a repository's
    import root is not always its checkout root — `src/` layouts are ordinary —
    and guessing it wrong produces `ModuleNotFoundError` at the point where the
    interesting failure should be a factory raising.

    Raises:
        FixtureError: the mechanism is not a factory, or the subject failed.
    """
    if mechanism.kind is not Kind.FACTORY:
        message = (
            f"{mechanism.name} is a {mechanism.kind.value} and this runs factories; a command "
            "is run through the subject's own manage.py and a fixture file through loaddata"
        )
        raise FixtureError(message)

    root = Path(root)
    settings = _settings_for(root)
    where = surface or HostSurface(root)
    before = count_models(root, python=python, surface=where, timeout=timeout)

    _run_in_subject(
        _FACTORY,
        (json.dumps({"module": module, "factory": mechanism.name, "count": count}),),
        surface=where,
        python=python,
        settings=settings,
        timeout=timeout,
    )

    after = count_models(root, python=python, surface=where, timeout=timeout)
    counted_before = _counts_of(before)
    counted_after = _counts_of(after)

    spread = None
    pair = _related_pair(after, counted_before, counted_after)
    if pair is not None:
        child, field, parent = pair
        spread = measure_spread(
            root, python=python, child=child, field=field, parent=parent, timeout=timeout
        )

    return Exercise(
        mechanism=mechanism,
        requested=count,
        before=counted_before,
        after=counted_after,
        spread=spread,
    )


def _related_pair(
    models: Mapping[str, Mapping[str, Any]],
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> tuple[str, str, str] | None:
    """The `(child, field, parent)` among the grown models, or nothing.

    Returns nothing where fewer than two models grew: there is no per-parent shape
    when there are no children, and inventing a `Spread` for that case would make
    `uniform` true of a fixture with no parents to be uniform across.
    """
    grew = {label: after[label] - before.get(label, 0) for label in after}
    grown = {label for label, delta in grew.items() if delta > 0}
    if len(grown) < 2:  # noqa: PLR2004 - a parent and a child is two
        return None

    for child in sorted(grown):
        for pointer in models.get(child, {}).get("points_to", []):
            target = str(pointer.get("target", ""))
            if target in grown and target != child and grew[target] > 0:
                return child, str(pointer.get("name", "")), target
    return None


def measure_spread(  # noqa: PLR0913 - a GROUP BY needs the child, the field it
    # groups on and the parent it counts absences against; none of the three is
    # recoverable from the other two.
    root: Path,
    *,
    python: Sequence[str],
    child: str,
    field: str,
    parent: str,
    surface: Surface | None = None,
    timeout: float = EXERCISE_TIMEOUT_SECONDS,
) -> Spread:
    """Count the children each parent actually has.

    A real `GROUP BY`, because the alternative is arithmetic on two totals — forty
    children over ten parents divides evenly and says nothing about whether one
    parent holds thirty-one of them. That division is the assumption S-3.3's
    argument is *about*, so making it here would put the blindest possible reading
    into the field that exists to record which reading was taken.

    **Parents holding nothing are counted too.** They do not appear in a `GROUP
    BY` over the child table, and leaving them out makes *nine parents with one
    child and one with none* indistinguishable from *nine parents with one child*
    — the first is not uniform and the second is.
    """
    payload = _run_in_subject(
        _SPREAD,
        (json.dumps({"child": child, "field": field, "parent": parent}),),
        surface=surface or HostSurface(Path(root)),
        python=python,
        settings=_settings_for(root),
        timeout=timeout,
    )
    counts = [int(value) for value in payload.get("per_parent", [])]
    childless = int(payload.get("childless", 0))
    return Spread(
        parent=parent,
        child=child,
        per_parent=tuple(sorted(counts + [0] * childless, reverse=True)),
    )


def factory_seeder(
    mechanism: Mechanism,
    *,
    module: str,
    source: str | None = None,
    surface: Surface | None = None,
) -> Callable[..., tuple[FixtureRecipe, Mapping[str, int]]]:
    """A seeder that fills the subject using **its own factory**, for S-7.8 to drive.

    **Added by the Epic 7 composition check**, which found that AC 2 — *use them
    in preference to synthesis* — was honoured inside this module and nowhere
    else: the only code that seeded at scale synthesized from the schema
    unconditionally, so a repository shipping a perfectly good `BookFactory` was
    measured against rows this system invented.

    `module` is the caller's to supply, exactly as `exercise_factory` requires and
    for the same reason: a `src/` layout means the checkout root is not the import
    root, and guessing produces `ModuleNotFoundError` where the interesting
    failure should be a factory raising.
    """

    def seed(
        *, root: Path, python: Sequence[str], scale: int, timeout: float
    ) -> tuple[FixtureRecipe, Mapping[str, int]]:
        exercised = exercise_factory(
            root,
            python=python,
            mechanism=mechanism,
            module=module,
            count=scale,
            surface=surface,
            timeout=timeout,
        )
        return recipe_from(exercised, source=source), exercised.grew

    return seed


def recipe_from(exercise: Exercise, *, source: str | None = None) -> FixtureRecipe:
    """AC 3: the recipe S-4.1's workload artifact carries, built from what was written.

    Every field comes from a measurement. `entity` is the model that grew by what
    was asked for, `per_parent` is the ratio the foreign keys say is a ratio, and
    `distribution` is `UNIFORM` only when every parent was measured to hold the
    same number of children.

    Raises:
        FixtureError: nothing was written, or the spread is not uniform. The
            second is a refusal rather than a gap: `Distribution` has three values
            and a non-uniform pile of rows is not made `POWER_LAW` by not being
            uniform. S-7.7 owns distribution as a parameter; naming one here would
            put a fitted label on the field carrying S-3.3's qualification.
    """
    if exercise.wrote_nothing:
        said = "; ".join(exercise.problems) or "no model's row count changed"
        message = (
            f"{exercise.mechanism.name} wrote nothing at n={exercise.requested}: {said}. A recipe "
            "built from this would describe a fixture that does not exist, and every measurement "
            "taken against it would be a measurement of an empty database"
        )
        raise FixtureError(message)

    entity = exercise.entity
    if entity is None:  # pragma: no cover - `wrote_nothing` is the only way here
        message = "nothing grew, so there is no entity to name"
        raise FixtureError(message)

    spread = exercise.spread
    if spread is not None and spread.distribution is None:
        message = (
            f"{spread.child} is spread unevenly across {spread.parent} "
            f"({sorted(spread.per_parent)}), and Distribution has no value for *not uniform*. "
            "Which skewed shape it is, is a fit rather than a count, and S-7.7 is the story that "
            "makes distribution a parameter of scale() instead of a label on found data"
        )
        raise FixtureError(message)

    per_parent = 1
    if spread is not None:
        per_parent = spread.per_parent[0]
        if per_parent < 1:
            # Measured zero, not missing. The child model grew and none of it
            # landed under these parents, so the pair this measured is not the
            # relationship the fixture built — and clamping to 1 would record a
            # child per parent that was counted and found not to be there.
            message = (
                f"every {spread.parent} was measured to hold no {spread.child}, so the pair "
                "this counted is not the relationship the fixture populated"
            )
            raise FixtureError(message)

    return FixtureRecipe(
        entity=entity,
        per_parent=per_parent,
        # S-7.7 widened the artifact, and a measured spread already knows this:
        # the parent count is how many parents were counted. Recording it makes a
        # discovered fixture describable in the same terms as a synthesized one,
        # which is what lets the two be compared at all.
        parents=len(spread.per_parent) if spread is not None else None,
        distribution=Distribution.UNIFORM,
        source=source or f"{exercise.mechanism.kind.value} {exercise.mechanism.name}",
        seed=None,
    )

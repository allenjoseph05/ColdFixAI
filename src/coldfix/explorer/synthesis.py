"""Making rows a repository cannot make for itself, and admitting what they are.

Epic 7, S-7.6. S-7.5 locates a project's own factories and commands and prefers
them; `NeedsSynthesis` is what it returns when there is nothing to prefer, and
this is the floor underneath it. `02-architecture.md` §1.2 names the fallback in
one line — *synthesize from schema by walking FK chains* — and the four
acceptance criteria are what that costs.

**Nothing here calls a model.** Walking foreign keys is a topological sort and
picking the first of a field's `choices` is indexing.

**The ORM's declaration is not the database's constraint**, and AC 3 names the
instrument that settles the difference. `blank=True` is a *form* concept and says
nothing about `NOT NULL`; a `UniqueConstraint` over two columns is invisible on
either of them; a migration nobody applied leaves the database stricter than the
models that describe it. So the schema read here is a **declaration** — a good
first plan and not a specification — and `IntegrityError` is the enforcement. The
loop is plan, attempt, read what the database refused, revise.

**An error message is not a diagnosis.** S-7.2's rule, and this is the second
place it bites: the text of an integrity error differs by driver, by server
version and by locale. `psycopg` carries structured diagnostics — the column and
constraint by name, from the server — so those are read first, and the text is
parsed only where the driver offers nothing better. Which of the two settled it
travels with the result, because a revision built on a regex over someone's
locale-translated error is worth less than one built on `diag.column_name` and a
reader deserves to know which they have.

**Synthesized data is uniform by construction, and that is a limitation this
module states rather than hides.** `07-use-cases.md` §5 is blunt about it: *if
every generated customer has three orders, an N+1 that only hurts customers with
three thousand orders stays invisible*, and S-3.3 proved the uniform fixture is
the provably blindest one for any per-parent cost. So the recipe this emits says
`UNIFORM` because that is what was built, `Synthesis.blindness` says it in words,
and S-7.7 is the story that makes the shape a parameter rather than a
consequence.

**An unknown field type is a refusal, not a default.** A column this cannot fill
is AC 4's case — reported with the model, the field and its type — because a
`None` written into a `NOT NULL` column fails at the database with a worse
message, and a zero written into a column that means something else produces rows
that are valid and meaningless.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from coldfix.bench.execute import ExecutionError
from coldfix.explorer.entrypoints import settings_module
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.primitives.scaling import Allocation, Distribution, allocate
from coldfix.screening.workload import FixtureRecipe

SYNTHESIS_TIMEOUT_SECONDS = 300.0
"""Inherited from S-7.5's exercising budget: this writes rows through the ORM,
one `INSERT` at a time, against whatever database the subject configured."""

MAX_REVISIONS = 6
"""How many times a plan may be revised before synthesis reports failure.

Each revision is a fact the database taught us — one missing column, one unique
collision — so the cap is a bound on *unknown* constraints rather than on effort.
Six is above the deepest chain S-0.3 met across three repositories and low enough
that a subject refusing every plan for the same reason stops rather than looping.
"""

_MARKER = "<<<COLDFIX-SYNTHESIS>>>"

# What this can put in a column, by Django's own internal type name. Deliberately
# a table rather than a chain of `isinstance` checks: the subject's field classes
# live in the subject's interpreter and only their names cross the boundary.
#
# Every entry is a value that satisfies the column and means nothing, which is the
# honest thing for synthesized data to be. Nothing here tries to look real —
# `07-use-cases.md` is explicit that realistic *shape* is what matters and this
# module cannot supply it.
_FILLERS: frozenset[str] = frozenset(
    {
        "BigIntegerField",
        "BinaryField",
        "BooleanField",
        "CharField",
        "DateField",
        "DateTimeField",
        "DecimalField",
        "DurationField",
        "EmailField",
        "FileField",
        "FloatField",
        "GenericIPAddressField",
        "IntegerField",
        "JSONField",
        "PositiveBigIntegerField",
        "PositiveIntegerField",
        "PositiveSmallIntegerField",
        "SlugField",
        "SmallIntegerField",
        "TextField",
        "TimeField",
        "URLField",
        "UUIDField",
    }
)

_TEXTUAL: frozenset[str] = frozenset(
    {"CharField", "EmailField", "FileField", "SlugField", "TextField", "URLField"}
)

_NUMERIC: frozenset[str] = frozenset(
    {
        "BigIntegerField",
        "DecimalField",
        "FloatField",
        "IntegerField",
        "PositiveBigIntegerField",
        "PositiveIntegerField",
        "PositiveSmallIntegerField",
        "SmallIntegerField",
    }
)

# The two databases this project supports, in the two spellings each uses. Read
# only where the driver offered no structured diagnostics — see `_refusal_of`.
_NOT_NULL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # SQLite: NOT NULL constraint failed: shop_book.publisher_id
    re.compile(r"NOT NULL constraint failed: (?P<table>\w+)\.(?P<column>\w+)"),
    # Postgres: null value in column "publisher_id" of relation "shop_book"
    re.compile(r'null value in column "(?P<column>\w+)" of relation "(?P<table>\w+)"'),
)

_UNIQUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # SQLite: UNIQUE constraint failed: shop_author.email
    re.compile(r"UNIQUE constraint failed: (?P<table>\w+)\.(?P<column>\w+)"),
    # Postgres names the index, not the column: shop_author_email_key
    re.compile(r'duplicate key value violates unique constraint "(?P<constraint>[\w]+)"'),
)


class SynthesisError(Exception):
    """Rows could not be synthesized, and the report says at which column."""


class Violation(StrEnum):
    """What the database refused, which decides how the plan is revised.

    Two kinds because there are two different repairs: a missing value needs a
    value (and possibly a parent row to point at), and a repeated value needs a
    different one. A third — *something else* — is not revised at all, because a
    check constraint this cannot read is not made satisfiable by guessing again.
    """

    NOT_NULL = "a column that must hold a value was left empty"
    UNIQUE = "a value this wrote was already there"
    OTHER = "the database refused for a reason this cannot act on"


class Learned(StrEnum):
    """How a refusal was read. S-7.2's rule: the instrument travels with the answer."""

    DIAGNOSTICS = "the server's own structured diagnostics"
    MESSAGE = "a pattern over the driver's error text"
    NEITHER = "neither; the refusal names no column this could find"


@dataclass(frozen=True)
class Refusal:
    """One `IntegrityError`, read as far as it can honestly be read."""

    violation: Violation
    learned: Learned
    table: str | None = None
    column: str | None = None
    constraint: str | None = None
    message: str = ""

    @property
    def actionable(self) -> bool:
        """Whether this names something a revision can act on.

        A `UNIQUE` violation whose constraint names an index rather than a column
        is still actionable — the index name begins with the table and holds the
        column — but a refusal naming nothing is not, and revising on it would be
        the same plan submitted twice.
        """
        return self.violation is not Violation.OTHER and bool(self.column or self.constraint)

    def describe(self) -> str:
        where = ".".join(part for part in (self.table, self.column) if part) or "?"
        return f"{self.violation.value} ({where}, read from {self.learned.value})"


@dataclass(frozen=True)
class SchemaField:
    """One column, as the ORM describes it.

    A *declaration*. `null` is what the model says, and the database is the thing
    that decides — which is the whole of AC 3.
    """

    name: str
    column: str
    kind: str
    null: bool = False
    has_default: bool = False
    unique: bool = False
    choices: tuple[str, ...] = ()
    max_length: int | None = None
    relates_to: str | None = None
    auto: bool = False

    @property
    def needs_a_value(self) -> bool:
        """Whether a first plan must supply this.

        Auto fields and fields with defaults are the database's job. **`blank` is
        deliberately not consulted**: it governs form validation and a
        `blank=True, null=False` column is required by every database and optional
        in every admin form, which is the commonest way a plan built from the
        models alone fails on its first row.
        """
        return not self.auto and not self.has_default and not self.null

    @property
    def fillable(self) -> bool:
        return self.relates_to is not None or self.kind in _FILLERS


@dataclass(frozen=True)
class SchemaModel:
    """One model's columns and the parents it requires."""

    label: str
    table: str
    fields: tuple[SchemaField, ...]

    def field_named(self, name: str) -> SchemaField | None:
        return next((f for f in self.fields if f.name == name), None)

    def field_for_column(self, column: str) -> SchemaField | None:
        return next((f for f in self.fields if f.column == column), None)

    @property
    def required_parents(self) -> tuple[str, ...]:
        """Models that must hold a row before this one can."""
        return tuple(
            f.relates_to for f in self.fields if f.relates_to is not None and f.needs_a_value
        )


@dataclass(frozen=True)
class Value:
    """What to put in one column, as an instruction the subject can carry out.

    Three kinds, because three different things have to happen in the subject's
    interpreter rather than here: a literal is copied, a sequence has to differ
    per row, and a reference has to become a primary key that only exists once the
    parent step has run.
    """

    kind: str
    literal: object = None
    template: str = ""
    model: str = ""
    assignment: tuple[int, ...] = ()
    """For a reference: which parent, by position, each child row points at.

    S-7.7. The shape lives here rather than in the subject, because it is
    arithmetic — S-3.3's `allocate` is deterministic and already tested — and a
    subject that decided its own spread would be a second generator to keep in
    step with the first. Empty means round-robin, which is what uniform is."""

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "literal": self.literal,
            "template": self.template,
            "model": self.model,
            "assignment": list(self.assignment),
        }


@dataclass(frozen=True)
class Step:
    """Create `count` rows of one model with these columns filled."""

    model: str
    count: int
    values: Mapping[str, Value]

    def as_json(self) -> dict[str, object]:
        return {
            "model": self.model,
            "count": self.count,
            "values": {name: value.as_json() for name, value in self.values.items()},
        }


@dataclass(frozen=True)
class Plan:
    """Every step, parents before children."""

    target: str
    count: int
    per_parent: int
    steps: tuple[Step, ...]
    distribution: Distribution = Distribution.UNIFORM
    allocation: Allocation | None = None
    """How the target's children landed on their parents, where the target has
    parents at all. `None` for a model nothing points at from above."""

    def as_json(self) -> dict[str, object]:
        return {"steps": [step.as_json() for step in self.steps]}

    def describe(self) -> str:
        lines = [f"synthesize {self.count} × {self.target} ({self.distribution.value})"]
        lines.extend(
            f"  {step.count} × {step.model}: {', '.join(sorted(step.values)) or 'defaults only'}"
            for step in self.steps
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class Synthesis:
    """What was built, how many attempts it took, and what the database taught.

    `revisions` is not bookkeeping. Each one is a constraint the models did not
    declare, and a subject that needed four of them is a subject whose schema and
    whose database disagree — which is worth reporting even when synthesis
    succeeded.
    """

    plan: Plan
    created: Mapping[str, int]
    revisions: tuple[Refusal, ...] = ()

    @property
    def blindness(self) -> str:
        """What this fixture cannot show, stated with the fixture.

        Carried on the result rather than left in a docstring because it travels:
        an exclusion drawn under synthesized data is only true of uniform data,
        and `CLAUDE.md`'s rule is that exclusions carry their preconditions.
        """
        allocation = self.plan.allocation
        if allocation is None:
            return (
                "nothing points at this model from above, so there is no per-parent shape and "
                "no per-parent cost for one to hide"
            )
        if allocation.distribution is Distribution.UNIFORM:
            return (
                f"every parent holds the same {allocation.largest} child(ren). Uniform data is "
                "provably the blindest shape for any per-parent cost (S-3.3), so a cost that "
                "only appears on a heavy parent cannot appear here — pass a distribution"
            )
        return (
            f"{allocation.distribution.value}: the heaviest of {allocation.groups} parents holds "
            f"{allocation.largest} of {allocation.total} children, and the largest tenth holds "
            f"{allocation.head_mass:.0%}. A per-parent cost is paid here where a uniform fixture "
            "would never pay it — and a *flat* result under this shape is a much stronger "
            "exclusion than a flat result under uniform data"
        )

    def describe(self) -> str:
        lines = [self.plan.describe()]
        lines.extend(f"  created {label}: {count}" for label, count in sorted(self.created.items()))
        lines.extend(f"  learned: {refusal.describe()}" for refusal in self.revisions)
        lines.append(f"  blindness: {self.blindness}")
        return "\n".join(lines)

    def recipe(self) -> FixtureRecipe:
        """AC 3 of S-7.5 and AC 3 of S-7.7: the artifact S-4.1 carries.

        Every field is a statement about what was *built*, because synthesis
        chooses the shape rather than reading one — the asymmetry with S-7.5,
        where the distribution is measured and a non-uniform spread is refused.

        **`per_parent` is the heaviest parent, not the mean.** S-4.1 widened the
        field at S-7.7 rather than have it mean two things: the whole reason to
        build a long tail is the request that takes minutes while every other
        request stays fast, and that request is made by the heaviest parent.
        `parents` travels with it so `allocate` can rebuild the same fixture
        anywhere.
        """
        allocation = self.plan.allocation
        return FixtureRecipe(
            entity=self.plan.target,
            per_parent=allocation.largest if allocation else self.plan.per_parent,
            parents=allocation.groups if allocation else None,
            distribution=self.plan.distribution,
            source=f"synthesized from schema ({len(self.plan.steps)} step(s))",
            seed=None,
        )


# ================================================================== reading the schema

# Runs in the *subject's* interpreter. Reports what the ORM declares and nothing
# about what the database enforces — the second is `IntegrityError`'s to say.
_SCHEMA_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.apps import apps

models = {}

for model in apps.get_models():
    fields = []
    for f in model._meta.get_fields():
        if not getattr(f, "concrete", False):
            continue
        choices = [str(c[0]) for c in (getattr(f, "choices", None) or [])]
        related = getattr(f, "related_model", None)
        fields.append({
            "name": f.name,
            "column": f.column,
            "kind": f.get_internal_type(),
            "null": bool(f.null),
            "has_default": f.has_default() or bool(getattr(f, "auto_now", False))
                or bool(getattr(f, "auto_now_add", False)),
            "unique": bool(f.unique),
            "choices": choices,
            "max_length": getattr(f, "max_length", None),
            "relates_to": related._meta.label if related is not None else None,
            "auto": bool(getattr(f, "auto_created", False)) or f.get_internal_type() in (
                "AutoField", "BigAutoField", "SmallAutoField",
            ),
        })
    models[model._meta.label] = {"table": model._meta.db_table, "fields": fields}

print("__MARKER__" + json.dumps({"models": models}))
"""

_SCHEMA = _SCHEMA_SOURCE.replace("__MARKER__", _MARKER)

# Carries out one plan. Every decision was made by the planner; this creates rows
# and, when the database refuses, reports the refusal as structurally as the
# driver allows.
#
# `diag` is psycopg's — the server's own error fields, naming the column and the
# constraint. It is absent on SQLite, and the planner falls back to patterns over
# the message there. The distinction is reported rather than smoothed over.
_APPLY_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.apps import apps
from django.db import IntegrityError, transaction

PLAN = json.loads(sys.argv[1])

created = {}
pool = {}


def resolve(spec, index):
    kind = spec["kind"]
    if kind == "literal":
        return spec["literal"]
    if kind == "sequence":
        return spec["template"].replace("{i}", str(index))
    if kind == "reference":
        rows = pool.get(spec["model"]) or []
        if not rows:
            raise LookupError("no rows of " + spec["model"] + " were created to point at")
        # An empty assignment is round-robin, which is what uniform is. A
        # non-empty one is S-7.7's shape, decided by the planner: the subject
        # indexes it and decides nothing.
        assignment = spec.get("assignment") or []
        if assignment:
            return rows[assignment[index % len(assignment)] % len(rows)]
        return rows[index % len(rows)]
    raise LookupError("unknown value kind " + kind)


answer = {"ok": True, "created": created}
at = {"position": 0, "model": ""}

# ONE transaction for the whole plan, not one per step. A failed attempt must
# leave the database exactly as it found it, because the next attempt is the same
# plan with one column changed — and rows a previous attempt committed collide
# with the rows this one writes. Found by a real run: the first attempt created
# its authors, failed on a unique book code, and the revision then failed on the
# author e-mail it had itself inserted a second earlier.
#
# The exception is caught OUTSIDE the block so the rollback has happened by the
# time anything is reported; querying inside a broken atomic block raises
# TransactionManagementError and would hide the integrity error underneath it.
try:
    with transaction.atomic():
        for position, step in enumerate(PLAN["steps"]):
            at = {"position": position, "model": step["model"]}
            model = apps.get_model(step["model"])
            made = []
            for index in range(step["count"]):
                values = {}
                for name, spec in step["values"].items():
                    value = resolve(spec, index)
                    if spec["kind"] == "reference":
                        values[name + "_id"] = value
                    else:
                        values[name] = value
                made.append(model._default_manager.create(**values).pk)
            pool[step["model"]] = made
            created[step["model"]] = created.get(step["model"], 0) + len(made)
except IntegrityError as error:
    cause = getattr(error, "__cause__", None)
    diag = getattr(cause, "diag", None)
    answer = {
        "ok": False,
        "created": {},
        "step": at["position"],
        "model": at["model"],
        "message": str(error),
        "column": getattr(diag, "column_name", None),
        "constraint": getattr(diag, "constraint_name", None),
        "table": getattr(diag, "table_name", None),
    }
except Exception as error:
    answer = {
        "ok": False,
        "created": {},
        "step": at["position"],
        "model": at["model"],
        "message": type(error).__name__ + ": " + str(error),
        "fatal": True,
    }

print("__MARKER__" + json.dumps(answer))
"""

_APPLY = _APPLY_SOURCE.replace("__MARKER__", _MARKER)


def _run_in_subject(  # noqa: PLR0913 - S-7.4's shape, for S-7.4's reason: what to
    # run, what to pass it, where, with which interpreter and under which settings
    # are five facts and three of them belong to the sandbox.
    program: str,
    arguments: Sequence[str],
    *,
    surface: Surface,
    python: Sequence[str],
    settings: str,
    timeout: float,
) -> Mapping[str, Any]:
    """Run one program in the subject's interpreter and read its answer.

    `Any` at a subprocess boundary: another interpreter's JSON, whose shape
    nothing here can know statically. Every field is converted at the call site.
    """
    try:
        result = surface.run(
            [*python, "-c", program, *arguments],
            timeout=timeout,
            env={"DJANGO_SETTINGS_MODULE": settings},
        )
    except ExecutionError as error:
        raise SynthesisError(str(error)) from error

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        message = f"the subject's interpreter did not answer (exit {result.exit_code}): {said}"
        raise SynthesisError(message)

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        message = f"the subject's answer was not JSON: {error}"
        raise SynthesisError(message) from error
    return payload


def _settings_for(root: Path) -> str:
    settings = settings_module(root)
    if settings is None:
        message = (
            "no DJANGO_SETTINGS_MODULE was found in manage.py, wsgi.py or asgi.py, so the "
            "subject cannot be asked what its schema is"
        )
        raise SynthesisError(message)
    return settings.value


def read_schema(
    root: Path,
    *,
    python: Sequence[str],
    surface: Surface | None = None,
    timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
) -> Mapping[str, SchemaModel]:
    """AC 1's first half: what the ORM says every model requires.

    A declaration, and named as one. What the database enforces is a different
    fact and `synthesize` is what establishes it.
    """
    payload = _run_in_subject(
        _SCHEMA,
        (),
        surface=surface or HostSurface(Path(root)),
        python=python,
        settings=_settings_for(root),
        timeout=timeout,
    )
    return {
        str(label): SchemaModel(
            label=str(label),
            table=str(entry.get("table", "")),
            fields=tuple(_field_of(raw) for raw in entry.get("fields", [])),
        )
        for label, entry in payload.get("models", {}).items()
    }


def _field_of(raw: Mapping[str, Any]) -> SchemaField:
    length = raw.get("max_length")
    return SchemaField(
        name=str(raw.get("name", "")),
        column=str(raw.get("column", "")),
        kind=str(raw.get("kind", "")),
        null=bool(raw.get("null")),
        has_default=bool(raw.get("has_default")),
        unique=bool(raw.get("unique")),
        choices=tuple(str(choice) for choice in raw.get("choices", [])),
        max_length=int(length) if isinstance(length, int) else None,
        relates_to=raw.get("relates_to") if isinstance(raw.get("relates_to"), str) else None,
        auto=bool(raw.get("auto")),
    )


# ================================================================== planning


def _filler_for(schema_field: SchemaField, *, unique: bool) -> Value:
    """A value that satisfies this column and claims nothing about the domain.

    **A field with `choices` takes one of them**, and that is AC 2's enum half:
    Django does not enforce choices at the database level, so a row holding
    `"coldfix-0"` in a status column inserts cleanly and then breaks the
    application that reads it — a workload built on such rows measures error
    handling.

    `unique` makes the value vary per row. A unique column filled with one
    constant collides on the second row, which is the commonest synthesis failure
    and the one AC 3's loop would otherwise spend a revision learning.
    """
    if schema_field.choices:
        return Value(kind="literal", literal=schema_field.choices[0])

    kind = schema_field.kind
    if kind in _TEXTUAL:
        stem = "coldfix-{i}" if unique else "coldfix"
        if kind == "EmailField":
            stem = f"{stem}@example.invalid"
        length = schema_field.max_length
        if length is not None and len(stem.replace("{i}", "000")) > length:
            stem = "{i}" if unique else "c"
        return Value(kind="sequence", template=stem)

    if kind in _NUMERIC:
        # Sequences are strings by construction, so a unique numeric column is
        # handled by the row index rather than by a template.
        return (
            Value(kind="sequence", template="{i}") if unique else Value(kind="literal", literal=1)
        )

    return {
        "BooleanField": Value(kind="literal", literal=False),
        "DateField": Value(kind="literal", literal="2020-01-01"),
        "DateTimeField": Value(kind="literal", literal="2020-01-01T00:00:00+00:00"),
        "TimeField": Value(kind="literal", literal="00:00:00"),
        "DurationField": Value(kind="literal", literal=0),
        "JSONField": Value(kind="literal", literal={}),
        "UUIDField": Value(kind="sequence", template="00000000-0000-4000-8000-{i}"),
        "GenericIPAddressField": Value(kind="literal", literal="127.0.0.1"),
        "BinaryField": Value(kind="literal", literal=""),
    }[kind]


def _unfillable(model: SchemaModel) -> tuple[SchemaField, ...]:
    return tuple(f for f in model.fields if f.needs_a_value and not f.fillable)


def plan(  # noqa: PLR0913 - the schema, what to build, how many, how they spread
    # and what the database has already taught are five independent facts; the last
    # two are what makes a revision a revision rather than the same plan again.
    schema: Mapping[str, SchemaModel],
    *,
    target: str,
    count: int,
    per_parent: int = 1,
    distribution: Distribution = Distribution.UNIFORM,
    also_fill: Mapping[str, frozenset[str]] | None = None,
    vary: Mapping[str, frozenset[str]] | None = None,
) -> Plan:
    """AC 1: walk the foreign keys and put the parents first.

    S-7.7 adds `distribution`, and it applies to **the target's own parents** —
    the relationship the workload traverses. Levels above it stay uniform: a
    shape applied at every level compounds into one nobody asked for, and the
    cost S-3.3 is about is paid where the request walks, not three joins up.

    `also_fill` and `vary` are what the database has taught so far — columns it
    refused as empty, and columns whose value it refused as repeated. A first plan
    passes neither; every revision passes more.

    Raises:
        SynthesisError: a required column this cannot fill, a chain that cycles,
            or a shape that will not fit the parent population. All three are
            AC 4: reported with the model and the field rather than left to fail
            as an opaque insert.
    """
    if target not in schema:
        known = ", ".join(sorted(schema)[:8]) or "nothing"
        message = f"{target} is not a model this subject has; it has {known}"
        raise SynthesisError(message)
    if count < 1:
        message = f"a plan for {count} row(s) seeds nothing and would report success for it"
        raise SynthesisError(message)

    also_fill = also_fill or {}
    vary = vary or {}

    order = _order_from(schema, target)
    counts = _counts_for(order, target=target, count=count, per_parent=per_parent)
    allocation = _allocation_for(
        schema[target], distribution=distribution, count=count, parents=counts
    )

    steps = []
    for label in order:
        model = schema[label]
        missing = _unfillable(model)
        if missing:
            named = ", ".join(f"{f.name} ({f.kind})" for f in missing)
            message = (
                f"{label} requires {named}, and this cannot make a value for that type. "
                "Writing null would fail at the database with a worse message, and writing a "
                "zero would produce a row that is valid and means nothing"
            )
            raise SynthesisError(message)

        steps.append(
            Step(
                model=label,
                count=counts[label],
                values=_values_for(
                    model,
                    also_fill.get(label, frozenset()),
                    vary.get(label, frozenset()),
                    allocation if label == target else None,
                ),
            )
        )

    return Plan(
        target=target,
        count=count,
        per_parent=per_parent,
        steps=tuple(steps),
        distribution=distribution,
        allocation=allocation,
    )


def _allocation_for(
    model: SchemaModel,
    *,
    distribution: Distribution,
    count: int,
    parents: Mapping[str, int],
) -> Allocation | None:
    """How the target's children land on their parents, or nothing if it has none.

    **A shape that will not fit is refused, never quietly flattened.** Asking for
    a long tail with `per_parent=1` gives one parent per child, and since
    `allocate` guarantees every parent at least one child the only allocation that
    exists is uniform — so the recipe would name a shape the data does not have,
    in the one field that exists to stop exactly that. The refusal says which
    knob to turn.
    """
    required = model.required_parents
    if not required:
        return None

    groups = parents.get(required[0], 1)
    built = allocate(distribution, groups=groups, total=count)

    if distribution is not Distribution.UNIFORM and built.largest == min(built.counts):
        message = (
            f"a {distribution.value} spread of {count} {model.label} over {groups} parents is "
            f"flat: every parent must hold at least one child, so {groups} parents and {count} "
            "children leave nothing to skew with. Raise per_parent to make fewer, heavier "
            "parents, or ask for more rows"
        )
        raise SynthesisError(message)

    return built


def _values_for(
    model: SchemaModel,
    also_fill: frozenset[str],
    vary: frozenset[str],
    allocation: Allocation | None = None,
) -> dict[str, Value]:
    values: dict[str, Value] = {}
    for schema_field in model.fields:
        wanted = schema_field.needs_a_value or schema_field.name in also_fill
        if not wanted or schema_field.auto:
            continue
        if schema_field.relates_to is not None:
            values[schema_field.name] = Value(
                kind="reference",
                model=schema_field.relates_to,
                assignment=_assignment_from(allocation)
                if allocation is not None and schema_field.relates_to == model.required_parents[0]
                else (),
            )
        else:
            values[schema_field.name] = _filler_for(
                schema_field, unique=schema_field.unique or schema_field.name in vary
            )
    return values


def _assignment_from(allocation: Allocation) -> tuple[int, ...]:
    """Which parent, by position, each child row points at.

    The allocation says parent 0 holds five children and parent 1 holds one; this
    turns that into the per-row list the subject indexes. Built here because
    `allocate` is deterministic, so the assignment is a property of the plan and
    lands in the replay key rather than being decided again at write time.
    """
    return tuple(position for position, held in enumerate(allocation.counts) for _ in range(held))


def _order_from(schema: Mapping[str, SchemaModel], target: str) -> list[str]:
    """The target and everything it needs, parents first.

    A depth-first walk with the visiting set carried, so a self-referential model
    — a category with a parent category — is reported as a cycle rather than
    recursed into. Django allows those and they are always nullable in practice,
    which means `needs_a_value` is false and they never reach here; a
    non-nullable one is genuinely unsatisfiable and says so.
    """
    order: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(label: str, chain: tuple[str, ...]) -> None:
        if label in done:
            return
        if label in visiting:
            path = " → ".join([*chain, label])
            message = (
                f"the foreign keys cycle and every link is required: {path}. No row of any of "
                "these can exist before the others, so no order of inserts satisfies them"
            )
            raise SynthesisError(message)
        if label not in schema:
            message = f"{label} is required by {chain[-1] if chain else target} and is not a model"
            raise SynthesisError(message)

        visiting.add(label)
        for parent in schema[label].required_parents:
            visit(parent, (*chain, label))
        visiting.discard(label)
        done.add(label)
        order.append(label)

    visit(target, ())
    return order


def _counts_for(
    order: Sequence[str], *, target: str, count: int, per_parent: int
) -> dict[str, int]:
    """How many rows of each model.

    `count` of the target, and enough of every ancestor for the target's children
    to be spread `per_parent` at a time. Ancestors get the ceiling of the
    division, because a plan short by one parent fails on the last row and spends
    a revision learning something arithmetic already knew.
    """
    parents = max(1, -(-count // max(per_parent, 1)))
    return {label: (count if label == target else parents) for label in order}


# ================================================================== the loop


def _refusal_of(payload: Mapping[str, Any]) -> Refusal:
    """Read what the database refused, structurally where the driver allows it.

    Diagnostics first: `psycopg` carries the server's own `column_name` and
    `constraint_name`, which are the same strings whatever the locale. Patterns
    over the message are the fallback, for SQLite, and the result records which
    of the two answered — a revision built on a regex over someone's translated
    error is worth less than one built on the server's own field.
    """
    message = str(payload.get("message", ""))
    column = payload.get("column")
    constraint = payload.get("constraint")
    table = payload.get("table")

    if isinstance(column, str) and column:
        violation = Violation.NOT_NULL if "null" in message.lower() else Violation.UNIQUE
        return Refusal(
            violation=violation,
            learned=Learned.DIAGNOSTICS,
            table=table if isinstance(table, str) else None,
            column=column,
            constraint=constraint if isinstance(constraint, str) else None,
            message=message,
        )

    for pattern in _NOT_NULL_PATTERNS:
        found = pattern.search(message)
        if found:
            return Refusal(
                violation=Violation.NOT_NULL,
                learned=Learned.MESSAGE,
                table=found.groupdict().get("table"),
                column=found.groupdict().get("column"),
                message=message,
            )

    for pattern in _UNIQUE_PATTERNS:
        found = pattern.search(message)
        if found:
            return Refusal(
                violation=Violation.UNIQUE,
                learned=Learned.MESSAGE,
                table=found.groupdict().get("table"),
                column=found.groupdict().get("column"),
                constraint=found.groupdict().get("constraint"),
                message=message,
            )

    if isinstance(constraint, str) and constraint:
        return Refusal(
            violation=Violation.UNIQUE,
            learned=Learned.DIAGNOSTICS,
            constraint=constraint,
            message=message,
        )

    return Refusal(violation=Violation.OTHER, learned=Learned.NEITHER, message=message)


def _field_named_by(refusal: Refusal, model: SchemaModel) -> SchemaField | None:
    """Which of this model's fields the refusal is about.

    The column first, because it is exact. Where only a constraint name is
    available — Postgres names the index on a unique violation, not the column —
    the fields whose column appears in the index name are candidates, and the
    longest match wins so that `email` is not chosen over `email_address`.
    """
    if refusal.column:
        return model.field_for_column(refusal.column) or model.field_named(refusal.column)
    if refusal.constraint:
        candidates = [f for f in model.fields if f.column and f.column in refusal.constraint]
        if candidates:
            return max(candidates, key=lambda f: len(f.column))
    return None


def synthesize(  # noqa: PLR0913 - the subject, its interpreter, what to build, how
    # many and how they spread are five independent facts; `per_parent` belongs to
    # the caller because S-7.7 makes it a parameter of `scale()`.
    root: Path,
    *,
    python: Sequence[str],
    target: str,
    count: int,
    per_parent: int = 1,
    distribution: Distribution = Distribution.UNIFORM,
    surface: Surface | None = None,
    timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
) -> Synthesis:
    """AC 1 to 4: build rows, learn what the models did not declare, and report either way.

    The loop is the story. A first plan is built from what the ORM says is
    required; the database is the thing that decides, and every `IntegrityError`
    it raises names one constraint the plan did not know about. A missing column
    is added, a repeated value is made to vary, and the plan is submitted again.

    Raises:
        SynthesisError: AC 4. Either a column this cannot fill — reported before
            anything is written — or a refusal it cannot act on, reported with
            the database's own words, the column where one was named, and every
            revision that had already been applied.
    """
    root = Path(root)
    settings = _settings_for(root)
    schema = read_schema(root, python=python, timeout=timeout)

    also_fill: dict[str, frozenset[str]] = {}
    vary: dict[str, frozenset[str]] = {}
    learned: list[Refusal] = []

    for _attempt in range(MAX_REVISIONS + 1):
        attempted = plan(
            schema,
            target=target,
            count=count,
            per_parent=per_parent,
            distribution=distribution,
            also_fill=also_fill,
            vary=vary,
        )
        payload = _run_in_subject(
            _APPLY,
            (json.dumps(attempted.as_json()),),
            surface=surface or HostSurface(Path(root)),
            python=python,
            settings=settings,
            timeout=timeout,
        )

        if payload.get("ok"):
            return Synthesis(
                plan=attempted,
                created={str(k): int(v) for k, v in (payload.get("created") or {}).items()},
                revisions=tuple(learned),
            )

        refusal = _refusal_of(payload)
        learned.append(refusal)
        label = str(payload.get("model", ""))
        model = schema.get(label)

        if payload.get("fatal") or not refusal.actionable or model is None:
            raise SynthesisError(_failure_report(target, label, refusal, learned))

        named = _field_named_by(refusal, model)
        if named is None:
            raise SynthesisError(_failure_report(target, label, refusal, learned))

        if refusal.violation is Violation.NOT_NULL:
            also_fill[label] = also_fill.get(label, frozenset()) | {named.name}
        else:
            vary[label] = vary.get(label, frozenset()) | {named.name}

    message = (
        f"synthesis for {target} was revised {MAX_REVISIONS} times and the database still "
        f"refused. Each revision was a constraint the models do not declare, which usually "
        f"means migrations and models disagree. Last: {learned[-1].describe()}"
    )
    raise SynthesisError(message)


def _failure_report(target: str, label: str, refusal: Refusal, learned: Sequence[Refusal]) -> str:
    """AC 4: what stopped it, where, and what had already been learned."""
    lines = [
        f"synthesis for {target} stopped at {label or 'an unnamed step'}: {refusal.describe()}",
        f"  the database said: {refusal.message.strip()[:400]}",
    ]
    if refusal.violation is Violation.OTHER:
        lines.append(
            "  This names no column to act on. A check constraint, a trigger or a database "
            "default this cannot read is not made satisfiable by trying again"
        )
    if len(learned) > 1:
        lines.append("  already learned:")
        lines.extend(f"    {earlier.describe()}" for earlier in learned[:-1])
    return "\n".join(lines)

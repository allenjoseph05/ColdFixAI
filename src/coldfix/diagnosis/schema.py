"""What a primitive will accept, derived from the primitive rather than declared.

Epic 8, S-8.2. AC 2 says the specification *is validated against the chosen
primitive's schema*, and this module is what a schema turns out to be.

**Read from the callable, never written twice.** S-3.1's `Primitive.signature`
already made this argument for the tool list — *two statements of one signature
drift, and the one the agent reads would be the one that is not executed* — and
it applies with more force here, because this statement is not read by a human.
A schema declared beside `run` would be a second place to update when a
parameter is added, and the failure mode is a design that validates and then
cannot be called.

**A primitive's parameters are not one kind of thing, and this is the story's
substance.** `scale_volume` takes nine, and only three of them are answerable by
a model:

    seed: Callable[[int], object]         the harness's, from the Explorer
    invoke: Callable[[], object]          the harness's
    reset: VerifiedReset                  the harness's
    scales: Sequence[int]                 **the design**
    distribution: Distribution            **the design**
    counters: Sequence[str]               **the design**
    extra_counters / clear_caches / process_identity      the harness's

So a specification is not a call. It is the answerable half of one, and the
partition falls out of the annotations that are already written: a parameter
whose type a JSON document can express is one the model chooses, and everything
else is supplied by whoever grounded the workload. Nothing had to be added to a
primitive to make this work, which is the test of whether the partition is real.

**A mapping of numbers is never specifiable, and that is a safety property
rather than a gap.** The one `Mapping[str, float]` parameter in the registry is
`bounds.headroom(metrics=...)`, and it is the shape of a *measurement*.
`CLAUDE.md`: *do not let an agent report a measurement; agents reason about
measurements the harness took.* A schema that let a design carry it would defeat
that non-negotiable through the front door — the numbers would arrive inside a
validated artifact, having been typed by a model. There is no second case, so
there is no mapping support at all.

**Specifiability is a whitelist, and the mapping exclusion is its default rather
than a rule of its own.** Worth stating because a sabotage pass tried to open
the hole by adding `Mapping` to `_SEQUENCE_ORIGINS` and **nothing failed** — the
element check rejected it anyway, for the unrelated reason that a mapping has two
type arguments and a sequence has one. The property is real (adding the origin
*and* relaxing the arity does fail three tests), but it is guarded twice over and
neither guard is the one a reader would name. A rule that can only be broken by
two simultaneous edits cannot be tested by one, and pretending otherwise would
be a test that passes against exactly the code it was written to reject.

**This checks shape, not sense, and the distinction is load-bearing.**
`scales=[10, 100, 1000]` and `scales=[-4]` both satisfy `Sequence[int]`;
`scale_volume` refuses the second, on its own rules, at the point of running.
Restating those rules here would be the second statement this module exists to
avoid. So *validated against the schema* means **the primitive will accept the
call**, and never **the experiment will produce a measurement**.
"""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Union, get_args, get_origin

from coldfix.primitives.registry import Primitive

# Origins that a JSON array can fill. `Mapping` is deliberately absent — see the
# module docstring — and so is anything a caller would have to construct, which
# is every remaining container in the registry.
_SEQUENCE_ORIGINS = frozenset({list, tuple, set, frozenset, Sequence, Iterable})


@dataclass(frozen=True)
class _Scalar:
    """How one scalar type reads in a prompt, and what it accepts from JSON.

    One table for both, because they are the same statement made twice
    otherwise — and the pair that would drift is *what the model was told* and
    *what the model's answer is checked against*.
    """

    describes: str
    accepts: Callable[[object], bool]


# **`type(value) is int` rather than `isinstance`, and that is the whole point of
# this table.** `bool` is a subclass of `int` in Python, so a permissive check
# accepts `repetitions=true`, the primitive runs one iteration, and nothing
# raises. The one deliberate widening is `int` for a `float`: JSON has no way to
# write `3.0` as distinct from `3`, and refusing it would fail a correct design
# over its notation.
_SCALARS: Mapping[type, _Scalar] = {
    bool: _Scalar("true or false", lambda value: type(value) is bool),
    int: _Scalar("a whole number", lambda value: type(value) is int),
    float: _Scalar("a number", lambda value: type(value) in (int, float)),
    str: _Scalar("text", lambda value: isinstance(value, str)),
}


class SchemaError(Exception):
    """A schema could not be read, or a specification did not satisfy one."""


@dataclass(frozen=True)
class Parameter:
    """One of a primitive's parameters, and who is expected to fill it."""

    name: str
    required: bool
    describes: str | None
    """How the type reads in a prompt, or `None` where no JSON value can express
    it — which is what makes the parameter the harness's rather than the
    model's."""

    annotation: object

    @property
    def specifiable(self) -> bool:
        """Whether a design may name this parameter at all."""
        return self.describes is not None


@dataclass(frozen=True)
class PrimitiveSchema:
    """What one primitive accepts, split into the design and the bindings.

    Frozen and derived, so two schemas of the same primitive are equal and
    neither can be edited into disagreeing with the function it describes.
    """

    primitive: str
    parameters: tuple[Parameter, ...]

    @property
    def specifiable(self) -> tuple[Parameter, ...]:
        """The parameters a model may choose. The design is exactly these."""
        return tuple(parameter for parameter in self.parameters if parameter.specifiable)

    @property
    def bound(self) -> tuple[Parameter, ...]:
        """The parameters the harness supplies: workloads, resets, sessions.

        Exposed rather than merely excluded, because a caller assembling the real
        call needs to know what it still owes — and because a specification that
        looks complete while missing every binding is the shape this project
        keeps finding in its own artifacts.
        """
        return tuple(parameter for parameter in self.parameters if not parameter.specifiable)

    def render(self) -> str:
        """The design surface, for the prompt.

        Only the specifiable half, and the other half is *named* rather than
        hidden: a model told nothing about `invoke` will invent a value for it,
        and a model told the harness supplies it will not.
        """
        lines = []
        for parameter in self.specifiable:
            necessity = "required" if parameter.required else "optional"
            lines.append(f"  {parameter.name}: {parameter.describes} ({necessity})")
        chosen = "\n".join(lines) or "  (this instrument takes no parameters you choose)"

        supplied = ", ".join(parameter.name for parameter in self.bound) or "none"
        return (
            f"{self.primitive} — parameters you choose:\n{chosen}\n"
            f"  Supplied by the harness, and not yours to name: {supplied}"
        )

    def check(self, arguments: Mapping[str, object]) -> str | None:
        """Every reason this argument map is not a call, or `None` if it is one.

        **All of them, not the first.** A rejection costs a model call to correct,
        so reporting one problem at a time buys one round trip per mistake — and
        the cascade has only three attempts to spend.

        Returns a message rather than raising, because this is the mechanical
        check S-5.6 cascades on: a rejected attempt has to be retryable, and an
        exception thrown out of the attempt would end the step instead.
        """
        by_name = {parameter.name: parameter for parameter in self.parameters}
        offered = ", ".join(parameter.name for parameter in self.specifiable) or "none"
        problems: list[str] = []

        for name, value in arguments.items():
            parameter = by_name.get(name)
            if parameter is None:
                problems.append(f"{self.primitive} has no parameter {name!r}; it takes {offered}")
                continue
            if not parameter.specifiable:
                problems.append(
                    f"{name!r} is supplied by the harness, not by a design — it is a "
                    f"{parameter.annotation!r}, which no JSON value can be"
                )
                continue
            fault = _check(value, parameter.annotation, name)
            if fault is not None:
                problems.append(fault)

        missing = [
            parameter.name
            for parameter in self.specifiable
            if parameter.required and parameter.name not in arguments
        ]
        if missing:
            problems.append(f"{self.primitive} requires {missing}, which this design does not set")

        if not problems:
            return None
        return "; ".join(problems)


def schema_of(primitive: Primitive) -> PrimitiveSchema:
    """Read a primitive's schema off the function that implements it.

    Raises:
        SchemaError: the signature's annotations cannot be resolved. Refused
            loudly rather than degraded to *nothing is specifiable*, which would
            produce an empty design that validates and then cannot be run.
    """
    try:
        signature = inspect.signature(primitive.run, eval_str=True)
    except (NameError, TypeError) as error:
        message = (
            f"{primitive.name}'s signature could not be resolved ({error}), so there is no schema "
            "to design against. Treating it as taking no parameters would produce an empty "
            "specification that validates and cannot be called"
        )
        raise SchemaError(message) from error

    return PrimitiveSchema(
        primitive=primitive.name,
        parameters=tuple(
            Parameter(
                name=name,
                required=parameter.default is inspect.Parameter.empty,
                describes=_describe(parameter.annotation),
                annotation=parameter.annotation,
            )
            for name, parameter in signature.parameters.items()
        ),
    )


def _describe(annotation: object) -> str | None:  # noqa: PLR0911 - a dispatch over
    # annotation kinds, where every branch is one kind and returning early is how
    # a dispatch reads. Collapsing them behind a result variable would not remove
    # a case, it would only move the cases.
    """How this type reads in a prompt, or `None` if no JSON value can be it.

    The two questions are one question: a type a model can be told how to fill is
    a type a model can fill, and one this cannot phrase is one it must not be
    asked for.
    """
    if annotation is inspect.Parameter.empty:
        # An unannotated parameter says nothing about what it takes, and a schema
        # that guessed would be guessing on the model's behalf.
        return None

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        members = ", ".join(repr(str(member.value)) for member in annotation)
        return f"one of {members}"

    scalar = _SCALARS.get(annotation) if isinstance(annotation, type) else None
    if scalar is not None:
        return scalar.describes

    origin = get_origin(annotation)

    if origin in (types.UnionType, Union):
        inner = [arg for arg in get_args(annotation) if arg is not types.NoneType]
        described = [_describe(arg) for arg in inner]
        if not described or any(item is None for item in described):
            return None
        joined = " or ".join(item for item in described if item is not None)
        return f"{joined}, or null" if len(inner) != len(get_args(annotation)) else joined

    if origin in _SEQUENCE_ORIGINS:
        args = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        if len(args) != 1:
            # A heterogeneous tuple has a position-by-position meaning that a
            # one-line description cannot carry. None exists in the registry.
            return None
        element = _describe(args[0])
        return None if element is None else f"a list of {element}"

    return None


def _check(  # noqa: PLR0911 - one branch per annotation kind, and each
    # returns its own message. A generic *that value is wrong* would collapse the
    # branch count and cost the cascade its only means of correcting itself.
    value: object,
    annotation: object,
    where: str,
) -> str | None:
    """Whether `value` is something this annotation accepts. A message, or `None`.

    Scalars are compared through `_SCALARS`, which is where the `bool`-is-an-`int`
    trap and the `int`-for-a-`float` widening are both recorded.
    """
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        allowed = [str(member.value) for member in annotation]
        if isinstance(value, str) and value in allowed:
            return None
        return f"{where}={value!r} is not one of {allowed}"

    origin = get_origin(annotation)

    if origin in (types.UnionType, Union):
        args = get_args(annotation)
        if value is None:
            return None if types.NoneType in args else f"{where} may not be null"
        faults = [_check(value, arg, where) for arg in args if arg is not types.NoneType]
        if any(fault is None for fault in faults):
            return None
        return f"{where}={value!r} is not any of the types this parameter takes"

    if origin in _SEQUENCE_ORIGINS:
        # A JSON array is a `list` and nothing else. `isinstance(value, Sequence)`
        # would accept a string for `counters: Sequence[str]`, and "queries"
        # would arrive as seven single-character counter names.
        if not isinstance(value, list):
            return f"{where} takes a list, not {type(value).__name__}"
        elements = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        if len(elements) != 1:
            return f"{where} has a shape no design can fill"
        for position, item in enumerate(value):
            fault = _check(item, elements[0], f"{where}[{position}]")
            if fault is not None:
                return fault
        return None

    scalar = _SCALARS.get(annotation) if isinstance(annotation, type) else None
    if scalar is not None:
        return None if scalar.accepts(value) else f"{where} takes {scalar.describes}, not {value!r}"

    return f"{where} is not something a design can set"

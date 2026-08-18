"""Compare two JSON payloads and say precisely where they differ.

The fourth operation of the lab bench, and the one with the most dangerous
failure mode. Every other instrument produces a number a human reads. This one
produces a verdict a patch is gated on: output equivalence is how the system
decides a fix changed the cost of a program without changing its behaviour.

That makes a false "identical" the worst outcome this module can produce — it
does not fail a run, it approves a patch. Every default here is chosen to be
the strict one, and every loosening is something a caller has to ask for by
name.

**Order-insensitivity is opt-in per comparison.** Not per session, not per
project, not inferred. The decision belongs to whoever knows whether the query
behind the payload had an `ORDER BY`, and that is a fact about one endpoint.
Defaulting to it would silently accept a patch that changed a result set's
order — which for a paginated endpoint is a behaviour change with real
consequences, and for a sorted one is the bug itself.

**JSON types, not Python types.** JSON has one number type, so `1` and `1.0`
are equal. It has a separate boolean type, so `True` and `1` are not — even
though Python says they are, and a comparison written with `==` would call them
identical.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# `Mapping` and `Sequence` rather than `dict` and `list`, because both are
# covariant in their contents and `dict` is not: a literal
# `{"results": [{"id": 1}]}` is a `dict[str, list[dict[str, int]]]`, which is
# not assignable to `dict[str, JsonValue]` and would need a cast at every call
# site. Accepting the protocols is also true to what the code does — nothing
# here needs a value to be exactly a `dict`.
type JsonValue = bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None

# The path element type: object keys are strings, array positions are integers.
# Kept structural rather than pre-rendered so that a key containing a dot or a
# bracket cannot be confused for a path separator.
type PathElement = str | int


# Deeper than any JSON a service returns. A payload past this is either
# generated — a fuzzer, S-3.10 — or not a tree at all, and the recursion here
# would otherwise die of a `RecursionError` that names no path and explains
# nothing. Measured: a self-referential dict crashed at ~490 levels.
MAXIMUM_DEPTH = 200


class DiffError(Exception):
    """The comparison could not be made."""


class TooDeepError(DiffError):
    """The payload nests deeper than `MAXIMUM_DEPTH`.

    Most likely a structure that references itself. JSON cannot express a
    cycle, but a Python object graph handed to this function can, and following
    one forever produces a stack overflow rather than an answer.
    """

    def __init__(self, path: tuple[PathElement, ...]) -> None:
        self.path = path
        super().__init__(
            f"payload nests deeper than {MAXIMUM_DEPTH} at {render_path(path)}; "
            "a structure that references itself cannot be compared"
        )


class UnsupportedValueError(DiffError):
    """A value is not JSON.

    Raised rather than compared with `==`, because the fallback for an
    unrecognized type is identity comparison — under which two equal
    `datetime`s from separate runs are "different" and two distinct objects
    that happen to be the same instance are "identical". Both are wrong, and
    neither announces itself.
    """

    def __init__(self, path: tuple[PathElement, ...], value: object) -> None:
        self.path = path
        self.value = value
        super().__init__(
            f"{render_path(path)} is {type(value).__name__}, which is not a JSON value"
        )


class DifferenceKind(StrEnum):
    """What kind of difference was found at a path."""

    VALUE = "value"
    TYPE = "type"
    MISSING = "missing"  # present in the left payload, absent from the right
    EXTRA = "extra"  # absent from the left payload, present in the right


class Absent:
    """Marks the side of a difference where nothing was present.

    A distinct sentinel because `None` is a real JSON value. A key holding
    `null` and a key that does not exist are different payloads, and a
    comparison that conflates them cannot tell a serializer that dropped a
    field from one that emptied it.
    """

    def __repr__(self) -> str:
        return "<absent>"


ABSENT: Final = Absent()


@dataclass(frozen=True)
class Difference:
    """One place where two payloads disagree."""

    path: tuple[PathElement, ...]
    kind: DifferenceKind
    left: JsonValue | Absent
    right: JsonValue | Absent

    @property
    def location(self) -> str:
        """The path rendered for a human — `$.results[3].price`."""
        return render_path(self.path)

    def __str__(self) -> str:
        return f"{self.location}: {self.kind} ({self.left!r} vs {self.right!r})"


@dataclass(frozen=True)
class Comparison:
    """The verdict, and everywhere the two payloads disagreed."""

    differences: tuple[Difference, ...]

    @property
    def identical(self) -> bool:
        return not self.differences

    @property
    def first(self) -> Difference | None:
        """The difference nearest the start of the document, or None.

        Order is deterministic: object keys are visited sorted, arrays in
        index order. Sorted rather than in document order because two payloads
        can carry the same keys in different orders, which would leave "the
        first difference" dependent on which of them was serialized first.
        """
        return self.differences[0] if self.differences else None


@dataclass(frozen=True)
class _Options:
    ignore_order: bool
    float_tolerance: float


def diff(
    a: JsonValue,
    b: JsonValue,
    *,
    ignore_order: bool = False,
    float_tolerance: float = 0.0,
) -> Comparison:
    """Compare two parsed JSON payloads.

    Args:
        a: the reference payload — "left" in every reported difference.
        b: the payload under test.
        ignore_order: compare arrays as multisets rather than sequences, at
            every depth. **Decide this per comparison**, from whether the query
            behind the payload had an `ORDER BY`. It is a multiset and not a
            set: `[1, 1, 2]` and `[1, 2, 2]` differ.
        float_tolerance: numbers within this of each other count as equal, used
            as both a relative and an absolute bound so that values near zero
            do not need infinite precision. Defaults to exact, because a
            tolerance nobody chose is a tolerance that hides a rounding
            regression.

    Raises:
        UnsupportedValueError: a value is not JSON.
        ValueError: `float_tolerance` is negative.
    """
    if float_tolerance < 0:
        message = f"float_tolerance must not be negative, got {float_tolerance}"
        raise ValueError(message)

    options = _Options(ignore_order=ignore_order, float_tolerance=float_tolerance)
    found: list[Difference] = []
    _compare(a, b, (), found, options)
    return Comparison(differences=tuple(found))


def render_path(path: tuple[PathElement, ...]) -> str:
    """`('results', 3, 'price')` → `$.results[3].price`."""
    rendered = "$"
    for element in path:
        if isinstance(element, int):
            rendered += f"[{element}]"
        elif element.isidentifier():
            rendered += f".{element}"
        else:
            rendered += f"[{element!r}]"
    return rendered


def _compare(
    left: JsonValue,
    right: JsonValue,
    path: tuple[PathElement, ...],
    found: list[Difference],
    options: _Options,
) -> None:
    if len(path) > MAXIMUM_DEPTH:
        raise TooDeepError(path)

    left_type = _json_type(left, path)
    right_type = _json_type(right, path)

    if left_type != right_type:
        found.append(Difference(path, DifferenceKind.TYPE, left, right))
        return

    # Scalars first. A `str` is also a `Sequence`, so testing for a container
    # before ruling out a string would compare "abc" and "abd" character by
    # character and report a difference at `$[2]` inside a value that has no
    # inside. The `isinstance` checks that follow are for narrowing; the types
    # are already known equal.
    if left_type in {"null", "boolean", "string"}:
        if left != right:
            found.append(Difference(path, DifferenceKind.VALUE, left, right))
    elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not _numbers_equal(left, right, options.float_tolerance):
            found.append(Difference(path, DifferenceKind.VALUE, left, right))
    elif isinstance(left, Mapping) and isinstance(right, Mapping):
        _compare_objects(left, right, path, found, options)
    elif isinstance(left, Sequence) and isinstance(right, Sequence):
        if options.ignore_order:
            _compare_arrays_unordered(left, right, path, found, options)
        else:
            _compare_arrays_in_order(left, right, path, found, options)


def _compare_objects(
    left: Mapping[str, JsonValue],
    right: Mapping[str, JsonValue],
    path: tuple[PathElement, ...],
    found: list[Difference],
    options: _Options,
) -> None:
    for key in sorted(left.keys() | right.keys()):
        if key not in right:
            found.append(Difference((*path, key), DifferenceKind.MISSING, left[key], ABSENT))
        elif key not in left:
            found.append(Difference((*path, key), DifferenceKind.EXTRA, ABSENT, right[key]))
        else:
            _compare(left[key], right[key], (*path, key), found, options)


def _compare_arrays_in_order(
    left: Sequence[JsonValue],
    right: Sequence[JsonValue],
    path: tuple[PathElement, ...],
    found: list[Difference],
    options: _Options,
) -> None:
    for index in range(min(len(left), len(right))):
        _compare(left[index], right[index], (*path, index), found, options)

    for index in range(len(right), len(left)):
        found.append(Difference((*path, index), DifferenceKind.MISSING, left[index], ABSENT))
    for index in range(len(left), len(right)):
        found.append(Difference((*path, index), DifferenceKind.EXTRA, ABSENT, right[index]))


def _compare_arrays_unordered(
    left: Sequence[JsonValue],
    right: Sequence[JsonValue],
    path: tuple[PathElement, ...],
    found: list[Difference],
    options: _Options,
) -> None:
    """Match elements as multisets, reporting whatever is left over.

    Exact comparison canonicalizes each element to a hashable key and takes a
    `Counter` difference, which is linear. A tolerance makes that impossible —
    approximate equality is not transitive, so no canonical key exists — and
    the fallback is pairwise matching, which is quadratic. That is the reason
    the tolerant path is documented as the expensive one rather than made the
    default.
    """
    if options.float_tolerance == 0:
        unmatched_left, unmatched_right = _match_by_key(left, right)
    else:
        unmatched_left, unmatched_right = _match_pairwise(left, right, options)

    for index in unmatched_left:
        found.append(Difference((*path, index), DifferenceKind.MISSING, left[index], ABSENT))
    for index in unmatched_right:
        found.append(Difference((*path, index), DifferenceKind.EXTRA, ABSENT, right[index]))


def _match_by_key(
    left: Sequence[JsonValue],
    right: Sequence[JsonValue],
) -> tuple[list[int], list[int]]:
    left_keys = [_canonical(value) for value in left]
    right_keys = [_canonical(value) for value in right]

    surplus_left = Counter(left_keys) - Counter(right_keys)
    surplus_right = Counter(right_keys) - Counter(left_keys)

    return (
        _indices_of(left_keys, surplus_left),
        _indices_of(right_keys, surplus_right),
    )


def _indices_of(keys: list[object], surplus: Counter[object]) -> list[int]:
    """Positions accounting for a surplus, taking earlier duplicates first."""
    remaining = Counter(surplus)
    indices = []
    for index, key in enumerate(keys):
        if remaining[key] > 0:
            remaining[key] -= 1
            indices.append(index)
    return indices


def _match_pairwise(
    left: Sequence[JsonValue],
    right: Sequence[JsonValue],
    options: _Options,
) -> tuple[list[int], list[int]]:
    """Greedy first-match, which is quadratic and order-dependent.

    Greedy is exact when equality is transitive. Approximate equality is not:
    with a tolerance of 1, `0 ≈ 1` and `1 ≈ 2` while `0 ≉ 2`, so pairing `1`
    with the wrong partner can leave two elements unmatched that some other
    pairing would have matched. This reports a difference that a maximum
    matching would not have — the conservative direction, and the only one
    acceptable for a check that gates patches.
    """
    available = list(range(len(right)))
    unmatched_left = []

    for left_index, value in enumerate(left):
        for position, right_index in enumerate(available):
            if _equal(value, right[right_index], options):
                available.pop(position)
                break
        else:
            unmatched_left.append(left_index)

    return unmatched_left, available


def _equal(left: JsonValue, right: JsonValue, options: _Options) -> bool:
    found: list[Difference] = []
    _compare(left, right, (), found, options)
    return not found


def _numbers_equal(left: float, right: float, tolerance: float) -> bool:
    # Two NaNs compare unequal under IEEE 754, which would make any payload
    # containing one impossible to verify as unchanged — the same payload would
    # differ from itself. For an equivalence check, "not a number in the same
    # place" is agreement.
    if math.isnan(left) and math.isnan(right):
        return True
    if tolerance == 0:
        return left == right
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


def _json_type(value: JsonValue, path: tuple[PathElement, ...]) -> str:
    # Order matters: `bool` is a subclass of `int` in Python, so the boolean
    # check has to come first or `True` is reported as the number 1 — and the
    # two payloads `{"ok": true}` and `{"ok": 1}` would compare identical.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    # Before the `Sequence` check, which `bytes` also satisfies. Bytes are not
    # JSON, and treating them as an array of integers would turn an encoding
    # mistake into a plausible-looking structural diff.
    if isinstance(value, (bytes, bytearray)):
        raise UnsupportedValueError(path, value)
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence):
        return "array"
    raise UnsupportedValueError(path, value)


def _canonical(value: JsonValue) -> object:
    """A hashable stand-in for a value, equal exactly when the values are.

    Numbers are tagged apart from booleans for the reason `_json_type`
    explains. Arrays nested inside an unordered comparison are canonicalized as
    multisets too, since the option applies at every depth; they are sorted by
    `repr` only to make the key deterministic, never to order the payload.
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        # 1 and 1.0 hash and compare equal in Python, which is the JSON rule.
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, (bytes, bytearray)):
        raise UnsupportedValueError((), value)
    if isinstance(value, Mapping):
        return ("object", tuple(sorted((key, _canonical(item)) for key, item in value.items())))
    if isinstance(value, Sequence):
        return ("array", tuple(sorted((_canonical(item) for item in value), key=repr)))
    raise UnsupportedValueError((), value)

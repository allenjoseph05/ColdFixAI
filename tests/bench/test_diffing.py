"""`diff()` is strict by default, and every loosening has to be asked for.

This is the instrument a patch gets gated on, so the failure that matters is a
false "identical" — it does not fail a run, it approves a change. Most of these
tests are therefore attempts to make two different payloads compare equal.

Three of them are the ones worth keeping if the rest were deleted:

- `test_reordering_an_array_is_a_difference_by_default` — the story's note. A
  default of order-insensitive would hide a real regression.
- `test_ignoring_order_compares_multisets_not_sets` — the obvious wrong
  implementation of that option, which calls `[1, 1, 2]` and `[1, 2, 2]` equal.
- `test_a_boolean_is_not_the_number_one` — the obvious wrong implementation of
  the whole module, since Python's `==` says `True == 1`.
"""

from __future__ import annotations

import copy
import math

import pytest

from coldfix.bench.diffing import (
    ABSENT,
    Comparison,
    DifferenceKind,
    JsonValue,
    TooDeepError,
    UnsupportedValueError,
    diff,
    render_path,
)


def kinds(comparison: Comparison) -> list[DifferenceKind]:
    return [difference.kind for difference in comparison.differences]


def locations(comparison: Comparison) -> list[str]:
    return [difference.location for difference in comparison.differences]


# ------------------------------------------------------------------ agreement


def test_identical_nested_payloads_agree() -> None:
    # Annotated because a heterogeneous literal infers `dict[str, object]`,
    # which is what a caller holding a payload as a literal has to do too. A
    # payload arriving from `json.loads` needs nothing.
    payload: JsonValue = {
        "count": 2,
        "results": [
            {"id": 1, "tags": ["a", "b"], "score": 1.5},
            {"id": 2, "tags": [], "score": 0.0},
        ],
        "next": None,
    }

    comparison = diff(payload, copy.deepcopy(payload))

    assert comparison.identical
    assert comparison.differences == ()
    assert comparison.first is None


def test_key_order_in_an_object_is_not_a_difference() -> None:
    """JSON objects are unordered, and a serializer is free to reorder them."""
    assert diff({"a": 1, "b": 2}, {"b": 2, "a": 1}).identical


def test_an_integer_and_a_float_of_the_same_value_agree() -> None:
    """JSON has one number type. `1` and `1.0` are the same number."""
    assert diff({"total": 1}, {"total": 1.0}).identical


# ------------------------------------------------------- the equality traps


def test_a_boolean_is_not_the_number_one() -> None:
    """Python says `True == 1`. JSON says a boolean is not a number.

    Written with `==`, or with an `isinstance(value, int)` check placed before
    the boolean one, `{"ok": true}` and `{"ok": 1}` compare identical — and a
    patch that swapped a flag for a count would ship.
    """
    comparison = diff({"ok": True}, {"ok": 1})

    assert not comparison.identical
    assert kinds(comparison) == [DifferenceKind.TYPE]
    assert comparison.differences[0].left is True


def test_null_and_a_missing_key_are_different_payloads() -> None:
    """A serializer that dropped a field is not one that emptied it."""
    comparison = diff({"note": None}, {})

    assert kinds(comparison) == [DifferenceKind.MISSING]
    assert comparison.differences[0].left is None
    assert comparison.differences[0].right is ABSENT


def test_a_string_and_a_number_that_look_alike_differ() -> None:
    assert not diff({"id": "1"}, {"id": 1}).identical


def test_a_string_is_not_an_array_of_characters() -> None:
    """A `str` satisfies `Sequence`, so container handling must rule it out first.

    Otherwise "abc" and "abd" are compared element by element and the reported
    difference is at `$.name[2]` — a path pointing inside a value that has no
    inside, in a payload where the whole string is the thing that changed.
    """
    comparison = diff({"name": "abc"}, {"name": "abd"})

    assert kinds(comparison) == [DifferenceKind.VALUE]
    assert locations(comparison) == ["$.name"]
    assert comparison.differences[0].left == "abc"


def test_any_sequence_counts_as_an_array() -> None:
    """The parameter types are the covariant protocols, and this is the consequence."""
    assert diff({"tags": ("a", "b")}, {"tags": ["a", "b"]}).identical


def test_a_structure_that_references_itself_is_refused_with_a_path() -> None:
    """Rather than dying of a stack overflow that names nothing.

    JSON cannot express a cycle, but a Python object graph handed to this
    function can, and following one produces a `RecursionError` at around 490
    levels — an error with no path in it, from a comparison that had already
    done a great deal of useless work.
    """
    cyclic: dict[str, JsonValue] = {}
    cyclic["self"] = cyclic

    with pytest.raises(TooDeepError) as caught:
        diff(cyclic, cyclic)

    assert len(caught.value.path) > 100
    assert "references itself" in str(caught.value)


def test_deep_but_finite_nesting_still_compares() -> None:
    """The guard is above anything a service returns."""
    deep: dict[str, JsonValue] = {}
    cursor = deep
    for _ in range(150):
        nested: dict[str, JsonValue] = {}
        cursor["n"] = nested
        cursor = nested

    assert diff(deep, copy.deepcopy(deep)).identical


def test_bytes_are_refused_rather_than_read_as_an_array() -> None:
    """The type checker cannot catch this one, so the runtime has to.

    `bytes` satisfies `Sequence[int]` and every `int` is a `JsonValue`, so
    `bytes` is a structurally valid argument as far as mypy is concerned —
    passing an unparsed response body type-checks cleanly. Read as an array it
    would produce a per-byte structural diff of an encoding mistake.
    """
    with pytest.raises(UnsupportedValueError, match="bytes"):
        diff({"body": b"ab"}, {"body": b"ab"})


def test_nan_agrees_with_nan() -> None:
    """Otherwise a payload containing one would differ from itself.

    IEEE 754 says NaN != NaN, so an equivalence check that took `==` at face
    value could never verify such a payload as unchanged — including against a
    rerun of the same unpatched code.
    """
    assert diff({"rate": math.nan}, {"rate": math.nan}).identical
    assert not diff({"rate": math.nan}, {"rate": 0.0}).identical


# ------------------------------------------------------------------ ordering


def test_reordering_an_array_is_a_difference_by_default() -> None:
    """The story's note, as a test.

    Defaulting to order-insensitive would hide a real regression: for a sorted
    endpoint the ordering *is* the behaviour, and for a paginated one a changed
    order silently changes which rows a client sees on page two.
    """
    comparison = diff({"results": [1, 2, 3]}, {"results": [3, 2, 1]})

    assert not comparison.identical
    assert locations(comparison) == ["$.results[0]", "$.results[2]"]


def test_ignoring_order_is_opt_in_per_comparison() -> None:
    same_rows = [{"id": 2}, {"id": 1}]

    assert not diff({"results": [{"id": 1}, {"id": 2}]}, {"results": same_rows}).identical
    assert diff(
        {"results": [{"id": 1}, {"id": 2}]},
        {"results": same_rows},
        ignore_order=True,
    ).identical


def test_ignoring_order_compares_multisets_not_sets() -> None:
    """The obvious wrong implementation of the option.

    Comparing `set(a) == set(b)` calls these equal. They are not: a patch that
    duplicated one row and dropped another produces exactly this shape.
    """
    comparison = diff([1, 1, 2], [1, 2, 2], ignore_order=True)

    assert not comparison.identical
    assert kinds(comparison) == [DifferenceKind.MISSING, DifferenceKind.EXTRA]
    assert comparison.differences[0].left == 1
    assert comparison.differences[1].right == 2


def test_ignoring_order_applies_at_every_depth() -> None:
    assert diff(
        {"groups": [{"tags": ["a", "b"]}, {"tags": ["c"]}]},
        {"groups": [{"tags": ["c"]}, {"tags": ["b", "a"]}]},
        ignore_order=True,
    ).identical


def test_ignoring_order_still_reports_a_genuinely_missing_element() -> None:
    comparison = diff([{"id": 1}, {"id": 2}], [{"id": 2}], ignore_order=True)

    assert kinds(comparison) == [DifferenceKind.MISSING]
    assert comparison.differences[0].left == {"id": 1}


# ------------------------------------------------------------------- lengths


def test_a_shorter_array_reports_the_missing_positions() -> None:
    comparison = diff([1, 2, 3], [1, 2])

    assert kinds(comparison) == [DifferenceKind.MISSING]
    assert locations(comparison) == ["$[2]"]


def test_a_longer_array_reports_the_extra_positions() -> None:
    comparison = diff([1], [1, 2, 3])

    assert kinds(comparison) == [DifferenceKind.EXTRA, DifferenceKind.EXTRA]
    assert locations(comparison) == ["$[1]", "$[2]"]


# ------------------------------------------------------------------- floats


def test_floats_are_compared_exactly_by_default() -> None:
    """A tolerance nobody chose is a tolerance that hides a rounding change."""
    assert not diff({"total": 0.1 + 0.2}, {"total": 0.3}).identical


def test_a_tolerance_makes_representation_noise_agree() -> None:
    assert diff({"total": 0.1 + 0.2}, {"total": 0.3}, float_tolerance=1e-9).identical


def test_the_tolerance_is_absolute_as_well_as_relative() -> None:
    """Near zero a relative tolerance demands infinite precision.

    `0.0` and `1e-15` differ by 100% relatively, so a relative-only bound would
    reject them however loose it was set.
    """
    assert diff({"drift": 0.0}, {"drift": 1e-15}, float_tolerance=1e-9).identical


def test_a_tolerance_does_not_swallow_a_real_change() -> None:
    assert not diff({"total": 100.0}, {"total": 101.0}, float_tolerance=1e-9).identical


def test_a_negative_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        diff(1, 1, float_tolerance=-0.1)


def test_tolerance_and_ignored_order_work_together() -> None:
    """The quadratic path — matching pairwise, because no canonical key exists."""
    comparison = diff(
        [{"score": 0.30000000001}, {"score": 0.1}],
        [{"score": 0.1}, {"score": 0.3}],
        ignore_order=True,
        float_tolerance=1e-9,
    )

    assert comparison.identical


# --------------------------------------------------------------------- paths


def test_the_path_to_the_first_difference_is_reported() -> None:
    comparison = diff(
        {"page": {"results": [{"id": 1}, {"id": 2, "price": 9.99}]}},
        {"page": {"results": [{"id": 1}, {"id": 2, "price": 19.99}]}},
    )

    first = comparison.first
    assert first is not None
    assert first.path == ("page", "results", 1, "price")
    assert first.location == "$.page.results[1].price"
    assert (first.left, first.right) == (9.99, 19.99)


def test_differences_are_reported_in_a_deterministic_order() -> None:
    """Sorted by key, so "first" does not depend on which payload was built first."""
    forward = diff({"b": 1, "a": 1}, {"b": 2, "a": 2})
    reversed_keys = diff({"a": 1, "b": 1}, {"a": 2, "b": 2})

    assert locations(forward) == ["$.a", "$.b"]
    assert locations(forward) == locations(reversed_keys)


def test_a_key_that_is_not_an_identifier_is_rendered_unambiguously() -> None:
    """A key containing a dot must not read as a path separator."""
    comparison = diff({"a.b": 1}, {"a.b": 2})

    assert locations(comparison) == ["$['a.b']"]
    assert comparison.differences[0].path == ("a.b",)


def test_the_root_path_renders_as_the_document() -> None:
    assert render_path(()) == "$"
    assert locations(diff(1, 2)) == ["$"]


# ----------------------------------------------------------------- not JSON


def test_a_value_that_is_not_json_is_refused() -> None:
    """Rather than falling back to `==`, which compares by identity.

    Under identity comparison two equal values from separate runs are
    "different" and two references to one object are "identical". Both are
    wrong, and neither announces itself.
    """
    with pytest.raises(UnsupportedValueError) as caught:
        diff({"when": {1, 2}}, {"when": {1, 2}})  # type: ignore[dict-item]

    assert caught.value.path == ("when",)
    assert "set" in str(caught.value)

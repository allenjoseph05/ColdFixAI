"""The planted defects exhibit the signatures they claim to.

A fixture repository is a measuring standard, and an uncalibrated standard is
worse than none — every downstream test inherits its error silently. So each
planted defect is asserted here against the signature its docstring states, and
each control is asserted *not* to exhibit it.

These are not tests of the lab bench. The lab bench does not exist yet (E1).
They are tests that the thing E1 will be measured against is what it claims to
be, which has to be true first.
"""

from __future__ import annotations

import importlib
import sys
import time

import pytest

from fixtures.planted import loops, queries
from fixtures.planted.store import build_store

# ---------------------------------------------------------------- N+1


@pytest.mark.parametrize("authors", [1, 5, 20])
def test_n_plus_one_query_count_scales_with_rows(authors: int) -> None:
    """The defect's signature is `1 + A`, growing with the dataset."""
    store = build_store(authors=authors, books_per_author=3)
    queries.list_books_n_plus_one(store)
    assert store.query_count == 1 + authors


@pytest.mark.parametrize("authors", [1, 5, 20])
def test_batched_control_query_count_is_constant(authors: int) -> None:
    """The control stays at 2 queries however large the dataset gets.

    This is the assertion that stops a detector from passing by always
    reporting an N+1.
    """
    store = build_store(authors=authors, books_per_author=3)
    queries.list_books_batched(store)
    assert store.query_count == 2


def test_defect_and_control_return_the_same_answer() -> None:
    """Both produce identical output, so only cost distinguishes them.

    If they disagreed, a "fix" that changed behaviour would look correct here,
    and output equivalence is the property E10's verification depends on.
    """
    defective = build_store(authors=6, books_per_author=4)
    clean = build_store(authors=6, books_per_author=4)
    assert queries.list_books_n_plus_one(defective) == queries.list_books_batched(clean)


# ---------------------------------------------------------- over-fetch


def test_over_fetch_is_invisible_to_query_count() -> None:
    """Both variants issue exactly one query. Counting queries cannot tell them apart.

    This is the point of the defect: a tool measuring only query count reports
    nothing here, and would be wrong.
    """
    wide = build_store(authors=10, books_per_author=5)
    narrow = build_store(authors=10, books_per_author=5)

    queries.list_titles_over_fetching(wide)
    queries.list_titles_narrow(narrow)

    assert wide.query_count == narrow.query_count == 1


def test_over_fetch_is_visible_to_the_guard_counter() -> None:
    """The payload proxy separates them cleanly — 5 columns against 1."""
    wide = build_store(authors=10, books_per_author=5)
    narrow = build_store(authors=10, books_per_author=5)

    titles_wide = queries.list_titles_over_fetching(wide)
    titles_narrow = queries.list_titles_narrow(narrow)

    assert titles_wide == titles_narrow
    assert wide.rows_returned == narrow.rows_returned
    assert wide.cells_returned == 5 * narrow.cells_returned


# --------------------------------------------------------------- decoy


@pytest.mark.parametrize("authors", [4, 40, 400])
def test_decoy_is_expensive_but_does_not_scale(authors: int) -> None:
    """The decoy's cost is constant, so it is not an N+1 at any size.

    A detector that flags "many queries" reports this. A detector that flags
    "queries that scale with rows returned" does not. The difference is the
    whole point, and this asserts the fixture actually has that property.
    """
    store = build_store(authors=authors, books_per_author=2)
    queries.summarize_with_fixed_floor(store)
    assert store.query_count == queries.DECOY_FIXED_QUERIES + 2


def test_decoy_costs_more_than_the_real_defect_at_small_sizes() -> None:
    """At 4 authors the correct code issues 37 queries and the defect 5.

    Absolute query count ranks these backwards. Only the growth rate gets it
    right, which is why the signature in ADR 011 is a formula and not a number.
    """
    decoy = build_store(authors=4, books_per_author=2)
    defect = build_store(authors=4, books_per_author=2)

    queries.summarize_with_fixed_floor(decoy)
    queries.list_books_n_plus_one(defect)

    assert decoy.query_count > defect.query_count


# ------------------------------------------- expensive downstream work


def test_expensive_downstream_work_is_invisible_to_query_count() -> None:
    """Two queries, and nearly all the cost is after them.

    The case S-0.4 could not exercise: there, the ablated component dominated
    and the work it fed was negligible, so replay and empty stubs were
    indistinguishable on timing. Here the ratio is inverted, which is what
    S-3.4 needs to prove the two strategies differ.
    """
    store = build_store(authors=8, books_per_author=5)
    store.reset()
    queries.render_with_expensive_downstream(store)
    assert store.query_count == 2


def test_downstream_cost_scales_with_rows_not_queries() -> None:
    """Double the rows, double the downstream work, same two queries."""
    small = build_store(authors=4, books_per_author=5)
    large = build_store(authors=8, books_per_author=5)

    small_total = queries.render_with_expensive_downstream(small)
    large_total = queries.render_with_expensive_downstream(large)

    assert small.query_count == large.query_count == 2
    assert large_total > small_total


# ---------------------------------------------------------- complexity


def test_quadratic_defect_recovers_an_exponent_of_two() -> None:
    """Doubling the input quadruples the operations, exactly."""
    growth = loops.measure_growth(loops.quadratic_pairs, (10, 20, 40))
    assert growth[10] == 100
    assert growth[20] == 400
    assert growth[40] == 1600
    assert growth[20] / growth[10] == 4.0
    assert growth[40] / growth[20] == 4.0


def test_linear_control_recovers_an_exponent_of_one() -> None:
    """Doubling the input doubles the operations."""
    growth = loops.measure_growth(loops.linear_scan, (10, 20, 40))
    assert growth[20] / growth[10] == 2.0
    assert growth[40] / growth[20] == 2.0


def test_hidden_quadratic_is_also_quadratic() -> None:
    """`x in list` is quadratic even with no visible nested loop.

    Operations are n(n-1)/2 — item i scans the i items already seen, and with
    distinct inputs the inner loop never breaks early. The ratio between
    doublings therefore approaches 4 **from above** rather than landing on it,
    so a curve fitter keying on an exact ratio rather than a fitted exponent
    gets this wrong.
    """
    growth = loops.measure_growth(loops.quadratic_membership, (10, 20, 40))
    assert growth[10] == 45
    assert growth[20] == 190
    assert growth[40] == 780
    assert 4.0 < growth[20] / growth[10] < 4.3
    assert 4.0 < growth[40] / growth[20] < 4.2


def test_constant_lookup_does_not_grow() -> None:
    growth = loops.measure_growth(loops.constant_lookup, (10, 100, 1000))
    assert set(growth.values()) == {1}


# ------------------------------------------------------------- imports


def _import_cost(module_name: str) -> float:
    """Import a module fresh and return the seconds it took.

    Python caches modules in `sys.modules`, so a second import is free and a
    naive measurement reports zero. The cache entry is removed first.
    """
    full_name = f"fixtures.planted.{module_name}"
    sys.modules.pop(full_name, None)
    start = time.perf_counter()
    importlib.import_module(full_name)
    elapsed = time.perf_counter() - start
    sys.modules.pop(full_name, None)
    return elapsed


@pytest.mark.slow
def test_slow_import_costs_materially_more_than_the_control() -> None:
    """The defect pays at import; the control pays at first use.

    Asserted as a ratio against a control rather than as an absolute duration,
    because an absolute threshold encodes the speed of whichever machine wrote
    it. S-0.4 measured the same endpoint drifting 12% between runs minutes
    apart on an idle container.
    """
    slow = min(_import_cost("slow_import") for _ in range(3))
    fast = min(_import_cost("fast_import") for _ in range(3))

    assert slow > fast * 5, (
        f"slow_import took {slow * 1000:.1f} ms and fast_import {fast * 1000:.1f} ms; "
        "the planted import defect is no longer materially slower"
    )


@pytest.mark.slow
def test_the_two_import_variants_are_functionally_equivalent() -> None:
    """Same answers, so only *when* the cost is paid distinguishes them."""
    slow_module = importlib.import_module("fixtures.planted.slow_import")
    fast_module = importlib.import_module("fixtures.planted.fast_import")

    for value in (0, 1, 99, 1234, 199_999):
        assert slow_module.lookup(value) == fast_module.lookup(value)

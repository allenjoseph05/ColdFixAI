"""The question this primitive refuses is the one that made it attractive.

S-3.18. `08-audit.md` F8: "how many queries must this endpoint issue" is a
question about intent, and an agent that could answer it would already know the
fix. So the interesting tests here are the refusals — a floor on `db.query`
cannot be constructed at all, not by the helper and not by writing the arithmetic
out by hand — and the null result, since F8's finding is that no computable bound
is the *ordinary* case rather than an error.

The three kept bounds are all facts about data: the bytes an input contains, the
distinct entities a measured response names, the instructions something already
achieved. None of them is an opinion about what the program ought to do.
"""

from __future__ import annotations

import re

import pytest

from coldfix.primitives.bounds import (
    INSTRUCTIONS,
    WORTH_INVESTIGATING,
    Bound,
    BoundError,
    BoundKind,
    Comparison,
    ImpossibleBoundError,
    NotComputableError,
    bytes_that_must_be_read,
    instructions_of_reference,
    rows_required_by,
    screen,
)
from coldfix.primitives.counters import (
    BLOCKED_NETWORK_CALLS,
    DB_BYTES,
    DB_QUERY,
    DB_ROWS,
    FILE_OPEN,
    HTTP_REQUEST,
)
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, ProjectProfile

# One order row per order, plus one row per distinct customer. The endpoint that
# returns this with an N+1 issues 200 queries; the rows it *must* return are 103,
# and 103 is computable while "how many queries" is not.
ORDERS = [f"order-{n}" for n in range(100)]
CUSTOMERS = ["alice", "bob", "carol"] * 34


def a_bound(floor: float = 10.0, metric: str = DB_ROWS) -> Bound:
    return Bound(
        kind=BoundKind.ROWS_REQUIRED,
        metric=metric,
        floor=floor,
        basis="stated directly, for the tests about the arithmetic",
    )


# ------------------------------------- AC 2: the refusal, which is the design


def test_a_floor_on_queries_cannot_be_constructed() -> None:
    """AC 2, and F8's whole finding. One query per row and one query for all rows
    return the same answer; deciding which is necessary *is* deciding the fix."""
    with pytest.raises(NotComputableError, match="question about intent"):
        rows_required_by({"orders": ORDERS}, metric=DB_QUERY)


def test_the_refusal_survives_writing_the_arithmetic_out_by_hand() -> None:
    """The check is in the constructor rather than in the helper, so a caller who
    skips the helper does not get a circular answer for their trouble."""
    with pytest.raises(NotComputableError, match=re.escape("db.query")):
        Bound(
            kind=BoundKind.ROWS_REQUIRED,
            metric=DB_QUERY,
            floor=103.0,
            basis="I counted them myself",
        )


def test_the_refusal_names_the_computable_alternative() -> None:
    """A refusal that leaves somebody stuck gets routed around. There *is* a
    computable floor next door, and the message says which."""
    alternative = re.escape("Floor `db.rows` from the response schema")

    with pytest.raises(NotComputableError, match=alternative):
        a_bound(metric=DB_QUERY)


@pytest.mark.parametrize("metric", [DB_QUERY, HTTP_REQUEST, FILE_OPEN, BLOCKED_NETWORK_CALLS])
def test_every_metric_with_a_semantic_minimum_is_refused(metric: str) -> None:
    with pytest.raises(NotComputableError):
        a_bound(metric=metric)


def test_a_floor_must_record_what_it_was_computed_from() -> None:
    """A number whose evidence is not recorded cannot be argued with, and this
    primitive exists precisely because some floors are not computable."""
    with pytest.raises(BoundError, match="states no basis"):
        Bound(kind=BoundKind.BYTES_READ, metric=DB_BYTES, floor=1.0, basis="   ")


# ------------------------------------------------ AC 1: the three that are kept


def test_the_bytes_floor_is_the_sum_of_what_must_be_read() -> None:
    """Arithmetic over the data. A transform over three files of known size has to
    read their bytes, whatever it then does with them."""
    bound = bytes_that_must_be_read(
        {"orders.csv": 4_000, "customers.csv": 1_000},
        metric=DB_BYTES,
    )

    assert bound.floor == 5_000
    assert bound.kind is BoundKind.BYTES_READ
    assert "orders.csv (4000 bytes)" in bound.basis


def test_the_row_floor_counts_distinct_entities_in_the_measured_response() -> None:
    """AC 1. Read off the response we measured, not off an opinion about what the
    endpoint ought to return: 100 orders naming 3 customers is 103 rows."""
    bound = rows_required_by({"orders": ORDERS, "customers": CUSTOMERS}, metric=DB_ROWS)

    assert bound.floor == 103
    assert "3 distinct customers" in bound.basis


def test_repeated_entities_count_once() -> None:
    """The whole point of the row floor. A response naming the same customer 34
    times still only requires that customer's row once, and an implementation
    that fetched it 34 times is the finding."""
    bound = rows_required_by({"customers": CUSTOMERS}, metric=DB_ROWS)

    assert bound.floor == 3


def test_the_instruction_floor_names_the_reference_that_achieved_it() -> None:
    """The number is only a bound because something achieved it, so the something
    is part of the bound."""
    bound = instructions_of_reference(2_000, reference="a hand-written radix sort")

    assert bound.metric == INSTRUCTIONS
    assert bound.floor == 2_000
    assert "hand-written radix sort" in bound.basis


def test_an_instruction_floor_with_no_reference_is_refused() -> None:
    with pytest.raises(BoundError, match="must name the reference"):
        instructions_of_reference(2_000, reference="  ")


def test_a_bytes_floor_over_no_sources_is_refused() -> None:
    with pytest.raises(BoundError, match="sum of nothing is not a bound"):
        bytes_that_must_be_read({}, metric=DB_BYTES)


def test_a_negative_source_size_is_refused() -> None:
    with pytest.raises(BoundError, match="negative size"):
        bytes_that_must_be_read({"orders.csv": -1}, metric=DB_BYTES)


def test_a_row_floor_over_no_entities_is_refused() -> None:
    with pytest.raises(BoundError, match="no response schema"):
        rows_required_by({}, metric=DB_ROWS)


# ------------------------------------------------------- the comparison itself


def test_a_workload_near_its_bound_has_little_to_win() -> None:
    """§13's own example: 76% of bound, so 1.3x available, which it calls nothing
    left."""
    comparison = Comparison(bound=a_bound(floor=76.0), measured=100.0)

    assert comparison.fraction_of_bound == pytest.approx(0.76)
    assert comparison.available == pytest.approx(1.32, rel=0.01)
    assert not comparison.worth_investigating


def test_a_workload_far_from_its_bound_has_thirty_fold_available() -> None:
    comparison = Comparison(bound=a_bound(floor=3.0), measured=100.0)

    assert comparison.available == pytest.approx(33.3, rel=0.01)
    assert comparison.worth_investigating


def test_every_explanation_says_a_bound_is_a_ceiling_not_a_target() -> None:
    """The failure mode of a headroom check is somebody reading 3% of bound as a
    promise of thirty-fold. The model is optimistic and ignores non-overlapping
    bottlenecks, so the achievable share is smaller than the gap."""
    explanation = Comparison(bound=a_bound(floor=3.0), measured=100.0).explanation()

    assert "ceiling, not a target" in explanation
    assert "non-overlapping bottlenecks" in explanation


def test_measuring_below_the_floor_is_an_error_not_an_efficiency_above_one() -> None:
    """A bound claims the work *cannot* be done for less. A measurement under it
    falsifies the claim, and the useful output is that one of the two is wrong."""
    with pytest.raises(ImpossibleBoundError, match="below its floor"):
        Comparison(bound=a_bound(floor=100.0), measured=99.0)


def test_a_floor_of_zero_bounds_nothing_and_says_so() -> None:
    """Not infinite headroom. A bound that permits skipping the work entirely is
    a bound that has not been computed."""
    comparison = Comparison(bound=a_bound(floor=0.0), measured=100.0)

    assert comparison.available is None
    assert comparison.fraction_of_bound is None
    assert comparison.worth_investigating
    assert "gives no ratio" in comparison.explanation()


# --------------------------- AC 3: an optional check, and its ordinary answer


def test_screening_with_no_computable_bound_is_an_answer() -> None:
    """F8's consequence. The ordinary case is that nothing here applies, and an
    optional check that raised on the common path would be switched off — after
    which it would not run on the workloads where a bound does exist either."""
    screening = screen({DB_ROWS: 200.0, DB_QUERY: 200.0})

    assert screening.comparisons == ()
    assert screening.worth_investigating
    assert "says nothing about it" in screening.report()
    assert "opportunistically" in screening.report()


def test_screening_reports_the_metrics_it_could_not_bound() -> None:
    """As prominently as the ones it could. A report listing only what it bounded
    reads as a clearance for everything else."""
    screening = screen(
        {DB_ROWS: 200.0, DB_QUERY: 200.0, "seconds": 0.4},
        [rows_required_by({"orders": ORDERS, "customers": CUSTOMERS}, metric=DB_ROWS)],
    )

    assert screening.unbounded == (DB_QUERY, "seconds")
    assert "No computable floor for db.query, seconds" in screening.report()


def test_the_n_plus_one_workload_has_room_by_the_only_floor_that_is_computable() -> None:
    """The realistic shape. 200 rows returned where 103 are required — and the
    query count, which is the thing actually wrong, is exactly what cannot be
    floored."""
    screening = screen(
        {DB_ROWS: 200.0, DB_QUERY: 200.0},
        [rows_required_by({"orders": ORDERS, "customers": CUSTOMERS}, metric=DB_ROWS)],
    )

    assert screening.worth_investigating
    assert screening.comparisons[0].available == pytest.approx(1.94, rel=0.01)


def test_a_bound_on_a_metric_that_was_not_measured_is_refused() -> None:
    """ADR 013's rule about counters, applied to floors: a mistyped metric name
    must not become a quiet "nothing to bound here"."""
    with pytest.raises(BoundError, match="does not contain"):
        screen({DB_ROWS: 200.0}, [a_bound(metric=DB_BYTES)])


def test_one_bound_with_no_room_does_not_speak_for_the_others() -> None:
    """A workload may be spending its time somewhere nothing here can floor, so
    a single tight bound is not a verdict on the workload."""
    screening = screen(
        {DB_ROWS: 100.0, DB_BYTES: 100.0},
        [a_bound(floor=90.0, metric=DB_ROWS), a_bound(floor=3.0, metric=DB_BYTES)],
    )

    assert not screening.comparisons[0].worth_investigating
    assert screening.worth_investigating


def test_when_every_computable_bound_is_tight_the_report_still_refuses_to_clear_it() -> None:
    screening = screen(
        {DB_ROWS: 100.0, DB_BYTES: 100.0},
        [a_bound(floor=90.0, metric=DB_ROWS), a_bound(floor=80.0, metric=DB_BYTES)],
    )

    assert not screening.worth_investigating
    assert "not a statement that the workload is fast" in screening.report()


def test_the_threshold_is_a_factor_of_the_measurement() -> None:
    """Stated as what an investigation is deciding about — the most a perfect fix
    could win — rather than as a percentage of a bound nobody can act on."""
    just_under = Comparison(bound=a_bound(floor=100.0), measured=100.0 * WORTH_INVESTIGATING * 0.99)
    just_over = Comparison(bound=a_bound(floor=100.0), measured=100.0 * WORTH_INVESTIGATING)

    assert not just_under.worth_investigating
    assert just_over.worth_investigating


# ------------------------------------------------------------- the registration


def test_the_primitive_costs_seconds_because_there_is_no_second_run() -> None:
    """§13: no second run. The bound is arithmetic over a measurement already
    taken, which is what makes it a screening check."""
    primitive = REGISTRY.get("bounds.headroom")

    assert primitive.cost is CostClass.SECONDS


def test_the_primitive_is_offered_everywhere_because_the_gate_is_the_bound_itself() -> None:
    """There is no project fact that decides this. Whether a computable floor
    exists is decided per workload, and `screen` answers it."""
    primitive = REGISTRY.get("bounds.headroom")

    assert primitive.verdict(ProjectProfile()).applicable
    assert primitive.required_capabilities == frozenset()


def test_the_primitive_survives_a_selection_with_nothing_available() -> None:
    selection = REGISTRY.select(ProjectProfile(capabilities=frozenset(Capability)))

    assert "bounds.headroom" in selection.names

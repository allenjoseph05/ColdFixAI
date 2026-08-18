"""Epic 3 composed: one toolkit, five workloads, and the answers each instrument cannot reach.

Every other file here tests one primitive. This one runs the epic as a whole,
because Epic 2's largest finding was that per-module verification says nothing
about composition — after nine stories and 487 passing tests, that epic could not
perform its own purpose, and the architecture actively prevented writing the test
that would have shown it.

The shape of an investigation is what gets composed, in the order
`01-primitives.md` §17 gives and the registry's own ordering enforces: take a
toolkit for the project, screen every workload with the cheapest instrument,
localize what the screen flagged, and check the answer against a floor.

**The composition's real subject is the workload the screen cannot see.**
`list_titles_over_fetching` and `list_titles_narrow` both issue exactly one query
at every volume, so query counting reports the two as identical — correctly, and
uselessly. The guard counter separates them, and then a floor has to say which
one is wasteful, because a payload that is large is not yet a payload that is
larger than it has to be. That hand-off across three primitives is what no
single-module test could check, and it is where this file found something.

**Three defects, found by writing it.** Each is fixed in code, and each was
invisible while the primitives were tested apart:

1. *The registry's contents depended on import order.* Registration is an import
   side effect and nothing imported them all, so a caller with two primitives
   imported got a two-instrument toolkit and a `Selection` listing nothing
   missing — because a primitive nobody imported is not withheld, it does not
   exist. `coldfix.primitives.__init__` now imports every one.

2. *A missing capability outranked a settled fact.* A subject known not to parse
   untrusted input, in an environment with no mutation engine, came back
   `UNSUPPORTED` — which reads as *install a fuzzer* for a subject that will
   never need one. `Primitive.verdict` now takes the most decisive of the two,
   the rule `all_of` already followed.

3. *S-3.18 had no floor for an over-fetch.* The row floor puts the defect and its
   control at exactly 1.0x, because over-fetching is a width defect and the row
   floor measures height. §13's table had named the case — *serialization: fields
   consumed vs fields serialized* — and `fields_required_by` is it.

The decoy is the other half. `summarize_with_fixed_floor` issues 37 queries at
any volume, so it out-costs the real N+1 beside it at every scale below the
crossover — measured here at three of four points, with the defect passing it
somewhere above 36 authors. A screen ranking by cost investigates the wrong
workload on any small dataset. Growth is right at every point, including the ones
where cost is not.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import coldfix.primitives
from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Growth
from coldfix.primitives.bounds import fields_required_by, rows_required_by, screen
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.instructions import separate
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    Capability,
    CostClass,
    ProjectFact,
    ProjectProfile,
    Selection,
)
from coldfix.primitives.scaling import Distribution, ScalingResult, scale_volume
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.loops import (
    linear_scan,
    linearithmic_sort,
    make_items,
    quadratic_membership,
)
from fixtures.planted.queries import (
    DECOY_FIXED_QUERIES,
    list_books_batched,
    list_books_n_plus_one,
    list_titles_narrow,
    list_titles_over_fetching,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store, build_store

# Four points, an order of magnitude apart at the ends. Enough for `fit_growth`
# to recover an exponent, small enough that the whole composition runs in the
# fast subset — these fixtures count operations rather than timing them.
SCALES = (5, 10, 20, 40)

# The cells the narrow projection returns: one column per book row. Anything
# above this on the same output is payload nobody asked for.
COLUMNS_ACTUALLY_USED = 1

# The store's payload guard, and the metric the field floor bounds. Named here
# rather than taken from the counter catalogue because it is the *store's* proxy
# for response bytes, which is a fixture decision rather than a vocabulary one.
CELLS = "cells_returned"

# Thirteen registrations across twelve modules — `scaling` declares both axes of
# primitive 1, which `01-primitives.md` §2 treats as one primitive. Written out
# so that adding a module without wiring it into the package fails here.
EPIC_THREE_PRIMITIVES = 13


@pytest.fixture
def query_counter() -> Iterator[None]:
    """The store's `select`, registered as a counted hook for one test."""
    register_hook(DB_QUERY, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(DB_QUERY)


class StoreReset(ResetMechanism):
    """Deep-copies the store back, which for this fixture is a real reset.

    A snapshot-restore in miniature: the state lives in one object, so putting a
    copy back is the whole of it. S-2.6's verification is what makes it a
    `VerifiedReset` rather than a promise.
    """

    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Workload) -> None:
        self.subject = subject
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def begin(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)


@dataclass
class Workload:
    """One planted workload, seeded fresh at each volume.

    Named the way a real subject would be — the thing under investigation is a
    function plus the data it runs against, and the screen has to sweep both.
    """

    name: str
    call: Any
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def seed(self, scale: int) -> None:
        self.store = build_store(authors=scale, books_per_author=2)

    def invoke(self) -> object:
        return self.call(self.store)

    def process_identity(self) -> str:
        identity = f"{self.name}-container-{len(self.processes)}"
        self.processes.append(identity)
        return identity

    def guard_counters(self) -> Mapping[str, float]:
        """The payload guard. S-3.8's rule: a count without one is half a metric."""
        return {"cells_returned": float(self.store.cells_returned)}


def sweep(workload: Workload) -> ScalingResult:
    """S-3.2 over one workload, with the conditions this fixture can honestly claim."""
    return scale_volume(
        seed=workload.seed,
        invoke=workload.invoke,
        reset=VerifiedReset(
            mechanism=StoreReset(workload),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        scales=SCALES,
        # Declared, not observed: `build_store` gives every author the same
        # number of books, and S-3.3 is what makes that admission load-bearing.
        # Every exclusion below is an exclusion *under uniform data*.
        distribution=Distribution.UNIFORM,
        counters=[DB_QUERY],
        extra_counters=workload.guard_counters,
        process_identity=workload.process_identity,
    )


def toolkit() -> Selection:
    """The instruments offered for a Django-shaped subject with a real database.

    Deliberately partial. Nobody has established whether this project runs
    concurrent code inside a request, so S-3.14 is withheld as UNDETERMINED
    rather than offered or refused — which is the three-answer applicability
    ADR 030 exists for, seen from the composition rather than from one test.
    """
    return REGISTRY.select(
        ProjectProfile(
            capabilities={
                Capability.EVENT_COUNTERS,
                Capability.STACK_CAPTURE,
                Capability.OFF_CPU_TIMING,
                Capability.INSTRUCTION_COUNTING,
                Capability.FIXTURE_SEEDING,
                Capability.FIXTURE_SHAPING,
                Capability.STATE_RESET,
                Capability.DIAGNOSTIC_WORKTREE,
            },
            facts={
                ProjectFact.SERVES_CONCURRENT_REQUESTS: True,
                ProjectFact.LONG_RUNNING_PROCESS: True,
                ProjectFact.PARSES_UNTRUSTED_INPUT: False,
                ProjectFact.HAS_EXTERNAL_DEPENDENCIES: False,
            },
        )
    )


# --------------------------------------------------- the toolkit the epic hands over


def test_every_module_that_registers_a_primitive_is_imported_by_the_package() -> None:
    """The defect this composition found first, and the one that hid the others.

    Registration is a side effect of import, so before `coldfix.primitives`
    imported them all, the contents of `REGISTRY` depended on what a process
    happened to have imported. A caller with `scaling` and `bounds` got a
    two-instrument toolkit and a `Selection` that listed nothing missing —
    because a primitive nobody imported is not *withheld*, it does not exist, and
    absent and inapplicable are the two answers ADR 030 exists to separate.

    Asserted by reading the package directory rather than by listing names here,
    because a list in a test is a list somebody forgets to update at exactly the
    same moment they forget the import.
    """
    package = Path(coldfix.primitives.__file__).parent
    registering = {
        module.stem
        for module in package.glob("*.py")
        if "REGISTRY.register(" in module.read_text(encoding="utf-8")
    }

    imported = {name for name in registering if hasattr(coldfix.primitives, name)}

    assert registering, "no module registers a primitive; this test has stopped testing"
    assert registering == imported


def test_importing_the_package_alone_registers_every_primitive() -> None:
    """The consequence, stated in the vocabulary an investigation uses.

    `Selection` is a snapshot so the tool list cannot change mid-run (ADR 002).
    Nothing made the list *complete* at the moment the snapshot was taken.
    """
    declared = {primitive.name for primitive in REGISTRY.declared()}

    assert {"scaling.volume", "ablation.stub", "inputs.search", "faults.injection"} <= declared
    assert len(declared) == EPIC_THREE_PRIMITIVES


def test_the_registry_hands_over_a_usable_toolkit_and_says_what_it_withheld() -> None:
    """The composition's first step, and the one that decides every later one.

    Nineteen primitives, four states of applicability, and a selection is a
    snapshot — so what an investigation can do is fixed here, before any
    measurement, and everything it cannot do carries a recorded reason.
    """
    selection = toolkit()

    assert "scaling.volume" in selection.names
    assert "bounds.headroom" in selection.names
    assert "observation.instructions" in selection.names

    withheld = {item.primitive.name: item.verdict for item in selection.withheld}
    assert withheld["inputs.search"].applicability is Applicability.NOT_APPLICABLE
    assert withheld["faults.injection"].applicability is Applicability.NOT_APPLICABLE
    # Nobody established this one, which is a different answer and a different
    # next action: go and find out, rather than never ask again.
    assert withheld["perturbation.sensitivity"].applicability is Applicability.UNDETERMINED


def test_the_toolkit_is_ordered_so_the_cheap_instruments_come_first() -> None:
    """§17's advice to the agent, made structural. An investigation that reached
    for the hour-long instrument first would spend its budget before the
    second-long one had ruled anything out."""
    selection = toolkit()
    costs = [REGISTRY.get(name).cost for name in selection.names]

    assert costs == sorted(costs, key=lambda cost: list(CostClass).index(cost))
    assert REGISTRY.get(selection.names[0]).cost is CostClass.SECONDS


# ------------------------------------------------------ screening every workload


def test_the_screen_flags_the_n_plus_one_and_clears_its_control(query_counter: None) -> None:
    """The defect and its clean counterpart, measured the same way.

    A detector that reports N+1 unconditionally passes the first half of this
    and fails the second, which is why the fixture ships the control.
    """
    defect = sweep(Workload("n_plus_one", list_books_n_plus_one))
    control = sweep(Workload("batched", list_books_batched))

    assert defect.fits[DB_QUERY].growth is Growth.LINEAR
    assert control.fits[DB_QUERY].growth is Growth.CONSTANT


def test_the_decoy_costs_more_than_the_defect_and_is_still_not_flagged(
    query_counter: None,
) -> None:
    """The composition's sharpest test, and it is about ranking.

    `summarize_with_fixed_floor` issues 37 queries at every volume — modelled on
    the ~35-query floor S-0.3 measured on a real mature system. At every scale in
    this sweep it costs *more* than the N+1 beside it, so a screen that ranked by
    absolute cost would investigate the wrong workload and find nothing wrong
    with it. Growth is what separates them, and this asserts both halves: the
    decoy really is the more expensive one, and it really is not flagged.
    """
    defect = sweep(Workload("n_plus_one", list_books_n_plus_one))
    decoy = sweep(Workload("decoy", summarize_with_fixed_floor))

    costs = [
        (decoy.points[i].raw[DB_QUERY], defect.points[i].raw[DB_QUERY]) for i in range(len(SCALES))
    ]
    ranked_wrong = [i for i, (by_decoy, by_defect) in enumerate(costs) if by_decoy > by_defect]

    # Ranking by cost gets it backwards at every scale below the crossover, and
    # the crossover is a property of the dataset rather than of either workload:
    # the decoy is flat at 37 while the defect climbs past it somewhere above 36
    # authors. On a small staging dataset — which is what most investigations
    # start from — a cost ranking is wrong.
    assert ranked_wrong == [0, 1, 2]
    assert costs[-1][0] < costs[-1][1]

    # Growth is right at every scale, including the ones where cost is not.
    assert decoy.fits[DB_QUERY].growth is Growth.CONSTANT
    assert defect.fits[DB_QUERY].growth is Growth.LINEAR


def test_the_screen_is_blind_to_the_over_fetch_and_the_guard_counter_is_not(
    query_counter: None,
) -> None:
    """The hand-off this composition exists to check.

    Both workloads issue exactly one query at every volume, so the instrument the
    screen leads with reports them identical — correctly, and uselessly. The
    guard counter S-3.8 requires on every metric is what separates them, and
    without the composition nothing would have checked that the two travel
    together through one sweep.
    """
    defect = sweep(Workload("over_fetch", list_titles_over_fetching))
    control = sweep(Workload("narrow", list_titles_narrow))

    assert defect.fits[DB_QUERY].growth is control.fits[DB_QUERY].growth
    assert defect.points[-1].raw[DB_QUERY] == control.points[-1].raw[DB_QUERY]

    assert defect.points[-1].raw["cells_returned"] > control.points[-1].raw["cells_returned"]


# ------------------------------------------- from a flagged metric to a bounded one


def test_the_row_floor_cannot_see_an_over_fetch_and_the_field_floor_can(
    query_counter: None,
) -> None:
    """S-3.18 closing what S-3.2 opened — but only after composing found the hole.

    A guard counter that rose says the payload is large. It does not say the
    payload is larger than it *has* to be, and the difference between those two
    is the difference between a finding and a complaint.

    The row floor is the wrong floor here, and this asserts that it is: the
    over-fetch returns exactly as many rows as the projected control, so the row
    floor puts it at 1.0x and reports no room in the one workload built to have
    some. Over-fetching is a width defect. `fields_required_by` was added because
    this test failed, and §13's own table had named the case all along —
    *serialization: fields consumed downstream vs fields serialized*.
    """
    workload = Workload("over_fetch", list_titles_over_fetching)
    workload.seed(SCALES[-1])
    titles = workload.invoke()
    assert isinstance(titles, list)

    measured = {
        DB_ROWS: float(workload.store.rows_returned),
        CELLS: float(workload.store.cells_returned),
    }

    by_rows = screen(measured, [rows_required_by({"book titles": titles}, metric=DB_ROWS)])
    by_fields = screen(measured, [fields_required_by(titles, metric=CELLS)])

    assert by_rows.comparisons[0].available == pytest.approx(1.0)
    assert not by_rows.worth_investigating

    assert by_fields.comparisons[0].bound.floor == len(titles) * COLUMNS_ACTUALLY_USED
    assert by_fields.comparisons[0].available == pytest.approx(5.0)
    assert by_fields.worth_investigating


def test_the_field_floor_counts_fields_and_not_items(query_counter: None) -> None:
    """Width, asserted where width and height differ.

    The over-fetch test above cannot prove this on its own: its response is a
    list of plain strings, so one field per item makes the field count and the
    item count the same number, and a floor that counted items would pass. A
    serializer's response is a list of mappings, and there the two diverge —
    which is the shape §13's serialization row is actually about.
    """
    workload = Workload("n_plus_one", list_books_n_plus_one)
    workload.seed(SCALES[0])
    rows = workload.invoke()
    assert isinstance(rows, list)

    floor = fields_required_by(rows, metric=CELLS)

    fields_per_row = 2  # each row carries an author and its books
    assert floor.floor == len(rows) * fields_per_row
    assert floor.floor != len(rows)
    assert "field(s) each" in floor.basis


def test_the_field_floor_clears_the_projected_control(query_counter: None) -> None:
    """The control, which is what stops the field floor flagging every response.

    `list_titles_narrow` returns the same titles from the same rows, projected.
    Its payload is exactly what its response carries, so it sits on its floor.
    """
    workload = Workload("narrow", list_titles_narrow)
    workload.seed(SCALES[-1])
    titles = workload.invoke()
    assert isinstance(titles, list)

    screening = screen(
        {CELLS: float(workload.store.cells_returned)},
        [fields_required_by(titles, metric=CELLS)],
    )

    assert screening.comparisons[0].available == pytest.approx(1.0)
    assert not screening.worth_investigating


# ------------------------------- the metric that works where the timer cannot


def test_two_complexities_are_separated_below_the_timing_floor(query_counter: None) -> None:
    """The last instrument in the chain, on the fixture built to need it.

    `quadratic_membership` hides its quadratic behind `x in list` and has no
    visible nested loop. At the sizes this suite can afford, the wall-clock
    difference against the linear control is far under S-0.4's ~20ms floor and is
    not reportable at all. Counted, the two are separated exactly.
    """
    items = make_items(200)

    comparison = separate(
        lambda: quadratic_membership(list(items)),
        lambda: linear_scan(list(items)),
        label_a="quadratic_membership",
        label_b="linear_scan",
    )

    assert comparison.trustworthy
    assert comparison.cheaper == "linear_scan"
    assert comparison.a.reference_seconds < 0.020  # below the floor that hides it
    assert (comparison.ratio or 0) < 0.2


def test_the_linearithmic_control_is_invisible_because_its_work_is_in_c(
    query_counter: None,
) -> None:
    """Composing found this, and it is a fact about the fixture as much as the tool.

    `linearithmic_sort` is `sorted()`, so its work happens below the interpreter
    and S-3.19 cannot see it. Comparing it to a Python quadratic gives two exact
    counts and no statement about which is faster — which is the instrument
    behaving correctly and refusing, rather than reporting the C implementation
    as almost free. Anything wanting to rank these two needs S-1.6 and a clock.
    """
    items = make_items(200)

    comparison = separate(
        lambda: quadratic_membership(list(items)),
        lambda: linearithmic_sort(list(items)),
        label_a="quadratic_membership",
        label_b="linearithmic_sort",
    )

    assert comparison.b.hidden_work
    assert not comparison.trustworthy
    assert "Neither number is a statement about speed" in comparison.explanation()


# ---------------------------------------------------------------- the null result


def test_the_whole_screen_over_the_clean_workloads_finds_nothing(query_counter: None) -> None:
    """`CLAUDE.md`: null results are valid output, and never manufacture a finding.

    Three workloads that are correct — one already batched, one already
    projected, one expensive on purpose — swept by the same instrument that
    flagged the defects. Nothing grows. An epic that could not produce this
    outcome would be a detector that says yes.
    """
    clean = {
        "batched": list_books_batched,
        "narrow": list_titles_narrow,
        "fixed floor": summarize_with_fixed_floor,
    }

    growths = {
        name: sweep(Workload(name, call)).fits[DB_QUERY].growth for name, call in clean.items()
    }

    assert set(growths.values()) == {Growth.CONSTANT}


def test_every_exclusion_carries_the_shape_it_was_measured_under(query_counter: None) -> None:
    """`CLAUDE.md`: exclusions carry their preconditions.

    *Queries flat across an eightfold volume increase* is only true of the
    fixture shape it was measured under — `build_store` gives every author the
    same two books, and S-3.3 proved the uniform fixture is the blindest one for
    any per-parent cost. The result records that, so a null result cannot be
    quoted without it.
    """
    result = sweep(Workload("batched", list_books_batched))

    assert result.distribution is Distribution.UNIFORM
    assert result.reset_strategy is ResetStrategy.SNAPSHOT_RESTORE
    assert result.cache_control.value  # recorded, not assumed
    assert DECOY_FIXED_QUERIES  # the decoy's floor is a fixture constant, not a threshold

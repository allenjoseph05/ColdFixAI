"""Put it back, and check that you did.

S-3.10. Two halves. Swapping an implementation or a configuration value is easy
and the tests for it are short. **Reverting is the half that has to be
structural**, because a substitution that quietly failed to revert does not
raise, does not stop anything, and silently changes every measurement taken
afterwards — ADR 008's failure with the subject's own configuration instead of
an instrument.

So every substitution here reads the value back after restoring it, and the
tests include the case that motivated it: a restore that appears to work and
does not.

The other thing worth stating in tests is what a sweep *is not*. Measuring eight
pool sizes once each is a search, not a ranking — eight single samples cannot
separate differences below S-0.4's ~20ms noise floor. The candidate becomes a
claim only through S-1.6's interleaved comparison, and `confirm` is the only
function here that produces one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from coldfix.primitives.substitution import (
    IrreversibleError,
    NotRestoredError,
    PlanShape,
    QueryPlan,
    SubstitutionError,
    compare_plans,
    confirm,
    explain,
    substitute,
    substitute_item,
    sweep_configuration,
)


class Serializer:
    """The implementation under substitution."""

    def render(self, rows: int) -> int:
        return sum(range(rows))


class Settings:
    """A settings object, which is what a configuration value usually lives on."""

    def __init__(self, batch_size: int = 1) -> None:
        self.batch_size = batch_size


def cost_of(settings: Settings) -> float:
    """A workload whose cost has a genuine optimum, so a sweep has something to find.

    Too small a batch pays per-batch overhead; too large pays for the batch it
    did not need. The minimum is at 8.
    """
    return 100.0 / settings.batch_size + settings.batch_size * 1.5


# ------------------------------------------------ AC 1: swapping an implementation


def test_an_implementation_is_swapped_for_the_duration_of_the_block() -> None:
    """AC 1. A method on a class, which is what a serializer is."""

    def cheap(self: Serializer, rows: int) -> int:
        return 0

    original = Serializer.render
    with substitute(Serializer, "render", cheap):
        assert Serializer().render(1000) == 0

    assert Serializer.render is original
    assert Serializer().render(4) == 6


def test_a_configuration_value_is_swapped_and_restored() -> None:
    """AC 1's other half, and `01-primitives.md` §9's highest-value sub-case:
    reversible, no syntax risk, bounded search space."""
    settings = Settings(batch_size=1)

    with substitute(settings, "batch_size", 64):
        assert settings.batch_size == 64

    assert settings.batch_size == 1


def test_a_value_the_subject_never_had_can_be_tried_and_is_then_removed() -> None:
    """A configuration key the subject has never set is a legitimate thing to
    try, and restoring it means removing it again rather than setting a zero."""
    settings = Settings()

    with substitute(settings, "prefetch_depth", 4):
        assert settings.prefetch_depth == 4  # type: ignore[attr-defined]

    assert not hasattr(settings, "prefetch_depth")


def test_configuration_held_in_a_mapping_works_the_same_way() -> None:
    """Both shapes exist in real subjects — Django's settings are attributes, a
    parsed config file is a mapping — and a caller should not have to wrap one to
    look like the other."""
    config: dict[str, Any] = {"pool_size": 5}

    with substitute_item(config, "pool_size", 40):
        assert config["pool_size"] == 40
    with substitute_item(config, "timeout", 30):
        assert config["timeout"] == 30

    assert config == {"pool_size": 5}


# --------------------------------------------- AC 3: reversal, verified


def test_the_original_is_restored_even_when_the_block_raises() -> None:
    """The measurement that failed is exactly the case where a subject is most
    likely to be left modified."""
    settings = Settings(batch_size=2)

    with pytest.raises(RuntimeError, match="deliberate"), substitute(settings, "batch_size", 99):
        message = "deliberate"
        raise RuntimeError(message)

    assert settings.batch_size == 2


def test_a_restore_that_does_not_take_is_caught() -> None:
    """The reason reverting is verified rather than performed.

    This object accepts the assignment and keeps its own value, which is what a
    settings object with a cached property or a `__setattr__` of its own does.
    Nothing about the restore looks wrong; only reading it back shows it.
    """

    class Stubborn:
        def __init__(self) -> None:
            object.__setattr__(self, "batch_size", 1)

        def __setattr__(self, name: str, value: object) -> None:
            if value != 99:  # accepts the substitution, refuses the restore
                return
            object.__setattr__(self, name, value)

    subject = Stubborn()

    with (
        pytest.raises(NotRestoredError, match="did not come back"),
        substitute(subject, "batch_size", 99),
    ):
        pass


def test_an_unreadable_original_is_refused_before_anything_changes() -> None:
    """Setting first and discovering afterwards that the original is
    unrecoverable leaves the subject permanently modified — and for a
    configuration value, permanently modified is indistinguishable from always
    having been configured that way."""

    class NoDict:
        __slots__ = ()

    with (
        pytest.raises(IrreversibleError, match="could not be read"),
        substitute(NoDict(), "batch_size", 8),
    ):
        pass


def test_a_descriptor_is_refused() -> None:
    """S-1.3's rule. A `classmethod` replaced by a plain value stops receiving
    its class, so the measurement is a correct number about a different
    program."""

    class Owner:
        @classmethod
        def build(cls) -> int:
            return 1

    with pytest.raises(IrreversibleError, match="classmethod"), substitute(Owner, "build", 2):
        pass


def test_an_attribute_that_cannot_be_set_changes_nothing() -> None:
    """The refusal costs nothing here because nothing has happened yet."""

    class Frozen:
        __slots__ = ("kept",)

        def __init__(self) -> None:
            self.kept = 1

    subject = Frozen()

    with pytest.raises(IrreversibleError), substitute(subject, "unknown", 2):
        pass

    assert subject.kept == 1


# ------------------------------------------------ AC 2: sweeping a range


def test_a_sweep_measures_every_value_and_names_the_best() -> None:
    """AC 2. The bounded search `01-primitives.md` §9 calls the highest-value
    sub-case."""
    settings = Settings(batch_size=1)

    sweep = sweep_configuration(
        settings, "batch_size", [1, 2, 8, 32, 128], lambda: cost_of(settings)
    )

    assert [reading.value for reading in sweep.readings] == [1, 2, 8, 32, 128]
    assert sweep.candidate == 8
    assert sweep.changes_anything


def test_the_value_is_restored_between_every_reading() -> None:
    """A reading taken with two substitutions live at once is a measurement of
    something nobody asked about."""
    settings = Settings(batch_size=3)
    seen: list[int] = []

    def observe() -> float:
        seen.append(settings.batch_size)
        return 1.0

    sweep_configuration(settings, "batch_size", [1, 2, 4], observe)

    assert seen == [1, 2, 4]
    assert settings.batch_size == 3


def test_a_sweep_says_it_is_a_search_and_not_a_finding() -> None:
    """Eight single samples cannot separate differences below the noise floor,
    and a configuration value tuned on one workload is a claim about that
    workload only."""
    settings = Settings(batch_size=1)

    sweep = sweep_configuration(settings, "batch_size", [1, 8], lambda: cost_of(settings))

    assert "search result, not a finding" in sweep.explanation()
    assert "that workload only" in sweep.explanation()


def test_a_sweep_that_recovers_the_incumbent_proposes_nothing() -> None:
    """A real result: the configuration is already the best over the range
    tried. Proposing a change here would be proposing the value it already had."""
    settings = Settings(batch_size=8)

    sweep = sweep_configuration(settings, "batch_size", [8, 32, 128], lambda: cost_of(settings))

    assert sweep.candidate == 8
    assert not sweep.changes_anything
    assert "nothing to propose" in sweep.explanation()


def test_one_value_is_not_a_sweep() -> None:
    settings = Settings()

    with pytest.raises(SubstitutionError, match="at least two values"):
        sweep_configuration(settings, "batch_size", [4], lambda: 1.0)


def test_a_sweep_can_be_told_that_larger_is_better() -> None:
    """Throughput goes up where latency goes down, and a sweep that assumed one
    direction would silently recommend the worst value for the other."""
    settings = Settings(batch_size=1)

    sweep = sweep_configuration(
        settings,
        "batch_size",
        [1, 8, 128],
        lambda: float(settings.batch_size),
        lower_is_better=False,
    )

    assert sweep.candidate == 128


# -------------------------------- the candidate becomes a claim through S-1.6


def test_confirming_puts_the_candidate_against_the_incumbent_interleaved() -> None:
    """S-1.6 takes both variants as callables and runs them alternately, which is
    what removes the drift a block design absorbs into the delta — and is why a
    stored measurement cannot be passed to it."""
    settings = Settings(batch_size=1)
    sweep = sweep_configuration(settings, "batch_size", [1, 8], lambda: cost_of(settings))

    result = confirm(settings, "batch_size", sweep, lambda: cost_of(settings), n=8, seed=7)

    assert result.rounds == 8
    assert len(result.run_a) == len(result.run_b) == 8
    assert "batch_size=8" in result.label_b
    assert settings.batch_size == 1


def test_confirming_a_candidate_that_changes_nothing_is_refused() -> None:
    """And the refusal says the thing worth recording: over this range, the
    configuration is already at its best."""
    settings = Settings(batch_size=8)
    sweep = sweep_configuration(settings, "batch_size", [8, 128], lambda: cost_of(settings))

    with pytest.raises(SubstitutionError, match="already at its best"):
        confirm(settings, "batch_size", sweep, lambda: cost_of(settings), n=8)


# ----------------------------------------- AC 4: query plans, and their limits


def plan_payload(node: str, cost: float, *, children: list[dict[str, Any]] | None = None) -> str:
    return json.dumps(
        [
            {
                "Plan": {
                    "Node Type": node,
                    "Total Cost": cost,
                    "Plan Rows": 10,
                    "Plans": children or [],
                }
            }
        ]
    )


class FakeConnection:
    """Returns whatever plan a test wants, in the shape Postgres returns it."""

    def __init__(self, payloads: list[str]) -> None:
        self.payloads = payloads
        self.statements: list[str] = []

    def execute(self, query: str, params: Any = (), /) -> Any:
        self.statements.append(query)
        payload = self.payloads.pop(0)

        class Result:
            def fetchone(self) -> tuple[str]:
                return (payload,)

        return Result()


def test_a_plan_is_read_rather_than_scraped() -> None:
    """AC 4. JSON, so the node types come out as data."""
    connection = FakeConnection([plan_payload("Seq Scan", 431.0)])

    plan = explain(connection, "SELECT * FROM ticket WHERE status = 'open'")

    assert plan.node_types == ("Seq Scan",)
    assert plan.shape is PlanShape.SEQUENTIAL
    assert plan.estimated_cost == 431.0
    assert "FORMAT JSON" in connection.statements[0]


def test_nested_plan_nodes_are_all_seen() -> None:
    """An index scan under a sort is still an index scan, and a comparison that
    only read the outermost node would miss every plan with a wrapper."""
    connection = FakeConnection(
        [
            plan_payload(
                "Sort", 500.0, children=[json.loads(plan_payload("Index Scan", 8.2))[0]["Plan"]]
            )
        ]
    )

    plan = explain(connection, "SELECT ...")

    assert plan.node_types == ("Sort", "Index Scan")
    assert plan.shape is PlanShape.INDEXED


def test_an_index_hypothesis_shows_up_as_a_change_of_shape() -> None:
    """The fact an index hypothesis predicts: the planner stopped reading the
    whole relation."""
    before = explain(FakeConnection([plan_payload("Seq Scan", 431.0)]), "SELECT ...")
    after = explain(FakeConnection([plan_payload("Index Scan", 8.2)]), "SELECT ...")

    change = compare_plans(before, after)

    assert change.became_indexed
    assert change.shape_changed
    assert change.estimated_cost_ratio < 0.05


def test_an_index_the_planner_ignores_is_reported_as_such() -> None:
    """The control, and a real outcome: adding an index and seeing the same plan
    means the planner declined to use it."""
    before = explain(FakeConnection([plan_payload("Seq Scan", 431.0)]), "SELECT ...")
    after = explain(FakeConnection([plan_payload("Seq Scan", 431.0)]), "SELECT ...")

    change = compare_plans(before, after)

    assert not change.became_indexed
    assert "the planner declined to use" in change.explanation()


def test_plan_costs_are_labelled_as_the_planner_s_opinion() -> None:
    """The project's first non-negotiable is no finding without a measurement,
    and `EXPLAIN` without `ANALYZE` measures nothing — it reports what the
    planner believes."""
    before = explain(FakeConnection([plan_payload("Seq Scan", 431.0)]), "SELECT ...")
    after = explain(FakeConnection([plan_payload("Index Scan", 8.2)]), "SELECT ...")

    change = compare_plans(before, after)

    assert not after.measured
    assert "not a measurement" in change.explanation()
    assert "time the workload or count its queries" in change.explanation()


def test_analyze_executes_the_statement_and_says_the_timing_is_real() -> None:
    """`EXPLAIN ANALYZE` of an `INSERT` inserts, which is why it is not the
    default and why the production guard is what makes it safe rather than
    harmless."""
    payload = json.dumps(
        [
            {
                "Plan": {
                    "Node Type": "Index Scan",
                    "Total Cost": 8.2,
                    "Plan Rows": 10,
                    "Actual Total Time": 12.5,
                }
            }
        ]
    )
    connection = FakeConnection([payload])

    plan = explain(connection, "SELECT ...", analyze=True)

    assert plan.measured
    assert plan.actual_seconds == pytest.approx(0.0125)
    assert "ANALYZE" in connection.statements[0]


def test_a_plan_that_cannot_be_parsed_is_refused() -> None:
    connection = FakeConnection([json.dumps({"not": "a plan"})])

    with pytest.raises(SubstitutionError, match="cannot read"):
        explain(connection, "SELECT ...")


def test_a_plan_with_no_index_and_no_scan_is_neither() -> None:
    """A category that exists rather than being forced into one of the two.
    Reporting a `Result` node as a sequential scan would be a plan shape nobody
    measured."""
    plan = QueryPlan(node_types=("Result",), estimated_cost=0.01, estimated_rows=1)

    assert plan.shape is PlanShape.OTHER

"""The N+1 is linear, so "flag superlinear growth" would walk past it.

S-4.3. The first test in this file is the story's whole problem: a textbook N+1
grows *linearly* in query count, in this project's planted fixture and in the
unplanted defect ADR 011 pinned the development target for. AC 1 read literally
flags superlinear growth and would clear it.

The second is the decoy, from the other direction. `summarize_with_fixed_floor`
issues 37 queries at any volume, modelled on the ~35-query floor S-0.3 measured
on a real mature system, and the fixture README is explicit that flagging it is
the metastability trap. AC 1 also asks for "unexplained high flat cost", and the
two requirements meet here.

The third is the ordering. `08-audit.md` §6 gives the exact scenario — a tenfold
win on a monthly batch job against a twofold win on the hottest endpoint — and
the requirement is not that the ranking solve it. It is that the ranking say it
cannot.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Growth
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.flagging import (
    FLAT_COST_THRESHOLD,
    FREQUENCY_UNKNOWN,
    FlaggingError,
    FlagKind,
    WithheldReason,
    expected_growth,
    flag,
    rank,
    withheld_reason,
)
from coldfix.screening.growth import MetricGrowth, screen_growth
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    BoundWorkload,
    FixtureRecipe,
    Observation,
    Workload,
)
from fixtures.planted.queries import (
    list_books_batched,
    list_books_n_plus_one,
    list_titles_narrow,
    list_titles_over_fetching,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(DB_QUERY, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(DB_QUERY)


class StoreReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Subject) -> None:
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
class Subject:
    call: Any
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def scale(self, n: int) -> None:
        self.store = build_store(authors=n, books_per_author=2)

    def invoke(self) -> object:
        return self.call(self.store)

    def process_identity(self) -> str:
        self.processes.append(f"container-{len(self.processes)}")
        return self.processes[-1]

    def payload(self) -> Mapping[str, float]:
        return {
            CELLS: float(self.store.cells_returned),
            DB_ROWS: float(self.store.rows_returned),
            RESPONSE_BYTES: float(self.store.cells_returned * 8),
        }


def screened(name: str, call: Any) -> Any:
    """One planted workload, swept by S-4.2 exactly as an investigation would."""
    subject = Subject(call)
    descriptor = Workload(
        id=name,
        description=f"the planted {name} workload",
        entry_point=f"fixtures.planted.queries.{call.__name__}",
        fixture=FixtureRecipe(
            entity="author",
            per_parent=2,
            distribution=Distribution.UNIFORM,
            source="fixtures.planted.store.build_store",
            seed=0,
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )
    bound = BoundWorkload(
        descriptor,
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        extra_counters=subject.payload,
    )
    return screen_growth(bound, counters=[DB_QUERY])


# ----------------------------------- AC 1: the defect the literal reading misses


def test_the_n_plus_one_is_flagged_although_its_growth_is_only_linear(
    query_counter: None,
) -> None:
    """The story's central problem, measured rather than argued.

    Query count grows linearly here — it is `1 + A`, one per author — so a screen
    flagging only superlinear growth clears the single defect this system was
    built around, and clears it in the pinned development target too.
    """
    result = screened("n.plus.one", list_books_n_plus_one)

    assert result.metric(DB_QUERY).growth is Growth.LINEAR

    flags = {item.metric: item for item in flag(result)}

    assert DB_QUERY in flags
    assert flags[DB_QUERY].kind is FlagKind.GROWTH
    assert flags[DB_QUERY].expected is Growth.CONSTANT


def test_the_batched_control_is_skipped(query_counter: None) -> None:
    """AC 4, and the reason the fixture ships a control at all: a detector that
    reports N+1 unconditionally passes the test above and fails this one."""
    result = screened("batched", list_books_batched)

    assert not flag(result)


def test_a_round_trip_count_is_expected_to_stay_constant() -> None:
    """The rule the flag rests on, stated where it can be read.

    One batched query serves a hundred rows as easily as ten, which is exactly
    what a fix for an N+1 produces — so a round-trip count that climbs with the
    data is the shape being removed.
    """
    assert expected_growth(DB_QUERY) is Growth.CONSTANT


def test_an_amount_is_expected_to_grow_and_a_stranger_metric_too() -> None:
    """More data is more data. And a metric nothing is known about gets the
    linear expectation, so it is flagged only when superlinear — AC 1 read
    literally, kept as the default for the unknown case."""
    assert expected_growth(DB_ROWS) is Growth.LINEAR
    assert expected_growth("something.nobody.registered") is Growth.LINEAR


def test_rows_growing_linearly_is_not_flagged(query_counter: None) -> None:
    """The other half of the same rule, on a workload that returns more data
    because there is more data."""
    result = screened("over.fetch", list_titles_over_fetching)

    assert result.metric(DB_ROWS).growth is Growth.LINEAR
    assert DB_ROWS not in {item.metric for item in flag(result)}


# ------------------------------- AC 1: flat cost, and the decoy it must not call a defect


def test_the_decoy_is_not_flagged_for_growth(query_counter: None) -> None:
    """`summarize_with_fixed_floor` issues 37 queries at any volume. The fixture
    README is explicit that a fix here is the metastability trap `00-BRIEF.md` §4
    warns about — an optimization that improves every metric measured while
    removing slack."""
    result = screened("fixed.floor", summarize_with_fixed_floor)

    assert result.metric(DB_QUERY).growth is Growth.CONSTANT
    assert not [item for item in flag(result) if item.kind is FlagKind.GROWTH]


def test_the_flat_cost_threshold_sits_clear_of_a_real_mature_floor() -> None:
    """S-0.3 measured ~35 queries as a real endpoint's floor, and the decoy sits
    at 37. A threshold that caught either would teach a reader that the ordinary
    shape of a mature system is a defect."""
    assert FLAT_COST_THRESHOLD >= 3 * 35


def test_a_genuinely_high_flat_cost_is_flagged_and_says_it_may_be_correct(
    query_counter: None,
) -> None:
    """AC 1's second half. The flag exists; what it claims is deliberately weak,
    because "unexplained" is a thing screening has no way to establish."""
    result = screened("fixed.floor", summarize_with_fixed_floor)
    inflated = result.workload.observations[-1].metrics[DB_QUERY]
    assert inflated < FLAT_COST_THRESHOLD  # the decoy is below it, as intended

    raised = _with_metric(result, DB_QUERY, FLAT_COST_THRESHOLD + 20)
    flags = flag(raised)

    assert [item.kind for item in flags] == [FlagKind.FLAT_COST]
    assert "may be correct" in flags[0].explanation()
    assert "metastability trap" in flags[0].explanation()


def test_a_flat_duration_is_never_flagged_on_cost(query_counter: None) -> None:
    """S-0.4 measured wall-clock timings drifting 12% between runs minutes apart
    while counters reproduced to the byte. A cost threshold on the noisy one
    would flag a workload for having been measured on a slow afternoon."""
    result = screened("batched", list_books_batched)

    raised = _with_metric(result, SECONDS, FLAT_COST_THRESHOLD + 500, everywhere=True)
    flat = _with_fit(raised, SECONDS, Growth.CONSTANT)

    assert not flag(flat)


def test_a_duration_that_rose_less_than_the_noise_floor_cannot_flag(
    query_counter: None,
) -> None:
    """The rule this screen found by flagging its own control.

    The batched workload — the clean counterpart, the shape a fix produces — came
    back `SUPERLINEAR` in `seconds` at 8.7x across a sixteenfold sweep, on a
    workload that runs in under a millisecond. Screening takes one sample per
    scale point, so a fitted exponent over four single samples of a
    sub-millisecond workload is a fit to noise.

    Stated deterministically here rather than left to whether the noise happens
    to cooperate: the same shape, with the absolute rise set on either side of
    S-0.4's floor.
    """
    result = screened("batched", list_books_batched)
    shaped = _with_fit(result, SECONDS, Growth.SUPERLINEAR)

    measurable = _with_metric(shaped, SECONDS, 0.400, at_smallest=True)
    imperceptible = _with_metric(measurable, SECONDS, 0.410)
    real = _with_metric(measurable, SECONDS, 1.400)

    assert SECONDS not in {item.metric for item in flag(imperceptible)}
    assert SECONDS in {item.metric for item in flag(real)}


def test_a_duration_too_small_to_measure_at_all_cannot_flag(query_counter: None) -> None:
    """Found by Epic 4's composition check, and it is the same clock granularity
    that bit S-3.7 and S-3.13.

    `cpu_seconds` comes from `process_time`, which moves in ~15.6ms steps on
    Windows. A sub-millisecond workload records zero ticks at the small scale and
    two at the large one, so a **quantisation artefact of 31ms** clears a 20ms
    floor and flags a workload that did nothing — which is what happened to the
    batched control the first time the whole epic ran at once. Both ends have to
    be measurable, or the denominator is rounding.
    """
    result = screened("batched", list_books_batched)
    shaped = _with_fit(result, SECONDS, Growth.SUPERLINEAR)

    quantised = _with_metric(_with_metric(shaped, SECONDS, 0.0, at_smallest=True), SECONDS, 0.03125)

    assert SECONDS not in {item.metric for item in flag(quantised)}


def test_a_count_needs_no_absolute_floor_to_flag(query_counter: None) -> None:
    """Counts reproduce to the integer, so the shape is the whole of the
    evidence. Two queries becoming thirty-two is thirty extra round trips
    whatever a clock says about them."""
    result = screened("n.plus.one", list_books_n_plus_one)

    assert DB_QUERY in {item.metric for item in flag(result)}


# --------------------------------- AC 2 and 3: ranked, and honest about it


def test_the_ranking_states_that_call_frequency_is_unknown(query_counter: None) -> None:
    """AC 3 and `08-audit.md` §6. An ordering that looks like a priority *is* a
    priority to whoever reads it, and there is no call-frequency information
    anywhere in this system."""
    ranking = rank([screened("n.plus.one", list_books_n_plus_one)])

    assert FREQUENCY_UNKNOWN in ranking.report()
    assert "monthly batch job" in ranking.report()


def test_the_audits_own_scenario_ranks_the_way_it_says_it_will(
    query_counter: None,
) -> None:
    """The exact case §6 raises: a tenfold win on something rare sorting above a
    twofold win on something hot. The requirement is not that the ranking solve
    it — nothing measured here can — but that it not imply a priority it cannot
    justify.
    """
    rare = _with_ratio(screened("monthly.batch", list_books_n_plus_one), DB_QUERY, 10.0)
    hot = _with_ratio(screened("hot.endpoint", list_books_n_plus_one), DB_QUERY, 2.0)

    ranking = rank([hot, rare])

    assert next(item.workload_id for item in ranking.flagged) == "monthly.batch"
    assert "cannot tell the two apart" in ranking.report()


def test_growth_flags_outrank_flat_cost_flags(query_counter: None) -> None:
    """Two different units, and no honest exchange rate between them. A metric
    watched across a sixteenfold increase and found to grow is stronger evidence
    than a number that crossed a threshold somebody chose."""
    growing = screened("n.plus.one", list_books_n_plus_one)
    flat = _with_metric(
        screened("fixed.floor", summarize_with_fixed_floor),
        DB_QUERY,
        FLAT_COST_THRESHOLD * 100,
    )

    ranking = rank([flat, growing])

    assert ranking.flagged[0].kind is FlagKind.GROWTH
    assert ranking.flagged[-1].kind is FlagKind.FLAT_COST


def test_healthy_workloads_are_named_rather_than_ranked_low(query_counter: None) -> None:
    """AC 4. A healthy workload with a low score still appears on a list of
    findings, and S-4.5 needs the names to report what was looked at."""
    ranking = rank(
        [
            screened("n.plus.one", list_books_n_plus_one),
            screened("batched", list_books_batched),
            screened("narrow", list_titles_narrow),
        ]
    )

    assert ranking.healthy == ("batched", "narrow")
    assert "batched" not in {item.workload_id for item in ranking.flagged}
    assert set(ranking.screened) == {"n.plus.one", "batched", "narrow"}


def test_a_metric_whose_growth_could_not_be_fitted_is_neither_flagged_nor_cleared(
    query_counter: None,
) -> None:
    """*Could not tell* is a third answer, and S-4.5 needs it separated from
    *nothing there*. A metric that was zero at some scale point has no exponent,
    because the power fit runs through logarithms."""
    result = screened("batched", list_books_batched)
    unfittable = _unfitted(_with_metric(result, CELLS, 0.0, at_smallest=True), CELLS)

    ranking = rank([unfittable])

    assert ("batched", CELLS) in ranking.unclassified
    assert CELLS not in {item.metric for item in ranking.flagged}
    assert "could not tell" in ranking.report()


def test_a_metric_that_could_not_have_flagged_is_not_reported_as_unclassified(
    query_counter: None,
) -> None:
    """Found by Epic 4's composition check, and it is about caveat inflation.

    `blocked_seconds` is elapsed minus CPU, so on a workload fast enough for the
    two clocks to agree it is zero or negative and has no exponent — unfittable
    on essentially every healthy fixture here. Recorded as *could not tell*, it
    made every null result say it did not cover everything it screened, and a
    caveat attached to everything is one a reader learns to skip. It also could
    not have flagged: it is below the timing floor at both ends, so its exponent
    was never going to change anything.
    """
    result = screened("batched", list_books_batched)

    unfittable_duration = _unfitted(result, SECONDS)
    unfittable_count = _unfitted(result, DB_QUERY)

    assert rank([unfittable_duration]).unclassified == ()
    assert rank([unfittable_count]).unclassified == (("batched", DB_QUERY),)


def test_ranking_nothing_is_an_error_and_not_a_clean_bill(query_counter: None) -> None:
    """Nothing found and nothing looked at are different answers, and S-4.5
    reports only the first."""
    with pytest.raises(FlaggingError, match="different answers"):
        rank([])


def test_a_screen_that_flagged_nothing_reports_what_it_looked_at(
    query_counter: None,
) -> None:
    ranking = rank([screened("batched", list_books_batched)])

    assert not ranking.flagged
    assert "Screened 1 workloads and flagged none" in ranking.report()


def _with_metric(
    result: Any,
    metric: str,
    value: float,
    *,
    at_smallest: bool = False,
    everywhere: bool = False,
) -> Any:
    """The same screening result with one measurement replaced.

    Rewriting a measured number is normally the thing this project refuses. It is
    done here to reach a *threshold* branch and an *unfittable* branch that the
    planted fixture cannot produce — the decoy sits below the flat-cost line by
    design, and no planted metric is zero mid-sweep. The growth flags above are
    all measured.
    """
    observations = list(result.workload.observations)
    positions = range(len(observations)) if everywhere else [0 if at_smallest else -1]
    for index in positions:
        updated = dict(observations[index].metrics)
        updated[metric] = value
        observations[index] = Observation(scale=observations[index].scale, metrics=updated)

    return replace(
        result,
        workload=result.workload.model_copy(update={"observations": tuple(observations)}),
    )


def _unfitted(result: Any, metric: str) -> Any:
    """The same result with one metric's growth left unclassified.

    What a metric that was zero at some scale point produces: the power fit runs
    through logarithms, so it has no exponent and no growth class. Kept apart
    from `_with_metric` because coupling the two hid a real failure — a test
    setting a metric at the smallest scale silently also erased its fit, and the
    duration rule it was meant to exercise was never reached.
    """
    growth = dict(result.growth)
    growth[metric] = replace(growth[metric], fit=_unfittable(growth[metric]), ratio=None)
    return replace(result, growth=growth)


def _with_ratio(result: Any, metric: str, ratio: float) -> Any:
    """The same screening result with one metric's growth ratio replaced.

    Magnitude is what the ranking sorts on, and the planted fixture offers only
    one growth ratio — every workload in it that grows, grows with the sweep. The
    audit's scenario is specifically about two *different* magnitudes, so the
    number is stated rather than measured. What is being tested is the sort and
    the sentence attached to it, both of which are arithmetic over this value.
    """
    growth = dict(result.growth)
    growth[metric] = replace(growth[metric], ratio=ratio)
    return replace(result, growth=growth)


def _with_fit(result: Any, metric: str, growth: Growth) -> Any:
    """The same result with one metric's growth classification stated.

    A duration fitted over four single samples of a sub-millisecond workload
    lands wherever the noise puts it, so a test that waited for it to come back
    superlinear would be flaky in both directions — and one of these sabotages
    passed for exactly that reason. The shape is stated; what is under test is
    the rule applied to it.
    """
    growths = dict(result.growth)
    growths[metric] = replace(growths[metric], fit=replace(growths[metric].fit, growth=growth))
    return replace(result, growth=growths)


def _unfittable(measured: MetricGrowth) -> Any:
    """A fit with no exponent, which is what a zero at any scale point produces."""
    return replace(measured.fit, exponent=None, power_r_squared=None, growth=None)


# ========================== S-16.3: the negative half of the same decision


@pytest.mark.parametrize(
    "call",
    [list_books_n_plus_one, list_books_batched, summarize_with_fixed_floor],
)
def test_every_fitted_metric_is_either_flagged_or_withheld_with_a_reason(
    query_counter: None, call: Any
) -> None:
    """The property that stops the two decisions drifting apart.

    S-16.3 needed to say *why* a metric raised no flag, and the first attempt
    inferred it from the shapes and got it wrong. `withheld_reason` is the
    negative half of `flag`'s own decision, which is only worth anything if the
    two partition the metrics: **exactly one of them is true of every metric that
    could be fitted.**

    Without this, a change to `_above_the_noise` moves one and not the other, and
    the null result starts explaining flags that were raised or staying silent
    about metrics that were not.
    """
    result = screened("subject", call)
    flagged = {item.metric for item in flag(result)}

    for metric, measured in result.growth.items():
        if measured.growth is None:
            continue
        reason = withheld_reason(metric, measured, result)
        assert (metric in flagged) != (reason is not None), (
            f"{metric} is {'flagged' if metric in flagged else 'not flagged'} "
            f"and withheld_reason said {reason!r}"
        )


def test_the_two_reasons_are_different_statements(query_counter: None) -> None:
    """One is about the code, the other about the instrument.

    A metric that did what it may and a metric that did more than it may but by
    less than the harness can resolve are both unflagged, and reporting them
    identically would call the second one clean.
    """
    assert "fit to noise" in WithheldReason.BELOW_THE_NOISE_FLOOR.value
    assert "fit to noise" not in WithheldReason.WITHIN_EXPECTATION.value


def test_an_unfittable_metric_is_withheld_by_neither(query_counter: None) -> None:
    """`unclassified` is a third answer, and it belongs to `rank`.

    Returning a reason here would put *could not tell* into the measured basis,
    which is the collapse S-4.5's own docstring is arranged around.
    """
    result = screened("subject", list_books_batched)
    unfittable = MetricGrowth(
        metric=SECONDS,
        kind=result.growth[SECONDS].kind,
        fit=replace(result.growth[SECONDS].fit, growth=None),
        ratio=None,
    )

    assert withheld_reason(SECONDS, unfittable, result) is None

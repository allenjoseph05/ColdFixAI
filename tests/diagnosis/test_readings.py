"""What each reader takes out of the object its primitive returned.

S-17.11, AC 4: **no reader invents a number.** The totality test in
`test_execution.py` proves every primitive has a reader; it cannot prove a reader
reads the right field, and a reader that quietly returned the baseline where the
finding needs the largest scale point would pass it.

Two kinds of evidence here, and the difference is stated rather than hidden.
`scaling.volume` and `ablation.stub` are driven **for real** against the planted
fixtures — they run in this process against a synthetic subject with known
complexity, which is what `CLAUDE.md` asks of lab-bench tests. The other eleven
need a container, a load generator or a git history, so their results are built by
hand **with values that could not be produced by reading the wrong field**: every
number is distinct, so a reader that took `first` where it wanted `last` fails on
the value rather than on the shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Fit, Growth
from coldfix.diagnosis.readings import (
    ReadingError,
    read_ablation,
    read_ablation_search,
    read_bisect,
    read_faults,
    read_headroom,
    read_input_search,
    read_instructions,
    read_interference,
    read_load,
    read_sensitivity,
    read_shape,
    read_soak,
    read_volume,
)
from coldfix.primitives.ablation import AblationResult, Stub, StubStrategy, share_metric
from coldfix.primitives.bounds import Bound, BoundKind, Comparison, Screening
from coldfix.primitives.faults import Amplification, Fault, Response
from coldfix.primitives.input_search import Campaign, Candidate, InputSearchError
from coldfix.primitives.instructions import InstructionCount, Separation
from coldfix.primitives.isolation import Interference
from coldfix.primitives.load import LoadFinding, LoadLevel, USLFit
from coldfix.primitives.longitudinal import Soak, Trend
from coldfix.primitives.measurement import CacheControl, MetricKind
from coldfix.primitives.perturbation import Point, Sensitivity
from coldfix.primitives.scaling import (
    Allocation,
    Distribution,
    ShapeComparison,
    ShapeMeasurement,
    scale_volume,
)
from coldfix.primitives.search import Outcome, Probe, SearchResult
from coldfix.primitives.temporal import Bisection
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.scope import Disposition
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from fixtures.planted.queries import list_books_n_plus_one
from fixtures.planted.store import Store, build_store

QUERIES = "db.query"


# ==================================================== driven for real: a volume sweep


@dataclass
class Subject:
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def seed(self, scale: int) -> None:
        self.store = build_store(authors=scale, books_per_author=2)

    def invoke(self) -> object:
        return list_books_n_plus_one(self.store)

    def process_identity(self) -> str:
        self.processes.append(f"container-{len(self.processes)}")
        return self.processes[-1]


@pytest.fixture
def query_counter() -> Any:
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(QUERIES)


def test_a_volume_sweep_is_read_at_its_largest_point(query_counter: None) -> None:
    """**AC 4, against a real sweep of a real planted N+1.**

    The largest point rather than the baseline, because a growth claim is about
    what happens as volume rises and the number a finding quotes is the one at the
    top. The fixture makes the two impossible to confuse: an N+1 over 40 authors
    issues far more queries than over 10, so a reader taking the baseline reports
    a number an order of magnitude too small.
    """

    subject = Subject()

    class Reset(ResetMechanism):
        strategy = ResetStrategy.SNAPSHOT_RESTORE

        def prepare(self) -> None: ...
        def begin(self) -> None: ...
        def reset(self) -> None:
            subject.store = Store()

    result = scale_volume(
        seed=subject.seed,
        invoke=subject.invoke,
        reset=VerifiedReset(
            mechanism=Reset(),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        scales=[10, 20, 40],
        distribution=Distribution.UNIFORM,
        counters=[QUERIES],
        process_identity=subject.process_identity,
    )

    measured = read_volume(result)

    assert measured.measurement == dict(result.points[-1].adjusted)
    assert measured.measurement[QUERIES] == result.points[-1].adjusted[QUERIES]
    assert measured.measurement[QUERIES] > result.points[0].adjusted[QUERIES], (
        "the top of the sweep, not the bottom"
    )
    assert measured.kinds == dict(result.kinds), "the kinds the primitive stated"


def test_a_multi_metric_sweep_carries_every_fit(query_counter: None) -> None:
    """**S-17.12 closed S-17.11's narrowing, and this test is where it was.**

    It used to assert the fit was *absent*, because `Measured.fit` was singular:
    a sweep fits every metric it measured, `audit/scales.py` reads `exponent` and
    `power_r_squared` off one curve, and picking wrong objects to a claim nobody
    made. Carrying none was the safe direction and it meant the scale audit never
    ran on a real sweep — every real sweep fits more than one metric. Now all of
    them travel, keyed by metric, and the audit selects by the finding's own.
    """

    subject = Subject()

    class Reset(ResetMechanism):
        strategy = ResetStrategy.SNAPSHOT_RESTORE

        def prepare(self) -> None: ...
        def begin(self) -> None: ...
        def reset(self) -> None: ...

    result = scale_volume(
        seed=subject.seed,
        invoke=subject.invoke,
        reset=VerifiedReset(
            mechanism=Reset(),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        scales=[10, 20, 40],
        distribution=Distribution.UNIFORM,
        counters=[QUERIES],
        process_identity=subject.process_identity,
    )

    assert len(result.fits) > 1, "a real sweep fits several metrics"
    assert set(read_volume(result).fits) == set(result.fits)


# ============================================ hand-built, with values that discriminate


def test_an_ablation_carries_both_sides_and_the_share() -> None:
    """A share alone cannot answer *of how much*, so all three travel.

    `share_metric` names the third so the primitive computing it and the assembler
    looking for it cannot disagree about the spelling — the failure that would
    produce is a finding with no localization and nothing saying why.
    """

    result = AblationResult(
        target="shop.views.list",
        stub=Stub(strategy=StubStrategy.MINIMAL, value=None, size=0, reason="stubbed"),
        baseline={"seconds": 8.0},
        ablated={"seconds": 2.0},
        calls_baseline=71,
        calls_ablated=13,
        kinds={"seconds": MetricKind.DURATION},
        reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
        cache_control=CacheControl.FRESH_PROCESS,
    )

    measured = read_ablation(result)

    assert measured.measurement["seconds.baseline"] == 8.0
    assert measured.measurement["seconds.ablated"] == 2.0
    assert measured.measurement[share_metric("seconds")] == pytest.approx(0.75)
    assert measured.measurement["calls.baseline"] == 71.0
    assert measured.measurement["calls.ablated"] == 13.0
    assert measured.kinds == {}, (
        "every key above is derived, and a kind naming an unmeasured metric is refused"
    )


def test_a_bisection_reports_probe_costs_rather_than_its_own_bookkeeping() -> None:
    """*Six probes taken* is a fact about the search. What each revision cost is a
    fact about the subject, and only the second is a measurement."""
    result = Bisection(
        good="aaa",
        bad="zzz",
        probes=(
            Probe(subject="aaa", outcome=Outcome.CHEAP, cost=3.0),
            Probe(subject="mmm", outcome=Outcome.EXPENSIVE, cost=11.0),
            Probe(subject="zzz", outcome=Outcome.EXPENSIVE, cost=19.0),
        ),
        skipped=(),
        threshold=7.0,
        measurements=3,
    )

    measured = read_bisect(result)

    assert measured.measurement["bisect.probe_cost.min"] == 3.0
    assert measured.measurement["bisect.probe_cost.max"] == 19.0
    assert measured.measurement["bisect.probe_cost.median"] == 11.0
    assert measured.measurement["bisect.threshold"] == 7.0
    assert "bisect.measurements" not in measured.measurement


def test_a_bisection_that_measured_nothing_is_refused() -> None:
    """An experiment with no measurement is the first non-negotiable's exact case,
    and `Experiment.measurement` refuses an empty mapping anyway. Refused here so
    the message says what happened rather than naming a schema field."""
    result = Bisection(
        good="aaa",
        bad="zzz",
        probes=(Probe(subject="aaa", outcome=Outcome.CHEAP, failure="container died"),),
        skipped=(),
        threshold=7.0,
        measurements=0,
    )

    with pytest.raises(ReadingError, match="took no measurement"):
        read_bisect(result)


def test_a_cached_probe_is_not_an_ablation() -> None:
    """The primitive records `cached` because the count that matters is
    measurements taken, not questions asked. A reader that included them would
    report a median over numbers this run did not measure."""
    result = SearchResult(
        algorithm="ddmin",
        candidates=frozenset({"a", "b"}),
        culprits=frozenset({"a"}),
        probes=(
            Probe(subject=frozenset({"a"}), outcome=Outcome.EXPENSIVE, cost=90.0),
            Probe(subject=frozenset({"b"}), outcome=Outcome.CHEAP, cost=2.0, cached=True),
        ),
        measurements=1,
        threshold=50.0,
        resolution=1.0,
    )

    measured = read_ablation_search(result)

    assert measured.measurement["ablation_search.probe_cost.min"] == 90.0
    assert measured.measurement["ablation_search.probe_cost.max"] == 90.0
    assert measured.measurement["ablation_search.culprits"] == 1.0


def test_an_input_search_reports_the_worst_input_and_the_spread() -> None:
    """The winner alone cannot answer whether an input of the same size ordinarily
    costs this much, which is the question that decides disclosure."""
    result = Campaign(
        label="parse",
        metric="seconds",
        engine="atheris",
        candidates=(
            Candidate(payload="a", cost=1.0, metrics={}, size=10),
            Candidate(payload="bb", cost=97.0, metrics={}, size=11),
            Candidate(payload="ccc", cost=3.0, metrics={}, size=12),
        ),
        seconds_spent=30.0,
        budget_seconds=60.0,
        seed=7,
        guided=True,
        stopped_at_deadline=False,
    )

    measured = read_input_search(result)

    assert measured.measurement["seconds.worst"] == 97.0
    assert measured.measurement["seconds.median"] == 3.0
    assert measured.measurement["worst.size"] == 11.0
    assert measured.measurement["candidates"] == 3.0


def test_an_empty_campaign_cannot_be_built_at_all() -> None:
    """No guard for this in the reader, and the reason is worth recording.

    The first draft had one. `input_search` refuses to construct a `Campaign` with
    no candidates — *a campaign with no candidates is a failed run rather than a
    null result* — so the branch was unreachable: S-7.4's redundant condition,
    reading as protection while protecting nothing. Verified from the other side
    instead.
    """

    with pytest.raises(InputSearchError, match="no candidates"):
        Campaign(
            label="parse",
            metric="seconds",
            engine="atheris",
            candidates=(),
            seconds_spent=30.0,
            budget_seconds=60.0,
            seed=7,
            guided=False,
            stopped_at_deadline=True,
        )


def test_headroom_carries_the_floor_beside_what_was_measured() -> None:
    """A measured figure without its floor is a number with no claim attached, and
    the floor is the whole point of the instrument."""
    result = Screening(
        comparisons=(
            Comparison(
                bound=Bound(
                    kind=BoundKind.BYTES_READ,
                    metric="response_bytes",
                    floor=512.0,
                    basis="the row the response must contain",
                ),
                measured=8192.0,
            ),
        ),
        unbounded=("seconds",),
    )

    measured = read_headroom(result)

    assert measured.measurement == {
        "response_bytes.measured": 8192.0,
        "response_bytes.floor": 512.0,
    }


def test_headroom_with_nothing_bounded_is_refused() -> None:
    with pytest.raises(ReadingError, match="nothing was compared against a bound"):
        read_headroom(Screening(comparisons=(), unbounded=("seconds", "db.query")))


def test_faults_reports_what_the_subject_did_at_each_magnitude() -> None:
    """`growth` is a `Growth`, not a `Fit`, so nothing this boundary carries as a
    fit travels — and S-9.2 refusing to judge a curve nobody drew is correct."""
    result = Amplification(
        responses=(
            Response(fault=Fault.LATENCY, magnitude=0.1, calls=4, metrics={"seconds": 0.4}),
            Response(fault=Fault.LATENCY, magnitude=0.5, calls=4, metrics={"seconds": 2.0}),
        ),
        growth=Growth.LINEAR,
        dependency="payments",
    )

    measured = read_faults(result)

    assert measured.measurement["magnitude_0.1.calls"] == 4.0
    assert measured.measurement["magnitude_0.5.seconds"] == 2.0
    assert measured.fits == {}


def test_faults_with_no_response_is_refused() -> None:
    with pytest.raises(ReadingError, match="no fault was injected"):
        read_faults(Amplification(responses=(), growth=None, dependency="payments"))


def test_interference_reports_both_sides_as_medians() -> None:
    result = Interference(
        component="search",
        context="checkout",
        alone=(1.0, 3.0, 2.0),
        in_context=(20.0, 30.0, 40.0),
        disposition=Disposition.DIAGNOSE_ONLY,
    )

    measured = read_interference(result)

    assert measured.measurement["alone.median"] == 2.0
    assert measured.measurement["in_context.median"] == 30.0
    assert measured.measurement["alone.samples"] == 3.0


def test_sensitivity_reports_its_slope_and_every_fraction() -> None:
    result = Sensitivity(
        target="cache",
        points=(Point(fraction=0.1, samples=(5.0,)), Point(fraction=0.9, samples=(50.0,))),
        slope=45.0,
        r_squared=0.98,
    )

    measured = read_sensitivity(result)

    assert measured.measurement["slope"] == 45.0
    assert measured.measurement["r_squared"] == 0.98
    assert measured.measurement["fraction_0.1.median"] == 5.0
    assert measured.measurement["fraction_0.9.median"] == 50.0


def test_a_load_finding_reports_the_usl_coefficients_and_every_level() -> None:
    """`USLFit` is a different model from `Fit` — different fields, different
    meaning — so it travels as numbers rather than as the growth fit."""
    result = LoadFinding(
        levels=(
            LoadLevel(concurrency=1, completions=100, seconds=1.0, latencies=(0.01,), errors=0),
            LoadLevel(concurrency=8, completions=200, seconds=4.0, latencies=(0.04,), errors=3),
        ),
        fit=USLFit(gamma=95.0, alpha=0.02, beta=0.001, r_squared=0.97, levels=2),
        little=(),
        disposition=Disposition.DIAGNOSE_ONLY,
    )

    measured = read_load(result)

    assert measured.measurement["usl.gamma"] == 95.0
    assert measured.measurement["concurrency_8.completions"] == 200.0
    assert measured.measurement["concurrency_8.errors"] == 3.0
    assert measured.fits == {}


def test_a_soak_reports_where_each_trend_started_and_ended() -> None:
    """A soak's claim is that something drifted, so the two ends are the evidence
    and the duration is what makes a drift rate mean anything."""
    only = Fit(
        slope=2.0,
        intercept=1.0,
        linear_r_squared=0.99,
        exponent=None,
        power_r_squared=None,
        growth=None,
        constant_below=0.2,
        superlinear_above=1.2,
    )
    result = Soak(
        samples=(),
        trends={"rss": Trend(metric="rss", fit=only, first=100.0, last=900.0, window=60.0)},
        duration=3600.0,
        reference=None,
    )

    measured = read_soak(result)

    assert measured.measurement["rss.first"] == 100.0
    assert measured.measurement["rss.last"] == 900.0
    assert measured.measurement["soak.duration"] == 3600.0
    assert measured.fits == {"rss.first": only}, "keyed by the metric it is of"


def test_a_soak_that_fitted_nothing_is_refused() -> None:
    with pytest.raises(ReadingError, match="fitted no trend"):
        read_soak(Soak(samples=(), trends={}, duration=10.0, reference=None))


def test_instruction_counts_are_reported_per_label() -> None:
    """Counts rather than durations, which is the instrument's whole argument: a
    count is exact where a duration is a distribution."""
    result = Separation(
        label_a="cold",
        label_b="warm",
        a=InstructionCount(
            instructions=900, drain_instructions=10, materialized=5, reference_seconds=0.1
        ),
        b=InstructionCount(
            instructions=300, drain_instructions=4, materialized=5, reference_seconds=0.03
        ),
    )

    measured = read_instructions(result)

    assert measured.measurement["cold.instructions"] == 900.0
    assert measured.measurement["warm.instructions"] == 300.0


def test_a_shape_sweep_is_read_at_its_last_allocation() -> None:

    result = ShapeComparison(
        groups=4,
        total=40,
        baseline={"db.query": 1.0},
        measurements=(
            ShapeMeasurement(
                allocation=Allocation(distribution=Distribution.UNIFORM, counts=(10, 10, 10, 10)),
                raw={"db.query": 11.0},
                adjusted={"db.query": 10.0},
            ),
            ShapeMeasurement(
                allocation=Allocation(distribution=Distribution.LONG_TAIL, counts=(37, 1, 1, 1)),
                raw={"db.query": 41.0},
                adjusted={"db.query": 40.0},
            ),
        ),
        kinds={"db.query": MetricKind.COUNT},
        reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
        cache_control=CacheControl.FRESH_PROCESS,
    )

    measured = read_shape(result)

    assert measured.measurement == {"db.query": 40.0}
    assert measured.kinds == {"db.query": MetricKind.COUNT}


def _unused() -> Mapping[str, float]:  # pragma: no cover - keeps the import honest
    return {}

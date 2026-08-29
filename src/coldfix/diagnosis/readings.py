"""What each primitive measured, read off the object it returned. **S-17.11.**

`Executor` is `Callable[[ExperimentSpec], Measured]`, and the call is uniform:
look the primitive up in the registry, merge the design's arguments with the
bound half the harness owes, run it. **The conversion is not uniform.** Thirteen
primitives return thirteen distinct result types and none of them exposes a
common accessor, so something has to know all thirteen shapes. This module is
that something, and it is the only place in the system that does.

**Here rather than on the registry entry**, because `Measured` is a diagnosis
type and primitives are the layer below it — a reader living beside the
registration would point `primitives/` at `diagnosis/`. What that costs is a
table the registry can outgrow, and `test_readings.py` asserts the table is
**total over `REGISTRY.names`**: a primitive the agent can select and the
executor cannot run is an investigation that dies mid-loop having already spent
its budget on the design.

**Not every primitive measures a mapping of numbers, and an empty one is refused
by schema.** `Experiment.measurement` validates: *an experiment with no
measurement is a conclusion drawn from reading code, which the first
non-negotiable exists to prevent.* Five results already are mappings of numbers.
Four are searches returning a decision — a commit, a set of culprits, an input, a
growth class — and for those the honest numbers are **the probe values the search
took along the way**. *Six probes taken* is a fact about the search; the cost
measured at the bad commit is a fact about the subject, and only the second is a
measurement.

**Nothing here computes.** Every number is read off a field the primitive set.
Where a reader summarises a sequence it uses `stats()`, the same function the
primitives use, so a median in a log is the median the harness computes
everywhere else.

**`kinds` and `fit` are carried where the primitive produced them and absent
where it did not** — never inferred from a metric's name, which is S-8.12's
recorded failure: `metric_kind` defaults to `COUNT`, the thesis ablation reports
`seconds.share_removed` — a share of a duration — and a reproducibility check
reading kinds off spelling would mark every re-run divergent and every finding
unsound for ever.

**Every fit travels, keyed by its metric.** S-17.11 could carry only one, because
`Measured.fit` was singular and a volume sweep fits *every* metric it measured —
so a fit travelled only for the single-metric case, which no real sweep is, and
`audit/scales.py`'s check never ran. S-17.12 made it a mapping: the executor
carries all of them and the audit picks the one the finding's claim rests on,
which is a choice only the interpretation can make and it runs after this.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from coldfix.bench.stats import stats
from coldfix.diagnosis.loop import Measured
from coldfix.primitives.ablation import AblationResult, share_metric
from coldfix.primitives.bounds import Screening
from coldfix.primitives.faults import Amplification
from coldfix.primitives.input_search import Campaign
from coldfix.primitives.instructions import Separation
from coldfix.primitives.isolation import Interference
from coldfix.primitives.load import LoadFinding
from coldfix.primitives.longitudinal import Soak
from coldfix.primitives.perturbation import Sensitivity
from coldfix.primitives.scaling import ScalingResult, ShapeComparison
from coldfix.primitives.search import SearchResult
from coldfix.primitives.temporal import Bisection


class ReadingError(Exception):
    """A primitive's result could not be read as a measurement."""


def _median(values: Sequence[float]) -> float:
    """The middle value, however many there are.

    `stats()` refuses fewer than two samples, which is right for a summary that
    also reports dispersion and wrong here: a bisection that took one probe, or a
    sensitivity point sampled once, measured something real. Reporting the single
    value is not a weaker summary — it is the only one there is — and refusing it
    would discard a measurement the harness actually took.
    """
    return values[0] if len(values) == 1 else stats(values).median


def _probed(costs: Sequence[float], label: str) -> dict[str, float]:
    """What a search actually measured, summarised the way the bench summarises.

    A search's own bookkeeping — probes taken, threshold, resolution — describes
    the search. These are the costs it measured on the subject, which is what
    makes the record an experiment rather than a note about an algorithm.
    """
    if not costs:
        message = (
            f"{label} took no measurement anybody can record: every probe failed or was served "
            "from its cache. An experiment with no measurement is the first non-negotiable's "
            "exact case, so this is refused rather than logged with the search's own counters "
            "standing in for numbers about the subject"
        )
        raise ReadingError(message)
    return {
        f"{label}.probe_cost.median": _median(costs),
        f"{label}.probe_cost.min": min(costs),
        f"{label}.probe_cost.max": max(costs),
    }


# ============================================================ the five that measure


def read_volume(result: ScalingResult) -> Measured:
    """The largest scale point, with the fits and kinds the sweep produced.

    The largest point rather than the baseline: a growth claim is about what
    happens as volume rises, and the number a finding quotes is the one at the
    top of the sweep. `adjusted` rather than `raw`, because that is the series
    the fit was taken over.
    """
    top = result.points[-1]
    # **Every metric's fit, which is the whole of S-17.12.** The sweep fitted all
    # of them and the interpretation has not yet chosen which the finding rests
    # on, so carrying one would be this module guessing at that choice — and
    # carrying none, which is what it did, left the scale audit unable to run on
    # any real sweep.
    return Measured(
        measurement=dict(top.adjusted), kinds=dict(result.kinds), fits=dict(result.fits)
    )


def read_shape(result: ShapeComparison) -> Measured:
    """The last allocation measured. A shape sweep fits nothing, so no fit."""
    last = result.measurements[-1]
    return Measured(measurement=dict(last.adjusted), kinds=dict(result.kinds))


def read_ablation(result: AblationResult) -> Measured:
    """Baseline, ablated, and the share removed — the three a finding quotes.

    `share_metric` names the third, so the primitive that computes it and the
    assembler that looks for it cannot disagree about the spelling. Both raw sides
    are kept because a share alone cannot answer *of how much*.
    """
    measurement: dict[str, float] = {}
    for metric, value in result.baseline.items():
        measurement[f"{metric}.baseline"] = float(value)
    for metric, value in result.ablated.items():
        measurement[f"{metric}.ablated"] = float(value)
    for metric in result.baseline:
        measurement[share_metric(metric)] = result.share(metric)
    measurement["calls.baseline"] = float(result.calls_baseline)
    measurement["calls.ablated"] = float(result.calls_ablated)
    # `kinds` describes the primitive's own metric names, and every name above is
    # a derived one. Reporting the originals would attach a kind to a key nobody
    # measured, which `Measured.__post_init__` refuses outright.
    return Measured(measurement=measurement)


def read_interference(result: Interference) -> Measured:
    """One component measured alone and in context. The pair is the experiment."""
    return Measured(
        measurement={
            "alone.median": _median(list(result.alone)),
            "in_context.median": _median(list(result.in_context)),
            "alone.samples": float(len(result.alone)),
            "in_context.samples": float(len(result.in_context)),
        }
    )


def read_sensitivity(result: Sensitivity) -> Measured:
    """Slope and fit quality, plus what was measured at each fraction."""
    measurement = {"slope": result.slope, "r_squared": result.r_squared}
    for point in result.points:
        measurement[f"fraction_{point.fraction:g}.median"] = _median(list(point.samples))
    return Measured(measurement=measurement)


# ================================================= the four searches, and their probes


def read_bisect(result: Bisection) -> Measured:
    """A commit is the answer and the probe costs are the measurement.

    `measurements` and `threshold` describe the search. What was measured about
    the subject is what each probed revision cost, and the threshold is carried
    beside them because a cost means nothing without the line it was compared to.
    """
    costs = [probe.cost for probe in result.probes if probe.cost is not None]
    return Measured(measurement={**_probed(costs, "bisect"), "bisect.threshold": result.threshold})


def read_ablation_search(result: SearchResult) -> Measured:
    """A set of culprits is the answer; the costs of the sets it tried are the
    measurement. Cached probes are excluded — a cache hit is not an ablation."""
    costs = [probe.cost for probe in result.probes if probe.cost is not None and not probe.cached]
    return Measured(
        measurement={
            **_probed(costs, "ablation_search"),
            "ablation_search.threshold": result.threshold,
            "ablation_search.culprits": float(len(result.culprits)),
        }
    )


def read_input_search(result: Campaign[object]) -> Measured:
    """The worst input found, and what it cost.

    Every candidate is kept by the primitive because the winner alone cannot
    answer whether an input of the same size ordinarily costs this much. The
    dearest one is what the finding is about; the spread is what makes it a claim.
    """
    # No empty-candidates guard: `input_search` refuses to build a `Campaign`
    # with none, so a branch for it here would be unreachable — S-7.4's redundant
    # condition, which reads as protection while protecting nothing.
    costs = [candidate.cost for candidate in result.candidates]
    worst = max(result.candidates, key=lambda candidate: candidate.cost)
    measurement = {
        f"{result.metric}.worst": worst.cost,
        f"{result.metric}.median": _median(costs),
        "candidates": float(len(result.candidates)),
    }
    if worst.size is not None:
        measurement["worst.size"] = float(worst.size)
    return Measured(measurement=measurement)


def read_faults(result: Amplification) -> Measured:
    """What the subject did at each injected magnitude.

    `growth` is a `Growth`, not a `Fit` — the primitive classifies without fitting
    a curve this module could carry — so no fit travels, and S-9.2 refuses to
    judge a rejection nobody drew a curve for, which is correct here.
    """
    if not result.responses:
        message = (
            f"no fault was injected into {result.dependency}, so nothing was measured. An "
            "amplification with no responses is a design that never reached the subject"
        )
        raise ReadingError(message)
    measurement: dict[str, float] = {}
    for response in result.responses:
        measurement[f"magnitude_{response.magnitude:g}.calls"] = float(response.calls)
        for metric, value in response.metrics.items():
            measurement[f"magnitude_{response.magnitude:g}.{metric}"] = float(value)
    return Measured(measurement=measurement)


# ================================================================= the remaining four


def read_headroom(result: Screening) -> Measured:
    """Each computable floor and what was measured against it.

    Both halves, always. A measured figure without its floor is a number with no
    claim attached, and the floor is the whole point of the instrument.
    """
    if not result.comparisons:
        message = (
            "nothing was compared against a bound. `bounds.headroom` with no comparison has "
            f"computed no floor — {len(result.unbounded)} metric(s) were unbounded — and a "
            "record of it would be an experiment reporting the absence of one"
        )
        raise ReadingError(message)
    measurement: dict[str, float] = {}
    for comparison in result.comparisons:
        metric = comparison.bound.metric
        measurement[f"{metric}.measured"] = comparison.measured
        measurement[f"{metric}.floor"] = comparison.bound.floor
    return Measured(measurement=measurement)


def read_load(result: LoadFinding) -> Measured:
    """The USL coefficients and what each concurrency level did.

    `USLFit` is not a `Fit` — different model, different fields — so it travels as
    numbers in the measurement rather than as the growth fit this boundary carries.
    """
    measurement = {
        "usl.gamma": result.fit.gamma,
        "usl.alpha": result.fit.alpha,
        "usl.beta": result.fit.beta,
        "usl.r_squared": result.fit.r_squared,
    }
    for level in result.levels:
        measurement[f"concurrency_{level.concurrency}.completions"] = float(level.completions)
        measurement[f"concurrency_{level.concurrency}.seconds"] = level.seconds
        measurement[f"concurrency_{level.concurrency}.errors"] = float(level.errors)
    return Measured(measurement=measurement)


def read_soak(result: Soak) -> Measured:
    """Where each trend started and ended, over how long.

    A soak's claim is that something drifted, so the first and last readings are
    the evidence and the duration is what makes a drift rate meaningful.
    """
    if not result.trends:
        message = (
            f"the soak ran for {result.duration:g}s over {len(result.samples)} sample(s) and "
            "fitted no trend, so it measured nothing a later experiment can be compared against"
        )
        raise ReadingError(message)
    measurement = {"soak.duration": result.duration, "soak.samples": float(len(result.samples))}
    for metric, trend in result.trends.items():
        measurement[f"{metric}.first"] = trend.first
        measurement[f"{metric}.last"] = trend.last
    # Keyed by the metric the trend is of, and every trend has one — a soak that
    # fitted three metrics carries three curves, and the audit picks by name.
    fits = {f"{metric}.first": trend.fit for metric, trend in result.trends.items()}
    return Measured(measurement=measurement, fits=fits)


def read_instructions(result: Separation) -> Measured:
    """Two labelled counts, and what decided between them.

    Instruction counts rather than durations, which is the instrument's whole
    argument: a count is exact where a duration is a distribution.
    """
    return Measured(
        measurement={
            f"{result.label_a}.instructions": float(result.a.instructions),
            f"{result.label_b}.instructions": float(result.b.instructions),
            f"{result.label_a}.materialized": float(result.a.materialized),
            f"{result.label_b}.materialized": float(result.b.materialized),
        }
    )


READERS: Mapping[str, Callable[[Any], Measured]] = {
    "ablation.search": read_ablation_search,
    "ablation.stub": read_ablation,
    "bounds.headroom": read_headroom,
    "faults.injection": read_faults,
    "inputs.search": read_input_search,
    "isolation.interference": read_interference,
    "load.usl": read_load,
    "longitudinal.soak": read_soak,
    "observation.instructions": read_instructions,
    "perturbation.sensitivity": read_sensitivity,
    "scaling.shape": read_shape,
    "scaling.volume": read_volume,
    "temporal.bisect": read_bisect,
}
"""One reader per registered primitive. **Asserted total, not merely listed.**

`Any` in the value type is deliberate and is the honest annotation: thirteen
distinct result types share no supertype, and a union of all thirteen would make
every reader's parameter a union it immediately narrows. The type is checked by
the test that calls each reader with its primitive's real result.
"""

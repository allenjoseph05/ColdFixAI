"""Contention and coherency are different numbers and different next questions.

S-3.12. The story's note is the whole design brief: *the fitted coefficients are
diagnostic, not just descriptive — high α points at a shared resource, high β at
coordination cost. Surface them to the agent, not just the curve.*

So the tests are built around telling the two apart. A workload that queues on a
lock and a workload that does not are driven through the same curve, and the
coefficients have to separate them — a fit that reported "scales badly" for both
would be a chart, which is what the note is against.

Two things get more attention than the fit itself, because both produce a
confident number from nothing:

**A negative coefficient.** There is no negative contention. The naive
implementation reports it anyway and derives a peak from it, and the peak is a
number about nothing.

**A load generator that never drove the concurrency it claimed.** Little's Law is
the only check here that notices, because the USL fit of a curve that was never
concurrent is smooth, plausible and meaningless.

The synthetic levels are constructed from the model rather than driven, wherever
the test is about the arithmetic: a real thread pool measures the machine's
scheduler as much as the code, and the arithmetic is what has to be right.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import pytest

from coldfix.primitives.load import (
    LITTLE_TOLERANCE,
    Coefficient,
    LoadError,
    LoadFinding,
    LoadLevel,
    USLFit,
    check_little,
    drive_load,
    fit_usl,
    measure_load,
)
from coldfix.primitives.registry import REGISTRY, Capability
from coldfix.sandbox.scope import (
    DiagnoseOnlyError,
    DiagnoseOnlyReason,
    Disposition,
    RepairableFinding,
    classify,
)

REPO = Path("/srv/subject")


def usl(concurrency: int, *, gamma: float, alpha: float, beta: float) -> float:
    """The model, used to build curves whose coefficients are known exactly."""
    n = float(concurrency)
    return gamma * n / (1 + alpha * (n - 1) + beta * n * (n - 1))


def level_from_model(
    concurrency: int, *, gamma: float, alpha: float, beta: float, seconds: float = 1000.0
) -> LoadLevel:
    """A level whose throughput is what the model says, and whose latency agrees.

    Latency is set to `N / X`, which is Little's Law rearranged — so a curve
    built this way is self-consistent by construction and the consistency check
    has something to be right about.

    The window is long so that rounding completions to a whole number is
    negligible. At a one-second window it is not: that rounding alone was enough
    to fit β = 6.5e-5 to a curve generated with β = 0, which is how the
    extrapolation limit on `nmax` came to be written.
    """
    throughput = usl(concurrency, gamma=gamma, alpha=alpha, beta=beta)
    completions = round(throughput * seconds)
    latency = concurrency / throughput
    return LoadLevel(
        concurrency=concurrency,
        completions=completions,
        seconds=seconds,
        latencies=(latency,) * max(completions, 1),
    )


def curve(
    *, gamma: float, alpha: float, beta: float, levels: tuple[int, ...] = (1, 2, 4, 8, 16)
) -> tuple[LoadLevel, ...]:
    return tuple(
        level_from_model(concurrency, gamma=gamma, alpha=alpha, beta=beta) for concurrency in levels
    )


# ---------------------------------------------------- AC 2: the three coefficients


def test_the_fit_recovers_the_coefficients_it_was_built_from() -> None:
    """AC 2. The arithmetic first: given a curve the model generated, the fit
    has to return the numbers that generated it."""
    levels = curve(gamma=100.0, alpha=0.05, beta=0.01)

    fit = fit_usl(levels)

    assert fit.gamma == pytest.approx(100.0)
    assert fit.alpha == pytest.approx(0.05, abs=1e-6)
    assert fit.beta == pytest.approx(0.01, abs=1e-6)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-6)


def test_a_contended_system_and_a_coherent_one_are_told_apart() -> None:
    """The story's note. A chart says both scale badly; the coefficients say one
    is queueing on a shared resource and the other is paying to stay consistent,
    and those are different next questions."""
    contended = fit_usl(curve(gamma=100.0, alpha=0.30, beta=0.0))
    coherent = fit_usl(curve(gamma=100.0, alpha=0.0, beta=0.02))

    contended_limit, coherent_limit = contended.dominant(), coherent.dominant()

    assert contended_limit is Coefficient.CONTENTION
    assert coherent_limit is Coefficient.COHERENCY
    assert "One-Lane Bridge" in contended_limit.value
    assert "adding capacity makes the system slower" in coherent_limit.value


def test_the_peak_is_where_the_model_says_it_is() -> None:
    """`Nmax = sqrt((1-α)/β)` — the number a capacity decision rests on."""
    fit = fit_usl(curve(gamma=100.0, alpha=0.02, beta=0.005))

    assert fit.nmax == pytest.approx(((1 - 0.02) / 0.005) ** 0.5, rel=1e-3)


def test_an_amdahl_shaped_system_has_a_ceiling_and_no_peak() -> None:
    """β = 0 means throughput approaches `γ/α` and never turns back down. Naming
    an enormous peak would be worse than saying there is none."""
    fit = fit_usl(curve(gamma=100.0, alpha=0.10, beta=0.0))

    assert fit.nmax is None
    assert fit.ceiling == pytest.approx(1000.0, rel=1e-3)


def test_a_peak_beyond_the_measured_range_is_withheld_as_an_extrapolation() -> None:
    """β here is real — a fiftieth of a percent, comfortably above the noise —
    and the model puts the peak at about 22. The curve was only driven to 8.

    Reporting 22 would be reporting the model, not the system: nothing was
    measured anywhere near there, and the whole value of this primitive is that
    its numbers came from running the thing.
    """
    fit = fit_usl(curve(gamma=100.0, alpha=0.001, beta=0.002, levels=(1, 2, 4, 8)))

    assert fit.fits
    assert fit.beta == pytest.approx(0.002, rel=1e-3)
    assert math.sqrt((1 - fit.alpha) / fit.beta) > 2 * 8
    assert fit.nmax is None


def test_a_peak_inside_the_measured_range_is_reported() -> None:
    """The control for the rule above. A limit that withheld every peak would
    be a primitive that never answers the question it exists for."""
    fit = fit_usl(curve(gamma=100.0, alpha=0.02, beta=0.005, levels=(1, 2, 4, 8, 16)))

    assert fit.nmax is not None
    assert fit.nmax <= 2 * 16


def test_a_perfectly_scaling_system_has_neither() -> None:
    """The control. A fit that reported contention for a system with none would
    send an investigation after a lock that does not exist."""
    fit = fit_usl(curve(gamma=100.0, alpha=0.0, beta=0.0))

    assert fit.alpha == pytest.approx(0.0, abs=1e-9)
    assert fit.beta == pytest.approx(0.0, abs=1e-9)
    assert fit.dominant() is None
    assert fit.ceiling is None


# ------------------------------------- a negative coefficient is not a quantity


def test_a_negative_coefficient_is_reported_and_the_fit_is_marked_unfitted() -> None:
    """There is no negative contention. Reporting it as measured is honest;
    deriving a peak from it is a number about nothing."""
    superlinear = (
        LoadLevel(concurrency=1, completions=100, seconds=1.0, latencies=(0.01,)),
        LoadLevel(concurrency=2, completions=260, seconds=1.0, latencies=(0.0077,)),
        LoadLevel(concurrency=4, completions=700, seconds=1.0, latencies=(0.0057,)),
        LoadLevel(concurrency=8, completions=2000, seconds=1.0, latencies=(0.004,)),
    )

    fit = fit_usl(superlinear)

    assert fit.alpha < 0
    assert not fit.fits
    assert fit.nmax is None
    assert fit.ceiling is None
    assert fit.dominant() is None


def test_the_explanation_says_the_curve_is_not_usl_shaped() -> None:
    finding = LoadFinding(
        levels=(),
        fit=USLFit(gamma=100.0, alpha=-0.2, beta=0.01, r_squared=0.9, levels=8),
        little=(),
    )

    assert "not a physical quantity" in finding.explanation()
    assert "no ceiling and no peak are reported" in finding.explanation()


# ----------------------------------------- AC 3: Little's Law as a validity check


def test_a_self_consistent_curve_passes_the_law() -> None:
    """AC 3. `N = X × R` holds by construction for a curve built from the model,
    so the check has something to be right about before it is asked to be wrong."""
    checks = check_little(curve(gamma=100.0, alpha=0.05, beta=0.01))

    assert all(check.consistent for check in checks)


def test_a_generator_that_never_sustained_its_concurrency_is_caught() -> None:
    """The failure nothing else here sees. This curve fits the USL smoothly —
    but at N=16 the latencies say only about four requests were ever in flight,
    so the level measured something other than what it claims."""
    honest = list(curve(gamma=100.0, alpha=0.05, beta=0.01))
    broken = [
        *honest[:-1],
        LoadLevel(
            concurrency=16,
            completions=honest[-1].completions,
            seconds=honest[-1].seconds,
            latencies=(honest[-1].mean_latency / 4,) * len(honest[-1].latencies),
        ),
    ]

    checks = check_little(broken)
    failed = [check for check in checks if not check.consistent]

    assert [check.concurrency for check in failed] == [16]
    assert failed[0].predicted == pytest.approx(4.0, rel=0.2)


def test_the_finding_says_to_fix_the_generator_before_reading_the_coefficients() -> None:
    """A USL fit of a curve that was never concurrent is smooth, plausible and
    meaningless, so the order of operations matters."""
    levels = list(curve(gamma=100.0, alpha=0.05, beta=0.01))
    levels[-1] = LoadLevel(
        concurrency=16, completions=levels[-1].completions, seconds=1.0, latencies=(0.001,)
    )

    finding = LoadFinding(
        levels=tuple(levels), fit=fit_usl(tuple(levels)), little=check_little(levels)
    )

    assert not finding.self_consistent
    assert "failed Little's Law" in finding.explanation()
    assert "before reading the coefficients" in finding.explanation()


def test_the_tolerance_allows_for_the_ramp_at_the_edges_of_a_level() -> None:
    """Latency and throughput are measured over the same window but not the same
    instant — a level's first requests start before its last ones do — so an
    exact equality would fail every real measurement."""
    level = LoadLevel(concurrency=8, completions=80, seconds=1.0, latencies=(0.09,) * 80)

    check = check_little([level])[0]

    assert check.error < LITTLE_TOLERANCE
    assert check.consistent


# ------------------------------------- AC 4: diagnose-only, and structurally so


def test_the_finding_is_diagnose_only() -> None:
    """AC 4. Not a field a caller sets — it is what this primitive produces."""
    finding = LoadFinding(
        levels=(), fit=USLFit(gamma=1.0, alpha=0.1, beta=0.01, r_squared=1.0, levels=8), little=()
    )

    assert finding.disposition is Disposition.DIAGNOSE_ONLY
    assert "diagnosed and never patched" in finding.explanation()


def test_the_mechanism_this_emits_is_refused_by_the_scope_check() -> None:
    """The structural half, and the reason the mechanism sentence is worded the
    way it is: S-2.9 refuses it independently, so this module does not have to be
    trusted to remember what it produced."""
    finding = LoadFinding(
        levels=(),
        fit=USLFit(gamma=100.0, alpha=0.3, beta=0.0, r_squared=1.0, levels=16),
        little=(),
    )

    verdict = classify(finding.mechanism, "app/views.py", repository=REPO)

    assert verdict.disposition is Disposition.DIAGNOSE_ONLY
    assert DiagnoseOnlyReason.CONCURRENCY in verdict.reasons


def test_a_load_finding_cannot_be_offered_to_the_repair_path() -> None:
    """`00-BRIEF.md` §3: output equivalence cannot detect an introduced race, so
    no falsification test this system writes makes a contention fix safe."""
    finding = LoadFinding(
        levels=(),
        fit=USLFit(gamma=100.0, alpha=0.3, beta=0.0, r_squared=1.0, levels=16),
        little=(),
    )

    with pytest.raises(DiagnoseOnlyError):
        RepairableFinding(mechanism=finding.mechanism, site="app/views.py", repository=REPO)


# ------------------------------------------- AC 1: driving the load for real


def test_load_is_driven_at_the_concurrency_asked_for() -> None:
    """AC 1, against a real thread pool. The workload sleeps, which releases the
    GIL — which is what a subject waiting on a database does."""
    peak = 0
    live = 0
    lock = threading.Lock()

    def workload() -> None:
        nonlocal peak, live
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.01)
        with lock:
            live -= 1

    level = drive_load(workload, 4, requests=16)

    assert peak > 1
    assert level.completions == 16
    assert level.throughput > 0
    assert level.mean_latency >= 0.01


def test_a_contended_workload_measures_worse_than_an_uncontended_one() -> None:
    """The end-to-end shape: a shared lock serializes the work, so throughput
    stops improving with concurrency while the unshared version keeps up."""
    shared = threading.Lock()

    def contended() -> None:
        with shared:
            time.sleep(0.005)

    def free() -> None:
        time.sleep(0.005)

    contended_level = drive_load(contended, 8, requests=32)
    free_level = drive_load(free, 8, requests=32)

    assert contended_level.throughput < free_level.throughput


def test_more_workers_than_requests_is_refused() -> None:
    """The level would measure a concurrency that never existed, which is the
    same lie Little's Law exists to catch — refused earlier and more cheaply."""
    with pytest.raises(LoadError, match="never existed"):
        drive_load(lambda: None, 8, requests=4)


def test_a_workload_that_fails_under_load_is_counted_rather_than_lost() -> None:
    """Errors appearing only at concurrency is a finding in itself — it is what
    pool exhaustion looks like from the outside."""
    calls = 0
    lock = threading.Lock()

    def flaky() -> None:
        nonlocal calls
        with lock:
            calls += 1
            mine = calls
        if mine % 2 == 0:
            message = "connection pool exhausted"
            raise RuntimeError(message)

    level = drive_load(flaky, 2, requests=10)

    assert level.completions == 10
    assert level.errors == 5


def test_the_whole_curve_runs_end_to_end() -> None:
    """AC 1 and AC 2 together, on a real pool, with the data size held fixed by
    construction: the same workload is called at every level."""
    finding = measure_load(lambda: time.sleep(0.002), [1, 2, 4, 8], requests_per_level=16)

    assert [level.concurrency for level in finding.levels] == [1, 2, 4, 8]
    assert finding.disposition is Disposition.DIAGNOSE_ONLY
    assert len(finding.little) == 4


# ------------------------------------------------------- what the fit refuses


def test_a_curve_without_a_single_user_level_is_refused() -> None:
    """γ is defined as throughput at N=1. Without it the other two coefficients
    are being fitted against an unknown scale."""
    levels = curve(gamma=100.0, alpha=0.05, beta=0.01, levels=(2, 4, 8, 16))

    with pytest.raises(LoadError, match="no single-user level"):
        fit_usl(levels)


def test_too_few_levels_to_fit_a_line_is_refused() -> None:
    """Two points define a line through themselves. Three is a fit."""
    levels = curve(gamma=100.0, alpha=0.05, beta=0.01, levels=(1, 2, 4))

    with pytest.raises(LoadError, match="at least 4 distinct levels"):
        fit_usl(levels)


def test_a_level_that_completed_nothing_is_refused() -> None:
    levels = (
        *curve(gamma=100.0, alpha=0.05, beta=0.01, levels=(1, 2, 4)),
        LoadLevel(concurrency=8, completions=0, seconds=1.0, latencies=()),
    )

    with pytest.raises(LoadError, match="completed nothing"):
        fit_usl(levels)


def test_the_primitive_is_registered() -> None:
    primitive = REGISTRY.get("load.usl")

    assert primitive.required_capabilities == {
        Capability.LOAD_GENERATION,
        Capability.STATE_RESET,
    }
    assert primitive.run is measure_load

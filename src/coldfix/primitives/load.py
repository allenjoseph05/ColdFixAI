"""What happens when many things arrive at once, and why — never how to fix it.

Epic 3, S-3.12. `01-primitives.md` §3: hold the data size fixed and raise
concurrency instead. This is **orthogonal to scaling**, and the sentence worth
keeping is that a system can be flawless at a million rows single-user and
collapse at fifty concurrent users with ten.

**The coefficients are the finding, not the curve.** The Universal Scalability
Law fits throughput with three of them, and the story's note insists they reach
the agent rather than a picture:

    X(N) = γN / (1 + α(N-1) + βN(N-1))

- **γ** is the single-user throughput — ideal linear scaling.
- **α** is *contention*: queueing for a shared resource. It creates a horizontal
  asymptote, so throughput flattens and stops improving. High α points at the
  One-Lane Bridge — a lock, a pool, a single writer.
- **β** is *coherency*: the cost of keeping data consistent between workers. It
  makes throughput **decrease** past a peak, which is the shape that surprises
  people, because adding capacity makes the system slower.

With β = 0 this reduces to Amdahl's Law. With β > 0 there is a peak, and
`Nmax = sqrt((1-α)/β)` is where it sits.

**The fit is ordinary least squares, because the model linearizes.** Dividing
through and rearranging gives `(γN/X(N) - 1)/(N-1) = α + βN`, which is a straight
line in N whose intercept is α and whose slope is β. ADR 015 keeps the statistics
in the standard library and this needs nothing more.

**A negative coefficient is the model telling you it does not fit.** There is no
such thing as negative contention. A naive implementation reports it anyway and
computes a confident `Nmax` from it, which is a number with no referent. Here the
fit is returned as measured, flagged as not fitting, and `Nmax` is withheld with
the reason.

**Little's Law is the check that the measurements are of what they claim.** In a
closed system `N = X × R` — concurrency equals throughput times residence time —
so any two of them predict the third. A load generator that never actually
sustained N in flight still produces a beautiful USL fit, of nothing. Comparing
the observed concurrency against `X × R` is what catches that, and it costs one
multiplication.

**Every finding here is diagnose-only, and that is structural.** `00-BRIEF.md`
§3 refuses concurrency and locking fixes outright: output equivalence cannot
detect an introduced race, so no falsification test this system can write makes a
contention patch safe. S-2.9 already enforces it — a finding whose mechanism
mentions contention has no route to repair — and the mechanism sentence this
module emits is written to be caught by it.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from coldfix.primitives.measurement import MeasurementError
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.sandbox.scope import Disposition

# `γ` comes from the single-user level, and the line `α + βN` needs three points
# to be a fit rather than a construction — so four levels, of which one is N=1.
MINIMUM_LEVELS = 4
SINGLE_USER = 1

# How far `X × R` may sit from the concurrency actually driven before the
# measurements are called inconsistent. Generous, because latency and throughput
# are measured over the same window but not over the same instant: a level's
# first requests start before its last ones do.
LITTLE_TOLERANCE = 0.25

# How far past the largest concurrency measured a peak may sit before it is an
# extrapolation rather than a finding. Twice is generous — it allows the ordinary
# case of measuring to 16 and peaking at 24 — and it stops a near-zero β from
# manufacturing a peak in the hundreds out of a curve that never turned down.
EXTRAPOLATION_LIMIT = 2.0

# Least squares on real numbers never returns an exact zero: a curve generated
# with β = 0 fits β = -8.6e-08 here, and one generated with α = 0 fits
# α = -1.2e-06. Neither is negative contention — they are zero with arithmetic
# dust on them. So a coefficient counts as absent inside this band and as *not
# physical* only outside it, which is the shape of rule S-3.8 needed for its
# envelope: a sign test without a tolerance decides on noise.
#
# A thousandth is chosen against what a load measurement can resolve rather than
# against the arithmetic. α = 0.001 says one part in a thousand of the work is
# serialized; no timing-based load test separates that from zero, so a fit
# claiming it is reporting its own residual.
COEFFICIENT_FLOOR = 1e-3


class LoadError(MeasurementError):
    """A load curve could not be driven, or could not be fitted."""


class Coefficient(StrEnum):
    """What a large value of each coefficient points at.

    The story's note in one type: *surface them to the agent, not just the
    curve*. An agent given α = 0.42 and this sentence can choose its next
    instrument; one given a chart cannot.
    """

    CONTENTION = (
        "contention (α): work queueing for a shared resource. Look for the One-Lane Bridge — "
        "a lock, a connection pool, a single writer — with isolation (S-3.13) to find which"
    )
    COHERENCY = (
        "coherency (β): the cost of keeping workers consistent with each other. Throughput "
        "falls past a peak, so adding capacity makes the system slower"
    )


@dataclass(frozen=True)
class LoadLevel:
    """One concurrency level, measured."""

    concurrency: int
    completions: int
    seconds: float
    latencies: tuple[float, ...] = field(repr=False, default=())
    errors: int = 0

    @property
    def throughput(self) -> float:
        """Completions per second. `X` in the law."""
        return self.completions / self.seconds if self.seconds > 0 else 0.0

    @property
    def mean_latency(self) -> float:
        """Mean residence time. `R` in the law."""
        return statistics.fmean(self.latencies) if self.latencies else 0.0


@dataclass(frozen=True)
class LittleCheck:
    """Whether one level's numbers are consistent with each other."""

    concurrency: int
    predicted: float
    """`X × R`, which in a closed system is the concurrency actually in flight."""

    tolerance: float

    @property
    def error(self) -> float:
        return abs(self.predicted - self.concurrency) / self.concurrency

    @property
    def consistent(self) -> bool:
        return self.error <= self.tolerance


@dataclass(frozen=True)
class USLFit:
    """The three coefficients, and whether they mean anything.

    `alpha` and `beta` are reported as measured even when they are negative,
    because a negative coefficient is the model's way of saying it does not
    describe this system — and replacing it with zero would hide that behind a
    curve that looks fitted.
    """

    gamma: float
    alpha: float
    beta: float
    r_squared: float
    levels: int

    @property
    def fits(self) -> bool:
        """Whether the coefficients are physically meaningful.

        There is no negative contention and no negative coherency. Either one,
        by more than the floating-point dust least squares always leaves, means
        the throughput curve is not USL-shaped and every number derived from the
        fit is a number about nothing.
        """
        return self.alpha >= -COEFFICIENT_FLOOR and self.beta >= -COEFFICIENT_FLOOR

    @property
    def nmax(self) -> float | None:
        """The concurrency at which throughput peaks, where there is one to name.

        `None` when β is zero, when the fit is not meaningful, or **when the peak
        sits far beyond the concurrency actually driven** — which is the case
        that matters and is easy to get wrong.

        β is never exactly zero in a real measurement. An Amdahl-shaped system
        measured with any noise at all fits some tiny positive β, and
        `sqrt((1-α)/β)` turns a tiny β into an enormous peak: measured here, a
        curve generated with β = 0 and rounded to whole completions produced
        β = 6.5e-5 and a confident peak at N=118, from data that stopped at 16.
        A peak that far outside the measured range is an extrapolation of the
        model rather than a property of the system, and this system does not
        report those.
        """
        if not self.fits or self.beta <= COEFFICIENT_FLOOR or self.alpha >= 1:
            return None
        peak = math.sqrt((1 - self.alpha) / self.beta)
        return peak if peak <= EXTRAPOLATION_LIMIT * self.levels else None

    @property
    def ceiling(self) -> float | None:
        """The throughput asymptote where contention alone limits the system."""
        if not self.fits or self.alpha <= COEFFICIENT_FLOOR:
            return None
        return self.gamma / self.alpha

    def dominant(self) -> Coefficient | None:
        """Which coefficient is carrying the loss, if either is.

        Compared at the largest level measured, because α costs `α(N-1)` and β
        costs `βN(N-1)` — so which one dominates depends on where the system is
        being asked to run, not on which number is larger in the abstract.
        """
        if not self.fits:
            return None
        at = self.levels
        contention, coherency = self.alpha * (at - 1), self.beta * at * (at - 1)
        if contention <= COEFFICIENT_FLOOR and coherency <= COEFFICIENT_FLOOR:
            return None
        return Coefficient.CONTENTION if contention >= coherency else Coefficient.COHERENCY


@dataclass(frozen=True)
class LoadFinding:
    """What the load curve showed. **Diagnose-only, always.**"""

    levels: tuple[LoadLevel, ...]
    fit: USLFit
    little: tuple[LittleCheck, ...]

    disposition: Disposition = Disposition.DIAGNOSE_ONLY
    """Never anything else.

    `00-BRIEF.md` §3 refuses concurrency and locking fixes because output
    equivalence cannot detect an introduced race. This is not a field a caller
    sets — it is what this primitive produces, and S-2.9 refuses the mechanism
    below independently of it.
    """

    @property
    def mechanism(self) -> str:
        """The sentence a finding would carry, written to be refused by S-2.9.

        Not a formality. S-2.9's `RepairableFinding` runs the classification in
        its constructor, so a mechanism naming contention has no route to repair
        — and this module does not have to be trusted to remember that.
        """
        dominant = self.fit.dominant()
        if dominant is None:
            return (
                "throughput under concurrent load did not fit the scalability model, so "
                "contention and coherency could not be separated"
            )
        return f"under concurrent load this system is limited by {dominant.value}"

    @property
    def self_consistent(self) -> bool:
        return all(check.consistent for check in self.little)

    def explanation(self) -> str:
        lines = [
            f"γ={self.fit.gamma:.3g} α={self.fit.alpha:.3g} β={self.fit.beta:.3g} "
            f"(r²={self.fit.r_squared:.3f} over {self.fit.levels} levels)."
        ]
        if not self.fit.fits:
            lines.append(
                "At least one coefficient is negative, which is not a physical quantity: the "
                "throughput curve is not USL-shaped, so no ceiling and no peak are reported "
                "from it. Measure more levels, or look for a load generator that is not "
                "driving what it claims."
            )
        else:
            ceiling = f"{self.fit.ceiling:.3g}" if self.fit.ceiling else "unbounded"
            peak = f"{self.fit.nmax:.1f}" if self.fit.nmax else "no peak (β=0, Amdahl-shaped)"
            lines.append(f"Throughput ceiling {ceiling}/s; peak concurrency {peak}.")
            dominant = self.fit.dominant()
            if dominant is not None:
                lines.append(f"Dominant limit: {dominant.value}.")

        if not self.self_consistent:
            failed = [check for check in self.little if not check.consistent]
            lines.append(
                f"**{len(failed)} level(s) failed Little's Law**: concurrency should equal "
                "throughput times residence time, and it does not — so these measurements are "
                "not of what they claim. Most often the load generator never sustained the "
                "concurrency it was asked for. Fix that before reading the coefficients."
            )

        lines.append(
            "This finding is diagnosed and never patched. Output equivalence cannot detect an "
            "introduced race, so no test this system writes makes a contention fix safe "
            "(`00-BRIEF.md` §3)."
        )
        return "\n".join(lines)


def drive_load(
    workload: Callable[[], object],
    concurrency: int,
    *,
    requests: int,
) -> LoadLevel:
    """Run `requests` calls across `concurrency` workers and time them.

    Threads rather than processes, because the workload is a callable in this
    process. **For CPU-bound Python the GIL means this does not produce real
    concurrency** — which is not a defect to hide: the Little's Law check is what
    notices, because a level that never had N requests in flight will not satisfy
    `N = X × R`. Real subjects here wait on a database, and a thread waiting on a
    socket has released the GIL.

    Raises:
        LoadError: fewer requests than workers, which would leave workers idle
            and measure a concurrency level that never happened.
    """
    if concurrency < 1:
        message = f"concurrency must be at least 1, got {concurrency}"
        raise LoadError(message)
    if requests < concurrency:
        message = (
            f"{requests} request(s) cannot occupy {concurrency} worker(s); the level would "
            "measure a concurrency that never existed"
        )
        raise LoadError(message)

    latencies: list[float] = []
    errors = 0
    lock = threading.Lock()

    def once() -> None:
        nonlocal errors
        started = time.perf_counter()
        try:
            workload()
        except Exception:  # noqa: BLE001 - a failure under load is a measurement
            elapsed = time.perf_counter() - started
            with lock:
                errors += 1
                latencies.append(elapsed)
        else:
            elapsed = time.perf_counter() - started
            with lock:
                latencies.append(elapsed)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for future in [pool.submit(once) for _ in range(requests)]:
            future.result()
    seconds = time.perf_counter() - started

    return LoadLevel(
        concurrency=concurrency,
        completions=requests,
        seconds=seconds,
        latencies=tuple(latencies),
        errors=errors,
    )


def fit_usl(levels: Sequence[LoadLevel]) -> USLFit:
    """Fit the Universal Scalability Law to a throughput curve.

    Linearized: `(γN/X(N) - 1)/(N-1) = α + βN`, whose intercept is contention and
    whose slope is coherency. Ordinary least squares on that line, which keeps
    the statistics in the standard library (ADR 015).

    Raises:
        LoadError: no single-user level to take γ from, too few levels to fit a
            line, or a level whose throughput is zero.
    """
    by_concurrency = {level.concurrency: level for level in levels}
    if SINGLE_USER not in by_concurrency:
        message = (
            "the curve has no single-user level, and γ is defined as throughput at N=1. "
            "Without it the other two coefficients are being fitted against an unknown scale"
        )
        raise LoadError(message)
    if len(by_concurrency) < MINIMUM_LEVELS:
        message = (
            f"fitting two coefficients needs at least {MINIMUM_LEVELS} distinct levels "
            f"(N=1 and three more), got {len(by_concurrency)}"
        )
        raise LoadError(message)

    gamma = by_concurrency[SINGLE_USER].throughput
    if gamma <= 0:
        message = "single-user throughput is zero, so there is no scale to fit against"
        raise LoadError(message)

    concurrencies: list[float] = []
    deficits: list[float] = []
    for concurrency, level in sorted(by_concurrency.items()):
        if concurrency == SINGLE_USER:
            continue
        if level.throughput <= 0:
            message = f"level N={concurrency} completed nothing, so it has no throughput to fit"
            raise LoadError(message)
        concurrencies.append(float(concurrency))
        deficits.append(
            ((gamma * concurrency / level.throughput) - 1) / (concurrency - SINGLE_USER)
        )

    line = statistics.linear_regression(concurrencies, deficits)
    r_squared = (
        statistics.correlation(concurrencies, deficits) ** 2 if len(set(deficits)) > 1 else 1.0
    )

    return USLFit(
        gamma=gamma,
        alpha=line.intercept,
        beta=line.slope,
        r_squared=r_squared,
        levels=int(max(concurrencies)),
    )


def check_little(
    levels: Sequence[LoadLevel], *, tolerance: float = LITTLE_TOLERANCE
) -> tuple[LittleCheck, ...]:
    """Check each level against `N = X × R`.

    The cheapest validity check available and the one that catches the failure
    everything else here is blind to: a load generator that never sustained the
    concurrency it was asked for still produces a smooth, confident, meaningless
    USL fit.
    """
    return tuple(
        LittleCheck(
            concurrency=level.concurrency,
            predicted=level.throughput * level.mean_latency,
            tolerance=tolerance,
        )
        for level in levels
    )


def measure_load(
    workload: Callable[[], object],
    concurrencies: Sequence[int],
    *,
    requests_per_level: int,
    tolerance: float = LITTLE_TOLERANCE,
) -> LoadFinding:
    """Drive the curve, fit it, check it, and mark the result diagnose-only.

    The data size is held fixed by construction: the same workload is called at
    every level and nothing here seeds or scales it. That is what makes this
    orthogonal to S-3.2 rather than a second way of doing it.
    """
    levels = tuple(
        drive_load(workload, concurrency, requests=requests_per_level)
        for concurrency in concurrencies
    )
    return LoadFinding(
        levels=levels,
        fit=fit_usl(levels),
        little=check_little(levels, tolerance=tolerance),
    )


REGISTRY.register(
    Primitive(
        name="load.usl",
        summary=(
            "Raise concurrency at a fixed data size and fit throughput to the Universal "
            "Scalability Law, returning contention, coherency and the peak. Diagnose-only."
        ),
        cost=CostClass.TENS_OF_MINUTES,
        run=measure_load,
        required_capabilities={Capability.LOAD_GENERATION, Capability.STATE_RESET},
    )
)

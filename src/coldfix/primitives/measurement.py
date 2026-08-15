"""One measured run of a workload, and the conditions that make it mean anything.

Extracted at the second caller, which is the only point `CLAUDE.md` permits an
abstraction to appear: S-3.2 and S-3.3 needed this, S-3.4 needs the same thing,
and the alternative was an ablation module reaching into a scaling module for a
private helper. Nothing here is new — it is the machinery those stories arrived
at, with the reasons they arrived at it.

Every primitive that contrasts two executions needs the same four guarantees,
and each exists because skipping it produces a *confident wrong number* rather
than an error:

**The window closes after the result is drained.** A queryset, a generator or a
streaming response has done nothing when it is returned. The counters read zero
and the clock stops early, and both are wrong in the direction of "nothing here".

**A metric set that changes between runs is refused.** A metric present in one
run and absent in another cannot be contrasted, and quietly dropping it publishes
a comparison that covered less than it claims.

**Cache control is required, not requested.** ADR 026 established that a warm
cache cannot be detected after the fact — a stale cache and a correct reset both
make every run identical — so the condition that makes one possible is refused
instead: a process that survives from one run to the next. A caller with neither
a fresh process nor a way to clear gets a refusal rather than numbers nobody can
qualify.

**How that guarantee was held is recorded.** `CLAUDE.md` requires exclusions to
carry their preconditions, and *flat across every condition* means one thing when
each run had its own container and something much weaker when a caller's hook was
trusted to empty the caches it knew about.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from enum import StrEnum
from time import perf_counter

from coldfix.bench.counting import count

# Metric names this module produces itself. An extra counter colliding with one
# of these would overwrite a measurement with another measurement, silently.
SECONDS = "seconds"
MATERIALIZED = "materialized"
RESERVED_METRICS = frozenset({SECONDS, MATERIALIZED})

# Every counter contributes two metrics: how many events, and the sum of their
# amounts. The second is named by suffixing the first.
TOTAL_SUFFIX = ".total"


class MeasurementError(Exception):
    """A measurement could not be taken, or could not be trusted."""


class CacheControlError(MeasurementError):
    """Nothing guarantees this run did not read what the previous one warmed.

    A refusal rather than a warning, because the wrong answer a warm cache
    produces is *the second run looking cheaper* — which reads as an improvement,
    or as growth that is flatter than it is, and either closes an investigation
    rather than opening one.
    """


class MetricSetError(MeasurementError):
    """The metrics recorded in one run are not the ones recorded in another."""


class BaselineError(MeasurementError):
    """The reference run could not be measured.

    Raised rather than carrying on without it. Every primitive here reports a
    *difference*, and a difference against a reference that was never taken is
    not a weaker result — it is one that is wrong by an unknown amount.
    """


class MetricKind(StrEnum):
    """What a metric's numbers are made of, which decides what may be read into them.

    Recorded because the two kinds do not deserve the same confidence. A count is
    exact and reproduces to the integer. A duration here is **one sample**: S-0.4
    measured the timing noise floor at roughly 20 ms, about 6% of a 350 ms
    endpoint, so a duration column is context for a shape, never evidence of a
    small difference. Interleaved statistical timing is S-1.6's job and
    instruction counting is S-3.19's.
    """

    COUNT = "count"
    DURATION = "duration"


class CacheControl(StrEnum):
    """How one run's cache was kept out of the next run's numbers."""

    FRESH_PROCESS = "a process that did not outlive the previous run"
    EXPLICIT_CLEAR = "a clear the caller performed between runs"
    BOTH = "a fresh process, and a clear the caller performed"


def require_cache_control(
    clear_caches: Callable[[], object] | None,
    process_identity: Callable[[], object] | None,
) -> CacheControl:
    """Which guarantee this experiment has, refusing if it has none.

    ADR 026 left the equivalent hole open deliberately — supplying no
    `process_identity` to reset verification skips the check — because
    verification is about a reset a caller may reasonably not be able to observe.
    This is a measurement, and an unqualifiable measurement is worth less than
    none, so the same hole is closed rather than documented.
    """
    if clear_caches is not None and process_identity is not None:
        return CacheControl.BOTH
    if process_identity is not None:
        return CacheControl.FRESH_PROCESS
    if clear_caches is not None:
        return CacheControl.EXPLICIT_CLEAR

    message = (
        "this experiment needs either a process identity that changes at every run or a way "
        "to clear caches between them, and was given neither. Without one of the two, work "
        "the first run warmed is free for the second, so the second looks cheaper than it "
        "is — and no comparison of the results can detect it, because a stale cache and a "
        "correct reset both make every run identical (ADR 026)"
    )
    raise CacheControlError(message)


class IdentityLedger:
    """Every process an experiment has run a condition in, and which one.

    A repeat is the condition that makes a stale cache possible, and it is
    checkable without knowing anything about the framework — which output
    comparison is not.
    """

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def record(self, identity: object, condition: str) -> None:
        """Note that `condition` ran in this process, refusing a process reuse.

        Raises:
            CacheControlError: this process already ran another condition.
        """
        key = repr(identity)
        if key in self._seen:
            message = (
                f"the process running {condition} is the one that already ran "
                f"{self._seen[key]} ({key}), so anything it cached there is still there. A "
                "cache cannot be detected from the results — a stale one and a correct one "
                "both report the same thing every time (ADR 026) — so this stops here"
            )
            raise CacheControlError(message)
        self._seen[key] = condition


def measure_once(
    invoke: Callable[[], object],
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
) -> Mapping[str, float]:
    """One measured run, with the window closed only after the result is drained.

    `counters` are hook names registered with S-1.3's `count`. An unregistered
    name raises rather than counting zero, which is ADR 013's rule and the reason
    a typo cannot become an exclusion.

    `extra_counters` are further **deterministic counters** read after the
    workload — guard counters such as rows or bytes returned, which are sums
    rather than call counts and so cannot come from a hook.

    Raises:
        MetricSetError: an extra counter collides with one produced here.
    """
    with ExitStack() as stack:
        tallies = {name: stack.enter_context(count(name)) for name in counters}

        started = perf_counter()
        materialized = materialize(invoke())
        seconds = perf_counter() - started

    metrics: dict[str, float] = {SECONDS: seconds, MATERIALIZED: float(materialized)}
    for name, tally in tallies.items():
        # Both numbers, always, for every hook. A hook that only counts makes
        # the two equal — which is a fact about that hook rather than noise —
        # and a hook that measures a quantity would otherwise be recorded as
        # its own operation count, silently: `db.rows` read as events is the
        # number of queries, which is a plausible number and the wrong one.
        metrics[name] = float(tally.events)
        metrics[f"{name}{TOTAL_SUFFIX}"] = tally.total

    if extra_counters is not None:
        extra = extra_counters()
        collisions = sorted(set(extra) & (RESERVED_METRICS | set(metrics)))
        if collisions:
            message = (
                f"extra counters {collisions} would overwrite metrics this run already "
                "measured; name them differently"
            )
            raise MetricSetError(message)
        metrics.update({name: float(value) for name, value in extra.items()})

    return metrics


def materialize(result: object) -> int:
    """Force a lazy result and report how many items that took.

    **One level deep, and that is a real limit.** A mapping's values are drained
    because a view's context is the shape this meets most often; anything lazy
    nested deeper than that is the workload's own to force, and the count is what
    says whether something was found — a run reporting zero materialized items
    has either measured a workload that returns nothing lazy, or failed to reach
    the laziness. Those look identical here and are separated by the counters,
    which is why both numbers are recorded.

    Strings and bytes are already materialized; iterating one yields characters,
    which is expense without information.
    """
    if result is None or isinstance(result, (str, bytes, bytearray)):
        return 0
    if isinstance(result, Mapping):
        return sum(drain(value) for value in result.values())
    return drain(result)


def drain(value: object) -> int:
    """Consume one possibly-lazy value, counting what came out of it."""
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return 0
    if not isinstance(value, Iterable):
        return 0
    # `sum(1 for ...)` rather than `len()`: a queryset has a length only after
    # it has run its query, and a generator never has one. Iterating is the
    # operation that forces the work in every case.
    items: Iterator[object] = iter(value)
    return sum(1 for _ in items)


def check_same_metrics(
    reference: Mapping[str, float], measured: Mapping[str, float], condition: str
) -> None:
    """Refuse two runs that did not record the same things.

    Raises:
        MetricSetError: the metric sets differ.
    """
    if set(reference) != set(measured):
        missing = sorted(set(reference) - set(measured))
        unexpected = sorted(set(measured) - set(reference))
        message = (
            f"{condition} recorded a different metric set from the reference run; missing "
            f"{missing}, unexpected {unexpected}. A metric present in one run and absent in "
            "another cannot be contrasted, and dropping it would publish a comparison that "
            "silently covered less than it claims"
        )
        raise MetricSetError(message)


def metric_kind(name: str) -> MetricKind:
    return MetricKind.DURATION if name == SECONDS else MetricKind.COUNT

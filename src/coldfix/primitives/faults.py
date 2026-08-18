"""Degrade something the subject depends on, and watch what the subject does.

Epic 3, S-3.16. `01-primitives.md` §15: ablation removes *our own* component to
measure what it costs; fault injection degrades something we *depend on* to
measure our behaviour under partial failure. Different axis, different findings —
misconfigured timeouts, retry logic that amplifies rather than recovers, missing
fallbacks, cascading latency, degradation paths nobody designed.

**Two failure modes cover most of it.** Netflix's chaos platform injects a service
becoming slower or a service returning errors, because many distinct faults reduce
to those two: a bad deploy looks like errors, and CPU, thread, memory and
bandwidth exhaustion all look like slowness. A third is kept separate here — a
connection dropped *after* the request was sent — because it is the one where a
retry re-sends work that may already have happened, and a client that is safe to
retry against a refusal is not necessarily safe to retry against a drop.

**Retry amplification is why this story matters more than its size suggests.**
`08-audit.md` F1 downgraded the metastability gate: a spike-and-recovery test
needs scale and cannot run in a single container, so primitive 3 became a risk
class we detect and hand off rather than a verification we perform. §15 notes
what is still executable — *injecting latency into a dependency and observing
whether retry logic amplifies load* — and retries are the most commonly cited
metastable trigger. So this check partially rescues that gate. **It does not
prove safety**, and nothing here should be read as if it did; it catches the
common case, which is more than the gate had before.

**Blast radius is one dependency at a time, enforced rather than advised.**
Standard chaos practice is to start with the smallest scope and expand only after
the safety controls are shown to work. Two simultaneous injections produce a
measurement that cannot be attributed to either, so the second one raises.

**The subject must have dependencies at all.** §15: not applicable to libraries,
CLI tools or self-contained batch jobs, so the primitive declares
`HAS_EXTERNAL_DEPENDENCIES` and S-3.1's registry withholds it otherwise — and
withholds it from a project where nobody has established the fact.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from coldfix.bench.stats import Growth, fit_growth
from coldfix.primitives.measurement import MeasurementError, measure_once
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
    ProjectFact,
    requires,
)
from coldfix.primitives.substitution import substitute

# A curve needs three points to be a fit, and one of them is the undegraded
# baseline that everything else is a multiple of.
MINIMUM_LEVELS = 3

# Below this, more calls under degradation is not amplification: a client that
# makes one extra call on a slow dependency is retrying once, which is what a
# retry is for. Amplification is the shape where one request becomes many.
AMPLIFICATION_FACTOR = 2.0

# One injection at a time. Held at module scope because the guarantee is about
# the process, not about any one caller — two callers each holding their own
# flag would be two simultaneous injections.
_ACTIVE = threading.Lock()


class FaultError(MeasurementError):
    """A fault could not be injected, or its effect could not be measured."""


class BlastRadiusError(FaultError):
    """A second dependency was being degraded while the first still was.

    Refused rather than allowed. Two simultaneous injections produce a
    measurement that cannot be attributed to either of them, and chaos practice
    is explicit about expanding scope only after the smallest one is shown to
    work.
    """


class InjectedFaultError(Exception):
    """What a degraded dependency raises. Never raised by the subject itself."""


class Fault(StrEnum):
    """How a dependency is degraded.

    The first two are Netflix's: many distinct faults reduce to *slower* or
    *returning errors*. The third is kept apart because retrying against it is a
    different question — the request was already sent.
    """

    LATENCY = "added latency"
    ERROR = "error responses"
    DROPPED_CONNECTION = "connections dropped after the request was sent"


@dataclass
class Calls:
    """How many times the degraded dependency was reached. Filled as it runs."""

    count: int = 0
    completed: int = 0
    """Calls that returned rather than raising. Under `ERROR` this stays zero."""


@dataclass(frozen=True)
class Response:
    """What the subject did at one level of degradation."""

    fault: Fault
    magnitude: float
    """Seconds of latency, or 1.0 for a fault that either happens or does not."""

    calls: int
    metrics: Mapping[str, float]
    failed: bool = False
    failure: str | None = None

    @property
    def survived(self) -> bool:
        """Whether the subject produced an answer despite the degradation.

        A subject that survives has a fallback, a cache or a shorter path. One
        that does not may still be correct — failing when a dependency fails is a
        design, not a defect — and which of the two it is belongs to whoever
        reads this rather than to the instrument.
        """
        return not self.failed


@dataclass(frozen=True)
class Amplification:
    """Whether degrading a dependency made the subject call it more.

    The check §15 says partially rescues the metastability gate. **It does not
    prove safety.** A subject that does not amplify under injected latency has
    passed the common case and nothing more.
    """

    responses: tuple[Response, ...]
    growth: Growth | None
    dependency: str

    @property
    def baseline_calls(self) -> int:
        """Calls with no degradation, which is what the others are a multiple of."""
        return self.responses[0].calls

    @property
    def factor(self) -> float:
        """The worst multiplication of outbound calls seen."""
        worst = max(response.calls for response in self.responses)
        if self.baseline_calls == 0:
            return float("inf") if worst else 1.0
        return worst / self.baseline_calls

    @property
    def amplifying(self) -> bool:
        """Whether one request became many under a slow dependency.

        A single extra call is a retry doing its job. Amplification is the shape
        that sustains a metastable failure: the slower the dependency gets, the
        more work the subject sends it.

        **A multiple, not a fitted exponent.** S-3.16's AC says "rising
        superlinearly", and ADR 045 records why that is the wrong test to write:
        every retrying client has a limit, so its measured curve is a step —
        1, 4, 4, 4 for a four-attempt client — which fits no growth class at all.
        Requiring superlinearity would report nothing for the textbook case this
        check exists to catch. `growth` is still fitted and still reported; it is
        just not what decides.
        """
        return self.factor >= AMPLIFICATION_FACTOR

    def explanation(self) -> str:
        counts = ", ".join(
            f"{response.magnitude:g}s→{response.calls}" for response in self.responses
        )
        head = f"Outbound calls to {self.dependency} under injected latency: {counts}."

        if not self.amplifying:
            return (
                f"{head} Calls did not multiply as the dependency slowed ({self.factor:.1f}x at "
                "worst), so retry amplification was not observed over this range. **That is "
                "not proof of safety.** `08-audit.md` F1 downgraded the metastability gate "
                "because a spike-and-recovery test needs scale this cannot reach; this check "
                "catches the common case and no more."
            )
        shape = (
            f"The count grew {self.growth} against injected latency."
            if self.growth is not None
            else (
                "The count fits no growth class, which is what a retry limit looks like from "
                "outside: it steps up to the ceiling and stays there."
            )
        )
        return (
            f"{head} One request became {self.factor:.1f} at worst. {shape} **The slower this "
            "dependency gets, the more work the subject sends it** — which is a feedback loop, "
            "and retries are the most commonly cited trigger of the metastable failures "
            "`00-BRIEF.md` §4 is about. Any patch that reduces slack here needs human review "
            "regardless of trust level."
        )


@contextmanager
def degrade(
    owner: object,
    attribute: str,
    fault: Fault,
    *,
    seconds: float = 0.0,
) -> Iterator[Calls]:
    """Degrade `owner.attribute` for the duration of the block, counting calls.

    One dependency at a time: a second `degrade` while this one is open raises,
    because two simultaneous injections produce a measurement attributable to
    neither.

    The substitution is S-3.10's, so the dependency is restored **and the
    restoration is verified** — a fault left injected would degrade every
    measurement taken afterwards.

    Raises:
        BlastRadiusError: another dependency is already being degraded.
        FaultError: the attribute cannot be wrapped, or latency was asked for
            without a duration.
    """
    if fault is Fault.LATENCY and seconds <= 0:
        message = "injecting latency needs a duration; zero seconds is not a degradation"
        raise FaultError(message)

    if not _ACTIVE.acquire(blocking=False):
        message = (
            f"another dependency is already being degraded, so degrading {attribute!r} as well "
            "would produce a measurement that cannot be attributed to either. Chaos practice "
            "is to expand scope only after the smallest one is shown to work"
        )
        raise BlastRadiusError(message)

    try:
        original = vars(owner)[attribute]
    except (TypeError, KeyError) as error:
        _ACTIVE.release()
        message = f"{attribute!r} is not defined on {owner!r} itself, so it cannot be degraded"
        raise FaultError(message) from error

    calls = Calls()
    try:
        with substitute(owner, attribute, _degraded(original, fault, seconds, calls)):
            yield calls
    finally:
        _ACTIVE.release()


def inject(  # noqa: PLR0913 - see the note on scale_volume
    owner: object,
    attribute: str,
    workload: Callable[[], object],
    fault: Fault,
    *,
    seconds: float = 0.0,
    counters: Sequence[str] = (),
) -> Response:
    """Run the workload once with the dependency degraded, and record what happened.

    A workload that raises is **not an error here** — it is the measurement. A
    subject that fails when its dependency fails may be behaving correctly, and
    the instrument's job is to record which happened rather than to decide
    whether it should have.
    """
    with degrade(owner, attribute, fault, seconds=seconds) as calls:
        failure: str | None = None
        try:
            metrics = measure_once(workload, counters)
        except Exception as error:  # noqa: BLE001 - a failure under fault is the measurement
            failure = f"{type(error).__name__}: {error}"
            metrics = {}

    return Response(
        fault=fault,
        magnitude=seconds if fault is Fault.LATENCY else 1.0,
        calls=calls.count,
        metrics=metrics,
        failed=failure is not None,
        failure=failure,
    )


def check_retry_amplification(
    owner: object,
    attribute: str,
    workload: Callable[[], object],
    latencies: Sequence[float] = (0.0, 0.05, 0.1, 0.2),
    *,
    dependency: str | None = None,
) -> Amplification:
    """Slow a dependency by increasing amounts and count what the subject sends it.

    AC 4, and the check §15 says partially rescues the metastability gate. The
    undegraded level is measured first and everything else is a multiple of it.

    Raises:
        FaultError: fewer than three levels, or no undegraded level to compare
            against.
    """
    if len(latencies) < MINIMUM_LEVELS:
        message = (
            f"a retry-amplification curve needs at least {MINIMUM_LEVELS} levels, got "
            f"{len(latencies)}"
        )
        raise FaultError(message)
    if 0.0 not in latencies:
        message = (
            "the curve has no undegraded level, so there is no call count for the degraded "
            "ones to be a multiple of"
        )
        raise FaultError(message)

    measured: list[Response] = []
    for seconds in sorted(latencies):
        if seconds > 0:
            measured.append(inject(owner, attribute, workload, Fault.LATENCY, seconds=seconds))
        else:
            measured.append(_undegraded(owner, attribute, workload))
    responses = tuple(measured)

    counts = [float(response.calls) for response in responses]
    growth = (
        fit_growth([response.magnitude for response in responses], counts).growth
        if len(set(counts)) > 1
        else Growth.CONSTANT
    )

    return Amplification(
        responses=responses,
        growth=growth,
        dependency=dependency or f"{_name(owner)}.{attribute}",
    )


def _undegraded(owner: object, attribute: str, workload: Callable[[], object]) -> Response:
    """The baseline: the dependency counted but not degraded."""
    with degrade(owner, attribute, Fault.LATENCY, seconds=1e-9) as calls:
        failure: str | None = None
        try:
            metrics = measure_once(workload)
        except Exception as error:  # noqa: BLE001 - as `inject`
            failure = f"{type(error).__name__}: {error}"
            metrics = {}

    return Response(
        fault=Fault.LATENCY,
        magnitude=0.0,
        calls=calls.count,
        metrics=metrics,
        failed=failure is not None,
        failure=failure,
    )


def _degraded(
    original: Callable[..., Any],
    fault: Fault,
    seconds: float,
    calls: Calls,
) -> Callable[..., Any]:
    """The dependency, behaving badly in exactly one declared way."""

    def degraded(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        calls.count += 1

        if fault is Fault.ERROR:
            # Refused before the request is made: nothing on the other side
            # happened, so a retry is safe in a way the dropped case is not.
            message = "the dependency returned an error"
            raise InjectedFaultError(message)

        if fault is Fault.DROPPED_CONNECTION:
            # The call *is* made, and then the connection goes. Whatever it did
            # on the other side has happened, which is why this is kept separate
            # from a plain error: retrying it re-sends work that may not be safe
            # to repeat.
            original(*args, **kwargs)
            message = "the connection dropped after the request was sent"
            raise InjectedFaultError(message)

        time.sleep(seconds)
        result = original(*args, **kwargs)
        calls.completed += 1
        return result

    return degraded


def _name(owner: object) -> str:
    return str(getattr(owner, "__name__", None) or type(owner).__name__)


REGISTRY.register(
    Primitive(
        name="faults.injection",
        summary=(
            "Degrade one declared dependency — latency, errors, dropped connections — and "
            "measure what the subject does, including whether its retries amplify load."
        ),
        cost=CostClass.MINUTES,
        run=check_retry_amplification,
        required_capabilities={Capability.DEPENDENCY_INTERPOSITION},
        applies=requires(
            ProjectFact.HAS_EXTERNAL_DEPENDENCIES,
            because=(
                "there is nothing to degrade in a library, a CLI tool or a self-contained "
                "batch job, which `01-primitives.md` §15 names as out of scope for it"
            ),
        ),
    )
)

"""Search the input space for the input that costs the most, not the biggest one.

Epic 3, S-3.17. `01-primitives.md` §14: every other primitive here varies *how
much* input — size, shape, concurrency, environment, component presence. This one
varies **which** input. Algorithmic complexity vulnerabilities live exactly in
that gap: worst-case cost far above average case for particular user-controlled
values, so a system measured with generated fixtures at increasing scale reports
these programs as healthy. Regex catastrophic backtracking, hash-collision
attacks, worst-case sort inputs and deeply nested structures are all invisible to
`scale_volume`, and all reachable here.

**The engine is Hypothesis, and this module does not mutate anything.** §14's
implementation note is emphatic — do not write a fuzzer — and `hypothesis.target`
is targeted property-based testing from Löscher & Sagonas (ISSTA 2017), an
existing search engine whose fitness function the caller supplies. That is the
part that matters: the fitness here is a **measured resource cost**, not
coverage. ADR 046 records why no AFL-lineage engine is wrapped instead; the short
version is that atheris does not build on this platform and that AFL is
coverage-guided, which is the thing SlowFuzz and PerfFuzz forked AFL to change.

**Hypothesis hill-climbs numeric draws, and only numeric draws.** Measured, not
assumed: `hypothesis/internal/conjecture/optimiser.py` skips any node whose type
is not `integer`, `float`, `bytes` or `boolean`, so a campaign over `st.text()`
is *silently* an unguided random sample — six seeds of guided and unguided search
over a text strategy returned byte-identical worst cases. Over
`st.lists(st.integers())` the same subject gave a 2.3× better worst case under
guidance. Fuzz numbers, lists of numbers, or bytes decoded into text; a text
strategy is not an error here but it is not a search either.

**A single fuzzed timing is not a finding.** S-0.4 measured the timing noise
floor at about 20ms, and the search takes one sample per input. `confirm` is what
turns the champion into a claim, by handing both inputs to S-1.6's interleaved
comparison. The search proposes; the comparison decides.

**Findings here may be vulnerability reports.** §14: ReDoS is a denial-of-service
vector rather than a slowness bug. When the worst input costs an order of
magnitude more than an *equally large* one, the asymmetry is the finding — an
attacker spends the same and the subject spends ten times more — and `report`
withholds the payload, which at that point is a working exploit. It is still
available, deliberately through one named accessor rather than by printing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic

import hypothesis
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import seed as with_seed
from hypothesis import target as observe
from hypothesis.strategies import SearchStrategy

from coldfix.bench.interleaving import InterleavedComparison, compare
from coldfix.primitives.measurement import SECONDS, MeasurementError, measure_once
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
    ProjectFact,
    requires,
)

# §14 calls this the most expensive primitive by far, and a campaign is measured
# in hours. Four of them is the ceiling: past that it is a security engagement
# with someone accountable for it, not a step inside a diagnosis.
MAXIMUM_SECONDS = 4 * 60 * 60

DEFAULT_SECONDS = 60.0
DEFAULT_EXAMPLES = 500

# Two inputs are the same size within a fifth of each other. The band exists
# because the whole claim of this primitive is *which* input rather than *how
# much*, and comparing a 40-element worst case against a 3-element typical one
# would report scaling as a complexity attack.
SIZE_BAND = 0.2

# Below this many same-size inputs there is no median worth taking — the median
# of two numbers is one of them — so the asymmetry cannot be established and the
# disclosure question stays open rather than being answered by a small sample.
MINIMUM_COMPARABLE = 5

# An order of magnitude, and the denominator is stated: the median cost of the
# inputs that are the same size as the worst one. Ten times the work for the same
# number of bytes sent is what separates a denial-of-service vector from a
# function that is slower than it should be.
DISCLOSURE_AMPLIFICATION = 10.0

# S-0.4 measured the timing noise floor at 20 repetitions. The confirmation runs
# at the same count for the same reason.
CONFIRMATION_SAMPLES = 20


class InputSearchError(MeasurementError):
    """A campaign could not be run, or its result could not be interpreted."""


class BudgetError(InputSearchError):
    """The requested campaign is longer than the cap, or is not a campaign at all.

    Refused rather than clamped, which is ADR 044's rule and for its reason: a
    silent clamp turns a rejected argument into a commitment of the cap's whole
    duration, and the cap here is four hours.
    """


class Disclosure(StrEnum):
    """How a finding from this primitive should be handled.

    Three states rather than two, because "we could not tell" is a real outcome
    and must not collapse into "ordinary". The payload is withheld from the
    report in every state except `ORDINARY` — failing closed, since the
    unmeasured case is exactly the one where nobody has established that the
    input is safe to print.
    """

    ORDINARY = "an ordinary performance finding"
    RESTRICTED = "denial-of-service potential; handle as a vulnerability report"
    UNDETERMINED = "not enough same-size inputs to separate which input from how much"


@dataclass(frozen=True)
class Candidate[Payload]:
    """One input the engine tried, and what it cost.

    `payload` is kept out of the repr on purpose. A dataclass that prints its
    payload puts a possibly-weaponised input into every log line, traceback and
    experiment record that happens to touch it, and the whole point of the
    disclosure state below is that some of these inputs are not for general
    circulation.
    """

    payload: Payload = field(repr=False)
    cost: float
    metrics: Mapping[str, float]
    size: int | None
    """`len(payload)` where the payload has one. `None` is not zero."""


@dataclass(frozen=True)
class Campaign[Payload]:
    """Everything one search tried, and what it concluded.

    Holds every candidate rather than the winner alone. The winner on its own
    cannot answer the question that decides disclosure — whether an input of the
    same size ordinarily costs this much — and a primitive that threw the losers
    away would have to guess it.
    """

    label: str
    metric: str
    engine: str
    candidates: tuple[Candidate[Payload], ...]
    seconds_spent: float
    budget_seconds: float
    seed: int | None
    guided: bool
    stopped_at_deadline: bool

    def __post_init__(self) -> None:
        if not self.candidates:
            message = (
                f"the campaign over {self.label} measured no inputs at all, so there is nothing "
                "to conclude from; a campaign with no candidates is a failed run rather than a "
                "null result"
            )
            raise InputSearchError(message)

    @property
    def _worst_index(self) -> int:
        """Position rather than value, because peers can tie with the champion."""
        costs = [candidate.cost for candidate in self.candidates]
        return costs.index(max(costs))

    @property
    def worst(self) -> Candidate[Payload]:
        """The most expensive input found. A candidate, not yet a finding."""
        return self.candidates[self._worst_index]

    @property
    def comparable(self) -> tuple[Candidate[Payload], ...]:
        """The other inputs that are the same size as the worst one, within the band.

        These are the control. Against them the worst case is a statement about
        *which* input; against the whole population it would be a statement about
        size, which `scale_volume` already answers and answers better.

        The champion itself is excluded. Leaving it in would put the largest
        value in its own denominator, which pulls the median up and understates
        every asymmetry — in the direction of not reporting a vulnerability.
        """
        reference = self.worst.size
        if reference is None:
            return ()
        low, high = reference * (1 - SIZE_BAND), reference * (1 + SIZE_BAND)
        champion = self._worst_index
        return tuple(
            candidate
            for index, candidate in enumerate(self.candidates)
            if index != champion and candidate.size is not None and low <= candidate.size <= high
        )

    @property
    def typical(self) -> Candidate[Payload] | None:
        """The median-cost input among those the same size as the worst one."""
        peers = self.comparable
        if len(peers) < MINIMUM_COMPARABLE:
            return None
        ordered = sorted(peers, key=lambda candidate: candidate.cost)
        return ordered[len(ordered) // 2]

    @property
    def amplification(self) -> float | None:
        """How much more the worst input costs than an equally large one.

        `None` where there were too few same-size inputs to say. Not 1.0: a
        missing measurement and a measured absence of asymmetry are different
        answers, and only one of them is evidence.
        """
        peer = self.typical
        if peer is None or peer.cost <= 0:
            return None
        return self.worst.cost / peer.cost

    @property
    def disclosure(self) -> Disclosure:
        """Whether this finding goes through the ordinary channel."""
        ratio = self.amplification
        if ratio is None:
            return Disclosure.UNDETERMINED
        if ratio >= DISCLOSURE_AMPLIFICATION:
            return Disclosure.RESTRICTED
        return Disclosure.ORDINARY

    def witness(self) -> Payload:
        """The worst input, by name.

        The one way to get a restricted payload out of this object. Named rather
        than printed so that handing an exploit to somebody is a line of code a
        reviewer can see, not a side effect of logging a result.
        """
        return self.worst.payload

    def report(self) -> str:
        """What happened, and what to do about it — with the payload if it is safe.

        **The payload is withheld unless the disclosure state is `ORDINARY`.**
        Not a policy this text describes, one it performs: the string is built
        without it. `witness` is how a caller who needs it asks.
        """
        head = (
            f"Searched {len(self.candidates)} inputs to {self.label} in "
            f"{self.seconds_spent:.1f}s, maximising {self.metric} with {self.engine}. "
            f"Worst measured {self.metric}: {self.worst.cost:g}"
        )
        if self.worst.size is not None:
            head += f" at size {self.worst.size}"
        head += "."

        if not self.guided:
            head += (
                " **The search was not guided.** Targeting was switched off for this campaign, "
                "so this is a random sample of the input space and the worst case is whatever "
                "turned up."
            )
        if self.stopped_at_deadline:
            head += (
                f" The campaign stopped on its {self.budget_seconds:g}s budget rather than "
                "exhausting the example count, so the space was searched less than it was asked "
                "to be."
            )

        return f"{head} {self._verdict()} {self._standing()}"

    def _verdict(self) -> str:
        peer = self.typical
        ratio = self.amplification
        if ratio is None or peer is None:
            return (
                f"Fewer than {MINIMUM_COMPARABLE} of the inputs tried were the same size as the "
                "worst one, so there is no way to tell here whether this input is expensive "
                "because of *what* it is or because of *how big* it is — which is the entire "
                "question this primitive exists to answer. **The payload is withheld**, because "
                "the case where nobody has established that an input is safe to circulate is "
                "not the case to print it in. `witness()` returns it."
            )
        if ratio >= DISCLOSURE_AMPLIFICATION:
            return (
                f"It costs **{ratio:.1f}x** what an equally large input costs, which is the "
                "asymmetry that makes an algorithmic complexity attack worth mounting: the "
                "sender spends the same and the subject spends "
                f"{ratio:.0f} times more. Treat this as a vulnerability report rather than a "
                "performance finding — `01-primitives.md` §14 — and **the payload is withheld "
                "from this text** because at this ratio it is a working exploit. `witness()` "
                "returns it to a caller who has decided to handle it."
            )
        return (
            f"It costs {ratio:.1f}x what an equally large input costs "
            f"({peer.cost:g}), which is an ordinary performance finding rather than a "
            f"denial-of-service vector. The input was {self.worst.payload!r}."
        )

    def _standing(self) -> str:
        return (
            "**This is a candidate, not a finding.** One sample per input is below the ~20ms "
            "timing floor S-0.4 measured, and the search selected this input for being the "
            "extreme of a noisy population, which is the shape a false positive takes. `confirm` "
            "is what settles it."
        )


def search[Payload](  # noqa: PLR0913 - see the note on scale_volume
    subject: Callable[[Payload], object],
    strategy: SearchStrategy[Payload],
    *,
    label: str,
    metric: str = SECONDS,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
    examples: int = DEFAULT_EXAMPLES,
    seconds: float = DEFAULT_SECONDS,
    seed: int | None = None,
    guided: bool = True,
) -> Campaign[Payload]:
    """Hunt for the input that costs the most, guided by what each one measured.

    `strategy` is what to fuzz, and it has to be a Hypothesis strategy. There is
    deliberately no way to hand this function a list of inputs: a list would make
    *this* module the generator, which is the one thing §14's implementation note
    forbids.

    `metric` names the number the search maximises, and it must be one
    `measure_once` produces — wall seconds by default, but a deterministic
    counter is far better where the subject has one, because the search then
    hill-climbs a signal instead of hill-climbing noise.

    `guided=False` runs the same campaign with targeting switched off. It exists
    because a search that finds a bad input proves nothing on its own — the
    unguided run is the control that says whether the guidance did the finding —
    and the resulting campaign says so in its own report.

    **The budget is checked between examples.** A single input that takes an hour
    overruns the cap by an hour, and there is no version of this primitive where
    that is not true: it is hunting for slow inputs, so the mechanism that would
    cut one short is the mechanism that would throw away the answer.

    Raises:
        BudgetError: a budget above the four-hour cap, or a non-positive budget
            or example count.
        MeasurementError: as `measure_once` — an unregistered counter name, or a
            collision between an extra counter and a measured one.
    """
    if seconds <= 0 or examples <= 0:
        message = (
            f"a campaign needs a positive budget and a positive example count, got "
            f"{seconds}s and {examples} examples"
        )
        raise BudgetError(message)
    if seconds > MAXIMUM_SECONDS:
        message = (
            f"{seconds}s is longer than the {MAXIMUM_SECONDS}s cap on an input search. Refused "
            "rather than shortened: a campaign this long belongs to whoever owns the security "
            "process, and silently running the cap instead would commit four hours that were "
            "never agreed to"
        )
        raise BudgetError(message)

    found: list[Candidate[Payload]] = []
    deadline = monotonic() + seconds
    started = monotonic()
    overran = False

    configured = settings(
        max_examples=examples,
        # A per-example deadline would fail the campaign on precisely the input
        # it was run to find. The same reasoning covers the health check: a
        # subject that got slow is the result, not an unhealthy test.
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
        # No example database. Reuse would make a campaign depend on what earlier
        # campaigns happened to store, which is the same defect S-1.6 refuses
        # when it declines to compare against a saved baseline.
        database=None,
        # The targeting phase is always on, because it is not what guides: with
        # no observation recorded the optimiser finds nothing to climb and
        # returns without spending a call. Guidance is carried by `observe`
        # below, which the sabotage showed — removing this phase left the guided
        # campaign still beating the unguided one, since the generation phase
        # also refuses mutations that lower an observed target.
        phases=(Phase.generate, Phase.target),
        derandomize=seed is None,
    )

    def probe(payload: Payload) -> None:
        nonlocal overran
        if monotonic() >= deadline:
            # Return rather than raise. An exception here is a *failing* test to
            # Hypothesis, which would send it shrinking towards a minimal input
            # and then hand the result back as an error — losing the campaign to
            # report that it ran out of time.
            overran = True
            return

        metrics = measure_once(lambda: subject(payload), counters, extra_counters)
        if metric not in metrics:
            message = (
                f"the campaign maximises {metric!r}, which this run did not measure. Measured: "
                f"{sorted(metrics)}"
            )
            raise InputSearchError(message)

        cost = metrics[metric]
        if guided:
            observe(cost, label=metric)
        found.append(Candidate(payload=payload, cost=cost, metrics=metrics, size=_size_of(payload)))

    driven = configured(given(strategy)(probe))
    (driven if seed is None else with_seed(seed)(driven))()

    return Campaign(
        label=label,
        metric=metric,
        engine=f"hypothesis {hypothesis.__version__}",
        candidates=tuple(found),
        seconds_spent=monotonic() - started,
        budget_seconds=seconds,
        seed=seed,
        guided=guided,
        stopped_at_deadline=overran,
    )


def confirm[Payload](
    subject: Callable[[Payload], object],
    campaign: Campaign[Payload],
    *,
    n: int = CONFIRMATION_SAMPLES,
    seed: int | None = None,
) -> InterleavedComparison:
    """Re-measure the champion against an equally large input, properly.

    The step that turns a candidate into a claim. The search took one sample per
    input and then selected the maximum of a noisy population, which is how a
    false positive is manufactured; this hands both inputs to S-1.6's interleaved
    comparison, which is the thing in this codebase allowed to say one is slower
    than the other.

    Raises:
        InputSearchError: there is no equally large input to compare against, so
            there is nothing to confirm *against* — the same condition that
            leaves the disclosure state undetermined.
    """
    peer = campaign.typical
    if peer is None:
        message = (
            f"fewer than {MINIMUM_COMPARABLE} inputs the same size as the worst one were tried, "
            "so there is no equally large input to compare it against and any difference "
            "measured would be about size rather than about content"
        )
        raise InputSearchError(message)

    worst = campaign.worst
    return compare(
        lambda: subject(worst.payload),
        lambda: subject(peer.payload),
        n,
        label_a="worst input found",
        label_b="an equally large input",
        seed=seed,
    )


def _size_of(payload: object) -> int | None:
    """`len(payload)` where that means something, and `None` where it does not."""
    try:
        return len(payload)  # type: ignore[arg-type]
    except TypeError:
        # An integer or a float has no size, and reporting 0 or 1 would make
        # every such campaign look like it held size constant when it has no
        # notion of size at all.
        return None


REGISTRY.register(
    Primitive(
        name="inputs.search",
        summary=(
            "Search the input space for the input that costs the most, with an existing "
            "engine guided by measured resource use rather than by coverage."
        ),
        cost=CostClass.HOURS,
        run=search,
        required_capabilities={Capability.INPUT_MUTATION},
        applies=requires(
            ProjectFact.PARSES_UNTRUSTED_INPUT,
            because=(
                "a worst-case input is only reachable by whoever chooses the input, so on a "
                "subject whose inputs nobody outside chooses there is no attacker to search "
                "on behalf of — `01-primitives.md` §14"
            ),
        ),
    )
)

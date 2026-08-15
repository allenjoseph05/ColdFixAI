"""Find which components own the cost, in log(n) ablations rather than n.

Epic 3, S-3.5. Ablation answers *what does this component cost*; asking it of
forty candidates one at a time costs forty runs, and each run is a reset, a
reseed and a workload. Delta debugging solved this search problem in 2002, and
`01-primitives.md` §7 records the result it is famous for: 896 lines of HTML
reduced to the single causative line in 139 automated runs, with no
understanding of the input's syntax or semantics.

**Two algorithms, and the story's note says which to prefer.** `ddmin` reduces
one expensive configuration to a 1-minimal one. `dd` isolates the *difference*
between a cheap configuration and an expensive one, working from both ends at
once — and we almost always have both, because "everything ablated" is the cheap
case and "nothing ablated" is the expensive one. Isolating the difference between
them is exactly what `dd` was written for.

**The oracle is a threshold, not a crash.** That is the whole adaptation to
performance, and it costs the algorithm a guarantee that is worth stating
plainly:

- Delta debugging's 1-minimality result assumes the outcome is **monotone** —
  that any superset of an expensive configuration is also expensive. Cost is
  usually monotone in the set of active components, because a component either
  does work or does not. It is not *always*: a component that populates a cache
  another component reads makes the second cheaper by being present. Where that
  happens the algorithms still terminate and still return a set that is
  1-minimal *as measured*, and the claim to make is that one and not the
  stronger one.
- **A measurement near the threshold is a coin flip.** S-0.4 put the timing noise
  floor at roughly 20 ms, about 6% of a 350 ms endpoint, so a configuration whose
  cost lands within that band of the threshold decides a branch of the search on
  noise. That is what `resolution` is for: inside the band the answer is
  `UNRESOLVED`, which is a state the algorithm already has and already knows how
  to make progress around. The alternative is not a better answer — it is the
  same wrong answer without the label.

**A subset that breaks the workload entirely is also unresolved.** Ablation
breaks correctness on purpose, and some combinations break it far enough that the
workload raises rather than returning something wrong. The exception is recorded
against the configuration that caused it, and the search carries on — a measured
failure to measure, not a swallowed one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum

from coldfix.primitives.ablation import (
    ExecutionModeError,
    Stub,
    choose_stub,
    record_returns,
    stubbed,
)
from coldfix.primitives.measurement import (
    IdentityLedger,
    MeasurementError,
    measure_once,
    require_cache_control,
)
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.sandbox.modes import DiagnosticSession, ExecutionMode
from coldfix.sandbox.verification import VerifiedReset

# Delta debugging starts by halving and doubles the granularity when halving
# stops making progress. Both algorithms start here.
INITIAL_GRANULARITY = 2

# A difference of one candidate is already isolated; there is nothing left to
# split.
MINIMAL_DIFFERENCE = 1


class SearchError(MeasurementError):
    """The search could not run, or its preconditions do not hold."""


class Outcome(StrEnum):
    """What one configuration's measurement said.

    The literature's PASS, FAIL and UNRESOLVED, named for what they mean here.
    `EXPENSIVE` is the *failure* being localized: the search looks for the
    smallest set of active components that still costs more than the threshold.
    """

    EXPENSIVE = "expensive"
    CHEAP = "cheap"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Probe:
    """One configuration, what it cost, and what that was taken to mean."""

    active: frozenset[str]
    outcome: Outcome
    cost: float | None = None
    failure: str | None = None
    """Why the measurement could not be taken, when it could not be."""

    cached: bool = False
    """True when this configuration had already been measured.

    Recorded because the count that matters for the story is measurements taken,
    not questions asked, and delta debugging asks the same question repeatedly.
    """

    def margin(self, threshold: float) -> float | None:
        """How far the cost sat from the threshold. `None` if never measured."""
        return None if self.cost is None else self.cost - threshold


@dataclass
class Oracle:
    """Measures a configuration and says whether it is still expensive.

    Caches by configuration, because both algorithms re-ask about sets they have
    already seen and a cache hit is not an ablation. The trace is append-only,
    for the same reason the experiment log is: it is the record of what was
    asked, in the order it was asked.
    """

    measure: Callable[[frozenset[str]], float]
    threshold: float
    resolution: float = 0.0
    """Half-width of the band around the threshold inside which no answer is given.

    Zero for counts, which are exact. For a timing, the noise floor — S-0.4
    measured ~20 ms — because a configuration inside that band decides a branch
    of the search on noise, and `UNRESOLVED` is the honest name for that.
    """

    probes: list[Probe] = field(default_factory=list)
    measurements: int = 0
    """How many times the workload was actually run.

    Counted here rather than derived from the number of distinct configurations
    seen, because those two numbers agree only while the cache works — and a
    broken cache would then double the real ablations while the reported figure
    stayed flat. This is the number AC 3 is about, so it counts the thing.
    """

    _known: dict[frozenset[str], Probe] = field(default_factory=dict, repr=False)

    def __call__(self, active: frozenset[str]) -> Outcome:
        known = self._known.get(active)
        if known is not None:
            self.probes.append(
                Probe(
                    active=active,
                    outcome=known.outcome,
                    cost=known.cost,
                    failure=known.failure,
                    cached=True,
                )
            )
            return known.outcome

        self.measurements += 1
        try:
            cost = self.measure(active)
        except Exception as error:  # noqa: BLE001 - any failure means the same thing
            # AC 4. Ablation breaks correctness deliberately, and some subsets
            # break it far enough that nothing can be measured. That is a third
            # outcome the algorithm already knows how to make progress around,
            # not an error to abort the search with — and it is recorded against
            # the configuration that caused it rather than swallowed.
            probe = Probe(
                active=active,
                outcome=Outcome.UNRESOLVED,
                failure=f"{type(error).__name__}: {error}",
            )
        else:
            probe = Probe(active=active, outcome=self._classify(cost), cost=cost)

        self._known[active] = probe
        self.probes.append(probe)
        return probe.outcome

    def _classify(self, cost: float) -> Outcome:
        if abs(cost - self.threshold) <= self.resolution:
            return Outcome.UNRESOLVED
        return Outcome.EXPENSIVE if cost > self.threshold else Outcome.CHEAP

    @property
    def configurations(self) -> int:
        """Distinct configurations seen, whether or not each cost a run."""
        return len(self._known)


@dataclass(frozen=True)
class SearchResult:
    """What the search isolated, and everything it asked to get there."""

    algorithm: str
    candidates: frozenset[str]
    culprits: frozenset[str]
    """The components that own the cost, as far as the measurements can tell."""

    probes: tuple[Probe, ...]
    measurements: int
    threshold: float
    resolution: float
    cheapest_expensive: frozenset[str] | None = None
    """`dd` only: the smallest configuration measured as expensive."""

    largest_cheap: frozenset[str] | None = None
    """`dd` only: the largest configuration measured as cheap."""

    @property
    def unresolved(self) -> int:
        return sum(1 for probe in self.probes if probe.outcome is Outcome.UNRESOLVED)

    def closest_call(self) -> float | None:
        """The smallest distance from the threshold any decision rested on.

        A search whose closest call is a hair wide was decided by that hair. The
        number is here so a reader can see that rather than infer it from a
        result that looks equally confident either way.
        """
        margins = [
            abs(margin)
            for probe in self.probes
            if not probe.cached and (margin := probe.margin(self.threshold)) is not None
        ]
        return min(margins) if margins else None


def ddmin(candidates: Iterable[str], oracle: Oracle) -> SearchResult:
    """Reduce an expensive configuration to a 1-minimal one.

    Zeller and Hildebrandt's `ddmin`, with `EXPENSIVE` in the place of their
    FAIL. Splits the active set into `n` parts, keeps the first part that is
    still expensive on its own, and when none is, tries removing each part
    instead. When neither works it doubles the granularity, which is what lets it
    find a culprit that only shows up in combination.

    Prefer `dd` where a cheap configuration is available, which for ablation it
    always is — see the module docstring and the story's note.

    Raises:
        SearchError: the full set is not expensive, so there is nothing to
            reduce, or the empty set already is, so no candidate owns the cost.
    """
    everything = frozenset(candidates)
    _require_bounds(everything, oracle)

    active = sorted(everything)
    granularity = INITIAL_GRANULARITY

    while len(active) > MINIMAL_DIFFERENCE:
        chunks = _partition(active, granularity)

        smaller = next((c for c in chunks if oracle(frozenset(c)) is Outcome.EXPENSIVE), None)
        if smaller is not None:
            active, granularity = smaller, INITIAL_GRANULARITY
            continue

        complements = [[item for item in active if item not in set(chunk)] for chunk in chunks]
        without = next(
            (c for c in complements if c and oracle(frozenset(c)) is Outcome.EXPENSIVE), None
        )
        if without is not None:
            active = without
            granularity = max(granularity - 1, INITIAL_GRANULARITY)
            continue

        if granularity >= len(active):
            break
        granularity = min(granularity * 2, len(active))

    return SearchResult(
        algorithm="ddmin",
        candidates=everything,
        culprits=frozenset(active),
        probes=tuple(oracle.probes),
        measurements=oracle.measurements,
        threshold=oracle.threshold,
        resolution=oracle.resolution,
    )


def dd(candidates: Iterable[str], oracle: Oracle) -> SearchResult:
    """Isolate the difference between a cheap configuration and an expensive one.

    The variant the story's note prefers, and the one that fits ablation: the
    cheap case is everything stubbed, the expensive case is nothing stubbed, and
    what is wanted is the smallest difference between them that still costs.

    Works from both ends. Each round tries four things with each chunk of the
    remaining difference — add it to the cheap side and see if that is now
    expensive, remove it from the expensive side and see if that is now cheap,
    and the two complementary moves that make progress without jumping. The first
    two narrow the difference sharply; the second two narrow it by one chunk.

    Raises:
        SearchError: the two ends are not cheap and expensive respectively.
    """
    everything = frozenset(candidates)
    _require_bounds(everything, oracle)

    cheap: frozenset[str] = frozenset()
    expensive = everything
    granularity = INITIAL_GRANULARITY

    while True:
        difference = expensive - cheap
        if len(difference) <= MINIMAL_DIFFERENCE:
            break

        chunks = _partition(sorted(difference), granularity)
        moved = False

        for chunk in chunks:
            part = frozenset(chunk)
            added = cheap | part
            removed = expensive - part

            if oracle(added) is Outcome.EXPENSIVE:
                expensive, granularity, moved = added, INITIAL_GRANULARITY, True
                break
            if oracle(removed) is Outcome.CHEAP:
                cheap, granularity, moved = removed, INITIAL_GRANULARITY, True
                break
            if oracle(added) is Outcome.CHEAP:
                cheap = added
                granularity = max(granularity - 1, INITIAL_GRANULARITY)
                moved = True
                break
            if oracle(removed) is Outcome.EXPENSIVE:
                expensive = removed
                granularity = max(granularity - 1, INITIAL_GRANULARITY)
                moved = True
                break

        if moved:
            continue
        if granularity >= len(difference):
            break
        granularity = min(granularity * 2, len(difference))

    return SearchResult(
        algorithm="dd",
        candidates=everything,
        culprits=expensive - cheap,
        probes=tuple(oracle.probes),
        measurements=oracle.measurements,
        threshold=oracle.threshold,
        resolution=oracle.resolution,
        cheapest_expensive=expensive,
        largest_cheap=cheap,
    )


def _require_bounds(everything: frozenset[str], oracle: Oracle) -> None:
    """Both ends must say what the search assumes they say.

    Checking costs two measurements and buys the difference between a localized
    culprit and a confidently reported arbitrary subset. Both failures are real
    findings in their own right, which is why each says what it means rather than
    only what went wrong.
    """
    if not everything:
        message = "no candidates were offered, so there is nothing to search"
        raise SearchError(message)

    whole = oracle(everything)
    if whole is not Outcome.EXPENSIVE:
        message = (
            f"the workload with every candidate active measured {whole.value} against a "
            f"threshold of {oracle.threshold:g}, so there is no expensive case to reduce. "
            "Either the threshold is above what this workload ever costs, or the cost is "
            "not here at all — which is an exclusion worth recording rather than a search "
            "worth running"
        )
        raise SearchError(message)

    nothing = oracle(frozenset())
    if nothing is Outcome.EXPENSIVE:
        message = (
            "the workload with every candidate ablated is still expensive, so none of them "
            "owns the cost and no subset of them will. The remainder after ablation is the "
            "finding here, and it is a different set of candidates"
        )
        raise SearchError(message)


def _partition(items: Sequence[str], parts: int) -> list[list[str]]:
    """Split into `parts` chunks as evenly as the length allows, order preserved.

    Every chunk is non-empty: asking for more parts than there are items yields
    one item each, because an empty chunk costs a measurement that can only
    repeat one already taken.
    """
    parts = max(1, min(parts, len(items)))
    size, remainder = divmod(len(items), parts)

    chunks: list[list[str]] = []
    start = 0
    for index in range(parts):
        stop = start + size + (1 if index < remainder else 0)
        chunks.append(list(items[start:stop]))
        start = stop
    return chunks


@dataclass(frozen=True)
class AblationTarget:
    """One component the search may switch off.

    Named separately from its owner and attribute because the search works on
    names — sets of them, hashed, cached and compared — and an owner object is
    not reliably hashable.
    """

    name: str
    owner: object
    attribute: str


def ablation_measure(  # noqa: PLR0913 - see the note on scale_volume
    targets: Sequence[AblationTarget],
    *,
    invoke: Callable[[], object],
    reset: VerifiedReset,
    session: DiagnosticSession,
    metric: str,
    seed: Callable[[], object] | None = None,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
    clear_caches: Callable[[], object] | None = None,
    process_identity: Callable[[], object] | None = None,
) -> Callable[[frozenset[str]], float]:
    """Build the measure function the oracle drives: ablate everything inactive.

    One baseline pass records a stub for every target, using S-3.4's rules — the
    value closest to the median size, or a minimal one where replay is
    impossible. Every configuration afterwards runs in its own reset cycle with
    stubs installed for the targets *not* in the active set.

    Recording once rather than per configuration is deliberate. Re-recording
    would give different configurations stubs taken under different conditions,
    and the search would then be comparing measurements that differ by more than
    the thing it is varying.

    Raises:
        ExecutionModeError: the session is not diagnostic — ablation installs
            stubs, and S-3.4 owns that refusal.
        SearchError: a target was never called during the baseline, so it owns
            no cost and would spend measurements proving it.
    """
    if session.mode is not ExecutionMode.DIAGNOSTIC:
        raise ExecutionModeError(session.mode)
    require_cache_control(clear_caches, process_identity)
    ledger = IdentityLedger()

    def prepare() -> None:
        if seed is not None:
            seed()
        if clear_caches is not None:
            clear_caches()

    with reset.mechanism.cycle():
        prepare()
        with ExitStack() as stack:
            recordings = {
                target.name: stack.enter_context(record_returns(target.owner, target.attribute))
                for target in targets
            }
            invoke()

    stubs: dict[str, Stub] = {}
    silent: list[str] = []
    for name, recording in recordings.items():
        if recording.calls == 0:
            silent.append(name)
            continue
        stubs[name] = choose_stub(recording)

    if silent:
        message = (
            f"these targets were never called with the workload intact: {sorted(silent)}. A "
            "component that does not run owns none of the cost, and including it would spend "
            "measurements establishing that"
        )
        raise SearchError(message)

    by_name = {target.name: target for target in targets}

    def measure(active: frozenset[str]) -> float:
        with reset.mechanism.cycle():
            prepare()
            if process_identity is not None:
                # Every configuration is measured once — the oracle caches — so
                # a repeated identity means a process outlived a configuration
                # and carried its caches into the next one.
                ledger.record(process_identity(), f"the configuration {sorted(active)}")
            with ExitStack() as stack:
                for name, target in by_name.items():
                    if name not in active:
                        stack.enter_context(
                            stubbed(target.owner, target.attribute, stubs[name], session=session)
                        )
                metrics = measure_once(invoke, counters, extra_counters)
        return metrics[metric]

    return measure


REGISTRY.register(
    Primitive(
        name="ablation.search",
        summary=(
            "Localize which of many components own a cost, by delta debugging over ablation "
            "subsets rather than one ablation per candidate. Diagnostic sessions only."
        ),
        cost=CostClass.TENS_OF_MINUTES,
        run=dd,
        required_capabilities={Capability.DIAGNOSTIC_WORKTREE, Capability.STATE_RESET},
    )
)

"""Remove a component, measure what changed, and record which removal it was.

Epic 3, S-3.4. `01-primitives.md` §7 calls ablation the most important primitive
because it is resource-agnostic: stub the serializer and the endpoint gets three
times faster, and the cost was in serialization without anyone having built a
serialization counter. It finds categories nobody anticipated, which no counter
can do.

**What a stub returns decides what the number means, and S-0.4 proved it the
expensive way.** The story's note states the principle — an empty-collection stub
measures the component *plus* all the downstream work that consumed its output,
a replayed real value measures the component alone — and the spike measured it on
a real Django endpoint:

- The two strategies were **statistically indistinguishable on timing**
  (434.64 ms against 438.14 ms, p = 0.64) while differing **six-fold in
  payload** (432 KB against 71 KB). Had the spike measured only wall time it
  would have concluded the strategy does not matter and deleted this story's
  recording requirement. The guard counters are the only reason the right
  conclusion was reachable.
- Its first run recorded a replay value of **one** followup, because the first
  ticket with any was a demo row that sorted ahead of the synthesized ones. That
  made the replay payload nearly as small as the empty stub's, and the two
  strategies looked interchangeable — the correct conclusion, by accident, for
  entirely the wrong reason. **A replay stub that is not size-representative is
  the empty stub's semantics wearing a disguise.** So the value replayed here is
  the one whose size is closest to the median of everything observed, and the
  distribution it was chosen from is recorded next to it.
- Replaying one fixed value does not preserve per-instance cardinality: the
  spike's ablated run emitted 600 followups where the baseline emitted 586, so
  the ablated condition was charged *more* downstream work than the baseline ever
  did, which makes the delta **understate** the component's cost. That gap was
  0.8% there and would not be harmless if the component fed something expensive,
  so it is computed and recorded rather than assumed small.

**Ablation deliberately breaks correctness, so it runs only in a diagnostic
session.** `CLAUDE.md` makes this structural rather than conventional: a
`DiagnosticSession` is the only kind that has no method returning a diff
(ADR 022) and whose worktree is destroyed on exit, and it can be obtained only
from `Workbench.open(mode=DIAGNOSTIC)`. Installing a stub requires one. A
`CandidateSession` is a different type, so passing one fails type-checking, and
it is refused at runtime as well — because a monkeypatched candidate run would
let numbers taken from deliberately broken code justify a shippable patch.
"""

from __future__ import annotations

import copy
import statistics
from collections.abc import Callable, Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import Any

from coldfix.primitives.measurement import (
    BaselineError,
    CacheControl,
    IdentityLedger,
    MeasurementError,
    MetricKind,
    check_same_metrics,
    measure_once,
    metric_kind,
    require_cache_control,
)
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.sandbox.modes import DiagnosticSession, ExecutionMode
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerifiedReset

# Minimal valid values, by type. Only types whose empty instance is unambiguous
# — an arbitrary class has no minimal instance that can be constructed without
# knowing what its consumers need, and guessing one produces an ablation that
# measures an AttributeError.
_MINIMAL: Mapping[type, object] = {
    list: [],
    tuple: (),
    dict: {},
    set: set(),
    frozenset: frozenset(),
    str: "",
    bytes: b"",
    bytearray: bytearray(),
    int: 0,
    float: 0.0,
    bool: False,
}


class AblationError(MeasurementError):
    """An ablation could not be set up, or could not be trusted."""


class ExecutionModeError(AblationError):
    """A stub was offered to something other than a diagnostic session.

    The refusal exists because the failure it prevents is not a broken run but a
    plausible one: numbers taken from deliberately broken code, in a session that
    can hand back a diff, become the evidence for a patch.
    """

    def __init__(self, mode: ExecutionMode) -> None:
        self.mode = mode
        super().__init__(
            f"ablation installs a stub that deliberately breaks correctness, so it needs a "
            f"{ExecutionMode.DIAGNOSTIC.value} session and was given a {mode.value} one. A "
            "diagnostic session has no method that returns a diff and its worktree is "
            "destroyed on exit; that is what makes an ablated run structurally incapable of "
            "producing a patch, and it is not a property this can assert on its own"
        )


class TargetError(AblationError):
    """The attribute named cannot be recorded or replaced faithfully."""


class MinimalValueError(AblationError):
    """No minimal value can be constructed for what the target returned.

    Refused rather than substituted with `None`. A stub returning `None` where
    the consumer expects a collection does not measure the component's cost — it
    measures how long the workload takes to raise an `AttributeError`, and that
    number looks exactly like a very fast component.
    """

    def __init__(self, observed: type) -> None:
        self.observed = observed
        super().__init__(
            f"the target returned a {observed.__name__}, which cannot be replayed and has no "
            "minimal value this can construct. Both stub strategies are unavailable, so "
            "there is no ablation of this target that would measure anything"
        )


class StubStrategy(StrEnum):
    """Which stub was installed, which is what decides what the delta means.

    Recorded on every result. The two do not measure the same thing, and a delta
    that does not say which was used cannot be interpreted or compared with
    another.
    """

    REPLAY = "replay"
    """A real value the target returned, replayed. Measures the component alone.

    Downstream work that consumed the component's output still happens, and is
    still charged to the ablated run, so what disappears from the measurement is
    the component's own cost.
    """

    MINIMAL = "minimal"
    """A minimal valid value of the same type. Measures the component *plus* what
    consumed it.

    Used where replay is impossible: a generator or other single-use iterator
    cannot be captured without consuming it, and a value holding a live resource
    cannot be copied. The delta is larger, and it is larger for a reason that has
    nothing to do with the component.
    """


@dataclass(frozen=True)
class Unreplayable:
    """One returned value that could not be captured, and what it was."""

    kind: type
    reason: str


@dataclass
class Recording:
    """What the target returned during the baseline run.

    Mutable and filled in as the run proceeds, like `Count` in S-1.3, so a long
    workload can be inspected part-way through rather than only at the end.
    """

    target: str
    values: list[object] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    unreplayable: list[Unreplayable] = field(default_factory=list)
    """One entry per call that could not be captured, with the type and the why."""

    @property
    def calls(self) -> int:
        return len(self.values) + len(self.unreplayable)

    @property
    def total_size(self) -> int:
        return sum(self.sizes)

    def median_size(self) -> float:
        return statistics.median(self.sizes) if self.sizes else 0.0


@dataclass(frozen=True)
class Stub:
    """The value the target will return during ablation, and where it came from."""

    strategy: StubStrategy
    value: object
    size: int
    reason: str
    """Why this strategy. On a fallback it names what made replay impossible."""

    recorded_calls: int = 0
    recorded_total_size: int = 0
    recorded_sizes: tuple[int, ...] = ()
    """The distribution the replayed value was chosen from.

    Recorded because S-0.4's first run picked a one-followup value out of a
    population whose median was six, and the resulting measurement was the empty
    stub's under another name. A reader who can see the distribution can see that
    happening; one who is given only the chosen size cannot.
    """

    def size_representative(self) -> bool:
        """Whether the replayed value sits at the middle of what was observed.

        False is not an error — a target returning two sizes has no middle — but
        it is the condition under which a replay stub quietly measures more than
        the component.
        """
        if self.strategy is not StubStrategy.REPLAY or not self.recorded_sizes:
            return False
        return self.size == _closest_to_median(self.recorded_sizes)


def share_metric(metric: str) -> str:
    """What a share of `metric` is called once it is just a number in a mapping.

    One function so the primitive that computes the share and the assembler that
    looks for it cannot disagree about its name — the failure that spelling would
    produce is a finding with no localization and nothing saying why.
    """
    return f"{metric}.share_removed"


@dataclass(frozen=True)
class AblationResult:
    """What the workload cost with the component, and what it cost without it."""

    target: str
    stub: Stub
    baseline: Mapping[str, float]
    ablated: Mapping[str, float]
    calls_baseline: int
    calls_ablated: int
    kinds: Mapping[str, MetricKind]
    reset_strategy: ResetStrategy
    cache_control: CacheControl

    def delta(self, metric: str) -> float:
        """How much of `metric` disappeared when the component did."""
        return self.baseline[metric] - self.ablated[metric]

    def share(self, metric: str) -> float:
        """The fraction of `metric` the component owned, between 0 and 1.

        The number a finding quotes. Returns 0.0 when the baseline charged
        nothing, because a component owning all of nothing is not a finding.
        """
        total = self.baseline[metric]
        if total == 0:
            return 0.0
        return self.delta(metric) / total

    def reported(self, metric: str) -> tuple[str, float]:
        """This share, named the way it must be to survive the loop boundary.

        **Added at S-8.11, and the name is the whole point.** `Executor` returns
        `Mapping[str, float]`, so the only way a share reaches the experiment log
        is as one more number in that mapping — and a number whose key each
        caller spells for itself is one the chain assembler cannot find. Epic 8's
        own fixture already emitted this exact quantity under this exact key
        before anything in `src/` named it, which is the drift this closes.

        The metric is part of the name because a share is always *of* something:
        the fraction of wall time a component owned and the fraction of queries
        it owned are two different findings.
        """
        return (share_metric(metric), self.share(metric))

    @property
    def cardinality_gap(self) -> float:
        """How much more (or less) the stub supplied than the real values did.

        One recorded value replayed for every call does not preserve per-instance
        cardinality. S-0.4 measured +0.8% and called it harmless *there*, while
        noting it would not be harmless if the component fed something expensive:
        the ablated run is then charged more downstream work than the baseline
        ever did, and the delta **understates** the component's cost.

        Zero when nothing was recorded to compare against.
        """
        if self.stub.recorded_total_size == 0:
            return 0.0
        replayed = self.calls_ablated * self.stub.size
        return (replayed - self.stub.recorded_total_size) / self.stub.recorded_total_size


@contextmanager
def record_returns(owner: object, attribute: str) -> Iterator[Recording]:
    """Capture what `owner.attribute` returns, without changing what it does.

    Every returned value is deep-copied once, here, so that the recording is the
    value as it was returned rather than as something later mutated it. The copy
    happens during the *baseline* run — where its cost is charged to both
    conditions equally is a question this cannot dodge, so see the note in
    `ablate`.

    **A single-use iterator is passed through untouched and marked
    unreplayable.** Capturing one means consuming it, which would break the very
    run being measured; a generator that has been read cannot be read again by
    the workload that asked for it.

    Raises:
        TargetError: the attribute is missing, not callable, or a descriptor
            this cannot wrap faithfully.
    """
    original = _stored_callable(owner, attribute)
    recording = Recording(target=_describe(owner, attribute))

    @wraps(original)
    def recorded(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        # Wrapping someone else's callable means accepting its signature
        # unchanged, whatever it is — the same reason S-1.3's counter does.
        result = original(*args, **kwargs)
        _capture(recording, result)
        return result

    setattr(owner, attribute, recorded)
    try:
        yield recording
    finally:
        setattr(owner, attribute, original)


def choose_stub(recording: Recording) -> Stub:
    """Pick what to replay, or fall back to a minimal value and say why.

    The replayed value is the one whose size is closest to the median of every
    size observed. Picking the first one is what S-0.4 did on its first run, and
    it produced a stub the size of an empty collection out of a population whose
    median was six times that.

    Raises:
        MinimalValueError: nothing could be captured and the type observed has no
            minimal value that can be constructed.
    """
    if recording.values:
        index = _index_closest_to_median(recording.sizes)
        return Stub(
            strategy=StubStrategy.REPLAY,
            value=recording.values[index],
            size=recording.sizes[index],
            reason=(
                f"a real value the target returned, chosen from {len(recording.values)} "
                f"observed of sizes {min(recording.sizes)}-{max(recording.sizes)} "
                f"(median {recording.median_size():g})"
            ),
            recorded_calls=recording.calls,
            recorded_total_size=recording.total_size,
            recorded_sizes=tuple(recording.sizes),
        )

    if not recording.unreplayable:
        message = (
            f"{recording.target} was never called during the baseline run, so there is "
            "nothing to ablate. A component that does not run cannot own any of the cost"
        )
        raise AblationError(message)

    observed = recording.unreplayable[0]
    minimal, described = _minimal_for(observed.kind)
    return Stub(
        strategy=StubStrategy.MINIMAL,
        value=minimal,
        size=0,
        reason=(
            f"replay was impossible ({observed.reason}), so {described} was used instead — "
            "which measures the component plus everything downstream that consumed its output"
        ),
        recorded_calls=recording.calls,
    )


@contextmanager
def stubbed(
    owner: object,
    attribute: str,
    stub: Stub,
    *,
    session: DiagnosticSession,
) -> Iterator[list[int]]:
    """Replace `owner.attribute` with something that returns `stub.value`.

    Yields a one-element list holding the call count, so a caller can read how
    many times the stub was used without the stub having to record it anywhere
    global.

    `session` is not used for anything. It is required because possessing one is
    the proof that this run is diagnostic: a `DiagnosticSession` can only come
    from `Workbench.open(mode=DIAGNOSTIC)`, has no method that returns a diff,
    and has its worktree destroyed on exit. Taking the object rather than a
    boolean is what makes the requirement structural — there is no value of
    `mode=True` a caller can pass.

    **The same object is returned to every call, never a copy.** Copying per call
    would charge deep-copy cost to the ablated condition only, which is the
    measurement distortion S-0.4 avoided by installing its patch once and
    switching it with a flag. The consequence is real and stated rather than
    fixed: a workload that mutates what the target returned will see a value an
    earlier call already mutated.

    Raises:
        ExecutionModeError: the session is not diagnostic.
        TargetError: the attribute cannot be replaced faithfully.
    """
    if session.mode is not ExecutionMode.DIAGNOSTIC:
        raise ExecutionModeError(session.mode)

    original = _stored_callable(owner, attribute)
    calls = [0]

    @wraps(original)
    def stub_call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        calls[0] += 1
        return stub.value

    setattr(owner, attribute, stub_call)
    try:
        yield calls
    finally:
        setattr(owner, attribute, original)


def ablate(  # noqa: PLR0913 - see the note on scale_volume
    *,
    owner: object,
    attribute: str,
    invoke: Callable[[], object],
    reset: VerifiedReset,
    session: DiagnosticSession,
    seed: Callable[[], object] | None = None,
    counters: Sequence[str] = (),
    extra_counters: Callable[[], Mapping[str, float]] | None = None,
    clear_caches: Callable[[], object] | None = None,
    process_identity: Callable[[], object] | None = None,
) -> AblationResult:
    """Measure the workload with `owner.attribute` intact, then with it stubbed.

    Two cycles of the verified reset, one per condition, so the ablated run
    starts from the same state the baseline did rather than from what the
    baseline left. Cache control is required for the same reason it is in a
    scaling sweep, and here it bites harder: the baseline run warms exactly the
    caches the ablated run would otherwise skip, which inflates the delta and
    makes the component look more expensive than it is.

    **The recording wrapper runs during the baseline condition and not during the
    ablated one**, so its cost is charged to the baseline alone. That is the
    opposite of S-0.4's `setattr`-per-request trap only in direction: it makes the
    baseline look slightly *more* expensive, so the delta is conservative — it
    can overstate the component by the cost of one `deepcopy` per call, never
    understate it. The copies are counted, so a target called thousands of times
    with large values is visible as such rather than as a component that got
    faster.

    Raises:
        ExecutionModeError: the session is not diagnostic.
        CacheControlError: neither cache control was supplied, or a process ran
            both conditions.
        BaselineError: the workload could not be measured with the component
            intact, so there is nothing to compare an ablated run against.
        MinimalValueError: the target's return value can be neither replayed nor
            minimally constructed.
    """
    if session.mode is not ExecutionMode.DIAGNOSTIC:
        raise ExecutionModeError(session.mode)
    control = require_cache_control(clear_caches, process_identity)
    ledger = IdentityLedger()

    def prepare() -> None:
        if seed is not None:
            seed()
        if clear_caches is not None:
            clear_caches()

    try:
        with reset.mechanism.cycle():
            prepare()
            if process_identity is not None:
                ledger.record(process_identity(), "the baseline condition")
            with record_returns(owner, attribute) as recording:
                baseline = measure_once(invoke, counters, extra_counters)
    except MeasurementError:
        # Already specific — a missing cache control or a colliding counter name
        # is not "the baseline could not be measured".
        raise
    except Exception as error:
        message = (
            f"the workload could not be measured with {_describe(owner, attribute)} intact, "
            "so there is nothing for an ablated run to be compared against"
        )
        raise BaselineError(message) from error

    stub = choose_stub(recording)

    with reset.mechanism.cycle():
        prepare()
        if process_identity is not None:
            ledger.record(process_identity(), "the ablated condition")
        with stubbed(owner, attribute, stub, session=session) as calls:
            ablated = measure_once(invoke, counters, extra_counters)

    check_same_metrics(baseline, ablated, "the ablated condition")

    return AblationResult(
        target=recording.target,
        stub=stub,
        baseline=baseline,
        ablated=ablated,
        calls_baseline=recording.calls,
        calls_ablated=calls[0],
        kinds={name: metric_kind(name) for name in sorted(baseline)},
        reset_strategy=reset.strategy,
        cache_control=control,
    )


def _capture(recording: Recording, result: object) -> None:
    """Record one returned value, or say why it could not be recorded."""
    if isinstance(result, Iterator):
        # Reading it here is the only way to capture it, and reading it here is
        # what would break the run being measured.
        recording.unreplayable.append(
            Unreplayable(type(result), f"a {type(result).__name__} is consumed by reading it")
        )
        return
    try:
        captured = copy.deepcopy(result)
    except Exception as error:  # noqa: BLE001 - every copy failure means the same thing here
        recording.unreplayable.append(
            Unreplayable(
                type(result),
                f"a {type(result).__name__} could not be copied ({type(error).__name__})",
            )
        )
        return

    recording.values.append(captured)
    recording.sizes.append(_size(captured))


def _size(value: object) -> int:
    """How big a returned value is, for choosing a representative one.

    `len` where there is one. A value without a length has no size that means
    anything here, and every such value is given the same one so that the choice
    degenerates to the first observed rather than to an arbitrary ordering.
    """
    return len(value) if isinstance(value, Sized) else 1


def _minimal_for(kind: type) -> tuple[object, str]:
    """A minimal valid value of the type the target returned, and its description.

    Exact type first, then a subclass of one that is known — a `UserList` gets an
    empty list rather than a refusal. An iterator's minimal value is an empty
    iterator, which is the one case where the thing that made replay impossible
    also names the fallback exactly.

    Raises:
        MinimalValueError: nothing here can construct a minimal instance.
    """
    minimal = _MINIMAL.get(kind)
    if minimal is not None:
        return copy.copy(minimal), f"a minimal {kind.__name__}"

    if issubclass(kind, Iterator):
        return iter(()), "an empty iterator"

    for known, value in _MINIMAL.items():
        if issubclass(kind, known):
            return copy.copy(value), f"a minimal {known.__name__}"

    raise MinimalValueError(kind)


def _stored_callable(owner: object, attribute: str) -> Callable[..., Any]:
    """The attribute as `owner` itself stores it, refused if it cannot be wrapped.

    The same rules as S-1.3's `calls_to`, and for the same reasons: an inherited
    attribute would be patched on the wrong object, and a `classmethod`,
    `staticmethod` or `property` replaced by a plain function binds differently —
    giving a faithful measurement of a program that is no longer the one under
    test.
    """
    stored: object
    try:
        stored = vars(owner)[attribute]
    except TypeError as error:
        message = f"{owner!r} has no attribute dictionary to patch"
        raise TargetError(message) from error
    except KeyError as error:
        message = (
            f"{owner!r} does not define {attribute!r} itself; "
            "name the owner where the attribute is stored"
        )
        raise TargetError(message) from error

    if isinstance(stored, (classmethod, staticmethod, property)):
        message = (
            f"{attribute!r} is a {type(stored).__name__}, which cannot be replaced "
            "without changing how it binds"
        )
        raise TargetError(message)

    if not callable(stored):
        message = f"{attribute!r} is not callable"
        raise TargetError(message)

    return stored


def _describe(owner: object, attribute: str) -> str:
    name = getattr(owner, "__name__", None) or type(owner).__name__
    return f"{name}.{attribute}"


def _closest_to_median(sizes: Sequence[int]) -> int:
    return sizes[_index_closest_to_median(sizes)]


def _index_closest_to_median(sizes: Sequence[int]) -> int:
    median = statistics.median(sizes)
    return min(range(len(sizes)), key=lambda index: abs(sizes[index] - median))


REGISTRY.register(
    Primitive(
        name="ablation.stub",
        summary=(
            "Replace a component with a recorded value or a minimal one, re-measure, and "
            "report the cost that disappeared with it. Diagnostic sessions only."
        ),
        cost=CostClass.MINUTES,
        run=ablate,
        required_capabilities={Capability.DIAGNOSTIC_WORKTREE, Capability.STATE_RESET},
    )
)

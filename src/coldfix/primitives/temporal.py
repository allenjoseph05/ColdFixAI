"""Which commit made it slow, found by measuring old revisions rather than reading them.

Epic 3, S-3.11. `01-primitives.md` §6 on why this punches above its weight: it
**requires no understanding of the code at all**, is fully automatable, and
produces the most actionable output any primitive here can — a specific commit
and a specific number. No hypothesis, no instrument selection, no interpretation.
Check out, measure, halve the range, repeat.

**The bisect is over a threshold, and a threshold has three answers.** This is
S-3.5's adaptation reused rather than reinvented: `Oracle` is generic over what
it measures, so the same noise band, the same caching and the same append-only
probe log serve a revision here and an ablation subset there. Two threshold
oracles with separately-invented semantics would produce findings that disagree
for reasons nobody could see.

**Both failure modes §6 names are handled, and neither is silent.**

*Older commits may not build.* A revision that cannot be measured is **skipped**,
which is what `git bisect skip` exists for, and the search tries a neighbour. A
range where everything is skipped is reported as unbisectable rather than
resolved to whichever commit happened to be tested last — the honest answer is
that the regression is somewhere in here and these revisions could not say where.

*The workload must exist at both endpoints.* Checked before anything is bisected,
because a workload that was added halfway through the range makes every revision
before it unmeasurable, and a bisect over that returns the commit that added the
workload. Which is true, and is not the regression.

**Every revision is measured in its own worktree, and the worktree is
destroyed.** S-2.2 owns that; what matters here is that an old revision's
checkout is exactly the thing that must not become a patch — the diff from an
old revision to the current one is a revert of everything since.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from coldfix.primitives.measurement import MeasurementError
from coldfix.primitives.registry import REGISTRY, Capability, CostClass, Primitive
from coldfix.primitives.search import Oracle, Outcome, Probe
from coldfix.sandbox.worktrees import Repository

# A range of one revision has no boundary inside it, and a range of two has
# exactly one candidate — which is already the answer, with nothing to search.
MINIMUM_RANGE = 2


class TemporalError(MeasurementError):
    """A revision could not be measured, or the range cannot support a bisect."""


class UnbisectableError(TemporalError):
    """Every revision that could have narrowed the range failed to be measured.

    A report rather than a guess. The regression is inside this range and these
    revisions cannot say where, which is a smaller answer than a commit and a far
    better one than the wrong commit.
    """


class Endpoint(StrEnum):
    """Which end of the range a revision sits at."""

    GOOD = "good"
    BAD = "bad"


@dataclass(frozen=True)
class Measurement:
    """What one revision cost, or why it could not be made to say."""

    revision: str
    cost: float | None
    failure: str | None = None

    @property
    def skipped(self) -> bool:
        """Whether this revision could not be measured at all."""
        return self.failure is not None


@dataclass(frozen=True)
class Bisection:
    """Where the cost crossed, and everything tried on the way there."""

    good: str
    """The newest revision measured as cheap. The last one before the crossing."""

    bad: str
    """The oldest revision measured as expensive. The first one after it."""

    probes: tuple[Probe[str], ...]
    skipped: tuple[str, ...]
    threshold: float
    measurements: int

    def explanation(self) -> str:
        head = (
            f"The cost crossed {self.threshold:g} between {self.good} and {self.bad}, found in "
            f"{self.measurements} measurement(s)."
        )
        if self.skipped:
            return (
                f"{head} {len(self.skipped)} revision(s) could not be measured and were "
                f"skipped ({', '.join(self.skipped)}), so the crossing is bounded by these two "
                "and may have happened at any of the skipped commits between them."
            )
        return f"{head} Every revision between them was measured, so this pair is adjacent."


@contextmanager
def at_revision(repository: Repository, revision: str, root: Path) -> Iterator[Path]:
    """A worktree at `revision`, destroyed on the way out.

    The checkout is the whole of what this primitive does to the subject, and it
    is why the worktree goes away afterwards: the diff from an old revision to
    the current one is a revert of everything since, and S-2.2's destruction is
    what stops that text existing to be applied.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"temporal-{revision[:12]}-{uuid.uuid4().hex[:8]}"
    worktree = repository.create_worktree(path, revision)
    try:
        yield worktree.path
    finally:
        repository.destroy_worktree(worktree.path)


def measure_revisions(
    repository: Repository,
    revisions: Sequence[str],
    measure: Callable[[Path], float],
    *,
    root: Path,
) -> tuple[Measurement, ...]:
    """Run the same measurement at each revision, in its own worktree.

    The straight-line version, for a range small enough to measure whole — which
    is the honest thing to do when the range is short, and what makes the
    bisect's answer checkable on a range where both are affordable.
    """
    results: list[Measurement] = []
    for revision in revisions:
        try:
            with at_revision(repository, revision, root) as path:
                cost = measure(path)
        except Exception as error:  # noqa: BLE001 - any failure means unmeasurable
            results.append(
                Measurement(
                    revision=revision,
                    cost=None,
                    failure=f"{type(error).__name__}: {error}",
                )
            )
        else:
            results.append(Measurement(revision=revision, cost=cost))
    return tuple(results)


def bisect_regression(  # noqa: PLR0913 - see the note on scale_volume
    repository: Repository,
    revisions: Sequence[str],
    measure: Callable[[Path], float],
    *,
    root: Path,
    threshold: float,
    resolution: float = 0.0,
) -> Bisection:
    """Find where the cost crossed `threshold`, oldest revision first.

    `revisions` is the range in chronological order: the first is expected to be
    cheap and the last expensive. Both are measured before anything is bisected,
    because a bisect over a range whose ends do not say what they are assumed to
    say returns a commit with full confidence and no meaning.

    `resolution` is the noise band. For a timing it should be the measured noise
    floor — S-0.4 put that at roughly 20ms — because a revision whose cost lands
    inside the band decides a step of the search on noise, and every step after
    it inherits that.

    Raises:
        TemporalError: the range is too short, or its endpoints are not a cheap
            one and an expensive one.
        UnbisectableError: every revision that could have narrowed the range
            failed to be measured.
    """
    if len(revisions) < MINIMUM_RANGE:
        message = (
            f"a bisect needs at least {MINIMUM_RANGE} revisions to have a boundary between "
            f"them, got {len(revisions)}"
        )
        raise TemporalError(message)

    skipped: list[str] = []

    def measure_at(revision: str) -> float:
        with at_revision(repository, revision, root) as path:
            return measure(path)

    oracle: Oracle[str] = Oracle(measure=measure_at, threshold=threshold, resolution=resolution)
    _require_endpoints(oracle, revisions)

    low, high = 0, len(revisions) - 1
    while high - low > 1:
        candidate = _pick_measurable(oracle, revisions, low, high, skipped)
        if candidate is None:
            # Everything between the two ends failed to be measured. The answer
            # is the pair we have and the list of what could not be tried, not
            # whichever revision happened to be reached last.
            break
        index, outcome = candidate
        if outcome is Outcome.EXPENSIVE:
            high = index
        else:
            low = index

    return Bisection(
        good=revisions[low],
        bad=revisions[high],
        probes=tuple(oracle.probes),
        skipped=tuple(skipped),
        threshold=threshold,
        measurements=oracle.measurements,
    )


def _require_endpoints(oracle: Oracle[str], revisions: Sequence[str]) -> None:
    """Both ends must be what a bisect assumes they are.

    AC 4's *verify the workload exists at both endpoints* and more: a workload
    that does not exist at the old end cannot be measured there, and a bisect
    over that returns the commit that introduced the workload — which is true and
    is not the regression.
    """
    oldest, newest = revisions[0], revisions[-1]

    for revision, endpoint in ((oldest, Endpoint.GOOD), (newest, Endpoint.BAD)):
        outcome = oracle(revision)
        if outcome is Outcome.UNRESOLVED:
            probe = oracle.probes[-1]
            detail = probe.failure or "the measurement landed inside the noise band"
            message = (
                f"the {endpoint.value} endpoint {revision} could not be measured: {detail}. A "
                "bisect needs both ends to say what they are assumed to say — most often this "
                "is a workload that does not exist at the older revision, and a bisect over "
                "that returns the commit that added the workload, which is true and is not "
                "the regression"
            )
            raise TemporalError(message)

    if oracle(oldest) is not Outcome.CHEAP:
        message = (
            f"the oldest revision {oldest} is already above the threshold, so the regression "
            "is older than this range. Extend it backwards, or record that the cost was "
            "always here — which is an exclusion rather than a failed search"
        )
        raise TemporalError(message)

    if oracle(newest) is not Outcome.EXPENSIVE:
        message = (
            f"the newest revision {newest} is not above the threshold, so there is no "
            "regression in this range to find. That is a result: whatever was slow is not "
            "slow at the head of this range"
        )
        raise TemporalError(message)


def _pick_measurable(
    oracle: Oracle[str],
    revisions: Sequence[str],
    low: int,
    high: int,
    skipped: list[str],
) -> tuple[int, Outcome] | None:
    """The midpoint, or the nearest revision to it that can be measured.

    AC 3. A revision that does not build is skipped and a neighbour tried, which
    is what `git bisect skip` does and for the same reason: an old revision that
    needs a dependency nobody can install any more is an ordinary event in a real
    repository, not a reason to abandon the search.

    Returns `None` when every revision strictly between the two ends was tried
    and none could be measured.
    """
    midpoint = (low + high) // 2
    for index in _outward_from(midpoint, low, high):
        outcome = oracle(revisions[index])
        if outcome is Outcome.UNRESOLVED:
            if revisions[index] not in skipped:
                skipped.append(revisions[index])
            continue
        return index, outcome
    return None


def _outward_from(midpoint: int, low: int, high: int) -> list[int]:
    """The candidates strictly between the ends, nearest the midpoint first.

    Nearest first because a bisect's value is halving the range, so the closer a
    usable revision is to the middle the less a skip costs. Ties break toward the
    newer revision, which keeps the order deterministic.
    """
    return sorted(range(low + 1, high), key=lambda index: (abs(index - midpoint), -index))


REGISTRY.register(
    Primitive(
        name="temporal.bisect",
        summary=(
            "Measure the same workload at earlier revisions and bisect to the commit where a "
            "cost crossed a threshold. Revisions that cannot be built are skipped and named."
        ),
        cost=CostClass.TENS_OF_MINUTES,
        run=bisect_regression,
        required_capabilities={Capability.REVISION_HISTORY, Capability.STATE_RESET},
    )
)

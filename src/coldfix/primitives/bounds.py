"""How much of the ceiling is already used, where a ceiling can be computed at all.

Epic 3, S-3.18. `01-primitives.md` §13: compute a theoretical floor on the work a
workload must do, compare it to what the workload actually did, and read off how
much room is left. A workload at 76% of its bound has nothing to win; one at 3%
has thirty-fold available. No second run — the bound is arithmetic over a
measurement already taken, which makes this the cheapest check in the set.

**`08-audit.md` F8 cut this primitive down, and the cut is the design.** The
attractive version of bound comparison asks *how many queries must this endpoint
issue*, and that question is circular: it is a question about intent, and an
agent able to answer it would already know the fix. F8 keeps three bounds and
drops the rest:

| kept, because it is a fact about data | dropped, because it is a question about intent |
|---|---|
| bytes that must be read for a transform | queries an endpoint "should" need |
| rows the response schema requires | requests a page "should" make |
| instructions a reference implementation retires | anything a business rule decides |

**The refusal is in the constructor, not in the documentation.** `Bound` raises
when asked to floor a metric whose minimum is semantic, so a caller cannot get a
circular answer by writing the arithmetic out by hand. That check is the whole of
the story: everything else here is a division.

**A bound is a ceiling, not a target.** Roofline is deliberately optimistic — it
ignores non-overlapping bottlenecks across hierarchy levels — so the achievable
share of the gap is always smaller than the gap. Every explanation this module
produces says so, because the failure mode of a headroom check is somebody
reading "3% of bound" as a promise of thirty-fold.

**No computable bound is the ordinary case, and it is an answer.** F8's
consequence in full: screening reduces to scaling plus flat-cost detection in
the general case, and bound comparison applies opportunistically rather than as a
universal pre-check. `screen` therefore accepts no bounds at all and says what
that means, rather than raising or quietly returning something that reads like a
clearance.
"""

from __future__ import annotations

from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.primitives.counters import (
    BLOCKED_DISK_CALLS,
    BLOCKED_LOCK_CALLS,
    BLOCKED_NETWORK_CALLS,
    DB_QUERY,
    FILE_OPEN,
    HTTP_REQUEST,
)
from coldfix.primitives.measurement import INSTRUCTIONS, MeasurementError
from coldfix.primitives.registry import REGISTRY, CostClass, Primitive

# Below this much available, the check says an investigation is unlikely to be
# worth its cost. The number is a factor of the measurement, not a percentage of
# the bound, because a factor is what an investigation is deciding about: 1.5x
# available is an *optimistic* ceiling of a 33% improvement, of which a real fix
# gets a fraction, against the ~6% timing noise floor S-0.4 measured on a 350ms
# endpoint. §13's own example — 76% of bound, so 1.32x available — is the case it
# calls "nothing left".
WORTH_INVESTIGATING = 1.5

# Every metric whose minimum is a question about intent. Floors on these are what
# F8 dropped, and `Bound` refuses them by name so that the refusal survives
# somebody writing the division out by hand.
_SEMANTIC: Mapping[str, str] = {
    DB_QUERY: (
        "how many queries this workload *must* issue is a question about intent, not about "
        "data: one query per row and one query for all rows both return the same answer, and "
        "deciding which is necessary is deciding the fix. Floor `db.rows` from the response "
        "schema instead — that one is computable"
    ),
    HTTP_REQUEST: (
        "how many requests are necessary depends on what the remote API offers and on what the "
        "caller decided to ask for. Floor `http.bytes` against the payload that is semantically "
        "required if there is one"
    ),
    FILE_OPEN: (
        "how many files must be opened is a consequence of how the data was laid out, which is "
        "a decision rather than a measurement"
    ),
    BLOCKED_DISK_CALLS: "how long a workload must wait is a property of the environment",
    BLOCKED_NETWORK_CALLS: "how long a workload must wait is a property of the environment",
    BLOCKED_LOCK_CALLS: "how long a workload must wait is a property of the environment",
}


class BoundError(MeasurementError):
    """A bound could not be constructed, or could not be compared against."""


class NotComputableError(BoundError):
    """A floor was asked for on a metric whose minimum is a question about intent.

    `08-audit.md` F8. Refused rather than estimated: an agent that could answer
    it would already know the fix, so a number here would be the fix wearing the
    costume of a measurement.
    """


class ImpossibleBoundError(BoundError):
    """The workload did less than its floor, so the floor or the measurement is wrong.

    Never reported as efficiency above 100%. A bound is a claim that the work
    *cannot* be done for less; a measurement below it falsifies the claim, and
    the useful output is that one of the two inputs is broken.
    """


class BoundKind(StrEnum):
    """The three F8 kept. There is no fourth, and adding one is a design decision."""

    BYTES_READ = "bytes that must be read"
    ROWS_REQUIRED = "rows the response schema requires"
    INSTRUCTIONS = "instructions a reference implementation retires"


@dataclass(frozen=True)
class Bound:
    """A floor on one metric, and the evidence that made it computable.

    The constructor is where F8 is enforced. A `Bound` on `db.query` cannot be
    made, so no later arithmetic can produce a circular headroom figure from one.
    """

    kind: BoundKind
    metric: str
    floor: float
    basis: str
    """What makes this computable — the sizes, the entities, the reference."""

    def __post_init__(self) -> None:
        reason = _SEMANTIC.get(self.metric)
        if reason is not None:
            message = f"no computable floor exists for {self.metric!r}: {reason}"
            raise NotComputableError(message)
        if self.floor < 0:
            message = f"a floor of {self.floor} is not a minimum amount of work"
            raise BoundError(message)
        if not self.basis.strip():
            message = (
                f"the {self.metric} bound states no basis. A floor whose evidence is not "
                "recorded cannot be checked by whoever reads the finding, and this primitive "
                "exists precisely because some floors are not computable"
            )
            raise BoundError(message)


@dataclass(frozen=True)
class Comparison:
    """One measurement against one floor."""

    bound: Bound
    measured: float

    def __post_init__(self) -> None:
        if self.measured < self.bound.floor:
            message = (
                f"{self.bound.metric} measured {self.measured:g}, below its floor of "
                f"{self.bound.floor:g} — so either the floor is wrong or the measurement is. "
                f"The floor was computed from: {self.bound.basis}"
            )
            raise ImpossibleBoundError(message)

    @property
    def available(self) -> float | None:
        """The most a perfect fix could win, as a factor. `None` at a floor of zero.

        A floor of zero says the work could in principle be skipped entirely,
        which is a division by zero and also not a useful thing to tell anybody:
        "infinite headroom" is what a bound that bounds nothing looks like.
        """
        if self.bound.floor <= 0:
            return None
        return self.measured / self.bound.floor

    @property
    def fraction_of_bound(self) -> float | None:
        """How much of the ceiling is already used, which is §13's own phrasing."""
        factor = self.available
        return None if factor is None else 1 / factor

    @property
    def worth_investigating(self) -> bool:
        """Whether there is enough room for a fix to be worth looking for.

        True when the bound says nothing useful, which is the safe direction: a
        headroom check that cannot compute a factor must not be the reason
        nobody looked.
        """
        factor = self.available
        return factor is None or factor >= WORTH_INVESTIGATING

    def explanation(self) -> str:
        factor = self.available
        head = (
            f"{self.bound.metric} measured {self.measured:g} against a floor of "
            f"{self.bound.floor:g} ({self.bound.kind.value}, from {self.bound.basis})."
        )
        if factor is None:
            return (
                f"{head} The floor is zero, so this bound gives no ratio and no reason to "
                "expect anything in particular. Treat the workload as unbounded by this check."
            )

        fraction = self.fraction_of_bound or 0.0
        room = (
            f"{head} That is {fraction:.0%} of the bound, so the most a perfect fix could win "
            f"is {factor:.1f}x."
        )
        if not self.worth_investigating:
            room += (
                f" Below the {WORTH_INVESTIGATING:g}x this check treats as worth pursuing — and "
                "the real ceiling is lower than this one, because a bound of this kind ignores "
                "non-overlapping bottlenecks."
            )
        return f"{room} {_CEILING}"


@dataclass(frozen=True)
class Screening:
    """What the headroom check had to say about one workload, including nothing.

    Carries the metrics it could *not* bound as prominently as the ones it could,
    because F8's finding is that the second list is usually the short one and a
    report that omitted the first would read as a clearance.
    """

    comparisons: tuple[Comparison, ...]
    unbounded: tuple[str, ...]

    @property
    def worth_investigating(self) -> bool:
        """True unless every computable bound says there is nothing to win.

        A single bound saying *no room in queries* does not mean no room: the
        workload may be spending its time somewhere nothing here can floor.
        """
        if not self.comparisons:
            return True
        return any(comparison.worth_investigating for comparison in self.comparisons)

    def report(self) -> str:
        if not self.comparisons:
            return (
                "No computable bound applied to this workload, so this check says nothing about "
                f"it — which `08-audit.md` F8 found to be the ordinary case: {_OPPORTUNISTIC} "
                f"Unbounded here: {', '.join(self.unbounded) or 'every metric measured'}."
            )

        lines = [comparison.explanation() for comparison in self.comparisons]
        if self.unbounded:
            lines.append(
                f"No computable floor for {', '.join(self.unbounded)}, so nothing above is a "
                f"statement about them. {_OPPORTUNISTIC}"
            )
        if not self.worth_investigating:
            lines.append(
                "Every bound that could be computed says the room is small. That is a reason to "
                "spend the next experiment elsewhere, not a finding — and not a statement that "
                "the workload is fast."
            )
        return " ".join(lines)


_CEILING = (
    "**A bound is a ceiling, not a target.** This model is deliberately optimistic and does not "
    "account for non-overlapping bottlenecks, so the achievable share of that gap is smaller "
    "than the gap."
)

_OPPORTUNISTIC = (
    "bound comparison applies opportunistically rather than as a universal pre-check, and "
    "screening in the general case is scaling plus flat-cost detection."
)


def bytes_that_must_be_read(sources: Mapping[str, int], *, metric: str) -> Bound:
    """A floor on bytes: the transform has to read its inputs.

    Computable because it is arithmetic over the data, not a judgement about the
    program. A transform over three files of known size must read their bytes,
    whatever it then does with them.

    Raises:
        BoundError: no sources, or a negative size.
        NotComputableError: `metric` has no computable minimum.
    """
    if not sources:
        message = "a bytes floor needs at least one source; the sum of nothing is not a bound"
        raise BoundError(message)
    negative = sorted(name for name, size in sources.items() if size < 0)
    if negative:
        message = f"these sources report a negative size, which is not a size: {negative}"
        raise BoundError(message)

    listed = ", ".join(f"{name} ({size} bytes)" for name, size in sorted(sources.items()))
    return Bound(
        kind=BoundKind.BYTES_READ,
        metric=metric,
        floor=float(sum(sources.values())),
        basis=f"the inputs the transform must read — {listed}",
    )


def rows_required_by(entities: Mapping[str, Collection[Hashable]], *, metric: str) -> Bound:
    """A floor on rows: the response already says how many distinct things it names.

    Computable because it is read off the **measured response**, not off an
    opinion about what the endpoint ought to return. A response naming 100 orders
    and 3 customers requires at least 103 rows; that it issued 200 queries to get
    them is the finding, and `db.query` is not what gets floored.

    Raises:
        BoundError: no entity types given.
        NotComputableError: `metric` has no computable minimum — which is what
            asking for a floor on `db.query` gets.
    """
    if not entities:
        message = (
            "a row floor needs the entities the response contains; with none there is no "
            "response schema to read a minimum off"
        )
        raise BoundError(message)

    distinct = {name: len(set(values)) for name, values in entities.items()}
    listed = ", ".join(f"{count} distinct {name}" for name, count in sorted(distinct.items()))
    return Bound(
        kind=BoundKind.ROWS_REQUIRED,
        metric=metric,
        floor=float(sum(distinct.values())),
        basis=f"the response itself contains {listed}",
    )


def instructions_of_reference(count: int, *, reference: str) -> Bound:
    """A floor on instructions: something already does this job in this many.

    F8's third computable case, and the one that only becomes useful when S-3.19
    lands to measure the subject. A hand-written reference implementation that
    produces the same output is a lower bound on the work, in the only unit that
    is independent of machine and load.

    Raises:
        BoundError: a non-positive count, or an unnamed reference. A floor whose
            provenance is not recorded cannot be argued with.
    """
    if count <= 0:
        message = f"a reference implementation retiring {count} instructions is not a measurement"
        raise BoundError(message)
    if not reference.strip():
        message = (
            "an instruction floor must name the reference it came from; the number is only a "
            "bound because something achieved it"
        )
        raise BoundError(message)

    return Bound(
        kind=BoundKind.INSTRUCTIONS,
        metric=INSTRUCTIONS,
        floor=float(count),
        basis=f"{reference} retires {count} instructions producing the same output",
    )


def screen(metrics: Mapping[str, float], bounds: Sequence[Bound] = ()) -> Screening:
    """Compare a measurement already taken against whatever floors exist for it.

    §13's screening check, at F8's scope. Called with no bounds — the ordinary
    case — it returns a screening that says so, rather than raising: an optional
    check that made callers handle an exception for the common path would be
    switched off, and then it would not run on the workloads where a bound *does*
    exist either.

    Raises:
        BoundError: a bound floors a metric this measurement does not contain. A
            mistyped metric name must not become a quiet "nothing to bound here",
            which is ADR 013's rule about counters applied to floors.
        ImpossibleBoundError: a measurement below its floor.
    """
    comparisons: list[Comparison] = []
    bounded: set[str] = set()
    for bound in bounds:
        if bound.metric not in metrics:
            message = (
                f"a bound was given for {bound.metric!r}, which this measurement does not "
                f"contain. Measured: {sorted(metrics)}"
            )
            raise BoundError(message)
        comparisons.append(Comparison(bound=bound, measured=metrics[bound.metric]))
        bounded.add(bound.metric)

    return Screening(
        comparisons=tuple(comparisons),
        unbounded=tuple(sorted(set(metrics) - bounded)),
    )


REGISTRY.register(
    Primitive(
        name="bounds.headroom",
        summary=(
            "Compare a measurement already taken against a computable floor — bytes that must "
            "be read, rows the response requires, a reference implementation's instructions — "
            "and report how much room is left. Says 'not computable' rather than guessing."
        ),
        cost=CostClass.SECONDS,
        run=screen,
    )
)

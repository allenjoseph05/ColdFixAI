"""Swap one thing for another, measure, and put it back — checked, not assumed.

Epic 3, S-3.10. `01-primitives.md` §9: replace an implementation or a
configuration value and measure the delta. It detects the wrong data structure,
the slow serializer, the general algorithm where a specialized one applies — and
**configuration is the highest-value sub-case**, because it is reversible, has
no syntax risk, no correctness risk from a malformed edit, and a bounded search
space, while a widely-cited figure attributes a majority of real performance
problems to it rather than to code.

**A sweep is a search. The winner is not evidence until it is compared
properly.** Measuring eight pool sizes once each is a cheap way to find out which
one to look at, and nothing more: eight single samples against S-0.4's ~20ms
noise floor cannot rank anything whose differences are smaller than that. So the
sweep returns a candidate and `confirm` puts that candidate against the
incumbent through S-1.6's interleaved comparison, which is the only thing here
that produces a claim. `01-primitives.md` §12 states the same pattern for
instruction counting: *search first, then validate the single winner with proper
interleaved statistical timing.*

**Reverting is verified, not performed.** Every substitution restores in a
`finally` and then **reads the value back and checks it**. Instrumentation that
outlives its block is the failure ADR 008 records — it raises nothing, stops
nothing, and silently taxes every measurement taken afterwards for the life of
the process. A substitution that quietly failed to revert would do the same
thing while also changing what the program does.

**A value that cannot be read cannot be restored, so it is refused up front.**
Setting first and discovering afterwards that the original is unrecoverable
leaves the subject permanently modified, which for a configuration value is
indistinguishable from a subject that was always configured that way.

**Query plans are estimates and are labelled as estimates.** `EXPLAIN` reports
what the planner *believes* an index would cost, and this project's first
non-negotiable is that there is no finding without a measurement. So a plan
comparison is evidence of a **shape change** — a sequential scan became an index
scan, which is a fact about the plan — and its cost numbers are the planner's
opinion. The finding still needs a timing or a count.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from coldfix.bench.interleaving import InterleavedComparison, compare

# What `EXPLAIN` is asked for. JSON so the plan is parsed rather than scraped,
# and without ANALYZE so the statement is not executed — see `explain`.
_EXPLAIN = "EXPLAIN (FORMAT JSON)"
_EXPLAIN_ANALYZE = "EXPLAIN (ANALYZE, FORMAT JSON)"

# Plan nodes that read a whole relation. The presence of one where an index was
# expected is the shape an index hypothesis is about.
SEQUENTIAL_NODES = frozenset({"Seq Scan"})
INDEX_NODES = frozenset({"Index Scan", "Index Only Scan", "Bitmap Index Scan"})

_MISSING = object()

# A sweep compares values against each other; one value is a substitution, and
# `substitute` is the thing for that.
MINIMUM_SWEEP_VALUES = 2


class SubstitutionError(Exception):
    """A substitution could not be made, or could not be trusted."""


class IrreversibleError(SubstitutionError):
    """The original cannot be read, so it could not be put back afterwards.

    Refused before anything is changed. Setting first and discovering this
    afterwards leaves the subject permanently modified — and for a configuration
    value, a subject that was permanently modified is indistinguishable from one
    that was always configured that way.
    """


class NotRestoredError(SubstitutionError):
    """The value did not come back, so every measurement after this one is suspect.

    Loud for the same reason `ContainerNotDestroyedError` and
    `WorktreeNotDestroyedError` are: a substitution that outlives its block does
    not raise, does not stop anything, and silently changes every measurement
    taken afterwards for the life of the process.
    """


class Connection(Protocol):
    """The little of a database connection this needs.

    Narrower than `psycopg.Connection` on purpose: what a plan comparison
    requires is the ability to run a statement and read rows back, and naming
    the whole driver here would make the query-plan half untestable without one.
    """

    def execute(self, query: str, params: Sequence[Any] = ..., /) -> Any:  # noqa: ANN401
        """Run a statement. The driver's own return type is its business."""
        ...


@dataclass(frozen=True)
class Reading:
    """One configuration value and what the workload cost with it."""

    value: object
    metric: float

    def __str__(self) -> str:
        return f"{self.value!r}: {self.metric:g}"


@dataclass(frozen=True)
class Sweep:
    """A search over configuration values. **Not evidence** — see `confirm`."""

    attribute: str
    incumbent: object
    readings: tuple[Reading, ...]
    lower_is_better: bool = True

    @property
    def best(self) -> Reading:
        """The value that measured best. One sample each, so a candidate only."""
        chooser = min if self.lower_is_better else max
        return chooser(self.readings, key=lambda reading: reading.metric)

    @property
    def candidate(self) -> object:
        return self.best.value

    @property
    def changes_anything(self) -> bool:
        """Whether the best value differs from what the subject already had."""
        return self.candidate != self.incumbent

    def explanation(self) -> str:
        ranked = ", ".join(str(reading) for reading in self.readings)
        head = (
            f"{self.attribute} swept over {len(self.readings)} value(s): {ranked}. "
            f"Best measured: {self.best}."
        )
        if not self.changes_anything:
            return (
                f"{head} That is the value the subject already had, so there is nothing to propose."
            )
        return (
            f"{head} **This is a search result, not a finding.** Each value was measured once, "
            "which cannot rank differences smaller than the noise floor, and a configuration "
            "value tuned on one workload is a claim about that workload only "
            "(`01-primitives.md` §9). Put it against the incumbent with `confirm` before "
            "proposing it."
        )


class PlanShape(StrEnum):
    """How the planner intends to reach the rows."""

    SEQUENTIAL = "sequential scan"
    INDEXED = "index scan"
    OTHER = "neither a sequential nor an index scan"


@dataclass(frozen=True)
class QueryPlan:
    """What the planner said it would do. **Estimates, except where noted.**"""

    node_types: tuple[str, ...]
    estimated_cost: float
    estimated_rows: int
    actual_seconds: float | None = None
    """Present only when the statement was actually executed (`analyze=True`)."""

    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def shape(self) -> PlanShape:
        if any(node in INDEX_NODES for node in self.node_types):
            return PlanShape.INDEXED
        if any(node in SEQUENTIAL_NODES for node in self.node_types):
            return PlanShape.SEQUENTIAL
        return PlanShape.OTHER

    @property
    def measured(self) -> bool:
        """Whether any number here came from running the statement."""
        return self.actual_seconds is not None


@dataclass(frozen=True)
class PlanChange:
    """What changed between two plans for the same statement."""

    before: QueryPlan
    after: QueryPlan

    @property
    def shape_changed(self) -> bool:
        return self.before.shape is not self.after.shape

    @property
    def became_indexed(self) -> bool:
        """The shape an index hypothesis predicts. A fact about the plan."""
        return self.before.shape is PlanShape.SEQUENTIAL and self.after.shape is PlanShape.INDEXED

    @property
    def estimated_cost_ratio(self) -> float:
        if self.before.estimated_cost == 0:
            return float("inf") if self.after.estimated_cost else 1.0
        return self.after.estimated_cost / self.before.estimated_cost

    def explanation(self) -> str:
        if self.became_indexed:
            head = (
                "The planner switched from reading the whole relation to using an index. That "
                "is a change in plan shape, which is a fact rather than an estimate."
            )
        elif self.shape_changed:
            head = (
                f"The plan shape changed from {self.before.shape.value} to "
                f"{self.after.shape.value}."
            )
        else:
            head = (
                f"The plan shape did not change: still {self.after.shape.value}. An index that "
                "does not change the plan is an index the planner declined to use."
            )

        estimate = (
            f" Estimated cost moved {self.before.estimated_cost:g} to "
            f"{self.after.estimated_cost:g} ({self.estimated_cost_ratio:.2f}x)."
        )
        if self.after.measured:
            return f"{head}{estimate} Execution was also timed, so that part is measured."
        return (
            f"{head}{estimate} **Those costs are the planner's opinion, not a measurement.** "
            "This system does not report a finding without one, so time the workload or count "
            "its queries before concluding anything about what the index bought."
        )


@contextmanager
def substitute(owner: object, attribute: str, replacement: object) -> Iterator[None]:
    """Replace `owner.attribute` for the duration of the block, and check it back.

    Works for an implementation — a method on a class, a function on a module —
    and for a configuration value held as an attribute, which is what a settings
    object is.

    The attribute must be defined on `owner` itself, the same rule S-1.3's
    `calls_to` follows: patching a name where it is *found* rather than where it
    is *stored* changes which objects are affected, and restoring it afterwards
    would write an attribute onto an object that never had one.

    An attribute that does not exist yet may be introduced — a configuration key
    the subject has never set is a legitimate thing to try — and restoring then
    means removing it again.

    Raises:
        IrreversibleError: the original cannot be read or the attribute cannot be
            set, checked before anything changes.
        NotRestoredError: the value did not come back afterwards.
    """
    original = _read_attribute(owner, attribute)
    _refuse_descriptor(attribute, original)

    try:
        setattr(owner, attribute, replacement)
    except (AttributeError, TypeError) as error:
        # Nothing has changed yet, so this is the one place the refusal costs
        # nothing. Probing settability by assigning first would be a mutation
        # taken before the caller asked for one.
        message = f"{_name(owner)}.{attribute} cannot be set, so it could not be undone: {error}"
        raise IrreversibleError(message) from error

    try:
        yield
    finally:
        if original is _MISSING:
            _remove_attribute(owner, attribute)
        else:
            setattr(owner, attribute, original)
        _verify_restored(_read_attribute(owner, attribute), original, f"{_name(owner)}.{attribute}")


@contextmanager
def substitute_item(
    mapping: MutableMapping[Any, Any],
    key: Any,  # noqa: ANN401 - a mapping's key type is the caller's
    replacement: object,
) -> Iterator[None]:
    """The same, for configuration held in a mapping rather than on an object.

    Both shapes exist in real subjects — Django's settings are attributes, a
    parsed config file is a mapping — and a caller should not have to wrap one
    to look like the other.
    """
    original = mapping.get(key, _MISSING)

    mapping[key] = replacement
    try:
        yield
    finally:
        if original is _MISSING:
            mapping.pop(key, None)
        else:
            mapping[key] = original
        _verify_restored(mapping.get(key, _MISSING), original, f"{key!r}")


def sweep_configuration(
    owner: object,
    attribute: str,
    values: Sequence[object],
    measure: Callable[[], float],
    *,
    lower_is_better: bool = True,
) -> Sweep:
    """Measure the workload once at each value, restoring between every one.

    A bounded search over the space `01-primitives.md` §9 calls the highest-value
    one. Each value is set, measured and put back before the next is tried, so a
    reading is never taken with two substitutions live at once.

    **Returns a candidate, not a conclusion.** One sample per value cannot
    separate differences smaller than the noise floor, which for timings S-0.4
    measured at roughly 20ms. `confirm` is what turns the candidate into a claim.

    Raises:
        SubstitutionError: fewer than two values, which is not a sweep.
        IrreversibleError, NotRestoredError: as `substitute`.
    """
    if len(values) < MINIMUM_SWEEP_VALUES:
        message = (
            f"a sweep needs at least two values to compare, got {len(values)}. One value is a "
            "substitution, and `substitute` is the thing for that"
        )
        raise SubstitutionError(message)

    incumbent = _read_attribute(owner, attribute)
    readings: list[Reading] = []
    for value in values:
        with substitute(owner, attribute, value):
            readings.append(Reading(value=value, metric=measure()))

    return Sweep(
        attribute=f"{_name(owner)}.{attribute}",
        incumbent=None if incumbent is _MISSING else incumbent,
        readings=tuple(readings),
        lower_is_better=lower_is_better,
    )


def confirm(  # noqa: PLR0913 - see the note on scale_volume
    owner: object,
    attribute: str,
    sweep: Sweep,
    workload: Callable[[], object],
    n: int,
    *,
    seed: int | None = None,
) -> InterleavedComparison:
    """Put the sweep's candidate against the incumbent, interleaved.

    The step that produces evidence. S-1.6 takes both variants as callables and
    runs them alternately, which is what removes the drift a block design absorbs
    into the delta — and which is why a stored measurement cannot be passed to it.

    Raises:
        SubstitutionError: the sweep's best value is the one the subject already
            had, so there is nothing to compare it against.
    """
    if not sweep.changes_anything:
        message = (
            f"the best value in this sweep is {sweep.candidate!r}, which is what "
            f"{sweep.attribute} was already set to. There is no substitution to confirm, and "
            "that is a result worth recording: the configuration is already at its best over "
            "the range tried"
        )
        raise SubstitutionError(message)

    def with_candidate() -> object:
        with substitute(owner, attribute, sweep.candidate):
            return workload()

    return compare(
        workload,
        with_candidate,
        n,
        label_a="incumbent",
        label_b=f"{attribute}={sweep.candidate!r}",
        seed=seed,
    )


def explain(
    connection: Connection,
    statement: str,
    params: Sequence[Any] = (),
    *,
    analyze: bool = False,
) -> QueryPlan:
    """Ask the planner what it intends to do with `statement`.

    **`analyze=True` executes the statement.** That is what makes its timings
    real rather than estimated, and it is also why it is not the default: an
    `EXPLAIN ANALYZE` of an `INSERT` inserts. The production guard (S-2.5) means
    this can only ever point at a test database, which makes it safe rather than
    harmless.

    Raises:
        SubstitutionError: the plan could not be read or parsed.
    """
    prefix = _EXPLAIN_ANALYZE if analyze else _EXPLAIN
    try:
        row = connection.execute(f"{prefix} {statement}", params).fetchone()
    except Exception as error:
        message = f"could not explain {statement!r}: {error}"
        raise SubstitutionError(message) from error

    if not row:
        message = f"EXPLAIN returned no rows for {statement!r}"
        raise SubstitutionError(message)

    payload = row[0]
    parsed = json.loads(payload) if isinstance(payload, str) else payload
    try:
        plan = parsed[0]["Plan"]
    except (KeyError, IndexError, TypeError) as error:
        message = f"EXPLAIN returned something this cannot read: {parsed!r}"
        raise SubstitutionError(message) from error

    return QueryPlan(
        node_types=tuple(_node_types(plan)),
        estimated_cost=float(plan.get("Total Cost", 0.0)),
        estimated_rows=int(plan.get("Plan Rows", 0)),
        actual_seconds=(float(plan["Actual Total Time"]) / 1000 if analyze else None),
        raw=plan,
    )


def compare_plans(before: QueryPlan, after: QueryPlan) -> PlanChange:
    """What changed between two plans for the same statement.

    Reports the *shape* change as a fact and the cost change as the planner's
    opinion, because that is what each of them is.
    """
    return PlanChange(before=before, after=after)


def _node_types(plan: Mapping[str, Any]) -> list[str]:
    """Every node type in the tree, outermost first."""
    found = [str(plan.get("Node Type", "Unknown"))]
    for child in plan.get("Plans", []) or []:
        found += _node_types(child)
    return found


def _read_attribute(owner: object, attribute: str) -> Any:  # noqa: ANN401
    """The attribute as `owner` itself stores it, or `_MISSING`."""
    try:
        return vars(owner)[attribute]
    except TypeError as error:
        message = (
            f"{_name(owner)} has no attribute dictionary, so {attribute!r} could not be read "
            "and therefore could not be put back"
        )
        raise IrreversibleError(message) from error
    except KeyError:
        return _MISSING


def _refuse_descriptor(attribute: str, original: Any) -> None:  # noqa: ANN401
    """S-1.3's rule: replacing a descriptor changes how the attribute binds.

    A `classmethod` swapped for a plain value stops receiving its class, so the
    measurement would be of a program that is no longer the one under test — a
    correct number about the wrong thing.
    """
    if isinstance(original, (classmethod, staticmethod, property)):
        message = (
            f"{attribute!r} is a {type(original).__name__}; replacing it changes how the "
            "attribute binds, so what would be measured is a different program"
        )
        raise IrreversibleError(message)


def _remove_attribute(owner: object, attribute: str) -> None:
    # Already absent is the state this is trying to reach.
    with suppress(AttributeError):
        delattr(owner, attribute)


def _verify_restored(found: Any, original: Any, described: str) -> None:  # noqa: ANN401
    """Read the value back and check it, rather than trusting the assignment.

    Identity first, because an implementation swap puts back the same object and
    equality on a function is identity anyway; equality second, because a
    configuration value may be restored to an equal-but-not-identical one and
    that is a correct restoration.
    """
    if found is original or (
        found is not _MISSING and original is not _MISSING and found == original
    ):
        return
    if found is _MISSING and original is _MISSING:
        return

    message = (
        f"{described} did not come back: it is {found!r} and should be {original!r}. Every "
        "measurement taken after this one is of a subject that is still substituted, which is "
        "the failure mode a substitution has to be unable to reach"
    )
    raise NotRestoredError(message)


def _name(owner: object) -> str:
    return str(getattr(owner, "__name__", None) or type(owner).__name__)

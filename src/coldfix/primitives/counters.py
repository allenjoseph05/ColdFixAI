"""The six named counters: what each one means, and who is allowed to supply it.

Epic 3, S-3.6. ADR 013 deferred exactly this: S-1.3 shipped the counting
mechanism and no counters, on the grounds that *what* to count is a question
about a framework and *how* to count is not. This is the other half.

**Most of what this module ships is a vocabulary, and that is the point.** A
counter is a name a primitive asks for and an adapter answers. If the Django
adapter registers `db.queries` and the SQLAlchemy adapter registers `db.query`,
then a primitive written against one silently measures nothing on the other —
except it does not measure *nothing*, it raises, because ADR 013 made an unknown
hook name an error rather than a zero. That refusal only helps if there is one
spelling to be wrong about, so the catalogue here is the spelling, and
registering a counter outside it is refused at registration rather than
discovered at first use.

**Two counters are framework-free and are shipped whole.** File opens are
`builtins.open`, and allocations are `tracemalloc`, neither of which needs an
adapter to know anything. The other four — queries, rows, bytes, HTTP requests —
are declarations plus a constructor, because only the adapter knows where the
cursor is (S-14.2, and ADR 008 for why it is `force_debug_cursor` and never
`settings.DEBUG`).

**Allocations do not fit the hook shape, and are not forced into it.** A hook
fires on an event; allocation counting has no event a Python-level probe can
attach to. `tracemalloc` measures over a block instead, and gives back
per-site totals with their own tracebacks — so the counter is block-scoped, its
attribution comes from tracemalloc rather than from S-1.3's stack capture, and
it is declared as a different shape rather than wrapped to look the same.

**The overhead is measured, and one of these is not cheap.** S-1.3's criterion
is five percent, and its own mechanism came in at 0.07% only after a defect that
cost 590µs per event was found and removed. See `CounterOverhead` and the
measurement in the tests: `tracemalloc` is in a different class from the others
and is declared as such, because a screening pass that quietly ran it would be
measuring the instrument.
"""

from __future__ import annotations

import builtins
import tracemalloc
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import Any

from coldfix.bench.counting import (
    Hook,
    HookError,
    Record,
    calls_to,
    register_hook,
    unregister_hook,
)
from coldfix.primitives.off_cpu import BLOCKED_DISK, BLOCKED_LOCK, BLOCKED_NETWORK

# The six the story names. Dotted and lowercase, matching the primitive registry's
# rule, because both end up in the same prompt.
DB_QUERY = "db.query"
DB_ROWS = "db.rows"
DB_BYTES = "db.bytes"
HTTP_REQUEST = "http.request"
FILE_OPEN = "file.open"
ALLOCATION = "memory.allocation"


class CounterError(Exception):
    """A counter could not be declared, registered or read."""


class UnknownCounterError(CounterError):
    """That name is not one of the counters this system knows about.

    Refused at registration rather than at first use, which is one step earlier
    than ADR 013's rule and for the same reason: an adapter that registers
    `db.queries` has produced a system where every primitive asking for
    `db.query` fails, and the failure surfaces a long way from the typo.
    """

    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        self.name = name
        self.known = known
        super().__init__(
            f"{name!r} is not a known counter; the catalogue holds: {', '.join(known)}"
        )


class Reading(StrEnum):
    """Which of a tally's two numbers a counter is asking for.

    `db.query` and `db.rows` are one attachment read two ways — the events are
    the queries, the total is the rows they returned. Wrapping the cursor twice
    to get them separately would double the cost on the hottest path in the
    system and, worse, would let the two numbers come from different runs.
    """

    EVENTS = "events"
    TOTAL = "total"


class CounterShape(StrEnum):
    """How a counter observes, which decides what attribution it can offer."""

    EVENT_HOOK = "event hook"
    """Fires per operation. Supports S-1.3's optional per-event stack capture."""

    BLOCK_METER = "block meter"
    """Measures over a whole block. Attribution comes from the tool, not from us."""


class CounterOverhead(StrEnum):
    """What attaching this counter costs the thing it is measuring.

    Declared rather than assumed, because the honest answer is not the same for
    all six and a screening pass that attached every counter it could find would
    be measuring the instrument for one of them.
    """

    NEGLIGIBLE = "negligible"
    """Well inside S-1.3's five percent. An attribute wrap and an increment."""

    HEAVY = "heavy"
    """Costs enough to distort what it measures. Attach deliberately, never by default."""


@dataclass(frozen=True)
class Counter:
    """One named counter: what an event is, what the amount means, what guards it."""

    name: str
    hook: str
    """The registered hook this reads. Not always its own name — see `Reading`."""

    reads: Reading
    event: str
    """What one event is, in the subject's terms."""

    amount: str
    """What the recorded amount means, or `"one per event"` where it only counts."""

    guard: str | None
    """The counter this one can be traded against.

    `01-primitives.md` §2: every metric pairs with the resource it can be traded
    against, because halving the queries while quadrupling the rows returned is
    not an improvement. Declared here; S-3.8 is what enforces the pairing and
    adds the global envelope, since a guard *pair* is a denylist and fails by
    omission.
    """

    shape: CounterShape = CounterShape.EVENT_HOOK
    overhead: CounterOverhead = CounterOverhead.NEGLIGIBLE
    adapter_supplied: bool = True
    """Whether a framework adapter has to attach it. Two of the six do not."""


CATALOGUE: Mapping[str, Counter] = {
    counter.name: counter
    for counter in (
        Counter(
            name=DB_QUERY,
            hook=DB_QUERY,
            reads=Reading.EVENTS,
            event="one statement sent to the database",
            amount="rows returned by that statement",
            guard=DB_ROWS,
        ),
        Counter(
            name=DB_ROWS,
            hook=DB_QUERY,
            reads=Reading.TOTAL,
            event="one statement sent to the database",
            amount="rows returned by that statement",
            guard=DB_QUERY,
        ),
        Counter(
            name=DB_BYTES,
            hook=DB_BYTES,
            reads=Reading.TOTAL,
            event="one statement whose result size was measured",
            amount="bytes in the result",
            guard=DB_QUERY,
        ),
        Counter(
            name=HTTP_REQUEST,
            hook=HTTP_REQUEST,
            reads=Reading.EVENTS,
            event="one outbound HTTP request",
            amount="bytes in the response",
            guard=f"{HTTP_REQUEST}, by response size",
        ),
        Counter(
            name=FILE_OPEN,
            hook=FILE_OPEN,
            reads=Reading.EVENTS,
            event="one call to the builtin `open`",
            amount="one per event",
            guard=None,
            adapter_supplied=False,
        ),
        # S-3.7. Blocked time is counted with the same mechanism as everything
        # else — the events are the waiting calls and the amount is the seconds
        # they waited — which is what lets a primitive read *waited on the
        # database* beside *queried the database* without a second instrument.
        Counter(
            name=BLOCKED_DISK,
            hook=BLOCKED_DISK,
            reads=Reading.TOTAL,
            event="one call to a declared disk waiting point",
            amount="seconds elapsed in that call",
            guard=None,
        ),
        Counter(
            name=BLOCKED_NETWORK,
            hook=BLOCKED_NETWORK,
            reads=Reading.TOTAL,
            event="one call to a declared network waiting point",
            amount="seconds elapsed in that call",
            guard=None,
        ),
        Counter(
            name=BLOCKED_LOCK,
            hook=BLOCKED_LOCK,
            reads=Reading.TOTAL,
            event="one call to a declared lock acquisition",
            amount="seconds elapsed in that call",
            guard=None,
        ),
        Counter(
            name=ALLOCATION,
            hook=ALLOCATION,
            reads=Reading.EVENTS,
            event="one allocation tracemalloc attributed to the subject",
            amount="bytes allocated",
            guard=f"{ALLOCATION}, by bytes",
            shape=CounterShape.BLOCK_METER,
            overhead=CounterOverhead.HEAVY,
            adapter_supplied=False,
        ),
    )
}


def describe(name: str) -> Counter:
    """The catalogue entry for a counter.

    Raises:
        UnknownCounterError: the name is not one of the six.
    """
    try:
        return CATALOGUE[name]
    except KeyError:
        raise UnknownCounterError(name, tuple(CATALOGUE)) from None


def register_counter(name: str, hook: Hook) -> None:
    """Attach an adapter's hook to one of the catalogue's names.

    The name is checked against the catalogue first, so a misspelling fails here
    rather than at the point some primitive asks for the counter that was never
    registered.

    Raises:
        UnknownCounterError: the name is not in the catalogue.
        CounterError: this counter is not the adapter's to supply, or the name
            is a reading of a hook rather than a hook of its own.
        HookError: something is already registered under that name.
    """
    counter = describe(name)
    if not counter.adapter_supplied:
        message = (
            f"{name} is not supplied by an adapter — it is framework-free and this module "
            "installs it. Registering another would mean two answers to one question"
        )
        raise CounterError(message)
    if counter.hook != name:
        message = (
            f"{name} is a reading of the {counter.hook!r} hook rather than a hook of its "
            f"own; register {counter.hook!r} and both are available"
        )
        raise CounterError(message)
    register_hook(name, hook)


def measuring(owner: object, attribute: str, amount: Callable[[Any], float]) -> Hook:
    """A hook that fires per call to `owner.attribute` and records a quantity.

    The constructor an adapter needs for the counters whose amount is not one:
    `amount` is handed whatever the call returned and yields the number to add —
    a cursor's `rowcount`, a response's content length.

    The same rules as `calls_to`, which this is the measuring sibling of: the
    attribute must be defined on the owner itself, and a descriptor is refused
    rather than wrapped into something that binds differently.

    **The amount is computed inside the measured call**, so a callable that does
    real work there is charging that work to the subject. Keep it to an attribute
    read; anything that walks a result set is measuring by consuming, which for a
    cursor is also destroying what the subject was about to read.
    """

    @contextmanager
    def install(record: Record) -> Iterator[None]:
        # Resolved here rather than when the hook is constructed, which is what
        # `calls_to` does and for a reason this system meets constantly: an
        # ablation stub may have replaced the attribute in between, and wrapping
        # the value captured at construction would silently measure a callable
        # nobody is calling.
        original = _stored_callable(owner, attribute)

        @wraps(original)
        def measured(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            result = original(*args, **kwargs)
            record(amount(result))
            return result

        setattr(owner, attribute, measured)
        try:
            yield
        finally:
            setattr(owner, attribute, original)

    return install


def file_opens() -> Hook:
    """Calls to the builtin `open`. No adapter needed, and no framework either.

    **It sees `open(...)` and nothing else.** `io.open`, `os.open`, `Path.open`
    and anything holding its own reference from before the wrap are invisible,
    which is the property of replacing an attribute that `calls_to` documents at
    length. An undercount is a measurement that looks like a finding, so the
    limit is stated here rather than left to be discovered from a number that
    seemed low.
    """
    return calls_to(builtins, "open")


@dataclass
class Allocations:
    """How much a block allocated, and where.

    Filled in when the block closes rather than as it runs, because tracemalloc
    reports by comparing snapshots and there is nothing to read part-way through.
    """

    events: int = 0
    total: float = 0.0
    """Bytes allocated, net of what was freed within the block."""

    peak: float = 0.0
    sites: tuple[tuple[str, int, float], ...] = field(default_factory=tuple)
    """The heaviest allocation sites: file:line, count, bytes.

    Attribution comes from tracemalloc's own tracebacks. It is the same job
    S-1.3's stack capture does for event hooks and it is not the same mechanism,
    which is why this counter is declared a block meter rather than dressed up as
    a hook.
    """


@contextmanager
def allocations(*, top_sites: int = 10) -> Iterator[Allocations]:
    """Count allocations for the duration of the block.

    A block meter, not an event hook: nothing in Python fires per allocation that
    a probe can attach to without a C-level profiler, so the alternative to this
    shape is inventing events, and an invented event is a fabricated
    measurement.

    **Expensive, and declared as such** — `CounterOverhead.HEAVY`. tracemalloc
    stores a traceback for every live allocation, which is exactly what makes the
    attribution possible and exactly what makes it cost. Attach it when
    allocations are the hypothesis, never as part of a screening sweep that
    attaches whatever it can.

    Raises:
        CounterError: tracemalloc is already tracing, which means somebody else
            owns the measurement and this one would report their allocations
            alongside the subject's.
    """
    if tracemalloc.is_tracing():
        message = (
            "tracemalloc is already tracing, so this block cannot own the measurement; "
            "counting allocations inside somebody else's trace mixes two subjects"
        )
        raise CounterError(message)

    result = Allocations()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    try:
        yield result
    finally:
        after = tracemalloc.take_snapshot()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        differences = after.compare_to(before, "lineno")
        result.events = sum(max(item.count_diff, 0) for item in differences)
        result.total = float(sum(item.size_diff for item in differences))
        result.peak = float(peak)
        result.sites = tuple(
            (str(item.traceback[0]), item.count_diff, float(item.size_diff))
            for item in sorted(differences, key=lambda item: item.size_diff, reverse=True)[
                :top_sites
            ]
            if item.size_diff > 0
        )


def _stored_callable(owner: object, attribute: str) -> Callable[..., Any]:
    """`calls_to`'s rules, applied to the measuring constructor."""
    stored: object
    try:
        stored = vars(owner)[attribute]
    except TypeError as error:
        message = f"{owner!r} has no attribute dictionary to patch"
        raise HookError(message) from error
    except KeyError as error:
        message = (
            f"{owner!r} does not define {attribute!r} itself; "
            "name the owner where the attribute is stored"
        )
        raise HookError(message) from error

    if isinstance(stored, (classmethod, staticmethod, property)):
        message = (
            f"{attribute!r} is a {type(stored).__name__}, which cannot be wrapped "
            "without changing how it binds"
        )
        raise HookError(message)

    if not callable(stored):
        message = f"{attribute!r} is not callable"
        raise HookError(message)

    return stored


@contextmanager
def framework_free_counters() -> Iterator[None]:
    """Register the counters that need no adapter, for the duration of a block.

    Only `file.open`. Allocations are a block meter and are entered directly
    rather than registered as a hook, which is the practical consequence of the
    two shapes being different rather than an omission.
    """
    register_hook(FILE_OPEN, file_opens())
    try:
        yield
    finally:
        unregister_hook(FILE_OPEN)

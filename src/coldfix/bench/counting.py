"""Count events while something runs, without changing what it does.

The third operation of the lab bench. Counting is where the cheapest primitive
lives — `01-primitives.md` §2 notes that counts are deterministic, needing no
warmup, no interleaving and no statistical test, because the validity problems
that plague timings do not apply to integers.

That cheapness is conditional on the counter being invisible. An instrument
that changes the program it observes produces integers that are just as
confident and no longer about the program. ADR 008 is the recorded instance:
counting queries by flipping `settings.DEBUG` works on two repositories out of
three and makes the third fail to serve a request at all.

**This module is the mechanism, not the counters.** The named counters —
queries, rows, bytes, file opens, allocations — are S-3.6, and they attach
through hooks a framework adapter declares (S-14.1). What lives here is the
registry those hooks land in, the context manager that installs one for the
duration of a block and guarantees its removal, and the optional stack capture.

A hook is process-global while installed. Events raised on any thread land in
the count, which is the same contract as a framework's own query log, and it
means two concurrent workloads counting the same hook cannot be told apart.
Nothing in the system does that yet; when something does, this is the sentence
it will need to have read.
"""

from __future__ import annotations

import os.path
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from types import FrameType
from typing import Any

# `Record` is what a hook calls when the thing it watches happens. `Hook` is
# what a framework adapter registers: given that callback, hand back a context
# manager that has the instrumentation installed for its duration.
Record = Callable[[], None]
Hook = Callable[[Record], AbstractContextManager[None]]

# Frames belonging to this package are dropped from captured stacks. An
# observer frame appears only because the observation is happening, so leaving
# it in would put a coldfix function at the top of every event's stack — and
# S-3.9 localizes a finding by walking those stacks to their divergence point.
#
# Compared as a plain normalized string, never resolved per frame. The first
# version of this called `Path.resolve()` — a filesystem syscall — once per
# counted event, which is exactly the defect class this tool exists to find and
# made the counting tests take two minutes. The check only has to be accurate
# for frames belonging to *this package*, and an imported module's
# `co_filename` is absolute, so a prefix test is sufficient.
_OBSERVER_ROOT = os.path.normcase(str(Path(__file__).resolve().parent.parent))

_HOOKS: dict[str, Hook] = {}


class HookError(Exception):
    """A hook could not be registered, found, or installed."""


class UnknownHookError(HookError):
    """No hook is registered under that name.

    Deliberately an exception rather than a count of zero. Zero is a real
    measurement in this system — "queries flat at 7,7,7 across 100x scale" is a
    published exclusion — so a typo in an instrument name must never be able to
    enter the evidence chain as evidence of absence.
    """

    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        self.name = name
        self.available = available
        known = ", ".join(available) if available else "none registered"
        super().__init__(f"no hook named {name!r}; registered hooks: {known}")


@dataclass
class Count:
    """The tally, and where each event came from.

    Mutable on purpose: it is handed to the caller when the block opens and
    fills in as the block runs, so a long workload can be inspected from a
    debugger partway through rather than only at the end.
    """

    hook_name: str
    capture_stacks: bool
    events: int = 0
    stacks: list[traceback.StackSummary] = field(default_factory=list)


def register_hook(name: str, hook: Hook) -> None:
    """Make `hook` available to `count(name)`.

    Raises on a duplicate name rather than replacing. Two adapters silently
    disagreeing about what `db.query` means is a way to produce measurements
    that are wrong rather than missing, and missing is the recoverable one.
    """
    if name in _HOOKS:
        message = f"a hook named {name!r} is already registered"
        raise HookError(message)
    _HOOKS[name] = hook


def unregister_hook(name: str) -> None:
    """Remove a hook. For adapters being torn down, and for tests."""
    if name not in _HOOKS:
        raise UnknownHookError(name, registered_hooks())
    del _HOOKS[name]


def registered_hooks() -> tuple[str, ...]:
    """Every hook name currently available, sorted."""
    return tuple(sorted(_HOOKS))


@contextmanager
def count(hook_name: str, *, capture_stacks: bool = False) -> Iterator[Count]:
    """Count events from `hook_name` for the duration of the block.

    `capture_stacks` is off by default because it is the expensive half. The
    tally costs an attribute increment per event; a stack costs a walk of the
    whole call stack per event, and the measured difference is more than an
    order of magnitude. Turn it on when localizing (S-3.9), not when screening.

    Captured stacks are innermost frame first — the call site that raised the
    event, then its callers — and frames belonging to this package are omitted.

    Raises:
        UnknownHookError: nothing is registered under that name. It does not
            return zero. See the exception's own docstring.
    """
    try:
        hook = _HOOKS[hook_name]
    except KeyError:
        raise UnknownHookError(hook_name, registered_hooks()) from None

    tally = Count(hook_name=hook_name, capture_stacks=capture_stacks)

    def record() -> None:
        tally.events += 1
        if capture_stacks:
            # `sys._getframe` is private but is the documented-in-practice way
            # to read the caller's frame without the cost of `inspect`. Frame 1
            # is whatever the hook calls `record()` from, which is where the
            # walk outward to the real call site starts.
            tally.stacks.append(_capture_stack(sys._getframe(1)))

    with hook(record):
        yield tally


def calls_to(owner: object, attribute: str) -> Hook:
    """A hook that fires once per call to `owner.attribute`.

    The general case underneath most counters worth having: a query counter is
    calls to a cursor's `execute`, a file-open counter is calls to `open`. It
    is what a framework adapter reaches for when the framework offers no hook
    of its own.

    `attribute` must be defined on `owner` itself — `vars(owner)` — rather than
    inherited. Patching a name where it is *found* instead of where it is
    *stored* silently changes which objects are affected, and restoring it
    afterwards would write an attribute onto a class that never had one.

    **It counts calls that go through the attribute, and only those.** A
    consumer holding its own reference calls the original and is never seen::

        import target;        target.work()   # counted
        from target import work;  work()      # NOT counted

    The undercount is silent, and an undercount is a measurement that looks
    like a finding. This is a property of replacing an attribute, not a defect
    that can be fixed here — a name bound at import time cannot be reached
    afterwards. It matters much less than it first appears, because a method
    reached through an instance (`cursor.execute(...)`) is looked up on the
    class at every call, and that is the shape of nearly every counter worth
    having. Framework hooks — Django's `execute_wrapper`, S-14.2 — do not
    depend on attribute lookup at all and are preferred where they exist.

    **An `async def` is counted when the coroutine is created**, not when it is
    awaited. For a call count those are the same number; for anything that
    needs completions, the hook belongs around the await.

    Raises:
        HookError: the attribute is missing from `owner`, is not callable, or
            is a descriptor this cannot wrap faithfully.
    """

    def install(record: Record) -> AbstractContextManager[None]:
        return _wrap_for_counting(owner, attribute, record)

    return install


@contextmanager
def _wrap_for_counting(owner: object, attribute: str, record: Record) -> Iterator[None]:
    original = _stored_callable(owner, attribute)

    @wraps(original)
    def counted(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        # Wrapping someone else's callable means accepting its signature
        # unchanged, whatever it is. Naming the types here would be inventing
        # them for an API this module cannot see.
        record()
        return original(*args, **kwargs)

    setattr(owner, attribute, counted)
    try:
        yield
    finally:
        # In a `finally`, and unconditional. Instrumentation that outlives its
        # block is the failure ADR 008 documents: it does not raise, it does
        # not stop anything, it silently taxes and reshapes every measurement
        # taken afterwards for the life of the process.
        setattr(owner, attribute, original)


def _stored_callable(owner: object, attribute: str) -> Callable[..., Any]:
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

    # A `classmethod`/`staticmethod`/`property` object is not the function it
    # holds. Replacing one with a plain wrapper changes how the attribute binds
    # — a classmethod would stop receiving its class — so the count would be
    # right and the program would be different. Refuse instead.
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


def _capture_stack(frame: FrameType) -> traceback.StackSummary:
    """The call stack at an event, innermost first, without observer frames.

    Built frame by frame rather than through `StackSummary.extract`, which is
    twice as slow because it primes `linecache` for every frame it sees even
    when told not to look up lines. This runs once per counted event on the
    localization path, and the source text is not wanted: the point of a stack
    here is which file, line and function raised the event. Reading the target
    repository's source is a later step that happens once, not per event.
    """
    summary = traceback.StackSummary()
    for caller, lineno in traceback.walk_stack(frame):
        code = caller.f_code
        summary.append(
            traceback.FrameSummary(code.co_filename, lineno, code.co_name, lookup_line=False)
        )
    while summary and _is_observer_frame(summary[0]):
        summary.pop(0)
    return summary


def _is_observer_frame(frame: traceback.FrameSummary) -> bool:
    return os.path.normcase(frame.filename).startswith(_OBSERVER_ROOT)

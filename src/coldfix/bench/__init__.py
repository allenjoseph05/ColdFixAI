"""The lab bench — deterministic operations that measure faithfully and decide nothing.

Five operations: `execute`, `time`, `count`, `diff`, `stats`. Agents reason about
the measurements these take; agents never report a measurement themselves.

Epic 1.
"""

from coldfix.bench.counting import (
    Count,
    HookError,
    UnknownHookError,
    calls_to,
    count,
    register_hook,
    registered_hooks,
    unregister_hook,
)
from coldfix.bench.execute import (
    ExecutionError,
    ExecutionResult,
    ExecutionTimeoutError,
    execute,
)
from coldfix.bench.timing import (
    ProcessState,
    Sample,
    TimingError,
    TimingRun,
    time,
)

__all__ = [
    "Count",
    "ExecutionError",
    "ExecutionResult",
    "ExecutionTimeoutError",
    "HookError",
    "ProcessState",
    "Sample",
    "TimingError",
    "TimingRun",
    "UnknownHookError",
    "calls_to",
    "count",
    "execute",
    "register_hook",
    "registered_hooks",
    "time",
    "unregister_hook",
]

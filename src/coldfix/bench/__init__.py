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
from coldfix.bench.diffing import (
    ABSENT,
    Comparison,
    Difference,
    DifferenceKind,
    DiffError,
    JsonValue,
    UnsupportedValueError,
    diff,
)
from coldfix.bench.execute import (
    ExecutionError,
    ExecutionResult,
    ExecutionTimeoutError,
    execute,
)
from coldfix.bench.stats import (
    Fit,
    Growth,
    RankTest,
    StatsError,
    Summary,
    fit_growth,
    rank_test,
    stats,
)
from coldfix.bench.timing import (
    ProcessState,
    Sample,
    TimingError,
    TimingRun,
    time,
)

__all__ = [
    "ABSENT",
    "Comparison",
    "Count",
    "DiffError",
    "Difference",
    "DifferenceKind",
    "ExecutionError",
    "ExecutionResult",
    "ExecutionTimeoutError",
    "Fit",
    "Growth",
    "HookError",
    "JsonValue",
    "ProcessState",
    "RankTest",
    "Sample",
    "StatsError",
    "Summary",
    "TimingError",
    "TimingRun",
    "UnknownHookError",
    "UnsupportedValueError",
    "calls_to",
    "count",
    "diff",
    "execute",
    "fit_growth",
    "rank_test",
    "register_hook",
    "registered_hooks",
    "stats",
    "time",
    "unregister_hook",
]

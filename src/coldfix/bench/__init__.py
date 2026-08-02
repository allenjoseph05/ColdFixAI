"""The lab bench — deterministic operations that measure faithfully and decide nothing.

Five operations: `execute`, `time`, `count`, `diff`, `stats`. Agents reason about
the measurements these take; agents never report a measurement themselves.

Epic 1.
"""

from coldfix.bench.execute import (
    ExecutionError,
    ExecutionResult,
    ExecutionTimeoutError,
    execute,
)

__all__ = [
    "ExecutionError",
    "ExecutionResult",
    "ExecutionTimeoutError",
    "execute",
]

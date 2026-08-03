"""**DEFECT — slow import.** Builds a lookup table at module import time.

Signature: importing this module costs materially more than importing
`fast_import`, and the cost is paid on *import*, not on first use — so it is
invisible to any measurement that starts after the process is up.

The work is CPU-bound rather than a `time.sleep`, for two reasons. S-0.4
measured `time.sleep` carrying 80-100 microseconds of syscall overhead per call
regardless of the duration requested, so a sleep-based fixture partly measures
the sleep. And a real slow import is almost always module-level computation or a
heavy dependency graph, not a deliberate pause.

Tests must import this through `importlib` with `sys.modules` cleared, since
Python caches modules and a second import costs nothing.
"""

from __future__ import annotations

# Module-level work. This is the defect: it runs at import, once, before any
# caller has asked for anything.
_LOOKUP: dict[int, int] = {}
for _n in range(200_000):
    _LOOKUP[_n] = (_n * _n) % 9973


def lookup(value: int) -> int:
    """Cheap at call time. All the cost was paid at import."""
    return _LOOKUP.get(value, 0)

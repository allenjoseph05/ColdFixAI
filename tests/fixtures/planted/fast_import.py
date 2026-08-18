"""**CONTROL — the same capability, computed lazily.**

Signature: importing this module is near-free; the cost moves to first call.
Functionally equivalent to `slow_import` from a caller's point of view, which
is what makes it a fair control rather than a different program.
"""

from __future__ import annotations

_LOOKUP: dict[int, int] = {}


def lookup(value: int) -> int:
    """Populates on first use rather than at import."""
    if not _LOOKUP:
        for n in range(200_000):
            _LOOKUP[n] = (n * n) % 9973
    return _LOOKUP.get(value, 0)

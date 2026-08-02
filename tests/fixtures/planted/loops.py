"""Synthetic programs with known complexity, for curve fitting.

`CLAUDE.md` requires the lab bench be tested "against synthetic programs with
known complexity". These are those programs. Their complexity is known by
construction rather than by measurement, so a curve fitter is correct exactly
when it recovers the exponent stated in each docstring.

Every function counts its own inner operations. A curve fitter validated against
wall-clock time on these would be measuring the machine as much as the
algorithm — S-0.4 found timings drifting 12% between runs minutes apart — so
each returns an exact operation count, which is deterministic and reproducible.
Timing-based fitting is a separate, noisier problem and belongs in an
integration test.
"""

from __future__ import annotations

from typing import Any


def constant_lookup(items: list[int]) -> int:
    """**O(1).** Operation count is 1 regardless of input size."""
    if not items:
        return 0
    _ = items[0]
    return 1


def linear_scan(items: list[int]) -> int:
    """**O(n).** Operation count equals `len(items)`.

    The control for `quadratic_pairs` — same inputs, same shape of answer.
    """
    operations = 0
    for _ in items:
        operations += 1
    return operations


def quadratic_pairs(items: list[int]) -> int:
    """**DEFECT — O(n²).** Operation count is exactly `len(items) ** 2`.

    The nested-loop defect the backlog names. Doubling the input quadruples the
    work, which is the signature a curve fitter must recover: an exponent of 2,
    not "slow".
    """
    operations = 0
    for _ in items:
        for _ in items:
            operations += 1
    return operations


def quadratic_membership(items: list[int]) -> int:
    """**DEFECT — O(n²) hidden behind a list membership test.**

    Operation count is `n(n-1)/2` for distinct inputs: item *i* scans the *i*
    items already seen, and with no duplicates the inner loop never breaks
    early. Still quadratic, with half the constant of `quadratic_pairs`.

    Included because this is how a quadratic actually appears in real code — as
    `if x in seen` where `seen` is a list — rather than as an obvious nested
    loop. A detector that finds only literal nested loops misses it.

    The ratio between successive doublings approaches 4 **from above**
    (4.22, 4.11, 4.05, …) rather than landing on it, because the `-1` matters at
    small n. A curve fitter keying on an exact ratio rather than a fitted
    exponent gets this wrong, which is the property that makes this worth
    planting separately from `quadratic_pairs`.
    """
    seen: list[int] = []
    operations = 0
    for item in items:
        for existing in seen:
            operations += 1
            if existing == item:
                break
        seen.append(item)
    return operations


def linearithmic_sort(items: list[int]) -> list[int]:
    """**CONTROL — O(n log n).** Correct and not a defect.

    A curve fitter that classifies anything above linear as quadratic will
    misreport this. Its presence is what makes the "recovers the exponent"
    assertion meaningful rather than a two-way guess.
    """
    return sorted(items)


def make_items(n: int) -> list[int]:
    """Deterministic input of a given size. No randomness anywhere."""
    return list(range(n))


def measure_growth(function: Any, sizes: tuple[int, ...]) -> dict[int, int]:
    """Run one of the above at several sizes and return {size: operations}.

    The shape a curve fitter consumes. Kept here so the fixture supplies both
    the subject and the ground truth in one place.
    """
    return {size: function(make_items(size)) for size in sizes}

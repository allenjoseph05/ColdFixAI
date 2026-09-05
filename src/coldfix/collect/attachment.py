"""A zero from an instrument that never attached is not a measurement.

S-18.5. This is the failure the project exists to refuse, and it was reproduced
on the first thing tried on 2026-09-05: OpenTelemetry's sqlite3 instrumentation
loaded, registered itself, and emitted **zero spans** for a program issuing three
thousand queries -- because it wraps `cursor.execute` and never sees the
`connection.execute` shortcut, which creates its cursor down in C.

A system reading that number reports *"no database activity"* about a program
hammering a database. Nothing downstream can recover from it: the ratio is wrong,
the ranking is wrong, and the run ends by confidently finding nothing.

**The witness for a false zero is a declared expectation, not the zero itself.**
The corpus corrected this before a line of the guard was written. Stated as *"a
zero span count is a failed measurement"* it fired on six of eight subjects --
every one of them a program with no database, correctly emitting no spans.
Absence is the right answer when nothing said otherwise. It is only wrong when
something did.

**Expectations are declared, never inferred.** The harness does not read a
manifest and decide a project uses Postgres; that is a catalogue, and catalogues
are what v3 exists to remove. What creates an expectation is something *observed*
-- an import seen while grounding, or a count the program printed about itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel

from coldfix.collect.usage import MeasurementError

AGREEMENT = 0.8
"""A count within this share of what the program said about itself is attached.
Below it, some call path is invisible to the instrument, and a ratio built on a
number that is 60% of the truth is worse than no ratio at all."""


class Basis(StrEnum):
    """Where an expectation came from. Recorded so a reader can weigh it."""

    OBSERVED_IMPORT = "observed_import"
    """Grounding saw the subject import something that produces this metric."""

    REPORTED_BY_SUBJECT = "reported_by_subject"
    """The program printed a count of its own, and the instrument must agree."""

    COMPANION_METRIC = "companion_metric"
    """Another measurement moved in a way this one cannot have stayed at zero for."""


class Expectation(BaseModel, frozen=True):
    """Something that would disagree with this metric reading zero."""

    metric: str
    basis: Basis
    witness: str
    at_least: int = 1


class InstrumentNotAttachedError(MeasurementError):
    """The metric read zero and something said it should not have."""

    def __init__(self, expectation: Expectation, measured: int) -> None:
        super().__init__(
            f"{expectation.metric} measured {measured}, but {expectation.witness}. "
            "The instrument is not attached to how this program does that work -- "
            "this is a failed measurement, not a finding that the work does not happen. "
            "Nothing may be concluded about this dimension."
        )
        self.metric = expectation.metric
        self.expectation = expectation


class InstrumentPartiallyAttachedError(MeasurementError):
    """The metric moved, but disagrees with what the program says about itself.

    Strict on purpose. A query count that is 60% of the truth still produces a
    ratio, a ranking and a finding, all of them quietly wrong. A number nobody
    can reconcile is worse than a number nobody has.
    """

    def __init__(self, expectation: Expectation, measured: int) -> None:
        super().__init__(
            f"{expectation.metric} measured {measured}, but {expectation.witness}. "
            f"That is below the {AGREEMENT:.0%} agreement floor, so some call path is "
            "invisible to the instrument and any ratio built on this would be wrong."
        )
        self.metric = expectation.metric
        self.measured = measured


def verify_attached(counts: Mapping[str, int], expectations: Sequence[Expectation]) -> None:
    """Raise if any expected metric reads zero, or disagrees with its witness.

    **No expectation means no check.** A program nothing said anything about is
    a program whose zeros are correct, and that is the common case.
    """
    for expectation in expectations:
        measured = counts.get(expectation.metric, 0)
        if measured <= 0:
            raise InstrumentNotAttachedError(expectation, measured)
        if measured < expectation.at_least * AGREEMENT:
            raise InstrumentPartiallyAttachedError(expectation, measured)


_REPORTED = re.compile(r"\b([a-z][a-z0-9_.]*)=(\d+)\b")


def reported_by_subject(stdout: str) -> dict[str, int]:
    """Counts the program printed about itself, as `name=123` pairs.

    The strongest witness available, because it comes from inside the program
    and the instrument comes from outside. When the two disagree, the instrument
    is what is wrong.
    """
    return {name: int(value) for name, value in _REPORTED.findall(stdout)}


def expect_from_report(stdout: str, *, metric: str, reported_as: str) -> Expectation | None:
    """Turn a self-reported count into an expectation for a measured metric.

    Returns `None` when the program said nothing -- silence is not a claim that
    the work happens, so it creates no expectation and no failure.
    """
    counts = reported_by_subject(stdout)
    if reported_as not in counts:
        return None
    stated = counts[reported_as]
    if stated <= 0:
        return None
    return Expectation(
        metric=metric,
        basis=Basis.REPORTED_BY_SUBJECT,
        witness=f"the program reported {reported_as}={stated}",
        at_least=stated,
    )

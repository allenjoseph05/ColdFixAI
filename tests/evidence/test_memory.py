"""S-29.1 — what a rewind must not discard. ADR 186.

The journal itself is Postgres and its tests skip without Docker. What is
asserted here is the policy: the round trip, what is recorded, and that a
malformed row is refused by name rather than surfacing as a validation error
from three layers down.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from pydantic import JsonValue

from coldfix.evidence.memory import (
    RememberedEntryError,
    as_entry,
    from_entry,
    recall,
    record_all,
    remember,
)
from coldfix.evidence.repair import Candidate, Scored


class Journal:
    """A `Remembers` that keeps entries in memory, oldest first."""

    def __init__(self) -> None:
        self.entries: dict[str, list[Mapping[str, JsonValue]]] = {}

    def remember(self, finding: str, entry: Mapping[str, JsonValue]) -> None:
        self.entries.setdefault(finding, []).append(entry)

    def recalled(self, finding: str) -> Sequence[Mapping[str, JsonValue]]:
        return tuple(self.entries.get(finding, ()))


def scored(approach: str, *, wall: float = 9.0, tests_pass: bool = True) -> Scored:
    return Scored(
        candidate=Candidate(
            identifier=f"c-{approach}", approach=approach, diff=f"--- a\n+++ b\n@@\n+{approach}\n"
        ),
        measurement_id=f"m-{approach}",
        wall_s=wall,
        peak_rss_bytes=1024,
        outputs_match=True,
        tests_pass=tests_pass,
    )


def test_a_candidate_survives_the_round_trip_whole() -> None:
    """The diff above all: the archive's repeat check compares edits, and a
    memory holding only the approach label would feed it the one field F12 says
    a model can rename freely."""
    original = scored("prefetch")
    restored = from_entry(as_entry(original))

    assert restored == original
    assert restored.candidate.diff == original.candidate.diff


def test_what_was_measured_comes_back_oldest_first() -> None:
    """The journal's order is the thing being preserved: what was learned, and
    in what order."""
    journal = Journal()
    remember(journal, "f-1", scored("first"))
    remember(journal, "f-1", scored("second"))

    assert [item.candidate.approach for item in recall(journal, "f-1")] == ["first", "second"]


def test_one_finding_does_not_recall_another_s_failures() -> None:
    """A repair working on the N+1 in the book list has no use for what failed
    on an unrelated slow import."""
    journal = Journal()
    remember(journal, "f-1", scored("prefetch"))

    assert recall(journal, "f-2") == ()


def test_the_winner_is_recorded_too() -> None:
    """The patch audit can send a patch back after the Adversary breaks it, and a
    candidate that passed its own test and failed the audit is exactly the one
    the next round must not propose again."""
    journal = Journal()
    written = record_all(journal, "f-1", [scored("lost", wall=9.9), scored("won", wall=0.5)])

    assert written == 2
    assert [item.candidate.approach for item in recall(journal, "f-1")] == ["lost", "won"]


def row(**changes: JsonValue) -> dict[str, JsonValue]:
    """A well-formed journal row, with one thing done to it.

    Built by removing or replacing a field of a complete entry rather than by
    listing a few keys: since S-30.1 made `outputs_match` and `tests_pass`
    required, a sparse literal is refused for *those* whatever else is wrong with
    it, and each case below would pass while testing something it does not name.
    """
    entry = dict(as_entry(scored("prefetch")))
    for name, value in changes.items():
        if value is None:
            del entry[name]
        else:
            entry[name] = value
    return entry


def test_a_complete_row_is_accepted() -> None:
    """The positive control for the refusals below. Without it they would all
    still pass against a `from_entry` that refused everything."""
    assert from_entry(row()).candidate.approach == "prefetch"


@pytest.mark.parametrize(
    ("entry", "because"),
    [
        ({}, "nothing at all"),
        (row(candidate=None), "no candidate"),
        (row(measurement_id=None), "no measurement cited"),
        (row(candidate="not a candidate"), "a candidate that is not one"),
        (row(tests_pass=None), "no record of whether the suite passed"),
        (row(outputs_match=None), "no record of whether the output held"),
    ],
)
def test_a_row_this_system_did_not_write_is_refused_by_name(
    entry: dict[str, JsonValue], because: str
) -> None:
    """The caller's question is *can I trust this memory*, and a validation error
    from three layers down answers a narrower one.

    The last two are S-30.1: a row written before the observations were required
    is not a row this system would write now, and reading it back would restore a
    candidate claiming a check nobody ran.
    """
    assert because
    with pytest.raises(RememberedEntryError, match="not a candidate this system measured"):
        from_entry(entry)

"""What was tried for a finding and did not work, where a rewind cannot reach it.

S-29.1, ADR 186. S-28.5 seeded the Optimizer's archive from `state.candidates` so
a second search does not re-propose the candidate that just lost. That channel is
checkpointed, and a rewind restores the state at checkpoint T while the reason for
rewinding was discovered at T+n -- so the restore puts back exactly the ignorance
that caused it. `08-audit.md` F5: *we want to rewind the code and keep the
learning.*

**The seam is defined here, and the store is bound at the composition root.**
`state/persistent.py` imports a database driver at module scope, and this package
has to stay importable in a container with nothing installed. So `Remembers` is
two methods; `cli/scan.py` adapts the real journal to them.

**The whole candidate is stored, never the approach label.** F12 is that the
label is the one part a model can rename while changing nothing, and the archive's
repeat check compares edits. A memory holding labels would feed that check the one
field it cannot trust.

**Nothing here decides what to do about a repeat.** `repair.search` owns that,
through `Archive.already_tried` and the edit comparison; this owns only *what was
tried*. Two answers to one question disagree the first time either moves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import JsonValue, ValidationError

from coldfix.evidence.repair import Scored


class RememberedEntryError(Exception):
    """A remembered candidate could not be written or read back."""


class Remembers(Protocol):
    """Somewhere a checkpoint restore cannot reach.

    Two methods rather than the store's own, so this package depends on an
    interface it defines rather than on a driver it cannot install.
    """

    def remember(self, finding: str, entry: Mapping[str, JsonValue]) -> None: ...

    def recalled(self, finding: str) -> Sequence[Mapping[str, JsonValue]]: ...


def as_entry(scored: Scored) -> Mapping[str, JsonValue]:
    """One measured candidate, as the journal stores it.

    The whole `Scored`: the candidate with its diff, and what running it showed.
    A journal that kept the verdict without the diff would remember that
    something failed and not what it was.
    """
    dumped = scored.model_dump(mode="json")
    return dict(dumped)


def from_entry(entry: Mapping[str, JsonValue]) -> Scored:
    """Read one back.

    Raises:
        RememberedEntryError: the entry is not one of ours -- a row written by an older
            shape, or by something else sharing the collection. Named rather than
            surfaced as a `ValidationError`, because the caller's question is
            *can I trust this memory* and a validation error answers a narrower
            one.
    """
    try:
        return Scored.model_validate(entry)
    except ValidationError as error:
        message = (
            f"this entry is not a candidate this system measured: {error.errors()[0]['msg']}. "
            f"It holds {sorted(entry)}, and a measured candidate needs the candidate itself and "
            "what running it showed"
        )
        raise RememberedEntryError(message) from error


def remember(store: Remembers, finding: str, scored: Scored) -> None:
    """Record that this candidate was measured for this finding.

    Append-only, and that is the store's guarantee rather than this module's:
    a superseded lesson and a current one are both part of what was learned.
    """
    store.remember(finding, as_entry(scored))


def record_all(store: Remembers, finding: str, measured: Sequence[Scored]) -> int:
    """Record every candidate one search measured. Returns how many were written.

    **The winner is recorded too.** The patch audit can send a patch back after
    the Adversary breaks it, and a candidate that passed its own test and failed
    the audit is exactly the one the next round must not propose again.
    """
    for scored in measured:
        remember(store, finding, scored)
    return len(measured)


def recall(store: Remembers, finding: str) -> tuple[Scored, ...]:
    """Everything measured for this finding, oldest first.

    Oldest first because the journal's order is the thing being preserved: what
    was learned, and in what order.
    """
    return tuple(from_entry(entry) for entry in store.recalled(finding))

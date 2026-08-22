"""What was tried for a finding and did not work, in the half a rewind cannot reach.

Epic 13, S-13.3. `08-audit.md` F5 names failure memory as one of the four things
that must outlive a rewind, and S-6.2 built the store it lives in. S-12.6 built
the seam that puts it back in front of the Surgeon — `repair`'s `remembered` — and
left it a parameter nothing filled. **Until this module, a rewound run repeated
the approach that had already failed**, which is the defect F5 exists to prevent
and which S-12.6's control test held open on purpose.

**Keyed per finding, because that is the question being asked.** `PersistentStore`
refuses an unkeyed entry in as many words — *the ledger is read per project shape,
playbooks per fingerprint, and failure memory per finding* — and a Surgeon working
on the N+1 in the book list has no use for what failed on an unrelated slow
import.

**The whole attempt is stored, not the approach string.** S-10.4 first showed
retries only the previous `approach` labels, which is precisely the self-judged
field F12 says an agent can rename; S-10.5's repeat check compares **diffs**, so a
memory that kept only labels would feed the check the one thing it cannot trust.
The diff is what makes a remembered attempt useful.

**Nothing here decides what to do about a repeat.** `retry.repeats` owns that, and
this module owns only *what was tried*. Two answers to *is this a repeat* would
disagree the first time either moved — which is the failure this project has now
found at six consecutive epic joins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue, ValidationError

from coldfix.repair.patch import Attempt, Patch, PatchError
from coldfix.state.persistent import Collection, Entry, PersistentStore


class FailureMemoryError(Exception):
    """A recorded failure could not be written or read back."""


def as_entry(attempt: Attempt) -> Mapping[str, JsonValue]:
    """One attempt, as the journal stores it.

    Both halves, because `Attempt` refuses one without the other: *an attempt
    recorded with no failure reason gives the next one nothing to avoid.* An
    entry that dropped the reason would round-trip into a constructor that
    rejects it, which is the right failure and a late one.
    """
    return {"patch": attempt.patch.model_dump(mode="json"), "failure": attempt.failure}


def from_entry(entry: Mapping[str, JsonValue]) -> Attempt:
    """Read one back.

    Raises:
        FailureMemoryError: the entry is not one of ours — a row written by an
            older shape, or by something else using the same collection. Named
            rather than allowed to surface as a `ValidationError`, because the
            caller's question is *can I trust this memory* and a pydantic error
            answers a narrower one.
    """
    try:
        return Attempt(
            patch=Patch.model_validate(entry["patch"]),
            failure=str(entry["failure"]),
        )
    except (KeyError, TypeError, ValidationError, PatchError) as error:
        message = (
            f"this failure-memory entry is not an attempt this system wrote: {error}. It holds "
            f"{sorted(entry)}, and an attempt needs a patch and the reason it failed"
        )
        raise FailureMemoryError(message) from error


def remember(store: PersistentStore, finding: str, attempt: Attempt) -> Entry:
    """Record that this approach was tried for this finding and did not work.

    **Append-only, and that is the store's guarantee rather than this module's.**
    Nothing here updates or removes: a superseded lesson and a current one are
    both part of *what was learned and in what order*, which is what `read`
    preserves by returning oldest first.
    """
    return store.append(Collection.FAILURE_MEMORY, key=finding, entry=as_entry(attempt))


def recall(store: PersistentStore, finding: str) -> tuple[Attempt, ...]:
    """Everything tried for this finding, oldest first. **AC 2's input.**

    Oldest first because the journal's order is the thing being preserved, and
    because `repair` puts these in front of the Surgeon as *prior attempts* — a
    list that led with the most recent would present the newest idea as the one
    to move away from first.
    """
    return tuple(from_entry(item.entry) for item in store.read(Collection.FAILURE_MEMORY, finding))


def record_all(store: PersistentStore, finding: str, attempts: Sequence[Attempt]) -> int:
    """Record every attempt of one repair. Returns how many were written.

    A helper because the caller is a node that has just finished a repair and
    holds all of them, and a loop at that call site is a place to forget the last
    one. **The successful attempt is recorded too** — S-11.7 can send a patch back
    after the Adversary breaks it, and an approach that passed its own test and
    failed the audit is exactly the kind the next attempt must not re-propose.
    """
    for attempt in attempts:
        remember(store, finding, attempt)
    return len(attempts)

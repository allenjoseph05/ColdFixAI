"""What has been learned about grounding projects of a kind.

Epic 13, S-13.1. Three places in this codebase say *S-13.1 decides what an entry
means* and then decline to guess: `persistent.py` stores `(collection, key,
entry)` and leaves the columns to this epic, `auth.PlaybookLookup` returns
`Mapping`s and reads inside none of them, and `Resolution` carries what it was
given **unread**. This module is the schema those three deferred to.

**The key already existed and is not re-derived here.** S-7.1's
`Fingerprint.playbook_key()` is *framework and major version* — a playbook
learned against Django 5.0 applies to 5.0.3, and keying on the full version would
make every patch release a cold start. `playbook_from_store` already reads by it.
What was missing is only what an entry *is*.

**Situation, action, outcome — the three the criterion names and no fourth.**
The temptation is a `worked: bool`, and it is exactly the field S-13.2 owns:
*new entries are provisional and carry success/failure counters, promotion
requires N successes across different projects, two failures demote and
quarantine.* A boolean here would be that judgement made one story early and
without the counters that justify it, which is the failure F15 describes for the
trust ledger — autonomy inferred from a tally nobody scoped.

**Nothing believes an entry yet, and that is deliberate rather than unfinished.**
`resolve_auth` consults the playbook *before* probing and carries the result
without reading it; S-13.2 decides when one may be acted on. So the safety
boundary this epic needs is already drawn in code, and this module stays on the
read-only side of it: **there is no production writer.** A store that recorded
what worked before anything could gate it is precisely *a wrong entry propagates
silently to all future runs and compounds*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from coldfix.state.persistent import Collection, Entry, PersistentStore


class PlaybookError(Exception):
    """An entry could not be written or read back."""


class PlaybookEntry(BaseModel):
    """One thing learned about grounding projects of a kind. **AC 2.**

    Frozen and `extra="forbid"`. Forbidding extras is what keeps S-13.2's fields
    from arriving early by accident: an entry carrying a `worked` flag would be
    refused here rather than quietly stored and later believed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    situation: str = Field(min_length=1)
    """What was true of the project when this applied. The half that decides
    whether the entry is relevant at all — an action with no situation is advice
    to do something unconditionally, which is not what a playbook is."""

    action: str = Field(min_length=1)
    """What was done."""

    outcome: str = Field(min_length=1)
    """What happened. **Not whether it was good**: `resolve_auth` carries entries
    unread and S-13.2 decides when one may be believed, so a verdict recorded
    here would be a judgement made a story early."""

    @field_validator("situation", "action", "outcome")
    @classmethod
    def _substantive(cls, value: str) -> str:
        """**Whitespace is not content, and `min_length` does not know that.**

        A single space satisfies `min_length=1` and says nothing, which is the
        same hole `Implicated.reason` and `FalsificationTest` close the same way.
        An entry whose action is a space is advice nobody can follow, filed where
        the next run will read it.
        """
        if not value.strip():
            message = "a playbook entry needs a situation, an action and an outcome with words in"
            raise ValueError(message)
        return value

    def describe(self) -> str:
        return f"when {self.situation}: {self.action} — {self.outcome}"


def as_entry(entry: PlaybookEntry) -> Mapping[str, str]:
    return dict(entry.model_dump(mode="json"))


def from_entry(entry: Mapping[str, object]) -> PlaybookEntry:
    """Read one back.

    Raises:
        PlaybookError: the mapping is not a playbook entry — a row from an older
            shape, or something else filed under the same collection. Named
            rather than surfacing as a `ValidationError`, because the caller's
            question is *can I use this* and a pydantic error answers a narrower
            one.
    """
    try:
        return PlaybookEntry.model_validate(entry)
    except ValidationError as error:
        message = (
            f"this playbook entry is not one this system wrote: {error.errors()[0]['msg']}. It "
            f"holds {sorted(entry)}, and an entry is a situation, an action and an outcome"
        )
        raise PlaybookError(message) from error


def recall(store: PersistentStore, key: str) -> tuple[PlaybookEntry, ...]:
    """Everything filed under this fingerprint key, oldest first.

    Oldest first because the journal's order is what is being preserved — *what
    was learned and in what order* — and a reader sorted by recency would present
    a superseded lesson and a current one alike.
    """
    return tuple(from_entry(item.entry) for item in store.read(Collection.PLAYBOOKS, key))


def record(store: PersistentStore, key: str, entry: PlaybookEntry) -> Entry:
    """File one under a fingerprint key.

    **Nothing in `src/` calls this, and that is the story boundary rather than an
    oversight.** S-13.2 is marked SAFETY because *a wrong entry propagates
    silently to all future runs and compounds*, and it is the story that makes an
    entry provisional, counts its successes across different projects, and
    demotes it after two failures. A production writer before that gate exists
    would be the propagation with nothing to stop it.

    So this is the way in that S-13.2 will gate, and the only callers today are
    its tests.
    """
    return store.append(Collection.PLAYBOOKS, key=key, entry=as_entry(entry))


def describe_all(entries: Sequence[PlaybookEntry]) -> str:
    """What the Explorer is shown. **AC 3's rendering.**

    Says how many there are even when there are none, because `no_playbook`
    exists so that *consulted and empty* and *not consulted* are different call
    sites — and S-13.5 measures whether the tenth project of a kind grounds
    faster than the first, which a silent non-consult would make meaningless.
    """
    if not entries:
        return "playbook: nothing learned about projects of this kind yet"
    lines = [f"playbook: {len(entries)} entry(s) for projects of this kind"]
    lines.extend(f"  {item.describe()}" for item in entries)
    return "\n".join(lines)

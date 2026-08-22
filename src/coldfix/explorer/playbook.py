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

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from coldfix.state.persistent import (
    Collection,
    Entry,
    PersistentStore,
    PersistentStoreError,
)


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

    def digest(self) -> str:
        """A stable identity for this entry, so a use can name which one it was.

        Canonical JSON — sorted keys, fixed separators — so the digest is a
        property of the entry rather than of how the object was assembled, which
        is `Experiment.digest`'s construction and for the same reason: two
        processes that recorded the same entry must agree about it.
        """
        rendered = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode()).hexdigest()[:16]

    def describe(self) -> str:
        return f"when {self.situation}: {self.action} — {self.outcome}"


ENTRY = "entry"
USE = "use"
"""What kind of record a journal row is.

**The row and the thing are two schemas, and this is the seam between them.**
`PlaybookEntry` stays the three fields S-13.1's criterion names — adding a fourth
is the mistake that story refused — while the *row* carries a discriminator,
because S-13.2 files uses under the same key and a reader has to tell them apart.
Explicit rather than inferred from shape: a row with neither tag is a corrupt
record and must raise, and a row tagged `use` is a different record and must be
skipped. Guessing from which fields happen to be present would collapse those.
"""


def as_entry(entry: PlaybookEntry) -> Mapping[str, str]:
    return {"kind": ENTRY, **entry.model_dump(mode="json")}


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
        return PlaybookEntry.model_validate(
            {name: value for name, value in entry.items() if name != "kind"}
        )
    except ValidationError as error:
        message = (
            f"this playbook entry is not one this system wrote: {error.errors()[0]['msg']}. It "
            f"holds {sorted(entry)}, and an entry is a situation, an action and an outcome"
        )
        raise PlaybookError(message) from error


def recall(store: PersistentStore, key: str) -> tuple[PlaybookEntry, ...]:
    """Every entry filed under this fingerprint key, oldest first.

    Oldest first because the journal's order is what is being preserved — *what
    was learned and in what order* — and a reader sorted by recency would present
    a superseded lesson and a current one alike.

    **Uses are skipped and corrupt rows are not.** A row tagged `use` is a record
    this module wrote for a different purpose; a row tagged neither is one nobody
    can account for, and `from_entry` says so. Collapsing the two would let a
    malformed entry disappear as quietly as a use.
    """
    return tuple(
        from_entry(item.entry)
        for item in store.read(Collection.PLAYBOOKS, key)
        if item.entry.get("kind") != USE
    )


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


# ============================================================ S-13.2: what may be believed

PROMOTION_THRESHOLD = 3
"""Successful uses on **distinct** projects before an entry may be trusted.

F4 says *N successful uses across different projects* and does not fix N, so this
is a decision rather than a transcription. Two is the smallest number for which
*different projects* means anything at all, and is therefore the weakest reading
that satisfies the words; three is the smallest that survives one coincidence —
two projects sharing a wrong belief is an ordinary thing when both are built from
the same tutorial.

**It must also exceed the demotion threshold**, or an entry with two successes
and two failures is simultaneously promotable and quarantined. Trust being
strictly harder to reach than quarantine is the asymmetry a safety property
wants.
"""

DEMOTION_THRESHOLD = 2
"""Failures after which an entry is quarantined. F4's *fails twice*, verbatim."""


class Status(StrEnum):
    """What may be done with an entry. **F4's three states.**"""

    PROVISIONAL = "provisional: written but not yet earned, and not to be acted on"
    TRUSTED = "trusted: it worked on enough different projects"
    QUARANTINED = "quarantined: it failed twice and is not offered again"


@dataclass(frozen=True)
class Standing:
    """One entry and everything recorded about how it has gone.

    **The counters are derived, and F4 assumes otherwise.** It says entries
    *carry a use counter with success and failure tallies* — but the journal is
    append-only at the database level, where a trigger refuses `UPDATE`, `DELETE`
    and `TRUNCATE`. A counter on the entry could never be incremented. So a use
    is its own appended row and the tally is a fold over them, which is a better
    answer than the one the audit imagined: **the evidence for a promotion is
    itself append-only**, so nothing can quietly raise a count.
    """

    entry: PlaybookEntry
    succeeded_on: frozenset[str]
    failures: int

    @property
    def status(self) -> Status:
        """**Quarantine is checked first, and the order is the safety property.**

        An entry that failed twice is quarantined however many successes it also
        has: F4's remedy for a poisoned entry is that it stops being offered, and
        a rule that let successes outvote failures would let a widely-repeated
        mistake earn its way back.
        """
        if self.failures >= DEMOTION_THRESHOLD:
            return Status.QUARANTINED
        if len(self.succeeded_on) >= PROMOTION_THRESHOLD:
            return Status.TRUSTED
        return Status.PROVISIONAL

    @property
    def trusted(self) -> bool:
        return self.status is Status.TRUSTED

    def describe(self) -> str:
        return (
            f"{self.status.value}\n  {self.entry.describe()}\n"
            f"  worked on {len(self.succeeded_on)} project(s), failed {self.failures} time(s)"
        )


def note_use(
    store: PersistentStore, key: str, entry: PlaybookEntry, *, project: str, worked: bool
) -> Entry:
    """Record that this entry was used on this project, and how it went.

    `project` is what makes promotion mean *across different projects* rather
    than *often*. Fifty successes on one project is one project's opinion, which
    is exactly F15's finding about the trust ledger — trust learned elsewhere is
    context, not authority — reached from the playbook side.

    Raises:
        PersistentStoreError: an empty project, which would make a use
            unattributable and let one project promote an entry by itself.
    """
    if not project.strip():
        message = (
            "a use needs the project it was used on. Promotion is *across different projects*, "
            "and an unattributed use is one that could have come from the same project every "
            "time — which is the tally F15 says is not authority"
        )
        raise PersistentStoreError(message)

    return store.append(
        Collection.PLAYBOOKS,
        key=key,
        entry={"kind": USE, "of": entry.digest(), "project": project, "worked": worked},
    )


def standings(store: PersistentStore, key: str) -> tuple[Standing, ...]:
    """Every entry under this key with what has been recorded about it.

    **Fingerprint-scoped, never global**, which is F4's fourth point and comes
    free from the key: `store.read` takes one, and there is no call here that
    reads the collection whole. An entry learned about Django 5 is not offered to
    a Flask project because nothing asks for it under that key.
    """
    rows = store.read(Collection.PLAYBOOKS, key)
    entries = [from_entry(row.entry) for row in rows if row.entry.get("kind") != USE]
    uses = [row.entry for row in rows if row.entry.get("kind") == USE]

    return tuple(
        Standing(
            entry=item,
            succeeded_on=frozenset(
                str(use["project"])
                for use in uses
                if use.get("of") == item.digest() and use.get("worked")
            ),
            failures=sum(
                1 for use in uses if use.get("of") == item.digest() and not use.get("worked")
            ),
        )
        for item in entries
    )


def trusted(store: PersistentStore, key: str) -> tuple[PlaybookEntry, ...]:
    """Only the entries that have earned it. **AC 5's protection.**

    A caller acting on a playbook asks this rather than `recall`. A provisional
    entry is still *readable* — the Explorer may be shown it as context, and
    `resolve_auth` carries entries unread for exactly that reason — but nothing
    that acts on one should be reading a list that contains it.

    So a wrong entry learned on one project reaches a different project as
    something nobody may act on until two more projects have agreed, and a
    quarantined one does not reach it at all.
    """
    return tuple(item.entry for item in standings(store, key) if item.trusted)

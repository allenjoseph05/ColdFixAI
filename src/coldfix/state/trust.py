"""Autonomy earned per fix category and project shape, and never transferred.

Epic 13, S-13.4. `08-audit.md` F15:

> A `select_related` fix approved 50 times may have been on projects with narrow
> tables. Applied to a project with a wide parent table, it trades queries for
> enormous payloads.
>
> **Fix:** ledger keys include project shape characteristics, not just fix
> category. A new project starts at level 0 for every category until it has its
> own history, with cross-project history shown as advisory context rather than
> as earned autonomy.

**The shape is measured, not declared.** F15's worry is about payload per row, and
`RESPONSE_BYTES` is a metric the harness already takes at every scale point — so
*wide parent table* is a number this system has rather than a label somebody
applies. `payload_magnitude` is its order of magnitude, which avoids inventing a
threshold: nobody has to decide where *wide* begins, and trust learned at a
hundred bytes a row does not reach a project at ten thousand.

**Level is derived, for S-13.2's reason.** The journal refuses `UPDATE` at the
database, so a level cannot be a field anybody increments — it is a fold over
appended outcomes, and the evidence for an autonomy grant is therefore as
immutable as the grant. ADR 136 records why that is better than the counter
`08-audit.md` imagined.

**Nothing here reaches up.** `state` imports only `cost`, `replay` and `sandbox`,
and a ledger that pulled in a `Fingerprint` and a `Workload` to build its own key
would invert that. It takes the measured values instead, from whichever caller
already holds both.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from coldfix.state.persistent import Collection, Entry, PersistentStore, PersistentStoreError

ACCEPTED_PER_LEVEL = 3
"""Clean outcomes on *this* project before its level rises by one.

F15 fixes the demotion size — *any revert or rejection demotes one level* — and
says nothing about promotion, so this is a decision. Three, for S-13.2's reason:
it is the smallest number that survives one coincidence. It also means level 2,
the most autonomy this ledger grants, costs six clean fixes in one category on
one project shape — which is the right price for the thing F15 says was being
given away.
"""

MAX_LEVEL = 2


class Level(IntEnum):
    """The three levels S-13.4's first criterion names.

    An `IntEnum` because *demotes one level* is arithmetic, and a `StrEnum` would
    make the one operation the criterion specifies into a lookup table.
    """

    GATED = 0
    """Every fix is reviewed. **Where every project starts**, and where it stays
    until it has its own history — cross-project agreement does not move it."""

    FAMILIAR = 1
    TRUSTED = 2


class Outcome(StrEnum):
    """What became of one proposed fix. F15's vocabulary."""

    ACCEPTED = "shipped and stood"
    REJECTED = "a human declined it"
    REVERTED = "it shipped and was taken back"

    @property
    def demotes(self) -> bool:
        """*Any revert or rejection demotes one level* — the two together, and
        deliberately not `not ACCEPTED`, so a fourth outcome added later has to
        state which it is rather than inheriting an answer."""
        return self in {Outcome.REJECTED, Outcome.REVERTED}


@dataclass(frozen=True)
class Shape:
    """The project characteristics a trust key includes. **F15's whole fix.**

    Three facts, each of which changes whether a fix that worked elsewhere works
    here: what the ORM is, what the database is, and how much payload a row
    carries. The third is the one F15 names, and it is the one nobody had.
    """

    orm: str
    database: str
    payload_magnitude: int
    """`floor(log10(bytes per unit of scale))`. **An order of magnitude rather
    than a band**, so that no threshold has to be argued for: the question is not
    *is this table wide* but *is this the same kind of project*, and a tenfold
    difference in payload is a different kind."""

    def key(self) -> str:
        return f"{self.orm}/{self.database}/1e{self.payload_magnitude}"


def payload_magnitude(observations: Sequence[Mapping[str, float]], *, metric: str) -> int:
    """The order of magnitude of payload per unit of scale, from what was measured.

    Takes the largest scale point, because that is where F15's failure shows: a
    wide parent table at ten rows looks like a narrow one, and the trade the
    fix makes only becomes enormous at volume.

    Raises:
        PersistentStoreError: nothing was measured, or nothing at a positive
            scale. A shape derived from no measurement would be a label, and the
            whole point of F15's fix is that it is not one.
    """
    usable = [item for item in observations if item.get("scale", 0) > 0 and item.get(metric, 0) > 0]
    if not usable:
        message = (
            f"no observation carries a positive {metric!r} at a positive scale, so there is "
            "nothing to derive a project shape from. F15's fix is that the key includes a "
            "*measured* characteristic; a shape guessed here would be the label it replaces"
        )
        raise PersistentStoreError(message)

    widest = max(usable, key=lambda item: item["scale"])
    return math.floor(math.log10(widest[metric] / widest["scale"]))


def ledger_key(category: str, shape: Shape) -> str:
    """What a level is filed under. **Category *and* shape, which is F15's point.**

    Raises:
        PersistentStoreError: an empty category, which would file every fix
            together and make the ledger say *this project is trusted* rather
            than *this project is trusted for this kind of change*.
    """
    if not category.strip():
        message = (
            "a trust entry needs the fix category it was earned in. Filing every kind of change "
            "under one level is the autonomy F15 says was being transferred unsafely, one axis over"
        )
        raise PersistentStoreError(message)
    return f"{category.strip()}@{shape.key()}"


@dataclass(frozen=True)
class Standing:
    """What one project has earned here, and what everyone else has recorded.

    The two are separate fields and never added together, which is the whole of
    F15's second sentence: *cross-project history shown as advisory context
    rather than as earned autonomy.*
    """

    project: str
    accepted: int
    demotions: int
    elsewhere: Mapping[str, int]
    """Accepted counts from **other** projects at this key. Advisory. Nothing in
    `level` reads it, and a test asserts that changing it changes nothing."""

    @property
    def level(self) -> Level:
        """This project's own level, from this project's own history.

        **A new project is `GATED` however much everyone else agrees**, which is
        F15's third criterion and the reason `elsewhere` is not in this sum.
        """
        earned = self.accepted // ACCEPTED_PER_LEVEL - self.demotions
        return Level(max(Level.GATED, min(MAX_LEVEL, earned)))

    def describe(self) -> str:
        others = sum(self.elsewhere.values())
        return (
            f"level {int(self.level)} ({self.level.name.lower()}) for {self.project}: "
            f"{self.accepted} accepted, {self.demotions} demotion(s)\n"
            f"  advisory: {others} accepted across {len(self.elsewhere)} other project(s), "
            "which is context and not earned autonomy"
        )


def record_outcome(store: PersistentStore, key: str, *, project: str, outcome: Outcome) -> Entry:
    """Record what became of one fix, for one project, at one key.

    Raises:
        PersistentStoreError: an empty project. A level is *this project's own
            history*, and an unattributed outcome could have come from anywhere —
            which is exactly the transfer F15 refuses.
    """
    if not project.strip():
        message = (
            "a trust outcome needs the project it happened on. A new project starts at level 0 "
            "until it has its own history, and an unattributed outcome would be somebody else's "
            "history counted as this project's"
        )
        raise PersistentStoreError(message)

    return store.append(
        Collection.TRUST_LEDGER,
        key=key,
        entry={"project": project, "outcome": outcome.name},
    )


def standing(store: PersistentStore, key: str, *, project: str) -> Standing:
    """What `project` has earned at `key`, with everyone else's record beside it."""
    rows = [item.entry for item in store.read(Collection.TRUST_LEDGER, key)]
    mine = [item for item in rows if str(item.get("project")) == project]
    others: dict[str, int] = {}
    for item in rows:
        name = str(item.get("project"))
        if name != project and str(item.get("outcome")) == Outcome.ACCEPTED.name:
            others[name] = others.get(name, 0) + 1

    return Standing(
        project=project,
        accepted=sum(1 for item in mine if str(item.get("outcome")) == Outcome.ACCEPTED.name),
        demotions=sum(
            1
            for item in mine
            if str(item.get("outcome")) in {Outcome.REJECTED.name, Outcome.REVERTED.name}
        ),
        elsewhere=others,
    )

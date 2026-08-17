"""The store a rewind must not touch.

Epic 6, S-6.2. `08-audit.md` F5 is the whole story: time travel restores the
state at checkpoint T, but the *reason* for rewinding is a failure discovered at
T+n — and that failure record lives in the state being discarded. Rewind, and the
agent repeats the attempt it rewound to avoid. **We want to rewind the code and
keep the learning.**

So there are two stores, and ADR 003 is explicit that the separation is the
decision rather than the engines: *not a second schema in the checkpoint
database — a separate store, so that dropping checkpoints (a routine operation)
cannot touch the playbook (a destructive one).*

**Append-only is enforced by the database, not by this module's method list.**
A Python class with no `update` method is append-only until somebody opens a
connection, and the whole point of this store is that it survives operations
performed *on* the system rather than through it. A trigger refuses `UPDATE`,
`DELETE` and `TRUNCATE` on the journal, which means the guarantee holds for
`psql` too. `TRUNCATE` is guarded deliberately: it is the exact verb ADR 003
names when it says dropping checkpoints must not reach the playbook.

**One journal, not four schemas.** The four things F5 lists are *what* must
survive, not four shapes this story knows. A playbook is S-13.1's artifact, the
trust ledger S-13.4's, failure memory S-13.3's — and inventing their columns here
is the guess S-5.4 declined to make when it left the checkpoint schema to S-6.1,
and that S-6.1 declined again for the experiment log. The journal stores
`(collection, key, entry)` and Epic 13 decides what an entry means.

**The replay cache is a member of this store and is not in this database.**
S-5.1 already built it as a directory of JSON recordings, partitioned by machine
(ADR 054), and two of its properties depend on that: a recording can be opened by
hand when it produced a surprising answer, which is S-5.2's whole debugging
method, and a foreign machine's recording misses rather than matching. Moving it
into Postgres to satisfy a reading of AC 1 would trade both away for tidiness.
What matters for F5 is that a checkpoint restore cannot reach it, and living on a
filesystem outside the checkpoint database satisfies that more completely than a
table would. `MEMBERS` records all four and where each one lives, so the list is
checkable rather than partly implicit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from coldfix.sandbox.production import VerifiedDatabase

JOURNAL = "coldfix_persistent"
GUARD_FUNCTION = "coldfix_refuse_rewrite"


class PersistentStoreError(Exception):
    """The persistent store could not be opened, written, or read."""


class SharedStoreError(PersistentStoreError):
    """The persistent store was pointed at the checkpoint database.

    ADR 003's decision, enforced rather than described. Sharing a database makes
    *dropping checkpoints* — a routine operation, performed with a `DROP` that
    knows nothing about this module — capable of destroying the playbook. The
    separation is what makes the routine operation safe, so it cannot be left to
    whoever writes the connection strings.
    """


class AppendOnlyViolationError(PersistentStoreError):
    """Something tried to change or remove an entry that had been written.

    Raised by the database rather than by this module, and the distinction is
    the point: a guard that lives in Python protects the callers who go through
    Python.
    """


class Collection(StrEnum):
    """What F5 says must outlive a rewind, minus the one that is not a table."""

    FAILURE_MEMORY = "failure_memory"
    """What was tried and did not work. S-13.3 gives it meaning."""

    PLAYBOOKS = "playbooks"
    """What worked, keyed by framework fingerprint. S-13.1, with S-13.2's gate."""

    TRUST_LEDGER = "trust_ledger"
    """Autonomy earned per fix category and project shape. S-13.4, with F15."""


@dataclass(frozen=True)
class Member:
    """One of the four things a rewind must not discard, and where it lives."""

    name: str
    stored_in: str
    story: str


MEMBERS: tuple[Member, ...] = (
    Member("failure_memory", f"{JOURNAL} journal", "S-13.3"),
    Member("playbooks", f"{JOURNAL} journal", "S-13.1"),
    Member("trust_ledger", f"{JOURNAL} journal", "S-13.4"),
    # Listed because AC 1 lists it, and recorded as filesystem-backed because
    # that is where ADR 054 put it and why. A member of this store either way:
    # what makes something persistent here is being unreachable from a
    # checkpoint restore, not being a row.
    Member("replay_cache", "filesystem directory (ADR 054)", "S-5.1"),
)


@dataclass(frozen=True)
class Entry:
    """One append. Immutable here because it is immutable in the database."""

    id: int
    collection: Collection
    key: str
    entry: Mapping[str, JsonValue]
    written_at: datetime


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {JOURNAL} (
    id          bigserial PRIMARY KEY,
    collection  text        NOT NULL,
    key         text        NOT NULL,
    entry       jsonb       NOT NULL,
    written_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS {JOURNAL}_lookup ON {JOURNAL} (collection, key, id);

CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger AS $guard$
BEGIN
    RAISE EXCEPTION
        'coldfix: % is append-only and % is refused. This store holds the '
        'knowledge a rewind must not discard (08-audit.md F5); an entry that '
        'could be edited or dropped is one a checkpoint restore could undo.',
        TG_TABLE_NAME, TG_OP;
END;
$guard$ LANGUAGE plpgsql;
"""

# Row-level for UPDATE and DELETE, statement-level for TRUNCATE — Postgres will
# not accept TRUNCATE on a FOR EACH ROW trigger, and TRUNCATE is the one ADR 003
# names when it says dropping checkpoints must not reach the playbook.
_TRIGGERS = f"""
DROP TRIGGER IF EXISTS {JOURNAL}_no_rewrite ON {JOURNAL};
CREATE TRIGGER {JOURNAL}_no_rewrite
    BEFORE UPDATE OR DELETE ON {JOURNAL}
    FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}();

DROP TRIGGER IF EXISTS {JOURNAL}_no_truncate ON {JOURNAL};
CREATE TRIGGER {JOURNAL}_no_truncate
    BEFORE TRUNCATE ON {JOURNAL}
    FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}();
"""


def _same_database(left: str, right: str) -> bool:
    """Whether two URLs name the same database on the same server."""
    first, second = urlsplit(left), urlsplit(right)
    return (
        (first.hostname or "").lower() == (second.hostname or "").lower()
        and first.port == second.port
        and first.path.lstrip("/") == second.path.lstrip("/")
    )


def refuse_shared_store(persistent: VerifiedDatabase, checkpoints: str | Path) -> None:
    """Refuse a persistent store that shares the checkpoint database. ADR 003.

    A `Path` is a SQLite file (ADR 003's development checkpointer) and is
    trivially a different store, so only a URL can collide.

    Raises:
        SharedStoreError: both name the same database on the same server.
    """
    if isinstance(checkpoints, Path):
        return
    if not _same_database(persistent.url, checkpoints):
        return
    message = (
        f"the persistent store and the checkpointer both point at {persistent.name!r} on "
        f"{persistent.host}. ADR 003 keeps them apart so that dropping checkpoints — which is "
        "routine, and is done with a DROP that knows nothing about this store — cannot destroy "
        "the playbook, the trust ledger, or the record of what has already been tried"
    )
    raise SharedStoreError(message)


@dataclass(frozen=True)
class PersistentStore:
    """Append-only knowledge that outlives a run, a rewind, and a crash.

    Takes a `VerifiedDatabase` rather than a URL for S-2.5's reason: the check
    is the constructor, so there is no unverified handle to hold. The guard's
    default name patterns already include `coldfix_*`, which is what this
    store's database is called.
    """

    database: VerifiedDatabase
    replay_root: Path

    def initialize(self) -> None:
        """Create the journal and the triggers that make it append-only.

        Idempotent, so a run against an existing store is not a special case.
        """
        with psycopg.connect(self.database.dsn, autocommit=True) as connection:
            connection.execute(_SCHEMA)
            connection.execute(_TRIGGERS)

    def append(
        self,
        collection: Collection,
        key: str,
        entry: Mapping[str, JsonValue],
    ) -> Entry:
        """Write one entry. The only way anything enters this store.

        Raises:
            PersistentStoreError: an empty key, which would make the entry
                unfindable by the lookup every reader uses.
        """
        if not key.strip():
            message = (
                f"an entry in {collection.value} needs a key: the ledger is read per project "
                "shape, playbooks per fingerprint, and failure memory per finding. An unkeyed "
                "entry is written and never read again"
            )
            raise PersistentStoreError(message)

        with psycopg.connect(self.database.dsn, autocommit=True) as connection:
            row = connection.execute(
                f"INSERT INTO {JOURNAL} (collection, key, entry) "
                "VALUES (%s, %s, %s) RETURNING id, written_at",
                (collection.value, key, Jsonb(entry)),
            ).fetchone()

        if row is None:  # pragma: no cover - an INSERT ... RETURNING always returns
            message = "the insert reported no row, so what was written is not known"
            raise PersistentStoreError(message)

        return Entry(
            id=row[0],
            collection=collection,
            key=key,
            entry=entry,
            written_at=row[1],
        )

    def read(self, collection: Collection, key: str | None = None) -> Sequence[Entry]:
        """Every entry in a collection, oldest first, optionally for one key.

        Oldest first because this is a journal: what was learned and in what
        order is the thing being preserved, and a reader that sorted by
        recency would present a superseded lesson and a current one alike.
        """
        sql = f"SELECT id, collection, key, entry, written_at FROM {JOURNAL} WHERE collection = %s"
        parameters: tuple[object, ...] = (collection.value,)
        if key is not None:
            sql += " AND key = %s"
            parameters += (key,)
        sql += " ORDER BY id"

        with psycopg.connect(self.database.dsn) as connection:
            rows = connection.execute(sql, parameters).fetchall()

        return [
            Entry(
                id=row[0],
                collection=Collection(row[1]),
                key=row[2],
                entry=row[3],
                written_at=row[4],
            )
            for row in rows
        ]

    def members(self) -> tuple[Member, ...]:
        """AC 1's four, and where each is stored.

        Enumerated rather than implied, because one of them is not in this
        database and a list that quietly held three would be the kind of partial
        truth this project keeps finding in its own summaries.
        """
        return MEMBERS

    def describe(self) -> str:
        lines = [
            f"Persistent store: {self.database} — append-only, never rolled back by a "
            "checkpoint restore (08-audit.md F5)",
        ]
        lines.extend(f"  {member.name}: {member.stored_in} ({member.story})" for member in MEMBERS)
        lines.append(f"  replay cache root: {self.replay_root}")
        return "\n".join(lines)

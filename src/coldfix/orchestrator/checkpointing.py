"""Where a run's state is written, and the bound it has to fit in.

Epic 12, S-12.2. *SQLite checkpointer in development, Postgres supported for
concurrent campaigns. State persisted after every node. Checkpoint size bounded
per S-6.3.*

**AC 2 needs no code, and saying that is the story's first honest sentence.**
LangGraph writes a checkpoint after every node when a graph is compiled with a
checkpointer — that is what a checkpointer is. What this story owes is not a
mechanism but a *demonstration*: a test that walks a real graph and reads the
saved checkpoints back, one per node, in order. A criterion satisfied by a
library's default is still a criterion, and the way to meet it is to show it
rather than to write something.

**The two backends differ in what they are for, not in how good they are.**
SQLite is a file, and a file is a lock — one process at a time. `03-agents.md`
puts Postgres against *concurrent campaigns*, which is the case a file cannot
serve. Choosing between them is a question about how many runs share the store,
and `for_development` and `for_campaigns` are named for that question rather than
for the technology.

**S-6.3 already owns the bound and this module does not restate it.** That story
size-checks every `ExperimentRef` against 1 KiB at construction, so a
forty-experiment log cannot exceed 40 KiB whatever was measured, and it fixed the
state limit at 64 KiB with the arithmetic behind it. What was never checked is the
thing actually written: S-6.3 measured the *JSON* encoding as a conservative
proxy, and LangGraph serialises with msgpack. `measure` reads the real serialised
bytes through the checkpointer's own serialiser, so the bound is checked against
what lands on disk rather than against a stand-in.

**A checkpoint that fits the limit in the wrong units is the failure to avoid.**
S-6.3 recorded that its proxy over-estimates by about 15%, which is the safe
direction — a state fitting the JSON bound fits msgpack too. Reading the real
encoding here keeps that true rather than assumed, and would catch a serialiser
change that reversed it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from coldfix.state.reference import CHECKPOINT_SIZE_LIMIT_BYTES

MAX_CHECKPOINT_BYTES = CHECKPOINT_SIZE_LIMIT_BYTES
"""S-6.3's limit, imported rather than restated.

That story derived it — 40 experiments at 1 KiB each leaves room for the artifacts
that do not grow with the investigation — and a second copy here would be a second
answer that drifts the first time the cap moves."""


class CheckpointingError(Exception):
    """A run's state could not be persisted, or does not fit."""


class Backend(StrEnum):
    """Which store a campaign writes to, named for the question that decides it.

    Not *sqlite* and *postgres* — the choice is about how many runs share the
    store, and naming it after the technology would invite picking one for
    familiarity.
    """

    DEVELOPMENT = "one run at a time, in a file"
    CONCURRENT_CAMPAIGNS = "many runs at once, in a server"


@dataclass(frozen=True)
class Size:
    """What one checkpoint actually costs, and whether it fits."""

    bytes_written: int
    limit: int = MAX_CHECKPOINT_BYTES

    @property
    def fits(self) -> bool:
        return self.bytes_written <= self.limit

    @property
    def headroom(self) -> int:
        return self.limit - self.bytes_written

    def describe(self) -> str:
        share = self.bytes_written / self.limit
        verdict = "fits" if self.fits else "**OVER**"
        return (
            f"{self.bytes_written} bytes of {self.limit} ({share:.0%}) — {verdict}, "
            f"{self.headroom} to spare"
        )


@contextmanager
def for_development(path: str | Path) -> Iterator[BaseCheckpointSaver[Any]]:
    """A checkpointer backed by one file. **One run at a time.**

    SQLite serialises writers, so two campaigns against one file take turns and
    the second blocks. That is a correct answer for development and the wrong one
    for a fleet — `for_campaigns` is the other case.

    A context manager because the connection is a resource: `SqliteSaver` holds an
    open handle, and a run that abandoned it would leave the file locked for
    whatever tried next. The tables are created on entry, so a fresh path is
    usable without a migration step.
    """
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        saver = SqliteSaver(connection)
        saver.setup()
        yield saver
    finally:
        connection.close()


def for_campaigns(dsn: str) -> str:
    """The Postgres checkpointer's connection string, checked before it is used.

    **This returns the DSN rather than a saver, and that is deliberate.**
    `PostgresSaver` wants a live connection or a pool, and opening one here would
    make importing this module a thing that talks to a database. The caller owns
    the connection's lifetime; what this owns is the refusal below.

    Raises:
        CheckpointingError: the DSN names something that is not Postgres, or names
            a production database. S-2.5 refuses to start against production by
            pattern-matching the URL, and a *checkpoint* store is not the subject —
            but a DSN that points at the subject's own production database is a
            configuration mistake worth catching in both places.
    """
    if not dsn.startswith(("postgresql://", "postgres://")):
        message = (
            f"{dsn.split('://', maxsplit=1)[0]!r} is not a Postgres DSN. `03-agents.md` puts "
            "Postgres against concurrent campaigns because a file locks; another store would "
            "need its own argument"
        )
        raise CheckpointingError(message)
    return dsn


def measure(state: Mapping[str, Any], *, limit: int = MAX_CHECKPOINT_BYTES) -> Size:
    """How many bytes this state costs **in the encoding that is written**.

    S-6.3 measured JSON as a conservative proxy and recorded that LangGraph's
    msgpack encoding is about 15% smaller — the safe direction, since a state
    fitting the proxy fits what lands on disk. This reads the real serialiser, so
    the bound is checked against the thing itself and a serialiser change that
    reversed the relationship would show up here rather than on somebody's disk.
    """
    _, blob = JsonPlusSerializer().dumps_typed(dict(state))
    return Size(bytes_written=len(blob), limit=limit)


def refuse_oversized(state: Mapping[str, Any], *, limit: int = MAX_CHECKPOINT_BYTES) -> Size:
    """Measure, and raise if it does not fit.

    **A checkpoint is written after every node**, so a state that has outgrown the
    bound does not cost one write — it costs one per transition for the rest of the
    run, and the first symptom is a slow campaign rather than an error.

    Raises:
        CheckpointingError: the state is larger than the limit.
    """
    size = measure(state, limit=limit)
    if not size.fits:
        message = (
            f"this state serialises to {size.bytes_written} bytes against a limit of {limit} "
            f"({-size.headroom} over). S-6.3's bound holds only while the experiment log stores "
            "references rather than results, and a checkpoint is written after every node — so "
            "the cost is one oversized write per transition, which reads as a slow run"
        )
        raise CheckpointingError(message)
    return size


def saved(checkpointer: BaseCheckpointSaver[Any], thread: str) -> Sequence[Mapping[str, Any]]:
    """Every checkpoint written for one run, oldest first. **AC 2's demonstration.**

    LangGraph writes one per node when a graph is compiled with a checkpointer, so
    this story's job is to show that rather than to build it. Reading them back is
    what makes *persisted after every node* a checked fact instead of a property of
    somebody else's library that nobody looked at.

    Reversed because `list` yields newest first, and a reader comparing this against
    the order the nodes ran should not have to know that.
    """
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    written = reversed(list(checkpointer.list(config)))
    return [item.checkpoint["channel_values"] for item in written]


def thread(run_id: str) -> RunnableConfig:
    """The config a compiled graph needs to write a run's checkpoints under one id.

    A helper because the nesting — `configurable.thread_id` — is the sort of thing
    that gets spelled two ways in two call sites, and the second one silently
    starts a new run rather than resuming the first.
    """
    return {"configurable": {"thread_id": run_id}}

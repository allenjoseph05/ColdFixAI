"""Picking a killed run back up, and what re-running a node costs.

Epic 12, S-12.3. *A run killed mid-investigation resumes from the last checkpoint
with full state. Tested by killing the process at three different nodes. The
resumed run produces the same final result as an uninterrupted one.*

**Resuming is `invoke(None, ...)` and starting over is `invoke(state, ...)`, and
the two are one argument apart.** LangGraph reads `None` as *continue from where
the thread left off* and anything else as *begin with this*. Handing back the
initial state after a crash is therefore not a resume — it is a second run that
silently repeats every node, bills every phase again, and produces a plausible
answer. `resume` and `start` are two names for that one difference, because the
mistake is invisible: both return a final state, and the wrong one is only
detectable by the bill.

**Checkpoints are written asynchronously unless you say otherwise, and a hard
kill loses them.** LangGraph submits each write to a background executor and does
not wait; `os._exit` takes the process before those threads run, so the default
leaves *one* checkpoint holding nothing but `__start__` however far the run got.
`durability="sync"` is what makes AC 1's *resumes from the last checkpoint with
full state* true rather than vacuous — measured, killing at five successive nodes
goes from one surviving checkpoint to two, three, four, five and six.

The failure this hid is the nastiest kind: with nothing durable, a resume restarts
from the beginning and **reaches the same final answer**, so every test of AC 1 and
AC 3 passes while the crash saved nothing. `DURABILITY` is a module constant rather
than an argument at each call site, because a run started durable and resumed
asynchronously would be half-protected in a way nothing reports.

**A checkpoint is written after a node, so a crash inside one re-runs it.** That
is at-least-once execution and it is not a defect to be fixed here — it is the
property every node has to be written against. The bite is on the append-only
channels: a node that appends and is killed before the checkpoint lands appends
again on resume, and `experiments` gaining the same entry twice is a log that
disagrees with itself. What makes this safe today is *where* the appends happen —
after the work, in the node's return value — so an interrupted node contributes
nothing at all rather than contributing half.

**A checkpoint holds only the channels written so far.** S-12.2 found that: the
earliest ones have no `project` key rather than an empty one, so anything reading
a resumed state has to treat a missing channel as *not yet written* rather than as
a schema violation. `state_of` returns what is there and does not fill gaps,
because filling them would invent a value the run never had.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from coldfix.orchestrator.checkpointing import thread
from coldfix.state.checkpoint import CheckpointedState

DURABILITY = "sync"
"""Write each checkpoint before the next node starts.

LangGraph's default submits the write to a background executor and carries on,
which is faster and loses everything to a hard kill. A campaign that runs for
hours and is expected to survive a reboot cannot take that trade, and the cost
is one synchronous write per node against a budget of 40 experiments."""


class ResumeError(Exception):
    """A run could not be picked back up."""


@dataclass(frozen=True)
class Progress:
    """How far a thread got before it stopped.

    `checkpoints` counts writes rather than nodes: LangGraph writes one before the
    first node runs and one after each, so a run that completed three nodes has
    four. Counting nodes would need this module to know the graph, which it
    deliberately does not.
    """

    run_id: str
    checkpoints: int
    state: Mapping[str, Any]

    @property
    def started(self) -> bool:
        return self.checkpoints > 0

    def describe(self) -> str:
        if not self.started:
            return f"{self.run_id}: nothing was ever written; this is a new run"
        written = ", ".join(sorted(self.state))
        return (
            f"{self.run_id}: {self.checkpoints} checkpoints, channels written: {written or 'none'}"
        )


def progress_of(checkpointer: BaseCheckpointSaver[Any], run_id: str) -> Progress:
    """What a thread has on disk. **Reads, never fills.**

    A channel absent from the last checkpoint has not been written yet, which is
    not the same as having been written empty — S-12.2's finding. Supplying a
    default here would hand a resumed run a value it never had, and the run would
    proceed on it.
    """
    written = list(checkpointer.list(thread(run_id)))
    latest = written[0].checkpoint["channel_values"] if written else {}
    return Progress(run_id=run_id, checkpoints=len(written), state=dict(latest))


def start(
    graph: Any,  # noqa: ANN401 - LangGraph's compiled graph type; see `graph.assemble`
    run_id: str,
    state: CheckpointedState | None = None,
) -> Mapping[str, Any]:
    """Begin a new run under `run_id`.

    Separate from `resume` for the reason the module docstring gives: the two
    differ by one argument and confusing them produces a run that looks right and
    costs twice.
    """
    return dict(
        graph.invoke(
            state if state is not None else CheckpointedState(),
            thread(run_id),
            durability=DURABILITY,
        )
    )


def resume(
    graph: Any,  # noqa: ANN401 - see `start`
    checkpointer: BaseCheckpointSaver[Any],
    run_id: str,
) -> Mapping[str, Any]:
    """Continue a run from its last checkpoint. **AC 1.**

    `invoke(None, ...)` is what makes this a resume rather than a restart, and the
    checkpointer is a parameter so the refusal below can be made: continuing a
    thread that was never written would silently start a new run under a name
    somebody chose because they believed it already existed.

    Raises:
        ResumeError: nothing has ever been written for this run.
    """
    progress = progress_of(checkpointer, run_id)
    if not progress.started:
        message = (
            f"nothing was ever checkpointed for {run_id!r}, so there is nothing to resume. "
            "`invoke(None, ...)` against an unknown thread starts a new run rather than "
            "failing, which would spend a whole campaign under a name chosen in the belief "
            "that it already existed — use `start` if that is what you meant"
        )
        raise ResumeError(message)
    return dict(graph.invoke(None, thread(run_id), durability=DURABILITY))


def resumed_config(run_id: str) -> RunnableConfig:
    """The config a resume runs under. One spelling, for S-12.2's reason."""
    return thread(run_id)


def same_outcome(
    uninterrupted: Mapping[str, Any],
    resumed: Mapping[str, Any],
    *,
    ignoring: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """**AC 3.** Which channels differ between the two runs — empty means they agree.

    Returns the disagreements rather than a boolean, because *the resumed run
    produced a different answer* is only actionable with the channel named. A
    caller that wants the boolean asks whether the tuple is empty, and one that
    wants to act has what it needs.

    `ignoring` exists for channels that legitimately differ — a wall-clock reading,
    a run identifier — and is empty by default, so a caller has to name each one it
    is prepared to overlook. A default that excused anything would make this
    function agree with itself.
    """
    channels = (set(uninterrupted) | set(resumed)) - set(ignoring)
    return tuple(sorted(name for name in channels if uninterrupted.get(name) != resumed.get(name)))


def agrees(
    uninterrupted: Mapping[str, Any],
    resumed: Mapping[str, Any],
    *,
    ignoring: tuple[str, ...] = (),
) -> bool:
    """Whether a resumed run reached the same place. AC 3, as the question."""
    return not same_outcome(uninterrupted, resumed, ignoring=ignoring)


def duplicated(state: Mapping[str, Any], channel: str, *, key: str) -> tuple[Any, ...]:
    """Entries appearing more than once in an append-only channel, by `key`.

    **The failure at-least-once execution produces.** A node killed after its
    append landed and before the run moved on would append again on resume, and
    `experiments` holding the same entry twice is a log that disagrees with itself
    — S-8.4's append-only guarantee broken by a crash rather than by a rewrite.

    Reported rather than prevented: this module cannot make a node idempotent, and
    a deduplication here would hide the fact that one was not.
    """
    seen: dict[Any, int] = {}
    for entry in state.get(channel) or ():
        if isinstance(entry, Mapping) and key in entry:
            seen[entry[key]] = seen.get(entry[key], 0) + 1
    return tuple(sorted(value for value, count in seen.items() if count > 1))

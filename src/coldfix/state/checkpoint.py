"""The checkpointed state, its append-only channels, and what a node may return.

Epic 6, S-6.1. `03-agents.md` §1.1 gives the field list; `08-audit.md` F5 moves
the trust ledger out of it. The story's note is unusually direct about why this
matters: **`Annotated[list, add]` is load-bearing — without it the agent loses
its own history, re-tests rejected hypotheses, and loops while appearing to
work.** That is the failure mode this module exists to make impossible.

**A Pydantic model rather than §1.1's literal `TypedDict`.** `CLAUDE.md` requires
a Pydantic model for every artifact that crosses a node boundary, and the
checkpointed state is the artifact that crosses *every* node boundary. A
`TypedDict` also cannot carry AC 3's validation, cannot be checked at
construction, and gives `dict[str, Any]` under `mypy --strict`. Verified against
a real `StateGraph`: LangGraph accepts a `BaseModel` schema and honours
`Annotated[..., reducer]` on its fields exactly as it does on a `TypedDict`.

**Three things LangGraph does not do, measured rather than assumed.** A spike
compiled a graph for each before this module was designed:

| Behaviour | What actually happens |
|---|---|
| Node returns an unannotated list field | **Replaces.** The bug the note names. |
| Node returns an *unknown* key | **Silently ignored** — no error, no write |
| State validated on a node transition | **Not at all**, `extra="forbid"` included |

So the append semantics come from the framework, and the validation does not.
`node` supplies it, and it is the reason AC 3 is a wrapper rather than a claim.

**The reducer is checked rather than `operator.add`.** `add` appends, which is
most of the job, but it accepts the mirror-image bug in silence: a node that
returns the whole accumulated channel instead of its delta *doubles* the history
rather than losing it. Nothing downstream survives that — S-5.7's non-negotiable
is an append-only log whose bytes never move, and S-5.8's `read_experiment(7)`
has to mean the seventh experiment. `AppendOnly` refuses it by name.

**Every field is JSON-representable, and that is a constraint rather than a
placeholder.** ADR 003 puts checkpoints in SQLite or Postgres, so a state that
cannot serialize is a state that cannot checkpoint. The artifacts these channels
carry belong to epics that do not exist yet — a workload is S-7.9's, an
experiment is S-8.4's, a chain is S-8.6's — and inventing their schemas here is
the guess S-5.4 declined to make when it left the checkpoint schema to this
story. `JsonValue` says what this story knows and no more.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

T = TypeVar("T")


class StateError(Exception):
    """The state could not be changed, or a change was refused."""


class UnknownChannelError(StateError):
    """A node returned a key the schema does not have.

    Raised rather than ignored, which is what LangGraph does with it. A node
    that returns `{"experiements": [...]}` writes nothing, reports nothing, and
    leaves an investigation that runs to its cap having recorded no experiments
    — the story's *loops while appearing to work*, reached through a typo.
    """


class HistoryRewrittenError(StateError):
    """A node returned a channel's whole contents instead of what it added.

    The mirror of the bug `Annotated[list, add]` prevents. Losing history and
    doubling it are the same defect seen from two sides, and only one of them
    has a framework guard.
    """


@dataclass(frozen=True)
class AppendOnly:
    """A reducer that appends, and refuses the two ways that goes wrong.

    LangGraph calls this with `(current, update)` on every write to the channel
    and stores what it returns. A plain `operator.add` would do the appending;
    what it would not do is notice that the update already contains the history
    it is being added to.
    """

    channel: str

    def __call__(self, current: Sequence[T], update: Sequence[T]) -> list[T]:
        """Append `update` to `current`.

        Raises:
            StateError: the update is not a sequence of entries — most often a
                single entry returned bare, which `operator.add` would reject
                with a `TypeError` naming neither the channel nor the cause.
            HistoryRewrittenError: the update repeats what the channel already
                holds, so appending it would record every earlier entry twice.
        """
        if isinstance(update, str | bytes) or not isinstance(update, Sequence):
            message = (
                f"a write to {self.channel!r} must be a sequence of entries and this is "
                f"{type(update).__name__}. A node adds to an append-only channel by returning a "
                "list of what it added, never the entry on its own"
            )
            raise StateError(message)

        repeats_history = (
            bool(current)
            and len(update) >= len(current)
            and list(update[: len(current)]) == list(current)
        )
        if repeats_history:
            message = (
                f"this write to {self.channel!r} starts with the {len(current)} entries the "
                f"channel already holds, so appending it would record each of them twice. A node "
                "returns what it added, not the channel it added to — the reducer does the "
                "appending, which is what makes the log's earlier bytes never move"
            )
            raise HistoryRewrittenError(message)

        return [*current, *update]


class CheckpointedState(BaseModel):
    """What a rewind may discard. `03-agents.md` §1.1, with F5's split applied.

    **The trust ledger is deliberately absent.** §1.1 lists `ledger` here and
    `08-audit.md` F5 supersedes that: the ledger, failure memory, playbooks and
    the replay cache belong to S-6.2's persistent store, written append-only and
    never rolled back. Keeping the ledger here is the F5 defect itself — a
    rewind would restore the trust level that preceded the failure that caused
    the rewind, and the agent would re-earn the same lesson.

    `extra="forbid"` because a state that accepts an unrecognised key is a state
    where a typo is a silent no-op. It does not catch the same typo arriving
    from a *node* — LangGraph filters those before they reach the model — which
    is what `node` is for.
    """

    model_config = ConfigDict(extra="forbid")

    project: Mapping[str, JsonValue] = Field(default_factory=dict)
    """Fingerprint, adapter and workspace path. S-7.1 decides its shape."""

    workloads: Sequence[JsonValue] = ()
    """What grounding produced. S-7.9's artifact."""

    screening: Mapping[str, JsonValue] = Field(default_factory=dict)
    """The growth table, ranked by suspicion, **keyed by workload id**. S-4.3's.

    Keyed rather than the sequence this was until Epic 6's composition check.
    F14 invalidates screening results *per workload* — the workloads whose files
    a patch touched — and a flat sequence of opaque entries cannot be filtered
    that way: nothing says which workload an entry belongs to, so S-6.4's policy
    produced a correct answer that had nowhere to go. Neither story could see it
    alone, because one owns the shape and the other owns the rule.
    """

    target: JsonValue | None = None
    """The workload under investigation, or `None` between investigations."""

    experiments: Annotated[list[JsonValue], AppendOnly("experiments")] = Field(default_factory=list)
    """The append-only experiment log. S-8.4's artifact, S-6.3's by reference."""

    attempts: Annotated[list[JsonValue], AppendOnly("attempts")] = Field(default_factory=list)
    """Surgeon attempts and why each failed. S-10.5's retry discipline reads it."""

    flags: Annotated[list[JsonValue], AppendOnly("flags")] = Field(default_factory=list)
    """Items awaiting a human. S-12.4's interrupt drains it."""

    chain: JsonValue | None = None
    """The proven cause, once one is proven. S-8.6's evidence chain."""

    verdict: str | None = None
    """E9's finding-audit outcome. Left a string because E9 names the values."""

    budget: Mapping[str, JsonValue] = Field(default_factory=dict)
    """Steps and euros remaining per phase — a projection of S-5.4's `Budget`,
    which owns the caps themselves and cannot be reconstructed from here."""

    @classmethod
    def channels(cls) -> Mapping[str, bool]:
        """Every field, and whether writing to it appends or replaces.

        Enumerable rather than something a reader has to infer from the
        annotations: which channels accumulate is the whole subject of this
        story, and a list is easier to check against `03-agents.md` §1.1 than a
        set of `Annotated` metadata.
        """
        return {
            name: any(isinstance(item, AppendOnly) for item in field.metadata)
            for name, field in cls.model_fields.items()
        }

    @classmethod
    def append_only_channels(cls) -> tuple[str, ...]:
        return tuple(name for name, appends in cls.channels().items() if appends)


def check_update(
    update: Mapping[str, object],
    *,
    schema: type[CheckpointedState] = CheckpointedState,
) -> Mapping[str, object]:
    """Refuse a node's return value that the schema cannot accept. AC 3.

    Checks the two things LangGraph does not. **Unknown keys**, which it drops
    without a word, and **the type of each value**, which it never looks at. For
    an append-only channel the value is the delta, so it is checked against the
    channel's own element type — the same annotation either way.

    Returns the update unchanged, so it can wrap a node's return in place.

    Raises:
        UnknownChannelError: a key the schema does not have.
        StateError: a value the field's type rejects.
    """
    unknown = sorted(set(update) - set(schema.model_fields))
    if unknown:
        known = ", ".join(sorted(schema.model_fields))
        message = (
            f"this node returned keys the state does not have: {unknown}. LangGraph drops an "
            f"unrecognised key silently, so the write simply would not happen and nothing would "
            f"say so. The state's channels are: {known}"
        )
        raise UnknownChannelError(message)

    for name, value in update.items():
        field = schema.model_fields[name]
        try:
            TypeAdapter(field.annotation).validate_python(value)
        except ValidationError as error:
            message = (
                f"this node's write to {name!r} does not fit the channel: "
                f"{error.errors()[0]['msg']}. A checkpoint is stored as JSON (ADR 003), so a "
                "value that cannot be represented is a run that cannot resume"
            )
            raise StateError(message) from error

    return update


def node[NodeFunction: Callable[..., Mapping[str, object]]](
    function: NodeFunction,
) -> NodeFunction:
    """Wrap a graph node so its write is validated on every transition. AC 3.

    The decorator form exists because AC 3 says *every* transition, and a check
    a caller has to remember to make is one that holds until somebody adds a
    node. `assemble` in S-12.1 should register nodes through this and nothing
    else.

    **The decorated function keeps its own type**, which is not cosmetic:
    LangGraph's node protocol declares `__call__(self, state: ...)` with a
    *named* parameter, and a plain `Callable[[State], ...]` has positional-only
    parameters — so returning one would make every correctly-annotated node fail
    to type-check at `add_node` while an unannotated lambda passed.
    """

    @wraps(function)
    def validated(*args: object, **kwargs: object) -> Mapping[str, object]:
        return check_update(function(*args, **kwargs))

    return cast("NodeFunction", validated)

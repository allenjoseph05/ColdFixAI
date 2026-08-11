"""Epic 6 in one flow: state that survives a crash, knowledge that survives a rewind.

The epic's goal, from the backlog, is exactly that sentence. Four stories build
the parts — a checkpointed state with append-only channels, a persistent store a
rewind cannot reach, results held by reference, and a policy for what a shipped
patch invalidates — and until this module existed there was no way to run one
investigation through them. Three of the joins had no correct form at all.

**The staleness policy's answer had nowhere to go.** S-6.1 held `screening` as a
flat sequence of opaque entries; S-6.4 invalidates *per workload*. Nothing said
which workload an entry belonged to, so a correct answer could not be applied —
the caller had to rebuild the channel by hand, from a shape that does not carry
the identity the rebuild needs. Neither story could see it: one owns the shape,
the other owns the rule. `screening` is now keyed by workload id and `apply_ship`
is the rebuild, once.

**`experiments` accepted a full measurement.** S-6.3 bounds a checkpoint by
storing references, and S-6.1's channel is `list[JsonValue]` — so a node that
appended the measurement itself passed every check either module makes, and F13's
guarantee held only as long as every caller remembered. `record_experiment` is
the one way in, and `check_experiments` refuses a state whose log holds anything
that is not a reference.

**Nothing produced coverage, so nothing could be shown untouched.** S-6.4 is
explicit that unrecorded is not untouched, which is correct and leaves every
workload invalidated forever unless something records what they run. The
reference already carries the key, and the key names the workload — so an
investigation that stored its experiments by reference has, in the state,
everything needed to say which workloads it exercised. `coverage_from_state`
reads it back rather than asking for it again.

Nothing here runs a graph. S-12.1 assembles one; this is what its nodes return.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from pydantic import JsonValue, TypeAdapter, ValidationError

from coldfix.replay.cache import Determinism, ExperimentKey, Recall, ReplayCache
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.persistent import Collection, PersistentStore
from coldfix.state.reference import (
    CHECKPOINT_SIZE_LIMIT_BYTES,
    ExperimentRef,
    checkpoint_size_bytes,
    reference,
    resolve,
)
from coldfix.state.staleness import (
    Coverage,
    FindingAction,
    Patch,
    StalenessReport,
    after_ship,
)

_REF = TypeAdapter(ExperimentRef)


class InvestigationError(Exception):
    """An investigation's state could not be advanced or read back."""


class UnreferencedResultError(InvestigationError):
    """The experiment log holds something that is not a reference.

    F13's bound is that a checkpoint holds hashes and summaries. S-6.1's channel
    is `list[JsonValue]`, so a node returning the measurement itself satisfies
    the schema, the reducer, and the node validator — and the checkpoint grows
    without anything objecting until it is megabytes.
    """


class CheckpointTooLargeError(InvestigationError):
    """The state is over the size S-6.3 states a checkpoint stays under.

    S-6.3 proved the bound holds for 40 references and S-5.4 caps investigation
    at 40 experiments, but the two live in different modules and nothing joined
    them — an investigation that never consulted the budget could append past
    the cap and past the limit with nothing to say so.
    """


def record_experiment[T](  # noqa: PLR0913 - each argument is a thing this join
    # needs and none may be defaulted: the cache and key decide where the
    # measurement is stored, `compute` and `result_type` are S-5.1's two halves,
    # `outcome` is the one part of the summary an agent supplies, and
    # `determinism` defaults to S-5.2's conservative half rather than to nothing.
    state: CheckpointedState,
    *,
    cache: ReplayCache,
    key: ExperimentKey,
    result_type: type[T],
    compute: Callable[[], T],
    outcome: str,
    determinism: Determinism = Determinism.SAMPLED,
) -> Mapping[str, JsonValue]:
    """Run one experiment and return the update that adds it to the log.

    The only way an experiment enters the state. The measurement goes to the
    replay cache and the checkpoint gets a reference, which is F13's fix applied
    at the one place it can be enforced rather than remembered.

    The index comes from the state's own log, so it cannot be supplied wrongly —
    `read_experiment(7)` has to mean the seventh, and a caller-chosen index can
    collide, skip or restart.
    """
    recalled = cache.run(key, result_type, compute, determinism=determinism)
    ref = reference(recalled, key, index=len(state.experiments) + 1, outcome=outcome)
    return {"experiments": [ref.model_dump(mode="json")]}


def experiments_of(state: CheckpointedState) -> tuple[ExperimentRef, ...]:
    """The log, parsed back into references.

    Raises:
        UnreferencedResultError: an entry that is not a reference — most often a
            measurement a node appended directly.
    """
    parsed: list[ExperimentRef] = []
    for position, entry in enumerate(state.experiments, start=1):
        try:
            parsed.append(_REF.validate_python(entry))
        except ValidationError as error:
            message = (
                f"entry {position} of the experiment log is not a reference: "
                f"{error.errors()[0]['msg']}. A checkpoint holds hashes and one-line summaries "
                "(08-audit.md F13); the measurement itself belongs in the replay cache, and a "
                "log that holds one grows to megabytes with nothing objecting"
            )
            raise UnreferencedResultError(message) from error
    return tuple(parsed)


def check_state(state: CheckpointedState, *, limit: int = CHECKPOINT_SIZE_LIMIT_BYTES) -> None:
    """Refuse a state that cannot be checkpointed as S-6.3 promises.

    Both halves of the promise, because each is silent alone: a log holding a
    measurement is under the limit until it is not, and a log of references is
    bounded only while something keeps it to the cap.

    Raises:
        UnreferencedResultError: the log holds something that is not a reference.
        CheckpointTooLargeError: the state is over the stated limit.
    """
    experiments_of(state)

    size = checkpoint_size_bytes(state)
    if size > limit:
        message = (
            f"this state encodes to {size} bytes and a checkpoint is supposed to stay under "
            f"{limit} (S-6.3). It holds {len(state.experiments)} experiments; S-5.4 caps "
            "investigation at 40, and the bound is only a bound while something enforces that cap"
        )
        raise CheckpointTooLargeError(message)


def read_experiment[T](
    state: CheckpointedState, index: int, *, cache: ReplayCache, result_type: type[T]
) -> Recall[T]:
    """F13's tool call, against the log the checkpoint holds.

    Raises:
        InvestigationError: no experiment has that index.
        ResultNotStoredError: the recording is not in this store.
    """
    log = experiments_of(state)
    if not 1 <= index <= len(log):
        available = f"1-{len(log)}" if log else "none yet"
        message = (
            f"there is no experiment {index}; this investigation has run {available}. A retrieval "
            "that returned the nearest record would answer a question nobody asked"
        )
        raise InvestigationError(message)
    return resolve(cache, log[index - 1], result_type)


def coverage_from_state(state: CheckpointedState) -> tuple[Coverage, ...]:
    """Which workloads this investigation exercised, read back from its own log.

    S-6.4 records that unrecorded is not untouched, which leaves every workload
    invalidated until something records what it runs. The reference carries the
    experiment key and the key names the workload, so an investigation that
    stored its results by reference already holds part of that record.

    **This is coverage at workload granularity, not file granularity**, and the
    difference matters: it says which workloads were measured, not which source
    files they executed. A caller that has file-level coverage — S-3.9's stacks —
    should use it. This exists so that the workloads an investigation never
    touched are not invalidated for want of any record at all.
    """
    return tuple(
        Coverage.unrecorded(workload_id)
        for workload_id in sorted({ref.key.workload_id for ref in experiments_of(state)})
    )


def learn(store: PersistentStore, *, finding_id: str, entry: Mapping[str, JsonValue]) -> None:
    """Write what was tried and did not work, where a rewind cannot reach it.

    The half of F5 that is not about checkpoints: the reason to rewind is a
    failure discovered after the checkpoint being rewound to, so the record of it
    has to live somewhere the restore does not touch.
    """
    store.append(Collection.FAILURE_MEMORY, key=finding_id, entry=entry)


def apply_ship(
    state: CheckpointedState,
    *,
    patch: Patch,
    workloads: Sequence[Coverage],
    pending: Iterable[Coverage] = (),
) -> tuple[Mapping[str, JsonValue], StalenessReport, Mapping[str, FindingAction]]:
    """The update a node returns after a patch ships, and why.

    Returns the state update, the report a human reads, and what happens to each
    pending finding. The update rebuilds `screening` with the invalidated
    workloads dropped — which is F14's *others keep their measurements*, and is
    the branch that lived in no module: `screening` is a replace channel, so a
    caller had to reconstruct it, from a shape that until now did not say which
    workload an entry was for.
    """
    report = after_ship(workloads, patch)
    invalidated = set(report.invalidated)
    kept = {
        workload_id: result
        for workload_id, result in state.screening.items()
        if workload_id not in invalidated
    }

    findings = {
        assessment.subject: assessment.finding_action
        for assessment in after_ship(tuple(pending), patch).assessments
        if assessment.subject != patch.finding_id
    }
    return {"screening": kept}, report, findings

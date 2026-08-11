"""What a checkpoint holds instead of a measurement.

Epic 6, S-6.3. `08-audit.md` F13: *`experiments` is append-only and lives in
checkpointed state. Forty experiments × full measurement output, checkpointed
after every node, is megabytes of duplicated writes.* The fix it names is to put
the results in the replay cache keyed by hash and leave the state holding hashes
and one-line summaries, with the agent fetching detail through a tool call.

**"Bounded" is a guarantee here, not an observation.** A limit that is merely
measured holds for the experiments somebody happened to test with. Every
reference is size-checked at construction against `MAX_REFERENCE_BYTES`, so
S-5.4's cap of 40 experiments gives a checkpoint whose experiment log cannot
exceed 40 KiB whatever the measurements were — and the stated limit for the whole
state has that arithmetic behind it rather than a sample.

**The reference carries the key, not only the digest.** F13 says *keyed by hash*,
and the digest is the filename, so a digest alone is enough to find the file —
but only by scanning the store, since S-5.1's lookup takes a key and derives the
digest from it. Carrying the key makes a fetch one read instead of a sweep, and
makes the reference self-describing: a log line that says *which* experiment this
was, without opening anything. A key is identity, not a result, so this is still
the state holding no measurements.

**The outcome bound is S-5.8's, deliberately.** F13 ends by noting the alignment
with `04-cost.md` §5's pruning, and the two are the same discipline applied to
two artifacts — what goes in the prompt and what goes in the checkpoint. Sharing
`MAX_SUMMARY_CHARS` is what keeps them from drifting apart.

**Size is measured as JSON, which over-estimates on purpose.** S-6.1 kept
`langgraph` out of `src/` and every state field JSON-representable precisely so
this is possible. LangGraph's own checkpoint serializer is msgpack rather than
JSON and is therefore *smaller* — measured at ~85% of the JSON encoding for a
full forty-experiment log — so a state that fits this limit fits what the
checkpointer actually writes, with room to spare. Erring the other way would give
a limit that passed here and was breached on disk. A test pins the ratio against
the real serializer, so the proxy is verified rather than reasoned about.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coldfix.cost.pruning import MAX_SUMMARY_CHARS
from coldfix.replay.cache import Determinism, ExperimentKey, Recall, ReplayCache
from coldfix.state.checkpoint import CheckpointedState

# What one reference may occupy, encoded. Enforced at construction, which is what
# turns AC 3's *stays bounded* from something measured into something guaranteed:
# an experiment whose spec carried a large parameter block would otherwise grow
# the checkpoint without anything noticing.
MAX_REFERENCE_BYTES = 1024

# S-5.4 caps investigation at 40 experiments, so the log's contribution to a
# checkpoint cannot exceed 40 KiB. The rest of the state — project, workloads,
# screening, budget — gets the remaining 24 KiB, which is generous for artifacts
# that do not grow with the investigation.
MAX_EXPERIMENTS = 40
CHECKPOINT_SIZE_LIMIT_BYTES = 64 * 1024


class ExperimentReferenceError(Exception):
    """A reference could not be built, or a result could not be fetched back.

    Not `ReferenceError`, which is a Python builtin — a name that shadows one is
    caught by anything that writes `except ReferenceError` meaning the builtin.
    """


class ReferenceTooLargeError(ExperimentReferenceError):
    """A reference would not fit the budget the checkpoint size limit rests on.

    Refused rather than truncated. Truncating would quietly produce a reference
    that no longer identifies its experiment, and the whole value of the
    reference is that the measurement can be found again from it.
    """


class ResultNotStoredError(ExperimentReferenceError):
    """The reference names an experiment the replay cache does not hold.

    The state and the store disagreeing is worth a name of its own: it means
    either the cache was written by a different machine — S-5.1 partitions by
    environment, so a checkpoint carried between machines resolves nothing — or
    the recordings were deleted under a live checkpoint.
    """


class ExperimentRef(BaseModel):
    """One experiment as the checkpoint holds it: identity, and one line.

    Frozen because it is a record of something that already happened, and
    `extra="forbid"` for S-6.1's reason — a field this schema does not know is a
    write that goes nowhere.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1)
    """Which experiment this was. One-based, because S-5.8's `read_experiment(7)`
    has to mean the seventh and the two indexes describe the same log."""

    key: ExperimentKey
    """Where the measurement is. Its digest is the recording's filename."""

    outcome: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    """What it established, in one line. Bounded by S-5.8's figure, per F13."""

    recorded_at: datetime
    determinism: Determinism
    hit: bool
    """Whether this was replayed rather than measured.

    Kept in the checkpoint rather than fetched, because `CLAUDE.md`'s first
    non-negotiable is only meaningful if *when* survives with the number — and a
    log that needed a disk read to say whether a measurement is current would
    have given back what pruning bought.
    """

    @field_validator("outcome")
    @classmethod
    def _one_line(cls, outcome: str) -> str:
        if "\n" in outcome:
            message = (
                "an outcome spans multiple lines, and a summary that grows with its subject is "
                "the thing storing by reference exists to prevent"
            )
            raise ValueError(message)
        return outcome

    def model_post_init(self, _: object, /) -> None:
        encoded = len(self.model_dump_json().encode())
        if encoded > MAX_REFERENCE_BYTES:
            message = (
                f"this reference encodes to {encoded} bytes and the limit is "
                f"{MAX_REFERENCE_BYTES}. A checkpoint is written after every node, so the log's "
                f"size is multiplied by the whole run — the {CHECKPOINT_SIZE_LIMIT_BYTES}-byte "
                "limit rests on each reference fitting. The experiment spec's parameters are "
                "usually what overflows this; the measurement itself belongs in the replay cache"
            )
            raise ReferenceTooLargeError(message)

    @property
    def digest(self) -> str:
        """The hash F13 says the state holds, and the recording's filename."""
        return self.key.digest()

    def summary(self) -> str:
        """§5's shape, composed by the harness rather than written by an agent.

        S-5.8's construction: the header comes from the primitive and the target,
        which are facts about what ran, and only the outcome is supplied — F6's
        finding that everything an agent can author about its own success, it
        will author favourably.
        """
        return (
            f"experiment {self.index} — {self.key.experiment_spec.primitive} "
            f"of {self.key.workload_id}\n  → {self.outcome}"
        )

    def provenance(self) -> str:
        when = self.recorded_at.isoformat(timespec="seconds")
        if not self.hit:
            return f"measured {when}"
        if self.determinism is Determinism.DETERMINISTIC:
            return f"replayed from {when}; declared deterministic"
        return f"replayed from {when}; sampled, so a fresh run would answer differently"


def reference(
    recall: Recall[object], key: ExperimentKey, *, index: int, outcome: str
) -> ExperimentRef:
    """Build the checkpoint's record of an experiment that has just been stored.

    Takes the `Recall` rather than its parts so the reference cannot describe a
    measurement that was never made: `run` is the only thing that produces one,
    and it produces one only by measuring or by finding a recording.

    Raises:
        ReferenceTooLargeError: the reference does not fit the per-entry budget.
        ValidationError: an empty or multi-line outcome, or an index below one.
    """
    return ExperimentRef(
        index=index,
        key=key,
        outcome=outcome,
        recorded_at=recall.recorded_at,
        determinism=recall.determinism,
        hit=recall.hit,
    )


def resolve[T](cache: ReplayCache, ref: ExperimentRef, result_type: type[T]) -> Recall[T]:
    """Fetch the full result a reference points at. F13's *tool call*.

    Raises:
        ResultNotStoredError: this cache holds no recording under the key.
        ResultTypeError: an entry exists holding a different type.
    """
    recalled = cache.recall(ref.key, result_type)
    if recalled is None:
        message = (
            f"experiment {ref.index} ({ref.key.workload_id} / "
            f"{ref.key.experiment_spec.primitive}) is referenced by the checkpoint and its "
            f"recording is not in this store. Digest {ref.digest}. Either the recordings were "
            "removed under a live checkpoint, or this checkpoint was made on another machine — "
            "S-5.1 partitions the store by environment, so a checkpoint carried across machines "
            "references measurements that are not here"
        )
        raise ResultNotStoredError(message)
    return recalled


def checkpoint_size_bytes(state: CheckpointedState) -> int:
    """What this state costs to write, encoded as the checkpointer stores it."""
    return len(state.model_dump_json().encode())


def within_limit(state: CheckpointedState, limit: int = CHECKPOINT_SIZE_LIMIT_BYTES) -> bool:
    return checkpoint_size_bytes(state) <= limit


def size_report(state: CheckpointedState, limit: int = CHECKPOINT_SIZE_LIMIT_BYTES) -> str:
    """AC 4's figure, with the arithmetic that makes it a bound rather than a sample."""
    size = checkpoint_size_bytes(state)
    experiments = len(state.experiments)
    verdict = "within" if size <= limit else "OVER"
    return (
        f"Checkpoint: {size} bytes over {experiments} experiments — {verdict} the "
        f"{limit}-byte limit.\n"
        f"  Bounded by construction: {MAX_EXPERIMENTS} experiments x {MAX_REFERENCE_BYTES} bytes "
        f"per reference = {MAX_EXPERIMENTS * MAX_REFERENCE_BYTES} bytes of log, whatever the "
        "measurements were.\n"
        "  Full results are in the replay cache, fetched by reference (08-audit.md F13)."
    )

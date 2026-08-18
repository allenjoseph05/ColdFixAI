"""S-6.3 — the checkpoint holds hashes and summaries, the cache holds results.

`08-audit.md` F13's claim is quantitative — *forty experiments × full measurement
output, checkpointed after every node, is megabytes of duplicated writes* — so
these tests measure it rather than repeat it, and the by-value case is kept as a
control. Without it, a limit of 64 KiB proves nothing: it would pass equally for
a design that stored everything and happened to be tested with small results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel, JsonValue, ValidationError

from coldfix.replay.cache import (
    Determinism,
    ExperimentKey,
    ExperimentSpec,
    Recall,
    ReplayCache,
    ResultTypeError,
)
from coldfix.state.checkpoint import CheckpointedState
from coldfix.state.reference import (
    CHECKPOINT_SIZE_LIMIT_BYTES,
    MAX_EXPERIMENTS,
    MAX_REFERENCE_BYTES,
    ExperimentRef,
    ReferenceTooLargeError,
    ResultNotStoredError,
    checkpoint_size_bytes,
    reference,
    resolve,
    size_report,
    within_limit,
)

WHEN = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def as_json(model: BaseModel) -> JsonValue:
    """`model_dump(mode="json")` types as `dict[str, Any]`; the state wants JSON."""
    return cast("JsonValue", model.model_dump(mode="json"))


class Measurement(BaseModel):
    """A measurement of the shape a real primitive returns: mostly output."""

    workload: str
    points: list[dict[str, float]]
    stdout: str


def measurement(size: int = 30_000) -> Measurement:
    return Measurement(
        workload="api.books.list",
        points=[
            {"scale": float(n), "seconds": 0.1 * n, "queries": float(n)} for n in (10, 40, 160)
        ],
        stdout="x" * size,
    )


def key_for(index: int) -> ExperimentKey:
    return ExperimentKey(
        repo_sha="9c68122+deadbeef",
        workload_id="api.books.list",
        experiment_spec=ExperimentSpec(
            primitive="scale_volume",
            parameters={"scales": [10, 40, 160], "run": index},
        ),
        fixture_hash="f" * 64,
    )


def ref_for(
    index: int, outcome: str = "quadratic in queries; 87% of cost localized"
) -> ExperimentRef:
    return ExperimentRef(
        index=index,
        key=key_for(index),
        outcome=outcome,
        recorded_at=WHEN,
        determinism=Determinism.DETERMINISTIC,
        hit=False,
    )


def state_of(count: int) -> CheckpointedState:
    return CheckpointedState(experiments=[as_json(ref_for(index)) for index in range(1, count + 1)])


# ================================== AC 1: hashes and summaries, not full results


def test_a_reference_carries_no_measurement() -> None:
    """The whole of F13 in one assertion: what a checkpoint holds is identity."""
    stored = ref_for(1)

    encoded = stored.model_dump_json()

    assert "stdout" not in encoded
    assert "x" * 100 not in encoded
    assert set(ExperimentRef.model_fields) == {
        "index",
        "key",
        "outcome",
        "recorded_at",
        "determinism",
        "hit",
    }


def test_a_reference_holds_the_hash_f13_names() -> None:
    stored = ref_for(1)

    assert stored.digest == key_for(1).digest()
    assert len(stored.digest) > 0


def test_the_summary_is_composed_from_what_ran_not_authored() -> None:
    """S-5.8's construction, and F6's reason: the header comes from the primitive
    and the target, so only the outcome is supplied."""
    summary = ref_for(7).summary()

    assert summary.startswith("experiment 7 — scale_volume of api.books.list")
    assert "87% of cost localized" in summary


def test_an_outcome_that_spans_lines_is_refused() -> None:
    with pytest.raises(ValidationError, match="spans multiple lines"):
        ref_for(1, outcome="line one\nline two")


def test_an_outcome_longer_than_the_pruning_budget_is_refused() -> None:
    """Bounded by S-5.8's figure, which F13 says to align with."""
    with pytest.raises(ValidationError):
        ref_for(1, outcome="x" * 500)


def test_the_index_is_one_based_like_the_pruned_log() -> None:
    """`read_experiment(7)` has to mean the seventh in both logs."""
    with pytest.raises(ValidationError):
        ref_for(0)


def test_a_state_full_of_references_contains_no_measurement() -> None:
    encoded = state_of(40).model_dump_json()

    assert "stdout" not in encoded
    assert "x" * 100 not in encoded


# ============================================ AC 2: full results in the replay cache


def test_the_result_is_fetched_back_from_the_cache(tmp_path: Path) -> None:
    """F13's tool call: the state points, the cache holds."""
    cache = ReplayCache(tmp_path / "recordings")
    key = key_for(1)
    recalled = cache.run(key, Measurement, measurement, determinism=Determinism.DETERMINISTIC)
    stored = reference(recalled, key, index=1, outcome="quadratic in queries")

    fetched = resolve(cache, stored, Measurement)

    assert fetched.value.stdout == "x" * 30_000
    assert fetched.value.points[0]["scale"] == 10


def test_fetching_a_result_the_store_does_not_hold_is_refused(tmp_path: Path) -> None:
    """The state and the store disagreeing gets a name of its own — S-5.1
    partitions by machine, so a checkpoint carried across machines lands here."""
    cache = ReplayCache(tmp_path / "recordings")

    with pytest.raises(ResultNotStoredError, match="not in this store"):
        resolve(cache, ref_for(1), Measurement)


def test_the_refusal_names_the_experiment_and_its_digest(tmp_path: Path) -> None:
    cache = ReplayCache(tmp_path / "recordings")

    with pytest.raises(ResultNotStoredError) as raised:
        resolve(cache, ref_for(3), Measurement)

    assert "experiment 3" in str(raised.value)
    assert key_for(3).digest() in str(raised.value)


def test_fetching_with_the_wrong_result_type_still_raises(tmp_path: Path) -> None:
    """S-5.1's refusal survives the indirection rather than being swallowed."""
    cache = ReplayCache(tmp_path / "recordings")
    key = key_for(1)
    cache.run(key, Measurement, lambda: measurement(100), determinism=Determinism.DETERMINISTIC)

    with pytest.raises(ResultTypeError):
        resolve(cache, ref_for(1), CheckpointedState)


def test_a_reference_records_whether_the_number_was_replayed(tmp_path: Path) -> None:
    """*When* has to survive with the number, and without a disk read."""
    cache = ReplayCache(tmp_path / "recordings")
    key = key_for(1)

    first = cache.run(
        key, Measurement, lambda: measurement(100), determinism=Determinism.DETERMINISTIC
    )
    second = cache.run(
        key, Measurement, lambda: measurement(100), determinism=Determinism.DETERMINISTIC
    )

    assert not reference(first, key, index=1, outcome="measured").hit
    replayed = reference(second, key, index=2, outcome="replayed")
    assert replayed.hit
    assert "replayed from" in replayed.provenance()


# ============================================= AC 3: bounded as the log grows


def test_the_checkpoint_size_does_not_depend_on_the_measurement_size(
    tmp_path: Path,
) -> None:
    """The sharpest statement of what storing by reference means.

    Two investigations identical but for the size of what they measured — one
    100 bytes of output per experiment, one 100 kilobytes — produce checkpoints
    of exactly the same size. A design that stored results would differ by
    roughly four megabytes here.
    """
    cache = ReplayCache(tmp_path / "recordings")
    sizes: list[int] = []
    for output in (100, 100_000):
        refs: list[JsonValue] = []
        for index in range(1, 11):
            key = key_for(index)
            measured = measurement(output)

            def compute(made: Measurement = measured) -> Measurement:
                return made

            recalled = cache.run(key, Measurement, compute, determinism=Determinism.SAMPLED)
            refs.append(as_json(reference(recalled, key, index=index, outcome="linear")))
        sizes.append(checkpoint_size_bytes(CheckpointedState(experiments=refs)))

    assert sizes[0] == sizes[1]


def test_a_reference_too_large_to_bound_the_checkpoint_is_refused() -> None:
    """AC 3 as a guarantee rather than an observation.

    A spec carrying a large parameter block would otherwise grow the checkpoint
    with nothing noticing, and the stated limit rests on each reference fitting.
    """
    with pytest.raises(ReferenceTooLargeError, match="bytes and the limit is"):
        ExperimentRef(
            index=1,
            key=ExperimentKey(
                repo_sha="abc",
                workload_id="api.books.list",
                experiment_spec=ExperimentSpec(
                    primitive="scale_volume",
                    parameters={"corpus": ["x" * 100 for _ in range(50)]},
                ),
                fixture_hash="f" * 64,
            ),
            outcome="linear",
            recorded_at=WHEN,
            determinism=Determinism.SAMPLED,
            hit=False,
        )


def test_an_ordinary_reference_fits_the_per_entry_budget() -> None:
    """The control: the guard above must not be refusing everything."""
    assert len(ref_for(1).model_dump_json().encode()) <= MAX_REFERENCE_BYTES


def test_the_log_grows_linearly_and_slowly() -> None:
    ten, forty = checkpoint_size_bytes(state_of(10)), checkpoint_size_bytes(state_of(40))

    per_experiment = (forty - ten) / 30
    assert per_experiment <= MAX_REFERENCE_BYTES


# ============================ AC 4: forty experiments under a stated limit


def test_forty_experiments_stay_under_the_stated_limit() -> None:
    """AC 4. The limit is `CHECKPOINT_SIZE_LIMIT_BYTES`, stated in the module."""
    state = state_of(MAX_EXPERIMENTS)

    assert within_limit(state)
    assert checkpoint_size_bytes(state) < CHECKPOINT_SIZE_LIMIT_BYTES


def test_the_limit_has_arithmetic_behind_it_rather_than_a_sample() -> None:
    """40 references that each cannot exceed 1 KiB cannot exceed 40 KiB."""
    assert MAX_EXPERIMENTS * MAX_REFERENCE_BYTES < CHECKPOINT_SIZE_LIMIT_BYTES


def test_storing_the_same_forty_by_value_blows_past_the_limit() -> None:
    """F13's *megabytes of duplicated writes*, measured rather than repeated.

    The control that makes the limit mean something: the same investigation with
    results in the state instead of references.
    """
    by_value = CheckpointedState(
        experiments=[as_json(measurement()) for _ in range(MAX_EXPERIMENTS)]
    )

    size = checkpoint_size_bytes(by_value)

    assert not within_limit(by_value)
    # 1.21 MB at 30 KB of output per experiment, which is a conservative
    # measurement dump — F13's "megabytes", and ~18x the limit.
    assert size > 1_000_000
    assert size > 15 * CHECKPOINT_SIZE_LIMIT_BYTES


def test_the_size_report_states_the_limit_and_the_bound() -> None:
    report = size_report(state_of(MAX_EXPERIMENTS))

    assert "within" in report
    assert str(CHECKPOINT_SIZE_LIMIT_BYTES) in report
    assert "whatever the measurements were" in report


def test_the_size_report_says_so_when_a_state_is_over() -> None:
    over = CheckpointedState(experiments=[as_json(measurement()) for _ in range(MAX_EXPERIMENTS)])

    assert "OVER" in size_report(over)


def test_the_json_figure_over_estimates_what_the_checkpointer_writes() -> None:
    """The proxy, verified rather than assumed — and it errs the safe way.

    `src/` deliberately does not import LangGraph (S-6.1), so size is measured as
    JSON. LangGraph's serializer is msgpack and is *smaller*, so a state that
    fits this limit fits what is actually written. A proxy that under-estimated
    would give a limit that passed here and was breached on disk, which is the
    only direction that matters.
    """
    state = state_of(MAX_EXPERIMENTS)
    _, blob = JsonPlusSerializer().dumps_typed(state)

    ours = checkpoint_size_bytes(state)
    assert len(blob) <= ours
    # ~85%: close enough that the JSON figure is a useful bound rather than a
    # wildly loose one, which would make the stated limit meaningless.
    assert len(blob) >= 0.7 * ours


def test_a_recall_is_what_builds_a_reference(tmp_path: Path) -> None:
    """A reference cannot describe a measurement nobody made: only `run`
    produces a `Recall`, and only by measuring or by finding a recording."""
    cache = ReplayCache(tmp_path / "recordings")
    key = key_for(1)
    recalled: Recall[Measurement] = cache.run(
        key, Measurement, lambda: measurement(100), determinism=Determinism.SAMPLED
    )

    stored = reference(recalled, key, index=1, outcome="flat")

    assert stored.recorded_at == recalled.recorded_at
    assert stored.determinism is recalled.determinism

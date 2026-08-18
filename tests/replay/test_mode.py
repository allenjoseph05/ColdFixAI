"""S-5.2: the three modes, and the mark that decides whether a replay is honest.

S-5.1 established that a hit returns the right measurement. This file is about
the two questions it left open, and both of them are about *when a replay is the
wrong answer* rather than about whether it works.

**The mark.** Every measurement carries durations, and no duration repeats — so
determinism cannot mean byte-equality without meaning that nothing is ever
deterministic. It means the experiment's *answer* reproduces, it is declared, and
the default is the one that fails closed. The tests hold both directions: a
sampled experiment is never served from a recording while recording, and neither
side of the claim can be overridden by the other.

**The modes.** `RECORD` measures what is missing, `REPLAY` measures nothing, `OFF`
looks at nothing. The property worth having is that the *caller does not branch*
— the same `run` call in all three — because a caller that branched would be
S-15.1 writing `if use_cache:` around every experiment, which is how a study with
the cache disabled by its own acceptance criterion quietly hits one.

The last section is AC 2: for a deterministic experiment, replay is byte-identical
to the original. Asserted on the real artifact screening produces, and as a fixed
point — a value recorded, replayed and recorded again is the same file.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Fit, Growth
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.measurement import CacheControl, MetricKind
from coldfix.primitives.scaling import Distribution, ScalePoint, ScalingResult
from coldfix.replay.cache import (
    Determinism,
    ExperimentKey,
    ExperimentSpec,
    MissingRecordingError,
    ModeError,
    Recording,
    ReplayCache,
    ReplayMode,
)
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.assess import conclude
from coldfix.screening.budget import Plan
from coldfix.screening.growth import SCREENING_SCALES, ScreenedWorkload, screen_growth
from coldfix.screening.workload import (
    RESPONSE_BYTES,
    BoundWorkload,
    FixtureRecipe,
    Workload,
)
from fixtures.planted.queries import (
    list_books_batched,
    list_books_n_plus_one,
    list_titles_narrow,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"
SHA = "0" * 40

DETERMINISTIC = Determinism.DETERMINISTIC
SAMPLED = Determinism.SAMPLED


def recipe(**overrides: Any) -> FixtureRecipe:
    fields: dict[str, Any] = {
        "entity": "author",
        "per_parent": 2,
        "distribution": Distribution.UNIFORM,
        "source": "fixtures.planted.store.build_store",
        "seed": 0,
    }
    return FixtureRecipe(**{**fields, **overrides})


def key(workload: str = "api.books.list", primitive: str = "scale_volume") -> ExperimentKey:
    return ExperimentKey(
        repo_sha=SHA,
        workload_id=workload,
        experiment_spec=ExperimentSpec(primitive=primitive, parameters={"scales": [10, 40, 160]}),
        fixture_hash=recipe().digest(),
    )


def result(seconds: float = 0.25) -> ScalingResult:
    fit = Fit(1.0, 2.0, 0.999, 1.0, 0.998, Growth.LINEAR, 0.2, 1.2)
    return ScalingResult(
        baseline={"seconds": 0.001953125, DB_QUERY: 2.0},
        points=tuple(
            ScalePoint(
                scale=scale,
                raw={"seconds": seconds * scale, DB_QUERY: 40.0 * scale},
                adjusted={"seconds": seconds * scale, DB_QUERY: 40.0 * scale},
            )
            for scale in SCREENING_SCALES
        ),
        fits={"seconds": fit, DB_QUERY: fit},
        kinds={"seconds": MetricKind.DURATION, DB_QUERY: MetricKind.COUNT},
        reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
        cache_control=CacheControl.BOTH,
        distribution=Distribution.UNIFORM,
    )


class Counted:
    """A compute that reports how often it was actually called, with a moving answer.

    The moving answer matters. A stub returning a constant makes *replayed* and
    *re-run* produce the same value, so a test that only compared results would
    pass either way — which is the whole question this file is about.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> ScalingResult:
        self.calls += 1
        return result(seconds=float(self.calls))


# ---------------------------------------------------- AC 3: the mark, both ways


def test_a_deterministic_experiment_is_measured_once(tmp_path: Path) -> None:
    """The claim that buys the cache anything at all."""
    compute = Counted()
    cache = ReplayCache(tmp_path)

    first = cache.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)
    second = cache.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    assert compute.calls == 1
    assert second.hit is True
    assert second.value == first.value


def test_a_sampled_experiment_is_re_run_every_time(tmp_path: Path) -> None:
    """AC 3. Not *ignored* — re-run, and its recording refreshed.

    A load curve, an interleaved timing comparison and a fuzzing campaign all
    answer differently on a second run. Serving one of them from a recording
    would hand a fresh investigation an afternoon's sample as a standing fact,
    and it would be the cheapest possible way to get a stable wrong answer.
    """
    compute = Counted()
    cache = ReplayCache(tmp_path)

    first = cache.run(key(), ScalingResult, compute, determinism=SAMPLED)
    second = cache.run(key(), ScalingResult, compute, determinism=SAMPLED)

    assert compute.calls == 2
    assert second.hit is False
    assert second.value != first.value


def test_a_sampled_experiment_is_still_recorded(tmp_path: Path) -> None:
    """Marked and re-run, not marked and discarded.

    The recording is what replay mode plays back, and it is evidence of what
    happened whether or not it may be reused.
    """
    cache = ReplayCache(tmp_path)
    cache.run(key(), ScalingResult, Counted(), determinism=SAMPLED)

    held = cache.recordings()

    assert [stored.determinism for stored in held] == [SAMPLED]


def test_the_re_run_replaces_the_recording_rather_than_keeping_the_first(
    tmp_path: Path,
) -> None:
    """A sampled recording is the last thing that happened, not the first.

    Replay mode exists to reproduce a session, so the recording has to be the
    session's own measurement — keeping the oldest would replay an experiment
    from a run nobody asked about.
    """
    compute = Counted()
    cache = ReplayCache(tmp_path)
    cache.run(key(), ScalingResult, compute, determinism=SAMPLED)
    latest = cache.run(key(), ScalingResult, compute, determinism=SAMPLED)

    replayed = ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key(), ScalingResult)

    assert replayed.value == latest.value


def test_an_undeclared_experiment_is_treated_as_sampled(tmp_path: Path) -> None:
    """The default fails closed, and it costs time rather than correctness.

    An experiment nobody classified is one nobody has thought about. Defaulting
    the other way would spend the very first unconsidered call site on a silently
    stale answer, and there is no signal anywhere that it happened.
    """
    compute = Counted()
    cache = ReplayCache(tmp_path)

    cache.run(key(), ScalingResult, compute)
    cache.run(key(), ScalingResult, compute)

    assert compute.calls == 2


def test_a_recording_made_from_a_sample_is_not_replayed_for_a_later_claim(
    tmp_path: Path,
) -> None:
    """One half of *the stricter of the two wins*.

    A caller who has decided their experiment is deterministic must not thereby
    promote somebody else's sample. The recording carries what it was taken as,
    and that half of the decision is not the current caller's to make.
    """
    compute = Counted()
    ReplayCache(tmp_path).run(key(), ScalingResult, compute, determinism=SAMPLED)

    later = ReplayCache(tmp_path)
    recalled = later.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    assert compute.calls == 2
    assert recalled.hit is False


def test_a_caller_expecting_a_sample_does_not_get_a_deterministic_recording(
    tmp_path: Path,
) -> None:
    """The other half, and the one that looks harmless.

    If this experiment is a sample, then a recording of it is a sample whatever
    the run that made it believed — and the caller has said they need a fresh
    one. Serving the recording would be the cache overruling the only person who
    knows what the number is for.
    """
    compute = Counted()
    ReplayCache(tmp_path).run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    later = ReplayCache(tmp_path)
    recalled = later.run(key(), ScalingResult, compute, determinism=SAMPLED)

    assert compute.calls == 2
    assert recalled.hit is False


def test_a_replayed_sample_says_a_fresh_run_would_answer_differently(
    tmp_path: Path,
) -> None:
    """The sentence a report cannot accidentally leave out.

    Replay mode plays a sampled experiment back — that is its job — so the only
    thing standing between the recording and a run report quoting it as a current
    measurement is what the `Recall` says about itself.
    """
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=SAMPLED)

    replayed = ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key(), ScalingResult)

    assert replayed.hit is True
    assert replayed.reproducible is False
    assert "would answer differently" in replayed.provenance()
    assert "not a current measurement" in replayed.provenance()


def test_a_replayed_deterministic_experiment_does_not_carry_that_warning(
    tmp_path: Path,
) -> None:
    """The control. A caveat attached to everything is one nobody reads — Epic
    4's composition check found exactly that with `blocked_seconds`."""
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=DETERMINISTIC)

    replayed = ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key(), ScalingResult)

    assert replayed.reproducible is True
    assert "would answer differently" not in replayed.provenance()
    assert "declared deterministic" in replayed.provenance()


# ------------------------------------------------------- AC 1: the modes


def test_replay_mode_never_runs_the_experiment(tmp_path: Path) -> None:
    """AC 1, and the reason `run` takes the same shape in every mode: the agent
    being debugged is not rewritten to be debugged."""
    compute = Counted()
    ReplayCache(tmp_path).run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    replaying = ReplayCache(tmp_path, mode=ReplayMode.REPLAY)
    recalled = replaying.run(key(), ScalingResult, compute, determinism=SAMPLED)

    assert compute.calls == 1
    assert recalled.hit is True


def test_replay_mode_refuses_an_experiment_it_does_not_hold(tmp_path: Path) -> None:
    """A refusal rather than a fallback to running it.

    There is no subject in a replay session, so *run it instead* is not a slower
    answer — it is a crash somewhere further down with a cause nobody connects to
    the cache. And where a subject does happen to be present, falling back is
    worse: the session being debugged quietly stops being the recorded one.
    """
    ReplayCache(tmp_path).run(key("api.books.list"), ScalingResult, Counted(), determinism=SAMPLED)

    replaying = ReplayCache(tmp_path, mode=ReplayMode.REPLAY)

    with pytest.raises(MissingRecordingError) as refusal:
        replaying.replay(key("api.orders.list"), ScalingResult)

    assert "api.orders.list" in str(refusal.value)


def test_the_refusal_lists_what_the_store_does_hold(tmp_path: Path) -> None:
    """Because the four things that could be wrong look identical from the call
    site: a different working tree, a different fixture, a spec spelled
    differently, or an experiment the recorded session never ran."""
    recording = ReplayCache(tmp_path)
    for name in ("api.books.list", "api.authors.list"):
        recording.run(key(name), ScalingResult, Counted(), determinism=SAMPLED)

    with pytest.raises(MissingRecordingError) as refusal:
        ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key("api.orders.list"), ScalingResult)

    assert "api.books.list" in str(refusal.value)
    assert "api.authors.list" in str(refusal.value)


def test_an_empty_store_says_it_is_empty_rather_than_listing_nothing(tmp_path: Path) -> None:
    """The state a replay session most often starts in — pointed at the wrong
    directory — and the one a bare "not found" describes worst."""
    with pytest.raises(MissingRecordingError, match="this store is empty"):
        ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key(), ScalingResult)


def test_replay_is_refused_outside_replay_mode(tmp_path: Path) -> None:
    """`replay` in a recording cache would answer from a recording at the moment
    a caller had asked to measure, which is the one thing the mark exists to
    stop."""
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=DETERMINISTIC)

    for mode in (ReplayMode.RECORD, ReplayMode.OFF):
        with pytest.raises(ModeError, match=mode.value):
            ReplayCache(tmp_path, mode=mode).replay(key(), ScalingResult)


def test_a_replay_session_needs_no_subject_to_pass_in(tmp_path: Path) -> None:
    """`replay` has no `compute` parameter, and that is structural rather than
    convenient: a caller able to pass one is a caller who still had to build the
    checkout, the container and the database that a debugging session does not
    have."""
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=DETERMINISTIC)

    recalled = ReplayCache(tmp_path, mode=ReplayMode.REPLAY).replay(key(), ScalingResult)

    assert recalled.value == result(seconds=1.0)


def test_off_mode_neither_reads_nor_writes(tmp_path: Path) -> None:
    """S-15.1 runs diagnosis ten times with the cache disabled, because agreement
    between ten replays of one recording is 100% and means nothing.

    Disabled has to mean *neither half*. A mode that stopped reading but went on
    writing would leave the study's own runs in the store for whatever ran next.
    """
    compute = Counted()
    off = ReplayCache(tmp_path, mode=ReplayMode.OFF)

    first = off.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)
    second = off.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    assert compute.calls == 2
    assert first.value != second.value
    assert off.recordings() == ()
    assert off.statistics.recordings == 0


def test_off_mode_does_not_read_a_store_that_already_has_the_answer(tmp_path: Path) -> None:
    """The half that would otherwise be invisible: a disabled cache pointed at a
    populated directory must measure anyway, or ten independent runs are one run
    reported ten times."""
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=DETERMINISTIC)

    compute = Counted()
    off = ReplayCache(tmp_path, mode=ReplayMode.OFF)
    off.run(key(), ScalingResult, compute, determinism=DETERMINISTIC)

    assert compute.calls == 1
    assert off.statistics.hits == 0


def test_replay_mode_never_writes_to_the_store_it_is_reading(tmp_path: Path) -> None:
    """Debugging a run must not destroy the record of when the run happened.

    A replay that wrote back what it played back would restamp every recording
    with today's date, and one asked for as deterministic would promote a sampled
    recording permanently. Refused on `record` rather than merely avoided by
    `run`, so the property does not depend on one early return staying where it
    is.
    """
    ReplayCache(tmp_path).run(key(), ScalingResult, Counted(), determinism=SAMPLED)
    stored = next(iter(sorted(Path(tmp_path).rglob("*.json"))))
    before = stored.read_text(encoding="utf-8")

    replaying = ReplayCache(tmp_path, mode=ReplayMode.REPLAY)
    played = replaying.run(key(), ScalingResult, Counted(), determinism=DETERMINISTIC)

    assert played.hit is True
    assert replaying.statistics.recordings == 0
    assert stored.read_text(encoding="utf-8") == before

    with pytest.raises(ModeError, match="does not write"):
        replaying.record(key(), result(), ScalingResult, determinism=DETERMINISTIC)


def test_a_disabled_cache_refuses_to_record_even_when_asked_directly(tmp_path: Path) -> None:
    """The same rule for S-15.1: disabled means neither half, whichever method a
    caller reaches for."""
    off = ReplayCache(tmp_path, mode=ReplayMode.OFF)

    with pytest.raises(ModeError, match="does not write"):
        off.record(key(), result(), ScalingResult, determinism=DETERMINISTIC)

    assert off.recordings() == ()


def test_the_mode_in_effect_is_readable(tmp_path: Path) -> None:
    """Ten diagnoses that agree is a different number depending on whether the
    cache was off, so a run report has to be able to say which."""
    assert ReplayCache(tmp_path).mode is ReplayMode.RECORD
    assert ReplayCache(tmp_path, mode=ReplayMode.OFF).mode is ReplayMode.OFF


def test_the_inventory_lists_every_experiment_in_the_store(tmp_path: Path) -> None:
    """What replay actually raises — *what is on this disk* — rather than an
    account of an investigation, which is S-8.4's artifact and is not guessed at
    here."""
    cache = ReplayCache(tmp_path)
    cache.run(key("api.books.list"), ScalingResult, Counted(), determinism=DETERMINISTIC)
    cache.run(key("api.authors.list"), ScalingResult, Counted(), determinism=SAMPLED)

    held = {stored.key.workload_id: stored for stored in cache.recordings()}

    assert set(held) == {"api.books.list", "api.authors.list"}
    assert held["api.books.list"].determinism is DETERMINISTIC
    assert held["api.authors.list"].determinism is SAMPLED
    assert held["api.books.list"].result_type.endswith("ScalingResult")


def test_the_inventory_survives_one_unreadable_entry(tmp_path: Path) -> None:
    """One corrupt file must not make it impossible to see the rest, which is the
    state a replay session is in when it most needs to look."""
    cache = ReplayCache(tmp_path)
    cache.run(key("api.books.list"), ScalingResult, Counted(), determinism=DETERMINISTIC)
    cache.run(key("api.authors.list"), ScalingResult, Counted(), determinism=DETERMINISTIC)
    broken = next(iter(sorted(cache.directory.glob("*.json"))))
    broken.write_text("{ truncated", encoding="utf-8")

    held = cache.recordings()

    assert len(held) == 1
    assert cache.statistics.unreadable == 1


# ------------------------------------- AC 2: byte-identical for a deterministic experiment


@pytest.fixture
def query_counter() -> Iterator[None]:
    register_hook(DB_QUERY, calls_to(Store, "select"))
    try:
        yield
    finally:
        unregister_hook(DB_QUERY)


class StoreReset(ResetMechanism):
    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, subject: Subject) -> None:
        self.subject = subject
        self._snapshot: Store | None = None

    def prepare(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def begin(self) -> None:
        self._snapshot = deepcopy(self.subject.store)

    def reset(self) -> None:
        if self._snapshot is None:
            raise ResetNotPreparedError(self.strategy)
        self.subject.store = deepcopy(self._snapshot)


@dataclass
class Subject:
    call: Any
    store: Store = field(default_factory=Store)
    processes: list[str] = field(default_factory=list)

    def scale(self, n: int) -> None:
        self.store = build_store(authors=n, books_per_author=2)

    def invoke(self) -> object:
        return self.call(self.store)

    def process_identity(self) -> str:
        self.processes.append(f"container-{len(self.processes)}")
        return self.processes[-1]

    def payload(self) -> Mapping[str, float]:
        return {
            CELLS: float(self.store.cells_returned),
            DB_ROWS: float(self.store.rows_returned),
            RESPONSE_BYTES: float(self.store.cells_returned * 8),
        }


PLANTED = {
    "n.plus.one": list_books_n_plus_one,
    "batched": list_books_batched,
    "narrow": list_titles_narrow,
}

SCREENED = TypeAdapter(ScreenedWorkload)


def bind(name: str) -> BoundWorkload:
    subject = Subject(PLANTED[name])
    return BoundWorkload(
        Workload(
            id=name,
            description=f"the planted {name} workload",
            entry_point=f"fixtures.planted.queries.{PLANTED[name].__name__}",
            fixture=recipe(),
            reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        ),
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        extra_counters=subject.payload,
    )


def sweep_key(bound: BoundWorkload) -> ExperimentKey:
    return ExperimentKey.of(
        bound.descriptor,
        ExperimentSpec(
            primitive="scale_volume",
            parameters={"scales": list(SCREENING_SCALES), "counters": [DB_QUERY]},
        ),
        repo_sha=SHA,
    )


def screen_through(cache: ReplayCache) -> list[ScreenedWorkload]:
    screened: list[ScreenedWorkload] = []
    for name in PLANTED:
        bound = bind(name)
        screened.append(
            cache.run(
                sweep_key(bound),
                ScreenedWorkload,
                lambda bound=bound: screen_growth(bound, counters=[DB_QUERY]),  # type: ignore[misc]
                determinism=DETERMINISTIC,
            ).value
        )
    return screened


def test_a_replayed_screen_is_byte_identical_to_the_original(
    query_counter: None, tmp_path: Path
) -> None:
    """AC 2, against the artifact screening actually produces.

    Byte-identical rather than equal, because equality is `==` on a dataclass and
    would pass for a float that came back near enough. The bytes are what the
    next process reads and what a diff of two debugging sessions compares.
    """
    original = screen_through(ReplayCache(tmp_path))

    replayed = screen_through(ReplayCache(tmp_path, mode=ReplayMode.REPLAY))

    assert [SCREENED.dump_json(one) for one in replayed] == [
        SCREENED.dump_json(one) for one in original
    ]


def test_recording_a_replayed_value_produces_the_same_file(
    query_counter: None, tmp_path: Path
) -> None:
    """The fixed point, which is the property that makes replay chainable.

    A codec that were lossy by a little would still pass a single round trip and
    drift over a session replayed from a session replayed from a run. Recording
    what came back out has to produce the same bytes that went in.
    """
    cache = ReplayCache(tmp_path)
    screen_through(cache)
    stored = sorted(cache.directory.glob("*.json"))
    assert len(stored) == len(PLANTED)

    for path in stored:
        recording = Recording.model_validate_json(path.read_text(encoding="utf-8"))
        replayed = SCREENED.validate_python(recording.value)

        # Read out and written back, the measurement is the same JSON. Everything
        # else on a recording is about the write — `recorded_at` is when it
        # happened, and asserting on that would be asserting that two writes
        # occurred at the same instant.
        assert SCREENED.dump_python(replayed, mode="json") == recording.value


def test_the_replayed_screen_reaches_the_same_conclusion(
    query_counter: None, tmp_path: Path
) -> None:
    """The end of AC 1: an investigation, not an experiment.

    The agent above the cache is `conclude`, and it runs unchanged — which is the
    whole claim. It is handed replayed measurements and produces the plan the
    live run produced.
    """
    original = conclude(screen_through(ReplayCache(tmp_path)), cap=3)

    replaying = ReplayCache(tmp_path, mode=ReplayMode.REPLAY)
    replayed = conclude(screen_through(replaying), cap=3)

    assert isinstance(original, Plan)
    assert isinstance(replayed, Plan)
    assert replaying.statistics.hits == len(PLANTED)
    assert replaying.statistics.misses == 0
    assert replayed.investigate == original.investigate
    assert replayed.healthy == original.healthy

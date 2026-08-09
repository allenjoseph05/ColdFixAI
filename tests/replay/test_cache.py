"""S-5.1: an experiment measured once, and every way that could return the wrong one.

The cheap half of this file is that a recording goes in and comes back. The half
worth writing is the other one, because every defect a replay cache can have
presents as *a plausible number that belongs to something else* — the wrong
workload, the wrong code, the wrong machine, or a run that was interrupted
halfway through being written down. None of those raise anything.

So the tests are organised by what a hit has to prove:

- it is the same experiment (the key, and the string-join collision that is the
  classic way of losing that);
- it is the same code (`repo_identity`, which is why a commit sha is not enough);
- it is the same machine (the environment partition);
- it is whole (the atomic write and the unreadable entry);
- and it says it is a hit (`Recall`, which is what stops a recording being
  reported as a measurement taken now).

AC 4 is the last section: a screen of the planted repository, recorded once, then
replayed with the socket layer and the subprocess layer removed — no model call
and no container start survives losing either.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import pytest

from coldfix.bench.counting import calls_to, register_hook, unregister_hook
from coldfix.bench.stats import Fit, Growth
from coldfix.primitives.counters import DB_QUERY, DB_ROWS
from coldfix.primitives.measurement import CacheControl, MetricKind
from coldfix.primitives.scaling import Distribution, ScalePoint, ScalingResult
from coldfix.replay.cache import (
    Environment,
    ExperimentKey,
    ExperimentSpec,
    Recall,
    Recording,
    ReplayCache,
    ReplayError,
    RepositoryError,
    ResultTypeError,
    repo_identity,
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
    list_titles_over_fetching,
    render_with_expensive_downstream,
    summarize_with_fixed_floor,
)
from fixtures.planted.store import Store, build_store

CELLS = "cells_returned"

# The AC's ceiling for a hit, in seconds. Well clear of S-0.4's ~20 ms timing
# floor, so a measurement against it means something on this platform.
HIT_CEILING_SECONDS = 0.1

SHA = "0" * 40


def spec(**parameters: Any) -> ExperimentSpec:
    return ExperimentSpec(primitive="scale_volume", parameters=parameters)


def recipe(**overrides: Any) -> FixtureRecipe:
    fields: dict[str, Any] = {
        "entity": "author",
        "per_parent": 2,
        "distribution": Distribution.UNIFORM,
        "source": "fixtures.planted.store.build_store",
        "seed": 0,
    }
    return FixtureRecipe(**{**fields, **overrides})


def descriptor(name: str = "api.books.list", **overrides: Any) -> Workload:
    fields: dict[str, Any] = {
        "id": name,
        "description": f"the {name} workload",
        "entry_point": f"app.views.{name}",
        "fixture": recipe(),
        "reset_method": ResetStrategy.SNAPSHOT_RESTORE,
    }
    return Workload(**{**fields, **overrides})


def key(**overrides: Any) -> ExperimentKey:
    fields: dict[str, Any] = {
        "repo_sha": SHA,
        "workload_id": "api.books.list",
        "experiment_spec": spec(scales=[10, 40, 160]),
        "fixture_hash": recipe().digest(),
    }
    return ExperimentKey(**{**fields, **overrides})


def result(seconds: float = 0.25, queries: float = 40.0) -> ScalingResult:
    """A `ScalingResult` shaped like the ones a real sweep produces."""
    fit = Fit(
        slope=1.0,
        intercept=2.0,
        linear_r_squared=0.999,
        exponent=1.0009765625,
        power_r_squared=0.998,
        growth=Growth.LINEAR,
        constant_below=0.2,
        superlinear_above=1.2,
    )
    return ScalingResult(
        baseline={"seconds": 0.001953125, DB_QUERY: 2.0},
        points=tuple(
            ScalePoint(
                scale=scale,
                raw={"seconds": seconds * scale, DB_QUERY: queries * scale},
                adjusted={
                    "seconds": seconds * scale - 0.001953125,
                    DB_QUERY: queries * scale - 2.0,
                },
            )
            for scale in SCREENING_SCALES
        ),
        fits={"seconds": fit, DB_QUERY: fit},
        kinds={"seconds": MetricKind.DURATION, DB_QUERY: MetricKind.COUNT},
        reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
        cache_control=CacheControl.BOTH,
        distribution=Distribution.UNIFORM,
    )


@pytest.fixture
def cache(tmp_path: Path) -> ReplayCache:
    return ReplayCache(tmp_path / "recordings")


# ------------------------------------------------ a hit is the same experiment


def test_each_of_the_four_key_fields_changes_the_entry() -> None:
    """AC 1, field by field.

    A key that ignores one of its parts is not a weaker cache, it is a cache that
    returns another experiment's numbers — so each field is varied on its own and
    the digest has to move.
    """
    baseline = key().digest()

    assert key(repo_sha="1" * 40).digest() != baseline
    assert key(workload_id="api.books.detail").digest() != baseline
    assert key(experiment_spec=spec(scales=[10, 40, 320])).digest() != baseline
    assert key(fixture_hash=recipe(per_parent=3).digest()).digest() != baseline


def test_two_keys_that_would_join_to_the_same_string_are_two_entries(
    cache: ReplayCache,
) -> None:
    """The failure a digest over concatenated fields has, and it is not subtle.

    `("api.books", "list")` and `("api", "books.list")` join to `api.books.list`
    under any separator that can also occur inside a field — and a dot occurs in
    every workload id this project produces, because the slug rule permits it.
    The hit that results is a measurement of a different workload, returned with
    no indication that anything is wrong.
    """
    left = key(workload_id="api.books", experiment_spec=ExperimentSpec(primitive="list"))
    right = key(workload_id="api", experiment_spec=ExperimentSpec(primitive="books.list"))

    assert left.digest() != right.digest()

    cache.record(left, result(seconds=1.0), ScalingResult)

    assert cache.recall(right, ScalingResult) is None


def test_the_parameter_order_a_caller_happened_to_use_does_not_matter() -> None:
    """Two spellings of one spec are one entry, or the cache misses on itself."""
    forwards = ExperimentSpec(primitive="ablate", parameters={"target": "price", "repeats": 30})
    backwards = ExperimentSpec(primitive="ablate", parameters={"repeats": 30, "target": "price"})

    assert forwards.digest() == backwards.digest()


def test_a_key_digests_the_same_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """The guarantee a digest actually has to make.

    Within one process any hashing scheme is self-consistent, including one
    seeded by `PYTHONHASHSEED`. What a replay cache needs is that tomorrow's
    process finds today's recording, so the assertion is made across a process
    boundary — which is the only place it could fail.
    """
    script = tmp_path / "digest.py"
    script.write_text(
        "from coldfix.replay.cache import ExperimentKey, ExperimentSpec\n"
        "print(\n"
        "    ExperimentKey(\n"
        "        repo_sha='" + SHA + "',\n"
        "        workload_id='api.books.list',\n"
        "        experiment_spec=ExperimentSpec(\n"
        "            primitive='scale_volume', parameters={'scales': [10, 40, 160]}\n"
        "        ),\n"
        "        fixture_hash='abc123',\n"
        "    ).digest()\n"
        ")\n",
        encoding="utf-8",
    )

    printed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        env=dict(os.environ, PYTHONHASHSEED="1"),
        cwd=Path(__file__).parents[2],
    ).stdout.strip()

    assert printed == key(fixture_hash="abc123").digest()


def test_a_key_takes_the_workload_id_and_the_fixture_hash_from_one_artifact() -> None:
    """The two fields that describe the subject are derived together.

    Passed separately they can disagree — a workload's id beside another
    fixture's digest is a key nothing will ever look up, and the run that
    recorded it paid full price for an entry it can never find.
    """
    workload = descriptor()
    built = ExperimentKey.of(workload, spec(scales=[10]), repo_sha=SHA)

    assert built.workload_id == workload.id
    assert built.fixture_hash == workload.fixture.digest()


def test_a_workload_reseeded_from_a_different_recipe_is_a_different_experiment() -> None:
    """S-3.3's whole finding, expressed as a cache key: a measurement taken under
    a uniform fixture is a statement about a uniform fixture, so the same
    workload swept under a power-law one must not hit the uniform recording."""
    uniform = ExperimentKey.of(descriptor(), spec(scales=[10]), repo_sha=SHA)
    skewed = ExperimentKey.of(
        descriptor(fixture=recipe(distribution=Distribution.POWER_LAW)),
        spec(scales=[10]),
        repo_sha=SHA,
    )

    assert uniform.digest() != skewed.digest()


@pytest.mark.parametrize(
    "parameters",
    [
        {"threshold": float("nan")},
        {"threshold": float("inf")},
        {"limits": [1.0, float("-inf")]},
        {"nested": {"ceiling": float("nan")}},
    ],
)
def test_a_spec_holding_a_number_json_cannot_represent_is_refused(
    parameters: Mapping[str, Any],
) -> None:
    """`NaN` and `Infinity` are not JSON, and Python writes them anyway.

    An entry recorded with one in it renders to something no other JSON reader
    accepts, so it parses in this process and nowhere else — a cache that hits
    today and stops hitting the moment anything else touches the file.
    """
    with pytest.raises(ValueError, match="not numbers JSON can represent"):
        ExperimentSpec(primitive="scale_volume", parameters=parameters)


# ------------------------------------------------------ a hit is whole (AC 2)


def test_a_recorded_result_comes_back_equal_to_what_went_in(cache: ReplayCache) -> None:
    """AC 2. Not a summary of the measurement — the measurement."""
    original = result()
    cache.record(key(), original, ScalingResult)

    recalled = cache.recall(key(), ScalingResult)

    assert recalled is not None
    assert recalled.value == original


def test_the_floats_come_back_bit_for_bit(cache: ReplayCache) -> None:
    """The one part of a round trip that fails quietly.

    A duration is compared against a threshold and a baseline is subtracted from
    every point, so a value that returns approximately correct changes an
    exponent and therefore a growth classification. `0.1 + 0.2` is stored here
    deliberately: it is the value that is not what it looks like.
    """
    awkward = result(seconds=0.1 + 0.2)
    cache.record(key(), awkward, ScalingResult)

    recalled = cache.recall(key(), ScalingResult)

    assert recalled is not None
    for stored, original in zip(recalled.value.points, awkward.points, strict=True):
        assert stored.raw["seconds"].hex() == original.raw["seconds"].hex()


def test_a_recall_is_a_new_object_rather_than_the_one_that_was_recorded(
    cache: ReplayCache,
) -> None:
    """Two callers recalling one key must not share an object.

    Nothing in this project mutates a result today. The reason to close it now is
    that a shared object makes the *first* such mutation reach into the second
    caller's measurement, and a measurement that changed underneath somebody is
    the hardest kind of wrong number to find.
    """
    original = result()
    cache.record(key(), original, ScalingResult)

    first = cache.recall(key(), ScalingResult)
    second = cache.recall(key(), ScalingResult)

    assert first is not None
    assert second is not None
    assert first.value is not original
    assert first.value is not second.value
    assert first.value == second.value


# ------------------------------------------ a hit says it is one, and nothing runs


def test_a_miss_runs_the_experiment_and_the_next_call_does_not(cache: ReplayCache) -> None:
    """The whole cache, in the form worth using."""
    runs = 0

    def measure() -> ScalingResult:
        nonlocal runs
        runs += 1
        return result()

    first = cache.run(key(), ScalingResult, measure)
    second = cache.run(key(), ScalingResult, measure)

    assert runs == 1
    assert first.hit is False
    assert second.hit is True
    assert second.value == first.value


def test_a_replayed_measurement_says_so(cache: ReplayCache) -> None:
    """`CLAUDE.md`'s first non-negotiable is no finding without a measurement.

    A replayed number is a measurement — it happened — but it did not happen now,
    and a `Recall` is what carries that distinction to whoever writes the report.
    """
    cache.run(key(), ScalingResult, result)
    replayed = cache.run(key(), ScalingResult, result)

    assert "Replayed from a recording" in replayed.provenance()
    assert "Nothing ran" in replayed.provenance()


def test_a_fresh_measurement_does_not_claim_to_be_a_replay(cache: ReplayCache) -> None:
    """The other direction, which is the one that would be flattering."""
    measured = cache.run(key(), ScalingResult, result)

    assert measured.hit is False
    assert "Replayed" not in measured.provenance()
    assert measured.provenance().startswith("Measured")


def test_an_experiment_that_raised_records_nothing(cache: ReplayCache) -> None:
    """A failure has no result, and a cached failure would be permanent."""

    def explode() -> ScalingResult:
        message = "the subject would not start"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError):
        cache.run(key(), ScalingResult, explode)

    assert cache.recall(key(), ScalingResult) is None
    assert cache.statistics.recordings == 0


def test_the_statistics_separate_a_cold_cache_from_a_broken_one(cache: ReplayCache) -> None:
    """A cache that has silently stopped working looks exactly like a cold one
    from the hit rate alone, which is why `unreadable` is counted apart."""
    assert cache.statistics.hit_rate is None

    cache.run(key(), ScalingResult, result)
    cache.run(key(), ScalingResult, result)

    assert cache.statistics == type(cache.statistics)(hits=1, misses=1, recordings=1, unreadable=0)
    assert cache.statistics.hit_rate == 0.5


# ------------------------------------------------------- a hit is from this machine


def test_a_recording_made_on_another_machine_is_not_returned(tmp_path: Path) -> None:
    """The environment partitions the store rather than joining the key.

    A `ScalingResult` is mostly durations, and a duration is a property of the
    CPU and scheduler that produced it. Recording the environment and comparing
    it would leave somebody to decide whether a foreign entry is close enough;
    making it the directory means the question never arises.
    """
    elsewhere = ReplayCache(
        tmp_path,
        environment=Environment(
            system="Linux", machine="aarch64", node="build-07", python="3.12.4"
        ),
    )
    elsewhere.record(key(), result(), ScalingResult)

    here = ReplayCache(tmp_path)

    assert here.recall(key(), ScalingResult) is None


def test_a_second_cache_object_on_the_same_root_finds_the_first_one_s_recordings(
    tmp_path: Path,
) -> None:
    """The point of the whole thing: tomorrow's process replays today's run."""
    ReplayCache(tmp_path).record(key(), result(), ScalingResult)

    recalled = ReplayCache(tmp_path).recall(key(), ScalingResult)

    assert recalled is not None
    assert recalled.hit is True


# --------------------------------------------------- an entry is whole, or it is a miss


def test_a_truncated_recording_is_a_miss_and_is_counted_as_one_that_broke(
    cache: ReplayCache,
) -> None:
    """A process killed mid-write, or a disk that filled.

    Recomputing is always available and always correct, so this is a miss rather
    than an error — but it is counted separately, because a cache in this state
    costs a full run every time and reports a plausible-looking 0% hit rate.
    """
    cache.record(key(), result(), ScalingResult)
    stored = next(cache.directory.glob("*.json"))
    stored.write_text(stored.read_text(encoding="utf-8")[:120], encoding="utf-8")

    assert cache.recall(key(), ScalingResult) is None
    assert cache.statistics.unreadable == 1


def test_a_recording_whose_result_schema_has_moved_on_is_a_miss(cache: ReplayCache) -> None:
    """The entry an older version of this code wrote.

    A field added to `ScalingResult` makes every recording made before it invalid
    rather than corrupt. Both want the same answer — run it again — and neither
    should be an exception a caller has to catch to keep working.
    """
    cache.record(key(), result(), ScalingResult)
    stored = next(cache.directory.glob("*.json"))
    written = json.loads(stored.read_text(encoding="utf-8"))
    del written["value"]["distribution"]
    stored.write_text(json.dumps(written), encoding="utf-8")

    assert cache.recall(key(), ScalingResult) is None
    assert cache.statistics.unreadable == 1


def test_asking_for_the_wrong_result_type_raises_rather_than_evicting(
    cache: ReplayCache,
) -> None:
    """Two callers, one key, two result types is a bug in the caller.

    Treated as a miss it would be invisible and self-inflicting: each call would
    recompute and overwrite the other's recording, so both callers would work,
    neither would ever hit, and the statistics would say the cache is cold.
    """
    cache.record(key(), result(), ScalingResult)

    with pytest.raises(ResultTypeError, match="ScalingResult"):
        cache.recall(key(), ScreenedWorkload)


def test_a_write_that_fails_leaves_the_previous_recording_intact(
    cache: ReplayCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the write is staged and renamed rather than written in place.

    Written in place, a kill halfway through leaves a file that parses as far as
    it goes — and a partial measurement read back as a whole one is a wrong
    number, not an error. The rename is atomic, so a reader sees one recording or
    the other and never half of either.
    """
    good = result(seconds=0.25)
    cache.record(key(), good, ScalingResult)

    def refuse(self: Path, target: Any) -> Path:
        message = "the rename failed"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", refuse)

    with pytest.raises(ReplayError, match="could not be written"):
        cache.record(key(), result(seconds=99.0), ScalingResult)

    surviving = cache.recall(key(), ScalingResult)

    assert surviving is not None
    assert surviving.value == good


# ------------------------------------------------------------------ AC 3: speed


def test_a_hit_returns_in_under_a_tenth_of_a_second(tmp_path: Path) -> None:
    """AC 3, measured against a recording the size real screening produces.

    A toy result would pass this trivially and prove nothing, since the cost of a
    hit is dominated by parsing and validating the value. The subject here is a
    sweep with sixty metrics across six scale points, which is a workload with
    guard counters and off-CPU counters on a repository the Explorer has spread
    itself over.
    """
    cache = ReplayCache(tmp_path)
    fit = Fit(1.0, 2.0, 0.99, 1.0, 0.98, Growth.LINEAR, 0.2, 1.2)
    metrics = {f"metric.{index}": float(index) for index in range(60)}
    large = ScalingResult(
        baseline=metrics,
        points=tuple(
            ScalePoint(scale=scale, raw=metrics, adjusted=metrics)
            for scale in (10, 20, 40, 80, 160, 320)
        ),
        fits=dict.fromkeys(metrics, fit),
        kinds=dict.fromkeys(metrics, MetricKind.COUNT),
        reset_strategy=ResetStrategy.SNAPSHOT_RESTORE,
        cache_control=CacheControl.BOTH,
        distribution=Distribution.UNIFORM,
    )
    cache.record(key(), large, ScalingResult)

    def timed() -> float:
        started = perf_counter()
        cache.recall(key(), ScalingResult)
        return perf_counter() - started

    samples = [timed() for _ in range(5)]

    assert median(samples) < HIT_CEILING_SECONDS


# --------------------------------------------- a hit is from this code (repo_identity)


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "subject"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "views.py").write_text("def list_books():\n    return []\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "first")
    return repo


def test_a_clean_tree_identifies_as_its_commit(repository: Path) -> None:
    """So a recording made on a commit is still found after checking it out."""
    assert repo_identity(repository) == git(repository, "rev-parse", "HEAD")


def test_an_uncommitted_edit_changes_the_identity(repository: Path) -> None:
    """The reason this function exists at all, and the defect it removes.

    `git rev-parse HEAD` does not move for a whole afternoon of editing. A cache
    keyed on it hands back a recording made before the change under test, on
    every lookup — so the fix being debugged appears to do nothing, and the
    faster the cache is the more convincingly it lies.
    """
    before = repo_identity(repository)
    (repository / "views.py").write_text("def list_books():\n    return [1]\n", encoding="utf-8")

    assert repo_identity(repository) != before


def test_two_different_edits_identify_differently(repository: Path) -> None:
    """Names alone are not enough. `git status --porcelain` reports the same
    thing for every edit to one file, so an identity built from it would move
    once and then hold still for the rest of the session."""
    (repository / "views.py").write_text("def list_books():\n    return [1]\n", encoding="utf-8")
    first = repo_identity(repository)
    (repository / "views.py").write_text("def list_books():\n    return [2]\n", encoding="utf-8")

    assert repo_identity(repository) != first


def test_an_edit_reverted_identifies_as_the_clean_tree_again(repository: Path) -> None:
    """Determinism, and the property that makes a recording worth keeping: going
    back to what was committed finds the recordings made against it."""
    clean = repo_identity(repository)
    (repository / "views.py").write_text("changed\n", encoding="utf-8")
    (repository / "views.py").write_text("def list_books():\n    return []\n", encoding="utf-8")

    assert repo_identity(repository) == clean


def test_a_new_untracked_file_changes_the_identity(repository: Path) -> None:
    """A new module is code the experiment ran against, committed or not."""
    before = repo_identity(repository)
    (repository / "serializers.py").write_text("PRICE = 1\n", encoding="utf-8")

    assert repo_identity(repository) != before


def test_an_ignored_directory_does_not_change_the_identity(repository: Path) -> None:
    """A virtualenv is not part of the experiment, and hashing one would cost
    seconds on every key this cache builds."""
    before = repo_identity(repository)
    ignored = repository / "ignored"
    ignored.mkdir()
    (ignored / "wheel.txt").write_text("x" * 10_000, encoding="utf-8")

    assert repo_identity(repository) == before


def test_a_directory_that_is_not_a_repository_is_refused(tmp_path: Path) -> None:
    """Rather than falling back to a constant.

    A constant is a key field that never changes, so every recording ever made
    under it is returned for every later version of the code — the failure this
    whole function exists to prevent, arrived at through the error path.
    """
    with pytest.raises(RepositoryError):
        repo_identity(tmp_path)


# ------------------------------------ AC 4: an investigation replays end to end

LLM_SDKS = frozenset(
    {
        "anthropic",
        "openai",
        "langchain",
        "langgraph",
        "litellm",
        "cohere",
        "mistralai",
        "ollama",
        "transformers",
    }
)


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
    invocations: int = 0

    def scale(self, n: int) -> None:
        self.store = build_store(authors=n, books_per_author=2)

    def invoke(self) -> object:
        self.invocations += 1
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
    "over.fetch": list_titles_over_fetching,
    "downstream": render_with_expensive_downstream,
    "batched": list_books_batched,
    "narrow": list_titles_narrow,
    "fixed.floor": summarize_with_fixed_floor,
}


def bind(name: str) -> tuple[BoundWorkload, Subject]:
    subject = Subject(PLANTED[name])
    workload = Workload(
        id=name,
        description=f"the planted {name} workload",
        entry_point=f"fixtures.planted.queries.{PLANTED[name].__name__}",
        fixture=recipe(),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
    )
    bound = BoundWorkload(
        workload,
        invoke=subject.invoke,
        scale=subject.scale,
        reset=VerifiedReset(
            mechanism=StoreReset(subject),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        process_identity=subject.process_identity,
        extra_counters=subject.payload,
    )
    return bound, subject


def investigate(cache: ReplayCache) -> tuple[Plan, list[Subject]]:
    """Screen the planted repository through the cache, then decide.

    The decision half is `conclude` rather than `assess`, which S-4.1 and Epic
    4's composition check split apart for exactly this: the cache keys on the
    measurements, so re-deciding a screen has to be possible without re-running
    it.
    """
    sweep = spec(
        scales=list(SCREENING_SCALES),
        counters=[DB_QUERY],
        distribution=Distribution.UNIFORM.value,
    )

    screened: list[ScreenedWorkload] = []
    subjects: list[Subject] = []
    for name in PLANTED:
        bound, subject = bind(name)
        subjects.append(subject)
        recalled = cache.run(
            ExperimentKey.of(bound.descriptor, sweep, repo_sha=SHA),
            ScreenedWorkload,
            lambda bound=bound: screen_growth(bound, counters=[DB_QUERY]),  # type: ignore[misc]
        )
        screened.append(recalled.value)

    outcome = conclude(screened, cap=6)
    assert isinstance(outcome, Plan)
    return outcome, subjects


def test_a_recorded_investigation_replays_with_the_world_removed(
    query_counter: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 4, and the assertion is that the mechanisms are gone rather than unused.

    A model call needs a socket, whichever SDK makes it. A container start needs
    a subprocess, whichever way `docker run` is reached. Removing both is
    stronger than counting calls, because a count only covers the routes somebody
    thought to count — and it is the same argument S-4.2 made for walking the
    import graph instead of asserting no client was configured.
    """
    recorded, _ = investigate(ReplayCache(tmp_path))

    def refuse(*args: object, **kwargs: object) -> None:
        message = "a replay reached for the network or for a process, and has no reason to"
        raise AssertionError(message)

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)

    replayed, _ = investigate(ReplayCache(tmp_path))

    assert replayed.investigate == recorded.investigate
    assert replayed.deferred == recorded.deferred
    assert replayed.healthy == recorded.healthy


def test_a_replay_never_touches_the_subject(query_counter: None, tmp_path: Path) -> None:
    """The other half of *zero container starts*, from the subject's side.

    A replay that quietly re-seeded the database would still be fast, and would
    still be the ninety minutes this story exists to avoid. The bound workloads
    on the second pass are fresh objects, so a run that touched any of them shows
    up as an invocation they should not have.
    """
    investigate(ReplayCache(tmp_path))

    _, subjects = investigate(ReplayCache(tmp_path))

    assert [subject.invocations for subject in subjects] == [0] * len(PLANTED)
    assert [subject.processes for subject in subjects] == [[] for _ in PLANTED]


def test_the_replay_layer_cannot_reach_a_model_sdk(tmp_path: Path) -> None:
    """S-4.2's structural form, applied to this package.

    A cache that *imported* an SDK could call one whether or not it did on any
    given run, and "it did not this time" is not a property a test can hold on
    to. The import graph is walked in a clean interpreter instead.
    """
    script = tmp_path / "imports.py"
    script.write_text(
        "import sys\nimport coldfix.replay.cache\nprint('\\n'.join(sorted(sys.modules)))\n",
        encoding="utf-8",
    )

    loaded = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.split()

    roots = {name.split(".")[0] for name in loaded}
    assert not (roots & LLM_SDKS)


def test_a_replayed_screen_carries_the_date_it_was_actually_measured(
    query_counter: None, tmp_path: Path
) -> None:
    """The composition's version of the honesty rule.

    A run report assembled from replayed screens must be able to say when the
    numbers were taken, or it will say *measured* about a machine-week ago.
    """
    cache = ReplayCache(tmp_path)
    bound, _ = bind("n.plus.one")
    sweep = spec(scales=list(SCREENING_SCALES), counters=[DB_QUERY])
    entry = ExperimentKey.of(bound.descriptor, sweep, repo_sha=SHA)

    before = datetime.now(UTC)
    cache.run(entry, ScreenedWorkload, lambda: screen_growth(bound, counters=[DB_QUERY]))
    after = datetime.now(UTC)

    replayed = ReplayCache(tmp_path).recall(entry, ScreenedWorkload)

    assert replayed is not None
    assert replayed.hit is True
    # Bracketed on both sides. A lower bound alone passes for a `recall` that
    # stamps the moment of the replay, which is the wrong date and the flattering
    # one: it would make every recording look as though it were taken just now.
    assert before <= replayed.recorded_at <= after
    assert replayed.environment == Environment.current()


def test_a_screened_workload_survives_the_round_trip_whole(
    query_counter: None, tmp_path: Path
) -> None:
    """AC 2 against the artifact screening actually produces, rather than a
    hand-built one: the workload with its observations, every metric's fit, and
    the conditions the sweep was taken under."""
    cache = ReplayCache(tmp_path)
    bound, _ = bind("n.plus.one")
    entry = key(workload_id="n.plus.one", fixture_hash=bound.descriptor.fixture.digest())

    measured = cache.run(
        entry, ScreenedWorkload, lambda: screen_growth(bound, counters=[DB_QUERY])
    ).value
    replayed = ReplayCache(tmp_path).recall(entry, ScreenedWorkload)

    assert replayed is not None
    assert replayed.value == measured
    assert replayed.value.metric(DB_QUERY).growth is Growth.LINEAR
    assert replayed.value.workload.work_verified == measured.workload.work_verified
    assert replayed.value.cache_control is measured.cache_control


def test_a_recording_is_readable_by_something_that_is_not_this_code(
    query_counter: None, tmp_path: Path
) -> None:
    """The debugging property the whole story rests on.

    S-5.2 replays recordings to debug downstream agents, and the first thing
    anybody does with a recording that produced a surprising answer is open it.
    A pickled blob would be faster and would make that impossible.
    """
    cache = ReplayCache(tmp_path)
    bound, _ = bind("n.plus.one")
    cache.run(key(), ScreenedWorkload, lambda: screen_growth(bound, counters=[DB_QUERY]))

    written = json.loads(next(cache.directory.glob("*.json")).read_text(encoding="utf-8"))

    assert written["key"]["workload_id"] == "api.books.list"
    assert written["result_type"].endswith("ScreenedWorkload")
    assert DB_QUERY in written["value"]["result"]["fits"]


def test_a_recording_is_what_the_model_says_it_is(cache: ReplayCache) -> None:
    """The stored envelope round-trips as a `Recording`, so S-5.2 has a schema to
    read rather than a shape it has to infer from this module's source."""
    cache.record(key(), result(), ScalingResult)

    stored = Recording.model_validate_json(
        next(cache.directory.glob("*.json")).read_text(encoding="utf-8")
    )

    assert stored.key == key()
    assert stored.environment == Environment.current()
    assert stored.result_type.endswith("ScalingResult")


def test_a_recall_reports_what_it_is_without_being_asked_twice() -> None:
    """`Recall` is the type every call site sees, so its two states are checked
    directly rather than only through the cache that produces them."""
    environment = Environment(system="Linux", machine="x86_64", node="lab-1", python="3.12.4")
    when = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    replayed: Recall[int] = Recall(value=1, hit=True, recorded_at=when, environment=environment)
    measured: Recall[int] = Recall(value=1, hit=False, recorded_at=when, environment=environment)

    assert "lab-1" in replayed.provenance()
    assert "Nothing ran" in replayed.provenance()
    assert "Nothing ran" not in measured.provenance()

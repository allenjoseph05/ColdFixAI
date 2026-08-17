"""The replay cache: an experiment measured once, returned without running again.

Epic 5, S-5.1. `04-cost.md` §6 gives the key verbatim — `(repo_sha, workload_id,
experiment_spec, fixture_hash)` — and the value as the full measurement result.
Everything here is about the two ways that can go wrong, because a cache that
merely works is easy and a cache that cannot lie is the whole story.

**A hit must be the same experiment.** The key is a structured object hashed
through canonical JSON, never a joined string: `("api.books", "list")` and
`("api", "books.list")` join to the same text under any separator that can also
appear in a field, and the failure is a *measurement of one workload returned for
another*. Three of the four fields are also derivable rather than declared —
`ExperimentKey.of` takes the `Workload` artifact and reads the id and the fixture
digest off it together, so the pair cannot disagree.

**A hit must be admitted.** `run` returns a `Recall`, not a bare result, and
`Recall.hit` says whether anything ran. A replayed measurement is still a
measurement — it happened, on a recorded date, on a recorded machine — but it did
not happen now, and a caller that cannot tell the difference will eventually
publish one as though it had. This is the rule the rest of the project already
follows: exclusions carry their preconditions.

**What the key cannot check, and what is done about it instead.**

`experiment_spec` is *declared*. Nothing here can verify that a caller listed
every parameter that determines the result, and a spec missing one produces a hit
from a different experiment, silently. Two things narrow that:

- `repo_sha` is expected to come from `repo_identity`, which is a working-tree
  identity rather than a commit sha. During development the commit does not
  change for hours while the code changes constantly, so a cache keyed on
  `git rev-parse HEAD` returns a stale answer for every uncommitted edit — which
  is precisely the loop this story exists to speed up, poisoned.
- the environment is not part of the key at all; it partitions the *store*. A
  duration recorded on another machine is not this machine's duration, and
  making it a directory rather than a key field means a foreign recording
  **misses** rather than being weighed against a key it matches.

**A cached experiment's side effects do not happen.** A hit skips the seeding,
the reset cycle and the run. Anything downstream that depends on the subject
being in the state the experiment left it in must not sit behind this cache.

---

**S-5.2 adds the mode and the mark, and neither is a new code path.**

`ReplayMode` decides what `run` does with the store — measure what is missing,
never measure, or never look — and every caller above here makes the same call in
all three. That is what makes replay a way of debugging *the agent that calls
this* rather than a second route through it: the agent runs unchanged and every
experiment it asks for is answered from disk.

`Determinism` is the mark, and it is a claim about the **answer**, not about the
bytes. Every measurement carries durations and no duration repeats, so a
byte-equality reading would make nothing reproducible and no hit ever legitimate.
While recording, a sampled experiment is re-run and its recording refreshed;
while replaying it is played back, because the two modes are doing opposite jobs
— one must not treat an afternoon's sample as a standing fact, the other must not
let the session being debugged stop being the session that was recorded.

There is deliberately **no artifact here describing an investigation**. The
ordered account of what was asked and why — hypothesis, design, verdict — is
S-8.4's append-only experiment log, and inventing a second one would be guessing
at a schema that story already specifies (the argument S-1.7 recorded for leaving
`Certification` unlogged). What this module answers instead is the question replay
actually raises: `recordings()` says what is on the disk.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from coldfix.screening.workload import Workload

# Frozen and closed for S-4.1's reason, and it matters more here than usual: a
# key with a misspelled field would otherwise construct fine, silently drop the
# value, and hash to something that collides with every other key sharing the
# same mistake.
_STRICT = ConfigDict(frozen=True, extra="forbid")

# How much of the working tree's dirt digest is kept in a repo identity. It is
# appended to the full commit sha rather than replacing it, so this is a
# readability choice and not a collision budget.
_DIRT_CHARS = 16

_GIT_TIMEOUT_SECONDS = 60.0

# How many of a store's experiments a missing-recording refusal lists before it
# gives a count instead. A production store holds every experiment of every run
# against the repository, and an error that printed all of them would bury the
# sentence saying what went wrong.
_LISTED_ON_A_MISS = 10


class ReplayError(Exception):
    """A recording could not be made, read, or trusted."""


class ResultTypeError(ReplayError):
    """The entry under this key holds a different kind of result.

    Raised rather than reported as a miss. A miss recomputes and overwrites,
    which would turn a caller asking for the wrong type into a silent eviction —
    and two such callers would then evict each other's recordings forever while
    both appeared to be working.
    """


class RepositoryError(ReplayError):
    """The repository's identity could not be established.

    Raised rather than falling back to a constant, because a constant is a key
    field that never changes: every recording made under it would be returned
    for every later version of the code.
    """


class ModeError(ReplayError):
    """This cache is not in a mode where that operation means anything."""


class MissingRecordingError(ReplayError):
    """Replay was asked for an experiment the store does not hold.

    A refusal rather than a fallback to running it. The point of replay mode is
    that there is no subject — no repository checked out, no container, no
    database — so "run it instead" is not a slower answer, it is a crash
    somewhere further down with a cause nobody will connect to the cache. And
    where a subject *does* happen to be present, a silent fallback is worse: the
    session being debugged would quietly stop being the session that was
    recorded.
    """


class Determinism(StrEnum):
    """Whether re-running an experiment gives the same **answer**.

    Read that carefully, because the obvious reading makes this useless. It is
    not a claim about bytes. Every measurement `measure_once` takes carries
    `seconds`, `cpu_seconds` and `blocked_seconds`, and no duration ever repeats
    — so under a byte-equality reading nothing in this system is deterministic,
    nothing may ever be replayed, and S-5.1 was pointless.

    What the two values distinguish is whether the thing the experiment is *for*
    reproduces. A screening sweep concludes from counts, which reproduce to the
    integer, and S-4.3 already forbids a duration from raising a flag on its own;
    an interleaved timing comparison, a load curve or a fuzzing campaign concludes
    from a distribution, and a second run answers differently.

    **It is declared, and nothing here can check it.** Only the primitive knows
    what its own answer rests on. The declaration therefore lives at the call
    site nearest that knowledge, and the default is the safe one.
    """

    DETERMINISTIC = "deterministic"
    """Re-running answers the same. May be replayed."""

    SAMPLED = "sampled"
    """Re-running answers differently. Recorded, never replayed while recording.

    The default, because it fails closed: an experiment nobody classified is
    re-run, which costs time and never costs correctness. The other default
    would spend the first wrong declaration on a silently stale answer.
    """


class ReplayMode(StrEnum):
    """What this cache does with the store. Three behaviours, not a flag.

    A mode rather than a pair of booleans at every call site, because the branch
    that lives in no module is the one Epic 4's composition check found every
    caller reimplementing — and S-15.1's agreement study, whose acceptance
    criterion is *N runs with the cache disabled*, is precisely a caller that
    would otherwise write `if use_cache:` around every experiment.
    """

    RECORD = "record"
    """Measure what is not recorded, replay what is and may be. The normal path."""

    REPLAY = "replay"
    """Never measure. A hit is returned, a miss is refused.

    The debugging mode: the agent under test runs unchanged, and every experiment
    it asks for is answered from disk. **Sampled experiments are replayed here**,
    which is the opposite of what `RECORD` does with them and is deliberate — the
    purpose is to reproduce a session exactly, and an experiment that re-ran would
    make the session being debugged stop being the session that was recorded. The
    `Recall` says which it is.
    """

    OFF = "off"
    """Measure everything, read nothing, write nothing.

    S-15.1 runs diagnosis ten times with the cache disabled, because agreement
    between ten replays of one recording is 100% and means nothing.
    """


class Environment(BaseModel):
    """The machine and interpreter a recording was made on.

    Not part of the key — it names the store the key is looked up in, so a
    recording from elsewhere misses instead of matching. The reason it is here
    at all is that a measurement result is mostly numbers that are properties of
    a machine: `seconds` and `cpu_seconds` are what this CPU did under this
    scheduler, and S-0.4 measured the timing floor on *this* machine at roughly
    20 ms. Query counts would travel; durations recorded beside them would not,
    and they are in the same result.
    """

    model_config = _STRICT

    system: str
    machine: str
    node: str
    """The host. Two machines with the same OS and architecture still differ in
    every duration they record, and nothing cheaper than the hostname separates
    them."""

    python: str

    @classmethod
    def current(cls) -> Environment:
        return cls(
            system=platform.system(),
            machine=platform.machine(),
            node=platform.node(),
            python=".".join(str(part) for part in sys.version_info[:3]),
        )

    def slug(self) -> str:
        """A filesystem-safe directory name for this environment's recordings.

        The readable part is a convenience for whoever opens the cache directory;
        the digest is what actually separates two environments, because the
        readable part is lossy by construction.
        """
        described = f"{self.system}-{self.machine}"
        readable = "".join(
            character if character.isalnum() else "-" for character in described
        ).strip("-")
        return f"{readable.lower()}-{_digest(self.model_dump(mode='json'))[:_DIRT_CHARS]}"


class ExperimentSpec(BaseModel):
    """What was run, and with what settings.

    The one key field that cannot be derived from an artifact, and therefore the
    one a caller can get wrong. It is structured rather than a free string so
    that an omitted parameter is at least *visible* in the recording: opening an
    entry and seeing `{"scales": [10, 40, 160]}` is what lets somebody notice
    that the sweep which recorded it also chose a distribution nobody wrote down.

    Parameters hold JSON, not callables. The seeding function and the invocation
    determine the result and cannot be hashed, which is exactly why `repo_sha`
    has to identify the working tree rather than a commit — the code behind those
    callables is the part of the experiment a spec cannot carry.
    """

    model_config = _STRICT

    primitive: str = Field(min_length=1)
    """The instrument, by the name the registry knows it — `scale_volume`, `ablate`."""

    parameters: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def _representable(cls, parameters: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        unrepresentable = sorted(
            path for name, value in parameters.items() for path in _non_finite(value, name)
        )
        if unrepresentable:
            message = (
                f"these parameters are not numbers JSON can represent: {unrepresentable}. A key "
                "field that cannot be rendered canonically cannot be hashed reproducibly, and a "
                "spec that hashes differently in two processes is a cache that never hits"
            )
            raise ValueError(message)
        return dict(parameters)

    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


class ExperimentKey(BaseModel):
    """`04-cost.md` §6's four fields, hashed as a structure rather than a string.

    The four are not independent. `workload_id` and `fixture_hash` both describe
    the subject and have to describe the *same* subject, so `of` derives them
    together from one `Workload` and that is the constructor to prefer. The plain
    one stays public because a key also has to be rebuildable from a log line,
    where all that survives is strings.
    """

    model_config = _STRICT

    repo_sha: str = Field(min_length=1)
    """What `repo_identity` returns. A bare commit sha is only correct on a clean
    tree, and is wrong in exactly the case this cache is built for."""

    workload_id: str = Field(min_length=1)
    experiment_spec: ExperimentSpec
    fixture_hash: str = Field(min_length=1)

    @classmethod
    def of(cls, workload: Workload, spec: ExperimentSpec, *, repo_sha: str) -> ExperimentKey:
        """The key for running `spec` against `workload` in this working tree."""
        return cls(
            repo_sha=repo_sha,
            workload_id=workload.id,
            experiment_spec=spec,
            fixture_hash=workload.fixture.digest(),
        )

    def digest(self) -> str:
        """The entry's identity, and the name of the file that holds it.

        Over the whole model rendered canonically, which is what keeps
        `workload_id="api.books", experiment_spec.primitive="list"` distinct from
        `workload_id="api", experiment_spec.primitive="books.list"`. Any scheme
        that concatenates the fields makes those one entry, and the wrong half of
        that pair is a measurement of a different workload.
        """
        return _digest(self.model_dump(mode="json"))


class Recording(BaseModel):
    """One stored measurement and everything needed to know what it is.

    Serialized as the file on disk, so its fields are also the debugging surface:
    the whole point of a recording is that somebody can open it.
    """

    model_config = _STRICT

    key: ExperimentKey
    environment: Environment
    recorded_at: datetime
    result_type: str
    """Fully qualified, and checked on the way out. Two experiments producing
    different result types under one key is a caller error rather than a stale
    entry, and the two want opposite treatment."""

    determinism: Determinism = Determinism.SAMPLED
    """What the experiment was recorded as, which is half of what decides a replay.

    Defaulted so that a recording written before this field existed reads as
    sampled — the conservative half — rather than failing to parse and being
    counted as an unreadable entry.
    """

    value: JsonValue


@dataclass(frozen=True)
class StoredExperiment:
    """One entry's identity, without the measurement inside it.

    What `recordings()` returns, and what a missing-recording refusal lists. The
    value is deliberately left behind: enumerating a store to say what is in it
    should not cost the parse of every measurement it holds.
    """

    digest: str
    key: ExperimentKey
    result_type: str
    determinism: Determinism
    recorded_at: datetime

    def describe(self) -> str:
        return (
            f"{self.key.workload_id} / {self.key.experiment_spec.primitive} "
            f"({self.determinism.value}, {self.recorded_at.isoformat(timespec='seconds')})"
        )


@dataclass(frozen=True)
class Recall[T]:
    """A measurement, and whether it happened just now or was played back.

    The reason `run` does not simply return `T`. A bare result makes a recording
    indistinguishable from a fresh run at every call site downstream, and
    `CLAUDE.md`'s first non-negotiable — no finding without a measurement — is
    only meaningful if *when* the measurement happened survives with it.
    """

    value: T
    hit: bool
    recorded_at: datetime
    environment: Environment
    determinism: Determinism = Determinism.SAMPLED

    @property
    def reproducible(self) -> bool:
        """Whether a fresh run is expected to answer the same.

        Expected, not guaranteed: `Determinism` is declared by the caller nearest
        the primitive and nothing in this module can check it.
        """
        return self.determinism is Determinism.DETERMINISTIC

    def provenance(self) -> str:
        """One sentence a report can quote about where this number came from."""
        when = self.recorded_at.isoformat(timespec="seconds")
        if not self.hit:
            return f"Measured {when} on {self.environment.node}."

        played = (
            f"Replayed from a recording made {when} on {self.environment.node} "
            f"({self.environment.system}, Python {self.environment.python}). Nothing ran."
        )
        if self.reproducible:
            return (
                f"{played} The experiment is declared deterministic, so a fresh run is expected "
                "to answer the same."
            )
        # The sentence that has to be here. A sampled experiment replayed is a
        # record of one afternoon, and the mode that returns it is the debugging
        # mode — so anything quoting this as a current measurement is quoting a
        # number that no longer holds.
        return (
            f"{played} This experiment is sampled, so a fresh run would answer differently: "
            "this is a record of what happened, not a current measurement."
        )


@dataclass(frozen=True)
class CacheStatistics:
    """What the cache did, for the run report.

    `unreadable` is counted separately from `misses` although every unreadable
    entry is also a miss. They call for different actions — a miss is an
    experiment that has not been run yet, an unreadable entry is one that was run
    and whose recording is now worthless — and collapsing them would let a cache
    that has silently stopped working forever look like a cold one.
    """

    hits: int = 0
    misses: int = 0
    recordings: int = 0
    unreadable: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float | None:
        """`None` before anything has been looked up, rather than zero.

        Nothing looked up and nothing found are different states, and a cache
        reporting 0% before its first lookup is the kind of number that gets
        quoted.
        """
        return None if self.lookups == 0 else self.hits / self.lookups


class ReplayCache:
    """A directory of recordings, keyed by experiment and partitioned by machine.

    One file per entry, named by the key digest, written atomically. Atomically
    because the alternative failure is specific and bad: a process killed halfway
    through a write leaves a truncated file that parses as far as it goes, and a
    partial measurement read back as a whole one is a wrong number rather than an
    error. `Path.replace` is atomic on both platforms this runs on, so a reader
    sees either the previous recording or the new one.
    """

    def __init__(
        self,
        root: Path,
        *,
        mode: ReplayMode = ReplayMode.RECORD,
        environment: Environment | None = None,
    ) -> None:
        self._mode = mode
        self._environment = environment if environment is not None else Environment.current()
        self._directory = Path(root) / self._environment.slug()
        self._hits = 0
        self._misses = 0
        self._recordings = 0
        self._unreadable = 0

    @property
    def mode(self) -> ReplayMode:
        """Readable, because a report assembled from this cache's results wants it.

        Ten diagnoses that agree is a different number depending on whether the
        cache was off, and a run that does not say which was in effect has left
        out the thing that decides how to read it.
        """
        return self._mode

    @property
    def environment(self) -> Environment:
        return self._environment

    @property
    def directory(self) -> Path:
        """Where this environment's recordings live. Created on first write."""
        return self._directory

    @property
    def statistics(self) -> CacheStatistics:
        return CacheStatistics(
            hits=self._hits,
            misses=self._misses,
            recordings=self._recordings,
            unreadable=self._unreadable,
        )

    def run[T](
        self,
        key: ExperimentKey,
        result_type: type[T],
        compute: Callable[[], T],
        *,
        determinism: Determinism = Determinism.SAMPLED,
    ) -> Recall[T]:
        """Return the recorded result for `key`, or run `compute` and record it.

        The whole cache in one call, and the only form worth using: a caller that
        looks up and records separately has to remember to do the second, and the
        run that gets forgotten is the expensive one.

        **The same call in all three modes**, which is what makes replay a way of
        debugging the agent that calls it rather than a second code path through
        it. `RECORD` measures what is not recorded, `REPLAY` never measures, `OFF`
        always measures. Nothing above here branches.

        `determinism` is the caller's claim about *this* experiment, and a replay
        while recording needs it from both sides — the claim made now and the one
        the recording was made under. The stricter of the two wins, so neither a
        recording made from a sample nor a request that expects one can be served
        from the other.

        `compute` is not called on a hit — not with a short-circuit inside it, not
        under a flag. That is what makes S-5.1's *zero model calls and zero
        container starts* testable by removing the mechanisms rather than by
        counting invocations.

        A `compute` that raises records nothing. An experiment that failed has no
        result, and caching the failure would make it permanent.

        Raises:
            MissingRecordingError: replay mode, and there is no recording.
            ResultTypeError: an entry exists under this key holding another type.
            ReplayError: the recording could not be written.
        """
        if self._mode is ReplayMode.REPLAY:
            return self.replay(key, result_type)

        # The determinism is checked before the store is read, so a sampled
        # experiment does not even look. A lookup that found an entry and then
        # declined to use it would report a hit rate counting answers it refused.
        if self._mode is ReplayMode.RECORD and determinism is Determinism.DETERMINISTIC:
            recalled = self.recall(key, result_type)
            if recalled is not None and recalled.reproducible:
                return recalled

        value = compute()
        if self._mode is not ReplayMode.OFF:
            self.record(key, value, result_type, determinism=determinism)
        return Recall(
            value=value,
            hit=False,
            recorded_at=datetime.now(UTC),
            environment=self._environment,
            determinism=determinism,
        )

    def replay[T](self, key: ExperimentKey, result_type: type[T]) -> Recall[T]:
        """The recording for `key`, refusing if there is none. Replay mode only.

        **There is no `compute` parameter**, and that is the point rather than a
        convenience. A debugging session has no subject — no checkout, no
        container, no database — so a caller able to pass one would be a caller
        who still had to build all of it. The unsafe state has no argument to
        arrive through, which is the construction this project uses for every
        other ordering requirement.

        Sampled experiments are replayed here. `RECORD` re-runs them, because a
        fresh investigation must not treat one afternoon's sample as a standing
        fact; replay is the opposite job — reproducing a session — and an
        experiment that re-ran would make the session being debugged stop being
        the session that was recorded. The `Recall` says which it is.

        Raises:
            ModeError: this cache is recording or off.
            MissingRecordingError: nothing is recorded under this key.
            ResultTypeError: the entry holds a different result type.
        """
        if self._mode is not ReplayMode.REPLAY:
            message = (
                f"this cache is {self._mode.value}, so `replay` is not what it does. Replaying "
                "in either of the other modes would answer from a recording at a moment the "
                "caller had asked to measure"
            )
            raise ModeError(message)

        recalled = self.recall(key, result_type)
        if recalled is None:
            raise MissingRecordingError(self._nothing_recorded(key))
        return recalled

    def recall[T](self, key: ExperimentKey, result_type: type[T]) -> Recall[T] | None:
        """The recorded result for `key`, or `None` if there is nothing usable.

        Three things count as nothing usable, and all three are misses rather
        than errors, because recomputing is always available and always correct:
        no entry, an entry that does not parse, and an entry that no longer
        validates against `result_type` — which is what a changed result schema
        looks like from here. The last two are counted as `unreadable` as well,
        so a cache that has quietly stopped hitting says so in the statistics
        instead of just being slow.

        Raises:
            ResultTypeError: the entry holds a different result type.
        """
        try:
            raw = self._path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            self._misses += 1
            return None
        except OSError as error:
            message = f"the recording for {key.digest()} could not be read: {error}"
            raise ReplayError(message) from error

        try:
            recording = Recording.model_validate_json(raw)
        except ValidationError:
            self._misses += 1
            self._unreadable += 1
            return None

        expected = _type_name(result_type)
        if recording.result_type != expected:
            message = (
                f"the recording under {key.digest()} holds a {recording.result_type} and was "
                f"asked for as a {expected}. This is not a stale entry — the key says the two "
                "callers ran the same experiment on the same workload — so recomputing would "
                "silently evict one of them on every call"
            )
            raise ResultTypeError(message)

        try:
            value: T = _adapter(result_type).validate_python(recording.value)
        except ValidationError:
            self._misses += 1
            self._unreadable += 1
            return None

        self._hits += 1
        return Recall(
            value=value,
            hit=True,
            recorded_at=recording.recorded_at,
            environment=recording.environment,
            determinism=recording.determinism,
        )

    def recordings(self) -> tuple[StoredExperiment, ...]:
        """Every experiment this store holds, oldest first.

        The inventory, not a log. `04-cost.md` §6 makes an entry a measurement
        keyed by experiment, and the ordered account of an investigation — the
        hypothesis behind each experiment, its design, its verdict — is S-8.4's
        artifact and is deliberately not guessed at here. What this answers is
        the question replay actually raises: *what is on this disk*.

        Unreadable entries are skipped rather than raising, since one corrupt
        file must not make it impossible to see the rest, and they are counted.
        """
        found: list[StoredExperiment] = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                recording = Recording.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                self._unreadable += 1
                continue
            found.append(
                StoredExperiment(
                    digest=path.stem,
                    key=recording.key,
                    result_type=recording.result_type,
                    determinism=recording.determinism,
                    recorded_at=recording.recorded_at,
                )
            )
        return tuple(sorted(found, key=lambda stored: (stored.recorded_at, stored.digest)))

    def record[T](
        self,
        key: ExperimentKey,
        value: T,
        result_type: type[T],
        *,
        determinism: Determinism = Determinism.SAMPLED,
    ) -> None:
        """Store `value` as the result of `key`, replacing whatever was there.

        **Only a recording cache writes**, and the other two are refused here
        rather than merely avoided by `run`. Replay is the case that matters: a
        session that wrote back what it played back would stamp every recording
        with today's date, so the act of debugging a run would destroy the record
        of when the run happened — and a replay asked for as deterministic would
        promote a sampled recording permanently. Off is the same rule for
        S-15.1's reason: disabled has to mean neither half, or a study's ten
        independent runs are left in the store for whatever runs next.

        Raises:
            ModeError: this cache is replaying, or off.
            ReplayError: the value could not be serialized, or not written.
        """
        if self._mode is not ReplayMode.RECORD:
            message = (
                f"this cache is {self._mode.value}, so it does not write. A replay that recorded "
                "what it played back would restamp the session it was reading, and a disabled "
                "cache that recorded would leave its runs behind for the next one"
            )
            raise ModeError(message)

        try:
            rendered: JsonValue = _adapter(result_type).dump_python(value, mode="json")
        except (ValueError, TypeError) as error:
            message = (
                f"a {_type_name(result_type)} could not be rendered as JSON, so it cannot be "
                f"recorded: {error}"
            )
            raise ReplayError(message) from error

        recording = Recording(
            key=key,
            environment=self._environment,
            recorded_at=datetime.now(UTC),
            result_type=_type_name(result_type),
            determinism=determinism,
            value=rendered,
        )

        destination = self._path(key)
        # Staged in the same directory as the destination, because `replace` is
        # only atomic within one filesystem and a temporary directory may be on
        # another. The suffix is not `.json`, so a stage left behind by a kill
        # can never be read back as an entry.
        staged = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.part")
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            staged.write_text(recording.model_dump_json(indent=2), encoding="utf-8")
            staged.replace(destination)
        except OSError as error:
            with suppress(OSError):
                staged.unlink(missing_ok=True)
            message = f"the recording for {key.digest()} could not be written: {error}"
            raise ReplayError(message) from error

        self._recordings += 1

    def _nothing_recorded(self, key: ExperimentKey) -> str:
        """Why the replay stopped, and what the store does hold instead.

        A bare "not found" is close to useless here, because the four things that
        could be wrong all look identical from the call site: a different working
        tree, a different fixture, a spec spelled differently, or an experiment
        the recorded session never ran. Listing the store separates them at a
        glance — a workload that appears under a different `repo_sha` is the
        first case, one that does not appear at all is the last.
        """
        held = self.recordings()
        shown = "\n".join(f"  - {stored.describe()}" for stored in held[:_LISTED_ON_A_MISS])
        remainder = len(held) - _LISTED_ON_A_MISS
        if remainder > 0:
            shown = f"{shown}\n  ... and {remainder} more"
        inventory = shown or "  (nothing at all — this store is empty)"

        return (
            f"nothing is recorded for {key.workload_id} / {key.experiment_spec.primitive} under "
            f"repo {key.repo_sha} and fixture {key.fixture_hash} ({key.digest()}), and this "
            f"cache is in replay mode, so there is no subject to measure instead. "
            f"{self._directory} holds:\n{inventory}"
        )

    def _path(self, key: ExperimentKey) -> Path:
        """The file holding this key's recording.

        Named by the digest and nothing else. A name built from the workload id
        would put caller-supplied text into a path, and the digest is already the
        identity — hex, lowercase, and therefore the same file on a
        case-insensitive filesystem as on a case-sensitive one.
        """
        return self._directory / f"{key.digest()}.json"


def repo_identity(repo: Path) -> str:
    """What to pass as `repo_sha`: the working tree, not the commit.

    `git rev-parse HEAD` is the obvious answer and it is wrong for the case this
    cache exists to serve. Development means an uncommitted tree, and the commit
    sha is constant across a whole afternoon of edits — so every lookup would hit
    a recording made before the change under test, and the fix being debugged
    would appear to do nothing. The number this returns changes when the code
    changes, which is the property the key field actually needs.

    Clean trees return the bare sha, so a recording made on a commit is still
    found after checking that commit out again.

    Tracked modifications are hashed from `git diff HEAD` and untracked files
    from their contents, with ignored files left out — a virtualenv is not part
    of the experiment and would make this cost seconds.

    Raises:
        RepositoryError: this is not a repository, or git could not be run.
    """
    head = _git(repo, "rev-parse", "HEAD").decode().strip()

    dirt = hashlib.sha256()
    dirt.update(_git(repo, "diff", "HEAD"))

    # `-z` because it also turns off git's path quoting, so a name that is not
    # UTF-8 arrives as the bytes the filesystem holds rather than as an escape
    # sequence. Hashed as those bytes and decoded only to build a path, which is
    # what `fsdecode` is for.
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    for name in sorted(entry for entry in untracked.split(b"\0") if entry):
        dirt.update(name)
        try:
            dirt.update((repo / os.fsdecode(name)).read_bytes())
        except OSError:
            # Listed by git and unreadable now: a file deleted between the two
            # commands, or a symlink to nowhere. Its name is already in the
            # digest, which is what changed.
            dirt.update(b"<unreadable>")

    if dirt.hexdigest() == _EMPTY_TREE_DIRT:
        return head
    return f"{head}+{dirt.hexdigest()[:_DIRT_CHARS]}"


def _git(repo: Path, *arguments: str) -> bytes:
    """Run one git command and return its stdout, whole.

    Not S-1.1's `execute`, and the reason is specific: `execute` bounds captured
    output and reports how much it dropped. A truncated diff hashes the same for
    two working trees that differ past the limit, which is a stale hit rather
    than a visible failure — so this needs the bytes rather than a report about
    them.
    """
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        message = f"git {' '.join(arguments)} could not be run in {repo}: {error}"
        raise RepositoryError(message) from error

    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip() or "git reported nothing"
        message = f"git {' '.join(arguments)} failed in {repo}: {detail}"
        raise RepositoryError(message)

    return completed.stdout


_EMPTY_TREE_DIRT = hashlib.sha256(b"").hexdigest()


def _type_name(result_type: type[object]) -> str:
    return f"{result_type.__module__}.{result_type.__qualname__}"


def _adapter[T](result_type: type[T]) -> TypeAdapter[T]:
    """A validator for one result type.

    Rebuilt per call rather than memoized. Building one costs well under a
    millisecond against the 100 ms a hit is allowed, and `CLAUDE.md` asks that
    caching added to this tool's own hot paths be noted — a cache inside the
    cache, added on an assumption rather than a measurement, is the exact change
    this system flags in other people's code.
    """
    return TypeAdapter(result_type)


def _non_finite(value: JsonValue, path: str) -> list[str]:
    """Where a spec holds a number JSON cannot represent, if anywhere."""
    if isinstance(value, float) and not math.isfinite(value):
        return [path]
    if isinstance(value, dict):
        return [name for key, item in value.items() for name in _non_finite(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [
            name
            for index, item in enumerate(value)
            for name in _non_finite(item, f"{path}[{index}]")
        ]
    return []


def _canonical(payload: object) -> str:
    """JSON with one spelling per value, which is what makes a digest stable.

    `allow_nan=False` is the belt behind `_representable`: `NaN` and `Infinity`
    are not JSON, and Python emits them regardless unless told not to.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()

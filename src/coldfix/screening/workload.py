"""What a workload is, split into the half that travels and the half that runs.

Epic 4, S-4.1. `02-architecture.md` §1.3 sketches one object with six members:
`invoke()`, `scale(n)`, `reset()`, a baseline, a fixture recipe and a reset
method. Three of those are callables and three are data, and the story also
requires a Pydantic model with full validation — which those three callables
cannot be part of, because this artifact crosses a node boundary. The Explorer
produces it, the Diagnostician consumes it, S-5.1 keys a replay cache on it and
S-6.1 checkpoints it. A function object survives none of that.

So there are two types and the split is the design:

`Workload` is the artifact. Frozen, serializable, and everything about the
subject that a later process needs in order to reason about it or to look it up.
`BoundWorkload` is that artifact plus the three callables, assembled locally by
an adapter that has the repository in front of it. Nothing serializes a
`BoundWorkload` and nothing runs a `Workload`.

**`work_verified` is computed here and cannot be set.** `08-audit.md` F6: the
Explorer's success criterion was self-judged, and the agent is incentivised to
say yes because saying yes completes its task — an endpoint returning three rows
in three queries might be working or might be a stub route. The audit's fix is an
objective threshold computed by the harness, so this model has no field for it.
It has observations, and a property that reads them.

**It fails closed.** A missing metric, a single scale point, a scale ratio too
small to separate a response from noise — each of those makes `work_verified`
false with a reason, never true by default. `02-architecture.md` §1.5 names the
failure this protects: *workloads run but touch no data → report honestly and
stop, never report "no issues found"*.

**One of F6's three conditions is corrected, and ADR 051 records why.** The audit
asks that the query count *rise* with volume. Written that way it rejects every
correctly batched endpoint — two queries at ten rows and two at a hundred is what
a prefetched list view does, and it is the shape this tool exists to produce — so
a workload would be verified only when it has an N+1. The condition here is that
the query count did not *fall*, which leaves stub detection to the payload and
time conditions that already carry it.

**The fixture recipe records the shape, not only the size.** §1.3's sketch says
"how the data was created" and S-3.3 is what makes the omission matter: `Σk²` is
minimised when every parent has the same number of children, so a uniform fixture
is provably the blindest one for any per-parent cost. A baseline measured under
uniform data is a statement about uniform data, and an artifact that did not
carry its distribution would let that qualification fall off the first time
somebody quoted it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import BLOCKED_SECONDS, SECONDS, Counters
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerifiedReset

# The workload's own payload metric, in bytes. Distinct from `db.bytes`, which is
# what the database handed back: an endpoint can return a hundred bytes after
# reading a megabyte, and F6's test is about what the *response* does.
RESPONSE_BYTES = "response_bytes"

# `08-audit.md` F6's thresholds, verbatim, and they are ratios of the largest
# observation to the smallest. Queries need only rise, because a query count that
# moves at all has responded to volume and is exact; bytes and time carry
# multipliers because both are noisy and both can drift for reasons that are not
# the workload.
_BYTES_MULTIPLE = 2.0
_SECONDS_MULTIPLE = 1.5

# F6's formula was written against n=10 and n=100. Applied across a narrower
# spread it asks a workload to double its payload for twice the data, which is a
# much stronger demand than the audit made and would reject correct workloads.
# Below this ratio the harness says it cannot tell rather than saying no.
MINIMUM_SCALE_RATIO = 4.0

# Two points is what a response to volume needs: one measurement of a stub route
# and one of a real endpoint are the same measurement.
MINIMUM_POINTS = 2

_SLUG = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")

# Metrics that may legitimately be below zero, listed rather than assumed.
#
# `blocked_seconds` is elapsed minus CPU, so it goes negative whenever CPU time
# exceeds the wall clock — which S-3.7 reports as `Boundedness.PARALLEL` and
# deliberately does **not** clamp, because a zero there would say *never waited*
# and that is a finding this must not be able to manufacture. Found by S-4.2:
# every sweep records it, so a blanket non-negative rule rejected the first real
# screening result on a workload fast enough for the timers to disagree.
_SIGNED = frozenset({BLOCKED_SECONDS})

# **`extra="forbid"` is the F6 control, not tidiness.** Pydantic's default is to
# discard an unrecognised key without a word, so `Workload(..., work_verified=
# True)` constructed fine and quietly dropped the claim — an agent that wrote it
# would have had every reason to believe the harness accepted it, which is the
# self-judged criterion the audit removed, reintroduced by a library default.
# Found by a test that attempted the override and did not get an error.
#
# The same setting is what makes a hallucinated field an error rather than a
# silent omission, on artifacts an LLM assembles from a schema.
_STRICT = ConfigDict(frozen=True, extra="forbid")


class WorkloadError(Exception):
    """A workload could not be described, or could not be bound to its callables."""


class Observation(BaseModel):
    """What the harness measured at one data volume.

    Raw metrics, not adjusted: the framework baseline subtraction S-3.2 performs
    belongs to a sweep, and an artifact that stored adjusted numbers could not be
    re-derived from or compared against anything measured elsewhere.
    """

    model_config = _STRICT

    scale: int = Field(gt=0)
    """Units of the primary entity present when this was measured."""

    metrics: Mapping[str, float]

    @field_validator("metrics")
    @classmethod
    def _usable(cls, metrics: Mapping[str, float]) -> Mapping[str, float]:
        if not metrics:
            message = "an observation with no metrics records nothing"
            raise ValueError(message)

        infinite = sorted(name for name, value in metrics.items() if not math.isfinite(value))
        if infinite:
            message = f"these metrics are not numbers: {infinite}"
            raise ValueError(message)

        negative = sorted(
            name for name, value in metrics.items() if value < 0 and name not in _SIGNED
        )
        if negative:
            message = (
                f"these metrics are negative and cannot be: {negative}. The negative case S-3.2 "
                "allows is a metric with the framework baseline already subtracted, which an "
                f"observation does not hold; the ones that may legitimately go below zero are "
                f"{sorted(_SIGNED)}"
            )
            raise ValueError(message)
        return dict(metrics)


class FixtureRecipe(BaseModel):
    """How the data the workload ran against was created.

    Carries the **distribution** as well as the size, because S-3.3 proved the
    uniform fixture is the blindest one for any per-parent cost, so a measurement
    taken under one is a measurement about one.
    """

    model_config = _STRICT

    entity: str = Field(min_length=1)
    """The primary entity `scale(n)` seeds n of."""

    per_parent: int = Field(gt=0)
    """Children held by the **heaviest** parent. The number S-3.3's `Σk²` argument
    is about, and under `UNIFORM` it is simply children per parent.

    Widened at S-7.7 rather than reinterpreted. A skewed fixture has no single
    children-per-parent, and the mean is the one reading that is never the
    interesting one: the whole reason to build a long tail is the request that
    takes minutes while every other request stays fast, and that request is made
    by the heaviest parent. Recording the mean here would name the shape in
    `distribution` and then describe it with the number that shape exists to
    avoid."""

    parents: int | None = None
    """How many parents the children were spread across.

    With `entity`, `per_parent` and `distribution` this makes the fixture
    reproducible: S-3.3's `allocate` is deterministic, so the same shape over the
    same parent count is the same fixture on every machine. `None` means the
    recipe predates S-7.7 or its source never had a parent population to speak
    of — not that there was one parent."""

    distribution: Distribution
    source: str = Field(min_length=1)
    """Where the data came from — a factory, a fixture file, synthesis from schema."""

    seed: int | None = None
    """The generator's seed where it had one. `None` means not reproducible."""

    def digest(self) -> str:
        """A stable hash of the recipe, for S-5.1's replay key.

        Canonical JSON — sorted keys, fixed separators — so the digest is a
        property of the recipe rather than of how it was constructed. Pydantic
        already renders fields in declaration order, so `sort_keys` changes
        nothing today and is cheap insurance for the first field that holds a
        mapping. Said plainly because a sabotage removing it changed no test:
        the guarantee this method actually has is that a fresh interpreter
        computes the same digest, and that is what the test asserts.
        """
        rendered = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode()).hexdigest()[:16]


class EnvironmentAnchor(BaseModel):
    """What the workload's dependencies were resolved against. S-7.12 / ADR 010.

    Here rather than in Epic 7's own envelope because ADR 010's argument is about
    **reproducibility**: *anchoring makes the dependency set a recorded input
    rather than a function of when the tool happened to run*, and a measurement
    is only reproducible if the resolution inputs travel with it. S-0.4's
    byte-identical guard counters are void if a rerun silently resolves a
    different Django.
    """

    model_config = _STRICT

    anchor: date
    """The day the package index was read as of."""

    commit: str | None = None
    """The commit the anchor was derived from. `None` means it was overridden —
    which is why there is no separate flag: two fields that could disagree about
    whether an override happened would eventually disagree."""

    reason: str = Field(min_length=1)
    """Why this date. For a derived anchor that is the commit; for an override it
    is the operator's reason, which ADR 010 requires because a contemporary
    dependency may carry a since-fixed incompatibility or a known vulnerability
    and *why this run differs* is the whole value of recording it."""

    python_version: str | None = None
    """The interpreter the repository claimed, where it claimed one."""

    dependencies: tuple[str, ...] = ()
    """The resolved set, pinned. AC 4's *resolved dependency set*."""

    @property
    def overridden(self) -> bool:
        return self.commit is None


class Workload(BaseModel):
    """One runnable, scalable, resettable unit of work, and what was measured of it.

    The artifact half. Everything here survives serialization, which is what lets
    S-5.1 key a replay cache on it and S-8.4 append it to an experiment log.
    """

    model_config = _STRICT

    id: str
    """Stable identifier, and part of S-5.1's cache key — hence the slug rule.

    Two ids differing only in case or whitespace are two cache entries for one
    workload, and the failure is silent: everything still runs, and everything
    runs twice.
    """

    description: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    """What is actually invoked — a route, a management command, a function path."""

    fixture: FixtureRecipe
    reset_method: ResetStrategy
    environment: EnvironmentAnchor | None = None
    """What the dependencies were resolved against. Added at S-7.12.

    Optional because a workload can be described before its environment is stood
    up, and because artifacts predating S-7.12 exist. `None` means *not
    recorded*, never *resolved against today*: ADR 010's whole argument is that
    resolving against today is what breaks a 2019 repository, so the absence of
    an anchor is a gap in the record rather than a default.
    """

    observations: tuple[Observation, ...] = ()
    """Every scale point measured so far, ascending. May be empty.

    Empty is a real state: the Explorer produces a workload before anything has
    swept it. What an empty one cannot do is claim `work_verified`.
    """

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG.match(value):
            message = (
                f"{value!r} is not a usable workload id; expected a lowercase slug like "
                "'api.tickets.list'. The id is part of the replay cache key, and two spellings "
                "of one workload are two cache entries that both miss"
            )
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _ordered_and_comparable(self) -> Workload:
        scales = [observation.scale for observation in self.observations]
        if scales != sorted(scales):
            message = f"observations must be ordered by ascending scale, got {scales}"
            raise ValueError(message)
        if len(set(scales)) != len(scales):
            message = (
                f"two observations share a scale in {scales}. One volume measured twice is a "
                "repeat, and which of the two a later reader gets is undefined"
            )
            raise ValueError(message)

        recorded = [set(observation.metrics) for observation in self.observations]
        if len(set(map(frozenset, recorded))) > 1:
            everywhere = set.intersection(*recorded)
            missing = sorted(set.union(*recorded) - everywhere)
            message = (
                f"the observations do not record the same metrics; {missing} appear in some and "
                "not others. A metric present at one scale and absent at another cannot be "
                "contrasted, and dropping it publishes a comparison that covered less than it "
                "claims (S-3.2's rule, applied to the artifact)"
            )
            raise ValueError(message)
        return self

    @property
    def smallest(self) -> Observation | None:
        return self.observations[0] if self.observations else None

    @property
    def largest(self) -> Observation | None:
        return self.observations[-1] if self.observations else None

    @property
    def scale_ratio(self) -> float | None:
        """How far apart the extreme observations are. `None` below two points."""
        if len(self.observations) < MINIMUM_POINTS:
            return None
        return self.observations[-1].scale / self.observations[0].scale

    @property
    def work_verified(self) -> bool:
        """Whether the harness has established this workload does real work.

        `08-audit.md` F6's objective threshold. **There is no field behind this
        and no way to set it** — the flaw the audit found was that an agent
        decided, and an agent is incentivised to say yes because saying yes
        completes its task.
        """
        return self.work_evidence.startswith("Verified")

    @property
    def work_evidence(self) -> str:
        """The reasoning behind `work_verified`, in the form a reader can check.

        Returned as prose rather than a boolean and a code, because every way of
        failing this test calls for a different action: sweep a second point,
        widen the spread, measure the missing metric, or reject the workload.
        """
        if len(self.observations) < MINIMUM_POINTS:
            return (
                "Not verified: fewer than two scale points, so nothing here shows a response to "
                "data volume. One measurement of a stub route and one of a real endpoint look "
                "the same."
            )

        small, large = self.observations[0], self.observations[-1]
        ratio = self.scale_ratio or 0.0
        if ratio < MINIMUM_SCALE_RATIO:
            return (
                f"Not verified: the scale points are only {ratio:.1f}x apart, and "
                f"`08-audit.md` F6's thresholds were written against 10x. Applied this close "
                "together they demand a doubling of payload for a small increase in data, which "
                "rejects correct workloads. Widen the spread rather than reading this as a no."
            )

        required = (DB_QUERY, RESPONSE_BYTES, SECONDS)
        absent = [name for name in required if name not in small.metrics]
        if absent:
            return (
                f"Not verified: {absent} were not measured, and F6's test is defined over all "
                "three. A workload is not shown to do real work by the metrics that happened to "
                "be available."
            )

        checks = {
            # **F6 asks for `queries rose` and that is corrected here, to
            # `queries did not fall`.** Written as the audit has it, this
            # condition rejects every correctly batched endpoint: two queries at
            # ten rows and two at a hundred is exactly what a prefetched list
            # view does, and it is the shape this whole tool exists to produce.
            # A workload verified only when its query count climbs is a workload
            # verified only when it has an N+1, which would have the Explorer
            # discard the well-written half of every repository. ADR 051.
            #
            # Nothing is lost on the case F6 was defending against: a stub route
            # fails the payload and time conditions regardless, because it
            # returns the same bytes in the same time however much data exists.
            # Queries falling stays disqualifying — more data costing fewer
            # queries means something served the second measurement from a cache.
            f"{DB_QUERY} did not fall": large.metrics[DB_QUERY] >= small.metrics[DB_QUERY],
            f"{RESPONSE_BYTES} more than {_BYTES_MULTIPLE:g}x": (
                large.metrics[RESPONSE_BYTES] > _BYTES_MULTIPLE * small.metrics[RESPONSE_BYTES]
            ),
            f"{SECONDS} more than {_SECONDS_MULTIPLE:g}x": (
                large.metrics[SECONDS] > _SECONDS_MULTIPLE * small.metrics[SECONDS]
            ),
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        spread = f"across {small.scale} to {large.scale} ({ratio:.1f}x)"
        if failed:
            return (
                f"Not verified {spread}: {failed} did not hold. The workload runs, and nothing "
                "here shows it doing more work when there is more data — which is what a stub "
                "route looks like, and what an aggregate endpoint that legitimately returns a "
                "fixed-size answer also looks like. This test cannot tell those apart, so the "
                "answer is a refusal to verify rather than a claim the workload is broken."
            )
        return f"Verified {spread}: every one of {sorted(checks)} held."

    def digest(self) -> str:
        """The identity S-5.1 caches against: this workload on this fixture.

        Deliberately **not** a hash of the observations. A replay cache keyed on
        what was measured could never hit, since the measurement is what it is
        trying to avoid repeating.
        """
        return f"{self.id}@{self.fixture.digest()}"


class BoundWorkload:
    """A workload with the callables an adapter supplied. Never serialized.

    The constructor checks the callables against what the artifact claims, which
    is this project's recurring construction: an ordering or permission
    requirement becomes a type whose constructor performs the check, so the
    unsafe state has no object to exist in.

    The check that matters is the reset. A descriptor saying `snapshot_restore`
    bound to a rollback mechanism produces measurements qualified by a strategy
    that was never used — and S-2.6's whole argument is that a reset which did
    not happen is undetectable from the results, because a stale state and a
    correct one both report the same thing every time.

    **`clear_caches` and `process_identity` are here because S-4.2 could not run
    without them.** A reset returns the *database* to its baseline and says
    nothing about what the process cached in memory, and `measure_once` refuses a
    sweep that has neither guarantee — so a workload carrying only its reset
    method described something that could not be measured. Both are optional
    here and neither is defaulted: which one an adapter can supply is a fact
    about the environment, and inventing one on the workload's behalf would be
    inventing the guarantee itself.

    **`extra_counters` is here because Epic 4's composition check found it in the
    wrong place.** It began as a parameter to `screen`, which applied one
    callable to every workload in the project — so a screen of six workloads read
    one workload's guard counters six times and attributed them to the other
    five. A guard counter is a fact about a particular subject, exactly as its
    invocation and its reset are, so it belongs on the binding with them. Never
    exercised until a screen ran more than one workload with guard counters at
    once, which is a thing only a composition does.
    """

    def __init__(  # noqa: PLR0913 - see the note on scale_volume
        self,
        descriptor: Workload,
        *,
        invoke: Callable[[], object],
        scale: Callable[[int], object],
        reset: VerifiedReset,
        clear_caches: Callable[[], object] | None = None,
        process_identity: Callable[[], object] | None = None,
        extra_counters: Counters | None = None,
    ) -> None:
        # Widened to `object` for S-1.6's reason: the annotations say both are
        # callable, so a guard written against them is typed out of existence,
        # and the callers this protects are the ones nobody type-checked — an
        # adapter assembling a workload from an artifact it read.
        supplied: tuple[tuple[str, object], ...] = (("invoke", invoke), ("scale", scale))
        for name, given in supplied:
            if not callable(given):
                message = (
                    f"{name} is a {type(given).__name__}, not a callable. A bound workload runs "
                    f"the subject; a recorded result belongs on the {descriptor.id!r} artifact"
                )
                raise WorkloadError(message)

        performed = reset.mechanism.strategy
        if performed is not descriptor.reset_method:
            message = (
                f"{descriptor.id!r} is described as reset by {descriptor.reset_method.value} and "
                f"was bound to a {performed.value} mechanism. Every measurement taken through "
                "this would carry a strategy that was never used, and ADR 026's finding is that "
                "the results cannot reveal it — a stale state and a correct one look identical"
            )
            raise WorkloadError(message)

        self.descriptor = descriptor
        self.invoke = invoke
        self.scale = scale
        self.reset = reset
        self.clear_caches = clear_caches
        self.process_identity = process_identity
        self.extra_counters = extra_counters

    @property
    def id(self) -> str:
        return self.descriptor.id

    def __repr__(self) -> str:
        return f"BoundWorkload({self.descriptor.id!r}, reset={self.descriptor.reset_method.value})"

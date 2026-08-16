"""What was ruled out, under what conditions, and when that stops being true.

Epic 8, S-8.5. `08-audit.md` F3: *"Not the database — queries flat at 7, 7, 7"
holds at the scales tested, with the fixtures used, on this platform. If the
fixtures were uniform and the real defect is skew-dependent, the exclusion is
false — and it sits in the prompt as established fact, permanently blocking the
correct hypothesis.*

An exclusion is a **finding** (`00-BRIEF.md` §9 ships null results as answers), so
it is subject to the same non-negotiable as every other one: no finding without a
measurement. That comes free here — an exclusion can only be made from a rejected
`Experiment`, and S-8.4 already refuses an experiment with no measurement. There
is no constructor that takes a sentence.

**`invalidated_if` is derived, not stored, and that is the one place this departs
from F3's sketch.** The sketch carries `conditions` and `invalidated_if` as two
fields, and they are two statements of one fact: an exclusion is invalid exactly
when a condition it was established under no longer holds. Two fields that can
disagree eventually will, and the one that would drift is the one nobody reads
until it matters — the same argument S-7.12 made for refusing an override flag
beside an override value. So conditions are recorded and staleness is computed.

**Two kinds of condition, because the four the story names are two kinds.**

*Categorical* — fixture shape, platform. Coverage is membership: an exclusion
established under `uniform` covers `uniform` and nothing else. Multi-valued is
ordinary rather than hypothetical, since S-3.3's `compare_shapes` sweeps all
three distributions in one experiment and that exclusion genuinely covers three.

*Numeric* — concurrency, scale. Coverage is the **envelope**, min to max. An
exclusion that saw 10, 100 and 1000 covers 500 and does not cover 10 000, and
that asymmetry is the point rather than a convenience: a defect invisible at 10,
100 and 1000 but present at 500 would have to be non-monotonic, while one that
appears past 1000 is an ordinary threshold — a cache that stops fitting, a page
that splits, an index the planner abandons.

**All four are required, which is AC 1 read literally.** *Every exclusion records
its preconditions* leaves no room for an exclusion that records three of them,
and requiring all four is also what removes a tri-state: with an optional
dimension, a later experiment reporting a condition the exclusion never recorded
is neither a change nor a non-change, and S-3.1's lesson is that collapsing that
third answer into either of the others produces a specific wrong behaviour.

**Reopening is refused for an exclusion that is not stale, and that is the guard
that matters.** F3 names one danger — an exclusion treated as permanent fact —
and fixing it introduces the opposite one, an agent dismissing an inconvenient
result by calling it stale. Staleness is computed from recorded conditions, and
`reopen` refuses anything the conditions do not actually reopen.

**What this cannot do, stated rather than implied.** Nothing here decides whether
a *new* hypothesis is the same as an excluded one; that is a semantic judgement
with no deterministic check, so a live exclusion blocks by being rendered into
the prompt as settled and not by any refusal in this module. The four dimensions
are also the only ones modelled: a primitive that varies a fifth axis will
produce an exclusion that looks fully conditioned and is not, and `RESIDUE` says
so in words for the same reason S-7.12's `Anchor.residue` does.
"""

from __future__ import annotations

import platform as platform_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.diagnosis.log import Experiment, Verdict

RESIDUE = (
    "These four dimensions are the ones modelled. An experiment that varied "
    "something else — a database version, a cache setting, a feature flag — ran "
    "under a condition no exclusion records, and no later change to it will "
    "reopen anything."
)

type Value = str | float


class ExclusionError(Exception):
    """An exclusion could not be recorded, compared, or reopened."""


class Dimension(StrEnum):
    """A condition an exclusion holds under. AC 1's four, and adding a fifth is a
    line here plus a line in whatever populates it — neither is agent code."""

    FIXTURE_SHAPE = "fixture shape"
    PLATFORM = "platform"
    CONCURRENCY = "concurrency"
    SCALE = "scale"

    @property
    def numeric(self) -> bool:
        """Whether coverage is an envelope rather than membership.

        A property of the dimension rather than of a value, so that one exclusion
        cannot decide that scale is categorical while another treats it as a
        range.
        """
        return self in (Dimension.CONCURRENCY, Dimension.SCALE)


@dataclass(frozen=True)
class Observed:
    """The values one dimension took while an experiment ran.

    Never empty: a dimension recorded with no values is a dimension that was not
    recorded, and AC 1 requires all four.
    """

    dimension: Dimension
    values: tuple[Value, ...]

    def __post_init__(self) -> None:
        if not self.values:
            message = (
                f"{self.dimension.value} was recorded with no values at all, which is not a "
                "record of the condition — it is the absence of one"
            )
            raise ExclusionError(message)

        for value in self.values:
            # `bool` is a subclass of `int`, so `concurrency=True` would otherwise
            # be a concurrency of 1 that nobody wrote.
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            if self.dimension.numeric and not numeric:
                message = (
                    f"{self.dimension.value} is a numeric condition and was given {value!r}. Its "
                    "coverage is an envelope, and there is no envelope over text"
                )
                raise ExclusionError(message)
            if not self.dimension.numeric and not isinstance(value, str):
                message = (
                    f"{self.dimension.value} is a categorical condition and was given {value!r}. "
                    "Its coverage is membership, and a number compared by membership would "
                    "silently stop covering the range beside it"
                )
                raise ExclusionError(message)

        object.__setattr__(self, "values", _canonical(self.values, numeric=self.dimension.numeric))

    def covers(self, later: Observed) -> bool:
        """Whether this exclusion's evidence extends to what a later run did.

        Raises:
            ExclusionError: the two describe different dimensions, which is a
                caller mistake rather than a drift.
        """
        if later.dimension is not self.dimension:
            message = f"cannot compare {self.dimension.value} against {later.dimension.value}"
            raise ExclusionError(message)

        if not self.dimension.numeric:
            return set(later.values) <= set(self.values)

        floor = min(float(value) for value in self.values)
        ceiling = max(float(value) for value in self.values)
        return all(floor <= float(value) <= ceiling for value in later.values)

    def describe(self) -> str:
        rendered = ", ".join(_render(value) for value in self.values)
        if self.dimension.numeric and len(self.values) > 1:
            return f"{self.dimension.value} {_render(self.values[0])} to {_render(self.values[-1])}"
        return f"{self.dimension.value} {rendered}"


def _canonical(values: Sequence[Value], *, numeric: bool) -> tuple[Value, ...]:
    """Deduplicated, ordered, and stored as the type the dimension declares.

    Ordered because these render into a cached prompt — S-8.3's finding one
    module across: two equal conditions assembled in two orders would render as
    two different prompts, and a prompt that moves is a prefix that stops
    matching.

    **Numeric values are converted to `float` rather than kept as written**, which
    S-8.6 found by serializing one: `scales=[10, 100]` stores Python `int`s under
    a field annotated `str | float`, and the chain that carries an exclusion into
    a pull request then serializes them through a union that does not describe
    them. A stored type that disagrees with its annotation is a golden file
    waiting to move.
    """
    unique = set(values)
    if numeric:
        return tuple(sorted((float(value) for value in unique), key=float))
    return tuple(sorted(unique, key=str))


def _render(value: Value) -> str:
    """Whole numbers without their decimal point, so a scale reads as a scale."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class Drift:
    """One dimension on which a later run left what an exclusion covered."""

    dimension: Dimension
    covered: Observed
    encountered: Observed

    def describe(self) -> str:
        return (
            f"{self.dimension.value}: this was established at "
            f"{self.covered.describe()} and the run since went to "
            f"{self.encountered.describe()}"
        )


@dataclass(frozen=True)
class Conditions:
    """What was true of the world while an experiment ran.

    All four dimensions, always. See the module docstring for why an optional one
    would reintroduce a third answer that neither `covered` nor `changed`
    expresses.
    """

    observed: Mapping[Dimension, Observed]

    def __post_init__(self) -> None:
        missing = sorted(item.value for item in Dimension if item not in self.observed)
        if missing:
            message = (
                f"these conditions record nothing for {missing}. AC 1 requires every exclusion to "
                "record its preconditions, and one that records three of four cannot be reopened "
                "by a change to the fourth — which is the failure F3 exists to prevent"
            )
            raise ExclusionError(message)

        wrong = [
            dimension.value
            for dimension, entry in self.observed.items()
            if entry.dimension is not dimension
        ]
        if wrong:
            message = f"these conditions file the wrong dimension under {wrong}"
            raise ExclusionError(message)

        object.__setattr__(self, "observed", dict(self.observed))

    @classmethod
    def of(
        cls,
        *,
        fixture_shape: str | Sequence[str],
        platform: str,
        concurrency: float | Sequence[float],
        scales: Sequence[float],
    ) -> Conditions:
        """The four, named, so that nobody assembles them positionally.

        Keyword-only and individually named rather than a mapping, because the
        one mistake worth making impossible is filing a shape under the platform.
        """
        return cls(
            {
                Dimension.FIXTURE_SHAPE: Observed(
                    Dimension.FIXTURE_SHAPE, tuple(_sequence(fixture_shape))
                ),
                Dimension.PLATFORM: Observed(Dimension.PLATFORM, (platform,)),
                Dimension.CONCURRENCY: Observed(
                    Dimension.CONCURRENCY, tuple(_sequence(concurrency))
                ),
                Dimension.SCALE: Observed(Dimension.SCALE, tuple(scales)),
            }
        )

    def drift_from(self, later: Conditions) -> tuple[Drift, ...]:
        """Every dimension on which `later` went outside what these cover.

        Empty means the exclusion still applies. Ordered by `Dimension` so a
        report reads the same twice.
        """
        return tuple(
            Drift(
                dimension=dimension,
                covered=self.observed[dimension],
                encountered=later.observed[dimension],
            )
            for dimension in Dimension
            if not self.observed[dimension].covers(later.observed[dimension])
        )

    def describe(self) -> str:
        return "; ".join(self.observed[dimension].describe() for dimension in Dimension)


def _sequence(value: object) -> Sequence[Value]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return [value]  # type: ignore[list-item]
    return list(value)


def current_platform() -> str:
    """This machine, so that no caller types a platform string by hand.

    A wrong platform on an exclusion is invisible: it looks recorded, compares
    equal to the next wrong one, and never reopens anything.
    """
    return f"{platform_module.machine()}-{platform_module.system()}".lower()


@dataclass(frozen=True)
class Exclusion:
    """A hypothesis ruled out, the measurement that ruled it out, and where that holds.

    Built only from a **rejected** experiment. A confirmed one is a finding and a
    narrowed one is a hypothesis that survived, so neither excludes anything — and
    an exclusion assembled from either would tell the agent a live branch was
    closed.
    """

    experiment: Experiment
    conditions: Conditions

    def __post_init__(self) -> None:
        if self.experiment.verdict is not Verdict.REJECTED:
            message = (
                f"experiment {self.experiment.index} came back {self.experiment.verdict.value} "
                "and only a rejection excludes anything. A confirmed experiment is a finding and "
                "a narrowed one is a hypothesis that survived"
            )
            raise ExclusionError(message)

    @property
    def hypothesis(self) -> str:
        return self.experiment.hypothesis

    def stale_against(self, now: Conditions) -> tuple[Drift, ...]:
        """What about the world has moved since this was established."""
        return self.conditions.drift_from(now)

    def describe(self, now: Conditions | None = None) -> str:
        """The sentence S-8.1 renders into its prompt.

        A live exclusion reads as settled **and carries its scope**, because
        `00-BRIEF.md` §9's example is *not the database, queries flat across 100x
        scale* — the scale is part of the claim, not a footnote to it. A stale one
        reads as reopened and says which condition moved, since an agent told
        only that something is stale cannot tell whether it is worth re-testing.
        """
        settled = (
            f"{self.hypothesis} — ruled out by experiment {self.experiment.index} "
            f"({self.experiment.outcome}), under {self.conditions.describe()}"
        )
        if now is None:
            return settled

        drifts = self.stale_against(now)
        if not drifts:
            return settled

        moved = "; ".join(drift.describe() for drift in drifts)
        return (
            f"{settled}\n"
            f"    **REOPENED** — {moved}. This exclusion's evidence does not reach the "
            "conditions the investigation is now under, and the hypothesis may be tested again."
        )


class ExclusionRegister:
    """Everything ruled out so far, and which of it still holds.

    Staleness is **computed on every read** rather than stored. A stored flag
    would be a second statement of what the conditions already say, and it would
    be wrong for exactly as long as nobody recomputed it.
    """

    def __init__(self) -> None:
        self._exclusions: list[Exclusion] = []

    def record(self, experiment: Experiment, conditions: Conditions) -> Exclusion:
        """Rule something out. The only way anything enters this register.

        Raises:
            ExclusionError: the experiment did not come back rejected.
        """
        exclusion = Exclusion(experiment=experiment, conditions=conditions)
        self._exclusions.append(exclusion)
        return exclusion

    @property
    def exclusions(self) -> Sequence[Exclusion]:
        """Everything recorded, oldest first. A copy."""
        return tuple(self._exclusions)

    def stale(self, now: Conditions) -> tuple[Exclusion, ...]:
        """AC 2 — everything a change of conditions has reopened."""
        return tuple(item for item in self._exclusions if item.stale_against(now))

    def live(self, now: Conditions) -> tuple[Exclusion, ...]:
        """Everything whose evidence still reaches the current conditions."""
        return tuple(item for item in self._exclusions if not item.stale_against(now))

    def reopen(self, exclusion: Exclusion, now: Conditions) -> str:
        """AC 3 — take a stale exclusion back, and get the hypothesis to re-test.

        **Refuses one that is not stale**, which is the half of this story that
        F3 does not ask for. F3 names the danger of an exclusion treated as
        permanent fact; the fix introduces its opposite, an agent setting aside an
        inconvenient result by calling it stale. A reopening has to be something
        the recorded conditions actually justify.

        Raises:
            ExclusionError: nothing about the conditions has reopened it, or it
                was never recorded here.
        """
        if exclusion not in self._exclusions:
            message = "that exclusion is not in this register, so nothing here established it"
            raise ExclusionError(message)

        drifts = exclusion.stale_against(now)
        if not drifts:
            message = (
                f"{exclusion.hypothesis!r} cannot be reopened: it was established under "
                f"{exclusion.conditions.describe()}, and the investigation is still within every "
                "one of those. An exclusion that may be set aside without a condition having "
                "moved is not an exclusion"
            )
            raise ExclusionError(message)
        return exclusion.hypothesis

    def render(self, now: Conditions | None = None) -> tuple[str, ...]:
        """The sentences S-8.1 puts in its prompt.

        Strings rather than objects because that is the shape `generate` takes —
        S-8.1 recorded that it needed only the sentence and that fixing a
        structure there would be guessing at this story's design. This is the
        structure, and it still hands over sentences.
        """
        return tuple(item.describe(now) for item in self._exclusions)

    def report(self, now: Conditions) -> str:
        """What is closed, what has reopened, and what none of it covers."""
        if not self._exclusions:
            return "Nothing has been ruled out yet."
        stale = self.stale(now)
        lines = [
            f"{len(self._exclusions)} exclusion(s), {len(stale)} reopened "
            "by a change of conditions:"
        ]
        lines.extend(f"  - {item.describe(now)}" for item in self._exclusions)
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)

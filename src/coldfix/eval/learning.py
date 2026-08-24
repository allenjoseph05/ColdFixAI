"""Whether the playbook makes grounding cheaper, and the arithmetic that could say it does not.

Epic 13, S-13.5. `00-BRIEF.md` §6 lists the learning curve as an evaluation
metric — *Explorer steps vs projects seen, should decline* — and §5 step 13's
acceptance is that *the tenth Django project takes materially fewer Explorer
steps than the first*. `04-cost.md` §10 puts a number on the hope: ~120 calls on
the first project, ~40 on the tenth, ~10 on the fiftieth.

**This study was blocked for a day and the reason is worth keeping.** Until
S-7.14 there was no such thing as an Explorer step — `ground_workload` ran nine
stages once each, so *steps to ground* was the same constant for every repository
in the world — and until S-13.7 nothing read a playbook entry, so the retrieved
and withheld arms were **identical runs**. A harness built then could only ever
have measured zero, and *the playbook adds nothing* drawn from that would have
been true and actively misleading: a finding about the wiring wearing the costume
of a finding about the playbook.

**The curve is confounded and the ablation is not.** Steps decline if the
playbook works — and also if the projects that happened to come later were
easier. Nothing in a longitudinal series separates those, so `Curve` **reports
and never concludes**: it has no `IMPROVED`, it says what the series did, and it
points at the ablation for the causal question. The ablation grounds *the same
repository* both ways in interleaved order, which is S-0.4's method and the
project's own core primitive turned on the project.

**No rank test here, and `rank_test`'s own docstring is why.** *On heavily tied
data — a metric taking three distinct values — it runs about 30% the other way,
which is the unsafe direction. Counts are the tied case, and counts do not need
this test: they are deterministic, and a difference in them is read directly.*
Steps-to-ground is a count. Reaching for the nearest comparison would have
produced p-values biased towards inventing a difference, in the one study whose
whole job is to be able to report that there is none. What replaces it is the
shape the interleaving already provides: rounds are **paired**, so the statistic
is a sign test over rounds and the interval is Wilson's over that proportion.

**The guard counter decides before the step count does.** `CLAUDE.md`'s
non-negotiable — *guard counters on every metric; queries down while rows explode
is not an improvement* — has an exact reading here: a run that took fewer steps
because it gave up three stages earlier has not learned anything. So stage
completion is checked first, and a study whose guard moved reports that and makes
no claim about steps at all.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.interleaving import schedule
from coldfix.bench.stats import MINIMUM_GROUP_SIZE, MINIMUM_SAMPLES
from coldfix.bench.timing import ProcessState
from coldfix.eval.ablation import wilson
from coldfix.explorer.loop import Exploration
from coldfix.explorer.stages import Stage

STAGES = len(Stage)
"""ADR 009's nine, derived rather than written down again. A tenth stage should
move every completion figure in this study, not silently sit outside the
denominator."""

NO_BETTER_THAN_CHANCE = 0.5
"""What the lower bound of the interval has to clear.

A paired round is won or lost, so a playbook that changes nothing at all wins
half the decisive rounds. **The bound clears this, not the point estimate**:
winning six of ten is a lead whose interval runs from 26% to 88%, and a corpus
that could as easily have produced the opposite ordering has not shown one. That
is `eval/ablation.py`'s third test in the form a paired study takes."""

MINIMUM_PROJECTS = 2 * MINIMUM_SAMPLES
"""Projects of one fingerprint before a curve says anything about its direction.

Derived from `stats.MINIMUM_SAMPLES` rather than picked: a summary needs two
observations to have any dispersion at all, and this compares two halves. Below
four, a half is one project and one project is an anecdote — the curve still
plots, it simply reports `NOT_ESTABLISHED` for its direction."""


class LearningError(Exception):
    """The study could not be assembled."""


class Condition(StrEnum):
    """Which arm of the ablation an observation belongs to."""

    RETRIEVED = "the playbook was consulted and what it held was available to act on"
    WITHHELD = "the same repository, ground cold: nothing was offered"


@dataclass(frozen=True)
class Grounding:
    """One repository ground once, and what it cost.

    **Recorded by the harness, never computed here.** `CLAUDE.md` puts measuring
    in the harness and reasoning in the agent, and a study that drove the pipeline
    itself could not be re-checked without spending the corpus again — which is
    the construction `eval/ablation.py` established for the Adversary study one
    epic earlier.
    """

    project: str
    fingerprint: str
    """The playbook key: framework and major version. The curve is *per
    fingerprint* because that is what an entry is filed under, and pooling two
    frameworks would average a learned playbook with a cold one."""

    steps: int
    stages_completed: int
    ground: bool
    condition: Condition = Condition.RETRIEVED

    process_state: ProcessState = ProcessState.FRESH
    """Whether this sample shared a process with an earlier one. **Recorded and
    never acted on**, which is S-1.2's rule and the backlog note's instruction:
    S-0.4 hard-coded a warm-up discard, and Barrett et al. found at most 43.5% of
    VM/benchmark pairs reach steady state at all — so *discard the first N* is an
    assumption that is wrong more often than not. It is a column, and the
    analysis may decide."""

    entries_offered: int = 0
    """How many playbook entries the run was shown. Zero in a withheld arm by
    definition, and the constructor refuses anything else."""

    def __post_init__(self) -> None:
        if self.steps < 0:
            message = f"{self.project} recorded {self.steps} steps, which is not a count"
            raise LearningError(message)
        if not 0 <= self.stages_completed <= STAGES:
            message = (
                f"{self.project} completed {self.stages_completed} of {STAGES} stages, which is "
                "outside the pipeline ADR 009 defines"
            )
            raise LearningError(message)
        if self.ground and self.stages_completed != STAGES:
            message = (
                f"{self.project} is recorded as ground with {self.stages_completed} of {STAGES} "
                "stages complete. `GroundingRun.finish` refuses a run with any stage incomplete, "
                "so this observation did not come from a run this system finished"
            )
            raise LearningError(message)
        if self.condition is Condition.WITHHELD and self.entries_offered:
            message = (
                f"{self.project} was ground with the playbook withheld and was offered "
                f"{self.entries_offered} entry(s). That is the confound the ablation exists to "
                "remove, and a study carrying it would compare the playbook against itself"
            )
            raise LearningError(message)


def observed(  # noqa: PLR0913 - what the run cost is read off the exploration; who
    # it was, what it keys under, which arm it belongs to and how it was run are
    # four facts only the harness knows, and guessing any of them mislabels a
    # sample rather than failing.
    exploration: Exploration,
    *,
    project: str,
    fingerprint: str,
    condition: Condition = Condition.RETRIEVED,
    process_state: ProcessState = ProcessState.FRESH,
    entries_offered: int = 0,
) -> Grounding:
    """One `Exploration`, as an observation. **The join to S-7.14's loop.**

    Stage completion comes from whichever half of the result carries it — a
    grounded repository reports all nine and a failure reports where it stopped,
    and both hold a `Progress`. Reading it here rather than at each call site is
    what stops two harnesses disagreeing about what *completion* meant.
    """
    progress = (
        exploration.grounded.progress
        if exploration.grounded is not None
        else exploration.failure.progress
        if exploration.failure is not None
        else None
    )
    if progress is None:  # pragma: no cover - `Exploration` refuses to be neither
        message = "an exploration that is neither ground nor failed has nothing to record"
        raise LearningError(message)

    return Grounding(
        project=project,
        fingerprint=fingerprint,
        steps=exploration.steps,
        stages_completed=len(progress.completed),
        ground=exploration.grounded is not None,
        condition=condition,
        process_state=process_state,
        entries_offered=entries_offered,
    )


class Effect(StrEnum):
    """What a comparison established. Four, and two of them are refusals."""

    IMPROVED = "fewer steps to ground, with stage completion holding"
    NO_EFFECT = "no difference the samples can establish"
    GUARD_FELL = (
        "stage completion fell, so no claim about steps stands — less of the repository was "
        "ground, whatever it cost"
    )
    NOT_ESTABLISHED = "too few samples to separate the two"


# ================================================================== AC 1 to AC 3


@dataclass(frozen=True)
class Curve:
    """Steps to a runnable workload, per project, in the order they were seen.

    **AC 1 and AC 2**, and it deliberately stops short of AC 3's causal reading.
    `direction` says what the series did; the note on this story says why that is
    not the same as saying the playbook did it — *it declines if the playbook
    works, and also if later projects happen to be easier.* The ablation is what
    answers that, and `describe` says so rather than leaving a reader to infer it.
    """

    fingerprint: str
    samples: tuple[Grounding, ...]

    def __post_init__(self) -> None:
        wrong = {item.fingerprint for item in self.samples} - {self.fingerprint}
        if wrong:
            message = (
                f"this curve is for {self.fingerprint!r} and holds samples from {sorted(wrong)}. "
                "An entry is filed per fingerprint, so pooling two of them averages a learned "
                "playbook with a cold one"
            )
            raise LearningError(message)
        seen = [item.project for item in self.samples]
        if len(set(seen)) != len(seen):
            repeated = sorted({name for name in seen if seen.count(name) > 1})
            message = (
                f"the same project appears twice in one curve: {repeated}. AC 2 plots against "
                "the *number of projects* with this fingerprint, and a repeat counts one project "
                "twice"
            )
            raise LearningError(message)

    @property
    def steps(self) -> tuple[int, ...]:
        """**AC 1**, in the order the projects were seen. What gets plotted."""
        return tuple(item.steps for item in self.samples)

    @property
    def completion(self) -> tuple[int, ...]:
        """The guard, alongside. A decline in the first with a decline in this one
        is not a learning curve."""
        return tuple(item.stages_completed for item in self.samples)

    @property
    def halves(self) -> tuple[tuple[Grounding, ...], tuple[Grounding, ...]]:
        """The earlier projects and the later ones. An odd count gives the extra
        one to the earlier half, so the *later* half is never the more generously
        sampled of the two — the direction being looked for is a decline, and
        padding the side expected to be lower is how a study flatters itself."""
        middle = (len(self.samples) + 1) // 2
        return self.samples[:middle], self.samples[middle:]

    @property
    def direction(self) -> Effect:
        """**AC 3**, stated as arithmetic rather than as a test.

        The median of the later half against the earlier half, with the guard
        checked first. There is no p-value: these are counts, and `rank_test`
        refuses counts for a reason its own docstring gives — on tied data it errs
        towards *inventing* a difference, which is the one direction a study of
        one's own memory must not err in.
        """
        earlier, later = self.halves
        if len(earlier) < MINIMUM_SAMPLES or len(later) < MINIMUM_SAMPLES:
            return Effect.NOT_ESTABLISHED

        if _median(item.stages_completed for item in later) < _median(
            item.stages_completed for item in earlier
        ):
            return Effect.GUARD_FELL

        if _median(item.steps for item in later) < _median(item.steps for item in earlier):
            return Effect.IMPROVED
        return Effect.NO_EFFECT

    def describe(self) -> str:
        earlier, later = self.halves
        lines = [
            f"LEARNING CURVE — {self.fingerprint}, {len(self.samples)} project(s)",
            f"  steps, in the order seen: {list(self.steps)}",
            f"  stages completed:         {list(self.completion)}",
        ]
        if self.direction is not Effect.NOT_ESTABLISHED:
            lines.append(
                f"  median steps: {_median(item.steps for item in earlier):.1f} over the first "
                f"{len(earlier)} → {_median(item.steps for item in later):.1f} over the last "
                f"{len(later)}"
            )
        lines.append(f"  {self.direction.value}")
        if self.direction is Effect.NOT_ESTABLISHED:
            lines.append(
                f"    Fewer than {MINIMUM_PROJECTS} projects with this fingerprint. The series "
                "is still worth plotting; its direction is not worth quoting."
            )
        lines.append(
            "  **This is not evidence the playbook caused it.** The series declines if memory "
            "works and also if the later projects were easier, and nothing longitudinal "
            "separates those. The ablation is the causal measurement."
        )
        return "\n".join(lines)


def curve(fingerprint: str, samples: Sequence[Grounding]) -> Curve:
    """Assemble one fingerprint's curve from observations the harness recorded.

    Order is the caller's: the samples arrive in the order the projects were
    ground, because *the number of projects with that fingerprint* is a count over
    history and sorting them here by anything else would invent a different x-axis.

    Raises:
        LearningError: no samples, or samples from another fingerprint.
    """
    if not samples:
        message = (
            f"a curve for {fingerprint!r} with no projects in it. An empty series has no "
            "direction, and reporting one would be a claim about a playbook nobody used"
        )
        raise LearningError(message)
    return Curve(fingerprint=fingerprint, samples=tuple(samples))


# ================================================================== AC 4


@dataclass(frozen=True)
class Round:
    """One repository ground both ways. **The unit the ablation reasons over.**

    Paired by construction, which is what makes a sign test the right instrument:
    the two runs share a repository, a machine and a moment, so the difference
    between them is the condition and very little else.
    """

    retrieved: Grounding
    withheld: Grounding

    @property
    def saved(self) -> int:
        """Steps the playbook saved on this round. Negative where it cost some."""
        return self.withheld.steps - self.retrieved.steps

    @property
    def ground_less(self) -> bool:
        return self.retrieved.stages_completed < self.withheld.stages_completed


@dataclass(frozen=True)
class PlaybookAblation:
    """**AC 4.** The same repository, ground with the playbook and without it.

    The measurement the curve cannot make. `00-BRIEF.md` §8 calls an ablation the
    project's core primitive, and this is it applied to the project — with the
    same refusal the lab bench's `compare` enforces: both arms are run, neither is
    a number recorded earlier.
    """

    rounds: tuple[Round, ...]
    order: tuple[str, ...]
    seed: int

    def __post_init__(self) -> None:
        projects = {item.retrieved.project for item in self.rounds} | {
            item.withheld.project for item in self.rounds
        }
        if len(projects) > 1:
            message = (
                f"this ablation spans {sorted(projects)}. AC 4 grounds *the same unseen "
                "repository* both ways; two repositories differ by more than the condition"
            )
            raise LearningError(message)
        wrong = [
            item
            for item in self.rounds
            if item.retrieved.condition is not Condition.RETRIEVED
            or item.withheld.condition is not Condition.WITHHELD
        ]
        if wrong:
            message = (
                "a round holds an observation labelled for the other arm. The arm an "
                "observation is filed under and the condition it was taken under have to be the "
                "same fact, or the study is measuring the labelling"
            )
            raise LearningError(message)

    @property
    def helped(self) -> int:
        return sum(1 for item in self.rounds if item.saved > 0)

    @property
    def hurt(self) -> int:
        return sum(1 for item in self.rounds if item.saved < 0)

    @property
    def decisive(self) -> int:
        """Rounds where the two arms differed at all. Ties carry no sign and are
        dropped, which is what a sign test does with them."""
        return self.helped + self.hurt

    @property
    def steps_saved(self) -> float:
        """**AC 4's first figure**: the median steps the playbook saved per round."""
        return _median(item.saved for item in self.rounds)

    @property
    def completion_delta(self) -> float:
        """**AC 4's second figure**, and the guard. Median stages completed with the
        playbook minus without it. Anything below zero and no claim about steps
        stands."""
        return _median(
            item.retrieved.stages_completed - item.withheld.stages_completed for item in self.rounds
        )

    @property
    def interval(self) -> tuple[float, float] | None:
        """A 95% Wilson interval on the share of decisive rounds the playbook won.

        `None` where no round differed — which is not a failure of the study but
        its most likely early result, and the one the blocked version of this
        story would have produced for a completely different reason.
        """
        if not self.decisive:
            return None
        return wilson(self.helped, self.decisive)

    @property
    def effect(self) -> Effect:
        """**The guard is checked before the count, and that ordering is the rule.**

        `CLAUDE.md`: *guard counters on every metric — queries down while rows
        explode is not an improvement.* Here the guard is stage completion, and a
        run that took fewer steps because it stopped three stages earlier has not
        learned anything. So a fallen guard returns before any step arithmetic
        happens, rather than being reported alongside a claim it invalidates.

        Then the interval, and it has to clear a half rather than merely lead:
        winning six rounds of ten is a lead whose interval runs from 26% to 88%,
        and a corpus that could as easily have produced the opposite ordering has
        not shown one. That is `eval/ablation.py`'s third test, in the form a
        paired study takes.
        """
        if self.completion_delta < 0:
            return Effect.GUARD_FELL
        if len(self.rounds) < MINIMUM_GROUP_SIZE:
            return Effect.NOT_ESTABLISHED

        interval = self.interval
        if interval is None:
            return Effect.NO_EFFECT
        return Effect.IMPROVED if interval[0] > NO_BETTER_THAN_CHANCE else Effect.NO_EFFECT

    def describe(self) -> str:
        lines = [
            f"PLAYBOOK ABLATION — {len(self.rounds)} paired round(s), seed {self.seed}",
            f"  median steps saved: {self.steps_saved:+.1f}",
            f"  median stage-completion difference: {self.completion_delta:+.1f} of {STAGES}",
            f"  rounds won {self.helped}, lost {self.hurt}, tied "
            f"{len(self.rounds) - self.decisive}",
        ]
        interval = self.interval
        if interval is not None:
            lines.append(
                f"  share of decisive rounds won: {self.helped / self.decisive:.0%} "
                f"(95% CI {interval[0]:.0%} to {interval[1]:.0%})"
            )
        lines.append(f"  {self.effect.value}")
        if self.effect is Effect.GUARD_FELL:
            lines.append(
                "    Stage completion fell with the playbook in place. Whatever happened to the "
                "step count, this arm ground less of the repository — which is the shape "
                "`CLAUDE.md`'s guard-counter rule exists to refuse."
            )
        elif self.effect is Effect.NOT_ESTABLISHED:
            lines.append(
                f"    Fewer than {MINIMUM_GROUP_SIZE} rounds. The interval spans too much of the "
                "range to separate a playbook that helps from one that does nothing."
            )
        return "\n".join(lines)


def ablate(
    retrieved: Callable[[], Grounding],
    withheld: Callable[[], Grounding],
    rounds: int,
    *,
    seed: int | None = None,
) -> PlaybookAblation:
    """Ground the same repository both ways, interleaved. **AC 4.**

    Both arms are **callables and are run here**, which is S-1.6's construction
    borrowed wholesale: a stored measurement is a list of numbers and there is no
    parameter on this function a list of numbers fits. Comparing a fresh
    playbook-retrieved run against a step count recorded last week is the
    false-positive source `compare` exists to remove, and it would be worse here —
    the recorded number would predate the very entries under test.

    The order comes from `bench.interleaving.schedule`, so *what a fair schedule
    is* has one owner. `rounds` is floored at `MINIMUM_GROUP_SIZE` and checked
    **before anything runs**: each round is two full groundings of a real
    repository, and discovering the floor afterwards would waste all of them.

    Raises:
        TypeError: an arm is not callable — most likely measurements taken
            earlier, which is the one thing this must not accept.
        ValueError: `rounds` is below `MINIMUM_GROUP_SIZE`.
        LearningError: an arm returned an observation labelled for the other one.
    """
    # Widened to `object` so the check is not typed out of existence, which is
    # `compare`'s construction and for its reason: the annotations say both are
    # callable, and this guard is for the callers that are not type-checked.
    candidates: tuple[tuple[str, object], ...] = (("retrieved", retrieved), ("withheld", withheld))
    for label, arm in candidates:
        if not callable(arm):
            message = (
                f"the {label} arm is a {type(arm).__name__}, not a callable; `ablate` runs both "
                "arms itself and cannot accept measurements taken earlier — and a recorded step "
                "count would predate the entries under test"
            )
            raise TypeError(message)

    if rounds < MINIMUM_GROUP_SIZE:
        message = (
            f"rounds must be at least {MINIMUM_GROUP_SIZE}, got {rounds}; below that the "
            "interval cannot separate a playbook that helps from one that does nothing, and "
            "each round is two full groundings"
        )
        raise ValueError(message)

    drawn = schedule(rounds, "retrieved", "withheld", seed=seed)
    arms = {"retrieved": retrieved, "withheld": withheld}
    taken: dict[str, list[Grounding]] = {"retrieved": [], "withheld": []}

    for label in drawn.order:
        taken[label].append(arms[label]())

    return PlaybookAblation(
        rounds=tuple(
            Round(retrieved=first, withheld=second)
            for first, second in zip(taken["retrieved"], taken["withheld"], strict=True)
        ),
        order=drawn.order,
        seed=drawn.seed,
    )


def _median(values: Iterable[float]) -> float:
    """The median of an iterable of counts, as a float.

    Counts rather than durations, so the median is the summary that survives one
    run going badly — and `stats.stats` is not used because it refuses fewer than
    two samples and a one-round ablation is a legitimate thing to *describe*, just
    not to conclude from.
    """
    collected = [float(item) for item in values]
    return statistics.median(collected) if collected else 0.0

"""Running one experiment again, past the cache, and seeing whether it says the same.

Epic 9, S-9.6. *Re-runs one key experiment, bypassing the replay cache. Compares
against the recorded result. Material divergence produces `unsound`.*

**The whole story is what "material" means**, and the two figures it needs are
already in this project. `MetricKind` records that *a count is exact and
reproduces to the integer*, while *a duration here is one sample* — so the two
kinds do not deserve the same treatment, and a comparator that used one rule
would be wrong in one direction or the other:

- a **count** that moved at all is material. ADR 052 makes counts what raises a
  flag precisely because they reproduce exactly, so a query count of 7 becoming 8
  is not noise, it is a different run.
- a **duration** that moved is expected. S-0.4 measured the floor at roughly 20 ms
  on a 350 ms endpoint — about 12% — so only a move beyond the noise is evidence
  of anything.

**The control here is the one that matters most in this epic.** If timings within
the noise floor counted as divergence, every reproducibility check would fail,
every finding would be `unsound`, and the amended S-9.8 would route every
investigation back for more experiments — for ever. That is ADR 094's hazard
reached through the most mechanical attack in the epic, and it is why *a duration
that moved within the floor is not material* has its own test.

**A metric that vanished is material and is not a small divergence.** If the
recording holds `db.query` and the re-run does not, the two runs measured
different things and no comparison is possible. Reporting that as *unchanged*
because there is no difference to compute would be the S-3.1 failure — silence
read as agreement.

**The cache is bypassed with S-5.2's own mode, not with a flag.** `ReplayMode.OFF`
exists for exactly this: S-5.2 records that S-15.1's *cache disabled* study needs
a mode rather than an `if use_cache:` around every call site. Reusing it means
this audit cannot accidentally consult a recording — the object it holds has no
store to read.

**What this costs is stated rather than hidden.** A re-run is a real execution
against a real subject, so auditing a `longitudinal.soak` doubles an
hours-long experiment. The audit does not choose to spend that: it is handed an
executor and the caller decides, and `CostClass` on the primitive says in advance
what the bill will be.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from coldfix.audit.scales import MEASURED_DRIFT
from coldfix.diagnosis.log import Experiment
from coldfix.primitives.measurement import MetricKind

# What the harness does to run one experiment again: hand back what it measured.
# **This module never measures anything** — `CLAUDE.md` puts the measuring in the
# harness, and an auditor that produced its own numbers would be the one place
# that rule could not be enforced.
type Rerun = Callable[[Experiment], Mapping[str, float]]


class ReproducibilityError(Exception):
    """The re-run could not be compared against what was recorded."""


class Divergence(StrEnum):
    """What happened to one metric between the recording and the re-run.

    Five, and every one is a different next action: nothing; investigate a
    non-reproducing count; accept ordinary timing noise; treat a large timing
    move as real; and find out why the two runs measured different things.
    """

    UNCHANGED = "identical"
    COUNT_MOVED = "a count changed, and counts reproduce to the integer"
    DURATION_WITHIN_NOISE = "a duration moved, but by less than the measured noise floor"
    DURATION_BEYOND_NOISE = "a duration moved by more than the noise floor"
    NOT_REMEASURED = "the re-run did not measure this at all, so nothing can be compared"

    @property
    def material(self) -> bool:
        """Whether this divergence makes the finding unsound.

        `DURATION_WITHIN_NOISE` is the one that must not — see the module
        docstring for what happens to an investigation if it does.
        """
        return self in (
            Divergence.COUNT_MOVED,
            Divergence.DURATION_BEYOND_NOISE,
            Divergence.NOT_REMEASURED,
        )


@dataclass(frozen=True)
class MetricComparison:
    """One metric, before and after."""

    metric: str
    kind: MetricKind
    recorded: float
    rerun: float | None
    divergence: Divergence

    @property
    def relative_change(self) -> float | None:
        """How far it moved, as a fraction of what was recorded.

        `None` where it was not re-measured, or where the recording was zero —
        a relative change against nothing is a division nobody can read, and
        reporting `inf` would put a number in a report that means *undefined*.
        """
        if self.rerun is None or self.recorded == 0:
            return None
        return abs(self.rerun - self.recorded) / abs(self.recorded)

    def describe(self) -> str:
        if self.rerun is None:
            return f"{self.metric}: recorded {self.recorded!r}, not measured on the re-run"
        change = self.relative_change
        moved = f" ({change:.1%})" if change is not None else ""
        return (
            f"{self.metric} ({self.kind.value}): {self.recorded!r} -> {self.rerun!r}{moved}"
            f" — {self.divergence.value}"
        )


@dataclass(frozen=True)
class ReproducibilityAudit:
    """Whether one experiment says the same thing twice."""

    experiment: Experiment
    comparisons: tuple[MetricComparison, ...]
    relative_noise: float

    @property
    def material(self) -> tuple[MetricComparison, ...]:
        return tuple(item for item in self.comparisons if item.divergence.material)

    @property
    def unsound(self) -> bool:
        """AC 3. Material divergence, and only material divergence."""
        return bool(self.material)

    def describe(self) -> str:
        head = (
            f"Experiment {self.experiment.index} ({self.experiment.primitive} on "
            f"{self.experiment.target}) re-run with the replay cache off, against a "
            f"{self.relative_noise:.0%} noise floor."
        )
        if not self.unsound:
            return f"{head}\n  It reproduced. " + "\n  ".join(
                item.describe() for item in self.comparisons
            )
        lines = [f"{head}\n  It did not reproduce:"]
        lines.extend(f"    - {item.describe()}" for item in self.material)
        lines.append(
            "  A measurement that does not survive being taken twice cannot support a "
            "finding, whatever else the evidence says."
        )
        return "\n".join(lines)


def classify(
    *,
    kind: MetricKind,
    recorded: float,
    rerun: float | None,
    relative_noise: float,
) -> Divergence:
    """What kind of divergence one metric shows.

    The kind decides the rule, which is `MetricKind`'s whole purpose: *a count is
    exact and reproduces to the integer; a duration here is one sample.*
    """
    if rerun is None:
        return Divergence.NOT_REMEASURED
    if kind is MetricKind.COUNT:
        return Divergence.UNCHANGED if rerun == recorded else Divergence.COUNT_MOVED
    if rerun == recorded:
        return Divergence.UNCHANGED
    if recorded == 0:
        # Any movement away from zero is beyond every relative floor, and
        # dividing by it to say so would be arithmetic nobody can check.
        return Divergence.DURATION_BEYOND_NOISE
    moved = abs(rerun - recorded) / abs(recorded)
    return (
        Divergence.DURATION_WITHIN_NOISE
        if moved <= relative_noise
        else Divergence.DURATION_BEYOND_NOISE
    )


def check(
    experiment: Experiment,
    rerun: Rerun,
    *,
    kinds: Mapping[str, MetricKind],
    relative_noise: float = MEASURED_DRIFT,
) -> ReproducibilityAudit:
    """Run one experiment again and compare. AC 1, 2 and 3.

    `kinds` says which metrics are counts and which are durations. Supplied by
    the primitive that produced them — every result artifact in Epic 3 carries a
    `kinds` mapping — rather than guessed from the metric's name, because
    `seconds_ablated` and `render.calls_baseline` are not distinguishable by
    spelling and a wrong guess picks the wrong rule.

    `rerun` is the harness re-running it with the replay cache off. **Nothing
    here measures anything**, and there is no parameter through which a number
    could be supplied instead of executed.

    Raises:
        ReproducibilityError: a metric has no declared kind, which would leave
            this choosing a comparison rule for a number it cannot classify.
    """
    measured = rerun(experiment)

    missing = sorted(set(experiment.measurement) - set(kinds))
    if missing:
        message = (
            f"no metric kind was declared for {missing}. A count and a duration are compared by "
            "different rules — exactly, and against the noise floor — so guessing here would "
            "pick the wrong one and the wrong one is silent"
        )
        raise ReproducibilityError(message)

    comparisons = tuple(
        MetricComparison(
            metric=name,
            kind=kinds[name],
            recorded=float(value),
            rerun=None if name not in measured else float(measured[name]),
            divergence=classify(
                kind=kinds[name],
                recorded=float(value),
                rerun=None if name not in measured else float(measured[name]),
                relative_noise=relative_noise,
            ),
        )
        for name, value in sorted(experiment.measurement.items())
    )

    return ReproducibilityAudit(
        experiment=experiment,
        comparisons=comparisons,
        relative_noise=relative_noise,
    )

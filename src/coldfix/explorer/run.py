"""Bounding a grounding run, and failing in a way somebody can act on.

Epic 7, S-7.10. The last of the Explorer's own stories, and the one that decides
what happens when a repository will not ground — which, on the evidence of S-0.3,
is a thing that happens.

**Nothing here calls a model.** Counting attempts is arithmetic and comparing two
stage reports is a string comparison.

**Almost none of the machinery is new, and that is the point.** S-5.4 already
compiles `GROUND` at sixty steps with a disposition of `ABORT`, already refuses
the next step before the work rather than after it, and already has a stall check
whose whole argument is that *what counts as new information is decided by the
harness, not the agent*. Reimplementing any of it here would be a second set of
caps to keep in step with the first. What this story adds is the two things
S-5.4 could not supply on its own:

**S-7.11 gives "no new information" something to mean.** S-5.4's stall check
compares a digest of what a step *concluded*, and left to grounding that digest
would have to be the agent's own account of its progress — the self-judged
criterion `08-audit.md` F6 exists to remove. With nine harness-computed
predicates the digest is the **stage report**: fifteen steps that leave all nine
verdicts unchanged have taught the run nothing, whoever believes otherwise.

**A per-stage attempt budget is the tighter instrument.** S-0.3's grounding runs
took five to nineteen minutes each, and detecting at stage two that a repository
will not ground saves the other seven stages. Sixty steps spent entirely on
`configure` is a run that has already failed and does not know it, and the global
cap cannot see the difference.

**A failure names the stage, its predicate and what was tried there.** The
backlog note draws the distinction exactly: *reports what was attempted* is a
transcript, and *stage four never completed, here is its predicate and the last
error* is something a user can act on and S-17.2 can publish.

**There is no success path that avoids the gate.** AC 5 asks that the run never
report success when no workload does real work, and the way to guarantee that is
to have exactly one way to finish — `finish` calls S-7.9's `emit`, which calls
S-7.8's `accept`, which reads a verdict computed from measurements the harness
took. A run that stops any other way produces a `Failure`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from coldfix.cost.accounting import Phase
from coldfix.cost.budget import Budget, BudgetExhaustedError, ProgressStalledError
from coldfix.explorer.auth import Resolution as AuthResolution
from coldfix.explorer.emission import EmissionError, EmittedWorkload, emit
from coldfix.explorer.fingerprint import Identification
from coldfix.explorer.stages import Grounding, Outcome, Progress, Stage, evaluate
from coldfix.explorer.work import Verification
from coldfix.sandbox.verification import VerifiedReset

GROUNDING_STALL_AFTER = 15
"""AC 2's fifteen, and it is five times S-5.4's default for a reason.

An investigation repeating a conclusion three times is confirming something;
grounding legitimately repeats far more than that, because a stage is often
approached by trying one thing after another — install the driver, install the
other driver, set the environment variable — and each failed attempt leaves the
report unchanged without meaning the run has stopped learning. Fifteen is where
that stops being plausible.
"""

DEFAULT_STAGE_ATTEMPTS = 8
"""How many attempts one stage may take before the run gives up on it.

Chosen against S-0.3 rather than guessed: the worst single stage across three
repositories took six distinct attempts to satisfy, so eight leaves room above
the worst case actually observed and still fails a hopeless stage in under a
seventh of the global cap. It is a *per-stage* number and the sixty-step cap
still applies on top — a run can exhaust the global budget across seven stages
without ever exhausting one.
"""


class GroundingError(Exception):
    """A grounding run stopped, and the exception says where and why."""


@dataclass(frozen=True)
class Attempt:
    """One thing the agent tried at one stage, and what the harness saw after it.

    `what` is the agent's own account and is recorded as such — it is the only
    part of this record the agent writes, and it is a label rather than a
    verdict. `outcome` beside it is the harness's reading, measured after the
    attempt, which is what makes the pair readable: *installed psycopg2* next to
    *connect: does not hold* is a sentence a user can act on.
    """

    step: int
    stage: Stage
    what: str
    outcome: Outcome

    def describe(self) -> str:
        return (
            f"  step {self.step}: {self.stage.value} — {self.what} → {self.outcome.verdict.value}"
        )


@dataclass(frozen=True)
class Failure:
    """Why a run stopped, with the stage that never completed at the front.

    AC 4. The backlog note is the specification: a transcript is what somebody
    has to read, and *stage four never completed, here is its predicate and the
    last error* is what somebody can act on.
    """

    reason: str
    progress: Progress
    attempts: tuple[Attempt, ...]
    stopped_at: Outcome | None

    @property
    def attempts_at_the_stage(self) -> tuple[Attempt, ...]:
        """Everything tried at the stage that never completed, in order."""
        if self.stopped_at is None:
            return ()
        return tuple(entry for entry in self.attempts if entry.stage is self.stopped_at.stage)

    def report(self) -> str:
        lines = [f"Grounding failed: {self.reason}"]
        if self.stopped_at is not None:
            stage = self.stopped_at.stage
            lines.append(f"  never completed: {stage.value}")
            lines.append(f"    done would have meant: {stage.definition}")
            lines.append(f"    last measured: {self.stopped_at.detail}")
            tried = self.attempts_at_the_stage
            lines.append(f"    attempted {len(tried)} time(s) there:")
            lines.extend(f"    {entry.describe().strip()}" for entry in tried)
        lines.append(f"  completed: {[stage.value for stage in self.progress.completed]}")
        return "\n".join(lines)


class GroundingFailedError(GroundingError):
    """The run will not ground this repository. Carries the failure, not a message."""

    def __init__(self, failure: Failure) -> None:
        self.failure = failure
        super().__init__(failure.report())


@dataclass
class GroundingRun:
    """One repository's grounding attempt, bounded three ways.

    The three bounds are deliberately different instruments. S-5.4's sixty-step
    cap is the outer one and stops a run that is spending without arriving; its
    stall check stops a run that is spending without *learning*; and the
    per-stage budget stops a run that is spending all of it on one stage. A run
    can hit any of the three first, and they mean different things.
    """

    identification: Identification
    grounding: Grounding
    budget: Budget
    stage_attempts: int = DEFAULT_STAGE_ATTEMPTS
    attempts: list[Attempt] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.stage_attempts < 1:
            message = (
                f"a per-stage budget of {self.stage_attempts} lets no stage be attempted at all; "
                "a run that should not start is not configured, it is not begun"
            )
            raise GroundingError(message)
        if self.budget.stall_after != GROUNDING_STALL_AFTER:
            # Not a correction — a refusal. Silently substituting the right value
            # would hide that the caller asked for something else.
            #
            # **This comment used to say three was right for an investigation.**
            # It is not: `03-agents.md` §4.5 puts the investigate check at eight,
            # and S-8.9 refuses that phase's budget at any other value the same
            # way this refuses grounding's. S-5.4's default of three is a default,
            # not a phase's answer — every phase that has looked has needed its
            # own.
            message = (
                f"this budget stalls after {self.budget.stall_after} steps and grounding's "
                f"progress check is {GROUNDING_STALL_AFTER} (AC 2). Construct the budget with "
                f"stall_after={GROUNDING_STALL_AFTER}: a run that escalates after three "
                "unchanged reports would abandon a repository mid-install"
            )
            raise GroundingError(message)

    @property
    def steps(self) -> int:
        return len(self.attempts)

    def attempts_at(self, stage: Stage) -> int:
        return sum(1 for entry in self.attempts if entry.stage is stage)

    def observed(
        self, *, auth: AuthResolution | None = None, work: Verification | None = None
    ) -> None:
        """Record a harness result the later predicates read.

        Takes S-7.4's resolution and S-7.8's verification and nothing else. Both
        are objects whose contents were measured — a `Verification`'s verdict is
        computed from its observations, not stored — so this is a channel for
        evidence rather than for claims, which is why it exists at all.
        """
        if auth is not None:
            self.grounding = replace(self.grounding, auth=auth)
        if work is not None:
            self.grounding = replace(self.grounding, work=work)

    def progress(self) -> Progress:
        """Every stage, measured now."""
        return evaluate(self.identification, self.grounding)

    def attempt(self, stage: Stage, what: str, worst_case: Decimal = Decimal(0)) -> Outcome:
        """Spend one step on one stage and measure what it achieved.

        The order is the only one that enforces anything: authorize first — S-5.4
        is explicit that a ceiling can only be enforced before the work, because
        cost is known once a call returns — then evaluate, then record.

        Raises:
            GroundingFailedError: the per-stage budget for this stage is spent, the
                global cap is exhausted, or fifteen steps have left every stage
                report unchanged. All three carry the stage that never completed.
        """
        if self.attempts_at(stage) >= self.stage_attempts:
            raise GroundingFailedError(
                self._failure(
                    f"{stage.value} was attempted {self.stage_attempts} times without its "
                    "predicate coming true, which is its whole budget. Detecting here that this "
                    "repository will not ground saves the stages after it",
                    stopped_at=stage,
                )
            )

        try:
            self.budget.authorize(Phase.GROUND, worst_case=worst_case)
        except BudgetExhaustedError as error:
            raise GroundingFailedError(
                self._failure(error.exhaustion.report(), stopped_at=stage)
            ) from error

        progress = self.progress()
        outcome = progress.outcome(stage)
        self.attempts.append(Attempt(step=self.steps + 1, stage=stage, what=what, outcome=outcome))

        try:
            # The digest is the whole stage report, which is what makes AC 2's
            # "no new information" a measurement instead of an opinion. A step
            # that changed nothing anywhere reads identically to the one before
            # it; a step that moved any stage does not.
            self.budget.record_step(Phase.GROUND, conclusion=_digest(progress))
        except ProgressStalledError as error:
            raise GroundingFailedError(
                self._failure(error.stall.report(), stopped_at=stage, progress=progress)
            ) from error

        return outcome

    def finish(self, verification: Verification, *, reset: VerifiedReset) -> EmittedWorkload:
        """The only way a run succeeds. AC 5.

        Every check is somebody else's: `emit` refuses an unverified workload
        through S-7.8's gate, and that gate reads a verdict computed from
        measurements. There is nothing to decide here, which is the design — a
        second opinion about whether the run succeeded would be a second place
        for one to be wrong.

        Raises:
            GroundingFailedError: the workload does not do demonstrable work, or the
                nine stages are not all complete. Reported as a failure with the
                stage that never completed, not as an emission error, because the
                caller of a run wants to know where it stopped.
        """
        self.observed(work=verification)
        progress = self.progress()

        if not progress.complete:
            raise GroundingFailedError(
                self._failure(
                    "the run reached the end with a stage still incomplete, so there is no "
                    "grounded repository to report",
                    progress=progress,
                )
            )

        try:
            return emit(verification, reset=reset)
        except EmissionError as error:
            raise GroundingFailedError(
                self._failure(str(error), stopped_at=Stage.WORK, progress=progress)
            ) from error

    def give_up(self, reason: str) -> Failure:
        """Stop without a workload, for a reason the agent can name.

        Returns rather than raises: the agent choosing to stop is not an error,
        and `08-audit.md`'s null-result rule means an honest *this will not
        ground* is a legitimate output rather than a failure to produce one.
        """
        return self._failure(reason)

    def _failure(
        self,
        reason: str,
        *,
        stopped_at: Stage | None = None,
        progress: Progress | None = None,
    ) -> Failure:
        measured = progress or self.progress()
        stage_outcome = (
            measured.outcome(stopped_at) if stopped_at is not None else measured.first_incomplete
        )
        return Failure(
            reason=reason,
            progress=measured,
            attempts=tuple(self.attempts),
            stopped_at=stage_outcome,
        )

    def report(self) -> str:
        """Where the run stands, across all three bounds."""
        lines = [
            f"Grounding run: {self.steps} step(s), "
            f"{self.budget.remaining(Phase.GROUND)} of the global cap left"
        ]
        for stage in Stage:
            spent = self.attempts_at(stage)
            if spent:
                lines.append(f"  {stage.value}: {spent}/{self.stage_attempts} attempt(s)")
        return "\n".join(lines)


def _digest(progress: Progress) -> str:
    """What a step concluded, in the form S-5.4's stall check compares.

    The nine verdicts and nothing else. **Deliberately not the details**: a
    predicate's detail string carries row counts and error text that drift
    between otherwise identical steps — *0 of the application's own tables* one
    step and *0 of the application's own tables* the next, but a migration error
    naming a different table — and a digest that moved on those would never
    detect a stall at all. That is S-5.2's argument about durations, one layer up.
    """
    return "|".join(f"{entry.stage.value}={entry.verdict.name}" for entry in progress.outcomes)

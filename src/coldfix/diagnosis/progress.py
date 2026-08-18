"""Stopping, and having something to show for it.

Epic 8, S-8.9. Two of the three acceptance criteria were already built and the
third is the story.

**The 40-experiment cap exists and has since S-5.4**, compiled as
`Cap(40, StepUnit.EXPERIMENT, Scope.FINDING, Disposition.PARTIAL)`. Nothing here
re-implements it; the tests assert it, which is the third time an Epic 8 criterion
has turned out to be enforced elsewhere (S-8.1's no-cascade, S-8.6's attached
measurement).

**The progress check needed a number, and S-5.4's default is the wrong one.**
`03-agents.md` §4.5 says *8 experiments with no narrowing → escalate*, and
`DEFAULT_STALL_AFTER` is three. S-7.10 hit the same shape for grounding and
recorded the rule: **a budget with the wrong value is refused, not corrected**,
because silently substituting the right one hides that the caller asked for
something else. It also wrote, in a comment, that three *is* right for an
investigation — which §4.5 contradicts, and which is corrected there.

**"No narrowing" is decided by the harness, and that is S-5.4's own rule.** Its
docstring records F6's finding: *what counts as new information is decided by the
harness, not the agent*, because a self-judged success criterion is one the agent
is incentivised to claim. So the conclusion a step records is derived from the
verdict, and `02-architecture.md` §2.2 is what makes rejection different from
narrowing — *reject → new hypothesis informed by the exclusion; narrow → new
hypothesis, one level deeper.* Only the second goes deeper.

**A partial chain is not an evidence chain with fields missing.** This is the
story's substance. `EvidenceChain` requires at least one **confirming**
localization link, and relaxing that to accommodate an investigation that
confirmed nothing would destroy the guarantee S-8.6 exists for — a chain would
stop meaning *a cause was established*. So the two are separate types with
**opposite** requirements:

- an `EvidenceChain` requires at least one confirming experiment;
- a `PartialChain` requires **none**.

Together they partition, and neither can impersonate the other. A partial chain
carrying a confirmation would be a finding downgraded to a non-finding, which
loses a result — the mirror of the failure S-8.6's check prevents.

**It has no `mechanism`, no `site` and no `confidence`, and their absence is the
artifact's meaning.** Those are the claims an investigation that stopped early
cannot make, and a field for one is somewhere a reader could put a guess.
`00-BRIEF.md` §9 ships null results as answers; it does not ship them as findings
with the interesting parts left blank.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from coldfix.cost.budget import Budget, Disposition
from coldfix.diagnosis.chain import Symptom
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, Verdict

INVESTIGATION_STALL_AFTER = 8
"""`03-agents.md` §4.5. Eight, not S-5.4's three: an investigation that has
rejected three hypotheses has ruled out three things, which is progress of the
kind `00-BRIEF.md` §9 ships as an answer — abandoning it there would throw away
the exclusions it was buying."""

NO_NARROWING = "no narrowing"
"""The conclusion a step records when it did not go deeper.

A constant on purpose: S-5.4 stalls a phase whose last `stall_after` steps
concluded **the same thing**, so a run of these is what *eight experiments with no
narrowing* is expressed as. A step that did narrow records `None`, which clears
the run rather than extending it."""


class ProgressError(Exception):
    """An investigation could not be bounded, or could not report what it learned."""


def progress_conclusion(verdict: Verdict) -> str | None:
    """What a step concluded, in the only terms the stall check reads.

    `None` where the experiment made progress, which clears S-5.4's run of
    repeats; `NO_NARROWING` where it did not, which extends one.

    **A rejection is not narrowing**, and that is `02-architecture.md` §2.2's
    distinction rather than this module's: a rejection sends the agent to a new
    hypothesis *informed by an exclusion*, while a narrowing sends it *one level
    deeper*. Eight rejections in a row is an agent working through instruments
    without converging, which is exactly what §4.5's check is for.
    """
    return None if verdict is not Verdict.REJECTED else NO_NARROWING


def check_stall_configuration(budget: Budget) -> None:
    """Refuse a budget whose progress check is not the investigation's.

    S-7.10's construction, and its argument: not a correction but a refusal,
    because silently substituting the right value hides that the caller asked
    for something else.

    Raises:
        ProgressError: the budget stalls after some other number of steps.
    """
    if budget.stall_after != INVESTIGATION_STALL_AFTER:
        message = (
            f"this budget stalls after {budget.stall_after} steps and an investigation's progress "
            f"check is {INVESTIGATION_STALL_AFTER} (`03-agents.md` §4.5). Construct it with "
            f"stall_after={INVESTIGATION_STALL_AFTER}: at S-5.4's default of three, an agent that "
            "had ruled out three hypotheses would be stopped while it was still buying exclusions"
        )
        raise ProgressError(message)


class Stopped(StrEnum):
    """Why an investigation ended without establishing a cause.

    Three, because there are three ways to run out and a reader's next action
    differs for each — the same argument S-3.1 makes for four applicability
    states. Collapsing them into *it failed* would lose the one that says the
    subject may simply have no more applicable experiments.
    """

    CAP = "the experiment cap was reached"
    STALL = "no hypothesis narrowed for eight experiments"
    INSTRUMENTS = "every applicable instrument had already answered"

    @property
    def disposition(self) -> Disposition:
        """What §7.2's table says to do about it."""
        return Disposition.PARTIAL if self is Stopped.CAP else Disposition.ESCALATE


class PartialChain(BaseModel):
    """What an investigation learned when it did not find a cause.

    **Not an `EvidenceChain` and structurally unable to become one.** The two have
    opposite requirements — that one needs a confirming experiment, this one
    refuses to hold any — so they partition rather than overlap, and no consumer
    that wants a finding can be handed this by accident.

    There is no `mechanism`, no `site` and no `confidence`. Their absence is the
    artifact's meaning: those are the claims a stopped investigation cannot make,
    and a field for one is somewhere a reader could put a guess.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symptom: Symptom
    """What was observed. Screening established this before the investigation
    started, so it survives an investigation that establishes nothing else."""

    stopped: Stopped
    conditions: Conditions
    """What was in force when it stopped. Every exclusion here is conditional on
    these, and a partial chain read without them is F3 in a report again."""

    experiments: tuple[Experiment, ...] = Field(min_length=1)
    """Everything that ran. Non-empty, because an investigation that ran nothing
    has not learned that there is nothing to find — it has not looked."""

    exclusions: tuple[Exclusion, ...]
    """What was ruled out. **This is the result**, and it may legitimately be
    empty: forty experiments that all narrowed and never rejected exclude
    nothing while still having learned something."""

    @model_validator(mode="after")
    def _confirmed_nothing(self) -> Self:
        """A partial chain may not carry a confirmation.

        The mirror of `EvidenceChain`'s requirement. One that did would be a
        finding downgraded to a non-finding — a cause established and then
        reported as *we did not establish a cause* — which loses a result rather
        than merely mis-typing one.
        """
        confirmed = [item.index for item in self.experiments if item.verdict is Verdict.CONFIRMED]
        if confirmed:
            message = (
                f"experiment(s) {confirmed} came back confirmed, so this investigation found a "
                "cause and owes an evidence chain. A partial chain carrying a confirmation "
                "reports an established finding as an absent one"
            )
            raise ValueError(message)
        return self

    @property
    def narrowed(self) -> tuple[Experiment, ...]:
        return tuple(item for item in self.experiments if item.verdict is Verdict.NARROWED)

    def describe(self) -> str:
        """The result, written so that a reader can act on it.

        `00-BRIEF.md` §9 ships null results as answers, and §6 puts the failure
        catalogue above the success rate — so this reads as something learned
        rather than as an apology.
        """
        lines = [
            f"NO CAUSE ESTABLISHED — {self.stopped.value}.",
            f"  Disposition: {self.stopped.disposition.value}",
            f"\nSYMPTOM\n  {self.symptom.describe()}",
            f"\n{len(self.experiments)} experiment(s) ran, {len(self.narrowed)} of which narrowed.",
        ]
        if self.exclusions:
            lines.append(f"\nRULED OUT — under {self.conditions.describe()}:")
            lines.extend(
                f"  - {item.hypothesis} ({item.experiment.outcome})" for item in self.exclusions
            )
            lines.append(
                "\nThese hold under those conditions and no others. A reseed that moves one "
                "reopens the exclusions that depended on it."
            )
        else:
            lines.append(
                "\nNothing was ruled out: no hypothesis was rejected outright. The narrowings "
                "above are what this investigation bought."
            )
        return "\n".join(lines)


def partial_chain(
    *,
    symptom: Symptom,
    stopped: Stopped,
    conditions: Conditions,
    experiments: Sequence[Experiment],
    exclusions: Sequence[Exclusion],
) -> PartialChain:
    """Assemble what an investigation learned when it stopped short.

    Raises:
        ProgressError: nothing ran, or something was confirmed — in which case
            the investigation owes an evidence chain rather than this.
    """
    try:
        return PartialChain(
            symptom=symptom,
            stopped=stopped,
            conditions=conditions,
            experiments=tuple(experiments),
            exclusions=tuple(exclusions),
        )
    except ValueError as error:
        message = f"this is not a partial chain: {error}"
        raise ProgressError(message) from error

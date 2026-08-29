"""Reading what an experiment settled, and refusing a verdict with nothing under it.

Epic 8, S-8.3. The third of the Diagnostician's calls and the last of the loop
`02-architecture.md` §2.2 describes: hypothesize, design, execute, **read the
result**.

**Temperature 0.0, and the split from S-8.1 is the design decision the story is
about.** `03-agents.md` §2.4: *hypothesis generation benefits from diversity —
you want unusual explanations considered. Result interpretation must not vary —
8.24 seconds means the same thing every time.* Those two calls are frequently the
same question about the same log, which is why S-8.1 put the temperature into
S-0.7b's request digest; this is the call that change was made for.

**Determinism is a property of the request, not of the setting.** Temperature 0
makes a model's answer stable for a *fixed prompt*; it does nothing about a
prompt that differs. So the work here is making identical inputs render
identically — the measurement is rendered in sorted key order, and its values are
formatted canonically — and the test that matters compares **requests**, in a
fresh interpreter, rather than comparing two replays of one recording. Two
replays of one recording agree because the cache made them agree, which is a
property of S-0.7b and not of this module.

**Mechanical, mid tier, cascaded — and §3 had no row for it.** `04-cost.md` §2's
own table lists *Interpret a growth table — Diagnostician — ~40 calls/run*, the
most frequent step this agent takes, while §3's cascade table skipped it
entirely. The check that makes it cascade-safe is this module's substance.

**The verdict must cite measurements the harness recorded.** `CLAUDE.md`'s first
non-negotiable is *no finding without a measurement, enforced by schema and not
by prompt*, and its second is *do not let an agent report a measurement*. A model
that answers *confirmed — queries grew 40x* against a flat table has broken both,
and it is the archetypal cheap-model failure: inventing the number that supports
the answer it already gave. So the reply carries the figures it rests on, every
one is checked against what the harness measured, and a verdict resting on none
is refused.

**What that check does not catch, stated because a validator nobody can see the
limits of is worse than none.** It catches *fabrication*, not *misjudgement*: a
model can cite the right number and still call it the wrong way. The escalation
log is what tells the two apart in practice — S-5.6's `never_escalated()` exists
precisely because a check that has never rejected anything is either a step the
cheap model genuinely handles or a check that cannot fail, and it reports the
count rather than guessing which.

**The attached measurement is the harness's and there is no way for the model to
supply one.** `Interpretation.measurement` is filled from the mapping this
function was handed. A `measurement` key in the reply is ignored, and a test
asserts it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.cascade import NoDearerTierError
from coldfix.cost.context import Block
from coldfix.cost.pruning import MAX_SUMMARY_CHARS
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.log import Verdict
from coldfix.diagnosis.replies import Attempted, read_object
from coldfix.llm.client import ModelClient
from coldfix.llm.request import as_request, with_question
from coldfix.repair.sessions import refuse_foreign_session

INTERPRETATION_TEMPERATURE = 0.0
"""`03-agents.md` §2.4. 8.24 seconds means the same thing every time, and a
diagnosis that changes between identical runs is one nobody can act on —
`00-BRIEF.md` §6 makes *diagnostic agreement across ten runs* the headline
evaluation metric, so a call that varied here would be varying the number the
project is judged by."""

MAX_OUTPUT_TOKENS = 800
"""A verdict, a line, and the figures it rests on."""

_SYSTEM = """\
You are reading the result of one experiment. The measurement was taken by the \
harness; it is not yours to change and not yours to add to. Your job is to say \
what it settles.

Answer with a single JSON object and nothing else:

{"verdict": "confirmed|narrowed|rejected", "outcome": "...", "cites": {...}}

`verdict` is what this experiment settles about the hypothesis:
  confirmed  the measurement supports it
  narrowed   neither, but the search space is smaller than it was
  rejected   the measurement is inconsistent with it
`outcome` is one short line for the log, saying what happened.
`cites` are the measurements the verdict rests on, copied exactly from the \
MEASUREMENT block, as a JSON object of name to number.

A verdict resting on no measurement is not a verdict. Do not report a figure the \
MEASUREMENT block does not contain, and do not round one."""


class InterpretationError(Exception):
    """No usable reading of the result came back."""


class UninterpretableError(InterpretationError):
    """Every attempt failed the citation check, the dearest model included.

    Carries what each one got wrong, for ADR 085's reason: S-5.6 raises with the
    step type and the model and without the results it rejected, and *the verdict
    was invalid* is not actionable where *it cited a metric nobody measured three
    times* is.
    """

    def __init__(self, rejections: Sequence[str]) -> None:
        self.rejections = tuple(rejections)
        listed = "\n".join(f"  attempt {n}: {why}" for n, why in enumerate(rejections, start=1))
        super().__init__(f"no usable interpretation after {len(rejections)} attempts:\n{listed}")


def render_measurement(measurement: Mapping[str, float]) -> str:
    """The measurement block, and the reason AC 3 is achievable.

    **Sorted, and that is not tidiness.** A `Mapping` iterates in insertion order,
    so the same measurement assembled by two different code paths renders in two
    different orders, and two different prompts are two questions that a model at
    temperature 0 is free to answer differently. AC 3 asks that identical inputs
    give identical verdicts, and this is where identical inputs become an
    identical request.

    Values go through `json.dumps` so that the figure the model is asked to copy
    is written the way it will be read back.
    """
    if not measurement:
        return "  (nothing was measured)"
    return "\n".join(
        f"  {name} = {json.dumps(float(measurement[name]))}" for name in sorted(measurement)
    )


def check_citations(cites: Mapping[str, object], measurement: Mapping[str, float]) -> str | None:
    """Whether every figure quoted is one the harness recorded. A message, or `None`.

    This is §3's mechanical check for this step. It is deliberately exact: a
    tolerance would be this module deciding how far a quoted number may be from
    the measured one, which is a judgement, and the point of the check is that it
    contains none. The model is copying from a block in its own prompt.

    Returns a message rather than raising, because a rejected attempt has to stay
    retryable — an exception thrown out of the attempt would end the step instead
    of earning the retry.
    """
    if not cites:
        return (
            "the verdict cites no measurement at all. A conclusion with no measurement under it "
            "is one drawn from reading code, which the first non-negotiable exists to prevent"
        )

    recorded = ", ".join(sorted(measurement)) or "nothing"
    problems: list[str] = []

    for name in sorted(cites):
        quoted = cites[name]
        if name not in measurement:
            problems.append(f"{name!r} was not measured by this experiment; measured: {recorded}")
            continue
        # `bool` is a subclass of `int`, and `True == 1` — so a cited `true`
        # against a measured `1.0` would compare equal and pass as a figure.
        if isinstance(quoted, bool) or not isinstance(quoted, int | float):
            problems.append(f"{name} was cited as {quoted!r}, which is not a number")
            continue
        if float(quoted) != float(measurement[name]):
            problems.append(f"{name} was measured as {measurement[name]!r} and cited as {quoted!r}")

    return "; ".join(problems) if problems else None


class Interpretation(BaseModel):
    """What one experiment settled, and the figures that say so.

    **Not strict, and that is a decision rather than the default.** S-8.2 shipped
    a `strict=True` that turned out to guard nothing, so this one states what its
    configuration is for: strict mode would refuse `1004` for a `float` field, and
    JSON has no way to write `1004.0` as distinct from `1004` — the same notation
    argument the schema check makes. The value check that matters is
    `check_citations`, which runs on the raw reply before anything is coerced.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    outcome: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    """One line for the log. Bounded here for S-8.2's reason: S-5.8 refuses a
    summary that is empty, multi-line or over its budget, and a verdict refused at
    the log would fail after the experiment had already run."""

    cites: Mapping[str, float]
    """The figures the verdict rests on — a subset of what was measured."""

    measurement: Mapping[str, float]
    """Everything the harness measured. **Filled by the code, never by the reply.**

    `CLAUDE.md`: agents reason about measurements the harness took. There is no
    path from the model's answer to this field.
    """

    @field_validator("outcome")
    @classmethod
    def _one_line(cls, outcome: str) -> str:
        if "\n" in outcome or not outcome.strip():
            message = "an outcome must be one non-empty line; it is a summary field (S-5.8)"
            raise ValueError(message)
        return outcome

    @model_validator(mode="after")
    def _cited_what_was_measured(self) -> Self:
        """The non-negotiable, enforced where nothing can route around it.

        `parse` checks this first so the cascade gets a correctable sentence. It
        is checked again here so that **no other code path can build one without**
        — the first non-negotiable says *enforced by schema, not by prompt*, and a
        check that lived only in the parser would be enforced by the parser.
        """
        fault = check_citations(self.cites, self.measurement)
        if fault is not None:
            raise ValueError(fault)
        return self

    def describe(self) -> str:
        cited = ", ".join(f"{name}={self.cites[name]!r}" for name in sorted(self.cites))
        return f"{self.verdict.value}: {self.outcome} [{cited}]"


def render_question(
    *,
    hypothesis: Hypothesis,
    spec: ExperimentSpec,
    measurement: Mapping[str, float],
    rejections: Sequence[str] = (),
) -> str:
    """What was believed, what was run, what came back — and any earlier rejection.

    The log still reaches the model, and `NARROWED` still needs it: whether a
    result cut the search space is a judgement about the *investigation* rather
    than about one measurement. **It arrives as a cached block rather than in
    this question. S-17.16.** It used to be rendered in both places, so every
    call sent the log twice and paid full price for the copy that was supposed to
    be free.

    The rejections go last, for ADR 085's reason — everything before them is the
    part that must not move.
    """
    question = (
        f"HYPOTHESIS\n{hypothesis.describe()}\n\n"
        f"EXPERIMENT\n{spec.render()}\n\n"
        f"MEASUREMENT\n{render_measurement(measurement)}\n\n"
        "What does this experiment settle?"
    )
    if not rejections:
        return question

    listed = "\n".join(f"  - {why}" for why in rejections)
    return (
        f"{question}\n\n"
        f"YOUR EARLIER ANSWERS WERE REJECTED\n{listed}\n\n"
        "Answer again, fixing every problem listed. Copy the figures exactly."
    )


def parse(  # noqa: PLR0911 - every return is one distinct way a reply is not an
    # interpretation, and each carries the sentence a retry is corrected against.
    # A single *that answer was rejected* would collapse them and leave the
    # cascade retrying with nothing new to go on.
    text: str,
    measurement: Mapping[str, float],
) -> Attempted[Interpretation]:
    """Read one verdict out of a reply, and check what it rests on.

    Every failure is the retryable kind. `measurement` is this function's, not the
    reply's: a `measurement` key in the answer is never read, so the model has no
    way to report one.
    """
    read = read_object(text)
    if read.value is None:
        return Attempted.no(read.rejection)
    payload = read.value

    named = payload.get("verdict")
    if not isinstance(named, str):
        return Attempted.no("the answer has no `verdict`, so it settles nothing")
    try:
        verdict = Verdict(named.strip().lower())
    except ValueError:
        allowed = ", ".join(item.value for item in Verdict)
        return Attempted.no(f"{named!r} is not a verdict; it must be one of {allowed}")

    outcome = payload.get("outcome")
    if not isinstance(outcome, str):
        return Attempted.no("the answer has no `outcome`, and the log needs one line")

    cites = payload.get("cites", {})
    if not isinstance(cites, dict):
        return Attempted.no(
            f"`cites` must be an object of name to number, got {type(cites).__name__}"
        )

    fault = check_citations(cites, measurement)
    if fault is not None:
        return Attempted.no(fault)

    try:
        return Attempted.ok(
            Interpretation(
                verdict=verdict,
                outcome=outcome,
                cites=dict(cites),
                measurement=dict(measurement),
            )
        )
    except ValueError as error:
        return Attempted.no(f"this is not a usable interpretation: {error}")


def interpret(  # noqa: PLR0913 - what was believed, what was run and what came
    # back are three different facts, plus the session and the client. There is
    # deliberately no `validate` among them.
    session: Session,
    client: ModelClient,
    *,
    hypothesis: Hypothesis,
    spec: ExperimentSpec,
    measurement: Mapping[str, float],
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[Interpretation]:
    """Say what the experiment settled, at 0.0, on the mid tier, with a cascade.

    **There is no `validate` parameter**, for ADR 085's reason inverted from
    S-8.1's: this step cascades, so the danger is a caller supplying a check that
    accepts anything, which would make the citation check decorative and let a
    fabricated figure through inside a validated artifact.

    Raises:
        InterpretationError: the model declined or was cut off — an absent
            reading rather than a wrong one, so there is nothing to correct.
        UninterpretableError: every attempt cited something the harness did not
            measure, the dearest model included.
        BudgetExhaustedError: a cap or the ceiling stopped an attempt.
    """
    refuse_foreign_session(session, _SYSTEM, InterpretationError)
    rejections: list[str] = []
    first = render_question(hypothesis=hypothesis, spec=spec, measurement=measurement)
    step = Step(
        step_type=StepType.RESULT_INTERPRETATION,
        phase=Phase.INVESTIGATE,
        agent=Agent.DIAGNOSTICIAN,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str, blocks: Sequence[Block]) -> tuple[Attempted[Interpretation], TokenUsage]:
        question = render_question(
            hypothesis=hypothesis, spec=spec, measurement=measurement, rejections=rejections
        )
        # Rejections are the varying tail; only the question block moves, so the
        # retry reads the prefix the previous attempt cached.
        # **The system prompt is this module's, never the session's.** The
        # investigate loop runs three steps on one session, so the session's
        # string is not every step's prompt — sending it would tell two of them
        # to answer a third one's question. See `llm/request.py`.
        messages = as_request(with_question(blocks, question))
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=INTERPRETATION_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the model declined to interpret this result. A refusal is a successful response "
                "with an empty content list, and it is an absent reading rather than a wrong one"
            )
            raise InterpretationError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. Retrying under the same cap "
                "would truncate at the same place, so this is raised rather than rejected"
            )
            raise InterpretationError(message)

        attempt = parse(reply.text, measurement)
        if not attempt.valid:
            rejections.append(attempt.rejection)
        return attempt, reply.usage

    try:
        outcome = session.run(
            step,
            question=first,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
            call=call,
            validate=lambda attempt: attempt.valid,
        )
    except NoDearerTierError as error:
        raise UninterpretableError(rejections) from error

    reading = outcome.value.value
    if reading is None:  # pragma: no cover - the cascade cannot return an invalid attempt
        raise UninterpretableError(rejections)

    return StepOutcome(
        value=reading,
        step=outcome.step,
        routed_model=outcome.routed_model,
        blocks=outcome.blocks,
        viability=outcome.viability,
        calls=outcome.calls,
        escalated=outcome.escalated,
    )

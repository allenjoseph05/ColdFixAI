"""Turning a hypothesis into an experiment somebody can actually run.

Epic 8, S-8.2. S-8.1 asked *what do you believe and what would test it*; this
asks *what do you run it with*. It is the mirror of that story in every respect
that matters, and the contrast is the point: hypothesis generation is creative,
frontier-only and uncascadable because nothing can check it, while this is
mechanical, mid-tier and cascaded because `PrimitiveSchema.check` can.

**The primitive is not asked for again.** It came with the hypothesis, S-8.1
already validated it against S-3.1's `Selection`, and asking twice would create
two answers to one question with no rule for which wins. What the model chooses
here is the *design* — the parameters the schema marks as its to choose — and a
design assembled for some other instrument fails schema validation with the
parameter names in the message, which is a better diagnosis than a disagreement
would have been.

**A wrong answer is retried and an absent answer is raised, and the line between
them is what makes the cascade work.** A reply that is not JSON, or whose
arguments the schema rejects, is a *wrong design*: it fails the mechanical check,
S-5.6 tries again, and the step recovers. A refusal or a truncation is not a
wrong design, it is no design at all — there is nothing to correct, and feeding
*your previous answer was rejected because the model declined* back to a model is
noise. Those raise.

**A retry has to be able to differ from the attempt it retries.** §3's *2 cheap
attempts, then strong* silently assumes the second attempt is a second answer,
and at temperature 0 it is the same call: same model, same prompt, same sampling.
Two identical calls are one call and a wasted budget authorization — and against
S-0.7b's replaying client they are literally the same digest. So the rejection is
appended to the question, and the second attempt is a model being told what was
wrong rather than a model being asked again. The alternative was raising the
temperature, which buys variation by rolling dice on a step that has a correct
answer.

**There is no `validate` parameter, and here that is the opposite enforcement to
S-8.1's.** That story had nowhere to pass a validator so that no caller could
request a cascade. This one cascades, so the danger runs the other way: a
caller-supplied check that returned `True` would make the cascade decorative and
let an unrunnable specification through wearing a validated artifact's clothes.
The validator is the schema's, and it is not replaceable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from anthropic.types import MessageParam
from pydantic import BaseModel, ConfigDict, Field, field_validator

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.cascade import NoDearerTierError
from coldfix.cost.pruning import MAX_SUMMARY_CHARS
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.hypothesis import Hypothesis
from coldfix.diagnosis.log import ExperimentLog
from coldfix.diagnosis.schema import PrimitiveSchema, schema_of
from coldfix.llm.client import ModelClient
from coldfix.primitives.registry import Selection

DESIGN_TEMPERATURE = 0.0
"""A translation, not an invention.

`03-agents.md` §2.4 gives diversity to hypothesis generation because an unusual
explanation is worth considering; there is nothing unusual to want in *which
scales to sweep*, and a design that varies between identical calls is a design
nobody can reproduce. Variation between the cascade's attempts comes from the
rejection being fed back, which is a correction rather than a resample."""

MAX_OUTPUT_TOKENS = 1_000
"""A target line and an argument map. Required by S-5.4's `Step` rather than
defaulted there, because the ceiling is enforced against it."""

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None
"""What a model can put in an argument. Exactly what `json.loads` produces —
anything richer would be a value the reply could not have carried."""

_SYSTEM = """\
You are designing one experiment. The hypothesis is already formed and the \
instrument is already chosen. Your job is to say what to run it with.

Answer with a single JSON object and nothing else:

{"target": "...", "arguments": {...}}

`target` names what the instrument is pointed at — a workload, a call site, a \
component — on one short line.
`arguments` sets the parameters listed as yours to choose. Omit an optional \
parameter to accept its default, and never name one the harness supplies.

Choose the smallest settings that could still show the hypothesis to be wrong. \
An experiment that cannot come back negative is not an experiment."""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class DesignError(Exception):
    """No experiment specification came back."""


class UndesignableError(DesignError):
    """Every attempt failed the schema, including the one on the dearest model.

    Carries what each attempt got wrong. S-5.6 raises `NoDearerTierError` with
    the step type and the model and **without the results it rejected**, which
    for this step is the whole diagnosis — *the design was invalid* is not
    actionable and *it set `scales` to a string three times* is.
    """

    def __init__(self, primitive: str, rejections: Sequence[str]) -> None:
        self.primitive = primitive
        self.rejections = tuple(rejections)
        listed = "\n".join(f"  attempt {n}: {why}" for n, why in enumerate(rejections, start=1))
        super().__init__(
            f"no valid experiment specification for {primitive} after {len(rejections)} "
            f"attempts:\n{listed}"
        )


class ExperimentSpec(BaseModel):
    """A concrete experiment: the instrument, what it is pointed at, and its settings.

    Frozen for S-8.4's reason: this is about to be written into an append-only
    log, and a specification editable after the run is a record of what somebody
    wishes had been run.

    **An argument keeps the JSON type it arrived as, and `strict=True` is not
    what does it.** The first version of this docstring said strict mode stops
    `1` becoming `True`, and a sabotage pass found that turning strict off
    changes nothing here — because `JSONValue` covers every type JSON has, so
    there is nothing for a coercion to reach for. What actually preserves a
    boolean is `bool` being *in* the union; removing it is the sabotage that
    fails a test. Strict stays because it is the right stance for an artifact
    whose fields are exactly what a reply carried, but it is not load-bearing and
    saying it was would have left a guarantee resting on a setting that was not
    providing it.

    **This is not a call.** It carries the half of a primitive's parameters a
    model can answer; the harness still owes the workload, the reset and the
    session. `PrimitiveSchema.bound` is what it still owes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    primitive: str = Field(min_length=1)
    """S-3.1's registry name, taken from the hypothesis rather than re-asked."""

    target: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    """What the instrument is pointed at.

    Bounded here rather than at the log, because S-5.8 refuses a target that is
    empty, multi-line or over its summary budget — and a design that reached
    S-8.4 and was refused there would fail after the experiment had already run,
    with the measurement taken and nowhere to record it.
    """

    arguments: Mapping[str, JSONValue]
    """The parameters the schema marks as the design's. Empty is valid: a
    primitive can have nothing for a model to choose, and `bounds.headroom` does.
    """

    @field_validator("target")
    @classmethod
    def _one_line(cls, target: str) -> str:
        if "\n" in target or not target.strip():
            message = (
                "a target must be one non-empty line: it is a summary field, and S-5.8 refuses a "
                "summary that grows with its subject"
            )
            raise ValueError(message)
        return target

    @field_validator("arguments")
    @classmethod
    def _copied(cls, arguments: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
        return dict(arguments)

    def render(self) -> str:
        """The one-line form S-8.4's `design` field records.

        Canonical — sorted keys, JSON values — so two runs that designed the same
        experiment produce the same string and S-8.4's digest agrees about them.
        """
        settings = ", ".join(
            f"{name}={json.dumps(self.arguments[name])}" for name in sorted(self.arguments)
        )
        return f"{self.primitive}({settings}) on {self.target}"


@dataclass(frozen=True)
class Draft:
    """One attempt at a design: the specification, or why it was not one.

    The cascade's value type, and it has to be able to represent a failure.
    S-5.6 validates what the attempt *returns*, so an attempt that raised on a
    bad answer would end the step instead of earning the retry the cascade exists
    to provide.
    """

    spec: ExperimentSpec | None
    rejection: str

    @property
    def valid(self) -> bool:
        return self.spec is not None


def render_question(
    *,
    hypothesis: Hypothesis,
    schema: PrimitiveSchema,
    source: str,
    log: ExperimentLog,
    rejections: Sequence[str] = (),
) -> str:
    """The design question, with any earlier rejections last.

    Order is the cache's. `04-cost.md` §4 puts the stable prefix first and the
    varying question last, so the rejections go at the end: they are the only
    part that differs between the cascade's attempts, and putting them anywhere
    else would invalidate the prefix on every retry.
    """
    question = (
        f"HYPOTHESIS\n{hypothesis.describe()}\n\n"
        f"INSTRUMENT\n{schema.render()}\n\n"
        f"SOURCE UNDER SUSPICION\n{source}\n\n"
        f"EXPERIMENT LOG\n{log.render()}\n\n"
        "Specify the experiment."
    )
    if not rejections:
        return question

    listed = "\n".join(f"  - {why}" for why in rejections)
    return (
        f"{question}\n\n"
        f"YOUR EARLIER ANSWERS WERE REJECTED\n{listed}\n\n"
        "Answer again, fixing every problem listed. Do not repeat a rejected specification."
    )


def parse(  # noqa: PLR0911 - every return is one distinct way a reply is not a
    # specification, and each carries the sentence a retry needs to correct it. A
    # single *that reply was rejected* would collapse them and leave the cascade
    # retrying with nothing new to go on, which is the one thing it must not do.
    text: str,
    *,
    primitive: str,
    schema: PrimitiveSchema,
) -> Draft:
    """Read one specification out of a reply, and check it against the schema.

    Returns a `Draft` rather than raising, because every failure here is a *wrong
    answer* — the retryable kind. The mechanical check §3 names for this step is
    exactly this function returning a draft that is valid.
    """
    found = _JSON.search(text)
    if found is None:
        return Draft(None, f"no JSON object in the reply: {text.strip()[:200]!r}")

    try:
        payload = json.loads(found.group(0))
    except json.JSONDecodeError as error:
        return Draft(None, f"the reply was not valid JSON: {error}")

    if not isinstance(payload, dict):
        return Draft(None, f"a specification must be an object, got {type(payload).__name__}")

    target = payload.get("target")
    if not isinstance(target, str):
        return Draft(None, "the specification has no `target`, so nothing says what to point at")

    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        return Draft(None, f"`arguments` must be an object, got {type(arguments).__name__}")

    fault = schema.check(arguments)
    if fault is not None:
        return Draft(None, fault)

    try:
        spec = ExperimentSpec(primitive=primitive, target=target, arguments=arguments)
    except ValueError as error:
        return Draft(None, f"this is not a usable specification: {error}")

    return Draft(spec, "")


def design(  # noqa: PLR0913 - the hypothesis, its instruments, the source and the
    # log are what a design is made from, plus the session and the client. There
    # is deliberately no `validate` among them — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    hypothesis: Hypothesis,
    instruments: Selection,
    source: str,
    log: ExperimentLog,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[ExperimentSpec]:
    """Specify the experiment this hypothesis calls for, on the mid tier, with a cascade.

    The instrument is re-resolved through `instruments` rather than trusted from
    the hypothesis. A `Hypothesis` can be rebuilt from a log written in an earlier
    run, and S-3.1's selection is a snapshot of *this* run — so a primitive that
    was offered then and withheld now must be refused here, where the reason is
    still recorded, rather than at the point something tries to call it.

    `question` is measured against the first attempt. A retry differs only in its
    suffix, which is the part `04-cost.md` §4 says varies.

    Raises:
        DesignError: the model declined or was cut off — an absent design rather
            than a wrong one, so there is nothing for the cascade to correct.
        UndesignableError: every attempt failed the schema, the dearest included.
        PrimitiveUnavailableError: the hypothesis names an instrument this run
            withheld.
        UnknownPrimitiveError: it names one that does not exist.
        BudgetExhaustedError: a cap or the ceiling stopped an attempt.
    """
    schema = schema_of(instruments.get(hypothesis.primitive))
    rejections: list[str] = []

    first = render_question(hypothesis=hypothesis, schema=schema, source=source, log=log)
    step = Step(
        step_type=StepType.EXPERIMENT_DESIGN,
        phase=Phase.INVESTIGATE,
        agent=Agent.DIAGNOSTICIAN,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str) -> tuple[Draft, TokenUsage]:
        question = render_question(
            hypothesis=hypothesis,
            schema=schema,
            source=source,
            log=log,
            rejections=rejections,
        )
        messages: Sequence[MessageParam] = [{"role": "user", "content": question}]
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=DESIGN_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the model declined to design this experiment. A refusal is a successful response "
                "with an empty content list, and it is an absent design rather than a wrong one — "
                "there is nothing here for a retry to correct"
            )
            raise DesignError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. Retrying under the same cap "
                "would truncate at the same place, so this is raised rather than rejected"
            )
            raise DesignError(message)

        draft = parse(reply.text, primitive=hypothesis.primitive, schema=schema)
        if not draft.valid:
            rejections.append(draft.rejection)
        return draft, reply.usage

    try:
        outcome = session.run(
            step,
            question=first,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
            call=call,
            validate=lambda draft: draft.valid,
        )
    except NoDearerTierError as error:
        raise UndesignableError(hypothesis.primitive, rejections) from error

    spec = outcome.value.spec
    if spec is None:  # pragma: no cover - the cascade cannot return an invalid draft
        raise UndesignableError(hypothesis.primitive, rejections)

    # Rebuilt rather than mutated so the caller is handed the specification
    # itself. A `StepOutcome[Draft]` would make every caller unwrap a value that
    # the cascade has already guaranteed is there, and the one that forgot would
    # carry a `None` into an experiment.
    return StepOutcome(
        value=spec,
        step=outcome.step,
        routed_model=outcome.routed_model,
        blocks=outcome.blocks,
        viability=outcome.viability,
        calls=outcome.calls,
        escalated=outcome.escalated,
    )

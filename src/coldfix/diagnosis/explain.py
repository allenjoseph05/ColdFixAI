"""The half of an evidence chain the measurements do not contain.

Epic 8, S-8.11. `chain_from` takes *the measured half* off the investigation and
*the interpreted half* from its caller — mechanism, site, context — and until now
the only caller that supplied them was a test, which wrote three literal strings.
So `EvidenceChain` could be assembled by hand and by nothing in the system: the
criterion read as met and was unreachable, which is Epic 7's finding, Epic 8's,
and Epic 9's, a fourth time.

**This is a routed step and §3 already had a row for it.** `StepType.EVIDENCE_CHAIN`
carries the mechanical check *schema requires a measurement*, which is what makes
a cascade safe here: the artifact either validates or it does not, and a cheaper
tier that invents a site produces a chain the schema refuses. `CLAUDE.md` forbids
cascading on hypothesis generation and attack design because no deterministic
validator exists for those; here one does, and it is the schema rather than a
prompt.

**Every number still comes from the harness.** The reply carries a mechanism, a
line range and a list of implicated files — no measurement, no share of cost, no
confidence. `EvidenceChain` derives the confidence from the confirmations, the
localization carries each experiment with the measurement that produced it, and
there is no field on `Explanation` through which a figure could enter. That is
the first non-negotiable, kept by construction rather than by instruction.

**`context` is load-bearing for the patch, not commentary.** `02-architecture.md`
§3: *scope is determined by the evidence chain's context list, not by the agent's
guess.* A file admitted here is a file the Surgeon may edit, so every entry needs
a reason and `Implicated` refuses one that is whitespace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.cascade import NoDearerTierError
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import Implicated, Site, Symptom
from coldfix.diagnosis.log import Experiment
from coldfix.diagnosis.replies import Attempted, read_object
from coldfix.llm.client import ModelClient
from coldfix.primitives.ablation import share_metric

EXPLANATION_TEMPERATURE = 0.0
"""The same 0.0 `interpret` uses and for the same reason. `00-BRIEF.md` §6 makes
*diagnostic agreement across ten runs* the headline evaluation metric, and this
step decides the sentence a finding is judged by — a call that varied here would
be varying the number the project is judged on."""

MAX_OUTPUT_TOKENS = 1200
"""A mechanism, a line range, and the files the evidence implicated."""

_SYSTEM = """\
You are stating what a completed investigation established. The experiments have \
already run and the harness took every measurement; none of it is yours to \
change and none of it is yours to add to.

Answer with a single JSON object and nothing else:

{"mechanism": "...", "site": {"path": "...", "first_line": 1, "last_line": 1}, \
"context": [{"path": "...", "reason": "..."}]}

`mechanism` is one or two sentences saying *how* the cause produces the symptom \
— the causal story the experiments support, in the present tense.
`site` is where the cause lives: a repository-relative path and the line range \
of the code responsible.
`context` lists the other files the evidence implicated, each with the reason it \
is implicated. This list decides what a later repair is permitted to edit, so a \
file with no reason to be here must not be here. An empty list is a valid answer.

Do not report a measurement, a share of cost, a confidence, or a growth class. \
Those are the harness's and they are already recorded. State only what the \
evidence means."""


class ExplanationError(Exception):
    """The explanation could not be obtained."""


class UnexplainableError(ExplanationError):
    """Every attempt produced something the schema refused, the dearest included.

    Carries the rejections rather than the last one, because *what each tier got
    wrong* is the thing that says whether the prompt is at fault or the model is.
    """

    def __init__(self, rejections: Sequence[str]) -> None:
        self.rejections = tuple(rejections)
        joined = "\n  ".join(self.rejections) or "no attempt was made"
        super().__init__(f"no tier produced a usable explanation:\n  {joined}")


class Explanation(BaseModel):
    """The interpreted half of a chain, and nothing else.

    Frozen and `extra="forbid"`. **There is deliberately no field here that could
    carry a number**: a model that wanted to report a share of cost or a
    confidence has nowhere to put it, and `extra="forbid"` turns the attempt into
    a rejection rather than a silently dropped key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mechanism: str = Field(min_length=1)
    site: Site
    context: tuple[Implicated, ...] = ()

    def describe(self) -> str:
        files = ", ".join(item.path for item in self.context)
        return f"{self.site.describe()} — {self.mechanism}" + (f" [also: {files}]" if files else "")


def parse(text: str) -> Attempted[Explanation]:
    """Read one reply, or say why it is not usable.

    Returns rather than raises for `read_object`'s reason: every failure here is
    the correctable kind, and the sentence is written to be handed back to the
    next attempt rather than merely logged.
    """
    payload = read_object(text)
    if not payload.valid:
        return Attempted.no(payload.rejection)

    try:
        return Attempted.ok(Explanation.model_validate(payload.value))
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "the object"
        return Attempted.no(f"{where}: {first['msg']}")


def render_question(
    *,
    symptom: Symptom,
    confirming: Sequence[Experiment],
    exclusions: Sequence[str],
    source: str,
) -> str:
    """What the model is asked. The measured half, rendered for reading.

    The confirming experiments arrive **with their measurements**, because the
    question is *what does this mean* and a reply reasoning about numbers it was
    not shown is one nobody can check. The exclusions arrive too: what was ruled
    out is half of why the remaining explanation is the one that fits.
    """
    lines = [
        "SYMPTOM",
        f"  {symptom.describe()}",
        "",
        "CONFIRMING EXPERIMENTS",
    ]
    for item in confirming:
        measured = ", ".join(
            f"{name}={item.measurement[name]!r}" for name in sorted(item.measurement)
        )
        lines.append(f"  [{item.index}] {item.primitive} on {item.target}")
        lines.append(f"      hypothesis: {item.hypothesis}")
        lines.append(f"      measured:   {measured}")
        lines.append(f"      outcome:    {item.outcome}")

    if exclusions:
        lines.extend(["", "RULED OUT"])
        lines.extend(f"  {item}" for item in exclusions)

    lines.extend(["", "SOURCE", source])
    return "\n".join(lines)


def explain(  # noqa: PLR0913 - the symptom, the confirmations, the exclusions and
    # the source are four different facts about one investigation, plus the
    # session, the client and the two measured token counts. None is derivable
    # from the others.
    session: Session,
    client: ModelClient,
    *,
    symptom: Symptom,
    confirming: Sequence[Experiment],
    exclusions: Sequence[str],
    source: str,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[Explanation]:
    """State what the investigation established. **The interpreted half, produced.**

    **Refuses an investigation with nothing confirmed**, rather than asking a
    model to explain a cause that was never established. `00-BRIEF.md` §9 makes
    that a null result and S-8.9 gives it a partial chain; asking here would be
    the one place a finding could exist without a measurement behind it.

    Raises:
        ExplanationError: nothing was confirmed, or the model declined or was cut
            off — an absent reading rather than a wrong one.
        UnexplainableError: every tier produced something the schema refused.
        BudgetExhaustedError: a cap or the ceiling stopped an attempt.
    """
    if not confirming:
        message = (
            "this investigation confirmed nothing, so there is no cause to explain. What it has "
            "is a partial chain — `Investigation.partial_chain` — and asking a model to explain "
            "a cause nothing established is how a finding gets written without a measurement "
            "under it"
        )
        raise ExplanationError(message)

    rejections: list[str] = []
    question = render_question(
        symptom=symptom, confirming=confirming, exclusions=exclusions, source=source
    )
    step = Step(
        step_type=StepType.EVIDENCE_CHAIN,
        phase=Phase.INVESTIGATE,
        agent=Agent.DIAGNOSTICIAN,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str) -> tuple[Attempted[Explanation], TokenUsage]:
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _with_rejections(question, rejections)}],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=EXPLANATION_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the model declined to explain this investigation. A refusal is a successful "
                "response with an empty content list, and it is an absent reading rather than a "
                "wrong one"
            )
            raise ExplanationError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. Retrying under the same "
                "cap would truncate at the same place, so this is raised rather than rejected"
            )
            raise ExplanationError(message)

        attempt = parse(reply.text)
        if not attempt.valid:
            rejections.append(attempt.rejection)
        return attempt, reply.usage

    try:
        outcome = session.run(
            step,
            question=question,
            measured_prefix_tokens=measured_prefix_tokens,
            measured_prompt_tokens=measured_prompt_tokens,
            call=call,
            validate=lambda attempt: attempt.valid,
        )
    except NoDearerTierError as error:
        raise UnexplainableError(rejections) from error

    reading = outcome.value.value
    if reading is None:  # pragma: no cover - the cascade cannot return an invalid attempt
        raise UnexplainableError(rejections)

    return StepOutcome(
        value=reading,
        step=outcome.step,
        routed_model=outcome.routed_model,
        blocks=outcome.blocks,
        viability=outcome.viability,
        calls=outcome.calls,
        escalated=outcome.escalated,
    )


def _with_rejections(question: str, rejections: Sequence[str]) -> str:
    """The question, plus what the previous attempts got wrong.

    Appended rather than prepended so the cached prefix stays the prefix —
    `04-cost.md` §4 caches on a prefix match, and text inserted above the
    question would invalidate it on every retry.
    """
    if not rejections:
        return question
    listed = "\n  ".join(rejections)
    return f"{question}\n\nPREVIOUS ATTEMPTS WERE REFUSED\n  {listed}"


def shares_from(
    confirming: Sequence[Experiment], *, metric: str = "seconds"
) -> Mapping[int, tuple[str, float, str]]:
    """Each confirming experiment's share of `metric`, read off its own measurement.

    `chain_from` takes `(scope, share_of_cost, basis)` per experiment index. The
    scope is what the instrument was pointed at, the share is a number the
    primitive computed, and the basis names the instrument that computed it — so
    all three come off the record rather than from a caller's opinion.

    **`Executor` returns `Mapping[str, float]`, so a share reaches the log only as
    one more number in that mapping.** That is narrow — Epic 9 recorded that
    `kinds` and a `Fit` cannot cross at all, and answers `UNTESTED` for the three
    attacks that need them — but a fraction *is* a float, so this one fits. What
    was missing was a name both ends agree on, which `share_metric` now owns.

    An experiment carrying no share is refused by name rather than given a
    plausible fraction: a number nobody measured sitting under a finding is the
    first non-negotiable inverted.

    Raises:
        ExplanationError: a confirming experiment carries no share of `metric`.
    """
    key = share_metric(metric)
    missing = sorted(item.index for item in confirming if key not in item.measurement)
    if missing:
        message = (
            f"experiment(s) {missing} confirmed the cause and recorded no {key!r}. The primitive "
            f"that ran knows what fraction of {metric!r} disappeared with the component — "
            "`AblationResult.reported` names it — and a chain that guessed it would be putting a "
            "number nobody measured under a finding"
        )
        raise ExplanationError(message)

    return {
        item.index: (
            item.target,
            item.measurement[key],
            f"{key} reported by the {item.primitive} primitive that ran",
        )
        for item in confirming
    }

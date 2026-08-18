"""Handing a diagnosis to something that did not produce it.

Epic 9, S-9.1. `10-BACKLOG.md`: *audit the diagnosis before any repair spend.
Build before the Surgeon* — because the patch audit checks equivalence, cheats,
trades and scope, and **if the diagnosis is wrong all of those pass.** A correct
fix to a non-problem is equivalent, is not a cheat, trades nothing, and breaks no
callers.

**The isolation is structural or it is nothing.** `CLAUDE.md`'s non-negotiable:
*the Adversary never sees the Surgeon's reasoning — enforced by constructing a
fresh message list, not by instructing the model to ignore it.* A system prompt
saying *disregard the reasoning above* is a wish; a list that never contained it
is a guarantee.

**Composing S-8.7 with this story found a contradiction between two acceptance
criteria.** AC 1 says the auditor receives the **raw experiment log**; AC 2 says
**no Diagnostician reasoning** is included. S-8.7 added `rationale` to every log
record — *why this instrument was worth its cost* — which is the Diagnostician's
reasoning, written by the Diagnostician, sitting in the raw log. Handing the log
over verbatim satisfies AC 1 by breaking AC 2.

`08-audit.md` decides which way to resolve it, and gives the number: isolation
*removes the explicit rationalization, which is the documented risk — 72% of
reward-hacking episodes carry explicit justifying reasoning.* So the explicit
rationalization is exactly what comes out. `render_evidence` strips two fields:

- `rationale` — free prose whose entire purpose is to justify a choice;
- `outcome` — the agent's one-line gloss on what its own measurement meant.

and keeps what was tested, what ran, and what was measured. **`verdict` stays**,
and that is a decision rather than an oversight: it is a three-valued
classification S-8.3 ties to cited measurements, not prose, and an auditor asked
whether an exclusion was adequate has to know that something *was* excluded.

**The audit gets its own session, and reusing the Diagnostician's is refused.**
`Session` holds one assembled prompt per model, carrying that agent's system
prompt, playbook and source as a cached prefix. An audit run through the
Diagnostician's session would inherit all of it — the isolation would be undone
by the object it was billed through, silently, while every message list this
module built stayed clean.

**Isolation is partial and this module says so rather than implying otherwise.**
`08-audit.md`'s honest position: *it removes the explicit rationalization. It does
not remove framing bias.* The log still encodes which experiments the
Diagnostician thought worth running, and nothing here changes that. `RESIDUE`
carries the sentence, for the reason S-7.12's `Anchor.residue` does — a bound
nobody can read is one somebody will quote past.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from anthropic.types import MessageParam

from coldfix.cost.accounting import Agent, ExchangeRate, Phase, TokenUsage
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.llm.client import ModelClient

AUDIT_TEMPERATURE = 0.8
"""An audit is an attack, and `04-cost.md` §3 records that no deterministic
validator exists for designing one. Diversity is the point for the same reason
S-8.1 has it: an objection nobody thought of is the one worth paying for."""

MAX_OUTPUT_TOKENS = 2_000

RESIDUE = (
    "This isolation is partial. It removes the explicit rationalization — the "
    "documented risk, since 72% of reward-hacking episodes carry explicit "
    "justifying reasoning — and it does not remove framing bias: the log still "
    "records which experiments the Diagnostician thought worth running, and an "
    "auditor reading it inherits that frame. Do not describe this as clean "
    "separation."
)

WITHHELD = ("rationale", "outcome")
"""The fields `render_evidence` removes. Named as data so a test can assert the
list rather than assert against a rendering, and so adding a third is a line here
rather than an edit inside a loop."""

_SYSTEM = """\
You are auditing a performance diagnosis that somebody else produced. You did not \
run these experiments and you are not being asked to agree with them.

You are given what was tested, what instrument ran, how it was configured, and \
what the harness measured. You are deliberately not given the reasoning behind \
any of it.

Your job is to find the objection: a way these measurements could be consistent \
with a different cause, a condition under which a ruled-out hypothesis was never \
really ruled out, or a scale or fixture that could have hidden the real answer.

Quote the measurements you are reasoning from. An objection with no measurement \
under it is an opinion."""


class AuditError(Exception):
    """The audit could not be invoked in isolation."""


def render_evidence(log: ExperimentLog) -> str:
    """The raw log, with the explicit rationalization taken out. AC 1 and AC 2.

    Built from the `Experiment` artifacts rather than from S-5.8's rendered
    summaries, because the summary is composed from `outcome` — one of the two
    fields that has to come out. Rendering the log the Diagnostician reads and
    then trying to remove a field from the text would be editing prose to
    enforce a boundary, which is the thing this story exists not to do.
    """
    if not log.experiments:
        return "No experiments were run. There is nothing here to audit."

    lines = [
        "EVIDENCE — what was tested, what ran, and what the harness measured.",
        "The reasoning behind these choices is deliberately absent.",
        "",
    ]
    lines.extend(_describe(experiment) for experiment in log.experiments)
    return "\n".join(lines)


def _describe(experiment: Experiment) -> str:
    measured = ", ".join(
        f"{name}={experiment.measurement[name]!r}" for name in sorted(experiment.measurement)
    )
    return (
        f"{experiment.index}. tested: {experiment.hypothesis}\n"
        f"   instrument: {experiment.primitive} on {experiment.target}\n"
        f"   configured: {experiment.design}\n"
        f"   measured: {measured}\n"
        f"   verdict: {experiment.verdict.value}"
    )


def audit_messages(evidence: str, question: str) -> list[MessageParam]:
    """A **fresh** list, constructed here and shared with nothing.

    The non-negotiable in one function: there is no accumulated conversation to
    append to, no prior turn to carry forward, and no parameter through which
    either could arrive. A caller holding the Diagnostician's message history
    cannot pass it, because there is nowhere to put it.

    Returns a new `list` on every call rather than a cached or module-level one,
    so that a caller mutating what it got back cannot reach the next audit.
    """
    return [{"role": "user", "content": f"{evidence}\n\n{question}"}]


def audit_session(
    *,
    rate: ExchangeRate,
    source: str,
    ceiling_eur: Decimal | None = None,
    system: str = _SYSTEM,
) -> Session:
    """A session belonging to the auditor, with the auditor's prompt as its prefix.

    `system` defaults to the finding auditor's and is a parameter because a
    **second** adversarial audit exists: S-10.3 attacks a falsification test
    rather than a diagnosis, so its prompt differs while every isolation argument
    below applies unchanged. `CLAUDE.md` keeps things concrete until a second case
    turns up; this is the second case, and the alternative was a copy of this
    function that would drift.

    Separate from the Diagnostician's because `Session` caches one assembled
    prompt per model and that prompt carries its owner's system text. Sharing one
    would hand the auditor the Diagnostician's framing through the cache while
    every message list here stayed clean — isolation undone by the object it was
    billed through.

    The playbook is deliberately empty: a playbook is accumulated advice about
    how to investigate, and an auditor reasoning from the investigator's habits
    is inheriting exactly the frame `08-audit.md` says this cannot remove but
    should not add to.
    """
    return Session(
        system=system,
        playbook="(none: an auditor works from the measurements, not from a playbook)",
        source=source,
        rate=rate,
        ceiling_eur=ceiling_eur,
    )


def refuse_shared_session(session: Session, *, expected: str = _SYSTEM) -> None:
    """Refuse a session that belongs to some other agent.

    `expected` is the prompt this particular auditor should own — the finding
    auditor's by default, S-10.3's when a falsification test is the subject. Two
    audits with two prompts is still one rule: **a session whose prefix belongs to
    somebody else undoes the isolation silently.**

    Raises:
        AuditError: its system prompt is not the auditor's, so its cached prefix
            is somebody else's and running the audit through it would inherit
            what this module spent the rest of its length removing.
    """
    if session.system != expected:
        message = (
            "this session's prompt is not the auditor's, so its cached prefix belongs to another "
            "agent and every call billed through it would carry that agent's system text and "
            "source. Build one with `audit_session`: the isolation is the fresh message list "
            "*and* the fresh prompt, and a shared session undoes the second silently"
        )
        raise AuditError(message)


def invoke(  # noqa: PLR0913 - the log, the question and the measured token counts
    # are four different facts, plus the session and the client. There is
    # deliberately no parameter for prior turns or for a chain — see the module
    # docstring.
    session: Session,
    client: ModelClient,
    *,
    log: ExperimentLog,
    question: str,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[str]:
    """Ask an auditor to attack a diagnosis, from the measurements alone.

    **There is no `chain` parameter and no `messages` parameter.** AC 1 says the
    auditor receives the raw experiment log rather than the assembled evidence
    chain, and the enforcement is that there is nowhere to pass one — the
    construction S-8.1 used for `validate` and S-7.8 for `force`.

    Returns the reply text. S-9.1 owns the invocation; each attack story owns what
    it reads out of the answer, and a schema invented here would fix a shape those
    stories have not designed.

    Raises:
        AuditError: the session belongs to another agent, or the model declined
            or was cut off.
        BudgetExhaustedError: the audit's round cap is spent.
    """
    refuse_shared_session(session)

    evidence = render_evidence(log)
    step = Step(
        step_type=StepType.ATTACK_DESIGN,
        phase=Phase.FINDING_AUDIT,
        agent=Agent.FINDING_AUDITOR,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str) -> tuple[str, TokenUsage]:
        messages: Sequence[MessageParam] = audit_messages(evidence, question)
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=AUDIT_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the auditor declined to answer. A refusal is a successful response with an empty "
                "content list, so this is reported rather than read as an audit that found nothing "
                "to object to — which is the reading that would let a decline pass as a pass"
            )
            raise AuditError(message)
        if reply.truncated:
            message = (
                f"the audit was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated objection is "
                "one whose conclusion is missing, and treating it as complete would accept "
                "whatever it happened to say first"
            )
            raise AuditError(message)
        return reply.text, reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )

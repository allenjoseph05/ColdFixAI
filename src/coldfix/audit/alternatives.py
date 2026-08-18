"""A different mechanism the same measurements would also produce.

Epic 9, S-9.5. *Proposes a different mechanism consistent with the same
measurements. If one exists and was not excluded, verdict is `unsound`.*

**The first attack in this epic that genuinely needs a model.** S-9.2, S-9.3 and
S-9.4 turned out to be arithmetic — which axes were varied, how wide the span
was, what r² came back. *Is there another story these numbers would also tell* is
not computable, and `08-audit.md` names it as the flaw schema validation cannot
reach: *"No finding without a measurement" prevents fabrication. It does not
prevent a correct measurement supporting a wrong conclusion.*

**"No alternative" has to be a first-class answer, and that is S-0.8's finding
applied.** An attack that always finds something is worthless — and worse than
worthless here, because AC 2 turns any alternative into `unsound`, and the
amended S-9.8 routes `unsound` back to investigate. An auditor that cannot say
*I have nothing* would guarantee an investigation never ends, which is precisely
the failure S-0.8 measured 60 times out of 60. So the prompt offers the empty
answer explicitly, the parser treats it as ordinary rather than exceptional, and
a test asserts it survives the round trip.

**The judgement is the model's; the citations are checked.** An alternative that
quotes a figure nobody measured is not *consistent with the same measurements* —
it is a story that happens to mention numbers. So every figure it rests on is
checked against what the harness actually recorded, which is the same discipline
S-8.3 applies to a verdict and the same non-negotiable underneath both.

**Checked against every experiment, not against one.** `check_citations` compares
one mapping to one measurement; a log holds many, and the same metric legitimately
takes different values in different experiments — `db.query` is 7 in the sweep and
1004 in the ablation. Reusing that function here would call the second value a
fabrication. This checks each cited pair against **every** pair the log recorded,
which is what *consistent with the measurements* means when there is more than one.

**Whether an alternative was already excluded is left to the auditor to argue.**
AC 2 says an alternative makes a finding unsound *if it was not excluded*, and
deciding whether exclusion X covers alternative Y is the same semantic judgement
this whole story exists because code cannot make. So the auditor is shown the
rejections — S-9.1's evidence already carries them, verdict included — and asked
to say which one fails to cover its proposal. An alternative offered without that
argument is refused, because *there might be another explanation* with no account
of why the existing experiments missed it is not an objection anybody can act on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from coldfix.audit.invocation import AuditError, invoke
from coldfix.cost.session import Session, StepOutcome
from coldfix.diagnosis.log import Experiment, ExperimentLog
from coldfix.diagnosis.replies import read_object
from coldfix.llm.client import ModelClient

NONE_FOUND = "none"
"""What the auditor answers when the measurements support no other mechanism.

A literal rather than an absence, because an empty reply and a considered *there
is no alternative* are different answers and only the second is a result."""

QUESTION = f"""\
Propose a mechanism, different from the one these experiments were investigating, \
that would produce these same measurements.

Answer with a single JSON object and nothing else:

{{"mechanism": "...", "cites": {{"metric": number}}, "not_excluded_because": "..."}}

`mechanism` is the alternative cause, specifically enough to be tested.
`cites` are the measurements it is consistent with, copied exactly from the \
EVIDENCE block.
`not_excluded_because` says which of the rejections above fails to rule your \
alternative out, and why. An alternative the experiments already excluded is not \
an objection.

If the measurements support no other mechanism, answer exactly \
{{"mechanism": "{NONE_FOUND}"}} — that is a result, not a failure, and it is the \
right answer whenever you have to strain to find one."""


class AlternativeError(AuditError):
    """No usable answer came back from the alternative-explanation attack."""


@dataclass(frozen=True)
class Alternative:
    """A different mechanism the same numbers would also produce."""

    mechanism: str
    cites: Mapping[str, float]
    not_excluded_because: str

    def describe(self) -> str:
        quoted = ", ".join(f"{name}={self.cites[name]!r}" for name in sorted(self.cites))
        return (
            f"An alternative mechanism fits these measurements: {self.mechanism}\n"
            f"  consistent with: {quoted}\n"
            f"  not ruled out because: {self.not_excluded_because}"
        )


@dataclass(frozen=True)
class AlternativeAudit:
    """What the attack found, and what it means for the finding."""

    alternative: Alternative | None

    @property
    def unsound(self) -> bool:
        """AC 2, and the whole of it: an alternative that exists and was not
        excluded makes the finding unsound."""
        return self.alternative is not None

    def describe(self) -> str:
        if self.alternative is None:
            return (
                "No alternative mechanism was proposed: the auditor could find no other "
                "cause these measurements would also produce. That is this attack passing, "
                "not this attack failing to run."
            )
        return self.alternative.describe()


def measured_pairs(experiments: Sequence[Experiment]) -> Mapping[str, set[float]]:
    """Every value each metric took, across every experiment.

    A set per metric rather than one value, because the same metric legitimately
    differs between experiments — `db.query` is 7 in the sweep that ruled the
    database out and 1004 in the ablation that found the cause. Collapsing them
    would make one of the two real numbers look fabricated.

    Takes the experiments rather than the log so that S-10.1 can ask the same
    question of an `EvidenceChain`, whose experiments live inside its
    localization links and its exclusions. One loop, two artifacts — the
    alternative was a second copy differing only in how it reached the records.
    """
    values: dict[str, set[float]] = {}
    for experiment in experiments:
        for name, value in experiment.measurement.items():
            values.setdefault(name, set()).add(float(value))
    return values


def check_against_log(cites: Mapping[str, object], log: ExperimentLog) -> str | None:
    """Whether every figure quoted is one some experiment actually recorded.

    Deliberately **not** `check_citations`: that compares one mapping against one
    measurement, and a log is many. See `measured_pairs`.
    """
    if not cites:
        return (
            "the alternative cites no measurement, so nothing establishes it is consistent "
            "with what was observed rather than merely with what was expected"
        )

    recorded = measured_pairs(log.experiments)
    problems: list[str] = []
    for name in sorted(cites):
        quoted = cites[name]
        if name not in recorded:
            known = ", ".join(sorted(recorded)) or "nothing"
            problems.append(f"{name!r} was never measured; the log holds {known}")
            continue
        if isinstance(quoted, bool) or not isinstance(quoted, int | float):
            problems.append(f"{name} was cited as {quoted!r}, which is not a number")
            continue
        if float(quoted) not in recorded[name]:
            seen = ", ".join(repr(value) for value in sorted(recorded[name]))
            problems.append(f"{name} was cited as {quoted!r}; the log records {seen}")

    return "; ".join(problems) if problems else None


def parse(text: str, log: ExperimentLog) -> AlternativeAudit:
    """Read one alternative out of a reply, or read that there is none.

    Raises:
        AlternativeError: the reply is not a usable answer. Raised rather than
            returned, because `ATTACK_DESIGN` cannot cascade — `04-cost.md` §3
            records that no deterministic validator exists for designing an
            attack, so there is no cheap retry to fall back on and a malformed
            answer is an absent one.
    """
    read = read_object(text)
    if read.value is None:
        raise AlternativeError(read.rejection)
    payload = read.value

    mechanism = payload.get("mechanism")
    if not isinstance(mechanism, str) or not mechanism.strip():
        message = (
            "the reply names no mechanism. An attack that cannot say what the alternative *is* "
            f"has not proposed one, and {NONE_FOUND!r} is the way to say there is none"
        )
        raise AlternativeError(message)

    if mechanism.strip().lower() == NONE_FOUND:
        return AlternativeAudit(alternative=None)

    cites = payload.get("cites", {})
    if not isinstance(cites, dict):
        message = f"`cites` must be an object of metric to number, got {type(cites).__name__}"
        raise AlternativeError(message)

    fault = check_against_log(cites, log)
    if fault is not None:
        message = (
            f"this alternative does not rest on what was measured: {fault}. An explanation "
            "consistent with numbers nobody took is a story rather than an objection"
        )
        raise AlternativeError(message)

    because = payload.get("not_excluded_because")
    if not isinstance(because, str) or not because.strip():
        message = (
            "the alternative does not say which rejection fails to cover it. AC 2 makes a "
            "finding unsound only when an alternative was *not excluded*, and *there might be "
            "another explanation* with no account of why the existing experiments missed it is "
            "not an objection anybody can act on"
        )
        raise AlternativeError(message)

    return AlternativeAudit(
        alternative=Alternative(
            mechanism=mechanism.strip(),
            cites={name: float(value) for name, value in cites.items()},
            not_excluded_because=because.strip(),
        )
    )


def attack(  # noqa: PLR0913 - the log, the two measured token counts and the
    # finding id are four different facts, plus the session and the client. They
    # are S-9.1's parameters passed straight through; collapsing any of them
    # would be this module inventing a shape `invoke` already fixed.
    session: Session,
    client: ModelClient,
    *,
    log: ExperimentLog,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[AlternativeAudit]:
    """Ask for a rival explanation, from the measurements alone.

    Goes through S-9.1's `invoke`, so the isolation is that story's: a fresh
    message list, the auditor's own session, and evidence with the
    Diagnostician's reasoning removed.

    Raises:
        AlternativeError: no usable answer came back.
        AuditError: the session belongs to another agent, or the model declined.
    """
    outcome = invoke(
        session,
        client,
        log=log,
        question=QUESTION,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    audit = parse(outcome.value, log)
    return StepOutcome(
        value=audit,
        step=outcome.step,
        routed_model=outcome.routed_model,
        blocks=outcome.blocks,
        viability=outcome.viability,
        calls=outcome.calls,
        escalated=outcome.escalated,
    )

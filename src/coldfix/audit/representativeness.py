"""Whether the thing we made faster is a thing anybody runs.

Epic 9, S-9.7. *Assesses whether the workload resembles something users exercise.
Verdict `unrepresentative` skips to the next finding without repair spend.
**Limitation documented: the agent cannot know real traffic patterns.***

`08-audit.md` states the gap and its own fix in the same breath:

> We optimize what we can run. If the runnable workload is a test fixture that
> does not resemble production usage, we optimize the wrong thing with full
> confidence and complete evidence.
>
> **Fix:** this is the `unrepresentative` verdict in the finding audit. It is a
> **partial** fix — the agent still cannot know real traffic patterns.

So the third acceptance criterion is not a footnote on this story, it is most of
it. Everything here is arranged around one asymmetry.

**`unrepresentative` destroys a finding, so it defaults off.** The verdict skips
straight past repair, which means a wrong `unrepresentative` throws away a real
finding **silently** — nobody sees the thing that was not investigated. A wrong
`representative` spends repair effort on something that did not matter, which
somebody notices. The two errors are not symmetric, so the safe answer is the
default: a finding is representative unless there is a stated reason it is not,
and *absence of evidence* is not a reason. That is S-9.5's empty answer inverted —
there the safe default was *no alternative*, here it is *representative*.

**One fact is computable and is handed over rather than judged.** S-7.6 records
that synthesized data is uniform *by construction* — `Synthesis.blindness` says
so — so a workload seeded from schema is known not to resemble production
distributions, and that is a fact about the fixture rather than an opinion about
the endpoint. The auditor is given it; it does not have to infer it.

**What remains genuinely needs a model, and genuinely cannot be settled by one.**
Whether `shop/views.py::ListView.list_books` is something users exercise is a
judgement from a name and a description, with no traffic behind it. `RESIDUE`
carries that in the artifact, because a verdict that skips a finding without
repair spend is exactly the kind of claim somebody will quote without its bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from coldfix.audit.invocation import AuditError, invoke
from coldfix.cost.session import Session, StepOutcome
from coldfix.diagnosis.log import ExperimentLog
from coldfix.diagnosis.replies import read_object
from coldfix.llm.client import ModelClient
from coldfix.screening.workload import Workload

SYNTHESIS_MARKERS = ("synthesized from schema", "synthesis from schema")
"""How S-7.6 spells a fixture it invented. Matched rather than inferred, because
this is a fact the Explorer already recorded and re-deriving it would be a second
statement of one thing."""

RESIDUE = (
    "This verdict is a judgement from a name, a description and a fixture recipe. "
    "The agent has no traffic data and cannot know what users actually exercise, "
    "so `unrepresentative` means *this does not look like production usage to a "
    "reader of the code*, never *this is not exercised*. `08-audit.md` calls the "
    "whole check a partial fix, and it is: it can catch a workload that is "
    "obviously a test fixture, and it cannot catch one that merely is not typical."
)


class Representativeness(StrEnum):
    """Whether the workload looks like something users run."""

    REPRESENTATIVE = "resembles something users exercise, as far as anything here can tell"
    UNREPRESENTATIVE = "does not resemble production usage"


class RepresentativenessError(AuditError):
    """No usable assessment came back."""


QUESTION = """\
Does this workload resemble something a user of this software would actually \
exercise, or is it a test fixture, a health check, an admin page, or something \
else nobody runs in earnest?

Answer with a single JSON object and nothing else:

{"representative": true|false, "reason": "..."}

`reason` is required when you answer false, and must name what about this \
workload makes you think nobody exercises it.

**Answer true unless you have a positive reason not to.** A false answer skips \
this finding entirely and no repair is attempted, so a wrong one discards real \
work that nobody will see was discarded. Not being sure is not a reason."""


def synthesized(workload: Workload) -> bool:
    """Whether the fixture was invented rather than discovered.

    A fact, not a judgement: S-7.6 records that synthesized data is uniform **by
    construction**, so it is known not to resemble a production distribution
    whatever the endpoint is.
    """
    source = workload.fixture.source.lower()
    return any(marker in source for marker in SYNTHESIS_MARKERS)


@dataclass(frozen=True)
class RepresentativenessAudit:
    """What the auditor made of the workload, and what nobody can make of it."""

    verdict: Representativeness
    reason: str
    synthesized_fixture: bool

    @property
    def skips_repair(self) -> bool:
        """AC 2. The only verdict that spends no repair effort."""
        return self.verdict is Representativeness.UNREPRESENTATIVE

    def describe(self) -> str:
        lines = [f"Representativeness: {self.verdict.value}."]
        if self.reason:
            lines.append(f"  Because: {self.reason}")
        if self.synthesized_fixture:
            lines.append(
                "  The fixture was synthesized from the schema, so its distribution is "
                "uniform by construction and resembles no real dataset. That is a fact "
                "about the data, separate from the judgement above."
            )
        if self.skips_repair:
            lines.append(
                "  This finding will be skipped and no repair attempted. Nobody sees a "
                "finding that was not investigated, so overturn this if the reason above "
                "does not convince you."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def parse(text: str, *, synthesized_fixture: bool) -> RepresentativenessAudit:
    """Read an assessment, defaulting to representative.

    Raises:
        RepresentativenessError: the reply is unusable, or calls the workload
            unrepresentative without saying why. A verdict that discards a
            finding has to carry its reason — `08-audit.md` calls this a partial
            fix, and a partial fix applied without an argument is a coin toss
            with a finding on it.
    """
    read = read_object(text)
    if read.value is None:
        raise RepresentativenessError(read.rejection)
    payload = read.value

    verdict_field = payload.get("representative")
    if not isinstance(verdict_field, bool):
        message = (
            "the reply does not answer true or false. **Not being sure defaults to "
            "representative**, and saying so explicitly is how that default stays visible "
            "rather than being reached by a parser giving up"
        )
        raise RepresentativenessError(message)

    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        message = f"`reason` must be text, got {type(reason).__name__}"
        raise RepresentativenessError(message)

    if verdict_field:
        return RepresentativenessAudit(
            verdict=Representativeness.REPRESENTATIVE,
            reason=reason.strip(),
            synthesized_fixture=synthesized_fixture,
        )

    if not reason.strip():
        message = (
            "this workload was called unrepresentative with no reason given. That verdict "
            "skips the finding and attempts no repair, so a wrong one discards real work "
            "nobody will see was discarded — it has to name what makes this look like "
            "something nobody runs"
        )
        raise RepresentativenessError(message)

    return RepresentativenessAudit(
        verdict=Representativeness.UNREPRESENTATIVE,
        reason=reason.strip(),
        synthesized_fixture=synthesized_fixture,
    )


def assess(  # noqa: PLR0913 - the workload, the log and the two measured token
    # counts are four different facts, plus the session and the client; they are
    # S-9.1's parameters passed through.
    session: Session,
    client: ModelClient,
    *,
    workload: Workload,
    log: ExperimentLog,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[RepresentativenessAudit]:
    """Ask whether this workload is one anybody runs.

    The workload's own description and entry point are put to the auditor along
    with S-9.1's evidence, because *is this exercised* is a question about the
    subject rather than about the measurements — and unlike every other attack in
    this epic, the measurements cannot answer it.

    Raises:
        RepresentativenessError: no usable assessment came back.
        AuditError: the session belongs to another agent, or the model declined.
    """
    question = (
        f"WORKLOAD\n  {workload.id}: {workload.description}\n"
        f"  entry point: {workload.entry_point}\n"
        f"  fixture: {workload.fixture.entity} from {workload.fixture.source}\n\n"
        f"{QUESTION}"
    )
    outcome = invoke(
        session,
        client,
        log=log,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )
    audit = parse(outcome.value, synthesized_fixture=synthesized(workload))
    return StepOutcome(
        value=audit,
        step=outcome.step,
        routed_model=outcome.routed_model,
        blocks=outcome.blocks,
        viability=outcome.viability,
        calls=outcome.calls,
        escalated=outcome.escalated,
    )

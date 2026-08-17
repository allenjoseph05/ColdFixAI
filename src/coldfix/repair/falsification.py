"""The test that has to fail before anything is allowed to change.

Epic 10, S-10.1. *First output is a test, not a patch. The test asserts both cost
improvement and correctness preservation. It enumerates the cheat classes it is
designed to catch.*

`03-agents.md` §5.3 states the ordering and §5.4 the schema. Three things about
them decided this module.

**AC 1 is enforced by absence, not by ordering.** `generate` returns a
`FalsificationTest`, and there is no `diff` field, no `patch` field and no
parameter through which either could arrive. A caller holding a diff cannot pass
one, because there is nowhere to put it — the construction S-8.1 used for
`validate` and S-9.1 for `chain`. *First* is a claim about sequence, and a
sequence enforced by convention is one an agent can reorder; a type that cannot
express a patch cannot emit one.

**§5.4's `failed_on_unpatched: bool` is not a field here, and that is a
correction.** The schema in `03-agents.md` has the agent stating the result of a
run it did not perform, which `CLAUDE.md` forbids in as many words: *do not let
an agent report a measurement.* S-4.1 hit this exactly — `work_verified` is a
property with no field — and S-7.9 recorded what happens when such a value has to
survive serialization. Whether the test failed on unpatched code is something the
harness observes, and S-10.2 owns both the run and the gate.

**A cost claim with no guard counter is refused.** `CLAUDE.md`: *guard counters on
every metric — queries down while rows explode is not an improvement.* This is
the first artifact in the system where that invariant becomes checkable before
anything runs, because it is the first that states what *would* count as
improvement. A falsification test naming only the metric it wants to move is a
test that a cheat passes by moving it.

**The claimed baselines are checked against the evidence chain.** The Surgeon
writes the test from the chain, and a threshold quoted from a number nobody
measured is the first non-negotiable broken at the top of the repair phase — the
same discipline S-8.3 applies to a verdict and S-9.5 to an alternative. The
judgement of *what* to assert is the model's; the figures it rests on are not.

**Unlike hypothesis generation, this step may cascade.** `04-cost.md` §3 gives
`FALSIFICATION_TEST` a real check — *fails on unpatched code* — so a cheap
model's answer can be falsified deterministically, which is what S-5.6 requires.
The validator that performs it is S-10.2's, so `generate` takes one rather than
building one: this story cannot run a test. S-8.1's `generate` has **no**
`validate` parameter and asserts it by inspection; this one has it, and the
asymmetry is the table's rather than a preference.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coldfix.audit.alternatives import measured_pairs
from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.diagnosis.log import Experiment
from coldfix.diagnosis.replies import read_object
from coldfix.llm.client import ModelClient

SURGEON_TEMPERATURE = 0.2
"""`03-agents.md` §5.1: 0.2 on the first attempt, 0.6 on retries. S-10.5 owns the
raise — its argument is that a retry at 0.2 produces a variation of the same idea,
which fails the same way."""

MAX_OUTPUT_TOKENS = 4_000
"""Larger than the audit's 2,000 because the reply carries a script."""

CITATION_TOLERANCE = 1e-9
"""A quoted figure is compared against what the harness recorded, and the two
travel through JSON. Exact equality on floats that round-tripped through text is
a test of the serializer rather than of the citation."""


class FalsificationError(Exception):
    """No usable falsification test came back."""


class Cheat(StrEnum):
    """The five ways an improvement can be unreal.

    `02-architecture.md` §210, `03-agents.md` §412 and S-11.3's acceptance
    criteria list the same five, so this is a transcription rather than a
    judgement. An enum instead of §5.4's `list[str]` because AC 3 says the test
    *enumerates* the classes it catches, and a free string cannot be enumerated —
    S-11.3 has to ask *could a cheat of class X pass this test* and needs the same
    vocabulary to ask it in.
    """

    CACHED_STATE = "state cached across runs, so the second run is not doing the work"
    DEFERRED_WORK = "work moved out of the measured window rather than removed"
    OVER_FETCH = "fewer calls, each returning more than is needed"
    STUBBED_RESPONSE = "the response no longer contains what it used to"
    SHAPE_SPECIFIC = "a special case for the fixture's shape rather than a general fix"


class Guard(BaseModel):
    """A metric that must not get worse while the cost metric gets better.

    The non-negotiable in a type. *Queries down while rows explode is not an
    improvement*, and the only way a test can catch that is to have been told
    what *rows* was before.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str = Field(min_length=1)
    baseline: float
    at_most: float
    """What the patched run may not exceed. Above `baseline` where a small
    regression is tolerable, equal to it where none is."""

    @model_validator(mode="after")
    def _guards_something(self) -> Self:
        if self.at_most < self.baseline:
            message = (
                f"guard {self.metric!r} allows at most {self.at_most} against a baseline of "
                f"{self.baseline}, which demands an improvement rather than guarding one. A guard "
                "counter exists to catch what got worse; requiring it to get better makes the "
                "patch fail for succeeding somewhere nobody claimed"
            )
            raise ValueError(message)
        return self

    def describe(self) -> str:
        headroom = self.at_most - self.baseline
        allowance = "no regression at all" if headroom == 0 else f"at most +{headroom:g}"
        return f"{self.metric} was {self.baseline:g} and must stay ≤ {self.at_most:g} ({allowance})"


class CostClaim(BaseModel):
    """What must get better, by how much, and what must not get worse to pay for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str = Field(min_length=1)
    baseline: float
    at_most: float
    """The figure the patched run must come in under. Strictly below the
    baseline — see `_is_an_improvement`."""

    guards: tuple[Guard, ...] = Field(min_length=1)
    """**Required, and non-empty is the whole point.** A cost claim with no guard
    is a test a cheat passes by moving one number, which is `CLAUDE.md`'s
    *queries down while rows explode* stated as a schema."""

    @model_validator(mode="after")
    def _is_an_improvement(self) -> Self:
        if self.at_most >= self.baseline:
            message = (
                f"this claims {self.metric!r} must come in at {self.at_most} against a baseline "
                f"of {self.baseline}, which any unchanged run satisfies. A falsification test that "
                "the original code passes is testing nothing (§5.3), and a threshold at or above "
                "the baseline is that test written down"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _guards_are_not_the_claim(self) -> Self:
        clashing = sorted({guard.metric for guard in self.guards if guard.metric == self.metric})
        if clashing:
            message = (
                f"{clashing} is both the metric this patch must improve and a guard on itself. A "
                "guard exists to catch what was traded away to move the cost metric, and one "
                "pointed at the cost metric catches nothing"
            )
            raise ValueError(message)
        return self

    def describe(self) -> str:
        lines = [
            f"{self.metric} was {self.baseline:g} and must come in below {self.at_most:g}.",
            "  Guarded by:",
        ]
        lines.extend(f"    - {guard.describe()}" for guard in self.guards)
        return "\n".join(lines)


class FalsificationTest(BaseModel):
    """§5.4's artifact, minus the field that would be a self-reported measurement.

    **There is no `diff` and no `patch`.** AC 1 says the first output is a test,
    and this type cannot express the other thing.

    **There is no `failed_on_unpatched`.** §5.4 has one; it would be the agent
    stating the outcome of a run it did not perform. S-10.2 runs the test against
    unpatched code and owns the gate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str = Field(min_length=1)
    """What this test would prove, in one sentence, for a human reading the PR."""

    script: str = Field(min_length=1)
    cost: CostClaim
    equivalence: str = Field(min_length=1)
    """What must still be true of the output afterwards. AC 2's second half, and a
    sentence rather than a schema because *the same books in the same order* is
    not something this system can type — S-11.1 owns output comparison."""

    catches: tuple[Cheat, ...] = Field(min_length=1)
    """AC 3. Non-empty, because a test designed to catch nothing is one nobody can
    argue with — and S-10.3 asks the Adversary *could a cheat pass this test*,
    which needs a stated answer to disagree with."""

    @field_validator("claim", "script", "equivalence")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        """`min_length=1` is satisfied by a space, and a space is not a claim.

        Found by a test written for the criterion rather than for the field: AC 2
        requires the test to *assert* correctness preservation, and `equivalence`
        of `"  "` satisfies every schema check while asserting nothing. The same
        hole was open on `claim` and `script`, where a blank script would have
        reached S-10.2's runner.
        """
        if not value.strip():
            message = (
                "this field is blank. `min_length` counts characters and a space is a "
                "character, so a whitespace-only claim, script or equivalence statement "
                "satisfies the schema while saying nothing"
            )
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _catches_each_class_once(self) -> Self:
        seen = list(self.catches)
        repeated = sorted({item.name for item in seen if seen.count(item) > 1})
        if repeated:
            message = (
                f"these cheat classes are listed more than once: {repeated}. A list with "
                "duplicates reads as a broader claim than it is"
            )
            raise ValueError(message)
        return self

    @property
    def guarded_metrics(self) -> tuple[str, ...]:
        return tuple(guard.metric for guard in self.cost.guards)

    def describe(self) -> str:
        lines = [
            f"FALSIFICATION TEST — {self.claim}",
            f"  Cost: {self.cost.describe()}",
            f"  Equivalence: {self.equivalence}",
            "  Designed to catch:",
        ]
        lines.extend(f"    - {item.name.lower()}: {item.value}" for item in self.catches)
        lines.append(
            "  This test has not been run. Whether it fails on unpatched code — which is "
            "the only thing that makes it worth anything — is S-10.2's gate."
        )
        return "\n".join(lines)


def chain_experiments(chain: EvidenceChain) -> tuple[Experiment, ...]:
    """Every experiment the chain rests on, confirmations and exclusions alike.

    Both, because a cost baseline legitimately comes from either: the ablation
    that localized the cause measured it, and so did the sweep that ruled the
    database out. A checker reading only the confirmations would call a figure
    quoted from an exclusion a fabrication.
    """
    return (
        *(link.experiment for link in chain.localization),
        *(item.experiment for item in chain.exclusions),
    )


def check_baselines(test: FalsificationTest, chain: EvidenceChain) -> str | None:
    """Whether every baseline quoted is a number the harness recorded.

    Returns the objection, or `None` if every figure checks out. Deliberately a
    return rather than a raise: `generate` turns it into the correction the model
    is re-asked with, and S-8.2 established that a rejection worth feeding back is
    one carrying its reason.
    """
    recorded = measured_pairs(chain_experiments(chain))
    quoted: list[tuple[str, float]] = [(test.cost.metric, test.cost.baseline)]
    quoted.extend((guard.metric, guard.baseline) for guard in test.cost.guards)

    problems: list[str] = []
    for name, value in quoted:
        if name not in recorded:
            known = ", ".join(sorted(recorded)) or "nothing"
            problems.append(f"{name!r} was never measured; this chain records {known}")
            continue
        if not any(abs(value - seen) <= CITATION_TOLERANCE for seen in recorded[name]):
            seen = ", ".join(f"{item:g}" for item in sorted(recorded[name]))
            problems.append(f"{name} was measured at {seen}, and this test quotes {value:g}")

    if not problems:
        return None
    return (
        "a falsification test rests on figures the harness recorded, and these do not match: "
        + "; ".join(problems)
    )


_SYSTEM = """\
You are writing the test that will decide whether a proposed performance fix is \
real. You are not writing the fix.

You are given an evidence chain: what was measured, where the cost is, and what \
was ruled out. From it, write a test that FAILS on the code as it stands today \
and would PASS only if the cost were genuinely removed.

A test that the current code already passes is testing nothing.

Your test must assert two things at once — that the cost went down, and that the \
behaviour did not change — because a patch that deletes the work passes the first \
on its own.

Name the guard counters. A query count that falls while the row count explodes is \
not an improvement, and a test that watches only the number you want to move is a \
test that rewards making it move by any means."""

QUESTION = """\
Write the falsification test for this finding.

Answer with a single JSON object and nothing else:

{"claim": "...", "script": "...", "equivalence": "...",
 "cost": {"metric": "...", "baseline": number, "at_most": number,
          "guards": [{"metric": "...", "baseline": number, "at_most": number}]},
 "catches": ["cached_state", "deferred_work", ...]}

`baseline` figures must be copied from the measurements above — do not estimate \
them. `at_most` for the cost metric must be below its baseline; `at_most` for a \
guard is what that metric may reach without the improvement being a trade.

`catches` names which of these the test is built to detect:
  cached_state, deferred_work, over_fetch, stubbed_response, shape_specific

`script` is the test itself. It has no access to the patch and must not import \
one; it drives the workload and reads the counters, exactly as it would before \
and after."""


def parse(text: str, chain: EvidenceChain) -> FalsificationTest:
    """Read a falsification test, checking its figures against the chain.

    Raises:
        FalsificationError: the reply is unusable, or it quotes a figure the
            harness never recorded.
    """
    read = read_object(text)
    if read.value is None:
        raise FalsificationError(read.rejection)

    try:
        test = FalsificationTest.model_validate(_with_cheats(read.value))
    except ValueError as error:
        message = f"this is not a usable falsification test: {error}"
        raise FalsificationError(message) from error

    objection = check_baselines(test, chain)
    if objection is not None:
        raise FalsificationError(objection)
    return test


def _with_cheats(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Turn `catches` into `Cheat` members, refusing a name nobody defined.

    Refused rather than dropped: a test claiming to catch `"probably_fine"` has
    named a class S-11.3 cannot attack, and silently discarding it would leave a
    shorter list reading as a complete one.
    """
    raw = payload.get("catches")
    if not isinstance(raw, list):
        return payload

    known = {item.name.lower(): item for item in Cheat}
    catches: list[Cheat] = []
    for item in raw:
        if isinstance(item, str) and item.strip().lower() in known:
            catches.append(known[item.strip().lower()])
            continue
        named = ", ".join(sorted(known))
        message = (
            f"{item!r} is not one of the cheat classes this system knows how to attack. "
            f"Use one of: {named}. A class nobody defined is one S-11.3 cannot ask about"
        )
        raise FalsificationError(message)
    return {**payload, "catches": catches}


def render_chain(chain: EvidenceChain) -> str:
    """What the Surgeon is shown: the chain, and the numbers under it.

    The chain's own `render` is the report a human reads. This adds the measured
    pairs explicitly, because every baseline the reply quotes is checked against
    them and a model asked to copy figures it was never shown will estimate.
    """
    recorded = measured_pairs(chain_experiments(chain))
    lines = [chain.render(), "", "MEASURED — copy baselines from here, do not estimate:"]
    lines.extend(
        f"  {name} = {', '.join(f'{value:g}' for value in sorted(recorded[name]))}"
        for name in sorted(recorded)
    )
    return "\n".join(lines)


def generate(  # noqa: PLR0913 - the chain and the two measured token counts are
    # three different facts, plus the session, the client and S-10.2's validator.
    # There is deliberately no parameter for a diff — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    chain: EvidenceChain,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    validate: Callable[[FalsificationTest], bool] | None = None,
    finding_id: str | None = None,
) -> StepOutcome[FalsificationTest]:
    """Write the test that must fail before a patch may be written. AC 1 to 3.

    **There is no `diff` parameter and no way to return one.** AC 1 is a claim
    about what comes first, and the enforcement is that the other thing cannot be
    expressed here.

    `validate` opts this step into S-5.6's cascade, which `04-cost.md` §3 permits
    because `FALSIFICATION_TEST` has a real check — *fails on unpatched code*.
    **This module cannot perform it**: it has no runner, no worktree and no way to
    execute a script, so the callable comes from S-10.2 or nowhere. Left `None`,
    the step runs once on the routed tier.

    Raises:
        FalsificationError: no usable test came back, or its figures do not
            match what the chain recorded.
        BudgetExhaustedError: the repair phase's attempts are spent.
    """
    step = Step(
        step_type=StepType.FALSIFICATION_TEST,
        phase=Phase.REPAIR,
        agent=Agent.SURGEON,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )
    question = f"{render_chain(chain)}\n\n{QUESTION}"

    def call(model: str) -> tuple[FalsificationTest, TokenUsage]:
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=[{"role": "user", "content": question}],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=SURGEON_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the Surgeon declined to write a falsification test. A refusal is a successful "
                "response with an empty content list, so it is reported rather than read as a "
                "test that found nothing to assert"
            )
            raise FalsificationError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated script is one "
                "whose assertions may be missing, and running it would check whatever happened "
                "to fit"
            )
            raise FalsificationError(message)
        return parse(reply.text, chain), reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
        validate=validate,
    )


def catalogue() -> Sequence[tuple[str, str]]:
    """Every cheat class and what it means, for a prompt or a report.

    Exists so the five are enumerable rather than something a reader has to
    notice, which is `dispositions()`'s argument in S-5.4 one epic over.
    """
    return [(item.name.lower(), item.value) for item in Cheat]

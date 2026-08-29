"""Attacking the test before the patch it will judge exists.

Epic 10, S-10.3. *The Adversary reviews the falsification test **before** patch
generation. Question asked: could a cheat pass this test? If yes, the Adversary
supplies a strengthened test and the Surgeon must satisfy it. Costs under 5
calls.*

`08-audit.md` §3.3 names the flaw and its fix in two sentences:

> **The test is written by the agent that then writes the patch.** The Surgeon
> can write a weak test to make its own life easier. We noted the Adversary
> audits the test, but only *after* the patch exists — by then the weak test has
> already shaped the patch.
>
> **Fix:** the falsification test is submitted and audited **before** the patch
> is written.

**There is nothing to withhold, and that is S-10.1 paying off.** S-9.1 had to
strip `rationale` and `outcome` from the experiment log because the Diagnostician
had written justifying prose into it. `FalsificationTest` has no rationale field
— S-10.1 refused one — so the artifact can be handed over whole. `claim` stays
for the reason S-9.1 kept `verdict`: an auditor asked *could a cheat pass this
test* has to know what the test is claiming, and the opposite failure — isolation
by sending nothing — makes the audit useless while satisfying every rule.

**A strengthened test is not trusted, it is re-gated.** `strengthen` returns a
`FalsificationTest`, never a `Falsified`. Only S-10.2 can produce the second, so
a strengthened test has to go back through the must-fail gate before a patch may
be written — and it must, because a *stronger* test that the unpatched code
already passes is exactly as useless as a weak one. The type system carries that
requirement rather than a comment asking somebody to remember it.

**The strengthened test is checked for being stronger.** An adversary that
"strengthens" by loosening the threshold, dropping a guard or claiming fewer
cheat classes has handed back a weaker test wearing the word. Three refusals:
the cost threshold may not rise, the guarded metrics may not shrink, and
**`catches` must include every class the auditor just said could slip through** —
otherwise it names a hole and returns a test that does not claim to close it.

**The empty answer is a first-class result, and it is spelled one way.** S-9.5's
argument applies unchanged — an attack that always finds something is worthless,
and one that cannot say *I have nothing* would send every falsification test back
to be rewritten for ever. But there the empty answer is a **string field**
(`{"mechanism": "none"}`) and here it is an empty **list**, so a second accepted
spelling was written, tested by nothing and asked for by no prompt. A sabotage
deleted it and changed no outcome; it is gone. `weaknesses: []` is the contract.

**An omitted `weaknesses` field is refused, not read as empty.** That was a real
defect: a reply which never addressed the question would have passed a weak test
on silence, which is S-9.7's rule — the safe answer has to be reached
deliberately — in the place where it costs most.

**`Phase.TEST_AUDIT`'s two-round cap has had no caller since S-5.4**, the same
way `FINDING_AUDIT`'s had none until S-9.8. Whoever owns the unit counts the
unit, and a round of this phase ends here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    AuditError,
    audit_messages,
    refuse_shared_session,
)
from coldfix.cost.accounting import Agent, ModelCall, Phase, TokenUsage
from coldfix.cost.budget import Budget
from coldfix.cost.context import Block
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.diagnosis.replies import read_object
from coldfix.llm.client import ModelClient
from coldfix.repair.falsification import (
    Cheat,
    FalsificationError,
    FalsificationTest,
    catalogue,
)
from coldfix.repair.falsification import parse as parse_test

MAX_OUTPUT_TOKENS = 4_000
"""The reply may carry a whole replacement script, so it is the Surgeon's budget
rather than the finding auditor's 2,000."""

TEST_AUDIT_CALL_CEILING = 5
"""AC 4, read strictly: *under* 5 calls, so five is already too many.

The design makes **one**. The ceiling is checked anyway for S-9.8's reason: the
figure is a property of how many rounds this takes, and a later story that added
a second round would move it without anything noticing."""


class TestAuditError(AuditError):
    """No usable test audit came back."""

    __test__ = False
    """Not a pytest suite. The name begins with `Test` because the *subject* is a
    test, and pytest collects on the prefix alone — a class it could instantiate
    would be run as a suite and silently report passes."""


SYSTEM = """\
You are attacking a test, not writing one and not fixing any code.

Somebody is about to change a program to make it faster. Before they do, they \
wrote the test that will decide whether their change was real. They wrote it \
knowing they would then have to satisfy it.

Your question is the only one that matters here: **could a change that does not \
genuinely fix anything still pass this test?**

Those changes have names, and you are given the list. A test that watches one \
number can be satisfied by moving that number. A test that measures inside one \
process can be satisfied by caching between runs. A test that checks a count can \
be satisfied by fetching more per call.

If a cheat would pass, you must also write the test that would catch it. \
Objecting without that is asking somebody else to solve the problem you found.

If nothing you can think of would pass, say so plainly. That is a result."""


@dataclass(frozen=True)
class Weakness:
    """One cheat that would survive the test, and how.

    `how` is required for S-9.5's reason: *there might be a way round this* with
    no account of what the way is cannot be acted on, and cannot be checked
    against the strengthened test either.
    """

    cheat: Cheat
    how: str

    def describe(self) -> str:
        return f"{self.cheat.name.lower()}: {self.how}\n    ({self.cheat.value})"


@dataclass(frozen=True)
class TestAudit:
    """What the Adversary made of the Surgeon's test. AC 2 and AC 3."""

    __test__ = False
    """See `TestAuditError`. Declared rather than left to the constructor warning,
    because the warning is what happens today and collection is what happens the
    day somebody gives this a no-argument constructor."""

    original: FalsificationTest
    weaknesses: tuple[Weakness, ...]
    strengthened: FalsificationTest | None

    @property
    def sound(self) -> bool:
        """Whether no cheat the auditor could name would pass."""
        return not self.weaknesses

    @property
    def forward(self) -> FalsificationTest:
        """**The test the Surgeon must satisfy.** AC 3, as one accessor.

        The strengthened one where there is one, so a caller cannot accidentally
        carry the weak test forward by reading the wrong field — the mistake this
        story exists to prevent, made one layer up.
        """
        return self.strengthened if self.strengthened is not None else self.original

    def describe(self) -> str:
        if self.sound:
            return (
                "TEST AUDIT PASSED — no cheat the auditor could name would survive this "
                "test. That is this attack finding nothing, not this attack failing to run."
            )
        lines = ["TEST AUDIT — a cheat would pass the test as written:"]
        lines.extend(f"  - {item.describe()}" for item in self.weaknesses)
        lines.append(
            "  The strengthened test below replaces it, and must itself fail on unpatched "
            "code before any patch is written (S-10.2)."
        )
        lines.append(f"  {self.forward.describe()}")
        return "\n".join(lines)


def render_test(test: FalsificationTest) -> str:
    """The test, whole. Nothing is withheld and nothing needs to be.

    S-9.1 strips two fields from the experiment log because the Diagnostician
    wrote justifying prose into it. S-10.1 gave `FalsificationTest` no rationale
    field at all, so there is no Surgeon reasoning here to remove — the artifact
    was designed to be handed to an adversary.
    """
    catches = "\n".join(f"    - {item.name.lower()}: {item.value}" for item in test.catches)
    guards = "\n".join(f"    - {guard.describe()}" for guard in test.cost.guards)
    return (
        "THE TEST UNDER ATTACK\n"
        f"  claims: {test.claim}\n"
        f"  cost: {test.cost.metric} was {test.cost.baseline:g} and must come in "
        f"below {test.cost.at_most:g}\n"
        f"  guards:\n{guards}\n"
        f"  behaviour that must be preserved: {test.equivalence}\n"
        f"  it says it catches:\n{catches}\n"
        f"  script:\n    {test.script}"
    )


QUESTION = f"""\
Could a change that does not genuinely fix anything still pass this test?

Answer with a single JSON object and nothing else:

{{"weaknesses": [{{"cheat": "...", "how": "..."}}],
 "strengthened": {{"claim": "...", "script": "...", "equivalence": "...",
   "cost": {{"metric": "...", "baseline": number, "at_most": number,
             "guards": [{{"metric": "...", "baseline": number, "at_most": number}}]}},
   "catches": ["...", "..."]}}}}

`cheat` is one of: {", ".join(name for name, _ in catalogue())}
`how` says concretely what such a change would do and why this test would not \
notice. An objection with no account of how is not one anybody can act on.

`strengthened` is required whenever `weaknesses` is non-empty, and must be a \
complete replacement test that would catch every cheat you named. Its `catches` \
must include them. Its baselines must be the same measured figures, and its cost \
threshold may not be higher than the original's.

If no cheat you can think of would pass, answer exactly \
{{"weaknesses": []}} — that is a result, not a failure."""


def check_stronger(
    original: FalsificationTest,
    strengthened: FalsificationTest,
    weaknesses: Sequence[Weakness],
) -> str | None:
    """Whether the replacement is actually stronger. Returns the objection or `None`.

    **The three ways a "strengthened" test can be weaker**, each of which reads
    as an improvement to somebody skimming:

    1. the cost threshold rises, so more changes satisfy it;
    2. a guard disappears, so a trade stops being caught;
    3. it does not claim to catch what the auditor just said would slip through.

    The third is the one worth the most: an auditor that names a hole and hands
    back a test which does not claim to close it has produced a round of work and
    no coverage, and the Surgeon would satisfy it while the hole stayed open.
    """
    problems: list[str] = []

    if strengthened.cost.at_most > original.cost.at_most:
        problems.append(
            f"its cost threshold is {strengthened.cost.at_most:g} against the original's "
            f"{original.cost.at_most:g}, so more changes satisfy it than before"
        )

    dropped = sorted(set(original.guarded_metrics) - set(strengthened.guarded_metrics))
    if dropped:
        problems.append(
            f"it drops the guard(s) on {dropped}, so a trade the original would have "
            "caught now passes"
        )

    named = {item.cheat for item in weaknesses}
    uncovered = sorted(item.name.lower() for item in named - set(strengthened.catches))
    if uncovered:
        problems.append(
            f"it does not claim to catch {uncovered}, which is what this audit just said "
            "would slip through — the objection and the replacement disagree"
        )

    if not problems:
        return None
    return "this replacement is not stronger than what it replaces: " + "; ".join(problems)


def parse(text: str, original: FalsificationTest, chain: EvidenceChain) -> TestAudit:
    """Read the audit, and refuse a replacement that is not an improvement.

    Raises:
        TestAuditError: the reply is unusable, names a cheat class nobody
            defined, objects without a replacement, or replaces the test with a
            weaker one.
    """
    read = read_object(text)
    if read.value is None:
        raise TestAuditError(read.rejection)
    payload = read.value

    weaknesses = _weaknesses(payload)
    if not weaknesses:
        return TestAudit(original=original, weaknesses=(), strengthened=None)

    raw = payload.get("strengthened")
    if not isinstance(raw, Mapping):
        named = ", ".join(item.cheat.name.lower() for item in weaknesses)
        message = (
            f"the audit says a cheat would pass ({named}) and supplies no replacement test. "
            "`03-agents.md` §6.3 asks *would a cheat pass the Surgeon's own test — if so, "
            "write the test that wouldn't*: objecting without that is asking somebody else "
            "to solve the problem you found"
        )
        raise TestAuditError(message)

    try:
        strengthened = parse_test(_as_json(raw), chain)
    except FalsificationError as error:
        message = f"the replacement test is not usable: {error}"
        raise TestAuditError(message) from error

    objection = check_stronger(original, strengthened, weaknesses)
    if objection is not None:
        raise TestAuditError(objection)

    return TestAudit(original=original, weaknesses=weaknesses, strengthened=strengthened)


def _as_json(payload: Mapping[str, object]) -> str:
    """Hand the nested object back to S-10.1's parser, which owns the schema.

    Re-serialized rather than validated here, because every rule about what makes
    a falsification test usable — the guard requirement, the improvement
    threshold, the citation check — is S-10.1's, and a second implementation
    would be a second answer to the same question.
    """
    return json.dumps(payload)


def _weaknesses(payload: Mapping[str, object]) -> tuple[Weakness, ...]:
    raw = payload.get("weaknesses")
    if raw is None:
        # **An omitted field is not an answer, and defaulting it to empty was a
        # real defect a sabotage found.** `weaknesses: []` is the auditor saying
        # *I looked and found nothing*; a missing key is a reply that did not
        # address the question, and reading the second as the first lets a weak
        # test through on silence. S-9.7's rule, in the place where it costs
        # most: the safe answer has to be reached deliberately or nobody can
        # tell a considered *no* from a shrug.
        message = (
            "the reply does not say whether a cheat would pass. An empty `weaknesses` list is "
            "the way to answer *none*, and it is a result; a missing field is a reply that "
            "never addressed the question, and treating it as *none* would pass a weak test "
            "on silence"
        )
        raise TestAuditError(message)
    if not isinstance(raw, list):
        message = f"`weaknesses` must be a list, got {type(raw).__name__}"
        raise TestAuditError(message)

    known = {item.name.lower(): item for item in Cheat}
    found: list[Weakness] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            message = f"each weakness is an object with `cheat` and `how`, got {entry!r}"
            raise TestAuditError(message)

        name = entry.get("cheat")
        if not isinstance(name, str) or name.strip().lower() not in known:
            message = (
                f"{name!r} is not one of the cheat classes this system knows how to attack. "
                f"Use one of: {', '.join(sorted(known))}"
            )
            raise TestAuditError(message)

        how = entry.get("how")
        if not isinstance(how, str) or not how.strip():
            message = (
                f"the audit says a {name} cheat would pass and does not say how. An objection "
                "with no account of how cannot be acted on, and cannot be checked against the "
                "replacement either"
            )
            raise TestAuditError(message)

        found.append(Weakness(cheat=known[name.strip().lower()], how=how.strip()))
    return tuple(found)


def audit_test(  # noqa: PLR0913 - the test, the chain and the two measured token
    # counts are four different facts, plus the session and the client. There is
    # deliberately no parameter for a patch or a diff — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    test: FalsificationTest,
    chain: EvidenceChain,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[TestAudit]:
    """Ask whether a cheat could pass this test, before any patch exists. AC 1 to 3.

    **AC 1 is enforced by absence.** There is no `patch` parameter and no `diff`
    parameter, so this cannot be called with one — which is what *before patch
    generation* means when the ordering cannot be trusted to a caller.

    The session must be the test auditor's own: `refuse_shared_session` is
    checked before any spend, because a session carrying the Surgeon's prompt as
    its cached prefix would hand this auditor the Surgeon's framing while every
    message list stayed clean.

    Raises:
        TestAuditError: no usable audit came back, or the replacement is weaker.
        AuditError: the session belongs to another agent, or the model declined.
        BudgetExhaustedError: this finding's test-audit rounds are spent.
    """
    refuse_shared_session(session, expected=SYSTEM)

    evidence = render_test(test)
    step = Step(
        step_type=StepType.ATTACK_DESIGN,
        phase=Phase.TEST_AUDIT,
        agent=Agent.ADVERSARY,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str, blocks: Sequence[Block]) -> tuple[TestAudit, TokenUsage]:
        del blocks  # the Adversary builds its own list — see `audit_messages`
        reply = client.complete(
            model=model,
            system=SYSTEM,
            messages=audit_messages(evidence, QUESTION),
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=AUDIT_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the test auditor declined to answer. A refusal is a successful response with "
                "an empty content list, so it is reported rather than read as an audit that "
                "found no cheat — which is the reading that would let a decline pass as a pass"
            )
            raise TestAuditError(message)
        if reply.truncated:
            message = (
                f"the audit was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated replacement "
                "test is one whose assertions may be missing, and accepting it would carry a "
                "half-written test into the gate"
            )
            raise TestAuditError(message)
        return parse(reply.text, test, chain), reply.usage

    return session.run(
        step,
        question=QUESTION,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )


def authorize_round(budget: Budget, finding_id: str | None = None) -> None:
    """Refuse a third test-audit round before it spends anything.

    `Phase.TEST_AUDIT`'s cap has had **no caller since S-5.4** — the same way
    `FINDING_AUDIT`'s had none until S-9.8 counted it. Whoever owns the unit
    counts the unit.

    Raises:
        BudgetExhaustedError: both rounds are spent, with `ESCALATE` as §7.2's
            disposition for this phase.
    """
    budget.authorize(Phase.TEST_AUDIT, finding_id)


def record_round(budget: Budget, audit: TestAudit, finding_id: str | None = None) -> None:
    """Count one completed round, with the outcome as the stall conclusion."""
    conclusion = "sound" if audit.sound else "strengthened"
    budget.record_step(Phase.TEST_AUDIT, finding_id, conclusion=conclusion)


def refuse_overspend(calls: Sequence[ModelCall]) -> None:
    """AC 4. Refuse a test audit that has spent more calls than the ceiling allows.

    Raises:
        TestAuditError: the ceiling is reached.
    """
    if len(calls) >= TEST_AUDIT_CALL_CEILING:
        message = (
            f"this test audit has made {len(calls)} model calls against a ceiling of "
            f"{TEST_AUDIT_CALL_CEILING}. `08-audit.md` §3.3 justifies it as *a second cheap "
            "Adversary call*, and an audit that costs what the repair costs is not the cheap "
            "half of that trade"
        )
        raise TestAuditError(message)

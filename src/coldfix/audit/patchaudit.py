"""Handing a patch to something that did not write it.

Epic 11, S-11.1. *Receives original code, patched code, evidence chain,
falsification test. **Never** receives Surgeon reasoning, rationale, or prior
attempts. Isolation enforced by fresh message-list construction, not by prompt
instruction. Different model vendor where configured.*

This is S-9.1 run a second time — `08-audit.md` §4: *the Adversary runs twice* —
against a different subject, and the difference decides the design.

**S-9.1 had to strip; this one has nowhere to put it.** The experiment log's type
carries `rationale` and `outcome`, so the finding audit removes them while
rendering. `Patch` carries `rationale` and `approach`, and S-10.4 named the first
one for exactly this story. Rather than render-and-strip, the artifact handed
over is a **`Candidate`, which has no field for either** — so a caller holding a
`Patch` cannot pass its reasoning, because there is nowhere for it to go. That is
the construction S-10.1 used to keep a diff out of a falsification test, applied
to the thing this epic must not see.

**`approach` goes as well as `rationale`, and that is a decision.** §6.1 says
*never sees the Surgeon's reasoning, rationale, or prior attempts*, and
`approach` is not obviously reasoning — it is a label. But it is the Surgeon's
one-line account of what it believes it did, which is what `outcome` was to
S-9.1's log, and S-10.5 proved it is renameable without anything else changing.
The diff is the fact; the label is the gloss. `08-audit.md`'s number is the
argument: **72% of reward-hacking episodes carry explicit justifying reasoning**,
and a sentence saying *prefetch the authors once* over a diff that adds a cache
is that reasoning in miniature.

**The opposite failure is real and is not committed here.** S-9.1 recorded that
*isolation by sending nothing* satisfies every rule while making the audit
useless. The Adversary gets the whole diff, the original and patched source of
every file it touches, the evidence chain, and the falsification test it has to
decide the patch against. Everything it needs to attack; nothing about what the
Surgeon thought.

**Prior attempts have no parameter.** Not a rendering choice — `invoke` cannot be
called with them.

**Isolation is partial, again, and the residue says so.** `08-audit.md`: *the
evidence chain and the falsification test both encode the Diagnostician's and
Surgeon's framing of what matters. The Adversary inherits that frame even without
seeing their reasoning.* Both are required by AC 1, so this cannot be fixed here —
only stated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from coldfix.audit.invocation import (
    AUDIT_TEMPERATURE,
    AuditError,
    audit_messages,
    audit_session,
    refuse_shared_session,
)
from coldfix.cost.accounting import Agent, ExchangeRate, Phase, TokenUsage
from coldfix.cost.budget import Budget
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.llm.client import ModelClient
from coldfix.repair.falsification import FalsificationTest
from coldfix.repair.patch import Patch
from coldfix.sandbox.patching import touched_paths

MAX_OUTPUT_TOKENS = 4_000
"""Larger than the finding audit's 2,000 because an attack on a patch may carry a
reproducing input or a replacement script."""

WITHHELD = ("rationale", "approach")
"""The `Patch` fields a `Candidate` has no place for.

Named as data so a test can assert the list rather than assert against a
rendering — S-9.1's construction — and so that adding a third is a line here
rather than an edit inside a loop."""

RESIDUE = (
    "This isolation is partial, and in the same way S-9.1's is. It removes the "
    "Surgeon's rationale and its own label for what it did, which is the documented "
    "risk: 72% of reward-hacking episodes carry explicit justifying reasoning. It does "
    "not remove framing bias — `08-audit.md` says the evidence chain and the "
    "falsification test both encode the Diagnostician's and the Surgeon's sense of what "
    "matters, and AC 1 requires both to be sent. Do not describe this as clean "
    "separation."
)

SYSTEM = """\
You are trying to defeat a change somebody else made to a program, not to review it.

They were told a specific cost had been measured somewhere specific, and they \
changed the code to remove it. You are given the code before, the code after, \
the evidence the cost was real, and the test they had to make pass.

You are deliberately not given anything they wrote about their own change. What \
they believed they did is not evidence; the diff is.

Assume the change is wrong and look for the reason. An improvement that only \
holds for the data that happened to be there, work moved somewhere the test does \
not look, a response that no longer contains what it used to, a caller that now \
breaks — those are the shapes worth your attention.

Quote the lines you are reasoning about. An objection with no code under it is an \
opinion."""


class PatchAuditError(AuditError):
    """The patch audit could not be invoked in isolation."""


@dataclass(frozen=True)
class Candidate:
    """What the Adversary is given about the change. **AC 1 and AC 2 in a type.**

    There is no `rationale` field and no `approach` field. A caller holding a
    `Patch` cannot pass either, because there is nowhere for them to go — the
    enforcement S-10.1 used to keep a diff out of a falsification test, pointed
    at what this epic must not see.
    """

    diff: str
    original: Mapping[str, str]
    """Path to source, as it was. AC 1's *original code*."""

    patched: Mapping[str, str]
    """Path to source, as it is now. AC 1's *patched code*."""

    def __post_init__(self) -> None:
        if not self.diff.strip():
            message = (
                "a candidate with no diff is nothing to attack. An audit handed one would "
                "find no objection and report that as the patch surviving"
            )
            raise PatchAuditError(message)

    @property
    def files(self) -> frozenset[str]:
        """Derived from the diff, never reported — S-10.4's rule, one epic on."""
        return touched_paths(self.diff)

    @property
    def unreadable(self) -> tuple[str, ...]:
        """Files the diff touches whose source could not be supplied.

        Reported rather than passed over. S-3.9 reads source best-effort because a
        file it cannot see weakens a finding and an exception loses it — the same
        applies here, and an Adversary that cannot see a changed file should be
        told so rather than left to assume it saw everything.
        """
        return tuple(sorted(self.files - (self.original.keys() | self.patched.keys())))


def candidate_from(
    patch: Patch, *, original: Mapping[str, str], patched: Mapping[str, str]
) -> Candidate:
    """The boundary where the Surgeon's account of itself stops. AC 2.

    Takes the whole `Patch` and returns a type that cannot carry two of its
    fields. Written as a function rather than left to callers so there is one
    place where the reasoning is dropped, and so a test can assert that the thing
    handed over has no route for it.
    """
    return Candidate(diff=patch.diff, original=dict(original), patched=dict(patched))


def render_candidate(candidate: Candidate, chain: EvidenceChain, test: FalsificationTest) -> str:
    """Everything AC 1 requires, and nothing AC 2 forbids.

    The falsification test is rendered from its own fields rather than from
    S-10.3's audit report, because that report carries the *auditor's* argument
    about the test — a different agent's framing, and not one AC 1 asks for.
    """
    lines = [
        "THE CHANGE UNDER ATTACK",
        candidate.diff.rstrip(),
        "",
        "THE CODE BEFORE AND AFTER",
    ]
    for path in sorted(candidate.files):
        before = candidate.original.get(path)
        after = candidate.patched.get(path)
        if before is None or after is None:
            lines.append(f"  {path}: source was not available to this audit")
            continue
        lines.extend([f"  {path} — before:", before.rstrip(), f"  {path} — after:", after.rstrip()])

    lines.extend(
        [
            "",
            "WHY THE COST WAS BELIEVED REAL",
            chain.render(),
            "",
            "THE TEST THIS CHANGE HAD TO PASS",
            f"  {test.claim}",
            f"  {test.cost.describe()}",
            f"  behaviour that must be preserved: {test.equivalence}",
            f"  written to catch: {', '.join(item.name.lower() for item in test.catches)}",
            "",
            "Nothing the author wrote about their own change is included.",
        ]
    )
    return "\n".join(lines)


def patch_audit_session(
    *, rate: ExchangeRate, source: str, ceiling_eur: Decimal | None = None
) -> Session:
    """A session belonging to the patch auditor, with its prompt as the prefix.

    S-9.1's `audit_session` with this story's system text — the parameter S-10.3
    added when the second adversarial audit arrived. Three audits now share one
    constructor and one isolation argument rather than three copies that drift.
    """
    return audit_session(rate=rate, source=source, ceiling_eur=ceiling_eur, system=SYSTEM)


def invoke(  # noqa: PLR0913 - the candidate, the chain, the test, the question
    # and the two measured token counts are six different facts, plus the session
    # and the client. There is deliberately no parameter for a `Patch`, for prior
    # attempts, or for a message history — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    candidate: Candidate,
    chain: EvidenceChain,
    test: FalsificationTest,
    question: str,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> StepOutcome[str]:
    """Ask an adversary to defeat a patch it did not write. AC 1 to AC 3.

    **The enforcement is an absence three times over**, as it was in S-9.1: there
    is no `patch` parameter (so the rationale cannot arrive), no `attempts`
    parameter (so prior attempts cannot), and no `messages` parameter (so a
    conversation cannot). AC 3 is `audit_messages`, reused rather than copied —
    it builds a **new list every call** and there is nowhere to pass an old one.

    AC 4 needs no code: the vendor is `Router` configuration, and ADR 062 records
    the second-vendor blocker as indefinite.

    Returns the reply text. Each attack story owns what it reads out of the
    answer; a schema invented here would fix a shape those stories have not
    designed — S-9.1's line, and it held.

    Raises:
        PatchAuditError: the session belongs to another agent, or the model
            declined or was cut off.
        BudgetExhaustedError: this finding's patch-audit rounds are spent.
    """
    refuse_shared_session(session, expected=SYSTEM)

    evidence = render_candidate(candidate, chain, test)
    step = Step(
        step_type=StepType.ATTACK_DESIGN,
        phase=Phase.PATCH_AUDIT,
        agent=Agent.ADVERSARY,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )

    def call(model: str) -> tuple[str, TokenUsage]:
        reply = client.complete(
            model=model,
            system=SYSTEM,
            messages=audit_messages(evidence, question),
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=AUDIT_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the patch auditor declined to answer. A refusal is a successful response "
                "with an empty content list, so it is reported rather than read as an audit "
                "that found nothing wrong — which is the reading that would ship a patch on "
                "a decline"
            )
            raise PatchAuditError(message)
        if reply.truncated:
            message = (
                f"the audit was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated objection "
                "is one whose conclusion is missing, and treating it as complete accepts "
                "whatever it happened to say first"
            )
            raise PatchAuditError(message)
        return reply.text, reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )


def authorize_round(budget: Budget, finding_id: str | None = None) -> None:
    """Refuse a third patch-audit round before it spends anything.

    `Phase.PATCH_AUDIT`'s two-round cap has had **no caller since S-5.4** — the
    fourth of these, after `FINDING_AUDIT` (S-9.8), `TEST_AUDIT` (S-10.3) and
    `REPAIR` (S-10.5). Every phase whose cap is counted in something other than
    steps has needed the story that owns the unit to count it.

    Raises:
        BudgetExhaustedError: both rounds are spent, with `ESCALATE` as §7.2's
            disposition for this phase.
    """
    budget.authorize(Phase.PATCH_AUDIT, finding_id)


def record_round(budget: Budget, conclusion: str, finding_id: str | None = None) -> None:
    """Count one completed round, with what it concluded for the stall check.

    The conclusion is the caller's because S-11.2 to S-11.5 have not defined
    their verdicts yet, and inventing a vocabulary here would fix a shape those
    stories own — the same refusal S-9.1 made about the reply schema.
    """
    budget.record_step(Phase.PATCH_AUDIT, finding_id, conclusion=conclusion.strip() or None)

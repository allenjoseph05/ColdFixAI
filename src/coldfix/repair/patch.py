"""Writing the change, once the test that would catch a fake one exists.

Epic 10, S-10.4. *Scope determined by the evidence chain's context list, not
agent guessing. Multi-file patches supported. Runs in candidate mode only. Patch
applier rejects protected paths.*

**A patch cannot be generated without proof the test failed on unpatched code.**
`generate` requires a `Falsified`, which only S-10.2's gate constructs and which
refuses to represent a passing run. `03-agents.md` §5.3 states that ordering as a
list of steps for an agent to follow; here it is a parameter with no default, so
the step cannot be skipped by a caller who read the list quickly. That is the
whole point of S-10.2 having produced an artifact rather than a boolean.

**Scope is the chain's, and the model is not asked what it may touch.** The
evidence chain names a `site` and a list of `context` files, each with the reason
it is implicated. Those paths are the scope. A diff touching anything else is
refused *before* it is applied — not because the model was told the rules and
broke them, but because S-2.4's finding is that a rule a model is told is a rule
something can be argued out of. The chain is shown to the Surgeon as **evidence**,
which is a different thing from a permission list: it says where the cost was
measured, and the check happens server-side regardless.

**`files` is derived from the diff rather than reported.** `03-agents.md` §5.4
gives `Patch` a `files: list[str]`, which is the agent restating something the
harness can compute — and two statements of one fact drift. This is the third
correction of that shape in Epic 10, after §5.4's `failed_on_unpatched` in S-10.1
and S-8.5's `invalidated_if` before it. `touched_paths` already parses a diff
correctly, including the case where a removed line looks like a file header.

**Candidate mode only, and the type is the enforcement.** `CandidateSession` is
the one class with `apply_patch` and `diff`; `DiagnosticSession` has neither, by
S-2.3's construction. S-10.2's gate takes the diagnostic session because a patch
must not be able to exist there; this takes the candidate one because a patch has
to. The pair is the same rule read from both ends.

**Protected paths are refused by the applier, not here.** S-2.4 put that check
inside `apply_patch` because it is *the only route by which a diff becomes a
file*, and a second copy in this module would be a check something could be
routed around. What this module adds is a narrower rule on top — in-scope is a
smaller set than not-protected — and a file can be both in scope and protected,
in which case the applier still refuses it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.context import Block
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.diagnosis.replies import read_object
from coldfix.llm.client import ModelClient
from coldfix.llm.request import as_request
from coldfix.repair.mustfail import Falsified
from coldfix.repair.sessions import refuse_foreign_session
from coldfix.sandbox.modes import CandidateSession
from coldfix.sandbox.patching import touched_paths

SURGEON_TEMPERATURE = 0.2
"""`03-agents.md` §5.1: 0.2 on the first attempt. S-10.5 owns the raise to 0.6 on
retries, and its argument is that a retry at 0.2 produces a variation of the same
idea, which fails the same way."""

MAX_OUTPUT_TOKENS = 8_000
"""Twice the falsification test's, because a multi-file diff is the largest thing
any agent in this system emits."""


class PatchError(Exception):
    """No usable patch came back, or the one that did is out of scope."""


class Patch(BaseModel):
    """§5.4's artifact, minus the field the harness can compute.

    **There is no `files` field.** The agent would be restating what the diff
    already says, and a list that disagreed with its own diff is a scope check
    passing against a claim rather than against the change.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    diff: str = Field(min_length=1)
    approach: str = Field(min_length=1)
    """How this attempt differs from the last. Kept because S-10.5 shows prior
    ones to the model, and **not** trusted: F12 records that *must differ in
    approach* is self-judged and the agent can rename the same idea, so the
    structural check compares diffs. This field is context, not evidence."""

    rationale: str = Field(min_length=1)
    """Why this change fixes the measured cause.

    Written for a human reading the pull request. **The patch audit must not see
    it** — S-9.1's whole argument, and `08-audit.md`'s number: 72% of
    reward-hacking episodes carry explicit justifying reasoning. Epic 11 owns
    that withholding; the field is named here so that story knows what to strip.
    """

    @field_validator("diff", "approach", "rationale")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        """`min_length=1` is satisfied by a space. S-10.1 found this the hard way."""
        if not value.strip():
            message = (
                "this field is blank. `min_length` counts characters and a space is a "
                "character, so a whitespace-only diff or rationale satisfies the schema "
                "while saying nothing"
            )
            raise ValueError(message)
        return value

    @property
    def files(self) -> frozenset[str]:
        """Every path this diff touches. **Derived, never reported.**

        `touched_paths` handles the case a naive parser gets wrong: inside a hunk
        a removed line whose content begins `-- a/x` renders as `--- a/x` and is
        indistinguishable from a file header to anything scanning line prefixes.
        """
        return touched_paths(self.diff)

    def describe(self) -> str:
        listed = ", ".join(sorted(self.files)) or "nothing"
        return f"PATCH — {self.approach}\n  touches: {listed}\n  because: {self.rationale}"


@dataclass(frozen=True)
class Attempt:
    """A patch that was tried, and why it did not work.

    **The failure is the half worth carrying.** S-10.4 first showed retries only
    the previous `approach` strings, which is precisely the self-judged label F12
    says the agent can rename — so the context that was supposed to make the next
    attempt different consisted entirely of the thing that cannot be trusted to
    be different. `03-agents.md` §5.1 asks for *prior attempts **with failure
    reasons***, and this is that field.
    """

    patch: Patch
    failure: str

    def __post_init__(self) -> None:
        if not self.failure.strip():
            message = (
                "an attempt recorded with no failure reason gives the next one nothing to "
                "avoid. §5.1 asks for prior attempts *with failure reasons*, and an empty one "
                "leaves only the approach label, which F12 says can be renamed"
            )
            raise PatchError(message)

    def describe(self) -> str:
        return f"{self.patch.approach} — failed because {self.failure}"


def scope_of(chain: EvidenceChain) -> frozenset[str]:
    """The files this finding's evidence implicates. AC 1.

    The site is where the cost was measured; the context files are the ones
    S-8.6 requires each carry a **reason** for being listed. Together they are
    the scope, and nothing else is — an agent that has decided a fourth file also
    needs changing has decided something the evidence does not support, and the
    remedy is a new investigation rather than a wider patch.
    """
    return frozenset({chain.site.path} | {item.path for item in chain.context})


def check_scope(patch: Patch, chain: EvidenceChain) -> str | None:
    """Whether the diff stays inside the evidence. Returns the objection or `None`.

    A return rather than a raise so `generate` can feed the reason back, which is
    S-8.2's rule: a rejection worth re-asking on is one that carries why.
    """
    allowed = scope_of(chain)
    touched = patch.files
    if not touched:
        return (
            "this diff touches no file. A patch that changes nothing cannot fix the measured "
            "cause, and it would pass every gate downstream by having nothing to object to"
        )

    outside = sorted(touched - allowed)
    if not outside:
        return None
    return (
        f"this patch touches {outside}, which the evidence does not implicate. The chain "
        f"names {sorted(allowed)} — the site where the cost was measured and the files "
        "S-8.6 requires a stated reason for. Changing a file outside that set is a claim "
        "no experiment in this investigation supports"
    )


_SYSTEM = """\
You are writing the change that removes a measured cost. Somebody else found it, \
somebody else wrote the test that will judge you, and that test already failed \
against the code as it stands.

You are given the evidence: what was measured, where the cost is, which files are \
implicated and why, and the test your change has to make pass.

Change only what the evidence implicates. If the fix seems to need a file nobody \
measured, say so in your rationale rather than reaching for it — a change the \
evidence does not support is one nobody can verify.

Answer with a unified diff. Do not explain it inside the diff."""

QUESTION = """\
Write the patch.

Answer with a single JSON object and nothing else:

{"diff": "...", "approach": "...", "rationale": "..."}

`diff` is a unified diff with `---`/`+++` headers and `@@` hunks, applying \
cleanly to the code as shown.
`approach` names the technique in a few words, so a later attempt can be seen to \
differ from this one.
`rationale` says why this removes the measured cost, for a human reading the \
pull request."""


def render_brief(chain: EvidenceChain, falsified: Falsified) -> str:
    """What the Surgeon is shown: the chain, and the test it has to satisfy.

    **There is no separate scope block, and a sabotage is why.** One was written —
    *FILES THE EVIDENCE IMPLICATES*, listing the site and each context file with
    its reason — and `EvidenceChain.render` already emits `SITE` and `IMPLICATED
    FILES` with the same reasons. Two statements of one fact in a prompt cost
    tokens on every call and drift when one is edited, and the duplication made
    the second copy untestable: deleting the reasons from it changed no assertion,
    because the chain's own rendering still carried them.

    The scope is still **shown**, by the chain — these are the files the
    investigation implicated and the reasons it recorded. It is not shown as a
    permission list, and stating it changes nothing about the check: a diff
    outside it is refused whether or not the model was told.
    """
    return (
        f"{chain.render()}\n\n"
        f"THE TEST YOUR CHANGE MUST MAKE PASS\n"
        f"  {falsified.test.claim}\n"
        f"  {falsified.test.cost.describe()}\n"
        f"  behaviour that must be preserved: {falsified.test.equivalence}\n"
        f"  it was written to catch: "
        f"{', '.join(item.name.lower() for item in falsified.test.catches)}\n"
        f"  it already failed on the unpatched code, which is why you are being asked"
    )


def parse(text: str, chain: EvidenceChain) -> Patch:
    """Read a patch and refuse one that reaches outside the evidence.

    Raises:
        PatchError: the reply is unusable, or the diff touches a file the
            investigation never implicated.
    """
    read = read_object(text)
    if read.value is None:
        raise PatchError(read.rejection)

    try:
        patch = Patch.model_validate(read.value)
    except ValueError as error:
        message = f"this is not a usable patch: {error}"
        raise PatchError(message) from error

    objection = check_scope(patch, chain)
    if objection is not None:
        raise PatchError(objection)
    return patch


def generate(  # noqa: PLR0913 - the chain, the proof, the prior attempts and the
    # two measured token counts are five different facts, plus the session and
    # the client. `falsified` has no default on purpose — see the module docstring.
    session: Session,
    client: ModelClient,
    *,
    chain: EvidenceChain,
    falsified: Falsified,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    prior: Sequence[Attempt] = (),
    temperature: float = SURGEON_TEMPERATURE,
    finding_id: str | None = None,
) -> StepOutcome[Patch]:
    """Write the patch. AC 1 and AC 2.

    **`falsified` is required and has no default.** It is S-10.2's proof that the
    test failed against unpatched code, and only that gate constructs one. A
    caller who skipped the gate has nothing to pass, which is `03-agents.md`
    §5.3's mandatory ordering expressed as a signature rather than as a list.

    `prior` carries earlier attempts **with their failure reasons** so the model
    can differ from them. S-10.5 owns the retry discipline and the structural
    check that they *did* differ; this only makes the context available, and
    `temperature` is a parameter for the same reason — §5.1 raises it to 0.6 on
    retries and that story decides when.

    **Multi-file diffs are ordinary** (AC 2): the scope is a set, and a patch
    touching the site plus two implicated files is in scope by construction.

    Raises:
        PatchError: no usable patch came back, or it reached outside the chain.
        BudgetExhaustedError: this finding's repair attempts are spent.
    """
    refuse_foreign_session(session, _SYSTEM, PatchError)
    step = Step(
        step_type=StepType.PATCH,
        phase=Phase.REPAIR,
        agent=Agent.SURGEON,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        finding_id=finding_id,
    )
    question = f"{render_brief(chain, falsified)}\n\n{_render_prior(prior)}{QUESTION}"

    def call(model: str, blocks: Sequence[Block]) -> tuple[Patch, TokenUsage]:
        # **The system prompt is this module's, never the session's.** The
        # investigate loop runs three steps on one session, so the session's
        # string is not every step's prompt — sending it would tell two of them
        # to answer a third one's question. See `llm/request.py`.
        messages = as_request(blocks)
        reply = client.complete(
            model=model,
            system=_SYSTEM,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=temperature,
        )
        if reply.refused:
            message = (
                "the Surgeon declined to write a patch. A refusal is a successful response "
                "with an empty content list, so it is reported rather than read as a finding "
                "that needed no change"
            )
            raise PatchError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. A truncated diff is one "
                "whose last hunk is incomplete, and git would either refuse it or apply half a "
                "change — neither of which is the patch that was written"
            )
            raise PatchError(message)
        return parse(reply.text, chain), reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )


def _render_prior(prior: Sequence[Attempt]) -> str:
    """Earlier attempts and **why each failed**.

    The failure is what makes this context worth its tokens. A list of approach
    labels alone tells the model what it called things, not what went wrong — and
    F12's finding is that the label is the one part it can change freely while
    changing nothing else. `03-agents.md` §5.1 asks for *prior attempts **with
    failure reasons***; the first draft of this function carried only the labels.
    """
    if not prior:
        return ""
    lines = ["ATTEMPTS ALREADY MADE — yours must differ in approach, not only in wording:"]
    lines.extend(f"  {index + 1}. {item.describe()}" for index, item in enumerate(prior))
    return "\n".join(lines) + "\n\n"


@dataclass(frozen=True)
class Applied:
    """A patch that reached the worktree, and what it wrote."""

    patch: Patch
    written: frozenset[str]

    def describe(self) -> str:
        return f"Applied to {sorted(self.written)}.\n  {self.patch.describe()}"


def apply(patch: Patch, chain: EvidenceChain, session: CandidateSession) -> Applied:
    """Put the patch in a candidate worktree. AC 3 and AC 4.

    **The session type is AC 3.** `CandidateSession` is the only class with
    `apply_patch`, so there is no diagnostic session this could be pointed at —
    the mirror of S-10.2's gate, which takes the diagnostic one because a patch
    must not be able to exist there.

    **AC 4 is S-2.4's and is not reimplemented.** The protected-path filter lives
    inside `session.apply_patch` because that is the only route by which a diff
    becomes a file, and a second copy here would be a check something could be
    routed around. The scope check runs first because it is the narrower rule and
    the cheaper one — but a file can be both in scope and protected, and the
    applier refuses it either way.

    Raises:
        PatchError: the diff reaches outside the evidence.
        ProtectedPathError: it touches a file that decides whether the patch
            worked.
        PatchDidNotApplyError: it was allowed and does not fit.
    """
    objection = check_scope(patch, chain)
    if objection is not None:
        raise PatchError(objection)
    return Applied(patch=patch, written=session.apply_patch(patch.diff))


def summarize(patches: Sequence[Patch]) -> Mapping[str, int]:
    """How many attempts touched each file, for a report or a retry's context."""
    counts: dict[str, int] = {}
    for patch in patches:
        for path in patch.files:
            counts[path] = counts.get(path, 0) + 1
    return counts

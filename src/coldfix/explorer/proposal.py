"""Asking the model what to run next, and refusing an answer that is not a command.

Epic 7, S-7.14. **The Explorer's only model call**, and the first one this system
makes outside the Diagnostician, the Surgeon and the two auditors. Everything
Epic 7 built is deterministic — nine predicates, an auth probe, a fixture
synthesiser, a work gate — and `00-BRIEF.md` §5 step 5 says why that is not
enough on its own: *give it an unfamiliar Django repo and one goal.* Deciding
what to run against an unfamiliar repository is the part no predicate computes.

**The answer is a command, not prose, and that is the whole design.**
`04-cost.md` §3 lists this step as *decide next action from a command result*,
against the mechanical check **command exit code** — so a reply this cannot turn
into argv has failed its own check before anything runs it. A free-text
instruction would move that check to whoever had to interpret it, which is the
place the check stops existing.

**Cheap tier, 0.3, sliding window of twenty.** All three are `03-agents.md`
§2.1's, not chosen here: the steps are many and individually simple, and paying
frontier rates to run `ls` is the waste §12.3's engineered case is about. The
routing is S-5.5's and already sends `(GROUND, MECHANICAL)` to the cheap tier.

**There is no `validate` parameter, and the reason is the opposite of S-8.1's.**
Hypothesis generation may not cascade because no validator exists. This step *has*
one — §3 names it — but the exit code is known only after the command runs, and
S-5.6 validates the **reply**. So a cascade is not refused here on principle; it
has nothing to check at the moment it would have to check it. What does use the
exit code is the loop, which feeds a failed command back as the next question's
history — the same check, applied where it is available.

**`03-agents.md` §2.3 gives five sections and only three of them are in the
prompt.** ROLE, GOAL and RULES are the same on every call and belong in the
cached prefix; KNOWN and HISTORY change every turn, and putting a stage report
into the system prompt would move the cache boundary past the one thing that
differs — which is `04-cost.md` §4's whole arithmetic inverted. So the report and
the window ride in the question, which is the part nothing caches anyway.

**Stopping is one of the two things the model may answer.** `00-BRIEF.md` §9
ships a null result as an answer and S-7.11's acceptance is that the Explorer
*reports failure on a fourth repository rather than claiming success on empty
data*, so *this will not ground, and here is what would have to be true* has to
be sayable. A prompt whose only legal answer is another command is a prompt that
cannot express the honest outcome.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.context import Block
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step, StepOutcome
from coldfix.explorer.stages import Outcome, Progress, Stage
from coldfix.llm.client import ModelClient
from coldfix.llm.request import as_request

EXPLORER_TEMPERATURE = 0.3
"""`03-agents.md` §2.1. Between the Diagnostician's two: standing a project up is
not creative work, but the second thing to try after a failed install is not
mechanically derivable from the first either."""

MAX_OUTPUT_TOKENS = 500
"""`04-cost.md` §12.1's Ground row. An action is a command and a sentence."""

HISTORY_WINDOW = 20
"""`03-agents.md` §2.1: *a sliding window of the last 20 action/observation
pairs*. A window rather than the whole history because grounding is the phase
with the most steps and the least reason to remember all of them — what the
twenty-first command did is rarely why the fourth failed, and the prompt is what
`04-cost.md` §4 caches."""

_OUTPUT_KEPT = 600
"""Bytes of a command's output carried into the next question.

Bounded because a failing migration prints a stack per app and a pip resolution
prints a page per candidate, and a prompt that grew with them would spend the
window on one observation. The **tail** is kept: a traceback says what went wrong
at the end."""

_SYSTEM = """\
ROLE      You get unfamiliar projects running. You are not optimizing anything \
yet, and you do not read code to decide whether something works — after every \
command you propose, the harness measures nine predicates and tells you what it \
saw.

GOAL      Produce a workload: something runnable, at controllable input size, \
that does real work, and can be reset between runs. You are asked about one \
blocked stage at a time, and you propose the one command most likely to make \
that stage's predicate true.

RULES     One command at a time. It is run in the repository and you are told \
its exit code and its output before you are asked again.
          Answer with a single JSON object and nothing else, in one of two forms:

          {"command": ["...", "..."], "why": "..."}
          {"give_up": "..."}

          `command` is argv, already split. `why` says what you expect it to \
change, in terms of the stage's predicate.
          `give_up` is for a repository that will not ground: say what would \
have to be true for it to.
          Do not repeat a command already tried at this stage unless something \
in the report has changed since.
          Do not propose a command for a stage other than the one you were \
asked about.
          If you cannot make the project do real work, say so and stop. Never \
report success when the workload touches no data."""

# A model asked for JSON returns JSON, a fenced block, or JSON with a sentence in
# front of it. The first balanced object is taken and nothing is repaired: S-8.1's
# rule, and the same argument — *the model answered something else* and *the model
# was wrong* are different problems needing different fixes.
_JSON = re.compile(r"\{.*\}", re.DOTALL)


class ProposalError(Exception):
    """No usable action came back."""


@dataclass(frozen=True)
class Move:
    """One command to run, and what the agent expects it to change.

    `command` is a tuple because it is about to be handed to something that runs
    it, and a list an executor could append to is a command the record no longer
    describes.
    """

    command: tuple[str, ...]
    why: str

    def rendered(self) -> str:
        return " ".join(self.command)

    def describe(self) -> str:
        return f"{self.rendered()} — {self.why}"


@dataclass(frozen=True)
class GiveUp:
    """The agent's own answer that this repository will not ground.

    A separate type rather than an empty `Move`, because the two want opposite
    handling and a caller that had to test `if not move.command` would be one
    refactor away from treating a parse failure as a decision to stop.
    """

    reason: str


type Proposal = Move | GiveUp


@dataclass(frozen=True)
class Tried:
    """One command the Explorer ran at one stage, and what the harness saw.

    The pair is the unit `03-agents.md` §2.1 windows over — *action/observation*
    — and keeping them together is what stops the history rendering a list of
    commands whose results are somewhere else.
    """

    stage: Stage
    move: Move
    exit_code: int
    output: str

    def describe(self) -> str:
        tail = self.output.strip()[-_OUTPUT_KEPT:]
        return (
            f"  {self.stage.value}: {self.move.rendered()}\n"
            f"    exit {self.exit_code}\n"
            f"    said: {tail or '(no output)'}"
        )


def render_question(
    *,
    blocked: Outcome,
    progress: Progress,
    tried: Sequence[Tried],
    attempts_left: int,
    steps_left: int,
) -> str:
    """`03-agents.md` §2.3's shape, with the harness's report where KNOWN goes.

    **The report is nine measured verdicts and not the agent's account of its
    progress**, which is `08-audit.md` F6 one layer up from where F6 was found:
    the fix there was that *does real work* is computed rather than claimed, and
    the same rule applied to the other eight stages is what makes this question
    answerable at all. A question that asked *how is it going* would be asking the
    agent to grade itself and then acting on the grade.

    The two budgets are stated because they change what a sensible next command
    is: one attempt left at a stage is where a long-shot install belongs, and
    eight is where reading the log first does.
    """
    history = [entry for entry in tried if entry.stage is blocked.stage][-HISTORY_WINDOW:]
    rendered = "\n".join(entry.describe() for entry in history) or "  (nothing tried here yet)"
    return (
        f"BLOCKED AT\n  {blocked.stage.value}\n"
        f"  done means: {blocked.stage.definition}\n"
        f"  the harness measured: {blocked.verdict.value} — {blocked.detail}\n\n"
        f"EVERY STAGE\n{progress.describe()}\n\n"
        f"TRIED AT THIS STAGE\n{rendered}\n\n"
        f"BUDGET\n  {attempts_left} attempt(s) left at {blocked.stage.value}, "
        f"{steps_left} step(s) left in the run\n\n"
        f"What one command should be run next?"
    )


def parse(text: str, blocked: Stage) -> Proposal:
    """Read one command, or one refusal, out of a reply.

    `blocked` is taken so the stage-confusion case is refused rather than
    silently attributed: a reply naming another stage is an answer to a question
    nobody asked, and recording it against the stage that *was* asked would put a
    command that could not help it into that stage's history as a thing that was
    tried.

    Raises:
        ProposalError: the reply is not a JSON object, answers both ways or
            neither, carries a command that is not argv, or names a stage other
            than the one asked about.
    """
    found = _JSON.search(text)
    if found is None:
        message = (
            f"no action could be read from this reply: {text.strip()[:300]!r}. Nothing here "
            "repairs an answer — a reply that is not a command has already failed the check "
            "`04-cost.md` §3 puts against this step"
        )
        raise ProposalError(message)

    try:
        payload = json.loads(found.group(0))
    except json.JSONDecodeError as error:
        message = f"the action was not valid JSON: {error}"
        raise ProposalError(message) from error

    if not isinstance(payload, dict):
        message = f"an action must be an object, got {type(payload).__name__}"
        raise ProposalError(message)

    named = payload.get("stage")
    if named is not None and str(named).strip() != blocked.value:
        message = (
            f"this answer is about {str(named).strip()!r} and the question was about "
            f"{blocked.value!r}. Recording it against the stage that was asked would put a "
            "command that cannot help into that stage's history as a thing that was tried"
        )
        raise ProposalError(message)

    surrender = payload.get("give_up")
    command = payload.get("command")

    if surrender and command:
        message = (
            "this reply both proposes a command and gives up. The two are opposite outcomes and "
            "a caller picking one would be deciding what the agent meant"
        )
        raise ProposalError(message)

    if surrender:
        return GiveUp(reason=str(surrender).strip())

    if not command:
        message = (
            "this reply proposes no command and does not give up, so there is nothing to run and "
            "nothing to report. Stopping is an answer here and it has to be said"
        )
        raise ProposalError(message)

    if isinstance(command, str) or not isinstance(command, list):
        message = (
            f"the command is {type(command).__name__}, and argv is a list of strings. A string "
            "would have to be split by somebody, and whoever split it would be deciding what the "
            "quoting meant"
        )
        raise ProposalError(message)

    if any(not isinstance(word, str) or not word.strip() for word in command):
        message = f"every word of a command must be a non-empty string, got {command!r}"
        raise ProposalError(message)

    return Move(
        command=tuple(str(word) for word in command),
        why=str(payload.get("why", "")).strip(),
    )


def propose(  # noqa: PLR0913 - the four inputs `render_question` needs plus the
    # session, the client and the two measured token counts. None is derivable
    # from the others, and there is deliberately no `validate` among them.
    session: Session,
    client: ModelClient,
    *,
    blocked: Outcome,
    progress: Progress,
    tried: Sequence[Tried],
    attempts_left: int,
    steps_left: int,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
) -> StepOutcome[Proposal]:
    """Ask for the next command, on the cheap tier, at 0.3.

    Returns the whole `StepOutcome` rather than the proposal alone, for S-8.1's
    reason: which model answered and what it cost are part of what the run
    records, and a learning curve plotted against steps is worth less beside a
    cost nobody attributed.

    Raises:
        ProposalError: no usable action came back.
        BudgetExhaustedError: a cap or the ceiling stopped the call.
    """
    question = render_question(
        blocked=blocked,
        progress=progress,
        tried=tried,
        attempts_left=attempts_left,
        steps_left=steps_left,
    )
    step = Step(
        step_type=StepType.EXPLORER_ACTION,
        phase=Phase.GROUND,
        agent=Agent.EXPLORER,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    def call(model: str, blocks: Sequence[Block]) -> tuple[Proposal, TokenUsage]:
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
            temperature=EXPLORER_TEMPERATURE,
        )
        if reply.refused:
            message = (
                "the model declined to answer. A refusal is a successful response with an empty "
                "content list, so this is reported rather than parsed as a very short command"
            )
            raise ProposalError(message)
        if reply.truncated:
            message = (
                f"the reply was cut off at {MAX_OUTPUT_TOKENS} tokens. Half a JSON object parses "
                "as nothing, and a command assembled from half a line is a guess about what the "
                "model was going to run"
            )
            raise ProposalError(message)
        return parse(reply.text, blocked.stage), reply.usage

    return session.run(
        step,
        question=question,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        call=call,
    )

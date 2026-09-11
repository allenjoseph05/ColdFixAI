"""The Adversary: attack a patch without reading the code or the author. **S-27.2.**

ADR 181. E23 built the Adversary's tool (`revisions.compare`) and its input
(`PatchUnderAudit`) and no prompt. This is the prompt, and the loop around it.

**The model designs inputs. The harness decides what broke.** Each turn the model
proposes up to five inputs -- argument lists appended to the program's command --
and the harness runs every one on both revisions. Whether an input broke the
patch is a byte comparison of two outputs, each seen twice, and never something
the model says. The verdict is computed from the comparisons, and `PatchReview`
refuses to hold a verdict they contradict.

**Isolation is what the model is built from.** Every turn's message list is made
fresh from `PatchUnderAudit` and the comparisons so far. Nothing from the
Optimizer's conversation exists here to leak, the workspace paths are the
harness's and are not shown, and there is no field through which a reason for the
patch could arrive.

**Attack design is creative**, so every turn runs on the frontier tier and can
neither cascade nor escalate: §3 records no check that could catch a weak attack.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Final

from anthropic.types import MessageParam
from pydantic import BaseModel, ValidationError, model_validator

from coldfix.cost.accounting import Agent, Phase
from coldfix.cost.routing import StepType
from coldfix.evidence.revisions import Comparison, PatchUnderAudit
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS
from coldfix.llm.metered import Call, Meter

TEMPERATURE = 0.0
"""Recorded intent (ADR 178). The frontier tier ignores it; variety comes from what
the comparisons so far have shown."""

MAX_TURNS = 4
PER_TURN = 5
"""Four turns of five: `15-full-architecture.md`'s bound of 20 attacks."""

CACHE_TTL: Final = "5m"
"""ADR 181. A turn's gap is five inputs on two revisions, twice each -- seconds --
so the brief is still cached when the next turn asks. Unlike the Optimizer, whose
rounds are separated by measuring candidates (ADR 180)."""

ATTACK = Call(
    step=StepType.ATTACK_DESIGN,
    phase=Phase.PATCH_AUDIT,
    agent=Agent.ADVERSARY,
    max_tokens=NON_STREAMING_MAX_TOKENS,
)

SYSTEM = """\
You try to break a performance patch. Somebody changed a program to make it
faster, and its tests pass. Your job is to find an input on which the changed
program does something the original did not -- different output, or a failure
where the original worked.

You cannot read the code and you are not told why the change was made. You are
shown the diff, the test it was written to pass, and how the program is run. You
attack by choosing inputs: each is appended to the command and run on both
revisions, twice, and you are shown what each did. The harness decides whether an
input broke the patch, by comparing the two outputs byte for byte. You decide
only what to try.

Start from what the change assumes. A cache assumes its key captures every input
that matters. A hoisted computation assumes nothing inside the loop changes it. A
shortcut assumes the case it skips never happens. Then write the input that breaks
the assumption: two values where the author tested one, the empty case, the
repeated case, the boundary, the unusual type.

Reply with one JSON object and nothing else:

  {"inputs": [["hello", "world"], ["--count", "0"]]}

Each input is a list of arguments. Up to five per turn. An empty list of inputs
means you have nothing more worth trying.
"""

Compare = Callable[[Sequence[str]], Comparison]
"""`revisions.compare` with the patch under audit bound in: the one tool."""


class Verdict(StrEnum):
    BROKEN = "broken"
    CLEAN = "clean"
    UNSTABLE = "unstable"
    """Nothing broke, and the original's own output varied on some input -- which
    may hide a break on exactly that input. A person decides."""
    UNATTACKED = "unattacked"
    """Nothing was compared. Surviving an attack that was never mounted is not
    surviving."""


ROUTES: Mapping[Verdict, str] = {
    Verdict.BROKEN: "another_round",
    Verdict.CLEAN: "clean",
    Verdict.UNSTABLE: "escalate",
    Verdict.UNATTACKED: "escalate",
}
"""The `audit_patch` edges in `pipeline/graph.py`. Only `clean` reaches the ship
gate; both undecided verdicts go to a person rather than through."""


class VerdictContradictedError(ValueError):
    """A `PatchReview` whose verdict is not what its own comparisons show."""


def judge(comparisons: Sequence[Comparison]) -> Verdict:
    """The verdict the comparisons support. The only place one is decided."""
    if any(c.broke for c in comparisons):
        return Verdict.BROKEN
    compared = [c for c in comparisons if c.both_ran]
    if any(c.unstable for c in compared):
        return Verdict.UNSTABLE
    return Verdict.CLEAN if compared else Verdict.UNATTACKED


class PatchReview(BaseModel, frozen=True):
    """What `audit_patch` hands on: every input tried, and what they establish.

    The verdict is stored so a router can read it and a checkpoint can hold it,
    and validated so it cannot disagree with the evidence -- a `broken` with no
    reproducing input, or a `clean` over a break or over nothing, is refused
    however it was built.
    """

    verdict: Verdict
    comparisons: tuple[Comparison, ...]
    turns: int
    notes: tuple[str, ...] = ()
    """Turns that yielded nothing to run, and why."""

    @model_validator(mode="after")
    def _the_verdict_is_what_the_comparisons_show(self) -> PatchReview:
        supported = judge(self.comparisons)
        if self.verdict is not supported:
            message = (
                f"a {self.verdict.value} verdict over comparisons that show {supported.value}. "
                "The verdict is computed from what both revisions did; one that disagrees with "
                "them is a claim nobody measured"
            )
            raise VerdictContradictedError(message)
        return self

    @property
    def reproducing(self) -> tuple[Comparison, ...]:
        """The inputs that broke the patch, each with what both revisions did."""
        return tuple(c for c in self.comparisons if c.broke)

    @property
    def route(self) -> str:
        return ROUTES[self.verdict]


class _Reply(BaseModel, frozen=True):
    """A turn's reply. Any other key -- a verdict above all -- is dropped here."""

    inputs: list[list[str]]


def review_patch(meter: Meter, *, under_audit: PatchUnderAudit, compare: Compare) -> PatchReview:
    """Attack the patch for up to `MAX_TURNS` turns and return what was established.

    Stops after the turn in which something broke: one reproducing input is
    enough to send the patch back, and further attacks change no decision. A
    declined, cut-off or unreadable turn is spent and never read.

    Raises:
        BudgetExhaustedError: the meter refused a turn. Nothing was sent, and the
            graph decides what a halt means.
    """
    comparisons: list[Comparison] = []
    notes: list[str] = []
    brief = render_brief(under_audit)
    turns = 0
    for turn in range(1, MAX_TURNS + 1):
        turns = turn
        response = meter.complete(
            ATTACK,
            system=SYSTEM,
            messages=_request(brief, _history(comparisons, notes)),
            temperature=TEMPERATURE,
            cache_ttl=CACHE_TTL,
        )
        if response.refused:
            notes.append(f"turn {turn}: the model declined to answer")
            continue
        if response.truncated:
            notes.append(
                f"turn {turn}: the reply was cut off at {ATTACK.max_tokens} tokens and was not read"
            )
            continue
        try:
            proposed = _Reply.model_validate_json(_unfenced(response.text)).inputs
        except ValidationError as unreadable:
            notes.append(
                f"turn {turn}: the reply could not be read: {unreadable.errors()[0]['msg']}"
            )
            continue
        if not proposed:
            break

        tried = {c.given for c in comparisons}
        fresh = list(dict.fromkeys(tuple(given) for given in proposed if tuple(given) not in tried))
        if not fresh:
            notes.append(f"turn {turn}: every input had already been tried")
            continue
        comparisons.extend(compare(given) for given in fresh[:PER_TURN])
        if any(c.broke for c in comparisons):
            break

    return PatchReview(
        verdict=judge(comparisons), comparisons=tuple(comparisons), turns=turns, notes=tuple(notes)
    )


def render_brief(under_audit: PatchUnderAudit) -> str:
    """What does not change between turns: the diff, the test, the command.

    Built from `PatchUnderAudit` field by field, and not from the workspace paths,
    which are where the harness runs things and nothing the reviewer needs.
    """
    return "\n".join(
        [
            "THE PATCH",
            under_audit.diff,
            "",
            "THE TEST IT WAS WRITTEN TO PASS",
            under_audit.test,
            "",
            "HOW THE PROGRAM IS RUN -- your input is appended to this",
            f"  {' '.join(under_audit.command)}",
        ]
    )


def _history(comparisons: Sequence[Comparison], notes: Sequence[str]) -> str:
    lines: list[str] = []
    if comparisons:
        lines.append("TRIED SO FAR -- the same input on both revisions")
        lines.extend(f"  {json.dumps(list(c.given))}: {c.why()}" for c in comparisons)
    lines.extend(notes)
    lines.append(f"Write up to {PER_TURN} new inputs.")
    return "\n".join(lines)


def _request(brief: str, history: str) -> list[MessageParam]:
    """One user message: the brief, cached, then the history, which is not.

    The brief is byte-identical every turn and carries the only breakpoint, so each
    turn reads it from the cache and pays full price only for what changed.
    """
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": brief,
                    "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
                },
                {"type": "text", "text": history},
            ],
        }
    ]


def _unfenced(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

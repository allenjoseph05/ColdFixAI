"""Turning S-5.7's blocks into what the vendor's API takes. **S-17.16.**

Epic 5 built a prompt out of five segments — system, playbook, source, log,
question — with a cache breakpoint at the end of the first four. `Session.run`
renders them on every call and **nothing has ever sent them**: each agent builds
its own `messages=[{"role": "user", "content": question}]`, so no `cache_control`
has ever reached the API and `04-cost.md` §12.3's engineered cost has never been
achievable. Two composition checks recorded that and neither fixed it, because the
fix is not forwarding a value — see the module docstring's second half.

**Three segments are shaped here, not four, and the missing one is the finding
that nearly shipped a much worse defect than the one this story fixes.** The
first version put `Segment.SYSTEM`'s text into the request's `system`. It is the
obvious reading — the segment is called system — and it is wrong, because a
`Session`'s system string and the system prompt an agent *sends* are not the same
thing and never were.

`orchestrator/adapters.py` opens **one** session for the whole investigate loop,
with `_INVESTIGATION_PROMPT = hypothesis._SYSTEM`, and all three Diagnostician
steps run on it. While each agent sent its own `_SYSTEM` explicitly, that was a
billing and caching mismatch — the thing `repair/sessions.refuse_foreign_session`
was written for, and which nobody ever applied to the Diagnostician. Had the
session's string become what is sent, `design` and `interpret` would have been
handed the *hypothesis* prompt: told to answer with a statement, a primitive and
a rationale when they must return a specification and a verdict. Every test would
still have passed, because the fixtures build their recordings from the same
session.

So **the caller passes its own `_SYSTEM` and this function never touches it.**
The cost is one breakpoint of four: the system text is sent as a plain string and
does not cache. The playbook, the source and the log do, and they are the part
that grows.

**A breakpoint marks the *end* of a cacheable prefix**, so it is attached to the
last block of that prefix rather than the first block after it. The API caches
everything up to and including a marked block, which is why `Segment`'s order is,
as `context.py` puts it, *the whole technique*.

**The question never carries one.** Caching it writes an entry no later call can
read — the question is the part that varies — and spends one of only three
breakpoints doing it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from coldfix.cost.context import Block, Segment

CACHE_CONTROL = {"type": "ephemeral"}
"""What marks a breakpoint. `ephemeral` is the only kind the API has."""


class RequestError(Exception):
    """Blocks could not be shaped into a request."""


def as_request(blocks: Sequence[Block]) -> list[Any]:
    """The message list, with a breakpoint on each cacheable segment's end.

    **`Segment.SYSTEM` is skipped rather than sent**, for the reason in the module
    docstring: the session's system string identifies the session — it is what
    `owner_of` attributes, what `refuse_shared_session` compares and what the
    prefix is billed against — while what the model is *told* is the calling
    module's `_SYSTEM`. Those two are not the same string in the investigate
    loop, and sending the session's would silently replace two of the three
    Diagnostician prompts.

    Empty blocks are dropped rather than sent, because the API refuses an empty
    text block. **Nothing `Prompt.render` produces is ever empty**, and that was
    worth checking rather than assuming: `Investigation.__post_init__` refuses a
    blank system, playbook or source, `render` refuses a blank question, and the
    log — the one segment that legitimately starts with nothing in it — still
    renders S-5.8's retrieval notice, so its block carries text from the first
    call. The skip therefore guards a caller assembling blocks by hand, which
    this function's signature permits, rather than a state a session can reach.

    Raises:
        RequestError: nothing is left to ask. A request whose every block was
            empty is not a short prompt, it is an absent one, and the reply to it
            would be the model answering a question nobody put.
    """
    content: list[Any] = []

    for block in blocks:
        if block.segment is Segment.SYSTEM or not block.text.strip():
            continue
        shaped: dict[str, Any] = {"type": "text", "text": block.text}
        if block.breakpoint:
            shaped["cache_control"] = dict(CACHE_CONTROL)
        content.append(shaped)

    if not content:
        message = (
            "every block of this prompt was empty, so there is no question to ask. A request "
            "with nothing in it is not a cheap call — it is one whose answer is about nothing, "
            "and the cost of finding that out is the same as any other call"
        )
        raise RequestError(message)

    return [{"role": "user", "content": content}]


def with_question(blocks: Sequence[Block], question: str) -> list[Block]:
    """The same stable prefix, asking something else.

    **S-8.2's cascade re-renders its question on every attempt**, feeding the
    previous rejection back in — *a retry told what was wrong is a correction, a
    retry at a higher temperature is a dice roll* (ADR 085). `Session.run`
    renders the blocks once, from the first attempt's question, so a caller that
    sent them unchanged on attempt two would send the question that was already
    rejected and lose the correction entirely.

    Replacing only the question is also what makes the retry cheap: the prefix is
    byte-identical to the attempt before it, so the second call reads the cache
    the first one wrote. The dearest step in the system is the one that repeats.

    Raises:
        RequestError: these blocks have no question to replace, which means they
            did not come from `Prompt.render` and the caller is holding something
            other than a rendered prompt.
    """
    replaced = [block for block in blocks if block.segment is not Segment.QUESTION]
    if len(replaced) == len(blocks):
        message = (
            "these blocks carry no question, so there is nothing to replace. `Prompt.render` "
            "always emits one — a caller with blocks that do not is holding something other "
            "than a rendered prompt"
        )
        raise RequestError(message)
    replaced.append(Block(Segment.QUESTION, question, breakpoint=False))
    return replaced


def text_of(blocks: Sequence[Block]) -> str:
    """Everything the blocks say, in order, as one string.

    For the callers that still need a flat prompt — the token measurement, and
    anything reporting what was asked. Not for building a request: a flat string
    carries no breakpoint, which is the whole thing this module exists to add.
    """
    return "\n\n".join(block.text for block in blocks if block.text.strip())

"""The request carries its cache breakpoints. **S-17.16.**

Epic 5 built a five-segment prompt with a breakpoint at the end of each of the
four stable ones, `Session.run` rendered it on every call, and **nothing ever
sent it**: each agent built its own flat `messages=[{"role": "user", "content":
question}]`, so no `cache_control` reached the API and `04-cost.md` §12.3's
engineered figure was never achievable. Two composition checks recorded that and
neither fixed it.

The fix is not *forward the blocks*, and this module is mostly about why. The
agents rendered **monolithic questions containing the prefix** — `design` wrote
`SOURCE UNDER SUSPICION\n{source}` and the whole experiment log into its
question, while the blocks beside it carried the same two things. Forwarding the
blocks alone would have sent both copies and paid full price for the one that was
supposed to be free. So the content moved out of the questions, and what is
asserted here is that it moved rather than multiplied.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coldfix.audit.invocation import audit_messages
from coldfix.cost.accounting import Agent, ExchangeRate, Phase, TokenUsage
from coldfix.cost.context import Block, Segment, labelled
from coldfix.cost.pruning import PrunedLog
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step
from coldfix.diagnosis import hypothesis as hypothesis_module
from coldfix.llm.client import request_digest
from coldfix.llm.request import RequestError, as_request, text_of, with_question
from coldfix.primitives.registry import ProjectProfile, Selection

SOURCE = "shop/views.py::ListView.list_books"
PLAYBOOK = "Django: count queries with force_debug_cursor."
SYSTEM = "You find performance problems by running experiments."

SRC = Path(__file__).resolve().parents[2] / "src" / "coldfix"

LABELLED_SOURCE = labelled(Segment.SOURCE, SOURCE)
LOG_TEXT = labelled(Segment.LOG, PrunedLog().render())
"""What the source and log blocks say once `render` has labelled them. Built from
the same helper the prompt uses, so a changed header cannot leave this test
asserting a string nothing produces."""


def a_session() -> Session:
    return Session(
        system=SYSTEM,
        playbook=PLAYBOOK,
        source=SOURCE,
        rate=ExchangeRate(Decimal("0.92"), date(2026, 8, 29)),
    )


def rendered(question: str = "What is the next hypothesis worth testing?") -> Sequence[Block]:
    return a_session().prompt_for("claude-opus-5").render(question)


def blocks_of(messages: Any) -> list[dict[str, Any]]:
    """Every content block in a request.

    There is no system half to add: `as_request` never shapes `Segment.SYSTEM`,
    because the system prompt a call sends is the calling module's `_SYSTEM` and
    not the session's string. See `llm/request.py`.
    """
    shaped: list[dict[str, Any]] = []
    for message in messages:
        shaped.extend(message["content"])
    return shaped


# ============================================ AC 1: the request carries breakpoints


def test_the_three_cacheable_segments_each_end_in_a_breakpoint() -> None:
    """AC 1, at three rather than four. **A breakpoint marks the end of a
    cacheable prefix**, so it belongs on the last block of that prefix rather
    than on the first block after it — the API caches everything up to and
    including a marked block, which is why `Segment`'s order is, as `context.py`
    puts it, the whole technique.

    **Three, because the system prompt is the caller's and is sent as a plain
    string.** The first version of this story shaped `Segment.SYSTEM` into the
    request, which reads as obviously right and would have replaced two of the
    three Diagnostician prompts — `orchestrator/adapters.py` runs the whole
    investigate loop on one session, whose system string is `hypothesis._SYSTEM`.
    The breakpoint forgone buys the system text no caching; the playbook, the
    source and the log are the part that grows.
    """
    messages = as_request(rendered())

    marked = [block for block in blocks_of(messages) if "cache_control" in block]

    assert [block["text"] for block in marked] == [PLAYBOOK, LABELLED_SOURCE, LOG_TEXT], (
        f"playbook, source and log carry the breakpoints; got {marked}"
    )


def test_the_question_never_carries_a_breakpoint() -> None:
    """Caching the question writes an entry no later call can read — it is the
    part that varies — and spends one of only three breakpoints doing it."""
    messages = as_request(rendered("a question nobody will ask twice"))

    asked = [
        block
        for block in blocks_of(messages)
        if "a question nobody will ask twice" in block["text"]
    ]

    assert asked, "the question reached the request"
    assert all("cache_control" not in block for block in asked), (
        f"the question was given a breakpoint: {asked}"
    )


def test_the_session_system_string_never_reaches_the_request() -> None:
    """**The regression this story nearly shipped, as a test.**

    `orchestrator/adapters.py:492` opens one session for the whole investigate
    loop with `_INVESTIGATION_PROMPT = hypothesis._SYSTEM`, and all three
    Diagnostician steps run on it. While each agent sends its own `_SYSTEM` that
    is a billing mismatch — what `repair/sessions.refuse_foreign_session` exists
    for, and which was never applied to the Diagnostician. The moment the
    session's string becomes what is *sent*, `design` and `interpret` are handed
    the hypothesis prompt: told to answer with a statement, a primitive and a
    rationale when they owe a specification and a verdict.

    No agent test would catch it, because the fixtures build their recordings
    from the same session. So it is caught here, where the shaping happens.
    """
    messages = as_request(rendered())

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert all(SYSTEM not in block["text"] for block in blocks_of(messages)), (
        "the session's system string was shaped into the request"
    )


def test_a_session_never_produces_an_empty_block_to_drop() -> None:
    """Checked rather than assumed, and the answer was not the obvious one.

    The empty-block skip reads like it exists for the log, which is the one
    segment that legitimately starts with nothing in it. It does not: an empty
    `PrunedLog` still renders S-5.8's retrieval notice, so its block carries text
    from the first call. Together with the three blank segments `Investigation`
    refuses and the blank question `render` refuses, **no block a session builds
    is ever empty** — so the skip guards a hand-assembled list, which is the next
    test, and this one pins down that it is not guarding this path.
    """
    session = a_session()
    assert session.log.render().strip(), "an empty log still renders its retrieval notice"

    messages = as_request(session.prompt_for("claude-opus-5").render("ask"))

    shaped = blocks_of(messages)
    assert len(shaped) == len(Segment) - 1, (
        f"every segment but the skipped system survived; got {len(shaped)}"
    )
    assert all(block["text"].strip() for block in shaped)


def test_a_hand_assembled_block_with_no_text_is_dropped() -> None:
    """What the skip is actually for. `as_request` takes any `Sequence[Block]`,
    so a caller that is not `Prompt.render` can hand it one with nothing in it,
    and the API would refuse the whole request over a block that says nothing."""
    messages = as_request(
        [
            Block(Segment.SYSTEM, SYSTEM, breakpoint=True),
            Block(Segment.PLAYBOOK, "   ", breakpoint=True),
            Block(Segment.QUESTION, "ask", breakpoint=False),
        ]
    )

    assert [block["text"] for block in blocks_of(messages)] == ["ask"]


def test_a_prompt_with_nothing_in_it_is_refused_rather_than_sent_cheaply() -> None:
    """A request whose every block was empty is not a short prompt, it is an
    absent one, and the reply to it would be the model answering a question
    nobody put."""
    with pytest.raises(RequestError, match="every block of this prompt was empty"):
        as_request([Block(Segment.QUESTION, "   ", breakpoint=False)])


def test_the_session_hands_the_rendered_blocks_to_the_call() -> None:
    """AC 1's other half, and the defect this story is named for.

    `Session.run` computed `blocks = prompt.render(question)` and **never used
    them**. The value was rendered on every call, for every agent, and went
    nowhere — which is why a caller could not put a breakpoint on a prompt it did
    not have.
    """
    session = a_session()
    seen: list[Sequence[Block]] = []

    def call(model: str, blocks: Sequence[Block]) -> tuple[str, TokenUsage]:
        seen.append(blocks)
        return "ok", TokenUsage(input_tokens=100, output_tokens=10)

    session.run(
        Step(
            step_type=StepType.HYPOTHESIS_GENERATION,
            phase=Phase.INVESTIGATE,
            agent=Agent.DIAGNOSTICIAN,
            max_output_tokens=100,
        ),
        question="what next?",
        measured_prefix_tokens=2048,
        measured_prompt_tokens=4096,
        call=call,
    )

    assert len(seen) == 1
    assert [block.segment for block in seen[0]] == list(Segment), (
        "the call was handed every segment, in render order"
    )


# ================================ AC 2: a stable segment appears once, not twice


def test_the_source_appears_in_the_request_exactly_once() -> None:
    """AC 2, and the finding that made this story more than a one-line fix.

    Before S-17.16 the source was rendered into the agent's question *and* into
    the cached block beside it. Both went over the wire on every call, so the
    dominant cost variable in `04-cost.md` §12.2 was being paid twice on the
    half that was supposed to be free.
    """
    question = hypothesis_module.render_question(
        exclusions=(), instruments=Selection(profile=ProjectProfile(), available=(), withheld=())
    )
    messages = as_request(a_session().prompt_for("claude-opus-5").render(question))

    whole = text_of(
        [Block(Segment.QUESTION, block["text"], False) for block in blocks_of(messages)]
    )

    assert whole.count(SOURCE) == 1, f"the source is in the request {whole.count(SOURCE)} times"


def test_the_log_appears_in_the_request_exactly_once() -> None:
    """The same defect on the segment that actually grows. A duplicated source is
    one line paid twice; a duplicated log is the whole investigation paid twice,
    and it gets worse with every experiment."""
    session = a_session()
    session.log_experiment(
        primitive="scaling.volume",
        target="shop.books.list",
        outcome="queries flat at 2 across a 4x sweep",
        detail="db.query held at 2.0 from n=10 to n=40",
    )
    question = hypothesis_module.render_question(
        exclusions=(), instruments=Selection(profile=ProjectProfile(), available=(), withheld=())
    )

    messages = as_request(session.prompt_for("claude-opus-5").render(question))
    whole = "\n".join(block["text"] for block in blocks_of(messages))

    assert whole.count("queries flat at 2 across a 4x sweep") == 1


def test_no_agent_renders_a_stable_segment_into_its_question() -> None:
    """AC 2 as an absence, read off the source rather than off one rendered call.

    A test that drives one agent proves it about that agent. The way this defect
    arrived is that four agents each wrote their own header above their own copy
    of the source, so the check that matters is over every `render_question` in
    the tree — including one added tomorrow by somebody who copied an older
    module before this story landed.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "render_question"):
                continue
            taken = {argument.arg for argument in node.args.args + node.args.kwonlyargs}
            named = taken & {"source", "log"}
            if named:
                offenders.append(f"{path.relative_to(SRC)}: {sorted(named)}")

    assert not offenders, (
        "these questions still take a segment the session already caches, so it is "
        f"sent twice: {offenders}"
    )


# =========================== AC 3: a breakpoint changes the cost, not the question


def test_two_requests_differing_only_in_breakpoints_are_one_recording() -> None:
    """AC 3. The API returns the same answer to the same text whether or not a
    prefix of it was served from a cache, so a breakpoint is not part of what a
    request *asks*. Were it part of the digest, every recording in the suite
    would have been invalidated by a change that cannot alter a reply."""
    messages = as_request(rendered())
    bare_messages: Any = [
        {
            "role": message["role"],
            "content": [
                {key: value for key, value in block.items() if key != "cache_control"}
                for block in message["content"]
            ],
        }
        for message in messages
    ]

    assert any("cache_control" in block for block in blocks_of(messages)), (
        "the marked request really does carry breakpoints"
    )
    assert request_digest(
        model="claude-opus-5",
        system=SYSTEM,
        messages=messages,
        max_tokens=1_000,
        temperature=0.8,
    ) == request_digest(
        model="claude-opus-5",
        system=SYSTEM,
        messages=bare_messages,
        max_tokens=1_000,
        temperature=0.8,
    )


def test_moving_content_out_of_the_question_does_change_the_digest() -> None:
    """The other half of AC 3, and the half that keeps it honest. Stripping cache
    metadata must not become stripping content: a prompt that says the same thing
    in a different block is still a different prompt, and a recording made for
    one must not answer the other."""
    blocks = rendered("ask")
    folded = [
        Block(Segment.QUESTION, f"{SOURCE}\n\nask", breakpoint=False)
        if block.segment is Segment.QUESTION
        else block
        for block in blocks
    ]

    first_messages = as_request(blocks)
    second_messages = as_request(folded)

    assert request_digest(
        model="claude-opus-5",
        system=SYSTEM,
        messages=first_messages,
        max_tokens=1_000,
        temperature=0.8,
    ) != request_digest(
        model="claude-opus-5",
        system=SYSTEM,
        messages=second_messages,
        max_tokens=1_000,
        temperature=0.8,
    )


# ===================================== the retry keeps its correction and its cache


def test_a_retry_replaces_the_question_and_leaves_the_prefix_byte_identical() -> None:
    """ADR 085 through `with_question`, and the trap a mechanical migration falls into.

    `design`, `explain` and `interpret` re-render their question on every attempt
    with the previous rejection fed back — *a retry told what was wrong is a
    correction, a retry at a higher temperature is a dice roll*. `Session.run`
    renders the blocks once, from the **first** attempt's question, so a caller
    that forwarded them unchanged on attempt two would send the question that was
    already rejected and lose the correction entirely.
    """
    first = rendered("Specify the experiment.")
    second = with_question(first, "Specify the experiment.\n\nYOUR EARLIER ANSWER WAS REJECTED")

    assert [block.segment for block in second] == [block.segment for block in first]
    assert second[-1].text.endswith("REJECTED"), "the correction reached the model"
    assert [block.text for block in second[:-1]] == [block.text for block in first[:-1]], (
        "the prefix is byte-identical, so attempt two reads the cache attempt one wrote"
    )


def test_blocks_that_carry_no_question_are_refused() -> None:
    """`Prompt.render` always emits one — a caller with blocks that do not is
    holding something other than a rendered prompt."""
    with pytest.raises(RequestError, match="carry no question"):
        with_question([Block(Segment.SOURCE, SOURCE, breakpoint=True)], "ask")


# ============================================================== the partition

BLOCK_SHAPED = {
    "diagnosis/design.py",
    "diagnosis/explain.py",
    "diagnosis/hypothesis.py",
    "diagnosis/interpretation.py",
    "explorer/proposal.py",
    "repair/falsification.py",
    "repair/patch.py",
}
"""The call sites that shape their request from the session's cached blocks."""

OWN_MESSAGE_LIST = {
    "audit/invocation.py",
    "audit/patchaudit.py",
    "audit/testquality.py",
    "repair/testaudit.py",
}
"""The adversarial call sites that build their own. `CLAUDE.md`: *the Adversary
never sees the Surgeon's reasoning — enforced by constructing a fresh message
list, not by instructing the model to ignore it.*"""


def test_every_call_site_is_on_one_side_of_the_partition_or_the_other() -> None:
    """Both halves listed, because the dangerous direction is drift into the
    permissive one.

    An audit call site that quietly started shaping its request from the
    session's blocks would be handed a prompt assembled somewhere else, and the
    isolation `CLAUDE.md` calls non-negotiable would become a property of
    whatever that somewhere else happened to render. It would also still pass
    every audit test, because a fresh message list and a rendered prefix carry
    the same words.
    """
    shaping: set[str] = set()
    building: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "def call(model" not in text:
            continue
        name = path.relative_to(SRC).as_posix()
        if "as_request(" in text:
            shaping.add(name)
        if "audit_messages(" in text and "def audit_messages" not in text.split("import")[0]:
            building.add(name)

    assert shaping == BLOCK_SHAPED, f"the block-shaped half moved: {shaping ^ BLOCK_SHAPED}"
    assert building == OWN_MESSAGE_LIST, (
        f"the adversarial half moved: {building ^ OWN_MESSAGE_LIST}"
    )
    assert not (shaping & building), (
        f"a call site is on both sides of the partition: {shaping & building}"
    )


def test_an_adversarial_call_site_cannot_be_handed_the_sessions_prompt() -> None:
    """The violation, attempted.

    `audit_messages` returns a list built from its two arguments and nothing
    else. There is no parameter through which a rendered prefix could arrive, so
    the isolation is a shape rather than a discipline — which is what makes it
    survive somebody editing the call site without reading this test.
    """
    built = audit_messages("evidence", "question")

    assert built == [{"role": "user", "content": "evidence\n\nquestion"}]
    assert json.dumps(built).count("cache_control") == 0


# ================================================ AC 4: the cache is now readable


def test_consecutive_calls_send_a_prefix_the_second_can_read() -> None:
    """AC 4, proved as the property the cache actually keys on.

    A cache hit is a byte-prefix match: call N+1 reads what call N wrote when
    everything up to a breakpoint is identical. Under a replaying client
    `warm_hit_rate()` can only report the figure the *recording* carries, which
    would make an assertion on it a statement about the fixture — so what is
    asserted here is the thing that makes a hit possible, and the rate itself is
    left for the run S-17.3 will do against a real API.

    The log grows between the two calls, which is correct and is Epic 5's whole
    design: the prefix is append-only, so everything before the last write still
    matches.
    """
    session = a_session()
    first = as_request(session.prompt_for("claude-opus-5").render("what next?"))

    session.log_experiment(
        primitive="scaling.volume",
        target="shop.books.list",
        outcome="queries flat at 2 across a 4x sweep",
        detail="db.query held at 2.0 from n=10 to n=40",
    )
    second = as_request(session.prompt_for("claude-opus-5").render("and now?"))

    def cacheable(shaped: Any) -> str:
        upto = []
        for block in blocks_of(shaped):
            upto.append(block["text"])
            if "cache_control" not in block:
                break
        return "\n".join(upto[:-1])

    assert cacheable(first), "there is a cacheable prefix at all"
    assert cacheable(second).startswith(cacheable(first)), (
        "the second call's prefix does not extend the first's, so nothing it wrote can be read"
    )


def test_the_warm_rate_reports_what_the_api_said_and_not_an_estimate() -> None:
    """`warm_hit_rate` is fed by the API's own `cache_read_input_tokens` —
    `CLAUDE.md` forbids an agent reporting a measurement and a token count is
    one. It stays `None` below two calls, because the first can never hit."""
    prompt = a_session().prompt_for("claude-opus-5")

    assert prompt.warm_hit_rate() is None

    prompt.record(TokenUsage(input_tokens=4_000, output_tokens=80))
    assert prompt.warm_hit_rate() is None, "one call is not a warm call"

    prompt.record(TokenUsage(input_tokens=200, output_tokens=80, cache_read_input_tokens=3_800))
    warm = prompt.warm_hit_rate()
    assert warm is not None
    assert warm > 0


# =============================================== text_of, for the callers that flatten


def test_flattening_drops_the_breakpoints_and_says_so() -> None:
    """`text_of` is for the token measurement and for reporting what was asked.
    Not for building a request: a flat string carries no breakpoint, which is the
    whole thing this module exists to add."""
    flat = text_of(rendered("ask"))

    assert SOURCE in flat
    assert flat.endswith("ask")
    assert "cache_control" not in flat

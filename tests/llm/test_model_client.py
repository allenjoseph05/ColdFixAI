"""S-0.7b — the model seam, and the double that replays instead of calling.

No test here reaches the network, and none of them could: the replaying client
holds no vendor client. What they do exercise is the vendor's real response
model, because a recording is validated by it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import MessageParam

from coldfix.cost.accounting import (
    Agent,
    ExchangeRate,
    Phase,
    StepClass,
    TokenUsage,
)
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session, Step
from coldfix.llm.client import (
    ModelClientError,
    ModelResponse,
    NoRecordingError,
    Recording,
    ReplayingClient,
    request_digest,
    translate,
)

MESSAGES: Sequence[MessageParam] = [{"role": "user", "content": "What does the growth table show?"}]
TEMPERATURE = 0.0
"""What these tests send unless they are about temperature."""

SYSTEM = "You find performance problems by running experiments."


def payload(
    *,
    text: str = "Queries grow linearly with N: an N+1 on author.",
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5",
    usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """A real API response, in the shape the SDK validates."""
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_01ABC",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": dict(
            usage
            or {
                "input_tokens": 1_200,
                "output_tokens": 340,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 10_000,
            }
        ),
    }


def recording(**overrides: Any) -> Recording:
    """The requested model and the answering model are set together here.

    They are genuinely two fields — the response reports which model actually
    answered, which is not always the one asked for — but a helper that let them
    drift silently would make every assertion about either of them meaningless.
    """
    model = overrides.pop("model", "claude-sonnet-5")
    return Recording.of(
        model=model,
        system=overrides.pop("system", SYSTEM),
        messages=overrides.pop("messages", MESSAGES),
        max_tokens=overrides.pop("max_tokens", 1_000),
        temperature=overrides.pop("temperature", TEMPERATURE),
        response=payload(model=model, **overrides),
    )


# ============================================ a recording is a real API response


def test_a_payload_the_sdk_rejects_cannot_be_recorded() -> None:
    """The property that stops this double being more forgiving than the API.

    A recording the vendor's own model rejects would make every test against it
    a test of a fiction.
    """
    with pytest.raises(ModelClientError, match="not a response the API could have returned"):
        Recording.of(
            model="claude-sonnet-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=1_000,
            temperature=TEMPERATURE,
            response={"role": "assistant", "content": "not a list of blocks"},
        )


def test_a_real_payload_records() -> None:
    """The control: the guard above must not be refusing everything."""
    assert recording().message.model == "claude-sonnet-5"


# ============================================================= the translation


def test_the_usage_becomes_the_ledger_s_own_shape() -> None:
    """S-5.3 refuses to collapse the two cache figures, and this is where the
    vendor's four numbers become that shape."""
    reply = translate(recording().message)

    assert reply.usage == TokenUsage(
        input_tokens=1_200,
        output_tokens=340,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=10_000,
    )
    assert reply.usage.prompt_tokens == 11_200


def test_the_cache_ttl_comes_from_the_caller_not_the_response() -> None:
    """A write bills 1.25x at five minutes and 2x at an hour, and the response
    does not say which was asked for — the request did."""
    assert translate(recording().message, cache_ttl="1h").usage.cache_ttl == "1h"


def test_text_is_not_taken_from_the_first_content_block() -> None:
    """A refusal carries an **empty** content list, and extended thinking puts a
    non-text block first. `content[0].text` breaks on both."""
    refused = recording(text="", stop_reason="refusal")

    reply = translate(refused.message)

    assert reply.text == ""
    assert reply.refused


def test_text_survives_a_non_text_block_arriving_first() -> None:
    """The other half of the same claim, and the half an empty refusal cannot test.

    Adaptive thinking puts a `thinking` block ahead of the answer, so
    `content[0].text` returns nothing on a perfectly good response. The refusal
    case alone did not catch this — an empty content list behaves identically
    under both readings. Found by sabotage.
    """
    with_thinking = Recording.of(
        model="claude-opus-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
        response={
            **payload(model="claude-opus-5"),
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Let me check the growth table.",
                    "signature": "s",
                },
                {"type": "text", "text": "An N+1 on author."},
            ],
        },
    )

    assert translate(with_thinking.message).text == "An N+1 on author."


def test_a_cache_write_is_not_collapsed_into_the_read() -> None:
    """S-5.3's whole finding: the two bill in opposite directions, a write at
    1.25x and a read at 0.1x, so losing the write under-bills the first call of
    every investigation. The default fixture writes nothing, which made an
    earlier version of this file blind to it — found by sabotage.
    """
    cold = recording(
        usage={
            "input_tokens": 200,
            "output_tokens": 340,
            "cache_creation_input_tokens": 11_000,
            "cache_read_input_tokens": 0,
        }
    )

    usage = translate(cold.message).usage

    assert usage.cache_creation_input_tokens == 11_000
    assert usage.cache_read_input_tokens == 0
    assert usage.prompt_tokens == 11_200


def test_a_refusal_is_replayable_and_says_so() -> None:
    """A double that always answers `end_turn` hides the decline until
    production. The API returns HTTP 200 on one."""
    client = ReplayingClient([recording(text="", stop_reason="refusal")])

    reply = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
    )

    assert reply.refused
    assert not reply.truncated
    assert reply.text == ""


def test_a_truncated_answer_is_distinguishable_from_a_finished_one() -> None:
    client = ReplayingClient([recording(stop_reason="max_tokens")])

    reply = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
    )

    assert reply.truncated
    assert not reply.refused


def test_an_ordinary_answer_is_neither() -> None:
    """The control for the two above."""
    reply = translate(recording().message)

    assert not reply.refused
    assert not reply.truncated
    assert reply.text.startswith("Queries grow linearly")


# ======================================== an unrecorded request is never answered


def test_an_unrecorded_request_is_refused() -> None:
    """A default here would make every agent test pass while testing the default."""
    client = ReplayingClient([recording()])

    with pytest.raises(NoRecordingError, match="no recording for this request"):
        client.complete(
            model="claude-sonnet-5",
            system=SYSTEM,
            messages=[{"role": "user", "content": "a different question"}],
            max_tokens=1_000,
            temperature=TEMPERATURE,
        )


def test_a_recording_for_one_model_does_not_serve_another() -> None:
    """S-5.5 routes the same step to different tiers, which is exactly when this
    would happen — and the two answer differently and bill differently."""
    client = ReplayingClient([recording(model="claude-sonnet-5")])

    with pytest.raises(NoRecordingError):
        client.complete(
            model="claude-haiku-4-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=1_000,
            temperature=TEMPERATURE,
        )


def test_the_refusal_lists_what_is_recorded() -> None:
    """Four things look identical from the call site, so the message names them."""
    client = ReplayingClient([recording()])

    with pytest.raises(NoRecordingError) as raised:
        client.complete(
            model="claude-opus-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=1_000,
            temperature=TEMPERATURE,
        )

    assert "Recorded:" in str(raised.value)
    assert "a recording never made" in str(raised.value)


def test_an_empty_client_refuses_everything_by_name() -> None:
    with pytest.raises(NoRecordingError, match="Recorded: none"):
        ReplayingClient().complete(
            model="claude-opus-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=1_000,
            temperature=TEMPERATURE,
        )


def test_temperature_is_part_of_the_request_identity() -> None:
    """Added at S-8.1, for the reason the model is part of it.

    `03-agents.md` §2.4 sends the Diagnostician's two calls at 0.8 and 0.0 —
    *hypothesis generation benefits from diversity; result interpretation must
    not vary* — and those are frequently the **same question about the same
    log**. Without the temperature in the digest, the recording made for the call
    that must not vary would answer the call that is supposed to, and nothing
    would fail.
    """
    client = ReplayingClient([recording(temperature=0.0)])

    with pytest.raises(NoRecordingError, match="a different temperature"):
        client.complete(
            model="claude-sonnet-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=1_000,
            temperature=0.8,
        )


def test_the_same_request_at_the_same_temperature_replays() -> None:
    """The control. A digest that separated every request would pass the test
    above and serve nothing."""
    client = ReplayingClient([recording(temperature=0.8)])

    reply = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=0.8,
    )

    assert reply.text


def test_max_tokens_is_part_of_the_request_identity() -> None:
    """The same prompt asked with a different ceiling is a different request:
    it can return a different answer and it is authorized against a different
    worst case (S-5.4)."""
    client = ReplayingClient([recording(max_tokens=1_000)])

    with pytest.raises(NoRecordingError):
        client.complete(
            model="claude-sonnet-5",
            system=SYSTEM,
            messages=MESSAGES,
            max_tokens=4_000,
            temperature=TEMPERATURE,
        )


def test_the_same_request_replays() -> None:
    client = ReplayingClient([recording()])

    first = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
    )
    second = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
    )

    assert first == second
    assert len(client.served) == 2


# ============================================== it cannot reach the network


def test_the_replaying_client_holds_no_vendor_client() -> None:
    """*No test hits a real API* as a structural property rather than a rule:
    there is nothing here to call with."""
    client = ReplayingClient([recording()])

    held = list(vars(client).values())

    assert not any(type(value).__module__.startswith("anthropic") for value in held)
    assert not hasattr(client, "client")


def test_recordings_load_from_a_directory(tmp_path: Path) -> None:
    stored = recording()
    (tmp_path / f"{stored.digest}.json").write_text(
        json.dumps({"response": stored.message.model_dump(mode="json")}), encoding="utf-8"
    )

    client = ReplayingClient.from_directory(tmp_path)

    reply = client.complete(
        model="claude-sonnet-5",
        system=SYSTEM,
        messages=MESSAGES,
        max_tokens=1_000,
        temperature=TEMPERATURE,
    )
    assert reply.text.startswith("Queries grow linearly")


def test_the_digest_is_stable_across_processes() -> None:
    """A recording is found by digest, so an unstable one is a store that never
    hits — S-5.1's finding, in a second place."""
    once = request_digest(
        model="m", system="s", messages=MESSAGES, max_tokens=10, temperature=TEMPERATURE
    )
    again = request_digest(
        model="m", system="s", messages=MESSAGES, max_tokens=10, temperature=TEMPERATURE
    )

    assert once == again


# ==================================== Epic 5's first real caller


def test_a_replayed_call_prices_through_the_ledger() -> None:
    """Epic 5 built routing, budgets, a ledger and a cost report with no caller.

    This is the seam it was built for: `Session.run` takes a callable handed a
    model id and returning a result with its usage, which is exactly what a
    completion is.
    """
    client = ReplayingClient([recording()])
    session = Session(
        system="You find performance problems by running experiments.",
        playbook="Django: count queries with force_debug_cursor.",
        source="def list_books(): ...",
        rate=ExchangeRate(Decimal("0.90"), date(2026, 8, 11)),
    )

    def call(model: str) -> tuple[str, TokenUsage]:
        reply = client.complete(
            model=model, system=SYSTEM, messages=MESSAGES, max_tokens=1_000, temperature=TEMPERATURE
        )
        return reply.text, reply.usage

    outcome = session.run(
        Step(
            step_type=StepType.EVIDENCE_CHAIN,
            phase=Phase.INVESTIGATE,
            agent=Agent.DIAGNOSTICIAN,
            max_output_tokens=1_000,
            finding_id="F1",
        ),
        question="What does the growth table show?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=call,
    )

    assert outcome.routed_model == "claude-sonnet-5"
    assert outcome.value.startswith("Queries grow linearly")
    assert outcome.cost_usd > 0
    assert outcome.calls[0].step_class is StepClass.MECHANICAL
    assert "euros per confirmed finding" in session.report(confirmed_findings=1)


def test_a_response_carries_its_own_model_back() -> None:
    """The routed model and the answering model must be checkable against each
    other — S-5.5's tiers are only meaningful if the answer came from the tier."""
    reply: ModelResponse = translate(recording(model="claude-haiku-4-5").message)

    assert reply.model == "claude-haiku-4-5"

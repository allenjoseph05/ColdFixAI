"""One model call, and a double that replays a recorded one instead of making it.

S-0.7b. `docs/10-BACKLOG.md` deferred this with a precise reason: *the SDK and
provider strategy are undecided, and writing a mock against a guessed interface
is the speculative abstraction `CLAUDE.md` forbids.* Its dependencies — S-0.2,
E1, S-4.1 — are all done, so the guess is no longer necessary and the mock is
built against the real thing.

**A recording is a real API response, validated by the vendor's own model.** The
store holds the JSON an `anthropic.types.Message` parses, and loading one runs it
through that model — so a recording that is not something the API could have
returned fails to load rather than being replayed. This is the project's own
lesson applied to its most-used double: *a test double more forgiving than the
real thing turns a structural assertion into a decoration.*

**Both clients share one translation.** `translate` is the only place an API
response becomes an artifact of this system, so a test that passes against the
replaying client exercised the same parsing the real one uses. A double with its
own translation would be testing the double.

**An unrecorded request is refused, never answered.** A mock that returns a
plausible default is the most dangerous kind: every agent test would pass, and
what they would be testing is the default. The refusal lists what *is* recorded,
because the four things that could be wrong — different model, different prompt,
recording never made, recording made under another key — look identical from the
call site.

**`stop_reason` is carried, and `text` is never taken from `content[0]`.** The
API can decline a request: HTTP 200, `stop_reason: "refusal"`, and an **empty**
content list. Code that indexes the first block breaks on it, and a double that
always answers `end_turn` hides that failure until production. A refusal is
replayable here, and it yields empty text with `refused` set rather than an
exception or an invented answer.

**The TTL comes from the caller, not the response.** S-5.3 records it per call
because a cache write bills 1.25x at five minutes and 2x at an hour, and the
response does not say which was asked for — the request did.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from anthropic.types import Message, MessageParam

from coldfix.cost.accounting import TokenUsage

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from anthropic import Anthropic

# `stop_reason` values this system has to behave differently for. The rest —
# `end_turn`, `stop_sequence`, `tool_use`, `pause_turn` — are ordinary outcomes.
REFUSAL = "refusal"
TRUNCATED = "max_tokens"


class ModelClientError(Exception):
    """A model call could not be made, or a recorded one could not be replayed."""


class NoRecordingError(ModelClientError):
    """This request has no recording, and nothing here will invent one.

    The failure a forgiving double hides. If an unrecorded request returned a
    default, every agent test would pass and all of them would be testing the
    default rather than the agent.
    """


@dataclass(frozen=True)
class ModelResponse:
    """What one call returned, in the terms this system accounts in.

    `usage` is S-5.3's `TokenUsage` rather than the vendor's, because that is
    what the ledger prices and what refuses to collapse the two cache figures
    into one.
    """

    model: str
    text: str
    usage: TokenUsage
    stop_reason: str

    @property
    def refused(self) -> bool:
        """Whether the model declined. Check before reading `text`.

        A refusal is a successful HTTP response with empty content, so a caller
        that treats an empty answer as a short answer is reading a decline as a
        result.
        """
        return self.stop_reason == REFUSAL

    @property
    def truncated(self) -> bool:
        """Whether the answer was cut off at `max_tokens` rather than finished."""
        return self.stop_reason == TRUNCATED


def translate(message: Message, *, cache_ttl: str = "5m") -> ModelResponse:
    """Turn a vendor response into this system's artifact. The only such place.

    `text` is the concatenation of the text blocks and is **never**
    `content[0].text`: a refusal carries an empty content list, and extended
    thinking puts a non-text block first.
    """
    text = "".join(block.text for block in message.content if block.type == "text")
    usage = message.usage
    return ModelResponse(
        model=message.model,
        text=text,
        usage=TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_ttl=cache_ttl,
        ),
        stop_reason=message.stop_reason or "end_turn",
    )


def _asked(value: object) -> object:
    """`value` with every `cache_control` removed, however deeply it is nested.

    Recursive rather than a top-level pop, because a breakpoint sits on a content
    block inside a message's content list — two levels down — and a shallow strip
    would leave exactly the ones that are actually used.
    """
    if isinstance(value, dict):
        return {key: _asked(inner) for key, inner in value.items() if key != "cache_control"}
    if isinstance(value, list):
        return [_asked(item) for item in value]
    return value


def request_digest(
    *,
    model: str,
    system: str,
    messages: Sequence[MessageParam],
    max_tokens: int,
    temperature: float,
) -> str:
    """What identifies a request, for finding its recording.

    **The model is part of it.** Two models answer the same prompt differently
    and bill differently, so a recording made against one must never serve the
    other — S-5.5 routes the same step to different tiers, which is exactly when
    that would happen.

    **So is the temperature, added at S-8.1 for the same reason.** `03-agents.md`
    §2.4 sends the Diagnostician's two calls at 0.8 and 0.0 — *hypothesis
    generation benefits from diversity; result interpretation must not vary* —
    and those are frequently the **same question about the same log**. Without
    the temperature in the digest, a recording made for the call that must not
    vary would answer the call that is supposed to, and nothing would fail.

    Hashed over a canonical rendering rather than a joined string, for S-5.1's
    reason: any separator that can occur inside a field makes two different
    requests hash alike.

    **`cache_control` is not part of it, and that is a claim rather than a
    convenience.** S-17.16 puts breakpoints on the blocks of a request, and a
    breakpoint changes what the call *costs* rather than what it *asks*: the API
    returns the same answer to the same text whether or not a prefix of it was
    served from a cache. So two requests differing only in their breakpoints
    resolve to one recording — otherwise every recording in the suite would be
    invalidated by a change that cannot alter a reply.

    What is emphatically still part of it is the text. Moving content out of the
    question and into a block changes what is asked and gives a different digest,
    which is correct: that is a different prompt.
    """
    payload = json.dumps(
        {
            "model": model,
            "system": system,
            "messages": _asked(list(messages)),
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(payload.encode()).hexdigest()


class ModelClient(Protocol):
    """What the agents call. Two implementations, which is why it is a protocol."""

    def complete(  # noqa: PLR0913 - a request's identity is what it
        # is: the model, the prompt, the message list, the output cap and the
        # temperature. Bundling them into an object would hide that every one of
        # them changes which recording answers.
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[MessageParam],
        max_tokens: int,
        temperature: float,
        cache_ttl: str = "5m",
    ) -> ModelResponse: ...


@dataclass(frozen=True)
class AnthropicClient:
    """The real one. Thin on purpose: everything interesting is elsewhere.

    Routing is S-5.5's, budgets are S-5.4's, the prompt is S-5.7's and the
    accounting is S-5.3's. What is left here is the call and the translation.
    """

    client: Anthropic

    def complete(  # noqa: PLR0913 - a request's identity is what it
        # is: the model, the prompt, the message list, the output cap and the
        # temperature. Bundling them into an object would hide that every one of
        # them changes which recording answers.
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[MessageParam],
        max_tokens: int,
        temperature: float,
        cache_ttl: str = "5m",
    ) -> ModelResponse:
        message = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=list(messages),
            temperature=temperature,
        )
        return translate(message, cache_ttl=cache_ttl)


@dataclass(frozen=True)
class Recording:
    """One request, and the response the API gave it.

    The response is held as the vendor's own parsed model, so anything stored
    here is something the API could have returned.
    """

    digest: str
    message: Message

    @classmethod
    def of(  # noqa: PLR0913 - the five fields of a request's identity, plus the
        # response it produced. Same reason as `complete`.
        cls,
        *,
        model: str,
        system: str,
        messages: Sequence[MessageParam],
        max_tokens: int,
        temperature: float,
        response: Mapping[str, object],
    ) -> Recording:
        """Build one from a raw API payload, refusing anything the SDK rejects.

        Raises:
            ModelClientError: the payload is not a response the API could have
                returned. Refused here rather than at replay, so a malformed
                recording is a failure to load rather than a wrong answer.
        """
        try:
            parsed = Message.model_validate(response)
        except Exception as error:
            message = (
                f"this is not a response the API could have returned: {error}. A recording that "
                "the vendor's own model rejects would make every test against it a test of a "
                "fiction, which is the one thing a double must not be"
            )
            raise ModelClientError(message) from error

        return cls(
            digest=request_digest(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
            message=parsed,
        )


class ReplayingClient:
    """Answers only from recordings, and cannot reach the network.

    **It holds no vendor client at all**, which is what makes *no test hits a
    real API* structural rather than a rule: there is nothing here to call with.
    """

    def __init__(self, recordings: Sequence[Recording] = ()) -> None:
        self._recordings: dict[str, Message] = {r.digest: r.message for r in recordings}
        self._served: list[str] = []

    @classmethod
    def from_directory(cls, root: Path) -> ReplayingClient:
        """Load recordings written as `<digest>.json` holding an API response."""
        recordings: list[Recording] = []
        for path in sorted(Path(root).glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            recordings.append(
                Recording(digest=path.stem, message=Message.model_validate(payload["response"]))
            )
        return cls(recordings)

    @property
    def served(self) -> tuple[str, ...]:
        """Which recordings were used, so a test can assert what was asked."""
        return tuple(self._served)

    def complete(  # noqa: PLR0913 - a request's identity is what it
        # is: the model, the prompt, the message list, the output cap and the
        # temperature. Bundling them into an object would hide that every one of
        # them changes which recording answers.
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[MessageParam],
        max_tokens: int,
        temperature: float,
        cache_ttl: str = "5m",
    ) -> ModelResponse:
        """Replay this request's recording.

        Raises:
            NoRecordingError: no recording matches. Never a default.
        """
        digest = request_digest(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        recorded = self._recordings.get(digest)
        if recorded is None:
            known = ", ".join(sorted(self._recordings)) or "none"
            message = (
                f"no recording for this request to {model} (digest {digest}). Recorded: {known}. "
                "Nothing here invents a response: a double that answered anyway would make every "
                "agent test pass while testing the default. Four things look identical from here "
                "— a different model, a changed prompt, a recording never made, one made "
                "under a different max_tokens, and one made at a different temperature"
            )
            raise NoRecordingError(message)

        self._served.append(digest)
        return translate(recorded, cache_ttl=cache_ttl)

"""Building the live client. **S-17.1.**

`AnthropicClient` had existed since S-0.7b with no call site anywhere in the
tree — two docstrings mentioned it and nothing constructed one. That is the kind
of gap no test can see from the inside: `ReplayingClient` satisfies the same
protocol, so every suite in the project passes without a live client ever
existing.

The property worth asserting is not *does it return an object*. It is **does
building one cost anything** — because if it did, the refusal in `cli/main.py`
would be guarding the wrong side of the line.
"""

from __future__ import annotations

import pytest

from coldfix.llm.client import AnthropicClient, ModelClient, ModelClientError, connect


def test_it_returns_something_satisfying_the_protocol() -> None:
    """The seam is only a seam if the real implementation fits it.

    Checked structurally rather than with `isinstance`: `ModelClient` is a plain
    `Protocol`, and the thing that matters is that a node holding one can call
    `complete` without knowing which implementation it has.
    """
    client = connect("sk-ant-not-a-real-key")

    assert isinstance(client, AnthropicClient)
    assert callable(client.complete)

    def takes_a_client(_: ModelClient) -> None:
        return None

    takes_a_client(client)  # a type error here is the failure


def test_building_one_opens_no_connection_and_bills_nothing() -> None:
    """The load-bearing one.

    `cli/main.py` refuses a run without `--spend` *before* it builds anything, and
    that ordering is only worth having if building is free. The SDK client is lazy
    — it constructs an `httpx` client and sends nothing — so a key that could not
    possibly authenticate still constructs without error. If this ever starts
    failing on a bad key, the SDK began validating eagerly and the refusal order
    in the CLI has to be revisited.
    """
    client = connect("sk-ant-obviously-invalid-0000000000")

    assert client.client.api_key == "sk-ant-obviously-invalid-0000000000"


@pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
def test_an_empty_key_is_refused_before_anything_is_built(empty: str) -> None:
    """Refused here rather than at the first call.

    A blank `ANTHROPIC_API_KEY` is the ordinary shape of this mistake — the
    variable is set and exported as nothing. Left to the SDK it surfaces at the
    first `complete`, which is after the workbench and the store are open and
    after `choose_reset` has driven ten cycles against Postgres.
    """
    with pytest.raises(ModelClientError, match="empty API key"):
        connect(empty)


def test_the_refusal_names_a_key_that_is_only_whitespace() -> None:
    """The direction that fails silently.

    `if not api_key` would pass a key of three spaces straight through to the SDK,
    because a non-empty string is truthy. The strip is the whole check.
    """
    with pytest.raises(ModelClientError):
        connect(" ")

"""The shared reply reader, tested where its branches actually are.

Written because a sabotage pass on S-8.3 found **two guards no test reached**:
accepting a JSON array as an answer, and raising on malformed JSON instead of
returning a rejection. Both were extracted from S-8.2, both had been sabotaged
through their *callers*, and neither caller's suite ever sent a reply that
reached them — `CLAUDE.md`'s *a guard no test reaches is a guard nobody has
checked*, one module down.

The reason these branches matter is not tidiness. Every sentence here is fed
back to a model as a correction (ADR 085), so a branch that raises instead of
returning removes the retry the cascade exists to provide.
"""

from __future__ import annotations

import json

import pytest

from coldfix.diagnosis.replies import Attempted, read_object


def test_a_plain_object_is_read() -> None:
    read = read_object(json.dumps({"verdict": "confirmed"}))

    assert read.valid
    assert read.value == {"verdict": "confirmed"}
    assert read.rejection == ""


def test_an_object_wrapped_in_prose_is_still_read() -> None:
    """Models asked for JSON return JSON, a fenced block, or JSON with a sentence
    in front of it. Refusing the third would be refusing a correct answer."""
    read = read_object('Here you go:\n```json\n{"a": 1}\n```')

    assert read.value == {"a": 1}


def test_a_reply_with_no_object_is_rejected_with_what_was_said() -> None:
    read = read_object("I think it is the database.")

    assert not read.valid
    assert "no JSON object" in read.rejection
    assert "the database" in read.rejection


def test_malformed_json_is_rejected_rather_than_raised() -> None:
    """**Found by sabotage.** Making this branch raise survived both callers'
    suites, because neither ever sent a reply that looks like an object and is
    not one — a truncated reply has no closing brace, so it takes the *no object*
    path instead and this one was never executed."""
    read = read_object('{"verdict": "confirmed",}')

    assert not read.valid
    assert "not valid JSON" in read.rejection


@pytest.mark.parametrize(
    "reply",
    [
        "[1, 2, 3]",
        "42",
        '"just a string"',
        "true",
        "null",
        "[[1], [2]]",
    ],
)
def test_nothing_that_is_not_an_object_is_ever_read_as_one(reply: str) -> None:
    """**The intent verified from the other side.**

    S-8.2 carried a *that was not an object* rejection and a sabotage pass proved
    it could never fire: the pattern takes text from the first `{` to the last
    `}`, and JSON beginning with `{` that parses at all is an object. Deleting
    the branch changed no behaviour, which is what made it a redundant condition
    rather than a guard.

    So the property is asserted over the inputs that would have reached it: none
    of these is read as an answer, and each is refused for the reason that
    actually applies to it.
    """
    read = read_object(reply)

    assert not read.valid
    assert "no JSON object" in read.rejection


def test_an_object_inside_an_array_is_read_as_the_object() -> None:
    """The control for the sweep above, and the behaviour a reader should know
    about: a model that wraps its answer in an array has still answered, and the
    extraction finds it rather than refusing the wrapper."""
    read = read_object('[{"verdict": "confirmed"}]')

    assert read.value == {"verdict": "confirmed"}


def test_an_attempt_knows_whether_it_carries_a_value() -> None:
    assert Attempted.ok({"a": 1}).valid
    assert not Attempted[dict[str, int]].no("nope").valid
    assert Attempted[dict[str, int]].no("nope").rejection == "nope"

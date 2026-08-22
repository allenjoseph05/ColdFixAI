"""Failure memory, at the two speeds it can be checked at.

S-13.3. The journal is Postgres, so the round trip through it is `postgres` and
`slow`. The **translation** either side of it is neither, and it is where an
attempt is lost: an entry that drops the diff feeds S-10.5's repeat check the one
field F12 says cannot be trusted.
"""

from __future__ import annotations

import pytest

from coldfix.repair.memory import FailureMemoryError, as_entry, from_entry
from coldfix.repair.patch import Attempt, Patch

DIFF = """\
--- a/shop/views.py
+++ b/shop/views.py
@@ -12,1 +12,1 @@
-    books = Book.objects.all()
+    books = Book.objects.select_related("author")
"""


def an_attempt(*, approach: str = "select_related", failure: str = "still 1001 queries") -> Attempt:
    return Attempt(
        patch=Patch(diff=DIFF, approach=approach, rationale="the sweep says so"),
        failure=failure,
    )


def test_an_attempt_round_trips_through_the_journal_shape() -> None:
    restored = from_entry(as_entry(an_attempt()))

    assert restored.patch.diff == DIFF
    assert restored.patch.approach == "select_related"
    assert restored.failure == "still 1001 queries"


def test_the_diff_survives_and_not_only_the_label() -> None:
    """**The whole point of storing the attempt rather than the approach.**

    S-10.4 first showed retries only the previous `approach` strings, which is
    precisely the self-judged field F12 says an agent can rename — and S-10.5's
    repeat check compares diffs. A memory that kept only labels would feed that
    check the one thing it cannot trust.
    """
    entry = as_entry(an_attempt())

    assert isinstance(entry["patch"], dict)
    assert entry["patch"]["diff"] == DIFF, "the evidence, not the name for it"


def test_an_entry_with_no_failure_reason_is_refused_on_the_way_back() -> None:
    """`Attempt` refuses one at construction — *an attempt recorded with no
    failure reason gives the next one nothing to avoid* — and this is that
    refusal reached through the store rather than around it."""
    entry = dict(as_entry(an_attempt()))
    entry["failure"] = "   "

    with pytest.raises(FailureMemoryError, match="not an attempt this system wrote"):
        from_entry(entry)


@pytest.mark.parametrize("missing", ["patch", "failure"])
def test_an_entry_that_is_not_ours_is_named_rather_than_raised_through(missing: str) -> None:
    """A row written by an older shape, or by something else using the same
    collection. The caller's question is *can I trust this memory*, and a
    pydantic error answers a narrower one."""
    entry = {k: v for k, v in as_entry(an_attempt()).items() if k != missing}

    with pytest.raises(FailureMemoryError, match="not an attempt this system wrote"):
        from_entry(entry)


def test_a_patch_that_is_not_a_patch_is_refused() -> None:
    with pytest.raises(FailureMemoryError):
        from_entry({"patch": {"diff": "", "approach": "", "rationale": ""}, "failure": "no"})

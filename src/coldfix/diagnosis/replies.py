"""Reading a reply that a cascade is allowed to reject.

Epic 8. Extracted at S-8.3, the second case — S-8.2 wrote this and S-8.3 needed
the same thing, which is when `CLAUDE.md`'s *no speculative abstraction* stops
applying and its inverse starts.

**The rejection text is the reason to share this, not the parsing.** A rejected
attempt is fed back to the model as a correction (ADR 085), so these sentences
are read by the thing being corrected. Two hand-written versions of *no JSON
object in the reply* would drift, and the one that drifted would be the one a
model was asked to act on.

**S-8.1 deliberately does not use this.** Hypothesis generation *raises* on a
malformed reply where the two mechanical steps *return* a rejection, and that is
the line ADR 085 draws: a wrong answer is retried, an absent answer is raised.
Routing the creative step's refusal path through the cascading steps' helper
would blur exactly the distinction the design rests on, so the third caller is
not a caller.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

# A model asked for JSON returns JSON, a fenced block, or JSON with a sentence in
# front of it. The first balanced object is taken and **nothing is repaired**:
# *the model answered something else* and *the model was wrong* are different
# problems needing different fixes, and a parser that guesses turns the first
# into the second.
_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Attempted[T]:
    """A value read from a reply, or the reason it could not be.

    The cascade's value type. S-5.6 validates what an attempt *returns*, so an
    attempt that raised on a bad answer would end the step instead of earning the
    retry the cascade exists to provide.

    `None` is not a legitimate `T` anywhere this is used, which is what lets
    `valid` be a null check rather than a third field that could disagree with
    the other two.
    """

    value: T | None
    rejection: str

    @property
    def valid(self) -> bool:
        return self.value is not None

    @classmethod
    def ok(cls, value: T) -> Attempted[T]:
        return cls(value, "")

    @classmethod
    def no(cls, rejection: str) -> Attempted[T]:
        return cls(None, rejection)


def read_object(text: str) -> Attempted[Mapping[str, object]]:
    """The one JSON object in a reply, or a sentence saying why there is none.

    Every failure here is the retryable kind, which is why it returns rather than
    raises — and the sentences are written to be corrected against, not merely to
    be logged.
    """
    found = _JSON.search(text)
    if found is None:
        return Attempted.no(f"no JSON object in the reply: {text.strip()[:200]!r}")

    try:
        payload = json.loads(found.group(0))
    except json.JSONDecodeError as error:
        return Attempted.no(f"the reply was not valid JSON: {error}")

    # **There is no *that was not an object* branch, and its absence is the
    # point.** S-8.2 shipped one, and extracting this module found it could never
    # fire: the pattern above takes text from the first `{` to the last `}`, and
    # JSON that starts with `{` and parses at all is an object. So the check was a
    # redundant condition — unverifiable by construction, and reading as
    # protection while protecting nothing, which is the shape S-7.4 recorded and
    # whose remedy is to collapse it and verify the intent from the other side.
    # `test_replies.py` does that: it asserts the invariant over the inputs that
    # would have exercised the branch.
    return Attempted.ok(payload)

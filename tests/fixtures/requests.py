"""Building the request the real path builds, for a recording to answer. **S-17.16.**

`Session.run` renders the prompt into blocks and every non-adversarial agent
shapes them with `as_request`, so a recording made from a flat
`[{"role": "user", "content": question}]` answers a request nothing sends.

**Extracted rather than copied into each suite**, for the reason
`fixtures/thesis.py` gives about the subject it holds: seven test modules need
this and seven copies would drift, with the one that drifted being the one nobody
ran. It is one line of code and the argument for sharing it is not the line — it
is that *how a request is shaped* has to have a single answer, or a recording
made one way and a call made another disagree with no error saying so.

**The system prompt is not shaped here**, and that is the point the story nearly
missed: `as_request` never touches `Segment.SYSTEM`, because a session's system
string and the prompt an agent sends are different things — the investigate loop
runs three steps on one session. A recording's `system` is therefore the calling
module's own `_SYSTEM`, exactly as it was before this story, and every caller
here passes it explicitly.

The four adversarial call sites do not use this at all, and that is the partition
`tests/llm/test_request.py` asserts: `audit_messages` builds their message list,
because `CLAUDE.md` requires the Adversary's isolation to be a construction
rather than a discipline.
"""

from __future__ import annotations

from typing import Any

from coldfix.cost.session import Session
from coldfix.llm.request import as_request


def shaped(session: Session, model: str, question: str) -> list[Any]:
    """The messages the agent would send, for this session's prompt.

    Rendered through the session's own `prompt_for` rather than reconstructed
    from its fields, because a second assembly is a second thing to drift — and
    the thing it would drift from is the one this exists to match.

    **The session's log state is part of the answer.** The log rides in the
    cached blocks, so a recording for call N must be built from a session whose
    log holds what it held at call N. `fixtures.thesis.recording_session` is how
    a walk over a growing log does that.
    """
    return as_request(session.prompt_for(model).render(question))

"""One check both Surgeon steps need, in one place."""

from __future__ import annotations

from coldfix.cost.session import Session


def refuse_foreign_session(session: Session, expected: str, error: type[Exception]) -> None:
    """Refuse a session whose cached prefix is not this step's.

    `Session` assembles and caches one prompt per model from **its own** system
    string, while each `generate` sends **its module's** to the client. The two
    Surgeon steps have different system prompts, so a caller reusing one session
    for both bills and caches against a prefix that was never sent — silently,
    because nothing about the reply looks wrong.

    S-9.1 closed exactly this for the audit and nothing had closed it for the
    Surgeon. Checked inside each step rather than at the composed path, because a
    check only at the join is one something can be routed around.
    """
    if session.system != expected:
        message = (
            "this session's prompt is not this step's, so the prefix it caches and bills "
            "against is not the prompt being sent. Epic 10 has two Surgeon steps with two "
            "system prompts; give each its own session"
        )
        raise error(message)

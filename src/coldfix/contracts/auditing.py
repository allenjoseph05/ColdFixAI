"""The audit invocation protocol, shared by the two auditors that use it.

These four were defined in `audit/invocation.py`, which is where the finding
audit lives. S-10.3's test auditor is in `repair/`, and reaching across for them
made `repair` import `audit` while `audit` already imported `repair` — the last
of the seven-package cycle in `04-redesign-brief.md` §6.

**The non-negotiable moved file, not form.** `audit_messages` still constructs a
fresh list with nowhere to put a prior turn, which is how *the Adversary never
sees the Surgeon's reasoning* is enforced. Being reachable from two packages was
always true; it is now visible.
"""

from __future__ import annotations

from anthropic.types import MessageParam

from coldfix.cost.session import Session

AUDIT_TEMPERATURE = 0.8
"""An audit is an attack, and `04-cost.md` §3 records that no deterministic
validator exists for designing one. Diversity is the point for the same reason
S-8.1 has it: an objection nobody thought of is the one worth paying for."""


class AuditError(Exception):
    """The audit could not be invoked in isolation."""


def audit_messages(evidence: str, question: str) -> list[MessageParam]:
    """A **fresh** list, constructed here and shared with nothing.

    The non-negotiable in one function: there is no accumulated conversation to
    append to, no prior turn to carry forward, and no parameter through which
    either could arrive. A caller holding the Diagnostician's message history
    cannot pass it, because there is nowhere to put it.

    Returns a new `list` on every call rather than a cached or module-level one,
    so that a caller mutating what it got back cannot reach the next audit.

    **This is why no adversarial call site takes S-17.16's cached blocks.**
    `Session.run` renders a prompt and hands it to every `call`, and the seven
    investigation and repair sites shape their request from it — cheaply, because
    the prefix then caches. The four audit sites drop it and build their request
    here instead. Blocks assembled by the session are a prompt assembled
    somewhere else, and accepting one would make this function's guarantee
    depend on what that somewhere else happened to put in it. The caching
    forgone is the audit's ten calls, not investigation's hundred and twenty.
    """
    return [{"role": "user", "content": f"{evidence}\n\n{question}"}]


def refuse_shared_session(session: Session, *, expected: str) -> None:
    """Refuse a session that belongs to some other agent.

    `expected` is the prompt this particular auditor should own — the finding
    auditor's, or S-10.3's when a falsification test is the subject. It has no
    default: this guard serves two auditors in two packages, and a default would
    have been one of them, silently passing for the other. Two
    audits with two prompts is still one rule: **a session whose prefix belongs to
    somebody else undoes the isolation silently.**

    Raises:
        AuditError: its system prompt is not the auditor's, so its cached prefix
            is somebody else's and running the audit through it would inherit
            what this module spent the rest of its length removing.
    """
    if session.system != expected:
        message = (
            "this session's prompt is not the auditor's, so its cached prefix belongs to another "
            "agent and every call billed through it would carry that agent's system text and "
            "source. Build one with `audit_session`: the isolation is the fresh message list "
            "*and* the fresh prompt, and a shared session undoes the second silently"
        )
        raise AuditError(message)

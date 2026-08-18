# 093 — The log contains the reasoning the auditor must not see

**Status:** accepted
**Story:** S-9.1 — finding-audit invocation
**Date:** 2026-08-17

## Context

Three acceptance criteria — the Adversary role is invoked with the **raw
experiment log** rather than the assembled evidence chain; the message list is
constructed fresh with **no Diagnostician reasoning**; a different model vendor
where configured. The story's note: *isolation is partial, not clean — document
it as such.*

`CLAUDE.md` makes the second a non-negotiable and names how it must hold: *the
Adversary never sees the Surgeon's reasoning — enforced by constructing a fresh
message list, not by instructing the model to ignore it.*

## Decision

### AC 1 and AC 2 contradict each other, and S-8.7 is why

AC 1 says hand over the raw log. AC 2 says include no Diagnostician reasoning.
**S-8.7 added `rationale` to every log record** — *why this instrument was worth
its cost* — which is the Diagnostician's reasoning, written by the
Diagnostician, sitting in the raw log. Handing the log over verbatim satisfies
AC 1 by breaking AC 2.

Neither criterion is wrong; they were written before the field existed. This is
the third time composing two stories has exposed a conflict neither could see
alone, and it is the most direct: one story put a value somewhere another story
promised nothing would be.

`08-audit.md` decides which way to resolve it, and supplies the number:

> It removes the explicit rationalization, which is the documented risk — **72%
> of reward-hacking episodes carry explicit justifying reasoning.**

So the explicit rationalization is exactly what comes out. `render_evidence`
withholds two fields:

- **`rationale`** — free prose whose entire purpose is to justify a choice;
- **`outcome`** — the agent's one-line gloss on what its own measurement meant
  (*"queries flat at 7, 7, 7 — this is clearly not the database"*).

and keeps what was tested, what instrument ran, how it was configured, and what
the harness measured.

**`verdict` stays, and that is a decision rather than an oversight.** It is a
three-valued classification S-8.3 ties to cited measurements, not prose, and an
auditor asked whether an exclusion was adequate has to know that something *was*
excluded. The alternative — withholding it — is tested as a sabotage, because
"isolation by sending nothing" would satisfy AC 2 perfectly while making the
audit useless.

`WITHHELD` names the two fields as data rather than burying them in a format
string, so a test asserts the list and a third addition is a line rather than an
edit inside a loop.

### The evidence is rendered from the artifacts, not from S-5.8's summaries

`PrunedLog`'s rendering is composed from `outcome` — one of the two fields that
has to come out. Rendering the log the Diagnostician reads and then removing a
field from the resulting text would be editing prose to enforce a boundary,
which is precisely what `CLAUDE.md` says must not be how this holds.

### The audit gets its own session, because a shared one leaks silently

`Session` caches one assembled prompt per model, and that prompt carries its
owner's system text, playbook and source as a **cached prefix**. An audit billed
through the Diagnostician's session would inherit all of it — the isolation
undone by the object it was billed through, while every message list this module
built stayed clean.

So `audit_session` builds one with the auditor's prompt, and
`refuse_shared_session` refuses anything else — checked **before** any spend, so
a misconfigured caller is stopped rather than reported.

Its playbook is deliberately empty. A playbook is accumulated advice about how to
investigate, and an auditor reasoning from the investigator's habits is
inheriting exactly the frame `08-audit.md` says this cannot remove and should
not add to.

### The enforcement is an absence, three times over

`invoke` has no `chain` parameter (AC 1), no `messages` or `history` parameter
(AC 2), and no `validate` parameter. A caller holding the Diagnostician's
conversation cannot supply it because there is nowhere to put it — the
construction S-8.1 used for `validate`, S-7.8 for `force`, and S-7.10 for its
single exit.

`audit_messages` returns a **new list every call**, so a caller mutating what it
got back cannot reach the next audit. That is tested by mutating it.

### Routing reuses §3's existing row

An audit is an attack — on the diagnosis rather than on a patch — so
`StepType.ATTACK_DESIGN` applies, and with it `CLAUDE.md`'s *never cascade on
attack design*. No new row this time, which is worth noting after Epic 8 added
two: the question to ask is whether the step is genuinely new, and this one is
not.

### AC 3 needs no code

*Different model vendor where configured* is `Router`'s tier models, and Allen
has confirmed there is no second vendor account — ADR 062 records it as blocked
indefinitely. The audit routes to the frontier tier because attack design is
creative; which vendor sits behind that tier is configuration.

## The bound, carried in the artifact

`RESIDUE` states `08-audit.md`'s honest position: the isolation removes the
explicit rationalization and **does not remove framing bias**, because the log
still records which experiments the Diagnostician thought worth running. A test
asserts the words *partial*, *framing bias*, and *not describe this as clean
separation* all survive — S-7.12's `Anchor.residue` construction, for the same
reason: a bound nobody can read is one somebody will quote past.

## Consequences

This story is deliberately scoping-independent. Epic 9's shape is under review
against S-0.8's result — the audit was designed to catch fabrication, which
happened **0 times in 60 real-model runs**, while the measured failure was
non-termination — and every attack story S-9.2 through S-9.8 may change. The
invocation harness does not: an isolated auditor working from measurements is
needed under any scoping, and its input is already the log rather than the chain,
so pointing it at an unfinished investigation is a change to S-9.8's routing
rather than to this.

## Sabotage

Fifteen properties, all caught, no survivors. The pairing that matters is *the
rationale is handed over* against *the measurements are withheld too*: this story
can fail in both directions, and a module that sent nothing would satisfy the
non-negotiable while making the audit incapable of objecting to anything.

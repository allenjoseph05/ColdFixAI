# 111 — Epic 10 never ran the test against the patch

**Status:** accepted
**Story:** Epic 10 composition check
**Date:** 2026-08-18

## Context

Six stories: a falsification test, a must-fail gate, an audit of the test, a
slack-reducing classifier, a patch generator, a retry discipline. Every one
passed its own tests and its own sabotage pass.

After all of them the epic could not perform its own sentence — *fix the
confirmed finding, test first.* Fourth consecutive epic to end that way, and the
defect is the same shape every time: a value one story produces and another
consumes, where nothing in either story's tests holds both ends.

## The defects

### 1. The test was never run against the patched code

S-10.2 proves the falsification test **fails** on unpatched code, and takes a
`DiagnosticSession` precisely so a patch cannot be there. Nothing ran it
afterwards.

So the epic wrote a test, proved it failed, audited it, strengthened it,
generated a patch confined to the evidence, and classified that patch for slack —
and never asked whether the patch made the test pass. Epic 11 does not cover it
either: that epic *attacks* the patch, and `03-agents.md` §5.2 gives the Surgeon
its own `run_test(script, on_ref)`.

`verify` is that step, and it takes a `CandidateSession` — the exact inverse of
`run_gate`'s. Between them the pair states the whole rule: **the test is proved
to fail where a patch cannot exist, and proved to pass where it does.**

### 2. The same three exit codes mean different things on the two sides

On unpatched code, a script that raises something other than an assertion is a
**broken script** — S-10.2's third outcome, whose remedy is *repair the script*.

On patched code the same script has already run cleanly once. An error now means
**the patch removed something the test depended on** — a method it called, a
field it read. That is a correctness failure of the patch, and reporting it as
*fix your test* would have the Surgeon rewriting a test that was right.

The protocol itself is shared rather than re-derived: `verify` uses S-10.2's
`wrap` and its constants, because two encodings of *which exit code means an
assertion failed* would be two answers to a question with one right one, in the
two places that have to agree.

### 3. A strengthened test was never re-gated

S-10.3 returns a `FalsificationTest`, never a `Falsified`, exactly so the
replacement cannot reach a patch without going back through the gate. Nothing
made that second trip. A stronger test the unpatched code already passes is as
useless as a weak one.

### 4. Two Surgeon prompts, one session, and no complaint

S-10.1 and S-10.4 have different `_SYSTEM` text. `Session` caches one assembled
prompt per model built from **its** system string, while each `generate` sends
**its module's** to the client — so a caller reusing one Surgeon session bills
and caches against a prefix that was never sent, silently, because nothing about
the reply looks wrong.

S-9.1 closed exactly this for the audit with `refuse_shared_session`. The fix
lives in each `generate` rather than in the composed path, because a check only
at the join is one something can be routed around.

### 5. The stall fires before the cap can, and escaped as an exception

`Phase.REPAIR`'s cap is three attempts and `Budget`'s `stall_after` defaults to
**three**. Three attempts failing the same way raise `ProgressStalledError` on
the third — *before* the cap's `BudgetExhaustedError` could fire on the fourth.
A loop catching only exhaustion let the stall escape as an unhandled exception:
not an escalation, and carrying no history.

Both end the repair and §7.2 gives this phase `ESCALATE` either way, so both are
caught — separately, because *tried three things, none worked* and *tried three
things and got the same answer each time* send a reader somewhere different.

## Consequences

**Sabotage: 26 properties, all caught, zero skipped, after six survived.** Every
survivor was the same failure of the test doubles rather than of the code, and it
is worth naming as one thing: **the fakes threw away what the composed path
passed them.**

- `FakeCandidate.run` re-wrapped a canned script instead of executing what it was
  handed, so removing `wrap` from `verify` changed no outcome;
- it recorded no ordering, so verifying *before* applying — which would run the
  test against the unpatched worktree and burn all three attempts learning
  nothing — was invisible;
- the stand-in for `patch.generate` discarded its keyword arguments, so the
  temperature raise and the prior-attempt context were both untested at the one
  join that supplies them.

A composition check whose doubles swallow the arguments is testing that the
functions were called, not that they were called with the right things. The
doubles now record; the tests assert what was recorded.

**Two paths were never exercised** and needed tests rather than fixes: a timeout
against patched code, and a vacuous test stopping the story before the test audit
is paid for.

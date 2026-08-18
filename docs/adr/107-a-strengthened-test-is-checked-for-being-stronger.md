# 107 — A strengthened test is checked for being stronger

**Status:** accepted
**Story:** S-10.3 — test audit before patch
**Date:** 2026-08-17

## Context

`08-audit.md` §3.3 states the flaw and the fix together:

> **The test is written by the agent that then writes the patch.** The Surgeon
> can write a weak test to make its own life easier. We noted the Adversary
> audits the test, but only *after* the patch exists — by then the weak test has
> already shaped the patch.
>
> **Fix:** the falsification test is submitted and audited **before** the patch
> is written.

`03-agents.md` §6.3 gives the question: *would a cheat pass the Surgeon's own
test? if so, write the test that wouldn't.*

## Decisions

### 1. There is nothing to withhold, and that is S-10.1 paying off

S-9.1 had to strip `rationale` and `outcome` from the experiment log, because the
Diagnostician had written justifying prose into it and *72% of reward-hacking
episodes carry explicit justifying reasoning*. `FalsificationTest` has **no
rationale field** — S-10.1 refused one — so the artifact goes over whole.

`claim` stays, for the reason S-9.1 kept `verdict`: an auditor asked *could a
cheat pass this test* has to know what the test claims. The opposite failure,
isolation by sending nothing, satisfies every rule while making the audit
useless.

### 2. A strengthened test is re-gated, not trusted

`TestAudit.strengthened` is a `FalsificationTest`, never a `Falsified`. Only
S-10.2 produces the second, so a strengthened test must go back through the
must-fail gate before a patch may be written — and it must, because a *stronger*
test the unpatched code already passes is exactly as useless as a weak one. The
type carries the requirement instead of a comment asking somebody to remember it.

`TestAudit.forward` is the single accessor for *the test the Surgeon must
satisfy*, so a caller cannot carry the weak one forward by reading the wrong
field — the mistake this story exists to prevent, made one layer up.

### 3. The replacement is checked for actually being stronger

Three ways a "strengthened" test is weaker, each of which reads as an improvement
to anybody skimming:

1. **the cost threshold rises** — more changes satisfy it than before;
2. **a guard disappears** — a trade the original caught now passes;
3. **it does not claim to catch what the auditor just named.**

The third is worth the most. An auditor that names a hole and hands back a test
which does not claim to close it has produced a round of work and no coverage,
and the Surgeon would satisfy it while the hole stayed open.

Everything else about the replacement is validated by **S-10.1's parser** — the
guard requirement, the improvement threshold, the citation check — because a
second implementation would be a second answer to the same question.

### 4. S-9.1's isolation is parameterised, not copied

`audit_session(..., system=...)` and `refuse_shared_session(..., expected=...)`
gained one defaulted parameter each. `CLAUDE.md` keeps things concrete until a
second case exists; this is the second case, and the alternative was a copy that
would drift. A test asserts the finding auditor's session is refused here just as
the Surgeon's is — two audits, two prompts, one rule.

## Consequences

**A real defect the sabotage pass found: an omitted `weaknesses` field defaulted
to "no cheat found".** `weaknesses: []` is the auditor saying *I looked and found
nothing*; a missing key is a reply that never addressed the question. Reading the
second as the first **passes a weak test on silence** — S-9.7's rule (*the safe
answer has to be reached deliberately, or nobody can tell a considered no from a
shrug*) in the place where it costs most. It is now refused.

**A second spelling was deleted rather than tested.** `NO_CHEAT_PASSES = "none"`
was S-9.5's construction transplanted, and it does not fit: there the empty
answer is a *string field*, here it is an empty *list*. The prompt asks for `[]`
and nothing asked for the string, so the branch had no test and no caller. A
sabotage removed it and changed no outcome; it is gone. Widening what counts as
*the auditor found nothing* is the last thing this module should do.

**`Phase.TEST_AUDIT`'s two-round cap had no caller since S-5.4** — the same shape
`FINDING_AUDIT`'s had until S-9.8. `authorize_round`/`record_round` own it now.

**A second sabotage survivor was a test that could not discriminate.** The
round-conclusion test recorded two rounds and asserted the count, but the default
`stall_after` is 3 against a cap of 2 — the cap always fires first, so the
conclusion is never compared and a constant string passed. At `stall_after=2` the
two are distinguishable, and the control asserts a repeated conclusion stalls.

**`__test__ = False` on `TestAudit` and `TestAuditError`.** Their names begin with
`Test` because the *subject* is a test, and pytest collects on the prefix alone.
Today that is a warning; the day either gets a no-argument constructor it becomes
a silently-collected suite reporting passes.

**Sabotage: 24 properties, all caught, zero skipped, after two survived.**

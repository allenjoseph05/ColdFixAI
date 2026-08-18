# 110 — A repeat is decided, not scored

**Status:** accepted
**Story:** S-10.5 — retry discipline
**Date:** 2026-08-18

## Context

`10-BACKLOG.md`'s note on this story: ***"must differ in approach" cannot be
self-judged — the agent writes its own approach label and can rename the same
idea.*** `08-audit.md` F12 says the same and prescribes the fix: *compare the
diffs.*

The acceptance criterion asks for a structural check that rejects attempt 2 "if
its diff touches the same lines with a **similar edit shape**" as attempt 1.
*Similar* is the word that needed deciding.

## Decisions

### 1. The check is an equivalence, not a similarity score

The obvious implementation scores two diffs and rejects above a threshold — and
that threshold would be a number nobody measured. S-9.4's rule is that a
threshold is **derived or it does not belong**, and there is nothing here to
derive one from: no noise floor, no class gap, no measured quantity at all.

So *similar edit shape* is defined as an equivalence that can be **decided**: the
same edit, normalized. Added and removed lines only — context lines are the file,
not the change — stripped of whitespace and of trailing comments, compared as a
set so that reordering independent hunks is not a new idea.

**What it catches:** an identical diff, one differing only in whitespace, one
differing only in comments. That is what *renaming the same idea* looks like when
the idea is a diff.

**What it does not:** a renamed local variable. Deciding that two token streams
mean the same thing needs a parser and a judgement, and a judgement is what this
check exists to avoid. Stated rather than left to be discovered.

### 2. Both conditions, and requiring both is what keeps honest retries alive

A repeat is *overlapping original lines in a shared file* **and** *the same
normalized edit*.

- Same lines alone would refuse the second genuine idea at the same site — and
  the site is where the second idea usually is, because that is where the cost
  was measured.
- Same edit alone would refuse the same change applied somewhere else, which is a
  different target and therefore a different attempt.

### 3. The overlap is measured on the original side

Two attempts that both rewrite lines 41-42 are working on the same code however
far apart the results land; an earlier hunk that grew or shrank moves everything
after it on the new side. `hunk_ranges` is the third sibling in `patching.py`
after `touched_paths` and `hunk_lines`, sharing their hunk-counting discipline.

### 4. `Attempt` carries the failure, and that corrected S-10.4

S-10.4 first showed retries only the previous `approach` strings — **precisely
the self-judged label F12 says the agent can rename.** The context meant to make
the next attempt different consisted entirely of the thing that cannot be trusted
to differ. `03-agents.md` §5.1 asks for *prior attempts **with failure
reasons***; `Attempt` is that, and an empty reason is refused.

The failure is also the stall conclusion S-5.4 reads, for the same reason: three
attempts failing the same way is a phase repeating itself, while three attempts
*named* differently is not evidence of anything.

### 5. `Phase.REPAIR`'s three-attempt cap had no counter

Third of these in Epic 10, after `FINDING_AUDIT` (S-9.8) and `TEST_AUDIT`
(S-10.3). `Session.run` records a step only where a phase's cap counts steps, and
this one counts attempts. Whoever owns the unit counts it.

## Consequences

**A guard was deleted rather than tested.** `repeats` refused to compare two
edits that both normalized to nothing, on the worry that an empty edit compares
equal to every other empty edit. Its only reachable effect was to call two
comment-only patches at the same lines *different* — and they are the same no-op
twice. S-3.12's rule: a guard no test reaches is a guard nobody has checked.

**Two fixtures that could not discriminate, in one story.**

- The hunk headers used the same line number on both sides (`@@ -41,2 +41,2 @@`),
  so reading the new side instead of the original was invisible. Shifting both by
  a constant did **not** fix it — both diffs shift together and still overlap. It
  takes two diffs that share their original lines and share none of their new
  ones, which is the realistic case: an earlier hunk changed size.
- The fixtures contained no context lines, so dropping the filter that excludes
  them from the edit changed nothing.

Twelfth and thirteenth instances in this project.

**The substring-over-source trap, a fifth time — and the worst instance yet.**
The test asserting *there is no similarity threshold* read the module source and
failed against its **own docstring**, which uses the word *similarity* to explain
why there is no similarity score. The check now asserts what the module imports
and that its only module-level floats are the two documented temperatures. Five
occurrences is a habit, not a slip: **an absence or isolation test reads
structure — imports, signatures, model fields — never prose.**

**Sabotage: 26 properties, all caught, zero skipped, after three survived.**

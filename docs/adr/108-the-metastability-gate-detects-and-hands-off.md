# 108 — The metastability gate detects and hands off

**Status:** accepted
**Story:** S-10.6 — slack-reducing classifier (SAFETY)
**Date:** 2026-08-18

## Context

ADR 105 moved this story ahead of patch generation and left one question open:
`00-BRIEF.md` §4 requires a slack-reducing patch to *pass a spike-and-recovery
test (primitive 12) before it can be proposed*, and S-10.6's acceptance criteria
do not mention one. Blocking precondition, or attached result?

## The question was already answered

**Neither.** `08-audit.md` F1:

> `00-BRIEF.md` §4 makes a spike-and-recovery test mandatory for slack-reducing
> patches. **That test is not executable in our environment.**
>
> Metastable failure requires a sustaining feedback loop: many clients, retry
> logic, load balancing, queues feeding each other. In a single container with
> one synthetic driver, the loop does not exist. We can generate load. We cannot
> generate metastability.

F1's corrected gate is four steps: classify the diff statically, label and block
auto-approval permanently, emit a specific staging warning, **and do not claim we
tested it.** §7's revised build order records the substitution at step 9.
Primitive 3 is downgraded from *verification we perform* to **risk class we
detect and hand off**.

So the spike test is not a precondition and is not run at all. S-10.6's criteria
are F1's gate plus the retry-amplification check `01-primitives.md` §15 added as
a *partial rescue*.

**A stale claim, recorded rather than corrected in place:** `01-primitives.md` §4
still states the original mandatory gate, and §15 of the same file records the
downgrade. Where they disagree the audit wins, per `00-BRIEF.md`'s authority map.

## Decisions

### 1. A false negative is the dangerous direction

An unflagged slack-reducing patch reaches auto-approval; a wrongly flagged one
costs somebody a review. So the classifier leans toward flagging.

This is the **opposite** of S-9.7's conclusion, where a wrong `unrepresentative`
silently discarded a real finding and the safe answer was therefore the default.
Same reasoning, opposite outcome, because what a mistake costs is what decides.

### 2. Two of the six patterns are comparisons, not keywords

Four are keywords on *added* lines: a cache appearing, a retry appearing. Two —
pool size and timeout — are slack-reducing only when the value goes **down**. An
implementation that greps for `timeout` flags the patch that *raises* one, which
adds headroom, and **a label that fires on the opposite of its subject is one
every reader learns to ignore.**

Settings are matched by name across the two sides rather than paired by position:
git puts removed lines before added ones, but a hunk that reorders or reindents
makes position meaningless, and `pool_size 20 -> 5` is a sentence a reviewer can
check while *the third minus line* is not.

### 3. The diff parsing is S-2.4's

A `+` starts content inside a hunk and a file header outside one. `touched_paths`
already solved that by tracking the counts each `@@` declares, and re-deriving it
in a safety module would be a second answer to a question with one right answer.
`hunk_lines` is its sibling, and a test asserts the two agree on the adversarial
case — a removed line whose content begins `-- a/x`.

### 4. AC 3 is enforced by absence

F1: *block auto-approval permanently — no trust level can clear it.*
`may_auto_approve` **has no trust-level parameter**. A function taking a level is
one somebody can pass a high enough level to, and Epic 14's ledger does not exist
yet to be argued with. The construction S-9.1 used for `chain` and S-10.1 for
`diff`.

`label` is `None` when nothing matched — never a second label meaning *checked
and clean*, because this classifier cannot establish that and a value saying it
could would be read as one.

## Consequences

**Two real defects, both found by running the tests rather than by review.**

1. **The settings regex matched nothing.** Embedding the vocabulary inside the
   name pattern meant the leading `[A-Za-z_]` consumed the `P` of `POOL_SIZE`,
   leaving `OOL_SIZE` for the alternation to find `pool_size` in — which it never
   can. Finding assignments and deciding which are settings are two questions,
   and one regex answering both answered neither. Split.
2. **`\bbackoff\b` missed the ordinary spelling.** `\b` does not match against an
   underscore, so `adapter_with_backoff` and `retry_backoff_ms` — the usual
   compound identifiers — were invisible.

Both are false negatives in a safety classifier, which is the direction that
matters.

**A test fixture that was its own opposite.** The non-amplifying `Amplification`
used calls of `(1, 1, 2)`, and S-3.16's threshold is a factor of **2.0** — so the
fixture named *not amplifying* was exactly amplifying. Eleventh instance in this
project of a fixture that could not discriminate, and the first where it claimed
the opposite of its own name.

**Sabotage: 27 properties, all caught, zero skipped, after one survived.** The
survivor: **bypassing the settings vocabulary entirely changed no outcome**,
because nothing tested that an ordinary decreasing number — `page_size`,
`column_width` — is not a setting. Over-flagging is the safe direction for a
missed cache and the fatal one for a label: a classifier firing on every
decremented constant is one every reviewer learns to skip. That is decision 2's
argument again, about volume instead of direction.

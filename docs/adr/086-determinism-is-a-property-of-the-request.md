# 086 — Determinism is a property of the request, not of the setting

**Status:** accepted
**Story:** S-8.3 — result interpretation
**Date:** 2026-08-16

## Context

Three acceptance criteria — a separate call at temperature 0.0; a verdict of
confirmed / narrowed / rejected with the measurement attached; and a test that
identical inputs produce identical verdicts across repeated calls.

This closes the loop `02-architecture.md` §2.2 describes. S-8.1 forms the
hypothesis, S-8.2 designs the experiment, and this reads what came back.

## Decision

### §3's table was incomplete, and §2 proves it

S-8.2 added a row for experiment design on the grounds that the loop has a step
the table skipped. That was an argument from the loop's shape. This one is a
documented omission.

`04-cost.md` §2 — the section whose entire thesis is *route by step, not by
agent* — lists in its **mechanical** table:

| Interpret a growth table | Diagnostician | ~40 |

Forty calls a run makes it the most frequent step the Diagnostician takes and the
largest single mechanical workload in the system. §3's cascade table has no row
for it. Since the step class is derived from that table, the one step §2's
argument is most about was the one step §3 could not express — it would have had
to run creative, on the frontier, which is precisely the drift §2 exists to
prevent.

Two rows added by one epic is worth stating plainly: **§3 is not an enumeration
of the loop's steps.** It was written early, against the agents that existed
then, and Epic 8 is the first code to walk the whole investigate loop. A test
asserts from both stories that the two *none exists* rows are untouched, because
editing this table twice is exactly how they would stop being *none exists*
without anybody deciding to change them.

### The verdict must cite measurements the harness recorded

That is the row's mechanical check, and it is `CLAUDE.md`'s first two
non-negotiables made mechanical rather than asked for: *no finding without a
measurement, enforced by schema and not by prompt*, and *do not let an agent
report a measurement*.

The reply carries the figures the verdict rests on. Every one is checked against
the mapping the harness took: a name that was not measured is refused, a value
that does not match is refused, and a verdict citing nothing at all is refused.
`confirmed — queries grew 40x` against a flat table fails, which is the
archetypal cheap-model failure — inventing the number that supports the answer it
already gave.

Exact comparison, with no tolerance. A tolerance would be this module deciding
how far a quoted figure may sit from the measured one, which is a judgement, and
the point of the check is that it contains none — the model is copying from a
block in its own prompt.

**The attached measurement is the harness's, structurally.**
`Interpretation.measurement` is filled from the mapping the function was handed;
a `measurement` key in the reply is never read. There is no path from an answer
to that field, which is what makes the second non-negotiable a property of the
code rather than of the system prompt.

**Checked twice, deliberately.** `parse` checks it to produce a sentence the
cascade can correct against; the artifact checks it again in a validator, so no
other code path can construct one without. *Enforced by schema, not by prompt*
would be false if the enforcement lived only in the one function that happens to
call it today.

**What the check does not catch, stated because a validator whose limits are
invisible is worse than none.** It catches *fabrication*, not *misjudgement* — a
model can cite the right number and still call it the wrong way. S-5.6's
`never_escalated()` is what tells the two apart in practice, and it already
reports the count rather than guessing, precisely because a check that has never
rejected anything is either a step the cheap model handles or a check that cannot
fail.

### AC 3's obvious test is worthless, and the real property is upstream

*Identical inputs produce identical verdicts across repeated calls.* Against
S-0.7b's replaying client that is trivially true: identical inputs hash to one
digest, one digest replays one recording, and one recording holds one verdict.
Such a test passes against code that renders the measurement in a different order
on every call. It tests the cache.

Temperature 0 makes a model's answer stable for a **fixed prompt**. It says
nothing about a prompt that moves. So the property that has to hold is that
**identical inputs produce an identical request**, and the work is upstream of the
model:

- the measurement is rendered in sorted key order, because a `Mapping` iterates
  in insertion order and the same measurement assembled by two code paths would
  otherwise be two different prompts;
- its values go through `json.dumps`, so the figure the model is asked to copy is
  written the way it will be read back.

The tests follow the property rather than the sentence: two equal measurements
built in different orders must render identically; the whole question must be
byte-identical; and the **request digest must agree across a process boundary**,
which is S-8.4's construction for the same reason — hash-order randomization only
has room to move between processes. The end-to-end test asserts the two calls
resolved to the *same recording*, and its docstring says that the digest is the
assertion and the verdict is not.

### The shared reply reader

S-8.2 and S-8.3 both extract one JSON object from a reply and both must return a
rejection rather than raise, so `replies.py` holds `Attempted[T]` and
`read_object`. The reason to share is the **rejection text**, not the parsing:
those sentences are fed back to the model as corrections (ADR 085), so two
hand-written versions would drift and the one that drifted would be the one a
model was asked to act on.

**S-8.1 deliberately is not a third caller.** Hypothesis generation raises where
the two mechanical steps return, and that is the line ADR 085 draws between a
wrong answer and an absent one. Routing the creative step's refusal path through
the cascading steps' helper would blur the distinction the design rests on.

## Consequences

The Diagnostician's three calls now exist: 0.8 on the frontier with no cascade,
0.0 on the mid tier with a cascade, 0.0 on the mid tier with a cascade. S-8.7's
instrument switch and S-8.6's evidence chain are assembled from what they
produce.

Every field `Interpretation` carries is one `ExperimentLog.append` wants, asserted
by appending rather than by inspection.

## Sabotage

Twenty-two properties, all caught — after three survived, and the three failures
were of three different kinds.

*A weak assertion.* `test_every_bad_citation_is_reported_at_once` checked for
`"db.query"` in the rejection and passed against an implementation reporting only
the first problem — because the *unmeasured metric* message lists everything that
**was** measured, and `db.query` is in that list. An assertion that matches a
different part of the same output is not an assertion about the part it names.
Re-anchored on text unique to the second problem.

*Two guards no test reached.* Both were in the newly extracted module, both had
been sabotaged through their callers in S-8.2, and neither caller's suite ever
sent a reply that reached them — a truncated reply has no closing brace, so it
takes the *no object* path and the malformed-JSON path was never executed. The
shared module now has its own tests, which is where a shared module's branches
belong.

*And one of those guards could not fire at all.* The *that was not an object*
rejection, shipped in S-8.2, is unreachable: the pattern takes text from the first
`{` to the last `}`, and JSON beginning with `{` that parses at all is an object.
Deleting it changed no behaviour. That makes it S-7.4's redundant condition —
unverifiable by construction, reading as protection while protecting nothing —
and S-7.4's recorded remedy is the one applied: collapse it, and verify the
intent from the other side. A parametrised sweep now asserts that no array,
number, string, boolean or null is ever read as an answer, with an object nested
inside an array kept as the control.

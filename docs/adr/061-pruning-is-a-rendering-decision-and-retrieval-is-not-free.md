# 061 — Pruning is a rendering decision, and retrieval is not free

**Status:** accepted
**Story:** S-5.8 — context pruning with on-demand detail
**Date:** 2026-08-09

## Context

`04-cost.md` §5: the experiment log grows, but the agent does not need forty full
stdout dumps preloaded. It needs to know experiment 7 happened and what it
concluded, and to be able to ask for the rest.

    experiment 7 — ablation of get_discount_price
      → 8.24s becomes 1.11s. 87% of cost localized.

§5 calls zero information loss *the difference between this and naive
truncation*, which makes AC 4 the property everything else has to preserve.

## Decision — the detail is held, not referenced; pruning is what `render` omits

`PrunedLog` stores every record in full and `render` emits summaries. There is no
`truncate`, no `forget`, no `evict`, no `compact` — AC 4 as an absence rather than
a promise, the same construction S-5.7 used for the append-only rule.

The detail is held **here**, not merely pointed at. *Nothing discarded* should not
depend on another store still having the thing, and S-5.1's replay cache is keyed
on experiment identity rather than on log position, so a pointer would also have
to survive a key that changed.

## Decision — retrieved detail never re-enters the log

S-5.7 built a prompt whose log is append-only precisely so the cached prefix stays
byte-identical. Writing experiment 3's stack traces back into the middle of that
log at call fifty would invalidate every breakpoint after it — **turning the 23×
win into a loss on the same call that was trying to save tokens**.

So `read_experiment` returns text and mutates nothing. The caller places it after
the log, where a tool result belongs.

## Decision — the summary is composed, not authored

`08-audit.md` F6 for the fourth time in this epic. An agent asked to summarize its
own experiment can write *experiment 7 — nothing of interest*, and the detail is
then never retrieved by anyone: the information is preserved and lost at the same
time.

So the header is assembled by the harness from the primitive and the target —
facts about what ran — and the only supplied part is the outcome, which is the one
thing only the measurement knows. Indexes are assigned by the log for the same
reason: `read_experiment(7)` has to mean the seventh experiment, and a
caller-supplied index can collide, skip or restart, each of which returns somebody
else's measurement with no error.

An out-of-range index is refused by name rather than clamped. The caller asking is
a model, and a model that guesses an index must be told rather than handed the
nearest record.

## Decision — the 60–80% is measured, including what retrieval adds back

§5 claims context drops 60–80%. That is a claim this module measures rather than
repeats: `reduction()` against what an unpruned log would have carried, and
`net_reduction()` after what was actually pulled back.

**Retrieving every experiment exactly once cancels the saving precisely** — zero
tokens saved, plus the round trips the metric does not count — and any re-read
takes it negative. The figure is not clamped, because a clamp would hide exactly
the case it exists to expose. `meets_claim()` reports against §5's 60%, and the
report says when the technique is not delivering: a log whose detail is no bigger
than its summaries has nothing to defer.

## Consequences

**AC 1 says one line and §5 shows two.** Neither is the binding constraint — the
token budget is, and §12.3 assumes 12k for the whole pruned prompt. So each
*part* of a summary must be a single line and is length-bounded, which gives §5's
exact two-line shape with a size that stays predictable across forty experiments.
This is the fifth Epic 5 story where the acceptance criteria and the cost document
differ in detail and the document is the specification.

**Two defects were found by tests failing rather than by sabotage.** The
emptiness check in `reduction()` was written against total characters, but the
retrieval notice is always rendered — so an empty log reported a confident 0%
saving instead of *no experiments yet*. And *retrieving everything* turned out to
cancel the saving exactly rather than going negative, which is a sharper statement
than the one first written down; going negative needs a re-read.

**Sabotage-verified on twenty-two properties, all caught.** One needed rewriting:
a clamp inserted after the range check is dead code, not a sabotage.

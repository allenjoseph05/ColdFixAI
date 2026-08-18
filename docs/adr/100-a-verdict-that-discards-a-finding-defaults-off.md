# 100 — A verdict that discards a finding defaults off

**Status:** accepted
**Story:** S-9.7 — representativeness assessment
**Date:** 2026-08-17

## Context

*Assesses whether the workload resembles something users exercise. Verdict
`unrepresentative` skips to the next finding without repair spend. **Limitation
documented: the agent cannot know real traffic patterns.***

`08-audit.md` states the gap and the limits of its own fix in the same breath:

> We optimize what we can run. If the runnable workload is a test fixture that
> does not resemble production usage, we optimize the wrong thing with full
> confidence and complete evidence.
>
> **Fix:** this is the `unrepresentative` verdict in the finding audit. It is a
> **partial** fix — the agent still cannot know real traffic patterns.

So AC 3 is not a footnote on this story. It is most of it.

## Decision

### The two errors are not symmetric, so the safe answer is the default

`unrepresentative` skips straight past repair. A wrong one throws away a real
finding **silently** — nobody sees the thing that was not investigated. A wrong
`representative` spends repair effort on something that did not matter, which
somebody notices and can undo.

So a workload is representative unless there is a **stated reason** it is not,
and *absence of evidence* is not a reason. The prompt says which way to lean and
why, because a default nobody is told about is a default nobody uses. This is
S-9.5's empty answer inverted: there the safe default was *no alternative*, here
it is *representative*.

**The default must be reached deliberately.** A parser falling back to
`representative` on a malformed answer would make the safe default invisible, and
the next reader could not tell a considered *yes* from a shrug. So an unanswerable
reply is refused rather than defaulted.

### One fact is computable, and it is handed over rather than judged

S-7.6 records that synthesized data is uniform **by construction**, so a workload
seeded from schema is known not to resemble a production distribution whatever
the endpoint is. That is a fact about the fixture, not an opinion about the
route, and the report keeps the two apart — a reader who cannot tell a
measurement from a guess will trust both equally.

### The limitation is carried in the artifact

`RESIDUE` says that `unrepresentative` means *this does not look like production
usage to a reader of the code*, never *this is not exercised*; that there is no
traffic data behind it; and that `08-audit.md` calls the whole check a partial
fix. A verdict that skips a finding without repair spend is exactly the kind of
claim somebody quotes without its bound.

The report also tells the reader that a skipped finding **can be overturned**,
because nobody sees a finding that was not investigated and the only person who
can notice is the one reading this line.

## Consequences

`08-audit.md` asks for this limitation to be stated in `07-use-cases.md` as well.
That is a customer-facing capability claim and belongs with the others rather
than being duplicated here; recorded so it is not lost.

## Sabotage

Thirteen properties, all caught, **zero skipped** — and the skip count is now
printed by the runner rather than left in the scrollback, after S-9.6's pass
reported twelve of thirteen and quietly omitted the most important one.

The pair that matters is *every verdict becomes unrepresentative* against *every
verdict becomes representative*: this can fail in both directions, and the first
is the one that loses findings without anybody seeing.

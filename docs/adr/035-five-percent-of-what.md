# 035 — Five percent of what: the counter budget needs a denominator

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.6 asks for counters for database queries, rows returned, bytes returned,
HTTP requests, file opens and allocations; for each to attach through a
framework-specific hook the adapter declares; for optional per-event stack
capture; and for counter overhead verified under five percent.

ADR 013 deferred exactly this and said why: S-1.3 shipped the counting mechanism
and no counters, because *what* to count is a question about a framework and
*how* to count is not.

## Decision

**Four of the six need a magnitude, so the record callback grew one.** `Record`
is now a protocol whose amount defaults to one: a hook that counts calls
`record()`, a hook that measures calls `record(rows)`, and `Count` carries both
`events` and `total`. This is not two mechanisms. It is one, and it has to be
one, because `db.query` and `db.rows` are the project's canonical guard pair —
queries falling while rows explode is only visible if both numbers came from the
same run, and a second wrapper to get the second number would double the cost on
the hottest path in the system while letting the two numbers drift apart.

**Most of this module is a vocabulary, and the vocabulary is the deliverable.** A
counter is a name a primitive asks for and an adapter answers. ADR 013 made an
unknown hook name raise rather than return zero — but that only helps if there is
one spelling to be wrong about. So the catalogue is the spelling, and a name
outside it is refused *at registration* rather than discovered when some
primitive asks for the counter nobody registered. That is one step earlier than
ADR 013's rule, for the same reason.

**Allocations do not fit the hook shape and are not forced into it.** Nothing in
Python fires per allocation that a probe can attach to without a C-level
profiler. `tracemalloc` measures over a block and returns per-site totals with
their own tracebacks, so the counter is declared a `BLOCK_METER`, its attribution
comes from tracemalloc rather than from S-1.3's stack capture, and it says so.
The alternative was inventing events, and an invented event is a fabricated
measurement.

**"Under five percent" is not a property of an instrument.** This is the finding
of the story, and it arrived as a failing test: the counter measured 77% overhead
against the stand-in cursor these tests use. The counter was fine. The cursor
takes 0.4µs, and a counter costing a fixed 0.49µs per event *is* most of that.

The cost is fixed per event, so the ratio is a property of the **pair**, and the
budget is meaningless until the denominator is stated. It is stated: 366µs, the
figure ADR 013 measured for one instrumented database call on a real subject.
S-0.4's endpoint averaged roughly 1.2ms per query across 1193 of them; the
smaller is used, because a budget should be stated against the cheapest thing it
will observe. Against that, counting costs **0.13%**. There is also an absolute
bound of 5µs per event, because the defect ADR 013 records cost 590µs per event
and would pass any ratio stated against a slow enough operation.

**Per-event stack capture has no fixed percentage at all, and this is a finding
S-3.9 inherits.** Capturing a stack walks the whole stack, so its cost is linear
in depth. Measured on one machine, same counter, same events:

| Frames beneath the event | Counting | With stack capture |
|---|---|---|
| 0 | 0.40 µs | 12.4 µs |
| 10 | 0.36 µs | 25.9 µs |
| 50 | 0.66 µs | 86.5 µs |
| 200 | 0.44 µs | 295.7 µs |

Counting is flat. Stack capture is about 1.4µs a frame. A Django request is tens
of frames deep before the view is entered, so at a realistic depth **capturing a
stack per event costs as much as the database call it is observing** — 86µs
against 366µs is 24%, five times the budget. The tests assert the shape rather
than a bound, because the shape is what is true.

S-3.9 localizes findings by walking these stacks and therefore inherits this. The
options are to sample events rather than capture all of them, or to bound the
walk — but a screening sweep must not turn it on, and the story that needs it has
to pay for it deliberately.

**`tracemalloc` costs 327% of the run it observes** and is declared `HEAVY` for
it. That is not a defect: storing a traceback for every live allocation is
exactly what makes the attribution possible and exactly what makes it cost. What
matters is that the catalogue says so, so a screening pass attaching everything
it can find does not silently start measuring the instrument.

## Consequences

**Makes easy.** S-14.2's Django adapter: `measuring(cursor_class, "execute",
rowcount)` is the whole of the query counter, and registration refuses a
misspelling. S-3.8's guard pairs, which are declared here and enforced there.

**Makes hard.** Any future counter whose amount is not a number, and any
attribution that needs per-event stacks on a deep framework stack. Both are
stated rather than papered over.

**Rules out.** Quoting an overhead percentage without the operation it is a
percentage of, and attaching stack capture or `tracemalloc` by default.

## Provenance

Four sabotage runs, each asserting the edit was detected: bypassing the
catalogue check at registration fails 1 test; discarding the recorded amount
fails 2; resolving the measuring hook's target at construction rather than at
install fails 1; leaving `tracemalloc` running after the block fails 2.

The resolve-at-install rule is not cosmetic and was written against a failure
this codebase can actually produce: S-3.4 replaces attributes with ablation
stubs, so a hook that captured its target when it was constructed would wrap the
value from before the stub and silently measure a callable nobody is calling.

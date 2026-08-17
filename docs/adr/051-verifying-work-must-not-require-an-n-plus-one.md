# 051 — Verifying work must not require an N+1

**Status:** accepted
**Story:** S-4.1 — workload enumeration interface
**Date:** 2026-08-08

## Context

`08-audit.md` F6 found a real flaw: the Explorer's "this workload does real work"
criterion was self-judged, and the agent is incentivised to say yes because
saying yes completes its task. An endpoint returning three rows in three queries
might be working, or might be a stub route. The audit's fix is an objective
threshold computed by the harness, which the agent cannot override:

```
work_verified = (
    queries_at_n100 > queries_at_n10          # responds to data volume
    and response_bytes_at_n100 > 2 × at_n10   # returns more data
    and wall_time_at_n100 > 1.5 × at_n10      # does more work
)
```

The fix is right and the first condition is not. **`queries_at_n100 >
queries_at_n10` is false for every correctly batched endpoint.** Two queries at
ten rows and two at a hundred is what a prefetched list view does — it is the
planted `list_books_batched` control in this project's own fixture, it is the
shape ADR 011 describes when it records a repository whose list endpoint was
already correct, and it is what the entire tool exists to produce.

Written verbatim, a workload is verified only when its query count climbs, which
is to say only when it has an N+1. The Explorer would discard the well-written
half of every repository it looked at, and the discard would be silent: the
workload is rejected as *not doing real work*, which reads as a fact about the
subject rather than an artefact of the test.

The audit did not notice because F6 is a section about a stub route, and every
example in it is one.

## Decision

The condition becomes **`queries did not fall`**. The other two are implemented
as the audit wrote them.

Nothing is lost on the case F6 was defending against. A stub route fails the
payload and time conditions regardless — it returns the same bytes in the same
time however much data exists — so those two carry stub detection on their own,
and the query condition was never what caught one.

Queries *falling* stays disqualifying. More data costing fewer queries means
something served the second measurement from a cache, and ADR 026's finding is
that no comparison of results can reveal it: a stale cache and a correct reset
both make every run identical.

Two further conditions, neither in the audit:

**Two scale points minimum, and it fails closed.** One measurement of a stub and
one of a real endpoint are the same measurement. A workload with fewer than two
observations is not verified, and says so.

**A scale ratio of at least 4×.** F6's formula was written against n=10 and
n=100. Applied at 2× it demands a doubled payload for twice the data — a
materially stronger test than the audit specified, which would reject correct
workloads. Below the ratio the harness reports that it cannot tell, and names
widening the spread as the action, rather than returning a no that reads as a
verdict on the subject.

## Consequences

**One false negative survives deliberately.** An aggregate endpoint — 37 queries
and a fixed-size answer at any volume, the `summarize_with_fixed_floor` decoy —
fails this test and does real work. There is no measurement that separates it
from a stub route, because from outside they are the same shape: constant cost,
constant output. The evidence string says which two things it is failing to tell
apart, so the output is a refusal to verify rather than a claim the workload is
broken. That distinction is the whole of what can honestly be said here.

**`extra="forbid"` is part of this decision, not housekeeping.** Pydantic's
default is to discard an unrecognised key silently, so `Workload(...,
work_verified=True)` constructed fine and dropped the claim without a word — an
agent that wrote it had every reason to believe the harness accepted it. F6's
self-judged criterion, removed by design and reintroduced by a library default.
Found by a test that attempted the override and did not get an error.

**The artifact is split in two, and that is also from this story.**
`02-architecture.md` §1.3 sketches one object with three callables and three data
members, and the story requires a Pydantic model with full validation. A callable
does not cross a node boundary, does not go in a replay-cache key and does not
serialize into an experiment log. `Workload` is the artifact; `BoundWorkload` is
the artifact plus the callables an adapter supplies locally, and its constructor
refuses a binding whose reset mechanism is not the one the artifact claims —
because measurements taken through such a binding carry a strategy that was never
used, and ADR 026 says the results cannot reveal it.

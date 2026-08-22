# 134 — The memory is only real once something reads it

**Status:** accepted
**Date:** 2026-08-22

## Context

S-12.6 built time travel and added `remembered` to `repair` — the seam that puts
what already failed back in front of the Surgeon. It left that parameter empty,
and recorded the consequence rather than rounding it up: **a real rewound run
still repeated the approach that had already failed**, with a control test
holding the gap open.

S-13.3 fills it. `Collection.FAILURE_MEMORY` has existed since S-6.2 and nothing
had ever written to it.

## Decisions

### 1. The whole attempt is stored, not the approach

S-10.4 first showed retries only the previous `approach` strings, which F12
records as the self-judged field an agent can rename. S-10.5's repeat check
compares **diffs**. So a memory that kept only labels would feed that check the
one field it cannot trust — and would look correct in every test that asserted
something was remembered.

`as_entry` stores the patch and the failure reason. A test asserts the diff
survives, and sabotaging it to keep only the label fails two tests.

### 2. Keyed per finding

`PersistentStore.append` refuses an unkeyed entry in as many words: *the ledger is
read per project shape, playbooks per fingerprint, and failure memory per
finding*. A Surgeon working on the book list's N+1 has no use for what failed on
an unrelated slow import.

### 3. The successful attempt is recorded too

S-11.7 can send a patch back after the Adversary breaks it. An approach that
passed its own falsification test and then failed the audit is exactly the kind
the next attempt must not re-propose — so `record_all` runs on the repaired path
as well as the escalated one. Writing only on escalation would forget precisely
the attempts that got furthest.

### 4. `Resources.failures` is required, not optional

A run without failure memory is a run that repeats itself after a rewind, which
is F5's defect. An optional store would be a switch that turns that guarantee off
with nothing to justify it — the argument S-12.4 made about the ship gate, and
the reason there is no trust parameter anywhere.

## The sabotage that survived, and what it found

Three sabotages were run. Two failed tests immediately. The third —
**removing `remembered=recall(...)` from the repair adapter, which is the entire
wiring this story exists to add** — changed no test outcome.

The reason is the shape this project keeps finding: `test_adapters.py` covered the
translation helpers, and `test_repair_composed.py` called `repair()` directly with
`remembered=` supplied by hand. **Neither held both ends.** A story whose whole
content is a join had no test of the join, and every other test still passed.

The fix is two tests that drive `adapters.repair` with the epic calls recorded
rather than run — the `_Generations` technique Epic 10 already used for the same
reason, and which that epic's own note says was added because *sabotages that
stopped showing prior attempts changed no outcome*. The same gap, in the same
place, one layer up.

Both sabotages now fail. That is the seventh consecutive time a passing sabotage
has meant a missing test rather than a redundant guard.

## Consequences

`remembered` has a source. A rewound run is now handed what the discarded branch
learned, which is F5's *rewind the code and keep the learning* actually performed
rather than merely made possible.

**AC 3's empirical half is written and unverified here.** The journal is Postgres
and Docker Desktop is not running on this machine — the named pipe does not
exist — so the two `postgres`-marked tests that round-trip an `Attempt` through
the real store and confirm it survives a rewind are skipped rather than passed.
The translation either side of the store is checked at unit speed, and that is
where an attempt is actually lost; the store's own durability is S-6.2's, already
proven by a test in `test_persistent_store.py` that rewinds a graph and reads the
journal afterwards.

# 135 — Three modules deferred the schema, and none of them guessed

**Status:** accepted
**Date:** 2026-08-22

## Context

S-13.1 reads as though it needs a store built. Checking first — the habit that has
changed the design in six stories now — found most of it already there:

| AC | State before this story |
|---|---|
| entries keyed by framework fingerprint | `Fingerprint.playbook_key()` and `playbook_from_store` both existed |
| structured: situation, action, outcome | **missing** |
| retrieved into Explorer context at grounding | seam existed, **nothing filled it** |

Three separate modules had written down that the entry schema was S-13.1's and
then declined to guess at it: `persistent.py` stores `(collection, key, entry)`
and leaves the columns to Epic 13; `auth.PlaybookLookup` returns `Mapping`s and
reads inside none of them; `Resolution` carries what it was given **unread**, with
a docstring saying *S-13.1 gives the entries meaning and S-13.2 decides when one
may be believed.*

That restraint is why this story is small. The alternative — each module inventing
the shape it needed — is the failure this project has found at six consecutive
epic joins.

## Decisions

### 1. Situation, action, outcome, and no fourth

The tempting fourth is `worked: bool`, and it is precisely the field S-13.2 owns:
*new entries are provisional and carry success/failure counters; promotion
requires N successes across different projects; two failures demote and
quarantine.* A boolean here would be that judgement made a story early and
without any of the counters that justify it — F15's shape, one collection over.

`extra="forbid"` is what enforces it: an entry carrying a verdict is refused
rather than quietly stored and later believed. Sabotaging it to `extra="ignore"`
fails a test.

### 2. Whitespace is not content, and `min_length` does not know that

A single space satisfies `min_length=1`. `Implicated.reason` and
`FalsificationTest` close the same hole with a validator, and this does too —
found by a test that expected a refusal and did not get one.

### 3. There is no production writer, and that is the story boundary

`record` exists as the way in that S-13.2 will gate, and nothing in `src/` calls
it. S-13.2 is marked **SAFETY** because *a wrong entry propagates silently to all
future runs and compounds*; a writer before that gate would be the propagation
with nothing to stop it.

**The safety boundary was already drawn in code**, which is worth noting: entries
are *consulted* before probing and *carried unread*. Nothing believes one. So
S-13.1 can land the schema without opening the window S-13.2 closes.

### 4. The join is tested first-class

`ground_workload` called `resolve_auth` with no key, so the consult never
happened — not even `no_playbook` was reached. That is AC 3, and it is a join.

S-13.3 landed one day earlier with exactly this shape and **its sabotage
survived**: one test file covered each end and neither held both. So the test
here drives `ground_workload` and asserts the arguments `resolve_auth` was
passed, stopping at that stage rather than building a Django project to reach it.
Removing `playbook=` fails it.

`no_playbook` is the default rather than `None`, because a first run against a
fresh store has learned nothing and that is a real configuration — and because
S-13.5 measures whether the tenth project of a kind grounds faster than the
first, which a silently absent consult would make meaningless.

## Consequences

S-13.2 has something to gate. Its four criteria — provisional entries, counters,
promotion across *different* projects, demotion after two failures — all attach to
`PlaybookEntry`, and the reason it must land before any production writer is
recorded on `record` itself rather than only in the backlog.

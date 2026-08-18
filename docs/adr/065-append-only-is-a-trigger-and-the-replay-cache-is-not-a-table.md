# 065 — Append-only is a trigger, and the replay cache is not a table

**Status:** accepted
**Story:** S-6.2 — persistent store
**Date:** 2026-08-11

## Context

`08-audit.md` F5 is the whole story: time travel restores the state at
checkpoint T, but the *reason* for rewinding is a failure discovered at T+n — and
that failure record lives in the state being discarded. Rewind, and the agent
repeats the attempt it rewound to avoid. **We want to rewind the code and keep
the learning.**

ADR 003 already decided the shape and is explicit that the separation, not the
engine, is the decision: *not a second schema in the checkpoint database — a
separate store, so that dropping checkpoints (a routine operation) cannot touch
the playbook (a destructive one).*

Two things about the acceptance criteria needed resolving before anything could
be written.

**AC 1 lists the replay cache among the things the database holds, and S-5.1
already built it on a filesystem.** ADR 054 put it in a directory of JSON
recordings partitioned by machine, and two of its properties depend on that: a
recording can be opened by hand when it produced a surprising answer, which is
S-5.2's entire debugging method, and a foreign machine's recording misses rather
than matching.

**Three of the four members belong to Epic 13.** A playbook is S-13.1's artifact,
the trust ledger S-13.4's, failure memory S-13.3's. This story builds the store;
it does not get to invent what goes in it.

## Decision

### Append-only is enforced by the database

A Python class with no `update` method is append-only until somebody opens a
connection — and the point of this store is that it survives operations performed
*on* the system rather than through it. A trigger refuses `UPDATE`, `DELETE` and
`TRUNCATE` on the journal, so the guarantee holds for `psql` too. The tests
attempt each violation through `psycopg` rather than through the module, which is
`CLAUDE.md`'s rule for a safety property: the test attempts the violation and
asserts it fails.

**`TRUNCATE` is guarded deliberately and separately.** It is the exact verb
ADR 003 names when it says dropping checkpoints must not reach the playbook, and
it is not a row-level operation — Postgres will not accept it on a `FOR EACH ROW`
trigger, so the obvious single trigger would leave it open while looking
complete. It gets its own `FOR EACH STATEMENT` trigger, and its own test.

### One journal, not four schemas

The four things F5 lists are *what must survive*, not four shapes this story
knows. The journal stores `(collection, key, entry)` with a `jsonb` entry, and
Epic 13 decides what an entry means. This is the third time this project has
declined the same guess: S-5.4 left the checkpoint schema to S-6.1, S-6.1 left
the experiment-log entry to S-8.4, and S-6.2 leaves the playbook to S-13.1.

### The replay cache is a member of this store and is not in this database

Moving it into Postgres to satisfy a literal reading of AC 1 would trade away
both of ADR 054's properties for tidiness. What F5 actually requires is that a
checkpoint restore cannot reach it — and a directory outside the checkpoint
database satisfies that at least as completely as a row would.

So `MEMBERS` records all four with **where each one lives**, and a test asserts
the replay cache is filesystem-backed and is *not* a journal collection. A list
that quietly held three would be the kind of partial truth this project keeps
finding in its own summaries.

### The store takes a `VerifiedDatabase`

S-2.5's construction, reused: the check is the constructor, so there is no
unverified handle to hand over. Our store is not the subject's database, but it
is still a database this system writes to — and S-2.5's default name patterns
already include `coldfix_*`, which is evidence the guard was built expecting to
cover it.

### `refuse_shared_store` enforces ADR 003's separation

The decision was recorded and nothing enforced it. Sharing one database makes
dropping checkpoints — routine, and performed with a `DROP` that knows nothing
about this module — capable of destroying the playbook. A `Path` is ADR 003's
development SQLite checkpointer and cannot collide; only a URL can, and one
naming the same database on the same server is refused.

## Consequences

**AC 3 is proved against a real rewind rather than a simulated one.** S-6.1 made
`langgraph` available, so the test compiles a graph with a checkpointer, runs it,
learns something in a late node, then rewinds via `get_state_history` and asserts
the checkpointed state went back while the failure memory did not. A second test
takes the stronger form: it *resumes* from the rewound checkpoint and asserts the
resumed run can read what was learned after the checkpoint it resumed from —
which is the property that actually stops it repeating the rejected attempt.

**Makes easy.** Epic 13 gets a store with the durability argument already settled
and can spend its stories on meaning. S-12.6's time travel has the half of F5
that is not about checkpoints already done.

**Makes hard.** The persistent store needs a Postgres server, so its tests are
`postgres`-marked and excluded from the fast subset — `CLAUDE.md`'s warning that
*a green fast subset is not evidence the sandbox works* now covers this module
too. They were run: 19 tests, and the twelve sabotages below ran against a real
container.

**Rules out.** One database for both stores, an append-only guarantee that lives
in Python, and moving the replay cache off the filesystem.

**Sabotage-verified on twelve properties, all caught**, including removing the
`TRUNCATE` trigger independently of the row-level one, and dropping the replay
cache from the membership list. Baseline re-run green after the pass.

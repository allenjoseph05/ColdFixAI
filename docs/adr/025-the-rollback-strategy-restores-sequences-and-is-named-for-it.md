# 025 — The rollback strategy restores sequences, and is named for it

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.6 asks for three reset strategies: transaction rollback, database snapshot
restore, and container restart, selectable per project and recorded.

S-0.5 already measured all of this, and found that the first one as named does
not work. **Plain transaction rollback failed all ten cycles of the spike while
passing the check that story specified.** Every row count identical, every
content hash identical, every `max(id)` identical — and
`helpdesk_ticket_id_seq` at 759 where it started at 509, exactly the workload's
insert count accumulated over ten cycles and never given back.

Postgres sequences are non-transactional by design. `nextval()` must not be
rolled back, or two concurrent transactions could receive the same id. This is
correct database behaviour and precisely why it defeats a naive reset.

The spike recorded the required change explicitly: *"`reset()` restores
sequences explicitly after rollback | E2 | The defect is silent and cheap to
fix."*

## Decision

**The strategy is `ROLLBACK_AND_RESTORE_SEQUENCES`, not `ROLLBACK`.** The name
is part of the fix. Shipping it under the backlog's "transaction rollback" would
invite exactly the simplification that reintroduces the defect, and a test
asserts the name so that doing so has to argue with a failing assertion.

The measured cost is 19.2 ms against 0.4 ms for the broken version — the repair
is nearly free, and it is the only strategy that needs no exclusive access, so
it is the only one that composes with a concurrent experiment design.

**A never-used sequence restores to `start_value` with `is_called` false.**
`pg_sequences.last_value` is NULL until a sequence issues its first value, and
NULL cannot be handed to `setval`. Defaulting it to 1 with `is_called` true
makes the first real `nextval` return 2 — the same defect this strategy exists
to fix, one row smaller.

**`SNAPSHOT_RESTORE` is a template copy**, at 163 ms. It resets what a
transaction cannot, schema changes included, and undoes work another connection
committed. It must terminate every other connection before dropping the
database, which is why it cannot run alongside anything.

**`CONTAINER_RESTART` destroys the server and its storage, rebuilds, and
reseeds from SQL text** rather than a dump archive. `pg_restore` needs a client
binary whose version matches the server, which S-0.5 found reports errors it
then ignores; and the environment that owns dumps is standup, not reset.

**Rollback has a precondition this module cannot check, and that is left to
S-2.7.** A rollback undoes work done on *its own connection*. A workload driven
inside a container connects separately and commits separately, and nothing here
can undo that. A connection cannot see what another connection committed and
attribute it, so the precondition is not checkable from inside the strategy —
it is caught by S-2.7 running ten cycles and finding the state did not return.
This is why the story says each strategy is *verified before use* rather than
trusted, and it is the strongest argument for that criterion existing.

## Consequences

**Makes easy.** Choosing a strategy on cost, since all three are correct and the
spread is 19 ms to seconds. Recording which was used, since the enum value is
the record.

**Makes hard.** Using rollback for a containerised workload, which is the
architecture's normal case. That is a real constraint and the honest reading is
that `SNAPSHOT_RESTORE` is the default for container-driven workloads and
rollback is for in-process ones. S-2.7 is what discovers this per project rather
than assuming it.

**Rules out.** Trusting a reset because it was implemented. Every strategy here
is a candidate until the verification harness has run it ten times.

**Left open — process state, and why it is already handled.** S-0.5 found a
Django `QuerySet` still reporting a row that had been rolled back, because the
rows are cached in a Python object no database-side reset can reach, and
concluded that *the reset contract has to cover process state as well*. Nothing
in this module does. It does not need to: S-2.1 destroys the container after
every run, so the process holding the cache does not survive to the next
experiment. The reset contract is the database half of a guarantee whose other
half is the container lifecycle, and neither half is sufficient alone. Worth
stating because it is not obvious, and because a future change that made
containers persistent between runs would silently reopen it.

## Provenance

`docs/10-BACKLOG.md` S-2.6 and S-0.5's recorded result;
`spikes/S-0.5-reset/FINDINGS.md` for every number quoted here.

**Sabotage-verified on four properties, and one of them corrected a claim in
this codebase.** Removing the sequence restore fails 3 tests. Treating a
never-used sequence as `1, is_called=true` fails its own. Skipping the reset when
a workload raised fails its own.

Dropping `--volumes` from the container restart **failed nothing**, and the
comment explaining that flag was wrong. The Postgres image declares its data
directory as a volume, so every container gets an *anonymous* one and a rebuilt
container gets a fresh one whether or not the old was removed — the reset is
correct either way. What `--volumes` actually prevents is each cycle stranding a
full Postgres data directory that nothing reclaims; an investigation resetting a
few hundred times fills the disk. That is a slower failure than a bad reset and
a harder one to attribute, so it now has its own test asserting the dangling
volume count does not grow across two resets, and that test does fail under the
sabotage.

This is the third time in Epic 2 that sabotage found something review did not —
after ADR 021's `--detach` and ADR 023's case-insensitive matching. It is the
first time the thing it found was a comment stating a false reason for correct
code.

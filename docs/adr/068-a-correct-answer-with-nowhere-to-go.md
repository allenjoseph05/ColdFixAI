# 068 — A correct answer with nowhere to go

**Status:** accepted
**Story:** Epic 6 composition check
**Date:** 2026-08-11

## Context

Epic 6 finished with four stories, 95 passing tests, and no way to run one
investigation through them. Performing the epic's own sentence — *state that
survives crashes, and knowledge that survives rewinds* — meant calling four
modules in the right order plus three joins that lived in none of them. No test
file in the epic touched more than two of the four.

Same shape as Epics 2, 3, 4 and 5 before their composition checks, which found
respectively an architecture that could not run a Django app, three defects, five
defects, and four.

## Decision — three joins, and the one that matters most

### `screening` could not be filtered by the policy that filters it

S-6.1 held `screening` as a flat `Sequence[JsonValue]`. S-6.4 invalidates **per
workload** — the workloads whose files a patch touched. Nothing in a flat
sequence of opaque entries says which workload an entry belongs to, so S-6.4
produced a correct answer that **could not be applied**: a caller had to rebuild
the channel by hand, from a shape that does not carry the identity the rebuild
needs.

Neither story could see it. One owns the shape and knows nothing about the rule;
the other owns the rule and takes coverage as an argument. `screening` is now
`Mapping[str, JsonValue]` keyed by workload id, and `apply_ship` is the rebuild,
written once.

### `experiments` accepted a full measurement

S-6.3 bounds a checkpoint by storing references; S-6.1's channel is
`list[JsonValue]`. A node that appended the measurement itself satisfied the
schema, the reducer **and** `check_update` — every check either story makes — so
F13's guarantee held exactly as long as every caller remembered it. A test
reproduces that against the shipped modules before the fix.

`record_experiment` is now the one way in, and `check_state` refuses a log
holding anything that is not a reference. It also enforces the size limit, which
had the same shape of gap: S-6.3 proved the bound for 40 references and S-5.4
caps investigation at 40, but the two live in different modules and nothing
joined them, so a run that never consulted the budget went past both.

### Nothing produced coverage, so nothing could ever be shown untouched

S-6.4 is right that unrecorded is not untouched, and the consequence is that
every workload stays invalidated until something records what it runs. But an
investigation that stored its results by reference already holds part of that
record: the reference carries the experiment key, and the key names the workload.
`coverage_from_state` reads it back rather than asking for it again.

**It is workload-granular, not file-granular, and says so.** It cannot tell you
which source files a workload executed — that is S-3.9's stacks — so it does not
pretend to. What it removes is the case where workloads an investigation never
touched are invalidated for want of any record at all.

## Consequences

**The epic's sentence is now one flow.** Four experiments recorded by reference,
a failure learned into the persistent store, a crash resumed from its checkpoint,
and a rewind that takes the state back while leaving the learning in place — run
against a real graph, a real checkpointer and a real Postgres store.

**Sabotage-verified on twelve properties, all caught — after four survived the
first pass, and three of those were weak tests rather than weak code.** The
size-limit test built its oversized state from raw measurements, so it failed the
*reference* check first and removing the size check broke nothing. The
shipped-finding test left the patched finding out of the pending list, so
removing the filter changed nothing. And there was no test for an out-of-range
index at all. That is the sixth time in this project a passing sabotage has meant
a weak test.

**The fourth survivor was an artefact of the harness, not of the code.** The
runner passed `-m "not slow"`, which deselected the Postgres tests — and the
assertion that `learn` writes to failure memory lives in one. Re-run without the
filter, it was caught. A sabotage harness that excludes tests silently reports
properties as unverified; the Epic 5 run was invalidated by a different fault in
the same script, and both were only visible because the baseline is re-checked
after the pass.

**Makes hard.** `screening`'s shape changed, which is a change to a shipped
story's schema. It is the right shape — F14 requires per-workload addressing and
nothing else in the project reads the channel yet — but S-4.3's `Ranking` will
have to be keyed that way when E12 wires screening into the graph.

**Rules out.** A checkpoint log holding measurements, a staleness answer that
cannot be applied, and the claim that Epic 6's parts fit together because each
one passes its own tests.

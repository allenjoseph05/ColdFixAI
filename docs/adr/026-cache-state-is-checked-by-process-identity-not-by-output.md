# 026 — Cache state is checked by process identity, not by output

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.7 asks for a harness that runs seed → workload → reset ten times, asserts
row counts identical, checks **sequence counters and cache state** as well, and
falls back to the next strategy with a clear diagnostic when a reset is
unreliable.

Three of those four are settled by S-0.5, which found that row counting alone
certified a reset failing on every cycle: plain rollback returned identical row
counts, identical content hashes and identical `max(id)` across ten cycles while
the sequences climbed 250 higher. The fingerprint therefore has four parts and
the fourth is the one that earns its place.

**Cache state is the one the spike left unsolved.** It found a Django
`QuerySet` still reporting a row that had been rolled back, because the rows sit
in a Python object no database-side reset can reach, and concluded that *the
reset contract has to cover the process, not just the database*. Nothing the
harness can query sees it.

## Decision

**The workload's observation is compared for equality, and it is not the cache
check.** The tempting design — and the one this ADR exists to record as wrong —
is to catch a stale cache by noticing the workload says something different. It
cannot work, and the reason is worth stating because it is not obvious:

> A workload with a stale cache returns the same value every cycle. A workload
> with no cache also returns the same value every cycle, because a correct reset
> makes every cycle identical. The two are indistinguishable by output.

This was found by writing the test, watching it pass for a leaking workload, and
then working out why. The observation comparison stays — it catches state the
fingerprint cannot reach, such as a table it cannot hash or a file — but it is
not evidence about caches.

**Cache state is checked by requiring `process_identity` to differ on every
cycle.** A process that survives from one cycle to the next is a process that
can carry a cached row no reset here will ever clear. That is the *condition*
for the defect rather than the defect itself, and it is checkable without
knowing anything about the framework, which output comparison is not.

ADR 025 claims this is already guaranteed — S-2.1 destroys the container after
every run, so the process holding a cache does not survive. This is what turns
that claim into something checked, and what would notice if containers were ever
made persistent between runs as an optimisation.

**Supplying no `process_identity` skips the check.** That is a real hole and it
is documented as one at every level, because skipping it is precisely how
S-0.5's defect returns unnoticed.

**The content hash orders rows by their own text.** A restore does not preserve
physical row order, so a hash folded in table order would report every correct
strategy as broken. The ordering is not tidiness; without it the harness rejects
everything.

**An unreliable reset is a report, never an exception.** `SNAPSHOT_RESTORE`
exists precisely because rollback cannot undo what another connection committed,
which is the normal case for a containerised workload — so a strategy failing is
expected traffic, and the caller's response is to try the next one. Only running
out of candidates raises, and that error carries every report.

**`VerifiedReset` cannot be constructed from a failing report.** S-2.6's
criterion says each strategy is verified *before use*; making the verified state
a type is what turns that from an instruction into something a caller cannot
skip, because the object needed to run experiments is the one only verification
produces. Third use of this construction, after `VerifiedDatabase` and the
session types.

## Consequences

**Makes easy.** Adding a fifth check: it is one more comparison in `_compare`
and one more `kind` string in the diagnostic. Telling a user which strategy to
use, and why the cheap one was rejected.

**Makes hard.** Verifying a project whose workload cannot report a process
identity. Those get four checks instead of five and a documented gap rather than
a false assurance.

**Rules out.** Trusting a reset because it is implemented, and trusting output
comparison as a cache check.

**Left open.** The harness verifies against *a* workload, and a reset that
returns the state after one workload may not after another — a workload that
writes a file or sends an email is reset by no strategy here, which S-0.5 also
flagged. Verification is evidence about the workload it ran, and S-7.x's
workload artifacts are where that pairing gets recorded.

## Provenance

`docs/10-BACKLOG.md` S-2.7 and S-0.5's recorded result;
`spikes/S-0.5-reset/FINDINGS.md` for the four-part fingerprint and the
cached-queryset probe.

Sabotage-verified on five properties, each with an assertion that the edit
actually applied — a precaution added after ADR 024, where two sabotages
silently no-opped and read as evidence for properties they never tested.
Reducing the fingerprint to row counts alone fails 3 tests, including the one
that reproduces S-0.5's defect. Dropping the observation comparison fails 1.
Dropping the process check fails 1. Making `choose_reset` return its first
candidate regardless fails 2. Removing the `ORDER BY` from the content hash
fails the test asserting that reordered rows are not drift.

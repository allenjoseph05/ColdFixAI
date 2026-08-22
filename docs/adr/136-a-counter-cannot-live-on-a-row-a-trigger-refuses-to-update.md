# 136 — A counter cannot live on a row a trigger refuses to update

**Status:** accepted
**Date:** 2026-08-22

## Context

S-13.2 is Epic 13's **SAFETY** story. `08-audit.md` F4 names the flaw it exists
to close:

> The Explorer writes playbook entries that all future runs trust. A wrong entry
> — *"DRF always uses TokenAuthentication"* — propagates silently and compounds.
> Nothing validates a write.

Its fix has four parts: entries are provisional on write and **carry a use
counter with success and failure tallies**; promotion needs N successes across
different projects; two failures demote and quarantine; entries are scoped by
fingerprint.

The second clause of the first part turned out to be impossible as written.

## Decisions

### 1. The tally is derived, because the row cannot be updated

`persistent.py` installs a **database trigger** refusing `UPDATE`, `DELETE` and
`TRUNCATE` on the journal, and its docstring says why: *a guard that lives in
Python protects the callers who go through Python.* A counter on the entry could
never be incremented.

So a use is its own appended row and the tally is a fold over them. **That is a
better answer than the one the audit imagined**, and worth saying rather than
treating as a workaround: the evidence for a promotion is itself append-only, so
nothing can quietly raise a count. A stored counter would have been one number
that anybody with a connection could set; a fold over immutable rows is a claim
with its receipts attached.

### 2. Quarantine is checked before trust, and the order is the property

An entry that failed twice is quarantined however many successes it also has.
F4's remedy for a poisoned entry is that it stops being offered, and a rule where
successes outweighed failures would let a widely-repeated mistake earn its way
back — which is the compounding F4 names.

Sabotaging the order fails a unit test **and** the store test. Both, because the
rule has to hold in the fold as well as in the type.

### 3. N is three, and the reasoning is recorded because F4 does not fix it

- **Two** is the smallest number for which *different projects* means anything at
  all, so it is the weakest reading that satisfies the words.
- **Three** is the smallest that survives one coincidence. Two projects sharing a
  wrong belief is ordinary when both were built from the same tutorial.
- It must also **exceed the demotion threshold**. At two and two, an entry with
  two successes and two failures is simultaneously promotable and quarantined,
  and which wins is decided by the order of two `if`s rather than by a reason.
  Trust being strictly harder to reach than quarantine is the asymmetry a safety
  property wants, and a test asserts the inequality rather than the numbers.

### 4. Promotion counts projects, not uses

`note_use` refuses an empty project. Fifty successes on one project is one
project's opinion — which is F15's finding about the trust ledger (*a
`select_related` fix approved 50 times may have been on projects with narrow
tables*) reached from the playbook side.

### 5. The row and the thing are two schemas

`PlaybookEntry` stays the three fields S-13.1's criterion names — adding a fourth
is what that story refused — while the journal *row* carries a `kind`
discriminator, because uses are filed under the same key and a reader must tell
them apart.

Explicit rather than inferred from shape: a row tagged `use` is a different
record and is skipped; a row tagged neither is one nobody can account for and
raises. Guessing from which fields happen to be present would collapse those, and
a malformed entry would disappear as quietly as a use.

## Consequences

**AC 5 is proven against the real journal**, with F4's own example as the poison:
project A learns *"DRF always uses TokenAuthentication"* and it works there once;
project B shares the fingerprint, so it **sees** the entry through `recall` and
gets nothing from `trusted`. Readable as context, not actionable — which is the
distinction `resolve_auth` was already built around, carrying entries unread.

There is still no production writer. S-13.1 deferred it to this story on the
grounds that a writer before the gate *is* the propagation; the gate now exists,
and wiring the Explorer to record what it learned is the next piece of work
rather than part of this one. What changed is that it is now safe to do.

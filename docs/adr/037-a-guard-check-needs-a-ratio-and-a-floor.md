# 037 — An envelope check needs a ratio and a floor, and the floor is measured

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.8 asks that every primary counter declare a guard counter; that *additionally*
every candidate be measured against a global envelope of peak RSS, total CPU,
wall time, bytes written, file descriptors and process count; that any envelope
metric outside tolerance be flagged whether or not that trade was predicted; and
that a test prove a patch trading queries for a memory explosion is flagged.

`08-audit.md` F10 states the reason in four words: **guard counters are a
denylist.** A pair catches the trade somebody wrote down — queries against rows
returned — and catches nothing else. The trades worth catching are the ones
nobody listed, and a denylist cannot contain them by construction.

## Decision

**Every guard resolves, and it is checked at import.** S-3.6 left two guards as
prose (`"http.request, by response size"`) and three as `None`. Both are guards
that guard nothing while looking like guards. `guard` is now a required
reference that must name either another catalogue counter or an envelope metric,
`_check_guards_resolve` runs when the module is imported, and a counter added
with a dangling guard fails the import rather than the investigation. Six
catalogue entries were added so the references have somewhere to point —
`http.bytes`, `memory.bytes` and the three `blocked.*.calls`, each of them the
other reading of a hook that already exists.

**A guard may point at an envelope metric.** `file.open` has no counter to be
traded against: opening fewer files is bought by writing more through the ones
left open, and nothing here counts that. Forcing a counter-to-counter pairing
would have meant inventing a counter to satisfy a rule.

**The envelope checks every metric, always, and has no argument for expectations.**
`compare` takes two samples and a tolerance table. There is no parameter through
which a caller could say which trade to expect, because the entire difference
between this and a guard pair is that nothing consults a prediction.

**Increases flag; decreases never do.** A candidate exists to make something
smaller, and a two-sided check would flag every successful patch for the
improvement it was written to make.

**A rise must clear a ratio *and* an absolute floor**, and this is the finding of
the story. It arrived as the check failing its own control: two identical runs of
the same function, 2.4ms and 2.7ms, an 11% rise past a 10% tolerance, and nothing
whatsoever had happened. A ratio alone flags noise on anything small; a tolerance
loose enough not to would miss real trades. So both tests must pass, and **the
timing floor is not a guess** — it is S-0.4's measured noise floor, roughly 20ms,
about 6% of a 350ms endpoint. Counts get floors at the other end of the scale:
two file descriptors becoming three is a 50% rise and is nothing at all.

**Retained memory is a difference; peak RSS is a level.** The distinction is not
tidiness. `sys.getallocatedblocks()` is an interpreter-wide level, so its ratio is
diluted by everything else the process holds — measured under pytest, a run
retaining 24,000 blocks against a 200,000-block interpreter reads as a 12% rise
and passes the check. Differenced, it is what *this block* retained, which is
precisely what a cache is. Peak RSS stays a level because the peak is the number
and differencing it would report the peak of a difference.

**Unavailable is named, not passed.** Three metrics need `getrusage` or `/proc`,
which the Linux sandbox has and the Windows development host does not. A guard
check that quietly passed on the metrics it could not see would carry the same
reassurance with none of the coverage, so the report lists what it could not
read and says outright that it covers less than a sandbox run would. Two portable
siblings — retained blocks and thread count — exist so the memory and process
questions have *some* answer everywhere, not to stand in for the real ones.

## Consequences

**Makes easy.** S-10.6's slack-reducing classifier, which needs exactly this
check, and the metastability gate in `00-BRIEF.md` §4 — the patch that motivated
the whole envelope is a cache, and a cache is what that gate is about.

**Makes hard.** Setting tolerances for a subject whose ordinary run-to-run
variation is larger than the floors here. That is a real limit and the floors are
constants a caller can override, not a policy hidden in the comparison.

**Rules out.** Reporting a candidate as clean because the one metric its trade
used was not on anybody's list, and reporting it as clean on a platform where
that metric could not be read.

## Provenance

Five sabotage runs, each asserting the edit was detected: treating unmeasured
metrics as checked fails 2 tests; checking a chosen few metrics rather than all
of them fails 10; removing the absolute floor fails 2; flagging decreases as well
as increases fails 1; disabling the import-time guard check fails 1.

Two of this story's own tests failed before the code did, and both were right to.
The first candidate "patch" did not actually reduce the query count — it called
the same method forty times either way — so the AC-4 test was asserting an
improvement that was not there. The second was the noise failure above, which is
now a constant with a citation rather than a tolerance chosen by taste.

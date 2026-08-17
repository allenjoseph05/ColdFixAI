# 067 — Unrecorded is not untouched

**Status:** accepted
**Story:** S-6.4 — post-patch staleness policy
**Date:** 2026-08-11

## Context

Two flaws in `08-audit.md` ask the same question about different artifacts.

**F14:** after `ship` the graph returns to `screen`, but the code has changed and
every prior screening measurement is stale — *re-screen only the workloads whose
files the patch touched; others keep their measurements. Cheap, and correct.*

**§6, interacting findings:** two findings in one file, fixed in sequence, and
the second patch is written against pre-first-patch source — *invalidate any
pending finding whose `context` files the patch touched, and re-investigate
rather than repair from a stale chain.*

One mechanism, two consequences. The obstacle is that **nothing records which
files a workload touches.** S-4.1's `Workload` has an entry point, a fixture and
a reset method, and no notion of the source it executes. The honest source for
that is a measurement — S-3.9 captures stacks, and a stack frame names a file —
so this module takes coverage from its caller rather than inventing it.

## Decision

### Unrecorded is a third state, and it invalidates

A workload whose files nobody recorded cannot be *shown* unaffected. Flattening
that absence to "fresh" keeps a measurement the patch may have invalidated, which
is exactly the route to the Surgeon repairing from a stale chain that §6 exists
to close. So coverage is `FRESH`, `STALE` or `UNCOVERED`, and both non-fresh
states mean measure it again.

`UNCOVERED` stays **distinct from** `STALE` in the report while being identical
in the action, because they call for different fixes: a stale workload needs
re-screening, an uncovered one means nobody is recording what the workloads run.
Reported as one number, a missing instrument hides behind a routine
invalidation. S-3.1 made the same split, four ways, for the same reason.

The distinction is carried in the type as well: `Coverage.files` is
`frozenset[str] | None`, and `Coverage.unrecorded(subject)` is a named
constructor, so a caller with no coverage has to *say so* rather than pass an
empty set. An empty set is a claim that the subject runs no modified file; `None`
is the absence of a claim.

### There is no disposition meaning "repair from a stale chain"

`FindingAction` has two members, and only `FRESH` yields `REPAIR`. §6's rule is a
property of the type rather than a branch somebody has to remember. The test
enumerates `Freshness` rather than spot-checking, so a state added later has to
decide what it means here instead of defaulting to repairable.

### Paths are normalized to one form, and an unnormalizable one is refused

A patch's modified files come from git: repo-relative, forward slashes. A
workload's touched files come from stack frames: absolute, and on Windows with
backslashes. **Intersecting those two forms yields the empty set, every workload
reads as unaffected, and nothing raises.** The failure is silent and lands on the
flattering side, so `repo_path` refuses an absolute path it has no root to
relativize rather than guessing.

**A test found this same class of bug in the guard itself.**
`PurePath.is_absolute()` is platform-dependent: on Windows
`/home/allen/subject/src/api.py` has no drive letter, reports `False`, and would
have been normalized to `home/allen/subject/src/api.py` — a repo-relative-looking
path matching nothing git ever reports. The same input is correctly refused on
Linux. A guard whose answer depends on which machine ran it is precisely the
failure this module is about, and `_absolute_anywhere` now checks both flavours.

## Consequences

**Makes easy.** E12's post-ship edge has one call: `after_ship(coverages, patch)`
returns what to keep, what to screen again, and which pending findings must be
re-derived. F14's saving is real — untouched workloads keep their measurements.

**Makes hard.** The policy is only as good as the coverage it is given, and today
nothing produces it. Every workload will report `UNCOVERED` until something
records what they run, which means everything is re-screened after every ship.
That is the correct behaviour for the current state of the system and it is
visible in the report rather than silent — the alternative was a policy that
looked like it was saving work while keeping stale measurements.

**Rules out.** Treating "we did not measure it" and "it was not affected" as the
same answer, and any path comparison that depends on which platform ran it.

**Sabotage-verified on fifteen properties.** Fourteen were caught on the first
pass; the fifteenth survived and the fault was in the *test*, not the code — it
asserted the stale file's name appeared in `report.describe()`, which also prints
the patch's own modified files, so the assertion passed via the header line
whether or not the per-workload line said anything. Now asserted on the
assessment's own line. That is the fifth time in this project a passing sabotage
has meant a weak test rather than weak code.

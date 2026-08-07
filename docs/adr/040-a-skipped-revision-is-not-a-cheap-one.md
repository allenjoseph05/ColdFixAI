# 040 — A skipped revision is not a cheap one, and one threshold oracle serves both searches

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.11 asks for a worktree checkout of an earlier revision running the same
workload, a bisect across a commit range, revisions that fail to build reported
and skipped, and the workload verified to exist at both endpoints before
starting.

`01-primitives.md` §6 says why this primitive punches above its weight: it needs
**no understanding of the code at all**, is fully automatable, and produces the
most actionable output anything here can — a specific commit and a specific
number. It also names the two failure modes: older commits may not build, and the
workload must exist and be runnable at both points in history.

## Decision

**`Oracle` was generalized rather than duplicated.** S-3.5 built a threshold
oracle with a noise band, an outcome cache and an append-only probe log, and a
bisect needs exactly that keyed on a revision instead of a subset. It is now
generic over its subject. Two threshold oracles with separately-invented noise
semantics would produce findings that disagree for reasons nobody could see, and
this is the second caller — the point `CLAUDE.md` permits an abstraction to
appear.

**A revision that cannot be measured is skipped, never counted as cheap.**
Counting it as cheap moves the boundary past it and yields a confident wrong
commit; counting it as expensive does the same in the other direction. Skipping
and trying a neighbour is what `git bisect skip` does, and for the same reason:
an old revision needing a dependency nobody can install any more is ordinary in a
real repository, not a reason to abandon the search. Candidates are tried nearest
the midpoint first, because the closer a usable revision is to the middle the
less the skip costs.

**A range where everything between the ends is unmeasurable reports the pair.**
The regression is in there and these revisions cannot say where — a smaller
answer than a commit and a far better one than the wrong commit. The explanation
names every skipped revision so a reader knows the crossing may have happened at
any of them.

**The noise band is a skip too.** A revision whose cost lands inside the
resolution decides a step of the bisect on noise, and every step after it
inherits that choice. It is `UNRESOLVED`, which the search already knows how to
route around.

**Both endpoints are measured before anything is bisected**, and each failure is
its own message:

- The older end cannot be measured → most often the workload did not exist yet,
  and a bisect over that range returns *the commit that added the workload*. That
  commit is real, and it is not the regression. This is AC 4, and it is the
  failure worth the most words.
- The older end is already expensive → the regression is older than this range.
  Extend it backwards, or record that the cost was always here, which is an
  exclusion rather than a failed search.
- The newer end is not expensive → there is no regression in this range. Also a
  result.

**Every revision is measured in its own worktree, destroyed in a `finally`.**
S-2.2 owns worktrees; what matters here is that the diff from an old revision to
the current one is a revert of everything since, so a checkout that outlived its
measurement would be exactly that text sitting on disk.

## Consequences

**Makes easy.** The most actionable finding this system can produce, from a
primitive that needs no hypothesis and no instrument selection. Reusing the
oracle again for any future search over a threshold.

**Makes hard.** Bisecting a range whose ends do not behave — refused with the
reason rather than answered — and bisecting where nothing between the ends
builds, which reports a bracket instead of a commit.

**Rules out.** Treating an unbuildable revision as a data point, and reporting a
commit from a range that was never verified to contain a crossing.

## Provenance

Four sabotage runs, each asserting the edit landed: treating an unmeasurable
revision as cheap fails 3 tests; removing the endpoint verification fails 3;
destroying the worktree outside a `finally` fails 1; narrowing the bisect the
wrong way fails 3.

Two test defects were found before any sabotage. The history fixture wrote the
same cost in consecutive commits, and git refuses a commit that changes nothing —
half the history is deliberately flat, so every test errored in setup with an
empty stderr. And the noise-band test poisoned its own endpoint by banding every
revision that shared the endpoint's cost, which the endpoint check then correctly
refused.

**A scripted `str.replace` silently matched nothing for the second story
running**, this time applying one of two edits and leaving the other — which read
as a passing fixture change that had not happened. Both stories lost a cycle to
it. Edits to existing files are made with a tool that fails when its target is
absent; a replace that returns the string unchanged is not an edit, and asserting
the whole file changed does not prove that every part of it did.

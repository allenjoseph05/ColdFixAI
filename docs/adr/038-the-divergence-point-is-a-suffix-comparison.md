# 038 — The divergence point is a suffix comparison, and a sample suffices

**Status:** accepted
**Date:** 2026-08-07

## Context

S-3.9 asks for stacks normalized against an adapter-supplied deny list, grouped
by signature, walked to the divergence point — the deepest frame common to all
occurrences — and emitted as a causal site plus a dependency closure, with async
boundaries handled or reported as unhandleable.

The story's note carries the claim that makes it worth building: *this is how
findings span multiple files without the agent reading the repository. The
runtime names the files.* An evidence chain saying **the N+1 is at
`views.py:41`, reached from `list_tickets`** is worth far more than one saying a
number is large, and none of it requires a model to look at source.

## Decision

**Stripping happens everywhere in the stack, not at the ends.** A real stack is
framework at the bottom, the subject in the middle, request handling above that
and the server above that. Keeping only the subject's frames is what leaves a
signature worth grouping on; stripping only the innermost run leaves forty frames
of framework in every signature and groups nothing.

**The divergence point is the longest common suffix, and its first element is the
site.** Stacks are innermost-first, so the frames every occurrence shares are a
suffix and the deepest shared frame is that suffix's head. One computation
answers both shapes this meets:

- An N+1 produces identical stacks, so the whole stack is common and the site is
  the innermost frame — the line in the loop.
- Events from two different sites share only their caller, so the site is the
  function that calls both.

The tempting alternative — take the innermost frame of the largest group — gets
the N+1 right and the second case wrong, which is why the second case has a test.

**A sample localizes as well as a census.** Grouping is by distinct route and the
walk is over the groups, so the site does not depend on how many times each route
was taken. This is the mitigation S-3.6 handed over: capturing a stack costs
about 1.4µs per frame of depth, which at a realistic framework depth is 86µs an
event against a 366µs database call. Sampling is safe **by construction** here
rather than by a caller remembering to be careful, and a test asserts the sampled
and complete localizations agree.

**Two things are reported rather than guessed.**

Occurrences whose every frame belonged to the framework have no site in the
subject's code. Grouping them under an empty signature would invent a shared site
they do not have, so they are counted apart — and the fact is itself a finding,
because a cost inside a dependency is what S-2.9 already routes to diagnose-only.

A stack captured inside a coroutine shows the loop that resumed it rather than
whatever awaited it, so the callers past that point are the scheduler. The group
carries a flag saying the trail goes cold there, which is the AC's *or reports
that it cannot*. Naming `base_events.py` as a culprit would be worse than saying
nothing.

**The closure is honest about which half came from where.** Callers are exact,
because the runtime recorded them. The source excerpt is read by the harness —
the agent is what must not read the repository, not the harness. **Models and
relationship declarations are framework knowledge and come from an adapter's
resolver**, and with none supplied the closure says they were *not resolved*,
which is not the same as there being none. A closure over nothing is refused
outright, because an empty closure reads as *a site with no dependencies* and
that is a different claim from *there is no site*.

**`Frame` is ordered as well as hashable.** Groups with equal occurrence counts
would otherwise come out in an arbitrary order — and these end up in a prompt,
where ADR 002 makes a reordering an invalidated cache.

## Consequences

**Makes easy.** An evidence chain that names files and lines without a model
reading any of them, which is E8's whole input. Sampling stack capture, now that
the algorithm is known not to care.

**Makes hard.** Localizing a cost whose stacks genuinely share nothing — reported
as such rather than resolved to an arbitrary frame — and localizing across an
async boundary, which is not solvable at this level and says so.

**Rules out.** Reporting a framework frame as the causal site, and reporting a
site for occurrences that share none.

## Provenance

Five sabotage runs, each asserting the edit was detected: stripping only the
innermost run of framework frames fails 2 tests; grouping framework-only
occurrences under an empty signature fails 2; taking the busiest group's
innermost frame instead of the divergence point fails 3; disabling async
detection fails 3; returning an empty closure instead of refusing fails 1.

Two defects were found by the tests before any sabotage. `Frame` was hashable but
not orderable, so the group sort crashed whenever two groups had the same
occurrence count — which is exactly the unrelated-occurrences case. And a
Windows-path test was written with doubled backslashes inside a raw string, so it
was asserting about a path that contained `\\` and not the one it meant.

Separately, S-3.7's `test_elapsed_time_is_still_divided_when_the_block_raises`
failed here on timing: `process_time` ticks at about 15.6ms on Windows, so
`wall - cpu` can understate blocked time by a whole tick — a 0.15s sleep
reporting 0.1347s blocked. The assertions now allow one tick, with the number
named and explained, rather than a looser fraction that would have stopped being
an assertion.

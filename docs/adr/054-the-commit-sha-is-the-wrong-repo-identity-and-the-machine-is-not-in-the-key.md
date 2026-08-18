# 054 — The commit sha is the wrong repo identity, and the machine is not in the key

**Status:** accepted
**Story:** S-5.1 — experiment replay cache
**Date:** 2026-08-09

## Context

`04-cost.md` §6 specifies the replay cache key exactly:

```
key:    (repo_sha, workload_id, experiment_spec, fixture_hash)
value:  full measurement result
```

Two things the specification does not settle turned out to decide whether the
cache helps or harms, and both were only visible once the key was written down
next to what the cache is actually for.

## Decision 1 — `repo_sha` identifies the working tree, not the commit

`git rev-parse HEAD` is the obvious reading of the field name and it is wrong for
this cache's primary use.

§6's stated purpose is development speed: *debugging the Surgeon means replaying
a recorded investigation in seconds instead of re-running ninety minutes of
grounding and experiments*. Debugging means an uncommitted working tree. The
commit sha does not move for a whole afternoon of editing, so a cache keyed on it
returns a recording made **before the change under test** on every lookup — and
the faster the cache is, the more convincingly it does so. A stale hit does not
present as an error; it presents as a fix that appears to do nothing.

So `repo_identity(repo)` returns the commit sha on a clean tree, and
`<sha>+<digest>` where the digest covers `git diff HEAD` plus the contents of
untracked, non-ignored files. Three properties follow, and each is a test:

- an uncommitted edit changes it;
- **two different edits to the same file change it differently** — this is why
  the digest is over `git diff HEAD` and not over `git status --porcelain`, which
  reports the same thing for every edit to a file and would therefore move once
  and then hold still for the rest of the session;
- reverting to what was committed identifies as the clean tree again, so
  recordings made against a commit survive checking it out.

Ignored files are excluded. A virtualenv is not part of the experiment, and
hashing one would cost seconds on every key the cache builds.

The function does not use S-1.1's `execute`, which bounds captured output and
reports how much it dropped. A truncated diff hashes identically for two working
trees that differ past the limit — a stale hit rather than a visible failure — so
this needs the bytes rather than a report about them.

## Decision 2 — the environment partitions the store, it is not a fifth key field

A measurement result is mostly numbers that belong to a machine. `seconds`,
`cpu_seconds` and `blocked_seconds` are what one CPU did under one scheduler, and
S-0.4 measured the timing floor on *this* machine at roughly 20 ms. Query counts
would travel between machines; the durations recorded beside them would not, and
they are in the same artifact.

The four key fields are kept exactly as `04-cost.md` specifies. The environment —
system, architecture, hostname, Python version — names the **directory** the key
is looked up in. A recording from another machine therefore *misses*, rather than
matching a key it is entitled to match.

Making it a directory rather than a key field is the point. As a fifth field it
would be a value somebody could later be tempted to compare loosely — *close
enough, same OS* — and there is no defensible threshold for how similar two
machines have to be before one's durations may stand in for the other's. As a
partition there is no question to answer.

The cost is that a shared or committed cache never hits across machines. That is
the correct semantics rather than a limitation: a measurement from another
machine is not your measurement.

## Decision 3 — `run` returns a `Recall`, not the result

`CLAUDE.md`'s first non-negotiable is that there is no finding without a
measurement. A replayed number *is* a measurement — it happened, on a recorded
date, on a recorded machine — but it did not happen now, and a bare `T` makes a
recording indistinguishable from a fresh run at every call site downstream.

`Recall` carries `hit`, `recorded_at` and the environment, and `provenance()`
renders the sentence a report can quote. The caller has to unwrap it, which means
every call site is aware it may be holding a recording. This is the same rule the
rest of the project follows under the name *exclusions carry their preconditions*.

## Consequences

**The spec is still declared, and nothing here can check it.** `experiment_spec`
is the one key field not derivable from an artifact, and a spec that omits a
parameter which determines the result produces a hit from a different experiment,
silently. Two things narrow it and neither closes it: `repo_identity` covers
everything that lives in the code behind the experiment's callables, and
`ExperimentKey.of` derives `workload_id` and `fixture_hash` together from one
`Workload` so that the two fields describing the subject cannot describe different
subjects. Beyond that the spec is structured rather than free text, so an omission
is at least *visible* to somebody who opens the recording.

**A cached experiment's side effects do not happen.** A hit skips the seeding, the
reset cycle and the run. Anything downstream that depends on the subject being in
the state the experiment left it in must not sit behind this cache. Tested from
the subject's side: a replayed screen leaves every bound workload uninvoked.

**Recordings are JSON, not pickles.** S-5.2 replays recordings to debug downstream
agents, and the first thing anybody does with a recording that produced a
surprising answer is open it. A pickle would be faster and would make that
impossible — and would also execute whatever it contained.

**An unreadable entry is a miss, and is counted apart from one.** A truncated file
and a recording whose result schema has moved on both want the same treatment —
run it again — so neither is an exception a caller must catch. But collapsing them
into the miss count would let a cache that has silently stopped working forever
look exactly like a cold one, so `CacheStatistics` reports `unreadable` on its own.

**Asking for the wrong result type raises.** Treated as a miss it would be
invisible and self-inflicting: two callers sharing a key with different result
types would each recompute and overwrite the other's recording, so both would
work, neither would ever hit, and the statistics would report a cold cache.

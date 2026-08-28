# 162 — A hook is a harness metric, and the subject must report its own counts

**Status:** accepted
**Date:** 2026-08-28
**Amends:** ADR 158

## Context

S-17.10 produces the first `Binder`: a `Workload` artifact turned back into a
`BoundWorkload` whose numbers come from the subject. ADR 157 decided that the
subject measures itself; ADR 158 made *the subject measured this* a type. This is
the first thing that constructs one, and doing so found that the design as it
stood could not run.

## The measurement

A subject-vantage binding reports `db.query`, because that is the count the subject
took of itself. A real screen also installs a `db.query` hook —
`Resources.counters` has been required since the Epic 16 composition check. Run
together:

```
measure_once(invoke, [DB_QUERY], Reported(...))
-> MetricSetError: extra counters ['db.query'] would overwrite counters this run
   took from its own hooks; name them differently
```

With no hook installed the same call is accepted. **So the first screen of the
first workload of a real run raised**, and the message's advice was wrong here:
renaming to `subject.db.query` gives screening a metric it has no expectation for,
so the count would be measured, fitted, and never compared against anything — the
N+1 the system exists to find would never be flagged.

## Decision

**A hook counter is refused under the subject vantage.**

ADR 158's rule was right and its enumeration was incomplete. `HARNESS_ONLY_METRICS`
is `materialized`, `cpu_seconds` and `blocked_seconds`, chosen because each is *a
number this process produced about itself*. A hook tally is one of those:
`count()` installs an in-process wrapper, so against a subject running somewhere
else it counts zero — always — and files that zero under the name the subject's
real count belongs to.

ADR 158 kept hook counters under `SUBJECT` on the strength of a case that does not
exist: *the Django adapter's `execute_wrapper` is in-process, so a binding that can
reach the subject's connections from here keeps its query count*. A subject the
harness can reach in-process is a subject the harness can time, which is `HARNESS`.
The two vantages are not a spectrum.

The refusal names the process rather than the collision, because the collision
message pointed at the fixable-looking half. What the caller must change is where
the number comes from, not what it is called.

**And the caller must not silently lose the metric.** `screen_growth` declines to
install hooks against a subject-vantage binding, and then requires the subject to
have reported every counter it was asked for. Dropping them quietly is the Epic 16
composition check's failure with a new cause: a screen that measured no queries
could not verify the work and emitted a null result covering nothing, saying so
only in a field nobody read.

So `counters` now means *what must be measured*, not *how*. Under the harness
vantage they are hooks; under the subject's they are a requirement on the report.

## The cell, which is the binder's real risk

`scale_volume` runs reset, seed, invoke, and *then* reads the counters, once per
scale point. The binding's `invoke` drives the subject and its `Reported` hands
back what that drive measured, so something has to hold the drive in between.

**A cell that survived into the next point would report the previous scale's
numbers, and a growth fit over one measurement repeated is `CONSTANT`** — S-17.5's
failure reached by a different route, and it would publish as a healthy exclusion.

So `_Latest.taken()` clears as it returns. Reading twice, or reading before any
drive, raises rather than handing back a number the current invocation did not
produce. Sabotage confirms: never clearing the cell fails the test directly.

## Cache control rests on a `Surface` invariant, measured

S-3.2 refuses a sweep with no cache control, and this binding satisfies it by
process identity — *the interpreter that served one scale point is not the one that
serves the next*. That claim is only honest because **every `Surface.run` starts a
new process**: `execute` spawns a subprocess, and `Sandbox.run` creates a container
it destroys before returning.

Measured for the host rather than assumed — three runs, three distinct pids — and
structural for the session, because ADR 004 makes the destruction unconditional.
The invariant is now stated on the protocol and tested in
`tests/explorer/test_surface.py`, so a surface that pooled processes would fail
there rather than silently invalidating every screen taken through it.

## Consequences

**Three of the six are real**: `hands`, `ground`, `bind`. `measure`, `executor` and
`probe` remain, and nothing assembles a `Resources`, so S-17.1 is still not a run.

**Two of S-17.6's tests were replaced rather than adjusted**, because they asserted
the rule this ADR reverses. Their replacements say what the old rule was and why the
case behind it does not exist — the reasoning is worth more than the assertion.

**The binder synthesises rather than using the repository's own factory.** S-7.5
prefers a real factory, but re-deriving one needs the module it is importable from,
which `FixtureRecipe` does not carry. A binding that guessed would seed a different
table than the one the screen names. Closing that means widening the artifact, which
is a decision about what a checkpoint holds and belongs to its own story.

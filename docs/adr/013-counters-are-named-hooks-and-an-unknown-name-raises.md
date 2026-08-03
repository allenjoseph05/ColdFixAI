# 013 — Counters are named hooks, and an unknown name raises

**Status:** accepted
**Date:** 2026-08-03

## Context

S-1.3 specifies `count(hook_name)` as a context manager returning an integer and
a list of stacks. The name is a lookup, which means there is a registry, which
means there is a case where the lookup misses.

The obvious handling — return an empty count — is catastrophic here, and it is
catastrophic for a reason specific to this project rather than to software in
general. Zero is a **valid published result**. `00-BRIEF.md` §9 lists null
results as shippable output, and `02-architecture.md` §2.2 makes recording
exclusions a hard rule: *"Not the database, queries flat at 7,7,7 across 100×
scale"* is an answer the system is designed to emit and a human is expected to
act on.

So an instrument that answers zero when it is not attached does not produce an
obvious bug. It produces a finding of exactly the shape the system is built to
produce, indistinguishable from a real one, and it survives review because
"we screened it and found nothing" is an expected outcome. A misspelled
instrument name would become evidence of absence.

The second decision was scope. S-3.6 defines the actual counters — queries,
rows, bytes, HTTP requests, file opens, allocations — and S-14.1 makes hook
points part of the adapter interface. S-1.3 sits below both.

## Decision

**`count()` raises `UnknownHookError` for an unregistered name.** It never
returns zero for an instrument that is not attached. The exception carries the
list of registered names, because the realistic cause is a near-miss
(`db.queries` for `db.query`) and the diagnosis is the difference between the
two strings.

`register_hook` raises on a duplicate name rather than replacing. Two adapters
disagreeing about what `db.query` means yields measurements that are wrong;
refusing yields measurements that are missing, and missing is recoverable.

**This module ships the mechanism and no counters.** A `Hook` is a callable that
takes a `record` callback and returns a context manager with the instrumentation
installed. Adapters register them (S-14.1); the named counters are S-3.6. The
registry is the extension point `CLAUDE.md` permits — everything else here stays
concrete.

`calls_to(owner, attribute)` is the one general hook constructor, since most
counters worth having are "calls to this callable". It requires the attribute to
be defined on the owner itself, and **refuses** a `classmethod`, `staticmethod`
or `property`: wrapping one with a plain function changes how the attribute
binds, which would give a correct count of a program that is no longer the
program under test. That is the ADR 008 failure again, and refusing is the only
honest option available at this layer.

**Removal is unconditional and in a `finally`.** Instrumentation that outlives
its block raises nothing and taxes everything measured afterwards.

**Captured stacks are innermost-first, carry no source text, and omit frames
belonging to this package.** An observer frame exists only because the
observation is happening; leaving it in would put a coldfix function at the top
of every event's stack, and S-3.9 localizes by walking stacks to the deepest
frame they share — a frame common to all of them by construction is precisely
the wrong thing to leave in.

## Consequences

**Makes easy.** A typo fails loudly at the point of use. Adapters add counters
without touching this module. Guard counters work by construction: two hooks
count concurrently across one run, which is what "queries down while rows
returned explodes" requires in order to be visible at all.

**Makes hard.** Every counter now needs a registration site, so nothing can be
counted ad hoc — deliberate, but it means S-3.6 cannot be skipped past. Hooks
are process-global while installed, so two concurrent workloads counting the
same hook cannot be told apart; nothing does that yet, and the load primitive
will have to.

**Rules out.** Counting an inherited attribute by naming a subclass, and
counting anything reached through a descriptor. Both are refused rather than
approximated.

## Provenance

The five-percent overhead criterion in S-1.3 was met with room to spare — 0.26µs
per event against a 366µs operation, 0.07% — but only after fixing a defect in
the first implementation of this module: `Path.resolve()`, a filesystem call,
ran once per counted event on the stack-capture path. It cost 590µs per event,
made the test file take 132 seconds, and is exactly the class of defect this
tool exists to find in other people's code. Recorded here because the measured
five percent means nothing without the note that the first version failed it by
two orders of magnitude and looked fine.

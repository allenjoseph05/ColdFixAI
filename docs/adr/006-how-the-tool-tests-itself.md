# 006 — How the tool tests itself

**Status:** accepted
**Date:** 2026-08-02

Written *from* S-0.7's outcome rather than before it. S-0.2 lists this ADR and
S-0.7 depends on S-0.2, which is close to circular: deciding how the tool tests
itself is what S-0.7 does. The fixture repository was built first and this record
follows it.

## Context

A system whose job is verification has to be verifiable, and it has an unusual
failure mode: **a detector that always answers "yes" passes every test built
only from defects.** Correctness here is as much about the findings the system
declines to make as the ones it makes.

## Decision

Four layers, each testing something the others cannot.

### 1. Instruments against ground truth — `tests/fixtures/planted/`

Pure-Python fixtures with a query-counting store, complexity functions whose
operation counts are known by construction, and a slow/fast import pair. Not a
miniature Django app.

**Why pure Python:** a query counter is correct when it reports 21 and exactly 21
queries were issued. Establishing that needs a subject whose true count is known
by construction rather than measured. Realism has a different owner (layer 3).
The fixtures run in milliseconds, need no service, and produce only exact
integers — S-0.4 measured wall-clock timings drifting 12 % between runs while
guard counters reproduced to the byte, and these fixtures have only the second
kind of number.

**Every defect carries a control**, and this is the load-bearing rule. The
fixture also contains a **decoy**: 37 queries, constant with dataset size,
modelled on the ~35-query floor S-0.3 measured on a real mature system. At small
sizes it costs *more* than the real N+1 beside it, so absolute query count ranks
the two backwards. Only growth rate distinguishes them. A detector that flags
"many queries" fails here, which is the point.

### 2. Safety properties by adversarial test

For each structural guarantee, a test that **attempts the violation and asserts
it fails** — not one that confirms the happy path. `tests/test_holdout_discipline.py`
is the pattern: it scans the tree for the holdout, and it also plants a violation
to prove the scan would catch one. It caught two real leaks on its first runs,
one of them written by its own author.

The same shape is owed to: `apply_patch` refusing to touch tests or harness
(S-2.4), the production URL check refusing to start (S-2.5), an agent being
unable to advance past a false stage predicate (S-7.11), and a stale evaluated
`QuerySet` not being trusted after a reset (S-0.5).

### 3. Realism against the pinned target

Real SQL, a real planner, real connection behaviour. ADR 011 pins a real
application with a real unplanted defect and a measured signature. Integration
tests here are not optional — layer 1 explicitly cannot reach any of it.

### 4. Agent logic against a mock LLM client

Recorded responses, replayed. **No test hits a real API.** Deferred until
ADR-002's SDK choice is real — writing a mock against a guessed interface is the
speculative abstraction the project forbids.

## Consequences

**Makes easy.** The fast subset stays fast and cannot fail for environmental
reasons. Every planted defect has an asserted signature, so the measuring
standard is itself calibrated — an uncalibrated standard is worse than none,
because every downstream test inherits its error silently.

**Makes hard.** Four layers is four sets of fixtures to keep honest, and the
split has to be respected: adding "just one" Django-shaped fixture to layer 1
would reintroduce the service dependency it exists to avoid.

The fixtures must also grow. `CLAUDE.md` requires it — *grow it whenever a real
repo surprises you* — and three surprises from E0 are recorded as not yet
represented, because all three need a real framework: a hardcoded `DATABASES`
with no override, a setting that warns at startup and fails at use, and a
workload writing state outside the database that no reset strategy could undo.

**Rules out.** Testing detectors only against defects. Any new fixture ships with
a control, or it teaches the detector to say yes.

## Provenance

`CLAUDE.md` testing section; `docs/10-BACKLOG.md` S-0.2 AC (ADR-006), S-0.7;
`tests/fixtures/README.md` for the catalogue and the reasoning behind the split.

# 007 — The refusal list and its rationale

**Status:** accepted
**Date:** 2026-08-02

## Context

`00-BRIEF.md` §3 lists four categories the system declines. They are recorded
here so the reasoning survives contact with a future contributor who reads them
as unimplemented features.

**These are not gaps.** Each one is a category where no verifier we can build
makes the change safe. A refusal is a design output, not a missing capability.

## Decision

The system declines all four, permanently, and says so in its output rather than
attempting the work and hedging.

### Concurrency and locking fixes — diagnose and report only

Output equivalence is the verification mechanism: run the tests, compare the
results, ship if they match. **It cannot detect an introduced race.** A patch
that moves a lock can pass every test on every run and still be wrong under a
scheduling order the test suite never produced.

Diagnosis is still permitted and still useful — reporting "this endpoint
serializes on a lock" is a finding. Producing a patch for it is not.

### Hard real-time systems — detect and decline before grounding

Two independent reasons, and the second is worse than the first.

Measurement-based analysis is insufficient for worst-case execution time: the
distribution's tail is the requirement, and sampling does not bound it.

Worse, **a caching optimization improves every metric this system measures while
degrading worst-case timing.** The system would report a confident, verified,
correct-looking improvement that makes the system less safe. That is not a
limitation to work around; it is a reason to refuse before grounding, which is
where S-2.8 puts the check.

### Third-party dependency code — report the cause, never patch it

The system may correctly identify that the cost lives in an installed package.
Patching it means editing code the user does not own, that their package manager
will overwrite, and whose test suite is not in scope. The finding is the
deliverable.

### Production environments — enforced, not requested

Test environments only, enforced by a database-URL pattern check that refuses to
start (S-2.5). Every safety property in this system assumes state can be reset
ten times a run; against production that assumption is a data-loss incident.

Per `CLAUDE.md`: *"If you find yourself relying on this file to prevent
something dangerous, that rule needs code instead."* This one has code.

## Consequences

**Makes easy.** The evaluation story is honest by construction. A refused
category cannot produce a false positive, and S-15.4's failure catalogue can
report refusals as correct behaviour rather than as misses.

**Makes hard.** Two of the four need *detection* built before they can be
refused: S-2.8 must recognize a real-time system from its indicators, and the
dependency boundary must be computed rather than assumed. A refusal that fails
to trigger is worse than no refusal, because it is silently absent.

**Rules out.** Any future story proposing to "handle concurrency carefully" or
"support real-time with a warning". Reopening one of these requires a new ADR
superseding this one, and a verifier that did not exist when it was written.

## Provenance

`00-BRIEF.md` §3 and §4; `CLAUDE.md` refusals and hard-enforcement table;
`docs/10-BACKLOG.md` S-0.2 AC (ADR-007), S-2.5, S-2.8.

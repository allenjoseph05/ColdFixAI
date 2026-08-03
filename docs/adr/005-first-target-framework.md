# 005 — First target framework: Django + Postgres

**Status:** accepted
**Date:** 2026-08-02

## Context

The system needs one framework to work against before it can claim to work
against any. The choice determines the first adapter, the shape of every early
test, and which defects the system can find at all.

## Decision

**Django + Postgres**, with `django-helpdesk` as the pinned development target
and a designated holdout (ADR 011).

## Why this one

**It is measurable from the inside.** Django's ORM exposes a per-connection
query log reachable without patching the framework — the mechanism ADR 008
settles. The N+1, the single most common defect in this class of application, is
detectable by counting rather than by reading code, which is the project's whole
method.

**It is drivable.** S-0.3 took three unfamiliar Django repositories from `git
clone` to an authenticated endpoint returning real rows, in 8, 5, and 19
minutes. All three had a discoverable REST list endpoint; none needed a code
change; every migration ran clean on an empty database. That is the assumption
E7 rests on, tested before E7 was built.

**Its ecosystem is uniform enough that a playbook generalizes.** The sixteen
obstacles S-0.3 found were all distinct in their specifics and all landed in one
of nine stages (ADR 009). Categories converged even though details never
repeated — the property that makes a framework-scoped playbook worth building.

**Postgres specifically**, not "a database": S-0.5's reset strategy depends on
sequences being non-transactional and on `SET` being reverted by rollback, both
of which are Postgres behaviours. MySQL's `AUTO_INCREMENT` and implicit-commit
rules differ enough that the reset primitive would need rewriting, not
configuring.

## Consequences

**Makes easy.** Every early story has a real subject with a real, unplanted
defect and measured baselines: an ablation delta, a reset cost, and a noise
floor, all recorded before E1 exists.

**Makes hard.** Everything built before E14 will be Django-shaped, and some of
that shape will be invisible until a second adapter exists. The mitigation is
the holdout in ADR 011 and the honest limitation in S-17.2, not a premature
abstraction layer — the project's rule is that the primitive registry is the one
designed extension point and everything else stays concrete until a second case
appears.

**Rules out, for now.** GraphQL-only APIs — S-0.3 rejected `saleor` on exactly
this ground, since "find a candidate endpoint" is not representative there. Also
non-Python frameworks, until MCP in E14, which itself waits for a second adapter.

## Provenance

`docs/10-BACKLOG.md` S-0.2 AC (ADR-005), S-0.1 notes; `spikes/S-0.3-grounding/FINDINGS.md`
for groundability, `spikes/S-0.5-reset/FINDINGS.md` for the Postgres-specific reset semantics.

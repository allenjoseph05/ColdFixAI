# 004 — Sandboxing: Docker containers, with worktree separation

**Status:** accepted
**Date:** 2026-08-02

## Context

The system runs code it did not write, deliberately breaks that code to measure
it, and then writes patches to it. Three properties are needed, and only the
first is about security:

1. The subject cannot damage the host.
2. **An ablation run cannot produce a shippable patch.** `CLAUDE.md` requires
   this be structural: *"separate container, separate worktree, destroyed on
   exit. Enforced by the harness, never by prompt."*
3. Runs are reproducible enough that two measurements are comparable.

## Decision

**Docker.** One container per subject environment, plus a separate container and
separate git worktree for any diagnostic (ablation) run, destroyed on exit.

The E0 spikes used exactly this and it held: three isolated Postgres instances
on distinct ports, a deliberately minimal `python:3.12-slim` workbench recreated
between subjects, and — in S-0.5 — a maintenance connection outside the database
being dropped and recreated.

## Consequences

**Makes easy.** Property 2 becomes a fact about the filesystem rather than a
rule an agent might not follow: the ablation worktree is a different directory
in a different container, and `apply_patch` (S-2.4) simply has no path to it.

Reproducibility is cheap. S-0.4's guard counters reproduced byte-identically
across runs partly because the environment was pinned. ADR 010's date anchoring
extends the same idea to the dependency set.

**Makes hard.** Containers are not a security boundary against hostile code —
they are an isolation boundary against *accidents*. The subject repository is
assumed to be the user's own code, not an adversary's. That assumption belongs
in S-17.2, and anything stronger needs a VM, which costs startup time this
system spends on every experiment.

Three practical costs the spikes measured: a bare `-slim` image must install its
own build dependencies (S-0.3 obstacle A-1 and C-1); the Postgres client version
inside the workbench must match the server or `pg_restore` reports errors it
then ignores (S-0.5); and container recreation loses `apt` state, which is
deliberate for spikes and would be wasteful in production without a built image.

**Rules out.** Running the subject on the host, and reusing one container across
subjects — S-0.3 recreated the workbench between repositories precisely because
a container carrying the previous subject's packages grounds the next one for
free and hides a real obstacle.

## Provenance

`CLAUDE.md` non-negotiables and hard-enforcement table; `docs/10-BACKLOG.md`
S-0.2 AC (ADR-004), S-2.3, S-2.4. Practice validated across all three E0 spikes.

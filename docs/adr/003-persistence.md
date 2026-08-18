# 003 — Persistence: two stores, chosen separately

**Status:** accepted
**Date:** 2026-08-02

## Context

Two kinds of state, with different failure modes, get confused if they share a
store.

**Checkpoint state** is the orchestrator's resumable position — what E12 needs
to rewind a graph and re-run from a node. It is write-heavy, short-lived, and
worthless once the run ends.

**Persistent state** outlives runs: the playbook (S-13.1), the trust ledger
(S-13.4), failure memory (S-13.3), the replay cache (E5). A corrupted entry here
propagates silently into every future run — which is exactly why S-13.2 is
marked SAFETY.

## Decision

**Checkpoints: SQLite in development, Postgres for concurrent campaigns.**
SQLite is a file with no service to run, which suits a single investigation on
one machine. It is a poor fit for concurrent writers, so the moment E15 runs
campaigns in parallel the same interface points at Postgres.

**Persistent data: a separate store, always Postgres.** Not a second schema in
the checkpoint database — a separate store, so that dropping checkpoints (a
routine operation) cannot touch the playbook (a destructive one).

The separation is the decision. The engine choice for checkpoints is an
implementation detail behind one interface; the store separation is not.

## Consequences

**Makes easy.** Wiping a run's checkpoints is safe by construction. A developer
runs against a file and never provisions a database until concurrency demands it.

**Makes hard.** Two stores means two migration paths and two backup stories, and
the SQLite/Postgres interface has to stay honest — S-0.5 is the warning here:
Postgres sequences are non-transactional and MySQL's `AUTO_INCREMENT` differs
again, so "works on SQLite" is not evidence that concurrent Postgres behaves.
Any checkpoint test that matters must run against both.

**Rules out.** One database for everything, and using the subject's database for
our own state — that would put tool state inside the thing being reset ten times
a run.

## Provenance

`docs/10-BACKLOG.md` S-0.2 AC (ADR-003), E6, E13. Reset semantics from
`spikes/S-0.5-reset/FINDINGS.md`.

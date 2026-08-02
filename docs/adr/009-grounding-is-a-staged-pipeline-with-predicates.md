# 009 — Grounding is a staged pipeline, and every stage has a machine-checkable predicate

**Status:** accepted
**Date:** 2026-08-02

Numbered 009 because 001–007 are reserved for S-0.2 and 008 is taken.

## Context

E7 is named the riskiest component in the backlog, and the reason usually given
is that repositories are unboundedly varied. S-0.3 grounded three of them by
hand and produced sixteen distinct obstacles, no two identical.

But the obstacles were not distributed arbitrarily. **Every one of the sixteen
fell into one of nine stages**, and no obstacle required a tenth:

```
clone → resolve dependencies → configure → connect to database →
migrate → resolve auth → seed data → find endpoint → get real data
```

That asymmetry is the useful finding. The *specifics* did not repeat even once —
the Postgres driver failed three different ways across three repositories — while
the *stages* repeated perfectly. A taxonomy that stayed closed across three
deliberately dissimilar repositories is worth building on.

The current design does not exploit this. `S-7.8` computes `work_verified` for
the final stage and computes it in the harness precisely because the agent is
incentivized to claim success. `S-7.10` caps the whole run at 60 steps and
escalates after 15 steps without new information. Between those two there is
nothing: the eight earlier stages have no definition of done, so an agent stuck
at stage four and an agent progressing normally are indistinguishable until the
global cap fires.

## Decision

Grounding is modelled as a fixed nine-stage pipeline. **Each stage carries a
predicate that the harness evaluates, not the agent** — the same separation
`S-7.8` already applies to the last stage, extended to all of them.

| Stage | Predicate |
|---|---|
| clone | checkout exists and an entry point was located |
| dependencies | the framework imports in the target interpreter |
| configure | the framework's own check command exits 0 |
| connect | a trivial query succeeds against the target database |
| migrate | the migration tool reports zero unapplied migrations |
| auth | a credential authenticates against a protected route |
| seed | row counts exceed a stated threshold in at least two tables |
| endpoint | at least one candidate route was enumerated |
| work | HTTP success **and** query count, response bytes and wall time all rise between N=10 and N=100 |

The last row is `S-7.8` unchanged. The other eight are new and follow its rule:
the predicate is computed by the harness, the agent cannot supply or override it,
and a stage is not complete because the agent says so.

Predicates are framework-scoped, resolved through the `S-7.1` fingerprint, and
live beside the adapter rather than in agent prompts.

## Consequences

**Makes easy.** An unfamiliar obstacle stops being open-ended. "Something went
wrong" is unbounded; "stage 4's predicate is false, here is the error" is a small
problem with a stated success condition, which is the shape an agent handles
well. The unknown-unknowns problem becomes a bounded search inside a known stage.

Honest failure becomes specific. `S-7.10` currently reports "what was attempted
and why it stopped"; with stage predicates it can report *which stage never
completed*, which is the difference between a limitation someone can act on and
a transcript someone has to read.

Cost control improves for the same reason. S-0.3's runs took 5 to 19 minutes
each. Detecting at stage two that a repository will not ground saves the
remaining seven stages, and a per-stage attempt budget is a far tighter
instrument than one 60-step global cap.

It also gives `S-13.1` a natural key. A playbook entry that fires at any point in
a run is hard to evaluate; one scoped to "stage 4, this fingerprint" can be
measured against that stage's predicate directly, which is what `S-13.2`'s
promotion counters need to be trustworthy.

**Makes hard.** Nine predicates per framework is real adapter surface, and it has
to be written again for every adapter added in E14. That cost is accepted: the
predicates are small, deterministic, and testable, and the alternative is
agent-reported progress, which `S-7.8` already establishes we do not trust.

Stages are not strictly sequential in every repository — auth sometimes resolves
before seeding, and a repository shipping a seeded database skips a stage
entirely. The pipeline is therefore an ordering of *predicates to satisfy*, not a
script of steps to execute. A stage whose predicate is already true is complete
without action.

**Rules out.** Treating grounding as one opaque agent task with a single
success check at the end. That is the design this supersedes, and S-0.3's stage
log is the evidence that the finer decomposition matches how the work actually
divides.

## Provenance

`spikes/S-0.3-grounding/FINDINGS.md` — the per-repository stage logs, and the
recurrence matrix showing categories converging while specifics did not.

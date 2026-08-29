# 167 — N=0 is an empty subject, not a seeding plan for nothing

**Status:** accepted
**Date:** 2026-08-29

## Context

Epic 17 finished with fourteen stories: a spike, a measurement vantage, an
execution surface, six subject-facing producers and an assembly. Its own sentence
is **the pipeline can reach a live subject**.

Every story proved its piece, and most of those proofs replaced the two calls that
actually touch a subject. S-17.10 monkeypatched `drive` and `synthesize`. S-17.14
monkeypatched both again. S-17.15 replaced `choose_reset`. Each was the right call
for the story — what a container and a Postgres server prove is S-2.1's and
S-2.7's — but the consequence is that **nothing had ever taken an assembled
`Resources` and measured something real with it.**

The composition check does that: a Django project with a planted N+1, bound
through `Resources.bind`, screened, concluded.

## The defect

**The first screen of the first workload raised, and no screen was possible at
all.**

`scale_volume` measures a baseline at `BASELINE_SCALE = 0` before its scale
points, and subtracts it from every one of them — *"`adjusted` is `raw` with the
N=0 baseline removed"*. Without it, the framework's own fixed cost is folded into
every exponent.

`synthesize` refuses a zero-row plan: *"a plan for 0 row(s) seeds nothing and
would report success for it."*

Both are right. Between them, the binder's `scale(0)` asked synthesis for zero
rows and got a refusal, which `scale_volume` re-raised as `BaselineError` — *the
workload could not be measured at N=0*. Two modules each correct about their own
subject, producing a wrong answer between them: the shape every composition check
in this project has found, now thirteen for thirteen.

## Decision

**In a binding, N=0 means an empty subject.**

`scale_volume` calls `seed(scale)` *inside* `reset.mechanism.cycle()`, so by the
time the binder's `scale` runs, the subject has already been emptied. Seeding zero
rows into an empty subject is doing nothing — so the binder returns early at
`BASELINE_SCALE`, clearing `created` because nothing was created.

This is not a workaround for `synthesize`'s refusal. It is the correct reading of
what the baseline point means, and `synthesize` keeps its refusal intact for
callers actually asking it to seed. The alternative — relaxing `synthesize` to
accept zero — would give every caller a plan that reports success for having done
nothing, which is what that refusal exists to prevent.

## What the check establishes, and what it does not

**Establishes**, against a real migrated Django subject: an assembled `Resources`
binds a workload artifact; the binding drives the subject and reports its own
numbers; `db.query` fits `LINEAR` where a round-trip count is expected constant;
`conclude` returns a plan rather than a null result; the screen's vantage is
`SUBJECT`; and the seven nodes bind to the resources and compile into a graph.

**Does not cover**, and says so in the file: the ledger read inside `gated_graph`,
which opens the trust store and needs Postgres — that half is S-13.4's. The
session is a real path with a real executor rather than a container, because
standing one up would make this check about S-2.1. And the reset is the smallest
thing that genuinely empties the subject rather than S-2.6's mechanism, for the
same reason.

## A sabotage survived, and it was a missing test

Making the binding report a **constant** `seconds` instead of the subject's changed
no outcome. The test asserting the numbers come from the subject asserts the
*vantage* — and a hardcoded duration carries the right label on an invented
number.

The added test asserts three scale points produce three **distinct** durations,
because a constant is exactly equal and real timings never are. Distinct rather
than increasing: the comparison that matters is against a fabricated number, while
asserting a strict ordering on three sub-second measurements would be a flaky test
dressed as a strict one.

## Consequences

**Epic 17's sentence is performed.** The pipeline measures a real subject through
the assembled campaign, and the defect it exists to find is the one it found.

**S-17.1 is closer than the backlog says, but not unblocked.** It still needs a
live subject of its own, a database, an API key and a decision — and the
grounding half of a run makes model calls this check does not.

**Epics 0, 1, 13, 14 and 15 still have no composition check.** Twelve of the
thirteen that exist found a defect each; this one makes thirteen.

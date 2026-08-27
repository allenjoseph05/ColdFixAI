# 157 — The subject measures itself and the harness receives the numbers

**Status:** accepted
**Date:** 2026-08-27

## Context

S-17.1 cannot run: nothing in `src/` constructs a `Resources`, and six of its
twenty-three fields are the layer that reaches a live subject — `bind`, `ground`,
`measure`, `executor`, `hands`, `probe` — every one a protocol with no
implementation outside a test fake.

`Binder` decides the shape of the others, because it produces the
`BoundWorkload` whose `invoke` every screening measurement is taken around. This
codebase has two measurement worlds and S-14.2 recorded them as *not
competitors*: `measure_once` counts and times **in this process** around a
callable, and `explorer.work.drive` counts and times **inside the subject's own
interpreter**. That is true for counting and it does not tell `Binder` which one
to be.

S-17.5 measured what happens if it picks the obvious one.

## The measurement

A Flask + SQLAlchemy subject with an N+1, SQLite, no container — so the numbers
are a floor, since a Docker exec and a Postgres connection would both widen the
gap. Full detail in `spikes/S-17.5-measurement-boundary/FINDINGS.md`.

The endpoint costs **9.6 ms** as the subject measures it. The same call measured
from outside costs **1266 ms** — a ratio of **132x**, of which the endpoint is
**0.75%**. `measure_once` times the callable it is given, so an `invoke` that
launched the subject would record the second number as `seconds`.

The size of the overhead is not the finding. Screening does not read a duration,
it **fits growth** on one:

| series | n=10 | n=40 | n=160 | fitted |
|---|---|---|---|---|
| queries | 11 | 41 | 161 | **LINEAR** |
| endpoint ms, inside | 5.77 | 21.64 | 73.31 | **LINEAR** |
| subprocess ms, outside | 1282.9 | 1360.4 | 1447.1 | **CONSTANT** |

**A workload that is linear in time fits as constant**, because 67 ms of real
growth sits under 1257 ms of fixed startup. Reproduced on a second run: different
absolute timings, identical classifications.

## Decisions

### 1. `Binder` must not put an out-of-process launch behind `invoke`

The measured consequence is a wrong growth class on a metric this system
publishes *exclusions* about.

**That is worse than a missed finding.** The query count survives — it is
measured inside the subject and travels through `extra_counters` — so this
particular N+1 would still be flagged on `db.query`. What breaks is everything
timing-only, and the planted fixtures contain two such defects: a quadratic loop
and a slow import, neither of which moves a query count. `CLAUDE.md` ships
*"queries flat across 100x scale"* as a publishable result, so a screen taken
this way would publish **"seconds: constant across a sixteenfold increase"** when
what it measured was the cost of starting an interpreter. A false exclusion is an
answer rather than a silence.

### 2. The subject measures itself; the harness receives the numbers

Of the two worlds, this is the one that already works: `drive` times the request
inside the process that serves it, takes a median of samples, and discards a
warm-up.

The alternative — running coldfix inside the subject's container so `invoke` is
genuinely in-process — was **not measured and is not recommended**. It puts the
harness in the same process as deliberately broken ablated code, and S-2.1 mounts
exactly one directory into the sandbox, which is what makes ADR 004's *an
ablation run cannot produce a patch* structural rather than conventional.

### 3. The `seconds` reservation stays

The obvious repair is to hand the subject's own timing back through
`extra_counters`, which is how the query count already travels. Measured: `seconds`
raises `MetricSetError` while `db.query` and `response_bytes` are accepted. **The
one metric that is wrong is the one that cannot be corrected through the existing
seam.**

That reservation is right and should not be relaxed. It exists because two
records of one number are two things that can disagree. What the follow-on work
needs is a way to say *this measurement came from the subject* — not permission
to overwrite. S-8.12's `Measured` at the `Executor` boundary is the precedent:
widen the boundary, rather than smuggle a value through a seam meant for
something else.

## Consequences

**The six implementations split into two groups, and the order is now decided.**
`Hands` and `Grounder` measure nothing and can be written immediately. `Binder`,
`Executor` and `Measurer` all hand back numbers about a running subject and sit
on the far side of this boundary; none should be written until the answer above
is a type.

**This is the third E0-shaped spike to change a design by measuring the naive
implementation.** S-0.5's rollback kept row counts identical across ten cycles
and left the sequences permanently advanced; S-0.4's two stub strategies were
indistinguishable on timing and differed sixfold in payload. Each time the
obvious implementation passes the obvious check and is systematically wrong in a
direction nobody looks — which is the argument for spending a day on a spike
before spending a week on a layer.

**No model calls.** Arithmetic and a stopwatch, which is what the question
turned out to need.

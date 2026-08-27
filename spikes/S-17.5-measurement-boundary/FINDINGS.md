# S-17.5 — Where does a screening measurement happen?

**Run 2026-08-27.** `uv run python spikes/S-17.5-measurement-boundary/run.py`.
Raw numbers in `measurements.json`. No model calls: arithmetic and a stopwatch.

## The question

S-17.1 cannot run because nothing in `src/` constructs a `Resources`, and six of
its fields are the layer that reaches a live subject. `Binder` is the one that
decides the shape of the other five, because it produces the `BoundWorkload`
whose `invoke` every screening measurement is taken around.

There are two measurement worlds in this codebase and S-14.2 recorded them as
"not competitors":

- `bench.counting.count` and `primitives.measurement.measure_once` count and time
  **in this process**, around a callable;
- `explorer.work.drive` counts and times **inside the subject's own
  interpreter**, with an injected program, and returns the numbers.

That is true for *counting* and it does not tell `Binder` which one to be. This
spike asks what happens if it picks the obvious one: a `BoundWorkload.invoke`
that launches the subject.

## The subject

A Flask + SQLAlchemy application with a deliberate N+1 on `/tickets` — one query
for the list and one per row for the follow-ups. Chosen small on purpose: the
effect being measured is the ratio between what an endpoint costs and what
*reaching* it costs, and a subject that took a second to answer would hide it.
SQLite, no container, no network, so the numbers are a floor rather than a
worst case — a Docker exec and a Postgres connection would both make the gap
wider.

## Measurement 1 — the gap

Fifteen rounds, one request each, at 20 tickets.

| | |
|---|---|
| endpoint, as the subject measured it | **9.556 ms** |
| the same call, measured from outside | **1266.619 ms** |
| overhead — interpreter, imports, `create_app` | **1257.063 ms** |
| ratio | **132.5x** |
| share of the outside number that is the endpoint | **0.75%** |

`measure_once` times the callable it is given. So a `BoundWorkload.invoke` that
launched the subject would record the second number as `seconds`, of which
**more than 99% is the cost of starting Python**.

## Measurement 2 — the finding

The size of the overhead is not the finding. Screening does not read a duration,
it **fits growth** on one, so what matters is whether the outside number still
has the shape the workload has. The same workload, at three scales, timed both
ways and fitted with the project's own `fit_growth`:

| series | n=10 | n=40 | n=160 | fitted |
|---|---|---|---|---|
| queries | 11 | 41 | 161 | **LINEAR** |
| endpoint ms, measured inside | 5.77 | 21.64 | 73.31 | **LINEAR** |
| subprocess ms, measured outside | 1282.9 | 1360.4 | 1447.1 | **CONSTANT** |

**A workload that is linear in time is fitted as constant.** The 67 ms of real
growth is buried under 1257 ms of fixed startup, and the exponent goes to
approximately zero.

**Reproduced.** A second run gave 4.89 / 16.09 / 64.98 ms inside and
1154.9 / 1157.7 / 1289.8 ms outside — different absolute numbers, the same three
classifications. The absolute timings move with machine load; the fitted classes
do not, which is what makes this a property rather than an observation.

This is the same shape as the two E0 spikes that changed the design. S-0.5's
rollback kept row counts identical across ten cycles and left the sequences
permanently advanced; S-0.4's two stub strategies were indistinguishable on
timing and differed sixfold in payload. In each case the naive implementation
passes the obvious check and is systematically wrong in a direction nobody looks.

### Why it is worse than a missed finding

The query count survives, because it is measured inside the subject and comes
back through `extra_counters` — so *this* N+1 would still be flagged on
`db.query`, where the expectation is `CONSTANT` and the fit is `LINEAR`.

The damage is to everything timing-only. The planted fixtures include a
quadratic loop and a slow import, neither of which moves a query count. And
`CLAUDE.md` ships exclusions as findings: *"not the database, queries flat across
100x scale"* is a publishable result. So a screen taken this way would publish

> seconds: constant across a sixteenfold increase

as a measured exclusion, when what was measured was the cost of starting an
interpreter. **A false exclusion is worse than a missed finding**, because it is
an answer rather than a silence.

## Measurement 3 — the repair that is not available

If the outside timing is wrong, the obvious fix is to let the binding hand back
the subject's own number through `extra_counters`, which is already how the query
count travels. Attempted, per metric:

| metric | result |
|---|---|
| `seconds` | **`MetricSetError`: would overwrite metrics this run already measured** |
| `db.query` | accepted |
| `response_bytes` | accepted |

`measure_once` reserves the metrics it measures itself, deliberately — two
records of one number are two things that can disagree. **So the one metric that
is wrong is the one metric that cannot be corrected through the existing seam.**

## Verdict

**`Binder` must not put an out-of-process launch behind `invoke`.** The measured
consequence is a fitted growth class that is wrong, on a metric the system
publishes exclusions about, with no route to repair it through `extra_counters`.

Of the two worlds, **the subject measures itself and the harness receives the
numbers** — which is what `drive` already does, correctly, today: median of
samples with a warm-up discarded, timed inside the process that serves the
request.

The alternative — running coldfix inside the subject's container so that
`invoke` is genuinely in-process — was not measured and is not recommended. It
puts the harness in the same process as deliberately broken ablated code, and
S-2.1 mounts exactly one directory into the sandbox, which is what makes ADR
004's *an ablation run cannot produce a patch* structural rather than
conventional.

## What this costs the follow-on work

1. **Screening cannot reach a containerised subject through `measure_once` as it
   stands.** The entry point takes a callable and times it; the subject-facing
   path has already done the timing. Something has to name that case — S-8.12's
   `Measured` at the `Executor` boundary is the precedent for widening a
   boundary rather than smuggling a value through a seam meant for something
   else.
2. **The `seconds` reservation is right and should not be relaxed.** It exists
   because two records of one number disagree. What is needed is a way to say
   *this measurement came from the subject*, not permission to overwrite.
3. **`Executor` and `Measurer` inherit this.** Both hand back numbers about a
   running subject, and both are on the far side of the same boundary. Neither
   should be written before the answer above is a type.
4. `Hands` and `Grounder` are unaffected — neither measures anything. They are
   the two that can be written immediately.

# 152 — A project's cost per finding is not the mean of its runs'

**Status:** accepted
**Date:** 2026-08-27

## Context

S-15.3 asks for euros per confirmed finding **per run and per project**, broken
down by phase **and model tier**, with cache hit rate and escalation rate
included.

Most of it was already there. S-5.3 built `RunReport` with
`eur_per_confirmed_finding` and `Ledger.by_phase`, and Epic 5's composition check
put the cache and escalation sections into `Session.report`. Checking first —
tenth story running — left two gaps, one per criterion:

- **per project**: a `Ledger` is one run, and nothing aggregated runs;
- **by model tier**: `by_phase` existed and there was no tier cut.

## Decisions

### 1. The project ratio is total over total, not the mean of the runs'

`ProjectReport.eur_per_confirmed_finding` is `total_eur / confirmed_findings`.

The alternative is the one somebody reaches for, and it is wrong twice.
Averaging ratios weights a cheap run that found three things equally with an
expensive one that found one, which is neither the cost of a finding nor
anything else. Worse, **a run that confirmed nothing has no ratio to average
in** — so the arithmetic that looks like a summary is also the arithmetic that
deletes the null runs from the answer.

`04-cost.md` §11 is why the project is the right unit at all: grounding happens
once per repository rather than once per finding, so *what does a finding cost
here* is only answerable over a project's whole history. A single run that found
two things looks cheap because an earlier run paid to stand the repository up,
and the earlier run may have found nothing.

`runs_confirming_nothing` is reported for the same reason. A null result is an
answer and it is not a free one, and dropping those runs is the obvious way to
make the number look better.

### 2. Euros are converted once, from dollars

The vendor bills dollars; euros are a presentation. Summing each run's euro
figure adds numbers taken at several rates into a total nobody can reproduce, so
`ProjectReport` sums dollars and converts once at its own rate — and `render`
says so when a run was reported at a different one, rather than quietly
restating it. A project measured over months legitimately spans several rates,
so this is stated rather than refused.

### 3. The tier cut lives on `Session`, and the reason is the import graph

A tier is `cost.routing`'s idea and `routing` imports `cost.accounting`, so a
`by_tier` on the `Ledger` would be a cycle. The ledger gained `by_model` — what
it actually knows — and `Session.by_tier` maps those through the router that
chose them.

**Spend on a model no tier names is reported, not absorbed.** Folding it into the
cheapest band makes the table sum to the run while describing a routing that did
not happen; dropping it makes the table sum to less than the run, which is
`Ledger.reconciles`' defect one level up. So `unrouted_usd` is its own figure,
and a test asserts the two sum to the run. A call billed against a model the
router never chose is either an escalation target that has since been
reconfigured or a caller going around the router, and both are worth seeing.

### 4. AC 3 was already met

Cache hit rate is in `RunReport.render` and in `Session.cache_report`, per model
because caches are model-scoped. Escalation rate is `StepStatistics.escalation_rate`,
which returns `None` below a minimum sample count rather than calling one
escalation out of one attempt a 100% rate. Nothing was added.

## Consequences

**S-5.9's AC 6 is now unblocked in the mechanical sense and still blocked in the
real one.** *Result is published in the cost report* has a cost report to be
published in — but AC 1 needs a second vendor's account, which Allen confirmed
does not exist, and AC 2 needs findings that S-0.8's scenarios do not produce.
S-5.9 stays PARTIAL for the reasons already recorded, not for want of a report.

**Nothing in `src/` calls `Session.report` or `ProjectReport`.** That is the same
category as `eval/ablation.py` and the adapter conformance suite: a report is
driven by a person. Worth stating because this project treats an unreachable
module as a defect by default, and reports are the documented exception.

**Sabotage: 5 properties, 5 caught** — the mean-of-ratios substitution, dropping
null runs from the total, folding unrouted spend into the cheapest tier,
computing the tier cut without rendering it, and grouping `by_model` by phase.

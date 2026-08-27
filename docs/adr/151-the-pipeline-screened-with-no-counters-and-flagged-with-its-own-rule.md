# 151 — The pipeline screened with no counters and flagged with its own rule

**Status:** accepted
**Date:** 2026-08-27

## Context

Epic 16's composition check. The epic renders three things — the evidence chain,
the pull request, the null result — and each was tested against artifacts built
by hand. **Nothing had ever walked from a screen to a document**, which is where
the epic's sentence actually lives.

The walk needed a screen, so it drove `orchestrator.adapters.screen` for the
first time. `test_graph.py` tests the *router* that reads the screening channel
against synthetic state; `test_adapters.py` tested the *helper* that wrote it
against a stand-in metric object. Between the two there was no test that ran the
node and read what it produced — the shape every composition check in this
project has found.

Three defects, in ascending order of how badly they break the system.

## Decisions

### 1. The node held its own opinion about what is worth investigating

`screen` called a local helper backed by `_SUSPICIOUS = frozenset({Growth.SUPERLINEAR})`.

`tests/screening/test_flagging.py` opens with the reason that is wrong: *the N+1
is linear, so "flag superlinear growth" would walk past it*. A textbook N+1 grows
**linearly** in query count — one query per row — and the verdict `flag` makes is
the fit against each metric's own **expectation**, where a round-trip count is
expected to be *constant*.

Measured on the project's own planted fixture, before any change:

| | verdict |
|---|---|
| `flagging.flag()` | `db.query = GROWTH` |
| the node's helper | not flagged |

The whole `FLAT_COST` class was unreachable from a run as well, since a
flat-but-expensive workload fits `CONSTANT`.

The judgement now comes from `screening.assess.conclude` — the module Epic 4's
own composition check built for exactly this, returning a `Plan` or a
`NullResult` that are exclusive by construction. There is no threshold left in
the orchestrator to drift from it.

**A test had asserted the defect**, with reasoning that sounded right:
*"S-1.5's vocabulary decides, not a threshold invented at this boundary."* The
vocabulary does decide — against an expectation, which the helper never
consulted. That test now asserts the absence of any such rule.

### 2. The node attached no counters at all

`screen(bindings)` takes `counters: Sequence[str] = ()`, and the node passed
none. So `db.query` was never measured in a production screen, and the
consequences compound:

- no query growth could be fitted, so no N+1 could be flagged even after §1;
- `work_verified` was **False for every workload**, because F6's test is defined
  over the query count, the payload and the duration;
- therefore a real run could only ever produce a null result that **covered
  nothing at all**.

The artifact had been saying so the whole time. Its own `work_evidence` reads
*"['db.query'] were not measured, and F6's test is defined over all three."*
Nothing was reading it, because nothing ran the node.

`Resources.counters` is now a required field — supplied, not defaulted, on
S-7.2's convention. Which counters exist is a fact about the environment, and
Epic 14 makes it exactly the set of names an adapter declares in
`Declarations.hooks`.

### 3. The ranking was computed and thrown away

`rank` puts growth flags ahead of flat-cost ones as a class and orders by
magnitude within each, because the two are measured in different units and there
is no honest exchange rate between them. `_first_flagged` read the flagged set
back **in name order**, discarding it. The node now records `order` from
`Plan.investigate`, and sorting by name survives only as the fallback for a
checkpoint written before the field existed.

### 4. What the state records, after a sabotage survived

The first version of the regression test asserted the boolean — *this workload is
flagged* — and a sabotage restoring the superlinear-only rule **passed it**. The
N+1's `seconds` metric sometimes fits superlinear on timing noise, so the wrong
rule reached the right answer by accident.

So the screening channel now carries `flagged_metrics`, and the test asserts the
flag is `db.query`. A boolean says a workload is worth investigating and not what
about it is — which means neither a reader of the state nor a test could tell a
query count flagged against its expectation from a duration that fitted
superlinear on noise. `flag` is the same function `rank` calls, so the detail
cannot disagree with the plan.

### 5. What is still open, and why it is not stubbed

**`pull_request` has no caller.** `ship`'s docstring said the omission was
pending S-16.2; S-16.2 has landed, so it is now a named seam. The blocker is
concrete: `pull_request` needs a live `PatchVerdict`, and `audit_patch` puts only
`verdict.describe()` into `flags` — a rendered string cannot be rebuilt into the
object the report takes. Closing it means making the verdict travel structurally,
which is a state channel and a story of its own. A stub at `ship` would be a
second, worse answer to a question the report package already answers.

## Consequences

**This is the seventh epic composition check and the seventh to find a real
defect.** It is also the worst haul so far: before this change, a full run could
not have found anything at all — it would have screened without a query counter,
declared every workload unverified, and reported a null result covering nothing.

**S-17.1's estimate is unaffected in direction but its result would have been
worthless.** The expected path was ground → screen → null result, and the null
result would have arrived for the wrong reason: not *this repository is healthy*
but *the harness measured nothing*. The holdout would have been spent on a run
that could not distinguish those.

**A shared planted-subject fixture exists now**, at
`tests/fixtures/planted/subject.py`. Five screening modules define `Subject` and
`StoreReset` by hand; a sixth copy in another directory was not the problem, but
importing one test module from another is — mypy resolves it under two names and
refuses, the same collision `pyproject.toml` documents for `conftest`. The five
copies are deliberately left alone: consolidating them touches five passing
modules for no behavioural gain, and doing it inside a composition check would
mix a refactor into a finding.

**Sabotage: 4 properties, 4 caught**, one of them only after the survivor above
forced the state to carry which metric flagged.

# S-0.8 — Can a model select the right instrument?

**Status:** **executed**
**Built:** 2026-08-02
**Date run:** 2026-08-04
**Model:** `claude-opus-5`, adaptive thinking, `effort: high`, `max_tokens: 2048`

---

## What this decides

Whether the project's central claim holds at the step the brief names as the
bottleneck: *choosing which method applies*. Everything E0 tested so far proves
the machinery is buildable. This tests whether the thing the machinery exists to
enable actually works.

**Decision rule.** Set before running, so the result cannot be rationalized:

- **Trap avoidance ≥ 90% and finding discipline ≥ 90%** — the selection step is
  sound. Proceed to E1–E7 with the thesis materially de-risked.
- **Trap avoidance ≥ 90%, finding discipline < 90%** — the model reasons well
  and stops badly. E9's finding audit becomes non-negotiable and must ship
  before any finding reaches a human. Record in S-17.2.
- **Trap avoidance < 90%** — the thesis is in trouble. Do not proceed to E7
  without revisiting how much of the selection problem is code rather than
  agent.
- **`noise_no_finding` fails at all** — treat separately and seriously. It is
  the one scenario backed by a project invariant rather than a preference.

---

## Scorer calibration — done, before any run

`python run.py --self-check` reports **6/6 scenarios discriminate correctly**:
for each one, an ideal answer scores clean and a trap-falling answer is caught.
Re-confirmed immediately before this run.

This was not a formality. The first scorer searched the model's prose for
forbidden substrings and marked the *correct* decoy answer — "query count is
constant, so this is **not** an N+1" — as falling into the trap, because
substring matching cannot see negation. The sharpest scenario in the set would
have scored 0% however well the model reasoned. Diagnoses are an enum now.

Re-run the self-check after any change to a scenario, and before trusting a run.

**A second calibration defect surfaced in this run, and is recorded below rather
than silently fixed.** See *`post_ablation_residual` — a criterion defect*.

---

## Results

Raw per-run record: `results/selection.json`. 10 repeats per scenario, 60
requests total.

| Scenario | Instrument | Diagnosis | Trap avoided | Finding discipline | Instruments chosen |
|---|---|---|---|---|---|
| `real_n_plus_one` | 50% | **100%** | 100% | **100%** | substitution 5, ablation 5 |
| `decoy_fixed_floor` | 0% | **100%** | **100%** | 90% | ablation 10 |
| `over_fetch_invisible_to_query_count` | 30% | **100%** | **100%** | **100%** | substitution 7, scaling 1, ablation 1, observation 1 |
| `post_ablation_residual` | **100%** | 0% † | 100% | 0% † | scaling 10 |
| `flat_queries_time_grows` | **100%** | **100%** | 100% | 70% | observation 10 |
| `noise_no_finding` | 0% | **100%** | **100%** | **100%** | scaling 10 |

† Criterion defect, not a model failure — the scenario was repaired and re-run
the same day and scored **100% on all four axes**. See below. The row above is
left as it ran.

**Repeats per scenario:** 10
**Mean trap avoidance (trap scenarios only):** **100%** (30/30)
**Mean finding discipline (trap scenarios only):** **97%** (29/30)

Trap-tagged scenarios are `decoy_fixed_floor`,
`over_fetch_invisible_to_query_count`, and `noise_no_finding`.

**Cost:** ≈ $2 for 60 requests. Ceiling was $3.25, fixed by `max_tokens`.

---

## Where it failed, and what it said

### `post_ablation_residual` — a criterion defect

Scored diagnosis 0% and finding discipline 0%, and **neither number should be
read as a model failure.** The evidence presents 504 `helpdesk_customfield`
queries as a **single-point measurement with no row count**. From that, an N+1
and a high-but-fixed per-request count are not distinguishable. The scenario
requires the diagnosis `n_plus_one`.

The model answered `insufficient_evidence` 10/10 and chose `scaling` 10/10 —
the exact instrument that separates the two hypotheses:

> *"Both the ablated 686 queries and the residual 504 helpdesk_customfield
> queries are consistent with either an N+1 (count grows with followups/tickets
> returned) or a high but fixed per-request count (e.g. one query per custom
> field definition, independent of res…"*

> *"The ablation localized cost but the query counts are single-point
> measurements. Varying the number of tickets/followups in the response and
> fitting queries-vs-rows separates the two candidate defects…"*

**The scenario's stated hypothesis passed.** Its note reads: *"The correct move
is to keep going, not to stop at the first finding. A model that reports the
1020ms result and concludes the investigation has left a second defect on the
table."* The model did keep going — `no_defect_found` was avoided 10/10 and the
localizing instrument was chosen 10/10. What the criterion additionally demanded
was a conclusion the evidence cannot support, which is the opposite of what this
project asks of its own findings.

**This reading is post-hoc and was authorized by a human before being written
here.** The as-run numbers above are unaltered.

#### Resolved by re-run, same day

The scenario was repaired rather than its criterion loosened. The evidence now
carries a scale sweep — 126 / 252 / 504 customfield queries against 25 / 50 /
100 rows, a flat ~5.04 per row — so an N+1 is genuinely determinable from it.
`acceptable_diagnoses` was **deliberately left at `('n_plus_one',)`**; widening
it to admit `insufficient_evidence` would have made the scenario unable to fail,
and commitment is the thing it exists to test. The scorer self-check was re-run
after the change and still reports 6/6 discriminating.

Re-run alone, 10 repeats, $0.29 — `results/post-ablation-rerun.json`:

| Scenario | Instrument | Diagnosis | Trap avoided | Finding discipline | Instruments chosen |
|---|---|---|---|---|---|
| `post_ablation_residual` (repaired) | **100%** | **100%** | **100%** | **100%** | scaling 7, ablation 3 |

**The model was right and the criterion was wrong.** Given evidence that
determines the answer, it commits to `n_plus_one` 10/10 and warrants the finding
10/10 — the two axes it scored 0% on before. Nothing about the model changed
between the two runs; only the sufficiency of what it was shown.

That is worth more than the corrected score. The original 0% would have been
recorded as a model weakness and carried into E9's design, and the thing that
caught it was a model refusing to assert something its evidence did not support
— the exact behaviour this project asks of its own findings.

### `flat_queries_time_grows` — finding discipline 70%

Three runs of ten answered `finding_warranted: False` where the scenario expects
`True`. The *diagnosis* was correct 10/10 and the instrument switch was correct
10/10. The three off-cases decline to call a finding yet, not to reason wrongly:

> *"Query count is flat at 4 across an 8x increase in rows, and database time is
> constant at 11ms, so this is not an N+1 and the database is not the cost. Time
> grows almost exactly linearly with rows and bytes (about 1.1ms per row…"*

Same under-commitment as above: correct conclusion, withheld verdict pending
localization.

### `decoy_fixed_floor` — finding discipline 90%

One run of ten asserted `finding_warranted: True` while correctly diagnosing
`constant_per_request_overhead`. It is the only case in 30 trap-scenario runs
where discipline slipped toward asserting rather than withholding, and it did so
without falling into the forbidden diagnosis.

### The instrument-selection rates are low, and that is the real finding

`none_report_no_finding` was chosen **0 times in 60**. On `noise_no_finding` the
model correctly concluded nothing was there — diagnosis 100%, finding discipline
100%, all four forbidden diagnoses avoided — and still selected `scaling` as the
next experiment, against a shift of 7.4 ms sitting inside a measured 12.76 ms
spurious-shift floor with guard counters byte-identical.

This is the pattern behind every low instrument score in the table. It is
coherent, not noise: **the model reasons correctly about the evidence and then
declines to stop.**

---

## Verdict

**Decision: PASS on the pre-registered rule — first branch.** Trap avoidance
100% and finding discipline 97%, both clear of the ≥90% bar. The selection step
is sound; proceed to E1–E7 with the thesis materially de-risked.

`noise_no_finding` — the scenario backed by a project invariant rather than a
preference — did not fail. It produced no finding 10/10 and avoided all four
forbidden diagnoses 10/10.

The thesis scenario `flat_queries_time_grows` passed on both measures the
architecture is justified by: correct diagnosis 10/10, and the instrument switch
away from query counting 10/10.

**Consequences for the build** — no story gains or loses scope on the strength of
this result. The de-risking is of the premise, not of any particular design.

**Consequences for E9** — the finding audit is still required, but **for the
opposite reason than expected.** The audit was scoped against fabrication: a
model inventing a finding the measurements do not support. Across 60 runs that
did not happen once. What happened instead was persistent under-commitment —
correct reasoning, withheld verdicts, and a next experiment always proposed.

The risk E9 must actually address is **non-termination**: an investigation that
never concludes because the agent always has one more measurement worth taking.
Every extra experiment is a real run against a real repository, so this is a
cost and latency failure mode, not only an epistemic one. It also implies the
stopping decision probably cannot be the agent's own — a budget halt (S-5.4)
bounds the damage but does not decide sufficiency.

---

## Bounds on this verdict

State these whatever the outcome; a good result is easier to over-read than a
bad one.

- **Six scenarios, one framework.** All drawn from Django/Postgres measurements.
- **The evidence was handed over, not discovered.** A real run has the agent
  designing the experiment, reading its own noisy output, and deciding when to
  stop — none of which this tests. It isolates the selection step deliberately,
  and that is a narrower claim than "the agent works".
- **The scenarios are curated.** A human already worked out each correct answer
  with the same measurements in hand. Real investigations do not arrive
  pre-framed as a well-posed multiple choice.
- **Scored against one model.** Says nothing about routing, cascading, or the
  cheaper tiers E5 will introduce — and `CLAUDE.md` already forbids cascading
  hypothesis generation to a cheap model, which this cannot verify.
- **One scenario's criteria did not survive contact with a result.** That is one
  in six, found by a model disagreeing with it. The other five may contain the
  same defect undetected, because a model that happens to agree with a
  mis-specified criterion scores 100% and reveals nothing.
- **Passing is necessary, not sufficient.** S-8.7 remains the real test; this
  spike only makes it more likely to succeed, and cheaper to fail.

---

## Follow-on

### Done, 2026-08-04

1. ~~**Fix `post_ablation_residual` properly, then re-run that scenario
   alone.**~~ Done — scale sweep added, criterion left narrow, self-check
   re-confirmed 6/6, re-run scored 100% on all four axes for $0.29. Written up
   under *Resolved by re-run* above.
2. ~~**Record token usage per run.**~~ Done — `ask()` returns usage, every run
   carries `usage`, and the result file carries a `usage` total and a
   `cost_usd` estimate. The run now prints its own receipt:
   `10 requests, 13,270 in + 8,829 out, $0.29 at claude-opus-5 list price`.
   List price is hardcoded with the date it was correct; the billing console
   stays authoritative.
3. ~~**Flush stdout.**~~ Done — `flush=True` on every progress line.
4. **`--scenario` and `--out` added** while fixing the above. Re-running one
   repaired scenario costs a sixth of a full pass, and a partial run writes to
   its own file: model output is not deterministic, so overwriting a completed
   pass destroys evidence that cannot be regenerated. The trap summary is now
   guarded — a selection containing no trap scenario says so rather than
   dividing by zero.

### Still open

5. **`none_report_no_finding` needs a fixture, not a prompt.** Chosen 0/60. If
   the agent is to be able to stop, S-8.1 / S-8.2 cannot rely on the instrument
   merely being available in the list.
6. **Re-check the other five criteria the way this one got checked.** One in six
   did not survive contact with a result, and it was caught only because the
   model disagreed with it. A criterion the model happens to agree with is
   indistinguishable from a correct one at this sample size.
7. Whatever fails here becomes a scenario in `tests/fixtures/` (S-0.7) or a
   prompt requirement on S-8.1 / S-8.2.
8. The scenario set should grow the same way the fixture repo does: whenever a
   real investigation produces a shape that a careless reading gets wrong.

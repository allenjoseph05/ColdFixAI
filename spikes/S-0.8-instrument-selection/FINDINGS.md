# S-0.8 — Can a model select the right instrument?

**Status:** scenarios and harness built; **not yet executed** — no API
credentials in the environment this was written in.
**Built:** 2026-08-02
**Run by:**
**Date run:**
**Model:**

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

This was not a formality. The first scorer searched the model's prose for
forbidden substrings and marked the *correct* decoy answer — "query count is
constant, so this is **not** an N+1" — as falling into the trap, because
substring matching cannot see negation. The sharpest scenario in the set would
have scored 0% however well the model reasoned. Diagnoses are an enum now.

Re-run the self-check after any change to a scenario, and before trusting a run.

---

## Results

Fill from `results/selection.json`.

| Scenario | Instrument | Diagnosis | Trap avoided | Finding discipline | Instruments chosen |
|---|---|---|---|---|---|
| `real_n_plus_one` | | | | | |
| `decoy_fixed_floor` | | | | | |
| `over_fetch_invisible_to_query_count` | | | | | |
| `post_ablation_residual` | | | | | |
| `flat_queries_time_grows` | | | | | |
| `noise_no_finding` | | | | | |

**Repeats per scenario:**
**Mean trap avoidance (trap scenarios only):**
**Mean finding discipline (trap scenarios only):**

### Where it failed, and what it said

One block per failure. Quote the model's own `conclusion` and `why` — the
wording is the finding, not the score.

---

## Verdict

**Decision:**

**Consequences for the build** — which stories gain or lose scope:

**Consequences for E9** — does the finding audit need to ship earlier than
planned?

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
- **Passing is necessary, not sufficient.** S-8.7 remains the real test; this
  spike only makes it more likely to succeed, and cheaper to fail.

---

## Follow-on

- Whatever fails here becomes a scenario in `tests/fixtures/` (S-0.7) or a
  prompt requirement on S-8.1 / S-8.2.
- If finding discipline is the weak axis, S-9.x gains priority over E10 — there
  is no point building a Surgeon for findings that should not have been made.
- The scenario set should grow the same way the fixture repo does: whenever a
  real investigation produces a shape that a careless reading gets wrong.

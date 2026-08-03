# 011 — Development target, holdout, and reserve

**Status:** accepted
**Date:** 2026-08-02

Closes S-0.6. Pins are machine-readable in `targets.toml`; this file is the
rationale.

## Context

Development needs a fixed subject with known problems, and evaluation needs a
subject development has never touched. The backlog states the risk plainly:

> the holdout matters — developing and evaluating against the same repo produces
> a tool that works on exactly one repo.

S-0.3 grounded three Django repositories by hand and S-0.4 measured two of them.
That produced enough evidence to choose without guessing, which is the point of
having run the spikes first.

## Decision

| Role | Repository | Commit | Why |
|---|---|---|---|
| **Development target** | `django-helpdesk` | `3a22901` | A real, unplanted N+1 with a measured signature |
| **Holdout** | `healthchecks` | `5086d28` | Correctly optimized; the right answer is *nothing found* |
| **Reserve** | `netbox` | `4877d11` | Mature system, high fixed floor, nothing to fix |

### Development target — `django-helpdesk`

`GET /api/tickets/` runs **1193 queries to return 100 tickets**. The defect is a
nested N+1: the ticket serializer includes `followup_set` as a nested
`many=True` field, and the follow-up serializer in turn includes
`followupattachment_set`.

**Expected measurement signature.** Query count scales with rows returned rather
than staying constant:

```
queries ≈ 1 + T + F + T
          │   │   │   └── one custom-field query per ticket
          │   │   └────── one attachment query per followup
          │   └────────── one followup query per ticket
          └────────────── the ticket list itself
```

Measured at T=100 tickets, F=586 followups on the scaled dataset:

| | Baseline | After ablating `followup_set` |
|---|---|---|
| Queries | **1193** | 507 |
| Response bytes | 429 071 | 432 558 (replay) / 71 758 (empty) |
| Median response | ~1455 ms | ~435 ms |

Ablation ratio **0.29×**, Cliff's delta **−1.000** (no overlap across 400
pairwise comparisons), against a measured detection floor of ~20 ms. The effect
is roughly fifty times the noise.

**There is a second defect underneath the first.** After ablating `followup_set`,
507 queries remain, **504 of them on `helpdesk_customfield`** — one per ticket.
That second N+1 is invisible while the first dominates, which makes this target
useful for more than one story: it exercises the localization loop, not just
detection.

**Why an unplanted defect rather than one we plant.** A defect we introduce
encodes our own assumptions about what detection should look like, and a
detector built against it can pass by recognizing our handiwork. This one was
written by someone who did not know this tool would exist.

**Caveat carried from S-0.4.** The shipped fixture is 3 tickets. Any story using
this target must multiply the object graph first — `seeds/scale_helpdesk.py` in
the S-0.4 and S-0.5 spikes does this deterministically to 503 tickets / 3004
followups / 3002 attachments, and both spikes' numbers are taken at that scale.

### Holdout — `healthchecks`

`GET /api/v3/checks/` serves 50 checks in **3 queries**: one for the project, one
for the checks, one prefetch for channels. It is already correctly optimized and
S-0.3 found no defect to report.

**That is the reason it is the holdout.** Two project invariants —

> **Null results are valid output.** "Screened 9 workloads, nothing found" ships
> as an answer. Never manufacture a finding.

— describe the failure this repository tests for. A holdout that contains a
defect only measures whether the tool generalizes. A holdout where **the correct
output is "nothing found"** measures whether the tool can resist producing a
finding when there is nothing to find, which is the more dangerous failure and
the one the invariants exist to prevent.

`healthchecks` also happens to be the favourable end of the grounding range —
standalone project, `manage.py` at the root, clean settings — so a grounding
failure on it would be strong evidence of a real problem rather than an unlucky
repository.

### Reserve — `netbox`

Neither target nor holdout. Its endpoints show a **fixed floor of ~35 queries
with sublinear growth** — the shape a mature, well-maintained system actually
has. There is nothing to fix, so it is not a target; it is a poor null-result
test because its complexity is a confound, so it is not the holdout.

It is the right subject for S-17.3's realistic-scale work, and for exercising the
guard-counter invariant: a change lowering that floor while inflating rows
returned would look like a win on every metric.

**It ships `CLAUDE.md` and `AGENTS.md` at its root.** Any agent result obtained
from it is optimistic relative to a typical repository, and must be reported that
way.

## Holdout discipline

The holdout is worthless the moment it is used during development, and a rule in
a document does not prevent that. Per the project's own standard — *"if you find
yourself relying on this file to prevent something dangerous, that rule needs
code instead"* — the rule is enforced by `tests/test_holdout_discipline.py`,
which fails if the holdout is referenced outside the small set of files
permitted to name it.

Two consequences worth stating:

- **`healthchecks` was grounded once, in S-0.3, before it was designated.** That
  is a real if minor contamination: its grounding obstacles are recorded and
  informed the playbook proposals. It has not been measured, ablated, or used to
  develop anything since, and it will not be again until evaluation.
- **The enforcement test is the first test in the repository.** S-0.7 builds the
  actual test strategy; this is one guard, not that.

## Consequences

**Makes easy.** Both subjects are already grounded, reproducibly, by committed
scripts. S-0.4 and S-0.5 both ran against the target, so the development subject
arrives with a measured ablation delta, a measured reset cost, and a known noise
floor — the baselines E1 needs are already taken.

**Makes hard.** One target means one framework shape. Every early story will be
implicitly Django-shaped, and E14's adapter work is where that gets tested. This
is accepted: the alternative is generalizing before there is a second case, which
the project's own rule forbids.

**Rules out.** Using `healthchecks` for anything until evaluation, including as a
convenient second example when a Django question comes up. That temptation is
exactly what the enforcement test exists to catch.

## Provenance

`spikes/S-0.3-grounding/FINDINGS.md` for grounding and the null-result argument;
`spikes/S-0.4-ablation/FINDINGS.md` for the defect signature, the ablation delta,
the second N+1, and the detection floor.

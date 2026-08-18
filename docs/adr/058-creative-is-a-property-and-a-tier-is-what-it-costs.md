# 058 — "Creative" is a property, and a tier is what it costs

**Status:** accepted
**Story:** S-5.5 — model routing
**Date:** 2026-08-09

## Context

S-5.5 asks for four things:

- each call site declares `creative` or `mechanical`;
- routing maps step class to model tier;
- tier assignment is configurable **without code changes**;
- a test asserts mechanical steps never hit the frontier tier **by default**.

The third sits directly against S-5.4's backlog note — *caps must be in code, not
configuration* — one story earlier in the same epic. And `CLAUDE.md` carries a
non-negotiable that configuration could otherwise walk straight through: *never
cascade to a cheap model on hypothesis generation or attack design; no
deterministic validator exists for those.*

## Decision — the class is derived from `04-cost.md` §3, not believed

§3 is a table of eight step types and, against each, the mechanical check that
would catch a wrong cheap answer. Six have one. Hypothesis generation and attack
design record *none exists*.

So `creative` is a **property of the step type** — creative exactly where no
validator exists — and `STEP_KINDS` encodes §3 verbatim, keeping the *name* of
each check rather than reducing it to a boolean. A boolean nobody can audit is a
routing decision made on a fiction, and S-5.6 needs to know what validates a step
before it may retry one.

AC 1 still holds: a call site declares. But `check_declaration` refuses a
declaration that disagrees with the table. Believed, a mislabelled step is the
entire non-negotiable defeated through the front door — relabel hypothesis
generation as mechanical and the router sends it to the cheap model, with every
other guard here satisfied. This is `08-audit.md` F6's finding again: a
self-declared property that decides something is one the declarer is incentivised
to get wrong.

## Decision — configuration may route dearer, never cheaper

S-5.4 and S-5.5 are asymmetric in mirror image, and both are right, because the
harm runs in opposite directions:

| | Permitted | Refused | Why |
|---|---|---|---|
| S-5.4 caps | lowering | raising | unbounded spend is the harm |
| S-5.5 routing | dearer | creative below frontier | an unvalidatable wrong answer is the harm |

A cheaper model arriving is the normal case and must not need a release, which is
AC 3. Routing a *mechanical* step to the frontier costs money and cannot cost
correctness — which is exactly why AC 4 says mechanical avoids the frontier *by
default* rather than always, and that override is permitted and tested. Routing
creative work below the frontier is refused at both the class-level route and the
per-phase one, because a rule enforced only on the general key is one that gets
walked around on the specific one.

## Decision — a tier is what it costs, not what it is called

This is the hole every other rule leaves open. Creative work always routes to the
tier *named* frontier, and the models behind the tiers are configurable — so a
configuration that puts the cheapest model in the frontier tier satisfies every
check above while defeating all of them.

So the tiers are resolved against S-5.3's price book and must be **ordered by
price**: frontier at least as dear as mid, mid at least as dear as cheap.
Inversion is refused; equality is allowed, because two tiers on one model is a
legitimate deployment and refusing it would be strictness with nothing behind it.

A tier pointed at a model with no published price is also refused — a routing
that named an unpriceable model would produce a run whose cost is unknown, which
is the one thing S-5.3 exists to prevent.

## Decision — the route is keyed on phase as well as class

AC 2 says *routing maps step class to model tier*, and routing that literally
cannot express `04-cost.md` §12.3's engineered case: grounding's mechanical calls
run on the cheap model with a mature playbook — ten calls at $0.01 for the whole
phase — while the investigate loop's mechanical calls run mid-tier. Two mechanical
steps, two tiers, distinguished by phase. A router that could not express that
could not implement the cost model the project's own €2,150 figure rests on, so a
route falls back to the class only when no phase-specific one exists.

## Consequences

**`model_for` has no default step class.** A default would let a call site
decline to declare, and the ~220 mechanical calls a run makes would land on the
frontier model without anybody choosing it. `route(step_type, phase)` is the form
to prefer where the call site knows what it is doing, since a derived class
cannot be misdeclared at all.

**ADR-002 is not contradicted.** It names `claude-opus-5` as the default for
patch authorship, while §12.3 routes repair mid-tier with a cascade. Both are
right: ADR-002 states the *unrouted* default and explicitly makes cheaper tiers
available to E5's routing wherever a deterministic validator exists. A patch has
one — the test suite — so it is mechanical here.

**`frontier_share` puts the story's *why* in the run report.** ~30 of ~250 calls
should need the frontier model; a share drifting upward is the routing quietly
ceasing to work, and a report carrying only a total would never show it.

**This is the third story in Epic 5 whose acceptance criteria summarize a table
elsewhere in the documents, and the third where implementing the AC literally
loses what the table specifies.** S-5.3's field list omitted that the API reports
two cache figures; S-5.4's *exhaustion halts* omitted §7.2's four dispositions;
S-5.5's *class to tier* omits §12.3's per-phase routing and §3's validator column.
The pattern is worth expecting rather than rediscovering: in this epic the
backlog is the summary and `04-cost.md` is the specification.

**Sabotage-verified on twenty-one properties, all caught.**

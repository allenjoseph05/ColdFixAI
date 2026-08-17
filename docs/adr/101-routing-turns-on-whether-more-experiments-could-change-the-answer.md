# 101 — Routing turns on whether more experiments could change the answer

**Status:** accepted
**Story:** S-9.8 — verdict and routing
**Date:** 2026-08-17

## Context

Six attacks have answered. S-9.8 has to turn their answers into one verdict from
`10-BACKLOG.md`'s five-member vocabulary — `sound` / `unsound` + objection /
`unrepresentative` + reason / `negative_sound` / `inconclusive` + what is missing
— and decide where the run goes next.

Two of those five and the budget condition on `unsound` were added by ADR 094
after S-0.8 measured the agent declining to stop 60 times out of 60. That ADR's
warning governs every choice here: **an audit whose only lever is *run more
experiments* makes the one failure the spike actually measured worse.**

## Decisions

### 1. The verdict is computed, not asked

`CLAUDE.md`: *do not add a model call where a function would do.* Every attack
has already produced a boolean-ish answer; combining them is counting. That makes
five of Epic 9's seven attacks plus its routing decision arithmetic — worth
recording because the epic's own vocabulary (*attacks*, *the Adversary*) reads as
adversary calls from end to end, and a reader skimming the backlog would price it
that way.

### 2. `unsound` and `unrepresentative` are separated by whether an experiment
could settle them, and the routing falls out of that

An unsound finding is a claim the evidence does not support. Another experiment
can settle it, so it goes back to investigate.

An unrepresentative finding is usually **correct** — the N+1 is real — about
something nobody runs. No experiment changes that. Routing it back would spend
the investigate budget establishing a better answer about the wrong subject.

So when both land, **`unrepresentative` wins**. This is not a preference between
two objections; it is the only ordering under which the audit does not spend
experiments it cannot use. The precedence is a consequence of the routing rather
than an axiom sitting above it.

It is also safe in the direction S-9.7 cared about. That story made
`unrepresentative` default *off* because a wrong one discards a real finding
silently. Precedence does not make the verdict easier to reach — it still needs a
stated reason from the auditor — it only decides what happens once it has been
reached with one, and skipping a finding that is *also* unsound discards less
than skipping a sound one.

### 3. `inconclusive` exists so that an attack which did not run cannot read as
one that passed

This is S-3.1's distinction between *no* and *not known*, which S-9.4 already
drew for a missing fit and S-9.6 for a metric that vanished. A four-verdict
vocabulary reintroduces it at the top of the epic: an audit that ran two of six
attacks and objected to neither would report `sound`.

**An attack that does not apply is not an attack that is missing.** Collapsing
them makes `inconclusive` the answer to almost everything — a diagnosis resting
on an ablation has no sweep to audit — and an audit that escalates every finding
is as useless as one that passes every finding, and less obviously so. So
`Outcome` has four members, and the two that both mean *no answer* stay apart.

`inconclusive` sits **below** both objections. An audit that landed a real
objection has told the reader something actionable; reporting *the audit was
incomplete* instead would bury it.

### 4. `negative_sound` is unreachable from a finding by schema, not convention

ADR 094 added it because `00-BRIEF.md` §9 ships a null result as output and
nothing in Epic 9 could express one. S-9.9 decides when it applies; this story
owns the vocabulary.

An `AuditVerdict` carries the `Subject` it is about. `negative_sound` requires a
`PartialChain`; `sound`, `unsound` and `unrepresentative` require a finding —
each presupposes a claimed cause, and a `PartialChain` confirms nothing by
construction (S-8.9), so there is nothing in one to repair, to disprove, or to
call unrepresentative. `inconclusive` is legal about both, because *an attack did
not run* and *this run stopped too early* are the same verdict about different
artifacts.

This is S-8.9's shape reapplied: two artifacts that partition, neither able to
impersonate the other.

### 5. `inconclusive` escalates rather than asking for experiments

What is missing is an *attack*, not a measurement. More experiments cannot
complete an audit that did not run, and asking for them would add spend to close
a gap somewhere else entirely — ADR 094's hazard reached through the verdict.
`Disposition.ESCALATE` is already what `PHASE_CAPS` gives this phase.

### 6. The two-round audit cap has been decorative since S-5.4, and this story
owns it

`Phase.FINDING_AUDIT`'s cap counts **rounds**. S-8.9 made `Session.run` record a
step only where a phase's cap counts steps — correctly, since a round is six
attacks and a call is not a round. Nothing else counted rounds, so the counter
stayed at zero and `authorize` compared zero against two on every call. Whoever
owns the unit counts the unit, and a round of this phase begins and ends here.

`authorize_round` is separate from `Session.run`'s authorization because that one
only fires if a round makes a model call — and four of the six attacks are
arithmetic, so a round objecting on those alone would slip past a cap enforced at
the API boundary.

### 7. The budget is read, never spent, by routing

`remaining` is a question. Charging an experiment for the decision to run one
would make the forty-experiment cap a thirty-something cap, which is S-8.9's
finding in a new place.

## Consequences

**AC 3 is met by an order of magnitude, and the figure is measured rather than
asserted.** `08-audit.md` §4 costs the finding audit at *~10 calls* against a
~50-call repair phase — the whole economic argument for running it — and that
estimate assumed six adversary invocations. S-9.2, S-9.3, S-9.4 and S-9.6 turned
out to need no model, so a **full audit makes two model calls** plus whatever
S-5.6's cascade retries. The ceiling of 15 is checked anyway, because which
attacks need a model is a property that a seventh attack could move without
anything noticing, and because the Epic 8 composition check's closing lesson was
that *a defect whose only symptom is a cost figure needs a test that reads the
cost figure*.

**The adapters are part of the story, not convenience.** Each attack's audit
object is turned into an `AttackResult` by a function here rather than by the
caller. The Epic 8 composition check found the opposite arrangement three times:
*the conditions and the symptom had no producer — every caller built them by hand
including every test, which is precisely why nothing noticed.*

**Sabotage: 37 properties, all caught, zero skipped, after one survived.** The
survivor is worth recording because it is this project's recurring shape: *a gap
outranks an objection* was asserted for the hard objections and not for the soft
one, so a representativeness objection alongside an attack that did not run
returned `inconclusive` and nothing failed. The property was right, the coverage
was half.

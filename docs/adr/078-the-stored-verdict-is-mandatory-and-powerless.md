# 078 — The stored verdict is mandatory and powerless

**Status:** accepted
**Story:** S-7.9 — workload artifact emission
**Date:** 2026-08-14

## Context

Three acceptance criteria — emit a validated workload object; `evidence_of_work`
is mandatory and harness-computed; the reset method is verified by S-2.7 before
emission.

`03-agents.md` §2.4 gives the reason in one line: *`evidence_of_work` is mandatory
and exists to make "it ran but did nothing" structurally unreportable as success.*
This is the story where "structurally" has to become true of a **document** rather
than of an object in memory, and that turns out to be a different problem.

**S-4.1's solution does not survive serialization.** `work_verified` is a property
with no field behind it, which is exactly what stops an agent writing it into a
`Workload` — and a property is not serialized. Dump the artifact and the verdict
is simply absent, so a document that must carry the evidence has to carry a
**copy**. A copy is the one thing an agent could edit.

## Decision

### The copy is checked against the recomputation

`EmittedWorkload` holds `work_verified` and `evidence_of_work` as ordinary,
required fields, and validating one **recomputes both from the observations it
carries** and refuses any document where the stored value disagrees.

Tampering with the evidence therefore does not produce a more convincing
workload; it produces a document that will not load. The stored copy is
**mandatory and powerless**, which is the pair AC 2 asks for and neither half
alone would give: mandatory without the check is a field an agent fills in, and
the check without the field leaves *unverified* indistinguishable from *not yet
asked*.

Three things are checked, and the third is not obvious: the verdict, the evidence
string, and that the envelope's `reset_strategy` matches the workload's
`reset_method`. The evidence string names the scales and the ratio, which is what
makes the subtler edit — leave the verdict alone and improve an observation —
fail as well.

### The reset method is proved by a type, not asserted by a name

`Workload.reset_method` is a `ResetStrategy`: a name any caller can write. So
`emit` requires S-2.7's `VerifiedReset`, whose constructor refuses a report that
did not pass — there is no way to emit while holding only the name, and no
unverified proof exists to offer.

A proof of a *different* strategy is refused rather than silently preferred.
Verification is a property of a strategy **on a project**, not of a strategy:
S-0.5 had rollback alone pass its own check and fail 10/10 on sequences. The
cycle count travels in the document for the same reason — a reader comparing two
workloads needs to know whether the guarantee behind them was established over
three cycles or ten.

### Emission goes through S-7.8's gate

`emit` calls `accept`, so a workload that did not verify is refused at the
boundary with the evidence attached, rather than emitted with `work_verified:
false` for somebody downstream to notice.

## Consequences

**Makes easy.** S-8 gets a document it can trust without re-deriving anything, and
S-8.4 gets one it can append. A tampered document fails at the read rather than
three layers downstream, with the disagreement named.

**Makes hard.** The evidence string is now part of a validation contract, so
changing the wording of `work_evidence` invalidates every document emitted before
the change. That is the correct direction — a document whose evidence no longer
matches what the current harness would say *should* be re-examined — but it means
the string is an interface, not a message, and S-17.2 will have to version it if
it wants to publish old emissions.

**Rules out.** Writing a verdict into a document and having it believed. Emitting
a workload whose reset method was verified for a different mechanism, or not at
all.

**Sabotage-verified on sixteen properties across two passes, all caught — after
one survived.** The survivor was **the same shape S-7.4 recorded**: `emit` records
`reset.strategy` rather than `workload.reset_method`, and the guard immediately
above has just established that the two are equal — so swapping them changes no
outcome and no test can distinguish them. The pair is separable only by attacking
both at once, and that combined sabotage *is* caught: removing the guard and
recording the claimed strategy accepts a wrong proof. The single-source choice is
now documented as a statement of intent rather than a behaviour, which is what
S-7.4 concluded about a redundant condition.

**Second time a passing sabotage has meant the code states a thing twice**, and
the useful generalisation is that a guard which forces two expressions equal makes
every later choice between them untestable. Look for the pair, and attack the
guard and the choice together.

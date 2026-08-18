# 096 — Narrowness is what was not varied, not what was missed

**Status:** accepted
**Story:** S-9.2 — exclusion validity attack
**Date:** 2026-08-17

## Context

*Checks whether ruled-out hypotheses were ruled out under adequate conditions;
flags exclusions whose preconditions were too narrow.*

This is S-8.5's machinery turned on itself. That story made every exclusion carry
the conditions it holds under so it *could* be reopened; this one reads those
conditions and asks whether they were ever wide enough to establish anything.

## Decision

### The second attack that turns out to be arithmetic

Which axes were varied, and by how much, is computable from `Conditions`. That is
now two of Epic 9's seven attacks needing no model — S-9.4 and this one — which
is worth noting because the epic's framing (*attacks*, *the Adversary*) reads as
adversary calls throughout.

**What this cannot do is named rather than glossed over.** It sees which axes
were unvaried; it cannot judge whether an unvaried axis was *relevant to this
particular hypothesis*. That is semantic and belongs to S-9.5. So every objection
is phrased as **what was not varied**, and the report says in words that the
relevance judgement is not being made. Claiming otherwise would be an opinion
wearing a computation's clothes.

### Uniform-only is a sharper objection than single-shape, and the asymmetry is proved

S-3.3 exists because `Σ k²` is minimized exactly when every parent has the same
number of children. So for **any** per-parent cost, the uniform fixture is the
*provably blindest* one — an exclusion established only there was established
under the shape least able to reveal what it ruled out. That is F3's worked
example, attacked.

An exclusion established only under `long_tail` is also single-shape, but long
tail is the deliberate worst case, so the objection does not apply with the same
force. Two members rather than one, for S-3.1's reason: collapsing them would
tell a reader who already used the hardest fixture that they used the blindest.

### Actionable and inherent narrowness are reported separately

A uniform-only exclusion is reopened by S-8.8's reseed — and the remedy text says
so, which closes the loop S-8.5 opened and S-8.8 built. A serial-only one is
fixed by raising concurrency; a narrow sweep by widening it.

A **single-platform** exclusion is none of those. Demanding a second architecture
is not a remedy anybody can apply, so it is recorded as a bound rather than
flagged as a defect, and it does not make an exclusion inadequate. That is
`00-BRIEF.md`'s *exclusions carry their preconditions* read as a reporting rule:
the platform was always part of the claim.

### The scale axis delegates to S-9.4

Asking the same question twice would be two statements of one rule. A missing
fit is **not** an objection: an exclusion is a rejected hypothesis and not every
rejection came from a sweep — an ablation that removed nothing rules something
out with no exponent anywhere in it, and inventing a fit to judge would be
auditing a curve nobody drew. `scales is None` means *not audited*, which S-3.1
distinguishes from *passed*.

### The control is the load-bearing half

An auditor that objected to every exclusion satisfies both acceptance criteria
and makes `00-BRIEF.md` §9's *null results are valid output* unreachable — every
proven negative rejected on the grounds that it might not hold somewhere nobody
looked. So each objection has a case that must not raise it, and *a multi-shape
sweep is flagged anyway* and *every run is called serial* are sabotages in their
own right.

## Sabotage

Sixteen properties, all caught — after one survived.

*The survivor is a shape worth naming.* The report's remedy could be deleted
without failing anything, because the tests asserted that each objection **has** a
remedy and that the two sections exist — never that the rendering **prints** it.
Testing the data and not the rendering is the same gap the Epic 8 composition
found in a different place: the reader gets the report, not the enum.

# 094 — E9 is aimed at a failure the spike could not test for

**Status:** accepted
**Story:** Epic 9 scoping, decided before S-9.2
**Date:** 2026-08-17

## Context

S-0.8 ran `claude-opus-5` over six scenarios × ten repeats and found:

- fabrication **0 times in 60** — the model never manufactured a finding, not on
  the decoy and not on the noise scenario;
- `none_report_no_finding` chosen **0 times in 60**, *including where stopping
  was the only correct answer*.

Its own verdict section says E9 "is scoped against the wrong failure mode… the
risk E9 must actually address is **non-termination**."

Epic 9's eight stories are S-9.1 (invocation) plus seven attacks, of which five
ask *is this finding claiming more than the evidence supports?* — and S-9.8
routes every non-`sound` verdict back to investigate.

## The correction that decides this

The first reading — and the one recorded in this project's memory — was that E9
is aimed at a failure that does not occur, so the attacks should be re-scoped.
**The spike's own bounds section refutes that reading:**

> **The evidence was handed over, not discovered.** A real run has the agent
> designing the experiment, reading its own noisy output, and deciding when to
> stop — **none of which this tests.**
>
> **The scenarios are curated.** A human already worked out each correct answer
> with the same measurements in hand. Real investigations do not arrive
> pre-framed as a well-posed multiple choice.
>
> **Passing is necessary, not sufficient.**

Fabrication was measured in the conditions **least likely to produce it**: clean
evidence, handed over, pre-framed as a well-posed question. The honest statement
is not *fabrication does not happen* but **fabrication was not tested for**. An
agent reading its own noisy output on a real repository is the setting where the
risk lives, and that setting is exactly what the spike excludes.

Non-termination is the stronger signal for the opposite reason: it appeared
**despite** the friendly setting. Given clean evidence and a correct diagnosis,
the model still declined to stop, 60 times out of 60.

## Decision

**S-9.2 through S-9.7 stay unchanged.** The evidence does not support cutting
them. Removing an attack on the strength of a result that could not have detected
the thing it attacks would be reading a good outcome as broader than it is —
which the spike explicitly warns against, in the sentence *a good result is
easier to over-read than a bad one*.

**S-9.8's verdict vocabulary gains the null-result case, and its routing becomes
bounded.** Two changes:

- `unsound` currently means *return to investigate with the objection in
  context*, unconditionally. Against an agent that does not stop, an audit whose
  only lever adds experiments makes the measured failure worse. Routing back is
  now conditional on the investigation having budget left; with none, it
  escalates, which is what S-5.4's `Disposition.ESCALATE` already means.
- The vocabulary has no way to say *this investigation produced no finding and
  that is a trustworthy answer*. `00-BRIEF.md` §9 makes null results shippable
  output, and nothing in E9 validates one.

**New S-9.9 — sufficiency and the null result.** Audit a `PartialChain`: were the
exclusions established under adequate conditions, and is *nothing found* a
result or a run that stopped too early? This is where the stopping decision
lives, because S-0.8 concluded it "probably cannot be the agent's own" and
S-8.9's budget cap bounds the damage without deciding sufficiency.

That story is also the one that closes the gap the Epic 8 composition left: an
investigation stopped by the cap produces a `PartialChain`, and **nothing in E9
as written can audit one** — all eight stories assume a finding exists.

## What this does not decide

Whether the attacks are worth their cost. That is measurable — `00-BRIEF.md` §6
lists *adversary value* as an ablation: run with and without, count bad findings
reaching a human — and it belongs in the evaluation epic, not here. Until it is
measured, the attacks stay because the epic's premise stands on its own: if the
diagnosis is wrong, every check the patch audit performs passes.

## Consequences

`10-BACKLOG.md`'s Epic 9 section is amended: S-9.8's AC gains the two verdicts
and the budget condition, and S-9.9 is added with S-8.9 as a dependency.

Recorded here rather than silently: this ADR overrides the recommendation in
`spikes/S-0.8-instrument-selection/FINDINGS.md` § *Consequences for E9*, on the
strength of that same document's bounds section. The spike was right that
non-termination is unaddressed and wrong that fabrication is disproven — and it
supplied the evidence for both halves.

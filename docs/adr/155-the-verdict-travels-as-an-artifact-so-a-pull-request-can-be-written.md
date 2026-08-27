# 155 — The verdict travels as an artifact, so a pull request can be written

**Status:** accepted
**Date:** 2026-08-27

## Context

Epic 16's composition check (ADR 151) found that `report.pullrequest.pull_request`
had no caller: `ship`'s docstring said the omission was *pending S-16.2*, S-16.2
landed, and the seam stayed open for a concrete reason. `pull_request` takes a
live `PatchVerdict`, and `audit_patch` put only `verdict.describe()` into `flags`
— a rendered string cannot be rebuilt into the object the report takes.

That check recorded it as *a state channel and a story of its own*. This is that
story, and it closes the last designed-and-unreachable module in `src/`.

## Decisions

### 1. `CheckpointedState.audited`, holding the verdict and both measurement sets

`audit_patch` writes the `PatchVerdict` as JSON alongside `domain_before` and
`domain_after`. The measurements travel because `PullRequest`'s before/after
table needs the *same* numbers the audit reasoned over — the node's own comment
already says measuring twice would put two different sets of figures under one
patch, and a human comparing the report against the verdict would be right to
distrust both.

It replaces rather than appends, like `repaired`, and is cleared when `ship`
publishes. A stale one is an audit a resumed run would publish a second time.

### 2. The ship gate now shows the body, not the change

What was in `flags` was `patch.describe()`: the change, with none of the evidence
for it. S-12.4 parks at `ship` for human review, and a reviewer asked to approve
a patch needs the chain that proved the finding, the guard readings, and the
numbers showing it moved. That is exactly what S-16.2 built and nothing rendered.

### 3. The ledger is written after the patch can be published

**A defect this story introduced, found by its own test.** The first version
assembled the pull request *after* `record_outcome`, so a run missing its audit
refused with a clean acceptance already written to the trust ledger. The ledger
is append-only — S-6.2's whole point — so the correction would be a second entry
rather than an edit, and the project's trust level would have moved on a patch
that never shipped.

The slack-reducing branch has always returned before recording. Reading both
paths together is what made the ordering visible, and there is now a test that
fails if the two are ever reordered.

### 4. `earlier_rounds` stays empty, and the reason is the channel

`PullRequest` can show what a *previous* round's patch was caught by. `audited`
replaces, so a second round overwrites the first and the earlier reproduction is
gone by the time `ship` runs. Carrying them needs an append-only channel of
reproductions, which is a decision about what a checkpoint holds rather than a
detail of this node.

`PullRequest` renders an empty one as nothing rather than as a heading with
nothing under it, so the omission is invisible rather than misleading. Recorded
here so it is not later mistaken for an oversight.

## What the sabotage pass found

**Two of the four sabotages survived their first attempt, and one of them was a
real gap.**

Serializing the verdict *without its attack results* changed no test outcome.
`test_shipping_publishes_...` built the `audited` channel by hand and
`_audited_from` was tested against a hand-built dict — **neither test held both
ends**, and nothing drove the `audit_patch` node at all. That is the same shape
the composition check had just found at `screen`, one node along, which is worth
noting: a node nobody drives is the recurring hiding place in this codebase.

`PatchVerdict.results` matters because it is what a reviewer reads. A body saying
*clean* without naming the attacks that ran asks for a merge on the strength of
one word. There is now a test that drives `audit_patch` with the audit itself
recorded rather than run, and reads the channel it wrote.

The other survivor was my own sabotage being wrong: I moved `record_outcome`
*after* the check rather than before it, which does not reproduce the defect.
Re-applied correctly, it was caught.

## Consequences

**Nothing in `src/` is designed-and-unreachable any more.** `ExperimentRef` came
off that list at S-8.12, `gates_for` and the playbook earlier; `pull_request` was
the last, and the finding branch of a run now produces a document.

**A process slip is recorded with it.** The gate for S-15.4 was run before its
ADR and backlog note were written, and both named the holdout while explaining
why not to — so the commit that landed on `main` failed
`tests/test_holdout_discipline.py`. It was fixed in this story's branch. **Run
the gate after the documents, not before**: they are files in the repository and
one of the gate's tests reads them.

**Sabotage: 4 properties, 4 caught** — the verdict serialized without its
attacks, the pull request assembled and not published, the audit left behind for
a resumed run, and the ledger written before the patch could be published.

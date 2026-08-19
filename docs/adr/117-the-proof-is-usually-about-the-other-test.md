# 117 — The proof is usually about the other test

**Status:** accepted
**Story:** S-11.6 — test-quality attack
**Date:** 2026-08-19

## Context

The backlog calls this the deepest move in the design: *the Adversary audits the
verifier, not only the artifact.* Its acceptance criteria are:

- asks whether a cheat could pass the Surgeon's falsification test;
- if yes, writes the test that would catch it;
- the strengthened test becomes the permanent regression test.

The first two already exist. `08-audit.md` §3.3 found the flaw this story was
written against — *the test is written by the agent that then writes the patch* —
and its fix was to move the audit **earlier**: *the falsification test is
submitted and audited before the patch is written.* S-10.3 built exactly that,
with no `patch` parameter so the ordering could not be got wrong.

The backlog's wording for S-11.6 predates that correction. `CLAUDE.md` is explicit
that where `08-audit.md` and the earlier documents disagree, the audit wins.

## Decisions

### 1. AC 1 and AC 2 are imported, not written again

`Weakness`, `render_test`, `parse` and `check_stronger` come from S-10.3. The
reply schema is identical — weaknesses, and a replacement if there are any — so
`invoke` returns S-10.3's `TestAudit` rather than a parallel type that a composed
path would have to branch on twice for the same branch.

`check_stronger`'s three refusals therefore apply here unchanged: the cost
threshold may not rise, no guard may vanish, and the replacement must claim to
catch what this audit just said would slip through.

### 2. What this story owns is the evidence and the artifact

**The evidence.** S-10.3 asks *could some change* slip through this test. This
asks *did this one*, because by now the diff exists. A hole nobody could name in
the abstract is often obvious once you can see what the patch actually did. The
test is rendered first and the diff second, deliberately: leading with the diff
would invite an audit of the change, which is S-11.2 to S-11.5's job.

**The artifact.** AC 3 exists nowhere else. S-10.3's `forward` is the test the
Surgeon must satisfy *in this repair* — consumed and forgotten. A permanent
regression test ships with the patch and runs against every later change, so the
bar for creating one is higher.

### 3. `RegressionTest` needs both proofs, and the second is the trap

It must have **failed** on the unpatched code, or it is not about this bug. It
must have **passed** on the patched code, or it cannot ship green.

The trap is that the `Falsified` lying around at this point is a proof about the
**Surgeon's original** test, and the artifact being shipped is the **Adversary's
strengthened** one. Attaching the first to the second is S-10.3's *a strengthened
test is not trusted, it is re-gated* failed at the last possible moment — and the
result is a permanent regression test **nobody has ever watched fail**. The
constructor compares them.

`keep` reads `audit.forward` rather than either field directly, so the weak test
cannot be shipped by reading the wrong one. This is the layer where that mistake
would be permanent.

### 4. A sound test is kept too

`closes` may be empty. A regression test is still worth keeping when nobody found
a hole in it — what makes it worth keeping is the pair of proofs, not the audit
having objected.

### 5. Isolation is S-11.1's, unchanged

The subject is a `Candidate`, which has no field for `rationale` or `approach`, so
`invoke` cannot be handed the Surgeon's account of itself. The session is
`audit_session` with this story's system text — the **fourth** audit to share that
constructor, after the finding audit, S-10.3's and S-11.1's.

## Consequences

**The module warned about the pytest-collection hazard and then walked into it on
a function name.** `TestQualityError` and `RegressionTest` both carry
`__test__ = False` because pytest collects a class on the `Test` prefix alone —
and the session constructor was named `test_quality_session`, which pytest
collects as a **test function** on the `test_` prefix alone. It errored on
fixtures it does not have. Renamed to `quality_session`, with a test asserting no
name in the module starts that way.

**A second-round audit that finds nothing is the expected result, and the residue
says so.** The classes are the same five both times; the only new information is
the diff. If seeing the change does not suggest a hole that seeing the evidence
alone did not, this round cost a call and found nothing — which is precisely what
S-11.8's ablation exists to measure, and the honest place to record the doubt is
here rather than in the ablation's conclusion.

**Sabotage: 24 properties, all caught, zero skipped, none survived** — the first
story in this epic where the fixtures discriminated on the first pass. The
difference is that most of the properties are about *reuse* (is this S-10.3's
parser, is this S-10.3's renderer) and about a constructor's refusals, both of
which are hard to write an indiscriminate fixture for.

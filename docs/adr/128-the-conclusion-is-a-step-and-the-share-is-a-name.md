# 128 — The conclusion is a step, and the share is a name

**Status:** accepted
**Date:** 2026-08-21

## Context

ADR 127 lifted Epic 7's composition out of its test. S-8.11 is the same job for
Epic 8, and it turned out not to be the same shape.

Epic 7's sequence existed and only needed a home. Epic 8's did not exist. The
loop stops deliberately short of a chain — `confirming_links` says so in as many
words, *the loop does not build the chain, and that is a refusal rather than an
omission* — and `chain_from` takes the rest from its caller. The only caller was
a test, and it supplied the missing half as three literal strings.

So `chain_from` needed two producers, and neither had ever been written:

| Part of the chain | Who owns it | Existed? |
|---|---|---|
| symptom | screening, via `symptom_for` | yes |
| complexity | screening's growth table | yes |
| exclusions | the investigation's register | yes |
| mechanism, site, context | the Diagnostician | **no** |
| localization's share of cost | the primitive that ran | **no** |

## Decisions

### 1. Stating the conclusion is a routed step, and §3 already had a row for it

`StepType.EVIDENCE_CHAIN` was in the routing table before anything used it,
carrying the mechanical check *schema requires a measurement*. That check is what
makes a cascade safe here. `CLAUDE.md` forbids cascading on hypothesis generation
and attack design because no deterministic validator exists for either; here one
does, and it is the schema rather than a prompt — a cheaper tier that invents a
site produces a chain `EvidenceChain` refuses.

`explain` is therefore an ordinary cascading step, built the way `interpret` is.

**The reply cannot carry a number.** `Explanation` has three fields — a
mechanism, a site, a context list — and `extra="forbid"`. A model that wants to
report a confidence or a share has nowhere to put it, and the attempt is rejected
rather than silently trimmed. The test tries three spellings, because a schema
that refused one word would be refusing a vocabulary rather than a behaviour.
Sabotage-verified: `extra="ignore"` fails all three.

**An investigation that confirmed nothing is never asked.** The refusal is before
the call, not after it — `00-BRIEF.md` §9 makes a run with no cause a null result
and S-8.9 gives it a partial chain. Asking a model to explain a cause nothing
established is the one place a finding could be written with no measurement under
it, and it would cost a frontier call to do.

### 2. The share of cost was not unreachable — it was unnamed

The first draft of this work recorded that `AblationResult.share` computes the
fraction a finding quotes, that `Executor` returns `Mapping[str, float]`, and
that therefore nothing could carry it across the loop boundary. **That was
wrong**, and the thing that disproved it was Epic 8's own test fixture, which had
been emitting `round(result.share("seconds"), 2)` under the key
`"seconds.share_removed"` since the epic was written.

A fraction is a float, so it fits through the boundary. What was missing was a
name — and a name each end spells for itself is a finding with no localization
and nothing saying why. `share_metric(metric)` now owns it, in `ablation.py`
beside the arithmetic, with `AblationResult.reported` returning the pair. The
fixture calls it instead of spelling it.

This is worth separating from Epic 9's finding, which is real and still stands:
`kinds` and a `Fit` are **not** floats, cannot cross at all, and leave three
attacks answering `UNTESTED`. The boundary is narrow, not closed, and the two
failures want different fixes.

`shares_from` refuses an experiment carrying no share, naming every one that is
missing rather than the first. Sabotage-verified.

### 3. The order is shares, then the model

`chain_of` reads the shares off the log *before* it asks anything. An
investigation whose confirmations recorded no share fails without spending a
call, because that is a fact about the log and discovering it afterwards would be
paying to learn something already knowable.

### 4. `assemble_with` exists so the join can be tested without a client

`chain_of` buys an explanation; `assemble_with` takes one. The composition check
now uses the second, with the reply parsed through the same `parse` a live one
would take — so the test supplies a *reply*, which is the only thing a test can
honestly supply, rather than supplying the conclusion.

## Consequences

`investigate` has a production path to an `EvidenceChain`, which is what S-12.7's
node needs. **S-12.7 is now unblocked**: both S-7.13 and S-8.11 are done.

Two things this deliberately does not do. It does not judge whether the mechanism
follows from the evidence — `08-audit.md` gives that to E9's finding audit, and
records that schema validation and adversarial review address different failure
modes. And it does not widen the loop boundary, which is still the right fix for
Epic 9's three `UNTESTED` attacks and is its own story.

**The correction is the part worth keeping.** A claim that something is
structurally impossible is exactly the claim worth checking against what the
system already does, because the code that disproves it is often a fixture nobody
reads. *Unreachable* and *unnamed* look identical from the call site and want
completely different work.

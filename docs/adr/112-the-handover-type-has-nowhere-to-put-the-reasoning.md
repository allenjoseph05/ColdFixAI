# 112 — The handover type has nowhere to put the reasoning

**Status:** accepted
**Story:** S-11.1 — isolated context
**Date:** 2026-08-18

## Context

`08-audit.md` §4: *the Adversary runs twice.* S-9.1 built the first invocation,
handing a diagnosis to something that did not produce it. This is the second,
handing a **patch** to something that did not write it — and the difference in
subject decides the design.

## Decisions

### 1. S-9.1 had to strip; this one has nowhere to put it

The experiment log's type carries `rationale` and `outcome`, so the finding audit
removes them while rendering. `Patch` carries `rationale` and `approach` — and
S-10.4 named the first one for exactly this story.

Rather than render-and-strip, the artifact handed over is a **`Candidate`, which
has no field for either.** A caller holding a `Patch` cannot pass its reasoning,
because there is nowhere for it to go. That is the construction S-10.1 used to
keep a diff out of a falsification test, pointed at what this epic must not see,
and `candidate_from` is the single place the reasoning stops.

### 2. `approach` goes as well as `rationale`

§6.1 says *never sees the Surgeon's reasoning, rationale, or prior attempts*, and
`approach` is not obviously reasoning — it is a label. But it is the Surgeon's
one-line account of what it believes it did, which is what `outcome` was to
S-9.1's log, and S-10.5 proved it is renameable without anything else changing.

The diff is the fact; the label is the gloss. `08-audit.md`'s number is the
argument: **72% of reward-hacking episodes carry explicit justifying reasoning**,
and a sentence reading *prefetch the authors once* over a diff that adds a cache
is that reasoning in miniature.

### 3. The opposite failure is not committed

S-9.1 recorded that *isolation by sending nothing* satisfies every rule while
making the audit useless. The Adversary gets the whole diff, the original and
patched source of every file it touches, the evidence chain, and the
falsification test it must decide the patch against.

A file whose source could not be supplied is **named** rather than passed over —
S-3.9's best-effort reading, one epic on. An auditor that cannot see a changed
file should be told, not left to assume it saw everything.

### 4. Three audits, one isolation

`patch_audit_session` is S-9.1's `audit_session` with this story's system text —
the parameter S-10.3 added when the second adversarial audit arrived. The
message list is S-9.1's `audit_messages`, reused rather than copied:
`CLAUDE.md`'s non-negotiable is one function, and two implementations of it would
be two things to keep right.

`invoke` has no `patch` parameter, no `attempts` parameter and no `messages`
parameter. AC 4 needs no code — the vendor is `Router` configuration, and
ADR 062 records the second-vendor blocker as indefinite.

### 5. `Phase.PATCH_AUDIT`'s cap had no caller

The **fourth** of these, after `FINDING_AUDIT` (S-9.8), `TEST_AUDIT` (S-10.3) and
`REPAIR` (S-10.5). Every phase whose cap is counted in something other than steps
has needed the story that owns the unit to count it. The round's *conclusion* is
the caller's, because S-11.2 to S-11.5 have not defined their verdicts and
inventing a vocabulary here would fix a shape those stories own.

## Consequences

**A fixture that lied about its own hunk.** The test diff declared
`@@ -41,2 +41,2 @@` with one removed and one added line. The counts must match
the body, so the parser correctly consumed the *next file's header* as hunk
content — exactly what `touched_paths`' docstring says it does — and the second
file vanished from `files`. The parser was right and the fixture was wrong.

**Sabotage: 25 properties, all caught, zero skipped, after two survived.** Both
were fixtures that could not discriminate:

- the `original`/`patched` source blocks echoed the diff's own added and removed
  lines, so a sabotage that **stopped sending the diff entirely** changed no
  assertion. The blocks now carry markers only they have. **Fourteenth instance**
  in this project;
- the empty-conclusion test recorded `"   "` and then `""` — different strings,
  so they could not stall under either implementation, and the sabotage survived
  by accident rather than by the code being right. Two *identical* blanks
  discriminate.

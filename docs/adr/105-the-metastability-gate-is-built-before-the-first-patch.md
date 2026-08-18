# 105 — The metastability gate is built before the first patch

**Status:** accepted
**Story:** Epic 10 ordering, decided after S-10.1
**Date:** 2026-08-17

## Context

`00-BRIEF.md` §4 is unusually direct about this:

> **Our tool produces exactly these optimizations.** A caching fix reduces
> steady-state queries, passes every check, and can move a system from stable to
> vulnerable — where the next traffic spike does not recover.
>
> This gate is a safety requirement, not a quality one. **Implement it before the
> Surgeon can emit its first patch.**

`10-BACKLOG.md` places the gate at **S-10.6**, with `Depends: S-10.4` — and
S-10.4 is patch generation. Read literally, the backlog builds the gate one story
*after* the thing it exists to gate.

## The dependency is not real

S-10.6 pattern-matches **a diff**. Nothing about that requires the Surgeon to
have produced one:

- `sandbox/patching.py` (S-2.4) already parses diffs — `touched_paths`, the
  protected-path policy, and the applier that rejects them;
- `primitives/faults.py` (S-3.16) already provides the retry-amplification check
  S-10.6's fifth criterion asks for *where available*;
- the classifier itself is a pure function over diff text, which is the same
  shape as five of Epic 9's attacks.

The `Depends: S-10.4` line reads as *this is about patches, and patches come from
S-10.4*. That is a statement about subject matter, not about inputs.

## Decision

**Epic 10 is reordered: S-10.1 → S-10.2 → S-10.3 → S-10.6 → S-10.4 → S-10.5.**

S-10.6's dependency line becomes `S-2.4, S-3.16`.

Nothing before S-10.4 emits a patch — S-10.1 produces a test, S-10.2 runs it
against unpatched code, S-10.3 audits the test — so the brief's requirement is
satisfied by putting the gate immediately before the story that first generates a
diff.

## Why this way rather than the alternatives

**Not "build it after S-10.4 but before anything ships."** That reading survives
§4's sentence only by treating *emit* as *ship*, and the brief separates the two
deliberately: auto-approval blocking is the gate's **third** requirement, listed
after flagging and after the spike-and-recovery test. A patch that exists and is
unlabelled is already the failure — Epic 9's whole premise is that the expensive
mistakes are the ones that look fine downstream.

**Not "relax the gate to a warning."** §4 calls it a safety requirement in the
same breath as saying our own output is the class of change it catches.
`CLAUDE.md`'s hard-enforcement table exists for exactly this: *if you find
yourself relying on this file to prevent something dangerous, that rule needs
code instead.*

## What this does not decide

**S-10.6's second criterion — *pass a spike-and-recovery test before it can be
proposed* — is not in its acceptance criteria.** §4 lists three requirements and
the backlog's S-10.6 covers the first and third plus an attached
retry-amplification result. Primitive 12 is S-3.12 (load/USL), which exists.
Whether the spike-and-recovery run is a blocking precondition or an attached
result is S-10.6's to settle, and it is flagged here so it is settled rather than
absorbed: the difference is whether a slack-reducing patch can be *proposed* at
all without one.

# 109 — Scope is the chain's, and the proof is a parameter

**Status:** accepted
**Story:** S-10.4 — patch generation
**Date:** 2026-08-18

## Context

The first story in this system that emits a diff. `03-agents.md` §5.3 states the
Surgeon's mandatory ordering — test, run it unpatched, require failure, only then
patch — as a numbered list for an agent to follow, and §5.4 gives `Patch` its
schema.

## Decisions

### 1. The ordering is a signature, not a list

`generate` requires a `Falsified`, and it has **no default**. Only S-10.2's gate
constructs one, and that type refuses to represent a passing run. A caller who
skipped the gate has nothing to pass.

A numbered list is a thing an agent can read quickly. A required parameter is
not. This is why S-10.2 produced an artifact rather than returning a boolean —
the payoff arrives one story later.

### 2. Scope is the evidence chain's, and the model is not asked

The chain names a `site` and a list of `context` files, each carrying the reason
S-8.6 requires. Those paths are the scope; a diff touching anything else is
refused before it is applied.

The chain is shown to the Surgeon as **evidence** — where the cost was measured,
why each file is implicated — which is a different thing from a permission list.
S-2.4's finding is that a rule a model is *told* is a rule something can be
argued out of, so the check is server-side and runs whether or not the model was
told.

An agent that decided a fourth file also needs changing has decided something no
experiment in this investigation supports. The remedy is a new investigation, not
a wider patch.

### 3. `files` is derived, not reported

`03-agents.md` §5.4 gives `Patch` a `files: list[str]`. That is the agent
restating what the diff already says, and a reported list disagreeing with its
own diff would have the scope check passing against a **claim** rather than
against the change.

Third correction of this shape in Epic 10, after §5.4's `failed_on_unpatched` in
S-10.1 and S-8.5's `invalidated_if` before that. `touched_paths` already parses a
diff correctly, including the case where a removed line's content begins `-- a/x`
and renders as `--- a/x`.

### 4. Candidate mode only, and the pair is the point

`apply` takes a `CandidateSession` — the one class with `apply_patch` and
`diff`. S-10.2's gate takes the `DiagnosticSession`, which has neither. **A patch
must not be able to exist where the must-fail check runs, and has to exist where
the patch is applied**: the same rule read from both ends, and neither half is a
guard that could be bypassed.

### 5. Protected paths stay S-2.4's

The filter lives inside `apply_patch` because that is *the only route by which a
diff becomes a file*. A second copy here would be a check something could be
routed around.

What this module adds is a **narrower** rule on top: in-scope is a smaller set
than not-protected. A file can be both in scope and protected — a chain whose
context listed a test file would put it in scope — and the applier still refuses
it. The scope check runs first because it is cheaper and because a rejected diff
that had already been written would need reverting.

## Consequences

**A duplicate block was deleted rather than tested.** `render_brief` carried a
*FILES THE EVIDENCE IMPLICATES* section listing the site and each context file
with its reason — and `EvidenceChain.render` already emits `SITE` and
`IMPLICATED FILES` with the same reasons. Two statements of one fact in a prompt
cost tokens on every call and drift when one is edited. The duplication also made
the second copy **untestable**: a sabotage deleting its reasons changed no
assertion, because the chain's own rendering still carried them. Same shape as
S-8.5's `invalidated_if` and this story's own `files`, here in a prompt.

**The substring-over-source trap, a fourth time.** A test asserted
`"ProtectedPathError" not in inspect.getsource(patch_module)` — and `apply`'s
`Raises:` section names it, correctly, because that is what a caller has to
catch. Recorded at S-7.11, again at S-9.3, again at S-10.1. The check now asserts
what the module imports and what its functions take. Four occurrences is no
longer a slip; the rule is that **an isolation test never reads source text.**

**`attempts_differ` is deliberately the crudest possible check** — byte equality
on the diff. S-10.5 owns the real one (same lines, similar edit shape), and
inventing a similarity threshold here would be the guess S-9.4 refuses to make.
What it does establish is F12's point: two patches with entirely different
`approach` strings and an identical diff are not different attempts, and a check
reading the label would call them so.

**Sabotage: 23 properties, all caught, zero skipped, after one survived.**

# 121 — The reader belongs to the candidate session

**Status:** accepted
**Story:** Epic 11 composition follow-up — `read_file`
**Date:** 2026-08-20

## Context

`03-agents.md` §6.2 lists `read_file(path)` among the Adversary's tools. Nothing
implemented it. S-11.1's `Candidate` needs the original and patched source of every
file a patch touches, and S-11.5's `ScopeAudit` needs the whole repository's source
to find callers — and both had to be handed theirs by a caller that had no way to
obtain them.

Epic 11's composition check named the gap and left it open, because closing it is a
change to the sandbox rather than to the audit. It blocks S-12.1's *graph compiles
and runs end to end*: an orchestrator was the only thing that could fill those
arguments, and nothing gave it the means either.

## Decisions

### 1. The reader goes on `CandidateSession` and never on `Session`

The obvious place is the base class, where both session types inherit it. That is
the wrong place, and the reason is the same one S-2.3 exists for.

A `DiagnosticSession` may run **any command**, which means it may **write any
file**. Give it a way to read one back and a diagnostic run can emit a diff to disk
and hand it out — ADR 004's *an ablation run cannot produce a patch* defeated
through a reader rather than through a writer.

S-2.3's construction is that the operation is **absent, not guarded**, and the
existing surface test asserts `DiagnosticSession`'s public names by list precisely
so that adding one has to be argued for. It was argued for and refused. The test
now also names `sources` and `original_of` among what must not be there.

### 2. Both revisions come from the candidate worktree

`sources()` reads the **working tree**, because that is where the patch is —
reading the commit would hand an audit the code the patch replaced.

`original_of(paths)` reads `git show HEAD:path` in the **same** worktree, because
no applied patch has touched the commit. The alternative — a second session at the
base revision — would be a diagnostic session, and giving that one a reader is
exactly what decision 1 refuses.

### 3. Read on the host, like `diff`

The bind mount carries the working files and not the `.git` metadata, so git cannot
run inside the container at all. And a file read *through* the container would be a
file the container could have arranged; the audit's inputs should not be
influenceable by the thing under audit.

### 4. Best-effort, with the limits stated

A file that is not valid UTF-8, one above `MAXIMUM_SOURCE_BYTES`, or anything under
`.git` is skipped rather than raising — S-3.9's rule, because a file that cannot be
read weakens an audit while an exception loses the whole of it. A path that does not
exist at `HEAD` is absent from `original_of` rather than an error: a patch that adds
a file has no original for it, and that is a fact about the patch.

The byte bound is on the reader rather than on the caller because the caller is an
agent's context window, and one vendored megabyte fills it.

### 5. `Subject.of` assembles a subject from a diff and two sessions

The composition no longer takes source as a parameter it cannot get. The fields stay
constructible by hand so a test can supply source without a git checkout.

## Consequences

**This is the second time a capability listed in `03-agents.md` §6.2 turned out not
to exist**, and both were found by composition rather than by reading the document.
The tools table is a design sketch, not an inventory — worth treating as one when
planning Epic 12, whose nodes are specified the same way.

**The interesting part of the story was where the method could not go.** Writing
`read_file` is twenty lines; establishing that the base class is the wrong home for
it, and that the original revision must be read from the candidate's own `HEAD`
rather than from a second session, is the whole decision. The existing surface test
is what forced the question to be asked at all — a test that asserts a public
surface by name pays for itself the first time somebody tries to widen it.

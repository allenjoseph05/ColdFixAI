# 022 — A diagnostic session has no method that returns a diff

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.3 is where ADR 004's requirement — *an ablation run cannot produce a
patch* — stops being a description and becomes a fact about the program.
`CLAUDE.md` lists it in the hard-enforcement table against "worktree
separation (S-2.3)", and its story note is explicit: *this is enforcement, not
convention. A test must actively attempt the violation and assert it is
impossible.*

`02-architecture.md` §6 gives the specification as a table, and one row decides
the design: **Output — measurements only** for diagnostic, against **diff +
measurements** for candidate. That is a statement about what the two modes *can
do*, not about what they are permitted to do.

The acceptance criterion "mode is a required argument on every execution call,
with no default" admits two readings, and they differ in strength.

## Decision

**The enforcement is an absent method, not a rejected call.**
`DiagnosticSession` exposes `run`, `close`, `mode` and `worktree`. There is no
`diff`, no `patch`, no `changes` — nothing that returns anything about the state
of the files. `run` returns an `ExecutionResult`, which is measurements. A
caller holding a diagnostic session has no argument to pass and no flag to set
that produces a change, because the operation does not exist.

A test asserts the public surface **by name**, as an exact set. It is
deliberately brittle: adding any accessor to `DiagnosticSession` fails it, so
the widening has to be argued for rather than merged.

**Mode is supplied once, when the session is opened, and selects a type.**
`Workbench.open(revision, *, mode)` has no default and returns
`DiagnosticSession` or `CandidateSession`. This is slightly stronger than the
criterion's literal wording, which suggests a mode argument on each execution
call. A per-call mode is a mode that two calls on one session can disagree
about, and it makes the separation a runtime comparison rather than a difference
between two types. The user was asked and chose this reading.

**Three independent things must fail before a diagnostic change can ship**, and
they fail for unrelated reasons:

1. *There is no method.* Above.
2. *There is no repository inside the container.* A linked worktree's `.git` is
   a file naming a path inside the main repository's `.git/worktrees/`. S-2.1
   bind-mounts exactly one directory — the worktree — so that path does not
   exist in the container, and git run there has nothing to read.
3. *There is no worktree afterwards.* Closing a diagnostic session destroys it,
   verified against the filesystem by S-2.2. Text describing changes to files
   that no longer exist cannot be applied.

**Point 2 was not designed.** It falls out of S-2.1 mounting a single directory,
and was found by checking rather than assumed. It is asserted here so that
mounting a second directory later fails a test in this file — the property is
now load-bearing whether or not the person changing the mount knows it.

**Both session types destroy their worktree on close.** The architecture table
says a candidate container is "persistent within the attempt"; that is read as
*within the session*. The diff is returned as text, so nothing is lost by
destroying the directory, and a candidate worktree that outlived its session
would be a checkout with no owner and no `close()` able to reach it.

**A failed `open` destroys the worktree it had already created.** Between
`create_worktree` and constructing the `Sandbox` there is no session, so an
exception there would strand exactly the checkout S-2.2 exists to prevent,
arriving through the door S-2.3 opened.

## Consequences

**Makes easy.** Reasoning about whether an ablation can ship: the answer is a
property of a class definition, checkable by reading it. The Diagnostician can
be given a diagnostic session and no amount of prompt injection, tool
misuse or model error produces a patch from it, because the capability is not
in the object it holds.

**Makes hard.** Any future need for a diagnostic run to report *something*
about file state — say, "which files did the ablation touch" for an evidence
chain. That is deliberate. It should be added, if ever, as a named,
measurement-shaped artifact with its own review, not as a general accessor.

**Rules out.** A single `Session` class with a mode field and conditional
behaviour, which is the design this replaces and the one where a missing branch
in an `if` is a safety failure.

**Left open.** Container persistence within a candidate attempt, as the
architecture table describes it, is not implemented: S-2.1 destroys every
container after every run. That is safe and slow — a candidate attempt
re-enters a fresh container per command, so anything installed is reinstalled.
Reusing a container is a caching change to a hot path, which `CLAUDE.md`
requires be noted rather than slipped in, and it should be driven by a measured
cost rather than by this table row.

Nothing here checks that a candidate change *preserves correctness*. The
falsification test, the protected-path filter (S-2.4) and the Adversary are what
check that. `CandidateSession` provides the one sanctioned route by which a
change becomes text, so that the route exists in exactly one place.

## Provenance

`docs/10-BACKLOG.md` S-2.3 and its note; `02-architecture.md` §6 mode table and
its "Enforcement is in the harness ... never a prompt instruction"; ADR 004;
`03-agents.md` §7, which lists mode separation among layers that work because
**none of them asks the model to behave**.

Sabotage-verified, one route at a time. Adding a `diff` to `DiagnosticSession`
fails two tests. Making `close()` stop destroying the worktree fails three.
Giving `mode` a default fails one. Pointing both modes at one worktree fails
two — and is additionally impossible, because S-2.2's `create_worktree` refuses
a path that already exists, which is defence in depth that was not planned for
and is recorded so it is not removed by accident.

The route-2 test asserts the metadata's absence directly rather than running
`git diff` in the container and observing a failure. `python:3.12-slim` ships no
git at all, so the naive test would pass for the wrong reason and would keep
passing if the metadata were later mounted.

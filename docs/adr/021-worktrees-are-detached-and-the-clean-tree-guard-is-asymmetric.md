# 021 — Worktrees are detached, and the clean-tree guard is asymmetric

**Status:** accepted
**Date:** 2026-08-06

## Context

S-2.2 asks for programmatic create, list and destroy; creation at an arbitrary
revision; destruction that discards uncommitted changes; and a refusal to
operate on a dirty main working tree. It carries no `Notes:` line, so the
design pressure comes from ADR 004 and from four places in `02-architecture.md`
and `03-agents.md` that all say the same thing: a diagnostic worktree holds
deliberately broken code and must be destroyed on exit.

The last acceptance criterion is the one that needed a decision. "Refuses to
operate on a dirty main working tree" does not say which operations count, and
the two readings differ in what they protect.

## Decision

**Every worktree is detached at a resolved commit SHA.** The revision the caller
gives is anything `git rev-parse` accepts, resolved to a commit before git is
invoked. Three reasons: a branch checked out in one worktree cannot be checked
out in another, so branch-based creation would fail depending on what else is
running; a branch moves, so an investigation that measured "the revision on
`main`" measured different commits in different experiments; and a detached
worktree has no branch for anything this system does to advance.

`Worktree.revision` is the SHA, not the string asked for, because that is the
fact the experiment log has to carry — `HEAD` names different commits on
different days.

**The clean-tree guard applies to `create` and deliberately not to `destroy` or
`list`.** The danger it addresses is measuring a repository whose committed
state is not the state the user is looking at: uncommitted edits live in no
commit, so a worktree at HEAD does not contain them, and every finding would
cite code that differs from the user's copy. That argument is about *making* a
worktree.

Applied to removal it inverts. A main tree that goes dirty part-way through an
investigation would strand a worktree full of ablated source — the exact
outcome ADR 004 exists to prevent — and it would do so in the name of safety.
Listing is refused for no reason at all: a caller trying to discover what needs
cleaning up must not be blocked because something needs cleaning up.

**Untracked files count as dirty; ignored files do not.** `git status
--porcelain` does not list ignored files, so the usual build output and local
database leave a repository clean. A file that is untracked *and* not ignored
is code no commit contains. The two are carried separately on the error because
`stash` fixes one and does nothing about the other, and a message that
conflated them would send the reader to a command that cannot help.

**A worktree inside the main working tree is refused**, though git permits it.
It would appear in the main tree as untracked content, making that tree dirty,
making every subsequent `create` refuse — the module disabling itself by having
run once.

**Removal is verified against the filesystem, not against git's exit code.**
`git worktree remove --force` can report success and leave the directory behind
when something still holds a file in it. That is routine on Windows and becomes
possible on every platform once S-2.3 bind-mounts a worktree into a container.

**A stale registration is refused rather than forced over.** When the directory
was deleted by hand and git still has it registered, git offers `add --force`.
Taking that automatically would write over whatever the registration was
protecting without the caller learning the repository was in an unexpected
state.

## Consequences

**Makes easy.** An experiment log can name the exact commit every measurement
was taken at. Cleanup always works, including from the state where a user
started editing mid-run, which is the state cleanup is most needed in.

**Makes hard.** Measuring a repository with uncommitted work in it. That is
intended and the error says so, but it is a real friction: a user wanting to
measure work in progress must commit it first, and there is deliberately no
override flag.

**Rules out.** Creating a worktree that tracks a branch. Anything wanting to
follow a moving ref has to resolve it per experiment and own the fact that
successive experiments measured different code.

**Left open.** Nothing here removes a worktree whose *containing process* is
gone — a crashed run leaves both the directory and git's registration, and
`git worktree prune` is not called automatically because pruning is a
repository-wide operation and this module is not the only possible user of the
repository. `Worktree.prunable` is reported so a caller can see the state; who
acts on it belongs to whoever owns run lifecycle.

## Provenance

`docs/10-BACKLOG.md` S-2.2; ADR 004 for the requirement that a diagnostic
worktree be destroyed on exit.

The asymmetric guard is not in the AC, which says only "refuses to operate on a
dirty main working tree". Both readings satisfy that sentence and they differ in
what they protect, so the choice is recorded here — the same situation as ADR
018's randomization. `test_destroying_is_deliberately_not_refused_by_a_dirty_tree`
asserts the direction that is *not* obvious, so that tightening it into a
safety regression fails a test.

**Detachment has two independent sufficient guarantees, and this was found by
sabotage rather than by design.** Removing `--detach` alone changes nothing,
because resolution means git never sees a branch name; removing the resolution
alone changes nothing, because `--detach` still detaches. Only removing both
attaches the worktree to a branch, at which point both detachment tests fail.
The redundancy is worth keeping and worth recording: it also means neither test
can be read as evidence that either mechanism individually works, and a future
change that removes one will pass its tests while halving the guarantee. The
first attempt at these tests could not have caught it at all — every case used
`main`, which git refuses to check out twice, so its refusal masked the flag. A
branch checked out nowhere had to be added to the fixture before the question
could even be asked.

# 160 — The denylist is a backstop and the container is the guarantee

**Status:** accepted
**Date:** 2026-08-28

## Context

`Hands` is the only place in this system where argv comes from a model. S-7.14
left it as a supplied type for exactly that reason: *"a loop that could run a
command itself would be the one place a denylist, a container boundary and
`03-agents.md` §2.5's workspace confinement have to be re-implemented rather than
inherited."*

ADR 159 gave it a surface to run on. This ADR is about what "inherited" turns out
to cover, because it does not cover all three.

## Two of §2.5's protections are inherited and one is not

- **No external network** is the container's. `Workbench.network` defaults to
  `None`, which is loopback and nothing else (ADR 029). Nothing in `Hands`
  re-checks it.
- **Workspace confinement** is the surface's. `Surface.run` takes no `cwd` and the
  session mounts one directory, so there is no argument through which a command
  could name somewhere else to run.
- **No destructive shell is not inherited**, and ADR 159 is why. What makes a
  fresh container per command survivable is that the worktree is a bind mount —
  so **the one part of the filesystem a command can permanently damage is exactly
  the part that persists**. `rm -rf` in the workspace outlives the container that
  ran it.

So the denylist lives in `hands.py` and only there. The stage predicates run
`manage.py check` and must never be filtered by it; this is the only caller whose
argv was written by a model.

## Decision

**The denylist is a backstop, and this ADR refuses to describe it as the
guarantee.**

`08-audit.md` F10 — *"denylists fail by omission, and an optimizer under selection
pressure is exactly the process that finds omissions"* — is written about guard
counters and applies word for word here. What actually makes this safe is
structural: the container has no egress, it dies after every command, and the
workspace is a throwaway worktree S-2.2 destroys. The list catches the cheap
mistakes and nothing about it should be read as a boundary.

Three shape decisions follow.

**Patterns match anywhere in the rendered command, never anchored to the start.**
`sh -c "rm -rf /"` has `sh` at argv[0] and `rm` in the middle. A pattern beginning
`^` is one that every denial is a shell invocation away from — and a model
proposing `sh -c` is doing an ordinary thing with no intent at all.

**They err toward refusing**, which is S-2.9's bias for S-2.9's reason: a wrongly
refused command costs the agent one turn out of sixty, and a wrongly permitted one
damages the checkout every later measurement is taken against.

**A refusal is a failed `Effect`, not an exception**, and it does not share an exit
code with a failure. ADR 139 settled how this loop learns — *the loop uses the same
check where it is available, by feeding a failed command into the next question* —
so a refusal that raised past the loop would end the run, and a repository that
would ground perfectly well would read as one that will not. `REFUSED_EXIT_CODE`
is 126 rather than 1 because the next question is written from that number and the
two cases want different follow-ups: a failure is something to diagnose, a refusal
is something to replace. An agent that cannot tell them apart rephrases the command
it was denied. The message carries the reason *and quotes the move's own stated
purpose back*, because the useful correction is *this is denied, and here is what
you said you were trying to do*.

## The defect this story found in itself

The first draft of the table anchored four of the six patterns with `^`, while the
docstring above it claimed that matching the whole rendered string is what defeats
`sh -c`. Both were written in the same sitting. The test asserting the claim —
`refuse(("sh", "-c", "rm -rf /"))` — failed on the first run.

**The claim and the code disagreed, and the claim was the true one.** This is the
same shape as S-8.2's `strict=True` guarding nothing while its docstring said it
was, and S-2.6's `--volumes` comment giving a false reason for correct code: a
sentence explaining why something is safe is not evidence that it is, and it is the
sentence nobody re-checks. The fix was to make the code match the docstring, and
the sabotage pass now restores the anchored version as a deliberate mutant.

## Consequences

**`hands_on(surface)` is the producer for `Resources.hands`.** It does not check
that it was given a `SessionSurface`, and that is stated rather than enforced:
`HostSurface` is a legitimate implementation the grounding predicates use
throughout, so a type refusing it here would have to be a third kind and the check
would be about which constructor was called rather than about what the command can
reach. What makes the choice safe is the campaign assembling one surface for the
whole run — which ADR 159 established has to be true anyway, or the loop cannot
make progress.

**`refuse()` is separate from `hands_on` so a second caller reaches this list
rather than writing its own.** A future Surgeon proposing a build step is the
obvious one.

**Nothing assembles a `Resources` yet.** `Grounder`, `Binder`, `Executor`,
`Measurer` and `Probe` remain, and S-17.1 is still not a run.

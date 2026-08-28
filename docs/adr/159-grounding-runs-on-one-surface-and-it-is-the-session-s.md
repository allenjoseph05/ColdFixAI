# 159 — Grounding runs on one surface, and it is the session's

**Status:** accepted
**Date:** 2026-08-28

## Context

S-17.1 needs six subject-facing implementations. The smallest is `Hands`, the
thing that runs a command the Explorer proposed — and it cannot be written
without deciding **where** that command executes. The decision belongs to
grounding as a whole rather than to `Hands`, because whatever answer `Hands` gets
must be the answer the predicates judging its work already had.

Both obvious answers are wrong, and this was settled by reading rather than by a
spike.

### A container alone cannot make progress

`Sandbox.run` runs each command in a fresh container and *"the container is
destroyed before this returns, on every path"* (S-2.1). The predicates that judge
whether a stage now holds run

```python
execute([*grounding.python, "manage.py", "check"], timeout=..., cwd=Path(grounding.root))
```

— a **host** subprocess against the **host** checkout. So a `pip install`
performed in a container lands in a site-packages that is discarded, the host
predicate reports the stage still failing, and the loop reproposes until its
sixty-step cap. That is S-7.14's *"sends the end-to-end run round `auth` eight
times"*, reached through the executor instead of through `blocking()`.

### The host has none of the protections the design assigns to this step

`Hands`'s own docstring says a loop holding its own `execute` would be *"the one
place a denylist, a container boundary and `03-agents.md` §2.5's workspace
confinement have to be re-implemented rather than inherited"*. All three are
inherited **from the container**, and §2.5 is a table in a document: a search for
a command denylist in `src/` finds only `envelope.py` and `trades.py`, which are
about guard counters and mean something else entirely. Host execution runs
model-proposed argv unconfined.

## Decision

**Grounding runs on one surface, and it is the session's.** The checkout the
predicates measure *is* the container's workspace, so a command and the predicate
that judges it see the same filesystem. Decided with Allen.

`Surface` is a `Protocol` with a `root` and a `run`. Two implementations:
`HostSurface`, which is exactly the call every site made before, and
`SessionSurface`, which runs against a `Session`.

Three properties fall out of the type rather than out of a convention:

**No `cwd`.** Every command runs at `root`. That is §2.5's workspace confinement
made structural for the subject-facing half — there is no argument through which
a caller could reach outside the checkout. All eight sites passed `cwd=root`
already, so nothing lost an ability it was using.

**`env` is overrides, never the whole set.** `execute` *replaces* the environment
and `Sandbox.run` *adds to* the image's; both docstrings say so. Spelled at a call
site that only ever ran on the host, `{**os.environ, "DJANGO_SETTINGS_MODULE":
...}` reads as *add one variable* and silently becomes *push the harness's entire
environment into the subject's container* the moment the surface changes — and it
would clobber the image's `PATH` and `LANG`, which are what make its interpreter
runnable. A surface takes only the override and each implementation applies it the
way its own runner requires.

**What persists is the workspace.** A fresh container per command is survivable
because the worktree is a bind mount: a change written into the checkout survives
and one written into the image's filesystem does not. That is a real constraint
grounding inherits rather than a detail — an environment that must outlive a
command belongs in the workspace, which is where a project's virtualenv belongs
anyway. Verified under `docker`: a file written by a container command is on the
host afterwards.

## The fifteen call sites are not one kind

This is the part that would have been got wrong by a mechanical sweep.

**Eight are subject-facing** — `[*python, ...]` with `cwd=root` and the subject's
`DJANGO_SETTINGS_MODULE` — in `auth`, `entrypoints`, `fixtures`, `stages` (×2),
`synthesis` and `work` (×2). Four of them are the *same private helper*,
`_run_in_subject`, copied into four modules.

**Seven are harness control-plane and must stay on the host.** The four `docker`
invocations in `standup` — containerising those is docker-in-docker — and
`git -C root log` and `uv pip compile` in `anchor`, which are the harness's own
tools reasoning *about* the repository rather than the subject's interpreter
running *in* it.

A `Surface` applied to all fifteen breaks the sandbox it is trying to use, and
breaks it only on a machine where Docker is running.

## Consequences

**Adoption is provably behaviour-preserving.** Every subject-facing entry point
takes `surface: Surface | None = None` and resolves `None` to a `HostSurface` at
`root` — the call it replaced. The whole existing suite passes unchanged, which is
what makes a green run evidence rather than coincidence. `HostSurface` is not a
fallback and is not deprecated: the planted fixtures are packages this harness
imports, and a suite needing a Docker daemon to exercise `manage.py check` is a
suite nobody runs. It is, however, **unsafe for a command an agent proposed**, and
S-17.8 must not bind `Hands` to it.

**The partition is asserted, not the half we care about.** S-17.6's lesson: the
test sums every direct `execute` call in `explorer/` by parsing each module, and
compares the whole non-zero mapping against the control-plane set. Listing only
the modules that moved would pass while a sixteenth call site appeared in a module
named by neither. Sabotage confirmed both directions — a subject-facing site
reverted to `execute` fails two tests, and a `docker` command moved onto a surface
fails the same two.

**`Seeder` is a limit, stated rather than closed.** `verify_work` threads its
surface through `_reset`, `synthesize` and `drive`, but a caller-supplied `Seeder`
is a callable taking `root` and `python`, and binding a surface into it is the
caller's job. S-17.8's `Hands` and the `Binder` that follows both have to supply
one that agrees.

**S-17.8 is now writable and is small.** `Hands` is an adapter over a surface
rather than a second executor: if it grows a policy decision, that policy belongs
to the surface.

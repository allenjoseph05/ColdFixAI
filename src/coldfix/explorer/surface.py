"""Where a grounding command runs. **One decision, made once.**

S-17.7. Every subject-facing step of grounding — asking the framework whether it
imports, running `manage.py check`, seeding, driving a route — is a command, and
until this module existed each one reached for `bench.execute` directly. That is
a host subprocess against the host checkout, and it is the wrong surface for two
reasons that point in opposite directions.

**A container alone cannot make progress.** `Sandbox.run` runs each command in a
fresh container and destroys it before returning, on every path. So an install
performed by a command the Explorer proposed lands in a site-packages that is
discarded, while the predicate judging that stage runs on the host and reports it
still failing. The loop reproposes until its cap — S-7.14's *"round `auth` eight
times"*, reached through the executor rather than through `blocking()`.

**The host has none of the protections the design assigns to this step.**
`03-agents.md` §2.5 puts a denylist, blocked egress and workspace confinement on
the container, and none of the three exists in code anywhere else.

**So the command and the predicate that judges it must share a surface, and that
surface is the session's.** What makes a fresh container per command survivable is
that the workspace is a bind mount: a change written *into the checkout* persists,
and one written into the image's own filesystem does not. That is a real constraint
on grounding rather than a detail — an environment that must survive belongs in
the workspace, which is where a project's virtualenv belongs anyway.

**Not every command in `explorer/` belongs here, and the partition is the point.**
Seven call sites are harness control-plane: the four `docker` invocations in
`standup` (containerising those is docker-in-docker), and `git -C root log` and
`uv pip compile` in `anchor`, which are the harness's tools reasoning *about* the
repository rather than the subject's interpreter running *in* it. They stay on the
host deliberately, and `tests/explorer/test_surface.py` asserts both halves — the
dangerous direction is a `docker` command drifting onto the containerised side,
which fails only on a machine where Docker is running.

**`env` means the same thing here and does not below.** `execute` *replaces* the
environment and `Sandbox.run` *adds to* the image's, an asymmetry both docstrings
call out. Spelled at a call site that only ever ran on the host, `{**os.environ,
"DJANGO_SETTINGS_MODULE": ...}` reads as "add one variable" and becomes "push the
harness's whole environment into the subject's container" the moment the surface
changes. So a surface takes **only the overrides**, and each implementation
applies them the way its own runner requires.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from coldfix.bench.execute import DEFAULT_MAX_OUTPUT_CHARS, ExecutionResult, execute

if TYPE_CHECKING:
    from coldfix.sandbox.modes import Session


class Surface(Protocol):
    """Somewhere a subject-facing command can be run and its effect observed.

    **No `cwd`.** Every command runs at `root`, which is what makes the confinement
    §2.5 asks for structural rather than checked: there is no argument through
    which a caller could reach outside the workspace. The eight call sites this
    replaced all passed `cwd=root` already, so nothing lost an ability it used.
    """

    @property
    def root(self) -> Path:
        """Where commands run, and the path the harness reads files from.

        One value for both because a session's workspace is a bind mount, so the
        checkout the container writes to is the checkout on disk. A surface where
        those differed could not support `settings_module(root)` beside
        `run([*python, "manage.py", "check"])`, and every grounding step does both.
        """
        ...

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        """Run `command` at `root`. `env` is **overrides**, never the whole set."""
        ...


@dataclass(frozen=True)
class HostSurface:
    """A subprocess on this machine, in the checkout. **What grounding did before.**

    Kept as an implementation rather than deleted, and it is not a fallback: the
    planted fixtures are Python packages this harness imports, and a test suite
    that needed a Docker daemon to exercise `manage.py check` would be a suite
    nobody runs. It is also what makes the adoption of `Surface` provably
    behaviour-preserving — every existing test passes against it because it is
    the call it replaced.

    **It is not safe for a command an agent proposed.** Nothing here denies
    `rm -rf /`, blocks egress or confines a write that names an absolute path, and
    S-17.8 must not bind `Hands` to this.
    """

    root: Path

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        return execute(
            command,
            timeout=timeout,
            cwd=self.root,
            env={**os.environ, **(env or {})},
            max_output_chars=max_output_chars,
        )


@dataclass(frozen=True)
class SessionSurface:
    """A fresh container per command, against the session's worktree.

    **The workspace is what persists, and that is the whole contract.** S-2.1
    destroys the container on every path, so an effect survives exactly when it
    was written into the checkout. A command that installs into the image's own
    site-packages has done nothing the next predicate can see, which is not a
    defect here — it is the reason grounding must put a project's environment
    inside its workspace.

    `root` is the worktree path on the host, which is the same directory the
    container has mounted. So a predicate that reads a file and a command that
    writes one agree, and `settings_module(surface.root)` keeps working unchanged.
    """

    session: Session

    @property
    def root(self) -> Path:
        return self.session.worktree.path

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> ExecutionResult:
        # `env` is passed through as-is: `Sandbox.run` adds to the image's
        # environment, and the image's own PATH and LANG are what make its
        # interpreter runnable. Merging `os.environ` in here would push the
        # harness's environment into the subject and is the exact mistake the
        # module docstring describes.
        return self.session.run(
            command, timeout=timeout, env=dict(env or {}), max_output_chars=max_output_chars
        )

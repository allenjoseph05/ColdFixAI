"""Running a command the Explorer proposed. **The only place argv comes from a model.**

S-17.8. `Hands` is the seam S-7.14 left open: the loop sequences and the harness
acts, so a loop that could run a command itself would be the one place a denylist,
a container boundary and `03-agents.md` §2.5's workspace confinement had to be
re-implemented rather than inherited. This module is what does the acting, and it
is an adapter over S-17.7's `Surface` rather than a second executor.

**Two of §2.5's three protections are inherited and one is not, and saying which
is the honest part.**

- *No external network* is the container's. `Workbench.network` defaults to
  `None`, which is loopback and nothing else (ADR 029). Nothing here re-checks it.
- *Workspace confinement* is the surface's. `Surface.run` takes no `cwd` and the
  session mounts one directory, so there is no argument through which a command
  could name somewhere else to run.
- *No destructive shell* is **not** inherited, and S-17.7 is why. What makes a
  fresh container per command survivable is that the worktree is a bind mount —
  so the one part of the filesystem a command can permanently damage is exactly
  the part that persists. `rm -rf` in the workspace outlives the container that
  ran it.

So the denylist lives here, and only here: this is the only caller whose argv was
written by a model. The predicates run `manage.py check` and must never be
filtered by it.

**A denylist is a weak guarantee and this project already argued so.**
`08-audit.md` F10 — *"denylists fail by omission, and an optimizer under selection
pressure is exactly the process that finds omissions"* — is about guard counters
and applies word for word here. The real protection is structural: the container
has no egress and dies after every command, and the workspace is a throwaway
worktree S-2.2 destroys. The list below is a backstop against the cheap mistakes,
not the reason this is safe, and it should not be described as one.

**A refusal is a failed `Effect`, not an exception.** ADR 139 settled how this
loop learns: *the loop uses the same check where it is available, by feeding a
failed command into the next question.* An agent told why its command was refused
proposes a different one; an agent that raised past the loop ends the run, and a
repository that would ground perfectly well reads as one that will not.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from coldfix.explorer.loop import Effect, Hands
from coldfix.explorer.proposal import Move
from coldfix.explorer.surface import Surface

REFUSED_EXIT_CODE = 126
"""What a refused move reports. **Never 0, and deliberately not 1.**

A command that was never run must not be indistinguishable from one that ran and
failed, because the next question is written from this number and the two want
different follow-ups: a failure is something to diagnose, a refusal is something
to replace. 126 is the shell's *found but not executable*, which is the nearest
existing meaning.
"""

DEFAULT_TIMEOUT_SECONDS = 300.0
"""Long enough for a dependency install, which is the slowest thing an agent
proposes here. Bounded because `Surface.run` requires it — S-1.1's rule that a
subprocess with no deadline can hang an investigation with no diagnostic."""


@dataclass(frozen=True)
class Denial:
    """One thing a proposed command may not do, and why it may not.

    The reason is not decoration: it is fed back to the model as the correction
    that produces the next proposal, so *that is denied* would cost a turn and
    teach nothing.
    """

    pattern: re.Pattern[str]
    because: str


DENIED: tuple[Denial, ...] = (
    Denial(
        re.compile(r"\brm\s+.*?(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])\b"),
        "a recursive force-remove can destroy the checkout, and the workspace is the one "
        "part of the filesystem that outlives the container",
    ),
    Denial(
        re.compile(r"\bgit\s+push\b"),
        "pushing writes to somewhere other than the subject under test; grounding establishes "
        "that a repository can be measured and never changes it",
    ),
    Denial(
        re.compile(r"\bdd\s+(if|of)="),
        "dd writes raw blocks, which is not something grounding a repository ever requires",
    ),
    Denial(
        re.compile(r"\b(pip|pip3|uv|poetry|pipenv|conda)\b.*\buninstall\b"),
        "uninstalling changes the environment the measurement was taken in; a stage that "
        "needs a different package should install it, not remove one",
    ),
    Denial(
        re.compile(r"\bsudo\b|(?:^|[\s\"'])su\s"),
        "the container already runs the command with the privileges it is meant to have, so "
        "asking for more is either a mistake or an attempt to leave the sandbox",
    ),
    Denial(
        re.compile(r"\bmkfs\b|\bshutdown\b|\breboot\b|\bhalt\b"),
        "this operates on the machine rather than on the repository",
    ),
)
"""§2.5's four, plus privilege escalation and machine-level operations.

**Matched anywhere in the rendered command, never anchored to the start.**
`sh -c "rm -rf /"` has `sh` at argv[0] and `rm` in the middle, and it is a
perfectly ordinary thing for a model to propose with no intent at all — so a
pattern beginning `^` is one every denial here is a shell invocation away from.
The first draft of this table anchored four of the six and the test for that exact
case is what caught it.

**They err toward refusing**, which is S-2.9's bias for the same reason: a wrongly
refused command costs the agent one turn out of sixty, and a wrongly permitted one
damages the checkout every later measurement is taken against.
"""


def refuse(command: Sequence[str]) -> str | None:
    """Why this command may not run, or `None`.

    Separate from `hands_on` so the policy can be tested without a surface, and
    so that a second caller — a future `Surgeon` proposing a build step — reaches
    the same list rather than writing its own.
    """
    rendered = " ".join(command).strip()
    for denial in DENIED:
        if denial.pattern.search(rendered):
            return denial.because
    return None


def hands_on(surface: Surface, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Hands:
    """Run proposed moves on `surface`. **The producer for `Resources.hands`.**

    `surface` must be a `SessionSurface`, and that is stated rather than checked:
    `HostSurface` is a legitimate implementation used throughout the grounding
    predicates, so a type that refused it here would have to be a third kind, and
    the check would be about which constructor was called rather than about what
    the command can reach. What makes the choice safe is the campaign assembling
    one surface for the whole run — the same object the predicates judge with,
    which S-17.7 established has to be true anyway or the loop cannot make
    progress.
    """

    def run(move: Move) -> Effect:
        because = refuse(move.command)
        if because is not None:
            # The move's own reasoning is quoted back deliberately. The agent is
            # about to be asked for another command, and the useful correction is
            # *this is denied, and here is what you said you were trying to do* —
            # which is what lets it propose a different route to the same stage
            # instead of a rephrasing of the same one.
            return Effect(
                exit_code=REFUSED_EXIT_CODE,
                output=(
                    f"refused: {because}. The command was {move.rendered()!r}, proposed in "
                    f"order to {move.why}. Propose a different way to reach that stage."
                ),
            )

        result = surface.run(move.command, timeout=timeout)
        # Both streams, because a command that fails usually says why on stderr
        # and a command that succeeds usually says what it did on stdout, and the
        # next question is written from whichever one is not empty.
        return Effect(
            exit_code=result.exit_code,
            output="\n".join(part for part in (result.stdout, result.stderr) if part.strip()),
        )

    return run

"""The only place in this system where argv comes from a model.

S-17.8. Two kinds of test here and they are about opposite things. The first kind
is that a move *works* — that what a command did is visible to the stage predicate
which decides whether the stage now holds, which is S-17.7's property arriving
where it was needed. The second is that some moves do not work on purpose, and
that the refusal is shaped so the loop can learn from it rather than die.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from coldfix.explorer.hands import (
    DEFAULT_TIMEOUT_SECONDS,
    DENIED,
    REFUSED_EXIT_CODE,
    hands_on,
    refuse,
)
from coldfix.explorer.loop import explore
from coldfix.explorer.proposal import Move
from coldfix.explorer.surface import HostSurface

TOUCH = "import pathlib; pathlib.Path('installed').touch()"


@pytest.fixture
def checkout(tmp_path: Path) -> HostSurface:
    """A surface over a throwaway directory.

    `HostSurface` and not a session, because what these tests are about is the
    adapter — that a move reaches the surface, that its effect is visible on the
    same surface, and that a denial never reaches it at all. Which surface the
    campaign supplies is S-17.7's decision and is tested there, under `docker`.
    """
    return HostSurface(tmp_path)


def _predicate(where: HostSurface) -> bool:
    """A stage predicate, in the shape the real ones take: ask the subject's
    interpreter a question at the surface's root and read the answer."""
    result = where.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path('installed').exists())"],
        timeout=60.0,
    )
    return result.stdout.strip() == "True"


# ================================ AC 2: the move and the predicate agree


def test_what_a_move_did_is_visible_to_the_predicate_that_judges_it(
    checkout: HostSurface,
) -> None:
    """**AC 2, and the whole reason S-17.7 came first.**

    The Explorer proposes a command to make a stage hold, the harness runs it, and
    the predicate decides whether it now does. If those two disagree about the
    filesystem the loop reproposes until its sixty-step cap, having done the work
    correctly every time.
    """
    hands = hands_on(checkout)
    assert not _predicate(checkout)

    effect = hands(Move(command=(sys.executable, "-c", TOUCH), why="install the dependencies"))

    assert effect.succeeded
    assert _predicate(checkout), "the predicate sees what the move did"


def test_a_failing_move_reports_its_output_rather_than_raising(checkout: HostSurface) -> None:
    """A command that fails is a fact about the repository, not a fault.

    ADR 139: the loop feeds a failed command into the next question, so the
    failure has to arrive as an `Effect` carrying what was said.
    """
    hands = hands_on(checkout)

    effect = hands(
        Move(
            command=(
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('no such module'); raise SystemExit(1)",
            ),
            why="import the framework",
        )
    )

    assert not effect.succeeded
    assert "no such module" in effect.output


def test_stdout_and_stderr_both_reach_the_next_question(checkout: HostSurface) -> None:
    """Whichever stream is not empty is the one the correction is written from."""
    hands = hands_on(checkout)

    effect = hands(
        Move(
            command=(
                sys.executable,
                "-c",
                "import sys; print('did a thing'); sys.stderr.write('and warned')",
            ),
            why="check the configuration",
        )
    )

    assert "did a thing" in effect.output
    assert "and warned" in effect.output


# ============================== AC 3: what §2.5 says may not happen


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("rm", "-rf", "/"), id="rm -rf"),
        pytest.param(("rm", "-fr", "."), id="rm -fr, flags reversed"),
        pytest.param(("git", "push", "origin", "main"), id="git push"),
        pytest.param(("dd", "if=/dev/zero", "of=/dev/sda"), id="dd"),
        pytest.param(("pip", "uninstall", "-y", "django"), id="pip uninstall"),
        pytest.param(("uv", "pip", "uninstall", "django"), id="uv pip uninstall"),
        pytest.param(("sudo", "apt-get", "install", "postgres"), id="sudo"),
        pytest.param(("shutdown", "-h", "now"), id="shutdown"),
    ],
)
def test_a_denied_move_never_reaches_the_surface(
    checkout: HostSurface, command: tuple[str, ...]
) -> None:
    """**AC 3.** The test attempts the violation and asserts it fails, per `CLAUDE.md`.

    `rm -fr` is in the list because a check that reads flags in one order is a
    check an agent gets past by accident rather than by trying.
    """
    reached: list[tuple[str, ...]] = []

    class Recording(HostSurface):
        """A surface that records and refuses to run. **The assertion is the spy.**

        Checking only the returned `Effect` would pass against a `Hands` that ran
        the command and then reported a refusal, which is the failure mode worth
        testing for — the damage is done by then.
        """

        def run(self, command, **kwargs):  # type: ignore[no-untyped-def]  # a spy, deliberately loose
            reached.append(tuple(command))

    hands = hands_on(Recording(checkout.root))

    effect = hands(Move(command=command, why="make the stage hold"))

    assert reached == []
    assert not effect.succeeded


def test_sh_dash_c_does_not_get_a_denied_command_past_the_check(checkout: HostSurface) -> None:
    """The obvious way past a check that reads `argv[0]`, which is why there is not one.

    `sh -c "rm -rf /"` has `sh` at argv[0] and is a perfectly ordinary thing for a
    model to propose without any intent at all.
    """
    assert refuse(("sh", "-c", "rm -rf /")) is not None
    assert refuse(("bash", "-lc", "git push --force")) is not None


def test_a_refusal_is_told_apart_from_a_failure(checkout: HostSurface) -> None:
    """The two want different follow-ups, so they must not share an exit code.

    A failure is something to diagnose; a refusal is something to replace. An
    agent that cannot tell them apart rephrases the command it was denied.
    """
    hands = hands_on(checkout)

    refused = hands(Move(command=("git", "push"), why="publish the fix"))
    failed = hands(Move(command=(sys.executable, "-c", "raise SystemExit(1)"), why="check"))

    assert refused.exit_code == REFUSED_EXIT_CODE
    assert failed.exit_code == 1
    assert refused.exit_code != failed.exit_code


def test_the_refusal_says_why_and_quotes_what_was_attempted(checkout: HostSurface) -> None:
    """The correction the model is about to be asked to act on.

    *That is denied* costs a turn and teaches nothing. Naming the reason and the
    stage the agent said it was reaching for is what lets it propose a different
    route rather than a rephrasing of the same one.
    """
    hands = hands_on(checkout)

    effect = hands(Move(command=("git", "push"), why="publish the passing build"))

    assert "refused" in effect.output
    assert "somewhere other than the subject" in effect.output
    assert "git push" in effect.output
    assert "publish the passing build" in effect.output


def test_an_ordinary_grounding_command_is_not_denied() -> None:
    """The control, and the direction that would be invisible.

    A denylist that also refuses `manage.py migrate` turns every run into a
    repository that will not ground, and every one of the tests above still
    passes. These are the commands the loop exists to propose.
    """
    ordinary = [
        ("python", "-m", "pip", "install", "-r", "requirements.txt"),
        ("python", "manage.py", "migrate"),
        ("python", "manage.py", "check"),
        ("git", "checkout", "HEAD", "--", "settings.py"),
        ("docker", "compose", "up", "-d", "db"),
        ("createdb", "subject"),
        ("python", "-m", "venv", ".venv"),
    ]

    assert [command for command in ordinary if refuse(command) is not None] == []


def test_every_denial_carries_a_reason_a_model_can_act_on() -> None:
    """Asserted over the table rather than at one call site, so a seventh entry
    added later cannot arrive without one."""
    assert DENIED
    for denial in DENIED:
        assert denial.because
        assert not denial.because.endswith("."), "the message is embedded in a sentence"
        assert len(denial.because.split()) >= 8, "a reason, not a restatement of the rule"


# ============================================ AC 4: the field has a producer


def test_hands_on_produces_something_the_loop_will_accept(checkout: HostSurface) -> None:
    """AC 4. `Resources.hands` is `Hands`, and this is what a campaign calls.

    Nothing in `src/` assembles a whole `Resources` yet — that is still open — so
    what this asserts is that the field's producer exists and returns the shape
    the field is typed as.
    """
    hands = hands_on(checkout)
    assert callable(hands)

    accepted = inspect.signature(explore).parameters["hands"]
    assert accepted.annotation in ("Hands", "coldfix.explorer.loop.Hands")

    effect = hands(Move(command=(sys.executable, "-c", "pass"), why="check the interpreter"))
    assert effect.succeeded


def test_the_timeout_is_bounded_and_stated() -> None:
    """S-1.1's rule reaches this seam too: a subprocess with no deadline can hang
    an investigation with no diagnostic, and `Surface.run` requires one."""
    assert DEFAULT_TIMEOUT_SECONDS > 0
    assert "timeout" in inspect.signature(hands_on).parameters

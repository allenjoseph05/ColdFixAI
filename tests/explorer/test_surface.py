"""Where a grounding command runs, and what the predicate judging it can see.

S-17.7. The story exists because both obvious answers are wrong: a container
alone cannot make progress, because `Sandbox.run` destroys it before returning
while the predicates read the host checkout; and the host has none of the three
protections `03-agents.md` §2.5 assigns to this step, because all three live on
the container.

So the property under test is not *does a command run* — it is **does the
predicate that judges a command see what the command did**. The pair at the
bottom of this file is that property and its control, and the control is the
finding: across two surfaces, nothing is visible.
"""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from coldfix.bench.execute import execute
from coldfix.explorer import surface as surface_module
from coldfix.explorer.surface import HostSurface, SessionSurface, Surface
from coldfix.sandbox.modes import ExecutionMode, Workbench
from coldfix.sandbox.runner import docker_available
from coldfix.sandbox.worktrees import Repository

EXPLORER = Path(surface_module.__file__).parent

# The seven harness control-plane call sites, by module. **Named individually so
# that adding one is a decision.** Four are `docker` — containerising those is
# docker-in-docker — and `anchor`'s two are `git -C root log` and `uv pip
# compile`, the harness's own tools reasoning *about* the repository rather than
# the subject's interpreter running *in* it.
CONTROL_PLANE = {"standup.py": 4, "anchor.py": 2, "surface.py": 1}
"""`surface.py`'s own is `HostSurface.run`, which is where the host call now lives."""


def _execute_calls(path: Path) -> int:
    """How many times this module calls `bench.execute.execute` directly."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute"
    )


# ============================================ AC 2 and AC 5: the two halves


def test_the_subject_facing_modules_no_longer_run_commands_themselves() -> None:
    """AC 2. Eight call sites moved, and the test is that none came back.

    Asserted by parsing rather than by grepping a string, so a call spelled across
    several lines still counts and a mention in a docstring does not.
    """
    moved = ["auth.py", "entrypoints.py", "fixtures.py", "stages.py", "synthesis.py", "work.py"]

    offenders = {name: _execute_calls(EXPLORER / name) for name in moved}

    assert offenders == dict.fromkeys(moved, 0)


def test_the_control_plane_deliberately_still_does() -> None:
    """AC 2's other half, and the direction that actually fails silently.

    `docker run` inside a container is docker-in-docker, and `uv pip compile` is
    the harness resolving a dependency listing for its own reasoning. Moving
    either onto the subject's surface breaks only on a machine where Docker is
    running, which is not the machine the fast subset runs on.
    """
    counted = {name: _execute_calls(EXPLORER / name) for name in CONTROL_PLANE}

    assert counted == CONTROL_PLANE


def test_the_two_sets_partition_every_call_site_in_the_package() -> None:
    """AC 5, and S-17.6's lesson applied.

    Listing only the modules that moved would pass while a sixteenth call site
    appeared in a module named by neither set. The sum is what closes it: every
    direct `execute` in `explorer/` is accounted for by `CONTROL_PLANE`, and
    everything else is zero.
    """
    everywhere = {path.name: _execute_calls(path) for path in sorted(EXPLORER.glob("*.py"))}

    assert {name: n for name, n in everywhere.items() if n} == CONTROL_PLANE


# ================================================== AC 1: two implementations


def test_both_implementations_satisfy_the_protocol() -> None:
    """Structural, not nominal — neither class inherits from `Surface`."""
    host: Surface = HostSurface(Path.cwd())
    assert host.root == Path.cwd()

    assert hasattr(SessionSurface, "run")
    assert "root" in dir(SessionSurface)


def test_a_surface_takes_no_working_directory() -> None:
    """§2.5's workspace confinement, made structural for the subject-facing half.

    There is no argument through which a caller could run a command outside the
    checkout. The eight call sites this replaced all passed `cwd=root` already, so
    nothing lost an ability it was using — what went away is the ability to pass
    something else.
    """
    assert "cwd" not in inspect.signature(HostSurface.run).parameters
    assert "cwd" not in inspect.signature(SessionSurface.run).parameters


def test_the_environment_is_overrides_rather_than_the_whole_set() -> None:
    """The asymmetry the module exists to normalise.

    `execute` *replaces* the environment and `Sandbox.run` *adds to* the image's.
    Spelled at a call site that only ever ran on the host, `{**os.environ, "X":
    "y"}` reads as *add one variable* and becomes *push the harness's whole
    environment into the subject's container* the moment the surface changes. A
    surface takes only the override, and the host implementation is what merges.
    """
    marker = "COLDFIX_S_17_7"
    host = HostSurface(Path.cwd())

    result = host.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PATH', '') != '')"],
        timeout=60.0,
        env={marker: "1"},
    )

    assert result.stdout.strip() == "True", "the host's PATH survived an override"


# ======================================= AC 3 and AC 4: the property and its control


@pytest.fixture
def two_checkouts(tmp_path: Path) -> Iterator[tuple[HostSurface, HostSurface]]:
    """Two surfaces over two directories. **A model of the real split.**

    The finding is about a container and a host, which needs a daemon. The
    structural property underneath it does not: a surface's effects land in its
    own root, and a predicate reading a different root cannot see them. That is
    the same relationship a destroyed container has with the host checkout, and
    it is testable in the fast subset, which is where a property this load-bearing
    belongs. `test_a_session_surface_runs_against_its_own_worktree` covers the
    real one under `docker`.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    yield HostSurface(first), HostSurface(second)


def _wrote(where: HostSurface) -> bool:
    """A predicate, in the shape grounding's predicates take: run something in the
    subject's interpreter at the surface's root and read its answer."""
    result = where.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path('made-by-a-move').exists())"],
        timeout=60.0,
    )
    return result.stdout.strip() == "True"


def test_a_command_is_visible_to_a_predicate_on_the_same_surface(
    two_checkouts: tuple[HostSurface, HostSurface],
) -> None:
    """**AC 3.** The whole point of the story, in three lines.

    The Explorer proposes a command, the harness runs it, and the stage predicate
    decides whether the stage now holds. If those two disagree about the
    filesystem, the loop reproposes until its sixty-step cap — S-7.14's *round
    `auth` eight times*, reached through the executor.
    """
    where, _ = two_checkouts
    assert not _wrote(where)

    where.run(
        [sys.executable, "-c", "import pathlib; pathlib.Path('made-by-a-move').touch()"],
        timeout=60.0,
    )

    assert _wrote(where)


def test_a_command_is_invisible_to_a_predicate_on_another_surface(
    two_checkouts: tuple[HostSurface, HostSurface],
) -> None:
    """**AC 4 — the control, and it is the finding.**

    This is the failure the story was filed against, asserted rather than
    described: the command succeeded, it did exactly what it was asked to do, and
    the predicate judging it reports the stage still unmet. Nothing raises and
    nothing is logged. A `Hands` bound to one surface while the predicates read
    another produces precisely this, and looks like a repository that will not
    ground.
    """
    doing, judging = two_checkouts

    effect = doing.run(
        [sys.executable, "-c", "import pathlib; pathlib.Path('made-by-a-move').touch()"],
        timeout=60.0,
    )

    assert effect.exit_code == 0, "the command worked"
    assert _wrote(doing)
    assert not _wrote(judging), "and the predicate that judges it sees nothing"


@pytest.mark.docker
def test_a_session_surface_runs_against_its_own_worktree(tmp_path: Path) -> None:
    """AC 3 against the surface the decision actually chose.

    The pair above proves the relationship with no daemon. This proves the
    `SessionSurface` half of AC 1 is real: a command runs in the container and the
    file it wrote into the workspace is on the host afterwards, because the
    workspace is a bind mount. That is what makes a fresh container per command
    survivable, and it is the constraint grounding inherits — an environment that
    must outlive one command belongs in the workspace.
    """
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    repository = Repository(_seeded_repo(tmp_path / "origin"))
    workbench = Workbench(
        repository=repository,
        image="python:3.12-slim",
        worktree_root=tmp_path / "worktrees",
    )

    with workbench.open("HEAD", mode=ExecutionMode.DIAGNOSTIC) as session:
        where = SessionSurface(session)

        result = where.run(["python", "-c", "open('made-by-a-move','w').close()"], timeout=180.0)

        assert result.exit_code == 0
        assert (where.root / "made-by-a-move").exists(), "the workspace is a bind mount"


def _seeded_repo(path: Path) -> Path:
    """One commit, so `open("HEAD")` has a revision to check out."""
    path.mkdir(parents=True)
    (path / "README").write_text("s-17.7\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "s-17.7@example.invalid"],
        ["config", "user.name", "S-17.7"],
        ["add", "-A"],
        ["commit", "-m", "seed"],
    ):
        execute(["git", "-C", str(path), *args], timeout=60.0)
    return path


def test_every_run_starts_a_new_process(two_checkouts: tuple[HostSurface, HostSurface]) -> None:
    """S-17.10's cache control rests on this, so it is measured rather than assumed.

    A binding satisfies S-3.2 by process identity — *the interpreter that served
    one scale point is not the one that serves the next* — and that is only true
    because a surface never reuses a process. `execute` spawns a subprocess and
    `Sandbox.run` creates a container it destroys before returning, so nothing a
    command cached in memory can reach the next one.

    Measured for the host here. Structural for the session, because ADR 004 makes
    the destruction unconditional and `test_a_session_surface_runs_against_its_own_worktree`
    exercises it.
    """
    where, _ = two_checkouts

    pids = {
        where.run([sys.executable, "-c", "import os; print(os.getpid())"], timeout=60.0).stdout
        for _ in range(3)
    }

    assert len(pids) == 3, "a reused process would carry its caches into the next scale point"

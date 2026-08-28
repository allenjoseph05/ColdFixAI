"""Binding a repository to the grounding sequence, and the join S-17.7 left open.

S-17.9. Two properties, and the first is the one that was actually broken.

S-17.7 gave every subject-facing step a `surface` parameter and routed the eight
call sites through it. Nothing threaded one: `ground_workload` called all of them
with the default, every default resolved to `HostSurface(root)`, and the decision
never reached grounding. Both sides complete, the join with no owner — the shape
every composition check in this project has found.

So the test that matters is not *does each step accept a surface*. It is **did one
surface reach every step**, which needs the composed sequence driven and the
surface itself watching.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

from coldfix.bench.execute import ExecutionResult, execute
from coldfix.explorer import compose
from coldfix.explorer.auth import Reply
from coldfix.explorer.binding import (
    _PROBE_SOURCE,
    ProbeError,
    grounder_for,
    probe_through,
)
from coldfix.explorer.compose import NotGroundableError, Plan, ground_workload
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.orchestrator.adapters import Grounder
from coldfix.sandbox.reset import ResetMechanism, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset

MANAGE_PY = (
    "import os, sys\n"
    'if __name__ == "__main__":\n'
    '    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")\n'
    "    from django.core.management import execute_from_command_line\n"
    "    execute_from_command_line(sys.argv)\n"
)
"""The settings module has to be declared here: `settings_module(root)` reads
`manage.py`, `wsgi.py` and `asgi.py` for it, and without one the enumerator
returns before running any command — so the surface would legitimately see
nothing and the test would pass against a sequence that threaded nothing."""

PATH = "/books/"


class Watching:
    """A surface that records every command and answers them itself.

    Not a `HostSurface` subclass: the point is that nothing reaches a real
    subprocess, so a step that quietly built its own executor would show up as a
    command this never saw rather than as a passing test.
    """

    def __init__(self, root: Path, replies: Mapping[str, str] | None = None) -> None:
        self._root = root
        self._replies = dict(replies or {})
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[Mapping[str, str]] = []

    @property
    def root(self) -> Path:
        return self._root

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = 100_000,
    ) -> ExecutionResult:
        self.commands.append(tuple(command))
        self.environments.append(dict(env or {}))
        rendered = " ".join(command)
        for needle, answer in self._replies.items():
            if needle in rendered:
                return _result(command, answer)
        return _result(command, "")


def _result(command: Sequence[str], stdout: str, exit_code: int = 0) -> ExecutionResult:
    return ExecutionResult(
        command=tuple(command),
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        wall_seconds=0.0,
    )


# ============================================ AC 3: the probe needs no network


def test_the_probe_asks_the_subject_rather_than_the_network(tmp_path: Path) -> None:
    """AC 3. `Reply` is *deliberately not an HTTP client* and the subject has no
    egress, so the one anonymous GET is made by the subject about itself."""
    answer = json.dumps({"status": 200, "headers": {"X-Thing": "1"}, "answered_path": None})
    watching = Watching(tmp_path, {"-c": "__COLDFIX_PROBE__" + answer})

    reply = probe_through(watching, python=["python"], settings="shop.settings")(PATH)

    assert reply.status == 200
    assert reply.headers == {"X-Thing": "1"}
    assert watching.commands, "the subject was asked"
    assert watching.environments[0] == {"DJANGO_SETTINGS_MODULE": "shop.settings"}


def test_the_probe_reports_where_the_answer_came_from(tmp_path: Path) -> None:
    """`answered_path` is the load-bearing field, not a nicety.

    A client that follows redirects turns `login_required`'s 302 into a 200
    holding a login page, and nothing in the status or the headers tells that
    apart from the endpoint answering. Reading `redirect_chain` back is what makes
    the difference visible to `resolve_auth`.
    """
    answer = json.dumps(
        {"status": 200, "headers": {}, "answered_path": "/accounts/login/?next=/books/"}
    )
    watching = Watching(tmp_path, {"-c": "__COLDFIX_PROBE__" + answer})

    reply = probe_through(watching, python=["python"], settings="shop.settings")(PATH)

    assert reply.answered_path == "/accounts/login/?next=/books/"
    assert reply.status == 200, "and the status alone would have said the route is open"


def test_the_probe_follows_redirects_so_there_is_a_chain_to_read() -> None:
    """The half of the previous test that lives in the injected program.

    `redirect_chain` is empty unless the client was asked to follow, so a probe
    that did not would report `answered_path` as `None` for every login redirect
    in existence — and the test above would still pass, because it supplies the
    payload rather than producing it.
    """

    assert "follow=True" in _PROBE_SOURCE
    assert "redirect_chain" in _PROBE_SOURCE


def test_a_subject_that_does_not_answer_is_refused_rather_than_scored(tmp_path: Path) -> None:
    """A `Reply` this could not measure would be an observation of nothing, and
    `resolve_auth` would read it as a scheme."""
    watching = Watching(tmp_path)

    with pytest.raises(ProbeError, match="did not answer"):
        probe_through(watching, python=["python"], settings="shop.settings")(PATH)


def test_a_subject_that_raised_is_refused_with_what_it_raised(tmp_path: Path) -> None:
    answer = json.dumps({"status": 0, "headers": {}, "error": "ImproperlyConfigured: no db"})
    watching = Watching(tmp_path, {"-c": "__COLDFIX_PROBE__" + answer})

    with pytest.raises(ProbeError, match="ImproperlyConfigured"):
        probe_through(watching, python=["python"], settings="shop.settings")(PATH)


# ================================ AC 1 and 2: one surface reached every step


def test_the_composed_sequence_drives_the_surface_it_was_given(tmp_path: Path) -> None:
    """**AC 2, and the defect this story was filed against.**

    Driven rather than inspected. Before this story `ground_workload` accepted no
    surface at all and every step it called resolved the default to
    `HostSurface(root)`, so a `Watching` handed in would have recorded nothing
    while the sequence ran perfectly well against the host — the failure would have
    been invisible from the return value, because the run stops here either way.

    The sequence does not complete: `Watching` answers every command with empty
    output, so the enumerator finds no drivable route and the run is refused. That
    is the point at which the property has already been established.
    """
    root = tmp_path / "subject"
    root.mkdir()
    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "requirements.txt").write_text("django>=5.0,<6\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "settings.py").write_text("DATABASES = {}\n", encoding="utf-8")

    # `anchor_for` reads `git -C root log`, which is control-plane and stays on
    # the host by design — so the checkout needs a history for the sequence to
    # reach the steps this test is about.
    for args in (
        ["init", "--quiet"],
        ["config", "user.email", "s-17.9@example.invalid"],
        ["config", "user.name", "S-17.9"],
        ["add", "-A"],
        ["commit", "--quiet", "-m", "subject"],
    ):
        execute(["git", "-C", str(root), *args], timeout=60.0)

    watching = Watching(root)

    with pytest.raises(NotGroundableError):
        ground_workload(
            root,
            python=["python"],
            request=_never_requested,
            plan=Plan(workload_id="books", description="the books list"),
            reset=cast(VerifiedReset, object()),
            surface=watching,
        )

    assert watching.commands, "the sequence ran its steps on the surface it was handed"
    assert all("python" in command[0] for command in watching.commands)


def _never_requested(path: str) -> Reply:
    message = f"the run should have stopped before requesting {path}"
    raise AssertionError(message)


def test_one_surface_is_resolved_once_rather_than_per_step() -> None:
    """AC 2's real content, asserted against the source.

    Every subject-facing entry point spells `surface or HostSurface(root)`, and
    each of those is a place the resolution can stop agreeing with the others.
    `ground_workload` resolves once and passes the *object* down, so there is one
    surface for the whole sequence — which is what makes a command and the
    predicate judging it agree about the filesystem.
    """

    source = inspect.getsource(compose.ground_workload)

    assert source.count("HostSurface(") == 1, "resolved once"
    for step in ("enumerate_entry_points", "resolve_auth", "verify_work", "Grounding("):
        assert step in source
    assert source.count("surface=where") + source.count("where)") >= 4


def test_every_subject_facing_step_is_handed_the_resolved_surface() -> None:
    """The partition again: naming the steps, so a sixth cannot be added silently.

    Listing only the ones that are threaded would pass while a new step appeared
    below them taking the default — which is exactly how the gap this story closes
    came to exist.
    """

    source = inspect.getsource(compose.ground_workload)
    threaded = {
        "enumerate_entry_points": "surface=where",
        "resolve_auth": "surface=where",
        "_seeder": "_seeder(root, plan, where)",
        "verify_work": "surface=where",
        "Grounding": "surface=where",
    }

    missing = [name for name, spelling in threaded.items() if spelling not in source]

    assert missing == []


def test_grounder_for_returns_the_four_journal_seams_and_nothing_else(
    tmp_path: Path,
) -> None:
    """**AC 4.** `Grounder.__call__` takes exactly those four, keyword-only.

    They file under `Fingerprint.playbook_key()`, derived inside the sequence, so
    a caller could only bind one by fingerprinting the repository first — S-13.7's
    split between what the campaign owns and what the run owns. A `grounder_for`
    that bound them would be re-deciding that quietly.
    """

    ground = grounder_for(
        tmp_path,
        python=["python"],
        surface=HostSurface(tmp_path),
        plan=Plan(workload_id="books", description="the books list"),
        reset=VerifiedReset(
            mechanism=_NoReset(),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        request=lambda path: _reply(),
    )

    parameters = inspect.signature(ground).parameters
    assert list(parameters) == ["playbook", "trusted_entries", "learn", "used"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())


def test_a_grounder_without_a_request_or_settings_is_refused(tmp_path: Path) -> None:
    """Deriving the settings module here would make a probe against the wrong
    configuration look like a route that needs no credential — the one answer
    that costs a real measurement."""

    with pytest.raises(ProbeError, match="either a `request` or"):
        grounder_for(
            tmp_path,
            python=["python"],
            surface=HostSurface(tmp_path),
            plan=Plan(workload_id="books", description="the books list"),
            reset=VerifiedReset(
                mechanism=_NoReset(),
                report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
            ),
        )


def test_the_produced_callable_satisfies_the_grounder_protocol(tmp_path: Path) -> None:
    """**AC 5.** `Resources.ground` is typed `Grounder`, and this is what fills it.

    Checked here rather than in `explorer/` because the protocol lives in the
    orchestrator and the dependency runs that way: `binding.py` importing it would
    point the explorer at the layer above it.
    """

    ground: Grounder = grounder_for(
        tmp_path,
        python=["python"],
        surface=HostSurface(tmp_path),
        plan=Plan(workload_id="books", description="the books list"),
        reset=VerifiedReset(
            mechanism=_NoReset(),
            report=VerificationReport(strategy=ResetStrategy.SNAPSHOT_RESTORE, cycles=10),
        ),
        request=lambda path: _reply(),
    )

    assert callable(ground)


def _reply() -> Reply:
    return Reply(status=200)


class _NoReset(ResetMechanism):
    """The minimum a `VerifiedReset` will hold. Nothing here resets anything.

    These tests stop before emission, which is the only step that touches the
    reset — a real one needs a mechanism that passed ten cycles, and paying for
    that here would be proving something the run never uses.
    """

    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def prepare(self) -> None: ...
    def begin(self) -> None: ...
    def reset(self) -> None: ...


def test_the_surface_protocol_is_satisfied_by_the_test_double() -> None:
    """The double stands in for a surface, so it has to be one."""
    watching: Surface = Watching(Path.cwd())
    assert watching.root == Path.cwd()

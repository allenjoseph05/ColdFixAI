"""The real `Docker`, against the command-line client.

Split out so the module holding the tier logic imports no subprocess call, and
so a test can exercise every tier without Docker running.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from coldfix.collect.tiers import BuildResult

RUN_TIMEOUT = 120.0
BUILD_TIMEOUT = 900.0


class DockerCli:
    def can_run(self, image: str) -> BuildResult:
        """Start a process in the image. Nothing is assumed about what is inside.

        `--entrypoint` is overridden with a command every image that can execute
        anything can execute, and even that is allowed to fail on its own terms:
        what is being established is whether a process starts, not whether the
        image ships a particular binary.
        """
        completed = _run(["docker", "run", "--rm", "--entrypoint", "/bin/true", image], RUN_TIMEOUT)
        if completed.ok:
            return BuildResult(True, "a process started in the image")
        fallback = _run(["docker", "run", "--rm", image, "--version"], RUN_TIMEOUT)
        if fallback.ok:
            return BuildResult(True, "the image's own entrypoint ran")
        return BuildResult(False, completed.detail)

    def build(self, dockerfile: str, tag: str) -> BuildResult:
        directory = Path(tempfile.mkdtemp(prefix="coldfix-derived-"))
        try:
            (directory / "Dockerfile").write_text(dockerfile, encoding="utf-8")
            completed = _run(["docker", "build", "-q", "-t", tag, str(directory)], BUILD_TIMEOUT)
            return BuildResult(
                completed.ok,
                "instrumentation installed into a derived image"
                if completed.ok
                else completed.detail,
            )
        finally:
            for leftover in directory.glob("*"):
                leftover.unlink()
            directory.rmdir()

    def reads_compose(self, root: Path) -> BuildResult:
        """Ask Docker whether there is a composed environment here.

        Docker owns which filenames count, so it is asked rather than guessed at
        with a list of names that goes stale.
        """
        completed = _run(["docker", "compose", "config", "--quiet"], RUN_TIMEOUT, cwd=root)
        return BuildResult(
            completed.ok,
            "docker compose read an environment here"
            if completed.ok
            else "no composed environment docker would read",
        )


class _Completed:
    __slots__ = ("detail", "ok")

    def __init__(self, ok: bool, detail: str) -> None:
        self.ok = ok
        self.detail = detail


def _run(argv: list[str], timeout: float, *, cwd: Path | None = None) -> _Completed:
    try:
        completed = subprocess.run(
            argv,
            cwd=None if cwd is None else str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as failed:
        return _Completed(False, f"{argv[0]} did not answer: {failed}")
    if completed.returncode == 0:
        return _Completed(True, "")
    return _Completed(False, (completed.stderr or completed.stdout).strip()[-300:])

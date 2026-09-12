"""The ColdFix wheel the container installs. **S-28.1b.**

Every tool call runs `python -m coldfix.collect.run` *inside* the subject's
container, so the container needs this package. The derived image gets it from a
wheel copied into the build context, and this module is where that wheel comes
from.

**Built, not vendored.** A wheel checked into the tree is one that is wrong the
first time anybody edits `collect/`, and wrong silently -- the container would
run yesterday's collector against today's ledger and every field name would still
match. `uv build` produces it from the source that is actually installed.

**Built once per process.** A scan makes one image; a test suite makes one wheel
for however many it makes. The cache is keyed on nothing because there is only
one version of this package in a given interpreter.

**`uv` missing is a harness failure, not a subject failure.** It is a
requirement of the machine running ColdFix, and the message says so rather than
reporting an image that cannot be instrumented.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

BUILD_TIMEOUT = 300.0

_built: Path | None = None


class WheelError(Exception):
    """The ColdFix wheel could not be produced on this machine."""


def project_root() -> Path:
    """The directory holding `pyproject.toml`, found from this file.

    Walked rather than configured: the package may be installed anywhere, and a
    setting would be one more thing that can point at the wrong tree.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    message = (
        "no pyproject.toml above coldfix/collect/wheel.py, so the wheel the container "
        "installs cannot be built. This is a checkout problem, not a subject problem"
    )
    raise WheelError(message)


def build_wheel(*, out_dir: Path | None = None, root: Path | None = None) -> Path:
    """Build the ColdFix wheel and return its path. Memoised per process.

    Raises:
        WheelError: `uv` is not on PATH, the build failed, or it produced
            something other than exactly one wheel. Each is a fault on the
            machine running ColdFix, and none of them is an observation about
            the subject.
    """
    global _built  # noqa: PLW0603 - one wheel per interpreter is the whole point;
    # threading it through six call sites would put a cache key in the tier probe.
    if out_dir is None and _built is not None and _built.is_file():
        return _built

    if shutil.which("uv") is None:
        message = (
            "`uv` is not on PATH, and it is what builds the wheel the container installs. "
            "This is a requirement of the machine running ColdFix, not something about the "
            "image being measured"
        )
        raise WheelError(message)

    destination = out_dir or Path(tempfile.mkdtemp(prefix="coldfix-wheel-"))
    destination.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(destination), str(root or project_root())],
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as failed:
        message = f"`uv build` did not complete: {failed}"
        raise WheelError(message) from failed

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        message = f"`uv build --wheel` failed: {detail}"
        raise WheelError(message)

    wheels = sorted(destination.glob("coldfix-*.whl"))
    if len(wheels) != 1:
        message = (
            f"expected exactly one coldfix wheel in {destination} and found {len(wheels)}. "
            "A stale wheel beside a fresh one is how a container ends up running a collector "
            "that is not this source"
        )
        raise WheelError(message)

    if out_dir is None:
        _built = wheels[0]
    return wheels[0]

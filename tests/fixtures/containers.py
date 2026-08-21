"""Skipping a container test the image for which is not on this machine.

`docker_available()` answers *is a daemon listening*, and it answers it correctly.
What it cannot answer is *can this test actually run*, because a daemon that is up
will happily accept `docker run alpine` and then spend the whole timeout pulling
the image from a network that may be slow, metered or absent.

Measured, that cost 120 seconds per test and three failures on a machine whose
Docker was working perfectly — the image simply was not local. `pyproject.toml`
keeps the `docker` marker separate from `slow` because *a slow test is one you
choose not to wait for, a docker test is one this machine cannot run at all*, and
a missing image is the second of those wearing the costume of the first.

**Nothing here pulls.** A test fixture that fetched a few hundred megabytes on
somebody's first run would turn `uv run pytest` into a download, and the failure
mode when it did not work would be a timeout rather than a skip — which is exactly
the problem being fixed.
"""

from __future__ import annotations

import pytest

from coldfix.bench.execute import ExecutionStartError, ExecutionTimeoutError, execute

INSPECT_TIMEOUT_SECONDS = 30.0
"""Long enough for a daemon under load to answer a local metadata question, and
short enough that a hung one is not mistaken for a slow one."""


def image_present(image: str) -> bool:
    """Whether `image` is already on this machine. Never fetches it."""
    try:
        result = execute(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            timeout=INSPECT_TIMEOUT_SECONDS,
        )
    except (ExecutionStartError, ExecutionTimeoutError):
        return False
    return result.exit_code == 0


def require_image(image: str) -> None:
    """Skip unless `image` is local, naming the command that would make it so.

    The skip message carries the `docker pull` rather than only the fact, because
    a reader seeing a skipped test wants to know whether it is skipped for a reason
    they can fix.
    """
    if not image_present(image):
        pytest.skip(f"image {image!r} is not present locally; run `docker pull {image}`")

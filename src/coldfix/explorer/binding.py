"""Binding a repository's own facts to the grounding sequence. **`Resources.ground`.**

S-17.9. `ground_workload` takes six things from five owners and the `Grounder`
protocol takes four; the difference is what a campaign knows and a node does not.
This module is that difference, made into a function.

**The four journal seams stay the node's and are deliberately not bound here.**
`playbook`, `trusted_entries`, `learn` and `used` file under
`Fingerprint.playbook_key()`, which is derived *inside* the sequence — so a caller
could only bind one by fingerprinting the repository itself first. S-13.7 settled
the split: the campaign owns the repository, the run owns the journal. A
`grounder_for` that bound them would be re-deciding that, quietly.

**The anonymous probe needs no network, and could not have one.** `resolve_auth`
makes exactly one unauthenticated request to learn whether the route demands a
credential, and `Reply`'s docstring says the type is *deliberately not an HTTP
client* — nothing under `src/` may reach the network on its own account. The
subject also has no egress (ADR 029) and is not serving: grounding is `manage.py`
introspection, not a running server. So the request is made the way `drive` already
makes one — by the subject, in its own interpreter, through the surface.

`answered_path` is why this is not a formality. A client that follows redirects
turns `login_required`'s 302 into a 200 holding a login page, and nothing in the
status or the headers tells that apart from the endpoint answering. Django's test
client reports it as `redirect_chain`, which is read back here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from coldfix.explorer.auth import PlaybookLookup, Reply, TrustedLookup
from coldfix.explorer.compose import Grounded, Plan, ground_workload
from coldfix.explorer.playbook import PlaybookWriter, UseRecorder
from coldfix.explorer.surface import Surface
from coldfix.sandbox.verification import VerifiedReset

PROBE_TIMEOUT_SECONDS = 120.0

_MARKER = "__COLDFIX_PROBE__"

# Runs in the *subject's* interpreter. `follow=True` on purpose: the redirect has
# to actually be followed for `redirect_chain` to record where the answer came
# from, and that path is the only thing separating *the endpoint answered* from
# *something bounced me to a login page*.
_PROBE_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

answer = {"status": 0, "headers": {}, "answered_path": None, "error": None}
try:
    import django

    django.setup()
    from django.test import Client

    response = Client().get(__PATH__, follow=True)
    answer["status"] = int(response.status_code)
    answer["headers"] = {str(k): str(v) for k, v in response.headers.items()}
    chain = list(getattr(response, "redirect_chain", []) or [])
    if chain:
        answer["answered_path"] = str(chain[-1][0])
except Exception as error:
    answer["error"] = type(error).__name__ + ": " + str(error)

print("__COLDFIX_PROBE__" + json.dumps(answer))
"""


class ProbeError(Exception):
    """The subject could not be asked what a route requires."""


def probe_through(
    surface: Surface,
    *,
    python: Sequence[str],
    settings: str,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> Callable[[str], Reply]:
    """One anonymous GET, answered by the subject about itself.

    Raises:
        ProbeError: the subject's interpreter did not answer, or answered with
            something that is not the probe's JSON. Refused rather than reported
            as a status, because a `Reply` this could not measure would be an
            observation of nothing that `resolve_auth` would read as a scheme.
    """

    def request(path: str) -> Reply:
        program = _PROBE_SOURCE.replace("__PATH__", json.dumps(path))
        result = surface.run(
            [*python, "-c", program],
            timeout=timeout,
            env={"DJANGO_SETTINGS_MODULE": settings},
        )

        line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
        if line is None:
            said = (result.stderr or result.stdout).strip()[-600:]
            message = (
                f"the subject did not answer the anonymous probe of {path!r} "
                f"(exit {result.exit_code}): {said}"
            )
            raise ProbeError(message)

        try:
            payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
        except json.JSONDecodeError as error:
            message = f"the subject's answer to the probe was not JSON: {error}"
            raise ProbeError(message) from error

        if payload.get("error"):
            message = f"the subject could not serve {path!r}: {payload['error']}"
            raise ProbeError(message)

        headers: Mapping[str, str] = payload.get("headers", {})
        answered = payload.get("answered_path")
        return Reply(
            status=int(payload["status"]),
            headers=dict(headers),
            answered_path=None if answered is None else str(answered),
        )

    return request


def grounder_for(  # noqa: PLR0913 - the checkout, its interpreter, where commands
    # run, what the Explorer decided, the reset proof and how to make one request
    # are six facts from five owners, and this function exists precisely to hold
    # them. Bundling them would invent the config object `CLAUDE.md` refuses.
    root: Path,
    *,
    python: Sequence[str],
    surface: Surface,
    plan: Plan,
    reset: VerifiedReset,
    request: Callable[[str], Reply] | None = None,
    settings: str | None = None,
) -> Callable[..., Grounded]:
    """Bind a repository to `ground_workload`. **The producer for `Resources.ground`.**

    `request` defaults to `probe_through(surface, ...)`, which needs `settings` —
    supplied rather than detected here because `settings_module(root)` is a *file
    read* and this function has no other reason to open the checkout. A caller with
    its own client passes `request` and omits both.

    The returned callable takes exactly the four journal seams, which is the
    `Grounder` protocol. It is annotated `Callable[..., Grounded]` rather than as a
    nominal subclass because `Grounder` lives in `orchestrator.adapters` and
    importing it here would point `explorer` at the orchestrator — the dependency
    runs the other way, and a test asserts the produced callable satisfies the
    protocol structurally.
    """
    if request is None:
        if settings is None:
            message = (
                "grounder_for needs either a `request` or the subject's settings module to build "
                "one. Defaulting the settings to something derived from the checkout would make "
                "a probe against the wrong configuration look like a route that needs no "
                "credential, which is the one answer that costs a real measurement"
            )
            raise ProbeError(message)
        request = probe_through(surface, python=python, settings=settings)

    def ground(
        *,
        playbook: PlaybookLookup,
        trusted_entries: TrustedLookup,
        learn: PlaybookWriter,
        used: UseRecorder,
    ) -> Grounded:
        return ground_workload(
            root,
            python=python,
            request=request,
            plan=plan,
            reset=reset,
            surface=surface,
            playbook=playbook,
            trusted_entries=trusted_entries,
            learn=learn,
            used=used,
        )

    return ground

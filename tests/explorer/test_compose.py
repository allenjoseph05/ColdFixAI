"""The joins in `explorer.compose`, at unit speed.

`test_explorer_composed.py` drives a real Django repository and is `slow` for
that reason. **The joins do not need a repository**, and the seventh defect this
module fixes is the argument for testing them where they will actually be run: it
survived a whole composition check because the only thing exercising it was an
end-to-end test against a subject where the value it dropped did not matter.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from coldfix.explorer.auth import Credential, Recipe, Reply, Scheme
from coldfix.explorer.compose import NotGroundableError, Plan, carried, ground_workload
from coldfix.sandbox.verification import VerifiedReset

RECIPE = Recipe(scheme=Scheme.TOKEN, username="explorer", password="secret")


def plan(
    headers: Mapping[str, str] | None = None, cookies: Mapping[str, str] | None = None
) -> Plan:
    return Plan(
        workload_id="shop.books",
        description="the book list endpoint",
        headers=headers or {},
        cookies=cookies or {},
    )


def token(
    headers: Mapping[str, str] | None = None, cookies: Mapping[str, str] | None = None
) -> Credential:
    return Credential(
        scheme=Scheme.TOKEN,
        recipe=RECIPE,
        headers={"Authorization": "Token abc123"} if headers is None else headers,
        cookies=cookies or {},
    )


# ============================================ the seventh join


def test_a_minted_credential_is_carried_into_the_measurement() -> None:
    """**The defect a composition check could not see.**

    `resolve_auth` mints a credential, the run asserts it exists, and then drives
    the route. Until S-7.13 nothing put the two together: `attach` existed, and
    the composed sequence called it nowhere. On a subject whose every route is
    open that changes nothing, which is exactly why it survived.

    This fails if the credential is dropped, which is the whole point of it.
    """
    headers, cookies = carried(token(), plan())

    assert headers is not None
    assert headers["Authorization"] == "Token abc123"
    assert cookies == {}


def test_a_credential_carried_in_a_cookie_is_carried_too() -> None:
    """Headers and cookies are separate because HTTP keeps them separate, and a
    join that carried one of them would be the same defect half-fixed."""
    headers, cookies = carried(token(headers={}, cookies={"sessionid": "xyz"}), plan())

    assert cookies is not None
    assert cookies["sessionid"] == "xyz"
    assert headers == {}


def test_the_credential_wins_a_collision_with_the_callers_own_header() -> None:
    """`attach`'s rule, not this module's. A caller supplying its own
    `Authorization` beside a token credential has two credentials and one slot,
    and taking the caller's sends the request unauthenticated — which reads as a
    route that requires auth rather than as a header that was overwritten."""
    headers, _ = carried(token(), plan(headers={"Authorization": "Basic nope"}))

    assert headers is not None
    assert headers["Authorization"] == "Token abc123"


def test_a_route_needing_nothing_still_carries_what_the_caller_asked_for() -> None:
    """`Scheme.NONE` is a genuine answer and the best one. A route needing no
    credential must not lose the caller's own headers on the way past."""
    headers, cookies = carried(None, plan(headers={"X-Trace": "1"}, cookies={"tz": "UTC"}))

    assert headers == {"X-Trace": "1"}
    assert cookies == {"tz": "UTC"}


def test_nothing_to_carry_is_nothing_rather_than_an_empty_mapping() -> None:
    """`verify_work` takes `None` for *no headers*, and an empty mapping is a
    different argument. Passing `{}` where `None` is meant is the kind of thing
    that works until something downstream tells the two apart."""
    assert carried(None, plan()) == (None, None)


# ============================================ refusals


def test_a_repository_that_is_not_a_supported_framework_is_refused(tmp_path: Path) -> None:
    """S-7.1 answers `Unsupported` rather than raising, and this is the point at
    which that becomes a stop. Carrying on would enumerate routes in a directory
    that has none and report an empty result as a healthy screen.

    The reset proof is a sentinel that is never touched: a real `VerifiedReset`
    needs a mechanism that passed ten cycles, and a run refused before emission
    would be paying for one to prove it was not used.
    """
    (tmp_path / "README.md").write_text("not a web application", encoding="utf-8")
    unreachable = cast(VerifiedReset, object())

    def never_requested(path: str) -> Reply:
        message = f"nothing should have been requested: {path}"
        raise AssertionError(message)

    with pytest.raises(NotGroundableError, match="not a repository this system can ground"):
        ground_workload(
            tmp_path,
            python=["python"],
            request=never_requested,
            plan=plan(),
            reset=unreachable,
        )

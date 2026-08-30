"""What an entry is, and the join that makes grounding consult one.

S-13.1. Three modules deferred the entry schema to this story and then declined
to guess at it, so most of the machinery already existed: `playbook_key()` files
by framework and major version, `playbook_from_store` reads by that key, and
`Collection.PLAYBOOKS` has been a member since S-6.2. What was missing is what an
entry *means* — and the call that makes grounding ask for one at all.

**The join is tested first-class, because S-13.3 learned that the hard way.**
That story's whole content was a join and its sabotage survived: one file covered
each end and neither held both. Here the equivalent sabotage is dropping
`playbook=` from `ground_workload`'s call to `resolve_auth`, and the test below
fails when it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import cast

import pytest

import coldfix.adapters  # noqa: F401 - registers grounding support; the registry is empty without it
from coldfix.explorer import compose
from coldfix.explorer.anchor import Anchor
from coldfix.explorer.auth import (
    AuthProfile,
    Established,
    PlaybookLookup,
    Requirement,
    Scheme,
)
from coldfix.explorer.auth import Resolution as AuthResolution
from coldfix.explorer.entrypoints import (
    Candidate,
    Discovery,
    Enumeration,
    Kind,
    Resolution,
    Scored,
)
from coldfix.explorer.fingerprint import Detected, Fingerprint, Framework
from coldfix.explorer.playbook import (
    DEMOTION_THRESHOLD,
    PROMOTION_THRESHOLD,
    PlaybookEntry,
    PlaybookError,
    Standing,
    Status,
    as_entry,
    describe_all,
    from_entry,
    learned_from_auth,
)
from coldfix.explorer.registry import Grounds, grounds_for
from coldfix.sandbox.verification import VerifiedReset


def an_entry(**overrides: str) -> PlaybookEntry:
    fields = {
        "situation": "the settings module declares DRF token authentication",
        "action": "mint a token for a fresh user and send it as an Authorization header",
        "outcome": "the list endpoint answered 200 instead of 401",
    }
    fields.update(overrides)
    return PlaybookEntry(**fields)


# ==================================================== AC 2 — situation, action, outcome


def test_an_entry_round_trips_through_the_journal_shape() -> None:
    restored = from_entry(as_entry(an_entry()))

    assert restored.situation.startswith("the settings module")
    assert "Authorization header" in restored.action
    assert restored.outcome.endswith("instead of 401")


@pytest.mark.parametrize("field", ["situation", "action", "outcome"])
def test_all_three_are_required(field: str) -> None:
    """An action with no situation is advice to do something unconditionally,
    which is not what a playbook is."""
    with pytest.raises(PlaybookError, match="not one this system wrote"):
        from_entry({k: v for k, v in as_entry(an_entry()).items() if k != field})


def test_an_entry_cannot_carry_a_verdict() -> None:
    """**The field S-13.2 owns, refused a story early.**

    `worked` is the tempting fourth, and it is exactly what S-13.2 defines with
    the counters that justify it: *provisional, promoted after N successes across
    different projects, demoted after two failures.* A boolean here would be that
    judgement made without any of them — F15's shape, one collection over.
    """
    with pytest.raises(PlaybookError, match="not one this system wrote"):
        from_entry({**as_entry(an_entry()), "worked": True})


def test_an_empty_field_is_refused_rather_than_stored() -> None:
    with pytest.raises(PlaybookError):
        from_entry({**as_entry(an_entry()), "action": "  "})


# ==================================================== AC 3 — consulted and empty


def test_nothing_learned_still_says_it_was_asked() -> None:
    """`no_playbook` exists so *consulted and empty* and *not consulted* are
    different call sites — S-13.5 measures whether the tenth project of a kind
    grounds faster than the first, and a silent non-consult makes that
    meaningless."""
    rendered = describe_all([])

    assert "nothing learned" in rendered
    assert "playbook" in rendered


def test_what_the_explorer_is_shown_carries_all_three_parts() -> None:
    rendered = describe_all([an_entry(), an_entry(action="reuse the existing superuser")])

    assert rendered.startswith("playbook: 2 entry(s)")
    assert "when the settings module declares DRF token authentication" in rendered
    assert "reuse the existing superuser" in rendered


# ==================================================== AC 3 — the join


class ReachedAuthError(Exception):
    """Raised by the stand-in for `resolve_auth`, to stop the sequence there.

    Everything after that stage needs a real repository, and what is under test
    is which arguments the composition passes — so the test stops where the
    answer is already known rather than building a Django project to reach it.
    """


class Recorder:
    """A lookup that records the key it was asked for."""

    def __init__(self, entries: Sequence[Mapping[str, object]] = ()) -> None:
        self.entries = list(entries)
        self.asked: list[str] = []

    def __call__(self, key: str) -> Sequence[Mapping[str, object]]:
        self.asked.append(key)
        return self.entries


def test_grounding_consults_the_playbook_under_the_fingerprints_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The join, and the sabotage that would otherwise survive.**

    The seam was built at S-7.4 and nothing filled it: `ground_workload` called
    `resolve_auth` with no key, so the consult never happened and even
    `no_playbook` was not reached. Dropping `playbook=` from that call is the
    sabotage this test exists to fail on.

    Stops at `resolve_auth` deliberately — everything after it needs a real
    repository, and what is under test is which arguments the composition passes.
    """
    lookup = Recorder([as_entry(an_entry())])
    seen: dict[str, object] = {}

    def fake_resolve_auth(_root: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise ReachedAuthError

    monkeypatch.setattr(compose, "fingerprint", lambda _root: _a_fingerprint())
    monkeypatch.setattr(compose, "anchor_for", lambda _root: _an_anchor())
    monkeypatch.setattr(compose, "interpreter_for", lambda _root: None)
    # **The enumerator is the registry's now, not this module's. S-14.6.**
    # `compose` asks `grounds_for(...)` and calls what comes back, so patching
    # the name here would patch something that is no longer read.
    monkeypatch.setattr(
        compose,
        "grounds_for",
        lambda _framework: Grounds(
            framework="Django",
            enumerate_entry_points=lambda _r, **_k: _an_enumeration(),
            predicates=grounds_for("Django").predicates,  # type: ignore[union-attr]
        ),
    )
    monkeypatch.setattr(compose, "resolve_auth", fake_resolve_auth)

    with pytest.raises(ReachedAuthError):
        compose.ground_workload(
            _a_fingerprint().root,
            python=["python"],
            request=lambda path: pytest.fail(f"nothing should be requested: {path}"),
            plan=compose.Plan(workload_id="w", description="d"),
            reset=_never_used(),
            playbook=lookup,
        )

    assert seen["playbook"] is lookup, "the lookup itself, not a fresh no_playbook"
    assert seen["playbook_key"] == "Django/5", "the fingerprint's own key"


def test_grounding_without_a_playbook_still_consults_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is `no_playbook`, not `None`. A first run against a fresh
    store has learned nothing, and that is a real configuration rather than an
    absent argument."""
    seen: dict[str, object] = {}

    def fake_resolve_auth(_root: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise ReachedAuthError

    monkeypatch.setattr(compose, "fingerprint", lambda _root: _a_fingerprint())
    monkeypatch.setattr(compose, "anchor_for", lambda _root: _an_anchor())
    monkeypatch.setattr(compose, "interpreter_for", lambda _root: None)
    # **The enumerator is the registry's now, not this module's. S-14.6.**
    # `compose` asks `grounds_for(...)` and calls what comes back, so patching
    # the name here would patch something that is no longer read.
    monkeypatch.setattr(
        compose,
        "grounds_for",
        lambda _framework: Grounds(
            framework="Django",
            enumerate_entry_points=lambda _r, **_k: _an_enumeration(),
            predicates=grounds_for("Django").predicates,  # type: ignore[union-attr]
        ),
    )
    monkeypatch.setattr(compose, "resolve_auth", fake_resolve_auth)

    with pytest.raises(ReachedAuthError):
        compose.ground_workload(
            _a_fingerprint().root,
            python=["python"],
            request=lambda path: pytest.fail(f"nothing should be requested: {path}"),
            plan=compose.Plan(workload_id="w", description="d"),
            reset=_never_used(),
        )

    consulted = cast(PlaybookLookup, seen["playbook"])
    assert consulted is not None
    assert consulted("Django/5") == (), "consulted, and it knows nothing"


# ==================================================== the least fixture that reaches the join


def _a_fingerprint() -> Fingerprint:
    """A Django 5 project, which keys as `Django/5`."""
    return Fingerprint(
        root=Path("/tmp/subject"),
        framework=Detected(Framework.DJANGO, "manage.py"),
        declared_version=Detected(">=5.0", "pyproject.toml"),
        orm=None,
        database=None,
        test_runner=None,
    )


def _an_anchor() -> Anchor:
    return Anchor(on=date(2024, 5, 6), commit="abc1234", reason="the most recent commit")


def _an_enumeration() -> Enumeration:
    """One resolved route, so `drivable` is non-empty and has a request path.

    Resolved rather than parsed, because a parsed route carries no address —
    `drivable` filters on exactly that, and a parsed one would make this test
    stop at the wrong refusal.
    """
    candidate = Candidate(
        kind=Kind.HTTP_ROUTE,
        name="books/",
        evidence="django.urls resolver",
        discovery=Discovery.RESOLVED,
    )
    return Enumeration(
        root=Path("/tmp/subject"),
        scored=(Scored(candidate=candidate, score=4, reasons=("addresses a set",)),),
        unexpanded=(),
        resolution=Resolution(available=True),
    )


def _never_used() -> VerifiedReset:
    """A reset proof the test never reaches: it stops at `resolve_auth`, which is
    six stages before emission."""
    return cast(VerifiedReset, object())


# ==================================================== S-13.2 — what may be believed


def standing(*, succeeded_on: Sequence[str] = (), failures: int = 0) -> Standing:
    return Standing(entry=an_entry(), succeeded_on=frozenset(succeeded_on), failures=failures)


def test_a_new_entry_is_provisional() -> None:
    """**F4's first point.** Written is not earned — *the Explorer writes playbook
    entries that all future runs trust*, and nothing validated a write."""
    assert standing().status is Status.PROVISIONAL
    assert not standing().trusted


def test_promotion_counts_projects_and_not_uses() -> None:
    """**Fifty successes on one project is one project's opinion.**

    F15 reaches the same conclusion from the trust-ledger side: *a select_related
    fix approved 50 times may have been on projects with narrow tables.* Trust
    learned in one place is context, not authority.
    """
    same_project_repeatedly = standing(succeeded_on=["shop"] * 9)

    assert same_project_repeatedly.status is Status.PROVISIONAL


def test_enough_different_projects_promotes() -> None:
    promoted = standing(succeeded_on=["shop", "blog", "billing"])

    assert promoted.status is Status.TRUSTED
    assert len(promoted.succeeded_on) == PROMOTION_THRESHOLD


def test_one_short_is_still_provisional() -> None:
    """The boundary, from below. Without this the threshold could be anything
    less than three and the test above would still pass."""
    assert standing(succeeded_on=["shop", "blog"]).status is Status.PROVISIONAL


def test_two_failures_quarantine() -> None:
    """F4's third point, verbatim: *an entry that fails twice is demoted and
    quarantined.*"""
    assert standing(failures=DEMOTION_THRESHOLD).status is Status.QUARANTINED


def test_a_quarantined_entry_cannot_be_outvoted_by_successes() -> None:
    """**The order of the checks is the safety property.**

    F4's remedy for a poisoned entry is that it stops being offered. A rule where
    successes outweighed failures would let a widely-repeated mistake earn its
    way back — which is the compounding F4 names.
    """
    popular_and_broken = standing(succeeded_on=["a", "b", "c", "d", "e"], failures=2)

    assert popular_and_broken.status is Status.QUARANTINED
    assert not popular_and_broken.trusted


def test_trust_is_strictly_harder_to_reach_than_quarantine() -> None:
    """Otherwise an entry with two successes and two failures is both at once,
    and which one wins is decided by the order of two `if`s rather than by a
    reason."""
    assert PROMOTION_THRESHOLD > DEMOTION_THRESHOLD


# ==================================================== S-13.6 — writing one down


def test_what_the_auth_stage_learned_becomes_an_entry() -> None:
    """**F4's own poison, written down properly.** *"DRF always uses
    TokenAuthentication"* is a claim about what a project of a kind requires, so
    the situation is what this route turned out to require."""
    entry = learned_from_auth(requirement="TOKEN", credential="TOKEN", resolved=True)

    assert "required TOKEN" in entry.situation
    assert "minted a credential" in entry.action
    assert "could then be requested" in entry.outcome


def test_a_failed_resolution_is_worth_recording() -> None:
    """The outcome is what happened, not whether it was right. The next project
    of this kind learns that this approach did not work here, which is half of
    what a playbook is for."""
    entry = learned_from_auth(requirement="UNKNOWN", credential=None, resolved=False)

    assert "stayed unreachable" in entry.outcome
    assert "none was made" in entry.action


def test_grounding_records_what_the_auth_stage_learned(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The join.** S-13.1 shipped no production writer on purpose and S-13.2
    built the gate; this is the call that was still missing, and dropping `learn`
    from `ground_workload` is the sabotage it fails on.

    Stops after the auth stage, which is where the entry is written — a run that
    fails later has still learned whether this kind of project needs a credential.
    """
    written: list[tuple[str, PlaybookEntry]] = []

    def file(key: str, entry: PlaybookEntry) -> None:
        """**Not `written.append`, and S-6.3's note is why.** `PlaybookWriter`
        declares *named* parameters and `list.append`'s is positional-only, so
        the obvious one-liner does not satisfy the protocol. Fourth place in
        this project that trap has been hit.

        **The key is a parameter now, which is S-13.7's correction.** It is
        `Fingerprint.playbook_key()`, derived inside the sequence — so binding one
        into the writer meant fingerprinting the repository twice, and nothing
        ever did.
        """
        written.append((key, entry))

    def fake_resolve_auth(_root: object, **_kwargs: object) -> object:
        return _a_resolution()

    monkeypatch.setattr(compose, "fingerprint", lambda _root: _a_fingerprint())
    monkeypatch.setattr(compose, "anchor_for", lambda _root: _an_anchor())
    monkeypatch.setattr(compose, "interpreter_for", lambda _root: None)
    # **The enumerator is the registry's now, not this module's. S-14.6.**
    # `compose` asks `grounds_for(...)` and calls what comes back, so patching
    # the name here would patch something that is no longer read.
    monkeypatch.setattr(
        compose,
        "grounds_for",
        lambda _framework: Grounds(
            framework="Django",
            enumerate_entry_points=lambda _r, **_k: _an_enumeration(),
            predicates=grounds_for("Django").predicates,  # type: ignore[union-attr]
        ),
    )
    monkeypatch.setattr(compose, "resolve_auth", fake_resolve_auth)
    monkeypatch.setattr(compose, "carried", lambda *_a: (_ for _ in ()).throw(ReachedAuthError))

    with pytest.raises(ReachedAuthError):
        compose.ground_workload(
            _a_fingerprint().root,
            python=["python"],
            request=lambda path: pytest.fail(f"nothing should be requested: {path}"),
            plan=compose.Plan(workload_id="w", description="d"),
            reset=_never_used(),
            learn=file,
        )

    assert len(written) == 1, "grounding filed what it learned"
    assert "required NONE" in written[0][1].situation
    assert written[0][0] == _a_fingerprint().playbook_key(), "under the fingerprint's own key"


def _a_resolution() -> AuthResolution:
    """A route that needed nothing, resolved. The simplest real shape."""
    return AuthResolution(
        profile=AuthProfile(
            settings_module=Detected("config.settings", "manage.py"),
            declared=(),
            user_model=None,
            login_url=None,
            session_cookie_name="sessionid",
        ),
        requirement=Requirement(
            path="/books/",
            scheme=Scheme.NONE,
            established=Established.OBSERVED,
        ),
        credential=None,
    )

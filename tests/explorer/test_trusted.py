"""S-13.7 — acting on a playbook entry, and the four ways it refuses to.

Entries have been written since S-13.6, promoted since S-13.2 and read by nothing.
This is the story that lets one change an outcome, which makes it the story where
F4's poison — *a wrong entry propagates silently to all future runs and compounds*
— finally has somewhere to propagate to. So most of this file is about the
refusals rather than the action.

**The action is narrow on purpose and the tests pin the boundary from both
sides.** A route that answered `401` without naming a scheme is a gap: something
is enforcing authentication, `UNKNOWN` is not mintable, and today that repository
simply does not ground. Everything else is a measurement of *this* route, and a
prior about projects of its kind may not overrule one.

`read_profile` and `mint` are replaced here. Both are subprocesses against a real
Django project and both have their own slow tests in `test_auth.py`; what this
story owns is the *decision*, and running a subprocess to observe a branch would
make the boundary tests slow enough that nobody would add the next one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from coldfix.explorer import auth as auth_module
from coldfix.explorer import compose
from coldfix.explorer.anchor import Anchor
from coldfix.explorer.auth import (
    AuthProfile,
    Credential,
    Established,
    Recipe,
    Reply,
    Requirement,
    Resolution,
    Scheme,
    actionable,
    no_trusted,
    resolve_auth,
)
from coldfix.explorer.entrypoints import Candidate, Discovery, Enumeration, Kind, Scored
from coldfix.explorer.entrypoints import Resolution as RouteResolution
from coldfix.explorer.fingerprint import Detected, Fingerprint, Framework
from coldfix.explorer.playbook import (
    PlaybookEntry,
    learned_from_auth,
    remembered_requirement,
)
from coldfix.explorer.work import WorkVerificationError
from coldfix.sandbox.verification import VerifiedReset

KEY = "Django/5"


# ================================================ the reader, and the sentence it reads


def test_every_scheme_survives_the_round_trip_through_an_entry() -> None:
    """**One owner for the sentence.** `learned_from_auth` writes it and
    `remembered_requirement` reads it back, and a template edited on one side
    without the other is how a memory silently stops being actionable — the entry
    would still be trusted, still be offered, and never do anything."""
    for scheme in Scheme:
        entry = learned_from_auth(requirement=scheme.name, credential=None, resolved=True)

        assert remembered_requirement(entry) == scheme.name


def test_an_entry_recording_a_failure_is_not_actionable() -> None:
    """*The route stayed unreachable* is worth remembering and is not an
    instruction. Acting on one repeats a failure somebody already paid for."""
    failed = learned_from_auth(requirement="TOKEN", credential="TOKEN", resolved=False)

    assert remembered_requirement(failed) is None
    assert "TOKEN" in failed.situation, "and the entry still says what it learned"


def test_an_entry_some_other_stage_wrote_is_not_actionable() -> None:
    """A playbook is not only about auth. An entry about seeding is a perfectly
    good entry and says nothing about what a route requires."""
    other = PlaybookEntry(
        situation="the project ships factory_boy",
        action="seeded through the repository's own factories",
        outcome="the sweep ran at both scales",
    )

    assert remembered_requirement(other) is None


# ================================================ what may be acted on


def remembers(scheme: str, *, resolved: bool = True) -> PlaybookEntry:
    return learned_from_auth(requirement=scheme, credential=scheme, resolved=resolved)


def test_one_trusted_entry_naming_a_mintable_scheme_is_acted_on() -> None:
    entry = remembers("TOKEN")

    found = actionable([entry])

    assert found == (entry, Scheme.TOKEN)


def test_two_trusted_entries_naming_different_schemes_are_refused() -> None:
    """**No tie-break, and that is the design.** Each was earned on three
    different projects, so a disagreement is evidence that the fingerprint does
    not determine the answer. Picking one is the alphabetical tie-break that
    seeded a hundred authors and drove the wrong route at S-7.13."""
    assert actionable([remembers("TOKEN"), remembers("SESSION")]) is None


def test_two_trusted_entries_agreeing_are_not_a_disagreement() -> None:
    """The control. Refusing on *more than one entry* rather than on *more than
    one scheme* would refuse the ordinary case of a lesson learned twice."""
    found = actionable([remembers("TOKEN"), remembers("TOKEN")])

    assert found is not None
    assert found[1] is Scheme.TOKEN


def test_a_scheme_that_cannot_be_minted_is_not_acted_on() -> None:
    """`JWT` is detectable and not mintable, which S-7.4 records as deliberate.
    Acting on it would decide to mint something `mint` refuses to make, one call
    later and with the reason lost."""
    assert not Scheme.JWT.can_be_minted
    assert actionable([remembers("JWT")]) is None


def test_nothing_is_acted_on_for_a_route_that_needs_nothing() -> None:
    """`NONE` is a real answer and there is nothing to do about it."""
    assert actionable([remembers("NONE")]) is None


def test_a_name_that_is_not_a_scheme_is_not_acted_on() -> None:
    """An entry edited by hand, or written by a version that spelled schemes
    differently. `Scheme` is a `StrEnum` over sentences, so a lookup by value
    would not find `TOKEN` either — the table is by name, and a miss is a refusal
    rather than an exception."""
    assert actionable([remembers("SUPERUSER_COOKIE")]) is None


def test_an_empty_trusted_list_acts_on_nothing() -> None:
    """Every first run, and most runs after it."""
    assert actionable(()) is None
    assert no_trusted(KEY) == ()


# ================================================ the one place a memory decides


def a_profile() -> AuthProfile:
    return AuthProfile(
        settings_module=Detected("config.settings", "manage.py"),
        declared=(),
        user_model=None,
        login_url=None,
        session_cookie_name="sessionid",
    )


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> list[Scheme]:
    """Replace the two subprocesses, recording which scheme was minted.

    Both have their own slow tests against a real Django project. What is
    observable here is the decision: *which* credential this stage went and made.
    """
    asked: list[Scheme] = []

    def fake_mint(_root: object, *, scheme: Scheme, **_kwargs: object) -> Credential:
        asked.append(scheme)
        return Credential(
            scheme=scheme,
            recipe=Recipe(scheme=scheme, username="u", password="p"),
            headers={"Authorization": "Token abc"},
        )

    monkeypatch.setattr(auth_module, "read_profile", lambda *_a, **_k: a_profile())
    monkeypatch.setattr(auth_module, "mint", fake_mint)
    return asked


def answers(status: int, headers: Mapping[str, str] | None = None) -> object:
    def request(path: str) -> Reply:
        del path
        return Reply(status=status, headers=dict(headers or {}))

    return request


def resolve(answer: object, trusted: Sequence[PlaybookEntry] = ()) -> Resolution:
    return resolve_auth(
        Path("/repo"),
        python=["python"],
        path="/books/",
        request=answer,  # type: ignore[arg-type]
        trusted_entries=lambda key: list(trusted) if key == KEY else [],
        playbook_key=KEY,
    )


UNNAMED_401 = 401
"""A route saying *something is enforcing authentication* and refusing to say
what. The gap, and the only place a memory is allowed to fill one in."""


def test_the_same_repository_resolves_differently_with_a_trusted_entry(
    minted: list[Scheme],
) -> None:
    """**AC 4, both halves in one test.** Without the entry this route reports
    `UNKNOWN`, which is not mintable — so no credential is made, the resolution is
    unresolved, and `ground_workload` refuses the repository. With it, the same
    `401` produces a token credential and the route becomes drivable.

    Two calls rather than two tests, because the claim is a *difference* and a
    difference asserted across two fixtures is a claim about the fixtures.
    """
    without = resolve(answers(UNNAMED_401))
    with_entry = resolve(answers(UNNAMED_401), [remembers("TOKEN")])

    assert without.requirement.scheme is Scheme.UNKNOWN
    assert without.credential is None
    assert not without.resolved
    assert without.acted_on is None

    assert with_entry.requirement.scheme is Scheme.TOKEN
    assert with_entry.credential is not None
    assert with_entry.resolved
    assert with_entry.acted_on is not None
    assert minted == [Scheme.TOKEN], "and the memory is what said which"


def test_acting_on_a_memory_is_recorded_as_weaker_than_a_measurement(
    minted: list[Scheme],
) -> None:
    """`Established` never merges what settled a requirement. A report that
    showed a remembered scheme as `OBSERVED` would put a prior and a measurement
    in the same column, which is the rule S-7.3 set and S-7.4 inherited."""
    resolution = resolve(answers(UNNAMED_401), [remembers("TOKEN")])

    assert resolution.requirement.established is Established.REMEMBERED
    assert resolution.requirement.observation is not None, "the answer is still carried"
    assert "acted on a trusted entry" in resolution.describe()
    del minted


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (200, {}, Scheme.NONE),
        (403, {}, Scheme.SESSION),
        (401, {"WWW-Authenticate": "Basic realm=x"}, Scheme.BASIC),
    ],
)
def test_a_memory_never_overrules_a_route_that_answered(
    minted: list[Scheme], status: int, headers: Mapping[str, str], expected: Scheme
) -> None:
    """**The safety boundary, from the other side.** A trusted entry says what
    projects *of this kind* required; the observation measures what *this* route
    demands. A route that answers is the stronger evidence in every case, and
    `Requirement`'s own docstring says so — the settings are the weaker source for
    exactly this reason and a memory is weaker still."""
    resolution = resolve(answers(status, headers), [remembers("TOKEN")])

    assert resolution.requirement.scheme is expected
    assert resolution.requirement.established is Established.OBSERVED
    assert resolution.acted_on is None
    assert minted in ([], [expected])


def test_a_route_that_said_nothing_about_authentication_is_not_acted_on(
    minted: list[Scheme],
) -> None:
    """A `404` is not a refusal to authenticate. S-7.3 emits routes with path
    parameters and requesting `books/<int:pk>/` literally returns one, so the
    Explorer's next move is a different path — not a user nobody asked for, minted
    into a subject on the strength of a memory about a route that may not exist.
    """
    resolution = resolve(answers(404), [remembers("TOKEN")])

    assert resolution.requirement.inconclusive
    assert resolution.acted_on is None
    assert minted == []


def test_the_context_list_is_never_the_one_acted_on(minted: list[Scheme]) -> None:
    """**AC 2, and the distinction this story rests on.** `recall` returns
    provisional entries and is what the Explorer is *shown*; `trusted` is the only
    list a decision may rest on. Handing the same entry through `playbook=`
    changes nothing at all."""
    resolution = resolve_auth(
        Path("/repo"),
        python=["python"],
        path="/books/",
        request=answers(UNNAMED_401),  # type: ignore[arg-type]
        playbook=lambda key: [dict(remembers("TOKEN").model_dump(mode="json"))] if key else [],
        playbook_key=KEY,
    )

    assert len(resolution.playbook_entries) == 1, "it was shown the entry"
    assert resolution.acted_on is None, "and did not act on it"
    assert resolution.requirement.scheme is Scheme.UNKNOWN
    assert minted == []


# ================================================ the joins: compose, and the node


def a_fingerprint() -> Fingerprint:
    """A Django 5 project, which keys as `Django/5`."""
    return Fingerprint(
        root=Path("/tmp/subject"),
        framework=Detected(Framework.DJANGO, "manage.py"),
        declared_version=Detected(">=5.0", "pyproject.toml"),
        orm=None,
        database=None,
        test_runner=None,
    )


def an_anchor() -> Anchor:
    return Anchor(on=date(2024, 5, 6), commit="abc1234", reason="the most recent commit")


def an_enumeration() -> Enumeration:
    """One resolved route, so `drivable` is non-empty and carries an address."""
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
        resolution=RouteResolution(available=True),
    )


def resolution_that(acted_on: PlaybookEntry | None) -> Resolution:
    return Resolution(
        profile=a_profile(),
        requirement=Requirement(
            path="/books/",
            scheme=Scheme.TOKEN if acted_on else Scheme.NONE,
            established=Established.REMEMBERED if acted_on else Established.OBSERVED,
        ),
        credential=(
            Credential(
                scheme=Scheme.TOKEN,
                recipe=Recipe(scheme=Scheme.TOKEN, username="u", password="p"),
                headers={"Authorization": "Token abc"},
            )
            if acted_on
            else None
        ),
        acted_on=acted_on,
    )


def wire_sequence(
    monkeypatch: pytest.MonkeyPatch,
    resolution: Resolution,
    *,
    verifies: bool,
    recorded: list[tuple[str, PlaybookEntry, bool]],
) -> None:
    """Drive `ground_workload` to where a use is recorded, and no further.

    Everything expensive is replaced and the sequence's own wiring is left alone,
    which is `wire_repair`'s construction in `test_adapters.py`: what is under
    test is what the composition *passes*, not what the stages do with it.
    """

    def used(key: str, entry: PlaybookEntry, *, worked: bool) -> None:
        recorded.append((key, entry, worked))

    def fake_verify(*_a: object, **_k: object) -> object:
        if verifies:
            return object()
        message = "the endpoint answered 403 at both scales"
        raise WorkVerificationError(message)

    monkeypatch.setattr(compose, "fingerprint", lambda _root: a_fingerprint())
    monkeypatch.setattr(compose, "anchor_for", lambda _root: an_anchor())
    monkeypatch.setattr(compose, "interpreter_for", lambda _root: None)
    monkeypatch.setattr(compose, "enumerate_entry_points", lambda _r, **_k: an_enumeration())
    monkeypatch.setattr(compose, "resolve_auth", lambda _root, **_k: resolution)
    monkeypatch.setattr(compose, "verify_work", fake_verify)
    monkeypatch.setattr(compose, "emit", lambda *_a, **_k: object())
    monkeypatch.setattr(compose, "evaluate", lambda *_a, **_k: object())
    monkeypatch.setattr(compose, "carried", lambda *_a: (None, None))

    compose.ground_workload(
        Path("/tmp/subject"),
        python=["python"],
        request=lambda path: pytest.fail(f"nothing should be requested: {path}"),
        plan=compose.Plan(workload_id="w", description="d"),
        reset=cast(VerifiedReset, object()),
        used=used,
    )


def test_a_workload_that_verifies_records_the_entry_as_having_worked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Recorded where the answer is.** A mint that succeeds says a user exists;
    it does not say the route accepted the credential. What settles that is the
    workload being driven, and that happens in the composition."""
    entry = remembers("TOKEN")
    recorded: list[tuple[str, PlaybookEntry, bool]] = []

    wire_sequence(monkeypatch, resolution_that(entry), verifies=True, recorded=recorded)

    assert recorded == [(KEY, entry, True)]


def test_a_workload_that_will_not_verify_records_the_entry_as_having_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The demotion path, and the error still travels.** F4's poisoned entry
    mints perfectly well in a session-authenticated project and then gets a 403 on
    every request; `verify_work` refuses to measure an error response, so that
    arrives here as exactly this. Recording nothing would leave the entry with its
    three successes and no way to lose them."""
    entry = remembers("TOKEN")
    recorded: list[tuple[str, PlaybookEntry, bool]] = []

    with pytest.raises(WorkVerificationError):
        wire_sequence(monkeypatch, resolution_that(entry), verifies=False, recorded=recorded)

    assert recorded == [(KEY, entry, False)], "the failure is what demotes it"


def test_a_run_that_acted_on_nothing_records_no_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no *use* of an entry that was never read, and inventing one would
    put a success against whichever entry happened to be first."""
    recorded: list[tuple[str, PlaybookEntry, bool]] = []

    wire_sequence(monkeypatch, resolution_that(None), verifies=True, recorded=recorded)

    assert recorded == []


def test_the_sequence_hands_the_trusted_lookup_to_the_auth_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The join, held at both ends.** S-13.1's seam sat empty for two stories
    because nothing passed it; the sabotage here is dropping `trusted_entries=`
    from `ground_workload`'s call to `resolve_auth`, and this is what fails."""
    seen: dict[str, object] = {}

    def fake_resolve_auth(_root: object, **kwargs: object) -> Resolution:
        seen.update(kwargs)
        return resolution_that(None)

    monkeypatch.setattr(compose, "fingerprint", lambda _root: a_fingerprint())
    monkeypatch.setattr(compose, "anchor_for", lambda _root: an_anchor())
    monkeypatch.setattr(compose, "interpreter_for", lambda _root: None)
    monkeypatch.setattr(compose, "enumerate_entry_points", lambda _r, **_k: an_enumeration())
    monkeypatch.setattr(compose, "resolve_auth", fake_resolve_auth)
    monkeypatch.setattr(compose, "verify_work", lambda *_a, **_k: object())
    monkeypatch.setattr(compose, "emit", lambda *_a, **_k: object())
    monkeypatch.setattr(compose, "evaluate", lambda *_a, **_k: object())
    monkeypatch.setattr(compose, "carried", lambda *_a: (None, None))

    compose.ground_workload(
        Path("/tmp/subject"),
        python=["python"],
        request=lambda path: pytest.fail(f"nothing should be requested: {path}"),
        plan=compose.Plan(workload_id="w", description="d"),
        reset=cast(VerifiedReset, object()),
        trusted_entries=lambda key: [remembers("TOKEN")] if key == KEY else [],
    )

    lookup = seen["trusted_entries"]
    assert callable(lookup)
    assert list(lookup(KEY)) == [remembers("TOKEN")], "the sequence passed the real lookup"
    assert seen["playbook_key"] == KEY, "under the fingerprint's own key"

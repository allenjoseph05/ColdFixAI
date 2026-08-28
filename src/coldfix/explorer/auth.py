"""Getting past the front door, and knowing which door it was.

Epic 7, S-7.4. S-7.3 produced a ranked list of routes; most of the interesting
ones answer `401`, `403` or a redirect until something authenticates. ADR 009
makes this a stage of its own, whose predicate is *a credential was created and a
protected route answered with it*.

**Nothing here calls a model.** Reading `settings.REST_FRAMEWORK` is a
subprocess, base64 is a function, and deciding whether `401` means *authenticate*
is a table. `CLAUDE.md` is explicit that none of them may be replaced by a model
call.

**A declared scheme is not an enforced one.** This is S-7.1's finding and S-7.3's
finding a third time, and AC 1 names both halves of it in one line — *detects the
auth scheme from settings **and** failed-request responses*. Settings say what is
*available*: `DEFAULT_AUTHENTICATION_CLASSES` lists what DRF will accept, and
`AUTHENTICATION_BACKENDS` lists what can verify a password. Neither says what any
particular route *requires* — a view can be `AllowAny` inside a project whose
defaults demand a token, and a project with no auth settings at all can wrap one
view in `login_required`. Only a request establishes what a route enforces, so
the two sources are kept apart and every requirement records which one settled it.

**Credentials are minted, not negotiated.** The obvious way to obtain a session
is to drive the login form, and it is the wrong way: it means fetching a page,
finding the CSRF token, guessing what the username field is called, and giving up
the moment the project has two-factor or a third-party identity provider. Django
itself does not do that in its own test client — `force_login` writes a session
row and hands back a cookie — and this does the same thing in the subject's
interpreter. **A login flow is a flow; a session is a row.**

**A credential is a fixture, not a setup step.** S-7.8 drives a workload at N=10
and again at N=100, and S-2.6 resets the database between them. A reset destroys
the user, which destroys the session row, which turns the second half of a scaling
sweep into a wall of `401`s that reads as *the endpoint failed at scale*. So a
`Credential` carries the `Recipe` that made it and minting is idempotent: the
credential is re-established after every reset rather than captured once.

**A followed redirect hides the wall entirely.** `login_required` answers `302`
to `LOGIN_URL`, and a client with redirects enabled turns that into a `200` that
is the login page. Nothing in the status, the headers or the body distinguishes it
from the endpoint answering — so the Explorer would ground itself on a login form
and S-7.8 would correctly report that its bytes do not grow with the data. `Reply`
therefore carries the path that actually answered, and a `200` from a path other
than the one requested is never read as *no authentication required*.

**The playbook is consulted before the first request, and there are two lists.**
S-7.4 could only carry entries unread: S-13.1 owned what one means and S-13.2
owned the gate that makes trusting one safe, and reading them here would have been
this stage deciding both. Both now exist, so S-13.7 added the second list.
`playbook` is *context* — it holds provisional entries, it is what the Explorer is
shown, and `Resolution` still carries it unread. `trusted_entries` holds only what
three different projects recorded a successful use of, and it is the only one a
decision may rest on.

**A memory may fill a gap and may never overrule a measurement.** The one place
it decides anything is a route that answered `401` without naming a scheme:
something is enforcing authentication, `UNKNOWN` is not mintable, and today that
repository simply does not ground. Every other verdict is a measurement of *this*
route, and a prior about projects of its kind does not get to override it.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from coldfix.bench.execute import ExecutionError
from coldfix.explorer.entrypoints import settings_module
from coldfix.explorer.fingerprint import Detected
from coldfix.explorer.playbook import PlaybookEntry, remembered_requirement, trusted
from coldfix.explorer.surface import HostSurface, Surface
from coldfix.state.persistent import Collection, PersistentStore

PROFILE_TIMEOUT_SECONDS = 120.0
"""The same budget S-7.3 gives resolution, and for the same reason: reading the
auth settings means `django.setup()`, which imports every installed application."""

MINT_TIMEOUT_SECONDS = 120.0
"""Minting additionally touches the database, so it inherits the budget rather
than shortening it — a first write against a cold Postgres is not a fast one."""

# HTTP status codes, named because a bare 401 in a comparison is the kind of
# thing a reader has to look up to check.
_UNAUTHORIZED = 401
_FORBIDDEN = 403
_REDIRECT_LOW = 300
_REDIRECT_HIGH = 399
_OK_LOW = 200
_OK_HIGH = 299

# The marker S-7.3 established. A subject's `django.setup()` may print — a
# deprecation warning, an application banner — and `json.loads(stdout)` on such a
# project fails on output that is not an error.
_MARKER = "<<<COLDFIX-AUTH>>>"


class AuthError(Exception):
    """The auth scheme could not be resolved, or a credential could not be made."""


class Scheme(StrEnum):
    """How a route says who you are.

    `NONE` is a genuine answer and the best one: a route needing no credential is
    a route the Explorer can drive immediately, and manufacturing a user for it
    would be work that buys nothing.

    `UNKNOWN` is not a failure either. Something is enforcing authentication and
    nothing here could name it — which sends a reader to the response, whereas
    guessing `SESSION` would send them to mint a cookie the subject ignores.
    """

    NONE = "no credential is required"
    SESSION = "a session cookie, as Django's own auth writes it"
    TOKEN = "a token in an Authorization header, as DRF's TokenAuthentication reads it"
    JWT = "a bearer token"
    BASIC = "HTTP Basic, as RFC 7617 defines it"
    UNKNOWN = "something is enforcing authentication and nothing here named it"

    @property
    def can_be_minted(self) -> bool:
        """Whether `mint` can produce this credential.

        `JWT` is detectable and not mintable, and that asymmetry is deliberate
        rather than an omission — see `mint`.
        """
        return self in (Scheme.SESSION, Scheme.TOKEN, Scheme.BASIC)


class Established(StrEnum):
    """What settled a requirement, which is how much it is worth.

    Never merged into one field. A declaration is a statement about what the
    project *offers* and an observation is a measurement of what a route
    *demands*, and a report that flattened them would put a guess and a
    measurement in the same column — S-7.3's rule, one story later.
    """

    DECLARED = "configured in the subject's settings"
    OBSERVED = "established by making a request and reading the answer"
    REMEMBERED = "carried over from a playbook entry three other projects earned"
    """S-13.7. **The weakest of the three, and it may only fill a gap.**

    A declaration describes what the project offers and an observation measures
    what a route demands; this is neither — it is what projects *of this kind*
    demanded, which is a prior and not a fact about this one. So it never
    overrides an observation that established a scheme, and `resolve_auth` reaches
    for it in exactly one situation: the route said a credential is needed and
    would not say which.
    """


@dataclass(frozen=True)
class Reply:
    """What came back from one request, in the little of it that matters here.

    Deliberately not an HTTP client. Nothing under `src/` may reach the network
    on its own account, and what drives the subject is a fact about the sandbox
    that S-2.1 owns — so the caller makes the request and reports it, the
    convention S-7.2 set for commands and S-7.3 for interpreters.

    `answered_path` is the load-bearing field. A client that follows redirects
    turns `login_required`'s `302` into a `200` holding a login page, and there
    is nothing in the status or the headers to tell that apart from the endpoint
    answering. Reporting which path produced the response is a fact every HTTP
    client has (`requests` in `response.url`, Django's test client in
    `redirect_chain`) and is the only thing that makes the difference visible.
    """

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    answered_path: str | None = None
    """Where the response came from, when the client followed a redirect to get
    it. `None` means it came from the path that was requested."""

    def header(self, name: str) -> str | None:
        """Case-insensitively, because HTTP header names are."""
        wanted = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == wanted), None)


@dataclass(frozen=True)
class Observation:
    """One probe of one route, and what its answer establishes.

    Carries the reply rather than only a conclusion: the classification below is
    an inference over three fields, and a reader who disagrees with it needs the
    fields.
    """

    path: str
    reply: Reply

    @property
    def redirected_away(self) -> bool:
        """Whether this answer describes a different resource than the one asked for.

        Either the reply *is* a redirect, or the client followed one and said so.
        Both mean the same thing to the caller: whatever answered, it was not
        this route.
        """
        if _REDIRECT_LOW <= self.reply.status <= _REDIRECT_HIGH:
            return True
        answered = self.reply.answered_path
        return answered is not None and answered.rstrip("/") != self.path.rstrip("/")

    @property
    def speaks_to_auth(self) -> bool:
        """Whether this answer is about authentication at all.

        A `404` is not a refusal to authenticate and a `500` is not one either.
        Both fall through to `UNKNOWN` below, which is the right *scheme* — no
        scheme was named — but `UNKNOWN`'s sentence is *something is enforcing
        authentication*, and that sentence is false about a route that is not
        there. Keeping the two apart is what stops a report telling a reader to
        go looking for a credential.

        Not a hypothetical: S-7.3 emits routes with path parameters, and
        requesting `books/<int:pk>/` literally returns `404`.
        """
        return (
            self.redirected_away
            or self.reply.status in (_UNAUTHORIZED, _FORBIDDEN)
            or _OK_LOW <= self.reply.status <= _OK_HIGH
        )

    @property
    def scheme(self) -> Scheme:
        """What this answer says the route requires.

        | Answer | Reading |
        |---|---|
        | `401` with `WWW-Authenticate` | the server named the scheme itself |
        | `401` without | something wants a credential and would not say which |
        | `403` | DRF answers this when it cannot offer a challenge — a session |
        | `3xx`, or a followed redirect | a login flow, which is a session |
        | `2xx` from the path asked for | nothing is required |

        **`403` reads as `SESSION` rather than as unknown** because that is what
        produces it in practice: DRF returns `403` instead of `401` whenever no
        authenticator can supply a `WWW-Authenticate` header, and
        `SessionAuthentication` is the one that cannot. It is a weaker reading
        than the others, which is why `Requirement` keeps the observation.
        """
        if self.redirected_away:
            return Scheme.SESSION
        if self.reply.status == _UNAUTHORIZED:
            challenge = self.reply.header("WWW-Authenticate")
            return _scheme_of_challenge(challenge) if challenge else Scheme.UNKNOWN
        if self.reply.status == _FORBIDDEN:
            return Scheme.SESSION
        if _OK_LOW <= self.reply.status <= _OK_HIGH:
            return Scheme.NONE
        return Scheme.UNKNOWN

    def describe(self) -> str:
        challenge = self.reply.header("WWW-Authenticate")
        parts = [f"{self.path} answered {self.reply.status}"]
        if not self.speaks_to_auth:
            parts.append(
                "which says nothing about authentication — the route may not be there at that "
                "path, or it failed for its own reasons"
            )
        if challenge:
            parts.append(f"challenge {challenge!r}")
        if self.reply.answered_path and self.reply.answered_path != self.path:
            parts.append(f"but the answer came from {self.reply.answered_path}")
        location = self.reply.header("Location")
        if location:
            parts.append(f"redirecting to {location}")
        return ", ".join(parts)


def _scheme_of_challenge(challenge: str) -> Scheme:
    """Which scheme a `WWW-Authenticate` header names.

    The token is the first word of the header, per RFC 7235, and the comparison
    is case-insensitive because the grammar says so — `Basic`, `basic` and
    `BASIC` are one scheme, and a server that spells it the third way is not
    using a different one.
    """
    token = challenge.strip().split()[0].lower() if challenge.strip() else ""
    return {
        "basic": Scheme.BASIC,
        "bearer": Scheme.JWT,
        "token": Scheme.TOKEN,
    }.get(token, Scheme.UNKNOWN)


@dataclass(frozen=True)
class UserModel:
    """The subject's own user model, as the subject reports it.

    AC 3 is the reason this is asked rather than assumed. `create_user(username=…)`
    raises `TypeError` against a model whose `USERNAME_FIELD` is `email`, and a
    project that swapped it has not done anything unusual — Django documents
    `AUTH_USER_MODEL` as the expected path and a great many projects take it.
    """

    label: str
    username_field: str
    required_fields: tuple[str, ...]
    creates_users: bool
    """Whether the default manager offers `create_user`. A model swapped in
    without a manager derived from `BaseUserManager` has no supported way to make
    a user with a usable password, and guessing at `Model(**fields).save()` would
    write a row whose password hash nothing can authenticate against."""

    def describe(self) -> str:
        required = ", ".join(self.required_fields) or "nothing else"
        return f"{self.label} identified by {self.username_field}, also requiring {required}"


@dataclass(frozen=True)
class AuthProfile:
    """What the subject's settings declare about authentication.

    Every field is a *declaration*. None of it establishes what any route
    enforces, and the type says so by carrying `declared` as `Detected` values
    naming the setting each came from.
    """

    settings_module: Detected[str]
    declared: tuple[Detected[Scheme], ...]
    user_model: UserModel | None
    login_url: str | None
    session_cookie_name: str
    problems: tuple[str, ...] = ()

    @property
    def declared_schemes(self) -> tuple[Scheme, ...]:
        return tuple(dict.fromkeys(entry.value for entry in self.declared))

    def describe(self) -> str:
        lines = [f"Auth declared by {self.settings_module.describe()}"]
        lines.extend(f"  offers {entry.describe()}" for entry in self.declared)
        if not self.declared:
            lines.append("  no authentication scheme is configured in settings")
        lines.append(
            f"  user model: {self.user_model.describe() if self.user_model else 'not determined'}"
        )
        if self.login_url:
            lines.append(f"  login flow at {self.login_url}")
        lines.extend(f"  could not read {problem}" for problem in self.problems)
        lines.append(
            "  These are the schemes the project *offers*. What any route requires is a "
            "different fact, established by requesting it."
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class Requirement:
    """What one route requires, and what settled it.

    The observation wins where there is one. A route that answers `200` in a
    project whose settings demand a token requires nothing, and it is the
    settings that are the weaker evidence — they describe a default that any view
    may override.
    """

    path: str
    scheme: Scheme
    established: Established
    observation: Observation | None = None
    declared: tuple[Scheme, ...] = ()

    @property
    def needs_credential(self) -> bool:
        return self.scheme is not Scheme.NONE

    @property
    def inconclusive(self) -> bool:
        """Whether the probe failed to establish anything about auth.

        A route this reports on is not a route with no authentication; it is a
        route that has not been asked yet in a way that answered. The Explorer's
        next move is a different path — or the same one with parameters filled —
        rather than a credential.
        """
        return self.observation is not None and not self.observation.speaks_to_auth

    @property
    def declaration_disagrees(self) -> bool:
        """Whether the settings offered something other than what the route did.

        Not an error and not corrected. It is the ordinary case for an `AllowAny`
        view in a token-defaulted project, and it is worth reporting because the
        same shape — settings naming `TOKEN`, the route answering `403` — is what
        a session-authenticated DRF endpoint looks like from outside.
        """
        return bool(self.declared) and self.scheme not in self.declared

    def describe(self) -> str:
        if self.inconclusive:
            lines = [f"{self.path} established nothing about authentication"]
        else:
            lines = [f"{self.path} requires {self.scheme.value} ({self.established.value})"]
        if self.observation is not None:
            lines.append(f"  {self.observation.describe()}")
        if self.declaration_disagrees and not self.inconclusive:
            lines.append(
                f"  settings declared {', '.join(s.name for s in self.declared)}; the route "
                "answered otherwise, and a view may override the project default either way"
            )
        return "\n".join(lines)


# ================================================================== the playbook seam


class PlaybookLookup(Protocol):
    """What S-13.1 will be, seen from here.

    A callable from a fingerprint key to whatever has been learned about grounding
    projects like this one. Entries are `Mapping`s and nothing in this module
    reads inside them — S-13.1 decides what an entry means and S-13.2 decides
    when one may be trusted, and inventing either here is the guess `persistent.py`
    declined to make when it stored `(collection, key, entry)` and left the
    columns to Epic 13.
    """

    def __call__(self, key: str) -> Sequence[Mapping[str, object]]: ...


def no_playbook(key: str) -> Sequence[Mapping[str, object]]:
    """The lookup used when there is no store yet: nothing has been learned.

    A function rather than `None`, so that *consulted and empty* and *not
    consulted* are not the same call site. S-13.5 measures whether the tenth
    project of a kind grounds faster than the first, and a consult that silently
    did not happen is the one thing that would make that number meaningless.
    """
    del key
    return ()


class TrustedLookup(Protocol):
    """The entries a caller may **act on**, as opposed to be shown.

    **A second seam beside `PlaybookLookup`, and the separation is the safety
    property.** `recall` returns provisional entries too and that list is
    *context* — the Explorer may read it, `Resolution` carries it unread. This one
    returns only what `standings` promoted: three different projects recorded a
    successful use, and no two failures quarantined it. F4's poison propagates
    because nothing validates a write; three projects agreeing is the validation,
    and keeping the two lists apart at the type level is what stops a caller
    acting on the wrong one by reaching for the nearer name.
    """

    def __call__(self, key: str) -> Sequence[PlaybookEntry]: ...


def no_trusted(key: str) -> Sequence[PlaybookEntry]:
    """The lookup used when nothing has earned trust yet — which is most runs.

    A function rather than `None`, for `no_playbook`'s reason: *consulted and
    empty* and *not consulted* have to be different call sites, or S-13.5's
    learning curve is measuring something else.
    """
    del key
    return ()


def trusted_from_store(store: PersistentStore) -> TrustedLookup:
    """Only the entries `standings` promoted. **S-13.2's gate, as a lookup.**

    The counterpart of `playbook_from_store`, and deliberately not a filter a
    caller applies to it: a lookup that returned everything and left the
    filtering to the call site would put the safety decision at every call site.
    """

    def lookup(key: str) -> Sequence[PlaybookEntry]:
        return trusted(store, key)

    return lookup


def playbook_from_store(store: PersistentStore) -> PlaybookLookup:
    """Read playbook entries out of S-6.2's persistent store.

    The whole of the seam. `PLAYBOOKS` is one of the four members that store
    already enumerates, and the key is S-7.1's `playbook_key()` — framework and
    major version — which is what S-13.1's first acceptance criterion says an
    entry is filed under.

    What comes back is `Mapping`s, unread. The journal stores `(collection, key,
    entry)` precisely because Epic 13 decides what an entry means, and a reader
    that started interpreting them here would fix that shape a whole epic early.
    """

    def lookup(key: str) -> Sequence[Mapping[str, object]]:
        return [entry.entry for entry in store.read(Collection.PLAYBOOKS, key)]

    return lookup


# ================================================================== reading the settings

# Runs in the *subject's* interpreter. Source text rather than a module, for
# S-7.3's reason: nothing under `src/` imports Django, and this has to run under
# whatever interpreter and version the subject resolved to.
#
# Every read is defensive against a missing setting rather than against a broken
# one. A project without `REST_FRAMEWORK` is ordinary; a project whose settings
# raise on import is a failure this must report, not absorb.
_PROFILE_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.conf import settings

problems = []


def names(value):
    out = []
    for item in value or []:
        out.append(item if isinstance(item, str) else getattr(item, "__name__", str(item)))
    return out


rest = getattr(settings, "REST_FRAMEWORK", None)
answer = {
    "authentication_classes": names(rest.get("DEFAULT_AUTHENTICATION_CLASSES"))
    if isinstance(rest, dict)
    else [],
    "installed_apps": names(getattr(settings, "INSTALLED_APPS", [])),
    "middleware": names(getattr(settings, "MIDDLEWARE", [])),
    "login_url": getattr(settings, "LOGIN_URL", None),
    "session_cookie_name": getattr(settings, "SESSION_COOKIE_NAME", "sessionid"),
    "user_model": None,
}

try:
    from django.contrib.auth import get_user_model

    model = get_user_model()
    manager = getattr(model, "_default_manager", None)
    answer["user_model"] = {
        "label": model._meta.label,
        "username_field": getattr(model, "USERNAME_FIELD", None),
        "required_fields": list(getattr(model, "REQUIRED_FIELDS", []) or []),
        "creates_users": hasattr(manager, "create_user"),
    }
except Exception as error:
    problems.append("the user model: " + type(error).__name__ + ": " + str(error))

answer["problems"] = problems
print("__MARKER__" + json.dumps(answer))
"""

_PROFILE = _PROFILE_SOURCE.replace("__MARKER__", _MARKER)

# What each authentication class DRF ships means in this module's vocabulary, and
# what each installed application implies. Matched on the class name rather than
# the dotted path, because a project subclassing `TokenAuthentication` to change
# the keyword still authenticates with a token.
_CLASS_SCHEMES: tuple[tuple[str, Scheme], ...] = (
    ("SessionAuthentication", Scheme.SESSION),
    ("TokenAuthentication", Scheme.TOKEN),
    ("JWTAuthentication", Scheme.JWT),
    ("JSONWebTokenAuthentication", Scheme.JWT),
    ("BasicAuthentication", Scheme.BASIC),
)

_APP_SCHEMES: tuple[tuple[str, Scheme], ...] = (
    ("rest_framework.authtoken", Scheme.TOKEN),
    ("rest_framework_simplejwt", Scheme.JWT),
    ("knox", Scheme.TOKEN),
)


def _run_in_subject(  # noqa: PLR0913 - what to run, what to pass it, where, with
    # which interpreter and under which settings are five independent facts about
    # one subprocess, and three of them belong to the sandbox rather than here.
    program: str,
    arguments: Sequence[str],
    *,
    surface: Surface,
    python: Sequence[str],
    settings: Detected[str],
    timeout: float,
) -> Mapping[str, Any]:
    """Run one introspection program and read its answer, or say why not.

    Returns `Any` values: this is another interpreter's JSON and nothing here can
    know its shape statically. Every field is converted at the call site rather
    than trusted.
    """
    try:
        result = surface.run(
            [*python, "-c", program, *arguments],
            timeout=timeout,
            env={"DJANGO_SETTINGS_MODULE": settings.value},
        )
    except ExecutionError as error:
        raise AuthError(str(error)) from error

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        message = f"the subject's interpreter did not answer (exit {result.exit_code}): {said}"
        raise AuthError(message)

    try:
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        message = f"the subject's answer was not JSON: {error}"
        raise AuthError(message) from error
    return payload


def read_profile(
    root: Path,
    *,
    python: Sequence[str],
    surface: Surface | None = None,
    timeout: float = PROFILE_TIMEOUT_SECONDS,
) -> AuthProfile:
    """AC 1's first half, and AC 3's whole basis: ask the subject about its own auth.

    Asked of the framework rather than read out of files, for the reason S-7.3
    established. `AUTH_USER_MODEL` is a dotted path in a settings module that may
    itself be assembled from three imported files and an environment variable,
    and `USERNAME_FIELD` is a class attribute that a base class may set — neither
    is reliably readable as text, and `get_user_model()` answers both exactly.

    Raises:
        AuthError: the subject could not be configured or did not answer, which
            is a different situation from *this project has no authentication*
            and must not be flattened into it.
    """
    root = Path(root)
    settings = settings_module(root)
    if settings is None:
        message = (
            "no DJANGO_SETTINGS_MODULE was found in manage.py, wsgi.py or asgi.py, so the "
            "subject cannot be configured to answer what its auth scheme is"
        )
        raise AuthError(message)

    payload = _run_in_subject(
        _PROFILE,
        (),
        surface=surface or HostSurface(root),
        python=python,
        settings=settings,
        timeout=timeout,
    )

    declared: list[Detected[Scheme]] = []
    for name in payload.get("authentication_classes", []):
        for needle, scheme in _CLASS_SCHEMES:
            if needle in str(name):
                declared.append(
                    Detected(scheme, f"REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES: {name}")
                )
    for app in payload.get("installed_apps", []):
        for needle, scheme in _APP_SCHEMES:
            if str(app).startswith(needle):
                declared.append(Detected(scheme, f"INSTALLED_APPS: {app}"))
    middleware = [str(item) for item in payload.get("middleware", [])]
    if any("SessionMiddleware" in item for item in middleware) and any(
        "AuthenticationMiddleware" in item for item in middleware
    ):
        declared.append(
            Detected(Scheme.SESSION, "MIDDLEWARE: SessionMiddleware with AuthenticationMiddleware")
        )

    return AuthProfile(
        settings_module=settings,
        declared=tuple(declared),
        user_model=_user_model_of(payload.get("user_model")),
        login_url=_text(payload.get("login_url")),
        session_cookie_name=_text(payload.get("session_cookie_name")) or "sessionid",
        problems=tuple(str(problem) for problem in payload.get("problems", [])),
    )


def _text(value: object) -> str | None:
    """A string from the subprocess's JSON, or nothing. Never a stringified `None`."""
    return value if isinstance(value, str) else None


def _user_model_of(payload: object) -> UserModel | None:
    """A user model from the subject's answer, or nothing.

    Nothing is the right answer where the field is missing: a project whose user
    model could not be loaded has not got a default one, and defaulting to
    `auth.User` here would send `mint` to call `create_user(username=…)` against
    a model that does not have the field.
    """
    if not isinstance(payload, Mapping):
        return None
    username_field = _text(payload.get("username_field"))
    label = _text(payload.get("label"))
    if not username_field or not label:
        return None
    return UserModel(
        label=label,
        username_field=username_field,
        required_fields=tuple(str(name) for name in payload.get("required_fields", [])),
        creates_users=bool(payload.get("creates_users")),
    )


# ================================================================== making a credential


@dataclass(frozen=True)
class Recipe:
    """Everything needed to make this credential again.

    The reason this exists rather than a bare token is the one in the module
    docstring: S-2.6 resets the database between S-7.8's two scales, and a reset
    takes the user row with it. A credential that cannot be remade is a credential
    that works for the first half of every scaling sweep.

    It is also what S-7.9's workload artifact records, alongside S-7.5's fixture
    recipe, and for the same reason — a workload nobody else can reproduce is not
    a workload.
    """

    scheme: Scheme
    username: str
    password: str
    extra: Mapping[str, str] = field(default_factory=dict)
    """Values for the model's `REQUIRED_FIELDS`. Supplied where the caller knows
    them, filled in where it does not — a required field with no value makes
    `create_user` raise, and this stage has no way to ask a human."""

    superuser: bool = False
    """Off by default. A superuser reaches routes an ordinary account cannot,
    which sounds useful and means the Explorer can ground a workload no real
    request ever takes. Opt in where a route needs it and record that it did."""

    def describe(self) -> str:
        role = "superuser" if self.superuser else "ordinary user"
        return f"{self.scheme.name} for {self.username!r} ({role})"


@dataclass(frozen=True)
class Credential:
    """A credential, what it was made from, and how to attach it.

    Headers and cookies are separate because HTTP keeps them separate and every
    client takes them by different arguments. Merging them into one bag would
    make `attach` guess which is which.
    """

    scheme: Scheme
    recipe: Recipe
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    user_label: str | None = None

    def describe(self) -> str:
        carried = ", ".join([*self.headers, *(f"cookie {name}" for name in self.cookies)])
        return f"{self.recipe.describe()} carried in {carried or 'nothing'}"


def attach(
    credential: Credential,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, str], Mapping[str, str]]:
    """AC 2's second half: the headers and cookies a subsequent request carries.

    **The credential wins on a collision.** A caller passing its own
    `Authorization` header alongside a token credential has two credentials and
    one slot, and taking the caller's would silently send the request
    unauthenticated — the failure mode being an endpoint that reads as `401`
    forever while the log says a credential was attached.
    """
    return (
        {**(headers or {}), **credential.headers},
        {**(cookies or {}), **credential.cookies},
    )


# Runs in the subject's interpreter and **writes**. Its argument is JSON on the
# command line rather than a formatted program, so that a password containing a
# quote is a value rather than a syntax error.
#
# Minting is idempotent by construction: an existing user is found and its
# password reset rather than a second one created. S-2.6 resets between scales,
# so this runs repeatedly against the same subject and a version that raised on
# the second call would work exactly once per reset.
_MINT_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model

REQUEST = json.loads(sys.argv[1])
problems = []

model = get_user_model()
field = model.USERNAME_FIELD
manager = model._default_manager

values = {field: REQUEST["username"]}
for name in getattr(model, "REQUIRED_FIELDS", []) or []:
    if name == field:
        continue
    values[name] = REQUEST["extra"].get(name) or REQUEST["username"]

user = manager.filter(**{field: REQUEST["username"]}).first()
if user is None:
    make = manager.create_superuser if REQUEST["superuser"] else manager.create_user
    user = make(password=REQUEST["password"], **values)
else:
    user.set_password(REQUEST["password"])
    if REQUEST["superuser"]:
        user.is_staff = True
        user.is_superuser = True
    user.save()

answer = {"user_label": model._meta.label, "headers": {}, "cookies": {}}
scheme = REQUEST["scheme"]

if scheme == "SESSION":
    from importlib import import_module

    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    engine = import_module(settings.SESSION_ENGINE)
    session = engine.SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    answer["cookies"][settings.SESSION_COOKIE_NAME] = session.session_key

elif scheme == "TOKEN":
    # The import and the use are guarded together, because the import is not
    # where this fails. A model whose application is absent from INSTALLED_APPS
    # still *imports* — Django leaves it without a manager, and the AttributeError
    # arrives on the next line. Guarding only the import turns an ordinary
    # project (DRF installed, its optional token app not) into a traceback
    # instead of the sentence that says which app to add.
    try:
        from rest_framework.authtoken.models import Token

        token, _ = Token.objects.get_or_create(user=user)
    except Exception as error:
        problems.append("no usable token model: " + type(error).__name__ + ": " + str(error))
    else:
        answer["headers"]["Authorization"] = "Token " + token.key

answer["problems"] = problems
print("__MARKER__" + json.dumps(answer))
"""

_MINT = _MINT_SOURCE.replace("__MARKER__", _MARKER)


def mint(  # noqa: PLR0913 - the subject and its interpreter, what its settings
    # said, which scheme the *route* asked for and what to make the account from
    # are five facts from five different places; the profile cannot supply the
    # scheme, because what a project declares is not what a route enforces.
    root: Path,
    *,
    python: Sequence[str],
    profile: AuthProfile,
    scheme: Scheme,
    recipe: Recipe | None = None,
    surface: Surface | None = None,
    timeout: float = MINT_TIMEOUT_SECONDS,
) -> Credential:
    """AC 2's first half: create a credential the subject will accept.

    A session is written the way Django's own test client writes one — a session
    row and the cookie that addresses it — rather than by driving the login form.
    The form is a flow: it needs a CSRF token out of a page, the name of a field
    this stage has just finished establishing is not always `username`, and it
    stops dead at a second factor. The session is a row, and the framework
    supports writing it.

    **`JWT` is detectable and not mintable, and that is a stated limit rather
    than a gap.** Every JWT package signs its own way — SimpleJWT's
    `RefreshToken.for_user`, `django-rest-framework-jwt`'s handlers, a
    hand-rolled `PyJWT` call with the project's own claims — and none of them is
    installed here to test against. A branch that ships unverified is worth less
    than a refusal that says what would make it work.

    Raises:
        AuthError: the scheme cannot be minted, the subject has no user model
            able to make users, or the subject failed to answer.
    """
    if not scheme.can_be_minted:
        message = (
            f"{scheme.name} cannot be minted here: {scheme.value}. Detecting it is supported; "
            "creating one is not, because every package that issues these signs them differently "
            "and none is installed to verify against. Supply a credential, or use a route "
            "requiring a scheme this can mint"
        )
        raise AuthError(message)

    if profile.user_model is None:
        message = (
            "the subject reported no user model, so there is nothing to create a credential "
            "against. Its settings loaded, which means this is a project whose AUTH_USER_MODEL "
            "does not resolve rather than one that has no authentication"
        )
        raise AuthError(message)
    if not profile.user_model.creates_users:
        message = (
            f"{profile.user_model.label} has no create_user on its default manager, so there is "
            "no supported way to give it a usable password. Writing the row directly would store "
            "a hash nothing can authenticate against, which fails as a 401 rather than an error"
        )
        raise AuthError(message)

    recipe = recipe or default_recipe(scheme, profile.user_model)
    if recipe.scheme is not scheme:
        message = (
            f"the recipe makes a {recipe.scheme.name} credential and a {scheme.name} one was "
            "asked for; a credential that does not match the route's scheme is a 401 that looks "
            "like a wrong password"
        )
        raise AuthError(message)

    payload = _run_in_subject(
        _MINT,
        (
            json.dumps(
                {
                    "username": recipe.username,
                    "password": recipe.password,
                    "extra": dict(recipe.extra),
                    "superuser": recipe.superuser,
                    "scheme": scheme.name,
                }
            ),
        ),
        surface=surface or HostSurface(Path(root)),
        python=python,
        settings=profile.settings_module,
        timeout=timeout,
    )

    problems = tuple(str(problem) for problem in payload.get("problems", []))
    headers = {str(k): str(v) for k, v in (payload.get("headers") or {}).items()}
    cookies = {str(k): str(v) for k, v in (payload.get("cookies") or {}).items()}

    if scheme is Scheme.BASIC:
        # Computed here rather than in the subject: it is base64 of two strings
        # this function already holds, and a round trip through another
        # interpreter to run `b64encode` would be a model call's worth of
        # ceremony for a function.
        pair = f"{recipe.username}:{recipe.password}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode("ascii")

    if not headers and not cookies:
        said = "; ".join(problems) or "it returned neither a header nor a cookie"
        message = (
            f"the subject created the user but produced no {scheme.name} credential: {said}. "
            "A credential carrying nothing attaches nothing, and every subsequent request would "
            "be unauthenticated while the log said otherwise"
        )
        raise AuthError(message)

    return Credential(
        scheme=scheme,
        recipe=recipe,
        headers=headers,
        cookies=cookies,
        user_label=_text(payload.get("user_label")),
    )


def default_recipe(scheme: Scheme, user_model: UserModel) -> Recipe:
    """A recipe built around what the subject says identifies a user.

    AC 3, at the one place it bites. The username is spelled as an address when
    the model wants an address, because a `USERNAME_FIELD` of `email` is usually
    an `EmailField` and a validator refuses `coldfix-explorer` before any of this
    reaches the database.

    The password is random per credential rather than fixed. A constant would be
    written into every subject this tool ever grounds, and the accounts it
    creates outlive the run — S-2.6 resets the *data*, and a repository whose
    developer database this ran against keeps the row.
    """
    stem = "coldfix-explorer"
    address = f"{stem}@example.invalid"
    looks_like_email = "email" in user_model.username_field.lower()
    return Recipe(
        scheme=scheme,
        username=address if looks_like_email else stem,
        password=secrets.token_urlsafe(24),
        extra={name: address for name in user_model.required_fields if "email" in name.lower()},
    )


# ================================================================== acting on a memory


def actionable(entries: Sequence[PlaybookEntry]) -> tuple[PlaybookEntry, Scheme] | None:
    """The one trusted entry worth acting on, or `None`. **S-13.7's whole judgement.**

    Four ways to answer `None`, and each is a refusal rather than a gap:

    - **nothing is trusted.** The ordinary case, and the one every first run is in.
    - **no trusted entry names a scheme this can act on.** `remembered_requirement`
      returns `None` for an entry some other stage wrote, for one whose outcome
      records a failure, and for a name that is not a `Scheme`; `can_be_minted`
      removes `JWT`, which is detectable and not mintable, and `NONE`, which asks
      for nothing to be done.
    - **two trusted entries name different schemes.** Refused rather than
      resolved. Each was earned on three different projects, so a disagreement is
      evidence that the fingerprint does not determine the answer — and picking
      one is the alphabetical tie-break that seeded a hundred authors and drove
      the wrong route at S-7.13.
    - the caller's own guard: `resolve_auth` does not ask unless the probe left
      the scheme `UNKNOWN`.

    Returns the entry alongside the scheme, because acting on it has to be
    recorded against the entry that was acted on — `note_use` files a use by
    digest, and a scheme with no entry behind it could not be demoted.
    """
    named: dict[Scheme, PlaybookEntry] = {}
    for entry in entries:
        remembered = remembered_requirement(entry)
        if remembered is None:
            continue
        scheme = _SCHEMES_BY_NAME.get(remembered)
        if scheme is None or not scheme.can_be_minted:
            continue
        named.setdefault(scheme, entry)

    if len(named) != 1:
        return None
    scheme, entry = next(iter(named.items()))
    return entry, scheme


_SCHEMES_BY_NAME: Mapping[str, Scheme] = {item.name: item for item in Scheme}
"""By `name`, because that is what `learned_from_auth` was given to write down.

`Scheme` is a `StrEnum` whose *values* are sentences, so `Scheme("TOKEN")` raises
and `Scheme("a token in an Authorization header, …")` is what would work — which
is not what any entry holds. An unrecognised name yields `None` and the entry is
not acted on."""


# ================================================================== the stage


@dataclass(frozen=True)
class Resolution:
    """What auth resolution established, and everything it took to get there.

    Carries the playbook entries it was given without reading them. AC 4 asks for
    the consult; S-13.1 gives the entries meaning and S-13.2 decides when one may
    be believed, and interpreting them here would be this story deciding both.
    """

    profile: AuthProfile
    requirement: Requirement
    credential: Credential | None
    playbook_entries: tuple[Mapping[str, object], ...] = ()
    playbook_key: str | None = None

    acted_on: PlaybookEntry | None = None
    """The trusted entry this resolution acted on, where it acted on one.

    **Reported so that a use can be recorded against it.** S-13.2's demotion is
    what makes reading an entry safe at all, and a run that acted on one and did
    not say which could never demote it — the poisoned entry would keep its three
    successes and go on being offered. The composition records the use once the
    workload has been driven, because *the mint succeeded* is not the same claim
    as *the route answered*.
    """

    @property
    def resolved(self) -> bool:
        """Whether the route can now be requested.

        True when nothing is required, or when something is and a credential for
        it exists. Deliberately not *a credential exists*: a route needing none
        is resolved, and reporting otherwise would send the Explorer to mint a
        user it has no use for.
        """
        return not self.requirement.needs_credential or self.credential is not None

    def describe(self) -> str:
        lines = [self.profile.describe(), self.requirement.describe()]
        if self.playbook_key is not None:
            lines.append(
                f"  playbook consulted under {self.playbook_key}: "
                f"{len(self.playbook_entries)} entry(s), carried unread — S-13.1 defines them"
            )
        if self.acted_on is not None:
            lines.append(
                f"  acted on a trusted entry: {self.acted_on.describe()}\n"
                "    the route asked for a credential and would not say which; this is what "
                "three other projects of this kind turned out to need"
            )
        if self.credential is not None:
            lines.append(f"  credential: {self.credential.describe()}")
        elif self.requirement.needs_credential:
            lines.append("  no credential: the route needs one and none was made")
        return "\n".join(lines)


def resolve_auth(  # noqa: PLR0913 - the subject, how to run it, what to probe and
    # how to probe it are four independent facts, and the last two belong to the
    # sandbox rather than to this module. Bundling them would invent a config
    # object with one implementation, which `CLAUDE.md` refuses until a second exists.
    root: Path,
    *,
    python: Sequence[str],
    path: str,
    request: Callable[[str], Reply],
    playbook: PlaybookLookup = no_playbook,
    trusted_entries: TrustedLookup = no_trusted,
    playbook_key: str | None = None,
    recipe: Recipe | None = None,
    timeout: float = PROFILE_TIMEOUT_SECONDS,
) -> Resolution:
    """The stage: consult, read, probe, and mint only if the route asks for it.

    The order is AC 4's word *before*, and it is not decoration — a playbook entry
    that eventually says *this project needs a staff account* is worth nothing
    once the probe has already been made and the credential already minted. So the
    consult happens first, before any request reaches the subject.

    A route answering without a credential ends the stage there. Minting one
    anyway would write a user into the subject for no reason, and ADR 009's
    predicate for this stage is that a protected route answered — not that an
    account exists.

    **Two lookups, and only one of them may change what happens.** `playbook` is
    the context list: it holds provisional entries and is carried unread, which is
    what S-13.1 built and what the Explorer is shown. `trusted_entries` holds only
    what three different projects agreed on, and it is the one a decision may rest
    on. Both are consulted before the probe; the second is *used* after it, and
    only where the probe left a gap.
    """
    entries = tuple(playbook(playbook_key)) if playbook_key is not None else ()
    earned = tuple(trusted_entries(playbook_key)) if playbook_key is not None else ()

    profile = read_profile(root, python=python, timeout=timeout)
    observation = Observation(path=path, reply=request(path))
    requirement = Requirement(
        path=path,
        scheme=observation.scheme,
        established=Established.OBSERVED,
        observation=observation,
        declared=profile.declared_schemes,
    )

    # **The one place a memory may decide anything, and the condition is narrow on
    # purpose.** `UNKNOWN` is the route saying *something is enforcing
    # authentication and I will not say what* — a gap, with a credential known to
    # be needed. Every other verdict is either a measurement of this route, which
    # a prior about projects of its kind must not override, or `inconclusive`,
    # where the answer said nothing about authentication at all and the next move
    # is a different path rather than a user nobody asked for.
    acted_on = None
    if requirement.scheme is Scheme.UNKNOWN and not requirement.inconclusive:
        found = actionable(earned)
        if found is not None:
            acted_on, remembered = found
            requirement = Requirement(
                path=path,
                scheme=remembered,
                established=Established.REMEMBERED,
                observation=observation,
                declared=profile.declared_schemes,
            )

    # One condition, not two. *Do not mint for a route that needs nothing* and
    # *do not mint what cannot be minted* look like separate rules and are the
    # same one: `NONE` is not a mintable scheme, precisely because there is
    # nothing to mint for it. Spelling both out reads as two guards and hides that
    # only one of them can ever decide — a sabotage removing the first changed no
    # outcome, which is how this was found.
    credential = None
    if requirement.scheme.can_be_minted:
        credential = mint(
            root,
            python=python,
            profile=profile,
            scheme=requirement.scheme,
            recipe=recipe,
            timeout=timeout,
        )

    return Resolution(
        profile=profile,
        requirement=requirement,
        credential=credential,
        playbook_entries=entries,
        playbook_key=playbook_key,
        acted_on=acted_on,
    )

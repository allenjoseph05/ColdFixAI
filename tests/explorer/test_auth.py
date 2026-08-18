"""S-7.4 — what a route requires, and a credential it will accept.

Built against a real Django project with real DRF views, and every request in
this file is made by the subject's own interpreter through `django.test.Client`
— the full middleware stack, `login_required`, and DRF's exception handling.

That is deliberate and it is the whole basis of AC 1's second half. The rules
under test are readings of specific HTTP answers: that `login_required` redirects
rather than challenging, that DRF returns `401` *with* a `WWW-Authenticate` header
for token auth and `403` *without* one for session auth. A fake responder would
produce whichever of those this file believed in, which is S-0.7b's *a test double
more forgiving than the real thing turns a structural assertion into a
decoration*. Here the framework produces them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from coldfix.bench.execute import execute
from coldfix.explorer.auth import (
    AuthError,
    AuthProfile,
    Credential,
    Established,
    Observation,
    Recipe,
    Reply,
    Requirement,
    Scheme,
    attach,
    default_recipe,
    mint,
    no_playbook,
    playbook_from_store,
    read_profile,
    resolve_auth,
)
from coldfix.explorer.fingerprint import Detected
from coldfix.sandbox.production import VerifiedDatabase
from coldfix.sandbox.reset import wait_until_ready
from coldfix.sandbox.runner import docker_available
from coldfix.state.persistent import Collection, PersistentStore

pytestmark = pytest.mark.slow
"""Every test here starts at least one `django.setup()`, and the DB-backed ones
migrate a project first. Excluded from the fast subset for time, not because they
need anything this machine has not got — `django` and `djangorestframework` are
dev dependencies precisely so these run everywhere the suite does."""

MANAGE_PY = """\
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
"""

SETTINGS = """\
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "not-a-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
LOGIN_URL = "/accounts/sign-in/"
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "rest_framework",
__TOKEN_APP__
__EXTRA_APPS__
    "shop",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
__AUTH_USER_MODEL__
"""

# One route per answer the module claims to read, and every one of them produced
# by the framework rather than by this file. `basic` is the exception and is
# still real: Django has no Basic-auth view to borrow, so the view returns the
# challenge RFC 7235 specifies and Django serves it.
URLS = """\
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import path
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def open_view(request):
    return HttpResponse("open")


@login_required
def private_view(request):
    return HttpResponse("private")


def sign_in(request):
    return HttpResponse("a login page")


def bearer_view(request):
    response = HttpResponse("no", status=401)
    response["WWW-Authenticate"] = 'Bearer realm="api"'
    return response


def basic_view(request):
    if not request.headers.get("Authorization", "").startswith("Basic "):
        response = HttpResponse("no", status=401)
        response["WWW-Authenticate"] = 'Basic realm="api"'
        return response
    return HttpResponse("basic ok")


class TokenView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"who": str(request.user)})


class SessionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"who": str(request.user)})


urlpatterns = [
    path("open/", open_view),
    path("private/", private_view),
    path("accounts/sign-in/", sign_in),
    path("api/basic/", basic_view),
    path("api/bearer/", bearer_view),
    path("api/token/", TokenView.as_view()),
    path("api/session/", SessionView.as_view()),
]
"""

# AC 3's subject. `USERNAME_FIELD` is an address and `username` survives as a
# required field, which is the shape that breaks `create_user(username=...)`:
# the call succeeds and creates an account nothing can log in as, because the
# field the backend looks up is the other one.
ACCOUNTS_MODELS = """\
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]
"""

# Runs in the subject's interpreter and makes a real request through the real
# stack. This is what the module's `request` callable is in production — a thing
# the sandbox owns — and using the framework's own client keeps every status
# code in these tests something Django decided rather than something this file
# asserted.
REQUEST_PROGRAM = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.test import Client

REQUEST = json.loads(sys.argv[1])
client = Client()
for name, value in REQUEST["cookies"].items():
    client.cookies[name] = value

response = client.get(
    REQUEST["path"], headers=REQUEST["headers"], follow=REQUEST["follow"]
)
chain = getattr(response, "redirect_chain", None)
print("<<<REPLY>>>" + json.dumps({
    "status": response.status_code,
    "headers": dict(response.items()),
    "answered_path": chain[-1][0] if chain else None,
}))
"""


def write_project(root: Path, *, custom_user: bool, authtoken: bool = True) -> Path:
    """A real Django project, with or without a swapped user model.

    Two of them, because `08-audit.md`'s recurring lesson is that a detector needs
    a control: a recipe that says `email` for every project would pass every AC 3
    test and be wrong about the default model, which is most projects.
    """
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "shop").mkdir(parents=True, exist_ok=True)

    settings = (
        SETTINGS.replace("__EXTRA_APPS__", '    "accounts",' if custom_user else "")
        .replace("__AUTH_USER_MODEL__", 'AUTH_USER_MODEL = "accounts.User"' if custom_user else "")
        .replace("__TOKEN_APP__", '    "rest_framework.authtoken",' if authtoken else "")
    )

    (root / "manage.py").write_text(MANAGE_PY, encoding="utf-8")
    (root / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "config" / "settings.py").write_text(settings, encoding="utf-8")
    (root / "config" / "urls.py").write_text(URLS, encoding="utf-8")
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")

    if custom_user:
        (root / "accounts").mkdir(exist_ok=True)
        (root / "accounts" / "__init__.py").write_text("", encoding="utf-8")
        (root / "accounts" / "models.py").write_text(ACCOUNTS_MODELS, encoding="utf-8")

    return root


def migrate(root: Path, *, custom_user: bool) -> None:
    """Create the tables, so a credential can be written to something."""
    if custom_user:
        run_manage(root, "makemigrations", "accounts")
    run_manage(root, "migrate")


def run_manage(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"manage.py {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}")


def request_through(
    root: Path,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    follow: bool = False,
) -> Callable[[str], Reply]:
    """A `request` callable for `resolve_auth`, backed by the subject's own client."""

    def request(path: str) -> Reply:
        payload = json.dumps(
            {
                "path": path,
                "headers": dict(headers or {}),
                "cookies": dict(cookies or {}),
                "follow": follow,
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", REQUEST_PROGRAM, payload],
            cwd=root,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        line = next(
            (row for row in result.stdout.splitlines() if row.startswith("<<<REPLY>>>")), None
        )
        if line is None:
            pytest.fail(f"the subject did not answer:\n{result.stdout}\n{result.stderr}")
        answer = json.loads(line.removeprefix("<<<REPLY>>>"))
        return Reply(
            status=answer["status"],
            headers=answer["headers"],
            answered_path=answer["answered_path"],
        )

    return request


@pytest.fixture(scope="module")
def subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The default-user project, migrated once for the whole module."""
    root = write_project(tmp_path_factory.mktemp("subject"), custom_user=False)
    migrate(root, custom_user=False)
    return root


@pytest.fixture(scope="module")
def swapped_subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The AC 3 project: `USERNAME_FIELD` is an address, not a username."""
    root = write_project(tmp_path_factory.mktemp("swapped"), custom_user=True)
    migrate(root, custom_user=True)
    return root


@pytest.fixture(scope="module")
def tokenless_subject(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project that has DRF and has *not* installed its token app.

    Not a hypothetical: `rest_framework.authtoken` is opt-in, and a project using
    session auth alone never adds it. Importing its model then raises, which is
    the branch that decides whether a credential carrying nothing is returned or
    refused.
    """
    root = write_project(tmp_path_factory.mktemp("tokenless"), custom_user=False, authtoken=False)
    migrate(root, custom_user=False)
    return root


def probe(
    root: Path,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    follow: bool = False,
) -> Observation:
    request = request_through(root, headers=headers, cookies=cookies, follow=follow)
    return Observation(path=path, reply=request(path))


# ================================ AC 1: the scheme is read from settings *and* from answers


def test_settings_declare_every_scheme_the_project_configures(subject: Path) -> None:
    profile = read_profile(subject, python=[sys.executable])

    assert Scheme.TOKEN in profile.declared_schemes
    assert Scheme.SESSION in profile.declared_schemes
    assert any("DEFAULT_AUTHENTICATION_CLASSES" in d.evidence for d in profile.declared)
    assert any("INSTALLED_APPS" in d.evidence for d in profile.declared)


def test_a_declaration_carries_the_setting_that_made_it(subject: Path) -> None:
    """Not decoration: a scheme nobody can trace to a setting is one nobody can
    check when minting for it produces a 401."""
    profile = read_profile(subject, python=[sys.executable])

    token = next(d for d in profile.declared if d.value is Scheme.TOKEN)
    assert "rest_framework" in token.evidence


def test_the_login_url_is_read_from_settings(subject: Path) -> None:
    assert read_profile(subject, python=[sys.executable]).login_url == "/accounts/sign-in/"


def test_a_route_needing_nothing_reports_nothing(subject: Path) -> None:
    """The best answer this stage can give, and it must not be mistaken for a
    failure to detect a scheme."""
    observed = probe(subject, "/open/")

    assert observed.reply.status == 200
    assert observed.scheme is Scheme.NONE


def test_a_login_redirect_is_a_session_requirement(subject: Path) -> None:
    """`login_required` never challenges — it redirects. A reading built only on
    401 would call this route unprotected."""
    observed = probe(subject, "/private/")

    assert observed.reply.status == 302
    assert observed.redirected_away
    assert observed.scheme is Scheme.SESSION


def test_a_challenge_names_its_own_scheme(subject: Path) -> None:
    observed = probe(subject, "/api/basic/")

    assert observed.reply.status == 401
    assert observed.reply.header("WWW-Authenticate") == 'Basic realm="api"'
    assert observed.scheme is Scheme.BASIC


def test_a_token_view_challenges_with_the_token_keyword(subject: Path) -> None:
    """DRF's own answer, not this file's: `TokenAuthentication.authenticate_header`
    returns the keyword, so the 401 names the scheme."""
    observed = probe(subject, "/api/token/")

    assert observed.reply.status == 401
    assert observed.reply.header("WWW-Authenticate") == "Token"
    assert observed.scheme is Scheme.TOKEN


def test_a_session_view_answers_403_without_a_challenge_and_reads_as_session(
    subject: Path,
) -> None:
    """The rule that would be invented if this were faked. DRF turns its 401 into
    a 403 whenever no authenticator can produce a `WWW-Authenticate` header, and
    `SessionAuthentication` is the one that cannot — so 403-with-no-challenge is
    what a session-protected API endpoint looks like from outside."""
    observed = probe(subject, "/api/session/")

    assert observed.reply.status == 403
    assert observed.reply.header("WWW-Authenticate") is None
    assert observed.scheme is Scheme.SESSION


def test_a_401_with_no_challenge_is_unknown_rather_than_guessed(subject: Path) -> None:
    """Something wants a credential and would not say which. Guessing SESSION
    here would send the Explorer to mint a cookie the subject ignores."""
    observed = Observation(path="/x/", reply=Reply(status=401))

    assert observed.scheme is Scheme.UNKNOWN


def test_the_challenge_is_read_case_insensitively() -> None:
    """RFC 7235 says the token is case-insensitive, and a server spelling it
    `basic` is not using a different scheme."""
    lower = Observation("/x/", Reply(401, {"www-authenticate": "basic realm=x"}))

    assert lower.scheme is Scheme.BASIC


def test_a_followed_redirect_is_not_a_route_that_answered(subject: Path) -> None:
    """The failure this module exists to prevent. With redirects followed, the
    protected route returns 200 holding a login page, and nothing in the status
    or the headers separates that from the endpoint answering."""
    followed = probe(subject, "/private/", follow=True)

    assert followed.reply.status == 200
    assert followed.reply.answered_path is not None
    assert followed.redirected_away
    assert followed.scheme is Scheme.SESSION


def test_a_declaration_is_never_treated_as_a_requirement(subject: Path) -> None:
    """A route answering 200 in a token-defaulted project requires nothing, and
    the settings are the weaker evidence — any view may override the default."""
    resolution = resolve_auth(
        subject, python=[sys.executable], path="/open/", request=request_through(subject)
    )

    assert Scheme.TOKEN in resolution.profile.declared_schemes
    assert resolution.requirement.scheme is Scheme.NONE
    assert resolution.requirement.established is Established.OBSERVED
    assert resolution.requirement.declaration_disagrees
    assert resolution.resolved


def test_nothing_is_minted_for_a_route_that_needs_nothing(subject: Path) -> None:
    resolution = resolve_auth(
        subject, python=[sys.executable], path="/open/", request=request_through(subject)
    )

    assert resolution.credential is None
    assert resolution.resolved


# ============================================ AC 2: credentials are created and attached


def test_a_minted_session_opens_a_protected_route(subject: Path) -> None:
    """The whole of AC 2 in one assertion, verified by the framework: the route
    refuses, a credential is minted, the same route answers."""
    profile = read_profile(subject, python=[sys.executable])
    before = probe(subject, "/private/")
    assert before.scheme is Scheme.SESSION

    credential = mint(subject, python=[sys.executable], profile=profile, scheme=Scheme.SESSION)
    headers, cookies = attach(credential)
    after = probe(subject, "/private/", headers=headers, cookies=cookies)

    assert after.reply.status == 200
    assert not after.redirected_away


def test_a_minted_token_opens_a_drf_route(subject: Path) -> None:
    """The branch `djangorestframework` was installed for. A fake token model
    would assert only that this file knows what `get_or_create` is called."""
    profile = read_profile(subject, python=[sys.executable])
    credential = mint(subject, python=[sys.executable], profile=profile, scheme=Scheme.TOKEN)
    headers, cookies = attach(credential)

    assert credential.headers["Authorization"].startswith("Token ")
    assert probe(subject, "/api/token/", headers=headers, cookies=cookies).reply.status == 200


def test_a_minted_basic_credential_opens_a_basic_route(subject: Path) -> None:
    profile = read_profile(subject, python=[sys.executable])
    credential = mint(subject, python=[sys.executable], profile=profile, scheme=Scheme.BASIC)
    headers, cookies = attach(credential)

    assert probe(subject, "/api/basic/", headers=headers, cookies=cookies).reply.status == 200


def test_resolve_auth_probes_then_mints_and_the_credential_works(subject: Path) -> None:
    """The stage end to end, against the route DRF protects with a session."""
    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/api/session/",
        request=request_through(subject),
    )

    assert resolution.requirement.scheme is Scheme.SESSION
    assert resolution.credential is not None
    assert resolution.resolved

    headers, cookies = attach(resolution.credential)
    assert probe(subject, "/api/session/", headers=headers, cookies=cookies).reply.status == 200


def test_minting_twice_produces_a_credential_that_still_works(subject: Path) -> None:
    """S-2.6 resets between S-7.8's two scales, so this runs repeatedly against
    one subject. A version that raised on the second call — or created a second
    user — would work exactly once per reset."""
    profile = read_profile(subject, python=[sys.executable])
    recipe = default_recipe(Scheme.SESSION, profile.user_model)  # type: ignore[arg-type]

    first = mint(
        subject, python=[sys.executable], profile=profile, scheme=Scheme.SESSION, recipe=recipe
    )
    second = mint(
        subject, python=[sys.executable], profile=profile, scheme=Scheme.SESSION, recipe=recipe
    )

    headers, cookies = attach(second)
    assert probe(subject, "/private/", headers=headers, cookies=cookies).reply.status == 200
    assert first.recipe.username == second.recipe.username


def test_the_credential_carries_the_recipe_that_made_it(subject: Path) -> None:
    """A credential that cannot be remade is a credential that works for the
    first half of every scaling sweep."""
    profile = read_profile(subject, python=[sys.executable])
    credential = mint(subject, python=[sys.executable], profile=profile, scheme=Scheme.SESSION)

    assert credential.recipe.username
    assert credential.recipe.password
    assert credential.recipe.scheme is Scheme.SESSION


def test_the_credential_wins_a_collision_with_a_callers_header() -> None:
    """Two credentials and one slot. Taking the caller's would send the request
    unauthenticated while the log said a credential was attached."""
    credential = Credential(
        scheme=Scheme.TOKEN,
        recipe=Recipe(Scheme.TOKEN, "u", "p"),
        headers={"Authorization": "Token real"},
    )

    headers, _ = attach(credential, headers={"Authorization": "Token stale", "Accept": "json"})

    assert headers["Authorization"] == "Token real"
    assert headers["Accept"] == "json"


def test_a_recipe_for_the_wrong_scheme_is_refused(subject: Path) -> None:
    profile = read_profile(subject, python=[sys.executable])

    with pytest.raises(AuthError, match=r"does not match|asked for"):
        mint(
            subject,
            python=[sys.executable],
            profile=profile,
            scheme=Scheme.SESSION,
            recipe=Recipe(Scheme.TOKEN, "u", "p"),
        )


def test_a_scheme_that_cannot_be_minted_says_so_rather_than_returning_nothing() -> None:
    """JWT is detectable and not mintable. A branch that shipped unverified would
    be worth less than a refusal naming what would make it work."""
    profile = AuthProfile(
        settings_module=Detected("config.settings", "manage.py"),
        declared=(),
        user_model=None,
        login_url=None,
        session_cookie_name="sessionid",
    )

    with pytest.raises(AuthError, match="cannot be minted"):
        mint(Path(), python=[sys.executable], profile=profile, scheme=Scheme.JWT)


def test_a_subject_with_no_user_model_is_refused_rather_than_defaulted() -> None:
    """Not the same as *this project has no authentication*, and flattening the
    two would send `mint` at `auth.User` in a project that swapped it."""
    profile = AuthProfile(
        settings_module=Detected("config.settings", "manage.py"),
        declared=(),
        user_model=None,
        login_url=None,
        session_cookie_name="sessionid",
    )

    with pytest.raises(AuthError, match="no user model"):
        mint(Path(), python=[sys.executable], profile=profile, scheme=Scheme.SESSION)


# ================================================= AC 3: a custom user model with a
# ================================================= non-standard username field


def test_a_swapped_user_model_is_read_from_the_framework(swapped_subject: Path) -> None:
    profile = read_profile(swapped_subject, python=[sys.executable])

    assert profile.user_model is not None
    assert profile.user_model.label == "accounts.User"
    assert profile.user_model.username_field == "email"
    assert "username" in profile.user_model.required_fields


def test_the_recipe_identifies_the_user_by_the_field_the_model_names(
    swapped_subject: Path,
) -> None:
    """`create_user(username=...)` against this model creates an account nothing
    can log in as: the row is written and the backend looks up the other field."""
    profile = read_profile(swapped_subject, python=[sys.executable])

    recipe = default_recipe(Scheme.SESSION, profile.user_model)  # type: ignore[arg-type]

    assert "@" in recipe.username


def test_the_default_user_model_is_not_given_an_address(subject: Path) -> None:
    """The control. A recipe that said `email` for every project would pass every
    test above and be wrong about most projects."""
    profile = read_profile(subject, python=[sys.executable])

    recipe = default_recipe(Scheme.SESSION, profile.user_model)  # type: ignore[arg-type]

    assert profile.user_model is not None
    assert profile.user_model.username_field == "username"
    assert "@" not in recipe.username


def test_a_credential_for_a_swapped_model_opens_a_protected_route(
    swapped_subject: Path,
) -> None:
    """AC 3 proved rather than asserted: the account is created against a model
    whose username field is an address, and the framework logs it in."""
    profile = read_profile(swapped_subject, python=[sys.executable])
    credential = mint(
        swapped_subject, python=[sys.executable], profile=profile, scheme=Scheme.SESSION
    )
    headers, cookies = attach(credential)

    answer = probe(swapped_subject, "/private/", headers=headers, cookies=cookies)

    assert answer.reply.status == 200
    assert credential.user_label == "accounts.User"


def test_a_required_field_is_filled_rather_than_left_for_create_user_to_raise(
    swapped_subject: Path,
) -> None:
    """`REQUIRED_FIELDS` are required by the model, and this stage has no way to
    ask a human for one."""
    profile = read_profile(swapped_subject, python=[sys.executable])
    credential = mint(
        swapped_subject, python=[sys.executable], profile=profile, scheme=Scheme.TOKEN
    )

    assert credential.headers["Authorization"].startswith("Token ")


# ==================================================== AC 4: the playbook is consulted first


def test_the_playbook_is_consulted_before_any_request_is_made(subject: Path) -> None:
    """AC 4's word is *before*, and it is not decoration: an entry saying this
    project needs a staff account is worth nothing once the probe has been made
    and the credential minted."""
    order: list[str] = []

    def lookup(key: str) -> Sequence[Mapping[str, object]]:
        order.append(f"playbook:{key}")
        return ({"situation": "opaque", "action": "opaque"},)

    inner = request_through(subject)

    def request(path: str) -> Reply:
        order.append(f"request:{path}")
        return inner(path)

    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/open/",
        request=request,
        playbook=lookup,
        playbook_key="Django/5",
    )

    assert order[0] == "playbook:Django/5"
    assert any(step.startswith("request:") for step in order)
    assert len(resolution.playbook_entries) == 1


def test_playbook_entries_are_carried_and_not_interpreted(subject: Path) -> None:
    """S-13.1 decides what an entry means and S-13.2 decides when one may be
    believed. Reading inside one here would be this story deciding both."""
    entry: Mapping[str, object] = {"anything": ["at", "all"], "nested": {"x": 1}}

    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/open/",
        request=request_through(subject),
        playbook=lambda key: (entry,),
        playbook_key="Django/5",
    )

    assert resolution.playbook_entries == (entry,)


def test_no_playbook_is_a_consult_that_returns_nothing(subject: Path) -> None:
    """*Consulted and empty* and *not consulted* must not be one call site —
    S-13.5 measures whether the tenth project of a kind grounds faster."""
    assert no_playbook("Django/5") == ()

    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/open/",
        request=request_through(subject),
        playbook_key="Django/5",
    )

    assert resolution.playbook_key == "Django/5"
    assert resolution.playbook_entries == ()


@pytest.mark.postgres
def test_the_seam_reads_playbook_entries_out_of_the_real_journal(
    store: PersistentStore,
) -> None:
    """AC 4's other half, against S-6.2's actual store rather than a stand-in.

    What is asserted is the mapping and nothing about meaning: entries filed
    under this fingerprint come back, entries filed under another do not, and
    what is inside one is carried untouched.
    """
    store.append(Collection.PLAYBOOKS, "Django/5", {"situation": "opaque", "n": 1})
    store.append(Collection.PLAYBOOKS, "Django/4", {"situation": "another version"})
    store.append(Collection.FAILURE_MEMORY, "Django/5", {"situation": "not a playbook"})

    entries = playbook_from_store(store)("Django/5")

    assert [dict(entry) for entry in entries] == [{"situation": "opaque", "n": 1}]


@pytest.mark.postgres
def test_a_fingerprint_with_no_playbook_consults_and_gets_nothing(
    store: PersistentStore,
) -> None:
    """The ordinary case for every project until S-13.1 writes the first entry.
    It must be an empty consult, not an error and not a skipped one."""
    assert playbook_from_store(store)("Django/5") == []


# ================================ a scheme that is detected and deliberately not minted


def test_a_bearer_challenge_is_detected_and_no_credential_is_attempted(subject: Path) -> None:
    """The stage must report JWT and stop, not raise.

    `mint` refuses JWT, so a `resolve_auth` that called it for every route
    needing a credential would turn an ordinary token-protected API into an
    exception — and the Explorer would lose the route rather than learn what it
    wants. Detection and capability are two questions.
    """
    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/api/bearer/",
        request=request_through(subject),
    )

    assert resolution.requirement.scheme is Scheme.JWT
    assert resolution.requirement.needs_credential
    assert resolution.credential is None
    assert not resolution.resolved


# ================================= an answer that says nothing about authentication


def test_a_404_is_not_read_as_an_authentication_requirement(subject: Path) -> None:
    """S-7.3 emits routes with path parameters and requesting one literally
    returns 404. Reported as UNKNOWN alone it reads as *something is enforcing
    authentication*, which sends a reader after a credential for a route that is
    not there."""
    observed = probe(subject, "/no-such-route/")

    assert observed.reply.status == 404
    assert not observed.speaks_to_auth
    assert "says nothing about authentication" in observed.describe()


def test_an_inconclusive_probe_says_so_rather_than_naming_a_requirement(
    subject: Path,
) -> None:
    resolution = resolve_auth(
        subject,
        python=[sys.executable],
        path="/no-such-route/",
        request=request_through(subject),
    )

    assert resolution.requirement.inconclusive
    assert resolution.credential is None
    assert "established nothing about authentication" in resolution.requirement.describe()


def test_a_real_answer_is_not_reported_as_inconclusive(subject: Path) -> None:
    """The control. A property that said *inconclusive* about everything would
    pass the two tests above and make the distinction worthless."""
    for path in ("/open/", "/private/", "/api/basic/", "/api/session/"):
        assert probe(subject, path).speaks_to_auth, path


# ============================================ branches no other test reaches


def test_an_unrecognised_challenge_is_unknown_rather_than_a_guess() -> None:
    """A scheme this module cannot name is reported as one it cannot name.
    Defaulting to any particular one would send the Explorer to mint a credential
    the subject has no use for, and the 401 would then read as a wrong password."""
    observed = Observation("/x/", Reply(401, {"WWW-Authenticate": 'Negotiate realm="corp"'}))

    assert observed.scheme is Scheme.UNKNOWN


def test_a_user_model_that_cannot_make_users_is_refused(subject: Path) -> None:
    """Writing the row directly would store a hash nothing authenticates against,
    which fails later as a 401 rather than here as an error."""
    profile = read_profile(subject, python=[sys.executable])
    assert profile.user_model is not None
    without = replace(profile, user_model=replace(profile.user_model, creates_users=False))

    with pytest.raises(AuthError, match="create_user"):
        mint(subject, python=[sys.executable], profile=without, scheme=Scheme.SESSION)


def test_a_credential_carrying_nothing_is_refused_rather_than_returned(
    tokenless_subject: Path,
) -> None:
    """The real shape of it: DRF is installed, its token app is not, so the user
    is created and no token can be. A credential with neither header nor cookie
    attaches nothing, and every later request would go out unauthenticated while
    the log said one was attached."""
    profile = read_profile(tokenless_subject, python=[sys.executable])

    with pytest.raises(AuthError, match="produced no TOKEN credential"):
        mint(tokenless_subject, python=[sys.executable], profile=profile, scheme=Scheme.TOKEN)


# ================================================== a real Postgres for the seam above

IMAGE = "postgres:16-alpine"
USER = "coldfix_test"
PASSWORD = "coldfix_test"

# Not 5432, and not the port S-6.2's or S-2.6's tests pinned. A store pointed at
# the wrong database would still appear to work, which is the worst way to fail.
PORT = 55443


@pytest.fixture(scope="module")
def _server() -> Iterator[str]:
    if not docker_available():
        pytest.skip("no Docker daemon is listening")

    container = f"coldfix-auth-playbook-test-{uuid.uuid4().hex[:8]}"
    execute(
        [
            "docker", "run", "--detach", "--name", container,
            "--publish", f"{PORT}:5432",
            "--env", f"POSTGRES_USER={USER}",
            "--env", f"POSTGRES_PASSWORD={PASSWORD}",
            "--env", "POSTGRES_DB=postgres",
            "--", IMAGE,
        ],
        timeout=180.0,
    )  # fmt: skip
    try:
        yield container
    finally:
        execute(["docker", "rm", "--force", "--volumes", container], timeout=180.0)


def url_for(name: str) -> str:
    return f"postgresql://{USER}:{PASSWORD}@localhost:{PORT}/{name}"


@pytest.fixture
def store(_server: str, tmp_path: Path) -> PersistentStore:
    """A fresh, initialized store per test, named so S-2.5's guard permits it."""
    wait_until_ready(VerifiedDatabase(url_for("coldfix_bootstrap")), "postgres")

    name = f"coldfix_state_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url_for("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')

    built = PersistentStore(
        database=VerifiedDatabase(url_for(name)),
        replay_root=tmp_path / "recordings",
    )
    built.initialize()
    return built


# ============================================================ honest failure


def test_a_project_that_cannot_be_configured_is_an_error_not_an_absence(
    tmp_path: Path,
) -> None:
    """*This project has no authentication* and *this project would not load* are
    two answers, and flattening them reports an unreadable repository as an open
    one."""
    (tmp_path / "manage.py").write_text("nothing useful", encoding="utf-8")

    with pytest.raises(AuthError, match="DJANGO_SETTINGS_MODULE"):
        read_profile(tmp_path, python=[sys.executable])


def test_a_subject_whose_settings_do_not_import_reports_what_it_said(tmp_path: Path) -> None:
    root = write_project(tmp_path, custom_user=False)
    (root / "config" / "settings.py").write_text("import nonexistent_module", encoding="utf-8")

    with pytest.raises(AuthError, match="did not answer"):
        read_profile(root, python=[sys.executable])


def test_an_interpreter_that_cannot_be_started_is_an_error(subject: Path) -> None:
    with pytest.raises(AuthError):
        read_profile(subject, python=[str(subject / "no-such-python")])


def test_a_requirement_records_which_source_settled_it() -> None:
    requirement = Requirement(
        path="/books/",
        scheme=Scheme.TOKEN,
        established=Established.OBSERVED,
        declared=(Scheme.SESSION,),
    )

    assert requirement.needs_credential
    assert requirement.declaration_disagrees
    assert "established by making a request" in requirement.describe()

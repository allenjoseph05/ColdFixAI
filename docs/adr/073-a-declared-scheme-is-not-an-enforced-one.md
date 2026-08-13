# 073 — A declared scheme is not an enforced one, and a credential is a fixture

**Status:** accepted
**Story:** S-7.4 — auth resolution
**Date:** 2026-08-13

## Context

Four acceptance criteria — detect the auth scheme **from settings and
failed-request responses**; create credentials and attach them to subsequent
requests; handle custom user models with non-standard username fields; consult
the playbook before exploration.

The first one names two sources in one line, and they are not two ways of
learning the same fact. `DEFAULT_AUTHENTICATION_CLASSES` lists what DRF will
*accept*; `AUTHENTICATION_BACKENDS` lists what can verify a password. Neither
says what any particular route *requires* — a view can be `AllowAny` inside a
project whose defaults demand a token, and a project with no auth settings at all
can wrap one view in `login_required`.

This is the third appearance of one shape. ADR 070: a manifest's `django>=5.0` is
a constraint and the installed version is a different fact. ADR 072: a `path()`
call is a declaration and the route table is a different fact. Here: a settings
key is a declaration and what a route enforces is a different fact.

## Decision

### The two sources are kept apart, and the observation wins

`Established` records which one settled a requirement. A route that answers `200`
in a token-defaulted project requires nothing, and the settings are the weaker
evidence, because any view may override the project default.

Disagreement is reported (`declaration_disagrees`) rather than corrected. It is
the ordinary case for an `AllowAny` view, and it is worth surfacing because the
same shape — settings naming `TOKEN`, the route answering `403` — is also exactly
what a session-authenticated DRF endpoint looks like from outside.

| Answer | Reading |
|---|---|
| `401` with `WWW-Authenticate` | the server named the scheme itself |
| `401` without | `UNKNOWN` — something wants a credential and would not say which |
| `403` | `SESSION`: DRF downgrades its `401` whenever no authenticator can offer a challenge, and `SessionAuthentication` is the one that cannot |
| `3xx`, or a followed redirect | `SESSION` — a login flow |
| `2xx` from the path asked for | `NONE` |

`NONE` is a genuine answer and the best one; minting a user for such a route
would be work that buys nothing. `UNKNOWN` is not a failure either — guessing
`SESSION` there sends the Explorer to mint a cookie the subject ignores.

### A followed redirect is never a route that answered

`login_required` does not challenge, it redirects. A client with redirects
enabled turns that `302` into a `200` holding the login page, and **nothing in
the status, the headers or the body separates that from the endpoint answering.**
The Explorer would ground itself on a login form, and S-7.8 would then correctly
report that its bytes do not grow with the data — a true measurement of the wrong
thing.

`Reply` therefore carries `answered_path`. Which path produced the response is a
fact every HTTP client already has (`requests` in `response.url`, Django's test
client in `redirect_chain`), and it is the only thing that makes the difference
visible. A `200` from a path other than the one requested is never read as *no
authentication required*.

### An answer that says nothing about auth is not an unknown scheme

A `404` is not a refusal to authenticate and a `500` is not one either. Both name
no scheme, so both are `UNKNOWN` — but `UNKNOWN`'s sentence is *something is
enforcing authentication*, and that sentence is false about a route that is not
there. `speaks_to_auth` keeps the two apart, and `Requirement.inconclusive`
reports *established nothing about authentication* rather than a requirement.

This is not hypothetical. S-7.3 emits routes with path parameters, and requesting
`books/<int:pk>/` literally returns `404` — so the commonest probe the Explorer
will make against a ranked list is exactly this case. The next move is a
different path, not a credential.

### Credentials are minted, not negotiated

The obvious way to get a session is to drive the login form. It needs a CSRF
token out of a page, the name of a field this stage has just finished
establishing is not always `username`, and it stops dead at a second factor or a
third-party identity provider.

Django itself does not do that in its own test client: `force_login` writes a
session row and hands back a cookie. This does the same thing in the subject's
interpreter, using the framework's own `SESSION_KEY`, `BACKEND_SESSION_KEY` and
`HASH_SESSION_KEY`. **A login flow is a flow; a session is a row.**

### A credential is a fixture, not a setup step

S-7.8 drives a workload at N=10 and again at N=100, and S-2.6 resets the database
between them. The reset takes the user row, which takes the session row — so a
credential captured once works for the first half of every scaling sweep and
returns `401` for the second, which reads as *the endpoint failed at scale*.

`Credential` therefore carries the `Recipe` that made it, and minting is
idempotent by construction: an existing account is found and its password reset
rather than a second one created. This is the same decision S-7.5 makes for
fixtures, one story early and for the same reason.

### AC 3 is answered by the framework, never by a parser

`create_user(username=…)` against a model whose `USERNAME_FIELD` is `email`
creates an account nothing can log in as — the row is written and the backend
looks up the other field. So the user model is read with `get_user_model()` in
the subject's interpreter: its label, its `USERNAME_FIELD`, its `REQUIRED_FIELDS`
and whether its default manager can make users at all. `AUTH_USER_MODEL` is a
dotted path in a settings module that may be assembled from three imported files
and an environment variable, and `USERNAME_FIELD` is a class attribute a base
class may set. Neither is reliably readable as text.

A model whose manager has no `create_user` is **refused**, not worked around.
Writing the row directly would store a password hash nothing can authenticate
against, and that fails later as a `401` rather than here as an error.

### JWT is detected and not minted, deliberately

Every package that issues one signs it its own way — SimpleJWT's
`RefreshToken.for_user`, `django-rest-framework-jwt`'s handlers, a hand-rolled
`PyJWT` call with the project's own claims — and none is installed to verify
against. **A branch that ships unverified is worth less than a refusal that names
what would make it work**, so `Scheme.can_be_minted` is false for `JWT` and the
error says so.

`djangorestframework` is now a **dev** dependency on exactly the terms ADR 072
set for `django`: nothing under `src/` imports it, the minting program is source
text run in the subject's interpreter, and the alternative was a fake token model
asserting only that this module knows what `get_or_create` is called.

### AC 4 is a seam, not a schema

The playbook store is S-13.1, an epic away. `playbook_from_store` reads
`Collection.PLAYBOOKS` out of S-6.2's journal under S-7.1's `playbook_key()`, and
`resolve_auth` consults it **before the first request reaches the subject** —
which is AC 4's word *before*, and load-bearing: an entry that eventually says
*this project needs a staff account* is worth nothing once the probe has been made
and the credential minted.

Entries are carried and **not read**. The journal stores `(collection, key,
entry)` precisely because Epic 13 decides what an entry means, and S-13.2 owns
the promotion gate that makes trusting one safe. Interpreting them here would be
this story deciding both, a whole epic early. `no_playbook` is a function rather
than `None` so that *consulted and empty* and *not consulted* are not the same
call site — S-13.5 measures whether the tenth project of a kind grounds faster
than the first, and a consult that silently did not happen is the one thing that
would make that number meaningless.

## Consequences

**Every status code in the test suite was produced by the framework, not by this
file.** The tests build a real Django project with real DRF views and make every
request through `django.test.Client` in the subject's own interpreter — the full
middleware stack, `login_required`, and DRF's exception handling. The rules above
are readings of specific HTTP answers, and a fake responder would have produced
whichever of them the test file believed in.

**One defect was found that way and could not have been found otherwise.** The
token branch guarded the *import* of `rest_framework.authtoken.models`. A model
whose application is absent from `INSTALLED_APPS` still imports — Django leaves
it without a manager and the `AttributeError` arrives on the next line — so a
project with DRF and without its optional token app produced a traceback and the
generic *the subject did not answer* instead of the sentence naming the app to
add. The import and the use are now guarded together. **A guard on the wrong verb
is not a guard**, and only a subject that really lacks the app shows it.

**Makes easy.** ADR 009's auth stage becomes computable and its predicate honest:
a credential was created *and a protected route answered with it*. S-7.5 and
S-7.6 get a subject they can reach; S-7.9 gets a recipe to record next to the
fixture recipe.

**Makes hard.** The caller must report which path answered. A client configured
to follow redirects and not say so can still defeat the check — that is a fact
about the adapter, and it is the reason the field exists rather than a heuristic
that sniffs the body for a login form.

**Rules out.** Reading a project's settings and calling the result the auth
requirement. Capturing a credential once and reusing it across a reset.

**Sabotage-verified on thirty-six properties across two passes, all caught —
after one survived.** The survivor was neither weak code nor a weak test but **a
redundant condition**. `resolve_auth` guarded minting with `needs_credential and
can_be_minted`, which reads as two rules and is one: `NONE` is not a mintable
scheme *precisely because* there is nothing to mint for it, so removing the first
term changed no outcome anywhere. It is now one condition that says so, and the
intent the removed term expressed is verified from the other side — adding `NONE`
to `can_be_minted` is a sabotage the tests catch.

**Tenth time a passing sabotage has meant something other than working code**,
and the first time it has meant *the code states a thing twice*. The three known
causes were a weak test, an edit that did not apply, and a branch no test reaches;
this is a fourth.

The pass also exposed a gap by omission — nothing probed a route that answers
`404` — which is where `speaks_to_auth` came from, with a control asserting the
four real answers are not reported as inconclusive.

"""Production databases are refused, and there is no way to say "yes anyway".

ADR 007 gives the stakes: every safety property in this system assumes state can
be reset ten times a run, and S-2.6 implements resets that truncate and reseed.
Against production that is not a bug, it is a data-loss incident.

So these tests are mostly attempts to get past the guard: with a production URL,
with a URL that only looks like a test one, with an override flag, with an
environment variable, and by configuring the policy until it permits everything.
The last is the interesting one — a `*` in the name patterns is an override flag
with a different name, and it is refused as such.

One test is about neither: `test_the_repr_does_not_leak_the_password`. This
object is designed to be passed around and logged, and a frozen dataclass
renders every field by default. A guard that refused the production database
while printing its credential into a traceback would be its own incident.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from coldfix.sandbox.production import (
    DEFAULT_DATABASE_POLICY,
    DatabasePolicy,
    ProductionDatabaseError,
    ProductionGuardError,
    UnreadableDatabaseUrlError,
    VerifiedDatabase,
    redact,
)

TEST_URL = "postgresql://dev:hunter2@localhost:5432/myapp_test"


# ----------------------------------------------------------- what is allowed


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/test",
        "postgresql://localhost/test_myapp",
        "postgresql://localhost/myapp_test",
        "postgresql://localhost/myapp_test_ci",
        "postgresql://localhost/coldfix_subject_a",
        "postgresql://user:pw@127.0.0.1:5432/myapp_test",
        "postgres://db:5432/myapp_test",
        "postgresql+psycopg://localhost/myapp_test",
    ],
)
def test_a_test_database_is_accepted(url: str) -> None:
    """The guard has to let real test databases through, or it is just a refusal."""
    assert VerifiedDatabase(url).name.startswith(("test", "myapp_test", "coldfix_"))


def test_the_parsed_parts_are_available_without_reparsing() -> None:
    database = VerifiedDatabase(TEST_URL)

    assert database.scheme == "postgresql"
    assert database.host == "localhost"
    assert database.port == 5432
    assert database.name == "myapp_test"


# ------------------------------------------------------------- the refusals


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/myapp_production",
        "postgresql://localhost/myapp",
        "postgresql://localhost/prod",
        "postgresql://localhost/customers",
    ],
)
def test_a_database_whose_name_is_not_a_test_pattern_is_refused(url: str) -> None:
    """The load-bearing check.

    Host allowlists are weak on their own — `localhost` is one SSH tunnel from
    anything. What reliably separates the two is that people name production
    databases after the product and test databases after testing.
    """
    with pytest.raises(ProductionDatabaseError) as raised:
        VerifiedDatabase(url)

    assert raised.value.part == "name"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://myapp.cvxyz123.eu-west-1.rds.amazonaws.com/myapp_test",
        "postgresql://10.0.4.19/myapp_test",
        "postgresql://db.internal.example.com/myapp_test",
    ],
)
def test_a_host_that_is_not_permitted_is_refused_even_with_a_test_name(url: str) -> None:
    """Default-deny.

    A list of hosts that look like production fails the first time somebody
    names one something the list did not anticipate — silently, in the direction
    of destroying data.
    """
    with pytest.raises(ProductionDatabaseError) as raised:
        VerifiedDatabase(url)

    assert raised.value.part == "host"


def test_a_scheme_this_system_cannot_reset_is_refused() -> None:
    """The reset primitive is Postgres-specific. An unlisted scheme is not assumed."""
    with pytest.raises(ProductionDatabaseError) as raised:
        VerifiedDatabase("mysql://localhost/myapp_test")

    assert raised.value.part == "scheme"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "postgresql://localhost",
        "postgresql://localhost/",
        "postgresql:///myapp_test",
        "/var/lib/postgres/myapp_test",
    ],
)
def test_a_url_that_cannot_be_read_is_refused(url: str) -> None:
    """Fail closed. "Unknown" and "permitted" must not be the same answer."""
    with pytest.raises(UnreadableDatabaseUrlError):
        VerifiedDatabase(url)


def test_a_name_that_merely_contains_test_is_not_enough() -> None:
    """`latest` and `contest` contain "test" and are not test databases.

    A substring check would admit both. The patterns are anchored globs.
    """
    for name in ("latest", "contest", "greatest_hits", "protest"):
        with pytest.raises(ProductionDatabaseError):
            VerifiedDatabase(f"postgresql://localhost/{name}")


# --------------------------------------------------- there is no way around it


def test_no_override_argument_exists() -> None:
    """AC 4, asserted against the signature rather than described.

    Deliberately brittle. Adding a `force` or `allow_production` parameter fails
    here and has to be argued for rather than merged.
    """
    parameters = set(inspect.signature(VerifiedDatabase).parameters)

    assert parameters == {"url", "policy"}
    assert not parameters & {"force", "allow_production", "override", "skip_check", "unsafe"}


@pytest.mark.parametrize(
    "variable",
    [
        "COLDFIX_ALLOW_PRODUCTION",
        "COLDFIX_SKIP_GUARD",
        "COLDFIX_UNSAFE",
        "COLDFIX_FORCE",
        "CI",
    ],
)
def test_no_environment_variable_turns_the_guard_off(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "1")

    with pytest.raises(ProductionDatabaseError):
        VerifiedDatabase("postgresql://localhost/myapp_production")


def test_a_policy_that_permits_every_name_is_refused() -> None:
    """The override flag spelled as configuration.

    The policy is configurable because the story requires a *configured*
    pattern. `*` is not a test pattern; it is the absence of one, and accepting
    it would reintroduce exactly the escape hatch the story forbids.
    """
    with pytest.raises(ProductionGuardError, match="override flag"):
        DatabasePolicy(allowed_name_patterns=("*",))

    with pytest.raises(ProductionGuardError, match="override flag"):
        DatabasePolicy(allowed_hosts=("*",))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allowed_schemes": ()},
        {"allowed_hosts": ()},
        {"allowed_name_patterns": ()},
    ],
)
def test_an_empty_policy_is_refused(kwargs: dict[str, tuple[str, ...]]) -> None:
    with pytest.raises(ProductionGuardError, match="empty"):
        DatabasePolicy(**kwargs)


def test_the_check_cannot_be_escaped_by_replacing_a_verified_handle() -> None:
    """Constructing one *is* the check, so re-deriving one re-runs it.

    Attempts to take a handle that passed and swap the URL underneath it, which
    is the shape a bypass would take if the check happened anywhere other than
    the constructor.
    """
    verified = VerifiedDatabase(TEST_URL)

    with pytest.raises(ProductionDatabaseError):
        dataclasses.replace(verified, url="postgresql://localhost/myapp_production")


def test_a_project_can_add_its_own_pattern_without_loosening_the_default() -> None:
    """AC 1 and AC 3 together: configurable, and safe when not configured."""
    policy = DatabasePolicy(allowed_name_patterns=("ci_sandbox_*",))

    assert VerifiedDatabase("postgresql://localhost/ci_sandbox_7", policy).name == "ci_sandbox_7"

    with pytest.raises(ProductionDatabaseError):
        VerifiedDatabase("postgresql://localhost/ci_sandbox_7")

    with pytest.raises(ProductionDatabaseError):
        VerifiedDatabase("postgresql://localhost/myapp_production", policy)


# ------------------------------------------------------- the error, and secrets


def test_the_error_states_what_was_expected_and_what_was_found() -> None:
    """AC 3. A refusal that does not say which rule it failed cannot be acted on."""
    with pytest.raises(ProductionDatabaseError) as raised:
        VerifiedDatabase("postgresql://localhost/myapp_production")

    message = str(raised.value)
    assert "myapp_production" in message
    assert "expected" in message
    assert "found" in message
    assert "test_*" in message
    assert raised.value.expected == DEFAULT_DATABASE_POLICY.allowed_name_patterns


def test_the_error_does_not_leak_the_password() -> None:
    """This exception is destined for a log.

    A guard that refused the production database while printing its credential
    into a traceback would be its own kind of incident.
    """
    with pytest.raises(ProductionDatabaseError) as raised:
        VerifiedDatabase("postgresql://admin:s3cr3t@localhost/myapp_production")

    message = str(raised.value)
    assert "s3cr3t" not in message
    assert "***" in message
    assert "admin" in message


def test_the_repr_does_not_leak_the_password() -> None:
    """A frozen dataclass renders every field, and this one is passed around."""
    database = VerifiedDatabase(TEST_URL)

    assert "hunter2" not in repr(database)
    assert "hunter2" not in str(database)
    assert "hunter2" not in f"{database}"
    assert "myapp_test" in repr(database)


def test_the_credentialed_url_is_still_reachable_for_connecting() -> None:
    """Redaction is for display. Something eventually has to connect."""
    assert VerifiedDatabase(TEST_URL).url == TEST_URL


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h/d", "postgresql://u:***@h/d"),
        ("postgresql://u@h/d", "postgresql://u@h/d"),
        ("postgresql://h/d", "postgresql://h/d"),
        ("postgresql://u:p@h:5432/d", "postgresql://u:***@h:5432/d"),
    ],
)
def test_redaction_removes_the_password_and_nothing_else(url: str, expected: str) -> None:
    assert redact(url) == expected

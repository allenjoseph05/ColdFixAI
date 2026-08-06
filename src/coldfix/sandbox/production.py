"""Refuse to touch a database that might not be a test database.

Epic 2, S-2.5. ADR 007 states the danger precisely: *every safety property in
this system assumes state can be reset ten times a run; against production that
assumption is a data-loss incident.* S-2.6 is the story that implements those
resets, and one of its three strategies truncates and reseeds. This guard is
what stands between that code and somebody's customers.

**The check is the constructor.** `VerifiedDatabase(url)` either returns a verified
handle or raises; there is no unverified handle to hold. That is how "the check
runs before any other initialization" is enforced — not by calling the guard
first, which is an ordering anyone can get wrong, but by making a database
impossible to name until it has passed. Everything downstream takes a
`VerifiedDatabase`, so there is no code path that reaches a connection string
without going through here.

**Default-deny.** A URL is refused unless its scheme, host and database name are
all explicitly permitted. The alternative — listing the things that look like
production — fails the first time somebody names a database something the list
did not anticipate, and fails silently, in the direction of destroying data.

**The database name is the load-bearing check.** Host allowlists are weak on
their own: `localhost` is one SSH tunnel away from anything, and a production
compose file is as free to call its service `db` as a test one is. What
reliably separates the two is that people name production databases after the
product and test databases after testing.

**No override exists, including the ones spelled as configuration.** There is no
`force`, no `allow_production`, and no environment variable. The policy is
configurable because the acceptance criterion requires a *configured* pattern,
but a policy that admits everything is refused at construction — a `*` in the
name patterns is an override flag with a different name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from urllib.parse import urlsplit, urlunsplit

# Schemes this system knows how to reset. Postgres is the first target (ADR
# 005) and its reset primitive is Postgres-specific; a scheme not listed here
# is refused rather than assumed resettable.
DEFAULT_ALLOWED_SCHEMES: tuple[str, ...] = (
    "postgresql",
    "postgres",
    "postgresql+psycopg",
    "postgresql+psycopg2",
)

# Loopback, plus the service names a local compose file conventionally uses.
# The container ones are here because the architecture puts the database in a
# sibling container; they are also the weakest entries in this list, which is
# why the name check below is not optional.
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "::1",
    "db",
    "database",
    "postgres",
)

# What a test database is called. Deliberately conventional rather than
# imaginative: these are the names people actually give test databases, and a
# project whose convention differs configures its own rather than getting a
# looser default.
DEFAULT_ALLOWED_NAME_PATTERNS: tuple[str, ...] = (
    "test",
    "test_*",
    "*_test",
    "*_test_*",
    "*_tests",
    "coldfix_*",
)

# Patterns that would admit any name at all. Accepting one would turn the
# policy into the override flag the story says must not exist.
_VACUOUS_PATTERNS = frozenset({"*", "**", "?*", "*?"})

_REDACTED = "***"


class ProductionGuardError(Exception):
    """The database URL was refused, or the policy protecting it was."""


class UnreadableDatabaseUrlError(ProductionGuardError):
    """The URL could not be understood, so it cannot be cleared.

    Fail closed. A URL this module cannot parse is one whose host and database
    name it does not know, and "unknown" and "permitted" must never be the same
    answer here.
    """


class ProductionDatabaseError(ProductionGuardError):
    """The URL does not describe a database this system may touch.

    The message names what was expected and what was found, because a refusal
    that does not say which rule it failed cannot be acted on — a developer
    with a legitimately-named test database needs to know it was the host, and
    one who pasted the wrong URL needs to know it was the name.

    The URL is rendered with its password removed. This exception is destined
    for a log or a traceback, and a guard that leaked the production credential
    while refusing to use it would be its own kind of incident.
    """

    def __init__(self, part: str, found: str, expected: tuple[str, ...], url: str) -> None:
        self.part = part
        self.found = found
        self.expected = expected
        self.redacted_url = redact(url)
        super().__init__(
            f"refusing to start: the database {part} {found!r} is not permitted.\n"
            f"  expected {part} matching one of: {', '.join(expected)}\n"
            f"  found:    {found!r}\n"
            f"  in URL:   {self.redacted_url}\n"
            "This system truncates and reseeds the database it is pointed at. There is no "
            "override; if this really is a test database, add its pattern to the policy."
        )


@dataclass(frozen=True)
class DatabasePolicy:
    """What counts as a test database.

    Configurable per project, as the acceptance criterion requires, and unable
    to be configured into permitting everything. An empty pattern list or a
    bare `*` is refused at construction: those are not test patterns, they are
    the absence of one, and the story is explicit that no override may exist.
    """

    allowed_schemes: tuple[str, ...] = DEFAULT_ALLOWED_SCHEMES
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    allowed_name_patterns: tuple[str, ...] = DEFAULT_ALLOWED_NAME_PATTERNS

    def __post_init__(self) -> None:
        for label, values in (
            ("allowed_schemes", self.allowed_schemes),
            ("allowed_hosts", self.allowed_hosts),
            ("allowed_name_patterns", self.allowed_name_patterns),
        ):
            if not values:
                message = (
                    f"{label} is empty, which permits nothing and is more likely a mistake "
                    "than an intention"
                )
                raise ProductionGuardError(message)

        vacuous = sorted(set(self.allowed_name_patterns) & _VACUOUS_PATTERNS)
        if vacuous:
            message = (
                f"allowed_name_patterns contains {vacuous}, which matches every database name. "
                "That is an override flag spelled as configuration, and this guard has none."
            )
            raise ProductionGuardError(message)

        if "*" in self.allowed_hosts:
            message = (
                "allowed_hosts contains '*', which permits every host. "
                "That is an override flag spelled as configuration, and this guard has none."
            )
            raise ProductionGuardError(message)

    def permits_name(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatchcase(lowered, p.lower()) for p in self.allowed_name_patterns)


DEFAULT_DATABASE_POLICY = DatabasePolicy()


@dataclass(frozen=True, repr=False)
class VerifiedDatabase:
    """A database URL that has passed the guard. There is no other kind.

    Constructing one *is* the check. Downstream code takes a `VerifiedDatabase`
    rather than a string, so a connection to an unverified database cannot be
    described, let alone opened — which is what makes the ordering requirement
    structural instead of a rule about call sequence.

    Raises:
        UnreadableDatabaseUrlError: the URL could not be parsed into a scheme,
            host and database name.
        ProductionDatabaseError: the URL parsed and is not permitted.
    """

    url: str = field(repr=False)
    policy: DatabasePolicy = DEFAULT_DATABASE_POLICY

    scheme: str = field(init=False, default="")
    host: str = field(init=False, default="")
    port: int | None = field(init=False, default=None)
    name: str = field(init=False, default="")

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        scheme, host, name = parts.scheme, parts.hostname, parts.path.lstrip("/")

        if not scheme or not host or not name:
            message = (
                f"cannot read a scheme, host and database name from {redact(self.url)!r}. "
                "Refusing, because a URL this guard cannot parse is one whose database it "
                "cannot identify."
            )
            raise UnreadableDatabaseUrlError(message)

        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "name", name)
        try:
            object.__setattr__(self, "port", parts.port)
        except ValueError as error:
            message = f"the port in {redact(self.url)!r} is not a number: {error}"
            raise UnreadableDatabaseUrlError(message) from error

        if scheme.lower() not in {s.lower() for s in self.policy.allowed_schemes}:
            raise ProductionDatabaseError("scheme", scheme, self.policy.allowed_schemes, self.url)
        if host.lower() not in {h.lower() for h in self.policy.allowed_hosts}:
            raise ProductionDatabaseError("host", host, self.policy.allowed_hosts, self.url)
        if not self.policy.permits_name(name):
            raise ProductionDatabaseError("name", name, self.policy.allowed_name_patterns, self.url)

    def __repr__(self) -> str:
        """Never the password.

        A frozen dataclass renders every field by default, and this object
        exists to be passed around and logged. The generated repr would put a
        production credential into every traceback that mentioned it.
        """
        return f"VerifiedDatabase({redact(self.url)})"

    def __str__(self) -> str:
        return redact(self.url)


def redact(url: str) -> str:
    """The URL with its password replaced, for anything a human will read.

    Falls back to returning the whole string as redacted if it cannot be
    parsed: a string this function does not understand may still contain a
    credential, and printing it to find out is not an option.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return _REDACTED
    if parts.password is None:
        return url

    userinfo = f"{parts.username or ''}:{_REDACTED}"
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"{userinfo}@{host}{port}", parts.path, parts.query, parts.fragment)
    )

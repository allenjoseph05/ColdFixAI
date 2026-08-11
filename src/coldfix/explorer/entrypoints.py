"""Every way into a repository, and how much each one is worth trying.

Epic 7, S-7.3. The Explorer has an environment (S-7.2) and needs somewhere to
point it. `02-architecture.md` §1.2 names this step *enumerate: routes, CLI entry
points, test cases, job handlers*, and ADR 009 makes it a stage of its own whose
predicate is *at least one candidate route was enumerated*.

**Nothing here calls a model.** Reading `urls.py` is parsing and asking Django
for its route table is a subprocess; `CLAUDE.md` is explicit that neither may be
replaced by a model call.

**A parsed route is a declared route; a resolved route is a registered one.**
This is S-7.1's distinction one level down, and AC 3 is the reason it matters. A
URLconf is *code*, not configuration — a DRF router registers six routes per
viewset when the module imports, `include()` splices another application's table
in under a prefix, and a comprehension over a list of models produces a route per
model. None of those exist in the file as text, so a parser reading `path(...)`
calls does not miss them by being weak; it misses them by being a parser. The
only enumerator that sees them is the framework's own resolver, so this module
runs a program in the subject's interpreter and asks it.

That gives two discovery methods with genuinely different standing, and they are
kept apart rather than merged into one list:

| Discovery | What it establishes | What it cannot |
|---|---|---|
| `PARSED` | this call appears in this file | whether it registers, what prefix it ends up under |
| `RESOLVED` | the framework reports this route as live | nothing — it is the route table |

**So the route table is only ever claimed complete when the framework answered.**
A parse cannot prove completeness of something built by running code, and saying
otherwise would let the Explorer conclude a repository has three endpoints when
it has ninety. `Enumeration.routes_are_complete` is false whenever resolution did
not run, and `unexpanded` names the specific places a parse could see registration
happening and could not follow it — which is the honest form of AC 3 for the case
where the environment is not up yet.

**Ranking predicts S-7.8, and that is what keeps it from being taste.** "Likely
usefulness" needs an anchor or it is a pile of preferences, and there is exactly
one downstream gate: S-7.8 rejects a workload unless query count, response bytes
and wall time all move between N=10 and N=100. So a candidate is useful to the
degree it looks able to pass that, and every rule below is a reason to expect it
will or will not — which also makes the ranking checkable later, against the
measurement, rather than only reviewable.

The ranking is a **prior**, and the module says so in as many words. It orders
what to try; it never concludes anything. S-7.8 is what decides.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from coldfix.bench.execute import ExecutionError, execute
from coldfix.explorer.fingerprint import Detected

RESOLVE_TIMEOUT_SECONDS = 120.0
"""Long enough for a cold `django.setup()` on an unfamiliar project, which
imports every application in `INSTALLED_APPS`. Short enough that a settings
module blocking on a network call is diagnosed rather than waited on."""

# Directories that are never the subject's own entry points and are usually the
# bulk of the tree. `migrations` is here because a Django project can hold
# thousands of them and not one is a way in.
SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "migrations",
        "node_modules",
        "site-packages",
        "venv",
    }
)

# What Django's own URL-declaring callables are named. `url` is the pre-2.0
# spelling and old repositories are explicitly in scope (`00-BRIEF.md` §3: age is
# irrelevant).
_ROUTE_CALLS: frozenset[str] = frozenset({"path", "re_path", "url"})

# Decorators that mark a background job handler. Celery, RQ, Huey and Django-Q
# spell it four ways and all four mean *this function runs off the request path*.
_JOB_DECORATORS: frozenset[str] = frozenset(
    {"shared_task", "task", "periodic_task", "db_task", "db_periodic_task", "job", "background"}
)

# Route segments owned by the framework or by the plumbing rather than by the
# application. Ranked last, never dropped — AC 1 is enumeration and AC 2 is order.
#
# Two groups, and both are here for the same reason: neither does work that grows
# with the data, so neither can pass S-7.8. The first is served by the framework
# or by a probe; the second is an authentication flow, which is per-user by
# definition — a login page costs the same against ten rows and ten million.
#
# Deliberately short. `08-audit.md`'s recurring lesson is that a detector needs a
# control, and the control here is that ordinary application routes must survive:
# every word below names plumbing in every Django project, and none of them is a
# domain noun somebody's `books/` endpoint might be called.
_INFRASTRUCTURE: frozenset[str] = frozenset(
    {
        "__debug__",
        "admin",
        "favicon",
        "health",
        "healthz",
        "jsi18n",
        "liveness",
        "login",
        "logout",
        "media",
        "metrics",
        "oauth",
        "password",
        "ping",
        "readiness",
        "robots",
        "signup",
        "silk",
        "sso",
        "static",
        "two_factor",
    }
)

# Words that suggest a route addresses a *set*. Weak on its own, which is why it
# is worth one point and the absence of path parameters is worth four.
_COLLECTION_WORDS: frozenset[str] = frozenset(
    {
        "all",
        "dashboard",
        "export",
        "feed",
        "index",
        "list",
        "report",
        "reports",
        "search",
        "stats",
        "summary",
    }
)

_SETTINGS_MODULE = re.compile(
    r"""DJANGO_SETTINGS_MODULE["']\s*,\s*["']([A-Za-z0-9_.]+)["']""",
)

# The introspection program's answer is prefixed with this, because a subject's
# `django.setup()` may print — a deprecation warning, an application's own banner
# — and `json.loads(stdout)` on such a project fails on output that is not an
# error. The marker makes the answer findable in a stream we do not control.
_MARKER = "<<<COLDFIX-ENTRY-POINTS>>>"

# Runs in the *subject's* interpreter, not ours. Kept as source text rather than
# a module for that reason: nothing under `src/` may import Django, and this has
# to run under whatever interpreter and version the subject resolved to.
#
# The broad `except` around `url_patterns` is reporting, not swallowing: one
# application's broken `include()` must not cost the enumeration every other
# route, and the error travels back in `problems` rather than being discarded.
_INTROSPECT_SOURCE = """
import json, os, sys

sys.path.insert(0, os.getcwd())

import django
django.setup()

from django.core.management import get_commands
from django.urls import get_resolver
from django.urls.resolvers import URLResolver

MAX_DEPTH = 12
routes = []
problems = []


def view_of(entry):
    callback = getattr(entry, "callback", None)
    if callback is None:
        return None
    name = getattr(callback, "__qualname__", None) or type(callback).__name__
    return getattr(callback, "__module__", "?") + "." + name


def walk(resolver, prefix, depth):
    if depth > MAX_DEPTH:
        problems.append(prefix + ": nested deeper than " + str(MAX_DEPTH) + ", not followed")
        return
    try:
        patterns = resolver.url_patterns
    except Exception as error:
        problems.append(prefix + ": " + type(error).__name__ + ": " + str(error))
        return
    for entry in patterns:
        text = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            walk(entry, text, depth + 1)
        else:
            routes.append({"pattern": text, "name": entry.name, "view": view_of(entry)})


walk(get_resolver(), "", 0)
answer = {"routes": routes, "commands": get_commands(), "problems": problems}
print("__MARKER__" + json.dumps(answer))
"""

# Substituted rather than interpolated: the program is full of braces and an
# f-string would fight every one of them.
_INTROSPECT = _INTROSPECT_SOURCE.replace("__MARKER__", _MARKER)


class EnumerationError(Exception):
    """Entry points could not be enumerated."""


class Kind(StrEnum):
    """The five things AC 1 asks for. Ordered here by nothing; `rank` decides."""

    HTTP_ROUTE = "HTTP route"
    MANAGEMENT_COMMAND = "management command"
    CLI_ENTRY_POINT = "CLI entry point"
    JOB_HANDLER = "background job handler"
    INTEGRATION_TEST = "integration test"


class Discovery(StrEnum):
    """How a candidate was found, which is how much its existence is worth.

    Not a detail of implementation: a parsed route may never register and a
    resolved one certainly did, and an enumeration that flattened the two would
    report a guess and a measurement in the same column.
    """

    PARSED = "read from a file"
    RESOLVED = "reported by the framework itself"


@dataclass(frozen=True)
class Candidate:
    """One way into the subject, and where it came from."""

    kind: Kind
    name: str
    """The route pattern, command name, script name, dotted function or test file."""

    evidence: str
    """The file that declared it, or the resolver that reported it."""

    discovery: Discovery
    target: str | None = None
    """What runs — a view's dotted path, where that is knowable."""

    owner: str | None = None
    """Which application it belongs to. `django.core` means the framework's own."""

    route_name: str | None = None
    """The framework's own name for a route, which is how the subject's code
    refers to it (`reverse("book-list")`) and how S-7.9 will address it."""

    @property
    def parameters(self) -> tuple[str, ...]:
        """The path parameters a route requires before it can be requested.

        Both spellings: Django's `<int:pk>` converters and a regular
        expression's named groups.

        **The named groups are taken out before the converters are looked for**,
        because `(?P<pk>\\d+)` contains `<pk>` and matches both patterns. Counted
        twice it is one parameter charged the penalty twice, which would rank
        every pre-2.0 repository's routes below every modern one for a reason
        that is about regular expressions rather than about the routes.
        """
        if self.kind is not Kind.HTTP_ROUTE:
            return ()
        groups = re.findall(r"\(\?P<([^>]+)>", self.name)
        converters = re.findall(r"<(?:[^:>]+:)?([^>]+)>", re.sub(r"\(\?P<[^>]+>", "(", self.name))
        return tuple(groups + converters)

    def describe(self) -> str:
        target = f" → {self.target}" if self.target else ""
        return f"{self.kind.value} {self.name}{target} ({self.discovery.value}: {self.evidence})"


@dataclass(frozen=True)
class Unexpanded:
    """A place where a file registers routes that reading it cannot enumerate.

    AC 3's honest half. When the framework has answered these are redundant —
    the resolver already expanded them — but when it has not, this is the
    difference between *the subject has four routes* and *the subject has four
    routes that could be read plus a router whose contents are unknown*.
    """

    evidence: str
    construct: str
    reason: str

    def describe(self) -> str:
        return f"{self.evidence}: {self.construct} — {self.reason}"


@dataclass(frozen=True)
class Resolution:
    """What the framework said when asked, or why it could not be asked.

    A failure is carried rather than raised. Resolution needs a stood-up
    environment and the Explorer routinely enumerates before it has one — S-7.11
    puts *find endpoint* after *configure* for that reason — so *not resolved
    yet* is an ordinary state and must not cost the caller the parse.
    """

    available: bool
    settings_module: Detected[str] | None = None
    error: str | None = None
    problems: tuple[str, ...] = ()
    """Places the resolver itself could not follow, in its own words."""

    def describe(self) -> str:
        if not self.available:
            return f"route table not resolved: {self.error}"
        found = self.settings_module.describe() if self.settings_module else "settings not named"
        lines = [f"route table resolved via {found}"]
        lines.extend(f"  could not follow {problem}" for problem in self.problems)
        return "\n".join(lines)


@dataclass(frozen=True)
class Scored:
    """A candidate, its score, and the reasons that produced it.

    The reasons are not decoration. The score is a *prior* about whether this
    candidate can pass S-7.8, and a prior nobody can read is one nobody can
    correct when the ranking sends the Explorer at a health check nine times.
    """

    candidate: Candidate
    score: int
    reasons: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.score:+3d}  {self.candidate.describe()}\n      {'; '.join(self.reasons)}"


@dataclass(frozen=True)
class Enumeration:
    """Every way in that could be found, in the order worth trying them."""

    root: Path
    scored: tuple[Scored, ...]
    unexpanded: tuple[Unexpanded, ...]
    resolution: Resolution
    files_read: int = 0

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(entry.candidate for entry in self.scored)

    def of_kind(self, kind: Kind) -> tuple[Candidate, ...]:
        return tuple(entry.candidate for entry in self.scored if entry.candidate.kind is kind)

    @property
    def routes_are_complete(self) -> bool:
        """Whether the route table can be claimed whole.

        True only when the framework answered **and had no part of the table it
        could not follow**. A parse cannot establish this about something built
        by running code, and ADR 009's *endpoint* predicate would otherwise be
        satisfiable by a repository whose routes are all registered by a router.

        The second half matters more than it looks: an application whose URLconf
        does not import produces an *answer* — zero routes and one problem — and
        counting that as complete would report a repository as having no
        endpoints when what happened is that nobody could read them.
        """
        return self.resolution.available and not self.resolution.problems

    @property
    def dynamically_registered(self) -> tuple[Candidate, ...]:
        """Routes the framework reported that reading the files did not.

        The measured form of AC 3 — the routes a parser misses, named rather
        than asserted. Empty when resolution did not run, because nothing was
        compared.

        **The comparison is on the last literal segment, and it deliberately
        under-reports.** The two tables do not share a spelling: a parse yields
        the *fragment* `books/`, and the same route reaches the resolver as
        `api/books/` because an `include()` put a prefix on it, so comparing the
        strings would report every included route as dynamic. The last literal
        segment survives the prefix. It also makes a list route and its detail
        route look alike, so a genuinely dynamic route sharing a segment with a
        parsed one is missed — which is the safe direction, since the claim this
        list supports is *at least these*, and `routes_are_complete` is what
        carries the load-bearing one.
        """
        if not self.resolution.available:
            return ()
        seen = {
            _last_literal(entry.candidate.name)
            for entry in self.scored
            if entry.candidate.kind is Kind.HTTP_ROUTE
            and entry.candidate.discovery is Discovery.PARSED
        }
        return tuple(
            entry.candidate
            for entry in self.scored
            if entry.candidate.kind is Kind.HTTP_ROUTE
            and entry.candidate.discovery is Discovery.RESOLVED
            and _last_literal(entry.candidate.name) not in seen
        )

    def describe(self) -> str:
        lines = [f"Entry points under {self.root} ({self.files_read} files read)"]
        lines.append("  " + self.resolution.describe().replace("\n", "\n  "))
        if not self.routes_are_complete:
            lines.append(
                "  The route table is INCOMPLETE: it was read from files, and a URLconf is code. "
                "Routes registered by a router, a loop or an include are not in this list."
            )
        for entry in self.unexpanded:
            lines.append(f"  unexpanded: {entry.describe()}")
        lines.append(
            "  Ranked by whether the candidate looks able to pass S-7.8's work check. "
            "This is a prior about what to try, not a measurement of anything."
        )
        lines.extend("  " + entry.describe() for entry in self.scored)
        return "\n".join(lines)


def _text(value: object) -> str | None:
    """A string from the subprocess's JSON, or nothing. Never a stringified `None`."""
    return value if isinstance(value, str) else None


def _last_literal(pattern: str) -> str:
    """The most distinctive fixed segment of a route: the last one holding no parameter.

    `api/books/<int:pk>/` and `books/<int:pk>/` both reduce to `books`, which is
    what lets a parsed fragment be compared with a resolved path at all.
    """
    literal = [
        segment.strip()
        for segment in pattern.strip("^$").split("/")
        if segment.strip() and "<" not in segment and "(" not in segment
    ]
    return literal[-1] if literal else ""


# ================================================================== parsing


def _python_files(root: Path) -> Iterator[Path]:
    """Every Python file that could be the subject's own, cheapest walk first."""
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in SKIP_DIRECTORIES and not name.endswith(".egg-info")
        )
        for name in sorted(names):
            if name.endswith(".py"):
                yield Path(directory) / name


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        # A repository written for a Python this interpreter cannot parse is an
        # ordinary case (`00-BRIEF.md` §3), and one unreadable file must not cost
        # the enumeration the rest of the tree.
        return None


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _routes_in(tree: ast.Module, evidence: str) -> tuple[list[Candidate], list[Unexpanded]]:
    """Route calls whose pattern is a literal, and the registrations that are not.

    The second list is the point. A `path()` whose first argument is not a
    constant — an f-string, a variable, a name built in a comprehension — is a
    route this cannot name, and a `router.register(...)` is six of them. Both are
    recorded as places the file registers routes that reading it cannot expand.
    """
    found: list[Candidate] = []
    unexpanded: list[Unexpanded] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called_name(node)

        if called in _ROUTE_CALLS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if _mounts_another_urlconf(node):
                    # Not an entry point. Requesting the prefix alone returns 404
                    # — and ranked as a parameterless route it would sit at the
                    # top of the list the Explorer works down.
                    unexpanded.append(
                        Unexpanded(
                            evidence=evidence,
                            construct=f"include(...) mounted at {first.value!r}",
                            reason="another URLconf is spliced in here; which routes end up "
                            "under this prefix is decided when it imports",
                        )
                    )
                    continue
                found.append(
                    Candidate(
                        kind=Kind.HTTP_ROUTE,
                        name=first.value,
                        evidence=evidence,
                        discovery=Discovery.PARSED,
                        target=_route_target(node),
                    )
                )
            else:
                unexpanded.append(
                    Unexpanded(
                        evidence=evidence,
                        construct=f"{called}(...) with a computed pattern",
                        reason="the pattern is built at import time and is not in the file as text",
                    )
                )
        elif called == "register":
            unexpanded.append(
                Unexpanded(
                    evidence=evidence,
                    construct="a router registration",
                    reason="a router generates its routes when the module imports; "
                    "only the resolver can list them",
                )
            )

    return found, unexpanded


def _mounts_another_urlconf(node: ast.Call) -> bool:
    """Whether this route's second argument is an `include(...)` rather than a view."""
    return (
        len(node.args) > 1
        and isinstance(node.args[1], ast.Call)
        and _called_name(node.args[1]) == "include"
    )


def _route_target(node: ast.Call) -> str | None:
    """What a parsed route points at, where the call says so plainly."""
    if len(node.args) < 2:  # noqa: PLR2004 - `path(pattern, view)`; there is no view without a second argument
        return None
    view = node.args[1]
    if isinstance(view, ast.Name):
        return view.id
    if isinstance(view, ast.Attribute):
        return view.attr
    if isinstance(view, ast.Call):
        return f"{_called_name(view)}(...)"
    return None


def _jobs_in(tree: ast.Module, evidence: str) -> list[Candidate]:
    found: list[Candidate] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = (
                call.attr
                if isinstance(call, ast.Attribute)
                else call.id
                if isinstance(call, ast.Name)
                else ""
            )
            if name in _JOB_DECORATORS:
                found.append(
                    Candidate(
                        kind=Kind.JOB_HANDLER,
                        name=node.name,
                        evidence=f"{evidence}:{node.lineno}",
                        discovery=Discovery.PARSED,
                        target=f"@{name}",
                    )
                )
                break
    return found


def _is_test_file(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "tests.py"


def _management_command(root: Path, path: Path) -> Candidate | None:
    """`app/management/commands/name.py` is Django's own layout for one.

    The owning application is the directory two above `commands`, which is what
    separates the subject's batch job from `collectstatic`.
    """
    parts = _relative(root, path).split("/")
    if len(parts) < 4 or parts[-3:-1] != ["management", "commands"]:  # noqa: PLR2004 - app/management/commands/name.py is four parts
        return None
    name = parts[-1].removesuffix(".py")
    if name.startswith("_"):
        return None
    return Candidate(
        kind=Kind.MANAGEMENT_COMMAND,
        name=name,
        evidence=_relative(root, path),
        discovery=Discovery.PARSED,
        owner=parts[-4],
    )


def _console_scripts(root: Path) -> list[Candidate]:
    found: list[Candidate] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            parsed = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except (OSError, tomllib.TOMLDecodeError):
            parsed = {}
        project = parsed.get("project", {})
        declared: list[tuple[str, str]] = list((project.get("scripts", {}) or {}).items())
        entry_points = project.get("entry-points", {}) or {}
        declared.extend((entry_points.get("console_scripts", {}) or {}).items())
        poetry = parsed.get("tool", {}).get("poetry", {}).get("scripts", {}) or {}
        declared.extend(poetry.items())
        found.extend(
            Candidate(
                kind=Kind.CLI_ENTRY_POINT,
                name=str(name),
                evidence="pyproject.toml",
                discovery=Discovery.PARSED,
                target=str(target),
            )
            for name, target in declared
        )

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        text = setup_cfg.read_text(encoding="utf-8", errors="replace")
        section = re.search(r"console_scripts\s*=\s*\n((?:\s+\S.*\n?)+)", text)
        if section:
            for line in section.group(1).splitlines():
                if "=" in line:
                    name, _, target = line.partition("=")
                    found.append(
                        Candidate(
                            kind=Kind.CLI_ENTRY_POINT,
                            name=name.strip(),
                            evidence="setup.cfg",
                            discovery=Discovery.PARSED,
                            target=target.strip(),
                        )
                    )

    return found


@dataclass
class ParsedEntryPoints:
    candidates: list[Candidate] = field(default_factory=list)
    unexpanded: list[Unexpanded] = field(default_factory=list)
    files_read: int = 0


def parse_entry_points(root: Path) -> ParsedEntryPoints:
    """Everything a repository states about its own entry points, without running it.

    Works before the environment does, which is why it exists separately: ADR
    009's *clone* predicate is *a checkout exists and an entry point was
    located*, and that is checked long before anything is installed.
    """
    root = Path(root)
    parsed = ParsedEntryPoints(candidates=_console_scripts(root))

    for path in _python_files(root):
        relative = _relative(root, path)
        parsed.files_read += 1

        command = _management_command(root, path)
        if command is not None:
            parsed.candidates.append(command)
            continue

        if _is_test_file(relative):
            parsed.candidates.append(
                Candidate(
                    kind=Kind.INTEGRATION_TEST,
                    name=relative,
                    evidence=relative,
                    discovery=Discovery.PARSED,
                )
            )
            continue

        tree = _parse(path)
        if tree is None:
            continue

        if path.name == "urls.py" or "urlpatterns" in {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }:
            routes, unexpanded = _routes_in(tree, relative)
            parsed.candidates.extend(routes)
            parsed.unexpanded.extend(unexpanded)

        parsed.candidates.extend(_jobs_in(tree, relative))

    return parsed


# ================================================================== resolving


def settings_module(root: Path) -> Detected[str] | None:
    """Which module Django is told to configure itself from.

    Read from the files that set it rather than guessed from the directory
    layout: `manage.py` first because it is the one a project always has, then
    the server entry points, which are what a deployment actually runs.
    """
    manage = root / "manage.py"
    if manage.is_file():
        match = _SETTINGS_MODULE.search(manage.read_text(encoding="utf-8", errors="replace"))
        if match:
            return Detected(match.group(1), "manage.py")

    # The same bounded walk the parse uses, rather than `rglob`, which would
    # descend into an installed virtualenv where every package has a `wsgi.py`.
    for path in _python_files(root):
        if path.name not in ("wsgi.py", "asgi.py"):
            continue
        match = _SETTINGS_MODULE.search(path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return Detected(match.group(1), _relative(root, path))
    return None


def resolve_entry_points(
    root: Path,
    *,
    python: Sequence[str],
    timeout: float = RESOLVE_TIMEOUT_SECONDS,
) -> tuple[list[Candidate], Resolution]:
    """Ask the framework for its own route table and command list.

    `python` is the subject's interpreter — `["python"]` inside its container,
    or whatever E14's adapter knows launches it. Supplied rather than derived,
    the convention S-7.2 set: what runs *this* project is a fact about its
    tooling, and this module's job is to know what to ask, not where.
    """
    root = Path(root)
    settings = settings_module(root)
    if settings is None:
        return [], Resolution(
            available=False,
            error=(
                "no DJANGO_SETTINGS_MODULE was found in manage.py, wsgi.py or asgi.py, so the "
                "framework cannot be configured to answer"
            ),
        )

    try:
        result = execute(
            [*python, "-c", _INTROSPECT],
            timeout=timeout,
            cwd=root,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": settings.value},
        )
    except ExecutionError as error:
        return [], Resolution(available=False, settings_module=settings, error=str(error))

    line = next((row for row in result.stdout.splitlines() if row.startswith(_MARKER)), None)
    if line is None:
        said = (result.stderr or result.stdout).strip()[-600:]
        return [], Resolution(
            available=False,
            settings_module=settings,
            error=f"the subject's interpreter did not answer (exit {result.exit_code}): {said}",
        )

    try:
        # `Any` at a subprocess boundary: this is another interpreter's JSON and
        # nothing here can know its shape statically. Every field is converted
        # below rather than trusted.
        payload: dict[str, Any] = json.loads(line.removeprefix(_MARKER))
    except json.JSONDecodeError as error:
        return [], Resolution(available=False, settings_module=settings, error=str(error))

    found = [
        Candidate(
            kind=Kind.HTTP_ROUTE,
            name=str(route.get("pattern", "")),
            evidence=f"the resolver ({settings.value})",
            discovery=Discovery.RESOLVED,
            target=_text(route.get("view")),
            route_name=_text(route.get("name")),
        )
        for route in payload.get("routes", [])
    ]
    found.extend(
        Candidate(
            kind=Kind.MANAGEMENT_COMMAND,
            name=str(name),
            evidence=f"the resolver ({settings.value})",
            discovery=Discovery.RESOLVED,
            owner=str(app),
        )
        for name, app in sorted((payload.get("commands", {}) or {}).items())
    )
    return found, Resolution(
        available=True,
        settings_module=settings,
        problems=tuple(str(problem) for problem in payload.get("problems", [])),
    )


# ================================================================== ranking

# What each kind costs to turn into a workload, before anything about the
# individual candidate is considered. Every number here is a claim about S-7.8:
# can this thing be driven at two scales and have its work observed?
_KIND_SCORE: dict[Kind, tuple[int, str]] = {
    Kind.HTTP_ROUTE: (
        4,
        "an HTTP route is driven over a socket and its response bytes are S-7.8's "
        "measure without instrumenting the subject",
    ),
    Kind.MANAGEMENT_COMMAND: (
        3,
        "a management command runs the subject's own batch work, but its payload is "
        "stdout and many of them mutate",
    ),
    Kind.JOB_HANDLER: (
        2,
        "a job handler does real work off the request path, but invoking it means "
        "driving a broker or holding the application context open",
    ),
    Kind.INTEGRATION_TEST: (
        2,
        "an integration test seeds itself, but its scale is fixed by its own fixtures "
        "and S-2.4 refuses to edit a test to change them",
    ),
    Kind.CLI_ENTRY_POINT: (
        1,
        "a console script may not touch the subject's database at all",
    ),
}

# Endings that make a word look plural without being one. `status`, `address`,
# `process` and `analysis` are all ordinary route segments.
_NOT_PLURAL: tuple[str, ...] = ("ss", "us", "is", "sis", "ous")
_SHORTEST_PLURAL = 3

_INFRASTRUCTURE_PENALTY = -10
_PARAMETER_PENALTY = -2

# Gentler than a parameter, because depth is a hint and a parameter is a fact.
# It exists because without it netbox ranks thirty-nine routes level at the top
# and the order among them is alphabetical — a ranking that does not rank.
_DEPTH_PENALTY = -1
_COLLECTION_BONUS = 4
_NAME_BONUS = 1
_INTEGRATION_BONUS = 2


def _names_a_collection(name: str) -> bool:
    """Whether the name suggests the candidate addresses many things.

    A plural noun is the commonest spelling of a list route in every framework
    (`books/`, `invoices/`), so it counts — but only as a plural, which means
    excluding the words that end in `s` without being one. Worth a single point:
    a name is a hint and the absence of path parameters is a structural fact.
    """
    words = {word for word in re.split(r"[^a-z0-9]+", name.lower()) if word}
    if words & _COLLECTION_WORDS:
        return True
    return any(
        word.endswith("s") and not word.endswith(_NOT_PLURAL) and len(word) > _SHORTEST_PLURAL
        for word in words
    )


def _segments(name: str) -> list[str]:
    """A route's literal segments, lowercased. Parameters are not segments."""
    return [
        segment.strip().lower()
        for segment in name.strip("^$").split("/")
        if segment.strip() and "<" not in segment and "(" not in segment
    ]


def _looks_like_infrastructure(candidate: Candidate) -> bool:
    """Whether the candidate belongs to the framework or the plumbing.

    **Any segment, not just the first.** A login page costs the same against ten
    rows and ten million whether it is mounted at `login/` or at
    `accounts/login/`, so it can never pass S-7.8 either way — and a rule that
    depended on where an application chose to mount its auth would be about URL
    layout rather than about what the route does.
    """
    if candidate.owner is not None and candidate.owner.startswith("django."):
        return True
    return any(
        segment in _INFRASTRUCTURE or segment.split(".", 1)[0] in _INFRASTRUCTURE
        for segment in _segments(candidate.name)
    )


def score(candidate: Candidate) -> Scored:
    """How much this candidate looks like a workload, and why.

    Every term is a reason to expect S-7.8 to accept or reject it. Nothing here
    measures anything — a route that scores nine may still return a constant.
    """
    base, reason = _KIND_SCORE[candidate.kind]
    total = base
    reasons = [reason]

    if _looks_like_infrastructure(candidate):
        total += _INFRASTRUCTURE_PENALTY
        reasons.append(
            "framework or infrastructure, not the application: it is either code this "
            "system refuses to patch or an endpoint designed to do no work"
        )

    if candidate.kind is Kind.HTTP_ROUTE:
        depth = len(_segments(candidate.name)) - 1
        if depth > 0:
            total += _DEPTH_PENALTY * depth
            reasons.append(
                f"{depth} segment(s) below the top level, and a deeper path names a "
                "narrower thing — a step in a flow rather than a collection"
            )

        parameters = candidate.parameters
        if parameters:
            total += _PARAMETER_PENALTY * len(parameters)
            reasons.append(
                f"needs {', '.join(parameters)} before it can be requested, and a route "
                "addressing one object returns one object at every scale"
            )
        else:
            total += _COLLECTION_BONUS
            reasons.append(
                "takes no path parameter, so it addresses a set — and a set is what grows "
                "when S-7.8 seeds from N=10 to N=100"
            )

    if _names_a_collection(candidate.name):
        total += _NAME_BONUS
        reasons.append("named like a collection or an aggregation")

    if candidate.kind is Kind.INTEGRATION_TEST and any(
        word in candidate.name.lower() for word in ("integration", "functional", "e2e", "smoke")
    ):
        total += _INTEGRATION_BONUS
        reasons.append("named as an integration test rather than a unit test")

    return Scored(candidate=candidate, score=total, reasons=tuple(reasons))


def rank(candidates: Iterable[Candidate]) -> tuple[Scored, ...]:
    """Highest first, and deterministic.

    The tie-break is on the candidate itself rather than on discovery order,
    because two enumerations of one repository that disagree on order would make
    every downstream comparison — S-13.5's learning curve most of all — measure
    the walk instead of the repository.
    """
    return tuple(
        sorted(
            (score(candidate) for candidate in candidates),
            key=lambda entry: (-entry.score, entry.candidate.kind.name, entry.candidate.name),
        )
    )


def enumerate_entry_points(
    root: Path,
    *,
    python: Sequence[str] | None = None,
    timeout: float = RESOLVE_TIMEOUT_SECONDS,
) -> Enumeration:
    """Every way into this repository, ranked, with the route table's standing stated.

    With `python`, the framework is asked and the route table is complete. Without
    it, the files are read and the result says plainly that it is not — which is
    the answer ADR 009's *clone* stage needs before an environment exists.
    """
    root = Path(root)
    parsed = parse_entry_points(root)
    candidates = list(parsed.candidates)

    resolution = Resolution(
        available=False,
        error="not attempted: no interpreter was supplied to ask the framework with",
    )
    if python is not None:
        resolved, resolution = resolve_entry_points(root, python=python, timeout=timeout)
        candidates.extend(resolved)

    return Enumeration(
        root=root,
        scored=rank(candidates),
        unexpanded=tuple(parsed.unexpanded),
        resolution=resolution,
        files_read=parsed.files_read,
    )

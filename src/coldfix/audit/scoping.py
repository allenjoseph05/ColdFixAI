"""Who else calls the code the patch changed, and did anybody test them.

Epic 11, S-11.5. *`find_callers` locates other call sites of every modified
symbol. Runs the full test suite. Reports callers outside the tested workload.*

**The whole investigation looked at one workload.** The Diagnostician measured one
endpoint, the falsification test drives that endpoint, and S-10.4 confines the
patch to the files the evidence implicates. Every one of those is a statement
about the same narrow slice — and the symbol the patch rewrote is called from
wherever it is called from, which nothing upstream has any reason to know.

So this story asks the question none of the others can: **the change was verified
against one caller; who are the others?**

**Names, not bindings, and that is a limit rather than a shortcut.** Python
resolves attributes at run time, so no static pass can say which
`to_representation` a given call reaches. Matching by name is *over-inclusive* —
another class with a method of the same name is reported — and **still
under-inclusive**, because `getattr(obj, name)()`, a dispatch table and a
framework hook are all invisible. Over-inclusive is the direction to fail in: a
caller reported that turns out to be unrelated costs a reader a glance, and one
that is missed is the regression shipping. Both directions are in `RESIDUE`
rather than implied.

**The suite runs on both revisions, and that is the only way its answer means
anything.** A repository whose tests already fail — a stale snapshot, a missing
service, a flake — makes every patch look like it broke something, and an audit
that ran the suite once against the patched code would report that as the patch's
doing. Running the original too is what separates *this patch broke the suite*
from *the suite was already broken*, and the second establishes nothing in either
direction. S-11.2's control against nondeterminism and S-11.3's control against
framework warm-up, arriving a third time at the same shape.

**AC 3's *tested workload* is `scope_of`, reused.** S-10.4 already answers *which
files does this finding's evidence implicate* and uses it to confine the patch. A
caller inside that set is one the investigation looked at; a caller outside it is
a call site no experiment here has ever exercised. Deriving a second notion of
scope would let the patch be confined by one definition and audited against
another.

**Parsing is not judgement.** This module reports call sites and suite results. It
does not decide that a caller is broken — it cannot, without a test for that
caller — and `clean` means *no caller outside the evidence and the suite still
passes*, which is a much smaller claim than *safe*.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError
from coldfix.diagnosis.chain import EvidenceChain
from coldfix.repair.patch import scope_of
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.patching import Side, hunk_ranges

DEFAULT_SUITE_TIMEOUT_SECONDS = 1_800.0
"""Half an hour. A falsification test drives one endpoint; a full suite is the
whole repository, and one that cannot finish inside this is one nobody runs in CI
either."""

MODULE_SCOPE = "<module>"
"""What encloses a change that sits outside every definition — an import, a
constant, a decorator argument."""

RESIDUE = (
    "Callers are matched by name, because Python resolves attributes at run time and "
    "no static pass can say which `save` a call reaches. That is wrong in both "
    "directions at once: an unrelated class with a method of the same name is reported "
    "here, and `getattr(obj, name)()`, a dispatch table, a template, a signal handler "
    "and a framework hook are not reported at all. A short list is not evidence that "
    "few things call this."
)


class ScopeError(Exception):
    """The scope audit could not be carried out."""


class Reference(StrEnum):
    """How a modified symbol is reached at one site.

    Two, because a name that is passed rather than called is still a dependency on
    the thing the patch changed — `map(serializer.to_representation, rows)` breaks
    exactly as hard as a call does, and a pass collecting only `Call` nodes would
    report that file as untouched.
    """

    CALL = "called"
    PASSED = "referred to without being called"


class Unreadable(StrEnum):
    """Why a file the patch touched produced no symbols."""

    NOT_SUPPLIED = "the source was not supplied to this audit"
    NOT_PYTHON = "not a Python file, so nothing here can parse it"
    UNPARSABLE = "the source did not parse"


@dataclass(frozen=True)
class Symbol:
    """A definition the patch changed."""

    path: str
    qualname: str
    """`BookSerializer.to_representation`, or `<module>` for a change outside
    every definition."""

    first_line: int
    last_line: int

    @property
    def name(self) -> str:
        """The bare name a call site would use."""
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def callable_symbol(self) -> bool:
        """Whether there is a name for callers to have used.

        A module-level change — an import, a constant, a decorator argument — has
        no such name, and hunting for callers of `<module>` would return every
        line in the repository or none.
        """
        return self.qualname != MODULE_SCOPE

    def describe(self) -> str:
        return f"{self.path}:{self.first_line}-{self.last_line} {self.qualname}"


@dataclass(frozen=True)
class CallSite:
    """One place a modified symbol's name appears, and what encloses it."""

    path: str
    line: int
    inside: str
    kind: Reference
    text: str

    def describe(self) -> str:
        return f"{self.path}:{self.line} in {self.inside} — {self.kind.value}: {self.text.strip()}"


@dataclass(frozen=True)
class Caller:
    """A modified symbol, and somewhere else that reaches it."""

    symbol: Symbol
    site: CallSite

    @property
    def is_the_definition(self) -> bool:
        """Whether this site is inside the symbol's own body — a recursive call.

        Not a caller anybody needs warning about, and reporting it would put every
        recursive function on the list.
        """
        return (
            self.site.path == self.symbol.path
            and self.symbol.first_line <= self.site.line <= self.symbol.last_line
        )


class SuiteOutcome(StrEnum):
    """What running the whole suite on both revisions established.

    Four, and the middle two are why this is not a boolean. A suite that already
    failed says nothing about the patch, and reporting it as a breakage would
    send the Surgeon to fix somebody else's test.
    """

    PASSED_ON_BOTH = "the suite passed before the change and after it"
    BROKEN_BY_THE_PATCH = "the suite passed before the change and fails after it"
    ALREADY_BROKEN = "the suite failed before the change, so its failure now proves nothing"
    NOT_RUN = "the suite could not be run on both revisions"

    @property
    def informative(self) -> bool:
        """Whether the run said anything about the patch at all."""
        return self in {SuiteOutcome.PASSED_ON_BOTH, SuiteOutcome.BROKEN_BY_THE_PATCH}


@dataclass(frozen=True)
class SuiteRun:
    """AC 2, with the control that makes it mean something."""

    outcome: SuiteOutcome
    original_exit: int | None
    patched_exit: int | None
    evidence: str

    @property
    def broke_it(self) -> bool:
        return self.outcome is SuiteOutcome.BROKEN_BY_THE_PATCH

    def describe(self) -> str:
        codes = (
            f"original exit {_show(self.original_exit)}, patched exit {_show(self.patched_exit)}"
        )
        lines = [f"  Full suite: {self.outcome.value} ({codes})."]
        if self.outcome is SuiteOutcome.ALREADY_BROKEN:
            lines.append(
                "    A suite that was failing before the change cannot say whether the change "
                "broke anything. Fix the suite, then ask again — this is not a result about "
                "the patch."
            )
        if self.evidence.strip():
            lines.append(f"    {self.evidence.strip()[:600]}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ScopeAudit:
    """Everything the patch changed, everyone who reaches it, and who tested them."""

    symbols: tuple[Symbol, ...]
    callers: tuple[Caller, ...]
    suite: SuiteRun
    scope: frozenset[str]
    unreadable: Mapping[str, Unreadable]

    @property
    def outside(self) -> tuple[Caller, ...]:
        """**AC 3.** Callers in files the evidence never implicated.

        The investigation measured one workload and the patch was confined to the
        files that workload's evidence named. These call sites are in neither, so
        nothing in this investigation has run them.
        """
        return tuple(
            caller
            for caller in self.callers
            if caller.site.path not in self.scope and not caller.is_the_definition
        )

    @property
    def inside(self) -> tuple[Caller, ...]:
        return tuple(
            caller
            for caller in self.callers
            if caller.site.path in self.scope and not caller.is_the_definition
        )

    @property
    def complete(self) -> bool:
        """Whether every file the patch touched could actually be read."""
        return not self.unreadable

    @property
    def clean(self) -> bool:
        """No caller outside the evidence, the suite still passes, and everything
        the patch touched was readable.

        **A much smaller claim than *safe*.** `RESIDUE` says why: a name-matching
        pass cannot see a dispatch table, so an empty `outside` is the absence of
        evidence of other callers and not evidence of their absence.
        """
        return (
            not self.outside and self.suite.outcome is SuiteOutcome.PASSED_ON_BOTH and self.complete
        )

    def describe(self) -> str:
        lines = [
            f"SCOPE AUDIT — {len(self.symbols)} symbols changed, {len(self.callers)} call "
            f"sites found, {len(self.outside)} of them outside the evidence.",
            "  Changed:",
        ]
        lines.extend(f"    {symbol.describe()}" for symbol in self.symbols)
        lines.append(f"  The evidence implicates: {sorted(self.scope)}")

        if self.outside:
            lines.append(
                "  **Callers outside the tested workload.** The investigation measured one "
                "workload and verified the patch against it. These call the changed code and "
                "no experiment here has ever run them:"
            )
            lines.extend(f"    ! {caller.site.describe()}" for caller in self.outside)
        else:
            lines.append("  No call site outside the implicated files was found.")

        lines.extend(self.suite.describe().splitlines())

        if self.unreadable:
            unread = ", ".join(
                f"{path} ({why.value})" for path, why in sorted(self.unreadable.items())
            )
            lines.append(
                f"  **{len(self.unreadable)} files the patch touched produced no symbols** "
                f"({unread}), so nothing was looked up for them."
            )
        lines.append(f"  {RESIDUE}")
        return "\n".join(lines)


def modified_symbols(
    diff: str, sources: Mapping[str, str]
) -> tuple[tuple[Symbol, ...], Mapping[str, Unreadable]]:
    """AC 1's first half: which definitions the patch changed.

    **New-side line numbers, which is why S-10.5's `hunk_ranges` grew a `side`
    parameter.** The symbols are looked up in the *patched* source, and there the
    original numbering points at whatever the edit shifted — a hunk that inserted
    five lines above a method would find the method five lines short of where it
    now is, and attribute the change to whatever used to be there.

    Innermost definition wins, so a change inside a method is attributed to the
    method rather than to its class. A change outside every definition gets
    `MODULE_SCOPE`, which is reported and has no callers to look for.

    Returns the symbols and the files that produced none, because a file whose
    source was not supplied and a file with nothing in it look identical in an
    empty list.
    """
    found: list[Symbol] = []
    unreadable: dict[str, Unreadable] = {}

    for path, lines in sorted(hunk_ranges(diff, side=Side.NEW).items()):
        if not path.endswith(".py"):
            unreadable[path] = Unreadable.NOT_PYTHON
            continue
        source = sources.get(path)
        if source is None:
            unreadable[path] = Unreadable.NOT_SUPPLIED
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            unreadable[path] = Unreadable.UNPARSABLE
            continue

        definitions = _definitions(tree)
        seen: set[str] = set()
        for line in sorted(lines):
            enclosing = _innermost(definitions, line)
            if enclosing is None:
                qualname, first, last = MODULE_SCOPE, line, line
            else:
                qualname, first, last = enclosing
            if qualname in seen:
                continue
            seen.add(qualname)
            found.append(Symbol(path=path, qualname=qualname, first_line=first, last_line=last))

    return tuple(found), unreadable


def find_callers(name: str, sources: Mapping[str, str]) -> tuple[CallSite, ...]:
    """AC 1's second half: every site where this name is called or passed.

    **A bare-name match over an AST, not a grep and not a resolver.** A grep finds
    the word in comments, docstrings and unrelated strings; a resolver is not
    possible, because Python decides at run time which object an attribute names.
    So this walks the tree and reports `Call` nodes whose callee spells this name
    and `Name`/`Attribute` loads that spell it without calling — the second
    because a function passed as a value depends on the change exactly as hard as
    one invoked.

    Unparsable and non-Python files are skipped silently here: `modified_symbols`
    already reports the ones the patch touched, and a repository full of templates
    would otherwise drown the report in files nobody asked about.
    """
    sites: list[CallSite] = []
    for path, source in sorted(sources.items()):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        enclosing = _definitions(tree)
        lines = source.splitlines()
        called: set[int] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node) == name:
                called.add(id(node.func))
                sites.append(_site(path, node.lineno, enclosing, Reference.CALL, lines))

        for node in ast.walk(tree):
            # Narrowed here rather than inside `_loads`, because only these two
            # node types carry a line number and a helper that returned a name
            # for an `ast.AST` would leave the caller with nowhere to read one.
            if not isinstance(node, (ast.Name, ast.Attribute)) or id(node) in called:
                continue
            if _loads(node) == name:
                sites.append(_site(path, node.lineno, enclosing, Reference.PASSED, lines))

    return tuple(sorted(sites, key=lambda site: (site.path, site.line, site.kind)))


def run_suite(
    original: DiagnosticSession,
    patched: CandidateSession,
    *,
    command: Sequence[str],
    timeout: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
) -> SuiteRun:
    """AC 2, run on **both** revisions.

    The control is the whole value. A repository whose suite already fails — a
    stale snapshot, an unavailable service, a flake — makes every patch look like
    it broke something, and one run against the patched code alone cannot tell the
    two apart. `ALREADY_BROKEN` is a fourth answer for exactly that, and it
    establishes nothing in either direction rather than being read as a pass.

    The session types are the same opposite pair S-10.2 and S-11.2 use:
    `DiagnosticSession` has no `apply_patch`, so *before the change* is a fact
    about the type.
    """
    before = _run_once(original, command, timeout)
    after = _run_once(patched, command, timeout)
    evidence = "\n".join(
        part
        for part in (
            _tail("original", before),
            _tail("patched", after),
        )
        if part
    )

    if before is None or after is None:
        return SuiteRun(
            outcome=SuiteOutcome.NOT_RUN,
            original_exit=None if before is None else before.exit_code,
            patched_exit=None if after is None else after.exit_code,
            evidence=evidence,
        )

    if before.exit_code != 0:
        outcome = SuiteOutcome.ALREADY_BROKEN
    elif after.exit_code != 0:
        outcome = SuiteOutcome.BROKEN_BY_THE_PATCH
    else:
        outcome = SuiteOutcome.PASSED_ON_BOTH

    return SuiteRun(
        outcome=outcome,
        original_exit=before.exit_code,
        patched_exit=after.exit_code,
        evidence=evidence,
    )


def audit_scope(
    diff: str,
    *,
    sources: Mapping[str, str],
    chain: EvidenceChain,
    suite: SuiteRun,
) -> ScopeAudit:
    """Name what changed, find who reaches it, and say who the evidence never covered.

    `sources` is the **patched** revision's source for the whole repository, not
    only the files the patch touched: the callers this looks for are by definition
    somewhere else, and a mapping holding only the changed files would find none
    of them and report that as nothing to worry about.

    The scope is `scope_of(chain)` — S-10.4's answer to *which files does this
    evidence implicate*, reused rather than re-derived, so the patch cannot be
    confined by one definition of scope and audited against another.

    Raises:
        ScopeError: the diff touches nothing.
    """
    symbols, unreadable = modified_symbols(diff, sources)
    if not symbols and not unreadable:
        message = (
            "this diff names no file, so nothing was changed and there is nothing to find "
            "callers of. An audit of it would report no callers outside the evidence, which "
            "is what a safe patch looks like"
        )
        raise ScopeError(message)

    callers: list[Caller] = []
    for symbol in symbols:
        if not symbol.callable_symbol:
            continue
        callers.extend(
            Caller(symbol=symbol, site=site) for site in find_callers(symbol.name, sources)
        )

    return ScopeAudit(
        symbols=symbols,
        callers=tuple(callers),
        suite=suite,
        scope=scope_of(chain),
        unreadable=unreadable,
    )


def _definitions(tree: ast.Module) -> tuple[tuple[str, int, int], ...]:
    """Every definition in the module as `(qualname, first_line, last_line)`.

    Built by walking with an explicit stack rather than `ast.walk`, because a
    qualified name needs the path taken to reach a node and `walk` throws that
    away.
    """
    found: list[tuple[str, int, int]] = []

    def descend(node: ast.AST, prefix: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = (*prefix, child.name)
                last = getattr(child, "end_lineno", None) or child.lineno
                # The decorators are part of what a hunk can touch and are not
                # inside the body's line range, so a change to one would fall
                # outside every definition and be filed as module-level.
                first = min([child.lineno, *(item.lineno for item in child.decorator_list)])
                found.append((".".join(qualified), first, last))
                descend(child, qualified)
            else:
                descend(child, prefix)

    descend(tree, ())
    return tuple(found)


def _innermost(
    definitions: Sequence[tuple[str, int, int]], line: int
) -> tuple[str, int, int] | None:
    """The tightest definition containing `line`, or `None` for module level."""
    containing = [item for item in definitions if item[1] <= line <= item[2]]
    if not containing:
        return None
    return min(containing, key=lambda item: item[2] - item[1])


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _loads(node: ast.Name | ast.Attribute) -> str | None:
    """The name a load-context reference spells, ignoring stores and deletes.

    A store is not a use — `to_representation = something` rebinds the name and
    does not depend on what the patch did to the definition.
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return node.attr
    return None


def _site(
    path: str,
    line: int,
    definitions: Sequence[tuple[str, int, int]],
    kind: Reference,
    lines: Sequence[str],
) -> CallSite:
    enclosing = _innermost(definitions, line)
    return CallSite(
        path=path,
        line=line,
        inside=enclosing[0] if enclosing is not None else MODULE_SCOPE,
        kind=kind,
        text=lines[line - 1] if 0 < line <= len(lines) else "",
    )


def _run_once(
    session: DiagnosticSession | CandidateSession, command: Sequence[str], timeout: float
) -> ExecutionResult | None:
    try:
        return session.run(list(command), timeout=timeout)
    except ExecutionTimeoutError:
        return None


def _tail(label: str, result: ExecutionResult | None) -> str:
    if result is None:
        return f"[{label}] the suite did not finish inside its timeout"
    text = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return f"[{label}] {text[-400:]}" if text else ""


def _show(code: int | None) -> str:
    return "not run" if code is None else str(code)

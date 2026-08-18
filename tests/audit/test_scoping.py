"""Epic 11, S-11.5 — scope attacks.

*`find_callers` locates other call sites of every modified symbol. Runs the full
test suite. Reports callers outside the tested workload.*

The whole investigation looked at one workload, so the question none of the other
stories can ask is *the change was verified against one caller; who are the
others?*

The suite runs against real sessions executing real commands, because AC 2's whole
value is the **control**: a repository whose tests already fail makes every patch
look like it broke something, and only running the original too can tell the two
apart.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from coldfix.audit import scoping
from coldfix.audit.scoping import (
    MODULE_SCOPE,
    RESIDUE,
    CallSite,
    Reference,
    ScopeError,
    SuiteOutcome,
    SuiteRun,
    Symbol,
    Unreadable,
    audit_scope,
    find_callers,
    modified_symbols,
    run_suite,
)
from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError, execute
from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.primitives.scaling import Distribution
from coldfix.repair import patch
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.patching import Side, hunk_ranges
from coldfix.sandbox.worktrees import Worktree

REVISION = "9f1c0de"
SITE = "shop/serializers.py"
IMPLICATED = "shop/models.py"
ELSEWHERE = "reports/exports.py"

SERIALIZERS = '''\
from shop.models import Author


class BookSerializer:
    """Renders a book."""

    def to_representation(self, book):
        return {"title": book.title, "author": self.author_name(book)}

    def author_name(self, book):
        return Author.objects.get(pk=book.author_id).name
'''

MODELS = """\
class Author:
    pass


def render_all(books):
    serializer = BookSerializer()
    return [serializer.to_representation(book) for book in books]
"""

EXPORTS = """\
from shop.serializers import BookSerializer


def nightly_csv(books):
    serializer = BookSerializer()
    rows = map(serializer.to_representation, books)
    return list(rows)


def one_off(book):
    return BookSerializer().to_representation(book)
"""

SOURCES: Mapping[str, str] = {
    SITE: SERIALIZERS,
    IMPLICATED: MODELS,
    ELSEWHERE: EXPORTS,
}

# Touches lines 7-8 of the patched serializers.py, which is inside
# `BookSerializer.to_representation`.
DIFF = """\
diff --git a/shop/serializers.py b/shop/serializers.py
--- a/shop/serializers.py
+++ b/shop/serializers.py
@@ -7,2 +7,2 @@
-    def to_representation(self, book):
-        return {"title": book.title, "author": book.author.name}
+    def to_representation(self, book):
+        return {"title": book.title, "author": self.author_name(book)}
"""

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=[10, 100, 1000],
)


def a_chain() -> EvidenceChain:
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=UNIFORM_AT_1000)],
        localization=[
            LocalizationLink(
                scope="BookSerializer.to_representation",
                experiment=confirmed,
                share_of_cost=0.89,
                basis="8.24s baseline against 0.90s ablated",
            )
        ],
        mechanism="the serializer re-renders the author for every book",
        complexity={"rows": Growth.LINEAR},
        site=Site(path=SITE, first_line=7, last_line=8),
        context=[Implicated(path=IMPLICATED, reason="the relation walked per row")],
    )


def a_suite(outcome: SuiteOutcome = SuiteOutcome.PASSED_ON_BOTH) -> SuiteRun:
    codes = {
        SuiteOutcome.PASSED_ON_BOTH: (0, 0),
        SuiteOutcome.BROKEN_BY_THE_PATCH: (0, 1),
        SuiteOutcome.ALREADY_BROKEN: (1, 1),
        SuiteOutcome.NOT_RUN: (None, None),
    }[outcome]
    return SuiteRun(outcome=outcome, original_exit=codes[0], patched_exit=codes[1], evidence="...")


class _Suite:
    """A worktree whose suite command really runs, so exit codes are real."""

    def __init__(self, *, exit_code: int, raises: Exception | None = None) -> None:
        self._exit_code = exit_code
        self._raises = raises
        self.commands: list[list[str]] = []

    @property
    def worktree(self) -> Worktree:
        return Worktree(path=Path(), revision=REVISION, is_main=False)

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = 8 * 1024 * 1024,
    ) -> ExecutionResult:
        self.commands.append(list(command))
        if self._raises is not None:
            raise self._raises
        return execute(
            [
                sys.executable,
                "-c",
                f"import sys; print('ran {command[-1]}'); sys.exit({self._exit_code})",
            ],
            timeout=min(timeout, 30.0),
        )


class FakeOriginal(_Suite, DiagnosticSession):
    """Before the change. A `DiagnosticSession` has no `apply_patch`."""


class FakePatched(_Suite, CandidateSession):
    """After it."""


# ============ AC 1 — which symbols the patch modified


def test_a_change_inside_a_method_is_attributed_to_the_method() -> None:
    symbols, unreadable = modified_symbols(DIFF, SOURCES)
    assert not unreadable
    assert [symbol.qualname for symbol in symbols] == ["BookSerializer.to_representation"]
    assert symbols[0].name == "to_representation"
    assert symbols[0].path == SITE


def test_the_new_side_line_numbers_are_the_ones_used() -> None:
    """**The symbols live in the patched source**, where the original numbering
    points at whatever the edit shifted. A hunk inserting lines above a method
    would find it short of where it now is and blame whatever used to be there."""
    shifted = """\
diff --git a/shop/serializers.py b/shop/serializers.py
--- a/shop/serializers.py
+++ b/shop/serializers.py
@@ -1,1 +1,5 @@
-from shop.models import Author
+from shop.models import Author
+
+
+
+
@@ -7,1 +11,1 @@
-        return {"title": book.title, "author": book.author.name}
+        return {"title": book.title, "author": self.author_name(book)}
"""
    assert hunk_ranges(shifted, side=Side.ORIGINAL)[SITE] == frozenset({1, 7})
    assert hunk_ranges(shifted, side=Side.NEW)[SITE] == frozenset({1, 2, 3, 4, 5, 11})

    grown = SERIALIZERS.replace(
        "from shop.models import Author\n", "from shop.models import Author\n\n\n\n\n", 1
    )
    symbols, _ = modified_symbols(shifted, {SITE: grown})
    names = {symbol.qualname for symbol in symbols}
    assert "BookSerializer.to_representation" in names, "new-side line 11, where the method now is"
    assert MODULE_SCOPE in names, "and the import at the top"

    # What the original numbering would have said. Line 7 of the grown file is a
    # blank line four rows above the class, so reading the original side gives
    # module scope for both hunks and the method is never named at all.
    grown_lines = grown.splitlines()
    assert grown_lines[6].strip() == "", "line 7 is blank once the import block grew"
    assert grown_lines[10].strip().startswith("def to_representation")


def test_the_default_side_is_still_the_original_for_s_10_5() -> None:
    """S-10.5 asks *did this attempt change the same lines as the last one*, and
    original-side is the numbering two attempts have in common."""
    assert hunk_ranges(DIFF)[SITE] == hunk_ranges(DIFF, side=Side.ORIGINAL)[SITE]


def test_the_innermost_definition_wins_over_its_class() -> None:
    symbols, _ = modified_symbols(DIFF, SOURCES)
    assert symbols[0].qualname != "BookSerializer"


def test_a_change_outside_every_definition_is_module_level_and_has_no_callers() -> None:
    top = """\
diff --git a/shop/serializers.py b/shop/serializers.py
--- a/shop/serializers.py
+++ b/shop/serializers.py
@@ -1,1 +1,1 @@
-from shop.models import Author
+from shop.models import Author, Publisher
"""
    symbols, _ = modified_symbols(top, SOURCES)
    assert [symbol.qualname for symbol in symbols] == [MODULE_SCOPE]
    assert not symbols[0].callable_symbol

    result = audit_scope(top, sources=SOURCES, chain=a_chain(), suite=a_suite())
    assert not result.callers, "hunting for callers of <module> returns everything or nothing"


def test_a_decorator_change_belongs_to_what_it_decorates() -> None:
    """A decorator sits above the `def` line, so a definition's range starting at
    its own `lineno` would file the change as module-level."""
    decorated = """\
import functools


class Cache:
    @functools.cache
    def lookup(self, key):
        return key
"""
    diff = """\
diff --git a/shop/cache.py b/shop/cache.py
--- a/shop/cache.py
+++ b/shop/cache.py
@@ -5,1 +5,1 @@
-    @functools.cache
+    @functools.lru_cache
"""
    symbols, _ = modified_symbols(diff, {"shop/cache.py": decorated})
    assert [symbol.qualname for symbol in symbols] == ["Cache.lookup"]


def test_a_file_the_audit_could_not_read_is_named_not_dropped() -> None:
    """A file whose source was not supplied and a file with nothing in it look
    identical in an empty list."""
    symbols, unreadable = modified_symbols(DIFF, {})
    assert not symbols
    assert unreadable == {SITE: Unreadable.NOT_SUPPLIED}

    _, broken = modified_symbols(DIFF, {SITE: "def (:"})
    assert broken == {SITE: Unreadable.UNPARSABLE}

    template = DIFF.replace(SITE, "shop/templates/book.html")
    _, other = modified_symbols(template, SOURCES)
    assert other == {"shop/templates/book.html": Unreadable.NOT_PYTHON}


# ============ AC 1 — find_callers


def test_find_callers_locates_calls_and_references_alike() -> None:
    """`map(serializer.to_representation, rows)` breaks exactly as hard as a call
    does, and a pass collecting only `Call` nodes would report that file as
    untouched."""
    sites = find_callers("to_representation", SOURCES)
    by_path = {(site.path, site.line): site for site in sites}

    passed = [site for site in sites if site.kind is Reference.PASSED]
    assert [site.path for site in passed] == [ELSEWHERE]
    assert passed[0].inside == "nightly_csv"

    called = {site.path for site in sites if site.kind is Reference.CALL}
    assert called == {IMPLICATED, ELSEWHERE}
    assert any(site.inside == "render_all" for site in by_path.values())
    assert any(site.inside == "one_off" for site in by_path.values())


def test_a_bare_name_call_is_found_as_well_as_an_attribute_one() -> None:
    """**Every other fixture here calls through an attribute** —
    `serializer.to_representation(book)` — so a sabotage that stopped reading
    `ast.Name` callees changed no assertion. A module-level function called by its
    own name is the shape that separates the two branches.
    """
    helpers = "def render_all(books):\n    return books\n"
    users = (
        "from shop.helpers import render_all\n\n\n"
        "def report(books):\n    return render_all(books)\n"
    )
    diff = """diff --git a/shop/helpers.py b/shop/helpers.py
--- a/shop/helpers.py
+++ b/shop/helpers.py
@@ -2,1 +2,1 @@
-    return books
+    return list(books)
"""
    sources = {"shop/helpers.py": helpers, "reports/weekly.py": users}
    symbols, _ = modified_symbols(diff, sources)
    assert [symbol.qualname for symbol in symbols] == ["render_all"]

    sites = find_callers("render_all", sources)
    calls = [site for site in sites if site.kind is Reference.CALL]
    assert [(site.path, site.inside) for site in calls] == [("reports/weekly.py", "report")]


def test_a_reference_is_not_double_counted_as_a_call() -> None:
    sites = find_callers("to_representation", {ELSEWHERE: EXPORTS})
    lines = [(site.line, site.kind) for site in sites]
    assert len(lines) == len(set(lines))
    for line in {site.line for site in sites}:
        kinds = {site.kind for site in sites if site.line == line}
        assert len(kinds) == 1, f"line {line} counted twice"


def test_a_name_being_assigned_is_not_a_use() -> None:
    """A store rebinds the name; it does not depend on what the patch did to the
    definition."""
    stores = "def f():\n    to_representation = 1\n    return to_representation\n"
    sites = find_callers("to_representation", {"a.py": stores})
    assert [site.line for site in sites] == [3], "the load, not the store"


def test_a_docstring_mentioning_the_name_is_not_a_caller() -> None:
    """A grep finds the word in comments, docstrings and unrelated strings. This
    walks an AST, so it does not."""
    prose = '''\
def unrelated():
    """Calls to_representation are what we are replacing."""
    # to_representation should not be counted here
    return "to_representation"
'''
    assert find_callers("to_representation", {"a.py": prose}) == ()


def test_a_non_python_file_is_skipped_rather_than_parsed() -> None:
    assert find_callers("to_representation", {"a.html": "{{ to_representation }}"}) == ()


def test_the_definition_itself_is_not_reported_as_a_caller() -> None:
    result = audit_scope(DIFF, sources=SOURCES, chain=a_chain(), suite=a_suite())
    assert all(not caller.is_the_definition for caller in result.outside)
    assert all(not caller.is_the_definition for caller in result.inside)


def test_a_recursive_call_is_not_a_caller_of_itself() -> None:
    recursive = "def walk(node):\n    return [walk(child) for child in node]\n"
    diff = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -2,1 +2,1 @@
-    return [walk(child) for child in node]
+    return [walk(kid) for kid in node]
"""
    result = audit_scope(diff, sources={"a.py": recursive}, chain=a_chain(), suite=a_suite())
    assert result.callers, "the site is found"
    assert all(caller.is_the_definition for caller in result.callers)
    assert not result.outside


# ============ AC 3 — callers outside the tested workload


def test_a_caller_outside_the_evidence_is_the_headline() -> None:
    """**The question none of the other stories can ask.** The investigation
    measured one workload; `reports/exports.py` calls the changed method and no
    experiment here has ever run it."""
    result = audit_scope(DIFF, sources=SOURCES, chain=a_chain(), suite=a_suite())

    assert result.scope == frozenset({SITE, IMPLICATED})
    assert {caller.site.path for caller in result.outside} == {ELSEWHERE}
    assert {caller.site.path for caller in result.inside} == {IMPLICATED}
    assert not result.clean
    assert "outside the tested workload" in result.describe()
    assert ELSEWHERE in result.describe()


def test_the_scope_is_s_10_4s_and_not_a_second_definition() -> None:
    """Deriving a second notion of scope would let the patch be confined by one and
    audited against another."""
    assert vars(scoping)["scope_of"] is patch.scope_of
    chain = a_chain()
    result = audit_scope(DIFF, sources=SOURCES, chain=chain, suite=a_suite())
    assert result.scope == patch.scope_of(chain)


def test_a_patch_whose_callers_are_all_inside_the_evidence_is_clean() -> None:
    result = audit_scope(
        DIFF, sources={SITE: SERIALIZERS, IMPLICATED: MODELS}, chain=a_chain(), suite=a_suite()
    )
    assert not result.outside
    assert result.complete
    assert result.clean
    assert "No call site outside the implicated files" in result.describe()


def test_a_diff_that_names_nothing_is_refused() -> None:
    """An audit of it would report no callers outside the evidence, which is what a
    safe patch looks like."""
    with pytest.raises(ScopeError, match="what a safe patch looks like"):
        audit_scope("", sources=SOURCES, chain=a_chain(), suite=a_suite())


def test_an_unreadable_file_blocks_a_clean_verdict() -> None:
    result = audit_scope(DIFF, sources={IMPLICATED: MODELS}, chain=a_chain(), suite=a_suite())
    assert not result.complete
    assert not result.clean
    assert "produced no symbols" in result.describe()


def test_the_report_states_what_a_name_match_cannot_see() -> None:
    result = audit_scope(DIFF, sources=SOURCES, chain=a_chain(), suite=a_suite())
    assert RESIDUE in result.describe()
    assert "not evidence that" in RESIDUE


# ============ AC 2 — the suite, on both revisions


def test_a_suite_that_passed_before_and_fails_after_is_the_patch() -> None:
    run = run_suite(FakeOriginal(exit_code=0), FakePatched(exit_code=1), command=["pytest", "-q"])
    assert run.outcome is SuiteOutcome.BROKEN_BY_THE_PATCH
    assert run.broke_it
    assert (run.original_exit, run.patched_exit) == (0, 1)


def test_a_suite_that_already_failed_says_nothing_about_the_patch() -> None:
    """**The control, and the whole value of AC 2.** A stale snapshot or a missing
    service makes every patch look like it broke something, and one run against the
    patched code alone cannot tell the two apart."""
    run = run_suite(FakeOriginal(exit_code=1), FakePatched(exit_code=1), command=["pytest", "-q"])
    assert run.outcome is SuiteOutcome.ALREADY_BROKEN
    assert not run.broke_it, "not the patch's doing"
    assert not run.outcome.informative
    assert "cannot say whether the change broke anything" in run.describe()


def test_a_suite_green_on_both_revisions_passes() -> None:
    run = run_suite(FakeOriginal(exit_code=0), FakePatched(exit_code=0), command=["pytest", "-q"])
    assert run.outcome is SuiteOutcome.PASSED_ON_BOTH
    assert run.outcome.informative


def test_the_suite_is_actually_run_on_both_revisions() -> None:
    original, patched = FakeOriginal(exit_code=0), FakePatched(exit_code=0)
    run_suite(original, patched, command=["pytest", "-q", "--tb=short"])
    assert original.commands == [["pytest", "-q", "--tb=short"]]
    assert patched.commands == [["pytest", "-q", "--tb=short"]]


def test_a_suite_that_timed_out_is_not_a_pass_and_not_a_failure() -> None:
    run = run_suite(
        FakeOriginal(exit_code=0),
        FakePatched(exit_code=0, raises=ExecutionTimeoutError(["pytest"], 1800.0, "", "")),
        command=["pytest"],
    )
    assert run.outcome is SuiteOutcome.NOT_RUN
    assert not run.outcome.informative
    assert run.patched_exit is None


def test_a_broken_suite_blocks_a_clean_verdict() -> None:
    inside_only = {SITE: SERIALIZERS, IMPLICATED: MODELS}
    green = audit_scope(DIFF, sources=inside_only, chain=a_chain(), suite=a_suite())
    assert green.clean

    for outcome in (
        SuiteOutcome.BROKEN_BY_THE_PATCH,
        SuiteOutcome.ALREADY_BROKEN,
        SuiteOutcome.NOT_RUN,
    ):
        result = audit_scope(DIFF, sources=inside_only, chain=a_chain(), suite=a_suite(outcome))
        assert not result.clean, outcome


def test_the_three_conditions_on_clean_are_independently_observable() -> None:
    """One case per clause, because a `clean` whose failures are always
    overdetermined is a `clean` whose clauses nobody has checked."""
    inside_only = {SITE: SERIALIZERS, IMPLICATED: MODELS}

    only_outside = audit_scope(DIFF, sources=SOURCES, chain=a_chain(), suite=a_suite())
    assert only_outside.complete and only_outside.suite.outcome is SuiteOutcome.PASSED_ON_BOTH
    assert only_outside.outside and not only_outside.clean

    only_suite = audit_scope(
        DIFF, sources=inside_only, chain=a_chain(), suite=a_suite(SuiteOutcome.ALREADY_BROKEN)
    )
    assert only_suite.complete and not only_suite.outside
    assert not only_suite.clean

    only_unreadable = audit_scope(
        DIFF, sources={IMPLICATED: MODELS}, chain=a_chain(), suite=a_suite()
    )
    assert not only_unreadable.outside
    assert only_unreadable.suite.outcome is SuiteOutcome.PASSED_ON_BOTH
    assert not only_unreadable.complete and not only_unreadable.clean


def test_the_two_sides_take_opposite_session_types() -> None:
    parameters = inspect.signature(run_suite).parameters
    assert parameters["original"].annotation == "DiagnosticSession"
    assert parameters["patched"].annotation == "CandidateSession"


def test_a_call_site_renders_its_own_line() -> None:
    site = CallSite(
        path=ELSEWHERE, line=11, inside="one_off", kind=Reference.CALL, text="    return x()"
    )
    assert "reports/exports.py:11 in one_off" in site.describe()
    assert "return x()" in site.describe()


def test_a_symbol_takes_its_bare_name_from_its_qualname() -> None:
    assert Symbol(path="a.py", qualname="A.b.c", first_line=1, last_line=2).name == "c"
    assert Symbol(path="a.py", qualname="f", first_line=1, last_line=2).name == "f"

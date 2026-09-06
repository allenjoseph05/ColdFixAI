"""The package import graph, asserted rather than reviewed.

`docs/walkthrough/04-redesign-brief.md` §6 lists as defect 5 *"a seven-package
import cycle `{audit, diagnosis, explorer, repair, replay, screening, state}`
contradicts the layering claim"*, and prescribes extracting the shared
session/surface protocols. `coldfix.contracts` is that extraction.

Two properties are asserted here, and the second one is the load-bearing half.

**`contracts` is a leaf.** Not "imports little" — nothing it reaches imports it
back, transitively. A leaf that acquires one edge home stops being able to break
any cycle, and does so without any test noticing.

**The remaining cycles are named exactly.** A test asserting only "the
seven-package cycle is gone" would pass while six new ones appeared. So the
assertion is set equality against `KNOWN_CYCLES`: a new cycle fails, and breaking
a listed one *also* fails, which forces the fix and the record to move together.
`KNOWN_CYCLES` is empty today, and it caught its own update — it named
`audit <-> repair` while that cycle was being removed, and failed until the
record caught up. The dangerous direction is drift into the permissive half.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

import coldfix

ROOT = Path(coldfix.__file__).parent

KNOWN_CYCLES: set[frozenset[str]] = set()
"""Every package-level cycle that still exists. There are none.

The last one was `audit <-> repair`, and it was two imports wide in the return
direction. `measured_pairs` went to `diagnosis/log.py`, which is where the
`Experiment` it reads is defined; the four audit invocation primitives went to
`contracts/auditing.py`, because S-10.3's test auditor lives in `repair/` and is
as much an auditor as the one in `audit/`.

Empty is the strongest form this can take, and it is why the assertion is set
equality rather than a subset check: with nothing on record, any cycle at all
fails, and a contributor who adds one has to write down why here.
"""


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_graph() -> dict[str, set[str]]:
    """Top-level package -> the packages it imports, read from the AST.

    Parsed rather than grepped: an import inside a `TYPE_CHECKING` block or split
    across lines still binds the two packages together at type-check time, and a
    path named in a docstring does not.
    """
    files = sorted(ROOT.rglob("*.py"))
    known = {_module_name(p) for p in files}
    graph: dict[str, set[str]] = collections.defaultdict(set)

    for path in files:
        source = _module_name(path)
        if "." not in source:
            continue
        targets: list[str] = []
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                targets.append(node.module)
            elif isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)

        for target in targets:
            if not target.startswith("coldfix."):
                continue
            candidate = target[len("coldfix.") :]
            while candidate and candidate not in known:
                candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
            if not candidate:
                continue
            here, there = source.split(".")[0], candidate.split(".")[0]
            if here != there:
                graph[here].add(there)

    return graph


def _cycles(graph: dict[str, set[str]]) -> set[frozenset[str]]:
    """Every strongly connected component of size two or more.

    Path-based rather than Tarjan: the graph has a dozen nodes, and a reader
    verifying a safety property should not have to trust a clever algorithm.
    """
    nodes = set(graph) | {n for succ in graph.values() for n in succ}
    found: set[frozenset[str]] = set()

    def reaches(start: str, goal: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node == goal and node in seen:
                return True
            for nxt in graph.get(node, ()):
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    for a in sorted(nodes):
        component = {a} | {b for b in nodes if b != a and reaches(a, b) and reaches(b, a)}
        if len(component) > 1:
            found.add(frozenset(component))
    return found


# =================================================== contracts is a leaf


def test_contracts_imports_nothing_that_imports_it_back() -> None:
    """The property that makes the extraction worth anything.

    Asserted transitively: `contracts -> cost -> ... -> contracts` would be a
    cycle even though `contracts` imports no package that names it directly.
    """
    graph = _package_graph()

    reachable: set[str] = set()
    stack = list(graph.get("contracts", ()))
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(graph.get(node, ()))

    offenders = {pkg for pkg in reachable if "contracts" in graph.get(pkg, set())}

    assert offenders == set(), (
        f"contracts is no longer a leaf: {sorted(offenders)} import it back, "
        "so it can no longer break a cycle it participates in"
    )


def test_contracts_does_not_import_the_packages_it_was_extracted_for() -> None:
    """The direct half, which fails earlier and reads better when it does."""
    graph = _package_graph()

    extracted_for = {"audit", "diagnosis", "explorer", "repair", "replay", "screening", "state"}

    assert graph.get("contracts", set()) & extracted_for == set()


# ============================================ the remaining cycles, exactly


def test_the_package_cycles_are_exactly_the_ones_on_record() -> None:
    """Set equality, in both directions, and that is the point.

    A new cycle fails this. Breaking `audit <-> repair` *also* fails it, which is
    intended: the fix and `KNOWN_CYCLES` move in the same commit, so the record
    can never quietly describe a graph that is no longer there.
    """
    assert _cycles(_package_graph()) == KNOWN_CYCLES


def test_the_seven_package_cycle_is_gone() -> None:
    """Defect 5 named a specific component. It should not reassemble."""
    seven = frozenset({"audit", "diagnosis", "explorer", "repair", "replay", "screening", "state"})

    surviving = {c for c in _cycles(_package_graph()) if c & seven == seven}

    assert surviving == set()

"""The package import graph, asserted rather than reviewed.

`docs/walkthrough/04-redesign-brief.md` §6 listed as defect 5 *"a seven-package
import cycle `{audit, diagnosis, explorer, repair, replay, screening, state}`
contradicts the layering claim"*. S-31.1 deleted six of those seven packages
along with v1, so that particular component cannot reassemble from the same
parts.

**What survives is the assertion that had teeth: the cycle set is exactly empty.**
Set equality rather than a subset check, over whatever packages exist today — a
new cycle fails it, and quietly breaking a recorded one fails it too, which forces
the fix and the record to move in the same commit. `KNOWN_CYCLES` is empty, and
empty is the strongest form this can take.

**Three tests were deleted here rather than left passing.** They asserted that
`coldfix.contracts` was a leaf, and that it imported none of the seven packages it
had been extracted for. `contracts/` existed to break that cycle; the cycle went
with v1 and the package went with it. All three would now pass by having no
subject at all — and a test that cannot fail is worse than no test, because in a
list of green names it reads exactly like one that can.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

import coldfix

ROOT = Path(coldfix.__file__).parent

KNOWN_CYCLES: set[frozenset[str]] = set()
"""Every package-level cycle that still exists. There are none.

Empty is why the assertion below is set equality rather than a subset check: with
nothing on record, any cycle at all fails, and whoever adds one has to write down
here why it is acceptable.
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


def test_the_package_cycles_are_exactly_the_ones_on_record() -> None:
    """Set equality, in both directions, and that is the point.

    A new cycle fails this. Removing one that is on record *also* fails it, which
    is intended: the fix and `KNOWN_CYCLES` move in the same commit, so the record
    can never quietly describe a graph that is no longer there.
    """
    assert _cycles(_package_graph()) == KNOWN_CYCLES


def test_the_graph_is_read_from_something_rather_than_empty() -> None:
    """The control on the assertion above.

    `_package_graph` returning nothing would make an empty cycle set trivially
    true, and after a cut that deleted twelve packages that is a real way to be
    wrong. So: the graph has edges, and the pipeline is one of the things in it.
    """
    graph = _package_graph()

    assert graph, "no imports were found at all, so the cycle check proves nothing"
    assert graph["pipeline"], "the pipeline imports nothing, which cannot be right"

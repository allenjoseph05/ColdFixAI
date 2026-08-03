"""Planted query defects, each paired with a control that must not be flagged.

Every defect here has a documented signature and a clean counterpart with the
same *purpose*. The controls are the load-bearing part: a detector that reports
"N+1" unconditionally passes every defect test and fails every control, and
without controls in the fixture that detector looks perfect.

This mirrors the S-0.6 holdout argument. A holdout containing a defect measures
whether a tool generalizes; a holdout where the right answer is *nothing found*
measures whether it can resist manufacturing a finding. The same reasoning
applies one level down, to the fixtures themselves.
"""

from __future__ import annotations

from typing import Any

from .store import Store

Row = dict[str, Any]

# The decoy's fixed cost. Chosen to resemble the ~35-query floor S-0.3 measured
# on netbox's interface endpoint, which is a real mature system's real shape.
DECOY_FIXED_QUERIES = 35


def list_books_n_plus_one(store: Store) -> list[Row]:
    """**DEFECT — N+1.** One query for authors, then one per author.

    Signature: `queries == 1 + A` where A is the author count. Query count grows
    linearly with rows returned instead of staying constant. This is the defect
    shape found unplanted in the S-0.6 development target.
    """
    authors = store.select("author")
    result: list[Row] = []
    for author in authors:
        books = store.select("book", where=("author_id", author["id"]))
        result.append({"author": author["name"], "books": [b["title"] for b in books]})
    return result


def list_books_batched(store: Store) -> list[Row]:
    """**CONTROL — same output, no N+1.** Two queries regardless of author count.

    Signature: `queries == 2`, constant. Must never be flagged. This is the
    shape the S-0.6 holdout's endpoint already has.
    """
    authors = store.select("author")
    books = store.select("book")

    by_author: dict[Any, list[str]] = {}
    for book in books:
        by_author.setdefault(book["author_id"], []).append(book["title"])

    return [
        {"author": author["name"], "books": by_author.get(author["id"], [])} for author in authors
    ]


def list_titles_over_fetching(store: Store) -> list[str]:
    """**DEFECT — over-fetch.** One query, but drags back columns nobody uses.

    Signature: `queries == 1` — *identical to the control* — while
    `cells_returned` is several times higher. **Query count cannot detect this
    defect at all**, which is precisely why the store records guard counters.

    S-0.4 found the same asymmetry from the other direction: two ablation
    strategies were indistinguishable on timing while differing six-fold in
    payload. A tool measuring only one dimension would have called them
    identical.
    """
    books = store.select("book")
    return [book["title"] for book in books]


def list_titles_narrow(store: Store) -> list[str]:
    """**CONTROL — same output, projected.** One query, minimal payload.

    Signature: `queries == 1`, `cells_returned == rows * 1`.
    """
    books = store.select("book", columns=("title",))
    return [book["title"] for book in books]


def summarize_with_fixed_floor(store: Store) -> dict[str, int]:
    """**DECOY — expensive but correct.** A high constant cost, not an N+1.

    Signature: `queries == DECOY_FIXED_QUERIES + 2`, **independent of dataset
    size**. Expensive, and legitimately so: it answers 35 separate questions.

    This is the netbox shape from S-0.3 — a ~35-query floor with sublinear
    growth, which is what a mature system actually looks like. A detector that
    flags "many queries" rather than "queries that scale with rows" reports this
    as a defect. It is not one, and a fix here would be the metastability
    trap `00-BRIEF.md` §4 warns about: an optimization that improves every
    metric measured while removing slack.

    **A detector must not flag this.** That is the assertion this decoy exists
    to support.
    """
    authors = store.select("author")
    books = store.select("book")

    for decade in range(DECOY_FIXED_QUERIES):
        store.select("author", where=("born", 1900 + decade))

    return {"authors": len(authors), "books": len(books)}


def render_with_expensive_downstream(store: Store) -> int:
    """**DEFECT — cheap component feeding expensive downstream work.**

    Signature: `queries == 2`, but the work done *per row returned* is large.

    This is the gap S-0.4 explicitly could not test. There, the ablated
    component was database-bound (686 queries, ~1020 ms) and the work it fed was
    cheap (~3.5 ms), so the replay and empty stub strategies were
    indistinguishable on timing despite differing six-fold in payload. The
    conclusion — that stub strategy must be recorded — survived only because
    guard counters showed the difference.

    Here the ratio is inverted: the fetch is trivial and the downstream
    processing dominates. **Ablating this component with an empty stub should
    produce a materially different number than replaying a real value**, which
    is the case S-3.4 most needs and no real subject has yet provided.
    """
    books = store.select("book", columns=("title", "synopsis"))
    store.select("author", columns=("name",))

    total = 0
    for book in books:
        synopsis = book["synopsis"]
        # Deliberate per-row work proportional to the payload. An empty stub
        # removes all of it; a replayed real value removes none of it.
        for index, character in enumerate(synopsis):
            total += (index * ord(character)) % 7
    return total

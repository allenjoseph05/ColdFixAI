"""A defect that uniform fixtures cannot see, and a control that no fixture flags.

Planted for S-3.3. Every other defect in this package is visible at any data
volume if you look at the right metric. This one is invisible at *every* volume
under uniform data, and the reason is arithmetic rather than luck.

The defect does work proportional to `k(k-1)/2` for each parent with `k`
children — an ordinary pairwise scan, the shape `if x in seen` takes when `seen`
is a list. Its total cost is therefore `Σ k²` up to constants, and **for a fixed
number of children spread over a fixed number of parents, `Σ k²` is minimized
exactly when every parent has the same number.** That is Cauchy-Schwarz, not a
property of these numbers, and it makes the uniform fixture *provably* the
blindest shape for this class of defect. Doubling the data with a generator that
gives every author three books leaves the per-parent cost at six comparisons
forever, however large the dataset gets.

The control does work proportional to `Σ k`, which is the total child count, and
is therefore **identical under every distribution by construction**. It is what
stops a shape comparison passing by reporting that everything looks worse under
skew: a real skew-sensitivity check must leave this one flat.

Both functions issue exactly two queries and return exactly the same rows
whatever the shape, so the query count and the row count say *the volume was
held constant* — which is what makes the difference between them attributable to
shape at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .store import Row, Store


def titles_match(left: str, right: str) -> bool:
    """One pairwise comparison. The unit the defect spends and the control does not."""
    return left == right


def normalize(title: str) -> str:
    """One per-title operation. The unit the control spends, once per child."""
    return title.strip().casefold()


def _books_by_author(store: Store) -> dict[Any, list[Row]]:
    grouped: dict[Any, list[Row]] = {}
    for book in store.select("book"):
        grouped.setdefault(book["author_id"], []).append(book)
    return grouped


def deduplicate_pairwise(store: Store) -> dict[str, list[str]]:
    """**DEFECT — per-parent quadratic.** Comparisons are `Σ k(k-1)/2`.

    Signature: two queries, all rows returned, and a comparison count that
    depends on *how* the children are distributed rather than how many there
    are. At 20 authors with 10 books each that is 900 comparisons; at 20 authors
    holding the same 200 books under a power law it is several times that, and
    the single worst author accounts for most of it.
    """
    grouped = _books_by_author(store)

    result: dict[str, list[str]] = {}
    for author in store.select("author"):
        unique: list[str] = []
        for book in grouped.get(author["id"], []):
            title = book["title"]
            if not any(titles_match(title, seen) for seen in unique):
                unique.append(title)
        result[author["name"]] = unique
    return result


def deduplicate_by_key(store: Store) -> dict[str, list[str]]:
    """**CONTROL — same output, one operation per child.** Work is `Σ k`.

    Identical under every distribution, because `Σ k` *is* the volume and the
    volume is what a shape comparison holds constant. A check that flags this one
    under skew is flagging skew, not a defect.
    """
    grouped = _books_by_author(store)

    result: dict[str, list[str]] = {}
    for author in store.select("author"):
        seen: set[str] = set()
        unique: list[str] = []
        for book in grouped.get(author["id"], []):
            key = normalize(book["title"])
            if key not in seen:
                seen.add(key)
                unique.append(book["title"])
        result[author["name"]] = unique
    return result


def build_shaped_store(counts: Sequence[int]) -> Store:
    """One author per entry in `counts`, with that many books each.

    The explicit counterpart to `build_store`, which fixes a uniform
    `books_per_author` on purpose so that every expected count is a number rather
    than a range. Here the distribution is the subject, so it is supplied whole
    and every count stays exact.
    """
    store = Store()
    store.add(
        "author",
        [
            {
                "id": index,
                "name": f"author-{index:04d}",
                "biography": "x" * 200,
                "born": 1900 + index,
            }
            for index in range(len(counts))
        ],
    )
    store.add(
        "book",
        [
            {
                "id": index * 10_000 + n,
                "author_id": index,
                "title": f"book-{index:04d}-{n:04d}",
                "synopsis": "y" * 500,
                "pages": 100 + n,
            }
            for index, count in enumerate(counts)
            for n in range(count)
        ],
    )
    return store

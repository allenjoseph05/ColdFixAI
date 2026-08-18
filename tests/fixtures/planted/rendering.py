"""The thesis subject: flat queries, and the cost somewhere else entirely.

`00-BRIEF.md` §5 step 7 names the demo that justifies the architecture — *a repo
where query count is flat, the agent concludes "not the database," switches to
ablation, and localizes the real cause*. This is that repo, in miniature.

**It is not a new defect.** `queries.render_with_expensive_downstream` already
plants exactly this shape and says why: *the fetch is trivial and the downstream
processing dominates*, which is the inversion S-0.4 could not test. What that
function cannot do is be **ablated**, because the expensive work is inline in a
module-level function and `ablation.stub` needs an owner and an attribute. So the
same defect is expressed here as a collaborator with a method, which is what a
serializer, a renderer or a presenter is in every real framework.

**Two signatures, and the second is what makes the demo a demo:**

- `queries == 2` at every scale. A volume sweep fits `CONSTANT` on `db.query`
  and the honest conclusion is *not the database* — an exclusion, not a finding.
- Wall time and per-row work grow with rows, and **stubbing `Renderer.render`
  removes almost all of it**. The instrument that finds it is the one the first
  instrument cannot be.

**The control is the load-bearing half**, for `queries.py`'s stated reason: a
loop that always switched instruments would pass the demo while being useless.
`ListView` with a `CheapRenderer` has the same two queries and no cost to find,
so an ablation there returns a delta near zero and confirms nothing.
"""

from __future__ import annotations

from typing import Any, Protocol

from .store import Store

Row = dict[str, Any]

# Enough per-character work that the ablation delta clears the noise floor S-0.4
# measured (~20ms), without making the fast subset slow. Tuned against the
# fixture, not guessed: at 200 books this is tens of milliseconds and at 25 it is
# a few.
PASSES = 60


class Renderer(Protocol):
    """What a view calls per row. The seam `ablation.stub` interposes on."""

    def render(self, book: Row) -> int: ...


class ExpensiveRenderer:
    """**DEFECT — the cost is here, and no query counter can see it.**

    Per-row work proportional to the payload, exactly as
    `render_with_expensive_downstream` does it, but reachable through an
    attribute so an instrument can replace it.
    """

    def render(self, book: Row) -> int:
        synopsis = str(book["synopsis"])
        total = 0
        for _ in range(PASSES):
            for index, character in enumerate(synopsis):
                total += (index * ord(character)) % 7
        return total


class CheapRenderer:
    """**CONTROL — same interface, same output shape, no cost to find.**

    An ablation here removes nothing worth measuring. A loop that reported a
    confirmation against this one is reporting the instrument rather than the
    subject.
    """

    def render(self, book: Row) -> int:
        return len(str(book["synopsis"]))


class ListView:
    """Two queries, whatever the scale — and one render per row.

    The query count is the part a screening sweep sees, and it is flat by
    construction: one select for books, one for authors, no per-row lookup. That
    flatness is true and is the wrong answer to *why is this slow*.
    """

    def __init__(self, store: Store, renderer: Renderer) -> None:
        self.store = store
        self.renderer = renderer

    def list_books(self) -> list[int]:
        books = self.store.select("book", columns=("title", "synopsis"))
        self.store.select("author", columns=("name",))
        return [self.renderer.render(book) for book in books]

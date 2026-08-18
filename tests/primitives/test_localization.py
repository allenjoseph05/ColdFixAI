"""Two hundred stacks, forty frames of framework each, and one line that matters.

S-3.9. The claim in the story's note is large and worth testing as stated: this
is how a finding spans several files *without the agent reading the repository*.
So every test here works from captured stacks alone — no source, no framework
knowledge, no model — and the site that comes out is asserted to be the line the
fixture actually issues its queries from.

The two halves that have to be right:

**Stripping.** A stack through an ORM is framework at the bottom, the subject in
the middle, request handling above that and the server above that. Keeping only
the subject's frames is what leaves a signature worth grouping on — and the
grouping is what turns two hundred events into one finding.

**The walk.** The divergence point is the deepest frame every occurrence shares.
For an N+1 that is the line in the loop, because every stack is identical. For
events from two sites it is the function that calls both. The same computation
answers both, and the test for the second is what shows the first is not a
coincidence.
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

import pytest

from coldfix.bench.counting import calls_to, count, register_hook, unregister_hook
from coldfix.primitives.localization import (
    Frame,
    LocalizationError,
    Localizer,
    closure,
    localize,
    normalize,
)

QUERIES = "store.select"

# What an adapter would declare as framework-internal. Fragments rather than
# globs, matched against the frame's path.
DENY = ("/framework/", "site-packages", "django/db/")


class Store:
    """The thing whose calls are counted. Stands in for a cursor."""

    def select(self, table: str) -> list[dict[str, int]]:
        return [{"n": index} for index in range(3)]


def frame(filename: str, lineno: int, function: str) -> Frame:
    return Frame(filename=filename, lineno=lineno, function=function)


def stack(*frames: Frame) -> tuple[Frame, ...]:
    """Innermost first, as `count(capture_stacks=True)` produces them."""
    return frames


# --------------------------------------------------- AC 1: stripping the framework


def test_framework_frames_are_stripped_from_everywhere_in_the_stack() -> None:
    """AC 1. Not only from the ends: a real stack is framework, subject,
    framework, server, and only the subject's frames are a signature."""
    raw = stack(
        frame("/framework/orm/query.py", 900, "execute"),
        frame("app/views.py", 41, "list_tickets"),
        frame("/framework/core/handlers.py", 120, "dispatch"),
        frame("app/urls.py", 7, "route"),
        frame("site-packages/gunicorn/worker.py", 55, "handle"),
    )

    kept, crossed = normalize(raw, deny=DENY)

    assert [item.filename for item in kept] == ["app/views.py", "app/urls.py"]
    assert not crossed


def test_an_empty_deny_list_strips_nothing() -> None:
    """The deny list is the adapter's (S-14.1). With none supplied, nothing is
    assumed about which frames belong to a framework this module has never heard
    of."""
    raw = stack(frame("/framework/orm/query.py", 900, "execute"))

    kept, _ = normalize(raw)

    assert len(kept) == 1


def test_the_deny_list_matches_whichever_separator_the_host_uses() -> None:
    """Stacks come from a Linux container and may be read anywhere. An adapter
    naming `django/db/` should not have to know where the comparison runs."""
    windows = "C:\\project\\framework\\orm.py"

    kept, _ = normalize(stack(frame(windows, 900, "execute")), deny=("framework/orm",))

    assert kept == ()


# ------------------------------------------------- AC 2 and 3: grouping and the walk


def n_plus_one_stacks(count_of: int) -> list[tuple[Frame, ...]]:
    """What an N+1 produces: the same route, over and over."""
    return [
        stack(
            frame("/framework/orm/query.py", 900, "execute"),
            frame("app/views.py", 41, "list_tickets"),
            frame("app/urls.py", 7, "route"),
        )
        for _ in range(count_of)
    ]


def test_identical_routes_group_into_one_finding() -> None:
    """AC 2. Two hundred events by one route are one finding, not two hundred."""
    result = localize(n_plus_one_stacks(200), deny=DENY)

    assert len(result.groups) == 1
    assert result.groups[0].occurrences == 200
    assert result.localized == 200


def test_the_divergence_point_of_an_n_plus_one_is_the_line_in_the_loop() -> None:
    """AC 3. Every stack is identical, so the deepest shared frame is the
    innermost one — which is the line issuing the query."""
    result = localize(n_plus_one_stacks(200), deny=DENY)

    assert result.causal_site == frame("app/views.py", 41, "list_tickets")


def test_two_sites_diverge_at_the_function_that_calls_both() -> None:
    """The case that shows the N+1 answer is not a coincidence.

    Events from two places share only their caller, so the deepest common frame
    is that caller — which is the right thing to look at when the cost arrives
    from more than one line.
    """
    shared = (frame("app/views.py", 41, "list_tickets"), frame("app/urls.py", 7, "route"))
    stacks = [stack(frame("app/serializers.py", 12, "to_representation"), *shared)] * 3 + [
        stack(frame("app/models.py", 88, "cached_total"), *shared)
    ] * 5

    result = localize(stacks, deny=DENY)

    assert len(result.groups) == 2
    assert result.causal_site == frame("app/views.py", 41, "list_tickets")
    assert result.divergence[0] == result.causal_site


def test_groups_are_ordered_by_how_often_they_occurred() -> None:
    """Whoever reads this wants the route that happened five hundred times
    before the one that happened twice."""
    common = frame("app/urls.py", 7, "route")
    stacks = [stack(frame("app/a.py", 1, "a"), common)] * 2 + [
        stack(frame("app/b.py", 2, "b"), common)
    ] * 9

    result = localize(stacks, deny=DENY)

    assert [group.occurrences for group in result.groups] == [9, 2]


def test_unrelated_occurrences_report_that_nothing_explains_them() -> None:
    """A real answer rather than an arbitrary frame. These events came from
    different places and no single line is the cause."""
    stacks = [
        stack(frame("app/a.py", 1, "a")),
        stack(frame("app/b.py", 2, "b")),
    ]

    result = localize(stacks, deny=DENY)

    assert result.causal_site is None
    assert result.divergence == ()
    assert "share no frame at all" in result.explanation()


# ------------------------------------ a sample localizes as well as a census


def test_a_sample_of_the_stacks_finds_the_same_site() -> None:
    """S-3.6's finding handed to this story: capturing a stack costs about
    1.4µs per frame of depth, which at a realistic depth is a quarter of the
    database call being observed.

    Grouping is by distinct route and the walk is over the groups, so the site
    does not depend on how many times each route was taken. Sampling is
    therefore safe *by construction* rather than by somebody being careful.
    """
    every = localize(n_plus_one_stacks(200), deny=DENY)
    sampled = localize(n_plus_one_stacks(200)[:5], deny=DENY)

    assert sampled.causal_site == every.causal_site
    assert sampled.divergence == every.divergence
    assert sampled.localized < every.localized


# --------------------------------- events with no site in the subject's code


def test_occurrences_entirely_inside_the_framework_are_counted_apart() -> None:
    """Grouping them under an empty signature would invent a shared site they do
    not have — and the fact itself is a finding: the cost is in code the subject
    does not own, which S-2.9 already has a route for."""
    stacks = [
        stack(frame("/framework/orm/query.py", 900, "execute")),
        stack(frame("site-packages/redis/client.py", 40, "get")),
        *n_plus_one_stacks(3),
    ]

    result = localize(stacks, deny=DENY)

    assert result.outside_subject == 2
    assert result.localized == 3
    assert result.causal_site == frame("app/views.py", 41, "list_tickets")
    assert "not attributed here" in result.explanation()


def test_when_everything_is_framework_the_report_says_so() -> None:
    stacks = [stack(frame("/framework/orm/query.py", 900, "execute"))] * 4

    result = localize(stacks, deny=DENY)

    assert result.causal_site is None
    assert result.outside_subject == 4
    assert "code the subject does not own" in result.explanation()


# ------------------------------------------------ AC 5: the async boundary


def test_an_async_boundary_is_reported_rather_than_walked_through() -> None:
    """AC 5. A stack captured inside a coroutine shows the loop that resumed it,
    not the code that awaited — so the callers past that point are the
    scheduler, and naming one of them as the culprit would be worse than saying
    the trail goes cold."""
    raw = stack(
        frame("app/views.py", 41, "list_tickets"),
        frame("/usr/lib/python3.12/asyncio/events.py", 88, "_run"),
        frame("/usr/lib/python3.12/asyncio/base_events.py", 1900, "_run_once"),
    )

    kept, crossed = normalize(raw, deny=DENY)

    assert crossed
    assert [item.filename for item in kept] == ["app/views.py"]


def test_a_group_that_crossed_a_boundary_carries_the_flag() -> None:
    stacks = [
        stack(
            frame("app/views.py", 41, "list_tickets"),
            frame("/usr/lib/python3.12/asyncio/tasks.py", 300, "__step"),
        )
    ] * 4

    result = localize(stacks, deny=DENY)

    assert result.async_boundaries == 1
    assert result.groups[0].async_boundary
    assert "not recoverable" in result.explanation()


def test_a_synchronous_group_carries_no_flag() -> None:
    """The control. A flag on every group would say nothing."""
    result = localize(n_plus_one_stacks(4), deny=DENY)

    assert result.async_boundaries == 0
    assert not result.groups[0].async_boundary


def test_a_real_coroutine_stack_crosses_the_boundary() -> None:
    """Against a real event loop rather than a hand-written stack, because what
    is being claimed is a fact about how asyncio drives coroutines."""
    captured: list[traceback.StackSummary] = []

    async def work() -> None:
        captured.append(traceback.extract_stack())

    asyncio.run(work())

    _, crossed = normalize(captured[0], deny=DENY)

    assert crossed


# --------------------------------------------------- AC 4: the closure


def test_the_closure_carries_the_site_and_its_callers() -> None:
    """AC 4's runtime half, which is exact and free: the stacks recorded who
    called whom, so a finding spans files without anything reading them."""
    result = localize(n_plus_one_stacks(50), deny=DENY)

    built = closure(result)

    assert built.site == frame("app/views.py", 41, "list_tickets")
    assert built.callers == (frame("app/urls.py", 7, "route"),)
    assert "Reached from" in built.explanation()


def test_the_closure_can_show_the_source_the_agent_did_not_read(tmp_path: Path) -> None:
    """The harness may read the file even though the agent may not, and a loop
    header above a query is the difference between a line number and a finding."""
    source = tmp_path / "app" / "views.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(f"line {number}" for number in range(1, 60)) + "\n", encoding="utf-8"
    )

    built = closure(localize(n_plus_one_stacks(3), deny=DENY), root=tmp_path)

    assert any("line 41" in line for line in built.source)
    assert len(built.source) == 7


def test_an_unreadable_source_weakens_the_finding_rather_than_losing_it(
    tmp_path: Path,
) -> None:
    built = closure(localize(n_plus_one_stacks(3), deny=DENY), root=tmp_path / "absent")

    assert built.source == ()
    assert built.site is not None


def test_declarations_come_from_an_adapter_and_their_absence_is_stated() -> None:
    """Models and relationship declarations are framework knowledge. With no
    resolver the closure says they were not resolved — which is not the same as
    there being none, and the difference is what stops a reader concluding the
    site has no relations."""
    result = localize(n_plus_one_stacks(3), deny=DENY)

    without = closure(result)
    with_resolver = closure(
        result, resolver=lambda site: [f"Ticket.followup_set at {site.location}"]
    )

    assert without.declarations == ()
    assert "not resolved" in without.explanation()
    assert with_resolver.declarations == ("Ticket.followup_set at app/views.py:41",)
    assert "not resolved" not in with_resolver.explanation()


def test_a_closure_over_nothing_is_refused() -> None:
    """An empty closure would read as *a site with no dependencies*, which is a
    different claim from *there is no site*."""
    unrelated = localize(
        [stack(frame("app/a.py", 1, "a")), stack(frame("app/b.py", 2, "b"))], deny=DENY
    )

    with pytest.raises(LocalizationError, match="no causal site"):
        closure(unrelated)


# ------------------------------------------- against stacks the bench captured


def query_in_a_loop(store: Store, rows: int) -> int:
    """The planted shape: one query per row, from one line."""
    total = 0
    for _ in range(rows):
        total += len(store.select("book"))
    return total


def test_localization_works_on_stacks_the_counter_actually_captured() -> None:
    """End to end from S-1.3's capture rather than from stacks written by hand.

    The site this reports is the line in `query_in_a_loop` that calls `select`,
    which is the answer a person needs, and nothing read the file to get it.
    """
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        with count(QUERIES, capture_stacks=True) as tally:
            query_in_a_loop(Store(), 25)
    finally:
        unregister_hook(QUERIES)

    localizer = Localizer(deny=("site-packages", "/lib/python"), root=Path(__file__).parent)
    result = localizer.localize(tally.stacks)

    assert tally.events == 25
    assert result.causal_site is not None
    assert result.causal_site.function == "query_in_a_loop"
    assert "test_localization" in result.causal_site.filename


def test_the_captured_site_is_the_line_that_issues_the_query() -> None:
    """Not the function's first line, and not its caller. The line."""
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        with count(QUERIES, capture_stacks=True) as tally:
            query_in_a_loop(Store(), 5)
    finally:
        unregister_hook(QUERIES)

    result = localize(tally.stacks, deny=("site-packages",))
    source = Path(__file__).read_text(encoding="utf-8").splitlines()

    assert result.causal_site is not None
    assert "store.select" in source[result.causal_site.lineno - 1]


def test_the_closure_reads_the_source_of_a_captured_site() -> None:
    """The whole claim in one assertion: a stack, a deny list, and a finding
    that quotes the loop — with nothing having read the repository to find it."""
    register_hook(QUERIES, calls_to(Store, "select"))
    try:
        with count(QUERIES, capture_stacks=True) as tally:
            query_in_a_loop(Store(), 5)
    finally:
        unregister_hook(QUERIES)

    localizer = Localizer(deny=("site-packages",), root=Path(__file__).parent)
    built = localizer.closure(localizer.localize(tally.stacks))

    assert any("for _ in range(rows)" in line for line in built.source)

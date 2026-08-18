"""Which line caused these events, from the stacks rather than from reading code.

Epic 3, S-3.9. The story's note is the claim to keep in view: *this is how
findings span multiple files without the agent reading the repository. The
runtime names the files.* An evidence chain that says "the N+1 is in
`views.py:41`, reached from `list_tickets`, over the `followup_set` relation" is
worth vastly more than one that says a number is large — and none of it comes
from a model looking at source. It comes from two hundred captured stacks and a
suffix comparison.

**The method is `01-primitives.md` §12's, in three steps.** Strip the frames that
belong to the framework, because a stack through Django's ORM is forty frames of
Django and two of the subject and the two are the answer. Group what is left by
signature, because two hundred identical stacks are one finding and not two
hundred. Then walk to the divergence point — the deepest frame every occurrence
shares — which is where one call path became many.

For an N+1 every stack is identical and the divergence point is the innermost
frame: the line in the loop. For events arriving from two different sites the
divergence point is the function that calls both. Both are the right answer to
*what should I look at*, and they are the same computation.

**A sample localizes as well as a census, and S-3.6 measured why that matters.**
Capturing a stack costs about 1.4µs per frame of stack depth — 86µs an event at
fifty frames, which is a quarter of the database call being observed. Grouping
and the suffix walk care about which stacks occurred, not how many times, so a
sample of the events gives the same site as all of them. That is the mitigation
S-3.6 handed to this story, and it is a property of the algorithm rather than an
option somebody has to remember.

**Two things are reported rather than guessed.** Events whose every frame belongs
to the framework have no site in the subject's code at all — which is a finding
(the cost is in a dependency, and S-2.9 already says what happens to those) and
not an empty signature to be grouped with other empty signatures. And a stack
captured inside a coroutine shows the event loop rather than whatever awaited it,
so the callers past that boundary are not recoverable; the group says so instead
of naming `base_events.py` as the culprit.
"""

from __future__ import annotations

import os.path
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Frames belonging to the machinery that drives coroutines. A stack captured
# inside one shows the loop that resumed the coroutine, not the code that
# awaited it, so everything outward of this point is about the scheduler and
# nothing about the subject.
ASYNC_BOUNDARY_MARKERS: tuple[str, ...] = (
    "asyncio/base_events.py",
    "asyncio/events.py",
    "asyncio/futures.py",
    "asyncio/tasks.py",
    "concurrent/futures/thread.py",
)

# How many lines of source to carry either side of a site. Enough to see a loop
# header above a query inside it, which is the shape most findings have.
SOURCE_CONTEXT = 3


class LocalizationError(Exception):
    """A site could not be localized from the stacks given."""


@dataclass(frozen=True, order=True)
class Frame:
    """One position in a stack: file, line, function.

    Hashable so it can group, and ordered so that groups with the same number of
    occurrences still come out in the same order every run — which matters
    because these end up in a prompt, and ADR 002 makes a reordered prompt an
    invalidated cache.
    """

    filename: str
    lineno: int
    function: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno} in {self.function}"

    @property
    def location(self) -> str:
        """File and line, which is what a person pastes into an editor."""
        return f"{self.filename}:{self.lineno}"


@dataclass(frozen=True)
class StackGroup:
    """Every occurrence that arrived by the same route, counted once."""

    frames: tuple[Frame, ...]
    """Innermost first, framework frames removed."""

    occurrences: int
    async_boundary: bool = False
    """True when the callers past a point were the event loop rather than the subject."""

    @property
    def site(self) -> Frame | None:
        """The innermost frame in the subject's own code, or `None` if there is none."""
        return self.frames[0] if self.frames else None


@dataclass(frozen=True)
class DependencyClosure:
    """The causal site and everything the runtime can say about how it was reached.

    The story asks for models, relationship declarations, consumers and callers.
    The callers are here exactly, because the runtime recorded them. The source
    excerpt is here because the harness may read the file even though the agent
    may not. **Models and relationship declarations are framework knowledge and
    come from a resolver the adapter supplies** — with none, the closure is the
    runtime half and says so rather than implying the rest was checked and found
    empty.
    """

    site: Frame
    callers: tuple[Frame, ...]
    source: tuple[str, ...] = ()
    declarations: tuple[str, ...] = ()
    resolver_supplied: bool = False

    def explanation(self) -> str:
        lines = [f"Site: {self.site}"]
        if self.callers:
            lines.append("Reached from:")
            lines += [f"  {caller}" for caller in self.callers]
        if self.source:
            lines.append("Source:")
            lines += [f"  {line}" for line in self.source]
        if self.declarations:
            lines.append("Related declarations:")
            lines += [f"  {item}" for item in self.declarations]
        elif not self.resolver_supplied:
            lines.append(
                "Related model and relationship declarations were not resolved: that needs a "
                "framework adapter, and none was supplied. This is not the same as there "
                "being none."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class Localization:
    """What the stacks say about where these events came from."""

    groups: tuple[StackGroup, ...]
    causal_site: Frame | None
    divergence: tuple[Frame, ...]
    """The frames every localized occurrence shares, innermost first.

    Its first element is the causal site. Empty when the occurrences share no
    frame at all, which is a real answer — these events came from unrelated
    places and no single line explains them.
    """

    outside_subject: int = 0
    """Occurrences with no frame in the subject's code once the framework was stripped."""

    async_boundaries: int = 0

    @property
    def localized(self) -> int:
        return sum(group.occurrences for group in self.groups)

    def explanation(self) -> str:
        if self.causal_site is None:
            if self.outside_subject and not self.groups:
                return (
                    f"All {self.outside_subject} occurrence(s) were raised entirely inside "
                    "framework or dependency code, so there is no site in this project to "
                    "point at. The cause is in code the subject does not own, which is a "
                    "finding rather than a failure to find one."
                )
            return (
                f"{self.localized} occurrence(s) in {len(self.groups)} group(s) share no "
                "frame at all, so no single line explains them. Localize each group "
                "separately, or measure something narrower."
            )

        head = (
            f"{self.localized} occurrence(s) in {len(self.groups)} group(s) all pass through "
            f"{self.causal_site}."
        )
        if self.outside_subject:
            head += (
                f" A further {self.outside_subject} were raised entirely inside framework code "
                "and are not attributed here."
            )
        if self.async_boundaries:
            head += (
                f" {self.async_boundaries} group(s) crossed an async boundary, so their callers "
                "beyond it are the event loop and were not recoverable."
            )
        return head


def normalize(
    stack: Iterable[traceback.FrameSummary] | Sequence[Frame],
    *,
    deny: Sequence[str] = (),
) -> tuple[tuple[Frame, ...], bool]:
    """Drop framework frames, and say whether an async boundary was crossed.

    `deny` is the adapter's list of path fragments belonging to the framework —
    `django/db/`, `rest_framework/`, `site-packages`. Matching is on the
    normalized path, so a caller does not have to know which separator the host
    uses.

    **Stripping happens everywhere in the stack, not only at the ends.** A stack
    through an ORM is framework at the bottom, the subject in the middle, the
    framework's request handling above that and the server above that; keeping
    only the subject's frames is what leaves a signature two projects can be
    compared on.

    Returns the surviving frames innermost-first, and a flag that is true when
    the stack passed through the machinery that drives coroutines — beyond which
    the callers are the event loop and not the code that awaited.
    """
    frames = [_as_frame(item) for item in stack]
    crossed = any(_matches(frame.filename, ASYNC_BOUNDARY_MARKERS) for frame in frames)

    kept = tuple(
        frame
        for frame in frames
        if not _matches(frame.filename, deny)
        and not _matches(frame.filename, ASYNC_BOUNDARY_MARKERS)
    )
    return kept, crossed


def localize(
    stacks: Iterable[Iterable[traceback.FrameSummary] | Sequence[Frame]],
    *,
    deny: Sequence[str] = (),
) -> Localization:
    """Group the stacks and walk to the frame they all share.

    The whole of `01-primitives.md` §12's localization, and it needs no source,
    no framework knowledge and no model call — only the stacks S-1.3 captured.

    A sample works as well as every event: grouping is by distinct route and the
    walk is over the groups, so the site does not depend on how many times each
    route was taken. S-3.6 measured what that saves.
    """
    counted: dict[tuple[Frame, ...], int] = {}
    boundaries: dict[tuple[Frame, ...], bool] = {}
    outside = 0

    for stack in stacks:
        frames, crossed = normalize(stack, deny=deny)
        if not frames:
            # Every frame belonged to the framework. Grouping these under an
            # empty signature would invent a shared site they do not have.
            outside += 1
            continue
        counted[frames] = counted.get(frames, 0) + 1
        boundaries[frames] = boundaries.get(frames, False) or crossed

    groups = tuple(
        StackGroup(frames=frames, occurrences=count, async_boundary=boundaries[frames])
        for frames, count in sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    )
    divergence = _common_suffix([group.frames for group in groups])

    return Localization(
        groups=groups,
        causal_site=divergence[0] if divergence else None,
        divergence=divergence,
        outside_subject=outside,
        async_boundaries=sum(1 for group in groups if group.async_boundary),
    )


def closure(
    localization: Localization,
    *,
    root: Path | None = None,
    resolver: Callable[[Frame], Sequence[str]] | None = None,
) -> DependencyClosure:
    """The causal site, its callers, its source, and whatever an adapter can add.

    `root` lets the harness read the site's own source — which the agent is not
    doing, and which is the difference between naming a line and showing the loop
    it sits in. Reading is best-effort: a file the harness cannot see yields no
    excerpt rather than an error, because a missing excerpt weakens a finding and
    a raised exception loses it.

    Raises:
        LocalizationError: nothing was localized, so there is no site to close
            over. Refused rather than returning an empty closure, which would
            read as *a site with no dependencies*.
    """
    if localization.causal_site is None:
        message = (
            "these occurrences share no frame in the subject's code, so there is no causal "
            "site to build a closure around. " + localization.explanation()
        )
        raise LocalizationError(message)

    site = localization.causal_site
    return DependencyClosure(
        site=site,
        callers=localization.divergence[1:],
        source=_read_source(site, root),
        declarations=tuple(resolver(site)) if resolver is not None else (),
        resolver_supplied=resolver is not None,
    )


def _common_suffix(stacks: Sequence[tuple[Frame, ...]]) -> tuple[Frame, ...]:
    """The frames every stack ends with, innermost first.

    Innermost-first ordering means the shared *outer* frames are a suffix, and
    the first element of that suffix is the deepest frame they all have — the
    divergence point, and the site.
    """
    if not stacks:
        return ()

    shared = stacks[0]
    for stack in stacks[1:]:
        length = 0
        while (
            length < len(shared)
            and length < len(stack)
            and shared[len(shared) - 1 - length] == stack[len(stack) - 1 - length]
        ):
            length += 1
        shared = shared[len(shared) - length :] if length else ()
        if not shared:
            break
    return shared


def _read_source(site: Frame, root: Path | None) -> tuple[str, ...]:
    if root is None:
        return ()
    path = root / site.filename if not Path(site.filename).is_absolute() else Path(site.filename)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()

    first = max(site.lineno - 1 - SOURCE_CONTEXT, 0)
    last = min(site.lineno + SOURCE_CONTEXT, len(lines))
    return tuple(f"{number + 1:5d} {lines[number]}" for number in range(first, last))


def _as_frame(item: traceback.FrameSummary | Frame) -> Frame:
    if isinstance(item, Frame):
        return item
    return Frame(
        filename=item.filename,
        lineno=item.lineno or 0,
        function=item.name,
    )


def _matches(filename: str, patterns: Sequence[str]) -> bool:
    """Whether a frame's file matches any pattern, separator-insensitively.

    Patterns are path fragments rather than globs. An adapter naming
    `django/db/` should not have to know whether the subject ran on a host whose
    separator is a backslash — the stacks come from a Linux container and the
    comparison may happen anywhere.
    """
    normalized = os.path.normcase(filename).replace("\\", "/")
    return any(os.path.normcase(pattern).replace("\\", "/") in normalized for pattern in patterns)


@dataclass(frozen=True)
class Localizer:
    """A deny list and a source root, held so a caller states them once.

    The adapter supplies the deny list (S-14.1); everything else here is
    framework-free.
    """

    deny: tuple[str, ...] = ()
    root: Path | None = None
    resolver: Callable[[Frame], Sequence[str]] | None = field(default=None, repr=False)

    def localize(
        self, stacks: Iterable[Iterable[traceback.FrameSummary] | Sequence[Frame]]
    ) -> Localization:
        return localize(stacks, deny=self.deny)

    def closure(self, localization: Localization) -> DependencyClosure:
        return closure(localization, root=self.root, resolver=self.resolver)

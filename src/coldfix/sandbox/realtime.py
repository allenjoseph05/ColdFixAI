"""Recognise a hard real-time system and decline before touching it.

Epic 2, S-2.8. ADR 007 gives two reasons and says the second is worse.

Measurement-based analysis is insufficient for worst-case execution time: the
requirement is the tail of the distribution, and sampling does not bound a tail.
That alone would make this system's answers useless here.

**Worse, a caching optimisation improves every metric this system measures while
degrading worst-case timing.** The tool would report a confident, verified,
correct-looking improvement that makes the system less safe — and every check
downstream would agree with it, because every check downstream measures the
average case. `CLAUDE.md` names this the only category where running the system
could make things worse while reporting success. It is the reason the refusal
happens here rather than being handled later.

**Detection runs before grounding because grounding requires a
`ScreenedRepository`, and screening is the only thing that makes one.** The
ordering is not a rule about call sequence that a later story could get wrong;
there is no unscreened repository object for grounding to accept. Same
construction as `VerifiedDatabase`.

**The hard part is not detecting real-time systems, it is not refusing Django
apps.** This tool's development target is a helpdesk application, and `deadline`
is an ordinary field name in half the task trackers ever written; `scheduler`,
`priority`, `real time` and `critical` are ordinary English. A detector keying on
those refuses its own target repository on day one. So every pattern here is
anchored to a token that does not occur in ordinary application code —
`SCHED_DEADLINE` rather than `deadline`, `IEC 61508` rather than `safety`,
`\\bRTOS\\b` rather than `rtos` as a substring — and the fixture carries a
control that uses all the innocent words at once and must not be refused. ADR
006's rule: every defect carries a control, or the detector learns to say yes.

**An incomplete scan is not a clear one.** A repository too large to finish
scanning is refused certification rather than reported clean, because "nothing
was found" and "we stopped looking" must never be the same answer for a check
whose failure mode is degrading a safety-critical system while reporting
success.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# Generous enough that no real repository hits them, present so that a
# pathological one fails loudly instead of being reported clear.
MAX_FILES_SCANNED = 50_000
MAX_FILE_BYTES = 4 * 1024 * 1024

# Machine-generated or fetched trees. Deliberately does not include `vendor` or
# `third_party`: a vendored RTOS is exactly the thing being looked for, and
# third-party code being unpatchable (S-2.9) does not make it undetectable.
SKIPPED_DIRECTORIES = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache", ".ruff_cache"}
)

_BINARY_SNIFF_BYTES = 8192


class MarkerCategory(StrEnum):
    """The four kinds of evidence the story asks for."""

    RTOS = "RTOS import or header"
    DEADLINE = "deadline or real-time scheduling annotation"
    CERTIFICATION = "safety-certification marker"
    FRAMEWORK = "real-time framework signature"


@dataclass(frozen=True)
class Marker:
    """One thing worth refusing over, and the reason it is specific.

    `why` exists because a refusal a person cannot audit is one they will
    override. If this fires on an innocent repository, the reader needs to see
    what was matched and decide whether the pattern is wrong.
    """

    category: MarkerCategory
    name: str
    pattern: re.Pattern[str]


def _content(category: MarkerCategory, name: str, pattern: str) -> Marker:
    return Marker(category, name, re.compile(pattern, re.IGNORECASE))


# Every pattern is anchored to a token that does not appear in ordinary
# application code. The words that do — deadline, scheduler, priority, real
# time, critical, safety — are deliberately absent, because this tool's own
# development target is a helpdesk application full of them.
CONTENT_MARKERS: tuple[Marker, ...] = (
    # --- RTOS ---
    _content(MarkerCategory.RTOS, "FreeRTOS", r"\bFreeRTOS\b"),
    _content(MarkerCategory.RTOS, "VxWorks", r"\bvxWorks\b|\bVxWorks\b"),
    _content(MarkerCategory.RTOS, "QNX Neutrino", r"\bQNX\b|\bNeutrino\b"),
    _content(MarkerCategory.RTOS, "RTEMS", r"\bRTEMS\b"),
    _content(MarkerCategory.RTOS, "ThreadX", r"\bThreadX\b|\btx_api\.h\b"),
    _content(MarkerCategory.RTOS, "ChibiOS", r"\bChibiOS\b"),
    _content(MarkerCategory.RTOS, "NuttX", r"\bNuttX\b"),
    _content(MarkerCategory.RTOS, "Zephyr kernel", r"#include\s*<zephyr/|\bCONFIG_ZEPHYR\b"),
    _content(MarkerCategory.RTOS, "uC/OS or Micrium", r"\bMicrium\b|\buC/OS\b|\bucos_ii\b"),
    _content(MarkerCategory.RTOS, "Contiki", r"\bContiki\b"),
    _content(MarkerCategory.RTOS, "generic RTOS reference", r"\bRTOS\b"),
    # --- deadlines and real-time scheduling ---
    _content(MarkerCategory.DEADLINE, "SCHED_DEADLINE", r"\bSCHED_DEADLINE\b"),
    _content(MarkerCategory.DEADLINE, "SCHED_FIFO or SCHED_RR", r"\bSCHED_(FIFO|RR)\b"),
    _content(MarkerCategory.DEADLINE, "sched_setscheduler", r"\bsched_setscheduler\b"),
    _content(
        MarkerCategory.DEADLINE,
        "pthread real-time scheduling",
        r"\bpthread_(setschedparam|attr_setschedpolicy|attr_setinheritsched)\b",
    ),
    _content(MarkerCategory.DEADLINE, "worst-case execution time", r"\bWCET\b"),
    _content(
        MarkerCategory.DEADLINE,
        "worst-case execution time, spelled out",
        r"worst[- _]case[- _]execution[- _]time",
    ),
    _content(MarkerCategory.DEADLINE, "deadline annotation", r"@deadline\b|#pragma\s+deadline\b"),
    _content(MarkerCategory.DEADLINE, "mlockall", r"\bmlockall\b"),
    # --- safety certification ---
    _content(MarkerCategory.CERTIFICATION, "DO-178 or DO-254", r"\bDO[- ]?(178|254)[A-C]?\b"),
    _content(MarkerCategory.CERTIFICATION, "IEC 61508", r"\bIEC[- ]?61508\b"),
    _content(MarkerCategory.CERTIFICATION, "ISO 26262", r"\bISO[- ]?26262\b"),
    _content(MarkerCategory.CERTIFICATION, "EN 50128", r"\bEN[- ]?50128\b"),
    _content(MarkerCategory.CERTIFICATION, "ARINC 653", r"\bARINC[- ]?653\b"),
    _content(MarkerCategory.CERTIFICATION, "MISRA", r"\bMISRA\b"),
    _content(MarkerCategory.CERTIFICATION, "AUTOSAR", r"\bAUTOSAR\b"),
    # A bare "SIL" is silicon, silence and a hundred other things; the integrity
    # level is only meaningful with its number attached.
    _content(MarkerCategory.CERTIFICATION, "safety integrity level", r"\bSIL[- ]?[1-4]\b"),
    _content(MarkerCategory.CERTIFICATION, "automotive integrity level", r"\bASIL[- ]?[A-D]\b"),
    _content(MarkerCategory.CERTIFICATION, "design assurance level", r"\bDAL[- ][A-E]\b"),
    # --- real-time frameworks ---
    _content(MarkerCategory.FRAMEWORK, "PREEMPT_RT", r"\bPREEMPT_RT\b|\bpreempt[- ]rt\b"),
    _content(MarkerCategory.FRAMEWORK, "Xenomai", r"\bXenomai\b"),
    _content(MarkerCategory.FRAMEWORK, "RTAI", r"\bRTAI\b"),
    _content(MarkerCategory.FRAMEWORK, "cyclictest", r"\bcyclictest\b"),
    _content(MarkerCategory.FRAMEWORK, "Ravenscar profile", r"\bRavenscar\b"),
)

# Some evidence is the presence of a file rather than anything written in one.
FILENAME_MARKERS: tuple[tuple[MarkerCategory, str, str], ...] = (
    (MarkerCategory.RTOS, "FreeRTOS configuration", "FreeRTOSConfig.h"),
    (MarkerCategory.RTOS, "Zephyr project configuration", "prj.conf"),
    (MarkerCategory.FRAMEWORK, "PlatformIO project", "platformio.ini"),
    (MarkerCategory.FRAMEWORK, "Arduino sketch", "*.ino"),
)


class RealTimeSystemError(Exception):
    """The repository looks like a hard real-time system, so it is declined.

    The message is the explanation the story asks for, in one paragraph,
    followed by the evidence. It is written for a person deciding whether the
    refusal is correct, because a refusal nobody can audit is one that gets
    worked around.
    """

    def __init__(self, screening: Screening) -> None:
        self.screening = screening
        super().__init__(screening.explanation())


class IncompleteScreeningError(Exception):
    """The repository could not be scanned in full, so it cannot be cleared.

    Distinct from a refusal. Nothing real-time was necessarily found — the point
    is that not finding something is only meaningful if the search finished, and
    for a check whose failure mode is degrading a safety-critical system while
    reporting success, "we stopped looking" must never read as "nothing there".
    """


@dataclass(frozen=True)
class Detection:
    """One marker, and where it was seen, so the refusal can be audited."""

    marker: Marker
    path: str
    line: int
    excerpt: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} — {self.marker.name} ({self.marker.category.value})"


@dataclass(frozen=True)
class Screening:
    """What the scan found, and whether it finished."""

    root: Path
    detections: tuple[Detection, ...]
    files_scanned: int
    truncated: bool = False

    @property
    def clear(self) -> bool:
        """Nothing found *and* the search finished. Both, or neither."""
        return not self.detections and not self.truncated

    @property
    def categories(self) -> tuple[MarkerCategory, ...]:
        seen = {detection.marker.category for detection in self.detections}
        return tuple(category for category in MarkerCategory if category in seen)

    def explanation(self) -> str:
        """The one paragraph the story asks for, plus what was matched."""
        evidence = "\n".join(f"  - {detection}" for detection in self.detections[:20])
        more = (
            f"\n  ... and {len(self.detections) - 20} more"
            if len(self.detections) > 20  # noqa: PLR2004
            else ""
        )
        return (
            f"Refusing to analyse {self.root}: it looks like a hard real-time system. This tool "
            "works by measurement — run the program, time it, change one thing, time it again — "
            "and measurement-based analysis is insufficient for worst-case execution time, "
            "because the requirement is the tail of the distribution and sampling does not bound "
            "a tail. The second reason is worse than the first: a caching optimisation improves "
            "every metric this tool measures while degrading worst-case timing, so it would "
            "report a confident, verified, correct-looking improvement that makes the system "
            "less safe, and every check downstream would agree because every check downstream "
            "measures the average case. That is why this is a refusal rather than a caveat, and "
            "why it happens before anything is grounded, measured or changed.\n\n"
            f"Matched {len(self.detections)} marker(s) across "
            f"{len(self.categories)} categor(ies):\n{evidence}{more}"
        )


@dataclass(frozen=True)
class ScreenedRepository:
    """A repository that has been screened and not refused.

    Constructing one *is* the screening. Grounding takes one of these, so the
    check cannot run after grounding — there is no unscreened repository object
    for grounding to accept. Same construction as `VerifiedDatabase`, and for
    the same reason: an ordering requirement enforced by a type rather than by
    remembering to call something first.

    Raises:
        RealTimeSystemError: real-time markers were found.
        IncompleteScreeningError: the repository could not be scanned in full.
    """

    root: Path
    screening: Screening = field(init=False, repr=False)

    def __post_init__(self) -> None:
        resolved = self.root.resolve()
        if not resolved.is_dir():
            message = f"not a directory: {resolved}"
            raise IncompleteScreeningError(message)

        object.__setattr__(self, "root", resolved)
        result = screen(resolved)
        object.__setattr__(self, "screening", result)

        if result.detections:
            raise RealTimeSystemError(result)
        if result.truncated:
            message = (
                f"{resolved} holds more than {MAX_FILES_SCANNED} files, so the real-time "
                "screening did not finish. Refusing to certify it: not finding a marker only "
                "means something if the search completed."
            )
            raise IncompleteScreeningError(message)


def screen(root: Path) -> Screening:
    """Scan `root` for real-time markers and report, without deciding.

    Separated from the refusal so a caller can look at the evidence — a
    developer checking whether a pattern is over-broad needs the findings, not
    an exception. `ScreenedRepository` is what turns this into a decision.
    """
    detections: list[Detection] = []
    scanned = 0
    truncated = False

    for path in _walk(root):
        if scanned >= MAX_FILES_SCANNED:
            truncated = True
            break
        scanned += 1
        detections += _scan_file(root, path)

    return Screening(
        root=root,
        detections=tuple(detections),
        files_scanned=scanned,
        truncated=truncated,
    )


def _walk(root: Path) -> Iterator[Path]:
    """Every file worth reading, skipping trees nobody wrote by hand."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            # A directory that cannot be listed is not evidence of anything, and
            # failing the whole screening over one unreadable path would make
            # the tool unusable on repositories with odd permissions.
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIPPED_DIRECTORIES:
                    stack.append(entry)
            elif entry.is_file():
                yield entry


def _scan_file(root: Path, path: Path) -> list[Detection]:
    relative = path.relative_to(root).as_posix()
    detections: list[Detection] = []

    for category, name, glob in FILENAME_MARKERS:
        if path.match(glob):
            detections.append(
                Detection(
                    marker=Marker(category, name, re.compile(re.escape(glob))),
                    path=relative,
                    line=0,
                    excerpt=path.name,
                )
            )

    text = _read_text(path)
    if text is None:
        return detections

    for number, line in enumerate(text.splitlines(), start=1):
        for marker in CONTENT_MARKERS:
            if marker.pattern.search(line):
                detections.append(
                    Detection(
                        marker=marker,
                        path=relative,
                        line=number,
                        excerpt=line.strip()[:120],
                    )
                )
    return detections


def _read_text(path: Path) -> str | None:
    """The file's text, or `None` if it is binary or unreadable.

    A NUL byte in the first few kilobytes is the standard heuristic for binary,
    and it matters because a compiled artifact can contain any byte sequence and
    would produce matches nobody can act on.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in head:
                return None
            rest = handle.read(MAX_FILE_BYTES - len(head))
    except OSError:
        return None
    return (head + rest).decode("utf-8", errors="replace")

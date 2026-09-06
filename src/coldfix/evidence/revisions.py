"""Probing a patch without browsing the repository.

S-23.1 and S-23.2. The Adversary's only tool, and the reason it can be both
isolated and effective: it constructs any input it likes, runs it on both
revisions, and sees both outputs and both cost profiles. More attacking power
than a fixed list of input classes, and the same boundary.

**It returns what the program did, never what the program is.** No file contents,
no diff, no path into either workspace. A tool that could read source would be a
tool that lets a reviewer form its own view of a repository, which is exactly
what having no filesystem access is for.

**Both sides get the same input, structurally.** One argument, used twice. A
comparison where the two sides were given different inputs would find a
difference every time and mean nothing, and an API with two input parameters
invites exactly that.

**There is no field for the author's reasoning.** `PatchUnderAudit` forbids extra
keys, so passing one is an error rather than a value quietly dropped. A reviewer
handed a justification reviews the justification.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from coldfix.collect.measurement import BareMeasurement, Clock, measure
from coldfix.collect.usage import ChildRunner, MeasurementError

PROBE_REPEATS = 2
"""Enough for a spread, and no more: an attack input may be pathological on
purpose, and running it five times is paying four times over to learn what one
comparison already showed."""


class Side(BaseModel, frozen=True):
    """What one revision did with the input.

    Deliberately not a `BareMeasurement`: that carries a command and a digest of
    a whole run, and what a reviewer needs is what came out and what it cost.
    """

    ran: bool
    output_digest: str
    output_bytes: int
    wall_s: float
    peak_rss_bytes: int | None
    detail: str = ""


class Comparison(BaseModel, frozen=True):
    """One input, run on both revisions."""

    given: tuple[str, ...]
    baseline: Side
    patched: Side

    @property
    def outputs_agree(self) -> bool:
        """Byte for byte. The only definition that does not need a judgement."""
        return self.baseline.output_digest == self.patched.output_digest

    @property
    def both_ran(self) -> bool:
        return self.baseline.ran and self.patched.ran

    @property
    def broke(self) -> bool:
        """The patch changed what the program does, or stopped it working.

        A baseline that never ran is not the patch's fault, and this says so:
        an input the original also rejects has found nothing.
        """
        if not self.baseline.ran:
            return False
        return not self.patched.ran or not self.outputs_agree

    def why(self) -> str:
        if not self.baseline.ran:
            return f"the original rejects this input too: {self.baseline.detail}"
        if not self.patched.ran:
            return f"the patched revision stopped working: {self.patched.detail}"
        if not self.outputs_agree:
            return (
                f"the output changed: {self.baseline.output_bytes} bytes became "
                f"{self.patched.output_bytes}"
            )
        cost = self.patched.wall_s - self.baseline.wall_s
        return f"identical output, {cost:+.3f}s"


class PatchUnderAudit(BaseModel):
    """Everything the Adversary is given, and nothing else.

    `extra="forbid"` is the enforcement. Without it, a caller passing
    `reasoning=...` would have it silently dropped -- safe, and invisible, so
    nobody would learn they had tried. Forbidding it makes the attempt an error.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    diff: str
    test: str
    baseline: Path
    patched: Path
    command: tuple[str, ...]


def compare(
    given: Sequence[str],
    *,
    under_audit: PatchUnderAudit,
    runner: ChildRunner | None = None,
    clock: Clock | None = None,
) -> Comparison:
    """Run one input on both revisions and report what each did.

    The input is whatever the caller wants appended to the command. Nothing here
    validates or sanitises it -- constructing the input the patch cannot handle is
    the reviewer's entire job, and a tool that filtered them would be filtering
    out the finding.
    """
    return Comparison(
        given=tuple(given),
        baseline=_run(under_audit.baseline, under_audit.command, given, runner, clock),
        patched=_run(under_audit.patched, under_audit.command, given, runner, clock),
    )


def probe(
    inputs: Sequence[Sequence[str]],
    *,
    under_audit: PatchUnderAudit,
    runner: ChildRunner | None = None,
    clock: Clock | None = None,
) -> tuple[Comparison, ...]:
    """Every input, both revisions. Returns all of them, not only the failures.

    A reviewer that reported only what broke would leave nobody able to tell a
    patch that survived twenty inputs from one that survived two.
    """
    return tuple(
        compare(given, under_audit=under_audit, runner=runner, clock=clock) for given in inputs
    )


def broken_by(comparisons: Sequence[Comparison]) -> tuple[Comparison, ...]:
    """The ones that found something."""
    return tuple(comparison for comparison in comparisons if comparison.broke)


def _run(
    workspace: Path,
    command: Sequence[str],
    given: Sequence[str],
    runner: ChildRunner | None,
    clock: Clock | None,
) -> Side:
    """One revision, one input. A crash is an observation, not an exception."""
    arguments = {"cwd": workspace, "repeats": PROBE_REPEATS, "require_repeatable": False}
    if runner is not None:
        arguments["runner"] = runner
    if clock is not None:
        arguments["clock"] = clock
    try:
        measured: BareMeasurement = measure([*command, *given], **arguments)  # type: ignore[arg-type]
    except (MeasurementError, ValueError, OSError) as failed:
        # `ValueError` and `OSError` are here because of an input this file's own
        # tests produced by accident: a null byte cannot be passed in an argv, and
        # the operating system refuses it before any program sees it. That is a
        # fact about the input rather than about the patch, and an audit that
        # crashed on it would end a review over something the reviewer is
        # supposed to be free to try.
        return Side(
            ran=False,
            output_digest="",
            output_bytes=0,
            wall_s=0.0,
            peak_rss_bytes=None,
            detail=str(failed)[:300],
        )
    return Side(
        ran=True,
        output_digest=measured.output_digest,
        output_bytes=measured.output_bytes,
        wall_s=measured.wall.median,
        peak_rss_bytes=measured.peak_rss_bytes,
    )

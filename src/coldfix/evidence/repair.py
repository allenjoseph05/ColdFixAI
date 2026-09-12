"""Repair as a search whose winner is measured, not argued for.

E22. Asking a model once for a fix is the known ceiling: on SWE-Perf, correct
patches averaged 1.28% improvement against 10.85% for the humans who wrote the
originals. The difference is not model quality. It is that one is a guess and the
other is a search with measurement in the loop.

So: generate several candidates, **run every one**, keep what measurably won, and
archive what lost so a later round cannot propose it again.

**Nothing is selected for being plausible.** The archive holds a score per
candidate and the winner is chosen from it. A candidate that reads beautifully
and measured slower is a loser with a good explanation.

**Faster is not the same as better.** A candidate that saves a second and costs
300MB is a *trade* -- kept, scored, and reported as one. Silently shipping it
would be the guard-counter failure this project refuses everywhere else.

**No patch exists before a failing test does.** `Falsified` is minted only by
`must_fail`, and `apply` will not accept anything else -- not by checking a flag,
but because there is no other way to obtain the value it requires.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

MAX_CANDIDATES = 8
"""Enough for the search to be a search. Beyond this the cost of measuring grows
faster than the chance that attempt nine is the one."""

GUARD_MARGIN = 1.05
"""What another metric may drift before a saving counts as paid for elsewhere.
The same figure the finding audit uses, for the same reason."""


class RepairError(Exception):
    """The repair could not proceed."""


class ForgedTokenError(RepairError):
    """Something constructed a `Falsified` without running the gate.

    The gate is the only thing that may mint one. A token that could be written
    by hand is a rule, and the whole point of making it a value is that it is
    not.
    """

    def __init__(self) -> None:
        super().__init__(
            "a Falsified was constructed directly. Only `must_fail` mints one, because it is "
            "proof that a test failed against unpatched code -- and proof that can be written "
            "by hand is an assertion."
        )


class NoFailingTestError(RepairError):
    """A test that passes before anything changed is testing nothing."""

    PASSED = "it passed"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"the test did not fail against unpatched code: {detail}. A test that passes "
            "before you change anything proves the problem is absent, not that a fix works."
        )

    @classmethod
    def passed(cls) -> NoFailingTestError:
        return cls(cls.PASSED)

    @classmethod
    def never_ran(cls, detail: str) -> NoFailingTestError:
        """A broken test also exits non-zero, and under *non-zero means failed*
        it would authorise patching -- the gate inverted."""
        return cls(f"it did not run: {detail[:200]}")


class Outcome(StrEnum):
    """What became of one candidate."""

    WON = "won"
    TRADE = "trade"
    """Faster, and paid for somewhere else. Shown, never shipped silently."""

    SLOWER = "slower"
    BROKE_OUTPUT = "broke_output"
    BROKE_TESTS = "broke_tests"


_MINTED = object()
"""Held by this module and handed to nothing. See `ForgedTokenError`."""


class Falsified(BaseModel):
    """Proof that the test failed before any patch existed."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    test: str
    exit_code: int
    detail: str
    minted: object

    @model_validator(mode="after")
    def _only_the_gate_mints_these(self) -> Falsified:
        if self.minted is not _MINTED:
            raise ForgedTokenError
        return self


class Candidate(BaseModel, frozen=True):
    """One attempt at a fix, before anything is known about it."""

    identifier: str
    approach: str
    diff: str


class Scored(BaseModel, frozen=True):
    """A candidate, and what running it actually showed."""

    candidate: Candidate
    measurement_id: str
    wall_s: float
    peak_rss_bytes: int | None = None
    outputs_match: bool = True
    tests_pass: bool = True

    def outcome(self, *, baseline: Scored) -> Outcome:
        """What this candidate is, judged only against measurements."""
        if not self.tests_pass:
            return Outcome.BROKE_TESTS
        if not self.outputs_match:
            return Outcome.BROKE_OUTPUT
        if self.wall_s >= baseline.wall_s:
            return Outcome.SLOWER
        if self._paid_for_elsewhere(baseline):
            return Outcome.TRADE
        return Outcome.WON

    def _paid_for_elsewhere(self, baseline: Scored) -> bool:
        was, now = baseline.peak_rss_bytes, self.peak_rss_bytes
        if was is None or now is None:
            return False
        return now > 0 if was == 0 else now > was * GUARD_MARGIN

    def share_removed(self, baseline: Scored) -> float:
        if baseline.wall_s <= 0:
            return 0.0
        return (baseline.wall_s - self.wall_s) / baseline.wall_s


class Archive(BaseModel):
    """Every attempt and its score. Winners and losers alike.

    Losers are kept because the next round must not re-propose what already lost
    -- a search that forgets its failures is a search that repeats them, and
    every repeat is paid for twice: once to generate and once to measure.
    """

    baseline: Scored
    scored: tuple[Scored, ...] = ()

    def record(self, scored: Scored) -> Archive:
        return Archive(baseline=self.baseline, scored=(*self.scored, scored))

    def already_tried(self, approach: str) -> bool:
        return any(entry.candidate.approach == approach for entry in self.scored)

    def of(self, outcome: Outcome) -> tuple[Scored, ...]:
        return tuple(e for e in self.scored if e.outcome(baseline=self.baseline) is outcome)

    @property
    def winner(self) -> Scored | None:
        """The fastest candidate that broke nothing and cost nothing elsewhere."""
        won = self.of(Outcome.WON)
        return min(won, key=lambda e: e.wall_s) if won else None

    @property
    def trades(self) -> tuple[Scored, ...]:
        """Faster, and paid for. Reported so somebody can decide."""
        return self.of(Outcome.TRADE)

    @property
    def losers(self) -> tuple[Scored, ...]:
        return tuple(
            e
            for e in self.scored
            if e.outcome(baseline=self.baseline)
            in {Outcome.SLOWER, Outcome.BROKE_OUTPUT, Outcome.BROKE_TESTS}
        )

    def summary(self) -> str:
        won = self.winner
        head = (
            f"{won.candidate.approach}: {won.share_removed(self.baseline):.1%} faster"
            if won
            else "nothing beat the baseline"
        )
        return (
            f"{head}  ({len(self.scored)} measured, {len(self.trades)} trade(s), "
            f"{len(self.losers)} rejected)"
        )


def must_fail(test: str, run: Callable[[str], tuple[int, str]]) -> Falsified:
    """Run the test against unpatched code. Only a failure mints a token.

    Three outcomes, and the third is why the exit code alone is not enough: a
    script with a syntax error also exits non-zero, and under *non-zero means it
    failed* a broken test would authorise patching -- the gate inverted. So a
    result that names a collection or import problem is refused too.
    """
    exit_code, detail = run(test)
    if exit_code == 0:
        raise NoFailingTestError.passed()
    if _did_not_run(detail):
        raise NoFailingTestError.never_ran(detail)
    return Falsified(test=test, exit_code=exit_code, detail=detail[:300], minted=_MINTED)


BROKEN = ("SyntaxError", "ImportError", "ModuleNotFoundError", "fixture", "collection error")


def _did_not_run(detail: str) -> bool:
    """Whether the test failed to run rather than failed."""
    return any(marker.lower() in detail.lower() for marker in BROKEN)


Propose = Callable[[Archive], Sequence[Candidate]]
"""Where candidates come from. A model in production, a list in a test -- the
search does not care, which is what makes the search testable without one."""

Apply = Callable[[Falsified, Candidate], Scored]
"""Apply one candidate and measure it. Takes the token, so a candidate cannot be
measured -- or applied -- without a failing test having been proved first."""


def search(  # noqa: PLR0913 - the proof, the baseline, where candidates come from,
    # how they are measured, how many there may be, and what was already measured.
    # Every one is the caller's decision and a config object would hide them.
    *,
    falsified: Falsified,
    baseline: Scored,
    propose: Propose,
    apply: Apply,
    limit: int = MAX_CANDIDATES,
    already: Sequence[Scored] = (),
) -> Archive:
    """Generate, measure, keep, repeat. Selection is by measurement only.

    `propose` sees the archive, so a later round knows what already lost. It is
    called until the limit is reached or it offers nothing new; offering a repeat
    is not an error, it is simply not measured again.

    **`already` is what a second search must not forget.** S-28.5 found this at
    the join: when the patch audit sends a patch back, `optimize` runs again, and
    a fresh archive has never heard of the candidate that just lost -- so the
    same approach is proposed, measured a second time, and written into an
    append-only channel that correctly refuses it. Seeding the archive restores
    the property E22 built: a search that forgets its failures repeats them, and
    every repeat is paid for twice.

    The limit therefore bounds the candidates measured **for one finding**, not
    for one round. Eight attempts is eight attempts however many times the
    Adversary sends one back.
    """
    archive = Archive(baseline=baseline, scored=tuple(already))
    while len(archive.scored) < limit:
        fresh = [c for c in propose(archive) if not archive.already_tried(c.approach)]
        if not fresh:
            break
        for candidate in fresh[: limit - len(archive.scored)]:
            archive = archive.record(apply(falsified, candidate))
    return archive

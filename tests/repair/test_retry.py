"""S-10.5 — three attempts, and a structural reason to believe the second differs.

The backlog note is the whole story: *"must differ in approach" cannot be
self-judged — the agent writes its own approach label and can rename the same
idea.*

So the file is organised around two failures that pull in opposite directions. A
check that rejects too little lets the same patch through under a new name, and
the retry budget is spent on one idea. A check that rejects too much refuses the
second genuine idea at the same site — which is where the second idea usually is,
because that is where the cost was measured.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest

from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import (
    PHASE_CAPS,
    Budget,
    BudgetExhaustedError,
    Disposition,
    ProgressStalledError,
    Scope,
    StepUnit,
)
from coldfix.repair import retry as retry_module
from coldfix.repair.patch import SURGEON_TEMPERATURE, Attempt, Patch
from coldfix.repair.retry import (
    RETRY_TEMPERATURE,
    Escalation,
    RepeatedAttemptError,
    RetryError,
    authorize_attempt,
    check_attempt,
    escalate,
    exhausted,
    normalized_edit,
    record_attempt,
    repeats,
    shared_lines,
    temperature_for,
)

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 18))
FINDING = "n.plus.one"
SITE = "shop/serializers.py"
OTHER = "shop/models.py"


def a_diff(  # noqa: PLR0913 - every parameter is one axis a test varies on
    # its own, and bundling them into a shape object would be an abstraction
    # whose only purpose is to be unpacked here.
    *,
    path: str = SITE,
    start: int = 41,
    new_start: int | None = None,
    count: int = 2,
    removed: str = "        return AuthorSerializer(obj.author).data",
    added: str = "        return self._authors[obj.author_id]",
    context: str = "    def to_representation(self, obj):",
) -> str:
    return "\n".join(
        [
            f"--- a/{path}",
            f"+++ b/{path}",
            # The new-side start defaults to differing from the old-side one, and
            # `new_start` lets a test make them disagree *between* two diffs.
            # Shifting both by the same amount is not enough: `hunk_ranges`
            # reading group(3) instead of group(1) still overlaps, which is how
            # the first version of this fixture failed to discriminate.
            f"@@ -{start},{count} +{new_start if new_start is not None else start + 500},"
            f"{count} @@",
            f" {context}",
            f"-{removed}",
            f"+{added}",
            "",
        ]
    )


def a_patch(*, approach: str = "prefetch the authors", **kwargs: object) -> Patch:
    return Patch(
        diff=a_diff(**kwargs),  # type: ignore[arg-type]
        approach=approach,
        rationale="the serializer walked the relation per book",
    )


def an_attempt(
    patch: Patch | None = None, *, failure: str = "the cost claim still failed"
) -> Attempt:
    return Attempt(patch=patch if patch is not None else a_patch(), failure=failure)


def a_budget() -> Budget:
    return Budget(ledger=Ledger(), rate=RATE)


# ================== AC 1: three attempts, and the cap is S-5.4's


def test_the_repair_cap_is_three_attempts_per_finding() -> None:
    """**Asserted, not built.** S-5.4 compiled this in Epic 5, and nothing here
    re-implements it — the third Epic 10 criterion to turn out already enforced
    elsewhere, after S-10.2's ordering and S-10.4's protected paths."""
    cap = PHASE_CAPS[Phase.REPAIR]

    assert cap.limit == 3
    assert cap.unit is StepUnit.ATTEMPT
    assert cap.scope is Scope.FINDING
    assert cap.on_exhaustion is Disposition.ESCALATE


def test_nothing_counted_repair_attempts_before_this_story() -> None:
    """`Session.run` records a step only where a phase's cap counts steps, and
    this one counts *attempts* — so the cap has had no counter since S-5.4. Third
    of these in Epic 10 after `FINDING_AUDIT` and `TEST_AUDIT`."""
    budget = a_budget()
    assert budget.used(Phase.REPAIR, FINDING) == 0

    record_attempt(budget, an_attempt(), FINDING)
    assert budget.used(Phase.REPAIR, FINDING) == 1


def test_a_fourth_attempt_is_refused_before_it_spends_anything() -> None:
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.REPAIR].limit):
        authorize_attempt(budget, FINDING)
        record_attempt(budget, an_attempt(failure=f"failure {index}"), FINDING)

    with pytest.raises(BudgetExhaustedError) as raised:
        authorize_attempt(budget, FINDING)
    assert raised.value.exhaustion.disposition is Disposition.ESCALATE


def test_asking_whether_the_attempts_are_spent_does_not_spend_one() -> None:
    """A caller deciding *try again or escalate* is asking, not acting."""
    budget = a_budget()

    assert not exhausted(budget, FINDING)
    assert budget.used(Phase.REPAIR, FINDING) == 0

    for index in range(PHASE_CAPS[Phase.REPAIR].limit):
        record_attempt(budget, an_attempt(failure=f"failure {index}"), FINDING)
    assert exhausted(budget, FINDING)


def test_attempts_are_counted_per_finding() -> None:
    budget = a_budget()
    for index in range(PHASE_CAPS[Phase.REPAIR].limit):
        record_attempt(budget, an_attempt(failure=f"failure {index}"), "other-finding")

    authorize_attempt(budget, FINDING)
    assert budget.remaining(Phase.REPAIR, FINDING) == 3


def test_the_failure_is_the_stall_conclusion_not_the_approach() -> None:
    """S-5.4's stall check is explicitly *not* a self-judged criterion, and the
    approach label is exactly that. Three attempts failing the same way is a
    phase repeating itself; three attempts *named* differently is not evidence of
    anything."""
    budget = Budget(ledger=Ledger(), rate=RATE, stall_after=2)
    same = "the cost claim still failed"

    record_attempt(budget, an_attempt(a_patch(approach="one"), failure=same), FINDING)
    with pytest.raises(ProgressStalledError):
        record_attempt(budget, an_attempt(a_patch(approach="two"), failure=same), FINDING)


def test_different_failures_do_not_stall() -> None:
    """The control. A conclusion that never repeats makes the stall unreachable,
    which is S-8.8's recorded finding."""
    budget = Budget(ledger=Ledger(), rate=RATE, stall_after=2)

    record_attempt(budget, an_attempt(failure="the cost claim failed"), FINDING)
    record_attempt(budget, an_attempt(failure="the guard on rows regressed"), FINDING)
    assert budget.used(Phase.REPAIR, FINDING) == 2


# ============ AC 2: the structural check, which rejects too little or too much


def test_an_identical_diff_under_a_new_name_is_refused() -> None:
    """**F12, exactly.** The agent writes its own approach label and can rename
    the same idea; the diffs are compared instead."""
    first = an_attempt(a_patch(approach="prefetch the authors"))
    renamed = a_patch(approach="batch-load the author relation")

    with pytest.raises(RepeatedAttemptError, match="repeats attempt 1"):
        check_attempt(renamed, [first])


def test_a_diff_differing_only_in_whitespace_is_refused() -> None:
    """Reindenting is not a second attempt."""
    first = an_attempt(a_patch())
    respaced = a_patch(added="        return self._authors[ obj.author_id ]   ")

    # The added line differs, so this one legitimately gets through — the control
    # for the case below.
    assert repeats(respaced, [first]) is None

    padded = Patch(
        diff=a_diff().replace(
            "+        return self._authors[obj.author_id]",
            "+            return self._authors[obj.author_id]   ",
        ),
        approach="same thing, indented differently",
        rationale="r",
    )
    assert repeats(padded, [first]) is not None


def test_a_diff_differing_only_in_comments_is_refused() -> None:
    """A diff that differs only in what it says *about itself* is the textual
    form of renaming an approach."""
    first = an_attempt(a_patch())
    commented = Patch(
        diff=a_diff().replace(
            "+        return self._authors[obj.author_id]",
            "+        return self._authors[obj.author_id]  # prefetched above",
        ),
        approach="annotated",
        rationale="r",
    )

    with pytest.raises(RepeatedAttemptError):
        check_attempt(commented, [first])


def test_a_different_edit_at_the_same_lines_is_allowed() -> None:
    """**The refusal that would break honest retries.** Rejecting on *same lines*
    alone refuses the second genuine idea at the same site — and the site is
    where the second idea usually is, because that is where the cost was
    measured."""
    first = an_attempt(a_patch(approach="index the authors in memory"))
    different = a_patch(
        approach="select_related on the queryset",
        added="        return obj.author.name",
    )

    assert shared_lines(first.patch.diff, different.diff)
    assert repeats(different, [first]) is None
    check_attempt(different, [first])


def test_the_overlap_is_measured_on_the_original_side() -> None:
    """**Two attempts that both rewrite lines 41-42 are working on the same
    code**, however far apart the results land — an earlier hunk that grew or
    shrank moves everything after it on the new side.

    So these two share their *original* lines and share none of their new ones,
    and they are a repeat. Reading the new side would call them different.
    """
    first = an_attempt(a_patch(start=41, new_start=41))
    shifted = a_patch(approach="same change, later in the file", start=41, new_start=900)

    assert shared_lines(first.patch.diff, shifted.diff)
    assert repeats(shifted, [first]) is not None


def test_the_same_edit_at_different_lines_is_allowed() -> None:
    """A different target is a different attempt. The same change applied
    somewhere else is not a repeat of where it was applied before."""
    first = an_attempt(a_patch(start=41))
    elsewhere = a_patch(start=200)

    assert normalized_edit(first.patch.diff) == normalized_edit(elsewhere.diff)
    assert not shared_lines(first.patch.diff, elsewhere.diff)
    assert repeats(elsewhere, [first]) is None


def test_the_same_edit_in_a_different_file_is_allowed() -> None:
    first = an_attempt(a_patch(path=SITE))
    other_file = a_patch(path=OTHER)

    assert repeats(other_file, [first]) is None


def test_a_repeat_of_any_earlier_attempt_is_caught_not_only_the_last() -> None:
    """Three attempts, and the third repeating the first is the shape a
    last-only comparison misses."""
    first = an_attempt(a_patch(approach="one"))
    second = an_attempt(a_patch(approach="two", added="        return obj.author.name"))
    third = a_patch(approach="three")

    with pytest.raises(RepeatedAttemptError, match="repeats attempt 1"):
        check_attempt(third, [first, second])


def test_the_refusal_names_the_attempt_and_the_files() -> None:
    """A human has to be able to check the claim, rather than being told a
    similarity score crossed a line."""
    first = an_attempt(a_patch())

    with pytest.raises(RepeatedAttemptError) as raised:
        check_attempt(a_patch(approach="renamed"), [first])

    assert "attempt 1" in str(raised.value)
    assert SITE in str(raised.value)
    assert "self-judged" in str(raised.value)


def test_the_first_attempt_repeats_nothing() -> None:
    check_attempt(a_patch(), [])
    assert repeats(a_patch(), []) is None


def test_two_no_op_diffs_at_the_same_lines_are_a_repeat() -> None:
    """**A guard was deleted here.** It refused to call two content-free diffs
    repeats — but a diff whose only change is a comment *is* a no-op patch, and a
    second one at the same lines is the same no-op again. The guard's only
    reachable effect was to call that pair different, and S-3.12's rule is that a
    guard no test reaches is a guard nobody has checked."""
    comment_only = Patch(
        diff=a_diff(removed="  # old note", added="  # new note"),
        approach="tidy the comment",
        rationale="r",
    )
    again = Patch(
        diff=a_diff(removed="  # older note", added="  # newer note"),
        approach="tidy it differently",
        rationale="r",
    )

    assert normalized_edit(comment_only.diff) == frozenset()
    assert repeats(again, [an_attempt(comment_only)]) is not None


def test_a_diff_with_no_hunks_at_all_repeats_nothing() -> None:
    """S-10.4 already refuses a patch that touches no file; this simply has no
    lines in common with anything, so it cannot overlap."""
    empty = Patch(diff="no hunks here", approach="nothing", rationale="r")

    assert repeats(empty, [an_attempt()]) is None


def test_the_same_edit_under_different_context_is_still_a_repeat() -> None:
    """Context lines are the file, not the edit. Two runs of `git diff` with
    different `-U` settings show different surrounding code around an identical
    change, and treating that as a new attempt would let the same patch through
    by regenerating the diff."""
    first = an_attempt(a_patch())
    wider = a_patch(
        approach="same change, more context shown",
        context="class BookSerializer(serializers.ModelSerializer):",
    )

    assert normalized_edit(first.patch.diff) == normalized_edit(wider.diff)
    assert repeats(wider, [first]) is not None


def test_there_is_no_similarity_threshold() -> None:
    """**S-9.4's rule.** A threshold is derived or it does not belong, and there
    is no measured quantity here to derive one from — no noise floor, no class
    gap. So *similar edit shape* is an equivalence that can be decided rather
    than a score compared against a number nobody chose.

    Asserted over what the module **holds and imports**, never over its source
    text — the first draft of this test read the source and failed against its
    own docstring, which uses the word *similarity* to explain why there is no
    similarity score. That is the fifth time in this project; the rule is that an
    isolation or absence test reads structure, not prose.
    """
    imported = set(vars(retry_module))
    assert not imported & {"difflib", "SequenceMatcher", "get_close_matches"}

    numbers = {
        name: value
        for name, value in vars(retry_module).items()
        if isinstance(value, float) and not name.startswith("_")
    }
    assert numbers == {
        "RETRY_TEMPERATURE": RETRY_TEMPERATURE,
        "SURGEON_TEMPERATURE": SURGEON_TEMPERATURE,
    }


def test_the_check_needs_no_runner_so_it_cannot_come_after_the_gates() -> None:
    """AC 2 says *rejected before running gates*. Nothing in this module executes
    anything — no runner, no session, no worktree — so a caller cannot have spent
    a test run before reaching it. The ordering is a property of what the
    function needs."""
    imported = set(vars(retry_module))
    assert not imported & {"CandidateSession", "DiagnosticSession", "Sandbox", "execute"}

    parameters = {
        name
        for _, function in inspect.getmembers(retry_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"session", "runner", "worktree", "client"}


# ==================== AC 3: failure reasons carried in context


def test_an_attempt_carries_why_it_failed() -> None:
    attempt = an_attempt(failure="the guard on rows regressed to 4000")

    assert "the guard on rows regressed to 4000" in attempt.describe()
    assert "prefetch the authors" in attempt.describe()


def test_the_escalation_carries_every_attempt_with_its_reason() -> None:
    attempts = [
        an_attempt(a_patch(approach="one"), failure="the cost claim still failed"),
        an_attempt(a_patch(approach="two"), failure="the guard on rows regressed"),
        an_attempt(a_patch(approach="three"), failure="the diff did not apply"),
    ]
    report = escalate(attempts, FINDING).report()

    for attempt in attempts:
        assert attempt.patch.approach in report
        assert attempt.failure in report
    assert FINDING in report


# ============================ AC 4: temperature on retries


def test_the_first_attempt_and_the_retries_run_at_the_documented_temperatures() -> None:
    """§5.1: *0.2 first attempt, 0.6 on retries* — and its own justification, that
    a retry at 0.2 tends to produce a variation of the same idea, which will fail
    the same way."""
    assert temperature_for(1) == SURGEON_TEMPERATURE == 0.2
    assert temperature_for(2) == RETRY_TEMPERATURE == 0.6
    assert temperature_for(3) == RETRY_TEMPERATURE


def test_the_retry_temperature_is_not_a_ramp() -> None:
    """Two values because §5.1 gives two. A third point on a curve would be a
    number with no argument behind it."""
    assert temperature_for(2) == temperature_for(3)


def test_an_attempt_number_below_one_is_refused() -> None:
    with pytest.raises(RetryError, match="one-based"):
        temperature_for(0)


# ================== AC 5: escalation after three


def test_escalating_with_no_attempts_is_refused() -> None:
    """Nothing was tried, so this is not a repair that ran out of ideas — it is
    one that never started, and a report saying otherwise sends somebody looking
    for three diffs that do not exist."""
    with pytest.raises(RetryError, match="never started"):
        escalate([])


def test_the_escalation_keeps_the_attempts_rather_than_a_count() -> None:
    """§7.2's disposition for this phase is *escalate with the history*, and a
    history that dropped the attempts would be a number."""
    attempts = [an_attempt(a_patch(approach=f"attempt {index}")) for index in range(3)]
    escalation = escalate(attempts)

    assert isinstance(escalation, Escalation)
    assert len(escalation.attempts) == 3
    assert escalation.attempts[0].patch.diff


def test_a_repeat_and_an_exhausted_budget_are_different_exceptions() -> None:
    """S-5.4's argument: they call for opposite actions. A repeat means *think
    again, you still have attempts*; exhaustion means *stop*. A caller catching
    one type would handle the other wrongly."""
    assert not issubclass(RepeatedAttemptError, BudgetExhaustedError)
    assert not issubclass(BudgetExhaustedError, RetryError)

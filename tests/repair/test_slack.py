"""S-10.6 — detecting the class of change this system is built to produce.

`00-BRIEF.md` §4: *our tool produces exactly these optimizations.* A caching fix
reduces steady-state queries, passes every check, and can move a system from
stable to vulnerable.

Two things decide every test here.

**A false negative is the dangerous direction.** An unflagged slack-reducing
patch reaches auto-approval; a wrongly flagged one costs a review. So the
classifier leans toward flagging, and the tests that matter most are the ones
proving it does not miss.

**But the two comparison patterns must not be keyword matches**, or the patch
that *raises* a timeout gets flagged for adding headroom — and a label that fires
on the opposite of its subject is one every reader learns to ignore.
"""

from __future__ import annotations

import inspect

import pytest

from coldfix.bench.stats import Growth
from coldfix.primitives.faults import Amplification, Fault, Response
from coldfix.repair import slack as slack_module
from coldfix.repair.slack import (
    LABEL,
    RESIDUE,
    STAGING_WARNING,
    Classification,
    Removal,
    Slack,
    classify,
    may_auto_approve,
    patterns,
)
from coldfix.sandbox.patching import hunk_lines, touched_paths


def a_diff(*added: str, removed: tuple[str, ...] = (), path: str = "shop/views.py") -> str:
    """A unified diff whose hunk counts are honest, because the parser reads them."""
    body = [f"-{line}" for line in removed] + [f"+{line}" for line in added]
    return "\n".join(
        [
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -1,{len(removed) or 0} +1,{len(added) or 0} @@",
            *body,
            "",
        ]
    )


def an_amplification(*, amplifying: bool) -> Amplification:
    # S-3.16's threshold is a factor of 2.0, so the non-amplifying case has to
    # stay strictly under it — (1, 1, 2) is *exactly* amplifying, which is how
    # this fixture first claimed to be its own opposite.
    calls = (1, 6, 12) if amplifying else (1, 1, 1)
    return Amplification(
        responses=tuple(
            Response(
                fault=Fault.LATENCY,
                magnitude=float(index),
                calls=count,
                metrics={},
                failed=False,
            )
            for index, count in enumerate(calls)
        ),
        growth=Growth.LINEAR if amplifying else None,
        dependency="payments.charge",
    )


# ================= AC 1: the six patterns, on added lines


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("@lru_cache(maxsize=128)", Slack.CACHE),
        ("    from functools import cached_property", Slack.CACHE),
        ("    cache.set(key, value, 300)", Slack.CACHE),
        ("@retry(stop=stop_after_attempt(3))", Slack.RETRY),
        ("    session.mount(adapter_with_backoff)", Slack.RETRY),
        ("CONN_MAX_AGE = 600", Slack.CONNECTION_REUSE),
        ("    self.session = requests.Session()", Slack.CONNECTION_REUSE),
        ("    out = io.BufferedWriter(raw)", Slack.BUFFERING),
    ],
)
def test_each_added_pattern_is_matched(line: str, expected: Slack) -> None:
    """The four keyword patterns, one case each. Parametrised so no single
    pattern is the one that happens to be checked."""
    found = classify(a_diff(line))

    assert found.slack_reducing
    assert expected in found.kinds


def test_a_clean_diff_is_not_flagged() -> None:
    """**The control.** A classifier that flagged everything would satisfy every
    assertion above while making auto-approval unreachable for any patch — which
    would look like safety and is a broken gate."""
    found = classify(a_diff("    books = Book.objects.select_related('author')"))

    assert not found.slack_reducing
    assert found.label is None
    assert may_auto_approve(found)


def test_removing_a_cache_is_not_adding_one() -> None:
    """A diff that *deletes* `@lru_cache` adds slack back. Matching on removed
    lines would flag the patch that undoes the risk."""
    found = classify(a_diff("    return compute(x)", removed=("@lru_cache(maxsize=128)",)))

    assert not found.slack_reducing


def test_a_file_header_is_not_an_added_line() -> None:
    """**The parsing hazard S-2.4 already solved.** `+++ b/shop/views.py` starts
    with a `+`, and a classifier scanning for that prefix reads every header as
    content. Here it would be harmless; the mirror case is not."""
    diff = a_diff("    x = 1", path="shop/retry_helpers.py")

    assert not classify(diff).slack_reducing


def test_a_removed_line_that_looks_like_a_header_is_still_a_removed_line() -> None:
    """The adversarial case `touched_paths` exists for, from the other side: a
    removed line whose content begins `-- a/x` renders as `--- a/x`.

    Both functions must agree about where the hunk is, or one of them is reading
    file content as structure.
    """
    diff = "\n".join(
        [
            "--- a/shop/views.py",
            "+++ b/shop/views.py",
            "@@ -1,2 +1,1 @@",
            "-- a/etc/passwd",
            "-@lru_cache(maxsize=8)",
            "+    return compute(x)",
            "",
        ]
    )

    assert touched_paths(diff) == {"shop/views.py"}
    assert ("-", "- a/etc/passwd") in hunk_lines(diff)
    # The cache is on the removed side, so this patch is not slack-reducing.
    assert not classify(diff).slack_reducing


# ============ the two comparisons, which are not keyword matches


def test_a_reduced_pool_size_is_flagged() -> None:
    found = classify(a_diff("POOL_SIZE = 5", removed=("POOL_SIZE = 20",)))

    assert Slack.POOL_SHRUNK in found.kinds
    assert "POOL_SIZE 20 -> 5" in found.warning()


def test_a_reduced_timeout_is_flagged_as_a_timeout_not_a_pool() -> None:
    found = classify(a_diff("    timeout = 2", removed=("    timeout = 30",)))

    assert found.kinds == (Slack.TIMEOUT_SHRUNK,)


def test_a_raised_timeout_is_not_flagged() -> None:
    """**The test that keeps the label meaningful.** Raising a timeout *adds*
    headroom. An implementation that greps for `timeout` flags this, and a label
    that fires on the opposite of its subject is one every reader learns to
    ignore."""
    found = classify(a_diff("    timeout = 60", removed=("    timeout = 5",)))

    assert not found.slack_reducing
    assert may_auto_approve(found)


def test_a_raised_pool_size_is_not_flagged() -> None:
    assert not classify(
        a_diff("max_connections = 50", removed=("max_connections = 10",))
    ).slack_reducing


def test_an_unchanged_setting_is_not_flagged() -> None:
    """A hunk that reindents a line leaves the value alone; a classifier keying on
    *appears on both sides* would flag every reformatting."""
    found = classify(a_diff("        POOL_SIZE = 20", removed=("    POOL_SIZE = 20",)))

    assert not found.slack_reducing


def test_a_setting_only_added_is_not_a_reduction() -> None:
    """A pool that did not exist before has no before-value to have gone down
    from. Reporting it as a reduction would put a number nobody measured into a
    safety warning."""
    assert not classify(a_diff("POOL_SIZE = 5")).slack_reducing


@pytest.mark.parametrize(
    "name", ["page_size", "MAX_RETRIES_DISPLAYED", "column_width", "discount_percent"]
)
def test_an_ordinary_number_going_down_is_not_a_setting(name: str) -> None:
    """**The survivor of the sabotage pass.** Nothing tested that the vocabulary
    is consulted at all: with it bypassed, *any* numeric assignment that
    decreased became a slack-reducing removal.

    Over-flagging is the safe direction for a missed cache and the fatal one for
    a label — a classifier that fires on every decremented constant is one every
    reviewer learns to skip, which is the same argument as the raised timeout,
    made about volume instead of direction.
    """
    found = classify(a_diff(f"{name} = 5", removed=(f"{name} = 20",)))

    assert not found.slack_reducing
    assert may_auto_approve(found)


def test_settings_are_compared_by_name_not_by_position() -> None:
    """Git puts removed lines before added ones, but a hunk that reorders makes
    position meaningless — and `pool_size 20 -> 5` is a sentence a reviewer can
    check, while *the third minus line* is not."""
    found = classify(
        a_diff(
            "    read_timeout = 30",
            "    POOL_SIZE = 5",
            removed=("    POOL_SIZE = 20", "    read_timeout = 30"),
        )
    )

    assert found.kinds == (Slack.POOL_SHRUNK,)
    assert "POOL_SIZE 20 -> 5" in found.warning()


# ================ AC 2 and AC 3: the label, and what it forbids


def test_a_matched_patch_carries_the_label() -> None:
    found = classify(a_diff("@lru_cache(maxsize=128)"))

    assert found.label == LABEL
    assert LABEL == "slack-reducing"


def test_there_is_no_trust_level_that_clears_the_label() -> None:
    """**AC 3, enforced by absence.** F1: *block auto-approval permanently — no
    trust level can clear it.* A function taking a level is a function somebody
    can pass a high enough one to, and Epic 14's ledger does not exist yet to be
    argued with."""
    parameters = set(inspect.signature(may_auto_approve).parameters)

    assert parameters == {"classification"}
    assert not parameters & {"trust", "level", "trust_level", "force", "override"}


@pytest.mark.parametrize("line", ["@lru_cache()", "@retry()", "CONN_MAX_AGE = 60", "buffer = []"])
def test_no_matched_patch_may_ever_be_auto_approved(line: str) -> None:
    assert not may_auto_approve(classify(a_diff(line)))


def test_a_clean_classification_says_so_without_claiming_safety() -> None:
    """`label` is `None` rather than a second label meaning *checked and clean*.
    This classifier cannot establish that, and a value saying it could would be
    read as one."""
    found = classify(a_diff("    x = 1"))

    assert found.label is None
    assert "not a clean bill of health" in found.describe()
    assert RESIDUE in found.describe()


# ================== AC 4: a warning that names what was removed


def test_the_warning_names_the_specific_line_and_what_it_costs() -> None:
    """F1 asks for *a specific staging warning*. A banner saying *this patch may
    reduce slack* is one nobody reads twice."""
    warning = classify(a_diff("@lru_cache(maxsize=128)")).warning()

    assert "lru_cache(maxsize=128)" in warning
    assert "cold cache under load" in warning
    assert STAGING_WARNING in warning


def test_the_warning_says_this_system_cannot_run_the_check_it_asks_for() -> None:
    """The sentence F1 gives ends at *verify recovery after a load spike*, which
    alone reads as a suggestion. Nobody downstream should assume the check
    happened somewhere else."""
    assert "cannot run that test" in STAGING_WARNING


def test_a_clean_patch_has_no_warning() -> None:
    assert classify(a_diff("    x = 1")).warning() == ""


def test_every_pattern_has_a_headroom_sentence() -> None:
    """The warning is only specific if each pattern can say what it took away."""
    for kind in Slack:
        assert kind.headroom.strip()
    assert len(patterns()) == len(Slack)


# ============ AC 5 and AC 6: the partial rescue, and its bound


def test_an_amplifying_result_is_attached() -> None:
    found = classify(a_diff("@retry()"), amplification=an_amplification(amplifying=True))

    assert found.amplification is not None
    assert "One request became" in found.describe()


def test_a_non_amplifying_result_is_reported_in_the_primitives_own_words() -> None:
    """**AC 6.** S-3.16 already says a subject that did not amplify *has passed
    the common case and nothing more*. Restating it here in softer words is how a
    partial check becomes a claim of safety."""
    found = classify(a_diff("@retry()"), amplification=an_amplification(amplifying=False))

    assert "not proof of safety" in found.describe()


def test_an_unrun_amplification_check_is_unmeasured_rather_than_absent() -> None:
    """S-9.2's construction: `None` means *not checked*, which S-3.1 keeps apart
    from *checked and found nothing*."""
    found = classify(a_diff("@retry()"))

    assert found.amplification is None
    assert "not checked" in found.describe()


def test_nothing_here_claims_the_patch_was_tested_for_metastability() -> None:
    """F1's fourth instruction, verbatim: *do not claim we tested it.*"""
    flagged = classify(a_diff("@lru_cache()"), amplification=an_amplification(amplifying=False))

    assert RESIDUE in flagged.describe()
    assert "static detection, not verification" in flagged.describe()
    assert "has not been shown to be safe" in RESIDUE


def test_this_module_cannot_run_a_spike_test_and_does_not_pretend_to() -> None:
    """`08-audit.md` F1: metastability needs a sustaining feedback loop that a
    single container with one synthetic driver does not have. The spike test is
    not a precondition here because it cannot be run at all."""
    imported = set(vars(slack_module))
    assert not imported & {"drive_load", "measure_load", "fit_usl", "Sandbox", "Workbench"}

    parameters = {
        name
        for _, function in inspect.getmembers(slack_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"session", "subject", "workload", "spike"}


def test_the_amplification_result_is_supplied_not_measured() -> None:
    """This module has no subject, no dependency to degrade and no way to drive
    one — so the result arrives as a parameter, which is S-9.2's construction for
    a missing fit."""
    assert "amplification" in inspect.signature(classify).parameters


# ============================================ the artifact


def test_the_kinds_are_reported_once_each_in_the_order_found() -> None:
    """Two cache lines are two removals and one kind. A reader counting kinds is
    asking *what did this take away*, not *how many lines mention it*."""
    found = classify(a_diff("@lru_cache()", "    other_cache = {}"))

    assert found.kinds == (Slack.CACHE,)
    assert len(found.removals) == 2


def test_one_diff_can_remove_headroom_in_several_ways() -> None:
    found = classify(
        a_diff("@lru_cache()", "@retry()", "    timeout = 1", removed=("    timeout = 30",))
    )

    assert set(found.kinds) == {Slack.CACHE, Slack.RETRY, Slack.TIMEOUT_SHRUNK}
    assert not may_auto_approve(found)


def test_the_six_patterns_are_the_ones_the_audit_lists() -> None:
    """F1's list, transcribed. Deciding one of these is not really slack-reducing
    is a change to the audit, not to this module."""
    assert {item.name.lower() for item in Slack} == {
        "cache",
        "retry",
        "connection_reuse",
        "pool_shrunk",
        "timeout_shrunk",
        "buffering",
    }


def test_a_classification_can_be_built_empty_and_reads_as_clean() -> None:
    empty = Classification(removals=())

    assert not empty.slack_reducing
    assert may_auto_approve(empty)
    assert empty.kinds == ()


def test_a_removal_describes_both_what_and_what_it_costs() -> None:
    removal = Removal(kind=Slack.CACHE, evidence="@lru_cache()")

    assert "@lru_cache()" in removal.describe()
    assert "removes:" in removal.describe()

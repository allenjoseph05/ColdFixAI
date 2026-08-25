"""S-16.2 — the pull request body, and the two things a reviewer cannot check alone.

`adapters.ship` has said since S-12.7 that it does F14 and nothing else, because
*a stub here would be a second, worse answer to a question another epic owns*.
This is that answer.

**Most of the body is not new**, and the tests say so rather than re-asserting
other stories' work: `EvidenceChain.render` is S-16.1 and already carries the
growth table, the site and every exclusion with its preconditions. What is tested
here is what S-16.2 adds — the guards, the suite result, the earlier round, the
regression test — and the three ways a report can flatter a patch.
"""

from __future__ import annotations

import inspect

import pytest

from coldfix.audit.patchverdict import (
    Attack,
    AttackResult,
    Outcome,
    PatchVerdict,
    Reproduction,
    Verdict,
)
from coldfix.orchestrator.gate import Approval
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Patch
from coldfix.report.pullrequest import (
    REGRESSION_TEST_PATH,
    ReportError,
    change,
    deltas,
    pull_request,
)
from fixtures.chains import an_evidence_chain

DIFF = """\
--- a/shop/views.py
+++ b/shop/views.py
@@ -12,3 +12,3 @@
-    books = Book.objects.all()
+    books = Book.objects.select_related("author")
"""

SCRIPT = "def test_the_list_stops_re_querying():\n    assert queries(list_books) <= 3\n"


def a_patch() -> Patch:
    return Patch(
        diff=DIFF,
        approach="select_related on the author",
        rationale="the sweep says one query per book",
    )


def a_falsified(guards: tuple[Guard, ...] = ()) -> Falsified:
    return Falsified(
        test=FalsificationTest(
            claim="the list endpoint stops re-querying the author for every book",
            script=SCRIPT,
            equivalence="the same books in the same order",
            cost=CostClaim(
                metric="seconds",
                baseline=8.24,
                at_most=2.0,
                guards=guards or (Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
            ),
            catches=(Cheat.CACHED_STATE,),
        ),
        evidence="1 failed in 0.4s",
        wall_seconds=0.4,
    )


def a_clean_verdict(*, suite: bool = True) -> PatchVerdict:
    results = [
        AttackResult(attack=attack, outcome=Outcome.PASSED)
        for attack in Attack
        if suite or attack is not Attack.SCOPE
    ]
    return PatchVerdict(verdict=Verdict.CLEAN, results=tuple(results))


def a_pull_request(**overrides: object) -> object:
    fields: dict[str, object] = {
        "finding": "shop.books.list",
        "chain": an_evidence_chain(),
        "patch": a_patch(),
        "falsified": a_falsified(),
        "verdict": a_clean_verdict(),
        "before": {"seconds": 8.24, "rows": 1000.0},
        "after": {"seconds": 1.10, "rows": 1000.0},
    }
    fields.update(overrides)
    return pull_request(**fields)  # type: ignore[arg-type]


# ================================================ AC 1: what the body carries


def test_the_body_carries_the_before_and_after_on_every_axis() -> None:
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "seconds: 8.24 -> 1.1" in body
    assert "87% better" in body
    assert "rows: 1000 -> 1000" in body
    assert "unchanged" in body


def test_the_body_carries_the_evidence_chain() -> None:
    """S-16.1's rendering, embedded rather than re-implemented. The exclusion's
    preconditions travelling with it is the property that matters — *not the
    database, queries flat* in a pull request with no mention of the fixture shape
    is F3's false fact with a reviewer's signature under it."""
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "SYMPTOM" in body
    assert "COMPLEXITY" in body, "the growth table"
    assert "RULED OUT" in body
    assert "under fixture shape uniform" in body


def test_the_body_carries_the_adversary_verdict_and_every_attack() -> None:
    """A reader weighing a patch needs to see that the other four passed, not only
    the headline."""
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "clean" in body
    for attack in Attack:
        assert attack.value in body


# ================================================ the guards, and three ways to get them wrong


def test_the_guards_come_from_the_test_that_declared_them() -> None:
    """**Never a parameter.** `CostClaim.guards` is what S-10.1 required non-empty,
    because *a cost claim with no guard is a test a cheat passes by moving one
    number*. A body taking its own guard list could show a reviewer guards the
    test never checked."""
    parameters = set(inspect.signature(pull_request).parameters)

    assert not any("guard" in name for name in parameters)
    assert "falsified" in parameters


def test_a_guard_that_held_is_shown_with_the_limit_it_held_under() -> None:
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "rows: 1000 -> 1000 (limit 1000) — held" in body


def test_a_guard_that_regressed_is_called_out() -> None:
    """`CLAUDE.md`: queries down while rows explode is not an improvement. The
    report says so in those words rather than leaving a reader to compare two
    numbers in a table."""
    report = a_pull_request(after={"seconds": 1.10, "rows": 5000.0})

    assert report.regressed_guards  # type: ignore[attr-defined]
    body = report.body()  # type: ignore[attr-defined]
    assert "**REGRESSED**" in body
    assert "queries down while rows explode is not an improvement" in body


def test_a_guard_nobody_measured_is_unverified_rather_than_satisfied() -> None:
    """**The sharpest of the three.** A guard whose metric was not measured after
    the patch has not been checked, and rendering it as held would put the most
    flattering available answer under a reviewer's signature — which is the
    failure the guard-counter rule exists to prevent, reached through the
    report."""
    report = a_pull_request(after={"seconds": 1.10})

    assert report.unverified_guards  # type: ignore[attr-defined]
    assert not report.regressed_guards  # type: ignore[attr-defined]
    body = report.body()  # type: ignore[attr-defined]
    assert "NOT MEASURED" in body
    assert "Unverified, not satisfied" in body


def test_an_unmeasured_guard_did_not_hold() -> None:
    """**Found by sabotage: the first version of this file never asserted it.**

    Making `held` answer `True` for a guard nobody measured changed no test
    outcome, because the tests above read `unverified_guards` and the rendered
    text — neither of which goes through `held`. But `held` is the property a
    caller branches on, so `all(item.held for item in guards)` would have come
    back green on a patch whose guards were never checked. Absence is not
    satisfaction anywhere else in this system and it is not here either.
    """
    (unmeasured,) = a_pull_request(after={"seconds": 1.10}).guards  # type: ignore[attr-defined]

    assert not unmeasured.checked
    assert not unmeasured.held, "not checked is not the same as passed"


# ================================================ the suite, and the earlier round


def test_the_body_carries_the_suite_result() -> None:
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "Test results" in body
    assert Attack.SCOPE.value in body


def test_a_suite_that_did_not_run_says_so_rather_than_reading_as_green() -> None:
    """An absent section reads as *nothing to report*, which for a test suite is
    the most dangerous possible reading."""
    body = a_pull_request(verdict=a_clean_verdict(suite=False)).body()  # type: ignore[attr-defined]

    assert "the suite was not run as part of this audit" in body


def test_an_earlier_rounds_reproduction_travels_with_the_patch_that_survived() -> None:
    """S-11.7 sends a broken patch back with something that can be run. A reviewer
    who sees what the first attempt got caught by is better placed than one who
    sees only the version that passed."""
    caught = Reproduction(
        attack=Attack.EQUIVALENCE,
        shows="the second page came back in a different order",
        how="pytest tests/shop/test_pagination.py::test_order",
    )
    body = a_pull_request(earlier_rounds=[caught]).body()  # type: ignore[attr-defined]

    assert "What an earlier round got caught by" in body
    assert "different order" in body
    assert "test_pagination" in body


def test_a_first_time_patch_carries_no_empty_heading() -> None:
    """Most patches pass first time, and a heading with nothing under it reads as
    a section somebody forgot to fill in."""
    assert "earlier round" not in a_pull_request().body()  # type: ignore[attr-defined]


# ================================================ AC 2: the regression test


def test_the_falsification_test_is_attached_as_a_permanent_regression_test() -> None:
    """**AC 2**, with the evidence it failed unpatched — which is what makes it a
    regression test rather than a test. S-10.1's whole construction is that one
    passing before the patch proves nothing."""
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert SCRIPT.strip().splitlines()[0] in body
    assert REGRESSION_TEST_PATH in body
    assert "test_shop_books_list.py" in body, "named for the finding"
    assert "It failed on the unpatched revision: 1 failed in 0.4s" in body


# ================================================ AC 3: the slack warning


def test_a_slack_reducing_patch_carries_the_warning_first() -> None:
    """`00-BRIEF.md` §4 says *prominently*, and a label under four screens of diff
    is not prominent."""
    report = a_pull_request(slack_reducing=True)
    body = report.body()  # type: ignore[attr-defined]

    assert "SLACK" in body.splitlines()[0].upper()
    assert "any trust level" in body
    assert body.index("SLACK-REDUCING".title().upper()[:5]) < body.index("## Before and after")


def test_the_title_says_so_too_because_a_list_of_pull_requests_is_all_titles() -> None:
    assert a_pull_request(slack_reducing=True).title().startswith("[")  # type: ignore[attr-defined]
    assert not a_pull_request().title().startswith("[")  # type: ignore[attr-defined]


def test_an_ordinary_patch_carries_no_warning() -> None:
    """The control. A warning on every patch is a warning nobody reads."""
    body = a_pull_request().body()  # type: ignore[attr-defined]

    assert "SLACK" not in body.upper()


# ================================================ what a pull request refuses to be


@pytest.mark.parametrize("verdict", [Verdict.BROKEN, Verdict.SUSPICIOUS])
def test_a_patch_the_audit_did_not_clear_gets_no_pull_request(verdict: Verdict) -> None:
    """S-11.7 routes anything but `clean` back to the Surgeon or to a human. A body
    assembled from one would be asking for a merge the audit declined to
    recommend, with the objection printed at the top of it."""
    reproduction = Reproduction(attack=Attack.EQUIVALENCE, shows="wrong order", how="pytest x")
    refused = PatchVerdict(
        verdict=verdict,
        reproduction=reproduction if verdict is Verdict.BROKEN else None,
        concern="" if verdict is Verdict.BROKEN else "the test only checks one page",
        results=(
            AttackResult(
                attack=Attack.EQUIVALENCE,
                outcome=Outcome.BROKE_IT if verdict is Verdict.BROKEN else Outcome.SUSPECT,
                detail="the second page came back in a different order",
                reproduction=reproduction if verdict is Verdict.BROKEN else None,
            ),
        ),
    )

    with pytest.raises(ReportError, match="declined to recommend"):
        a_pull_request(verdict=refused)


# ================================================ the table has one owner


def test_the_ship_gate_and_the_pull_request_render_one_table() -> None:
    """Two renderings of the same numbers is how a gate report and a pull request
    come to disagree about what improved.

    Asserted on the output rather than on the source: what matters is that a
    reviewer and the person at the gate read the same rows, not which function
    produced them.
    """
    before = {"seconds": 8.24, "rows": 1000.0}
    after = {"seconds": 1.10, "rows": 1000.0}
    approval = Approval(
        finding="shop.books.list",
        chain=an_evidence_chain(),
        patch=a_patch(),
        verdict="clean",
        before=before,
        after=after,
        slack_reducing=False,
    )
    rendered = approval.render()

    for row in deltas(before, after):
        assert row in rendered


def test_a_metric_measured_on_one_side_only_is_never_a_delta() -> None:
    """A metric present before and absent after is a measurement nobody took, not
    an improvement to zero."""
    rendered = "\n".join(deltas({"seconds": 8.0, "rows": 10.0}, {"seconds": 1.0}))

    assert "rows: measured on only one side, so no delta" in rendered


def test_a_change_against_a_zero_baseline_is_named_rather_than_divided() -> None:
    assert change(0.0, 5.0) == "was zero, so no ratio"
    assert change(8.0, 8.0) == "unchanged"
    assert change(8.0, 1.0).endswith("better")
    assert change(1.0, 8.0).endswith("worse")

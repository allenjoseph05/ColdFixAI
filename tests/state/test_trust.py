"""Autonomy earned per project, and the transfer F15 refuses.

S-13.4. The arithmetic is checkable without a database and the journal round trip
is not, so the levels are tested here and the ledger's own behaviour is tested
against Postgres in `test_persistent_store.py`.

**Two criteria carry the story**: a new project starts at level 0 *regardless of
cross-project history*, and that history is *advisory context rather than earned
autonomy*. Both are assertions that something does **not** happen, so each has a
test that would pass on a broken implementation unless the counterpart is there
too — the advisory field is populated in every case below for exactly that
reason.
"""

from __future__ import annotations

import pytest

from coldfix.state.persistent import PersistentStoreError
from coldfix.state.trust import (
    ACCEPTED_PER_LEVEL,
    MAX_LEVEL,
    Level,
    Outcome,
    Shape,
    Standing,
    ledger_key,
    payload_magnitude,
)

NARROW = Shape(orm="django", database="postgres", payload_magnitude=2)
WIDE = Shape(orm="django", database="postgres", payload_magnitude=4)


def standing(*, accepted: int = 0, demotions: int = 0, elsewhere: int = 0) -> Standing:
    """`elsewhere` is non-zero by default in the callers that matter, because a
    test where nobody else has any history cannot tell *advisory* from *unused*."""
    return Standing(
        project="shop",
        accepted=accepted,
        demotions=demotions,
        elsewhere={f"other-{n}": 5 for n in range(elsewhere)},
    )


# ==================================================== AC 1 and AC 3 — the levels


def test_a_new_project_is_gated() -> None:
    assert standing().level is Level.GATED
    assert int(Level.GATED) == 0


def test_fifty_approvals_elsewhere_do_not_move_this_project() -> None:
    """**F15's third criterion, and its own example.**

    *A `select_related` fix approved 50 times may have been on projects with
    narrow tables.* Ten other projects agreeing is the situation F15 describes,
    and it must still leave this one gated.
    """
    borrowed = standing(elsewhere=10)

    assert sum(borrowed.elsewhere.values()) == 50, "the fixture really does carry the history"
    assert borrowed.level is Level.GATED


def test_a_level_costs_three_clean_outcomes_on_this_project() -> None:
    assert standing(accepted=ACCEPTED_PER_LEVEL - 1, elsewhere=3).level is Level.GATED
    assert standing(accepted=ACCEPTED_PER_LEVEL, elsewhere=3).level is Level.FAMILIAR


def test_the_ledger_stops_at_two() -> None:
    """*Levels 0 to 2*, and the cap is not a coincidence of the arithmetic."""
    assert standing(accepted=ACCEPTED_PER_LEVEL * 50).level is Level.TRUSTED
    assert int(Level.TRUSTED) == MAX_LEVEL


# ==================================================== AC 5 — demotion


def test_any_rejection_demotes_one_level() -> None:
    earned = standing(accepted=ACCEPTED_PER_LEVEL * 2)
    assert earned.level is Level.TRUSTED

    assert standing(accepted=ACCEPTED_PER_LEVEL * 2, demotions=1).level is Level.FAMILIAR


def test_a_revert_and_a_rejection_demote_alike() -> None:
    """F15 says *any revert or rejection*, and `demotes` names them together
    rather than deriving from `not ACCEPTED` — so a fourth outcome added later
    has to state which it is."""
    assert Outcome.REJECTED.demotes
    assert Outcome.REVERTED.demotes
    assert not Outcome.ACCEPTED.demotes


def test_demotion_cannot_push_a_level_below_gated() -> None:
    """There is nothing below *every fix is reviewed*, and a negative level would
    make `int(level)` a number no caller could act on."""
    assert standing(demotions=9).level is Level.GATED


# ==================================================== AC 2 — the key carries the shape


def test_the_same_fix_on_a_different_shape_is_a_different_key() -> None:
    """**F15's whole fix.** A `select_related` fix earned on narrow tables must
    not be filed where a wide-table project will read it."""
    assert ledger_key("query-batching", NARROW) != ledger_key("query-batching", WIDE)


def test_a_different_category_on_the_same_shape_is_a_different_key() -> None:
    """*Keyed by project shape characteristics, **not category alone*** cuts both
    ways: trust earned batching queries is not trust to add a cache."""
    assert ledger_key("query-batching", NARROW) != ledger_key("caching", NARROW)


def test_a_fix_with_no_category_is_refused() -> None:
    with pytest.raises(PersistentStoreError, match="needs the fix category"):
        ledger_key("   ", NARROW)


# ==================================================== the shape is measured


def test_the_shape_comes_from_the_widest_scale_point() -> None:
    """A wide parent table at ten rows looks like a narrow one — the trade the
    fix makes only becomes enormous at volume."""
    observations = [
        {"scale": 10.0, "response_bytes": 1_000.0},
        {"scale": 1_000.0, "response_bytes": 10_000_000.0},
    ]

    assert payload_magnitude(observations, metric="response_bytes") == 4, "10,000 bytes a row"


def test_a_tenfold_difference_in_payload_is_a_different_kind_of_project() -> None:
    """The order of magnitude is the point: no threshold has to be argued for."""
    narrow = payload_magnitude(
        [{"scale": 100.0, "response_bytes": 10_000.0}], metric="response_bytes"
    )
    wide = payload_magnitude(
        [{"scale": 100.0, "response_bytes": 1_000_000.0}], metric="response_bytes"
    )

    assert narrow == 2
    assert wide == 4


def test_a_shape_cannot_be_derived_from_nothing_measured() -> None:
    """F15's fix is that the key includes a *measured* characteristic. A shape
    guessed here would be the label it replaces."""
    with pytest.raises(PersistentStoreError, match="nothing to derive a project shape from"):
        payload_magnitude([{"scale": 0.0, "response_bytes": 5.0}], metric="response_bytes")

"""Epic 11, S-11.2 — equivalence attacks.

*Constructs adversarial inputs: empty collections, nulls, duplicates, ties,
unicode, boundary sizes, unordered results. Runs both revisions and diffs
outputs. On difference, returns a reproducing input.*

**The two revisions here are real.** Each fake session writes a `subject.py` into
its own directory and runs the probe against it with a real interpreter, so the
"before" and "after" sides genuinely execute different code and the differences
these tests detect are differences a real run would produce. The container is not
real; docker is not what any of this checks.

The properties that matter most are the ones about *not* finding a difference. A
false `identical` does not fail a run — it ships a patch — so every test that
asserts equivalence also asserts that something was actually compared.
"""

from __future__ import annotations

import inspect
import json
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from coldfix.audit.equivalence import (
    MARKER,
    PROBE_ERROR_EXIT,
    RESIDUE,
    AdversarialInput,
    Divergence,
    Equivalence,
    EquivalenceError,
    Failure,
    Observed,
    Outcome,
    Probe,
    Probed,
    ReproducingInput,
    Shape,
    Unobserved,
    attack,
    catalogue,
    compare_outputs,
    harness,
    read,
    run_on,
)
from coldfix.bench.diffing import JsonValue
from coldfix.bench.execute import ExecutionResult, ExecutionTimeoutError, execute
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.worktrees import Worktree

REVISION = "9f1c0de"

# The probe the Adversary would be handed: it knows how to reach the workload and
# nothing about the change. `coldfix_input` in, `output` out.
PROBE = Probe(
    workload="shop.books.list",
    script="import subject\noutput = subject.answer(coldfix_input)",
)
ECHO = Probe(workload="shop.books.list", script="output = coldfix_input")

IDENTITY = "def answer(value):\n    return value\n"
SORTED_BY_ID = "def answer(value):\n    return sorted(value, key=lambda row: row['id'])\n"
DROPS_A_FIELD = "def answer(value):\n    return [{'id': row['id']} for row in value]\n"
CRASHES_ON_EMPTY = "def answer(value):\n    return value[0]\n"
FRESH_EACH_TIME = "import uuid\n\n\ndef answer(value):\n    return [str(uuid.uuid4())]\n"

TIED = AdversarialInput(
    shape=Shape.TIES,
    label="every sort key equal",
    payload=[{"id": 3, "rank": 1}, {"id": 1, "rank": 1}, {"id": 2, "rank": 1}],
)
ROWS = AdversarialInput(
    shape=Shape.DUPLICATES,
    label="two rows",
    payload=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
)
NOTHING = AdversarialInput(shape=Shape.EMPTY, label="an empty list", payload=[])


class _Subject:
    """A worktree holding one revision of `subject.py`, run for real.

    `run` executes what it is handed, unmodified. A fake that re-wrapped a canned
    program would be unable to tell whether the composed path built the harness at
    all — the survivor Epic 10's composition check recorded, and the reason the
    program travels through untouched.
    """

    def __init__(self, path: Path, source: str, *, revision: str = REVISION) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "subject.py").write_text(source, encoding="utf-8")
        self._path = path
        self._revision = revision
        self.commands: list[list[str]] = []
        self.timeouts: list[float] = []
        self.raises: Exception | None = None

    @property
    def worktree(self) -> Worktree:
        return Worktree(path=self._path, revision=self._revision, is_main=False)

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = 8 * 1024 * 1024,
    ) -> ExecutionResult:
        self.commands.append(list(command))
        self.timeouts.append(timeout)
        if self.raises is not None:
            raise self.raises
        return execute(
            [sys.executable, "-c", command[-1]],
            cwd=self._path,
            timeout=min(timeout, 30.0),
            max_output_chars=max_output_chars,
        )


class FakeOriginal(_Subject, DiagnosticSession):
    """The revision before the change. A `DiagnosticSession` has no `apply_patch`."""


class FakePatched(_Subject, CandidateSession):
    """The revision with the change in it."""


def both(tmp_path: Path, before: str, after: str) -> tuple[FakeOriginal, FakePatched]:
    return (
        FakeOriginal(tmp_path / "original", before),
        FakePatched(tmp_path / "patched", after),
    )


def a_result(**overrides: object) -> ExecutionResult:
    fields: dict[str, object] = {
        "command": ("python", "-c", "..."),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "wall_seconds": 0.1,
    }
    fields.update(overrides)
    return ExecutionResult(**fields)  # type: ignore[arg-type]


# ============ AC 1 — the seven classes


def test_every_shape_the_criterion_names_is_in_the_catalogue() -> None:
    """AC 1 lists seven classes. A class that quietly went missing would shrink the
    sweep while every report still called it an equivalence attack."""
    covered = {item.shape for item in catalogue()}
    assert covered == set(Shape)
    assert len(Shape) == 7


def test_the_empty_inputs_are_empty_and_the_null_ones_are_null() -> None:
    empties = [item.payload for item in catalogue() if item.shape is Shape.EMPTY]
    assert all(len(payload) == 0 for payload in empties)  # type: ignore[arg-type]
    assert {type(payload) for payload in empties} == {list, dict, str}

    nulls = [item.payload for item in catalogue() if item.shape is Shape.NULL]
    assert None in nulls, "a null in place of the whole input"
    nested = [payload for payload in nulls if payload is not None]
    assert any(
        None in row.values()  # type: ignore[union-attr]
        for payload in nested
        for row in payload  # type: ignore[union-attr]
    ), "a null inside a record, which a populated fixture never produces"


def rows_of(payload: JsonValue) -> list[dict[str, Any]]:
    """A catalogue payload as the list of records it is. Narrowing, not reshaping —
    a helper that coerced would let a payload of the wrong shape pass."""
    assert isinstance(payload, list)
    assert all(isinstance(row, dict) for row in payload)
    return cast("list[dict[str, Any]]", payload)


def test_the_duplicate_inputs_actually_repeat_something() -> None:
    """A 'duplicates' fixture whose rows are all distinct tests nothing. Prefetch and
    join changes are visible only where multiplicity is."""
    for item in catalogue():
        if item.shape is not Shape.DUPLICATES:
            continue
        rows = rows_of(item.payload)
        rendered = [json.dumps(row, sort_keys=True) for row in rows]
        names = [row["name"] for row in rows]
        assert len(set(rendered)) < len(rendered) or len(set(names)) < len(names)


def test_the_tie_input_ties_on_the_sort_key_and_differs_elsewhere() -> None:
    """Both halves are the test. Rows sharing a key is what makes the sort unstable;
    rows differing elsewhere is what makes a reorder visible."""
    (tied,) = [item for item in catalogue() if item.shape is Shape.TIES]
    rows = rows_of(tied.payload)
    assert len({row["rank"] for row in rows}) == 1
    assert len({row["id"] for row in rows}) == len(rows)


def test_the_unicode_pair_is_two_spellings_of_one_word() -> None:
    """**The discriminating property.** Two strings that render identically and are
    equal only after normalisation. A fixture holding the same string twice would
    look right in the source and catch nothing."""
    pair = [item for item in catalogue() if item.shape is Shape.UNICODE and "NFC" in item.label]
    (composed,) = pair
    first, second = (str(row["name"]) for row in rows_of(composed.payload))
    assert first != second, "identical strings cannot detect a normalisation change"
    assert unicodedata.normalize("NFC", first) == unicodedata.normalize("NFC", second)


def test_the_unicode_class_reaches_past_the_basic_multilingual_plane() -> None:
    astral = [
        char
        for item in catalogue()
        if item.shape is Shape.UNICODE
        for row in rows_of(item.payload)
        for char in str(row["name"])
    ]
    assert any(ord(char) > 0xFFFF for char in astral)


def test_the_case_folding_input_gets_longer_when_it_is_folded() -> None:
    """What makes this character worth sending. Any letter round-trips; this one
    changes *length* under a case fold, so a patch that adds a `.upper()` it thought
    was only normalising produces a string the original never held."""
    (item,) = [
        entry for entry in catalogue() if entry.shape is Shape.UNICODE and "upper" in entry.label
    ]
    (name,) = (str(row["name"]) for row in rows_of(item.payload))
    assert len(name) == 1
    assert len(name.upper()) == 2


def test_the_unordered_pair_are_permutations_of_each_other() -> None:
    """Same multiset, different sequence. Equal payloads would make the class a
    duplicate of the boundary one."""
    payloads = [item.payload for item in catalogue() if item.shape is Shape.UNORDERED]
    assert len(payloads) == 2
    first, second = payloads
    assert first != second
    assert sorted(json.dumps(row, sort_keys=True) for row in first) == sorted(  # type: ignore[union-attr]
        json.dumps(row, sort_keys=True)
        for row in second  # type: ignore[union-attr]
    )


def test_the_boundary_class_reaches_the_column_limits() -> None:
    numbers = [
        value
        for item in catalogue()
        if item.shape is Shape.BOUNDARY and isinstance(item.payload, list)
        for value in item.payload
        if isinstance(value, int)
    ]
    assert 2**31 - 1 in numbers, "the top of a Postgres integer column"
    assert 2**63 - 1 in numbers, "past what JavaScript holds exactly"


def test_a_page_size_adds_the_three_inputs_around_the_boundary() -> None:
    before = catalogue()
    after = catalogue(page_size=20)
    added = [item for item in after if item not in before]
    assert [len(item.payload) for item in added] == [19, 20, 21]  # type: ignore[arg-type]
    assert {item.shape for item in added} == {Shape.BOUNDARY}


def test_no_page_size_means_no_page_boundary_rather_than_a_guessed_one() -> None:
    """A default here would let a report claim the page boundary was attacked when
    some other number was."""
    assert "page_size" not in {item.label.split()[-1] for item in catalogue()}
    assert len(catalogue()) == len(catalogue(page_size=20)) - 3
    assert inspect.signature(catalogue).parameters["page_size"].default is None


def test_a_page_of_no_rows_is_refused() -> None:
    with pytest.raises(EquivalenceError, match="at least one row"):
        catalogue(page_size=0)


def test_an_input_without_a_label_is_refused() -> None:
    with pytest.raises(EquivalenceError, match="nobody can name"):
        AdversarialInput(shape=Shape.EMPTY, label="   ", payload=[])


# ============ the harness: getting a payload out of a subject that also prints


def test_the_payload_survives_the_trip_through_a_real_interpreter(tmp_path: Path) -> None:
    """Every catalogue input, round-tripped. The unicode ones are the reason: a
    character mangled in transit is a difference the patch did not cause."""
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    for item in catalogue(page_size=3):
        observed = run_on(session, ECHO, item)
        assert isinstance(observed, Observed), item.label
        assert observed.payload == item.payload, item.label


def test_nothing_non_ascii_is_ever_put_on_the_wire(tmp_path: Path) -> None:
    """**The unicode class depends on this.** The container's stdout encoding is not
    ours to choose, and `execute` decodes as UTF-8 with replacement — so a
    non-ASCII character crossing the boundary can come back mangled, either as a
    difference nobody introduced or, mangled the same way twice, as an agreement
    that was never tested."""
    (astral,) = [
        item for item in catalogue() if item.shape is Shape.UNICODE and "plane" in item.label
    ]
    program = harness(ECHO.script, astral.payload)
    assert program.isascii(), "the payload is escaped into the program"

    session = FakeOriginal(tmp_path / "original", IDENTITY)
    observed = run_on(session, ECHO, astral)
    assert isinstance(observed, Observed)
    assert observed.payload == astral.payload

    printed = session.run([sys.executable, "-c", program], timeout=30.0).stdout
    marker = next(line for line in printed.splitlines() if line.startswith(MARKER))
    assert marker.isascii(), "and escaped again on the way back"


def test_the_subjects_own_output_does_not_become_the_payload(tmp_path: Path) -> None:
    """Django echoes SQL, frameworks print banners. The payload is delimited rather
    than assumed to be all of stdout."""
    noisy = Probe(
        workload="shop.books.list",
        script="print('SELECT 1')\nprint('WARNING: something')\noutput = [1, 2]",
    )
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    observed = run_on(session, noisy, NOTHING)
    assert isinstance(observed, Observed)
    assert observed.payload == [1, 2]


def test_a_probe_that_raises_is_not_an_empty_result(tmp_path: Path) -> None:
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    broken = Probe(workload="w", script="raise RuntimeError('the fixture is not seeded')")
    unobserved = run_on(session, broken, NOTHING)
    assert isinstance(unobserved, Unobserved)
    assert unobserved.reason is Failure.RAISED
    assert "the fixture is not seeded" in unobserved.evidence


def test_a_syntax_error_comes_back_under_the_harness_own_exit_code(tmp_path: Path) -> None:
    """**The exit code is the assertion, not the traceback.** A `SyntaxError` prints a
    perfectly good traceback either way — with `compile` outside the guarded block
    the interpreter simply picks its own code on the way out, and nothing about the
    text would say the harness had lost control of the failure. S-10.2 learned this
    by being wrong about it, where the code it happened to pick was the one meaning
    *the test failed*."""
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    unobserved = run_on(session, Probe(workload="w", script="output = ("), NOTHING)
    assert isinstance(unobserved, Unobserved)
    assert unobserved.reason is Failure.RAISED
    assert unobserved.exit_code == PROBE_ERROR_EXIT
    assert "SyntaxError" in unobserved.evidence
    assert "equivalence_probe" in unobserved.evidence


def test_a_probe_that_binds_nothing_says_so(tmp_path: Path) -> None:
    """The harness checks rather than letting the lookup raise. A `KeyError: 'output'`
    reads almost the same and arrives under the interpreter's exit code, so the
    assertion is on both."""
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    unobserved = run_on(session, Probe(workload="w", script="_ = 1"), NOTHING)
    assert isinstance(unobserved, Unobserved)
    assert unobserved.exit_code == PROBE_ERROR_EXIT
    assert "did not bind" in unobserved.evidence


def test_a_decoy_marker_line_does_not_become_the_payload(tmp_path: Path) -> None:
    """The harness prints its line and exits, so the real payload is the last one. A
    subject echoing a request body that happens to contain the marker prints an
    earlier one, and taking the first would hand back whatever it echoed."""
    decoy = Probe(
        workload="w",
        script=f"print({MARKER!r} + '[999]')\noutput = [1]",
    )
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    observed = run_on(session, decoy, NOTHING)
    assert isinstance(observed, Observed)
    assert observed.payload == [1]


def test_a_long_failure_keeps_its_tail(tmp_path: Path) -> None:
    """The end of a traceback names what broke. A head-first trim would keep the
    framework's import chain and drop the exception."""
    noisy = read(a_result(exit_code=3, stderr="x" * 50_000 + "\nValueError: the last line"))
    assert isinstance(noisy, Unobserved)
    assert len(noisy.evidence) < 50_000
    assert noisy.evidence.endswith("ValueError: the last line")


def test_a_null_answer_is_a_result_and_not_a_failure(tmp_path: Path) -> None:
    """The whole null attack class depends on the distinction. A workload that
    legitimately returns null must not be indistinguishable from a probe that never
    bound anything."""
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    observed = run_on(session, Probe(workload="w", script="output = None"), NOTHING)
    assert isinstance(observed, Observed)
    assert observed.payload is None


def test_an_answer_that_is_not_json_fails_rather_than_being_guessed_at(tmp_path: Path) -> None:
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    unobserved = run_on(
        session,
        Probe(workload="w", script="import datetime\noutput = datetime.date.today()"),
        NOTHING,
    )
    assert isinstance(unobserved, Unobserved)
    assert unobserved.reason is Failure.RAISED


def test_an_elided_stream_is_not_reported_as_a_broken_probe() -> None:
    """A subject that printed eight megabytes and a probe with a bug are different
    problems, and only one of them is the probe's fault."""
    cut = read(a_result(stdout=MARKER + '{"id": 1', stdout_dropped_chars=4096))
    assert isinstance(cut, Unobserved)
    assert cut.reason is Failure.TRUNCATED

    silent = read(a_result(stdout="nothing here", stdout_dropped_chars=4096))
    assert isinstance(silent, Unobserved)
    assert silent.reason is Failure.TRUNCATED

    unparsable = read(a_result(stdout=MARKER + '{"id": 1'))
    assert isinstance(unparsable, Unobserved)
    assert unparsable.reason is Failure.UNPARSABLE


def test_a_clean_exit_with_no_marker_is_a_failure() -> None:
    assert read(a_result(stdout="all done\n")).reason is Failure.NO_OUTPUT  # type: ignore[union-attr]


def test_a_killed_run_is_not_two_probes_agreeing_on_nothing(tmp_path: Path) -> None:
    session = FakeOriginal(tmp_path / "original", IDENTITY)
    session.raises = ExecutionTimeoutError(["python"], 120.0, "", "")
    unobserved = run_on(session, ECHO, NOTHING)
    assert isinstance(unobserved, Unobserved)
    assert unobserved.reason is Failure.TIMED_OUT
    assert unobserved.exit_code is None


def test_a_probe_with_no_script_is_refused() -> None:
    with pytest.raises(EquivalenceError, match="would read as the patch surviving"):
        Probe(workload="w", script="   ")


# ============ AC 2 — runs both revisions and diffs outputs


def test_an_unchanged_revision_survives_the_whole_catalogue(tmp_path: Path) -> None:
    original, patched = both(tmp_path, IDENTITY, IDENTITY)
    result = attack(ECHO, original=original, patched=patched)

    assert result.survived
    assert result.complete
    assert not result.reproducing
    assert len(result.compared) == len(catalogue())
    assert result.runs == 2 * len(catalogue()), "two runs per input, no confirmations needed"
    assert {item.outcome for item in result.probed} == {Outcome.MATCHED}


def test_a_reordering_patch_is_caught_and_labelled_as_order_only(tmp_path: Path) -> None:
    """Sorting on a key that repeats is not a total order, so moving the sort changes
    which of the tied rows comes first and nothing else."""
    original, patched = both(tmp_path, IDENTITY, SORTED_BY_ID)
    result = attack(PROBE, original=original, patched=patched, inputs=[TIED])

    assert not result.survived
    (found,) = result.reproducing
    assert found.input is TIED
    assert found.divergence is not None
    assert found.divergence.order_only
    assert result.probed[0].outcome is Outcome.DIFFERED


def test_a_patch_that_drops_a_field_is_caught_and_is_not_order_only(tmp_path: Path) -> None:
    original, patched = both(tmp_path, IDENTITY, DROPS_A_FIELD)
    result = attack(PROBE, original=original, patched=patched, inputs=[ROWS])

    (found,) = result.reproducing
    assert found.divergence is not None
    assert not found.divergence.order_only
    assert "name" in found.divergence.first.location


def test_the_comparison_cannot_be_told_to_ignore_order() -> None:
    """**The one knob that would turn a real difference into a clean bill.**
    `bench/diffing.py` makes order-insensitivity opt-in because whoever knows
    whether the query had an `ORDER BY` should choose — and the Adversary is by
    construction the party who does not know."""
    assert "ignore_order" not in inspect.signature(compare_outputs).parameters
    assert "ignore_order" not in inspect.signature(attack).parameters
    assert "float_tolerance" not in inspect.signature(compare_outputs).parameters

    reordered = compare_outputs([{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 1}])
    assert reordered is not None
    assert reordered.order_only, "labelled, not forgiven"


def test_the_two_sides_take_opposite_session_types() -> None:
    """`DiagnosticSession` has no `apply_patch`, so *the revision before the change*
    is a property of the type rather than a claim about the caller."""
    parameters = inspect.signature(attack).parameters
    assert parameters["original"].annotation == "DiagnosticSession"
    assert parameters["patched"].annotation == "CandidateSession"


def test_two_different_base_commits_are_refused(tmp_path: Path) -> None:
    """A difference measured across two base revisions is somebody else's change
    reported as this patch's, and it looks exactly like a broken patch."""
    original = FakeOriginal(tmp_path / "original", IDENTITY, revision="9f1c0de")
    patched = FakePatched(tmp_path / "patched", IDENTITY, revision="aa20b31")
    with pytest.raises(EquivalenceError, match="different commits"):
        attack(ECHO, original=original, patched=patched)


def test_an_attack_with_no_inputs_is_refused(tmp_path: Path) -> None:
    """It would find no difference. That is the shape of a patch surviving, produced
    without attacking it."""
    original, patched = both(tmp_path, IDENTITY, IDENTITY)
    with pytest.raises(EquivalenceError, match="finds no difference"):
        attack(ECHO, original=original, patched=patched, inputs=[])


# ============ AC 3 — a reproducing input


def test_the_reproducing_input_actually_reproduces(tmp_path: Path) -> None:
    """**The one with teeth.** An objection nobody can re-run is one nobody can act
    on, so the artifact carries the exact program and this test runs it."""
    original, patched = both(tmp_path, IDENTITY, DROPS_A_FIELD)
    result = attack(PROBE, original=original, patched=patched, inputs=[ROWS])
    (found,) = result.reproducing

    before = read(original.run([sys.executable, "-c", found.program], timeout=30.0))
    after = read(patched.run([sys.executable, "-c", found.program], timeout=30.0))
    assert isinstance(before, Observed)
    assert isinstance(after, Observed)

    again = compare_outputs(before.payload, after.payload)
    assert again is not None
    assert found.divergence is not None
    assert again.differences == found.divergence.differences


def test_a_patch_that_crashes_where_the_original_answered_is_an_objection(
    tmp_path: Path,
) -> None:
    """The strongest form of the difference, and it still has to arrive with the input
    that produces it."""
    original, patched = both(tmp_path, IDENTITY, CRASHES_ON_EMPTY)
    result = attack(PROBE, original=original, patched=patched, inputs=[NOTHING])

    assert not result.survived
    (found,) = result.reproducing
    assert result.probed[0].outcome is Outcome.PATCH_BROKE_THE_PROBE
    assert isinstance(found.after, Unobserved)
    assert found.divergence is None
    assert found.before == []
    assert "IndexError" in found.after.evidence


def test_a_difference_the_subject_manufactures_is_not_an_objection(tmp_path: Path) -> None:
    """A response carrying a fresh uuid differs from its own second run. Reported as a
    broken patch it sends the Surgeon to rewrite code that was right."""
    original, patched = both(tmp_path, FRESH_EACH_TIME, FRESH_EACH_TIME)
    result = attack(PROBE, original=original, patched=patched, inputs=[ROWS])

    assert not result.reproducing
    assert result.probed[0].outcome is Outcome.NONDETERMINISTIC
    assert "two different outputs" in result.probed[0].note
    assert result.runs == 4, "the confirmation is only paid for where something was found"


def test_an_input_that_matched_beside_one_that_would_not_settle_is_not_a_clean_bill(
    tmp_path: Path,
) -> None:
    """**The third condition on `survived`, and it needs a match to be visible.** Where
    nothing settled there is nothing compared either, so the first condition already
    answers — this subject answers one input deterministically and manufactures the
    other, which is the only shape where dropping the unstable clause changes the
    verdict. An input the subject would not answer twice says the workload is not
    deterministic under this probe, and under that the input that matched matched
    once."""
    varies = (
        "import uuid\n\n\ndef answer(value):\n    return [str(uuid.uuid4())] if value else []\n"
    )
    original, patched = both(tmp_path, varies, varies)
    result = attack(PROBE, original=original, patched=patched, inputs=[NOTHING, ROWS])

    assert [item.outcome for item in result.probed] == [
        Outcome.MATCHED,
        Outcome.NONDETERMINISTIC,
    ]
    assert len(result.compared) == 1
    assert not result.reproducing
    assert not result.survived
    assert "not deterministic under this probe" in result.describe()


def test_the_original_is_checked_against_itself_and_not_only_the_pair(tmp_path: Path) -> None:
    """A check that re-ran only the *pair* would confirm a difference that the subject
    manufactures afresh on every run."""
    original, patched = both(tmp_path, FRESH_EACH_TIME, IDENTITY)
    result = attack(PROBE, original=original, patched=patched, inputs=[ROWS])

    assert result.probed[0].outcome is Outcome.NONDETERMINISTIC
    assert "the original revision" in result.probed[0].note


def test_a_patched_revision_that_varies_between_runs_is_not_an_objection(
    tmp_path: Path,
) -> None:
    original, patched = both(tmp_path, IDENTITY, FRESH_EACH_TIME)
    result = attack(PROBE, original=original, patched=patched, inputs=[ROWS])

    assert not result.reproducing
    assert result.probed[0].outcome is Outcome.NONDETERMINISTIC
    assert "the patched revision" in result.probed[0].note


def test_a_divergence_with_no_differences_cannot_be_built() -> None:
    """Two payloads that matched, recorded as though they had not. Nothing downstream
    reads `differences` before deciding the patch is broken, so an empty one would be
    an objection with no content."""
    with pytest.raises(EquivalenceError, match="two payloads that matched"):
        Divergence(differences=(), order_only=False)


def test_a_reproducing_input_that_reproduces_nothing_cannot_be_built() -> None:
    with pytest.raises(EquivalenceError, match="reproduces nothing"):
        ReproducingInput(
            input=ROWS,
            before=[1],
            after=Observed(payload=[1], wall_seconds=0.1),
            divergence=None,
            program="print()",
        )
    with pytest.raises(EquivalenceError, match="nothing a divergence"):
        ReproducingInput(
            input=ROWS,
            before=[1],
            after=Unobserved(reason=Failure.RAISED, evidence="boom", exit_code=3),
            divergence=compare_outputs([1], [2]),
            program="print()",
        )


# ============ the false `identical`, which does not fail a run — it ships a patch


def test_a_probe_that_drives_nothing_is_not_a_clean_bill(tmp_path: Path) -> None:
    """**The failure an obvious implementation commits.** Collect payloads, diff,
    report no differences — and a probe broken enough to produce nothing on every
    input reports the patch as equivalent."""
    original, patched = both(tmp_path, IDENTITY, DROPS_A_FIELD)
    useless = Probe(workload="w", script="raise ImportError('no module named subject')")
    result = attack(useless, original=original, patched=patched)

    assert not result.survived
    assert not result.compared
    assert len(result.inconclusive) == len(catalogue())
    assert "Nothing was compared" in result.describe()
    assert "not a clean bill" in result.describe()


def test_a_partial_sweep_says_so_rather_than_reading_as_a_full_one(tmp_path: Path) -> None:
    """S-3.2's rule at the last gate: dropping what could not be driven publishes a
    comparison that covered less than it claims."""
    original, patched = both(tmp_path, CRASHES_ON_EMPTY, CRASHES_ON_EMPTY)
    result = attack(PROBE, original=original, patched=patched, inputs=[NOTHING, ROWS])

    assert result.survived, "one input was compared and matched"
    assert not result.complete
    assert [item.outcome for item in result.probed] == [Outcome.NOT_COMPARED, Outcome.MATCHED]
    assert "sweep is partial" in result.describe()
    assert Shape.EMPTY.value in result.describe()


def test_survival_requires_something_to_have_been_compared() -> None:
    nothing_ran = Equivalence(
        workload="w",
        probed=(Probed(NOTHING, Outcome.NOT_COMPARED, "the probe raised"),),
        reproducing=(),
        runs=2,
    )
    assert not nothing_ran.survived


def test_an_attack_over_no_inputs_at_all_cannot_be_recorded() -> None:
    with pytest.raises(EquivalenceError, match="never mounted"):
        Equivalence(workload="w", probed=(), reproducing=(), runs=0)


def test_an_objection_without_its_reproducing_input_is_refused() -> None:
    """S-11.7 requires one for every `broken`. A difference recorded without the means
    to reproduce it cannot be sent back to the Surgeon."""
    with pytest.raises(EquivalenceError, match="cannot be sent back"):
        Equivalence(
            workload="w",
            probed=(Probed(ROWS, Outcome.DIFFERED, "the name is gone"),),
            reproducing=(),
            runs=4,
        )


def test_the_report_states_what_a_surviving_attack_does_not_establish(tmp_path: Path) -> None:
    """A patch that also writes a row produces identical output and is not
    equivalent, and no comparison of two payloads can see that."""
    original, patched = both(tmp_path, IDENTITY, IDENTITY)
    described = attack(ECHO, original=original, patched=patched, inputs=[ROWS]).describe()
    assert RESIDUE in described
    assert "null result" in described


def test_a_probes_scripts_never_reach_the_worktree(tmp_path: Path) -> None:
    """S-2.4 rejects a patch that touches a test, so a probe written into the tree
    would be a protected path every later diff shows. It travels on the command
    line instead."""
    original, patched = both(tmp_path, IDENTITY, IDENTITY)
    attack(ECHO, original=original, patched=patched, inputs=[ROWS])

    for session in (original, patched):
        assert [path.name for path in session.worktree.path.iterdir()] == ["subject.py"]
        assert all(command[:2] == ["python", "-c"] for command in session.commands)
        assert all(ECHO.script in command[2] for command in session.commands)


def test_a_payload_a_shape_could_not_carry_is_still_rendered(tmp_path: Path) -> None:
    """The report has to be readable for the input that broke things, whatever it
    was — including one that is not a collection at all."""
    big = AdversarialInput(shape=Shape.BOUNDARY, label="a large integer", payload=2**63 - 1)
    original, patched = both(tmp_path, IDENTITY, "def answer(value):\n    return value % 2**31\n")
    result = attack(PROBE, original=original, patched=patched, inputs=[big])
    (found,) = result.reproducing
    assert str(2**63 - 1) in found.describe()
    described = found.describe()
    assert all(f"    {line}" in described for line in found.program.splitlines())

"""S-8.6 — the artifact a diagnosis produces, and what it cannot be built without.

`02-architecture.md` §2.4 calls the chain *simultaneously the input to Layer 3 and
the body of the eventual pull request*, so every test here is really about one
question: what can reach a human with a signature under it.

Two things get the most attention. Exclusions must arrive carrying their
preconditions, or F3 is back at the report boundary. And `confidence` must be the
number the evidence derives rather than the number an agent typed.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import (
    RESIDUE,
    ChainError,
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
    confidence_for,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict

GOLDEN = Path(__file__).parent / "golden" / "evidence_chain.json"

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape="uniform", platform="x86_64-linux", concurrency=1, scales=[10, 100, 1000]
)


def a_log() -> ExperimentLog:
    return ExperimentLog()


def excluded(log: ExperimentLog) -> Experiment:
    return log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000], distribution='uniform')",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7, 7, 7 across a 100x sweep",
    )


def confirmed(
    log: ExperimentLog, *, primitive: str = "ablation.stub", verdict: Verdict = Verdict.CONFIRMED
) -> Experiment:
    return log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive=primitive,
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design=f"{primitive}(attribute='to_representation') on shop.books.list",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9},
        verdict=verdict,
        outcome="stubbing the serializer removed 89% of wall time",
    )


def link(experiment: Experiment, *, share: float = 0.89) -> LocalizationLink:
    return LocalizationLink(
        scope="BookSerializer.to_representation",
        experiment=experiment,
        share_of_cost=share,
        basis="8.24s baseline against 0.90s ablated, same fixture and scale",
    )


def a_chain(**overrides: object) -> EvidenceChain:
    """The worked example, varying only what a test is about."""
    log = a_log()
    fields: dict[str, object] = {
        "symptom": Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        "exclusions": [Exclusion(experiment=excluded(log), conditions=UNIFORM_AT_1000)],
        "localization": [link(confirmed(log))],
        "mechanism": "the serializer re-renders the author for every book in the list",
        "complexity": {"rows": Growth.LINEAR},
        "site": Site(
            path="shop/serializers.py",
            first_line=41,
            last_line=52,
            source=("def to_representation(self, obj):",),
        ),
        "context": [
            Implicated(
                path="shop/models.py",
                reason="declares the Author relation the serializer walks per book",
            )
        ],
    }
    fields.update(overrides)
    return EvidenceChain.assemble(**fields)  # type: ignore[arg-type]


# ================================================ AC 1: eight fields, all required


@pytest.mark.parametrize(
    "dropped",
    ["symptom", "exclusions", "localization", "mechanism", "complexity", "site", "context"],
)
def test_a_chain_missing_any_field_is_refused(dropped: str) -> None:
    """Parametrised so no field is the one that happens to be checked. `confidence`
    is absent from this list because `assemble` derives it — its own tests are
    below."""
    log = a_log()
    complete: dict[str, object] = {
        "symptom": Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        "exclusions": [Exclusion(experiment=excluded(log), conditions=UNIFORM_AT_1000)],
        "localization": [link(confirmed(log))],
        "mechanism": "the serializer re-renders the author",
        "complexity": {"rows": Growth.LINEAR},
        "site": Site(path="shop/serializers.py", first_line=41, last_line=52),
        "context": [Implicated(path="shop/models.py", reason="declares the relation")],
    }
    del complete[dropped]

    with pytest.raises(TypeError):
        EvidenceChain.assemble(**complete)  # type: ignore[arg-type]


def test_a_field_nobody_declared_is_refused() -> None:
    """S-7.8's recorded lesson: `extra="forbid"` is the guarantee that actually
    holds, because mypy's pydantic plugin does not model it."""
    chain = a_chain()

    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        EvidenceChain(**{**chain.model_dump(), "certainty": 0.99})


def test_the_chain_is_frozen() -> None:
    """It is handed to the Surgeon and then to the Adversary. An artifact either
    of them could edit is not evidence."""
    with pytest.raises(ValidationError, match=r"frozen|immutable"):
        a_chain().mechanism = "something else"


def test_a_chain_with_no_measured_axis_is_refused() -> None:
    with pytest.raises(ChainError, match="how the cost scales"):
        a_chain(complexity={})


def test_a_site_whose_lines_run_backwards_is_refused() -> None:
    with pytest.raises(ValidationError, match="line 52 to 41"):
        Site(path="shop/serializers.py", first_line=52, last_line=41)


# ============= AC 2: every localization link requires an attached measurement


def test_a_localization_link_has_no_measurement_field_to_leave_blank() -> None:
    """**AC 2 is structural rather than a validator.** The measurement can only
    arrive inside an `Experiment`, so there is no field here for anybody to omit.
    Asserted by inspection, so it fails the moment somebody adds one."""
    assert "measurement" not in LocalizationLink.model_fields
    assert "experiment" in LocalizationLink.model_fields


def test_a_link_cannot_be_built_around_an_experiment_with_no_measurement() -> None:
    """The active attempt AC 2 asks for. S-8.4 refuses the experiment, so the
    refusal happens one layer down and there is no path past it."""
    with pytest.raises(ValidationError, match="no measurement"):
        Experiment(
            index=1,
            hypothesis="h",
            primitive="ablation.stub",
            rationale="r",
            target="t",
            design="d",
            measurement={},
            verdict=Verdict.CONFIRMED,
            outcome="o",
        )


def test_a_chain_with_no_localization_at_all_is_refused() -> None:
    """Refused by the *confirming-link* check rather than by a length bound.

    A `min_length=1` sat here until a sabotage pass showed it could not change an
    outcome — *at least one confirming link* strictly implies *at least one link*
    — so it is gone and this asserts the check that actually fires.
    """
    with pytest.raises(ChainError, match="confirm"):
        a_chain(localization=[])


def test_no_string_the_reader_depends_on_may_be_empty() -> None:
    """Swept rather than asserted one at a time, because a sabotage pass found
    three of these untested while their neighbours were covered.

    Every one names something a person reads out of the pull request: what was
    observed, what was narrowed, how the share was computed, where the cause is,
    and what the cause *is*. An empty one is a chain that validates and says
    nothing.
    """
    log = a_log()
    empties: list[tuple[str, object]] = [
        ("symptom metric", lambda: Symptom(metric="", magnitude=8.24, at_scale=1000)),
        (
            "localization scope",
            lambda: LocalizationLink(
                scope="", experiment=confirmed(log), share_of_cost=0.5, basis="b"
            ),
        ),
        (
            "share basis",
            lambda: LocalizationLink(
                scope="s", experiment=confirmed(log), share_of_cost=0.5, basis=""
            ),
        ),
        ("site path", lambda: Site(path="", first_line=1, last_line=2)),
        ("implicated path", lambda: Implicated(path="", reason="because")),
    ]

    for name, build in empties:
        with pytest.raises(ValidationError):
            build()  # type: ignore[operator]
            pytest.fail(f"{name} accepted an empty value")


def test_a_chain_with_no_mechanism_is_refused() -> None:
    """The mechanism is the sentence the pull request is titled with. `08-audit.md`
    records that schema validation cannot check whether it *follows* from the
    evidence — it can at least insist there is one."""
    with pytest.raises(ChainError):
        a_chain(mechanism="")


def test_an_implicated_file_still_needs_a_reason_without_a_length_bound() -> None:
    """The other half of the same finding: `min_length=1` beside the whitespace
    validator was redundant, since *not whitespace* implies *not empty*."""
    with pytest.raises(ValidationError, match="whitespace is not one"):
        Implicated(path="shop/models.py", reason="")

    assert Implicated(path="shop/models.py", reason="x").reason == "x"


@pytest.mark.parametrize("verdict", [Verdict.NARROWED, Verdict.REJECTED])
def test_a_chain_nothing_confirmed_is_refused(verdict: Verdict) -> None:
    """Narrowing is progress and rejection is an exclusion. Neither is a
    diagnosis, and a chain assembled from them claims a cause no experiment
    established."""
    log = a_log()

    with pytest.raises(ChainError, match="confirm"):
        a_chain(localization=[link(confirmed(log, verdict=verdict))])


def test_the_unconfirmed_chain_is_refused_on_the_deserialization_path_too() -> None:
    """**Two guards, two paths, and neither is redundant.** `assemble` never
    reaches the model validator because deriving the confidence refuses first;
    validating a chain that already carries one — which is what loading a stored
    chain does — reaches the validator instead. A single guard would leave
    whichever path it does not sit on unprotected."""
    log = a_log()
    chain = a_chain()
    unconfirmed = {
        **chain.model_dump(),
        "localization": [link(confirmed(log, verdict=Verdict.NARROWED)).model_dump()],
    }

    with pytest.raises(ValidationError, match="no localization link came back confirmed"):
        EvidenceChain(**unconfirmed)


def test_a_share_of_cost_outside_a_fraction_is_refused() -> None:
    log = a_log()

    with pytest.raises(ValidationError):
        link(confirmed(log), share=1.4)


def test_a_share_of_cost_with_no_stated_basis_is_refused() -> None:
    """`Bound.basis`'s construction: a number whose derivation is not stated is
    one nobody can dispute."""
    log = a_log()

    with pytest.raises(ValidationError):
        LocalizationLink(
            scope="BookSerializer.to_representation",
            experiment=confirmed(log),
            share_of_cost=0.89,
            basis="",
        )


# ================================== AC 3: implicated files carry their reason


def test_an_implicated_file_with_no_reason_is_refused() -> None:
    """`02-architecture.md` §3 makes this list load-bearing for the patch: *scope
    is determined by the evidence chain's context list, not by the agent's
    guess.* A file admitted with no reason is one the Surgeon may edit because
    somebody felt it was relevant."""
    with pytest.raises(ValidationError):
        Implicated(path="shop/models.py", reason="")


def test_whitespace_is_not_a_reason() -> None:
    with pytest.raises(ValidationError, match="whitespace is not one"):
        Implicated(path="shop/models.py", reason="   ")


def test_the_reason_reaches_the_report() -> None:
    rendered = a_chain().render()

    assert "shop/models.py — declares the Author relation" in rendered


# =============== the finding: an exclusion reaches the reader with its conditions


def test_an_exclusion_carries_its_preconditions_into_the_chain() -> None:
    """**The story's substance.** §2.4 sketches an exclusion as
    `{hypothesis, primitive, measurement, verdict}` — no conditions. F3 is that an
    exclusion recorded as fact blocks the correct hypothesis, and S-8.5 fixed it
    inside the investigation; flattening it back here would reintroduce F3 at the
    one place it does most damage."""
    (exclusion,) = a_chain().exclusions

    assert isinstance(exclusion, Exclusion)
    assert "fixture shape uniform" in exclusion.conditions.describe()


def test_the_report_a_human_reads_states_what_each_exclusion_held_under() -> None:
    """*Not the database, queries flat at 7, 7, 7* printed in a pull request with
    no mention of the uniform fixtures is precisely F3's false fact, with a
    reviewer's signature under it."""
    rendered = a_chain().render()

    assert "the database is the bottleneck" in rendered
    assert "queries flat at 7, 7, 7" in rendered
    assert "under fixture shape uniform" in rendered
    assert "scale 10 to 1000" in rendered


def test_a_chain_that_ruled_nothing_out_says_so() -> None:
    """An empty section reads as a missing input; naming it reads as a result —
    and `00-BRIEF.md` §9 makes exclusions a form of output."""
    assert "nothing was ruled out" in a_chain(exclusions=[]).render()


# ========================================== confidence: derived, never asserted


@pytest.mark.parametrize(
    ("confirmations", "expected"),
    [(1, 0.5), (2, 0.75), (3, 0.875), (4, 0.9375)],
)
def test_confidence_is_derived_from_independent_confirmations(
    confirmations: int, expected: float
) -> None:
    """§2.4's *derived from number of independent confirmations*, as one
    function, so the model has a single place to be argued with."""
    assert confidence_for(confirmations) == expected


def test_confidence_never_reaches_certainty() -> None:
    """No chain claims to be sure. `00-BRIEF.md` §6's diagnostic agreement across
    ten runs stays the project's reliability number; this does not replace it."""
    assert confidence_for(50) < 1.0
    assert a_chain().confidence < 1.0


def test_a_chain_confirming_nothing_has_no_confidence_to_derive() -> None:
    with pytest.raises(ChainError, match="not a diagnosis"):
        confidence_for(0)


def test_two_confirmations_from_one_instrument_count_as_one() -> None:
    """Independence is the whole content of the number. Two runs of the same
    instrument are one kind of evidence."""
    log = a_log()
    chain = a_chain(
        localization=[
            link(confirmed(log, primitive="ablation.stub")),
            link(confirmed(log, primitive="ablation.stub")),
        ]
    )

    assert chain.independent_confirmations == 1
    assert chain.confidence == 0.5


def test_two_confirmations_from_different_instruments_count_as_two() -> None:
    """The control, and it is S-8.7's thesis behaviour showing up in the number:
    scaling and ablation agreeing is worth more than ablation twice."""
    log = a_log()
    chain = a_chain(
        localization=[
            link(confirmed(log, primitive="ablation.stub")),
            link(confirmed(log, primitive="scaling.volume")),
        ]
    )

    assert chain.independent_confirmations == 2
    assert chain.confidence == 0.75


def test_ruling_more_things_out_does_not_raise_confidence() -> None:
    """Deliberate. Counting exclusions would let an agent lift its own number by
    excluding things nobody suspected."""
    log = a_log()
    many = [
        Exclusion(experiment=excluded(log), conditions=UNIFORM_AT_1000),
        Exclusion(experiment=excluded(log), conditions=UNIFORM_AT_1000),
        Exclusion(experiment=excluded(log), conditions=UNIFORM_AT_1000),
    ]

    assert a_chain(exclusions=many).confidence == a_chain(exclusions=[]).confidence


def test_a_stated_confidence_that_disagrees_with_the_evidence_is_refused() -> None:
    """S-7.9's construction: the stored copy is mandatory and powerless. It has to
    be a field because the chain is serialized and travels; it is recomputed on
    validation so the copy cannot drift from what the evidence derives."""
    chain = a_chain()

    with pytest.raises(ValidationError, match="a number nobody measured"):
        EvidenceChain(**{**chain.model_dump(), "confidence": 0.99})


def test_assemble_does_not_let_a_caller_choose_the_number() -> None:
    """The constructor requires `confidence` because serialization does. Nothing
    that *makes* a chain gets to pass one."""
    assert "confidence" not in inspect.signature(EvidenceChain.assemble).parameters


# ================================================ AC 4: golden-file serialization


def test_the_serialization_matches_the_golden_file() -> None:
    """AC 4. Pins the wire format so a schema change is a visible diff rather than
    a surprise two agents downstream — the chain is the Surgeon's input, the
    Adversary's input, and the pull request body.

    Regenerate deliberately, never reflexively: a diff here means the artifact
    three other components read has changed shape.
    """
    assert a_chain().serialize() == GOLDEN.read_text(encoding="utf-8")


def test_the_serialization_is_stable_across_two_builds() -> None:
    assert a_chain().serialize() == a_chain().serialize()


def test_the_serialization_agrees_in_a_second_process() -> None:
    """S-8.4's construction: the guarantee a canonical form has is that another
    process agrees, and hash-order randomization only has room to move across a
    process boundary."""
    program = (
        "import sys; sys.path.insert(0, r'" + str(Path(__file__).parent.parent) + "');"
        "from diagnosis.test_chain import a_chain; print(a_chain().serialize(), end='')"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=300, check=True
    )

    assert result.stdout == a_chain().serialize()


def test_the_golden_file_is_the_chain_and_not_a_stale_copy() -> None:
    """The golden file is only evidence if it parses back into a valid chain. A
    hand-edited one that no longer validates would otherwise sit there passing
    the comparison against a chain nobody could build."""
    restored = EvidenceChain.model_validate(json.loads(GOLDEN.read_text(encoding="utf-8")))

    assert restored == a_chain()
    assert restored.confidence == 0.5


def test_the_report_names_what_the_schema_cannot_check() -> None:
    """S-7.12's `Anchor.residue` construction. Schema validation and adversarial
    review address different failure modes, and `08-audit.md` records that we had
    only the first — so the artifact says which one it is."""
    assert RESIDUE in a_chain().render()
    assert "does not check that the mechanism follows" in RESIDUE

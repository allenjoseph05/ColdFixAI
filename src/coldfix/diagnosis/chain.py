"""The artifact a diagnosis produces, and what it cannot be assembled without.

Epic 8, S-8.6. `02-architecture.md` §2.4 calls this *simultaneously the input to
Layer 3 and the body of the eventual pull request*, which is why every guard here
is a schema rule rather than a convention: the next reader is the Surgeon, and
the one after that is a person deciding whether to merge.

**`exclusions` holds S-8.5's `Exclusion`, not a bare experiment, and that is this
story's substance.** §2.4 sketches an exclusion as
`{hypothesis, primitive, measurement, verdict: rejected}` — with no conditions.
`08-audit.md` F3 is the finding that an exclusion recorded as fact permanently
blocks the correct hypothesis, and S-8.5 fixed it *inside the investigation*. A
chain that flattened those back into bare experiments would reintroduce F3 at the
one boundary where it does the most damage: **the report a human reads.** *Not
the database, queries flat at 7, 7, 7* printed in a pull request with no mention
of the uniform fixtures it was established under is precisely the false fact F3
describes, now with a reviewer's signature under it.

**`confidence` is derived, required, and recomputed — S-7.9's construction.**
§2.4 says *derived from number of independent confirmations*; `03-agents.md` §4.4
writes it as a bare `float`, and the authority map gives artifact schemas to §2.4.
A confidence an agent writes is a number nobody measured. But a bare property
would not survive serialization — and this artifact is serialized, travels to two
other agents, and becomes a pull request — so the field is **required and checked
against the recomputation**, exactly as S-7.9 does for `work_verified`: the copy
is mandatory, and validating one refuses a copy that disagrees.

**Confidence is not a probability, and the schema says so in a bound rather than
in a docstring nobody reads.** `1 - 2**-k` over *k* independent confirmations is a
model — each new instrument that agrees halves the remaining doubt — and a model
is an assumption, not a measurement. Two properties keep it honest: it can never
reach 1, so no chain claims certainty, and `00-BRIEF.md` §6's *diagnostic
agreement across ten runs* remains the project's actual reliability number, which
this cannot substitute for.

**Independence is counted in distinct primitives, and exclusions never raise it.**
Two confirmations from one instrument are one kind of evidence; scaling and
ablation agreeing is two. Ruling something out does intuitively raise confidence
in what remains, and counting it here would let an agent lift its own number by
excluding things nobody suspected — so exclusions are carried, reported, and
deliberately not counted.

**What this cannot check, stated rather than implied.** `share_of_cost` is a
number whose arithmetic happened elsewhere; the schema requires a measurement
under it and a stated basis, the construction `Bound` already uses, and it cannot
verify the division. Nothing here decides whether the mechanism *follows* from the
evidence — `08-audit.md` names that as what E9's finding audit exists for, and
records that schema validation and adversarial review address different failure
modes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coldfix.bench.stats import Growth
from coldfix.diagnosis.exclusions import Exclusion
from coldfix.diagnosis.log import Experiment, Verdict

_STRICT = ConfigDict(frozen=True, extra="forbid")

CONFIDENCE_TOLERANCE = 1e-9
"""How far a stated confidence may sit from the derived one.

Float round-tripping through JSON, and nothing else: this is not a margin for
disagreement. A chain whose confidence differs by anything a reader could notice
is refused, because the point of recomputing it is that the two cannot drift.
"""

RESIDUE = (
    "This schema proves that every link carries a measurement and that the "
    "confidence matches the confirmations. It does not check that the mechanism "
    "follows from them: a faithful measurement can support a wrong conclusion, "
    "which is what the finding audit exists to catch."
)


class ChainError(Exception):
    """An evidence chain could not be assembled."""


class Symptom(BaseModel):
    """What was observed, and at what scale. §2.4's *metric and magnitude at
    reference scale*.

    The scale is part of the symptom rather than context for it: *8.24 seconds*
    is not a symptom, *8.24 seconds at a thousand rows* is.
    """

    model_config = _STRICT

    metric: str = Field(min_length=1)
    magnitude: float
    at_scale: float = Field(gt=0)

    def describe(self) -> str:
        return f"{self.metric} = {self.magnitude} at scale {self.at_scale:g}"


class LocalizationLink(BaseModel):
    """One step that narrowed the cause, and the experiment that produced it.

    **AC 2 is satisfied structurally rather than by a validator.** A link holds an
    `Experiment`, and S-8.4 already refuses one whose measurement is empty — so
    there is no way to construct a link with no measurement, and no `measurement`
    field here for anybody to leave blank. The test for AC 2 attempts it and
    asserts the refusal.
    """

    model_config = _STRICT

    scope: str = Field(min_length=1)
    """What this link narrowed to — a component, a call site, a query."""

    experiment: Experiment
    """The experiment that narrowed it, carrying its measurement."""

    share_of_cost: float = Field(ge=0.0, le=1.0)
    """How much of the observed cost this scope accounts for.

    Bounded to a fraction, and that is all this schema can do for it: the
    arithmetic that produced the number happened in the primitive, and `basis`
    is what makes it checkable by a person.
    """

    basis: str = Field(min_length=1)
    """How the share was computed. `Bound.basis`'s construction, for the same
    reason: a number whose derivation is not stated is one nobody can dispute."""

    @property
    def confirming(self) -> bool:
        return self.experiment.verdict is Verdict.CONFIRMED

    def describe(self) -> str:
        return (
            f"{self.scope} — {self.share_of_cost:.0%} of cost "
            f"({self.experiment.primitive}, experiment {self.experiment.index}: {self.basis})"
        )


class Site(BaseModel):
    """Where the cause is. §2.4's *file, line range, source*."""

    model_config = _STRICT

    path: str = Field(min_length=1)
    first_line: int = Field(gt=0)
    last_line: int = Field(gt=0)
    source: tuple[str, ...] = ()
    """The lines themselves, read by the harness. Empty is allowed: S-3.9's
    closure reads best-effort, because a file it cannot see weakens a finding
    and an exception loses it."""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.last_line < self.first_line:
            message = f"the site runs from line {self.first_line} to {self.last_line}"
            raise ValueError(message)
        return self

    def describe(self) -> str:
        return f"{self.path}:{self.first_line}-{self.last_line}"


class Implicated(BaseModel):
    """A file the evidence implicated, and why. AC 3.

    The reason is required because `02-architecture.md` §3 makes this list
    **load-bearing for the patch**: *scope is determined by the evidence chain's
    context list, not by the agent's guess.* A file admitted here with no reason
    is a file the Surgeon may edit because somebody felt it was relevant.
    """

    model_config = _STRICT

    path: str = Field(min_length=1)
    reason: str
    """Required, and checked for substance rather than for length.

    A `min_length=1` was written beside this validator and a sabotage pass showed
    it was redundant — *not whitespace* implies *not empty* — so it is gone, for
    the reason the `localization` field records."""

    @field_validator("reason")
    @classmethod
    def _substantive(cls, reason: str) -> str:
        if not reason.strip():
            message = "an implicated file needs a reason, and whitespace is not one"
            raise ValueError(message)
        return reason


def confidence_for(confirmations: int) -> float:
    """§2.4's *derived from number of independent confirmations*.

    `1 - 2**-k`: each independent instrument that agrees halves the remaining
    doubt. **That is a model and not a measurement**, and it is written here as
    one function so that the assumption has a single place to be argued with
    rather than being spread across the schema.

    Never reaches 1, so no chain can claim certainty. `00-BRIEF.md` §6's
    diagnostic agreement across ten runs stays the project's reliability number;
    this does not substitute for it.
    """
    if confirmations < 1:
        message = (
            "a chain with no confirming localization link is not a diagnosis. The first "
            "non-negotiable is that a conclusion drawn without a measurement is not a finding, "
            "and a chain confirming nothing has drawn one anyway"
        )
        raise ChainError(message)
    return 1.0 - 2.0**-confirmations


class EvidenceChain(BaseModel):
    """AC 1's eight fields, every one required.

    Frozen and `extra="forbid"`. Frozen because this is handed to the Surgeon and
    then to the Adversary, and an artifact either of them could edit is not
    evidence; `extra="forbid"` because S-7.8 recorded that this is the guarantee
    that actually holds — mypy's pydantic plugin does not model it, so a stray
    field is caught at validation rather than at type-check.
    """

    model_config = _STRICT

    symptom: Symptom
    exclusions: tuple[Exclusion, ...]
    """S-8.5's exclusions, **with their preconditions**. See the module docstring
    for why a bare experiment here would reintroduce F3 in the pull request."""

    localization: tuple[LocalizationLink, ...]
    """**No `min_length`, and its absence is deliberate.** One was written here
    and a sabotage pass proved it could not change an outcome: *at least one
    confirming link* strictly implies *at least one link*, so removing the length
    bound left every test passing. That makes it S-7.4's redundant condition —
    unverifiable by construction and reading as a guard while guarding nothing —
    and the remedy is S-7.4's: collapse it, and let the stronger check speak. The
    refusal it produces is also the better sentence."""
    mechanism: str = Field(min_length=1)
    complexity: Mapping[str, Growth]
    """Measured growth per varying axis. S-1.5's vocabulary rather than prose, so
    two chains can be compared instead of read."""

    site: Site
    context: tuple[Implicated, ...]
    confidence: float = Field(ge=0.0, lt=1.0)
    """Required **and** recomputed. See `_confidence_matches`."""

    @field_validator("complexity")
    @classmethod
    def _measured_on_some_axis(cls, complexity: Mapping[str, Growth]) -> Mapping[str, Growth]:
        if not complexity:
            message = (
                "no axis has a measured growth relationship. §2.4 asks for the growth on each "
                "varying axis, and a chain that names none has not established how the cost scales"
            )
            raise ValueError(message)
        return dict(complexity)

    @model_validator(mode="after")
    def _confirmed_by_something(self) -> Self:
        if not any(link.confirming for link in self.localization):
            message = (
                "no localization link came back confirmed. Narrowing is progress and rejection is "
                "an exclusion; neither is a diagnosis, and a chain assembled from them claims a "
                "cause no experiment established"
            )
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _confidence_matches(self) -> Self:
        """S-7.9's construction: the stored copy is mandatory and powerless.

        A property alone would stop an agent writing the number and would also
        vanish from the JSON, and this artifact is serialized on purpose. So the
        field is required and validating it recomputes the value — a chain whose
        confidence disagrees with its own confirmations does not validate.
        """
        expected = confidence_for(self.independent_confirmations)
        if abs(self.confidence - expected) > CONFIDENCE_TOLERANCE:
            message = (
                f"this chain states a confidence of {self.confidence} and its "
                f"{self.independent_confirmations} independent confirmation(s) derive "
                f"{expected}. §2.4 derives confidence from the confirmations, so a stated one "
                "that disagrees is a number nobody measured"
            )
            raise ValueError(message)
        return self

    @property
    def confirmations(self) -> tuple[LocalizationLink, ...]:
        return tuple(link for link in self.localization if link.confirming)

    @property
    def independent_confirmations(self) -> int:
        """Distinct **primitives** that confirmed, not distinct links.

        Two confirmations from one instrument are one kind of evidence; scaling
        and ablation agreeing is two, and it is the switch S-8.7 exists to
        produce. Exclusions are not counted — see the module docstring for why
        counting them would let an agent raise its own number.
        """
        return len({link.experiment.primitive for link in self.confirmations})

    @classmethod
    def assemble(  # noqa: PLR0913 - §2.4's eight fields minus the one that is
        # derived. Bundling them would invent a type whose only purpose is to be
        # unpacked here, and every one is a different kind of evidence.
        cls,
        *,
        symptom: Symptom,
        exclusions: Sequence[Exclusion],
        localization: Sequence[LocalizationLink],
        mechanism: str,
        complexity: Mapping[str, Growth],
        site: Site,
        context: Sequence[Implicated],
    ) -> EvidenceChain:
        """Build a chain, deriving the confidence rather than being told it.

        The constructor still requires `confidence`, because a serialized chain
        must carry it. This is how anything in this system *makes* one: nothing
        that assembles a chain gets to choose the number.

        Raises:
            ChainError: the chain is incomplete, or nothing confirmed it.
        """
        confirmations = len(
            {
                link.experiment.primitive
                for link in localization
                if link.experiment.verdict is Verdict.CONFIRMED
            }
        )
        try:
            return cls(
                symptom=symptom,
                exclusions=tuple(exclusions),
                localization=tuple(localization),
                mechanism=mechanism,
                complexity=dict(complexity),
                site=site,
                context=tuple(context),
                confidence=confidence_for(confirmations),
            )
        except ValueError as error:
            message = f"this is not a complete evidence chain: {error}"
            raise ChainError(message) from error

    def serialize(self) -> str:
        """Canonical JSON — sorted keys, fixed separators.

        AC 4's golden file compares against this. Stable so that a schema change
        is a visible diff rather than a surprise two agents downstream, and
        sorted so that two processes that built the same chain agree.
        """
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"

    def render(self) -> str:
        """The chain as a person reads it — the body of the pull request.

        Exclusions render **through S-8.5**, which is what carries their
        preconditions into the report. A reader who sees *not the database* has
        to see *under uniform fixtures at 10 to 1000* beside it, or the report
        has published F3's false fact.
        """
        ruled_out = [f"  {item.describe()}" for item in self.exclusions] or [
            "  (nothing was ruled out, which is itself worth a reader's attention)"
        ]
        instruments = sorted({link.experiment.primitive for link in self.confirmations})
        lines = [
            f"SYMPTOM\n  {self.symptom.describe()}",
            f"\nMECHANISM\n  {self.mechanism}",
            "\nLOCALIZATION",
            *(f"  {link.describe()}" for link in self.localization),
            "\nCOMPLEXITY",
            *(f"  {axis}: {growth.value}" for axis, growth in sorted(self.complexity.items())),
            f"\nSITE\n  {self.site.describe()}",
            "\nIMPLICATED FILES",
            *(f"  {item.path} — {item.reason}" for item in self.context),
            "\nRULED OUT",
            *ruled_out,
            (
                f"\nCONFIDENCE\n  {self.confidence:.3f}, from "
                f"{self.independent_confirmations} independent confirmation(s) "
                f"({', '.join(instruments)}).\n"
                "  This is a count expressed on a 0-1 scale, not a probability."
            ),
            f"\n  {RESIDUE}",
        ]
        return "\n".join(lines)

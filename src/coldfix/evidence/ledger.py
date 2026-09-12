"""A finding without an attached measurement cannot be built.

S-20.1. This is the property the whole system is for, so it is a **type**, not a
check somebody remembers to call.

An agent produces a `Claim`. A `Claim` cannot go in a report -- the report accepts
`Finding`, and the only way to obtain one is `Ledger.attest`, which verifies every
cited number against what the harness actually recorded. Forgetting to validate
is not a mistake that can be made here; there is no path from a claim to a report
that does not pass through the ledger.

**Exact, with no tolerance.** A cited 161 must be the 161 that was measured. A
near-miss is not a rounding difference, it is a number nobody took, and the
distance between 161 and 160 is exactly the distance between a finding and a
fabrication.

**A payoff without a proof is not a payoff.** Sampling says where time was spent;
only ablation says what removing it is worth. A claim with no ablation behind it
may still be reported -- as `suspected`, with no number -- because a suspicion
honestly labelled is useful and a suspicion wearing a percentage is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator


class EvidenceError(Exception):
    """A claim is not supported by what was measured."""


class UnknownMeasurementError(EvidenceError):
    """The claim cites a measurement the harness never took."""

    def __init__(self, measurement_id: str, known: Iterable[str]) -> None:
        super().__init__(
            f"the claim cites {measurement_id!r}, which is not a measurement this run "
            f"recorded. Known: {', '.join(sorted(known)) or 'none'}. A citation names "
            "something the harness measured; there is no other way to put a number in a report."
        )
        self.measurement_id = measurement_id


class FabricatedValueError(EvidenceError):
    """The claim cites a number that is not the number that was measured.

    Checked to the last digit on purpose. A tolerance here is a budget for being
    slightly wrong about the only thing this system promises to be right about.
    """

    def __init__(self, measurement_id: str, field: str, claimed: float, measured: object) -> None:
        super().__init__(
            f"the claim says {field}={claimed} citing {measurement_id}, which recorded "
            f"{field}={measured!r}. Cited values are checked exactly -- a number close to a "
            "measurement is still a number nobody measured."
        )
        self.field = field
        self.claimed = claimed


class MissingFieldError(EvidenceError):
    """The measurement exists; the field the claim reads off it does not."""

    def __init__(self, measurement_id: str, field: str, available: Iterable[str]) -> None:
        super().__init__(
            f"{measurement_id} has no {field!r}. It recorded: "
            f"{', '.join(sorted(available))}. A field that is absent from a measurement was "
            "not measured, and a claim may not read one that was not."
        )
        self.field = field


class UnprovenPayoffError(EvidenceError):
    """A number was attached to something nothing proved.

    The most valuable refusal here. A hot site with a percentage beside it reads
    as a finding, and until an ablation has removed the work and watched the cost
    go with it, it is a place the program happened to be when somebody looked.
    """

    def __init__(self, basis: Basis, payoff: float | None) -> None:
        super().__init__(
            f"a payoff of {payoff} was claimed on basis {basis.value!r}, which has no proof "
            "behind it. Only an ablation establishes what removing work is worth; a claim "
            "without one is reported as suspected, and carries no number."
        )
        self.basis = basis


class ProofIsNotAnAblationError(EvidenceError):
    """The proof cites a measurement that did not remove anything."""

    def __init__(self, measurement_id: str, kind: str) -> None:
        super().__init__(
            f"the proof cites {measurement_id}, which is a {kind} measurement. A proof is an "
            "ablation: work removed, and the cost observed to go with it."
        )


class UncitedClaimError(EvidenceError):
    """A claim that names no measurement at all is an opinion."""

    def __init__(self) -> None:
        super().__init__(
            "the claim cites no measurement at all. Every finding names at least one number "
            "the harness took; a claim with none is an opinion, however well argued."
        )


class Basis(StrEnum):
    """Where a claimed payoff came from. Never mixed, never sorted together."""

    ABLATION = "ablation"
    """Removed, and the cost went with it. The only basis that may be called proven."""

    SUSPECTED = "suspected"
    """A ratio, a count, a hot site. Worth chasing, worth nothing as a number."""


class Citation(BaseModel, frozen=True):
    """One number, and the measurement it was read from."""

    measurement_id: str
    field: str
    value: float


class Location(BaseModel, frozen=True):
    file: str
    line: int
    symbol: str = ""


class Proof(BaseModel, frozen=True):
    """An ablation: what it was before, what it was after, what that share is."""

    measurement_id: str
    before: float
    after: float
    share_removed: float


class Claim(BaseModel, frozen=True):
    """What an agent produces. **Cannot go in a report.**

    The structural rules are enforced here, at construction, so a malformed claim
    never reaches the ledger. The rules about *values* cannot be -- they need the
    measurements -- and that is what `attest` is for.
    """

    kind: str
    summary: str
    location: Location
    evidence: tuple[Citation, ...]
    basis: Basis = Basis.SUSPECTED
    payoff: float | None = None
    proof: Proof | None = None

    @model_validator(mode="after")
    def _a_number_needs_a_proof(self) -> Claim:
        if self.basis is Basis.ABLATION and self.proof is None:
            raise UnprovenPayoffError(self.basis, self.payoff)
        if self.payoff is not None and self.proof is None:
            raise UnprovenPayoffError(self.basis, self.payoff)
        return self

    @model_validator(mode="after")
    def _something_must_support_it(self) -> Claim:
        if not self.evidence:
            raise UncitedClaimError
        return self


class Finding(BaseModel, frozen=True):
    """A claim every number of which has been checked against a measurement.

    Only `Ledger.attest` returns one. A report accepts nothing else, so the check
    cannot be skipped by forgetting it.
    """

    claim: Claim
    attested_against: tuple[str, ...]

    @property
    def proven(self) -> bool:
        return self.claim.basis is Basis.ABLATION


class Ledger:
    """Every measurement this run took, and the only door to a report."""

    def __init__(self) -> None:
        self._records: dict[str, Mapping[str, Any]] = {}
        self._kinds: dict[str, str] = {}

    def record(self, measurement: Any) -> str:  # noqa: ANN401 - any measurement model
        """Keep a measurement so claims can be checked against it.

        **Dumped as JSON, not as Python.** A record holds tuples in Python mode --
        a command, an empty `not_measured` -- and a checkpoint is JSON (ADR 003),
        so those are values the state refuses to carry. Recording them in the form
        a checkpoint can hold is what lets `entries` be written to it and
        `restore` read it back identical; dumping one way and checkpointing
        another would leave a resumed ledger holding lists where this one holds
        tuples, and a citation checked against the wrong one.
        """
        data = measurement.model_dump(mode="json")
        identifier = str(data["measurement_id"])
        self._records[identifier] = _flatten(data)
        self._kinds[identifier] = type(measurement).__name__
        return identifier

    @property
    def known(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))

    def recorded(self, measurement_id: str) -> Mapping[str, Any] | None:
        """What was stored against an id, flattened, or `None`.

        For the audit, which needs to read the two runs an ablation compared
        rather than only check a number against them. Read-only: a caller cannot
        write through it, because the record is what claims are checked against
        and something that could edit it could edit the answer.
        """
        record = self._records.get(measurement_id)
        return None if record is None else dict(record)

    def entries(self) -> tuple[Mapping[str, Any], ...]:
        """Every record, in the shape a checkpoint carries. **S-28.3, ADR 183.**

        Id, kind and flattened fields -- exactly what `restore` reads back, and
        exactly what `attest` checks against. A checkpoint that carried less would
        restore a ledger that answers some citations and not others.
        """
        return tuple(
            {
                "measurement_id": identifier,
                "kind": self._kinds[identifier],
                "fields": dict(self._records[identifier]),
            }
            for identifier in self.known
        )

    def restore(self, entries: Iterable[Mapping[str, Any]]) -> None:
        """Rebuild from what a checkpoint carried.

        **A resumed run must be able to re-check what it already proved.** The
        finding audit's first attack re-attests every cited number against the
        ledger as it stands, which is right -- a `Finding` survives a rewind and
        the measurements behind it may not. But a resumed run starts with an empty
        ledger, so without this that attack fails *fatally*: `unsound`, the one
        verdict that does not send the run back for more evidence, for a finding
        nothing was ever wrong with.

        Nothing is re-measured. Running the workload again to rebuild this would
        spend the run's money to learn what it already wrote down, and would get
        slightly different numbers for it.

        Raises:
            EvidenceError: an entry is not one `entries` produced.
        """
        for entry in entries:
            try:
                identifier = str(entry["measurement_id"])
                fields = entry["fields"]
                kind = str(entry["kind"])
            except (KeyError, TypeError) as malformed:
                message = (
                    f"this is not a measurement a checkpoint wrote: {entry!r}. A restored ledger "
                    "is what a resumed run re-checks its findings against, so a record it cannot "
                    "read is refused rather than skipped"
                )
                raise EvidenceError(message) from malformed
            if not isinstance(fields, Mapping):
                message = f"{identifier} carries {type(fields).__name__} where its fields should be"
                raise EvidenceError(message)
            self._records[identifier] = dict(fields)
            self._kinds[identifier] = kind

    def attest(self, claim: Claim) -> Finding:
        """Check every cited number, or raise. There is no third outcome."""
        for citation in claim.evidence:
            self._check(citation)

        if claim.proof is not None:
            kind = self._kinds.get(claim.proof.measurement_id)
            if kind is None:
                raise UnknownMeasurementError(claim.proof.measurement_id, self.known)
            if "Ablation" not in kind:
                raise ProofIsNotAnAblationError(claim.proof.measurement_id, kind)
            for field, claimed in (
                ("share_removed", claim.proof.share_removed),
                ("before.wall.median", claim.proof.before),
                ("after.wall.median", claim.proof.after),
            ):
                self._check(
                    Citation(measurement_id=claim.proof.measurement_id, field=field, value=claimed)
                )

        cited = {citation.measurement_id for citation in claim.evidence}
        if claim.proof is not None:
            cited.add(claim.proof.measurement_id)
        return Finding(claim=claim, attested_against=tuple(sorted(cited)))

    def _check(self, citation: Citation) -> None:
        record = self._records.get(citation.measurement_id)
        if record is None:
            raise UnknownMeasurementError(citation.measurement_id, self.known)
        if citation.field not in record:
            raise MissingFieldError(citation.measurement_id, citation.field, record)
        measured = record[citation.field]
        if isinstance(measured, bool) or not isinstance(measured, (int, float)):
            raise FabricatedValueError(
                citation.measurement_id, citation.field, citation.value, measured
            )
        if float(measured) != citation.value:
            raise FabricatedValueError(
                citation.measurement_id, citation.field, citation.value, measured
            )


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested measurements addressed as `before.wall.median`.

    A citation names one number. Where that number lives inside another model --
    an ablation holds two whole measurements -- the path is the name, so a claim
    can cite the elapsed time of the run before the stub without the ledger
    having to know what an ablation is.
    """
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat

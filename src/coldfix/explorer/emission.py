"""Handing a workload on, with the evidence attached and unable to disagree.

Epic 7, S-7.9. The Explorer's last act. Everything before it established that a
repository can be stood up, reached, seeded and driven; this is what leaves the
epic and becomes S-8's input, S-5.1's cache key and S-8.4's log entry.

**Nothing here calls a model.** Copying a computed string into a document and
comparing it against a recomputation is a function.

`03-agents.md` §2.4 says why the evidence exists in one line — *`evidence_of_work`
is mandatory and exists to make "it ran but did nothing" structurally
unreportable as success* — and this module is where "structurally" has to become
true of a document rather than of an object in memory.

**The problem the artifact alone does not solve.** S-4.1 made `work_verified` a
property with no field behind it, which is what stops an agent writing it into a
`Workload`. But a property is not serialized: dump the artifact and the verdict is
simply absent, so a document that must carry the evidence has to carry a *copy* —
and a copy is exactly the thing an agent could edit.

**So the copy is checked against the recomputation.** The emitted document holds
`work_verified` and `evidence_of_work` as ordinary fields, and validating one
recomputes both from the observations it contains and **refuses any document
whose stored verdict disagrees**. Tampering with the evidence does not produce a
more convincing workload; it produces a document that will not load. The stored
copy is therefore mandatory *and* powerless, which is the pair AC 2 asks for.

**A reset method is claimed by the artifact and proved by a type.** S-4.1's
`Workload.reset_method` is a `ResetStrategy` — a name, which any caller can
write. AC 3 asks that the method be verified before emission, so `emit` requires
S-2.7's `VerifiedReset`, whose constructor refuses a report that did not pass.
There is no way to emit while holding only the name, and a `VerifiedReset` for a
different strategy than the artifact claims is refused rather than silently
preferred: an artifact whose reset method was verified for something else records
a guarantee nobody obtained.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from coldfix.explorer.work import Verification, WorkVerificationError, accept
from coldfix.sandbox.reset import ResetStrategy
from coldfix.sandbox.verification import VerifiedReset
from coldfix.screening.workload import Workload


class EmissionError(Exception):
    """The workload could not be emitted, or a document could not be trusted."""


_STRICT = ConfigDict(frozen=True, extra="forbid")


class EmittedWorkload(BaseModel):
    """A workload, its evidence, and the proof its reset method was verified.

    The envelope rather than the artifact. `Workload` is what survives into an
    experiment log and a replay key; this is what crosses the boundary out of the
    Explorer, and it exists because two of the three things a reader needs —
    whether the harness verified the work, and whether the reset was proved —
    are computed or held outside the artifact and would otherwise be lost the
    moment it was written down.

    **Validating one recomputes the verdict.** The two evidence fields are
    required and are checked against the observations in the workload they
    describe, so a document is either consistent or refused.
    """

    model_config = _STRICT

    workload: Workload

    work_verified: bool
    """Whether the harness established that this does real work.

    A stored copy of a computed property, present because a property does not
    serialize and a document that omitted it would let *unverified* read as
    *not yet asked*. Powerless: `_recomputed` refuses any value the observations
    do not support.
    """

    evidence_of_work: str = Field(min_length=1)
    """The harness's reasoning, in the form a reader can check.

    Mandatory, per `03-agents.md` §2.4. Prose rather than a code because every
    way of failing calls for a different action, and a reader deciding whether to
    trust this workload needs the sentence rather than the boolean.
    """

    reset_strategy: ResetStrategy
    """What returned the database to its baseline between scale points."""

    reset_cycles: int = Field(gt=0)
    """How many reset cycles S-2.7 proved it over.

    Recorded because *verified* is not a property of a strategy, it is a property
    of a strategy on a project: S-0.5 had rollback alone pass its own check and
    fail 10/10 on sequences. A reader comparing two workloads needs to know
    whether the guarantee behind them was established over three cycles or ten.
    """

    @model_validator(mode="after")
    def _recomputed(self) -> EmittedWorkload:
        """Refuse a document whose evidence its own observations do not support.

        This is the whole of AC 2's second half. The stored fields are a copy of
        something the harness computed, so the copy is worth exactly as much as
        the check that it still matches — and without the check, editing two
        strings in a JSON file would turn a rejected workload into an accepted
        one at the point where the artifact leaves the only code that knows how
        to judge it.
        """
        if self.work_verified != self.workload.work_verified:
            message = (
                f"this document says work_verified={self.work_verified}, and the observations "
                f"it carries say {self.workload.work_verified}. The verdict is computed from the "
                "measurements, so the two can only disagree if one of them was edited"
            )
            raise ValueError(message)

        if self.evidence_of_work != self.workload.work_evidence:
            message = (
                "the evidence in this document is not the evidence its observations produce. "
                f"Stored: {self.evidence_of_work[:120]!r}. Computed: "
                f"{self.workload.work_evidence[:120]!r}"
            )
            raise ValueError(message)

        if self.reset_strategy is not self.workload.reset_method:
            message = (
                f"the document was verified for {self.reset_strategy.value} and the workload "
                f"claims {self.workload.reset_method.value}. A workload whose reset method was "
                "proved for a different mechanism records a guarantee nobody obtained"
            )
            raise ValueError(message)

        return self

    def document(self) -> Mapping[str, Any]:
        """The JSON-ready form. What S-8.4 appends and S-17.2 can publish."""
        return self.model_dump(mode="json")

    def describe(self) -> str:
        return "\n".join(
            [
                f"{self.workload.id} → {self.workload.entry_point}",
                f"  fixture: {self.workload.fixture.entity} × {len(self.workload.observations)} "
                f"scale point(s), {self.workload.fixture.distribution.value}",
                f"  reset: {self.reset_strategy.value}, verified over {self.reset_cycles} cycle(s)",
                f"  {self.evidence_of_work}",
            ]
        )


def emit(verification: Verification, *, reset: VerifiedReset) -> EmittedWorkload:
    """AC 1 to 3: hand the workload on, or refuse to.

    Three things have to hold and none of them is this function's opinion. The
    work must have been verified — `accept` decides that, from measurements the
    harness took. The reset must have been *proved*, which is what holding a
    `VerifiedReset` means, because its constructor refuses a report that did not
    pass. And the proof must be about the mechanism the artifact claims.

    **There is no parameter here through which a claim could enter either**,
    which is S-7.8's construction one step further along: the caller supplies a
    measurement and a proof, and the document is assembled from them.

    Raises:
        EmissionError: the workload does not do demonstrable work, or the reset
            proof is for a different strategy than the artifact records.
    """
    try:
        workload = accept(verification)
    except WorkVerificationError as error:
        message = (
            f"refusing to emit {verification.workload.id}: it did not verify.\n{error}\n"
            "`03-agents.md` §2.4: evidence_of_work exists to make 'it ran but did nothing' "
            "structurally unreportable as success, and emitting this would be that report"
        )
        raise EmissionError(message) from error

    if reset.strategy is not workload.reset_method:
        message = (
            f"{workload.id} records {workload.reset_method.value} as its reset method and the "
            f"proof offered is for {reset.strategy.value}. Verification is a property of a "
            "strategy on a project, not of a strategy, so a proof of the wrong one proves "
            "nothing about this workload"
        )
        raise EmissionError(message)

    return EmittedWorkload(
        workload=workload,
        work_verified=workload.work_verified,
        evidence_of_work=workload.work_evidence,
        # The proved one, not the claimed one — though the guard above has just
        # established that they are the same value, so this is a statement of
        # intent rather than a behaviour any test can distinguish. Recorded
        # because a sabotage swapping it changed no outcome: the pair is only
        # separable if the guard goes too, and the guard is what produces the
        # message worth reading.
        reset_strategy=reset.strategy,
        reset_cycles=reset.report.cycles,
    )


def read_document(document: Mapping[str, Any] | str) -> EmittedWorkload:
    """Load an emitted workload, refusing one whose evidence does not hold up.

    The reading half of the same guarantee. A document that has been edited to
    claim verification it did not earn fails here rather than downstream, and
    fails with the disagreement named.

    Raises:
        EmissionError: the document is not valid JSON, is not a valid emission,
            or carries evidence its own observations do not produce.
    """
    if isinstance(document, str):
        try:
            parsed: Any = json.loads(document)
        except json.JSONDecodeError as error:
            message = f"the emitted document is not JSON: {error}"
            raise EmissionError(message) from error
    else:
        parsed = document

    try:
        return EmittedWorkload.model_validate(parsed)
    except ValueError as error:
        message = f"this document cannot be trusted as an emitted workload:\n{error}"
        raise EmissionError(message) from error

"""A real evidence chain, for tests that need one and are not about building one.

Three files wanted the same thing — the gate renders a chain, the repair adapter
validates one, and Epic 8's own tests build them — and the third copy is what
makes it a fixture rather than a coincidence.

**Real rather than a stub**, because every consumer validates it: `EvidenceChain`
recomputes the confidence from the confirmations, `LocalizationLink` refuses a
link with no measurement, and `Exclusion` carries the conditions it holds under. A
stub would prove that the stub validates.
"""

from __future__ import annotations

from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.primitives.scaling import Distribution

SCALES = [10, 100, 1000]


def an_evidence_chain() -> EvidenceChain:
    """One confirmed finding: the database excluded, the serializer localized."""
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design=f"scaling.volume(scales={SCALES})",
        measurement={"db.query": 2.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 2 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds.share_removed": 0.89, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    conditions = Conditions.of(
        fixture_shape=Distribution.UNIFORM.value,
        platform="x86_64-linux",
        concurrency=1,
        scales=SCALES,
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=conditions)],
        localization=[
            LocalizationLink(
                scope="BookSerializer.to_representation",
                experiment=confirmed,
                share_of_cost=0.89,
                basis="8.24s baseline against 0.90s ablated",
            )
        ],
        mechanism="the serializer re-renders the author for every book",
        complexity={"rows": Growth.LINEAR},
        site=Site(path="shop/serializers.py", first_line=41, last_line=52),
        context=[Implicated(path="shop/models.py", reason="declares the Author relation")],
    )

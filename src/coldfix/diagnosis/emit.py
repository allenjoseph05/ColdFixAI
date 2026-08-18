"""The joins Epic 8 needed and did not have.

Written at the epic's composition check. Every module in `diagnosis/` passed its
own tests and the epic **could not perform its own sentence**: take a screened
workload, investigate it, and emit either an evidence chain or a partial chain.
Three things were missing, and all three are the shape Epic 7 recorded — a value
one story produces and another consumes, where nothing in either story's tests
holds both ends.

**An investigation's conditions had no producer.** S-8.5 requires fixture shape,
platform, concurrency and scales on every exclusion, and every caller built them
by hand — including the tests, which is why nothing noticed. `Workload` already
carries the first and the last: `fixture.distribution` is the shape the data was
actually seeded at, and `observations` record the scales it was actually driven
at. A hand-built `Conditions` can say `uniform` while the recipe says
`long_tail`, and an exclusion recorded under a shape that was never used is
**permanently and wrongly live** — F3, reintroduced at the join that S-8.5 exists
to close.

**A symptom had no producer either**, and `EvidenceChain` and `PartialChain` both
require one. It is screening's observation, not the investigation's: the
investigation did not measure the symptom, it was handed it.

**S-8.6 was unreachable.** A confirmed investigation had no path to the artifact
the epic exists to produce — `EvidenceChain` could be constructed by hand in a
test and by nothing in the system. That is Epic 7's *AC satisfied in isolation
and unreachable in practice*, and it is the more dangerous half of that pair,
because the criterion reads as met.

**What this deliberately does not do.** It does not invent a mechanism, a site or
the implicated files. Those come from the agent and from S-3.9's localization,
and a join that manufactured them to satisfy a constructor would be inventing the
parts of a finding that are hardest to check — the refusal `loop.confirming_links`
already makes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import (
    ChainError,
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, current_platform
from coldfix.diagnosis.log import Verdict
from coldfix.diagnosis.loop import Investigation
from coldfix.screening.workload import Observation, Workload

DEFAULT_CONCURRENCY = 1.0
"""What a workload is driven at unless a load primitive says otherwise.

Stated rather than assumed: `Workload` records no concurrency because S-4.1's
workloads are driven serially, and S-3.12's load primitive is the thing that
changes it. A caller running under load passes its own."""


def conditions_for(
    workload: Workload,
    *,
    concurrency: float | Sequence[float] = DEFAULT_CONCURRENCY,
    platform: str | None = None,
) -> Conditions:
    """The conditions an investigation of `workload` actually runs under.

    Read from the workload rather than described alongside it, which is the same
    argument S-3.1 makes for reading a primitive's signature off its callable:
    two statements of one fact drift, and here the one that drifts decides
    whether a correct exclusion is ever reopened.

    Raises:
        ChainError: the workload has no observations, so there are no scales it
            was driven at — and an exclusion whose scale range is invented is one
            that reopens, or fails to, for a reason nobody measured.
    """
    if not workload.observations:
        message = (
            f"workload {workload.id!r} has no observations, so nothing records the scales it was "
            "driven at. Conditions built without them would give every exclusion a scale envelope "
            "that no experiment established"
        )
        raise ChainError(message)

    return Conditions.of(
        fixture_shape=workload.fixture.distribution.value,
        platform=platform if platform is not None else current_platform(),
        concurrency=concurrency,
        scales=[float(item.scale) for item in workload.observations],
    )


def symptom_for(observation: Observation, metric: str) -> Symptom:
    """The symptom a chain reports, taken from what screening measured.

    The investigation did not observe this — it was handed it — so it comes from
    an `Observation` rather than from anything in `diagnosis/`.

    Raises:
        ChainError: that observation did not measure that metric.
    """
    if metric not in observation.metrics:
        measured = ", ".join(sorted(observation.metrics)) or "nothing"
        message = (
            f"the observation at scale {observation.scale} did not measure {metric!r}; it "
            f"measured {measured}. A symptom quoting a metric nobody took is the first "
            "non-negotiable broken at the top of the report"
        )
        raise ChainError(message)

    return Symptom(
        metric=metric,
        magnitude=float(observation.metrics[metric]),
        at_scale=float(observation.scale),
    )


def chain_from(  # noqa: PLR0913 - the investigation supplies the measured half and
    # the caller the interpreted half; every parameter is one or the other, and
    # none is derivable from the rest.
    investigation: Investigation,
    *,
    symptom: Symptom,
    mechanism: str,
    complexity: Mapping[str, Growth],
    site: Site,
    context: Sequence[Implicated],
    shares: Mapping[int, tuple[str, float, str]],
) -> EvidenceChain:
    """Assemble the chain a confirmed investigation supports. **The missing path.**

    `shares` maps an experiment's index to its `(scope, share_of_cost, basis)`.
    Those come from the primitive that ran — an ablation knows what fraction
    disappeared with the component — and the loop does not carry them, so they
    are supplied rather than invented here.

    Everything the investigation *measured* comes from the investigation: the
    confirming experiments with their measurements, and the exclusions with the
    conditions they hold under. `EvidenceChain` derives the confidence from those,
    so nothing here chooses it.

    Raises:
        ChainError: nothing was confirmed — in which case the investigation owes
            a partial chain instead — or a confirming experiment has no share
            recorded for it.
    """
    confirming = [
        step.experiment for step in investigation.steps if step.verdict is Verdict.CONFIRMED
    ]
    if not confirming:
        message = (
            "this investigation confirmed nothing, so it has no cause to report. What it has is a "
            "partial chain — `Investigation.partial_chain` — and `00-BRIEF.md` §9 ships that as an "
            "answer rather than as a failure"
        )
        raise ChainError(message)

    missing = sorted(item.index for item in confirming if item.index not in shares)
    if missing:
        message = (
            f"experiment(s) {missing} confirmed the cause and no share of cost was supplied for "
            "them. The primitive that ran knows what fraction disappeared with the component; a "
            "chain that guessed it would be putting a number nobody measured under a finding"
        )
        raise ChainError(message)

    localization = []
    for experiment in confirming:
        scope, share, basis = shares[experiment.index]
        localization.append(
            LocalizationLink(scope=scope, experiment=experiment, share_of_cost=share, basis=basis)
        )

    return EvidenceChain.assemble(
        symptom=symptom,
        exclusions=investigation.exclusions.exclusions,
        localization=localization,
        mechanism=mechanism,
        complexity=complexity,
        site=site,
        context=context,
    )

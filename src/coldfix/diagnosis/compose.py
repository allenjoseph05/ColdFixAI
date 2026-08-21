"""Epic 8, composed: one confirmed investigation, all the way to an evidence chain.

Epic 8, S-8.11. The epic's composition check performs this sequence and performs
it **in a test** — the same thing S-7.13 found in Epic 7, and the reason S-12.7
has no `investigate` node to build. `run_investigation` is in `src/` and stops
deliberately short of a chain; `chain_from` is in `src/` and was called from
`tests/diagnosis/test_diagnosis_composed.py` and nowhere else.

**What the loop refuses to do is not what nobody should do.** `confirming_links`
says it plainly: *the loop does not build the chain, and that is a refusal rather
than an omission* — a symptom comes from screening, a share of cost from the
primitive, and a mechanism from the agent, so a loop that manufactured them would
be inventing the parts of a finding that are hardest to check. This module is
where those four sources meet, and it takes each from its owner rather than
inventing any of them:

| Part | Owner |
|---|---|
| symptom | screening's observation, via `symptom_for` |
| complexity | screening's growth table, passed in |
| exclusions | the investigation's own register |
| localization | the primitive that ran, via `shares_from` |
| mechanism, site, context | the Diagnostician, via `explain` |

**Nothing here decides whether the mechanism follows from the evidence.**
`08-audit.md` names that as what E9's finding audit exists for, and records that
schema validation and adversarial review address different failure modes. This
assembles; E9 attacks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from coldfix.bench.stats import Growth
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import EvidenceChain, Symptom
from coldfix.diagnosis.emit import chain_from, symptom_for
from coldfix.diagnosis.explain import Explanation, explain, shares_from
from coldfix.diagnosis.loop import Investigation, confirming_links
from coldfix.llm.client import ModelClient
from coldfix.screening.workload import Workload


def chain_of(  # noqa: PLR0913 - the investigation, the workload, the metric, the
    # growth table and the source are five facts from four different owners, plus
    # the session, the client and the two measured token counts. Bundling them
    # would invent a type whose only purpose is to be unpacked here.
    session: Session,
    client: ModelClient,
    *,
    investigation: Investigation,
    workload: Workload,
    metric: str,
    complexity: Mapping[str, Growth],
    source: str,
    measured_prefix_tokens: int,
    measured_prompt_tokens: int,
    finding_id: str | None = None,
) -> EvidenceChain:
    """Assemble the chain a confirmed investigation supports. **The missing path.**

    `metric` names which of the workload's measurements the symptom quotes, and
    it is required for `symptom_for`'s reason: a symptom quoting a metric nobody
    measured is the first non-negotiable broken at the top of the report.

    **The order matters and it is not arbitrary.** The shares are read *before*
    the model is asked, so an investigation whose confirmations carry no measured
    share fails without spending a call — the loop-boundary defect
    `shares_from` names is a fact about the log, and discovering it after paying
    for an explanation would be paying to learn something already knowable.

    Raises:
        ExplanationError: nothing was confirmed, or a confirming experiment
            carries no share of cost.
        UnexplainableError: no tier produced an explanation the schema accepts.
        ChainError: the symptom quotes a metric nobody measured, or the chain
            itself does not hold together.
    """
    confirming = confirming_links(investigation)
    shares = shares_from(confirming)
    symptom = symptom_for(workload.observations[-1], metric)

    explained = explain(
        session,
        client,
        symptom=symptom,
        confirming=confirming,
        exclusions=[item.describe() for item in investigation.exclusions.exclusions],
        source=source,
        measured_prefix_tokens=measured_prefix_tokens,
        measured_prompt_tokens=measured_prompt_tokens,
        finding_id=finding_id,
    )

    return assemble_with(
        investigation,
        symptom=symptom,
        complexity=complexity,
        shares=shares,
        explanation=explained.value,
    )


def assemble_with(
    investigation: Investigation,
    *,
    symptom: Symptom,
    complexity: Mapping[str, Growth],
    shares: Mapping[int, tuple[str, float, str]],
    explanation: Explanation,
) -> EvidenceChain:
    """The assembly, with the model's half already in hand.

    Separate from `chain_of` so the join can be tested without a client, and so a
    caller that already has an explanation — a rewind, a re-assembly after a
    correction — does not have to buy a second one.
    """
    return chain_from(
        investigation,
        symptom=symptom,
        mechanism=explanation.mechanism,
        complexity=complexity,
        site=explanation.site,
        context=list(explanation.context),
        shares=shares,
    )


__all__: Sequence[str] = ("assemble_with", "chain_of")

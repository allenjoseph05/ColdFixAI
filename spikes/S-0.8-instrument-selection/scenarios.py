"""Scenarios for S-0.8, built from measurements the E0 spikes actually took.

Every number here was recorded by a spike, not invented. That matters twice
over: the model is being asked to reason about real measurement shapes, and the
correct answer is known because a human already worked it out with the
measurements in hand.

## Every scenario carries a trap

A scenario a model can pass by pattern-matching teaches nothing. Each one below
has a plausible wrong answer that a naive detector reaches for:

- lots of queries looks like an N+1 even when the count is constant
- an unchanged query count looks like "not the database" even when the payload
  grew five-fold
- a separable timing delta looks like a finding even when nothing else moved

The third is the one that matters most. `CLAUDE.md` makes null results a
project invariant — *"Screened 9 workloads, nothing found" ships as an answer.
Never manufacture a finding.* A model that produces a finding from noise has
violated it, and no downstream verification catches that as cleanly as refusing
to start does.

## Traps are scored on an enum, not on prose

The first version of this scored traps by searching the model's prose for
forbidden substrings — `"n+1"` on the decoy, and so on. Calibrating the scorer
before spending anything on a real run showed it was broken: the *correct*
answer to the decoy is "query count is constant, so **this is not an N+1**",
which contains the forbidden substring and scored as falling into the trap.
Substring matching cannot see negation, and the decoy — the sharpest scenario in
the set — would have scored 0% however well the model reasoned.

So the model states its diagnosis as one of `DIAGNOSES`, and a trap is a
forbidden enum value. Nothing about the check depends on how a sentence is
phrased.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The instruments the Diagnostician may choose between, from 01-primitives.md.
INSTRUMENTS = (
    "ablation",
    "proportional_perturbation",
    "scaling",
    "observation",
    "temporal_bisection",
    "fault_injection",
    "substitution",
    "none_report_no_finding",
)

# What the evidence shows. Scored as an enum so that "this is NOT an N+1" and
# "this IS an N+1" are distinguishable — see the module docstring.
DIAGNOSES = (
    "n_plus_one",  # query count grows with rows returned
    "over_fetch",  # constant query count, payload larger than needed
    "constant_per_request_overhead",  # high but flat cost, not scaling with rows
    "cost_outside_database",  # database is not where the time goes
    "insufficient_evidence",  # a further experiment is needed to say
    "no_defect_found",  # the evidence does not support a defect
)


@dataclass(frozen=True)
class Scenario:
    """One recorded situation, with its known-correct next move."""

    name: str
    source: str
    evidence: str

    # Instruments that would be a defensible next experiment.
    acceptable_instruments: tuple[str, ...]

    # Whether the evidence justifies reporting a performance finding at all.
    finding_warranted: bool

    # Diagnoses the evidence supports.
    acceptable_diagnoses: tuple[str, ...]

    # The traps: diagnoses that are plausible here and that the evidence does
    # not support. Scored as enum membership, never as a substring of prose.
    forbidden_diagnoses: tuple[str, ...] = ()

    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="real_n_plus_one",
        source="S-0.4, django-helpdesk /api/tickets/",
        evidence="""\
GET /api/tickets/?page_size=100 against 503 tickets / 3004 followups.

  queries        1193
  response       429,071 bytes
  median time    1454.73 ms

Query counts grouped by table:
  helpdesk_followupattachment   586
  helpdesk_customfield          504
  helpdesk_followup             100
  session + auth + ticket         3

Returning 25 tickets instead of 100 gives 305 queries; returning 10 gives 131.""",
        acceptable_instruments=("ablation", "scaling"),
        finding_warranted=True,
        acceptable_diagnoses=("n_plus_one",),
        notes=(
            "The control case. Query count grows with rows returned, which is the "
            "N+1 signature. A model that cannot get this one right tells us nothing "
            "about the harder scenarios."
        ),
        tags=("control", "n+1"),
    ),
    Scenario(
        name="decoy_fixed_floor",
        source="S-0.3 netbox interfaces endpoint; fixture decoy in tests/fixtures",
        evidence="""\
An endpoint issues a large number of queries per request.

  rows returned    10     25     50    100    200
  queries          37     37     37     37     37
  median time    412ms  418ms  441ms  467ms  502ms

Guard counters: response bytes grow roughly linearly with rows returned.
The 37 queries are 35 distinct aggregate lookups plus 2 for the row set.""",
        acceptable_instruments=(
            "proportional_perturbation",
            "observation",
            "none_report_no_finding",
        ),
        finding_warranted=False,
        acceptable_diagnoses=(
            "constant_per_request_overhead",
            "no_defect_found",
            "insufficient_evidence",
        ),
        forbidden_diagnoses=("n_plus_one",),
        notes=(
            "The sharp one. 37 queries is a lot in absolute terms and it is NOT an "
            "N+1 — the count does not move with rows returned. A detector keying on "
            "'many queries' reports a defect that is not there. Worse, 'fixing' it "
            "is the metastability trap 00-BRIEF.md section 4 warns about: removing "
            "the constant work would improve every metric measured while removing "
            "slack. Ablation is not forbidden here, but reporting an N+1 is."
        ),
        tags=("trap", "decoy"),
    ),
    Scenario(
        name="over_fetch_invisible_to_query_count",
        source="S-0.7 fixture, list_titles_over_fetching vs list_titles_narrow",
        evidence="""\
Two endpoints return identical output. Both issue exactly one query.

                        endpoint A     endpoint B
  queries                        1              1
  rows returned                500            500
  cells returned              2500            500
  response bytes           184,220         31,940
  median time              128.4 ms        41.7 ms

Query count is flat across dataset sizes for both.""",
        acceptable_instruments=("ablation", "observation", "scaling"),
        finding_warranted=True,
        acceptable_diagnoses=("over_fetch",),
        forbidden_diagnoses=("cost_outside_database", "no_defect_found"),
        notes=(
            "Query count cannot distinguish these at all — it is 1 for both. The "
            "defect is visible only in the guard counter. A model that concludes "
            "'query count is flat, therefore not the database' has drawn the "
            "standard inference from an insufficient measurement. This is the "
            "exact asymmetry S-0.4 hit from the other direction, where two stub "
            "strategies were indistinguishable on timing and differed 6x in payload."
        ),
        tags=("trap", "over-fetch", "guard-counter"),
    ),
    Scenario(
        name="post_ablation_residual",
        source="S-0.4, after stubbing followup_set",
        evidence="""\
The previous experiment ablated the followup_set serializer field.

                     baseline      ablated
  queries                1193          507
  median time        1454.73ms     434.64ms
  Cliff's delta                     -1.000  (no overlap, 20 samples each)

Residual 507 queries by table:
  helpdesk_customfield   504
  session + auth + ticket  3

Detection floor for this endpoint was measured at ~20 ms.""",
        acceptable_instruments=("ablation", "scaling"),
        finding_warranted=True,
        acceptable_diagnoses=("n_plus_one",),
        forbidden_diagnoses=("no_defect_found",),
        notes=(
            "The localization loop. Removing the dominant component exposed a "
            "second, independent N+1 underneath it — 504 customfield queries, one "
            "per ticket, invisible while the first dominated. The correct move is "
            "to keep going, not to stop at the first finding. A model that reports "
            "the 1020ms result and concludes the investigation has left a second "
            "defect on the table."
        ),
        tags=("localization", "residual"),
    ),
    Scenario(
        name="flat_queries_time_grows",
        source="Synthetic, matching the S-8.7 acceptance criterion",
        evidence="""\
  rows returned     100      200      400      800
  queries             4        4        4        4
  response bytes  41,900   83,100  166,400  332,800
  median time     122ms    241ms    486ms    971ms

Database time, measured separately, is 11ms at every size.""",
        acceptable_instruments=("ablation", "proportional_perturbation", "observation"),
        finding_warranted=True,
        acceptable_diagnoses=("cost_outside_database", "insufficient_evidence"),
        forbidden_diagnoses=("n_plus_one",),
        notes=(
            "This is S-8.7's stated demo, offered here as a selection problem "
            "rather than an end-to-end one: query count is flat and database time "
            "is constant, so the cost is not in the database, and the next "
            "instrument must be a different one. A model that stays on query "
            "counting has failed to switch instruments, which is the behaviour the "
            "whole architecture is justified by."
        ),
        tags=("instrument-switch", "thesis"),
    ),
    Scenario(
        name="noise_no_finding",
        source="S-0.4 calibration, null trials",
        evidence="""\
Two conditions measured, 20 interleaved repetitions each, warm-up discarded.

  condition A    median 348.3 ms    CV 6.62%
  condition B    median 340.8 ms    CV 4.24%

  median shift   -7.4 ms
  Mann-Whitney   p = 0.008
  Cliff's delta  -0.48

Guard counters, both conditions:
  queries          507        507
  response bytes   71,758     71,758
  rows returned    100        100

Over 12 null trials on this endpoint — the identical condition compared against
itself — the largest spurious shift observed was 12.76 ms.""",
        acceptable_instruments=("none_report_no_finding",),
        finding_warranted=False,
        acceptable_diagnoses=("no_defect_found", "insufficient_evidence"),
        forbidden_diagnoses=(
            "n_plus_one",
            "over_fetch",
            "constant_per_request_overhead",
            "cost_outside_database",
        ),
        notes=(
            "The most important scenario in the set. p < 0.01 and a medium effect "
            "size make this look separable, and the 7.4 ms shift sits well inside "
            "the 12.76 ms envelope the null trials produced. Every guard counter is "
            "identical, so nothing physical changed. The correct answer is no "
            "finding. A model that produces one here has violated the invariant "
            "that null results are valid output, and it did so from real numbers "
            "that a careless reading calls significant."
        ),
        tags=("trap", "null-result", "invariant"),
    ),
)


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "conclusion": {
            "type": "string",
            "description": (
                "What the evidence supports, in one or two sentences. State what is "
                "NOT supported if that is the substantive point."
            ),
        },
        "diagnosis": {
            "type": "string",
            "enum": list(DIAGNOSES),
            "description": (
                "What the evidence shows. Choose no_defect_found when the evidence "
                "does not support a defect, and insufficient_evidence when a further "
                "experiment is needed before saying."
            ),
        },
        "finding_warranted": {
            "type": "boolean",
            "description": (
                "True only if the evidence supports reporting a performance finding. "
                "False if the correct output is 'nothing found' or 'more evidence "
                "needed'."
            ),
        },
        "next_instrument": {
            "type": "string",
            "enum": list(INSTRUMENTS),
            "description": "The single next experiment to run, or none_report_no_finding.",
        },
        "why": {
            "type": "string",
            "description": "Why that instrument, referencing the specific measurement.",
        },
    },
    "required": [
        "conclusion",
        "diagnosis",
        "finding_warranted",
        "next_instrument",
        "why",
    ],
    "additionalProperties": False,
}

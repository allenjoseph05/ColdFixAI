"""The artifact an agent cannot lie to, and the half of it that never travels.

S-4.1. Two things decide this file.

`08-audit.md` F6 found that the Explorer's success criterion was self-judged and
that the agent is incentivised to say yes, because saying yes completes its task.
So the tests that matter here are the ones that **attempt to set `work_verified`**
and fail — there is no field, no setter and no override, and a workload that has
not earned it says why in a sentence naming the next action.

The second is that the artifact crosses a node boundary and the callables cannot.
`Workload` is what survives serialization; `BoundWorkload` is what runs; the
constructor of the second checks it against the claims of the first.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from coldfix.primitives.counters import DB_QUERY
from coldfix.primitives.measurement import SECONDS
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetMechanism, ResetNotPreparedError, ResetStrategy
from coldfix.sandbox.verification import VerificationReport, VerifiedReset
from coldfix.screening.workload import (
    MINIMUM_SCALE_RATIO,
    RESPONSE_BYTES,
    BoundWorkload,
    FixtureRecipe,
    Observation,
    Workload,
    WorkloadError,
)
from fixtures.workloads import HELPDESK_TICKETS

RECIPE = FixtureRecipe(
    entity="ticket",
    per_parent=6,
    distribution=Distribution.UNIFORM,
    source="a factory",
    seed=7,
)


def observed(scale: int, queries: float, response_bytes: float, seconds: float) -> Observation:
    return Observation(
        scale=scale,
        metrics={DB_QUERY: queries, RESPONSE_BYTES: response_bytes, SECONDS: seconds},
    )


def a_workload(*observations: Observation, **overrides: object) -> Workload:
    arguments: dict[str, object] = {
        "id": "api.tickets.list",
        "description": "the ticket list endpoint",
        "entry_point": "GET /api/tickets/",
        "fixture": RECIPE,
        "reset_method": ResetStrategy.SNAPSHOT_RESTORE,
        "observations": observations,
    }
    arguments.update(overrides)
    return Workload(**arguments)  # type: ignore[arg-type]


# A workload that does more of everything when there is more data: ten times the
# tickets, more queries, four times the payload, three times the time.
DOES_REAL_WORK = (
    observed(scale=10, queries=21, response_bytes=4_000, seconds=0.08),
    observed(scale=100, queries=201, response_bytes=41_000, seconds=0.62),
)

# The same endpoint if it were a stub: identical at both volumes.
STUB_ROUTE = (
    observed(scale=10, queries=3, response_bytes=180, seconds=0.004),
    observed(scale=100, queries=3, response_bytes=180, seconds=0.004),
)


class NothingReset(ResetMechanism):
    """A reset double, so a bound workload can be built without a database."""

    strategy = ResetStrategy.SNAPSHOT_RESTORE

    def __init__(self, strategy: ResetStrategy = ResetStrategy.SNAPSHOT_RESTORE) -> None:
        self.strategy = strategy  # type: ignore[misc]
        self.prepared = False

    def prepare(self) -> None:
        self.prepared = True

    def begin(self) -> None:
        if not self.prepared:
            raise ResetNotPreparedError(self.strategy)

    def reset(self) -> None:
        return None


def verified(strategy: ResetStrategy = ResetStrategy.SNAPSHOT_RESTORE) -> VerifiedReset:
    return VerifiedReset(
        mechanism=NothingReset(strategy),
        report=VerificationReport(strategy=strategy, cycles=10),
    )


# ---------------------------------------- AC 1: the six members, in two halves


def test_the_artifact_carries_the_data_and_the_binding_carries_the_callables() -> None:
    """AC 1, split. `02-architecture.md` §1.3 lists six members; three are
    callables and cannot cross the node boundary this artifact exists to cross,
    so they live on the object an adapter builds locally."""
    descriptor = a_workload(*DOES_REAL_WORK)

    bound = BoundWorkload(
        descriptor,
        invoke=lambda: "a response",
        scale=lambda n: n,
        reset=verified(),
    )

    assert descriptor.fixture.entity == "ticket"
    assert descriptor.reset_method is ResetStrategy.SNAPSHOT_RESTORE
    assert descriptor.largest is not None
    assert bound.invoke() == "a response"
    assert bound.scale(50) == 50
    assert bound.id == descriptor.id


def test_the_artifact_survives_a_round_trip_through_json() -> None:
    """The whole reason for the split. S-5.1 keys a cache on this and S-8.4
    appends it to a log, and a function object survives neither."""
    descriptor = a_workload(*DOES_REAL_WORK)

    restored = Workload.model_validate_json(descriptor.model_dump_json())

    assert restored == descriptor
    assert restored.digest() == descriptor.digest()


def test_a_bound_workload_cannot_be_given_a_measurement_instead_of_a_callable() -> None:
    """S-1.6's rule, one layer up: the thing that runs the subject runs it."""
    with pytest.raises(WorkloadError, match="not a callable"):
        BoundWorkload(
            a_workload(*DOES_REAL_WORK),
            invoke=[0.4, 0.41],  # type: ignore[arg-type]
            scale=lambda n: n,
            reset=verified(),
        )


def test_a_binding_whose_reset_is_not_the_one_described_is_refused() -> None:
    """The constructor check that matters, and the fifth-and-then-some use of
    this project's recurring construction.

    Measurements taken through a mismatched binding carry a strategy that was
    never used, and ADR 026's finding is that the results cannot reveal it: a
    stale state and a correct reset both report the same thing every time.
    """
    descriptor = a_workload(*DOES_REAL_WORK, reset_method=ResetStrategy.SNAPSHOT_RESTORE)

    with pytest.raises(WorkloadError, match="was never used"):
        BoundWorkload(
            descriptor,
            invoke=lambda: None,
            scale=lambda n: n,
            reset=verified(ResetStrategy.ROLLBACK_AND_RESTORE_SEQUENCES),
        )


# ------------------------------------------- AC 2: validation that earns its name


def test_an_id_that_is_two_spellings_of_one_workload_is_refused() -> None:
    """The id is part of S-5.1's replay key. Two spellings are two cache entries
    for one workload, and the failure is silent — everything still runs, and
    everything runs twice."""
    for bad in ("API.Tickets.List", "api tickets list", "", "api..tickets"):
        with pytest.raises(ValidationError, match="not a usable workload id"):
            a_workload(*DOES_REAL_WORK, id=bad)


def test_observations_must_be_ordered_and_distinct() -> None:
    """Ascending because a rendered artifact goes into a cached prompt prefix
    (ADR 002) and must be byte-identical between runs; distinct because one
    volume measured twice leaves undefined which of the two a reader gets."""
    with pytest.raises(ValidationError, match="ascending scale"):
        a_workload(DOES_REAL_WORK[1], DOES_REAL_WORK[0])

    with pytest.raises(ValidationError, match="share a scale"):
        a_workload(DOES_REAL_WORK[0], DOES_REAL_WORK[0])


def test_observations_recording_different_metrics_are_refused() -> None:
    """S-3.2's rule applied to the artifact: a metric present at one scale and
    absent at another cannot be contrasted, and dropping it publishes a
    comparison that covered less than it claims."""
    partial = Observation(scale=100, metrics={DB_QUERY: 201})

    with pytest.raises(ValidationError, match="do not record the same metrics"):
        a_workload(DOES_REAL_WORK[0], partial)


def test_a_metric_that_is_not_a_measurement_is_refused() -> None:
    for value in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValidationError, match="not usable measurements"):
            Observation(scale=10, metrics={DB_QUERY: value})


def test_an_observation_with_no_metrics_records_nothing() -> None:
    with pytest.raises(ValidationError, match="records nothing"):
        Observation(scale=10, metrics={})


def test_the_artifact_is_frozen() -> None:
    """It goes in an append-only log. A mutable entry is an entry that can be
    changed after the thing that read it drew a conclusion."""
    descriptor = a_workload(*DOES_REAL_WORK)

    with pytest.raises(ValidationError):
        descriptor.id = "something.else"


def test_the_fixture_recipe_records_the_shape_and_not_only_the_size() -> None:
    """S-3.3: `Σk²` is minimised when every parent has the same number of
    children, so uniform is provably the blindest fixture for a per-parent cost.
    A baseline that did not carry its distribution would lose that qualification
    the first time it was quoted."""
    assert a_workload(*DOES_REAL_WORK).fixture.distribution is Distribution.UNIFORM

    with pytest.raises(ValidationError):
        FixtureRecipe(entity="ticket", per_parent=6, source="a factory")  # type: ignore[call-arg]


def test_the_replay_key_is_the_workload_and_its_fixture_and_not_its_measurements() -> None:
    """A cache keyed on what was measured could never hit, because the
    measurement is the thing it exists to avoid repeating."""
    unswept = a_workload()
    swept = a_workload(*DOES_REAL_WORK)

    assert unswept.digest() == swept.digest()
    assert a_workload(*DOES_REAL_WORK, id="api.tickets.detail").digest() != swept.digest()


def test_the_recipe_hashes_identically_in_a_fresh_interpreter() -> None:
    """The property that actually matters, tested where it could actually fail.

    A cache key is written by one process and read by another, so a digest that
    varied between interpreters would miss silently — and everything would still
    run, twice. Asserted against a subprocess with a different hash seed rather
    than against a second construction in this one, which proves only that equal
    inputs hash equally.
    """
    script = (
        "from coldfix.primitives.scaling import Distribution\n"
        "from coldfix.screening.workload import FixtureRecipe\n"
        "print(FixtureRecipe(entity='ticket', per_parent=6, "
        "distribution=Distribution.UNIFORM, source='a factory', seed=7).digest())\n"
    )
    elsewhere = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=dict(os.environ, PYTHONHASHSEED="1"),
        cwd=Path(__file__).parents[2],
    )

    assert elsewhere.stdout.strip() == RECIPE.digest()


def test_the_digest_changes_when_the_recipe_does() -> None:
    """A key that did not move with the fixture would replay a measurement taken
    against different data, which is the direction that produces a wrong number
    rather than a slow run."""
    shaped = RECIPE.model_copy(update={"distribution": Distribution.POWER_LAW})

    assert shaped.digest() != RECIPE.digest()


# ------------------------- F6: the verdict the agent is not allowed to supply


def test_work_verified_is_computed_and_has_no_field_to_set() -> None:
    """`08-audit.md` F6, structurally. The flaw was that an agent decided, and an
    agent is incentivised to say yes because saying yes completes its task."""
    with pytest.raises(ValidationError):
        a_workload(*STUB_ROUTE, work_verified=True)

    stub = a_workload(*STUB_ROUTE)
    assert not stub.work_verified

    with pytest.raises(ValidationError):
        stub.work_verified = True  # type: ignore[misc]


def test_a_workload_that_does_more_with_more_data_is_verified() -> None:
    """F6's three conditions, all holding across a tenfold spread."""
    workload = a_workload(*DOES_REAL_WORK)

    assert workload.work_verified
    assert "Verified across 10 to 100" in workload.work_evidence


def test_a_correctly_batched_endpoint_is_verified() -> None:
    """The reason F6's first condition is corrected here, and ADR 051.

    Two queries at ten rows and two at a hundred is exactly what a prefetched
    list view does. Under the audit's `queries rose` it fails work verification —
    so a workload would be verified only when it has an N+1, and the Explorer
    would discard the well-written half of every repository it looked at.
    """
    batched = a_workload(
        observed(scale=10, queries=2, response_bytes=4_000, seconds=0.05),
        observed(scale=100, queries=2, response_bytes=41_000, seconds=0.40),
    )

    assert batched.work_verified


def test_queries_falling_as_data_grows_is_still_disqualifying() -> None:
    """The half of F6's condition that is kept. More data costing fewer queries
    means something served the second measurement from a cache, and ADR 026's
    finding is that no comparison of results can reveal it."""
    cached = a_workload(
        observed(scale=10, queries=21, response_bytes=4_000, seconds=0.08),
        observed(scale=100, queries=3, response_bytes=41_000, seconds=0.62),
    )

    assert not cached.work_verified
    assert "did not fall" in cached.work_evidence


def test_a_stub_route_is_not_verified_and_the_reason_names_what_it_looks_like() -> None:
    workload = a_workload(*STUB_ROUTE)

    assert not workload.work_verified
    assert "stub route" in workload.work_evidence


def test_one_scale_point_cannot_show_a_response_to_volume() -> None:
    """Fail closed. One measurement of a stub and one of a real endpoint look the
    same, so the absence of a second is a refusal rather than a default yes."""
    workload = a_workload(DOES_REAL_WORK[1])

    assert not workload.work_verified
    assert "fewer than two scale points" in workload.work_evidence


def test_no_observations_at_all_is_a_valid_workload_that_claims_nothing() -> None:
    """The Explorer produces a workload before anything has swept it. What that
    workload cannot do is claim its work is verified."""
    workload = a_workload()

    assert workload.observations == ()
    assert not workload.work_verified


def test_scale_points_too_close_together_are_reported_as_undecidable() -> None:
    """F6's formula was written against 10x. Applied at 2x it demands a doubled
    payload for twice the data — a stronger test than the audit made, which would
    reject correct workloads. The harness says it cannot tell rather than no.
    """
    narrow = a_workload(
        observed(scale=50, queries=101, response_bytes=20_000, seconds=0.3),
        observed(scale=100, queries=201, response_bytes=41_000, seconds=0.62),
    )

    assert not narrow.work_verified
    assert f"{MINIMUM_SCALE_RATIO:.0f}" not in narrow.work_evidence  # states the measured ratio
    assert "2.0x apart" in narrow.work_evidence
    assert "Widen the spread rather than reading this as a no" in narrow.work_evidence


def test_a_missing_metric_is_not_a_pass_on_the_ones_that_were_measured() -> None:
    """A workload is not shown to do real work by whichever metrics happened to
    be available."""
    without_time = a_workload(
        Observation(scale=10, metrics={DB_QUERY: 21, RESPONSE_BYTES: 4_000}),
        Observation(scale=100, metrics={DB_QUERY: 201, RESPONSE_BYTES: 41_000}),
    )

    assert not without_time.work_verified
    assert SECONDS in without_time.work_evidence


def test_an_aggregate_endpoint_is_a_known_false_negative_and_says_so() -> None:
    """F6's test cannot separate a stub route from an endpoint that legitimately
    returns a fixed-size answer, and the evidence string says which two things it
    is failing to tell apart rather than asserting the workload is broken."""
    aggregate = a_workload(
        observed(scale=10, queries=37, response_bytes=90, seconds=0.02),
        observed(scale=100, queries=37, response_bytes=90, seconds=0.02),
    )

    assert not aggregate.work_verified
    assert "aggregate endpoint" in aggregate.work_evidence


# ----------------------------------- AC 3: the target repository, hand-written


def test_the_hand_written_target_workload_validates() -> None:
    """AC 3. Every number in it comes from `targets.toml` and ADR 011 — what
    S-0.3, S-0.4 and S-0.5 measured on `django-helpdesk` at `3a22901`."""
    assert HELPDESK_TICKETS.id == "api.tickets.list"
    assert HELPDESK_TICKETS.entry_point == "GET /api/tickets/?page_size=100"
    assert HELPDESK_TICKETS.largest is not None
    assert HELPDESK_TICKETS.largest.metrics[DB_QUERY] == 1193.0
    assert Workload.model_validate_json(HELPDESK_TICKETS.model_dump_json()) == HELPDESK_TICKETS


def test_the_target_workload_honestly_reports_that_its_work_is_not_verified() -> None:
    """The project's own target, failing F6's test on the project's own rules.

    ADR 011 measured one volume. The `queries ≈ 1 + T + F + T` line beside it is
    a model, and turning a model into a second observation would put a computed
    number where the artifact promises a measured one. So the descriptor says
    what it has: one point, and no claim.
    """
    assert not HELPDESK_TICKETS.work_verified
    assert "fewer than two scale points" in HELPDESK_TICKETS.work_evidence

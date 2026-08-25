"""One request becoming eight, which is how a metastable failure sustains itself.

S-3.16. `01-primitives.md` §15 says what this check is for and — as carefully —
what it is not: `08-audit.md` F1 downgraded the metastability gate because a
spike-and-recovery test needs scale a single container cannot reach, and
injecting latency to see whether retries amplify is what remains executable. It
catches the common case. **It does not prove safety**, and a test file that
implied otherwise would be doing the same damage the downgrade was meant to
prevent.

So the amplifying client and the well-behaved one are both here, and the report
for the well-behaved one is asserted to say that passing is not proof.

Three faults, and the third is separate for a reason worth testing: an error
refuses the request, while a dropped connection lets it through and *then*
fails — so retrying against a drop re-sends work that already happened.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from coldfix.bench.stats import Growth
from coldfix.primitives.faults import (
    AMPLIFICATION_FACTOR,
    BlastRadiusError,
    Fault,
    FaultError,
    InjectedFaultError,
    check_retry_amplification,
    degrade,
    inject,
)
from coldfix.primitives.registry import (
    REGISTRY,
    Applicability,
    Capability,
    PrimitiveUnavailableError,
    ProjectFact,
    ProjectProfile,
)

TIMEOUT = 0.04


@dataclass
class Arrivals:
    """How many requests actually reached the far side of the dependency."""

    count: int = 0


ARRIVALS = Arrivals()


def serve() -> str:
    """What the dependency does when nothing is wrong."""
    ARRIVALS.count += 1
    return "ok"


# A client object holding a callable attribute, which is the shape S-3.10 can
# interpose on: the attribute is stored on the owner itself, so it can be put
# back and the restoration verified. A classmethod could not be — S-3.10 refuses
# descriptors because replacing one changes how the attribute binds.
SERVICE = SimpleNamespace(call=serve)


class RetryingClient:
    """A client that retries on a slow dependency, which is the common shape.

    Each attempt that takes longer than its timeout is retried, up to a limit —
    so a dependency that slows down receives more requests, not fewer. That is
    the feedback loop retries are the most commonly cited trigger of.
    """

    def __init__(self, attempts: int = 4, timeout: float = TIMEOUT) -> None:
        self.attempts = attempts
        self.timeout = timeout

    def fetch(self) -> str:
        for _ in range(self.attempts):
            started = time.perf_counter()
            try:
                # Through the attribute, never a saved reference: S-3.14 measured
                # a component at slope -0.0002 because the workload had captured
                # the method before it was substituted.
                result: str = SERVICE.call()
            except InjectedFaultError:
                continue
            if time.perf_counter() - started < self.timeout:
                return result
        message = "gave up after retrying"
        raise TimeoutError(message)


class PatientClient:
    """The control: calls once and waits, however long it takes."""

    def fetch(self) -> str:
        result: str = SERVICE.call()
        return result


@pytest.fixture(autouse=True)
def _reset_service() -> None:
    ARRIVALS.count = 0
    # Deliberately *not* restoring `SERVICE.call` here. S-3.2 lost a sabotage run
    # to a reset double that fixed up what the code under test should have fixed;
    # if a fault leaks out of its block, the next test should see it.


# ------------------------------------------- AC 1: the three degradations


def test_latency_is_added_to_the_dependency() -> None:
    """AC 1. Netflix's first failure mode, and the one retry amplification is
    measured against."""
    client = PatientClient()

    response = inject(SERVICE, "call", client.fetch, Fault.LATENCY, seconds=0.05)

    assert response.calls == 1
    assert response.survived
    assert response.metrics["seconds"] >= 0.05


def test_errors_are_returned_instead_of_results() -> None:
    """Netflix's second mode. Many distinct faults reduce to this one: a bad
    deploy looks exactly like a service returning errors."""
    client = PatientClient()

    response = inject(SERVICE, "call", client.fetch, Fault.ERROR)

    assert response.calls == 1
    assert not response.survived
    assert response.failure is not None
    assert "InjectedFaultError" in response.failure


def test_an_error_refuses_the_request_before_it_reaches_the_dependency() -> None:
    """Which is what makes a retry against it safe, and is the difference from
    the dropped-connection case below."""
    client = PatientClient()

    inject(SERVICE, "call", client.fetch, Fault.ERROR)

    assert ARRIVALS.count == 0


def test_a_dropped_connection_lets_the_request_through_and_then_fails() -> None:
    """The third fault, and the reason it is not folded into the second.

    The work on the other side *happened*. A client that retries here re-sends
    something that may not be safe to repeat, and a client safe to retry against
    a refusal is not automatically safe against a drop.
    """
    client = PatientClient()

    response = inject(SERVICE, "call", client.fetch, Fault.DROPPED_CONNECTION)

    assert ARRIVALS.count == 1
    assert not response.survived
    assert "after the request was sent" in (response.failure or "")


def test_latency_without_a_duration_is_refused() -> None:
    with (
        pytest.raises(FaultError, match="not a degradation"),
        degrade(SERVICE, "call", Fault.LATENCY, seconds=0.0),
    ):
        pass


# ---------------------------------- AC 2: what the subject does about it


def test_a_workload_that_fails_under_fault_is_the_measurement() -> None:
    """Not an error to abort on. A subject that fails when its dependency fails
    may be behaving correctly, and which it is belongs to whoever reads this."""
    client = RetryingClient()

    response = inject(SERVICE, "call", client.fetch, Fault.ERROR)

    assert response.failed
    assert response.failure is not None
    assert response.calls == client.attempts  # it retried, then gave up


def test_a_subject_that_survives_degradation_is_recorded_as_surviving() -> None:
    """A fallback, a cache or a shorter path. The instrument records which
    happened rather than deciding whether it should have."""
    client = PatientClient()

    response = inject(SERVICE, "call", client.fetch, Fault.LATENCY, seconds=0.01)

    assert response.survived


def test_the_dependency_is_restored_afterwards() -> None:
    """A fault left injected would degrade every measurement taken afterwards —
    S-3.10's verified restoration is what prevents it."""
    original = SERVICE.call

    inject(SERVICE, "call", PatientClient().fetch, Fault.ERROR)

    assert SERVICE.call is original
    assert SERVICE.call() == "ok"


# --------------------------------------- AC 3: one dependency at a time


def test_a_second_injection_while_one_is_open_is_refused() -> None:
    """AC 3, structurally. Two simultaneous injections produce a measurement
    that cannot be attributed to either of them."""
    with degrade(SERVICE, "call", Fault.ERROR):  # noqa: SIM117 - the nesting is the assertion
        with pytest.raises(BlastRadiusError, match="already being degraded"):
            with degrade(SERVICE, "call", Fault.LATENCY, seconds=0.01):
                pass


def test_the_next_injection_works_once_the_first_has_finished() -> None:
    """The guard releases. A blast-radius rule that leaked would make the second
    experiment of every session impossible."""
    with degrade(SERVICE, "call", Fault.ERROR):
        pass

    with degrade(SERVICE, "call", Fault.LATENCY, seconds=0.01) as calls:
        assert calls.count == 0


def test_the_guard_releases_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError, match="deliberate"), degrade(SERVICE, "call", Fault.ERROR):
        message = "deliberate"
        raise RuntimeError(message)

    with degrade(SERVICE, "call", Fault.ERROR):
        pass


def test_the_guard_releases_when_the_target_cannot_be_wrapped() -> None:
    """The failure path that is easiest to leak: refusing *after* taking the
    lock would make every later injection in the process raise."""
    with pytest.raises(FaultError, match="not defined on"), degrade(SERVICE, "absent", Fault.ERROR):
        pass

    with degrade(SERVICE, "call", Fault.ERROR):
        pass


# ------------------------------------- AC 4: retry amplification


@pytest.mark.timing
def test_a_retrying_client_amplifies_load_as_the_dependency_slows() -> None:
    """AC 4, and the shape that sustains a metastable failure: the slower the
    dependency gets, the more work the subject sends it."""
    client = RetryingClient(attempts=4, timeout=TIMEOUT)

    result = check_retry_amplification(
        SERVICE,
        "call",
        client.fetch,
        latencies=(0.0, TIMEOUT * 2, TIMEOUT * 3),
        dependency="the pricing SERVICE",
    )

    assert result.amplifying
    assert result.factor >= AMPLIFICATION_FACTOR
    assert "more work the subject sends it" in result.explanation()
    assert "needs human review" in result.explanation()


@pytest.mark.timing
def test_a_retry_limit_makes_the_curve_a_step_and_it_is_still_amplification() -> None:
    """Why the criterion is a multiple and not a fitted exponent — ADR 045.

    A four-attempt client measures 1, 4, 4, 4: it saturates at its own limit, so
    no growth class fits and a superlinearity test would report nothing for the
    textbook amplifying case. The report says so rather than printing `None`.
    """
    client = RetryingClient(attempts=4, timeout=TIMEOUT)

    result = check_retry_amplification(
        SERVICE,
        "call",
        client.fetch,
        latencies=(0.0, TIMEOUT * 2, TIMEOUT * 3, TIMEOUT * 4),
    )

    assert [response.calls for response in result.responses] == [1, 4, 4, 4]
    assert result.growth is None
    assert result.amplifying
    assert "steps up to the ceiling" in result.explanation()
    assert "None" not in result.explanation()


def test_a_client_that_does_not_retry_does_not_amplify() -> None:
    """The control. A check that flagged every subject would be switched off,
    and then the amplifying ones go through too."""
    client = PatientClient()

    result = check_retry_amplification(SERVICE, "call", client.fetch, latencies=(0.0, 0.02, 0.04))

    assert not result.amplifying
    assert result.factor == pytest.approx(1.0)
    assert result.growth is Growth.CONSTANT


def test_not_amplifying_is_reported_as_not_proof_of_safety() -> None:
    """The sentence that keeps this check honest. `08-audit.md` F1 downgraded
    the metastability gate for a reason this does not undo — a spike-and-recovery
    test needs scale a single container cannot reach."""
    client = PatientClient()

    result = check_retry_amplification(SERVICE, "call", client.fetch, latencies=(0.0, 0.02, 0.04))

    assert "not proof of safety" in result.explanation()
    assert "catches the common case and no more" in result.explanation()


def test_the_undegraded_level_is_what_the_others_are_a_multiple_of() -> None:
    client = RetryingClient()

    result = check_retry_amplification(
        SERVICE, "call", client.fetch, latencies=(0.0, TIMEOUT * 2, TIMEOUT * 3)
    )

    assert result.responses[0].magnitude == 0.0
    assert result.baseline_calls == 1


def test_the_levels_are_ordered_however_they_were_given() -> None:
    """A curve read left to right, whatever order the caller listed.

    `baseline_calls` is the first response and the explanation prints them in
    sequence, so a caller who lists the levels out of order would otherwise get a
    degraded level as the baseline — every other count divided by an inflated
    denominator, which is the direction that hides amplification.
    """
    client = PatientClient()

    result = check_retry_amplification(SERVICE, "call", client.fetch, latencies=(0.02, 0.0, 0.01))

    assert [response.magnitude for response in result.responses] == [0.0, 0.01, 0.02]
    assert result.responses[0].magnitude == 0.0


def test_a_curve_with_no_undegraded_level_is_refused() -> None:
    with pytest.raises(FaultError, match="no undegraded level"):
        check_retry_amplification(SERVICE, "call", lambda: None, latencies=(0.01, 0.02, 0.03))


def test_a_curve_with_too_few_levels_is_refused() -> None:
    with pytest.raises(FaultError, match="at least 3 levels"):
        check_retry_amplification(SERVICE, "call", lambda: None, latencies=(0.0, 0.01))


# --------------------------------------- the dependency gate


def test_the_primitive_is_withheld_from_something_with_no_dependencies() -> None:
    """§15: not applicable to libraries, CLI tools or self-contained batch jobs.
    There is nothing to degrade."""
    primitive = REGISTRY.get("faults.injection")
    self_contained = ProjectProfile(
        capabilities={Capability.DEPENDENCY_INTERPOSITION},
        facts={ProjectFact.HAS_EXTERNAL_DEPENDENCIES: False},
    )

    verdict = primitive.verdict(self_contained)

    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert "nothing to degrade" in verdict.reason


def test_the_primitive_is_offered_where_there_are_dependencies() -> None:
    primitive = REGISTRY.get("faults.injection")
    networked = ProjectProfile(
        capabilities={Capability.DEPENDENCY_INTERPOSITION},
        facts={ProjectFact.HAS_EXTERNAL_DEPENDENCIES: True},
    )

    assert primitive.verdict(networked).applicability is Applicability.APPLICABLE


def test_the_selection_refuses_it_by_name_with_the_reason() -> None:
    selection = REGISTRY.select(
        ProjectProfile(
            capabilities=frozenset(Capability),
            facts={ProjectFact.HAS_EXTERNAL_DEPENDENCIES: False},
        )
    )

    with pytest.raises(PrimitiveUnavailableError, match="nothing to degrade"):
        selection.get("faults.injection")

"""Epic 10 composed: a confirmed finding in, a verified patch out.

Six stories — a test, a gate, an audit of the test, a slack classifier, a patch,
a retry discipline — and after all of them the epic **could not perform its own
sentence**. Fourth consecutive epic to end that way, and the defect is the same
shape every time: a value one story produces and another consumes, where nothing
in either story's tests holds both ends.

The three found here:

1. **The test was never run against the patched code.** S-10.2 proves it fails on
   unpatched code and takes a `DiagnosticSession` so a patch cannot be there.
   Nothing ran it afterwards, so nothing ever asked whether the patch worked.
2. **The same exit codes mean different things on the two sides.** An error
   against unpatched code is a broken script; against patched code — where the
   script has already run cleanly — it is the patch breaking what the test used.
3. **A strengthened test was never re-gated**, though S-10.3's return type exists
   precisely to force that second trip.

Measurements are real: the falsification scripts are executed by a real
interpreter. Model calls are replayed.
"""

from __future__ import annotations

import inspect
import sys
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from coldfix.bench.execute import ExecutionTimeoutError, execute
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import ExchangeRate, Phase
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog, Verdict
from coldfix.llm.client import ReplayingClient
from coldfix.primitives.scaling import Distribution
from coldfix.repair import compose, falsification, testaudit
from coldfix.repair import patch as patch_module
from coldfix.repair.compose import Outcome, Repaired, gate_and_audit, repair, verify
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import (
    BROKEN_EXIT,
    FAILED_EXIT,
    PASSED_EXIT,
    Falsified,
    NotFalsified,
    run_gate,
    wrap,
)
from coldfix.repair.patch import Patch, PatchError
from coldfix.repair.retry import Escalation
from coldfix.repair.sessions import refuse_foreign_session
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.patching import touched_paths

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 18))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"
SITE = "shop/serializers.py"
IMPLICATED = "shop/models.py"

FAILING_SCRIPT = "assert 1 == 2, 'the endpoint is still slow'"
PASSING_SCRIPT = "assert 1 == 1"
BROKEN_SCRIPT = "import a_module_that_does_not_exist"

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape=Distribution.UNIFORM.value,
    platform="x86_64-linux",
    concurrency=1,
    scales=[10, 100, 1000],
)


def a_chain() -> EvidenceChain:
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9, "rows": 1000.0},
        verdict=Verdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[Exclusion(experiment=excluded, conditions=UNIFORM_AT_1000)],
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
        site=Site(path=SITE, first_line=41, last_line=52),
        context=[Implicated(path=IMPLICATED, reason="declares the Author relation")],
    )


def a_test(*, script: str = FAILING_SCRIPT, **overrides: Any) -> FalsificationTest:
    fields: dict[str, Any] = {
        "claim": "the list endpoint stops re-rendering the author for every book",
        "script": script,
        "equivalence": "the same books in the same order",
        "cost": CostClaim(
            metric="seconds",
            baseline=8.24,
            at_most=2.0,
            guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
        ),
        "catches": (Cheat.CACHED_STATE,),
    }
    fields.update(overrides)
    return FalsificationTest(**fields)


def a_falsified(*, script: str = FAILING_SCRIPT) -> Falsified:
    return Falsified(
        test=a_test(script=script), evidence="AssertionError: still slow", wall_seconds=9.1
    )


def a_diff(*, path: str = SITE, start: int = 41, added: str = "        return cached") -> str:
    return "\n".join(
        [
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -{start},2 +{start},2 @@",
            "-        return AuthorSerializer(obj.author).data",
            f"+{added}",
            "",
        ]
    )


def a_patch(**overrides: Any) -> Patch:
    fields: dict[str, Any] = {
        "diff": a_diff(),
        "approach": "prefetch the authors once",
        "rationale": "the serializer walked the relation per book",
    }
    fields.update(overrides)
    return Patch(**fields)


class FakeCandidate(CandidateSession):
    """A candidate worktree that runs scripts on the host interpreter.

    The scripts are executed for real — the exit-code protocol is the thing under
    test — while the container is not, because docker is not what this checks.
    """

    def __init__(
        self, *, script_result: str | None = None, raises: Exception | None = None
    ) -> None:
        self._script_result = script_result
        self._raises = raises
        self.applied: list[str] = []
        self.commands: list[list[str]] = []
        self.applied_when_run: list[int] = []

    def apply_patch(self, diff: str) -> frozenset[str]:
        self.applied.append(diff)
        return touched_paths(diff)

    def run(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.commands.append(list(command))
        self.applied_when_run.append(len(self.applied))
        if self._raises is not None:
            raise self._raises
        # **Whatever it was handed, unmodified.** An earlier version re-wrapped a
        # canned script here, which meant the fake could not tell whether the
        # composed path had wrapped anything — and a sabotage removing `wrap`
        # from `verify` changed no outcome.
        program = command[-1]
        if self._script_result is not None:
            program = program.replace(FAILING_SCRIPT, self._script_result)
        return execute([sys.executable, "-c", program], timeout=30.0)


class FakeDiagnostic(DiagnosticSession):
    """A diagnostic worktree, likewise running scripts for real."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.commands.append(list(command))
        return execute([sys.executable, "-c", command[-1]], timeout=30.0)


def surgeon_session(system: str) -> Session:
    return Session(
        system=system,
        playbook="Django: prefetch_related for a relation walked per row.",
        source=SOURCE,
        rate=RATE,
    )


# ============ defect 1: the test was never run against the patched code


def test_a_passing_test_on_patched_code_is_a_verified_repair() -> None:
    """**The step the epic did not have.** S-10.2 proves the test fails on
    unpatched code and takes a `DiagnosticSession` so a patch cannot be there.
    Nothing ran it afterwards, so nothing ever asked whether the patch worked.

    Run for real: the script, the wrapper, the interpreter, the exit code.
    """
    session = FakeCandidate(script_result=PASSING_SCRIPT)

    assert verify(a_test(), session) is Outcome.VERIFIED
    assert verify(a_test(), session).worked


def test_a_still_failing_test_is_not_a_repair() -> None:
    """The control. A verifier that returned `VERIFIED` unconditionally would
    satisfy every other assertion here while shipping any patch at all."""
    session = FakeCandidate(script_result=FAILING_SCRIPT)

    assert verify(a_test(), session) is Outcome.STILL_FAILING
    assert not verify(a_test(), session).worked


def test_the_two_sides_take_opposite_session_types() -> None:
    """`run_gate` takes a `DiagnosticSession` because a patch must not be able to
    exist there; `verify` takes a `CandidateSession` because it must. Between
    them the pair says the whole rule."""
    assert inspect.signature(run_gate).parameters["session"].annotation == "DiagnosticSession"
    assert inspect.signature(verify).parameters["session"].annotation == "CandidateSession"


# ======== defect 2: the same exit codes mean different things


def test_an_errored_script_on_patched_code_blames_the_patch_not_the_test() -> None:
    """**The reading that would send the Surgeon to rewrite a correct test.**

    On unpatched code an error is a broken script — S-10.2's third outcome, whose
    remedy is *repair the script*. On patched code the same script has already
    run cleanly once, so an error means the patch removed something it depended
    on. Same exit code, opposite conclusion.
    """
    session = FakeCandidate(script_result=BROKEN_SCRIPT)
    outcome = verify(a_test(), session)

    assert outcome is Outcome.PATCH_BROKE_THE_TEST
    assert "the patch removed something the test depends on" in outcome.value


def test_the_protocol_is_shared_rather_than_re_derived() -> None:
    """Two encodings of *which exit code means an assertion failed* would be two
    answers to a question with one right one, in the two places that must agree.
    `verify` uses S-10.2's `wrap` and its constants."""
    for script, code in (
        (PASSING_SCRIPT, PASSED_EXIT),
        (FAILING_SCRIPT, FAILED_EXIT),
        (BROKEN_SCRIPT, BROKEN_EXIT),
    ):
        assert execute([sys.executable, "-c", wrap(script)], timeout=30.0).exit_code == code


def test_every_outcome_carries_a_failure_reason_for_the_next_attempt() -> None:
    """S-10.5's `Attempt` needs one, and *the test still fails* and *the patch
    broke the test* send the next attempt to different places."""
    assert Outcome.STILL_FAILING.failure != Outcome.PATCH_BROKE_THE_TEST.failure
    assert all(item.failure.strip() for item in Outcome)


# ============ defect 3: a strengthened test was never re-gated


def test_a_strengthened_test_goes_back_through_the_must_fail_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**S-10.3's return type exists to force this, and nothing performed it.**

    The audit hands back a `FalsificationTest`, never a `Falsified`, so the
    replacement cannot reach a patch without being re-run. A stronger test the
    unpatched code already passes is as useless as a weak one.
    """
    gated: list[str] = []

    def fake_gate(test, session, **kwargs):  # type: ignore[no-untyped-def]
        gated.append(test.script)
        return Falsified(test=test, evidence="AssertionError", wall_seconds=1.0)

    strengthened = a_test(
        script="assert 0, 'stronger'", catches=(Cheat.CACHED_STATE, Cheat.STUBBED_RESPONSE)
    )
    audit = testaudit.TestAudit(
        original=a_test(),
        weaknesses=(
            testaudit.Weakness(cheat=Cheat.STUBBED_RESPONSE, how="an empty list satisfies it"),
        ),
        strengthened=strengthened,
    )

    monkeypatch.setattr(compose, "run_gate", fake_gate)
    monkeypatch.setattr(
        falsification,
        "generate",
        lambda *a, **k: _outcome(a_test()),
    )
    monkeypatch.setattr(testaudit, "audit_test", lambda *a, **k: _outcome(audit))

    result = gate_and_audit(
        surgeon_session(falsification._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        diagnostic=FakeDiagnostic(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert gated == [FAILING_SCRIPT, "assert 0, 'stronger'"]
    assert not isinstance(result, tuple) or result[0].test is strengthened


def test_a_sound_audit_does_not_pay_for_a_second_gate_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control. Re-gating a test nobody changed would spend a run to
    establish what the first one established."""
    gated: list[str] = []

    def fake_gate(test, session, **kwargs):  # type: ignore[no-untyped-def]
        gated.append(test.script)
        return Falsified(test=test, evidence="AssertionError", wall_seconds=1.0)

    audit = testaudit.TestAudit(original=a_test(), weaknesses=(), strengthened=None)
    monkeypatch.setattr(compose, "run_gate", fake_gate)
    monkeypatch.setattr(falsification, "generate", lambda *a, **k: _outcome(a_test()))
    monkeypatch.setattr(testaudit, "audit_test", lambda *a, **k: _outcome(audit))

    gate_and_audit(
        surgeon_session(falsification._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        diagnostic=FakeDiagnostic(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert gated == [FAILING_SCRIPT]


def _outcome(value: Any) -> Any:
    """A `StepOutcome` shaped enough for the composed path, without a model."""

    class _Fake:
        def __init__(self, inner: Any) -> None:
            self.value = inner
            self.calls: tuple[Any, ...] = ()

    return _Fake(value)


# ============ defect 4: two Surgeon prompts, one session, no complaint


def test_each_surgeon_step_refuses_the_other_steps_session() -> None:
    """**S-9.1 closed this for the audit and nothing had closed it for the
    Surgeon.** `Session` caches one assembled prompt per model from *its* system
    string, while each `generate` sends *its module's* to the client — so a
    caller reusing one session bills and caches against a prefix that was never
    sent, silently, because nothing about the reply looks wrong."""
    wrong = surgeon_session(patch_module._SYSTEM)

    with pytest.raises(falsification.FalsificationError, match="not this step's"):
        falsification.generate(
            wrong,
            ReplayingClient([]),
            chain=a_chain(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )

    with pytest.raises(PatchError, match="not this step's"):
        patch_module.generate(
            surgeon_session(falsification._SYSTEM),
            ReplayingClient([]),
            chain=a_chain(),
            falsified=a_falsified(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


def test_the_right_session_is_accepted() -> None:
    """The control: a check that refused every session would make the epic
    unreachable rather than safe."""
    refuse_foreign_session(
        surgeon_session(falsification._SYSTEM), falsification._SYSTEM, RuntimeError
    )
    refuse_foreign_session(
        surgeon_session(patch_module._SYSTEM), patch_module._SYSTEM, RuntimeError
    )


# ============ the repair loop, end to end


class _Generations:
    """A stand-in for `patch.generate` that **records what it was passed**.

    An earlier version threw the keyword arguments away, so sabotages that
    stopped raising the temperature on a retry or stopped showing prior attempts
    changed no outcome — the composed path's two pieces of retry context were
    untested at the join that supplies them.
    """

    def __init__(self, patches: list[Patch]) -> None:
        self._patches = iter(patches)
        self.temperatures: list[float] = []
        self.priors: list[int] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.temperatures.append(kwargs["temperature"])
        self.priors.append(len(kwargs["prior"]))
        return _outcome(next(self._patches))


def _patched_generate(patches: list[Patch]) -> _Generations:
    return _Generations(patches)


def test_a_patch_that_makes_the_test_pass_is_returned_with_its_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The epic's sentence, performed: a patch is written inside the evidence,
    classified for slack, applied through S-2.4's filter, and **verified**."""
    monkeypatch.setattr(
        patch_module,
        "generate",
        _patched_generate([a_patch(diff=a_diff(added="        return cached"))]),
    )
    session = surgeon_session(patch_module._SYSTEM)
    candidate = FakeCandidate(script_result=PASSING_SCRIPT)

    result = repair(
        session,
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=candidate,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Repaired)
    assert len(result.attempts) == 1
    assert candidate.applied
    assert session.budget.used(Phase.REPAIR, FINDING) == 1


def test_a_cache_in_the_patch_is_labelled_and_needs_human_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**S-10.6 had no caller.** A patch that adds a cache is exactly what this
    system produces, and until the composed path existed nothing classified the
    diff it generated."""
    cached = a_patch(diff=a_diff(added="        return lru_cache(self._authors)[obj.author_id]"))
    monkeypatch.setattr(patch_module, "generate", _patched_generate([cached]))

    result = repair(
        surgeon_session(patch_module._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=FakeCandidate(script_result=PASSING_SCRIPT),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Repaired)
    assert result.needs_human_review
    assert "slack-reducing" in result.describe()


def test_three_failing_attempts_escalate_with_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**A defect the composition found: the stall fires before the cap can.**

    `Phase.REPAIR`'s cap is three attempts and `Budget`'s `stall_after` defaults
    to **three**, so three attempts failing the same way raise
    `ProgressStalledError` on the third — before the cap's
    `BudgetExhaustedError` could fire on the fourth. A loop catching only
    exhaustion let the stall escape as an unhandled exception: not an escalation,
    and carrying no history.

    The failure reasons here come from `verify` rather than from anything the
    agent said about itself, which is what makes the stall meaningful.
    """
    attempts = [
        a_patch(diff=a_diff(start=41, added="        return one"), approach="one"),
        a_patch(diff=a_diff(start=60, added="        return two"), approach="two"),
        a_patch(diff=a_diff(start=80, added="        return three"), approach="three"),
    ]
    monkeypatch.setattr(patch_module, "generate", _patched_generate(attempts))
    session = surgeon_session(patch_module._SYSTEM)

    result = repair(
        session,
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=FakeCandidate(script_result=FAILING_SCRIPT),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Escalation)
    assert len(result.attempts) == 3
    assert "still fails" in result.report()
    assert session.budget.used(Phase.REPAIR, FINDING) == 3


def test_three_differently_failing_attempts_escalate_through_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The control for the stall.** With distinct failures the stall never
    fires, so the escalation has to come from the cap instead — and if only one
    of the two paths were caught, exactly one of these two tests would pass."""
    attempts = [
        a_patch(diff=a_diff(start=41, added="        return one"), approach="one"),
        a_patch(diff=a_diff(start=60, added="        return two"), approach="two"),
        a_patch(diff=a_diff(start=80, added="        return three"), approach="three"),
    ]
    monkeypatch.setattr(patch_module, "generate", _patched_generate(attempts))

    outcomes = iter([Outcome.STILL_FAILING, Outcome.PATCH_BROKE_THE_TEST, Outcome.STILL_FAILING])
    monkeypatch.setattr(compose, "verify", lambda *a, **k: next(outcomes))
    session = surgeon_session(patch_module._SYSTEM)

    result = repair(
        session,
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=FakeCandidate(script_result=FAILING_SCRIPT),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Escalation)
    assert len(result.attempts) == 3
    assert session.budget.used(Phase.REPAIR, FINDING) == 3
    assert len({item.failure for item in result.attempts}) == 2


def test_the_patch_is_applied_before_the_test_is_run_against_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Order, not just presence.** Verifying before applying would run the test
    against the *unpatched* worktree — it would still fail, every patch would
    look useless, and the repair would burn all three attempts learning
    nothing."""
    monkeypatch.setattr(patch_module, "generate", _patched_generate([a_patch()]))
    candidate = FakeCandidate(script_result=PASSING_SCRIPT)

    repair(
        surgeon_session(patch_module._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=candidate,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert candidate.applied_when_run == [1]


def test_the_patched_side_runs_the_wrapped_program() -> None:
    """The protocol is S-10.2's or it is nothing: two encodings of *which exit
    code means an assertion failed* would be two answers in the two places that
    have to agree."""
    candidate = FakeCandidate(script_result=PASSING_SCRIPT)
    verify(a_test(), candidate)

    assert candidate.commands[0][-1] == wrap(FAILING_SCRIPT)
    assert candidate.commands[0][-1] != FAILING_SCRIPT


def test_a_timeout_against_patched_code_is_not_a_verification() -> None:
    """A killed run proves nothing, and reading it as *verified* would ship a
    patch on the strength of a script that hung."""
    candidate = FakeCandidate(raises=ExecutionTimeoutError(("python", "-c", "..."), 30.0, "", ""))

    assert verify(a_test(), candidate) is Outcome.PATCH_BROKE_THE_TEST


def test_a_vacuous_test_stops_the_story_before_the_audit_is_paid_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-10.2's gate ends the repair, and the test audit is a model call that
    should never be made about a test the unpatched code already passes."""
    audited: list[int] = []
    monkeypatch.setattr(
        falsification, "generate", lambda *a, **k: _outcome(a_test(script=PASSING_SCRIPT))
    )
    monkeypatch.setattr(testaudit, "audit_test", lambda *a, **k: audited.append(1))

    result = gate_and_audit(
        surgeon_session(falsification._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        diagnostic=FakeDiagnostic(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )

    assert isinstance(result, NotFalsified)
    assert result.vacuous
    assert audited == []


def test_each_retry_gets_a_higher_temperature_and_the_attempts_so_far(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5.1's raise and §5.1's context, asserted at the join that supplies them.

    Both survived a sabotage until the stand-in recorded its keyword arguments:
    the composed path passed them and nothing checked that it did.
    """
    generations = _patched_generate(
        [
            a_patch(diff=a_diff(start=41, added="        return one"), approach="one"),
            a_patch(diff=a_diff(start=60, added="        return two"), approach="two"),
            a_patch(diff=a_diff(start=80, added="        return three"), approach="three"),
        ]
    )
    monkeypatch.setattr(patch_module, "generate", generations)
    outcomes = iter([Outcome.STILL_FAILING, Outcome.PATCH_BROKE_THE_TEST, Outcome.STILL_FAILING])
    monkeypatch.setattr(compose, "verify", lambda *a, **k: next(outcomes))

    repair(
        surgeon_session(patch_module._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=FakeCandidate(),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert generations.temperatures == [0.2, 0.6, 0.6]
    assert generations.priors == [0, 1, 2]


def test_a_repeated_attempt_is_recorded_without_running_the_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**S-10.5's structural check had no caller either.** A second attempt that
    is the first one renamed costs an attempt and no test run — which is the
    whole point of rejecting it *before* the gates."""
    same = a_diff(added="        return one")
    monkeypatch.setattr(
        patch_module,
        "generate",
        _patched_generate(
            [
                a_patch(diff=same, approach="one"),
                a_patch(diff=same, approach="renamed"),
                a_patch(diff=a_diff(start=80, added="        return three"), approach="three"),
            ]
        ),
    )
    candidate = FakeCandidate(script_result=FAILING_SCRIPT)

    result = repair(
        surgeon_session(patch_module._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=a_falsified(),
        candidate=candidate,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Escalation)
    assert len(result.attempts) == 3
    # Two patches reached the worktree; the renamed repeat did not.
    assert len(candidate.applied) == 2
    assert "same lines" in result.attempts[1].failure


def test_the_verified_test_is_the_one_the_audit_let_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifying against the pre-audit test would check the patch against a test
    the Adversary said a cheat could pass."""
    strengthened = a_falsified(script=PASSING_SCRIPT)
    monkeypatch.setattr(patch_module, "generate", _patched_generate([a_patch()]))
    candidate = FakeCandidate()

    result = repair(
        surgeon_session(patch_module._SYSTEM),
        surgeon_session(testaudit.SYSTEM),
        ReplayingClient([]),
        chain=a_chain(),
        falsified=strengthened,
        candidate=candidate,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert isinstance(result, Repaired)


def test_an_out_of_scope_patch_never_reaches_the_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-10.4's scope check, reached through the loop rather than called
    directly — the difference between *the criterion is met* and *the criterion
    is reachable*."""
    monkeypatch.setattr(
        patch_module,
        "generate",
        _patched_generate([a_patch(diff=a_diff(path="shop/urls.py"))]),
    )
    candidate = FakeCandidate(script_result=PASSING_SCRIPT)

    with pytest.raises(PatchError, match="does not implicate"):
        repair(
            surgeon_session(patch_module._SYSTEM),
            surgeon_session(testaudit.SYSTEM),
            ReplayingClient([]),
            chain=a_chain(),
            falsified=a_falsified(),
            candidate=candidate,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )
    assert candidate.applied == []

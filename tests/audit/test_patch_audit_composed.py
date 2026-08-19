"""Epic 11 composed: a patch in, a verdict out.

Eight stories — an isolated handover, five attacks, a verdict, an ablation — and
after all of them the epic could not perform its own sentence: *defeat the patch,
not review it.* **Fifth consecutive epic to end that way**, and the defect is the
same shape every time: a value one story produces and another consumes, where
nothing in either story's tests holds both ends.

The four found here:

1. **The round is authorized twice and the second check answers a different
   question**, so the last permitted round sends a broken patch back to a Surgeon
   whose reply nothing is left to audit.
2. **The suite command was passed twice**, so the reproduction a human is told to
   paste could name a command that was never run.
3. **A strengthened test could never become a regression test**, because the only
   proof of failure in existence is about the test it replaced.
4. **Nothing can read a file out of a worktree**, so neither `Candidate` nor
   `ScopeAudit` can be assembled by anything inside this epic.

The sessions run real interpreters and the equivalence probe drives real source.
Model calls are replayed.
"""

from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from coldfix.audit import testquality
from coldfix.audit.cheating import Measure, Metrics, Reading, Revision
from coldfix.audit.equivalence import Probe
from coldfix.audit.invocation import AUDIT_TEMPERATURE
from coldfix.audit.patchaudit import SYSTEM as PATCH_SYSTEM
from coldfix.audit.patchaudit import Candidate, candidate_from, patch_audit_session
from coldfix.audit.patchcompose import (
    MISSING_READ_FILE,
    Audited,
    CompositionError,
    Measurements,
    Subject,
    attack_all,
    audit_patch,
    keep_regression_test,
    unattempted,
)
from coldfix.audit.patchverdict import Attack, Outcome, Route, Verdict, verdict_for
from coldfix.audit.testquality import QUESTION as TQ_QUESTION
from coldfix.audit.testquality import SYSTEM as TQ_SYSTEM
from coldfix.audit.testquality import render as tq_render
from coldfix.bench.execute import ExecutionResult, execute
from coldfix.bench.stats import Growth
from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import PHASE_CAPS, Budget, BudgetExhaustedError
from coldfix.cost.routing import StepType
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import ExperimentLog
from coldfix.diagnosis.log import Verdict as LogVerdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.primitives.envelope import (
    ALLOCATED_BLOCKS,
    BYTES_WRITTEN,
    CPU_SECONDS,
    OPEN_FILE_DESCRIPTORS,
    PEAK_RSS_BYTES,
    PROCESS_COUNT,
    THREAD_COUNT,
    WALL_SECONDS,
    EnvelopeSample,
)
from coldfix.primitives.measurement import MetricKind
from coldfix.primitives.scaling import Distribution
from coldfix.repair.compose import Outcome as VerifyOutcome
from coldfix.repair.falsification import Cheat, CostClaim, FalsificationTest, Guard
from coldfix.repair.mustfail import Falsified
from coldfix.repair.patch import Patch
from coldfix.repair.testaudit import TestAudit, Weakness
from coldfix.sandbox.modes import CandidateSession, DiagnosticSession
from coldfix.sandbox.worktrees import Worktree

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 19))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"
REVISION = "9f1c0de"
SUITE = ["pytest", "-q"]
SECONDS = "seconds"
ROWS = "rows"
QUERIES = "db.query"
TOTAL = "process.seconds"
SIZE = "response.bytes"

SERIALIZERS = "def answer(value):\n    return value\n"
MODELS = "def render_all(books):\n    return books\n"

DIFF = """\
diff --git a/shop/serializers.py b/shop/serializers.py
--- a/shop/serializers.py
+++ b/shop/serializers.py
@@ -1,2 +1,2 @@
-def answer(value):
-    return value
+def answer(value):
+    return list(value)
"""

PROBE = Probe(
    workload="shop.books.list",
    script="import subject\noutput = subject.answer(coldfix_input)",
)

QUIET = {
    WALL_SECONDS: 1.0,
    CPU_SECONDS: 1.0,
    PEAK_RSS_BYTES: 1e8,
    ALLOCATED_BLOCKS: 5_000.0,
    BYTES_WRITTEN: 0.0,
    OPEN_FILE_DESCRIPTORS: 20.0,
    THREAD_COUNT: 4.0,
    PROCESS_COUNT: 0.0,
}

KINDS = {
    SECONDS: MetricKind.DURATION,
    ROWS: MetricKind.COUNT,
    QUERIES: MetricKind.COUNT,
    TOTAL: MetricKind.DURATION,
    SIZE: MetricKind.COUNT,
}

# Built with `chr(10)` rather than an escape, for the reason S-11.2's unicode
# fixtures are: source-normalising tooling rewrites the escape into a real
# newline and the literal stops parsing.
STRONGER_SCRIPT = "import subject" + chr(10) + "assert hasattr(subject, 'fast')"

# **The patched revision adds a symbol and changes no answer.** The two have to
# differ enough for a falsification test to fail on one and pass on the other, and
# not at all for the equivalence attack — a fixture that changed `answer` would
# make every round `broken` and leave the clean path untested.
PATCHED = SERIALIZERS + chr(10) + "def fast():" + chr(10) + "    return True" + chr(10)


class _Worktree:
    """A directory holding one revision, running whatever it is handed."""

    def __init__(self, path: Path, source: str, *, suite_exit: int = 0) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "subject.py").write_text(source, encoding="utf-8")
        self._path = path
        self._suite_exit = suite_exit
        self.commands: list[list[str]] = []

    @property
    def worktree(self) -> Worktree:
        return Worktree(path=self._path, revision=REVISION, is_main=False)

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        max_output_chars: int = 8 * 1024 * 1024,
    ) -> ExecutionResult:
        self.commands.append(list(command))
        # **A probe arrives as `[interpreter, "-c", program]` and a suite arrives as
        # whatever the caller configured.** Recognising the suite by `argv[0] ==
        # "pytest"` made `python -m pytest` fall through to the probe branch, which
        # ran `--maxfail=1` as a program and failed on both revisions — so the
        # attack reported ALREADY_BROKEN and the test that checks the reproduction
        # never saw one.
        if len(command) >= 2 and command[1] == "-c":
            return execute(
                [sys.executable, "-c", command[-1]], cwd=self._path, timeout=min(timeout, 30.0)
            )
        program = f"import sys; sys.exit({self._suite_exit})"
        return execute([sys.executable, "-c", program], timeout=30.0)


class FakeDiagnostic(_Worktree, DiagnosticSession):
    """Before the change. No `apply_patch` exists on this type."""


class FakeCandidate(_Worktree, CandidateSession):
    """After it."""


def a_chain() -> EvidenceChain:
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000])",
        measurement={"db.query": 7.0},
        verdict=LogVerdict.REJECTED,
        outcome="queries flat at 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation')",
        measurement={"seconds": 8.24, "seconds_ablated": 0.9, "rows": 1000.0},
        verdict=LogVerdict.CONFIRMED,
        outcome="stubbing the serializer removed 89% of wall time",
    )
    return EvidenceChain.assemble(
        symptom=Symptom(metric="seconds", magnitude=8.24, at_scale=1000),
        exclusions=[
            Exclusion(
                experiment=excluded,
                conditions=Conditions.of(
                    fixture_shape=Distribution.UNIFORM.value,
                    platform="x86_64-linux",
                    concurrency=1,
                    scales=[10, 100, 1000],
                ),
            )
        ],
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
        site=Site(path="shop/serializers.py", first_line=1, last_line=2),
        context=[Implicated(path="shop/models.py", reason="the relation walked per row")],
    )


def a_claim() -> CostClaim:
    return CostClaim(
        metric=SECONDS,
        baseline=8.24,
        at_most=2.0,
        guards=(Guard(metric=ROWS, baseline=1000.0, at_most=1000.0),),
    )


def a_test(**overrides: Any) -> FalsificationTest:
    fields: dict[str, Any] = {
        "claim": "the list endpoint stops re-rendering the author for every book",
        "script": "import subject\nassert subject.answer([1]) == [1]",
        "equivalence": "the same books in the same order",
        "cost": a_claim(),
        "catches": (Cheat.CACHED_STATE,),
    }
    fields.update(overrides)
    return FalsificationTest(**fields)


def a_stronger_test() -> FalsificationTest:
    """Fails on the original worktree and passes on the patched one, which is what a
    regression test has to do and what the original test cannot demonstrate."""
    return a_test(
        claim="the endpoint is fast in a cold process and still returns every author",
        script='import subject\nassert hasattr(subject, "fast")',
    )


def a_falsified(test: FalsificationTest | None = None) -> Falsified:
    return Falsified(
        test=test if test is not None else a_test(),
        evidence="AssertionError: the endpoint is still slow",
        wall_seconds=8.4,
    )


def a_patch() -> Patch:
    return Patch(
        diff=DIFF,
        approach="return a list rather than the iterator",
        rationale="the serializer walked the relation per book",
    )


def a_measure(*, warm_only: bool = False) -> Measure:
    """**Every metric the cheat audit needs**, or four of its five classes come back
    `UNTESTED` and the round is `suspicious` however honest the patch is. That is
    S-11.3 behaving correctly and a fixture too thin to reach the clean path."""
    cold = (100.0, 100.0) if warm_only else (10.0, 2.0)
    warm = (75.0, 64.0) if warm_only else (10.0, 2.0)

    def measure(revision: Revision, shape: Distribution) -> Reading:
        index = 0 if revision is Revision.ORIGINAL else 1
        metrics = {
            SECONDS: cold[index],
            ROWS: 1000.0,
            QUERIES: (101.0, 2.0)[index],
            TOTAL: (12.0, 4.0)[index],
            SIZE: 2048.0,
        }
        later = {**metrics, SECONDS: warm[index]}
        return Reading(revision=revision, shape=shape, first=metrics, repeated=(later,))

    return measure


def sample(overrides: dict[str, float | None] | None = None) -> EnvelopeSample:
    metrics: dict[str, float | None] = {**QUIET, **(overrides or {})}
    return EnvelopeSample(metrics=metrics, unavailable={})


def measurements(**overrides: Any) -> Measurements:
    fields: dict[str, Any] = {
        "measure": a_measure(),
        "metrics": Metrics(
            cost=SECONDS,
            kinds=KINDS,
            calls=QUERIES,
            work=ROWS,
            whole_process=TOTAL,
            response_size=SIZE,
        ),
        "shape": Distribution.UNIFORM,
        "alternatives": (Distribution.LONG_TAIL,),
        "envelope_before": sample(),
        "envelope_after": sample(),
        "domain_before": {SECONDS: 8.24, ROWS: 1000.0, QUERIES: 101.0},
        "domain_after": {SECONDS: 1.5, ROWS: 1000.0, QUERIES: 2.0},
        "claim": a_claim(),
    }
    fields.update(overrides)
    return Measurements(**fields)


def a_subject(tmp_path: Path, *, suite_exit: int = 0, patched: str = PATCHED) -> Subject:
    return Subject(
        diagnostic=FakeDiagnostic(tmp_path / "original", SERIALIZERS),
        candidate=FakeCandidate(tmp_path / "patched", patched, suite_exit=suite_exit),
        sources={"shop/serializers.py": PATCHED, "shop/models.py": MODELS},
        original_sources={"shop/serializers.py": SERIALIZERS, "shop/models.py": MODELS},
        suite_command=SUITE,
        probe=PROBE,
    )


def quality_session() -> Session:
    return testquality.quality_session(rate=RATE, source=SOURCE)


def auditor_session() -> Session:
    return patch_audit_session(rate=RATE, source=SOURCE)


SOUND_REPLY = json.dumps({"weaknesses": []})
WEAK_REPLY = json.dumps(
    {
        "weaknesses": [{"cheat": "cached_state", "how": "the second call in a process is free"}],
        "strengthened": {
            "claim": "the endpoint is fast in a cold process and still returns every author",
            "script": STRONGER_SCRIPT,
            "equivalence": "the same books in the same order",
            "cost": {
                "metric": SECONDS,
                "baseline": 8.24,
                "at_most": 2.0,
                "guards": [{"metric": ROWS, "baseline": 1000.0, "at_most": 1000.0}],
            },
            "catches": ["cached_state"],
        },
    }
)


def replaying(session: Session, subject: Subject, reply: str) -> ReplayingClient:
    model = session.router.route(StepType.ATTACK_DESIGN, Phase.PATCH_AUDIT)
    candidate = candidate_from(
        a_patch(), original=dict(subject.original_sources), patched=dict(subject.sources)
    )
    return ReplayingClient(
        [
            Recording.of(
                model=model,
                system=TQ_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": f"{tq_render(candidate, a_test())}\n\n{TQ_QUESTION}",
                    }
                ],
                max_tokens=testquality.MAX_OUTPUT_TOKENS,
                temperature=AUDIT_TEMPERATURE,
                response={
                    "id": "msg",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": reply}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 900,
                        "output_tokens": 200,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 100,
                    },
                },
            )
        ]
    )


def run(
    tmp_path: Path,
    *,
    reply: str = SOUND_REPLY,
    subject: Subject | None = None,
    budget: Budget | None = None,
    **overrides: Any,
) -> Audited:
    chosen = subject if subject is not None else a_subject(tmp_path)
    quality = quality_session()
    return audit_patch(
        auditor_session(),
        quality,
        replaying(quality, chosen, reply),
        patch=a_patch(),
        test=a_test(),
        chain=a_chain(),
        falsified=a_falsified(),
        subject=chosen,
        measurements=overrides.get("measurements", measurements()),
        budget=budget if budget is not None else Budget(ledger=Ledger(), rate=RATE),
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )


# ============ the epic performs its own sentence


def test_a_patch_goes_through_all_five_attacks_to_one_verdict(tmp_path: Path) -> None:
    """*Defeat the patch, not review it* — end to end, for the first time."""
    audited = run(tmp_path)
    assert [item.attack for item in audited.verdict.results] == list(Attack)
    assert isinstance(audited.candidate, Candidate)


def test_the_surgeons_reasoning_stops_at_the_composition(tmp_path: Path) -> None:
    """S-11.1's boundary, with this module deliberately on the wrong side of it so
    no later caller has to remember to strip anything."""
    audited = run(tmp_path)
    assert not hasattr(audited.candidate, "rationale")
    assert not hasattr(audited.candidate, "approach")
    assert a_patch().approach not in audited.describe()
    assert a_patch().rationale not in audited.describe()


# ============ defect 1: the round is authorized twice, and the order matters


def test_the_last_permitted_round_does_not_send_a_patch_back_unaudited(
    tmp_path: Path,
) -> None:
    """**The defect this composition found.** `route` asks *may another round
    start*. Asked before the round that just happened is recorded, it says yes on
    the last permitted round — and the patch goes back to a Surgeon whose reply
    nothing is left to audit."""
    budget = Budget(ledger=Ledger(), rate=RATE)
    cap = PHASE_CAPS[Phase.PATCH_AUDIT].limit
    subject = a_subject(tmp_path, suite_exit=1)

    routes = []
    for index in range(cap):
        audited = run(
            tmp_path / f"round-{index}",
            subject=a_subject(tmp_path / f"round-{index}", suite_exit=1),
            budget=budget,
        )
        assert audited.verdict.verdict is Verdict.BROKEN
        routes.append(audited.routing.route)

    assert routes[0] is Route.RETURN_TO_SURGEON, "the first round may send it back"
    assert routes[-1] is Route.ESCALATE, (
        "the last permitted round must not, because nothing is left to audit the reply"
    )
    assert budget.used(Phase.PATCH_AUDIT, FINDING) == cap
    assert subject is not None


def test_a_round_past_the_cap_refuses_before_it_spends(tmp_path: Path) -> None:
    budget = Budget(ledger=Ledger(), rate=RATE)
    cap = PHASE_CAPS[Phase.PATCH_AUDIT].limit
    for index in range(cap):
        run(
            tmp_path / f"spent-{index}",
            subject=a_subject(tmp_path / f"spent-{index}", suite_exit=1),
            budget=budget,
        )

    with pytest.raises(BudgetExhaustedError):
        run(tmp_path / "third", subject=a_subject(tmp_path / "third"), budget=budget)


def test_the_round_is_recorded_before_it_is_routed(tmp_path: Path) -> None:
    """The ordering, asserted directly rather than only through its consequence."""
    body = inspect.getsource(audit_patch)
    assert body.index("patchverdict.record(") < body.index("patchverdict.route(")


# ============ defect 2: the suite command was passed twice


def test_the_reproduction_names_the_command_that_was_actually_run(tmp_path: Path) -> None:
    """**Silent when wrong**, and it is the one thing a human is told to paste."""
    subject = a_subject(tmp_path, suite_exit=1)
    audited = run(tmp_path, subject=subject)

    assert audited.verdict.verdict is Verdict.BROKEN
    assert audited.verdict.reproduction is not None
    assert audited.verdict.reproduction.how == " ".join(subject.suite_command)
    # The fake records what it was asked to run; `CandidateSession` does not
    # declare that, so the concrete type is what carries the evidence.
    ran = subject.candidate
    assert isinstance(ran, FakeCandidate)
    assert ["pytest", "-q"] in ran.commands, "and that command really ran"


def test_one_value_reaches_both_call_sites(tmp_path: Path) -> None:
    """The join. A `Subject` carries the command once; nothing takes a second."""
    unusual = ["python", "-m", "pytest", "--maxfail=1"]
    subject = Subject(
        diagnostic=FakeDiagnostic(tmp_path / "original", SERIALIZERS),
        candidate=FakeCandidate(tmp_path / "patched", PATCHED, suite_exit=1),
        sources={"shop/serializers.py": PATCHED},
        original_sources={"shop/serializers.py": SERIALIZERS},
        suite_command=unusual,
        probe=PROBE,
    )
    audited = run(tmp_path, subject=subject)
    assert audited.verdict.reproduction is not None
    assert audited.verdict.reproduction.how == " ".join(unusual)

    assert "suite_command" not in inspect.signature(audit_patch).parameters


def test_a_subject_with_no_suite_command_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CompositionError, match="report `NOT_RUN` on every patch"):
        Subject(
            diagnostic=FakeDiagnostic(tmp_path / "a", SERIALIZERS),
            candidate=FakeCandidate(tmp_path / "b", SERIALIZERS),
            sources={},
            original_sources={},
            suite_command=[],
            probe=PROBE,
        )


# ============ defect 3: a strengthened test could never become a regression test


def test_a_strengthened_test_is_re_gated_before_it_is_kept(tmp_path: Path) -> None:
    """**AC 3 of S-11.6 was unreachable.** `RegressionTest` refuses a proof about a
    different test, and the only `Falsified` in existence is the one for the
    Surgeon's original — so on the branch where the audit strengthened something,
    `keep` could not be called at all."""
    subject = a_subject(tmp_path)
    audit = TestAudit(
        original=a_test(),
        weaknesses=(Weakness(cheat=Cheat.CACHED_STATE, how="free on the second call"),),
        strengthened=a_stronger_test(),
    )
    kept = keep_regression_test(audit, original=a_falsified(), subject=subject, finding_id=FINDING)

    assert kept is not None
    assert kept.test == a_stronger_test(), "the strengthened one ships"
    assert kept.proof_of_failure.test == a_stronger_test(), "with its own proof"
    assert kept.verified is VerifyOutcome.VERIFIED
    assert kept.closes == (Cheat.CACHED_STATE,)


def test_the_original_proof_would_have_been_refused(tmp_path: Path) -> None:
    """The refusal the composition had to route around, stated directly."""
    audit = TestAudit(
        original=a_test(),
        weaknesses=(Weakness(cheat=Cheat.CACHED_STATE, how="free on the second call"),),
        strengthened=a_stronger_test(),
    )
    with pytest.raises(testquality.TestQualityError, match="ever watched fail"):
        testquality.keep(audit, proof_of_failure=a_falsified(), verified=VerifyOutcome.VERIFIED)


def test_a_strengthened_test_the_original_code_passes_is_not_kept(tmp_path: Path) -> None:
    """S-10.2's `PASSED_UNPATCHED` refusal reaching the permanent artifact: a
    *stronger* test the unpatched code already satisfies would install a regression
    test that can never fail."""
    vacuous = a_test(
        claim="always true", script="import subject\nassert subject.answer([1]) == [1]"
    )
    audit = TestAudit(
        original=a_test(),
        weaknesses=(Weakness(cheat=Cheat.CACHED_STATE, how="free on the second call"),),
        strengthened=vacuous,
    )
    kept = keep_regression_test(
        audit, original=a_falsified(), subject=a_subject(tmp_path), finding_id=FINDING
    )
    assert kept is None


def test_a_sound_audit_keeps_the_original_with_its_own_proof(tmp_path: Path) -> None:
    audit = TestAudit(original=a_test(), weaknesses=(), strengthened=None)
    kept = keep_regression_test(audit, original=a_falsified(), subject=a_subject(tmp_path))
    assert kept is not None
    assert kept.test == a_test()
    assert not kept.strengthened


def test_the_composed_round_keeps_a_regression_test(tmp_path: Path) -> None:
    audited = run(tmp_path, reply=WEAK_REPLY)
    assert audited.regression is not None
    assert audited.regression.closes == (Cheat.CACHED_STATE,)
    assert "PERMANENT REGRESSION TEST" in audited.describe()


# ============ defect 4: nothing can read a file out of a worktree


def test_the_missing_read_file_tool_is_named_rather_than_worked_around(
    tmp_path: Path,
) -> None:
    """§6.2 lists `read_file(path)` among the Adversary's tools and nothing
    implements it. `Candidate` and `ScopeAudit` both need source, so this
    composition takes it as a parameter and says why."""
    assert "read_file" in MISSING_READ_FILE
    assert MISSING_READ_FILE in run(tmp_path).describe()

    for session in (DiagnosticSession, CandidateSession):
        assert not hasattr(session, "read_file")
        assert not hasattr(session, "source")


def test_the_sources_are_the_whole_repository_not_only_the_touched_files(
    tmp_path: Path,
) -> None:
    """S-11.5 looks for callers, and callers are by definition somewhere else — a
    mapping holding only the changed files finds none and reports that as nothing
    to worry about."""
    subject = a_subject(tmp_path)
    assert "shop/models.py" in subject.sources
    assert "shop/models.py" not in {"shop/serializers.py"}


# ============ the two sessions


def test_the_two_audits_take_two_sessions(tmp_path: Path) -> None:
    """S-11.1's prompt and S-11.6's differ, so one session reused for both would
    bill and cache against a prefix that was never sent — Epic 10's composition
    found that for the Surgeon."""
    assert quality_session().system != auditor_session().system
    assert auditor_session().system == PATCH_SYSTEM


def test_reusing_one_session_for_both_is_refused(tmp_path: Path) -> None:
    subject = a_subject(tmp_path)
    wrong = auditor_session()
    with pytest.raises(Exception, match="session"):
        attack_all(
            wrong,
            wrong,
            replaying(wrong, subject, SOUND_REPLY),
            candidate=Candidate(diff=DIFF, original={}, patched={}),
            test=a_test(),
            chain=a_chain(),
            subject=subject,
            measurements=measurements(),
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
        )


# ============ a round that could not run is not a round that passed


def test_a_round_that_mounted_no_attacks_is_suspicious_never_clean() -> None:
    """The epic's recurring rule, at the composition. `verdict_for` refuses an
    empty list, so the path for *nothing ran* has to be spelled out."""
    verdict = verdict_for(unattempted("the subject would not stand up"))
    assert verdict.verdict is Verdict.SUSPICIOUS
    assert "would not stand up" in verdict.concern
    assert [item.outcome for item in verdict.results] == [Outcome.NOT_RUN] * len(Attack)


def test_a_clean_round_ships(tmp_path: Path) -> None:
    audited = run(tmp_path)
    assert audited.verdict.verdict is Verdict.CLEAN
    assert audited.ships
    assert audited.routing.route is Route.SHIP


def test_a_cheat_that_only_shows_warm_makes_the_round_suspicious(tmp_path: Path) -> None:
    """The five attacks reach the verdict from five different shapes; this is the
    one that arrives through measurements rather than through a run."""
    audited = run(tmp_path, measurements=measurements(measure=a_measure(warm_only=True)))
    assert audited.verdict.verdict is Verdict.SUSPICIOUS
    assert audited.routing.route is Route.ESCALATE

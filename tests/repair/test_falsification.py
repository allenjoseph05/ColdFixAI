"""S-10.1 — the test that has to fail before anything is allowed to change.

The whole story is one ordering claim — *first output is a test, not a patch* —
and an ordering enforced by convention is one an agent can reorder. So the tests
here mostly ask what the **type** can express, not what the code happens to do
first.

Two controls carry the file. A generator that refused every reply would satisfy
every negative assertion while making the repair phase unreachable, so a
well-formed test must round-trip. And a schema that accepted anything would let a
cost claim with no guard through, which is `CLAUDE.md`'s *queries down while rows
explode* arriving as a passing test.
"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from coldfix.bench.stats import Growth
from coldfix.cost.accounting import Agent, ExchangeRate, Phase, StepClass
from coldfix.cost.cascade import dearer_than
from coldfix.cost.routing import STEP_KINDS, StepType, classify
from coldfix.cost.session import Session
from coldfix.diagnosis.chain import (
    EvidenceChain,
    Implicated,
    LocalizationLink,
    Site,
    Symptom,
)
from coldfix.diagnosis.exclusions import Conditions, Exclusion
from coldfix.diagnosis.log import Experiment, ExperimentLog, Verdict
from coldfix.llm.client import Recording, ReplayingClient
from coldfix.repair import falsification as falsification_module
from coldfix.repair.falsification import (
    MAX_OUTPUT_TOKENS,
    SURGEON_TEMPERATURE,
    Cheat,
    CostClaim,
    FalsificationError,
    FalsificationTest,
    Guard,
    catalogue,
    chain_experiments,
    check_baselines,
    generate,
    parse,
    render_chain,
)

RATE = ExchangeRate(Decimal("0.92"), date(2026, 8, 17))
SOURCE = "shop/serializers.py::BookSerializer"
FINDING = "n.plus.one"

UNIFORM_AT_1000 = Conditions.of(
    fixture_shape="uniform", platform="x86_64-linux", concurrency=1, scales=[10, 100, 1000]
)


def a_chain() -> EvidenceChain:
    """The worked example S-8.6's tests use, so the figures are the same ones."""
    log = ExperimentLog()
    excluded = log.append(
        hypothesis="the database is the bottleneck",
        primitive="scaling.volume",
        rationale="queries have not been counted against volume yet",
        target="shop.books.list",
        design="scaling.volume(scales=[10, 100, 1000], distribution='uniform')",
        measurement={"db.query": 7.0},
        verdict=Verdict.REJECTED,
        outcome="queries flat at 7, 7, 7 across a 100x sweep",
    )
    confirmed = log.append(
        hypothesis="the serializer re-renders the author for every book",
        primitive="ablation.stub",
        rationale="the serializer is the only component not yet stubbed",
        target="BookSerializer.to_representation",
        design="ablation.stub(attribute='to_representation') on shop.books.list",
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
                basis="8.24s baseline against 0.90s ablated, same fixture and scale",
            )
        ],
        mechanism="the serializer re-renders the author for every book in the list",
        complexity={"rows": Growth.LINEAR},
        site=Site(path="shop/serializers.py", first_line=41, last_line=52),
        context=[Implicated(path="shop/models.py", reason="declares the Author relation")],
    )


def a_reply(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "claim": "the list endpoint stops re-rendering the author for every book",
        "script": "def test_books(): assert measure()['seconds'] < 2.0",
        "equivalence": "the same books in the same order with the same author fields",
        "cost": {
            "metric": "seconds",
            "baseline": 8.24,
            "at_most": 2.0,
            "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
        },
        "catches": ["cached_state", "stubbed_response"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def a_test(**overrides: Any) -> FalsificationTest:
    return parse(a_reply(**overrides), a_chain())


def a_session() -> Session:
    return Session(
        system=falsification_module._SYSTEM,
        playbook="Django: count queries with force_debug_cursor.",
        source=SOURCE,
        rate=RATE,
    )


def recorded(session: Session, question: str, reply: str) -> Recording:
    model = session.router.route(StepType.FALSIFICATION_TEST, Phase.REPAIR)
    return Recording.of(
        model=model,
        system=falsification_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=SURGEON_TEMPERATURE,
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
                "cache_read_input_tokens": 8000,
            },
        },
    )


# ==================================== AC 1: the first output is a test, not a patch


def test_the_artifact_cannot_express_a_patch() -> None:
    """**AC 1 enforced by absence rather than by ordering.** *First* is a claim
    about sequence, and a sequence enforced by convention is one an agent can
    reorder. A type that cannot hold a diff cannot emit one — the construction
    S-8.1 used for `validate` and S-9.1 for `chain`."""
    fields = set(FalsificationTest.model_fields)
    assert not fields & {"diff", "patch", "files", "approach"}

    with pytest.raises(ValidationError):
        FalsificationTest(  # type: ignore[call-arg]
            claim="c",
            script="s",
            equivalence="e",
            cost=CostClaim(
                metric="seconds",
                baseline=8.24,
                at_most=2.0,
                guards=(Guard(metric="rows", baseline=1000.0, at_most=1000.0),),
            ),
            catches=(Cheat.CACHED_STATE,),
            diff="--- a/shop/serializers.py",
        )


def test_generate_has_nowhere_to_receive_a_patch() -> None:
    parameters = set(inspect.signature(generate).parameters)
    assert not parameters & {"diff", "patch", "files"}


def test_the_agent_does_not_report_whether_its_own_test_failed() -> None:
    """**A correction to `03-agents.md` §5.4**, which has the model returning
    `failed_on_unpatched: bool` — the outcome of a run it did not perform.
    `CLAUDE.md` forbids exactly that, and S-4.1 already closed it once for
    `work_verified`. S-10.2 runs the test and owns the gate."""
    assert "failed_on_unpatched" not in FalsificationTest.model_fields

    with pytest.raises(FalsificationError, match="not a usable falsification test"):
        parse(a_reply(failed_on_unpatched=True), a_chain())


def test_the_report_says_the_test_has_not_been_run() -> None:
    """A reader handed a falsification test could reasonably assume somebody ran
    it. The one thing that makes it worth anything is that it failed on unpatched
    code, and that has not happened yet."""
    assert "has not been run" in a_test().describe()


# ============ AC 2: cost improvement *and* correctness preservation


def test_a_well_formed_test_round_trips() -> None:
    """**The control.** A parser that refused everything satisfies every negative
    assertion in this file while making the repair phase unreachable."""
    test = a_test()

    assert test.cost.metric == "seconds"
    assert test.cost.at_most < test.cost.baseline
    assert test.equivalence
    assert test.catches == (Cheat.CACHED_STATE, Cheat.STUBBED_RESPONSE)


@pytest.mark.parametrize("at_most", [8.24, 9.0])
def test_a_cost_threshold_the_unpatched_code_already_meets_is_refused(at_most: float) -> None:
    """§5.3: *a test that passes before you change anything is testing nothing.*
    A threshold at or above the baseline is that test written down, and it would
    reach S-10.2's gate looking well-formed."""
    with pytest.raises(FalsificationError, match="any unchanged run satisfies"):
        parse(
            a_reply(
                cost={
                    "metric": "seconds",
                    "baseline": 8.24,
                    "at_most": at_most,
                    "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
                }
            ),
            a_chain(),
        )


def test_a_cost_claim_with_no_guard_counter_is_refused() -> None:
    """**The non-negotiable as a schema.** `CLAUDE.md`: *guard counters on every
    metric — queries down while rows explode is not an improvement.* A test
    watching only the number it wants to move is one a cheat passes by moving it,
    and this is the first artifact in the system that can catch that before
    anything runs."""
    with pytest.raises(FalsificationError, match="at least 1 item"):
        parse(
            a_reply(cost={"metric": "seconds", "baseline": 8.24, "at_most": 2.0, "guards": []}),
            a_chain(),
        )


def test_a_guard_pointed_at_the_cost_metric_guards_nothing() -> None:
    """A guard exists to catch what was traded away to move the cost metric. One
    aimed at the cost metric is a second copy of the claim wearing a guard's
    name — and it would satisfy the non-empty check above."""
    with pytest.raises(FalsificationError, match="both the metric this patch must improve"):
        parse(
            a_reply(
                cost={
                    "metric": "seconds",
                    "baseline": 8.24,
                    "at_most": 2.0,
                    "guards": [{"metric": "seconds", "baseline": 8.24, "at_most": 8.24}],
                }
            ),
            a_chain(),
        )


def test_a_guard_that_demands_an_improvement_is_refused() -> None:
    """The opposite error, and it fails the patch for succeeding somewhere nobody
    claimed. A guard is a ceiling on regression, not a second cost claim."""
    with pytest.raises(ValidationError, match="demands an improvement"):
        Guard(metric="rows", baseline=1000.0, at_most=900.0)


def test_a_guard_may_allow_a_bounded_regression() -> None:
    """The control for the one above. Some trades are acceptable and stating the
    allowance is how a reader sees what was accepted."""
    guard = Guard(metric="rows", baseline=1000.0, at_most=1050.0)
    assert "at most +50" in guard.describe()
    assert "no regression at all" in Guard(metric="rows", baseline=1.0, at_most=1.0).describe()


def test_equivalence_is_required_and_cannot_be_blank() -> None:
    """AC 2's second half. A cost claim on its own is satisfied by deleting the
    work, which is the cheapest patch there is."""
    with pytest.raises(FalsificationError, match="not a usable falsification test"):
        parse(a_reply(equivalence="  "), a_chain())


# ============================== the figures are the chain's, not the model's


def test_a_baseline_the_harness_never_measured_is_refused() -> None:
    """S-8.3's discipline in the repair phase: the judgement of what to assert is
    the model's, the figures under it are not. A threshold quoted from an invented
    number is the first non-negotiable broken at the top of a repair."""
    with pytest.raises(FalsificationError, match="never measured"):
        parse(
            a_reply(
                cost={
                    "metric": "latency_p99",
                    "baseline": 8.24,
                    "at_most": 2.0,
                    "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
                }
            ),
            a_chain(),
        )


def test_a_baseline_that_disagrees_with_what_was_measured_is_refused() -> None:
    with pytest.raises(FalsificationError, match="quotes 9"):
        parse(
            a_reply(
                cost={
                    "metric": "seconds",
                    "baseline": 9.0,
                    "at_most": 2.0,
                    "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
                }
            ),
            a_chain(),
        )


def test_a_guards_baseline_is_checked_too() -> None:
    """The guard is the half a careless check would skip, and it is the half the
    non-negotiable is about."""
    with pytest.raises(FalsificationError, match="rows"):
        parse(
            a_reply(
                cost={
                    "metric": "seconds",
                    "baseline": 8.24,
                    "at_most": 2.0,
                    "guards": [{"metric": "rows", "baseline": 12.0, "at_most": 12.0}],
                }
            ),
            a_chain(),
        )


def test_a_figure_from_an_exclusion_is_a_real_measurement() -> None:
    """**The control, and it decides an implementation choice.** A cost baseline
    legitimately comes from the sweep that ruled the database out as well as from
    the ablation that found the cause. A checker reading only the confirmations
    would call `db.query = 7` a fabrication."""
    test = a_test(
        cost={
            "metric": "db.query",
            "baseline": 7.0,
            "at_most": 2.0,
            "guards": [{"metric": "rows", "baseline": 1000.0, "at_most": 1000.0}],
        }
    )
    assert test.cost.baseline == 7.0

    chain = a_chain()
    primitives = {item.primitive for item in chain_experiments(chain)}
    assert primitives == {"scaling.volume", "ablation.stub"}


def test_check_baselines_returns_its_objection_rather_than_raising() -> None:
    """S-8.2's construction: a rejection worth feeding back to the model is one
    that carries its reason, so this returns text and `parse` decides."""
    good = a_test()
    assert check_baselines(good, a_chain()) is None


def test_the_prompt_carries_the_measured_figures_it_demands_be_copied() -> None:
    """A model told to copy baselines it was never shown will estimate, and the
    check would then reject every reply — a generator that cannot succeed."""
    rendered = render_chain(a_chain())
    assert "do not estimate" in rendered
    assert "seconds = 8.24" in rendered
    assert "seconds_ablated = 0.9" in rendered
    assert "db.query = 7" in rendered


# =========================== AC 3: the cheat classes it is designed to catch


def test_the_five_cheat_classes_are_the_ones_the_documents_list() -> None:
    """`02-architecture.md` §210, `03-agents.md` §412 and S-11.3's criteria list
    the same five, so this is a transcription rather than a judgement."""
    assert {item.name.lower() for item in Cheat} == {
        "cached_state",
        "deferred_work",
        "over_fetch",
        "stubbed_response",
        "shape_specific",
    }
    assert len(catalogue()) == len(Cheat)


def test_a_test_that_catches_nothing_is_refused() -> None:
    """S-10.3 asks the Adversary *could a cheat pass this test*, and a stated
    answer is what it disagrees with. An empty list is unarguable."""
    with pytest.raises(FalsificationError, match="not a usable falsification test"):
        parse(a_reply(catches=[]), a_chain())


def test_a_cheat_class_nobody_defined_is_refused_not_dropped() -> None:
    """Dropping it silently would leave a shorter list reading as a complete one,
    and S-11.3 cannot attack a class that exists only in this reply."""
    with pytest.raises(FalsificationError, match="not one of the cheat classes"):
        parse(a_reply(catches=["cached_state", "probably_fine"]), a_chain())


def test_the_same_cheat_class_twice_is_refused() -> None:
    with pytest.raises(FalsificationError, match="more than once"):
        parse(a_reply(catches=["cached_state", "cached_state"]), a_chain())


def test_cheat_classes_are_read_case_insensitively() -> None:
    assert a_test(catches=["CACHED_STATE"]).catches == (Cheat.CACHED_STATE,)


def test_every_cheat_class_is_nameable_by_a_reply() -> None:
    """The control for the refusal above: a vocabulary the model cannot spell is
    one no test ever claims to catch."""
    named = [item.name.lower() for item in Cheat]
    assert a_test(catches=named).catches == tuple(Cheat)


# ======================================= routing, cost and the cascade


def test_this_step_is_mechanical_and_may_cascade() -> None:
    """**The asymmetry with S-8.1, and it is `04-cost.md` §3's rather than a
    preference.** Hypothesis generation records *no check exists* and cannot
    cascade; `FALSIFICATION_TEST` has a real one — *fails on unpatched code* — so
    a cheap model's answer can be falsified deterministically."""
    assert classify(StepType.FALSIFICATION_TEST) is StepClass.MECHANICAL
    assert STEP_KINDS[StepType.FALSIFICATION_TEST].mechanical_check == "fails on unpatched code"
    assert "validate" in inspect.signature(generate).parameters


def test_this_module_cannot_perform_the_check_that_makes_it_cascadeable() -> None:
    """The validator runs the script against unpatched code, and nothing here can
    execute anything. S-10.2 supplies it or nobody does — which is why `validate`
    is a parameter rather than something built here."""
    imported = set(vars(falsification_module))
    assert not imported & {"subprocess", "Popen", "run_test", "Worktree", "apply_patch"}

    parameters = {
        name
        for _, function in inspect.getmembers(falsification_module, inspect.isfunction)
        for name in inspect.signature(function).parameters
    }
    assert not parameters & {"worktree", "runner", "execute", "on_ref"}


def test_a_generated_test_comes_back_from_a_replayed_call() -> None:
    """End to end on the seam Epic 5 built: routed, authorized, billed, parsed."""
    session = a_session()
    chain = a_chain()
    question = f"{render_chain(chain)}\n\n{falsification_module.QUESTION}"
    client = ReplayingClient([recorded(session, question, a_reply())])

    outcome = generate(
        session,
        client,
        chain=chain,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        finding_id=FINDING,
    )

    assert outcome.value.cost.metric == "seconds"
    assert outcome.step.phase is Phase.REPAIR
    assert len(outcome.calls) == 1

    # **The step type is the one §3 has a check for, asserted through the call
    # rather than about the table.** A sabotage swapped it for `PATCH` and nothing
    # failed: both are mechanical and both route to the same tier today, so the
    # only visible difference is *which* deterministic check a cascade would be
    # validating against — `fails on unpatched code` against `test suite passes`.
    assert outcome.step.step_type is StepType.FALSIFICATION_TEST
    assert outcome.step.agent is Agent.SURGEON


def test_a_truncated_script_is_refused_rather_than_run() -> None:
    """**Untested until a sabotage proved it.** A truncated *script* is one whose
    assertions may be missing, and it would reach S-10.2's runner looking
    complete — a test that passes because the part that would have failed was cut
    off at the token limit."""
    session = a_session()
    chain = a_chain()
    question = f"{render_chain(chain)}\n\n{falsification_module.QUESTION}"
    model = session.router.route(StepType.FALSIFICATION_TEST, Phase.REPAIR)
    cut_off = Recording.of(
        model=model,
        system=falsification_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=SURGEON_TEMPERATURE,
        response={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": a_reply()[:120]}],
            "stop_reason": "max_tokens",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 900,
                "output_tokens": MAX_OUTPUT_TOKENS,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 8000,
            },
        },
    )

    with pytest.raises(FalsificationError, match="cut off"):
        generate(
            session,
            ReplayingClient([cut_off]),
            chain=chain,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )


def test_a_cascade_escalates_when_the_cheap_answer_fails_its_check() -> None:
    """The seam S-10.2 will use: the validator is supplied, the cheap tier's
    answer is rejected, and the step is re-run a tier up.

    Asserted here rather than left to S-10.2 because a parameter with no
    exercised path is the dead code this project deletes.
    """
    session = a_session()
    chain = a_chain()
    question = f"{render_chain(chain)}\n\n{falsification_module.QUESTION}"

    weak = a_reply(claim="a test that will not survive its check")
    strong = a_reply(claim="the list endpoint stops re-rendering the author")
    routed_tier = session.router.tier_for(StepClass.MECHANICAL, Phase.REPAIR)
    escalated_tier = dearer_than(routed_tier)
    assert escalated_tier is not None
    dearer = session.router.tier_models[escalated_tier]

    client = ReplayingClient(
        [
            recorded(session, question, weak),
            Recording.of(
                model=dearer,
                system=falsification_module._SYSTEM,
                messages=[{"role": "user", "content": question}],
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=SURGEON_TEMPERATURE,
                response={
                    "id": "msg",
                    "type": "message",
                    "role": "assistant",
                    "model": dearer,
                    "content": [{"type": "text", "text": strong}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 900,
                        "output_tokens": 200,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 8000,
                    },
                },
            ),
        ]
    )

    outcome = generate(
        session,
        client,
        chain=chain,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
        validate=lambda test: "will not survive" not in test.claim,
        finding_id=FINDING,
    )

    assert outcome.escalated
    assert outcome.value.claim == "the list endpoint stops re-rendering the author"


def test_a_refusal_is_reported_rather_than_read_as_nothing_to_assert() -> None:
    session = a_session()
    chain = a_chain()
    question = f"{render_chain(chain)}\n\n{falsification_module.QUESTION}"
    model = session.router.route(StepType.FALSIFICATION_TEST, Phase.REPAIR)
    refusal = Recording.of(
        model=model,
        system=falsification_module._SYSTEM,
        messages=[{"role": "user", "content": question}],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=SURGEON_TEMPERATURE,
        response={
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": "refusal",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 900,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 8000,
            },
        },
    )

    with pytest.raises(FalsificationError, match="declined"):
        generate(
            a_session(),
            ReplayingClient([refusal]),
            chain=chain,
            measured_prefix_tokens=100,
            measured_prompt_tokens=900,
            finding_id=FINDING,
        )


def test_the_first_attempt_runs_at_the_temperature_the_document_specifies() -> None:
    """§5.1: 0.2 first, 0.6 on retries. S-10.5 owns the raise, and its argument is
    that a retry at 0.2 produces a variation of the same idea."""
    assert SURGEON_TEMPERATURE == 0.2


def test_the_experiments_the_chain_rests_on_are_both_kinds() -> None:
    chain = a_chain()
    experiments = chain_experiments(chain)

    assert len(experiments) == 2
    assert {item.verdict for item in experiments} == {Verdict.CONFIRMED, Verdict.REJECTED}
    assert all(isinstance(item, Experiment) for item in experiments)

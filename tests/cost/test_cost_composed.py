"""Epic 5 composed: money spent through every control the epic builds.

Every other file here tests one story. This one performs the epic's own sentence
— *make development fast and production affordable* — end to end, for the reason
Epic 2's composition established and Epics 3 and 4 confirmed: a suite where each
file tests one import says nothing about whether the parts fit together, and all
three of those epics had defects no single-module test could reach.

The subject is one investigation: a grounding step routed to the cheap tier, an
investigate loop that cascades and escalates, a creative step that must never
cascade, forty experiments accumulating in a pruned log, and a run report that
ends in euros per confirmed finding.

No test here calls a model. `call` returns what an API returned — a result and
its usage — which is the same seam E7 will fill.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from coldfix.cost.accounting import (
    Agent,
    ExchangeRate,
    Ledger,
    ModelCall,
    Phase,
    StepClass,
    TokenUsage,
)
from coldfix.cost.budget import (
    PHASE_CAPS,
    BudgetExhaustedError,
    Disposition,
    ProgressStalledError,
)
from coldfix.cost.cascade import CHEAP_ATTEMPTS, NoValidatorError, cascade
from coldfix.cost.context import Cacheability, ContextError, Investigation, is_append_only
from coldfix.cost.pruning import RETRIEVAL_NOTICE, PrunedLog
from coldfix.cost.routing import (
    Router,
    StepType,
    Tier,
    frontier_share,
)
from coldfix.cost.session import (
    Session,
    SessionError,
    Step,
    call_counts,
    route_economics,
)
from coldfix.cost.vendors import WorkloadShape

RATE = ExchangeRate(Decimal("0.90"), date(2026, 8, 9))
DETAIL = "x" * 4_000

SYSTEM = "You find performance problems by running experiments."
PLAYBOOK = "Django: count queries with force_debug_cursor."
SOURCE = "def list_books(): ..."


def make_session(**overrides: object) -> Session:
    fields: dict[str, object] = {
        "system": SYSTEM,
        "playbook": PLAYBOOK,
        "source": SOURCE,
        "rate": RATE,
        "clock": lambda: datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return Session(**fields)  # type: ignore[arg-type]


def usage(
    *,
    input_tokens: int = 2_000,
    output_tokens: int = 1_000,
    read: int = 0,
    written: int = 0,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=written,
        cache_read_input_tokens=read,
    )


def api(
    result: str = "ok", used: TokenUsage | None = None
) -> Callable[[str], tuple[str, TokenUsage]]:
    """An API that returns a result and what it used, like the real one will."""
    reply = used if used is not None else usage()

    def call(model: str) -> tuple[str, TokenUsage]:
        return result, reply

    return call


def investigate(finding_id: str = "F1") -> Step:
    return Step(
        step_type=StepType.EVIDENCE_CHAIN,
        phase=Phase.INVESTIGATE,
        agent=Agent.DIAGNOSTICIAN,
        max_output_tokens=1_000,
        finding_id=finding_id,
    )


def ground() -> Step:
    return Step(
        step_type=StepType.EXPLORER_ACTION,
        phase=Phase.GROUND,
        agent=Agent.EXPLORER,
        max_output_tokens=1_000,
    )


# ============================================================ the whole epic


def test_an_investigation_runs_end_to_end_and_reports_euros_per_finding() -> None:
    """The epic's sentence, performed once: route, authorize, assemble, bill."""
    session = make_session(ceiling_eur=Decimal("50.00"))

    session.log_experiment(
        primitive="scaling", target="list_books", outcome="quadratic", detail=DETAIL
    )
    outcome = session.run(
        investigate(),
        question="What does the growth table show?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api("n+1 on author"),
    )

    assert outcome.value == "n+1 on author"
    assert outcome.model == "claude-sonnet-5"
    assert len(outcome.calls) == 1
    assert outcome.cost_usd > 0

    report = session.report(confirmed_findings=1)
    assert "euros per confirmed finding" in report
    assert "Budget:" in report
    assert "Frontier share:" in report
    assert "Pruning over 1 experiments" in report


def test_a_run_that_confirms_nothing_still_reports_what_it_cost() -> None:
    """S-4.5's rule survives composition: a null result is an answer, not a free run."""
    session = make_session()
    session.run(
        investigate(),
        question="Anything here?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )

    report = session.report(confirmed_findings=0)

    assert "not applicable — this run confirmed no findings" in report
    assert "Run cost: €" in report


# ================================================ defect 1: two append-only logs


def test_appending_summaries_to_the_prompt_loses_the_retrieval_notice() -> None:
    """The first naive join, and why it is silent.

    S-5.8's AC 3 is that the prompt *states* the detail is retrievable, because
    an agent that does not know it can ask will not ask — and the deferred detail
    is then lost in practice despite never being discarded. Appending each
    summary to S-5.7's log satisfies every check either module makes and drops
    that sentence entirely.
    """
    log = PrunedLog()
    prompt = Investigation(system=SYSTEM, playbook=PLAYBOOK, source=SOURCE, model="claude-opus-5")
    for index in range(3):
        record = log.append(
            primitive="ablation", target=f"step_{index}", outcome="87% localized", detail=DETAIL
        )
        prompt.append(record.summary())

    log_block = prompt.render("What next?")[3]

    assert "read_experiment" not in log_block.text
    assert RETRIEVAL_NOTICE not in log_block.text
    # And nothing objects: the prefix is still byte-identical and the log is
    # still append-only, so the cache keeps hitting while the agent is never told.
    assert is_append_only(list(prompt.entries)[:2], list(prompt.entries))


def test_appending_the_rendered_log_repeats_every_earlier_experiment() -> None:
    """The second naive join, and why it is worse the longer the run.

    Appending the rendered block after each experiment appends every earlier
    experiment with it. At S-5.4's cap of 40 the log is carried 40 times — on the
    prompt whose whole purpose is to be small — and this too keeps the byte-prefix
    property, so the cache hits on a prompt that has quietly gone quadratic.
    """
    log = PrunedLog()
    prompt = Investigation(system=SYSTEM, playbook=PLAYBOOK, source=SOURCE, model="claude-opus-5")
    for index in range(10):
        log.append(
            primitive="ablation", target=f"step_{index}", outcome="87% localized", detail=DETAIL
        )
        prompt.append(log.render())

    naive = prompt.render("What next?")[3].text

    assert naive.count(RETRIEVAL_NOTICE) == 10
    assert naive.count("experiment 1 —") == 10
    # Ten experiments carried once is what it should cost.
    assert len(naive) > 5 * len(log.render())


def test_the_session_renders_the_pruned_log_once_however_long_the_run() -> None:
    """The join with one owner. Ten experiments, ten summaries, one notice."""
    session = make_session()
    for index in range(10):
        session.log_experiment(
            primitive="ablation", target=f"step_{index}", outcome="87% localized", detail=DETAIL
        )

    outcome = session.run(
        investigate(),
        question="What next?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )
    log_block = outcome.blocks[3].text

    assert log_block.count(RETRIEVAL_NOTICE) == 1
    assert "read_experiment" in log_block
    assert log_block.count("experiment 1 —") == 1
    assert log_block == session.log.render()


def test_a_prompt_whose_log_is_owned_elsewhere_refuses_to_hold_its_own() -> None:
    """AC 2 of S-5.7 as an absence again: there is no second log to write to.

    The defect is not that the wrong join is easy — it is that both wrong joins
    look right. So the composed shape does not merely prefer one owner, it makes
    the other unreachable.
    """
    session = make_session()
    prompt = session.prompt_for("claude-sonnet-5")

    with pytest.raises(ContextError, match="cannot be appended to"):
        prompt.append("experiment 1 — ablation of get_price")

    with pytest.raises(ContextError, match="cannot be read as entries from"):
        _ = prompt.entries


def test_the_owned_log_still_grows_append_only_through_the_session() -> None:
    """Delegating the log must not cost S-5.7 the property it exists to keep."""
    session = make_session()
    session.log_experiment(primitive="scaling", target="a", outcome="linear", detail=DETAIL)
    prompt = session.prompt_for("claude-opus-5")
    early = prompt.log_text()

    session.log_experiment(primitive="scaling", target="b", outcome="quadratic", detail=DETAIL)

    assert prompt.log_text().startswith(early)


def test_the_stable_prefix_is_byte_identical_between_consecutive_calls() -> None:
    """S-5.7 AC 3, through the composition rather than against one object."""
    session = make_session()
    first = session.run(
        investigate(),
        question="First question?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )
    session.log_experiment(primitive="ablation", target="x", outcome="87%", detail=DETAIL)
    second = session.run(
        investigate(),
        question="A different question entirely?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )

    assert first.blocks[:3] == second.blocks[:3]
    assert second.blocks[3].text.startswith(first.blocks[3].text)
    assert first.blocks[4] != second.blocks[4]


# ============================================== defect 2: caches are model-scoped


def test_two_models_get_two_prompts_because_a_cache_is_scoped_to_one() -> None:
    """S-5.9 records the fact; nothing acted on it until the composition.

    A router that hands out three models within a run is handing out three
    caches. One `Investigation` bound to one model, reused across all of them,
    reports a hit rate averaged over caches that do not share an entry.
    """
    session = make_session()
    session.run(
        ground(),
        question="Which endpoints exist?",
        measured_prefix_tokens=5_000,
        measured_prompt_tokens=5_100,
        call=api(used=usage(read=4_000, input_tokens=100)),
    )
    session.run(
        investigate(),
        question="What does the growth table show?",
        measured_prefix_tokens=5_000,
        measured_prompt_tokens=5_100,
        call=api(used=usage(written=4_000, input_tokens=100)),
    )

    assert session.models_used == ("claude-haiku-4-5", "claude-sonnet-5")
    # Over the tokens the call reported, not the tokens the prefix measured:
    # 4,000 read of a 4,100-token prompt.
    assert session.prompt_for("claude-haiku-4-5").hit_rate() == Decimal(4_000) / Decimal(4_100)
    # The sonnet cache read nothing — it wrote. Blending the two would credit it
    # with haiku's warm call and report a cache that was never read as working.
    assert session.prompt_for("claude-sonnet-5").hit_rate() == 0

    report = session.cache_report()
    assert "2 used; each is a separate cache" in report


def test_a_calls_usage_is_recorded_against_the_cache_it_actually_used() -> None:
    """An escalation changes the model mid-step, so it changes the cache too."""
    session = make_session()
    rejected: list[str] = []

    def validate(_: str) -> bool:
        return len(rejected) > CHEAP_ATTEMPTS

    def call(model: str) -> tuple[str, TokenUsage]:
        rejected.append(model)
        return "patch", usage()

    session.run(
        Step(
            step_type=StepType.PATCH,
            phase=Phase.REPAIR,
            agent=Agent.SURGEON,
            max_output_tokens=1_000,
            finding_id="F1",
        ),
        question="Write the patch.",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=call,
        validate=validate,
    )

    # Two attempts on mid, one on frontier — and the frontier prompt is a
    # different object, because its cache is a different cache.
    assert session.models_used == ("claude-opus-5", "claude-sonnet-5")
    assert len(session.prompt_for("claude-sonnet-5")._usage) == CHEAP_ATTEMPTS
    assert len(session.prompt_for("claude-opus-5")._usage) == 1


# ============================================ defect 3: a cascade outspends its budget


def test_an_unbudgeted_cascade_spends_three_calls_for_one_authorization() -> None:
    """The hole, demonstrated on S-5.6 directly.

    `cascade` makes up to three calls; `authorize` was built to price one. A
    caller that authorized the step and then cascaded it spent three times what
    the ceiling was asked about — and the third call runs a tier dearer than the
    two it was asked about, so even multiplying by the attempt count under-prices
    it.
    """
    attempts: list[str] = []

    def attempt(model: str) -> str:
        attempts.append(model)
        return "patch"

    outcome = cascade(
        StepType.PATCH,
        attempt=attempt,
        validate=lambda _: len(attempts) > CHEAP_ATTEMPTS,
        router=Router(),
        phase=Phase.REPAIR,
    )

    assert len(attempts) == CHEAP_ATTEMPTS + 1
    assert attempts[-1] == "claude-opus-5"
    assert outcome.escalated


def test_the_session_authorizes_every_attempt_at_the_model_it_uses() -> None:
    """The ceiling holds inside a cascade, which is the only place it can.

    A ceiling that lets the first attempt through and stops the second is the
    whole difference between a ceiling and a report — S-5.4's argument, applied
    where three calls hide behind one step.
    """
    session = make_session(ceiling_eur=Decimal("0.03"))
    attempts: list[str] = []

    def call(model: str) -> tuple[str, TokenUsage]:
        attempts.append(model)
        return "patch", usage()

    with pytest.raises(BudgetExhaustedError) as raised:
        session.run(
            Step(
                step_type=StepType.PATCH,
                phase=Phase.REPAIR,
                agent=Agent.SURGEON,
                max_output_tokens=1_000,
                finding_id="F1",
            ),
            question="Write the patch.",
            measured_prefix_tokens=2_000,
            measured_prompt_tokens=2_100,
            call=call,
            validate=lambda _: False,
        )

    assert len(attempts) == 1
    assert raised.value.exhaustion.disposition is Disposition.HALT
    assert raised.value.exhaustion.spent_eur > 0


def test_a_creative_step_still_cannot_cascade_through_the_session() -> None:
    """`CLAUDE.md`'s non-negotiable, checked at the composed call site."""
    session = make_session()

    with pytest.raises(NoValidatorError):
        session.run(
            Step(
                step_type=StepType.HYPOTHESIS_GENERATION,
                phase=Phase.INVESTIGATE,
                agent=Agent.DIAGNOSTICIAN,
                max_output_tokens=1_000,
                finding_id="F1",
            ),
            question="What might be slow?",
            measured_prefix_tokens=2_000,
            measured_prompt_tokens=2_100,
            call=api(),
            validate=lambda _: True,
        )


def test_creative_work_routes_to_the_frontier_through_the_session() -> None:
    session = make_session()

    outcome = session.run(
        Step(
            step_type=StepType.HYPOTHESIS_GENERATION,
            phase=Phase.INVESTIGATE,
            agent=Agent.DIAGNOSTICIAN,
            max_output_tokens=1_000,
            finding_id="F1",
        ),
        question="What might be slow?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )

    assert outcome.routed_model == "claude-opus-5"
    assert outcome.step.step_class is StepClass.CREATIVE


# ================================= defect 4: frontier_share cannot see an escalation


def test_the_routed_frontier_share_misses_the_calls_that_escalate_to_it() -> None:
    """The metric is blind to the one path S-5.6 guarantees exists.

    `frontier_share` maps (phase, class) back through the router, and escalation
    is not a routing decision — so a repair step that failed its check twice and
    landed on the frontier model is counted as mid-tier. The figure exists to
    catch frontier use drifting upward, and escalation is exactly how it drifts.
    """
    session = make_session()
    seen: list[str] = []

    def call(model: str) -> tuple[str, TokenUsage]:
        seen.append(model)
        return "patch", usage()

    session.run(
        Step(
            step_type=StepType.PATCH,
            phase=Phase.REPAIR,
            agent=Agent.SURGEON,
            max_output_tokens=1_000,
            finding_id="F1",
        ),
        question="Write the patch.",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=call,
        validate=lambda _: len(seen) > CHEAP_ATTEMPTS,
    )

    routed = frontier_share(session.router, call_counts(session.ledger))
    observed = session.observed_frontier_share()

    assert routed == 0
    assert observed == Decimal(1) / Decimal(3)
    # The escalation itself is logged, which is what AC 3 of S-5.6 asks for and
    # what makes the gap above visible rather than merely present.
    assert session.escalations.statistics(StepType.PATCH).escalations == 1


def test_call_counts_gives_frontier_share_one_source_instead_of_two() -> None:
    """S-5.3's `reconciles` argument, applied to the routing figure."""
    ledger = Ledger()
    for phase, step_class in (
        (Phase.GROUND, StepClass.MECHANICAL),
        (Phase.GROUND, StepClass.MECHANICAL),
        (Phase.INVESTIGATE, StepClass.CREATIVE),
    ):
        ledger.record(
            ModelCall(
                phase=phase,
                agent=Agent.EXPLORER,
                step_class=step_class,
                model="claude-opus-5",
                usage=usage(),
                at=datetime(2026, 8, 10, tzinfo=UTC),
            )
        )

    counts = call_counts(ledger)

    assert counts == {
        (Phase.GROUND, StepClass.MECHANICAL): 2,
        (Phase.INVESTIGATE, StepClass.CREATIVE): 1,
    }
    assert frontier_share(Router(), counts) == Decimal(1) / Decimal(3)


# ============================ routing down a tier, priced rather than warned about


def test_the_cheap_tier_costs_more_when_the_prompt_is_below_its_minimum() -> None:
    """S-5.7's hazard as a number, with S-5.9's model doing the pricing.

    Grounding routes to `claude-haiku-4-5` because it is cheap, and §12.3's
    engineered grounding is a short prompt. At 2k tokens haiku caches nothing —
    its minimum is 4096 — while sonnet reads at 0.1x above its 1024. The cheap
    tier is then dearer per input token than the one above it.
    """
    economics = route_economics(
        Router(),
        StepType.EXPLORER_ACTION,
        WorkloadShape(
            calls=120,
            prompt_tokens=2_000,
            output_tokens=1_000,
            cached_share=Decimal("0.85"),
        ),
        phase=Phase.GROUND,
    )

    assert economics.routed_model == "claude-haiku-4-5"
    assert not economics.routed_caches
    assert economics.dearer_model == "claude-sonnet-5"
    assert economics.dearer_caches
    assert economics.false_economy
    assert "costs more here" in economics.describe()


def test_the_same_route_is_right_once_the_prompt_clears_that_minimum() -> None:
    """The control, without which the demonstration would pass for a function
    that simply always preferred the dearer tier."""
    economics = route_economics(
        Router(),
        StepType.EXPLORER_ACTION,
        WorkloadShape(
            calls=120,
            prompt_tokens=5_000,
            output_tokens=1_000,
            cached_share=Decimal("0.85"),
        ),
        phase=Phase.GROUND,
    )

    assert economics.routed_caches
    assert not economics.false_economy
    assert economics.routed_usd_per_mtok < economics.dearer_usd_per_mtok  # type: ignore[operator]


def test_the_session_reports_a_prompt_that_will_not_cache_on_its_routed_model() -> None:
    """The verdict travels with the step rather than needing to be asked for."""
    session = make_session()

    outcome = session.run(
        ground(),
        question="Which endpoints exist?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )

    assert outcome.routed_model == "claude-haiku-4-5"
    assert outcome.viability.verdict is Cacheability.BELOW_MINIMUM
    assert "routing this step down a tier can raise its effective cost" in (
        outcome.viability.describe()
    )


# ================================================================ the other joins


def test_a_prompt_cannot_be_shorter_than_its_own_prefix() -> None:
    """Two measured numbers, and swapping them is the conflation this epic keeps
    finding — the prefix decides whether anything caches, the whole prompt
    decides what the call is authorized against."""
    session = make_session()

    with pytest.raises(SessionError, match="which cannot be"):
        session.run(
            investigate(),
            question="What next?",
            measured_prefix_tokens=2_100,
            measured_prompt_tokens=2_000,
            call=api(),
        )


def test_the_investigate_cap_stops_the_run_with_a_partial_chain_not_a_halt() -> None:
    """S-5.4's disposition table survives composition, which is where it matters:
    forty experiments that established something and ran out is an answer.

    **This test used to count one `session.run` as one experiment**, which is the
    3x conflation S-5.4's own docstring predicts — *§12.1 budgets 120 model calls
    per finding against a cap of 40 experiments, so an experiment is about three
    calls.* Nothing ran a real investigate loop when it was written, so a call and
    an experiment looked like the same thing. S-8.9's loop is what counts an
    experiment now, and this stands in for it.
    """
    session = make_session()
    session.budget.tighten(Phase.INVESTIGATE, 2)

    for index in range(2):
        session.run(
            investigate(),
            question=f"Question {index}?",
            measured_prefix_tokens=2_000,
            measured_prompt_tokens=2_100,
            call=api(),
        )
        session.budget.record_step(Phase.INVESTIGATE, "F1", f"conclusion-{index}")

    with pytest.raises(BudgetExhaustedError) as raised:
        session.run(
            investigate(),
            question="One more?",
            measured_prefix_tokens=2_000,
            measured_prompt_tokens=2_100,
            call=api(),
        )

    assert raised.value.exhaustion.disposition is Disposition.PARTIAL
    assert raised.value.exhaustion.finding_id == "F1"


def test_a_phase_that_keeps_concluding_the_same_thing_escalates() -> None:
    """A stall and an exhaustion call for opposite actions, and the composed flow
    has to keep them distinguishable.

    The experiment is recorded separately from the calls that made it, for the
    reason the test above records.
    """
    session = make_session()

    with pytest.raises(ProgressStalledError) as raised:
        for index in range(3):
            session.run(
                investigate(),
                question=f"Question {index}?",
                measured_prefix_tokens=2_000,
                measured_prompt_tokens=2_100,
                call=api(),
            )
            session.budget.record_step(Phase.INVESTIGATE, "F1", "queries flat")

    assert raised.value.stall.repeated == 3
    assert "queries flat" in raised.value.stall.conclusion


def test_grounding_is_billed_to_no_finding_and_counted_by_nothing_here() -> None:
    """§11's sharing through the ledger — and **the counter this used to move**.

    Grounding's cap is counted in `StepUnit.STEP`, which was the one unit this
    session recorded for itself, and `GroundingRun.attempt` records the same unit
    for the same phase. Nothing noticed while no loop made a model call between
    two attempts; S-7.14's does, and then a turn costs two of sixty and a call
    carrying no conclusion clears the stall history every turn. The rule is now
    the one the other five phases already followed: the session bills, the
    phase's owner counts. ADR 139.
    """
    session = make_session()
    for index in range(3):
        session.run(
            ground(),
            question=f"Endpoint {index}?",
            measured_prefix_tokens=5_000,
            measured_prompt_tokens=5_100,
            call=api(),
        )

    assert session.budget.used(Phase.GROUND) == 0, "the owner of the unit counts it"
    assert session.ledger.by_finding() == {}
    assert session.ledger.unattributed_usd == session.ledger.total_usd
    assert session.ledger.reconciles


def test_the_cap_is_still_enforced_against_the_counter_the_owner_moves() -> None:
    """The control. Not recording is not the same as not enforcing: `authorize`
    reads the same counter before every attempt, so a phase at its cap refuses
    the next call whoever advanced it there."""
    session = make_session()
    for _ in range(PHASE_CAPS[Phase.GROUND].limit):
        session.budget.record_step(Phase.GROUND, conclusion=None)

    with pytest.raises(BudgetExhaustedError):
        session.run(
            ground(),
            question="One more endpoint?",
            measured_prefix_tokens=5_000,
            measured_prompt_tokens=5_100,
            call=api(),
        )


def test_the_ledger_reconciles_across_a_mixed_run() -> None:
    """The assertion that notices the first time a call is attributed twice."""
    session = make_session()
    session.run(
        ground(),
        question="Which endpoints?",
        measured_prefix_tokens=5_000,
        measured_prompt_tokens=5_100,
        call=api(),
    )
    for finding in ("F1", "F2"):
        session.run(
            investigate(finding),
            question="What does it show?",
            measured_prefix_tokens=2_000,
            measured_prompt_tokens=2_100,
            call=api(),
        )

    assert set(session.ledger.by_finding()) == {"F1", "F2"}
    assert session.ledger.unattributed_usd > 0
    assert session.ledger.reconciles


def test_forty_experiments_stay_within_the_shape_the_cost_model_assumes() -> None:
    """§12.3 budgets 12k for the whole pruned prompt across S-5.4's cap of 40."""
    session = make_session()
    for index in range(40):
        session.log_experiment(
            primitive="ablation",
            target=f"get_discount_price_{index}",
            outcome="8.24s becomes 1.11s. 87% of cost localized.",
            detail=DETAIL,
        )

    outcome = session.run(
        investigate(),
        question="What next?",
        measured_prefix_tokens=2_000,
        measured_prompt_tokens=2_100,
        call=api(),
    )

    log_block = outcome.blocks[3].text
    assert log_block.count(RETRIEVAL_NOTICE) == 1
    assert len(session.log.records) == 40
    assert session.log.meets_claim()
    # One block, not forty: a breakpoint looks back at most twenty.
    assert len(outcome.blocks) == 5


# ============================================ S-15.3: the cut that needs a router


def _billed(session: Session, model: str, millions: int = 1) -> None:
    """Record a call against `model` as the API would have reported it."""
    for _ in range(millions):
        session.ledger.record(
            ModelCall(
                phase=Phase.INVESTIGATE,
                agent=Agent.DIAGNOSTICIAN,
                step_class=StepClass.MECHANICAL,
                model=model,
                usage=TokenUsage(input_tokens=1_000_000, output_tokens=0),
                at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            )
        )


def test_cost_is_broken_down_by_tier() -> None:
    """AC 2's second half, and it needs the router rather than the ledger.

    A tier is a price band, not a model. The ledger records what was billed; only
    the router knows which band it was chosen from, which is why this cut lives
    here and `by_model` lives there.
    """
    session = make_session()
    frontier = session.router.tier_models[Tier.FRONTIER]
    cheap = session.router.tier_models[Tier.CHEAP]
    _billed(session, frontier, millions=2)
    _billed(session, cheap)

    by_tier = session.by_tier()

    assert by_tier[Tier.FRONTIER] == Decimal("10.00")
    assert by_tier[Tier.CHEAP] == Decimal("1.00")


def test_spend_on_a_model_no_tier_names_is_reported_not_absorbed() -> None:
    """**The number that would otherwise vanish into the cheapest band.**

    A call billed against a model the router never chose is either an escalation
    target that has since been reconfigured or a caller going around the router.
    Folding it into a tier would make the table sum to the run while describing
    a routing that did not happen; dropping it would make the table sum to less
    than the run, which is `Ledger.reconciles`' defect one level up.
    """
    session = make_session()
    _billed(session, "claude-fable-5")

    assert session.by_tier() == {}
    assert session.unrouted_usd() == Decimal("10.00")
    assert "no tier names" in session.tier_report()


def test_the_tier_table_and_the_unrouted_remainder_sum_to_the_run() -> None:
    """The reconciliation that makes the cut checkable rather than plausible."""
    session = make_session()
    _billed(session, session.router.tier_models[Tier.FRONTIER])
    _billed(session, "claude-fable-5")

    total = sum(session.by_tier().values(), Decimal(0)) + session.unrouted_usd()

    assert total == session.ledger.total_usd


def test_the_tier_breakdown_reaches_the_run_report() -> None:
    """The join: computed and rendered, not one or the other."""
    session = make_session()
    _billed(session, session.router.tier_models[Tier.FRONTIER])

    report = session.report(confirmed_findings=1)

    assert "Cost by tier:" in report
    assert Tier.FRONTIER.value in report

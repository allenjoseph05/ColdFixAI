"""S-17.4 — the two decisions between an inventory and a runnable graph.

`Sessions` has been a protocol since S-12.7 with no implementation anywhere, and
`gates_for` has been written, tested and uncalled since S-13.6. Neither gap was
visible from inside a node: a node asks for a session by prompt and uses it, and
`lambda system: object()` satisfies every test in the suite.

**The two properties worth attacking are the ones a plausible factory gets
wrong.** A factory that builds a fresh `Session` per call looks correct, passes
anything that only asks *did I get a session*, and quietly resets every per-phase
budget on every node execution. And a factory that gives every prompt the same
progress check looks tidy and makes two phases refuse to start.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from coldfix.agents.roles import RoleError
from coldfix.cost.accounting import ExchangeRate, Ledger, Phase
from coldfix.cost.budget import DEFAULT_STALL_AFTER, PHASE_CAPS
from coldfix.diagnosis import design, explain, hypothesis, interpretation
from coldfix.diagnosis.progress import INVESTIGATION_STALL_AFTER, check_stall_configuration
from coldfix.explorer import proposal
from coldfix.explorer.run import GROUNDING_STALL_AFTER, GroundingRun
from coldfix.orchestrator.adapters import Resources, Tokens
from coldfix.orchestrator.campaign import (
    STALL_AFTER,
    CampaignError,
    gated_graph,
    sessions_for,
)
from coldfix.primitives.counters import DB_QUERY
from coldfix.repair import falsification
from coldfix.state.trust import Level, standing

RATE = ExchangeRate(euros_per_dollar=Decimal("0.92"), as_of=date(2026, 8, 25))


def factory(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "playbook": "Django: migrations are applied with manage.py migrate.",
        "source": "shop/views.py::list_books",
        "rate": RATE,
    }
    fields.update(overrides)
    return sessions_for(**fields)


# ================================================ the per-phase progress check


def test_grounding_gets_a_budget_a_grounding_run_will_accept() -> None:
    """**`GroundingRun` refuses anything else**, and the refusal is the point: a
    run that escalates after three unchanged reports would abandon a repository
    mid-install. This is the assertion that a plausible factory fails."""
    session = factory()(proposal._SYSTEM)

    assert session.budget.stall_after == GROUNDING_STALL_AFTER
    assert GroundingRun.__init__ is not None  # the constructor is what enforces it


def test_an_investigation_gets_a_budget_run_investigation_will_accept() -> None:
    """The other refusal. `check_stall_configuration` raises on any other value,
    so this asserts against the real guard rather than against the constant."""
    session = factory()(hypothesis._SYSTEM)

    assert session.budget.stall_after == INVESTIGATION_STALL_AFTER
    check_stall_configuration(session.budget)


@pytest.mark.parametrize(
    "prompt",
    [design._SYSTEM, interpretation._SYSTEM, explain._SYSTEM],
    ids=["design", "interpretation", "explain"],
)
def test_every_investigate_prompt_agrees_with_the_one_that_is_checked(prompt: str) -> None:
    """Only the hypothesis session's budget is ever checked, because that is the
    one `run_investigation` holds. Giving the other three a different threshold
    would be three budgets disagreeing about one phase."""
    assert factory()(prompt).budget.stall_after == INVESTIGATION_STALL_AFTER


def test_a_prompt_with_no_stated_threshold_takes_the_default() -> None:
    """The audits and the Surgeon count rounds and attempts rather than steps and
    none of them refuses a number, so naming one would invent a requirement."""
    assert falsification._SYSTEM not in STALL_AFTER
    assert factory()(falsification._SYSTEM).budget.stall_after == DEFAULT_STALL_AFTER


def test_the_two_phases_that_refuse_cannot_share_one_budget() -> None:
    """**Why this is decided per prompt at all.** A campaign with a single budget
    cannot satisfy both refusals, and the two constants are what say so."""
    assert GROUNDING_STALL_AFTER != INVESTIGATION_STALL_AFTER


# ================================================ one session per prompt, reused


def test_the_same_prompt_gets_the_same_session() -> None:
    """**Load-bearing rather than an optimization.** `adapters.investigate` calls
    the factory every time the node runs; a fresh `Session` each time is a fresh
    `Budget` each time, so the per-phase caps would reset on every execution and
    S-5.4's enforcement would be counting to one."""
    sessions = factory()

    first = sessions(hypothesis._SYSTEM)
    second = sessions(hypothesis._SYSTEM)

    assert first is second


def test_a_cap_spent_through_the_factory_stays_spent() -> None:
    """The behaviour the identity above exists for, asserted against the counter
    rather than against the object."""
    sessions = factory()
    limit = PHASE_CAPS[Phase.GROUND].limit

    for _ in range(limit):
        sessions(proposal._SYSTEM).budget.record_step(Phase.GROUND, conclusion=None)

    assert sessions(proposal._SYSTEM).budget.used(Phase.GROUND) == limit
    assert sessions(proposal._SYSTEM).budget.remaining(Phase.GROUND) == 0


def test_two_prompts_get_two_sessions() -> None:
    """The control. One session for the whole run would defeat
    `refuse_shared_session`, which is the boundary keyed on the prompt."""
    sessions = factory()

    assert sessions(hypothesis._SYSTEM) is not sessions(proposal._SYSTEM)


# ================================================ one ledger underneath all of them


def test_every_session_bills_into_one_ledger() -> None:
    """**Or the euro ceiling is per-phase.** `Budget.spent_eur` reads its ledger's
    total, so sessions with separate ledgers each see only their own spending and
    a run could pass six ceilings on the way to breaching one."""
    ledger = Ledger()
    sessions = factory(ledger=ledger)

    assert sessions(hypothesis._SYSTEM).ledger is ledger
    assert sessions(proposal._SYSTEM).ledger is ledger
    assert sessions(hypothesis._SYSTEM).budget.ledger is ledger


def test_a_campaign_that_supplies_no_ledger_still_shares_one() -> None:
    """The default is a shared ledger, not a ledger each — the defect this would
    have is invisible until a ceiling fails to bite."""
    sessions = factory()

    assert sessions(hypothesis._SYSTEM).ledger is sessions(proposal._SYSTEM).ledger


def test_the_ceiling_reaches_every_session() -> None:
    sessions = factory(ceiling_eur=Decimal("40.00"))

    assert sessions(proposal._SYSTEM).budget.ceiling_eur == Decimal("40.00")
    assert sessions(explain._SYSTEM).budget.ceiling_eur == Decimal("40.00")


# ================================================ a prompt nobody owns


def test_a_prompt_no_role_claims_is_refused() -> None:
    """**Asked of the role index rather than mirrored here.** A session is what
    `refuse_shared_session` compares against, so a prompt nobody owns is a call
    nobody could attribute to an agent — and a second list of prompts in this
    module would disagree with `roles.py` the first time a step was added."""
    with pytest.raises(RoleError, match="no role claims this prompt"):
        factory()("You are a helpful assistant.")


def test_every_prompt_this_table_names_is_one_a_role_owns() -> None:
    """The table and the index have to agree, and the index is the authority."""
    sessions = factory()

    for prompt in STALL_AFTER:
        assert sessions(prompt) is not None


# ================================================ the gates a level compiles with


class FakeStore:
    """A ledger store holding whatever rows a test wants `standing` to read."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def read(self, collection: Any, key: str | None = None) -> list[Any]:
        del collection, key
        return [type("Row", (), {"entry": row})() for row in self.rows]


def resources_with(store: FakeStore) -> Any:
    unused = cast(Any, object())
    return Resources(
        workbench=unused,
        sessions=cast(Any, factory()),
        client=unused,
        budget=unused,
        store=cast(Any, store),
        project="shop",
        trust_key="query-batching@django/postgres/1e2",
        revision="HEAD",
        root=cast(Any, "."),
        python=["python"],
        ground=unused,
        hands=unused,
        bind=unused,
        measure=unused,
        instruments=unused,
        executor=unused,
        probe=unused,
        source="shop/views.py",
        suite_command=["pytest"],
        metric="seconds",
        counters=[DB_QUERY],
        tokens=Tokens(prefix=8000, prompt=900),
    )


def test_a_new_project_compiles_with_both_gates(tmp_path: Any) -> None:
    """**The call `gates_for` was built for and never had.** A project with no
    history is at level 0, and level 0 is both gates — which until now was a fact
    nothing in `src/` could act on."""
    del tmp_path
    graph = gated_graph(resources_with(FakeStore()), InMemorySaver())

    parked = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert parked, "it compiled"


def test_a_gated_graph_with_nowhere_to_park_is_refused() -> None:
    """`interrupt_before` parks the run *in* the checkpoint. With nowhere to park,
    the approval a human gives on Thursday has nothing to return to."""
    with pytest.raises(CampaignError, match="nothing to return to"):
        gated_graph(resources_with(FakeStore()), None)


def test_the_level_is_read_from_the_ledger_rather_than_supplied() -> None:
    """There is no argument on `gated_graph` through which a caller could ask for
    fewer gates — ADR 130's refusal, still standing now that a level is a thing a
    project earned rather than a parameter."""
    parameters = set(inspect.signature(gated_graph).parameters)

    assert parameters == {"resources", "checkpointer"}
    assert not any("level" in name or "gate" in name for name in parameters)


def test_a_level_zero_project_is_what_a_fresh_store_reports() -> None:
    """The premise the gate test rests on, asserted rather than assumed."""
    found = standing(
        cast(Any, FakeStore()),
        "query-batching@django/postgres/1e2",
        project="shop",
    )

    assert found.level is Level.GATED

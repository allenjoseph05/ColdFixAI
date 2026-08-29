"""The two decisions between a bag of resources and a runnable graph.

S-17.4. `Resources` is an inventory of live objects and `bind` turns it into
seven steps; between those and a run there are exactly two things nobody had
made: **which budget each agent step spends against**, and **which gates the
graph compiles with**. Both are decisions rather than plumbing, which is why they
are here and not in a constructor somewhere.

**There has never been a `Sessions` implementation.** The protocol has existed
since S-12.7 and every caller in the tree is `lambda system: object()` in a test.
That is not an oversight anybody could have noticed from inside a node: a node
asks for a session by prompt and uses it, and a factory that returned something
plausible would satisfy every test in the suite.

**Three phases want three different progress checks, and two of them refuse to
run against the wrong one.** `GroundingRun` will not be constructed unless its
budget stalls after 15, and `run_investigation` refuses anything but 8;
everything else takes S-5.4's default of 3. A single budget cannot satisfy the
first two at once, so a campaign needs one `Session` per prompt with its phase's
threshold — and `Session` is the only thing that builds a `Budget`, which is what
makes the prompt the place this is decided.

**One ledger underneath all of them, or the euro ceiling is per-phase.**
`Budget.spent_eur` reads its ledger's total, so sessions with separate ledgers
each see only their own spending and a run could pass six ceilings on the way to
breaching one. `04-cost.md` §12.1 costs a worst case at ~$291; a ceiling that
only ever sees a sixth of it is not a ceiling.

**The factory memoizes, and that is load-bearing rather than an optimization.**
`adapters.investigate` calls `sessions(...)` every time the node runs, and a
factory returning a fresh `Session` each time would hand back a fresh `Budget`
each time — so the per-phase caps would reset on every node execution and S-5.4's
whole enforcement would be counting to one. It would also discard the prompt
cache `04-cost.md` §4 is built around, which is the same defect S-5.9 records:
switching prompts mid-run discards the cache and nothing said so out loud.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from coldfix.agents.roles import owner_of
from coldfix.cost.accounting import ExchangeRate, Ledger
from coldfix.cost.budget import DEFAULT_STALL_AFTER, Budget
from coldfix.cost.session import Session
from coldfix.diagnosis import design, explain, hypothesis, interpretation
from coldfix.diagnosis.progress import INVESTIGATION_STALL_AFTER
from coldfix.explorer import proposal
from coldfix.explorer.run import GROUNDING_STALL_AFTER
from coldfix.orchestrator.adapters import Resources, Sessions, bind
from coldfix.orchestrator.gate import gates_for
from coldfix.orchestrator.graph import assemble
from coldfix.state.trust import standing


class CampaignError(Exception):
    """A campaign could not be assembled."""


STALL_AFTER: Mapping[str, int] = {
    proposal._SYSTEM: GROUNDING_STALL_AFTER,
    hypothesis._SYSTEM: INVESTIGATION_STALL_AFTER,
    design._SYSTEM: INVESTIGATION_STALL_AFTER,
    interpretation._SYSTEM: INVESTIGATION_STALL_AFTER,
    explain._SYSTEM: INVESTIGATION_STALL_AFTER,
}
"""How many identical conclusions each prompt's phase tolerates.

**The two that are not defaults are refusals somewhere else.** `GroundingRun`
raises unless its budget stalls after 15 — *a run that escalates after three
unchanged reports would abandon a repository mid-install* — and
`check_stall_configuration` raises unless an investigation's is 8, because *at
three, an agent that had ruled out three hypotheses would be stopped while it was
still buying exclusions.* Getting either wrong is a loud failure at the start of
a phase rather than a quiet one at the end, which is the only reason this table
can be trusted to be short.

Everything absent takes `DEFAULT_STALL_AFTER`. The audits and the Surgeon count
rounds and attempts rather than steps, and none of them refuses a particular
number, so naming them here would be inventing a requirement to record it."""


@dataclass
class _Sessions:
    """One session per prompt, built once and reused.

    A class rather than a closure over a dict because the cache is the behaviour
    being tested, and `sessions.opened` is how a test asks how many were built —
    which is the difference between a factory that memoizes and one that looks
    like it does.
    """

    playbook: str
    source: str
    rate: ExchangeRate
    ceiling_eur: Decimal | None
    ledger: Ledger
    opened: dict[str, Session] = field(default_factory=dict)
    budgets: dict[int, Budget] = field(default_factory=dict)
    """One budget per stall regime, keyed by the number itself. **S-17.17.**

    Not one per session: `Ledger` was shared and `Budget` was not, so a phase
    driven by two sessions counted its cap twice — the repair node opens two and
    `Phase.REPAIR` caps at three attempts, so a fourth was authorized after three.
    Not one per campaign either: `stall_after` is a single number per budget and
    `STALL_AFTER` gives grounding 15, an investigation 8 and the rest the default.
    Everything else in `Budget` is keyed by `(phase, finding_id)` already, so the
    stall value is the only thing that forces a split, and it is what this keys on.
    """

    def __call__(self, system: str) -> Session:
        """This prompt's session, refusing one no role owns.

        Raises:
            RoleError: no role claims this prompt, or more than one does. A
                session is what `refuse_shared_session` compares against, so a
                prompt nobody owns is a call nobody could attribute — and the
                index that knows is `agents/roles.py`, which is asked here rather
                than mirrored.
        """
        owner_of(system)
        if system not in self.opened:
            stall_after = STALL_AFTER.get(system, DEFAULT_STALL_AFTER)
            if stall_after not in self.budgets:
                self.budgets[stall_after] = Budget(
                    ledger=self.ledger,
                    rate=self.rate,
                    ceiling_eur=self.ceiling_eur,
                    stall_after=stall_after,
                )
            self.opened[system] = Session(
                system=system,
                playbook=self.playbook,
                source=self.source,
                rate=self.rate,
                ceiling_eur=self.ceiling_eur,
                stall_after=stall_after,
                ledger=self.ledger,
                shared_budget=self.budgets[stall_after],
            )
        return self.opened[system]


def sessions_for(
    *,
    playbook: str,
    source: str,
    rate: ExchangeRate,
    ceiling_eur: Decimal | None = None,
    ledger: Ledger | None = None,
) -> Sessions:
    """A session per agent step, sharing one ledger and one ceiling.

    `playbook` and `source` are the cached prefix every step carries, and they are
    the campaign's rather than a node's: `04-cost.md` §4 caches on the prefix, so
    two spellings of the same project would be two caches with half the hit rate
    each.

    The ledger is shared and defaulted rather than required, because a campaign
    that wanted its own is the ordinary case and one that wants to pool several
    runs into an existing ledger is a real thing S-15.3 will want.
    """
    return _Sessions(
        playbook=playbook,
        source=source,
        rate=rate,
        ceiling_eur=ceiling_eur,
        ledger=ledger if ledger is not None else Ledger(),
    )


def gated_graph(resources: Resources, checkpointer: Any = None) -> Any:  # noqa: ANN401 - the
    # saver and the compiled graph are generic over the state schema and their
    # parameters have moved between releases; `assemble` names neither for the
    # same reason.
    """Compile the seven nodes with the gates this project has earned.

    **The call ADR 138 built `gates_for` for and left with no caller.** A level is
    obtained only through `standing`, which reads the append-only ledger, so the
    gates a graph compiles with are a function of what this project's own history
    records — and there is no argument on this function through which a caller
    could ask for fewer.

    **The level is read once, at compile time, because that is when
    `interrupt_before` is decided.** S-12.2 established there is no runtime
    equivalent, which is also why `00-BRIEF.md` §4's refusal of a slack-reducing
    patch lives in the ship node instead: a patch does not exist yet here.

    Raises:
        CampaignError: a gated graph with no checkpointer. `interrupt_before`
            parks the run *in* the checkpoint, so an approval given on Thursday
            has nothing to return to — `assemble` refuses it too, and this says so
            in the campaign's vocabulary rather than the graph's.
    """
    level = standing(resources.store, resources.trust_key, project=resources.project).level
    gates = gates_for(level)
    if checkpointer is None and any(gates.values()):
        message = (
            f"{resources.project} is at {level.name} and compiles with "
            f"{sorted(name for name, on in gates.items() if on)}, and no checkpointer was "
            "supplied. An interrupt parks the run in the checkpoint; with nowhere to park, the "
            "approval a human gives has nothing to return to"
        )
        raise CampaignError(message)

    return assemble_with(bind(resources), checkpointer, gates)


def assemble_with(wiring: Any, checkpointer: Any, gates: Mapping[str, bool]) -> Any:  # noqa: ANN401
    """`assemble`, with the gates spread. Separated so a test can see the mapping.

    The spread is the whole of ADR 138's wiring — `assemble(wiring, saver,
    **gates_for(level))` — and keeping it in one named place is what lets a test
    assert *which* gates a level compiled with rather than only that the graph
    compiled.
    """
    return assemble(wiring, checkpointer, **gates)

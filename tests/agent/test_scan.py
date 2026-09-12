"""E19 — the scan loop, with no API call anywhere.

Two doubles, and the difference matters. `Scripted` returns replies in order, so
the loop's behaviour can be driven from the test. `ReplayingClient` is the
project's own double, and one test uses it to assert what a real unrecorded
request does: it is refused, never answered. Nothing here can reach the network,
and the last test says so about the module rather than about itself.

Every call goes through a real meter (S-26.1): the router, the budget and the
ledger are the production ones, and only the token count is supplied.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from coldfix.agent.prompt import SYSTEM
from coldfix.agent.protocol import Action, JsonReader, UnreadableReplyError
from coldfix.agent.scan import (
    EXPLORING_TOOLS,
    MEASURING_TOOLS,
    Bounds,
    MalformedSubmissionError,
    Outcome,
    Phase,
    ToolResult,
    scan,
)
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.cost.accounting import Agent, TokenUsage
from coldfix.cost.accounting import Ledger as Bill
from coldfix.cost.accounting import Phase as Spending
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Tier
from coldfix.evidence.ledger import FabricatedValueError, Ledger
from coldfix.llm.client import (
    NON_STREAMING_MAX_TOKENS,
    ModelResponse,
    NoRecordingError,
    ReplayingClient,
)
from fixtures.metering import metered

MODEL = "claude-opus-5"
FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]
CHEAP = DEFAULT_TIER_MODELS[Tier.CHEAP]


@dataclass
class Scripted:
    """Replies in order. Runs out rather than repeating."""

    replies: list[str]
    asked: list[Sequence[Mapping[str, Any]]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    ttls: list[str] = field(default_factory=list)
    caps: list[int] = field(default_factory=list)
    stop_reason: str = "end_turn"

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.asked.append(list(kwargs["messages"]))
        self.models.append(str(kwargs["model"]))
        self.ttls.append(str(kwargs.get("cache_ttl")))
        self.caps.append(int(kwargs["max_tokens"]))
        text = self.replies.pop(0) if self.replies else '{"tool": "submit", "arguments": {}}'
        return ModelResponse(
            model=MODEL,
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason=self.stop_reason,
        )


@dataclass
class FakeTools:
    """Records what was asked and answers however the test needs."""

    results: dict[str, ToolResult] = field(default_factory=dict)
    called: list[str] = field(default_factory=list)

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult:
        self.called.append(tool)
        return self.results.get(tool, ToolResult(content=f"{tool} ran"))


def text_of(message: Mapping[str, Any]) -> str:
    """What a message says, whatever blocks it is carried in."""
    return "".join(str(block["text"]) for block in message["content"])


def markers(request: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, Any]]:
    """Every cache breakpoint in a request, with where it sits."""
    return [
        (m, b, block["cache_control"])
        for m, message in enumerate(request)
        for b, block in enumerate(message["content"])
        if "cache_control" in block
    ]


def unmarked(request: Sequence[Mapping[str, Any]]) -> list[str]:
    """A request as the cache compares it: every marker removed, one line a message."""
    return [
        json.dumps(
            {
                "role": message["role"],
                "content": [
                    {k: v for k, v in block.items() if k != "cache_control"}
                    for block in message["content"]
                ],
            },
            sort_keys=True,
        )
        for message in request
    ]


def act(tool: str, **arguments: Any) -> str:
    return json.dumps({"tool": tool, "arguments": arguments, "reason": "because"})


def measured(identifier: str = "m-1", value: float = 2.41) -> BareMeasurement:
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=161,
        wall=Spread(median=value, low=value, high=value),
        cpu_s=value,
        mode=Mode.COMPUTING,
        peak_rss_bytes=1024,
        read_bytes=0,
        write_bytes=0,
    )


def verifying() -> FakeTools:
    return FakeTools(
        results={"measure": ToolResult(content="ok", measurement_id="m-1", verified=True)}
    )


def run(replies: list[str], tools: FakeTools | None = None, **kwargs: Any) -> Outcome:
    ledger = kwargs.pop("ledger", None) or Ledger()
    return scan(
        metered(Scripted(replies)),
        toolbox=tools or FakeTools(),
        ledger=ledger,
        system=SYSTEM,
        **kwargs,
    )


# --------------------------------------------------------- the phase gate


def test_profile_and_ablate_are_absent_until_a_measurement_verifies() -> None:
    """Not discouraged -- absent. A profile of a workload that will not run the
    same way twice is a profile of the machine."""
    assert "profile" not in EXPLORING_TOOLS
    assert "ablate" not in EXPLORING_TOOLS
    assert "measure" in EXPLORING_TOOLS, "verifying is how the phase ends"
    assert set(MEASURING_TOOLS) > set(EXPLORING_TOOLS)


def test_asking_for_a_locked_tool_is_refused_and_says_why() -> None:
    tools = FakeTools()
    outcome = run([act("profile", command=["python", "x.py"]), act("submit", findings=[])], tools)
    assert tools.called == [], "the toolbox was never reached"
    assert "not available yet" in outcome.transcript.turns[0][1].content
    assert outcome.stopped_by == "submitted"


def test_a_verified_measurement_opens_the_second_phase() -> None:
    tools = verifying()
    outcome = run([act("measure"), act("profile"), act("submit", findings=[])], tools)
    assert outcome.transcript.phase is Phase.MEASURING
    assert tools.called == ["measure", "profile"]


def test_only_measure_can_open_the_phase() -> None:
    """A tool that could set `verified` itself would be a tool that could unlock
    the instruments without anything having proved repeatable."""
    tools = FakeTools(results={"bash": ToolResult(content="ok", measurement_id="m-1")})
    outcome = run([act("bash", command="ls"), act("submit", findings=[])], tools)
    assert outcome.transcript.phase is Phase.EXPLORING


def test_the_agent_is_told_what_is_available_every_turn() -> None:
    """The list changes. Being told twice is cheaper than a turn spent asking for
    something that is not there yet."""
    client = Scripted([act("measure"), act("submit", findings=[])])
    scan(metered(client), toolbox=verifying(), ledger=Ledger(), system=SYSTEM)
    last = text_of(client.asked[-1][-1])
    assert "profile" in last and "ablate" in last


# ------------------------------------------------- routing and billing, S-26.1


def test_each_turn_is_routed_by_what_the_agent_is_doing() -> None:
    """Making the program run is checked by the harness, so it runs cheap;
    choosing an experiment is not checkable, so it runs on the frontier. The
    phase decides, and the phase is changed only by a verified `measure`."""
    client = Scripted([act("measure"), act("profile"), act("submit", findings=[])])
    scan(metered(client), toolbox=verifying(), ledger=Ledger(), system=SYSTEM)
    assert client.models == [CHEAP, FRONTIER, FRONTIER]


def test_every_turn_is_billed_to_the_scan_agent() -> None:
    bill = Bill()
    client = Scripted([act("measure"), act("submit", findings=[])])
    scan(metered(client, ledger=bill), toolbox=verifying(), ledger=Ledger(), system=SYSTEM)
    assert [call.agent for call in bill.calls] == [Agent.SCAN, Agent.SCAN]
    assert [call.phase for call in bill.calls] == [Spending.GROUND, Spending.INVESTIGATE]


# ------------------------------------------------ the cache breakpoint, S-26.2


def test_every_request_carries_one_breakpoint_on_its_newest_block() -> None:
    """One marker, moved forward each turn. A marker left behind on every turn
    would pass four -- the most a request may carry -- by the fourth turn."""
    client = Scripted([act("measure"), act("profile"), act("bash"), act("submit", findings=[])])
    scan(metered(client), toolbox=verifying(), ledger=Ledger(), system=SYSTEM)
    assert len(client.asked) == 4
    for request in client.asked:
        [(message, block, marker)] = markers(request)
        assert message == len(request) - 1, "on the newest message"
        assert block == len(request[-1]["content"]) - 1, "on its last block"
        assert marker == {"type": "ephemeral", "ttl": "1h"}


def test_each_request_begins_with_the_one_before_it() -> None:
    """The property caching rests on. With the markers removed, every request is
    the previous request plus what the turn added, byte for byte -- a message
    sent as a block on one request and as a string on the next would break it."""
    client = Scripted([act("measure"), act("profile"), act("bash"), act("submit", findings=[])])
    scan(metered(client), toolbox=verifying(), ledger=Ledger(), system=SYSTEM)
    for earlier, later in zip(client.asked, client.asked[1:], strict=False):
        before, after = unmarked(earlier), unmarked(later)
        assert after[: len(before)] == before
        assert len(after) == len(before) + 2, "one reply and one observation"


def test_the_ledger_is_told_the_hour_rate() -> None:
    """A 1-hour write bills at twice the input rate, and the ledger prices it from
    what the request asked for -- the response does not say."""
    client = Scripted([act("bash"), act("submit", findings=[])])
    scan(metered(client), toolbox=FakeTools(), ledger=Ledger(), system=SYSTEM)
    assert client.ttls == ["1h", "1h"]


def test_the_gap_between_requests_is_recorded() -> None:
    """So the first live run can say how often a gap outlives five minutes,
    instead of anyone assuming it (ADR 177)."""
    ticks = iter([0.0, 0.0, 30.0, 400.0])
    outcome = run(
        [act("bash"), act("bash"), act("submit", findings=[])],
        clock=lambda: next(ticks, 400.0),
    )
    assert outcome.transcript.gaps() == (30.0, 370.0)


def test_a_turn_the_budget_refused_is_not_a_request() -> None:
    client = Scripted([act("bash")])
    outcome = scan(
        metered(client, ceiling_eur=Decimal("0.000001")),
        toolbox=FakeTools(),
        ledger=Ledger(),
        system=SYSTEM,
    )
    assert outcome.transcript.requested_at == []


# ------------------------------------------------------------- the bounds


def test_the_turn_cap_ends_the_run_and_keeps_what_it_learned() -> None:
    """A scan that ran out of turns still knows things. Throwing them away to
    signal how it stopped would be the expensive kind of tidy."""
    outcome = run([act("bash", command="ls")] * 20, bounds=Bounds(turns=3, stall_turns=99))
    assert outcome.stopped_by == "turns"
    assert len(outcome.transcript.turns) == 3


def test_a_run_that_stops_measuring_is_stopped() -> None:
    """A loop reading files and thinking is a loop spending money on a decision
    it already had the evidence for."""
    outcome = run([act("read_file", path="a.py")] * 10, bounds=Bounds(turns=20, stall_turns=3))
    assert outcome.stopped_by == "stalled"
    assert len(outcome.transcript.turns) == 3


def test_a_measurement_resets_the_stall_counter() -> None:
    tools = FakeTools(results={"measure": ToolResult(content="ok", measurement_id="m-1")})
    replies = [act("read_file"), act("read_file"), act("measure"), act("read_file")]
    outcome = run([*replies, act("submit", findings=[])], tools, bounds=Bounds(stall_turns=3))
    assert outcome.stopped_by == "submitted"


def test_the_wall_clock_stops_a_run_that_will_not_finish() -> None:
    ticks = iter([0.0, 0.0, 5000.0])
    outcome = run(
        [act("bash", command="ls")] * 5,
        bounds=Bounds(wall_seconds=10.0),
        clock=lambda: next(ticks, 9999.0),
    )
    assert outcome.stopped_by == "wall_clock"


UNSPENT = "nothing should have been sent once the budget refused"


def test_the_budget_is_checked_before_the_call_not_after() -> None:
    """After would mean the call that broke the budget was already paid for. The
    run ends with `budget`, as every bound does, rather than raising."""
    client = Scripted([act("bash")])
    outcome = scan(
        metered(client, ceiling_eur=Decimal("0.000001")),
        toolbox=FakeTools(),
        ledger=Ledger(),
        system=SYSTEM,
    )
    assert outcome.stopped_by == "budget"
    assert client.asked == [], UNSPENT


# ------------------------------------------------------ reading the reply


def test_a_reply_that_is_not_an_action_is_handed_back_rather_than_guessed_at() -> None:
    """A parser that guessed would turn a misunderstanding into a run that did
    something nobody asked for, and the guess would be invisible."""
    outcome = run(["I think I should look at the models file.", act("submit", findings=[])])
    assert outcome.stopped_by == "submitted"
    assert outcome.transcript.turns == [], "nothing was executed from an unreadable reply"


def test_a_fenced_reply_is_read_because_a_fence_is_not_a_misunderstanding() -> None:
    reader = JsonReader()
    assert reader.read('```json\n{"tool": "bash", "arguments": {}}\n```') == Action(tool="bash")


def test_a_reply_missing_the_tool_is_refused() -> None:
    with pytest.raises(UnreadableReplyError):
        JsonReader().read('{"arguments": {"command": "ls"}}')


def test_a_refusal_ends_the_run_rather_than_being_read_as_an_answer() -> None:
    client = Scripted([""], stop_reason="refusal")
    outcome = scan(metered(client), toolbox=FakeTools(), ledger=Ledger(), system=SYSTEM)
    assert outcome.stopped_by == "refused"


# ------------------------------------------ S-26.4, room to think; a cut-off reply


def test_a_cut_off_reply_ends_the_run_without_being_read() -> None:
    """ADR 179. The reply is a whole, valid action cut off just past its closing
    brace -- so a loop that parsed before checking would run the tool."""
    client = Scripted([act("bash", command="ls")], stop_reason="max_tokens")
    tools = FakeTools()
    outcome = scan(metered(client), toolbox=tools, ledger=Ledger(), system=SYSTEM)
    assert outcome.stopped_by == "truncated"
    assert tools.called == []
    assert outcome.transcript.turns == []


def test_a_cut_off_reply_is_not_retried() -> None:
    """A retry runs at the same cap on the same prefix, and bills up to the cap
    again. One request, then the run ends."""
    client = Scripted(['{"tool": "ba'] * 3, stop_reason="max_tokens")
    scan(metered(client), toolbox=FakeTools(), ledger=Ledger(), system=SYSTEM)
    assert len(client.asked) == 1


def test_every_turn_is_asked_with_room_to_think() -> None:
    client = Scripted([act("bash", command="ls"), act("submit", findings=[])])
    scan(metered(client), toolbox=FakeTools(), ledger=Ledger(), system=SYSTEM)
    assert client.caps == [NON_STREAMING_MAX_TOKENS, NON_STREAMING_MAX_TOKENS]


# ------------------------------------- S-28.3, ADR 183: two nodes, one loop


def measuring() -> FakeTools:
    return FakeTools(
        results={"measure": ToolResult(content="ok", measurement_id="m-1", verified=True)}
    )


def test_grounding_stops_the_turn_the_workload_measures() -> None:
    """`ground` is done the moment a measurement verifies. Running on would spend
    the investigation's turns inside the node that installs things."""
    client = Scripted([act("measure", command=["python", "drive.py"]), act("bash", command="ls")])
    outcome = scan(
        metered(client),
        toolbox=measuring(),
        ledger=Ledger(),
        system=SYSTEM,
        bounds=Bounds(until_phase=Phase.MEASURING),
    )
    assert outcome.stopped_by == "grounded"
    assert outcome.transcript.phase is Phase.MEASURING
    assert len(client.asked) == 1


def test_without_that_bound_the_loop_runs_on_as_it_always_did() -> None:
    """The control: the same script, unbounded by phase, keeps going."""
    client = Scripted([act("measure", command=["python", "drive.py"]), act("submit", findings=[])])
    outcome = scan(metered(client), toolbox=measuring(), ledger=Ledger(), system=SYSTEM)
    assert outcome.stopped_by == "submitted"
    assert len(client.asked) == 2


def test_a_scan_can_start_already_measuring() -> None:
    """Grounding proved the workload measures, and the runnable artifact is that
    proof -- so the second node is offered the instruments from its first turn."""
    client = Scripted([act("profile", command=["python", "drive.py"]), act("submit", findings=[])])
    tools = FakeTools()
    outcome = scan(
        metered(client),
        toolbox=tools,
        ledger=Ledger(),
        system=SYSTEM,
        phase=Phase.MEASURING,
    )
    assert tools.called == ["profile"], "profile was offered on turn one"
    assert outcome.transcript.phase is Phase.MEASURING


def test_a_scan_that_starts_exploring_still_cannot_profile() -> None:
    """The phase gate is unchanged: the tools are what enforce it, not the caller."""
    client = Scripted([act("profile", command=["python", "drive.py"]), act("submit", findings=[])])
    tools = FakeTools()
    scan(metered(client), toolbox=tools, ledger=Ledger(), system=SYSTEM)
    assert tools.called == [], "profile is not offered until a measurement verifies"


def test_the_brief_opens_the_conversation() -> None:
    """How the runnable reaches a conversation that is deliberately fresh."""
    client = Scripted([act("submit", findings=[])])
    scan(
        metered(client),
        toolbox=FakeTools(),
        ledger=Ledger(),
        system=SYSTEM,
        phase=Phase.MEASURING,
        brief="Drive it with: python drive.py",
    )
    assert "Drive it with: python drive.py" in text_of(client.asked[0][0])


# ----------------------------------------------- the submission goes to the ledger


def test_a_submission_citing_an_unmeasured_number_fails_the_whole_run() -> None:
    """Not caught and softened into a partial result. A submission citing a
    number nobody measured is the failure this system is built to prevent, and
    swallowing it here would be the one place that could."""
    ledger = Ledger()
    ledger.record(measured("m-1", 2.41))
    claim = {
        "kind": "repeated_query",
        "summary": "s",
        "location": {"file": "a.py", "line": 1},
        "evidence": [{"measurement_id": "m-1", "field": "cpu_s", "value": 2.40}],
    }
    with pytest.raises(FabricatedValueError):
        run([act("submit", findings=[claim])], ledger=ledger)


def test_a_submission_whose_numbers_are_real_becomes_findings() -> None:
    ledger = Ledger()
    ledger.record(measured("m-1", 2.41))
    claim = {
        "kind": "repeated_query",
        "summary": "s",
        "location": {"file": "a.py", "line": 1},
        "evidence": [{"measurement_id": "m-1", "field": "cpu_s", "value": 2.41}],
    }
    outcome = run([act("submit", findings=[claim])], ledger=ledger)
    assert outcome.stopped_by == "submitted"
    assert len(outcome.findings) == 1
    assert not outcome.findings[0].proven, "no ablation, so suspected"


def test_a_submission_that_is_not_a_list_of_claims_is_refused() -> None:
    with pytest.raises(MalformedSubmissionError):
        run([act("submit", findings="everything is fine")])


def test_submitting_nothing_is_a_valid_ending() -> None:
    """Finding nothing is a result. It is not an error and not a failure."""
    outcome = run([act("submit", findings=[])])
    assert outcome.stopped_by == "submitted"
    assert outcome.findings == ()


# ------------------------------------------------------- nothing reaches a network


def test_the_projects_own_double_refuses_a_request_it_has_no_recording_for() -> None:
    """`ReplayingClient` holds no vendor client at all, so there is nothing here
    to call with. An unrecorded request is refused, never answered -- a double
    that answered anyway would make every agent test pass while testing the
    default."""
    with pytest.raises(NoRecordingError, match="no recording"):
        scan(
            metered(ReplayingClient()),
            toolbox=FakeTools(),
            ledger=Ledger(),
            system=SYSTEM,
            bounds=Bounds(turns=1),
        )


def test_the_prompt_states_the_two_traps_as_reasons_not_prohibitions() -> None:
    """A rule is argued with; an explanation is used.

    Whitespace is normalised because the assertion is about what the prompt says,
    not where the lines happen to wrap -- a test that broke on rewrapping would
    make the prompt harder to edit for no gain.
    """
    prose = " ".join(SYSTEM.split())
    assert "A profiler tells you where time is SPENT" in prose
    assert "does not tell you where time can be SAVED" in prose
    assert "A zero is not an absence" in prose
    assert "Finding nothing is a valid answer" in prose
    assert "Do not manufacture a finding to have one" in prose

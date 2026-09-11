"""S-27.2 — the Adversary designs inputs; the harness decides what broke. ADR 181.

Scripted turns against a harness double for most of this, because what is under
test is the loop and the verdict: which inputs run, when the attack stops, what
the model is shown, and that no verdict can disagree with what was measured. One
test at the end drives the real `revisions.compare` on E23's subject -- a cache
keyed on the first argument -- to show the whole path catches a real bad patch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from coldfix.collect.usage import ChildResult, Usage
from coldfix.cost.accounting import Agent, Phase, StepClass, TokenUsage
from coldfix.cost.budget import BudgetExhaustedError
from coldfix.cost.cascade import NoValidatorError
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Tier
from coldfix.evidence.adversary import (
    ATTACK,
    MAX_TURNS,
    PER_TURN,
    PatchReview,
    Verdict,
    review_patch,
)
from coldfix.evidence.revisions import Comparison, PatchUnderAudit, Side, compare
from coldfix.llm.client import NON_STREAMING_MAX_TOKENS, ModelResponse
from fixtures.metering import metered

FRONTIER = DEFAULT_TIER_MODELS[Tier.FRONTIER]

UNDER_AUDIT = PatchUnderAudit(
    diff="--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-x = f(a)\n+x = cached(a)\n",
    test="def test_shout(): assert shout('a') == 'A'",
    baseline=Path("baseline-workspace"),
    patched=Path("patched-workspace"),
    command=("python", "app.py"),
)


def side(output: str = "OUT", *, ran: bool = True, stable: bool = True) -> Side:
    return Side(
        ran=ran,
        output_digest=output if ran else "",
        output_bytes=len(output) if ran else 0,
        wall_s=1.0,
        peak_rss_bytes=1024,
        stable=stable and ran,
        detail="" if ran else "exit 1",
    )


@dataclass
class Harness:
    """Stands in for `revisions.compare`, and records what it was asked to run."""

    breaks: frozenset[tuple[str, ...]] = frozenset()
    varies: frozenset[tuple[str, ...]] = frozenset()
    rejects: frozenset[tuple[str, ...]] = frozenset()
    ran: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, given: Sequence[str]) -> Comparison:
        key = tuple(given)
        self.ran.append(key)
        if key in self.rejects:
            return Comparison(given=key, baseline=side(ran=False), patched=side(ran=False))
        baseline = side(stable=key not in self.varies)
        patched = side("CHANGED") if key in self.breaks else side(stable=key not in self.varies)
        return Comparison(given=key, baseline=baseline, patched=patched)


def reply(*inputs: Sequence[str], **extra: str) -> str:
    return json.dumps({"inputs": [list(given) for given in inputs], **extra})


@dataclass
class Scripted:
    """Answers each turn in order; an exhausted script has nothing more to try."""

    replies: list[str]
    stop_reason: str = "end_turn"
    asked: list[list[dict[str, Any]]] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    caps: list[int] = field(default_factory=list)
    ttls: list[str] = field(default_factory=list)

    def complete(self, **kwargs: Any) -> ModelResponse:
        self.asked.append(list(kwargs["messages"]))
        self.models.append(str(kwargs["model"]))
        self.caps.append(int(kwargs["max_tokens"]))
        self.ttls.append(str(kwargs["cache_ttl"]))
        text = self.replies.pop(0) if self.replies else reply()
        return ModelResponse(
            model=str(kwargs["model"]),
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason=self.stop_reason,
        )


def attack(client: Scripted, harness: Harness) -> PatchReview:
    return review_patch(metered(client), under_audit=UNDER_AUDIT, compare=harness)


def shown(client: Scripted, turn: int) -> str:
    return "\n".join(block["text"] for block in client.asked[turn][0]["content"])


# ------------------------------------------- the model designs, the harness runs


def test_the_harness_runs_what_the_model_designed() -> None:
    client = Scripted([reply(["a"], ["a", "b"])])
    harness = Harness()
    attack(client, harness)
    assert harness.ran == [("a",), ("a", "b")]


def test_the_verdict_is_the_harness_s_not_the_model_s() -> None:
    """The model says clean; the byte comparison says otherwise, and wins."""
    client = Scripted([reply(["a"], ["a", "b"], verdict="clean")])
    review = attack(client, Harness(breaks=frozenset({("a", "b")})))
    assert review.verdict is Verdict.BROKEN
    assert [c.given for c in review.reproducing] == [("a", "b")]
    assert review.route == "another_round"


def test_every_input_is_reported_not_only_the_break() -> None:
    """Otherwise nobody can tell a patch that survived twenty inputs from two."""
    client = Scripted([reply(["a"], ["b"], ["c"])])
    review = attack(client, Harness(breaks=frozenset({("c",)})))
    assert len(review.comparisons) == 3
    assert len(review.reproducing) == 1


def test_the_attack_stops_after_the_turn_that_broke() -> None:
    """One reproducing input sends the patch back; more attacks change nothing."""
    client = Scripted([reply(["a"], ["b"]), reply(["c"])])
    attack(client, Harness(breaks=frozenset({("b",)})))
    assert len(client.models) == 1


def test_a_repeated_input_is_not_run_twice() -> None:
    client = Scripted([reply(["a"]), reply(["a"], ["b"], ["b"])])
    harness = Harness()
    attack(client, harness)
    assert harness.ran == [("a",), ("b",)]


def test_twenty_attacks_at_most() -> None:
    """Four turns of five: the spec's bound, however much the model offers."""
    client = Scripted([reply(*([f"{t}-{i}"] for i in range(7))) for t in range(10)])
    harness = Harness()
    review = attack(client, harness)
    assert len(harness.ran) == MAX_TURNS * PER_TURN == 20
    assert len(client.models) == MAX_TURNS
    assert review.turns == MAX_TURNS


# ------------------------------------------------------- the verdicts that are not clean


def test_inputs_the_original_handled_and_nothing_broke_is_clean() -> None:
    client = Scripted([reply(["a"], ["b"])])
    review = attack(client, Harness())
    assert review.verdict is Verdict.CLEAN
    assert review.route == "clean"


def test_an_attack_never_mounted_is_not_clean() -> None:
    """The permissive failure: surviving an attack nobody made."""
    client = Scripted([reply()])
    review = attack(client, Harness())
    assert review.verdict is Verdict.UNATTACKED
    assert review.route == "escalate"
    assert len(client.models) == 1, "an empty list of inputs ends the attack"


def test_inputs_the_original_also_rejects_are_not_an_attack() -> None:
    client = Scripted([reply(["a"], ["b"])])
    review = attack(client, Harness(rejects=frozenset({("a",), ("b",)})))
    assert review.verdict is Verdict.UNATTACKED


def test_an_original_that_varies_on_its_own_is_escalated_not_passed() -> None:
    """Nothing broke, but on the input where the original disagreed with itself
    nothing could have been seen to break. A person decides."""
    client = Scripted([reply(["a"], ["b"])])
    review = attack(client, Harness(varies=frozenset({("b",)})))
    assert review.verdict is Verdict.UNSTABLE
    assert review.route == "escalate"


@pytest.mark.parametrize(
    ("stop_reason", "because"),
    [("max_tokens", f"cut off at {NON_STREAMING_MAX_TOKENS} tokens"), ("refusal", "declined")],
)
def test_a_cut_off_or_declined_turn_is_never_read(stop_reason: str, because: str) -> None:
    """A whole, valid reply -- so reading it before checking would run inputs the
    model had not finished choosing."""
    client = Scripted([reply(["a"])] * MAX_TURNS, stop_reason=stop_reason)
    harness = Harness()
    review = attack(client, harness)
    assert harness.ran == []
    assert review.verdict is Verdict.UNATTACKED
    assert because in review.notes[0]


def test_an_unreadable_turn_is_spent_and_the_next_is_asked() -> None:
    client = Scripted(["not json", reply(["a"])])
    harness = Harness()
    review = attack(client, harness)
    assert harness.ran == [("a",)]
    assert "could not be read" in review.notes[0]
    assert "could not be read" in shown(client, 1), "the next turn is told why"


def test_the_budget_refusing_a_turn_is_not_softened() -> None:
    client = Scripted([reply(["a"])])
    harness = Harness()
    with pytest.raises(BudgetExhaustedError):
        review_patch(
            metered(client, ceiling_eur=Decimal("0.000001")),
            under_audit=UNDER_AUDIT,
            compare=harness,
        )
    assert harness.ran == []


# -------------------------------------------------- no verdict without its evidence


def broken_comparison() -> Comparison:
    return Comparison(given=("a", "b"), baseline=side(), patched=side("CHANGED"))


def held_comparison() -> Comparison:
    return Comparison(given=("a",), baseline=side(), patched=side())


@pytest.mark.parametrize(
    ("verdict", "comparisons"),
    [
        (Verdict.CLEAN, (broken_comparison(),)),
        (Verdict.CLEAN, ()),
        (Verdict.BROKEN, (held_comparison(),)),
        (Verdict.BROKEN, ()),
        (Verdict.UNATTACKED, (held_comparison(),)),
    ],
)
def test_a_verdict_its_comparisons_contradict_cannot_be_built(
    verdict: Verdict, comparisons: tuple[Comparison, ...]
) -> None:
    """However the review was built -- by this module, by a checkpoint restored,
    by hand -- a `broken` needs a reproducing input and a `clean` needs an attack
    that held."""
    with pytest.raises(ValidationError, match="comparisons that show"):
        PatchReview(verdict=verdict, comparisons=comparisons, turns=1)


def test_a_verdict_the_comparisons_support_is_accepted() -> None:
    """The control for the refusals above."""
    review = PatchReview(verdict=Verdict.BROKEN, comparisons=(broken_comparison(),), turns=1)
    assert review.reproducing == (broken_comparison(),)


# ---------------------------------------------------- what the model is shown, and on what


def test_the_model_is_shown_the_patch_the_test_and_the_command_and_nothing_else() -> None:
    client = Scripted([reply(["a"])])
    attack(client, Harness())
    brief = shown(client, 0)
    for expected in (UNDER_AUDIT.diff, UNDER_AUDIT.test, "python app.py"):
        assert expected in brief
    assert "workspace" not in brief


def test_the_next_turn_is_shown_what_both_revisions_did() -> None:
    client = Scripted([reply(["a"]), reply(["b"])])
    attack(client, Harness())
    assert '["a"]: identical output' in shown(client, 1)


def test_the_brief_is_cached_and_the_history_is_not() -> None:
    """One breakpoint, on a block that is byte-identical every turn; the ledger is
    told the lifetime it asked for, because a write bills by it."""
    client = Scripted([reply(["a"]), reply(["b"])])
    attack(client, Harness())
    first, second = (turn[0]["content"] for turn in client.asked[:2])
    assert first[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert all("cache_control" not in block for block in (first[1], second[1]))
    assert first[0]["text"] == second[0]["text"]
    assert set(client.ttls) == {"5m"}


def test_the_ledger_is_told_the_lifetime_the_marker_asks_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two must move together: a write bills 1.25x at five minutes and 2x at
    an hour, and only the request says which. Checked at a lifetime other than
    the meter's default, because agreeing with the default proves nothing -- the
    sabotage pass found exactly that."""
    monkeypatch.setattr("coldfix.evidence.adversary.CACHE_TTL", "1h")
    client = Scripted([reply(["a"])])
    attack(client, Harness())
    assert client.asked[0][0]["content"][0]["cache_control"]["ttl"] == "1h"
    assert set(client.ttls) == {"1h"}


def test_each_turn_is_a_frontier_attack_design_that_cannot_escalate() -> None:
    """Creative: §3 has no check that could catch a weak attack, so there is no
    failed check to escalate on and no cheaper tier to start from."""
    client = Scripted([reply(["a"])])
    meter = metered(client)
    review_patch(meter, under_audit=UNDER_AUDIT, compare=Harness())
    bills = meter.budget.ledger.calls
    assert len(bills) == len(client.models) == 2, "one clean turn, then an empty one ends it"
    assert {(b.agent, b.phase, b.step_class) for b in bills} == {
        (Agent.ADVERSARY, Phase.PATCH_AUDIT, StepClass.CREATIVE)
    }
    assert set(client.models) == {FRONTIER}
    assert set(client.caps) == {NON_STREAMING_MAX_TOKENS}
    with pytest.raises(NoValidatorError):
        meter.model_for(ATTACK, escalation=1)


# ---------------------------------------------- the whole path, on a real bad patch

ORIGINAL = """\
import sys

def shout(word):
    return word.upper()

print(" ".join(shout(w) for w in sys.argv[1:]))
"""

CACHED = """\
import sys

_seen = {}

def shout(word):
    if not _seen:
        _seen["only"] = word.upper()
    return _seen["only"]

print(" ".join(shout(w) for w in sys.argv[1:]))
"""


class SteadyRunner:
    """Really runs the child; reports usage the test controls."""

    def run(self, command: Sequence[str], cwd: Path, env: Mapping[str, str] | None) -> ChildResult:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
        )
        return ChildResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            usage=Usage(cpu_s=0.5, peak_rss_bytes=2048, read_blocks=0, write_blocks=0),
        )


def test_a_real_cache_keyed_on_the_first_argument_is_caught(tmp_path: Path) -> None:
    """The author tested one word. The Adversary tries one, then two -- and the
    real harness, running both revisions, sees the second output change."""
    baseline, patched = tmp_path / "baseline", tmp_path / "patched"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "app.py").write_text(ORIGINAL, encoding="utf-8")
    (patched / "app.py").write_text(CACHED, encoding="utf-8")
    under_audit = PatchUnderAudit(
        diff="--- a/app.py\n+++ b/app.py\n",
        test="def test_shout(): assert shout('a') == 'A'",
        baseline=baseline,
        patched=patched,
        command=(sys.executable, "app.py"),
    )
    client = Scripted([reply(["hello"]), reply(["hello", "world"])])

    review = review_patch(
        metered(client),
        under_audit=under_audit,
        compare=partial(compare, under_audit=under_audit, runner=SteadyRunner()),
    )

    assert review.verdict is Verdict.BROKEN
    assert [c.given for c in review.reproducing] == [("hello", "world")]
    assert review.turns == 2

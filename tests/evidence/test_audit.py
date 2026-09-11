"""S-21.1 — the four attacks, none of which calls a model.

Every attack has a test that supplies an input which fails it. An attack that
cannot fire is a line of code that reads like a safeguard, and this file is
mostly about proving each one can.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from coldfix.collect.ablation import AblationMeasurement
from coldfix.collect.measurement import BareMeasurement, Mode, Spread
from coldfix.cost.accounting import Agent, Phase, TokenUsage
from coldfix.cost.budget import BudgetExhaustedError
from coldfix.cost.routing import DEFAULT_TIER_MODELS, Tier
from coldfix.evidence.audit import (
    GUARD_MARGIN,
    MINIMUM_MEASUREMENTS,
    Verdict,
    attack,
)
from coldfix.evidence.auditor import Presented, review
from coldfix.evidence.ledger import Basis, Citation, Claim, Finding, Ledger, Location, Proof
from coldfix.llm.client import ModelResponse
from fixtures.metering import metered

HERE = Location(file="app/models.py", line=112, symbol="Author.books")


def bare(
    identifier: str,
    median: float,
    *,
    spread: float = 0.0,
    peak: int = 1024,
    read: int = 0,
) -> BareMeasurement:
    half = median * spread / 2
    return BareMeasurement(
        measurement_id=identifier,
        command=("python", "app.py"),
        repeats=5,
        output_digest="0" * 64,
        output_bytes=1,
        wall=Spread(median=median, low=median - half, high=median + half),
        cpu_s=median,
        mode=Mode.COMPUTING,
        peak_rss_bytes=peak,
        read_bytes=read,
        write_bytes=0,
    )


def ablation(
    identifier: str,
    *,
    before: BareMeasurement,
    after: BareMeasurement,
    share: float,
) -> AblationMeasurement:
    return AblationMeasurement(
        measurement_id=identifier,
        symbol="Author.books",
        file="app/models.py",
        line=112,
        before=before,
        after=after,
        share_removed=share,
        output_changed=True,
    )


def audited(
    *,
    share: float = 0.784,
    baseline_spread: float = 0.02,
    after_peak: int = 1024,
    after_read: int = 0,
    extra_evidence: bool = True,
) -> tuple[Ledger, Finding]:
    """A finding and the ledger behind it, with the knobs each attack reads."""
    ledger = Ledger()
    before = bare("m-before", 2.41, spread=baseline_spread)
    after = bare("m-after", 2.41 * (1 - share), peak=after_peak, read=after_read)
    ledger.record(before)
    ledger.record(after)
    ledger.record(ablation("m-abl", before=before, after=after, share=share))

    evidence = [Citation(measurement_id="m-before", field="cpu_s", value=2.41)]
    if extra_evidence:
        evidence.append(Citation(measurement_id="m-abl", field="share_removed", value=share))

    claim = Claim(
        kind="repeated_query",
        summary="the serializer reads .books inside the loop",
        location=HERE,
        evidence=tuple(evidence),
        basis=Basis.ABLATION,
        payoff=share,
        proof=Proof(
            measurement_id="m-abl",
            before=2.41,
            after=2.41 * (1 - share),
            share_removed=share,
        ),
    )
    return ledger, ledger.attest(claim)


# ------------------------------------------------------------ nothing is asked


def test_no_attack_calls_a_model() -> None:
    """Counting, curve fitting and byte comparison are code. An audit that asked
    a model whether a payoff cleared the noise floor would be paying for an
    opinion about a number it could compute."""
    source = Path("src/coldfix/evidence/audit.py").read_text(encoding="utf-8")
    for forbidden in ("ModelClient", "anthropic", "complete(", "system="):
        assert forbidden not in source


def test_a_sound_finding_survives_all_four() -> None:
    ledger, finding = audited()
    verdict = attack(finding, ledger=ledger)
    assert verdict.verdict is Verdict.SOUND
    assert verdict.survived
    assert all(a.held for a in verdict.attacks)
    assert verdict.why() == "survived every attack"


def test_all_four_attacks_are_always_reported() -> None:
    """Including the ones that held. A reader needs to know what was tried, not
    only what broke."""
    ledger, finding = audited()
    names = [a.name for a in attack(finding, ledger=ledger).attacks]
    assert names == [
        "still_attested",
        "enough_measurements",
        "above_the_noise",
        "no_guard_worsened",
    ]


# ------------------------------------------------ each attack can actually fire


def test_a_payoff_inside_the_noise_floor_is_refused() -> None:
    """The attack worth the most. A 3% saving on a workload that varies by 20%
    between identical runs has not been observed; it has been guessed at from
    inside the spread."""
    ledger, finding = audited(share=0.03, baseline_spread=0.20)
    verdict = attack(finding, ledger=ledger)
    assert verdict.verdict is Verdict.UNSOUND, "inside the noise is wrong, not unproven"
    assert "inside the spread has not been observed" in verdict.why()


def test_a_payoff_just_above_the_noise_floor_survives() -> None:
    """The boundary matters: an attack that rejected everything would be as
    useless as one that rejected nothing."""
    ledger, finding = audited(share=0.25, baseline_spread=0.20)
    assert attack(finding, ledger=ledger).verdict is Verdict.SOUND


def test_a_payoff_standing_on_too_few_measurements_is_refused() -> None:
    """A comparison drawn from two numbers is a line through two points."""
    ledger = Ledger()
    before = bare("m-solo", 2.41, spread=0.01)
    ledger.record(before)
    ledger.record(ablation("m-abl", before=before, after=before, share=0.5))
    claim = Claim(
        kind="k",
        summary="s",
        location=HERE,
        evidence=(Citation(measurement_id="m-solo", field="cpu_s", value=2.41),),
        basis=Basis.ABLATION,
        payoff=0.5,
        proof=Proof(measurement_id="m-abl", before=2.41, after=2.41, share_removed=0.5),
    )
    verdict = attack(ledger.attest(claim), ledger=ledger)
    assert verdict.verdict is Verdict.NEEDS_EVIDENCE, "too little is unproven, not wrong"
    assert "line through two points" in verdict.why()
    assert MINIMUM_MEASUREMENTS == 3


def test_a_saving_paid_for_in_memory_is_refused() -> None:
    """Faster and three times the memory is a trade to be shown, not a win to be
    reported."""
    ledger, finding = audited(after_peak=4096)
    verdict = attack(finding, ledger=ledger)
    assert verdict.verdict is Verdict.NEEDS_EVIDENCE
    assert "made something else worse" in verdict.why()
    assert "peak_rss_bytes" in verdict.why()


def test_a_guard_that_drifts_within_the_margin_is_not_a_regression() -> None:
    """A peak-memory figure that moved by a kilobyte moved because it is a
    measurement, not because anything changed."""
    ledger, finding = audited(after_peak=int(1024 * (GUARD_MARGIN - 0.01)))
    assert attack(finding, ledger=ledger).verdict is Verdict.SOUND


def test_a_guard_that_was_zero_and_is_now_large_is_the_clearest_regression() -> None:
    """Written after the first version skipped it. `was > 0 and now > was * MARGIN`
    reads as careful and silently ignores nothing-to-something -- an ablation that
    removed work and started reading from disk passed every attack."""
    ledger, finding = audited(after_read=99_999)
    assert "read_bytes" in attack(finding, ledger=ledger).why()


def test_a_finding_the_ledger_no_longer_supports_is_refused() -> None:
    """A `Finding` survives a checkpoint and this pipeline rewinds. One restored
    into a run whose measurements are gone carries numbers nothing supports."""
    _, finding = audited()
    # A different ledger entirely: the run this finding came from is gone.
    verdict = attack(finding, ledger=Ledger())
    assert verdict.verdict is Verdict.UNSOUND
    assert "no longer supports this claim" in verdict.why()


# --------------------------------------------------- unproven versus contradicted


def test_too_little_evidence_sends_the_run_back_and_a_contradiction_does_not() -> None:
    """The distinction the graph acts on. `needs_evidence` means look again;
    `unsound` means a claim the evidence contradicts is not one experiment away
    from being true."""
    ledger, thin = audited(after_peak=4096)
    assert attack(thin, ledger=ledger).verdict is Verdict.NEEDS_EVIDENCE

    other, contradicted = audited(share=0.02, baseline_spread=0.30)
    assert attack(contradicted, ledger=other).verdict is Verdict.UNSOUND


def test_a_fatal_failure_outranks_a_survivable_one() -> None:
    """Both broken at once must not read as merely unproven."""
    ledger, finding = audited(share=0.02, baseline_spread=0.30, after_peak=4096)
    assert attack(finding, ledger=ledger).verdict is Verdict.UNSOUND


# ------------------------------------------------------------ suspected findings


def test_a_suspicion_is_not_attacked_for_a_payoff_it_never_claimed() -> None:
    """It carries no number, so there is no comparison to be too thin and no
    noise floor to clear. The attacks that do not apply say so rather than
    silently passing."""
    ledger = Ledger()
    ledger.record(bare("m-1", 2.41))
    finding = ledger.attest(
        Claim(
            kind="allocation_churn",
            summary="10,500 allocations per item",
            location=HERE,
            evidence=(Citation(measurement_id="m-1", field="cpu_s", value=2.41),),
        )
    )
    verdict = attack(finding, ledger=ledger)
    assert verdict.verdict is Verdict.SOUND
    assert "no payoff claimed" in verdict.attacks[1].detail
    assert "no measured payoff" in verdict.attacks[2].detail


# ------------------------------------------------ S-21.2, the single model call


class Answering:
    """Replies however the test needs, and records what it was shown."""

    def __init__(self, text: str, *, refused: bool = False) -> None:
        self.text = text
        self.refused = refused
        self.calls = 0
        self.shown: list[str] = []
        self.system = ""

    def complete(self, **kwargs: object) -> ModelResponse:
        self.calls += 1
        self.system = str(kwargs["system"])
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        self.shown.append(str(messages[0]["content"]))
        return ModelResponse(
            model="claude-opus-5",
            text=self.text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="refusal" if self.refused else "end_turn",
        )


def test_the_model_is_not_asked_about_a_finding_arithmetic_already_rejected() -> None:
    """The economics of the node. There is nothing a reviewer could add to a
    claim whose payoff is inside the noise floor."""
    ledger, finding = audited(share=0.02, baseline_spread=0.30)
    client = Answering('{"verdict": "sound", "because": "looks fine"}')
    reviewed = review(finding, attack(finding, ledger=ledger), meter=metered(client))
    assert client.calls == 0
    assert reviewed.verdict is Verdict.UNSOUND


def test_a_finding_that_survived_the_attacks_is_reviewed_once() -> None:
    ledger, finding = audited()
    client = Answering('{"verdict": "sound", "because": "the counts show the mechanism"}')
    reviewed = review(finding, attack(finding, ledger=ledger), meter=metered(client))
    assert client.calls == 1
    assert reviewed.verdict is Verdict.SOUND
    assert "the counts show the mechanism" in reviewed.why() or reviewed.survived


def test_the_reviewer_can_overturn_a_finding_the_arithmetic_accepted() -> None:
    """The one thing arithmetic cannot settle: whether the mechanism described is
    the mechanism the numbers show."""
    ledger, finding = audited()
    client = Answering(
        '{"verdict": "unsound", "because": "nothing cited is a count, so a per-row claim '
        'is not supported"}'
    )
    reviewed = review(finding, attack(finding, ledger=ledger), meter=metered(client))
    assert reviewed.verdict is Verdict.UNSOUND
    assert "not supported" in reviewed.why()


def test_everything_is_pre_loaded_and_no_tool_is_offered() -> None:
    """Pre-loading *is* the isolation. A reviewer that could go and look would be
    forming its own view of a repository; what it was asked to review is a chain
    of evidence."""
    ledger, finding = audited()
    client = Answering('{"verdict": "sound", "because": "ok"}')
    review(finding, attack(finding, ledger=ledger), meter=metered(client))
    shown = client.shown[0]
    assert "app/models.py:112" in shown
    assert "m-before.cpu_s" in shown
    # Normalised, because the assertion is about what the prompt says and not
    # where its lines happen to wrap.
    assert "cannot go and look at anything" in " ".join(client.system.split())


def test_the_reviewer_is_never_given_the_reasoning_that_produced_the_finding() -> None:
    """No field for it, the same way the Adversary has none for the Surgeon's."""
    fields = set(Presented.model_fields)
    assert not fields & {"reasoning", "transcript", "why", "rationale", "turns"}


def test_an_unreadable_reply_is_unproven_rather_than_approved() -> None:
    """Defaulting to the permissive answer is how a broken reviewer becomes an
    approving one."""
    ledger, finding = audited()
    client = Answering("I think this one is probably fine.")
    reviewed = review(finding, attack(finding, ledger=ledger), meter=metered(client))
    assert reviewed.verdict is Verdict.NEEDS_EVIDENCE
    assert "could not be read" in reviewed.why()


def test_a_refusal_is_unproven_rather_than_approved() -> None:
    ledger, finding = audited()
    client = Answering("", refused=True)
    reviewed = review(finding, attack(finding, ledger=ledger), meter=metered(client))
    assert reviewed.verdict is Verdict.NEEDS_EVIDENCE
    assert "declined" in reviewed.why()


# ------------------------------------------------ S-26.1, the review is metered


def test_the_review_runs_on_the_frontier_and_is_billed_as_the_finding_auditor() -> None:
    """Whether a mechanism follows from the numbers has no deterministic check,
    so the router keeps it on the frontier tier and nothing may cascade it."""
    ledger, finding = audited()
    meter = metered(Answering('{"verdict": "sound", "because": "ok"}'))
    review(finding, attack(finding, ledger=ledger), meter=meter)
    [call] = meter.budget.ledger.calls
    assert call.model == DEFAULT_TIER_MODELS[Tier.FRONTIER]
    assert call.agent is Agent.FINDING_AUDITOR
    assert call.phase is Phase.FINDING_AUDIT


def test_a_review_the_budget_refuses_is_never_asked() -> None:
    """Raised rather than folded into a verdict: a review that was never asked
    is not one that found the finding unproven."""
    ledger, finding = audited()
    client = Answering('{"verdict": "sound", "because": "ok"}')
    with pytest.raises(BudgetExhaustedError):
        review(
            finding,
            attack(finding, ledger=ledger),
            meter=metered(client, ceiling_eur=Decimal("0.000001")),
        )
    assert client.calls == 0

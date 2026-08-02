"""S-0.8 — can a model select the right instrument?

Presents each recorded scenario N times and scores three things separately,
because they fail independently:

1. **Instrument selection** — did it pick a defensible next experiment?
2. **Trap avoidance** — did it stay out of the plausible-but-wrong conclusion?
3. **Finding discipline** — did it agree a finding was or was not warranted?

The third is scored on its own because it is the one with an invariant behind
it. A model can pick a sensible instrument and still manufacture a finding from
noise, and that combination would look like a pass under a single aggregate
score.

Responses are constrained to a schema, so scoring is a comparison of structured
fields rather than a reading of prose. That removes the judgement call from the
scorer, which matters when the scorer and the subject are the same model family.

Run:
    ANTHROPIC_API_KEY=... uv run --with anthropic python run.py
    ANTHROPIC_API_KEY=... uv run --with anthropic python run.py --repeats 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from scenarios import RESPONSE_SCHEMA, SCENARIOS, Scenario

MODEL = "claude-opus-5"

SYSTEM = """\
You are the Diagnostician in a performance-investigation system. You are given \
the results of an experiment that has already been run, and you choose the next \
experiment.

Rules you operate under:

- A conclusion drawn from reading code is not a finding. Only measurements \
support conclusions.
- Guard counters accompany every timing measurement. A change in time without a \
corresponding change in queries, bytes, or rows returned is not evidence of a \
performance defect.
- Null results are valid output. "Nothing found" is a legitimate answer and is \
preferable to a finding the evidence does not support.
- An N+1 means query count grows with rows returned. A high but constant query \
count is not an N+1.

Available instruments:

- ablation — remove or stub a component, measure the change. Localizes cost.
- proportional_perturbation — slow a component by a known fraction. Estimates \
what optimizing it would gain.
- scaling — vary input size, fit the growth curve.
- observation — attach an instrument (allocation counter, profiler, query log) \
to a known path.
- temporal_bisection — bisect over commits to find where cost appeared.
- fault_injection — degrade a dependency, measure behaviour under partial failure.
- substitution — replace an implementation with an alternative, compare.
- none_report_no_finding — the evidence does not support continuing.
"""

PROMPT = """\
Experiment results:

{evidence}

Choose the next experiment."""


def ask(client: Any, scenario: Scenario) -> dict[str, Any]:
    """One structured response for one scenario."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        messages=[{"role": "user", "content": PROMPT.format(evidence=scenario.evidence)}],
    )
    if response.stop_reason == "refusal":
        raise SystemExit(f"request refused on {scenario.name}; nothing to score")
    text = next(block.text for block in response.content if block.type == "text")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def score(scenario: Scenario, answer: dict[str, Any]) -> dict[str, bool]:
    """Three independent checks. None of them reads prose for meaning."""
    instrument_ok = answer["next_instrument"] in scenario.acceptable_instruments

    haystack = f"{answer['conclusion']} {answer['why']}".lower()
    trap_ok = not any(bad in haystack for bad in scenario.forbidden_conclusions)

    finding_ok = bool(answer["finding_warranted"]) == scenario.finding_warranted

    return {
        "instrument": instrument_ok,
        "trap": trap_ok,
        "finding_discipline": finding_ok,
    }


def _rate(runs: list[dict[str, Any]], key: str) -> float:
    """Pass rate for one score across a scenario's repeats."""
    return sum(run["score"][key] for run in runs) / len(runs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. This spike measures a model's judgement "
            "and cannot be run offline."
        )

    import anthropic

    client = anthropic.Anthropic()
    results: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        per_run = []
        chosen: Counter[str] = Counter()
        for _ in range(args.repeats):
            answer = ask(client, scenario)
            marks = score(scenario, answer)
            chosen[answer["next_instrument"]] += 1
            per_run.append({"answer": answer, "score": marks})

        instrument_rate = _rate(per_run, "instrument")
        trap_rate = _rate(per_run, "trap")
        finding_rate = _rate(per_run, "finding_discipline")

        results.append(
            {
                "scenario": scenario.name,
                "tags": list(scenario.tags),
                "repeats": args.repeats,
                "instrument_rate": instrument_rate,
                "trap_rate": trap_rate,
                "finding_discipline_rate": finding_rate,
                "instruments_chosen": dict(chosen),
                "runs": per_run,
            }
        )

        print(
            f"{scenario.name:<38} "
            f"instrument {instrument_rate:>5.0%}  "
            f"trap {trap_rate:>5.0%}  "
            f"finding {finding_rate:>5.0%}   "
            f"{dict(chosen)}"
        )

    traps = [r for r in results if "trap" in r["tags"]]
    print(
        f"\ntrap scenarios: {len(traps)}  |  "
        f"mean trap-avoidance {sum(r['trap_rate'] for r in traps) / len(traps):.0%}  |  "
        f"mean finding-discipline "
        f"{sum(r['finding_discipline_rate'] for r in traps) / len(traps):.0%}"
    )

    out = Path(__file__).parent / "results" / "selection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "results": results}, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

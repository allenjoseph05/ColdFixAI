"""One run's worth of real nodes, assembled against doubles. S-28.5, S-29.2.

Extracted from `test_end_to_end.py` when S-29.2 needed the same assembly in a
**subprocess**: the crash harness has to drive the real adapters, and building a
second copy of this would let the two drift until the crash tests were proving
something about a pipeline the composition check never runs.

Not a `test_` module, because the subprocess imports it as an entry point's
dependency rather than as a test. The doubles it builds on still come from
`pipeline.test_nodes`, which is the import `test_end_to_end.py` already documents
as safe: `tests/pipeline` is a package, so the module resolves under exactly one
name.

**What is real here and what is not.** The seven node functions, the routing, the
meter, the budget, the ledger and the failing-test gate are production code. The
model is scripted, the toolbox is a fake that mints deterministic measurement
ids, and the half that needs a worktree and a container -- applying a candidate,
comparing two revisions -- is supplied as callables.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coldfix.agent.prompt import SYSTEM as SCAN_AGENT
from coldfix.agent.scan import Bounds, Phase
from coldfix.cost.accounting import TokenUsage
from coldfix.evidence.adversary import SYSTEM as ADVERSARY
from coldfix.evidence.auditor import SYSTEM as AUDITOR
from coldfix.evidence.falsify import SYSTEM as FALSIFY
from coldfix.evidence.falsify import falsify
from coldfix.evidence.ledger import Finding, Ledger
from coldfix.evidence.optimizer import SYSTEM as OPTIMIZER
from coldfix.evidence.repair import Candidate, Falsified, Scored
from coldfix.evidence.revisions import Comparison, PatchUnderAudit, Side
from coldfix.llm.client import ModelResponse
from coldfix.pipeline.nodes import Repairs, Resources
from fixtures.metering import metered
from pipeline.test_nodes import DRIVER, SOURCE, FakeDocker, FakeTools, act, finding_payload

DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n"
    "-        return [book for author in authors for book in author.books]\n"
    "+        return Book.objects.filter(author__in=authors)\n"
)
APPROACH = "prefetch the authors"


@dataclass
class ByCaller:
    """Answers each call by which agent is asking, and records the order."""

    finds: bool = True
    log: list[str] = field(default_factory=list)
    turns: int = 0
    rounds: int = 0
    attacks: int = 0

    def complete(self, **kwargs: Any) -> ModelResponse:
        who, text = self._answer(str(kwargs["system"]))
        self.log.append(who)
        return ModelResponse(
            model=str(kwargs["model"]),
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    def _answer(self, system: str) -> tuple[str, str]:
        if system == SCAN_AGENT:
            self.turns += 1
            if self.turns == 1:
                return "scan", act("measure", command=DRIVER)
            return "scan", act("submit", findings=[finding_payload()] if self.finds else [])
        if system == AUDITOR:
            return "auditor", json.dumps({"verdict": "sound", "because": "the count is cited"})
        if system == FALSIFY:
            return "falsify", json.dumps(
                {"test": "def test_one_query(): assert queries() == 1", "counts": "SELECTs"}
            )
        if system == OPTIMIZER:
            self.rounds += 1
            # The same approach every round, as a tiring model would. The archive
            # is what refuses to measure it a second time.
            return "optimizer", json.dumps({"candidates": [{"approach": APPROACH, "diff": DIFF}]})
        if system == ADVERSARY:
            self.attacks += 1
            inputs = [["one"], ["one", "two"]] if self.attacks == 1 else []
            return "adversary", json.dumps({"inputs": inputs})
        message = f"nobody owns this prompt: {system[:60]!r}"
        raise AssertionError(message)


def seams(resources: Resources, *, breaks: bool = False) -> Repairs:
    """The half that needs a worktree and a container, faked; the gate, real."""

    def apply(_: Falsified, candidate: Candidate) -> Scored:
        return Scored(
            candidate=candidate,
            measurement_id=f"m-{candidate.identifier}",
            wall_s=0.5,
            peak_rss_bytes=81_234,
            outputs_match=True,
            tests_pass=True,
        )

    def under_audit(diff: str) -> PatchUnderAudit:
        return PatchUnderAudit(
            diff=diff,
            test="def test_one_query(): assert queries() == 1",
            baseline=Path("baseline"),
            patched=Path("patched"),
            command=("python", "app.py"),
        )

    def compare(_: PatchUnderAudit) -> Any:
        def run(given: Sequence[str]) -> Comparison:
            side = Side(
                ran=True,
                output_digest="d",
                output_bytes=1,
                wall_s=1.0,
                peak_rss_bytes=1,
                stable=True,
            )
            patched = side.model_copy(update={"output_digest": "other"}) if breaks else side
            return Comparison(given=tuple(given), baseline=side, patched=patched)

        return run

    def write_and_run(finding: Finding, source: str) -> Falsified:
        return falsify(
            resources.meter,
            finding=finding,
            source=source,
            run=lambda _: (1, "AssertionError: expected 1 query, got 161"),
        )

    return Repairs(falsify=write_and_run, apply=apply, under_audit=under_audit, compare=compare)


def assembled(tmp_path: Path, client: ByCaller, *, breaks: bool = False) -> Resources:
    """One run's resources, with the repair seams bound to the same meter."""
    root = tmp_path / "subject"
    root.mkdir(exist_ok=True, parents=True)
    (root / "app.py").write_text(SOURCE, encoding="utf-8")
    ledger = Ledger()
    tools = FakeTools(ledger)
    bare = Resources(
        meter=metered(client),
        ledger=ledger,
        # One toolbox whatever image is asked for: these runs drive the nodes,
        # and which container they would have used is `refuse`'s business.
        toolbox=lambda _image: tools,
        repository=root,
        image="subject:latest",
        docker=FakeDocker(),
        read_source=lambda _: SOURCE,
        # Named, never built. `refuse` asks for the wheel, and the real one
        # shells out to `uv build` -- which would put a package build inside
        # every composition and crash-resume run.
        wheel=lambda: root / "coldfix-0.1.0-py3-none-any.whl",
        ground_bounds=Bounds(turns=4, until_phase=Phase.MEASURING),
        scan_bounds=Bounds(turns=4),
    )
    return Resources(**{**bare.__dict__, "repairs": seams(bare, breaks=breaks)})

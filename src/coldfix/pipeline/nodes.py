"""What each of the seven nodes does. **S-28.3, ADR 183.**

The graph owns the order, the epics own the work, and this owns the translation.
`PipelineState` is JSON because a checkpoint is JSON, and every entry point takes
live objects -- so a node is a *rehydrate, call, serialize* sandwich, and the two
slices are where a run loses things.

**`Resources` is everything a resumed run must be handed again**, because none of
it survives JSON: the meter, the ledger, the toolbox, the repository, the image.
Its length is the inventory rather than a smell.

**The ledger is restored from the state, never re-measured.** `audit_finding`
re-attests every cited number, and a resumed run starts with an empty ledger --
see `Ledger.restore`.

**Which finding is being worked on is carried by `resolved`.** The state has no
`target` channel and does not need one: `audit_finding` marks a finding sound,
and `optimize` takes the sound one that has no patch yet. One map, read two ways,
rather than a second channel that can disagree with it.

**A missing seam raises rather than routing.** The repair half needs a test
writer, a way to run a test, a way to apply and measure a candidate, and the two
revisions to compare. A pipeline assembled without them is misassembled, and
saying `nothing_beat_baseline` would report a search that never ran as a search
that found nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coldfix.agent.prompt import SYSTEM as SCAN_SYSTEM
from coldfix.agent.scan import Bounds, Outcome, Phase, Toolbox, scan
from coldfix.collect.tiers import Docker, Tier, detect
from coldfix.evidence.adversary import Compare, review_patch
from coldfix.evidence.audit import Verdict, attack
from coldfix.evidence.auditor import review
from coldfix.evidence.falsify import Falsify
from coldfix.evidence.ledger import Claim, Finding, Ledger
from coldfix.evidence.optimizer import Optimizer
from coldfix.evidence.repair import Apply, Candidate, Falsified, Scored, search
from coldfix.evidence.revisions import PatchUnderAudit
from coldfix.llm.metered import Meter
from coldfix.pipeline.graph import Step, Wiring
from coldfix.pipeline.state import PipelineState
from coldfix.sandbox.production import ProductionGuardError, VerifiedDatabase
from coldfix.sandbox.realtime import (
    IncompleteScreeningError,
    RealTimeSystemError,
    ScreenedRepository,
)

GROUND_BOUNDS = Bounds(turns=25, wall_seconds=900.0, until_phase=Phase.MEASURING)
"""Grounding gets its own budget (ADR 175, reason 5): a slow install must not eat
the investigation's turns. It ends the moment a measurement verifies."""

SCAN_BOUNDS = Bounds(turns=40, wall_seconds=1200.0)
"""What `15-full-architecture.md` bounds a scan at."""


class NodeError(Exception):
    """A node was asked to run without what it needs. Never an answer."""


@dataclass(frozen=True)
class Repairs:
    """What the repair half needs and cannot build for itself.

    Each is a seam because each needs a worktree, a container, or a prompt that is
    its own story -- and each is what makes `optimize` and `audit_patch` testable
    without Docker.
    """

    falsify: Falsify
    """A finding and its source in; the proof a test failed on unpatched code out.

    One seam rather than a writer and a runner (ADR 185), so `must_fail` stays the
    only thing that can mint a `Falsified` and this node cannot assemble one from
    a string it liked the look of."""

    apply: Apply
    under_audit: Callable[[str], PatchUnderAudit]
    """The winning diff in; the two revisions and how to run them out."""

    compare: Callable[[PatchUnderAudit], Compare]


@dataclass(frozen=True)
class Resources:
    """The live objects the seven nodes share. None of it survives a checkpoint."""

    meter: Meter
    ledger: Ledger
    toolbox: Toolbox
    repository: Path
    image: str
    docker: Docker
    read_source: Callable[[str], str]
    database_url: str | None = None
    repairs: Repairs | None = None
    ground_bounds: Bounds = GROUND_BOUNDS
    scan_bounds: Bounds = SCAN_BOUNDS
    system: str = SCAN_SYSTEM
    findings_per_scan: int = 8
    """A cap on ids minted in one pass, so a runaway submission cannot fill the state."""


# ------------------------------------------------------------------ the nodes


def refuse(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Decline what this system must not touch, before anything is built.

    Three checks, each a constructor or a probe rather than a rule somebody has to
    remember to apply: a hard real-time system (where a caching win improves every
    metric here while degrading what actually matters), a database that is not
    provably a test database, and an image nothing can run a process in.
    """
    project = dict(state.project)
    try:
        ScreenedRepository(resources.repository)
        if resources.database_url is not None:
            VerifiedDatabase(resources.database_url)
    except (RealTimeSystemError, IncompleteScreeningError, ProductionGuardError) as declined:
        return _refused(project, str(declined))

    capabilities = detect(resources.image, root=resources.repository, docker=resources.docker)
    if capabilities.tier is Tier.UNMEASURABLE:
        return _refused(project, capabilities.statement())

    return {
        "route": "proceed",
        "project": {
            **project,
            "image": resources.image,
            "tier": int(capabilities.tier),
            "available": list(capabilities.available),
            "unavailable": list(capabilities.unavailable),
            "statement": capabilities.statement(),
        },
    }


def ground(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Make the program measurable, and stop the moment it measures."""
    _restore(resources, state)
    outcome = scan(
        resources.meter,
        toolbox=resources.toolbox,
        ledger=resources.ledger,
        system=resources.system,
        bounds=resources.ground_bounds,
    )
    written = _measurements(resources, state)
    if outcome.stopped_by != "grounded":
        return {
            "route": "unmeasurable",
            "measurements": written,
            "coverage": {**state.coverage, "grounding": outcome.stopped_by},
        }
    return {
        "route": "runnable",
        "runnable": _runnable(outcome),
        "measurements": written,
        "coverage": {**state.coverage, "grounding": "measured"},
    }


def scan_for_waste(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Find and prove waste, starting from what grounding established."""
    _restore(resources, state)
    outcome = scan(
        resources.meter,
        toolbox=resources.toolbox,
        ledger=resources.ledger,
        system=resources.system,
        bounds=resources.scan_bounds,
        phase=Phase.MEASURING,
        brief=_brief(state.runnable),
    )
    written = _measurements(resources, state)
    coverage = {**state.coverage, "scan": outcome.stopped_by}
    if not outcome.findings:
        return {"route": "nothing_found", "measurements": written, "coverage": coverage}

    kept = outcome.findings[: resources.findings_per_scan]
    minted = {
        _mint(state, offset): finding.model_dump(mode="json") for offset, finding in enumerate(kept)
    }
    return {
        "route": "findings",
        "findings": {**state.findings, **minted},
        "measurements": written,
        "coverage": coverage,
    }


def audit_finding(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Four code attacks, then one model call only if they all held."""
    _restore(resources, state)
    identifier, finding = _next_unaudited(state)
    audited = attack(finding, ledger=resources.ledger)
    if audited.verdict is Verdict.SOUND:
        audited = review(finding, audited, meter=resources.meter)

    return {
        "route": audited.verdict.value,
        "verdict": audited.verdict.value,
        "resolved": {
            **state.resolved,
            identifier: {"verdict": audited.verdict.value, "why": audited.why()},
        },
    }


def optimize(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """A failing test first, then a measured search over candidate fixes."""
    _restore(resources, state)
    repairs = _repairs(resources)
    identifier, finding = _next_sound(state)
    source = resources.read_source(finding.claim.location.file)

    falsified = repairs.falsify(finding, source)
    baseline = _baseline(resources, state)
    archive = search(
        falsified=falsified,
        baseline=baseline,
        propose=Optimizer(
            meter=resources.meter, finding=finding, source=source, falsified=falsified
        ),
        apply=repairs.apply,
    )

    measured = [entry.model_dump(mode="json") for entry in archive.scored]
    winner = archive.winner
    if winner is None:
        return {
            "route": "nothing_beat_baseline",
            "candidates": measured,
            "resolved": {
                **state.resolved,
                identifier: {"verdict": "sound", "outcome": archive.summary()},
            },
        }
    return {
        "route": "candidate",
        "candidates": measured,
        "repaired": {
            "finding": identifier,
            "approach": winner.candidate.approach,
            "diff": winner.candidate.diff,
            "test": falsified.test,
            "measurement_id": winner.measurement_id,
            "share_removed": winner.share_removed(baseline),
            "trades": [entry.candidate.approach for entry in archive.trades],
        },
    }


def audit_patch(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Attack the winning patch with inputs the harness runs on both revisions."""
    _restore(resources, state)
    repairs = _repairs(resources)
    repaired = _repaired(state)
    under_audit = repairs.under_audit(str(repaired["diff"]))

    reviewed = review_patch(
        resources.meter, under_audit=under_audit, compare=repairs.compare(under_audit)
    )
    return {
        "route": reviewed.route,
        "verdict": reviewed.verdict.value,
        "audited": reviewed.model_dump(mode="json"),
    }


def ship(resources: Resources, state: PipelineState) -> Mapping[str, object]:
    """Record what shipped, and say whether anything is left to chase.

    The report a person reads at the gate is S-29.3; what this does is close the
    finding and clear the two handover channels, so a resumed run cannot ship the
    same patch twice.
    """
    del resources
    repaired = _repaired(state)
    identifier = str(repaired["finding"])
    resolved = {
        **state.resolved,
        identifier: {
            "verdict": "shipped",
            "approach": repaired["approach"],
            "share_removed": repaired["share_removed"],
        },
    }
    remaining = any(
        name not in resolved or _verdict_of(resolved[name]) not in {"shipped", "unsound"}
        for name in state.findings
    )
    return {
        "route": "more_findings" if remaining else "done",
        "resolved": resolved,
        "repaired": None,
        "audited": None,
    }


# ------------------------------------------------------------------ the wiring


def bind(resources: Resources) -> Wiring:
    """The seven steps, each closed over the same resources.

    Closures rather than a class per node, because a node is a function of the
    state and one bundle of live objects, and seven classes holding one field each
    would be seven places for that field to be named differently.
    """
    return Wiring(
        refuse=_step(refuse, resources),
        ground=_step(ground, resources),
        scan=_step(scan_for_waste, resources),
        audit_finding=_step(audit_finding, resources),
        optimize=_step(optimize, resources),
        audit_patch=_step(audit_patch, resources),
        ship=_step(ship, resources),
    )


def _step(
    adapter: Callable[[Resources, PipelineState], Mapping[str, object]], resources: Resources
) -> Step:
    """One adapter, bound to its resources, typed as the graph's `Step`.

    Annotated `Step` rather than `Callable`: `Step.__call__` declares a *named*
    parameter and a `Callable`'s are positional-only, so a closure returned as
    `Callable[[PipelineState], ...]` fails at `add_node`.
    """

    def run(state: PipelineState) -> Mapping[str, object]:
        return adapter(resources, state)

    run.__name__ = adapter.__name__
    return run


# ------------------------------------------------------------------ translation


def _refused(project: dict[str, Any], because: str) -> Mapping[str, object]:
    return {"route": "refused", "project": {**project, "refused": because}}


def _restore(resources: Resources, state: PipelineState) -> None:
    """Give the ledger back what the checkpoint carried, for ids it does not hold."""
    known = set(resources.ledger.known)
    entries = [
        entry
        for entry in state.measurements
        if isinstance(entry, Mapping) and str(entry.get("measurement_id")) not in known
    ]
    if entries:
        resources.ledger.restore(entries)


def _measurements(resources: Resources, state: PipelineState) -> list[Mapping[str, Any]]:
    """What this node added to the ledger, and only that.

    `measurements` is append-only, so returning the whole ledger would record
    every earlier entry twice -- which the reducer refuses by name.
    """
    already = {
        str(entry.get("measurement_id"))
        for entry in state.measurements
        if isinstance(entry, Mapping)
    }
    return [entry for entry in resources.ledger.entries() if entry["measurement_id"] not in already]


def _runnable(outcome: Outcome) -> Mapping[str, Any]:
    """How the subject is driven, taken from the turn that proved it measures."""
    for action, result in reversed(outcome.transcript.turns):
        if result.verified:
            return {
                "command": list(action.arguments.get("command", ())),
                "measurement_id": result.measurement_id,
                "turns": len(outcome.transcript.turns),
            }
    message = (
        "grounding ended as measured and no turn carries a verified measurement. "
        "The runnable artifact is what `scan` starts from, so an empty one would begin "
        "an investigation with nothing to drive"
    )
    raise NodeError(message)


def _brief(runnable: object) -> str:
    """What the scan's fresh conversation is opened with."""
    if not isinstance(runnable, Mapping):
        message = "the scan node ran with no runnable artifact; grounding is what produces one"
        raise NodeError(message)
    command = " ".join(str(part) for part in runnable.get("command", ()))
    return (
        "The program is already runnable and its workload already measures the same way "
        f"twice. Drive it with: {command}\n"
        f"That run was recorded as {runnable.get('measurement_id')}. Start from it: measure, "
        "profile and ablate are all available to you now."
    )


def _mint(state: PipelineState, offset: int) -> str:
    """`f-1`, `f-2`, ... by position, so a resumed run continues the sequence."""
    return f"f-{len(state.findings) + offset + 1}"


def _finding(payload: object) -> Finding:
    if not isinstance(payload, Mapping):
        message = f"the findings channel holds {type(payload).__name__}, not a finding"
        raise NodeError(message)
    return Finding(
        claim=Claim.model_validate(payload["claim"]),
        attested_against=tuple(str(item) for item in payload.get("attested_against", ())),
    )


def _next_unaudited(state: PipelineState) -> tuple[str, Finding]:
    for identifier, payload in state.findings.items():
        if identifier not in state.resolved:
            return identifier, _finding(payload)
    message = (
        "audit_finding ran with nothing left to audit. The graph reaches it only when scan "
        "routed `findings`, so this is a run whose state and route disagree"
    )
    raise NodeError(message)


def _next_sound(state: PipelineState) -> tuple[str, Finding]:
    for identifier, payload in state.findings.items():
        resolution = state.resolved.get(identifier)
        if isinstance(resolution, Mapping) and resolution.get("verdict") == "sound":
            return identifier, _finding(payload)
    message = "optimize ran with no sound finding waiting for a patch"
    raise NodeError(message)


def _baseline(resources: Resources, state: PipelineState) -> Scored:
    """The unpatched run, from the numbers the ledger already holds.

    Measuring again here would spend the run's money to learn what grounding wrote
    down, and would get a slightly different answer for it -- so every later
    comparison would be against a baseline nothing else in the run agrees with.
    """
    runnable = state.runnable
    identifier = runnable.get("measurement_id") if isinstance(runnable, Mapping) else None
    record = resources.ledger.recorded(str(identifier)) if identifier is not None else None
    if record is None:
        message = (
            "the run has no baseline measurement to compare candidates against. Grounding "
            "records one and `runnable` names it"
        )
        raise NodeError(message)
    peak = record.get("peak_rss_bytes")
    return Scored(
        candidate=Candidate(identifier="baseline", approach="baseline", diff=""),
        measurement_id=str(identifier),
        wall_s=float(record["wall.median"]),
        peak_rss_bytes=int(peak) if isinstance(peak, (int, float)) else None,
    )


def _repairs(resources: Resources) -> Repairs:
    if resources.repairs is None:
        message = (
            "this pipeline was assembled without the repair seams -- a test writer, a way to "
            "run a test, a way to apply and measure a candidate, and the two revisions to "
            "compare. That is a misassembled harness, not a finding that could not be fixed"
        )
        raise NodeError(message)
    return resources.repairs


def _repaired(state: PipelineState) -> Mapping[str, Any]:
    if not isinstance(state.repaired, Mapping):
        message = "this node needs the patch `optimize` produced, and the channel is empty"
        raise NodeError(message)
    return state.repaired


def _verdict_of(resolution: object) -> str:
    return str(resolution.get("verdict")) if isinstance(resolution, Mapping) else ""


__all__ = [
    "GROUND_BOUNDS",
    "SCAN_BOUNDS",
    "Falsified",
    "NodeError",
    "Repairs",
    "Resources",
    "bind",
]

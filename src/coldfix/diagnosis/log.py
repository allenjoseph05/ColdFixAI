"""The experiment log: what was tried, what it measured, and what that settled.

Epic 8, S-8.4. The record every later story reads — S-8.1 is handed it to
generate the next hypothesis, S-8.5 reopens exclusions against it, S-8.6 assembles
an evidence chain out of it, and S-8.7's instrument switch has to be visible in
it.

**Nothing here calls a model.** Appending a record and hashing it are functions.

**Append-only is a non-negotiable with a cost attached.** `CLAUDE.md`: *never
reorder or re-summarize it mid-investigation — that invalidates prompt caching
and multiplies cost by ~20x.* So the rule is expressed as an **absence**, the
construction S-5.7 and S-5.8 both used: there is no `reorder`, no `summarize`, no
`replace`, no `forget`. A method that cannot be called is a guarantee; a comment
asking callers not to call one is a request.

**There is exactly one log, and this is not it.** S-5.8 already owns the log that
reaches a prompt — `PrunedLog`, with its retrieval notice, its summaries and its
`read_experiment`. Epic 5's own composition check found **two append-only logs**
as a defect and the failure was silent: caching is a prefix match, so a log wrong
in *content* but still append-only reports full cache hits and a rising bill with
nothing failing. This module therefore owns the **artifact** and delegates the
rendering, and `append` is the single way into both — the index comes from the
pruned log, and the record is filed under it, so the two cannot drift.

**An experiment carries five things because a finding needs all five.** AC 1
lists hypothesis, primitive, design, measurement and verdict, and the first
non-negotiable is that *a conclusion drawn from reading code is not a finding*.
A record missing its measurement is exactly that conclusion, so it is refused at
the point of appending rather than discovered when an evidence chain is
assembled three stories later.

**Serialization is stable in the sense that matters.** Canonical JSON — sorted
keys, fixed separators — makes a digest a property of the record rather than of
how it was built. Cache-friendliness is the stronger claim and is tested
directly: the rendered log at N entries is a **byte prefix** of the rendered log
at N+1, which is the only property S-5.7's prefix cache actually needs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coldfix.cost.pruning import ExperimentRecord, PrunedLog, PruningError

_STRICT = ConfigDict(frozen=True, extra="forbid")


class ExperimentLogError(Exception):
    """An experiment could not be recorded."""


class Verdict(StrEnum):
    """What an experiment settled. S-8.3's three, named here because the log
    stores them and a record needs the vocabulary before the story that produces
    it exists.

    Three rather than two, and the middle one is the important one: an experiment
    that neither confirms nor refutes a hypothesis has usually *narrowed* it, and
    collapsing that into `REJECTED` throws away the half of the search space it
    bought.
    """

    CONFIRMED = "confirmed"
    NARROWED = "narrowed"
    REJECTED = "rejected"

    @property
    def settled(self) -> bool:
        """Whether this verdict closes the hypothesis it was reached about."""
        return self is not Verdict.NARROWED


class Experiment(BaseModel):
    """One experiment, complete enough to be argued with.

    Every field is required, and that is AC 1 rather than strictness for its own
    sake: this artifact is what S-8.6 assembles an evidence chain from, and a
    chain built from records missing their measurements is the *conclusion drawn
    from reading code* the first non-negotiable exists to prevent.
    """

    model_config = _STRICT

    index: int = Field(gt=0)
    """Position in the log, assigned by the log. One-based, because
    `read_experiment(1)` has to mean the first experiment."""

    hypothesis: str = Field(min_length=1)
    """What this experiment was run to test, as it was stated when it was run.

    Recorded as the statement rather than as a reference to a live object,
    because the log is read long after the hypothesis was superseded and *what we
    believed at the time* is the thing a reader needs.
    """

    primitive: str = Field(min_length=1)
    """Which instrument ran. S-3.1's registry name, and what S-8.7's switch is
    visible in — two consecutive records naming different primitives is the
    thesis behaviour, on the record."""

    rationale: str = Field(min_length=1)
    """Why this instrument was worth its cost given what was already known.

    **Added at S-8.7, whose AC 3 requires the switch *and its rationale* to appear
    in the log.** The primitive alone shows *that* the agent changed instrument;
    it is this that says why, and the thesis claim is about the choosing rather
    than about the change. S-8.1 already produces it — the field is where it
    stops being discarded.

    Required rather than defaulted, on S-5.4's argument: a default would make
    AC 3 hold only for the callers that remembered.
    """

    target: str = Field(min_length=1)
    """What the instrument was pointed at: a workload, a call site, a component."""

    design: str = Field(min_length=1)
    """The experiment specification, as run. S-8.2 produces these."""

    measurement: Mapping[str, float]
    """What the harness measured. **Never what the agent reported.**

    `CLAUDE.md`: *do not let an agent report a measurement; agents reason about
    measurements the harness took.* Numbers rather than prose, so a later
    experiment can be compared with this one rather than read against it.
    """

    verdict: Verdict
    outcome: str = Field(min_length=1)
    """One line, for the prompt. The measurement is the evidence; this is how the
    log reads at a glance, and S-5.8's summary is built from it."""

    detail: str = ""
    """The full output — stdout, stacks, per-call timings, raw counters. Held
    always and rendered never: S-5.8 defers it and `read_experiment` retrieves
    it, because writing it into the log would invalidate the cached prefix."""

    @field_validator("measurement")
    @classmethod
    def _measured(cls, measurement: Mapping[str, float]) -> Mapping[str, float]:
        if not measurement:
            message = (
                "an experiment with no measurement is a conclusion drawn from reading code, "
                "which the first non-negotiable exists to prevent. Record what was measured, or "
                "do not record an experiment"
            )
            raise ValueError(message)
        return dict(measurement)

    def digest(self) -> str:
        """A stable hash of the record.

        Canonical JSON — sorted keys, fixed separators — so the digest is a
        property of the experiment rather than of how the object was assembled.
        Two processes that recorded the same experiment agree on it.
        """
        rendered = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode()).hexdigest()[:16]


class ExperimentLog:
    """Every experiment, in the order they were run, and never in any other.

    **There is no `reorder`, no `summarize`, no `replace`, no `forget`.** The
    non-negotiable is expressed as an absence rather than as a warning, which is
    the construction S-5.7 used for the prompt and S-5.8 for the pruned view.

    Wraps S-5.8's `PrunedLog` rather than reimplementing it. Epic 5's composition
    found two append-only logs and the failure was silent — a prefix match still
    reports cache hits when the content is wrong — so there is one log, one
    rendering, and one way in.
    """

    def __init__(self) -> None:
        self._pruned = PrunedLog()
        self._experiments: dict[int, Experiment] = {}

    def append(  # noqa: PLR0913 - the five AC 1 names plus what the prompt and the
        # retrieval need; bundling them into a config object would invent a type
        # whose only purpose is to be unpacked here.
        self,
        *,
        hypothesis: str,
        primitive: str,
        rationale: str,
        target: str,
        design: str,
        measurement: Mapping[str, float],
        verdict: Verdict,
        outcome: str,
        detail: str = "",
    ) -> Experiment:
        """Record one experiment. The only way anything enters this log.

        **The artifact is validated before the summary is taken**, and the order
        is the whole correctness argument. An append-only log cannot retract an
        entry — that is what append-only *means* — so a record that passes S-5.8's
        summary rules and then fails AC 1's would leave a summary in the rendered
        prompt with no experiment behind it. The prompt would show an experiment
        that this log cannot produce a measurement for, and nothing would have
        raised at the point where the two stopped agreeing.

        Found by running it. The first version appended first, and a record with
        an empty hypothesis left exactly that orphan.

        Raises:
            ExperimentLogError: the record is missing something AC 1 requires, or
                a part the prompt needs is empty, multi-line or over S-5.8's
                summary budget. **Nothing is written in either case.**
        """
        try:
            experiment = Experiment(
                index=len(self._experiments) + 1,
                hypothesis=hypothesis,
                primitive=primitive,
                rationale=rationale,
                target=target,
                design=design,
                measurement=measurement,
                verdict=verdict,
                outcome=outcome,
                detail=detail,
            )
        except ValueError as error:
            message = f"this is not a complete experiment and was not logged: {error}"
            raise ExperimentLogError(message) from error

        try:
            record = self._pruned.append(
                primitive=primitive, target=target, outcome=outcome, detail=detail
            )
        except PruningError as error:
            raise ExperimentLogError(str(error)) from error

        if record.index != experiment.index:  # pragma: no cover - `append` is the only writer
            message = (
                f"the summary was filed as {record.index} and the artifact as "
                f"{experiment.index}. The two are only ever written together, so this means "
                "something else has appended to the pruned log"
            )
            raise ExperimentLogError(message)

        self._experiments[record.index] = experiment
        return experiment

    @property
    def experiments(self) -> Sequence[Experiment]:
        """Every experiment, oldest first. A copy, so a caller cannot reorder it."""
        return tuple(self._experiments[index] for index in sorted(self._experiments))

    def experiment(self, index: int) -> Experiment:
        """One experiment by its position.

        Raises:
            ExperimentLogError: no experiment at that index. Not a `None`,
                because every caller of this is about to read a measurement out
                of it and a `None` would reach the arithmetic.
        """
        found = self._experiments.get(index)
        if found is None:
            message = (
                f"there is no experiment {index}; this log holds "
                f"{len(self._experiments)}. Indexes are one-based and assigned in order"
            )
            raise ExperimentLogError(message)
        return found

    def render(self) -> str:
        """What reaches the prompt. S-5.8's rendering, unchanged.

        Delegated rather than reimplemented: the retrieval notice, the summary
        shape and the byte-prefix guarantee are all S-5.8's, and a second
        renderer would be a second thing to keep in step with S-5.7's cache.
        """
        return self._pruned.render()

    def read_experiment(self, index: int) -> str:
        """The full detail for one experiment, retrieved and not re-logged."""
        return self._pruned.read_experiment(index)

    @property
    def pruned(self) -> PrunedLog:
        """The log S-5.7 assembles a prompt from.

        Exposed so a caller wires *this* log into the prompt rather than building
        its own. The one Epic 5's composition check would have caught earlier if
        it had existed.
        """
        return self._pruned

    @property
    def records(self) -> Sequence[ExperimentRecord]:
        """The pruned view, for a caller that wants summaries rather than artifacts."""
        return self._pruned.records

    def switches(self) -> Sequence[tuple[Experiment, Experiment]]:
        """Consecutive pairs where the instrument changed. S-8.7's AC 3.

        A **view**, not a record: the switch is not a separate thing that
        happened, it is a property of two adjacent entries, and storing it would
        be a second statement of what the log already says — the shape S-8.5
        refused for `invalidated_if` and S-8.6 for `confidence`.

        Read from the artifacts rather than from the pruned summaries, because a
        pair is only a switch if both halves are real experiments.
        """
        entries = self.experiments
        return tuple(
            (earlier, later)
            for earlier, later in pairwise(entries)
            if earlier.primitive != later.primitive
        )

    def describe_switches(self) -> str:
        """The switches and **why each was made**, which is the other half of AC 3.

        The primitive alone shows *that* the instrument changed. The thesis claim
        is about the choosing, so the rationale travels with it — and the verdict
        that provoked the switch is named too, because *switched after a
        rejection* and *switched after a confirmation* are different behaviours
        and only the first is the one being claimed.
        """
        switches = self.switches()
        if not switches:
            return "No instrument switch: every experiment used the same primitive."
        lines = [f"{len(switches)} instrument switch(es):"]
        lines.extend(
            f"  {earlier.primitive} -> {later.primitive} after experiment {earlier.index} "
            f"came back {earlier.verdict.value}: {later.rationale}"
            for earlier, later in switches
        )
        return "\n".join(lines)

    def digest(self) -> str:
        """A stable hash of the whole log, in order.

        Over the per-experiment digests rather than a re-serialization, so that
        the log's identity is composed of its entries' identities and appending
        changes it in exactly one way.
        """
        joined = "|".join(experiment.digest() for experiment in self.experiments)
        return hashlib.sha256(joined.encode()).hexdigest()[:16]

    def describe(self) -> str:
        lines = [f"Experiment log: {len(self._experiments)} experiment(s)"]
        lines.extend(
            f"  {experiment.index}. {experiment.primitive} of {experiment.target} "
            f"→ {experiment.verdict.value}: {experiment.outcome}"
            for experiment in self.experiments
        )
        return "\n".join(lines)

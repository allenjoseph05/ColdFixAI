"""Epic 8 composed: a screened workload in, an evidence chain out.

Nine stories, and after all of them the epic **could not perform its own
sentence**. Every module passed its own tests; what none of them held was the
joins between them, which is the finding Epic 7 recorded and this epic repeats
almost exactly.

The three defects were all the same shape — a value one story produces and
another consumes, where nothing in either story's tests holds both ends:

1. **Two append-only logs, again.** `Session` renders a `PrunedLog` into the
   block `04-cost.md` §4 caches; `ExperimentLog` wraps its own. The session's
   block rendered an empty log forever while the real one rode in the uncached
   question. Epic 5 found this inside its own epic and recorded why it is silent:
   caching is a prefix match, so a log wrong in *content* is still append-only
   and still reports hits.
2. **The conditions and the symptom had no producer.** Every caller built them by
   hand, including the tests — which is exactly why nothing noticed. A hand-built
   `Conditions` can claim `uniform` while the recipe says `long_tail`, and an
   exclusion recorded under a shape that was never used is permanently and
   wrongly live: F3 reintroduced at the join S-8.5 exists to close.
3. **S-8.6 was unreachable.** A confirmed investigation had no path to the
   artifact the epic exists to produce. That is *AC satisfied in isolation and
   unreachable in practice* — the more dangerous half, because the criterion
   reads as met.

Measurements here are real; model calls are replayed. Same split as S-8.7, for
the same reason.
"""

from __future__ import annotations

import pytest

from coldfix.bench.stats import Growth
from coldfix.diagnosis.chain import ChainError, EvidenceChain, Implicated, Site
from coldfix.diagnosis.compose import assemble_with
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.emit import chain_from, conditions_for, symptom_for
from coldfix.diagnosis.exclusions import Dimension
from coldfix.diagnosis.explain import parse, shares_from
from coldfix.diagnosis.loop import Measured, confirming_links, run_investigation
from coldfix.diagnosis.progress import ProgressError, Stopped
from coldfix.llm.client import ReplayingClient
from coldfix.primitives.scaling import Distribution
from coldfix.sandbox.reset import ResetStrategy
from coldfix.screening.workload import FixtureRecipe, Observation, Workload
from fixtures.thesis import (  # the subject and its harness, not a second copy
    CONDITIONS,
    SCALES,
    Subject,
    _query_counter,  # noqa: F401 - registers the `query_counter` fixture
    ablate_renderer,
    an_investigation,
    sweep_queries,
)
from fixtures.thesis import (
    _thesis_recordings as thesis_recordings,
)


def a_workload() -> Workload:
    """What Epic 7 hands Epic 8: a grounded, screened workload.

    Built from the same planted subject the thesis run drives, so the conditions
    derived from it are the conditions the experiments actually ran under.
    """
    return Workload(
        id="shop-books-list",
        description="the book list endpoint, rendering every row",
        entry_point="shop/views.py::ListView.list_books",
        fixture=FixtureRecipe(
            entity="book",
            per_parent=2,
            parents=SCALES[-1],
            distribution=Distribution.UNIFORM,
            source="synthesis from schema",
        ),
        reset_method=ResetStrategy.SNAPSHOT_RESTORE,
        observations=tuple(
            Observation(scale=scale, metrics={"db.query": 2.0, "seconds": 0.4 * scale})
            for scale in SCALES
        ),
    )


def run_the_epic(subject: Subject) -> object:
    """The whole loop, driven the way a caller would."""

    def execute(spec: ExperimentSpec) -> Measured:
        if spec.primitive == "scaling.volume":
            return sweep_queries(subject)
        return ablate_renderer(subject)[0]

    investigation = an_investigation(ReplayingClient([]), execute)
    client = thesis_recordings(investigation, subject)
    return run_investigation(
        investigation.session,
        client,
        instruments=investigation.instruments,
        source=investigation.source,
        conditions=CONDITIONS,
        execute=execute,
        measured_prefix_tokens=100,
        measured_prompt_tokens=900,
    )


# ============================== defect 1: one log, and it is the one that caches


def test_the_log_the_agent_reads_is_the_log_the_prompt_caches(query_counter: None) -> None:
    """**Two append-only logs, found again.** Epic 5's composition found this
    inside its own epic; Epic 8 rebuilt it across the boundary, because `Session`
    constructs a `PrunedLog` and `ExperimentLog` wraps one and nothing joined
    them.

    The failure is silent for the reason Epic 5 recorded — the cached prefix is
    still a prefix, so the cache still reports hits — and what it costs is the
    whole growing part of the prompt at full price on every call, against a cost
    model that assumes 85% cached.
    """
    result = run_the_epic(Subject())

    assert len(result.log.experiments) == 2  # type: ignore[attr-defined]
    assert len(result.session.log.records) == 2  # type: ignore[attr-defined]
    assert result.session.log is result.log.pruned  # type: ignore[attr-defined]

    model = result.session.models_used[0]  # type: ignore[attr-defined]
    blocks = result.session.prompt_for(model).render("next?")  # type: ignore[attr-defined]
    rendered = next(block.text for block in blocks if "Experiment log" in block.text)

    assert "scaling.volume" in rendered
    assert "ablation.stub" in rendered


def test_the_session_reports_on_the_experiments_that_actually_ran(query_counter: None) -> None:
    """The other half of the same defect: S-5.8's pruning report described an
    empty log, so the figure `04-cost.md` §5's 60-80% claim is measured from was
    measured over nothing."""
    result = run_the_epic(Subject())

    assert "2" in result.session.log.report()  # type: ignore[attr-defined]


# ================== defect 2: the conditions and the symptom come from the workload


@pytest.mark.parametrize("shape", list(Distribution))
def test_the_conditions_are_read_from_the_workload_rather_than_described(
    shape: Distribution,
) -> None:
    """**A hand-built `Conditions` can disagree with the fixture that was
    actually seeded**, and an exclusion recorded under a shape nobody used is
    permanently and wrongly live — F3 at the join S-8.5 exists to close.

    **Parametrised over every shape, because the first version was not.** The
    subject's fixture is uniform, so hardcoding `"uniform"` in the join changed
    nothing and the sabotage survived — a fixture where the right answer and the
    wrong answer coincide. Eighth instance of that shape in this project.
    """
    workload = a_workload().model_copy(
        update={"fixture": a_workload().fixture.model_copy(update={"distribution": shape})}
    )

    conditions = conditions_for(workload, platform="x86_64-linux")

    assert conditions.observed[Dimension.FIXTURE_SHAPE].values == (shape.value,)
    assert conditions.observed[Dimension.SCALE].values == tuple(float(s) for s in SCALES)


def test_a_workload_nothing_has_swept_cannot_supply_conditions() -> None:
    """An empty observation list is a real state — the Explorer emits a workload
    before anything sweeps it — so conditions built from one would give every
    exclusion a scale envelope no experiment established."""
    workload = a_workload().model_copy(update={"observations": ()})

    with pytest.raises(ChainError, match="no observations"):
        conditions_for(workload)


def test_the_symptom_comes_from_what_screening_measured() -> None:
    """The investigation did not observe the symptom — it was handed it."""
    workload = a_workload()

    symptom = symptom_for(workload.observations[-1], "seconds")

    assert symptom.at_scale == float(SCALES[-1])
    assert symptom.magnitude == 0.4 * SCALES[-1]


def test_a_symptom_quoting_a_metric_nobody_measured_is_refused() -> None:
    """The first non-negotiable, at the top of the report rather than in the
    middle of it."""
    with pytest.raises(ChainError, match="did not measure"):
        symptom_for(a_workload().observations[0], "cpu_seconds")


EXPLAINED = """{"mechanism": "the renderer walks every row and re-renders its synopsis",
 "site": {"path": "shop/rendering.py", "first_line": 54, "last_line": 61},
 "context": [{"path": "shop/views.py",
              "reason": "constructs the renderer the list view calls per row"}]}"""
"""One reply, as the Diagnostician would answer it. Hard-coded because the only
alternative is a network call; everything it becomes goes through the same
`parse` a live reply would."""


# ============================ defect 3: a confirmed investigation reaches S-8.6


def test_a_confirmed_investigation_can_now_produce_an_evidence_chain(
    query_counter: None,
) -> None:
    """**The epic's own sentence, end to end.** Until this join existed,
    `EvidenceChain` could be constructed by hand in a test and by nothing in the
    system — the criterion read as met and was unreachable.

    **Both halves now have producers, and this test supplies neither.** At S-8.11
    the interpreted half stopped being three literal strings written here: it is
    an `Explanation` parsed from a reply, which is what a cascade would return.
    The shares stopped being a hand-built tuple: `shares_from` reads each one off
    the measurement the primitive recorded. What remains hard-coded is the reply
    itself, because the alternative is a network call.
    """
    workload = a_workload()
    result = run_the_epic(Subject())

    explanation = parse(EXPLAINED)
    assert explanation.value is not None, explanation.rejection

    chain = assemble_with(
        result,  # type: ignore[arg-type]
        symptom=symptom_for(workload.observations[-1], "seconds"),
        complexity={"rows": Growth.LINEAR},
        shares=shares_from(confirming_links(result)),  # type: ignore[arg-type]
        explanation=explanation.value,
    )

    assert isinstance(chain, EvidenceChain)
    assert chain.independent_confirmations == 1
    assert chain.confidence == 0.5

    # The exclusion the sweep bought reaches the report **with its conditions**,
    # which is what stops F3 arriving in a pull request.
    rendered = chain.render()
    assert "the database is the bottleneck" in rendered
    assert "fixture shape uniform" in rendered


def test_an_investigation_that_confirmed_nothing_is_refused_a_chain() -> None:
    """The pairing S-8.9 built, reached through the join: a run with no cause owes
    a partial chain, and `chain_from` says so rather than assembling something
    that claims one."""
    investigation = an_investigation(ReplayingClient([]), lambda spec: {"db.query": 2.0})

    with pytest.raises(ChainError, match="partial chain"):
        chain_from(
            investigation,
            symptom=symptom_for(a_workload().observations[0], "seconds"),
            mechanism="m",
            complexity={"rows": Growth.LINEAR},
            site=Site(path="a.py", first_line=1, last_line=2),
            context=[Implicated(path="b.py", reason="r")],
            shares={},
        )


def test_a_confirming_experiment_with_no_measured_share_is_refused(
    query_counter: None,
) -> None:
    """The primitive that ran knows what fraction disappeared with the component.
    A chain that guessed it would put a number nobody measured under a finding —
    the first non-negotiable, one level below the schema that enforces it."""
    workload = a_workload()
    result = run_the_epic(Subject())

    with pytest.raises(ChainError, match="no share of cost was supplied"):
        chain_from(
            result,  # type: ignore[arg-type]
            symptom=symptom_for(workload.observations[-1], "seconds"),
            mechanism="m",
            complexity={"rows": Growth.LINEAR},
            site=Site(path="a.py", first_line=1, last_line=2),
            context=[Implicated(path="b.py", reason="r")],
            shares={},
        )


# ============================================ the epic performs its own sentence


def test_the_investigation_stops_with_one_artifact_or_the_other_and_never_both(
    query_counter: None,
) -> None:
    """`EvidenceChain` and `PartialChain` partition, and composing the epic is
    where that stops being a property of two constructors and becomes a property
    of a run: a confirmed investigation has `stopped is None` and owes a chain; a
    stopped one owes a partial chain and cannot produce a chain."""
    result = run_the_epic(Subject())

    assert result.stopped is None  # type: ignore[attr-defined]
    with pytest.raises(ProgressError, match="has not stopped"):
        result.partial_chain(symptom_for(a_workload().observations[-1], "seconds"))  # type: ignore[attr-defined]


def test_every_stopped_reason_still_yields_something_a_reader_can_act_on() -> None:
    """`00-BRIEF.md` §9 and §6: null results ship as answers, and the failure
    catalogue is more credible than the success rate. Three ways to stop, three
    dispositions, and none of them is an exception escaping the run."""
    assert {item.disposition for item in Stopped} == {
        Stopped.CAP.disposition,
        Stopped.STALL.disposition,
    }
    assert len(set(Stopped)) == 3

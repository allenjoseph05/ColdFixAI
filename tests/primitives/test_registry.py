"""An instrument is offered, withheld with a reason, or absent — never silently gone.

S-3.1. The registry's mechanical half (declare, look up, refuse a duplicate) is
easy and is tested here quickly. The half worth the file is the applicability
verdict, which has to keep three answers apart:

- *applicable* — offered.
- *not applicable* / *unsupported* — a definite no, recorded with its reason.
- *undetermined* — nobody established the fact. Withheld, and **said out loud**,
  because a silently missing instrument becomes an agent that believes it has
  exhausted the applicable experiments, and an instrument run where it does not
  apply becomes a flat result that reads as a published exclusion.

The rest of the file holds the two properties this story inherits from elsewhere:
a selection is a snapshot (ADR 002 — the tool list renders at position 0 of a
cached prompt and cannot move mid-run), and an agent needs no edit when a
primitive is added (AC 3, tested by calling one that did not exist when the
stand-in agent was written).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from coldfix.primitives.registry import (
    Applicability,
    Capability,
    CostClass,
    Primitive,
    PrimitiveUnavailableError,
    ProjectFact,
    ProjectProfile,
    RegistrationError,
    Registry,
    Selection,
    UnknownPrimitiveError,
    all_of,
    always,
    requires,
)


def counting(workload: str, scale: int) -> object:
    """Stand-in for a primitive body. The registry never looks at a result."""
    return {"workload": workload, "scale": scale}


if TYPE_CHECKING:
    # Deliberately importable only to a type checker, so that the annotation on
    # `unresolvable_annotation` below cannot be resolved at runtime.
    from coldfix.primitives.registry import Primitive as Workload


def unresolvable_annotation(workload: Workload) -> object:
    return workload


def observation() -> Primitive:
    return Primitive(
        name="observation.on_cpu",
        summary="Count events for one run and attribute them to call sites.",
        cost=CostClass.SECONDS,
        run=counting,
        required_capabilities={Capability.EVENT_COUNTERS},
    )


def longitudinal() -> Primitive:
    return Primitive(
        name="longitudinal",
        summary="Run at fixed size over hours and fit metrics against elapsed time.",
        cost=CostClass.HOURS,
        run=counting,
        applies=requires(
            ProjectFact.LONG_RUNNING_PROCESS,
            because="fitting against elapsed time says nothing about a process that exits",
        ),
    )


# A project where nothing is unknown and nothing is missing, so that a test
# about rendering is not quietly also a test about applicability.
EVERYTHING = ProjectProfile(
    capabilities=frozenset(Capability),
    facts=dict.fromkeys(ProjectFact, True),
)


# ------------------------------------------------------- declaring a primitive


def test_a_primitive_declares_the_four_things_the_story_asks_for() -> None:
    """AC 1."""
    primitive = longitudinal()

    assert primitive.name == "longitudinal"
    assert primitive.cost is CostClass.HOURS
    assert primitive.required_capabilities == frozenset()
    assert callable(primitive.applies)


def test_a_registered_name_cannot_be_taken_twice() -> None:
    """ADR 013's rule: two disagreeing registrations give measurements that are
    wrong, refusing gives measurements that are missing, and missing is
    recoverable."""
    registry = Registry()
    registry.register(observation())

    with pytest.raises(RegistrationError):
        registry.register(observation())


@pytest.mark.parametrize("name", ["Scaling.Volume", "scaling volume", "", "2fast", "scaling."])
def test_an_unusable_name_is_refused_at_registration(name: str) -> None:
    """The name renders into a prompt prefix cached for a whole investigation.
    It is validated rather than trusted."""
    with pytest.raises(RegistrationError):
        Primitive(name=name, summary="x", cost=CostClass.SECONDS, run=counting)


def test_a_primitive_without_a_summary_is_refused() -> None:
    """The summary is the entire description the agent gets. An empty one is a
    tool the model cannot choose for the right reason."""
    with pytest.raises(RegistrationError):
        Primitive(name="scaling.volume", summary="   ", cost=CostClass.SECONDS, run=counting)


def test_an_unknown_name_reports_the_names_that_exist() -> None:
    registry = Registry()
    registry.register(observation())

    with pytest.raises(UnknownPrimitiveError) as raised:
        registry.get("observation.on_cpus")

    assert raised.value.available == ("observation.on_cpu",)


# ------------------------------------------------- the three-answer applicability


def test_a_known_fact_makes_the_primitive_available() -> None:
    profile = ProjectProfile(facts={ProjectFact.LONG_RUNNING_PROCESS: True})

    verdict = longitudinal().verdict(profile)

    assert verdict.applicability is Applicability.APPLICABLE


def test_a_fact_known_false_is_not_applicable_and_says_so() -> None:
    profile = ProjectProfile(facts={ProjectFact.LONG_RUNNING_PROCESS: False})

    verdict = longitudinal().verdict(profile)

    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert "not one that runs as a long-lived process" in verdict.reason


def test_an_unknown_fact_is_undetermined_and_never_applicable() -> None:
    """The central property of this module.

    `08-audit.md` F7 is the worked example of the other reading: proportional
    perturbation on single-threaded code does not fail, it degenerates into
    ablation and returns numbers. Longitudinal on a CLI tool fits a flat line,
    which reads as *no ramp*, which ships as an exclusion. An unknown fact must
    not resolve to *yes*.
    """
    verdict = longitudinal().verdict(ProjectProfile())

    assert verdict.applicability is Applicability.UNDETERMINED
    assert "not known whether" in verdict.reason


def test_undetermined_is_distinguishable_from_not_applicable() -> None:
    """The two withholdings call for different actions — establish the fact, or
    never ask again — so they must not share a label."""
    unknown = longitudinal().verdict(ProjectProfile())
    known_false = longitudinal().verdict(
        ProjectProfile(facts={ProjectFact.LONG_RUNNING_PROCESS: False})
    )

    assert unknown.applicability is not known_false.applicability


def test_a_missing_capability_is_unsupported_rather_than_not_applicable() -> None:
    """A primitive the environment cannot run and one the subject cannot use are
    different problems with different fixes."""
    verdict = observation().verdict(ProjectProfile())

    assert verdict.applicability is Applicability.UNSUPPORTED
    assert "event counters" in verdict.reason


def test_capability_and_fact_gate_independently() -> None:
    """`01-primitives.md` §3: load needs a load generator (this environment) and
    a subject that serves concurrent requests (this project). Either absence
    withholds the primitive."""
    load = Primitive(
        name="load.usl",
        summary="Drive concurrent load and fit throughput against concurrency.",
        cost=CostClass.TENS_OF_MINUTES,
        run=counting,
        required_capabilities={Capability.LOAD_GENERATION},
        applies=requires(
            ProjectFact.SERVES_CONCURRENT_REQUESTS,
            because="there is nothing to drive concurrently otherwise",
        ),
    )
    has_generator = ProjectProfile(capabilities={Capability.LOAD_GENERATION})
    has_subject = ProjectProfile(facts={ProjectFact.SERVES_CONCURRENT_REQUESTS: True})

    assert load.verdict(has_generator).applicability is Applicability.UNDETERMINED
    assert load.verdict(has_subject).applicability is Applicability.UNSUPPORTED
    assert (
        load.verdict(
            ProjectProfile(
                capabilities={Capability.LOAD_GENERATION},
                facts={ProjectFact.SERVES_CONCURRENT_REQUESTS: True},
            )
        ).applicability
        is Applicability.APPLICABLE
    )


@pytest.mark.parametrize("unknown_first", [True, False])
def test_a_definite_no_beats_an_unknown_when_conditions_combine(unknown_first: bool) -> None:
    """Reporting the unknown would send somebody to measure a fact that cannot
    change the answer.

    Both declaration orders, because the natural implementation — return the
    first condition that fails — gets this right only when the author happens to
    have listed the decisive one first.
    """
    known_false = requires(ProjectFact.LONG_RUNNING_PROCESS, because="a")
    unknown = requires(ProjectFact.PARSES_UNTRUSTED_INPUT, because="b")
    predicate = all_of(unknown, known_false) if unknown_first else all_of(known_false, unknown)
    profile = ProjectProfile(facts={ProjectFact.LONG_RUNNING_PROCESS: False})

    assert predicate(profile).applicability is Applicability.NOT_APPLICABLE


def test_all_of_is_applicable_only_when_every_condition_holds() -> None:
    predicate = all_of(
        requires(ProjectFact.LONG_RUNNING_PROCESS, because="a"),
        requires(ProjectFact.PARSES_UNTRUSTED_INPUT, because="b"),
    )
    both = ProjectProfile(
        facts={ProjectFact.LONG_RUNNING_PROCESS: True, ProjectFact.PARSES_UNTRUSTED_INPUT: True}
    )
    one = ProjectProfile(facts={ProjectFact.LONG_RUNNING_PROCESS: True})

    assert predicate(both).applicability is Applicability.APPLICABLE
    assert predicate(one).applicability is Applicability.UNDETERMINED


def test_always_needs_only_its_capabilities() -> None:
    predicate = always()

    assert predicate(ProjectProfile()).applicability is Applicability.APPLICABLE


# --------------------------------------------------- what the Diagnostician gets


def test_only_applicable_primitives_are_offered() -> None:
    """AC 2."""
    registry = Registry()
    registry.register(observation())
    registry.register(longitudinal())

    selection = registry.select(ProjectProfile(capabilities={Capability.EVENT_COUNTERS}))

    assert selection.names == ("observation.on_cpu",)


def test_a_withheld_primitive_cannot_be_run_and_the_refusal_carries_its_reason() -> None:
    """AC 2, structurally. The list is not the enforcement — the lookup is."""
    registry = Registry()
    registry.register(longitudinal())

    selection = registry.select(ProjectProfile())

    with pytest.raises(PrimitiveUnavailableError) as raised:
        selection.get("longitudinal")
    assert raised.value.withheld.verdict.applicability is Applicability.UNDETERMINED
    assert "not known whether" in str(raised.value)


def test_a_name_nobody_registered_is_a_different_failure_from_a_withheld_one() -> None:
    """One is a typo, the other is a subject that cannot support the experiment.
    Conflating them would send whoever reads the traceback to the wrong place."""
    registry = Registry()
    registry.register(longitudinal())

    selection = registry.select(ProjectProfile())

    with pytest.raises(UnknownPrimitiveError):
        selection.get("longitudnal")


def test_withholding_is_recorded_rather_than_silent() -> None:
    """`08-audit.md`: the agent cannot know what it does not know. An instrument
    that vanishes without a word becomes an agent that believes it ran out of
    applicable experiments."""
    registry = Registry()
    registry.register(observation())
    registry.register(longitudinal())

    selection = registry.select(ProjectProfile())
    notice = selection.withheld_notice()

    assert len(selection.withheld) == 2
    assert "longitudinal" in notice
    assert "observation.on_cpu" in notice
    assert "nothing it would have measured has been ruled out" in notice.lower()


def test_an_empty_selection_says_so_rather_than_rendering_nothing() -> None:
    """An empty TOOLS block would read as a prompt formatting slip. It is a
    project with no applicable instruments, which is a result."""
    selection = Registry().select(ProjectProfile())

    assert "No instruments" in selection.instrument_list()
    assert "Every registered instrument" in selection.withheld_notice()


# ------------------------------------------------------- the rendered tool list


def test_the_instrument_list_carries_signatures_and_cost() -> None:
    """`03-agents.md` §4.3 renders TOOLS as an instrument list with signatures."""
    registry = Registry()
    registry.register(observation())

    rendered = registry.select(EVERYTHING).instrument_list()

    assert "observation.on_cpu(workload: str, scale: int)" in rendered
    assert "[seconds]" in rendered
    assert "Count events for one run" in rendered


def test_an_annotation_renders_the_same_whether_or_not_its_module_defers_them() -> None:
    """This test module has `from __future__ import annotations`, so an
    unresolved signature would render `workload: 'str'`.

    A tool list is the cached prefix of every request in an investigation. Making
    its bytes depend on an import in the primitive's own file — one nobody would
    connect to prompt cost — is the kind of coupling that is discovered months
    later as an unexplained bill.
    """
    rendered = observation().signature

    assert "'" not in rendered
    assert rendered == "observation.on_cpu(workload: str, scale: int)"


def test_an_annotation_that_cannot_be_resolved_still_renders_unquoted() -> None:
    """The fallback path. `Workload` here exists only as a name in an annotation
    — which is what a `TYPE_CHECKING`-only import looks like at runtime — so
    resolution fails and the quotes are stripped instead."""
    primitive = Primitive(
        name="scaling.volume",
        summary="Run at several sizes and fit each metric against size.",
        cost=CostClass.MINUTES,
        run=unresolvable_annotation,
    )

    assert primitive.signature == "scaling.volume(workload: Workload)"


def test_the_rendering_does_not_depend_on_registration_order() -> None:
    """ADR 002: tools render at position 0 and prompt caching is a prefix match,
    so a list that reorders between runs invalidates every cached breakpoint
    behind it."""
    forward, backward = Registry(), Registry()
    for primitive in (observation(), longitudinal()):
        forward.register(primitive)
    for primitive in (longitudinal(), observation()):
        backward.register(primitive)

    assert forward.select(EVERYTHING).instrument_list() == (
        backward.select(EVERYTHING).instrument_list()
    )


def test_the_cheapest_band_renders_first() -> None:
    """`01-primitives.md` §17: check headroom first, skip the workload if there
    is none. The order the agent reads should be the order it should try."""
    registry = Registry()
    registry.register(longitudinal())
    registry.register(observation())

    rendered = registry.select(EVERYTHING).instrument_list()

    assert rendered.index("observation.on_cpu") < rendered.index("longitudinal")


# ------------------------------------------------------------ a fixed tool list


def test_registering_afterwards_does_not_change_a_selection_already_made() -> None:
    """ADR 002 again, and the reason the consequence is stated in the module
    docstring: an instrument learned about mid-investigation is available to the
    *next* investigation, not this one."""
    registry = Registry()
    registry.register(observation())
    selection = registry.select(EVERYTHING)
    before = selection.instrument_list()

    registry.register(longitudinal())

    assert selection.instrument_list() == before
    assert selection.names == ("observation.on_cpu",)
    with pytest.raises(UnknownPrimitiveError):
        selection.get("longitudinal")


def test_mutating_the_facts_afterwards_does_not_change_a_selection() -> None:
    """The profile is copied on construction. A caller holding the dictionary it
    passed cannot retroactively make an instrument have been available."""
    facts = {ProjectFact.LONG_RUNNING_PROCESS: False}
    profile = ProjectProfile(facts=facts)
    registry = Registry()
    registry.register(longitudinal())
    selection = registry.select(profile)

    facts[ProjectFact.LONG_RUNNING_PROCESS] = True

    assert selection.names == ()
    assert profile.facts[ProjectFact.LONG_RUNNING_PROCESS] is False


# ------------------------------------------- adding a primitive touches no agent


def stand_in_diagnostician(selection: Selection, name: str) -> object:
    """Everything an agent needs to know about primitives, written before any
    primitive existed: read the list, pick a name, call it.

    No branch per primitive, no import of one, no registration site to update.
    That is what AC 3 asks for, and the test below proves it by calling through
    this function into a primitive defined after it.
    """
    assert name in selection.instrument_list()
    return selection.get(name).run("checkout", 100)


def test_a_new_primitive_is_reachable_without_touching_agent_code() -> None:
    """AC 3 and AC 4. `10-BACKLOG.md`: this is what makes primitive fifteen an
    afternoon rather than a refactor."""
    registry = Registry()
    registry.register(
        Primitive(
            name="substitution.configuration",
            summary="Sweep a configuration value and re-measure.",
            cost=CostClass.MINUTES,
            run=counting,
        )
    )

    result = stand_in_diagnostician(registry.select(EVERYTHING), "substitution.configuration")

    assert result == {"workload": "checkout", "scale": 100}


def test_the_registry_is_introspectable_without_a_project() -> None:
    """AC 4. The full set is readable for documentation and for the instrument
    list, independently of any one project's selection."""
    registry = Registry()
    registry.register(longitudinal())
    registry.register(observation())

    assert registry.names == ("longitudinal", "observation.on_cpu")
    assert [primitive.name for primitive in registry.declared()] == [
        "observation.on_cpu",
        "longitudinal",
    ]


def test_unregistering_something_absent_raises() -> None:
    with pytest.raises(UnknownPrimitiveError):
        Registry().unregister("longitudinal")

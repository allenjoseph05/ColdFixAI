"""Turning a designed experiment into a measurement.

S-17.11. Two things are being tested and only one of them is the executor.

The executor itself is small: resolve, check what is owed, merge, run, read. What
is large is the claim that **every** registered primitive can be read — thirteen
result types with nothing in common — and the way that claim fails is not an
exception here but an investigation dying mid-loop, having already paid for the
design that selected the instrument nobody could read.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import pytest

import coldfix.primitives  # noqa: F401 - registers the thirteen; REGISTRY is empty without it
from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.execution import ExecutionError, executor_for
from coldfix.diagnosis.loop import Measured
from coldfix.diagnosis.readings import READERS
from coldfix.primitives.registry import (
    REGISTRY,
    Capability,
    CostClass,
    Primitive,
    ProjectProfile,
    Registry,
    UnknownPrimitiveError,
)


def spec(primitive: str = "toy", **arguments: Any) -> ExperimentSpec:
    return ExperimentSpec(primitive=primitive, target="shop.books.list", arguments=arguments)


# ============================================ the claim that costs a run if it is false


def test_every_registered_primitive_has_a_reader() -> None:
    """**AC 2, and it is a partition rather than a list.**

    S-17.6's lesson. Asserting that each name in `READERS` is registered would
    pass while a fourteenth primitive arrived with no reader — and the failure
    that produces is not an import error at startup. The agent selects the
    instrument, the design is written and paid for, and the executor raises on a
    turn that has already spent a frontier call.
    """
    registered = set(REGISTRY.names)

    assert set(READERS) == registered


def test_no_reader_exists_for_something_that_is_not_a_primitive() -> None:
    """The other half of the partition. A reader for a name nothing registers is
    dead code that reads as coverage."""
    assert set(READERS) - set(REGISTRY.names) == set()


@pytest.mark.parametrize("name", sorted(REGISTRY.names))
def test_each_reader_takes_the_type_its_primitive_returns(name: str) -> None:
    """The table is keyed by name, so nothing structural stops a reader being
    filed against the wrong primitive. This is what does."""
    returns = inspect.signature(REGISTRY.get(name).run, eval_str=True).return_annotation
    takes = next(iter(inspect.signature(READERS[name]).parameters.values())).annotation

    # `readings.py` has `from __future__ import annotations`, so the reader's is
    # a string. Compared by name rather than resolved, because resolving it would
    # be this test importing the same module under test to agree with itself.
    assert str(takes).split("[")[0] == returns.__name__, (
        f"{name}: reader takes {takes}, primitive returns {returns.__name__}"
    )


# ==================================================== AC 1: resolve, check, merge, run


def toy_registry() -> tuple[Registry, dict[str, Any]]:
    """One primitive whose bound half and answerable half are both non-empty.

    Built rather than borrowed: driving the merge through a real instrument would
    need a seeded fixture and a container, and the property under test is which
    arguments arrive, which a counting function shows exactly.
    """
    seen: dict[str, Any] = {}

    def run(*, invoke: Any, scales: list[int], label: str = "x") -> Mapping[str, float]:
        seen["invoke"] = invoke
        seen["scales"] = scales
        seen["label"] = label
        return {"db.query": float(len(scales))}

    registry = Registry()
    registry.register(
        Primitive(name="toy", summary="a toy instrument", cost=CostClass.SECONDS, run=run)
    )
    return registry, seen


def readers_for_toy() -> Mapping[str, Any]:
    return {"toy": lambda result: Measured(measurement=dict(result))}


def test_the_design_half_and_the_bound_half_arrive_together() -> None:
    """**AC 1.** The specification is not a call; this is what completes it."""
    registry, seen = toy_registry()
    invoke = object()

    execute = executor_for(
        registry.select(_profile()), {"invoke": invoke}, readers=readers_for_toy()
    )
    measured = execute(spec(scales=[10, 40, 160]))

    assert seen["invoke"] is invoke, "the harness's half"
    assert seen["scales"] == [10, 40, 160], "the design's half"
    assert seen["label"] == "x", "and a default the design did not answer"
    assert measured.measurement == {"db.query": 3.0}


def test_a_binding_the_primitive_needs_and_nobody_supplied_is_refused() -> None:
    """Refused before the call, and the message names the binding.

    A `TypeError` from inside a primitive arrives after the fixture is seeded and
    the container is up, and it names an argument rather than the thing the
    harness failed to supply.
    """
    registry, _ = toy_registry()

    execute = executor_for(registry.select(_profile()), {}, readers=readers_for_toy())

    with pytest.raises(ExecutionError, match=r"needs \['invoke'\]"):
        execute(spec(scales=[10]))


def test_an_argument_the_instrument_has_no_parameter_for_is_refused() -> None:
    """The direction that would otherwise read as a harness defect.

    `run(**merged)` with an unexpected key raises `TypeError: unexpected keyword`,
    which points at the call site rather than at the design that asked for
    something the instrument does not have.
    """
    registry, _ = toy_registry()

    execute = executor_for(
        registry.select(_profile()), {"invoke": object()}, readers=readers_for_toy()
    )

    with pytest.raises(ExecutionError, match="no parameter for"):
        execute(spec(scales=[10], repetitions=4))


def test_an_unregistered_primitive_is_refused_by_the_selection() -> None:
    """Not re-checked here. `Selection.get` already tells an unknown primitive
    from a withheld one, and both refusals carry what a caller needs."""
    registry, _ = toy_registry()

    execute = executor_for(
        registry.select(_profile()), {"invoke": object()}, readers=readers_for_toy()
    )

    with pytest.raises(UnknownPrimitiveError):
        execute(spec("scaling.volumee", scales=[10]))


def test_a_primitive_with_no_reader_is_refused_before_it_runs() -> None:
    """The failure the totality test exists to prevent, asserted from the other side."""
    registry, seen = toy_registry()

    execute = executor_for(registry.select(_profile()), {"invoke": object()}, readers={})

    with pytest.raises(ExecutionError, match="has no reader"):
        execute(spec(scales=[10]))
    assert seen == {}, "and the primitive was never run"


def _profile() -> ProjectProfile:
    """Everything available. Applicability is S-3.1's and is tested there; what is
    under test here is what happens after a primitive was offered."""
    return ProjectProfile(capabilities=frozenset(Capability))

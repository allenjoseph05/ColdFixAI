"""Running a designed experiment against the subject. **`Resources.executor`.**

S-17.11. `Executor` is `Callable[[ExperimentSpec], Measured]` and the loop calls
it once per turn. A spec carries a primitive name and **the answerable half** of
that primitive's parameters (S-8.2); `PrimitiveSchema.bound` names the half the
harness still owes — the seeding callable, the invoking callable, the verified
reset, the session. So the work is: resolve, merge, run, read.

**The selection refuses, not this module.** `Selection.get` already tells an
unknown primitive from a withheld one, and both refusals carry what a caller
needs — the near-miss names in one case, the recorded reason in the other. Adding
a check here would be a second opinion about applicability with nothing extra to
base it on.

**A missing binding is refused before the primitive is called.** The alternative
is a `TypeError` from deep inside a primitive after the fixture has been seeded
and the container is running, and the message names an argument rather than the
thing the harness failed to supply. What makes this checkable at all is that
`PrimitiveSchema` is read off the callable, so the list of what is owed cannot
drift from what the function takes.

**An argument the design named and the schema does not have is refused too**, and
that is the direction that would otherwise be silent: `run(**merged)` with an
unexpected key raises a `TypeError` naming the keyword, which reads as a bug in
the harness rather than as a design that asked for something the instrument has
no parameter for.

**This module measures nothing and computes nothing.** It calls the primitive and
hands the result to `readings.READERS`, which reads numbers off fields the
primitive set. `CLAUDE.md` puts the measuring in the harness and the reasoning in
the agent, and the executor is the seam between them — which is exactly where a
convenience like *fill in a missing metric with zero* would be invisible.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from coldfix.diagnosis.design import ExperimentSpec
from coldfix.diagnosis.loop import Executor, Measured
from coldfix.diagnosis.readings import READERS
from coldfix.diagnosis.schema import schema_of
from coldfix.primitives.registry import Selection


class ExecutionError(Exception):
    """A designed experiment could not be run as specified."""


def executor_for(
    selection: Selection,
    bindings: Mapping[str, object],
    *,
    readers: Mapping[str, Callable[[Any], Measured]] = READERS,
) -> Executor:
    """Run designed experiments against one bound subject.

    `bindings` is what the harness owes, by parameter name — `seed`, `invoke`,
    `reset`, `session`, and whatever else the registry's primitives declare.
    **One mapping for all thirteen rather than one per primitive**, because the
    names come from the schemas and a per-primitive table would be a second place
    that has to agree with them. A binding nothing asks for is simply unused; one
    something asks for and is absent is refused by name.

    `readers` is a parameter so a test can drive the merge and the refusals
    without thirteen real primitives, and defaults to the real table so no
    production caller chooses.

    Raises:
        ExecutionError: the primitive has no reader, a bound parameter was not
            supplied, or the design named an argument the instrument has no
            parameter for.
        UnknownPrimitiveError, PrimitiveUnavailableError: from `Selection.get`,
            which already tells those two apart and says why.
    """

    def execute(spec: ExperimentSpec) -> Measured:
        primitive = selection.get(spec.primitive)

        reader = readers.get(primitive.name)
        if reader is None:
            known = ", ".join(sorted(readers)) or "none"
            message = (
                f"{primitive.name} has no reader, so whatever it measured cannot be turned into "
                f"an experiment record. Readers exist for: {known}. A primitive the agent can "
                "select and this cannot run ends an investigation mid-loop, with the design "
                "already paid for"
            )
            raise ExecutionError(message)

        schema = schema_of(primitive)
        owed = [parameter.name for parameter in schema.bound]
        missing = [name for name in owed if name not in bindings]
        if missing:
            message = (
                f"{primitive.name} needs {missing} from the harness and they were not bound. "
                f"It owes {owed}; {sorted(bindings)} were supplied. Refused here rather than at "
                "the call, because a TypeError from inside a primitive arrives after the fixture "
                "is seeded and names an argument rather than the binding nobody supplied"
            )
            raise ExecutionError(message)

        answerable = {parameter.name for parameter in schema.specifiable}
        stray = sorted(set(spec.arguments) - answerable)
        if stray:
            message = (
                f"the design gave {primitive.name} {stray}, which it has no parameter for. It "
                f"answers {sorted(answerable)}. Refused rather than passed through, because an "
                "unexpected keyword raises from the call site and reads as a defect in the "
                "harness rather than as a design asking for something that does not exist"
            )
            raise ExecutionError(message)

        arguments: dict[str, Any] = {name: bindings[name] for name in owed}
        arguments.update(spec.arguments)

        return reader(primitive.run(**arguments))

    return execute
